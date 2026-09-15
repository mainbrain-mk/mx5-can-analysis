# Projektstand: MX-5 ND CAN-Bus-Logging

## Kontext
Ich besitze einen Mazda MX-5 ND RF G184 (Handschalter). Ich entwickle bereits seit Wochen ein physikalisches Python-Fahrleistungsmodell (Längsdynamik: Antrieb, Traktion, Schaltzeiten, Teillastkennfeld, Fahrwiderstände) basierend auf eigenen OBD/GPS-Logs (App "OBD Fusion", Mazda-Paket, Handy-GPS vom OnePlus CE5). Ziel ist perspektivisch die Berechnung einer theoretischen Bestzeit aus einem GPS-Track (Querdynamik/Kurvenmodell fehlt aktuell noch).

Um höher aufgelöste Rohdaten als über OBD-Fusion-Polling zu bekommen, logge ich jetzt zusätzlich rohe CAN-Bus-Frames.

**Dieses Dokument ist der Statusbericht zum CAN-Bus-Logging-Teilprojekt.** Der
übergeordnete Projektstand (Fahrleistungs-/Fahrdynamikmodell insgesamt -
Antriebsstrang, Teillast, Ausrollversuche, Vmax, IMU/Vibration etc.) steht in
[`PROJEKT_STAND.md`](PROJEKT_STAND.md).

## Kurzüberblick: aktueller Stand (2026-09-14)

**Status:** Vollautomatisches CAN-Logging läuft produktiv seit 2026-09-11 (KeyState-getriggert,
ein Log pro Fahrt, kein manueller Schritt nötig). Die DBC-Dekodierung deckt praktisch alle
bekannten Signale fehlerfrei ab (mehrere stille Decode-Fehler gefunden und behoben, siehe unten).
Kupplung, Tankfüllstand und Bremsdruck sind gegen echte OBD-Referenzwerte kalibriert. Erste
CAN-Kanäle (Drehzahl, Speed, Gas, Bremse, Lenkwinkel, Kupplung, Radgeschwindigkeiten) sind ins
bestehende Fahrleistungsmodell integriert und über mehrere Modelle (Beschleunigung, Bremsen, Vmax)
validiert. TPMS ist jetzt live am Fahrzeug bestätigt (siehe unten). Ein zweiter, bisher
unentdeckter Fall des Pi-Uhr-ohne-RTC-Bugs wurde gefunden und behoben (zwei CAN-Logs waren
~2:43-2:45h falsch datiert). Das Touchdisplay-GUI hatte einen >2s-Gauge-Lag, der auf zwei
konkrete Ursachen zurückgeführt und gefixt wurde (noch nicht live verifiziert). **Größter Sprung
heute:** OBD-Werte lassen sich jetzt direkt aus jedem CAN-Log dekodieren (kein Handy/`.dlg`
mehr nötig, framegenau statt zeitversatz-geschätzt) — darauf aufbauend ein wiederverwendbares
Byte-Sweep-Tool gebaut, das 3 neue CAN-Signale gefunden und die `SteeringAngle_related`-
"schwaches Signal"-Frage als Messmethoden-Artefakt aufgelöst hat (siehe Logbuch, Abschnitt
"CAN-Byte-Search-Projekt").

### Hardware & Infrastruktur
- **Adapter:** DSD TECH SH-C31A (CANable 2.0) am OBD-Port, nur HS-CAN (`can0`), 500 kbit,
  R120=OFF (kein Busende, Adapter hängt nur als Stich am Bus).
- **Pi:** Hostname **`car`** (umbenannt 2026-09-13, vorher `ras8` – der Name kollidierte im
  mDNS mit einem anderen Gerät im Netz). Erreichbar unter `pi@192.168.0.247` bzw. `car.local`,
  passwortloses SSH + sudo, Debian 12 Bookworm.
  - Root-Dateisystem läuft schreibgeschützt mit RAM-Overlay (`overlayroot`, `recurse=0`) – nur
    `/home/pi/canlogs` (USB-Stick, 14,6GB) ist persistent. Jede Config-Änderung am System
    (außerhalb des Sticks) braucht: Overlay disable (Parameter in `/boot/firmware/cmdline.txt`
    entfernen) → reboot → ändern → Overlay-Parameter wieder eintragen → reboot.
  - `can-logger.service` (systemd, root, device-bound an `can0`) startet `session_logger.py`,
    das per `KeyState` (0x050) automatisch pro Fahrt ein `candump -l`-Log startet/stoppt/gzippt.
  - 10"-Touchdisplay: `status_gui.py` (Tkinter) zeigt Live-Status, eine Testmodus-Checkliste und
    Live-Gauges (Gas/Bremse/Kupplung/Lenkwinkel/Speed). **2026-09-14: >2s-Gauge-Lag gefixt** –
    zwei Ursachen (pgrep-Fork pro Sekunde direkt in der Tk-Mainloop, ungefilterter CAN-Read)
    behoben, deployt, aber noch NICHT bei einer echten Fahrt live verifiziert.
- **DBC:** `data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc` ist die primäre, laufend gepflegte
  Datei (alle Fixes/Funde). `_HSCAN.dbc` bleibt bewusst unverändertes Upstream-Original
  (`berumiya/CAN_DBC_6thGenMazda`, CC-BY-4.0).

### Dekodierbare CAN-Datenpunkte
Alle gesicherten (nicht `_maybe`/`_related`) Signale aus der DBC, nach Funktion gruppiert.
✓ = bereits eigener Kanal im Datalake (`data/datalake.duckdb`), ⚠ = trotz unmarkierter
DBC-Definition durch eigene Analyse als fehlerhaft/unplausibel identifiziert – nicht blind
vertrauen, Details im Logbuch unten.

- **Antrieb/Motor:** EngineRPM✓ (rpm), APP_Accelerator_Pedal_Position✓ (%, Gaspedal),
  CoolantTemp✓ (°C), IAT_Sensor_No1✓ (°C), MAP_Manifold_absolute_pressure_sensor (kPa,
  Saugrohrdruck), VS1_Vaccum_Sensor_1 (kPa), MT_Gear_Actual✓ (0-7, Gang, Reverse=7 noch nicht
  zuverlässig bestätigt), MT_Gear_Position/MT_Gear_Select/MT_Gear_Recommend (weitere
  Getriebe-Rohsignale), Clutch_Pedal_Position_raw✓ (0-199 roh, **kalibriert:**
  `CPP_PER_MZ% ≈ 0,4665·raw+0,56`, sein Duplikat `Clutch_Pedal_Position_related_2`@0x166
  2026-09-14 über alle 4 Logs mit OBD-Traffic bestätigt, r=0,999), Fuel_Tank✓ (roh 0x09E,
  **kalibriert:** `FLI% ≈ 2,486·raw-0,02`), EngineLoad_or_Torque_pct_maybe (0x167,
  **kalibriert:** `Torque% ≈ raw·3,17-185,3`, ersetzt die frühere Fehlannahme
  "MassAirFlowRate", **2026-09-14 cross-log bestätigt** (3-4/4 Logs, korreliert mit
  Gaspedal/MAP) – vorher nur an einem Log geprüft), EngineLoad_related_maybe (0x200,
  ebenfalls 2026-09-14 cross-log bestätigt, gleiche Last-Domäne, kein reines Duplikat),
  EngineRPM_related_3_maybe (0x42B, **neu 2026-09-14**, Byte1-2, bisher komplett leere
  Botschaft – korreliert mit EngineRPM aber nur R²=0,44, kein reines Duplikat des
  bekannten "Drehzahl×2"-Signals auf 0x130, Rohwert-Durchreichung ohne Formel).
- **Fahrdynamik (IMU, RCM):** Longitudinal_Acc_Raw✓ (G), Lateral_Acc_Raw✓ (G), YawRate_Raw✓
  (deg/s) – **2026-09-13: Vorzeichen korrigiert** (DBC nutzt SAE-Konvention +=links, Datalake
  jetzt durchgängig +=Rechtskurve wie der Rest des Projekts, siehe PROJEKT_STAND.md
  "Kurvenmodell, erster Baustein"). Beide Kanäle per Y-Splitter-Log (gleichzeitiges CAN+OBD,
  candump-2026-09-12_211833/2026-09-12 211851.dlg) quantitativ gegen das validierte
  OBD-Lenkwinkelmodell bestätigt: Lateral_Acc_Raw r=0,91/slope=1,05, YawRate_Raw
  r=0,95/slope=0,78 (Modell flacht bei schnellen Lenkumkehrungen ab, bekannter Caveat,
  siehe mx5_steering_angle-Memory), beide RCM-Signale intern konsistent (r=0,965). ⚠
  Longi_Acc_Corr/Lateral_Acc_Corr_maybe (HS_ABS, 0x78/0x79) – nachweislich fehlerhaft
  skaliert, RCM-Werte oben nutzen statt dieser. **YawRate_Corr (0x79) 2026-09-14 REPARIERT** –
  war keine echte Fehlmessung wie seine Geschwister, sondern eine nie überprüfte Formel aus
  der ursprünglichen Community-DBC; per Regression gegen YawRate_Raw neu kalibriert
  (`(0,133,-68,1)`, R²=0,89-0,92, cross-log r=0,94-1,00). **YawRate_related (0x78, neu
  2026-09-14):** Byte2-3, eigenständiges ABS-Modul-Gierratensignal, `≈0,0011·raw` deg/s,
  R²=0,91-0,96, über 5/5 Logs bestätigt – interessanter Ansatzpunkt für einen künftigen
  ABS/DSC-Eingriffsindikator (noch nicht gefunden). **AmbientTemp✓ (2026-09-15 GELÖST)** –
  war derselbe Bug-Typ wie BrakePressure (unsigned statt signed, plus falsche Skala/Offset);
  neue Formel `(0,0025, +32)` signed liefert plausible 3,8-25,9°C über 3 Logs (siehe Logbuch,
  Abschnitt "gitgc/mx5-miata-nd2-obd-can").
- **Bremse/ABS:** BrakePressure✓ (bar) – Vorzeichen- UND Offsetfehler der Community-DBC
  gefunden und behoben (2026-09-12, jetzt `(0,0012413, +32,7986)`, R²=0,986 gegen
  echten OBD-Referenzkanal), kein Rollover-Sonderfall mehr nötig. BBP_Brake_Booster_Pressure_2/
  BARO_Barometric_pressure (kPa), WheelSpeed_1-4✓ (km/h, Sentinel 0xFFFF gefiltert),
  DSC_Status (nur System an/aus, kein Regelungseingriff), VehicleSpeed✓ (km/h).
- **Lenkung:** Steering_Wheel_Absolute_Angle✓ (deg) – Nullpunkt per GPS bestätigt (0,0°
  Median-Offset), Lock-to-Lock-Range ±490° bestätigt. `SteeringAngle_EPAS` (0x86, EPAS, früher
  `SteeringAngle_related`) – **2026-09-15 endgültig gelöst, die 09-14-Erklärung war falsch.**
  Das oberste Bit ist ein Gültigkeits-/Init-Flag (eigenes Signal `SteeringAngle_EPAS_Invalid`),
  kein Teil des Zahlenwerts; es ist nur in 0,0-0,5% der Frames gesetzt (Block am Logstart,
  Rohwert konstant 16000 = 0°) und drückte den Pearson von 1,00 auf 0,50. Nach Abtrennen ist
  das Signal **perfekt linear**: `0,1·raw − 1600` deg, R²=0,9997-0,99995, RMSE 0,65-0,94°
  über 6 Logs. Die frühere "stark nichtlinear"-Diagnose und die daraus gebaute
  Isotonic-Regression (`scripts/can_steering_angle_0x86.py`, R²=0,90, RMSE=10,5°) sind damit
  **obsolet**, ebenso die am 09-15 verworfene externe lineare Formel – sie war richtig. `SteeringAngle_EPAS_Coarse` (0x86, Bits 26|11) – **2026-09-15 korrigiert:** das frühere
  `SteeringAngle_related_3` saß auf den falschen Bits (Byte4-5). Der echte zweite Kanal liegt
  auf 26|11 und ist nach Verwerfen der Invalid-Frames ebenfalls perfekt linear
  (`1,6·raw − 1600`, R²=0,9997-0,9999, RMSE 0,72-1,07° über 5 Logs) – eine grob aufgelöste
  Zweitübertragung desselben Winkels, keine eigene Größe. Byte4-5 bleibt unidentifiziert.
- **Zündung/Fahrzustand:** KeyState✓/KeyStateInv (OFF/ACC/ON/START), StarterInterLockSW
  (Anlasssperren-Schalter), Parking_Brake (springt bei Zündung ACC/OFF fest auf "Applied",
  nur bei Motor ON aussagekräftig), CC_SetSpeed (km/h, Tempomat-Soll).
- **Karosserie/Komfort:** DoorLeft/DoorRight (Labels im Original-DBC vertauscht, hier
  korrigiert), Trunk, Turn/TURN_SW (Blinker), HAZ_SW (Warnblinker),
  Headlight/LIGHT/PanelLight/F_FOG_RELAY/R_FOG_LAMP (Lichtfunktionen, LIGHT-Bit-Overlap-Bug
  2026-09-12 behoben), FrontWiper/RearWiper/Washer/WIP_F_INT (Wischer, noch nicht inhaltlich
  verifiziert), ROOM_FUSE (Innenraumlicht-Sicherung).
- **Fahrerassistenz:** BSM_Left/BSM_Right/BSM_Status/BSM_Warning (Toter-Winkel-Warner),
  LDWS_Status1/LDWS_Status2/LDWS_ON_Switch (Spurhalteassistent), SCBS_SBS_Status
  (Notbremsassistent), DSC_OFF_Switch/iStop_OFF_Switch (Deaktivierungsschalter),
  RoofGraphicStatus (Verdeckstatus).
- **Sonstiges:** Mileage/Date (HS_CMU, multiplex-kodiert), `C001_ODO` (0x40A, exakter
  Gesamtkilometerstand, gegen Kombiinstrument bestätigt), `C0xx_VIN_*` (Fahrgestellnummer,
  byte-order-Fehler behoben), HUD_Height/HUD_Height_Moving/HUD_Bright (Head-Up-Display),
  Key_hold/Key_notfound/Key_Batt/Key_fob/IC_Buzz/INFO_SW/CRU_CON_SW1 (Schlüssel/Bedienelemente),
  SpeedUnit/StopVehicle (Anzeigeeinstellungen).
- **GPS (begleitender Track, nicht CAN selbst):** Breite✓/Länge✓ (deg), Höhe✓ (m),
  GPS-Geschwindigkeit✓ (km/h).
- **Berechnete Ersatzgrößen (kein eigenes CAN-Byte, aber validiert):** echter Luftmassenstrom
  `MAF(g/s) ≈ 0,00019·(RPM·MAP) - 8,08` (R²=0,945); Drosselklappenstellung nur als Schätzung
  aus Pedal+Drehzahl (R²≈0,89), kein Broadcast-Signal gefunden.
- **OBD-Kanäle direkt aus dem CAN-Log (neu 2026-09-14):** wann immer das Y-Splitter-Kabel
  genutzt wird, sind die Handy-OBD-Requests/-Antworten (`0x7E0`/`0x7E8`) selbst im CAN-Log
  enthalten – `scripts/obd_from_can.py` dekodiert sie direkt daraus, ganz ohne `.dlg`-Datei
  und ohne die bisherige Zeitversatz-Schätzung zwischen zwei unabhängig getakteten Dateien.
  6 Mode-22-DIDs über 4 unabhängige Logs identisch zugeordnet (`scripts/map_obd_dids.py`):
  `AFR_MZ`, `BFP_PRE_MZ`, `ETC_ACT`, `CPP_PER_MZ`, `FLI` (alle mit klarer Formel, R²=0,99+),
  plus `TM_GEST` (vermutlich eine Bitmask, kein linearer Kanal). Das Y-Kabel ist inzwischen
  offenbar Standard bei jeder Fahrt, nicht mehr nur eine einmalige Kalibrierfahrt – 3 von 4
  Logs mit OBD-Traffic stammen vom 14.09.
- **TPMS (Reifendruck/-temperatur, 2026-09-13, live bestätigt 2026-09-14):** einziges Signal
  in diesem Projekt, das NICHT periodisch gebroadcastet wird – wird per aktivem UDS-Request
  (`scripts/tpms_poller.py`, Header 0x720, Mode 0x22, Poll-Intervall bewusst 120s) abgefragt,
  Antwort auf `0x728` (BO_ 1832, DBC-Multiplex nach PID). Läuft automatisch als zweiter
  Kindprozess in `session_logger.py` bei jeder Fahrt mit; Kollision mit dem Handy-OBD-Adapter
  am Y-Kabel geprüft und für unkritisch befunden (Handy sendet selbst durchgehend ~16-17
  Requests/s auf anderen Headern; eigene Zusatzlast nur ~0,005% der Busauslastung, siehe
  Logbuch). Werte erscheinen zusätzlich in vier GUI-Bildschirmecken (`TpmsCornersPanel` in
  `status_gui.py`). Tire3=Hinten Links, Tire4=Hinten Rechts bestätigt; Tire1/Tire2=Vorderachse,
  Reihenfolge weiterhin offen (im GUI mit "*" markiert). Temperatur-PIDs (0x2A0A-0D) nur vom
  Nutzer per OBD getestet, noch nicht per CAN bestätigt. **Erste echte Auswertung
  (2026-09-14, `scripts/tpms_log_decode.py`, 2 Fahrten):** physikalisch plausibel (Druck
  steigt mit Temperatur, ideales Gasgesetz), Werte jetzt auch im Datalake
  (`TirePressure_CAN_Tire1-4`, `TireTemp_CAN_Tire1-4`, vorher in der DBC dekodiert aber nie
  in `CAN_SIGNAL_MAP` nachgetragen). **Auffällig, dem Nutzer noch nicht bestätigt:** Tire4
  (hinten rechts) liegt in beiden Fahrten durchgängig ~0,15-0,2 bar über den anderen drei
  Reifen.

Aktuell im Datalake sind nur die ✓-markierten, durchgehend numerischen Telemetriekanäle
integriert (siehe `CAN_SIGNAL_MAP` in `scripts/build_datalake.py`) – Schalter/Status-Signale
sind genauso dekodierbar, aber bisher nicht übernommen (bei Bedarf leicht ergänzbar).

### Neue Analyse-Tools (2026-09-14, portiert aus CSS-Electronics/can-bus-reverse-engineering-skills)
Nach Durchsicht des CSS-Electronics-Artikels/-Repos zwei reine, hardwareunabhängige
Analysebausteine übernommen (Plan + Details siehe `mx5_can_bus_logging.md`), plus eigener
Workflow-Skill **`.claude/skills/mx5-can-reverse-engineering/SKILL.md`** (Schritt-für-
Schritt-Anleitung für künftige Reverse-Engineering-Sessions + Blueprint für eine mögliche
künftige Live-Referenz-Erfassung über SocketCAN, falls je ein Signal ganz ohne
OBD/CAN-Referenz gesucht werden muss):
- **`scripts/can_re_toolkit.py`** – agnostische Sentinel-/Ausreißer-Erkennung
  (`detect_extreme_outliers`, Median±MAD/Gap/Teleport, wert-/vorzeichenunabhängig),
  referenzfreie Plausibilität (`plausibility`), und bias-gated (nicht R²-gated)
  Scale/Offset-Nachkorrektur (`propose_round_calibration`, `propose_anchor_calibration`
  für Ruhezustand-Refits). Selbsttest: `python scripts/can_re_toolkit.py`.
- **`scripts/can_bitsearch.py`** – exhaustive Sub-Byte-Feldsuche (Startbit×Länge×
  Endianness×Sign) für eine einzelne CAN-ID gegen eine Referenz (OBD-DID oder anderes
  DBC-Signal), mit Parsimonie-Regel gegen Over-Wide-Reads (genau der TM_GEST-Dual-Column-
  Bug-Typ). Schließt die Lücke, dass `can_byte_search.py` nur ganze Bytes/Byte-Paare
  scannt. **Bekannte Grenze:** ohne Resolution-Refinement (bewusst nicht portiert, braucht
  Live-Sweep-Daten) kann bei glatten Signalen eine kürzere, kaum schlechter fittende
  Teilspanne gewinnen – das Tool druckt dann einen `[Auflösungshinweis]` mit dem
  längeren Alternativkandidaten. Selbsttest: `python scripts/can_bitsearch.py --demo`.
- **Sanity-Check der neuen Funktionen gegen bestehende Kalibrierungen:** BrakePressure-
  Offset (32,7986→33,0) und YawRate_Corr-Offset (-68,1→-68,0) könnten mit <0,5% bzw.
  <0,1% Bias aufgerundet werden (beide "auto"-sicher, niedrige Priorität, nicht
  angewendet). BrakePressure zeigt einen 15,3%-"Ausreißer"-Cluster bei mittlerer
  Konfidenz über einen breiten Wertebereich (33-114 bar) – sieht nach echten
  Bremsereignissen aus (bimodales Signal: meist ~0, selten hoch), nicht nach Sentinels;
  bei "medium confidence" bewusst nicht automatisch verworfen. Fuel_Tank/
  Clutch_Pedal_Position_raw ließen sich nicht sinnvoll prüfen – ihre DBC-Signal-Definition
  trägt noch die alte/rohe Skala (0,2 bzw. 1), die tatsächlich genutzten Formeln
  (`FLI%`, `CPP_PER_MZ%`) leben nur außerhalb der DBC.

### Bekannte offene Punkte
- Reverse-Gang (`MT_Gear_Actual=7`) registriert bisher nur bei stabiler, nicht rutschender
  Kupplung – Hypothese noch nicht durch eine gezielte Testfahrt bestätigt.
- **ABS/DSC-Eingriffsindikator weiterhin nicht gefunden** – Kernziel des 2026-09-14
  CAN-Byte-Search-Projekts (siehe Logbuch), nicht erreicht. Der gefundene `YawRate_related`
  (0x78) ist ein analoges Gierratensignal aus der ABS-Domäne, kein sauberes Eingriffs-Flag.
  Braucht entweder mehr Logs mit einem echten Eingriff oder eine gezielte Testfahrt.
- AFR/Lambda, CommandEquivalenceRatio, TimingAdvance, ETC_ACT (echte Drosselklappenstellung):
  bestätigt nicht periodisch auf HS-CAN broadcastet (nur Mode-22-Polling) – seit 2026-09-14
  aber direkt aus dem CAN-Log dekodierbar, ohne Handy/`.dlg` (siehe oben). **2026-09-14,
  gezielter `can_bitsearch.py`-Lauf (3 Logs) gegen AFR_MZ/ETC_ACT als Referenz:** bestätigt
  dies nochmal deutlich rigoroser. Bester AFR_MZ-Kandidat (`0x0FD` Byte 5) fittet zwar über
  alle 3 Logs auf dieselbe Bitlage, aber R² fällt 0,86→0,66→0,63 UND der Rohwert nimmt nur
  3 diskrete Werte {0,1,2} an – kein analoges Lambda-Signal, eher ein Status-Flag (z.B.
  Closed-Loop/Warmlauf-Zustand), das nur zufällig mit dem AFR-Trend mitläuft. Zweiter
  Kandidat `0x20A` fittet R² 0,76→0,48→0,49, UND die Gewinner-Bitlage wechselt sogar
  zwischen den Logs (39|10 vs. 29|11) – klares Zeichen für Zufallskorrelation statt echter
  Kodierung. ETC_ACT-bester Kandidat `0x200`: R² 0,52→0,29→0,29, Gewinner ebenfalls
  instabil zwischen zwei nahezu gleich schlecht fittenden Bitlagen (7|16 vs. 39|16). Beide
  weit unter der Bestätigungsschwelle (alle bisher bestätigten Signale: R²>0,9, stabile
  Bitlage über Logs hinweg) – keine der beiden Referenzen hat ein natives CAN-Gegenstück,
  mit ziemlicher Sicherheit.
- **Drei neue, bisher unbenannte Mode-22-DIDs** (2026-09-14, beim OBD-Überblick aufgefallen):
  nur ~20 Treffer in einem einzigen Log (`candump-2026-09-12_211833`), in den drei
  09-14-Logs gar nicht vorhanden – zu wenig Daten für eine belastbare Namens-/CAN-Zuordnung.
  `DA86` (vermutlich 2. Lambda-nahe DID, schwacher/unsicherer `.dlg`-Match auf AFR_MZ,
  r=0,70 bei 112s Lag), `F40C` (Match auf "TotalCO2" sieht nach Zufallstreffer aus, −102s
  Lag), `4028` (Wert über die ganze Session konstant 88 – vermutlich einmalige Konfig-/
  Capability-Abfrage, kein echter Messwert). Bei Gelegenheit mit einem längeren,
  OBD-Traffic-reichen Log erneut prüfen.
- Wischer-Test (Testplan-Punkt) strukturell dekodierbar seit dem LIGHT-Bit-Fix, aber inhaltlich
  noch nicht ausgewertet.
- TPMS-Vorderachsen-Zuordnung (Tire1 vs. Tire2 = vorne-links/-rechts) noch nicht getestet.
- Pi-GUI-Gauge-Lag-Fix (2026-09-14) deployt, aber noch nicht bei einer echten Fahrt live
  verifiziert.
- ~~`SteeringAngle_related_3` braucht eine nichtlineare Umrechnung~~ – **erledigt/verworfen
  2026-09-15:** das Signal saß auf den falschen Bits. Der echte Kanal (26|11) ist linear
  (`SteeringAngle_EPAS_Coarse`); Byte4-5 bleibt unidentifiziert, die frühere Einstufung als
  nichtlinearer Lenkwinkel ist nicht belegt.
- Bei jedem CAN-only-Log ohne GPS-/OBD-Zeitanker: Datum mit Vorsicht behandeln – der
  Pi hat keine RTC, ohne NTP während der ganzen Session bleibt die Uhr durchgehend falsch,
  OHNE einen erkennbaren Sprung im Log zu zeigen (**2026-09-15 zum dritten Mal** aufgetreten,
  siehe Logbuch). Seit 2026-09-15 korrigiert `log_start_epoch()` in
  `build_datalake.py` das selbst: weichen Dateiname und Frame-Zeitstempel um >60s ab,
  gewinnt der Dateiname. Vorher war ein umbenanntes Log im Datalake **weiterhin falsch
  datiert** – die beiden 09-14-Logs standen dort einen Tag lang unter dem alten
  13.09.-Zeitstempel.

### Nächste Schritte
1. Standtests wiederholen: Zündung durchgehend auf ON/Motor an (nicht nur ACC), Start-Tap
   direkt bei der Handlung drücken; fehlende Punkte (Blinker/Licht/Wischer, Tür links) ergänzen.
2. Fahrmanöver aus Testplan-Abschnitt C: mehrere Vollbremsungen (Rollover-Test), Schaltvorgänge
   in verschiedenen Drehzahlbereichen, Kurven beidseitig.
3. Vorderachsen-Zuordnung TPMS (Tire1/Tire2) per gezieltem Luftablass-Test klären.
4. Sobald weitere CAN-Logs mit begleitendem GPS-Track vorliegen: Paar in `CAN_GPS_PAIRS`
   (`scripts/build_datalake.py`) ergänzen und neu bauen.
5. Perspektivisch: verifizierte CAN-Signale weiter ins Fahrleistungsmodell einspeisen, für die
   Querdynamik/Kurvenmodell-Erweiterung (RCM-Signale bereits gegen das OBD-Lenkwinkelmodell
   validiert, Kurvenauswertung läuft inzwischen automatisch für jedes CAN-Log – siehe
   PROJEKT_STAND.md, 220 Kurven aus 7 Fahrten).
6. Pi-GUI-Lag-Fix bei der nächsten Fahrt live prüfen (soll den >2s-Gauge-Lag behoben haben).
7. **Gezielte Testfahrt für die zwei letzten offenen Kernfragen** (Plan-Phase 6, siehe Logbuch):
   Rückwärtsgang mit vollständig durchgetretener statt rutschender Kupplung, und – falls sicher
   möglich – eine Situation mit echtem ABS/DSC-Eingriff. Reine Log-Analyse kann beides nicht
   mehr weiterbringen, es fehlen die Rohdaten dafür.
8. ~~Nichtlineare Umrechnung für `SteeringAngle_related_3`~~ – entfällt (2026-09-15, siehe oben:
   falsche Bitlage; der echte Kanal ist linear).
9. Den CAN-Byte-Sweep (`scripts/can_byte_search.py`) bei künftigen neuen Logs erneut laufen
   lassen – aktuell nur an den 5 vorhandenen "reichhaltigen" Logs geprüft.

## Zusätzliche Notizen (Claude-Memory)
Ergänzend zu diesem Dokument gepflegt, überlebt Kontext-Resets:
- `mx5_can_bus_logging.md` — laufendes Erkenntnis-Log (Bugfixes, Byte-Suchen, offene Fragen wie Reverse-Gang, das komplette 2026-09-14-CAN-Byte-Search-Projekt in voller Detailtiefe)
- `mx5_nd3_obdii_repo_reference.md` — externe Referenz (ND3-Repo), Parallele zu Gear_CAN/MT_Gear_Recommend
- `mx5_datalake_can_channels.md` — aktueller Stand von `CAN_SIGNAL_MAP` in `build_datalake.py`, DuckDB-Viewer-Setup
- `mx5_tpms.md` — TPMS-Historie (Aufbau, Live-Test, Vorderachsen-Frage)
- `mx5_pi_status_gui_lag_fix.md` — Diagnose/Fix des Touchdisplay-Gauge-Lags
- `feedback_background_wait_loops.md` — Session-Mechanik-Lehre (nicht CAN-Projekt-Inhalt): Warteschleifen brauchen eine konkrete PID, kein Namensmuster

## Hardware
- **USB-CAN-Adapter:** DSD TECH SH-C31A, basierend auf CANable 2.0 (STM32, candleLight-Firmware)
  - USB-ID im Work Mode: `1d50:606f` (OpenMoko/Geschwister Schneider)
  - USB-ID im DFU-Bootloader: `0483:df11`
  - Schalter: **BOOT = OFF** (Work Mode), **R120 = OFF** (Adapter hängt nur als Stich am OBD-Port, kein Busende – R120=ON würde die Terminierung verfälschen)
- **Verkabelung:** selbst zusammengelötet, Cat5e-Patchkabel (grün, Adern orange/blau/gelb/weiß) zwischen Adapter und OBD-Stecker, ca. 30 cm lang (Verdrillung bei dieser kurzen Länge unkritisch)
- **OBD2-Pinbelegung (relevant):** Pin 6 = CAN_H, Pin 14 = CAN_L, Pin 4/5 = GND, Pin 16 = +12V
- Angeschlossen an einen Raspberry Pi, der die Logs schreibt; Übertragung per `scp`/`rsync` auf den Rechner

---

## Verlauf / Logbuch (chronologisch, Details & Herleitungen)

Ab hier folgt das vollständige chronologische Arbeitsprotokoll mit allen Herleitungen,
Messungen und verworfenen Hypothesen – die Kurzfassung oben fasst den aktuell gültigen Stand
zusammen, hier stehen die Belege dazu.

## Workflow auf dem Pi (überholt, siehe "Pi-Infrastruktur" unten)
```bash
sudo apt install can-utils python3-pip
pip3 install python-can
sudo modprobe gs_usb
sudo ip link set can0 up type can bitrate 500000
candump -l can0        # schreibt candump-<timestamp>.log
```
Das war der manuelle Weg fürs allererste Testlog. Seit 2026-09-11 läuft das automatisiert, siehe unten.

## Pi-Infrastruktur (2026-09-11)
Pi (damals `ras8`, seit 2026-09-13 umbenannt zu `car` – siehe Kurzüberblick oben, Debian 12 Bookworm/Raspberry Pi OS, erreichbar unter `pi@192.168.0.247`) hat ein 10"-Touchdisplay, wird während der Fahrt vorerst nur über eine Powerbank versorgt (Stromversorgung im Auto noch nicht final verbaut). Bekanntes Risiko: harter Stromverlust ohne sauberes Shutdown lässt Raspberry-Pi-SD-Karten oft nicht mehr booten (Flash-Controller-Korruption, das ext4-Journal schützt nur Metadaten, nicht die Karte selbst).

**Aufbau:**
- **`can-logger.service`** (`/etc/systemd/system/can-logger.service`): startet/stoppt automatisch, sobald das Interface `can0` auftaucht/verschwindet (USB-Adapter rein/raus oder Boot), über ein systemd-Device-Binding (`BindsTo=sys-subsystem-net-devices-can0.device`) + udev-Rule (`/etc/udev/rules.d/90-can-logger.rules`, löst `SYSTEMD_WANTS` aus). Funktioniert unabhängig davon, ob der Pi durchläuft oder mit der Zündung bootet. Muss als root laufen (nicht `User=pi`), weil `ip link ... type can` `CAP_NET_ADMIN` braucht. `ExecStartPre` bringt `can0` runter/rauf mit 500kbit und komprimiert beim Start alte, fertige `.log`-Dateien mit gzip (6,1x) – das passiert beim nächsten Start statt beim Stop, damit es auch nach hartem Stromverlust nachgeholt wird.
- **`gs_usb`** lädt automatisch beim Boot (`/etc/modules-load.d/can.conf`), NetworkManager ignoriert CAN-Interfaces (`/etc/NetworkManager/conf.d/90-can-unmanaged.conf`).
- **USB-Stick (14,6GB) für Logs:** ext4-formatiert, gemountet auf `/home/pi/canlogs` (per UUID in `/etc/fstab`, `nofail` damit der Boot nicht hängt falls der Stick mal fehlt). Physisch getrennt von der SD-Karte, damit Log-Schreibvorgänge (die häufigsten/größten Writes) nicht die System-SD-Karte gefährden.
- **Overlay-Root** (`overlayroot`-Paket, über `raspi-config nonint do_overlayfs 0` aktiviert, `overlayroot=tmpfs:recurse=0` in `/boot/firmware/cmdline.txt`): Root-Dateisystem wird bei jedem Boot schreibgeschützt gemountet + RAM-Overlay für Schreibzugriffe (verworfen bei jedem Neustart) → das OS selbst kann durch Stromverlust nicht mehr korrumpieren, bootet garantiert wieder.
  - **Wichtiger Stolperstein:** `recurse` (Standard 1) zieht *alle* Mounts inkl. separat gemounteter Filesysteme ins RAM-Overlay – damit wäre auch der USB-Stick nur noch RAM-gestützt und nichts hätte einen Reboot überlebt. Explizit `recurse=0` setzen, damit nur `/` selbst geschützt wird und der Stick ein echter, persistenter Mount bleibt.
  - Verifiziert (2x echter Reboot): Testdatei auf `/home/pi/` verschwindet nach Reboot (Overlay funktioniert), Dateien auf `/home/pi/canlogs` (Stick) überleben unverändert.
  - Alle bisherigen Configs (`can-logger.service`, udev-Rule, `/etc/modules-load.d`, NetworkManager-Conf) liegen auf der jetzt schreibgeschützten SD-Karten-Basis – Änderungen daran brauchen künftig Overlay deaktivieren, ändern, reboot, wieder aktivieren, reboot.
  - **Achtung:** `raspi-config nonint do_overlayfs 1` (deaktivieren) erkennt unseren `:recurse=0`-Parameter nicht (ihr Regex erwartet exakt `overlayroot=tmpfs `, nicht `overlayroot=tmpfs:recurse=0 `) – zum De-/Aktivieren `overlayroot=tmpfs:recurse=0 ` direkt in `/boot/firmware/cmdline.txt` ein-/austragen (`sudo mount -o remount,rw /boot/firmware` davor), nicht raspi-config nutzen.

## KeyState-getriggertes Logging (2026-09-11)
`can-logger.service` ruft nicht mehr direkt `candump` auf, sondern `scripts/session_logger.py` (Kopie liegt auf dem Pi unter `/home/pi/canlogs/session_logger.py`, eigenes venv dort unter `/home/pi/canlogs/venv` – `pip install` systemweit geht auf Debian 12 nicht mehr (PEP 668) und würde durchs Overlay sowieso bei jedem Reboot verschwinden).

**Warum:** Der Pi läuft während der Fahrt durchgehend an einer Powerbank, unabhängig von der Zündung. Ohne eigenen Trigger würde ein ganzer Tag mit mehreren Fahrten in eine einzige candump-Datei laufen (Start/Stop war bisher an die `can0`-Geräte-Präsenz gebunden, nicht an die Zündung).

**Wie:** Das Skript lauscht selbst per `python-can` auf `0x050` (KeyState, HS_SSU) und startet/stoppt `candump -l can0` als Kindprozess bei jedem Wechsel `OFF <-> ACC/ON/START` – ein sauberes Log pro Fahrt. `candump` selbst bleibt fürs eigentliche Schreiben zuständig (bewährt, schneller als eine eigene Python-Schreibschleife), das Skript ist nur der Trigger. Nach jedem Stop wird die gerade geschlossene Datei automatisch gezippt (ersetzt das alte "beim Service-Start gzippen", passiert jetzt pro Fahrt statt pro Pi-Boot).

**Getestet:**
- Offline gegen die echten KeyState-Übergänge im ersten Testlog (`scripts/test_session_logger.py`) – erkennt beide Fahrt-Segmente korrekt (inkl. der versehentlichen Zündungs-Aus/Ein-Episode als eigenes Segment, was inhaltlich korrekt ist)
- Live auf dem Pi: synthetische `KeyState=ON`/`OFF`-Frames per `cansend` injiziert (echte Fahrt stand nicht zur Verfügung), Start/Stop/Gzip liefen wie erwartet

**Aufbau:** `data/can/MX5ND_6thGenMazda_HSCAN.dbc` liegt zusätzlich auf dem Pi (`/home/pi/canlogs/`, USB-Stick, übersteht Overlay-Reboots). Umgebungsvariablen `MX5_DBC`, `MX5_LOG_DIR`, `MX5_CAN_CHANNEL` erlauben Pfad-/Kanal-Overrides ohne Code-Änderung.

**Alles startet automatisch beim Boot** – verifiziert: `gs_usb` (modules-load.d) → `can0` erscheint sobald Adapter Strom hat → udev löst `can-logger.service` aus (`enabled`, startet auch ohne Adapter-Replug beim Boot) → `session_logger.py` wartet auf KeyState. Kein manueller Schritt nötig.

## Status-GUI auf dem Touchdisplay (2026-09-11)
`scripts/status_gui.py` (Tkinter, stdlib – `python3-tk` war auf dem Pi schon vorhanden, keine neue Abhängigkeit nötig). Vollbild-Anzeige mit großer farbiger Status-Meldung, leitet den Zustand rein aus bereits vorhandenen System-Fakten ab (kein eigenes Status-File/IPC nötig):
- **Blau "WARTE AUF CAN-BUS"** – `can0` existiert nicht (`/sys/class/net/can0/operstate` fehlt)
- **Gelb "BUS AKTIV / WARTE AUF ZÜNDUNG"** – `can0` da, `session_logger.py` läuft, aber kein `candump`-Kindprozess
- **Grün "LOGGING LÄUFT"** – `candump -l can0` läuft
- **Rot "FEHLER"** – `can0` da, aber `session_logger.py` läuft nicht (Service abgestürzt)

Live auf dem Pi getestet (alle 3 Normalzustände per `cansend`-Simulation durchgespielt, visuell vom Nutzer bestätigt). Deployment: Skript liegt auf dem Stick (`/home/pi/canlogs/status_gui.py`), Autostart über `~/.config/labwc/autostart` (labwc/Wayland-Compositor, Raspberry Pi OS Bookworm Standard) – diese Datei liegt auf der schreibgeschützten SD-Basis, Änderungen brauchen wieder die Overlay-Disable/Edit/Enable-Runde; das GUI-Skript selbst liegt auf dem Stick und lässt sich ohne Reboot-Tanz aktualisieren (labwc-Session neu starten oder Prozess manuell neu starten reicht).

**Pi-Uhr-Hinweis:** keine gepufferte RTC – nach jedem Boot ist die Systemzeit erst nach NTP-Sync (paar Sekunden) korrekt, `uptime -s` kurz nach Boot kann verwirrend/falsch aussehen.

## Bisherige Logs & Erkenntnisse
Ein erster ~76s-Testdump (`candump-2026-09-11_180456.log`, 160.290 Frames, 101 unique CAN-IDs) wurde bei stehendem Fahrzeug aufgenommen (Zündung an, dann versehentlich Zündung aus/ein wegen nicht getretener Kupplung, dann Motorstart, Warmlauf).

**Gefundene Community-Ressourcen für den ND:**
- `github.com/timurrrr/RaceChronoDiyBleDevice/blob/master/can_db/mazda_mx5_nd.md` – einfache RaceChrono-Formeln (RPM, Gas, Speed, Lenkwinkel, Bremse)
- `github.com/berumiya/CAN_DBC_6thGenMazda` – vollständiges DBC-File (HS-CAN + MS-CAN) für ND1/ND2, Community-reverse-engineered, CC-BY-4.0
- `RCP-ND-v9.rcp` (lokale Datei, RaceCapture/Track-MK2-Konfig für den ND) – 4 CAN-Kanäle inkl. Bremsdruck-Rollover-Algorithmus (Lua), 3 Standard-OBD-PIDs, siehe unten
- Autosport Labs Forum ("ND Miata CAN mapping – First pass" etc.) – Lenkwinkel-Offset pro Modelljahr, Bremsdruck-Rollover-Bug unabhängig bestätigt (Threads Cloudflare-blockiert, nur Suchergebnis-Snippets ausgewertet)
- `madox.net/blog/projects/mazda-can-bus/` – andere/ältere Mazda-CAN-Generation (RX-8/Mazda3 BK), IDs nicht übertragbar, aber Denkanstoß für Byte 6-7 von `0x202` (siehe unten)

**Erfolgreich dekodiert und im Log plausibel bestätigt:**
- `0x202` (PCM): Drehzahl (0.25·raw), Geschwindigkeit, Gaspedal
- `0x050` (SSU): Zündungsstatus (KeyState: 0=OFF,1=ACC,2=ON,3=START) – bildet die komplette Session-Timeline sauber ab
- `0x165` (PCM): Gangwahl/-position (zeigte korrekt "Neutral")
- `0x166` (PCM): Bremskraftverstärkerdruck, Luftdruck (~101,7 kPa – realistisch)
- `0x0FD` (PCM): MAP-Sensor (Saugrohrdruck, 22–102 kPa – realistisch)
- `0x215` (ABS): Radgeschwindigkeiten (alle 0 km/h, Auto stand)
- `0x420`, `0x4FA`, `0x325`: Kühlmittel-/Ansauglufttemperatur, Vakuumsensor – plausibel
- `0x25E`, `0x09F`, `0x43E`, `0x09A`: Anzeige-Geschwindigkeit, Parkbremse, Türstatus, Licht/Blinker – plausibel

**Noch unklar / unplausibel, braucht gezielten Test:**
- Lenkwinkel (`0x082`, `0x086`) – auch nach DBC-Decode unplausible Range (0x82: -5.3..1676.7, 0x86: 15816..48768 "raw counts"), da im Log nie gezielt gelenkt wurde. Laut Autosport-Labs-Forum ist die Formel modelljahr-abhängig (ein "adder"-Offset: 331.6 bei 2019er, 328.6 bei 2016er ND) – unser eigener Offset muss per Lenkradschwenk-Test bestimmt werden.
- Außentemperatur (`0x420` AmbientTemp) – Rohwert ~10368-12567, Skalierung im DBC weiterhin nicht plausibel
- Tankfüllstand (`0x09E` `Fuel_Tank`) – Rohwert 0-21, für ~vollen Tank (45l) zu niedrig, DBC-Skala vermutlich falsch. **Einfacherer Ausweg gefunden** (siehe unten): Standard-OBD-Mode-1-PID 0x2F liefert Tankfüllstand direkt, keine Mazda-Reverse-Engineering nötig.

**NEU gefunden (2026-09-11, DBC-Decode):**
- **`0x43E` DoorLeft/DoorRight vertauscht:** Im gesamten 76s-Log (linke Tür laut Test durchgehend offen) zeigt der DBC-Kanal `DoorLeft`=`Closed` und `DoorRight`=`Ajar` konstant. Bit-Layout ist `DoorRight`=Bit37, `DoorLeft`=Bit36 (direkt benachbart, klassischer RE-Vertauschungsfehler) → **Labels im Community-DBC sind mit hoher Sicherheit vertauscht**, physikalisch Bit37=links, Bit36=rechts. Beim Verwenden dieses DBC-Kanals umbenennen/spiegeln.
- **`KeyStateInv`** (0x050, zweites Byte) läuft invers zu `KeyState` mit, aber fällt bei manchen Frames auf reine Zahlencodes (`'1'`,`'2'`) statt Enum-Namen zurück – evtl. Redundanz-/Checksummen-Byte, nicht weiter untersucht.

## Bremsdruck-Rollover gelöst (2026-09-11, aus RaceCapture-Config `RCP-ND-v9.rcp`)
Eine gefundene RaceCapture/Track-MK2-Konfigfile für den ND enthält ein fertiges Lua-Script, das CAN-ID `120` (`0x78`, Byte-Offset 4, 2 Byte, big-endian) als Bremskanal dekodiert:

```
brake% = raw*60/65536 - 36.272278    (auf 0 geclampt)
```

Der eigentliche Fund ist die **Rollover-Korrektur**: der 16-bit-Rohwert läuft bei starkem Bremsen über/unter. Das Script erkennt Sprünge >30000 Counts zwischen aufeinanderfolgenden Samples und korrigiert mit +65535 (einfacher Wrap) bzw. +131070 (doppelter Wrap), bevor skaliert wird. Das deckt sich mit einem unabhängigen Autosport-Labs-Forenpost ("Bremswert läuft von 90 auf negativ über"). Unsere DBC dekodiert denselben Byte-Bereich naiv linear (`BrakePressure`, `raw*0.001-40` bar, **ohne** Rollover-Fix) → beim nächsten Bremstest diese Rollover-Logik statt der DBC-Formel verwenden.

**Weitere DBC-Bug-Bestätigung:** `Longi_Acc_Corr` (0x78, Byte 0) und `Lateral_Acc_Corr_maybe` (0x79, Byte 0) haben in der DBC identische Skalierungskonstanten (`0.000512295`, `-29.2787`) – für zwei unabhängig reverse-engineerte, physikalisch verschiedene Signale (Längs- vs. Querbeschleunigung) praktisch ausgeschlossen. Vermutlich copy-paste im Community-DBC, erklärt die identischen Min/Max/Mean-Werte im Log. Mindestens einer der beiden Kanäle ist mit hoher Wahrscheinlichkeit falsch/unverifiziert.

**Standard-OBD-Mode-1-PIDs bestätigt funktionsfähig** (aus derselben RCP-Datei, `obd2Cfg.pids`): EngineTemp (PID 0x05), IAT (PID 0x0F), **FuelLevel (PID 0x2F)** – alles normale SAE-J1979-Standard-PIDs (kein Mazda-Mode-22 nötig), Request auf `0x7E0`, Response `0x7E8`, Antwortformat `LL 41 <PID> <Data>` (Offset 3 im Frame). Für den Pi umsetzbar ohne die unsichere Mode-22-PID-Suche von letztem Mal.

**Cross-Validierung eigener DBC-Skalierung** gegen die RCP-Config (unabhängige Quelle): Gaspedal exakt identisch (`raw*0.0015625`), Speed sehr nah (0.01 km/h/count vs. 1/160 mph/count, ~0,6% Differenz), RPM nah (0.25 vs. 0.2525) – bestätigt, dass unsere DBC-Werte für diese drei Kanäle stimmen.

## Kandidat für Motorlast/Drehmoment (2026-09-11, aus madox.net-Blog-Inspiration)
Ein Blogpost (madox.net) zu einer **älteren** Mazda-CAN-Generation (RX-8/Mazda3 BK, andere CAN-IDs, nicht direkt übertragbar) vermutet in einem undokumentierten Byte-Paar neben RPM ein Drehmoment-Delta. Das brachte uns dazu, das ND-Äquivalent zu prüfen: `0x202` (HS_PCM) ist 8 Byte groß, unsere DBC nutzt nur Byte 0-5 (RPM, Speed, Gas) – **Byte 6-7 sind undokumentiert und NICHT konstant** (240 verschiedene Werte im Log). Verhalten im Log:
- Zündung an, Motor aus: konstant 6547
- Anlasser-Phase: springt auf ~6593, deutlich höhere Streuung (Kurbel-Transiente)
- Leerlauf/Warmlauf: sinkt allmählich auf ~6414

Korrelation mit RPM nur 0,28 (bewegt sich also eigenständig, nicht einfach ein RPM-Derivat) – passt zum Muster einer Last-/Drehmoment-/Zündwinkel-Schätzung. Skalierung/Bedeutung aus einem Stand-Log nicht bestimmbar. **Zu testen:** Log mit variabler Last (Gasstöße bei verschiedenen Drehzahlen), prüfen ob sich Byte 6-7 sinnvoll gegen Gaspedal/RPM fitten lässt – wäre ein Broadcast-Ersatz für das nicht-broadcastete `ActualEnginePercentTorque`.

## DBC-Integration (2026-09-11)
Community-DBC (`berumiya/CAN_DBC_6thGenMazda`, CC-BY-4.0) nach `data/can/` geladen und dekodiert – deckt 98 von 101 im Log vorkommenden CAN-IDs ab (vorher nur ~15 Signale von Hand). Nicht im DBC: `0x344`, `0x438`, `0x491`.

- Dateien: `data/can/MX5ND_6thGenMazda_HSCAN.dbc`, `..._MSCAN.dbc` (lokal minimal gepatcht, Original-Repo hat 3 DBC-Syntaxfehler: Bindestrich in Message-Name, rein numerische Signalnamen, eingebettetes "LICENSE"-Pseudo-Frame mit ungültiger 29-bit-ID)
- **Wichtig:** HS-CAN- und MS-CAN-DBC dürfen NICHT in dieselbe cantools-Datenbank gemerged werden – CAN-IDs sind pro Bus vergeben und überschneiden sich (MS-CAN-Platzhalter ohne Signale haben sonst echte HS-CAN-Messages verdeckt). Wir loggen nur `can0` = HS-CAN, also nur `MX5ND_6thGenMazda_HSCAN.dbc` laden.
- Parser/Decoder: `scripts/can_log_parser.py <candump.log>` → Long-Format-CSV (`t, can_id, message, signal, value`) in `data/can/`

## Erste echte Fahrt: candump-2026-09-11_202803 (9,5 Min, bis 151 km/h)
Erster Log über den vollautomatischen Pi-Unterbau (KeyState-Trigger, siehe unten), inkl. begleitendem GPS-Track (`20260911-202812.gpx`, BasicAirData GPS Logger, Handy separat da CAN-Adapter den OBD-Port belegt). Uhren von Pi und Handy stimmten ohne Korrektur überein (CAN-Start 18:28:03 UTC, GPS-Start 18:28:12 UTC, beide Enden <4s auseinander).

**Bestätigt/direkt nutzbar:** RPM/Speed/Gas/Gang, `0x75`/`0x76` (RCM, roh) Quer-/Längsbeschleunigung + Gierrate plausibel für spirited driving (Querbeschl. bis 0,78g), einzelne Radgeschwindigkeiten sauber bis 151 km/h.

**Widerlegt:** Byte 6-7 von `0x202` ist **kein** Drehmoment/Last-Signal – bei echter Fahrt Korrelation mit Gas/RPM praktisch Null (r=0,02/0,06), Mittelwert bei Vollgas (6525) und Leerlauf (6501) fast identisch. Kandidat gestrichen.

**GPS-Kreuzcheck (Gierrate):** grobe GPS-Gierrate (Peilungsänderung, 1Hz) korreliert nach Vorzeichenkorrektur mit `0x75 YawRate_Raw` (r=+0,30) – CAN nutzt SAE-Konvention (+ = links/gegen Uhrzeigersinn), GPS-Peilung zählt im Uhrzeigersinn positiv. Größte Peaks beider Quellen fallen auf dieselbe Sekunde.

**Lenkwinkel-Nullpunkt via OSM (analog `steering_zero_offset.py`, gleicher gecachter Korridor):** 14 bestätigte Geradeausfahrt-Fenster (262s), Median-Offset = **0,0°** – anders als bei `STEER_ANGL_EPS` (OBD, dort +148°/+20° Offset in anderen Logs) ist `0x82 Steering_Wheel_Absolute_Angle` bereits korrekt genullt, keine Korrektur nötig. Skript: `scripts/can_gps_yawrate_and_steering_offset.py`.

**Kupplung – Richtung bekannt, absolute Skala noch NICHT verifiziert (`Clutch_Related`, 0x165):**
- **Richtung gesichert:** höherer Rohwert = mehr ausgekuppelt. `23` ist mit hoher Sicherheit "ganz eingekuppelt" (62% aller Samples über die ganze Fahrt, konstanter Cruise-Wert).
- **Frühere Annahme "43 = ganz durchgetreten/Bodenanschlag" war falsch und wurde zurückgenommen.** Begründung (User-Einwand, physikalisch stichhaltig): die alten OBD-Logs haben einen echten, Mazda-skalierten 0-100%-Kanal `CPP_PER_MZ` ("Clutch Pedal Position", siehe `build_datalake.py`/`partial_load_model.py`), aus dem bekannt ist, dass die Kupplung bei ca. **55%** zu greifen beginnt. Mit nur ~15% der vermeintlichen 23-43-Skala (der Wert `26`, kurz vor beiden Ampel-Anfahrten beobachtet) könnte man realistischerweise weder einen Gang einlegen noch anfahren, ohne abzuwürgen.
- Richtiger Schluss: `StarterInterLockSW` ist ein Sicherheitsschalter, der typischerweise mit Marge *jenseits* des Greifpunkts auslöst, nicht zwingend am mechanischen Anschlag. Der in diesem Log beobachtete Rohwertbereich (23-43) ist also vermutlich nur ein schmaler Ausschnitt nahe dem oberen (eingekuppelten) Ende der wirklichen Pedal-Range, keine vollständige 0-100%-Spanne.
- **Für eine echte %-Kalibrierung fehlt ein Log mit gleichzeitig OBD-Fusion (`CPP_PER_MZ`) UND CAN** – genau der Y-Splitter-Ansatz aus der Unterbau-Diskussion (OBD-Fusion + Pi gleichzeitig am selben Bus). Damit ließe sich `Clutch_Related` direkt gegen die echte %-Skala regressieren.
- Schaltvorgänge und beide Ampel-Anfahrten zeigen reproduzierbar einen kurzen Anstieg auf `26`, gehalten bis kurz nach Bewegungsbeginn, dann Abfall zurück auf 23 – als *relative* Beobachtung (Kupplung wird betätigt) weiterhin gültig, nur die absolute %-Aussage dazu nicht.
- **Methodisches Muster, weiterhin gültig:** nach technisch erzwungenen Referenzpunkten suchen statt auf Fahrererinnerung zu vertrauen (das Interlock hat trotzdem die Richtung und den 23er-Anker bestätigt) – aber jeden Referenzpunkt selbst auf Physik gegenprüfen, bevor er als Kalibrierung gilt.

**Kupplungspedal identifiziert und ins DBC nachgetragen (2026-09-11):** systematische Suche in allen noch unzugeordneten Bytes (83 komplett leere + Rest-Bytes der 32 teilweise dekodierten Messages) nach einem Kandidaten, der mit der bekannten `Clutch_Related`-Trajektorie während des einzigen vollen Anfahrvorgangs korreliert (|r|>0,85) UND im ruhigen Cruise-Fenster (kein Kupplungseinsatz) nahezu konstant bleibt. Ein einziger Treffer: **`0x130` Byte 7** (`HS_PCM`), Korrelation +0,947, Wertebereich **0-199 über die komplette Fahrt** (viel vollständiger als `Clutch_Related`s schmale 23-43-Spanne).

- 0 = ganz eingekuppelt, ~199 = ganz ausgekuppelt (Vollbereich, nicht nur ein Ausschnitt)
- Beide bekannten Schaltvorgänge zeigen jetzt saubere Dreieckspulse 0→199→0 in 250-500ms (bei `Clutch_Related` nur ein kleiner Ausschlag) – erklärt, warum die alte Interpretation falsch wirkte: die Kupplung geht bei normalen Schaltungen tatsächlich ganz durch, `Clutch_Related` konnte das nur nicht zeigen.
- **Bisspunkt-Gegenprobe über RPM-Verhalten:** während des vollen Anfahrvorgangs sinkt die Drehzahl (Reibphase, RPM wird Richtung Raddrehzahl "eingefangen") exakt im Bereich raw≈114-123, das entspricht **~59% Pedalweg getreten** – nah am unabhängig bekannten **~55%-Bisspunkt aus den alten OBD-Logs** (`CPP_PER_MZ`). Zwei komplett unabhängige Quellen (CAN-Rohbyte-Suche vs. OBD-Referenzkanal) bestätigen sich gegenseitig.
- Eingetragen in `data/can/MX5ND_6thGenMazda_HSCAN.dbc` als `Clutch_Pedal_Position_raw` (BO_ 304/0x130), mit Kommentar zur Herleitung. **Lineare 0-100%-Skalierung plausibel, aber nicht durch Referenzmessung (Y-Splitter-Log) bestätigt** – bleibt als offener Punkt markiert.
- Methodik (skript-fähig, falls für andere unklare Signale gebraucht): bekanntes Referenzsignal/-verhalten in einem Zeitfenster nehmen, alle unzugeordneten Bytes/2-Byte-Felder aller Messages im selben Fenster auf Korrelation prüfen, mit einem ruhigen Gegenfenster ohne das gesuchte Verhalten filtern.

**MassAirFlowRate – Kandidat, NICHT verifiziert (2026-09-11):** `0x167` Byte 4 korreliert mit Gas (+0,89). Physik-Gegenprobe versucht (MAF sollte nach dem Speed-Density-Prinzip eher mit RPM×MAP als mit MAP allein korrelieren): Korrelation mit MAP allein (+0,87) ist aber **stärker** als mit RPM×MAP (+0,82) – untypisch für echten MAF, eher ein Hinweis, dass der Kandidat näher an MAP selbst liegt als an einem eigenständigen Luftmassenstrom-Signal. Kein OBD-Referenzkanal für MAP vorhanden, daher keine echte Gegenprobe möglich. Trotzdem als `MassAirFlowRate_maybe` (BO_ 359/0x167) ins DBC eingetragen, damit es beim nächsten Log automatisch mitdekodiert wird – **explizit als unsicher markiert**, braucht weitere Analyse (z.B. mit echtem OBD-MAF-Log parallel).

**CommandEquivalenceRatio, KnockingRetard, TimingAdvance, ActualValveTiming – kein Kandidat gefunden:** gezielte Suche nach dem aus alten OBD-Daten bekannten Muster (Einbruch bei Vollgas Richtung ~0,9, Sprung auf Sentinel-Wert ~2,0 bei Schubabschaltung) ergab keinen überzeugenden Treffer, der beide Kriterien erfüllt. Plausibelste Erklärung: eng verwandt mit Lambda/AFR_MZ und ActualEnginePercentTorque, die schon als nicht-broadcastet (nur Mode-22-Polling) bekannt sind – vermutlich ebenfalls nicht auf HS-CAN vorhanden.

**Bremse (`0x78`) weiterhin ungeklärt:** in dieser Fahrt kein einziger Rollover-Event aufgetreten – die %-Formel (RaceCapture) und die naive DBC-bar-Formel sind für dieses Log nur unterscheidbare Skalierungen derselben Rohwerte, nicht verifizierbar, welche stimmt. Braucht weiterhin einen Vollbrems-Test.

**Visualisierung:** `Rechtskurve → Vollgas-Beschleunigung` (t=249-282s, 151 km/h Peak) als interaktives HTML-Artefakt mit 6 synchronisierten Kanälen gebaut (Speed/RPM/Gas/Gierrate/Querbeschl./Lenkwinkel).

## Systematisches Reverse-Engineering unbekannter Signale (2026-09-11, zweiter Durchlauf)

Vollständiger Bericht: `data/can/CAN_unbekannte_signale_bericht_2026-09-11.md`, erweiterte
DBC: `data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc`, unaufgelöste IDs:
`results/can_unresolved_ids.csv`. Methodik: DBC-Abgleich, Ereigniserkennung (37 Schaltvorgänge,
2 Vollgas-Phasen, 3 harte Bremsungen, 2 Standstills automatisch erkannt), Ganz-Log-Korrelation
aller offenen Bytes gegen 32 bekannte Anker (20Hz-Raster, ±0,3s Lag-Suche) mit anschließender
Trendbereinigungs-Gegenprobe (gleitender Median entfernt, um Schein-Korrelationen durch
gemeinsamen langsamen Drift über die 9,5-Minuten-Fahrt auszuschließen), Web-Recherche, Heuristiken.

**Wichtigste Funde:**
- **`Longi_Acc_Corr`/`Lateral_Acc_Corr_maybe` (0x78/0x79, HS_ABS) bestätigt fehlerhaft**: Werte bis
  -25g im echten Fahr-Log. Mehrere Kandidaten, die stark damit korrelierten, verschwinden komplett
  (r→0) gegen den sauberen RCM-Kanal (`Longitudinal_Acc_Raw`, 0x76) – reines Artefakt der
  Rollover-/Skalierungs-Ausreißer. **Für g-Kraft-Analysen künftig 0x75/0x76 (RCM) statt 0x78/0x79
  (ABS) nutzen.**
- Neue redundante Signale gefunden: **0x217 Byte4-5 BE = Fahrzeuggeschwindigkeit** (raw*0.01 km/h,
  r=1.000 vs. 0x202), **0x130 Byte5-6 BE ≈ Drehzahl*2** (r=0.9999/0.845 trendbereinigt),
  **0x166 Byte0 ≈ Kupplungsweg*0.8** (r=0.9987/0.998 trendbereinigt) – alle drei "wahrscheinlich".
- **Bremspedal-% gefunden** (0x78, Bit 27|12 MSB-first) über Web-Quelle (`mazda_mx5_nd.md`,
  RaceChrono-Formel) bestätigt, r=0.998 gegen die schon vorhandene BrakePressure-Dekodierung –
  überlappt aber bitweise mit BrakePressure selbst (kein eigenständiges DBC-Signal möglich, nur
  als Formel dokumentiert).
- Web bestätigt zwei bestehende `_maybe`-Signale: `Reverse_Flag_maybe` (0x9F) und
  `RoofGraphicStatus` (0x472) via `mx5things.blog`.
- **Rollzähler-Muster** (4-Bit, >93% +1 mod 256) in Byte6 mehrerer Botschaften gefunden
  (0x217, 0x21e, 0x325, 0x344) sowie Byte3 von 0x91 – strukturell, kein Messwert.
- **0x438 (nicht in DBC)** sendet konstant den ASCII-String "D01690QK" (vermutlich Teilenummer),
  **0x491 (nicht in DBC)** ist ein reiner Heartbeat (immer 0x00…00), mehrere Botschaften
  (0x581/0x588/0x591) senden ein 0xAA/0x55-Füllmuster.
- Ein vermeintlicher Bremslichtschalter-Fund (0x43e Byte6) wurde **verworfen**: die Korrelation
  stammt vom bereits bekannten `ROOM_FUSE`-Bit im selben Byte, nicht von einem neuen Signal.
- Die im Fahr-Log gefundene 0x7xx-ID-Gruppe (~17 IDs, nur einmalig bei t≈17s) ist ein
  UDS-Diagnose-Sweep (Service 0x19 ReadDTCInformation) kurz nach Zündung ein, nicht
  fahrdynamik-relevant.
- **Methodischer Fallstrick bestätigt**: Korrelationen gegen `AmbientTemp`/`CoolantTemp` sind bei
  Einzelfahrten fast immer Trend-Artefakte (gemeinsamer langsamer Drift), keine echte Kopplung –
  Gegenprobe mit Trendbereinigung künftig Standard bei jeder Kanal-Identifikation per Korrelation.

## Testplan für gezielte Verifikationsfahrten (2026-09-12)

Vollständiger Maßnahmenkatalog (Y-Splitter, Standtests, Fahrmanöver, Reihenfolge-Empfehlung)
in `data/can/test_plan_2026-09-12.md`. Y-Kabel für OBD-Fusion+CAN gleichzeitig ist bestellt,
noch nicht geliefert – Standtests aus Abschnitt B sind schon vorher möglich (dafür ist jetzt
der Testmodus auf dem Pi-Display gebaut, siehe unten).

## Pi-Touchdisplay-GUI: Testmodus, Live-Werte, Pedal-Gauges (2026-09-12)

`scripts/status_gui.py` wurde stark erweitert (599 Zeilen, war ursprünglich ein reines
Status-Label). Lokale Datei ist die Quelle der Wahrheit; Deployment auf den Pi ist ein reiner
Datei-Kopiervorgang (siehe Abschnitt "Kommunikation mit dem Pi" unten) – **nach jeder Änderung
an der lokalen Datei erneut auf den Pi kopieren und den Prozess neu starten**, sonst läuft dort
weiter die alte Version.

**Neue Bausteine (alle in `status_gui.py`):**
- **Testmodus/Checkliste** (`ChecklistGui`, Konstante `CHECKLIST`): 30-Schritte-Checkliste
  exakt nach Testplan-Abschnitt B (Lenkrad, Blinker, Licht, Wischer, Türen, Kofferraum,
  Handbremse, Rückwärtsgang, Kupplung in 5 Stufen). Erscheint per Knopf "Testmodus starten",
  der nur sichtbar ist, wenn der Status "LOGGING LÄUFT" zeigt. Pro Schritt: **Start**-Tap
  markiert Beginn, **Fertig**-Tap markiert Ende und protokolliert beides als Unix-Epoch-
  Zeitstempel (`_write_row`) in `LOG_DIR/actions-<Datum_Zeit>.log` (CSV: step, phase,
  t_start_epoch, t_end_epoch, t_start_local, t_end_local). Abgleich mit dem CAN-Log läuft rein
  über die gemeinsame absolute Unix-Zeit (candump-Zeilen sind schon Epoch) – keine Kopplung an
  Dateinamen nötig. Zusätzlich Zurück/Überspringen/Test-beenden-Buttons.
- **Doppel-Tap-Fenstermodus** (`FullscreenToggler`): Doppel-Tap auf einen Hintergrund-Bereich
  (nicht auf einen Button, damit schnelles Start/Fertig-Tippen nichts auslöst) wechselt
  zwischen Vollbild und Fenstermodus. Zustand wird selbst in Python getrackt (nicht bei Tk
  abgefragt – `root.attributes("-fullscreen")` liefert je nach Fensterumgebung keinen
  verlässlichen Wert zurück).
- **Live-CAN-Werte** (`LiveCanValues`): Hintergrund-Thread liest direkt vom CAN-Socket
  (`python-can`, `interface="socketcan"`) **parallel** zu candump (SocketCAN erlaubt beliebig
  viele gleichzeitige Leser, kein Konflikt) und dekodiert mit `cantools` gegen die erweiterte
  DBC (Fallback auf die Basis-DBC, siehe `DBC_CANDIDATES`). Hält nur den jeweils letzten Wert +
  Zeitstempel pro (CAN-ID, Signal) vor, kein Verlauf nötig. Reconnect-Loop mit 2s-Backoff, falls
  `can0` (noch) nicht existiert.
- **Textwerte-Panel** (`LiveValuesPanel`, Liste `LIVE_SIGNALS`): 2-spaltige kompakte
  Live-Anzeige für alle übrigen Kanäle (Zündung, Drehzahl, Gang, Blinker, Licht, Wischer, Türen,
  Kofferraum, Parkbremse, Rückwärtsgang, plus die 5 neuen Kandidatensignale aus dem
  CAN-Bericht). Werte älter als `LIVE_STALE_S` (2s) werden ausgegraut.
- **Pedal-/Lenkwinkel-Gauges** (`BarGauge`, `PedalGaugesPanel`, Konstante `PEDAL_GAUGES`):
  grafische Balken für Gas, Bremse, Kupplung, Lenkwinkel, Speed – siehe unten.
  - Gas (blau, 0x202/514 `APP_Accelerator_Pedal_Position`, 0–100%)
  - Bremse (orange, 0–100%) – **kein echtes DBC-Signal**: die RaceChrono-Bremspedal-%-Formel aus
    dem CAN-Bericht überlappt bitweise mit `BrakePressure` und kann deshalb nicht als `SG_`
    existieren. Wird stattdessen live per Hand aus den Rohbytes von 0x78 berechnet
    (`extract_brake_pct`, Bit 27\|12 motorola).
  - Kupplung (aqua/grün, 0x130/304 `Clutch_Pedal_Position_raw`, 0–199 auf 0–100% normiert)
  - Lenkwinkel (gelb, 0x82/130 `Steering_Wheel_Absolute_Angle`, bidirektional, Skala ±480°,
    Balken füllt von der Mitte)
  - Speed (violett, 0x202/514 `VehicleSpeed`, 0–220 km/h)
  - Redundante Textzeilen für diese 5 Kanäle wurden aus `LIVE_SIGNALS` entfernt (nicht doppelt
    anzeigen). Rendering ist reines Tkinter-Canvas (2-3 Primitive pro Gauge, billig).
- **Env-Var-Overrides** (analog zu `session_logger.py`): `MX5_LOG_DIR`, `MX5_CAN_CHANNEL`
  (Default `can0`, für Tests z.B. `vcan0`), `MX5_DBC` (expliziter Pfad, sonst
  `DBC_CANDIDATES`-Fallback-Liste). Nur für Demo/Test gedacht, nicht persistiert:
  `MX5_SIMULATE_STATE` (`LOGGING`/`WAITING_IGNITION`/`WAITING_CAN`/`ERROR` – erzwingt einen
  Anzeigezustand ohne echten CAN-Bus) und `MX5_START_IN_TESTMODE=1` (startet direkt im
  Testmodus-Screen statt im Status-Screen).

**Lasttests (Raspberry Pi 4B, 4 Kerne, 1.8GiB RAM) – siehe Methodik unten:**

| | ohne Gauges | mit Gauges (Gas/Bremse/Kupplung/Lenkwinkel/Speed) |
|---|---|---|
| GUI-CPU Ø (1 Kern=100%) | 10,1% | 12,1% |
| GUI-CPU Max | 30% (einmalig) | 30% (einmalig) |
| RSS-Speicher | konstant 43.816 KB, kein Leck | konstant 45.012 KB, kein Leck |
| Load-Average (4 Kerne) | 1,4–2,2 | 0,9–2,3 |

Beide Tests liefen über den **kompletten** 567s-Fahrt-Log-Replay (1,45 Mio. Frames), nicht nur
eine Stichprobe – Ergebnis ist auch bei dichtestem echten Verkehr (Vollgas, Schaltvorgänge)
stabil. Kein Speicherwachstum über die volle Laufzeit in beiden Fällen.

## Kommunikation mit dem Pi (Referenz für künftige Sessions)

Der Pi (Hostname `car`, seit 2026-09-13, davor `ras8`) ist per SSH erreichbar unter
**`pi@192.168.0.247`** (auch `car.local`) – Passwort-loses SSH UND
passwort-loses `sudo` funktionieren direkt, keine weitere Authentifizierung nötig.

**WICHTIG (Overlayroot!):** Nur `/home/pi/canlogs` (der USB-Stick) ist persistent. Die
SD-Karten-Root ist schreibgeschützt + RAM-Overlay (siehe Abschnitt "Pi-Infrastruktur" oben) –
`/tmp` liegt ebenfalls auf dem RAM-Overlay (`overlayroot`-Dateisystem), Schreibvorgänge dort
verbrauchen also echtes RAM und werden bei Reboot verworfen. Für Tests ok, aber große Dateien
in `/tmp` (z.B. entpackte Logs) nach Gebrauch wieder löschen.

**Deployment (Code/DBC-Änderungen auf den Pi bringen):**
```bash
scp scripts/status_gui.py pi@192.168.0.247:/home/pi/canlogs/status_gui.py
scp data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc pi@192.168.0.247:/home/pi/canlogs/
scp data/can/MX5ND_6thGenMazda_HSCAN.dbc pi@192.168.0.247:/home/pi/canlogs/
```
Kein Overlay-Disable/Enable-Reboot-Tanz nötig, da `/home/pi/canlogs` der echte Stick-Mount ist.

**GUI-Prozess neu starten** (Python lädt Code nicht automatisch neu, nach jedem Deployment
nötig):
```bash
ssh pi@192.168.0.247 "pgrep -af status_gui.py"   # aktuelle PID herausfinden
ssh pi@192.168.0.247 "kill <PID>; sleep 1; DISPLAY=:0 setsid nohup /home/pi/canlogs/venv/bin/python3 /home/pi/canlogs/status_gui.py >/home/pi/canlogs/status_gui.out 2>&1 < /dev/null & disown"
```
Normalerweise übernimmt das der `~/.config/labwc/autostart` beim Boot/Session-Neustart
automatisch; der manuelle Neustart ist nur für Live-Iteration ohne Reboot.

**Screenshot vom echten Display machen** (Wayland/labwc, `grim` ist installiert):
```bash
ssh pi@192.168.0.247 "XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim /tmp/screenshot.png"
scp pi@192.168.0.247:/tmp/screenshot.png ./screenshot.png
```

**Ohne echten CAN-Adapter testen (vcan0 + synthetische Frames):**
```bash
ssh pi@192.168.0.247 "sudo modprobe vcan; sudo ip link add dev vcan0 type vcan; sudo ip link set vcan0 up"
# GUI mit Simulations-Flags gegen vcan0 starten:
ssh pi@192.168.0.247 "MX5_SIMULATE_STATE=LOGGING MX5_CAN_CHANNEL=vcan0 DISPLAY=:0 setsid nohup /home/pi/canlogs/venv/bin/python3 /home/pi/canlogs/status_gui.py >/home/pi/canlogs/status_gui.out 2>&1 < /dev/null & disown"
# einzelne Testframes injizieren (cansend braucht die CAN-ID als GENAU 3 Hex-Zeichen, also z.B. "078" nicht "78"!):
ssh pi@192.168.0.247 "cansend vcan0 202#2EE01F4096000000"   # RPM=3000, Speed=80, Gas=60%
# aufräumen danach:
ssh pi@192.168.0.247 "sudo ip link delete vcan0"
```

**Realistischen Lasttest mit echtem Log-Replay fahren** (für Performance-Checks bei
Code-Änderungen an `LiveCanValues`/den Gauges):
```bash
ssh pi@192.168.0.247 "gunzip -k -c /home/pi/canlogs/candump-2026-09-11_202803.log.gz > /tmp/replay.log"
scp scripts/monitor_load.sh pi@192.168.0.247:/tmp/monitor_load.sh   # liegt nur lokal im Projekt, jedes Mal frisch aufs (RAM-)/tmp kopieren
ssh pi@192.168.0.247 "chmod +x /tmp/monitor_load.sh"
ssh pi@192.168.0.247 "pgrep -af status_gui.py"   # GUI-PID fuer den Monitor-Aufruf ermitteln
ssh pi@192.168.0.247 "nohup /tmp/monitor_load.sh <GUI_PID> 580 /tmp/load.csv > /tmp/monitor.out 2>&1 & echo \$! > /tmp/monitor.pid"
ssh pi@192.168.0.247 "nohup canplayer -I /tmp/replay.log vcan0=can0 > /tmp/canplayer.out 2>&1 & echo \$! > /tmp/canplayer.pid"
# CSV-Spalten: t, loadavg1 (System, alle Kerne), gui_cpu_pct (Anteil EINES Kerns,
# 100%=1 Kern voll ausgelastet), gui_rss_kb. Nach Abschluss auswerten (z.B. min/avg/max
# per Python/pandas) und mit den Referenzwerten oben vergleichen.
```
Nach jedem Test: `canplayer`/`monitor`-Prozesse killen, `/tmp/replay.log` löschen, `vcan0`
löschen, GUI-Prozess ohne Simulations-Flags neu starten (siehe oben), übrig gebliebene
`actions-*.log`-Testdateien in `/home/pi/canlogs` löschen.

## Übersicht: alle gesicherten CAN-Signale (2026-09-12)

Vollständige Liste aller **gesicherten** Signale (unmarkierte DBC-`SG_`-Einträge, keine
`_maybe`/`_related`-Kandidaten) aus `MX5ND_6thGenMazda_HSCAN.dbc`, nach Funktion gruppiert.
✓ = bereits als eigener Kanal im Datalake (`data/datalake.duckdb`, siehe Abschnitt oben),
⚠ = trotz unmarkierter DBC-Definition durch eigene Analyse als fehlerhaft/unplausibel
identifiziert - nicht blind vertrauen.

**Antrieb/Motor:** EngineRPM✓ (rpm), APP_Accelerator_Pedal_Position✓ (%, Gaspedal),
CoolantTemp✓ (°C), IAT_Sensor_No1✓ (°C), MAP_Manifold_absolute_pressure_sensor (kPa,
Saugrohrdruck), VS1_Vaccum_Sensor_1 (kPa, Vakuum), MT_Gear_Actual✓ (0-7, Gang),
MT_Gear_Position/MT_Gear_Select/MT_Gear_Recommend (weitere Getriebe-Rohsignale),
Clutch_Pedal_Position_raw✓ (0-199 roh, Kupplungspedalweg), Fuel_Tank✓
(als `FuelTank_CAN_raw`, roh 0x09E - Kalibrierung `FLI% ≈ 2,486·raw-0,02` per
Y-Splitter-Log bestätigt, siehe oben, Umrechnung bewusst noch nicht im Datalake
angewendet, erst nach weiteren Kalibrierfahrten).

**Fahrdynamik (IMU, RCM):** Longitudinal_Acc_Raw✓ (G), Lateral_Acc_Raw✓ (G),
YawRate_Raw✓ (deg/s). ⚠ Longi_Acc_Corr/YawRate_Corr/Lateral_Acc_Corr_maybe (HS_ABS,
0x78/0x79) - nachweislich fehlerhaft skaliert (Ausreißer bis -25g), RCM-Werte oben nutzen,
NICHT diese. ⚠ AmbientTemp - Skala unplausibel, ungeklärt.

**Bremse/ABS:** BrakePressure✓ (bar, ⚠ bekannter Rollover-Bug bei starkem Bremsen, in der
bisherigen Fahrt nicht ausgelöst), BBP_Brake_Booster_Pressure_2/BARO_Barometric_pressure
(kPa), WheelSpeed_1-4✓ (km/h, einzelne Radgeschwindigkeiten, Sentinel-Ausreißer 0xFFFF
beim Datalake-Import gefiltert), DSC_Status (nur System an/aus, KEIN
Regelungseingriff - Annahme vom 2026-09-11 war falsch, siehe Korrektur
2026-09-13 unten), VehicleSpeed✓ (km/h).

**Lenkung:** Steering_Wheel_Absolute_Angle✓ (deg) - Nullpunkt per GPS bestätigt, ein
bekannter Einzel-Ausreißer im Fahrt-Log (t≈0,65s).

**Zündung/Fahrzustand:** KeyState✓/KeyStateInv (OFF/ACC/ON/START), StarterInterLockSW
(Anlasssperren-Schalter), Parking_Brake (Handbremse), CC_SetSpeed (km/h, Tempomat-Soll).

**Karosserie/Komfort:** DoorLeft/DoorRight (Labels im Original-DBC vertauscht, hier
korrigiert vermerkt), Trunk, Turn/TURN_SW (Blinker), HAZ_SW (Warnblinker),
Headlight/LIGHT/PanelLight/F_FOG_RELAY/R_FOG_LAMP (Lichtfunktionen),
FrontWiper/RearWiper/Washer/WIP_F_INT (Wischer/Waschanlage), ROOM_FUSE
(Innenraumlicht-Sicherung).

**Fahrerassistenz:** BSM_Left/BSM_Right/BSM_Status/BSM_Warning (Toter-Winkel-Warner),
LDWS_Status1/LDWS_Status2/LDWS_ON_Switch (Spurhalteassistent), SCBS_SBS_Status
(Notbremsassistent), DSC_OFF_Switch/iStop_OFF_Switch (Deaktivierungsschalter),
RoofGraphicStatus (Verdeckstatus, web-bestätigt).

**Sonstiges:** Mileage/Date (HS_CMU, multiplex-kodiert), HUD_Height/HUD_Height_Moving/
HUD_Bright (Head-Up-Display), Key_hold/Key_notfound/Key_Batt/Key_fob/IC_Buzz/INFO_SW/
CRU_CON_SW1 (Schlüssel/Bedienelemente), SpeedUnit/StopVehicle (Anzeigeeinstellungen).

**GPS (begleitender Track, nicht CAN selbst):** Breite✓/Länge✓ (deg), Höhe✓ (m),
GPS-Geschwindigkeit✓ (km/h).

Aktuell im Datalake sind nur die ✓-markierten, durchgehend numerischen Telemetriekanäle
(21 Kanäle, siehe "CAN-Log + GPS-Track in den Datalake integriert" oben) - Schalter/Status-
Signale (Türen, Blinker, Licht, Wischer, Assistenzsysteme etc.) sind zwar genauso gesichert
dekodierbar, aber bisher nicht übernommen (bei Bedarf leicht ergänzbar, siehe
`CAN_SIGNAL_MAP` in `scripts/build_datalake.py`).

## Weitere Referenzen aus dieser Session (2026-09-11/12)

- `data/can/CAN_unbekannte_signale_bericht_2026-09-11.md` – vollständiger Reverse-Engineering-
  Bericht (Methodik, Tabelle neuer/bestätigter Signale, verworfene Hypothesen, unaufgelöste IDs)
- `data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc` – erweiterte DBC (Basis-DBC + neue Funde,
  mit CM_-Kommentaren dokumentiert). **Ist die primäre DBC für `LiveCanValues`** auf dem Pi.
- `results/can_unresolved_ids.csv` – 65 CAN-IDs ohne belastbaren Treffer, zur manuellen Prüfung
- `data/can/test_plan_2026-09-12.md` – Testplan für morgen (Standtests, Fahrmanöver, Y-Splitter)
- Interaktives Artefakt "Pedal- und Lenkkanäle" (Gas/Bremse/Kupplung/Lenkwinkel-Zeitreihen aus
  dem Fahrt-Log, mit Zoom + synchronisiertem Tooltip) – über `/artifacts` bzw. die Konversation
  vom 2026-09-12 auffindbar, nicht im Projektverzeichnis abgelegt (reines Analyse-Artefakt)

## CAN-Log + GPS-Track in den Datalake integriert (2026-09-12)

`scripts/build_datalake.py` liest jetzt zusätzlich zu den OBD-Logs auch CAN-Logs mit
begleitendem GPS-Track ein (`candump-2026-09-11_202803.log` + `20260911-202812.gpx` als
erstes/bisher einziges Paar, Konfig-Liste `CAN_GPS_PAIRS` - neue Paare dort einfach ergänzen,
sobald weitere Fahrten mit CAN+GPS vorliegen). Nutzt die bereits vorhandene
`*_decoded.csv` von `can_log_parser.py` statt neu zu dekodieren. Zeitachse: CAN- und
GPS-Zeitstempel sind beide echte UTC-Epoch-Zeiten (NTP-synchronisiert) - Ausrichtung
also direkt über die absolute Zeit, kein manueller Offset nötig (per Stichprobe verifiziert:
Geschwindigkeit aus CAN vs. GPS zu gemeinsamen Zeitpunkten plausibel im selben Bereich).

**Kanal-Mapping** (`CAN_SIGNAL_MAP` in `build_datalake.py`): nur "sichere" (unmarkierte)
DBC-Signale mit eindeutiger physikalischer Entsprechung zu einem bestehenden OBD-Kanal werden
dorthin gemapped (`EngineRPM`, `VehicleSpeed`, `APP`, `EngineCoolantTemp`,
`IntakeAirTemperature`). Alles andere bekommt einen eigenen, klar mit `_CAN`-Suffix
markierten Namen (`BrakePressure_CAN`, `SteeringAngle_CAN`, `ClutchPosition_CAN_raw`,
`LongitudinalAcc_CAN`, `LateralAcc_CAN`, `YawRate_CAN`, `Gear_CAN`, `WheelSpeed_CAN_1..4`,
`KeyState_CAN`) - bewusst NICHT mit den gleichnamigen OBD-Kanälen (`CPP_PER_MZ`,
`BFP_PRE_MZ`) gleichgesetzt, weil deren CAN-Entsprechungen noch nicht referenzgemessen sind
bzw. einen bekannten Rollover-Bug haben (siehe CAN-Bericht). GPS-Felder (`Breite`, `Länge`,
`Höhe`, `GPS-Geschwindigkeit`) direkt in die bestehenden gleichnamigen Kanäle gemapped
(Einheiten passen: deg/deg/m/km/h).

**Zwei Bugs beim ersten Build gefunden und gefixt:**
- `KeyState` dekodiert (wie jedes Signal mit `VAL_`-Tabelle) per cantools-Default als
  Enum-String ("ACC"/"ON"/...) statt Zahl - hätte beim Zusammenführen mit den rein
  numerischen OBD-Werten die **gesamte** `value`-Spalte der Datenbank zu VARCHAR gemacht
  (inkl. dann falscher lexikographischer statt numerischer MIN/MAX über ALLE 83 Logs!).
  Fix: `KeyState` auf den DBC-Rohcode zurückgemappt, Rest zur Sicherheit hart numerisch
  erzwungen (nicht-numerische Ausreißer werden gedroppt).
- `WheelSpeed_1..4` (HS_ABS): der CAN-Sentinelwert `0xFFFF` ("Sensor ungültig") dekodiert
  nach der DBC-Formel zu genau 555,35 km/h - selten (<0,02% der Samples), aber eindeutig
  kein Messwert. Wird jetzt beim Einlesen rausgefiltert (>300 km/h auf diesen Kanälen).

Nach dem Fix: `WheelSpeed_CAN_1..4` max ~150-151 km/h (deckt sich mit dem dokumentierten
Topspeed), `EngineRPM` max ~7287 rpm, `GPS-Geschwindigkeit` max ~145 km/h (deckt sich mit
der "Max Speed = 145 km/h" aus dem GPX-Header) - alles plausibel. Datalake jetzt 83 Logs,
~45,4 Mio. Messwerte gesamt.

## Standtests durchgeführt (2026-09-12, Testmodus-Checkliste) – Ergebnis: Klima grösstenteils unbrauchbar

Fahrzeug stand, Zündung war während der gesamten Session nur in **ACC** (nicht ON/Motor an,
per `KeyState`-Decode verifiziert: `1`=ACC durchgehend von t=0 bis t=137s, kurz `2`=ON nur für
~6s ganz am Ende). Das widerspricht der Testplan-Vorgabe "Motor an" für Abschnitt B – Konsequenz:
**alle PCM-Botschaften (0x130 Kupplung, 0x165, 0x166) waren während der gesamten Session
komplett still** (0 Frames in den relevanten Zeitfenstern, erst ab t=150s kurz wieder aktiv).

Verwendete Logs: `data/can/2026-09-12/candump-2026-09-12_121333.log` (143s) +
`data/can/2026-09-12/actions-2026-09-12_121005.log` (Checkliste-Zeitstempel, Unix-Epoch,
direkt gegen candump-Zeitstempel abgleichbar, kein Offset nötig – wie schon beim GPS-Abgleich).
Dritte kurze Session (`candump-2026-09-12_121906.log`, 24s, nur `_session_start` in der
zugehörigen Actions-Datei) enthält keine Testschritte, nicht weiter ausgewertet.

**Im Detail pro Schritt:**

- **Kupplung (0/25/50/75/100%, 5 Stufen gehalten):** keine einzige Kupplungs-Botschaft im Log
  während der fünf Fenster (91-117s) – Test hat **keine verwertbaren Daten** geliefert, weil der
  Motor nicht lief. Einziges Signal mit Reaktion: `StarterInterLockSW` (0x50, läuft auch in ACC)
  kippt einmal zusammenhängend von t=113,3s bis t=117,3s (also ab Ende der 75%-Stufe bis Ende der
  100%-Stufe) – bestätigt nur qualitativ nochmal die Richtung/ungefähre Lage des Bisspunkts,
  bringt aber keine neue Kalibrierungsinformation (binäres Signal, kein Ersatz für den
  PCM-Kanal oder den Y-Splitter-Referenzwert).
- **Rückwärtsgang (rein/raus):** `Reverse_Flag_maybe` (0x9F) blieb während der gesamten Session
  konstant `0`, auch im "rein"-Fenster (76,5-80,6s) – keine Bestätigung. Ungeklärt, ob das
  Signal Zündung=ON statt ACC braucht, oder ob der web-bestätigte Kandidat doch nicht stimmt.
  Auffällig: zwei kurze `StarterInterLockSW`-Blips bei t=70,7-72,2s und 73,9-75,4s, beide
  **vor** dem geloggten "rein"-Fenster – passt dazu, dass man zum Einlegen des Rückwärtsgangs
  kurz kuppelt, aber nicht als eigener Checklisten-Schritt erfasst.
- **Handbremse (anziehen/lösen) – KORRIGIERT nach Nachfrage des Nutzers:** ursprünglich als
  "Tap-Timing-Drift" fehlinterpretiert. Beim Abgleich aller drei heutigen Sessions zeigt sich
  ein glasklares Muster: `Parking_Brake` springt in **allen drei** Logs exakt (<0,1s) beim
  `KeyState`-Wechsel ACC→ON auf `Released` und beim Wechsel zurück auf ACC/OFF sofort wieder auf
  `Applied` – unabhängig davon, was am Handbremshebel tatsächlich passiert. Bestätigt die
  Vermutung des Nutzers: **`Parking_Brake` liefert bei Motor aus/ACC keinen echten Wert, sondern
  fällt fest auf `Applied` (raw=1) zurück** (vermutlich Default/Sleep-Zustand des sendenden
  Moduls oder ein anderes, tatsächlich vom Motorzustand abhängiges Signal an gleicher Bit-Position
  kollidiert). Der Handbremsentest im Checklisten-Fenster (ACC, Motor aus) konnte damit **technisch
  nicht funktionieren**, unabhängig vom Tap-Timing – braucht Wiederholung bei laufendem Motor.
- **Tür rechts (öffnen/schliessen):** uneindeutig – sowohl `DoorLeft` als auch `DoorRight`
  (DBC-Label, bekannter Seitentausch siehe oben) kippen mehrfach, aber keines der beiden bracket
  das geloggte Aktionsfenster (20,3-41,6s) saubér: `DoorRight` kippt Ajar→Closed schon bei
  6,9-13,2s (vor dem "öffnen"-Start), dann wieder Ajar→Closed bei 29,4-36,4s (zwischen öffnen-Ende
  und schliessen-Start); `DoorLeft` kippt einmal Ajar→Closed bei 16,9-26,6s, auch nicht im
  Fenster. Bringt keine neue Erkenntnis zum bekannten Seitentausch – anders als bei der
  Handbremse ist `Parking_Brake`-artiges Einfrieren hier nicht die Ursache (Türsignale ändern
  sich auch bei Motor aus/ACC sichtbar, siehe Zeitstempel oben), bleibt also ein echtes
  Tap-Timing-Problem.
- **Kofferraum:** "öffnen"-Schritt wurde übersprungen (skipped), `Trunk` bleibt die ganze
  Session `Closed` – erwartungsgemäß keine Daten.
- **Lenkrad (Anschlag zu Anschlag) – NICHT fehlend, nur ausserhalb der Checkliste gefahren:**
  Nutzer hat den Motor vor dem eigentlichen Checklisten-Lauf kurz angelassen ("ohne
  Servounterstützung dreht sich das Lenkrad nicht bis zum Anschlag") – das ist in
  `data/can/2026-09-12/candump-2026-09-12_120952.log` erfasst, Motor ON von rel. t=23,6s bis
  t=218,6s (195s), **vor** Beginn der Actions-Datei, daher ohne Start/Fertig-Zeitstempel.
  `Steering_Wheel_Absolute_Angle` (0x82) erreicht in diesem Fenster **+489,9° bis −490,2°**
  (nach Entfernen zweier bekannter Einzel-Ausreißer bei 1676,7°, klassischer Glitch direkt nach
  Motorstart) – beide Extremwerte halten über mehrere aufeinanderfolgende Frames (Sättigung am
  mechanischen Anschlag), **Gesamtrange ≈980°, plausibel symmetrisch für den ND** (bestätigt die
  bereits bekannte Nullpunkt-Kalibrierung zusätzlich durch Symmetrie). Cross-Korrelation mit den
  offenen Kandidaten im selben Fenster: `SteeringAngle_related` (0x86) bleibt schwach (r=0,56,
  lineare Anpassung `raw≈10,28·angle+15712`) – der vermutete Modelljahr-Offset allein erklärt das
  nicht, Signal bleibt ungeklärt. Neu: `SteeringAngle_related_2_maybe` (0x240 Byte4, r=0,88) und
  `SteeringTorque_related` (0x240 Byte1, r=0,80) korrelieren deutlich besser, aber auf Basis von
  nur einer Sweep-Fahrt nicht von einem Zeit-Trend-Artefakt unterscheidbar – weitere Prüfung nötig.
- **Blinker/Licht – KORREKTUR, waren NICHT fehlend:** genau wie beim Lenkrad hat der Nutzer diese
  Punkte bereits während des Motor-an-Fensters in `candump-2026-09-12_120952.log` durchgeführt,
  nur nicht über die Checkliste erfasst. `Turn` (0x9A, HS_BCMM, das echte Relais-Ausgangssignal)
  zeigt drei klar abgegrenzte Blinkphasen: **Links** t=78,4-84,2s (8 Zyklen L↔Off, Periode
  ≈0,68s ≈ 1,47Hz), **Rechts** t=85,6-90,0s (7 Zyklen), **Warnblinker** t=95,9-100,3s (7 Zyklen,
  Zustand `LR`) – alles plausibel inkl. realistischer Blinkfrequenz. `Headlight` (0x9A) zeigt
  zwei komplette Durchschaltungen Aus→TNS→TNS_Lo→Hi→TNS_Lo→TNS→Aus zwischen t=107,7s und
  t=158,3s – deckt Testplan-Punkt 3 ab. **Tür (DoorRight/DBC-Label, d.h. nach Seitentausch
  physisch links):** durchgehend `Ajar` von t=0,4s bis t=211,1s (Fahrertür offen fast die ganze
  Session, dann beim Verstauen/Losfahren geschlossen) – kein gezielter Einzeltest, aber plausibel.
  **Gefunden dabei: `TURN_SW`/`FOG_SW`/`LIGHT`/die drei Wischer-Signale (alle auf 0x91, HS_SSU)
  lassen sich mit beiden DBC-Varianten GAR NICHT decodieren** (`LIGHT : 7|10@0+` ist mit 10 Bit
  offensichtlich fehlerhaft definiert, `cantools` wirft bei jedem Frame "Short data" →
  `can_log_parser.py`s pauschales `except Exception: continue` schluckt das, d.h. 0x91 wird seit
  jeher in der gesamten Pipeline lautlos komplett übersprungen). Betrifft nur die Schalter-Eingänge
  (Blinkerhebel-Stellung, Licht-Stellung, Wischerstufen) – die tatsächlichen Ausgangssignale
  (`Turn`, `Headlight`) auf 0x9A sind davon nicht betroffen und funktionieren wie oben gezeigt.
  **Wischer-Test (Testplan Punkt 4) bleibt daher unverifizierbar**, bis der DBC-Fehler behoben ist.

- **DBC-Fehler auf 0x91 (HS_SSU) behoben, 2026-09-12:** Ursache gefunden: `LIGHT : 7|10@0+`
  (10 Bit ab Bit7, Byte0 komplett + Bit15-14 von Byte1) überlappt zwangsläufig `FOG_SW`
  (Bit1-0 in Byte0). `cantools` baut für alle Big-Endian-Signale einer Botschaft EIN
  gemeinsames Packformat und geht dabei von überlappungsfreien Signalen aus – durch den
  Overlap zählt es Bit1-0 doppelt, kommt auf >64 Bit Gesamtlänge und wirft bei JEDEM Frame
  `ValueError: Short data` / `DecodeError`. Betraf 0x91 komplett, also auch `TURN_SW`,
  `HAZ_SW`, die drei Wischer-Signale und `WIP_F_INT`. Fix: `LIGHT` auf `7|6@0+` gekürzt
  (nur Bit7-2 von Byte0, die einzigen mit `FOG_SW` überlappungsfreien Bits ab Startbit 7)
  in beiden DBC-Dateien (`MX5ND_6thGenMazda_HSCAN.dbc` und `_extended.dbc`), inkl.
  `CM_ SG_`-Kommentar mit der Herleitung. Verifiziert gegen das komplette
  `candump-2026-09-12_120952.log`: alle 2218 Frames von 0x91 dekodieren jetzt fehlerfrei
  (vorher 100% Fehlerquote), `LIGHT` zeigt genau 4 saubere Werte 48/56/60/62 (binär
  110000/111000/111100/111110, Thermometer-/Balkencode-Muster, passt zu Lichtschalter-
  Raststufen). **Cross-Check `TURN_SW` gegen `Turn`@0x9A bestanden:** `TURN_SW` zeigt jetzt
  durchgehend `L` von t=78,4-84,2s und `R` von t=85,6-90,0s – exakt deckungsgleich mit den
  oben dokumentierten Blinkphasen auf 0x9A – und bleibt korrekt `Off` während des separaten
  Warnblinker-Tests (t=95,9-100,3s), da der Blinkerhebel dabei ja nicht bewegt wird.
  **Offen:** die bereits vorhandene `VAL_ 145 LIGHT`-Tabelle (769=Off/768=AUTO/896=TNS/
  960=HEAD) wurde für das alte 10-Bit-Feld erstellt und passt nicht mehr zum gekürzten
  6-Bit-Feld (max. Rohwert jetzt 63) – bewusst nicht angepasst, da eine korrekte
  Neuzuordnung (v.a. was aus "Off" vs "AUTO" wird, die im alten Feld nur über das jetzt
  entfernte Bit14 von Byte1 unterschieden wurden) eine echte Bedeutungs-Interpretation
  wäre, keine reine Bitlängen-Korrektur mehr. `LIGHT`s Skalierung/Bedeutung bleibt daher
  vorerst unsicher (Status `_maybe`). Der Wischer-Test selbst ist damit strukturell
  verifizierbar, aber inhaltlich noch nicht ausgewertet.

- **Systematischer Scan nach weiteren stillen Decode-Fehlern, 2026-09-12:** nach dem
  LIGHT-Fix per Skript geprüft, ob noch andere Botschaften am selben Bug leiden (`decode()`
  in `can_log_parser.py` hat *alle* Fehler global verschluckt, nicht nur die von 0x91).
  Gegen beide verfügbaren Logs (`candump-2026-09-12_120952.log` + `candump-2026-09-11_180456.log`,
  zusammen >670k Frames) getestet: zwei weitere Botschaften waren betroffen, beide behoben.
  - **0x4F2 (HUD_Height, HS_CMU) – 100% Fehlerquote:** `HUD_Bright : 35|4@0+` überlappte
    mit den unteren 4 Bit von `HUD_Height`s Byte4 (`27|12@0+`), exakt derselbe Fehlertyp wie
    LIGHT/FOG_SW. `HUD_Bright` **entfernt** (nicht verschoben – die Botschaft hat 6 komplett
    freie Bytes, eine neue Position wäre reines Raten ohne Beleg). Danach 2663/2663 Frames
    fehlerfrei, `HUD_Height` liegt bei 1797-1861 (0x705-0x745) – passt zum bereits vorhandenen
    Original-Kommentar "Min[+] 0x00C5, Max[-] 0x0DC5". `HUD_Bright` müsste bei Bedarf über
    einen gezielten Test (HUD-Helligkeit verstellen, welches freie Byte sich ändert) neu
    gefunden werden.
  - **0x40A (HS_IC_CentralConfig) – 316 Fehler:** `C100_VIN_1_6`/`C101_VIN_7_12`/
    `C102_VIN_13_17` hatten `@4-` als Byte-Order – "4" ist im DBC-Format ungültig (nur 0/1
    erlaubt), cantools interpretierte es stillschweigend als Little-Endian, was mit den
    Geschwisterfeldern derselben gemultiplexten Botschaft kollidierte. Auf `@0-` (Motorola,
    passend zum Muster aller anderen 48-Bit-Felder in dieser Botschaft) korrigiert. Danach
    5901/5901 Frames fehlerfrei, und die drei Rohwerte ergeben als ASCII gelesen durchgehend
    `JMZND6` + `E76004` + `12218` = `JMZND6E7600412218` (17 Zeichen, exakt VIN-Länge) – starkes
    Indiz für einen korrekten Fix, aber nicht gegen den echten Fahrzeugschein gegengeprüft.
  - **Ergebnis:** beide DBC-Dateien laden jetzt auch mit `strict=True` fehlerfrei, und alle
    Frames in beiden Logs dekodieren ohne Fehler (671956/671956 bzw. 660769/660769 Samples).
    `can_log_parser.py`s `decode()` verschluckt Fehler nicht mehr lautlos, sondern zählt und
    meldet sie pro Botschaft am Ende des Laufs – falls künftige Logs neue/andere Bit-Konflikte
    zeigen, fällt das jetzt sofort auf statt für Jahre unbemerkt zu bleiben.
  - **Nebenbefund, unabhängig von den obigen Fixes entdeckt:** `MX5ND_6thGenMazda_HSCAN.dbc`
    (die Basis-Datei) hatte gegenüber `_extended.dbc` zwischenzeitlich den Busknoten
    `UNKNOWN_ECU` sowie die drei darauf aufbauenden Botschaften 0x344/0x438/0x491 UND die
    `MsgCounter_maybe`-Signale in 0x121/0x542 verloren – nicht durch mich in dieser Session
    geändert, Nutzer bestätigt ebenfalls keine eigene Bearbeitung, Ursache bleibt ungeklärt.
    Anlass für die untenstehende Architekturklärung.
  - **DBC-Architektur geklärt (2026-09-12):** `HSCAN.dbc` soll unverändertes Original/Fallback
    bleiben (Spiegel des berumiya-Upstream-Repos), `_extended.dbc` ist der Fork, in dem Funde
    und Fixes gesammelt werden – deckt sich mit der schon bestehenden Notiz weiter unten
    ("Ist die primäre DBC für `LiveCanValues`"). `can_log_parser.py` folgte dem bisher NICHT
    (`DBC_HSCAN` zeigte auf die Basis-Datei) – jetzt behoben: lädt bevorzugt `_extended.dbc`,
    fällt nur auf die Basis-Datei zurück falls die erweiterte mal fehlt (gleiches Muster wie
    `status_gui.py` auf dem Pi). Sofort messbarer Effekt: `can_log_parser.py` deckt jetzt
    101/101 statt 98/101 IDs ab, 812128 statt 650039 dekodierte Signal-Samples im
    2026-09-12-Log. Die Basis-Datei bleibt ab jetzt bewusst unangetastet.

- **Gesamtkilometerstand gefunden und bestätigt, 2026-09-12:** `C001_ODO` in derselben
  gemultiplexten Botschaft 0x40A (HS_IC_CentralConfig) wie die VIN, Multiplexer-Wert 49153,
  Skalierung 1:1 km. Decodiert in `candump-2026-09-12_120952.log` durchgehend zu **169756 km**
  – deckt sich **exakt** mit dem am Kombiinstrument abgelesenen Wert. Im älteren
  `candump-2026-09-11_180456.log` 169749 km (7 km weniger, konsistent mit einer Fahrt am
  Vortag). Damit unabhängig vom VIN-Fund gegenverifiziert und ohne `_maybe` eingetragen.
  Zum Vergleich geprüft: `Mileage` bei 0x977 (andere Botschaft, `SG_ Mileage : 39|22@0+`)
  liefert dagegen nur Müllwerte (z.B. 1576872, 4008) – ist NICHT der Gesamtkilometerstand,
  bisher nicht weiter untersucht.

- **`Mileage`@0x977 gegen Momentan-/Durchschnittsverbrauch geprüft, 2026-09-12 – Hypothese
  verworfen, neue Hypothese offen:** Bitherkunft empirisch geprüft (Einzelbit-Tests): Feld
  besteht aus Byte4 (komplett) + Byte5 (komplett) + oberen 6 Bit von Byte6, kein Overlap mit
  `Date`. Werteverlauf über die Fahrt `candump-2026-09-11_202803.log` neben RPM/Speed/Pedal
  (0x202) gelegt: springt zwischen wenigen Clustern (z.B. ~2625450, ~528300, ~1576878) hin
  und her, OHNE erkennbaren Zusammenhang zu Drehzahl/Pedal/Geschwindigkeit (derselbe Cluster
  tritt bei 15-73 km/h und Pedal 8-37% auf, kehrt auch wiederholt zu fast denselben Werten
  zurück). Spricht GEGEN Momentanverbrauch/Durchschnittsverbrauch/Einspritzmenge – dafür
  müsste der Wert eng mit Pedal/Drehzahl mitlaufen. Byte4 allein macht bei den großen
  Sprüngen einen Satz (z.B. +1,57 Mio. Gesamtwert in 20s), der für keine übliche
  Verbrauchsgröße plausibel ist – eher ein Hinweis, dass Byte4 nicht zu Byte5/6 gehört
  (Botschaftsname `HS_CMU_MMmonth` deutet auf eine eigene, nie sauber verifizierte
  Vermutung des Original-Autors hin). **Neue Hypothese (Nutzer, 2026-09-12): könnte
  Restreichweite sein, aber noch nicht in km** – die Cluster-Größenordnung (Zehntausende bis
  niedrige Millionen) passt der Größenordnung nach eher zu einer sehr fein aufgelösten
  Reichweiten-/Distanzgröße (z.B. in Metern oder einer internen Einheit) als zu einer
  Verbrauchsrate. **Bei künftigen Logs im Auge behalten** und sobald das Y-Splitter-Kabel da
  ist (heute Abend erwartet) gegen den OBD-Fusion-Reichweiten-/Tankfüllstandswert legen und
  vergleichen (siehe `data/can/test_plan_2026-09-12.md` Abschnitt A).

- **Restlicher Bytebereich von 0x40A durchsucht, 2026-09-12:** `Central_Config_Index` ist
  praktisch eine UDS-DID (die Mux-Werte 49152/49153/49408/48896 etc. sind exakt die Hex-Codes
  0xC000/0xC001/0xC100/0xBF00...), Byte2-7 die zugehörige "ReadDataByIdentifier"-Antwort –
  erklärt das Namensschema C0xx/BFxx. Alle 41 definierten Sub-Felder dekodieren fehlerfrei
  (Datei besteht komplett `strict=True`, keine weiteren Bit-Overlaps/ungültigen Byte-Order-
  Ziffern wie beim VIN-Fix gefunden). Zwei weitere zusätzlich zu VIN/ODO unabhängig bestätigt:
  - `C000_TOTAL_TIME`: läuft in allen drei verfügbaren Logs exakt 1:1 mit der echten
    verstrichenen Zeit mit (+566,0s bei 566,7s Log-Dauer, +76,0s bei 76,0s, +218,0s bei
    218,4s) – vermutlich ein interner Uptime-/Betriebszeit-Zähler des Steuergeräts.
  - `BF00_IG_ON_Timer`: zählt Sekunden seit Zündung an. Fällt in
    `candump-2026-09-11_180456.log` mitten im Log von 191 auf 0 zurück – exakt der bereits
    dokumentierte Moment, in dem die Zündung wegen nicht getretener Kupplung versehentlich
    aus/ein ging, bevor der Motor gestartet wurde. Schöne unabhängige Kreuzbestätigung einer
    bereits bekannten Beobachtung.
  - Die restlichen 34 Felder (`C102`-`C104`, `BF01`-`BF21` außer `BF00`) sind in allen drei
    Logs komplett konstant – passt zu statischer Fahrzeug-Konfiguration/Kalibrierdaten, nicht
    zu Live-Telemetrie. Auffällig: viele haben nur 1-3 führende Bytes ungleich Null, der Rest
    durchgehend `00` (z.B. `BF01`=`31 00 00 00 00 00`) – möglicherweise ist die echte
    UDS-Nutzlast kürzer als die pauschal angenommenen 48 Bit (Präzedenzfall: `BF00` hat sich
    unabhängig als echte 16-Bit-Größe herausgestellt). **Bewusst nicht gekürzt** – mit nur
    einem Fahrzeugstand nicht unterscheidbar, ob die Nullbytes strukturell ungenutzt sind
    oder nur zufällig bei diesem Fahrzeug Null sind. Offen für den Fall, dass ein zweites
    Fahrzeug oder ein Log nach einem Konfigurations-Update zum Diffen verfügbar wird.

**Lektion für den nächsten Durchlauf:** (1) Zündung wirklich auf ON/Motor-an bringen, sonst sind
Kupplungs- UND Handbremsentest komplett wertlos (PCM-Kanäle senden nur mit laufendem Motor, und
`Parking_Brake` springt bei ACC/OFF fest auf "Applied" zurück). (2) Start-Tap erst drücken, wenn
man direkt an der Bedienung ist (bei der Tür lag die echte Aktion ausserhalb des geloggten
Fensters) – oder testweise die Checkliste durchgehen statt frei zu handeln, damit alles erfasst
wird. (3) Restliche Checklisten-Punkte (Blinker/Licht/Wischer/Tür links) noch nachholen.

## Y-Splitter-Kalibrierung durchgeführt (2026-09-12, candump-2026-09-12_211833, 20 Min)

Erste Fahrt mit OBD-Fusion + CAN gleichzeitig (Y-Kabel). Lag-Suche (Kreuzkorrelation, 5Hz-
Gitter) + lineare Regression zwischen echtem OBD-Referenzkanal und CAN-Kandidat:

- **Kupplung:** `CPP_PER_MZ` (OBD, echte 0-100%) vs. `Clutch_Pedal_Position_raw` (CAN, 0x130
  Byte7, 0-199 roh) – r=0,96 bei Lag -0,4s. Kalibrierung: **`CPP_PER_MZ% ≈ 0,4665 · raw + 0,56`**.
  Gegenprobe: der bekannte Bisspunkt-Rohwert (114-123, aus RPM-Reibphase) ergibt damit 55,6% –
  deckt sich exakt mit dem alten ~55%-Bisspunkt aus `CPP_PER_MZ`. Absolute Skala von
  `Clutch_Pedal_Position_raw` damit referenzgemessen (vorher nur Richtung+Anker bekannt).
  RMSE 6,4 Prozentpunkte, einzelne Ausreißer bei schnellen Schaltvorgängen (OBD pollt nur ~2Hz,
  aliast bei den 250-500ms-Kupplungspulsen) – kein Kalibrierfehler, Sampling-Artefakt.
- **Tankfüllstand:** `FLI` (OBD, echte %) vs. `Fuel_Tank` (CAN, 0x09E roh) – r=0,995 nach 15s-
  Medianglättung (filtert Tankschwappen raus). Kalibrierung: **`FLI% ≈ 2,486 · Fuel_Tank_raw − 0,02`**,
  RMSE 0,85 Prozentpunkte. **Löst den bisherigen "Fuel_Tank-Skala vermutlich falsch"-Verdacht** –
  die DBC-Formel kann durch diese lineare Beziehung ersetzt werden (bisheriger Ausweg über den
  Standard-OBD-PID 0x2F bleibt trotzdem als unabhängiger Referenzkanal sinnvoll).
- **MAF-Kandidat:** `MassAirFlowRate` (OBD, echt) vs. `MassAirFlowRate_maybe` (CAN, 0x167 Byte4) –
  nur r=0,85, hohe Residuen. Bestätigt den früheren Verdacht (korreliert eher mit MAP als mit
  echtem Luftmassenstrom) – **weiterhin nicht vertrauenswürdig**, keine neue Erkenntnis.

Analyseskript war Ad-hoc (nicht ins Projekt übernommen), Methodik (Lag-Suche per
Kreuzkorrelation auf gemeinsamem Zeitgitter, dann lineare Regression) bei Bedarf reproduzierbar.

## Byte-Suche Drosselklappenstellung (ETC_ACT) - kein Broadcast-Signal gefunden (2026-09-12)

Auftrag: eigenständiges CAN-Signal für die tatsächliche Drosselklappenstellung (`ETC_ACT`,
OBD-Referenzkanal) im Y-Splitter-Log (`candump-2026-09-12_211833.log`) per Byte-Suche
identifizieren - nicht das Gaspedal (`APP`), das schon bekannt ist.

**Methodik:** alle 111 CAN-IDs, jedes einzelne Byte + jedes benachbarte 16-Bit-Bytepaar
(BE/LE), auf gemeinsames 10Hz-Zeitraster gebracht, gegen `ETC_ACT` korreliert (roh +
trendbereinigt per gleitendem Median, wie im Bericht vom 2026-09-11).

**Ergebnis:** stärkste Kandidaten (r=0,93 roh/0,83 trendbereinigt) sind ausschließlich
bereits bekannte `APP_Accelerator_Pedal_Position`-Bytes (0x202/0x167) - die Suche findet
nur das Pedal wieder, kein unbekanntes Byte kommt annähernd heran.

**Berechnungsversuch:** `ETC_ACT ~ Pedal` linear, R²=0,87; mit Drehzahl zusätzlich
R²=0,89-0,91. Systematischer Rest bleibt, konzentriert um Schaltvorgänge/hohe Drehzahl
(z.B. t=325,9s: Pedal=0%, ETC_ACT=78,7% - entweder echter Zwischengas-Effekt oder
Latenzartefakt der nur ~2Hz gepollten OBD-Quelle). Keine belastbare 1:1-Rekonstruktion.

**Zwei externe Gegenproben, beide negativ:**
- Werks-Kommunikationsmatrix (`mx-5_can1.xlsx`): keine eigene Zeile für Drosselklappen-
  stellung, nur "Accelerator pedal opening angle information" (= Pedalsignal).
- RaceChrono-DB der NC-Vorgängerplattform (2005-2015, `mazda_mx5_nc.md`): 3 Kandidaten
  (0x200 Byte7, 0x215 Byte6, 0x240 Byte3) genannt - alle drei gegen unsere ND-Daten
  getestet, keine relevante Korrelation (≤0,15 trendbereinigt); 0x215 Byte6 ist bei uns
  bereits `WheelSpeed_4` - Byte-Belegung zwischen den Generationen neu vergeben.

**Fazit:** kein Beleg für ein eigenständig gebroadcastetes Drosselklappen-Signal auf
diesem HS-CAN-Bus - passt zum bekannten Muster (`AFR_MZ`/`ActualEnginePercentTorque`
ebenfalls Mode-22-only). Beste CAN-Näherung: berechnete Schätzung aus Pedal+Drehzahl
(R²~0,89), mit bekannten Schwachstellen bei Schaltvorgängen/Volllast. Nicht ohne neue
Datenlage (z.B. gezielter Leerlauf-mit-Last-Test) erneut aufrollen.

## Nachtrag: nichtlineare Pedal-Drossel-Beziehung + Abgleich gegen Teillastmodell (2026-09-12)

**Sättigung bestätigt:** eigene Bin-Analyse zeigt, `ETC_ACT` erreicht sein Maximum (86,2°)
bereits bei mehreren APP-Bins deutlich unter 80% - die Pedal->Drossel-Kennlinie ist
nichtlinear/sättigend, keine einfache lineare Skalierung. Pearson-Korrelation
unterschätzt das systematisch. Byte-Suche mit Rangkorrelation (Spearman) auf den
trendbereinigten Werten neu gerankt: die bereits bekannten Last-Signale
`MAP_Manifold_absolute_pressure_sensor` (0xFD Byte6, r=0,63), `MassAirFlowRate_maybe`
(0x167 Byte4-5, r=0,62) und `EngineLoad_related_maybe` (0x200 Byte0-1, r=0,59) liegen
jetzt VOR dem Pedalsignal selbst (r=0,58) - physikalisch schlüssig, da Luftmasse/MAP
direkte Folgen der echten Klappenstellung sind, das Pedal dagegen erst vom ECU
nichtlinear verarbeitet wird. Kein neues Byte gefunden, aber `MAP_CAN` ist der
physikalisch beste verfügbare CAN-Ersatz, falls je ein kontinuierliches
"wie offen ist die Drossel"-Signal gebraucht wird.

Nebenbefund: die frühere "Pedal=0%, ETC_ACT=78%"-Diskrepanz (t≈325,9s) war ein
Auflösungsartefakt der nur ~2Hz gepollten `ETC_ACT`-Quelle während einer <0,5s-
Schaltvorgangsserie bei Volllast (t=320-330s) - kein echtes Phänomen.

**Abgleich gegen das Teillastmodell (`ActualEnginePercentTorque`, siehe
`PROJEKT_STAND.md` "Teillastmodell"):** dieselbe Byte-Suche direkt gegen den
Teillastmodell-Zielkanal `ActualEnginePercentTorque` (auch in diesem Log vorhanden,
2415 Samples) wiederholt. Ergebnis deutlich klarer als bei ETC_ACT:
**`MassAirFlowRate_maybe` (0x167 Byte4-5) korreliert mit r=0,86 roh/0,79 rang-
trendbereinigt** - klar vor Pedal (0,72/0,68), `EngineLoad_related_maybe`
(0,76/0,76) und MAP (0,76/0,72). Lineares Modell (bester Lag -0,3s):
**`Torque% ≈ 3,17 · MassAirFlowRate_maybe_raw - 185,3`, R²=0,96, RMSE 6,1
Prozentpunkte**; mit Drehzahl zusätzlich R²=0,975. Das ist eine deutlich bessere
Drehmoment-Schätzung rein aus CAN-Daten als der bisherige (ETC_ACT,Drehzahl)-
Kennfeld-Ansatz nahelegen würde - stützt die Vermutung, dass `MassAirFlowRate_maybe`
trotz der am 2026-09-11 geäußerten Zweifel (Korrelation eher mit MAP als mit
RPM×MAP) eine echte last-/luftmassenbezogene Größe ist. Noch nicht in
`partial_load_model.py`/`performance_simulation.py` eingebaut, nur auf diesem
einen Log geprüft.

**Nachgetragen + validiert (2026-09-12):** direkte Kalibrierung des Rohwerts gegen
echten OBD-Luftmassenstrom (g/s) passt schlecht (R²=0,72, nicht durchgehend monoton -
Sprung zwischen raw=80 und raw=85). Grund: echter Luftmassenstrom skaliert bei
Volllast weiter mit der Drehzahl, Last-/Drehmomentprozent dagegen sättigt bei
Volllast unabhängig von der Drehzahl (per Definition "% des bei DIESER Drehzahl
möglichen Maximaldrehmoments") - genau das beobachtete Muster im engen Rohwertbereich
54-89. **Sinnvolle Einheit ist also Prozent (Last/Drehmoment), nicht g/s.** DBC-Signal
`MassAirFlowRate_maybe` (0x167, `MX5ND_6thGenMazda_HSCAN_extended.dbc`) umbenannt zu
`EngineLoad_or_Torque_pct_maybe`, Skalierung eingetragen (`raw*3.17-185.3`, [0|100]%).
Log neu dekodiert und direkt gegen den echten OBD-Kanal geprüft: **RMSE=5,65
Prozentpunkte, R²=0,966, mittlere Abweichung -0,33pp** (unverzerrt) - Formel greift
korrekt. Rohwerte außerhalb des in diesem Log beobachteten Bereichs (54-89) laufen
über die lineare Formel ins Negative/>100 (Extrapolation), auf 0-100 clippen. Nur auf
diesem einen Log kalibriert/geprüft, keine Cross-Validation über mehrere Fahrten.

## Konzept: weitere OBD-Werte per Byte-Suche in CAN-Daten wiederfinden (2026-09-12)

Nach dem Muster von ETC_ACT/Torque%: welche OBD-Kanäle aus `2026-09-12 211851`
(Y-Splitter-Log) sind noch nicht als CAN-Signal bestätigt, und lohnt sich dafür
eine Byte-Suche? Übersicht aller 60 Kanäle dieses Logs geprüft und in drei Gruppen
sortiert - **noch keine Suche durchgeführt, nur Priorisierung.**

**Methodik (unverändert, wie ETC_ACT/Torque%):** CAN-Rohlog einmal parsen, alle
111 IDs x (8 Einzelbytes + 14 Bytepaare BE/LE), gegen Zielkanal auf 10Hz-Raster
korrelieren (Pearson roh, Pearson+Spearman trendbereinigt - Spearman nicht
vergessen, hat bei ETC_ACT/Torque% die eigentlich besseren Kandidaten erst
sichtbar gemacht), bekannte Signal-Belegung aus der DBC ausschließen/kennzeichnen,
Web-Gegenprobe (Werksmatrix `mx-5_can1.xlsx`, Community-DBCs/RaceChrono) wo
sinnvoll. **Technische Verbesserung fürs nächste Mal:** Rohlog-Parsing +
Byte-Extraktion einmal pro Log in ein Skript auslagern (`scripts/can_byte_search.py
<log> <ziel_channel>` o.ä.), das den bisher jedes Mal neu geschriebenen Ad-hoc-Code
ersetzt - bisher 3x fast identisch neu getippt.

**Gruppe A - lohnt sich, genug Samples (~2000+, ~2Hz) für Korrelation:**
- `MassAirFlowRate` (echter g/s-Kanal) - **erneut offen**, weil sich der bisherige
  Kandidat (0x167 Byte4-5) als Last-/Drehmomentprozent statt echtem Luftmassenstrom
  herausgestellt hat (s.o.). Eigener, neuer Suchlauf nötig.
- `BFP_PRE_MZ` (Bremsflüssigkeitsdruck) direkt gegen CAN `BrakePressure` (0x78,
  bereits signed-korrigiert) - beide sollen dieselbe physikalische Größe sein,
  einfacher direkter Plausibilitätscheck (Kalibrierung/Einheit), kein Byte-Scan
  nötig, nur Lag+Regression wie bei Kupplung/Tank.
- `AFR_MZ`/`AFR_ACT_MZ` (Lambda/AFR) - bisher nur als "wahrscheinlich Mode-22-only"
  angenommen (Analogieschluss zu ActualEnginePercentTorque), nie selbst durchsucht.
  Für WOT-Erkennung relevant ([[mx5_wot_detection_criteria]]) - ein CAN-Fund wäre
  fürs Live-GUI (`status_gui.py`) wertvoll.
- `CommandEquivalenceRatio` - am 2026-09-11 schon gesucht (kein Treffer), aber nur
  mit Pearson, vor der Sättigungs-Erkenntnis. Mit Spearman erneut versuchen.
- `TimingAdvance` - gleicher Fall wie CommandEquivalenceRatio, gleiche Wiederholung
  wert.

**Gruppe B - nur ~20 Samples/Log (grob 1/Minute, "Slow-Group"-PIDs), Korrelation
nicht sinnvoll (zu wenig Zeitauflösung):** `BATT_SOC`, `ControlModuleVoltage` (nur
2 Samples!), `CatalystTemperatureBank1Sensor1`, `CommandedFuelRailPressureA`,
`FuelRailPressureA`, `FuelRailTemperatureA`, `FuelPressureControlSystemSupported`,
`TransmissionActualGearRatio/Status/Supported`, `WU1-4_P_TPM_IC`, `Rpm`, `APP`
(die beiden letzten sind ohnehin die bekannten Duplikat-Slots, siehe
`build_datalake.py`-Kommentare). **Alternative statt Byte-Korrelation:** groben
Fenster-Mittelwert-Vergleich (z.B. 60s-Bins) statt Zeitreihen-Korrelation, oder
die OBD-Fusion-PID-Priorität für die interessanten Kanäle (`ControlModuleVoltage`,
`BATT_SOC`) erhöhen, falls die App das zulässt - sonst bleibt die Zeitauflösung
zu grob für einen Byte-Fund. `ControlModuleVoltage`/`BATT_SOC` haben zudem
mögliche Entsprechungen in der Werksmatrix ("Supply voltage", "Battery charge
status") - dort zuerst nach Kandidaten-IDs suchen, dann grob gegenprüfen.

**Gruppe C - abgeleitet/berechnet, kein eigener Sensor-Rohwert, keine Suche
sinnvoll:** `CO2Flow`, `InstantCO2Rate`, `TotalCO2`, `InstantFuelEconomy`,
`TotalFuelEconomy`, `FuelRate` (alle vermutlich reine Funktionen von
MAF/AFR/Speed, keine eigenständige Bus-Nachricht zu erwarten). `TM_GEST`
(Getriebe-Gangstatus) ist ebenfalls kein neues Ziel - Gang ist über
`Gear_CAN`/`MT_Gear_Status` bereits aus CAN bekannt, `TM_GEST` wäre nur eine
redundante Bestätigung, kein neuer Fund.

**Empfohlene Reihenfolge:** BFP_PRE_MZ-Check zuerst (billig, hohe Erfolgschance,
kein neuer Scan-Code nötig), dann MassAirFlowRate (echtes g/s), dann AFR_MZ,
zuletzt CommandEquivalenceRatio/TimingAdvance-Wiederholung (niedrigere
Erfolgswahrscheinlichkeit, schon einmal ergebnislos durchsucht).

### Detailplan Gruppe A - DBC-Richtigkeit explizit mit in Frage stellen (2026-09-12)

**Leitgedanke:** die Community-DBC hat sich wiederholt als fehlerhaft erwiesen
(DoorLeft/Right vertauscht, BrakePressure Vorzeichenfehler, LIGHT-Bit-Overlap,
VIN-Byte-Order-Fehler, und jetzt `MassAirFlowRate_maybe` war tatsächlich Last-%).
Die Byte-Suche darf sich deshalb NICHT auf unbelegte ("unclaimed") Bytes
beschränken - bereits durch ein `SG_` belegte Bytes bleiben Teil des
Kandidaten-Pools (technisch ohnehin schon so: die `coverage`-Prüfung markiert
bekannte Belegung nur als Kontext-Spalte, filtert nicht raus). Auswertung
explizit auch auf diese Frage hin: **korreliert ein bereits belegtes Byte
stärker mit einer ANDEREN physikalischen Größe als der, die die DBC ihm
zuschreibt?** Das wäre ein Hinweis auf eine weitere DBC-Fehlzuordnung, nicht nur
auf ein neues Signal.

Pro Zielkanal geplanter Ablauf:

1. **`BFP_PRE_MZ` (Bremsflüssigkeitsdruck, OBD) vs. `BrakePressure`@0x78 (CAN)** -
   direkter DBC-Korrektheitstest, kein Byte-Scan nötig (Ziel-Byte schon bekannt).
   Bisher wurde bei `BrakePressure` nur der Vorzeichenfehler (Unstetigkeit)
   behoben, die **absolute Kalibrierung nie gegen einen echten Referenzkanal
   geprüft** (Statusdok: "bleibt weiterhin ungeklärt"). Plan: `BFP_PRE_MZ`
   (Einheit prüfen - vermutlich kPa laut `CANONICAL_UNIT`) direkt gegen die
   DBC-Formel (`raw*0.001-40` bar, signed) mit Lag-Suche + Regression
   vergleichen, wie bei Kupplung/Tank. Stimmt Steigung/Offset NICHT (z.B. falscher
   Faktor oder Offset ungleich der erwarteten Einheitenumrechnung) - Faktor/Offset
   in der DBC korrigieren, nicht nur als "ungeklärt" stehen lassen.
2. **`MassAirFlowRate` (echtes g/s) - voller Byte-Scan, KEINE Bytes ausschließen.**
   Da der bisherige Kandidat an dieser Stelle (0x167 B4-5) nachweislich falsch
   benannt war, ist auch zu prüfen, ob ein AKTUELL ANDERS belegtes Byte
   (z.B. `EngineLoad_related_maybe`@0x200, `MAP_Manifold_absolute_pressure_sensor`
   @0xFD, oder sogar ein bisher als "sicher" geltendes Signal) in Wirklichkeit
   besser zu echtem Luftmassenstrom passt als zu seiner jetzigen Bezeichnung.
   Physik-Erwartung als Gegenprobe (wie am 2026-09-11 versucht, aber diesmal mit
   echtem MAF-Referenzkanal statt Kandidat-gegen-Kandidat): MAF sollte mit
   RPM*MAP staerker korrelieren als mit MAP allein (Speed-Density-Prinzip) -
   dieser Test war vorher nicht moeglich (kein Referenzkanal), jetzt schon.
3. **`AFR_MZ`/`AFR_ACT_MZ` (Lambda) - voller Byte-Scan.** Bisher nie gezielt
   gesucht, nur als "wahrscheinlich Mode-22-only" angenommen (Analogieschluss).
   Zusaetzlich pruefen: gibt es ein Byte, das aktuell als etwas anderes
   deklariert ist, aber das bekannte AFR-Verhalten zeigt (Absinken Richtung
   ~0.9 bei Volllast, Sprung auf ~2.0-Sentinel bei Schubabschaltung - das
   Fingerprint-Muster aus der 2026-09-11-Suche, diesmal mit echtem
   Referenzkanal statt nur Muster-Heuristik pruefbar)?
4. **`CommandEquivalenceRatio` / `TimingAdvance` - Wiederholung mit Spearman +
   Reference vorhanden.** Der Suchlauf vom 2026-09-11 hatte noch KEINEN
   Referenzkanal (beide OBD-Werte lagen damals nicht vor/wurden nicht genutzt)
   und nur Pearson - beide Einschraenkungen sind jetzt behoben. Gleiche
   "auch belegte Bytes pruefen"-Regel wie oben.

**Ergebnisformat wie gewohnt:** Treffer mit Formel + R²/RMSE (roh + trendbereinigt
+ Spearman) ins Statusdok/Memory, inkl. expliziter Aussage ob eine bestehende
DBC-Zuordnung dadurch bestaetigt, unveraendert unsicher, oder widerlegt wird.

### Gruppe A abgearbeitet (2026-09-12)

**1. `BFP_PRE_MZ` vs. `BrakePressure` - DBC-Fehler gefunden und behoben.** Direkter
Soll-Ist-Vergleich (Lag-Suche + Regression, kein Byte-Scan noetig) zeigt: die
bisherige Formel `(0.001,-40)` sagt beim Ruhewert (92,6% der `BFP_PRE_MZ`-Samples
sind exakt 0 kPa, haeufigster Rohwert -26429) `-66,4 bar` voraus - physikalisch
unmoeglich. Neu kalibriert direkt gegen `BFP_PRE_MZ` (bester Lag -0,3s):
**Faktor `0.0012413`, Offset `+32.7986`** statt `(0.001,-40)` - nicht nur die
Groesse des Offsets war falsch, auch das Vorzeichen. Trifft den Ruhewert jetzt
korrekt (Vorhersage bei raw=-26429: ~0 bar), **R²=0,986, RMSE 0,57 bar** ueber die
komplette Fahrt inkl. mehrerer Bremsungen. DBC (`MX5ND_6thGenMazda_HSCAN_extended.dbc`,
BO_120/0x78) aktualisiert, alle 4 CAN-Logs neu dekodiert, Datalake neu gebaut -
`BrakePressure_CAN` liegt jetzt bei ~0 bar in Ruhe und bis ~49 bar bei einer
bekannten Vollbremsung (vorher durchgehend -27 bis -67 bar). Nur an diesem einen
Log kalibriert.

**2. `MassAirFlowRate` (echtes g/s) - voller Scan ohne Ausschluss: kein CAN-Byte,
aber ein physikalischer Ersatz gefunden.** Bester Byte-Kandidat nur r=0,58
(Spearman, trendbereinigt) - deutlich schwaecher als die Treffer bei Torque%
(0,79) oder Bremsdruck (0,99). Physik-Gegenprobe (diesmal mit echtem
Referenzkanal moeglich): **`RPM*MAP` korreliert mit r=0,956 (unbereinigt) bzw.
0,972 (bester Lag -0,4s)**, deutlich vor MAP allein (r=0,69) - genau das
erwartete Speed-Density-Verhalten fuer echten Luftmassenstrom (im Gegensatz zu
`EngineLoad_or_Torque_pct_maybe`, das staerker mit MAP allein als mit RPM*MAP
korreliert - bestaetigt nochmal, dass es KEIN echtes MAF-Signal ist). Lineares
Modell: **`MAF(g/s) ≈ 0.00019004*(RPM*MAP) - 8.08`, R²=0,945, RMSE 8,6 g/s**.
Kein neuer CAN-Fund, aber ein berechenbarer Ersatz aus zwei bereits bekannten,
bestaetigten Signalen - kein Mode-22-Polling fuer MAF mehr noetig.

**3. `AFR_MZ`/`AFR_ACT_MZ` (Lambda) - kein Kandidat.** Voller Scan (auch belegte
Bytes), bester Treffer nur r≈0,47 (Spearman) - deutlich unter der Signifikanz-
Schwelle aller bisherigen echten Funde (≥0,6). Bestaetigt die bisherige Annahme
"wahrscheinlich Mode-22-only" jetzt direkt statt nur per Analogieschluss.

**4. `CommandEquivalenceRatio`/`TimingAdvance` - Wiederholung mit Spearman +
Referenzkanal, weiterhin kein Kandidat.** Max. r≈0,43 bzw. 0,53 (Spearman) - auch
mit den beiden Verbesserungen gegenueber dem 2026-09-11-Suchlauf (Referenzkanal
diesmal vorhanden, Spearman statt nur Pearson) kein belastbarer Treffer.
Schliesst die am 2026-09-11 offen gelassene Frage endgueltig: beide Kanaele sind
nicht auf HS-CAN broadcastet.

**Fazit Gruppe A:** von 5 gepruesten Zielen 1 echter DBC-Fehler gefunden+behoben
(Bremsdruck), 1 physikalischer Ersatz statt direktem Fund (MAF via RPM*MAP), 3
Bestaetigungen "nicht broadcastet" (AFR, CommandEquivalenceRatio, TimingAdvance).
Der DBC-Skepsis-Ansatz (auch belegte Bytes mitpruefen) hat sich bei Punkt 1
ausgezahlt - kein weiterer Fehlkandidat bei den anderen vier gefunden.

## Bidirektionale Vollgas-Fahrt: Beschleunigungsmodell validiert (2026-09-12)

Lang erwartetes Ereignis im Y-Splitter-Log: derselbe Autobahn-Abschnitt in beide
Richtungen mit Vollgas gefahren, ~142s Abstand. Automatisch erkannt aus CAN-Pedal
+ GPS-Peilung: West t=508,6-566,9s (Peilung ~270-276°, Gänge 4-6, 131->228 km/h),
Ost t=709,4-791,8s (Peilung ~82-93°, Gänge 3-6, 104->237 km/h) - exakt derselbe
Lat/Lon-Korridor. Vollständige Auswertung, siehe Memory `mx5_bidirectional_wot_
validation.md` fuer alle Details.

**Erster Durchgang (nur bidirektionale Mittelung):** Gang 4 zeigte in beiden
Richtungen ein Verhaeltnis Messung/Modell < 1,0 (0,90/0,94-0,99) - sah nach echtem
Modell-Bias aus. Gang 5 zeigte grosse Ost-West-Differenz (0,68-0,76 vs. 0,93-0,96)
- Verdacht auf Windeinfluss.

**Zweiter Durchgang, MIT echten Hoehendaten (DGM) und echtem Wind (Nutzer-Hinweis:
die Strecke fuehrt ueber eine Kuppe) - bestehende Infrastruktur wiederverwendet
(`top_speed_validation.segment_grade()`, `coastdown_analysis.wind_at()`):**
- **Kuppe bestaetigt und in beiden Richtungen fast identisch vermessen** (Median-
  Differenz nur 0,25m ueber 3045m gemeinsame Strecke - starker Beleg, dass es
  wirklich dieselbe Strasse ist): Hoehe steigt von ~51,6m auf einen Scheitelpunkt
  ~57,6m (bei ~55-65% der gemeinsamen Strecke), dann Abfall auf ~49,2m.
- **Echter Wind fuer exakt diese Stunde** (Open-Meteo-Archiv): ~5,6 km/h aus
  ~253-254° (WSW) -> Ruckenwind-Komponente West -1,50 m/s, Ost +1,49 m/s - fast
  perfekt symmetrisch (validiert die Winddaten selbst).
- **Korrigiertes Verhaeltnis Messung/Modell:** West Gang4 (Steigung +0,77%):
  0,91->**0,98**. West Gang5 (+0,74%): 0,76->**0,93**. West Gang6 (-0,52%):
  0,95->**1,05**. Ost Gang3/4: Segmente nur 34m/177m lang - unter der 200m-
  Mindestlaenge von `segment_grade()`, Korrektur dort nicht belastbar. Ost Gang5
  (-0,07%, fast flach): 0,97->0,89. Ost Gang6 (-0,38% ueber 3,3km gemittelt, nahe
  Vmax): 1,54->0,65, aber absolute Differenz nur 0,06 m/s² - Verhaeltnis nahe
  Vmax weiterhin nicht aussagekraeftig.

**Revidierte Kernaussage:** der zunaechst vermutete "Modell-Bias" in Gang 4/5 war
zu einem grossen Teil echte Physik - die Westfahrt lag genau auf dem Anstieg zur
Kuppe plus Gegenwind. Nach Korrektur trifft das Modell alle drei West-Gaenge sehr
gut (0,93-1,05). Das Antriebsstrangmodell validiert sich damit deutlich besser,
als die erste (hoehen-naive) Auswertung nahelegte.

**Nebenfund:** `wot_segments()` in `drivetrain_model_validation.py` lieferte fuer
dieses Log lautlos 0 Segmente (bekannter kaputter APP-Duplikat-Kanal wurde nicht
als unplausibel erkannt) - gefixt: faellt jetzt automatisch auf den ETC_ACT+
Lambda-Fallback zurueck. Keine Regression (166 statt 153 Segmente insgesamt ueber
alle Logs, Korrelation weiterhin 0,99).

## Praezisions-Kuppenmodell aus ALS-Rohdaten gebaut (2026-09-13)

Auftrag: Kuppe so genau wie moeglich modellieren und fuer die Vmax-Validierung
verfuegbar machen. Vorfrage des Nutzers dazu bereits beantwortet: das global
genutzte 1m-DGM-Cache-Raster (`elevation_model.py`, `GRID_RES_M=1.0`) ist eine
Vereinfachung - die rohen ALS-Punktwolken haben eine native Dichte von
**~12-15 Punkten/m² (mittlerer Abstand ~0,27-0,29m)**, also deutlich feiner als
die angefragten 0,4m.

**Neues Skript `scripts/crest_profile_wot_2026_09_12.py`:** extrahiert rohe
Bodenpunkte direkt aus `höhendaten/als_*.zip` (6 Kacheln, ~75 Mio. Punkte) in
einem Fahrbahnkorridor um die tatsaechlich gefahrene GPS-Spur, robuster
Median je 1m-Bin, 60m-Glaettung (loest die ~1km-Kuppenform sauber auf, ohne
ALS-Rauschen).

**Zwei Iterationen bis zur sauberen Loesung:**
1. Erster Versuch: EINE gemeinsame Mittellinie (Polynom durch beide
   Richtungen kombiniert) + 20m-Korridor - erzeugte eine falsche -11,5%-
   Steigungsspitze direkt nach dem Scheitelpunkt. Ursache gefunden: die beiden
   Fahrbahnen dieser geteilten Autobahn liegen ~14-16m seitlich versetzt
   (direkt an den GPS-Spuren beider Richtungen bei gleicher Ost-Koordinate
   nachgewiesen) - eine gemeinsame Mittellinie liegt dazwischen, der 20m-
   Korridor faengt streckenweise BEIDE Fahrbahnen ein.
2. Fix: **getrenntes Profil pro Richtung**, jeweils eigene GPS-Spur als
   Mittellinie, 8m-Korridor (bleibt sicher auf der eigenen Fahrspur). Zusaetzlich
   Mittellinie von Polynom-Fit auf **stueckweise-lineare Interpolation durch die
   rohen GPS-Fixe** umgestellt - ein Polynom durch nur 65-89 Punkte ueber 3-5km
   war nicht flexibel genug (erste Version: West-Scheitelpunkt 370m von Ost's
   unabhaengig ermitteltem Scheitelpunkt entfernt, 3m niedriger). Nach dem Fix:
   **beide unabhaengig aus entgegengesetzten Fahrten gebauten Profile stimmen
   fast exakt ueberein** (Scheitelpunkt 4m auseinander, Hoehe 0,31m Differenz) -
   starke Validierung der Methode selbst.

**Neues Skript `scripts/precise_vmax_validation_2026_09_12.py`:** nutzt fuer
jedes der 7 Gang-Segmente (3x West, 4x Ost) die LOKALE Steigung an der
tatsaechlichen GPS-Position (nicht einen Segment-Mittelwert) plus echten
historischen Wind. Wichtige Vorzeichenfalle dokumentiert: die Kuppen-Datei
speichert d(Hoehe)/d(Ost-Koordinate) - Ost kann das direkt nutzen (faehrt in
Ost-Richtung), West (faehrt in West-Richtung) muss das Vorzeichen umdrehen.

**Ergebnis - deutlich praeziser als die Segment-Mittelwert-Methode:**

| Richtung | Gang | v_mean | lokale Steigung | Verh. flach | Verh. korrigiert |
|---|---|---|---|---|---|
| West | 4 | 159 km/h | +0,13% | 0,94 | **0,98** |
| West | 5 | 206 km/h | +0,67% | 0,81 | **1,01** |
| West | 6 | 227 km/h | -0,36% | 1,91 | 3,51 (Vmax-nah, absolute Luecke nur 0,08 m/s²) |
| Ost | 3 | 124 km/h | +0,02% | 0,97 | **0,96** |
| Ost | 4 | 163 km/h | +0,02% | 1,01 | **0,98** |
| Ost | 5 | 202 km/h | -0,07% | 1,05 | **0,97** |
| Ost | 6 | 229 km/h | -0,42% | 3,01 | 0,77 (Vmax-nah, absolute Luecke nur ~0,03 m/s²) |

Alle nicht-Vmax-limitierten Gaenge (3,4,5 in beide Richtungen, 124-206 km/h)
landen bei **0,96-1,01** - eine sehr starke Bestaetigung der CdA/Crr/eta-Kette
des Modells ueber einen weiten Geschwindigkeitsbereich. Die beiden Vmax-nahen
Gang-6-Segmente bleiben der bekannte Schwierigkeitsfall (winzige Nettokraft
nahe dem Gleichgewicht macht die Verhaeltniszahl instabil bei kleiner absoluter
Differenz).

**Datenprodukte:** `data/elevation/crest_profile_2026-09-12_wot.npz` (Profile
pro Richtung: `s_m`, `elev_m`, `grade`, GPS-Referenzpunkte),
`results/precise_vmax_validation_2026-09-12.json`. Spezifisch fuer diese eine
Fahrt/Strecke - fuer eine andere Strecke muesste `crest_profile_wot_2026_09_12.py`
erneut mit anderen Zeitfenstern/Kacheln laufen.

## Praezisions-Steigung in segment_grade() selbst uebernommen (2026-09-13)

Auf Nutzerwunsch generalisiert: nicht nur dieses eine Kuppenereignis, sondern
alle Modelle, die in die Simulation einfliessen, sollen von der hoeheren
Aufloesung profitieren. Die Korridor-Technik aus `crest_profile_wot_2026_09_12.py`
ist jetzt fest in `top_speed_validation.segment_grade()` eingebaut - der
Funktion, die BEIDE Pipelines (`coastdown_analysis.py` fuer CdA/Crr-Fits,
`top_speed_validation.py` fuer Vmax-Checks) fuer jedes Segment ueber alle ~100
Logs aufrufen.

**Neue Funktion `_precise_grade_from_raw_points()`:** eigene GPS-Spur des
Segments als Mittellinie, 8m-Korridor, dominante Achse (Ost oder Nord, je nach
Spannweite) als Positionsachse. Faellt automatisch auf die alte 1m-Raster-
Methode zurueck, wenn eine beruehrte Kachel keine rohe ALS-Punktwolke hat, oder
das Segment zu kurz (<50m, vorher 200m) bzw. zu duenn besetzt ist.

**Neu in `elevation_model.py`:** `get_raw_ground_points(e_km, n_km)` - 8-Kacheln-
LRU-Cache fuer rohe ALS-Bodenpunkte (float32, ~150MB/Kachel), bewusst kleiner
als der bestehende 1m-Raster-Cache (64 Kacheln, ~4MB/Kachel je nach Groesse).

**Bug gefunden + behoben beim Testen:** die Positionsachse hatte einen ±50m-
Rand ueber die GPS-Spur hinaus (fuer den Glaettungsfenster-Rand), aber das
Binning nutzte `s = p_pos - pos_sorted[0]` - Punkte im Rand VOR dem Spurbeginn
bekamen negatives `s`, `np.digitize` warf sie alle in Bin 0, und
`bins[grouped.index-1]` fuer Index 0 ergab `bins[-1]` (Python-Negativindex)
statt `bins[0]` - verwuerfelte die Positions-Reihenfolge komplett. Fix:
`s = p_pos - p_pos.min()` (immer nicht-negativ). Durch Gegenpruefung gegen die
bereits bekannten West/Ost-Kuppenwerte sofort aufgefallen.

**Validiert:** die 7 urspruenglichen bidirektionalen Segmente durch die neu
generalisierte Funktion gejagt - liegen 0,02-0,05 Prozentpunkte von den alten
Werten entfernt (z.B. West Gang4: +0,755% neu vs. +0,77% alt), **Ost Gang4
(177m) bekommt jetzt erstmals ueberhaupt eine Steigung** (+0,202%) - die alte
200m-Mindestlaenge hatte das vorher ausgeschlossen.

**Voller Regressionslauf, beide Pipelines, alle ~100 Logs:** keine Abstuerze,
keine Verzerrung. `top_speed_validation.py`: 104 Segmente, 96 mit Steigungs-
korrektur, mehrere jetzt mit Quelle "Brandenburg-ALS-praezise". `coastdown_
analysis.py`: 14 Ausrollereignisse, ebenfalls teilweise aufgewertet. Bekanntes
Muster (Bayern-DGM1 unzuverlaessig, Steigungskorrektur hilft moderat) bleibt
erhalten. Tradeoff wie angekuendigt: pro Segment spuerbar langsamer (volle
Rohpunktwolke statt kompaktem Cache) - fuer gelegentliche Validierungslaeufe
akzeptabel, kein Hot-Path-Einsatz.

**Dritter Abgleich mit `top_speed_validation.py`** (spezialisiert auf Gang-6/
Vmax, regressionsbasierte Beschleunigung statt Endpunkt-Differenz, eigene
DGM-Gefaellekorrektur, bias-korrigiertes Modell aus `performance_simulation.py`):
fand dieselben zwei Segmente automatisch (nach demselben Silent-Bug-Fix in
`wot_gear6_candidate_mask()`, gleiches Muster wie oben - keine Regression,
104 Segmente insgesamt).

| | West (523,6-548,1s) | Ost (719,5-773,2s) |
|---|---|---|
| Gefaelle (DGM, Segment-Mittel) | -0,55% | -0,34% |
| a gemessen (Regression) | +0,119 m/s² | +0,106 m/s² |
| a Modell flach (bias-korr.) | -0,023 m/s² | -0,048 m/s² |
| a Modell +Gefaelle (bias-korr.) | **+0,031 m/s²** | -0,015 m/s² |
| Vmax bei diesem Gefaelle | **227,0 km/h** | 225,4 km/h |

West: Gefaellekorrektur bringt das Modell sehr nah an die Messung, berechnete
Vmax (227,0) trifft die tatsaechlich erreichte Hoechstgeschwindigkeit (228 km/h)
fast exakt. Ost: Korrektur hilft deutlich weniger, berechnete Vmax (225,4) liegt
klar unter der tatsaechlich erreichten 237 km/h. Erklaerung: Ost-Segment ist mit
53,6s/~3,3km viel laenger als West (24,5s) - der eine gemittelte Gefaellewert
verwaessert eine guenstigere (staerker abschuessige) Teilstrecke gegen Ende, wo
237 km/h erreicht wurden - eine Grenze der Ein-Wert-Gefaellekorrektur bei langen,
ungleichmaessigen Segmenten, kein Modellfehler.

## Nächste sinnvolle Schritte
1. ~~Y-Splitter-Kabel ankommen lassen, dann Abschnitt A aus `data/can/test_plan_2026-09-12.md`
   (OBD-Fusion + CAN gleichzeitig) für echte %-Referenzwerte (Kupplung, Tankfüllstand, MAF).~~
   **Erledigt für Kupplung + Tank (siehe oben), MAF weiterhin ungeklärt.**
2. ~~Standtests aus Testplan-Abschnitt B~~ **teilweise am 2026-09-12 durchgeführt, siehe
   "Standtests durchgeführt" oben.** Lenkrad-Anschläge erfolgreich eingefangen (±490°,
   Motor lief kurz vorher). Kupplung + Handbremse unbrauchbar, weil Motor während der
   Checkliste aus/ACC war (PCM still, `Parking_Brake` springt bei ACC fest auf "Applied").
   Tür-Test durch Tap-Timing-Drift uneindeutig. Wiederholen mit: Zündung durchgehend auf
   ON/Motor an, Start-Tap direkt bei der Handlung drücken, und die noch fehlenden Punkte
   (Blinker/Licht/Wischer, Tür links) ergänzen.
3. Fahrmanöver aus Testplan-Abschnitt C (mehrere Vollbremsungen für Rollover-Test, Schaltvorgänge
   in verschiedenen Drehzahlbereichen, Vollgas-Sprints, Kurven beidseitig).
4. Standard-OBD-Mode-1-Polling (PID 0x05/0x0F/0x2F) auf dem Pi ergänzen, für sauberen Tankfüllstand.
5. Sobald weitere CAN-Logs mit begleitendem GPS-Track vorliegen: Paar in `CAN_GPS_PAIRS`
   (`scripts/build_datalake.py`) ergänzen und `python scripts/build_datalake.py` neu laufen lassen.
6. Perspektivisch: verifizierte CAN-Signale (jetzt schon im Datalake: Drehzahl, Gas, Bremse,
   Lenkwinkel, Kupplung, Radgeschwindigkeiten) ins bestehende Fahrleistungsmodell einspeisen,
   für die geplante Querdynamik/Kurvenmodell-Erweiterung.
7. **Laufende Prüfung:** `EngineLoad_or_Torque_pct_maybe` (0x167, Formel `raw*3.17-185.3`)
   ist bisher nur an EINEM Log (2026-09-12 211851) kalibriert/geprüft (RMSE 5,65pp,
   R²=0,966) - keine Cross-Validation über mehrere Fahrten. Bei jedem künftigen Log mit
   gleichzeitig CAN und dem OBD-Kanal `ActualEnginePercentTorque` (selten, siehe
   Teillastmodell-Datenbasis): decodierten Wert gegen `ActualEnginePercentTorque`
   desselben Logs vergleichen (RMSE/R²) und hier nachtragen.

## Bremsmodell mit CAN-Daten angereichert (2026-09-13)

Auf Nutzerwunsch begonnen (siehe "weitere Modelle, die von CAN-Aufloesung
profitieren wuerden" oben): `scripts/braking_model.py` nutzt jetzt zusaetzlich
`CAN_LOG_PAIRS` (analog zu `CAN_GPS_PAIRS`, bisher nur `2026-09-12 211851.dlg`
<-> `candump-2026-09-12_211833`) und eine neue Funktion `_can_enrich_event()`:
pro Bremsereignis auf einem gepaarten Log zusaetzlich CAN-`VehicleSpeed`
(~50Hz statt OBD ~2-4Hz, regressionsbasierte Verzoegerung statt Zwei-Punkt-
Differenz), das heute erst korrekt kalibrierte `BrakePressure_CAN` (bar) als
unabhaengige Referenz neben `BFP_PRE_MZ` (kPa), und ein erster ABS-/Schlupf-
Indikator (max. Spreizung zwischen den 4 `WheelSpeed_CAN_*`-Kanaelen, kein
vollstaendiges ABS-Modell). Rein additiv (neue `can_*`-Felder) - die ~100
OBD-only-Logs bleiben unveraendert (Anreicherung wird uebersprungen, wenn kein
gepaartes CAN-Log existiert).

**Vorzeichenfehler beim Test sofort aufgefallen** (Gegenprobe gegen das
etablierte `obd_avg_decel_g`): `can_decel_g` hatte ein zusaetzliches
Vorzeichen-Flip - ergab zunaechst eine unsinnige Korrelation von -0,925
zwischen OBD und CAN, nach dem Fix +0,925. Gleiche Lektion wie bei der
Hoehen-Praezisionsarbeit: neue Kennzahl immer gegen eine bekannt-gute
gegenpruefen, bevor man ihr vertraut.

**Ergebnis ueber die 14 auswertbaren Bremsereignisse dieses einen gepaarten
Logs:**
- **Korrelation OBD- vs. CAN-Verzoegerung: 0,925, RMSE 0,048g** - beide
  Methoden bestaetigen sich gegenseitig, CAN liefert bei kurzen/schwachen
  Ereignissen die praezisere Zahl (mehr Stuetzpunkte fuer die Regression).
- `BrakePressure_CAN` (bar) folgt `BFP_PRE_MZ` (kPa) proportional ueber den
  ganzen beobachteten Bereich (5-44 bar / 470-4498 kPa).
- Radspreizung meist klein (0,2-1,8 km/h), ein 3,3 km/h-Ausreisser beim
  schwaechsten Ereignis (470 kPa/0,18g) - nicht ueberinterpretieren (nur ein
  Sample), aber im Auge behalten.

**Voller Regressionslauf ueber alle Logs** (987 Bremsereignisse insgesamt,
unveraendert fuer die CAN-losen Logs) - kein Absturz, keine Verzerrung.

**Naechster Schritt fuer dieses Modell:** wie bei Drivetrain/Coastdown wirkt
sich das erst auf zukuenftige CAN-gepaarte Fahrten aus - fuer eine breitere
Wirkung braucht es mehr Sessions mit laufendem CAN-Logger waehrend echter
Bremsmanoever (idealerweise auch eine Vollbremsung mit ABS-Eingriff, um den
Radspreizungs-Indikator an einem eindeutigen Fall zu pruefen).

## Drivetrainmodell: Steigungs-/Windkorrektur auf alle Gaenge/Logs erweitert (2026-09-13)

`drivetrain_model_validation.py`s breiterer WOT-Check (alle Gaenge, alle Logs -
bisher nur der spezialisierte Gang-6/Vmax-Zwilling `top_speed_validation.py`
hatte die Korrektur) nutzt jetzt optional dieselbe `segment_grade()`+Wind-
Korrektur. Neue `_grade_and_tailwind()`-Hilfsfunktion mit Lazy-Imports (um
einen Zirkelimport zu vermeiden - `top_speed_validation.py` und
`coastdown_analysis.py` importieren beide bereits VON diesem Modul).
`a_model_ms2` (flach) bleibt fuer Ruecktkompatibilitaet unveraendert, neue
Felder `a_model_corrected_ms2`/`grade_pct`/`tailwind_ms`.

**Ergebnis ueber alle 166 Segmente/alle Logs: Korrektur bewegt das
Gesamtbild kaum** (Median-Verhaeltnis weiterhin 1,00, Std weiterhin 0,14;
Gang-Mediane verschieben sich um hoechstens 0,02: Gang 2 bleibt 0,89, Gang 6
geht 1,12->1,09). **Das ist ein aussagekraeftiges Negativergebnis:** bestaetigt,
dass das bekannte Gang-abhaengige Muster eine echte Modelleigenschaft ist
(CdA/eta/Drehmomentkurve nicht perfekt gang-unabhaengig), nicht ein Artefakt
ignorierter Steigung/Wind - anders als beim einen kontrollierten
bidirektionalen Ereignis (ein langer Anstieg + konstanter Seitenwind, wo die
Korrektur stark half) verteilen sich Steigung/Wind im grossen, ueberwiegend
kurzen/opportunistischen Datensatz ueber viele verschiedene Orte/Zeiten und
heben sich im Aggregat weitgehend auf. Zeigt gut, warum der kontrollierte
bidirektionale Test als eigene Untersuchung lohnend war - er isoliert einen
Effekt, der in der Aggregatstatistik unsichtbar bleibt.

## TPMS (Reifendruck/-temperatur) per aktivem UDS-Request erschlossen (2026-09-13)

Reifendruck/-temperatur werden nicht periodisch gebroadcastet (mehrfach erfolglos gesucht,
siehe oben) - anders als der Rest dieses Projekts (reines passives Mitlesen) braucht das
einen **aktiven** Diagnose-Request. Nutzer hat die PIDs bereits eigenstaendig per Handy-OBD-
Adapter live getestet und funktionierende Werte gefunden, ein ND-spezifisches Forum
(mx5-nd-forum.de) bestaetigte dieselben PIDs unabhaengig:

- Header `0x720` (Kombiinstrument), Mode `0x22` (ReadDataByIdentifier)
- Druck-PIDs `0x2A05`-`0x2A08` (Tire1-4), Formel `(raw*1373/1000)/100` → bar
- Temperatur-PIDs `0x2A0A`-`0x2A0D` (Tire1-4), Formel `raw-50` → °C (nur vom Nutzer per OBD
  getestet, noch nie als eigener CAN-Frame beobachtet - bleibt `_maybe`)

**Offene Frage aus dem Forenbericht widerlegt:** das Forum behauptet, Header 0x720 sei
"nur ans MS-CAN angebunden" - unsere Verkabelung haengt aber nur an HS-CAN (`can0`, Pins
6/14). Direkt am **Y-Splitter-Log** (`candump-2026-09-12_211833.log`, OBD+CAN gleichzeitig)
geprueft: im exakt selben Zeitfenster, in dem OBD-Fusion die vier `WU1-4_P_TPM_IC`-Werte
loggt (166.1/179.9/175.7/192.2 kPa), erscheinen im candump-Log Request/Antwort-Paare
`720#03222A05...`/`728#04622A0583...` usw. - **byte-genau** deckungsgleich mit den erwarteten
Formeln (z.B. raw=0x83=131 -> 1.79863 bar, exakt der OBD-Wert). Das beantwortet gleich zwei
offene Punkte: **HS-CAN reicht aus** (kein zweiter MS-CAN-Tap noetig) und die **Antwort-ID ist
`0x728`** (Request+8, vorher nur vermutet).

**Rad-Zuordnung teilweise geklaert:** Nutzer berichtete fuer denselben Zeitpunkt reale
Reifendruecke "hinten rechts=1,9 bar, hinten links=1,7 bar" (gerundet). Abgleich mit den
dekodierten Werten: **Tire3 (DID 0x2A07) = 1.661 bar = Hinten Links**, **Tire4 (DID 0x2A08)
= 1.922 bar = Hinten Rechts**. Tire1 (1.799 bar) und Tire2 (1.757 bar) sind damit die
Vorderachse, aber die Reihenfolge (welches vorne-links vs. vorne-rechts ist) folgt nur der
Vermutung "Nummerierung 1-2-3-4 = VL-VR-HL-HR" - noch NICHT durch einen gezielten Test
(z.B. Luft an einem einzelnen Vorderrad ablassen) bestaetigt.

**Gebaut:**
- `scripts/tpms_poller.py` - sendet die 8 UDS-Requests aktiv auf `can0`, ISO-TP-Single-Frame
  (kein `isotp`-Paket noetig, Request/Antwort passen beide in einen CAN-Frame). Eigener
  Selbsttest `scripts/test_tpms_poller.py` fuer Framing/Decoding ohne echten Bus.
- `data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc`: neue Botschaft `BO_ 1832` (0x728),
  gemultiplext nach DID (analog zum bestehenden `HS_IC_CentralConfig`-Muster bei 0x40A) -
  gegen das komplette Y-Splitter-Log verifiziert (`can_log_parser.py` dekodiert alle Frames
  fehlerfrei, Werte exakt wie oben).
- `session_logger.py` startet `tpms_poller.py` jetzt automatisch als zweiten Kindprozess
  bei jeder Fahrt (gleicher KeyState-Trigger wie candump) - die Antwort-Frames landen wie
  jeder andere Frame im ohnehin laufenden candump-Log, kein separates TPMS-Log noetig.
  Alle drei Dateien per `scp` auf den Pi deployt, noch nicht live am Fahrzeug getestet
  (Pi/Adapter beim Schreiben dieses Eintrags nicht am Auto).
- **Poll-Intervall bewusst auf 120s begrenzt** (`TPMS_POLL_INTERVAL_S` in `session_logger.py`,
  `--interval`-Default in `tpms_poller.py`) - Reifendruck aendert sich langsam, Nutzerwunsch
  war, Bus/OBD-Schnittstelle so wenig wie moeglich mit eigenen Requests zu belasten.

**Naechster Schritt:** bei der naechsten Fahrt pruefen, ob `tpms_poller.py` automatisch
mitlaeuft und die TPMS-Werte im neuen Log auftauchen; danach Vorderachsen-Zuordnung (Tire1
vs. Tire2 = vorne-links/-rechts) per gezieltem Luftablass-Test klaeren, und Temperatur-PIDs
einmal live gegen einen echten CAN-Capture bestaetigen (bisher nur die Druck-PIDs).

**Kollisionsfrage geklaert (2026-09-13): eigener Sender vs. Handy-OBD-Adapter am Y-Kabel.**
Nutzer will das Y-Splitter-Setup (Handy + Pi gleichzeitig am OBD-Port) dauerhaft beibehalten
und fragte, ob unser aktiver TPMS-Request mit dem Handy kollidieren kann. Kompletten
OBD-Bereich des Y-Splitter-Logs dafuer ausgewertet (nicht nur das TPMS-Fenster):
- Alle 80 Requests auf `0x720` in der ganzen 20-Minuten-Fahrt liegen in einem einzigen
  ~22s-Fenster (max. Luecke 1,25s dazwischen) - davor/danach null Traffic. Bestaetigt: TPMS
  ist ein einmaliger, vom Nutzer manuell ausgeloester Dashboard-Aufruf der App, kein
  Bestandteil der normalen Polling-Liste.
- Das Handy selbst sendet waehrend der GESAMTEN Fahrt kontinuierlich Mode-22-Requests:
  `0x7E0`/`0x7E8` (Standardkette: ETC_ACT, AFR_MZ, ActualEnginePercentTorque, MassAirFlowRate,
  TimingAdvance, CommandEquivalenceRatio, Speed, Kupplung, Bremsdruck, ...) mit **14.737
  Requests ueber 1199s (~12,3/s)**, plus `0x730`/`0x738` (2 alternierende PIDs) mit **4.881
  Requests (~4,1/s)** - macht ~16-17 Requests/Sekunde, die ganze Zeit, nur vom Handy.
- Unser geplanter Poller (4 Requests alle 120s ≈ 0,03/s) auf einem Header, den das Handy im
  Normalfall gar nicht anfasst, ist dagegen vernachlaessigbar. Einzige verbleibende (sehr
  kleine) Kollisionsgefahr: Nutzer oeffnet manuell das TPMS-Dashboard, waehrend der Poller
  zufaellig zeitgleich auf `0x720` sendet - CAN-Arbitrierung ist bei UNTERSCHIEDLICHEN IDs
  non-destruktiv/selbstheilend, nur bei IDENTISCHER ID + gleichzeitigem Sendebeginn mit
  unterschiedlichen Daten koennte es zu einem einzelnen Bitfehler kommen (beide Seiten
  wiederholen automatisch). Setup bleibt wie gehabt (Y-Splitter dauerhaft dran), keine
  Aenderung noetig - Nutzer loest TPMS-Dashboard weiterhin manuell aus, nicht automatisiert.

## TPMS-Anzeige im Touchdisplay-GUI ergaenzt (2026-09-13)

`scripts/status_gui.py` bekam eine neue `TpmsCornersPanel`-Klasse: Reifendruck (bar, gross)
+ Temperatur (°C) in den vier Bildschirmecken, an der Radposition ausgerichtet (oben=vorne,
unten=hinten) - nutzt `place()` statt `pack()`, damit die Ecken unabhaengig vom mittigen
Status-Layout auf dem ganzen Fenster sitzen. "Vorne Links"/"Vorne Rechts" tragen ein "*" im
Label als staendige Erinnerung, dass diese Zuordnung nur eine Vermutung ist (siehe TPMS-
Abschnitt oben) - "Hinten Links"/"Hinten Rechts" sind dagegen bestaetigt und ohne Markierung.
Werte gelten 200s als aktuell (`TPMS_STALE_S`, grosszuegiger als die sonst ueblichen 2s
`LIVE_STALE_S`), passend zum 120s-Poll-Intervall - kein staendiges Ausgrauen zwischen den
Abfragen. Erscheint/verschwindet zusammen mit den anderen Live-Panels, nur waehrend
"LOGGING LAEUFT".

**Verifiziert per Simulation (gleiche Methode wie schon beim urspruenglichen GUI-Bau):**
`vcan0` auf dem Pi aufgesetzt, synthetische `728#...`-Antwort-Frames fuer alle 8 Signale
(4x Druck, 4x Temperatur) per `cansend` injiziert, `MX5_SIMULATE_STATE=LOGGING
MX5_CAN_CHANNEL=vcan0` gestartet, echten Screenshot vom Display gezogen (`grim`) - zeigt
exakt die erwarteten Werte in allen vier Ecken (z.B. raw=0x83 -> 1.80 bar bei "Vorne Links").
Danach `vcan0` geloescht und der normale Prozess (echtes `can0`, kein Simulations-Flag)
wiederhergestellt. Noch nicht mit echten TPMS-Antworten vom Fahrzeug getestet (dafuer muss
zuerst der Poller selbst einmal live laufen, siehe "Naechster Schritt" oben).

## TPMS erstmals live ausgewertet & zweiter No-RTC-Uhr-Bug gefunden (2026-09-14)

Nutzerfrage "können wir uns die Reifendruck-Daten anschauen?" stieß die erste echte Auswertung
an - `tpms_poller.py`/`session_logger.py` liefen zwar schon länger automatisch mit, wurden aber
nie ausgewertet. 0x720/0x728-Frames sind zuverlässig in den Logs (~192-200 pro Fahrt, exakt
Fahrtdauer/120s · 8 PIDs).

**Neues Skript `scripts/tpms_log_decode.py`** (parst 0x728-Antworten direkt aus einem
candump-Log, wiederverwendet `tpms_poller.py`s PID-Formeln/`decode_response()` als einzige
Quelle der Wahrheit). Dabei aufgefallen: die DBC (`BO_1832`) dekodiert die TPMS-Signale schon
korrekt in die `*_decoded.csv`, aber `build_datalake.py`s `CAN_SIGNAL_MAP` hatte sie nie
aufgenommen - landeten also nirgends im Datalake. Nachgetragen (`TirePressure_CAN_Tire1-4` bar,
`TireTemp_CAN_Tire1-4` °C), Datalake neu gebaut.

**Ergebnis (2 Fahrten):** physikalisch sauberes Bild - Druck steigt mit Temperatur (ideales
Gasgesetz, 22-40°C), erste 2-3 Samples pro Fahrt zeigen oft noch "alte" warme Werte vom
Sensor, bevor er nach etwas Raddrehung frische, oft kältere Werte sendet (erwartetes
Sensorverhalten). **Tire4 (hinten rechts) liegt in beiden Fahrten durchgängig ~0,15-0,2 bar
über den anderen drei Reifen** - konsistent, kein Ausreißer, dem Nutzer noch nicht bestätigt.

**Zweiter No-RTC-Uhr-Bug gefunden, diesmal ohne erkennbaren Zeitsprung im Log.** Zwei CAN-only-
Logs (ursprünglich `candump-2026-09-13_135400`/`144611` datiert) hatten laut Nutzer-Hinweis
vermutlich das falsche Datum. Bestätigt per Dauer- UND VehicleSpeed-Kreuzkorrelation
(r=1,0000 bzw. r=0,9997) gegen die zwei OBD-Logs vom Nachmittag des 14.09.: beide CAN-Logs
waren ~2:43-2:45h zu früh datiert (fast identischer Versatz in beiden Fällen - ein einziger,
konsistenter Uhrenfehler für die ganze Pi-Session). Grund: der Pi hat keine RTC und bekam
während dieser Session nie NTP (Powerbank im Auto, kein WLAN unterwegs) - die Uhr bleibt dann
für die GANZE Session falsch, OHNE den sonst erkennbaren Zeitsprung mitten im Log zu zeigen
(der nur bei einer NTP-Korrektur MITTEN im Log entsteht). Bestätigt per `uptime -p` auf dem Pi
(`journalctl --list-boots` hatte fälschlich einen durchgehenden Boot seit dem 11.09. behauptet,
echte monotone Uptime war nur 65 Minuten).

**Korrektur durchgeführt:** Dateien umbenannt (lokal UND auf dem Pi) auf die wahren Zeitstempel
(`candump-2026-09-14_163711.log`/`candump-2026-09-14_173057.log`), `data/can_gps_pairs.json`
nachgezogen, Datalake + Kurven-/TPMS-Auswertung neu gelaufen - alle Werte identisch zu vorher,
nur unter korrektem Namen. Rohdateien selbst wurden nicht inhaltlich verändert, nur umbenannt.
Damit ist auch klar: `tpms_poller.py` lief zum ersten Mal am 14.09. morgens (`081105`), dann
nochmal nachmittags (`163711`/`173057`) - nicht schon am 13.09. wie zunächst angenommen.

**Lehre:** jeder CAN-only-Log ohne GPS-/OBD-Zeitanker ist grundsätzlich mit Vorsicht zu
behandeln, egal ob ein Zeitsprung sichtbar ist oder nicht - fehlendes NTP während der ganzen
Session zeigt sich NICHT als Sprung. Details in der `mx5_can_bus_logging`-Memory.

## Pi-Touchdisplay-Gauge-Lag gefunden und gefixt (2026-09-14)

Nutzer meldete sichtbare Verzögerung (>2s) bei den Gas/Bremse/Kupplung/Lenkwinkel/Speed-
Balkenanzeigen auf dem Pi-Touchdisplay. Per SSH auf `car` diagnostiziert: Hardware unauffällig
(kein Throttling, 50°C, Load niedrig), deployte Version identisch zum Repo. Zwei echte
Ursachen im Code:
1. `get_state()` forkte JEDE Sekunde zwei `pgrep -f ...`-Subprozesse direkt im Tk-`.after()`-
   Callback - blockierte dabei die komplette Mainloop inkl. aller 300ms-Gauge-Refreshes für die
   fork()+exec()-Dauer, verschärft durch I/O-Last von candump/session_logger/tpms_poller
   während einer Fahrt.
2. `LiveCanValues._run()` öffnete den SocketCAN-Bus ohne Filter - jeder Frame (nicht nur die
   ~17 angezeigten IDs) wurde per cantools in Python dekodiert, unnötige CPU-Last im
   Hintergrundthread (konkurriert um die GIL mit der Mainloop).

Der Zufallstreffer: `LIVE_STALE_S=2.0` (Schwelle für "ausgegraut") erklärt, warum der Nutzer
die Verzögerung genau bei ">2s" verortete - ein Mainloop-Stall zeigt sich nicht als sanfter
Nachlauf, sondern als eingefrorener Wert bis zum Ausgrauen, dann Sprung auf aktuell.

**Fix (committed, deployed):** `process_running()` liest jetzt `/proc/*/cmdline` direkt (kein
Subprozess-Fork mehr). `can.interface.Bus(...)` bekommt jetzt `can_filters` mit
`NEEDED_CAN_IDS` (Kernel-seitiger `SO_CAN_RAW_FILTER`, aus LIVE_SIGNALS/PEDAL_GAUGES/
BRAKE_PCT_CAN_ID/TPMS_CAN_ID zusammengesetzt). Deployt und auf dem Pi neu gestartet, läuft
fehlerfrei - aber die eigentliche Verbesserung ist noch NICHT bei einer echten Fahrt
verifiziert.

## CAN-Byte-Search-Projekt: OBD direkt aus dem CAN-Log, neues Sweep-Tool, 3 neue Signale, SteeringAngle_related-Rätsel gelöst (2026-09-14)

Ausgangsfrage: wie kommen wir systematisch an weitere Signale in den 107 komplett leeren und
39 teilweise dekodierten DBC-Botschaften? Die letzte systematische Suche war vom 11.09. und
nie als wiederverwendbares Werkzeug verstetigt. Vollständiger Plan und alle Zwischenschritte
in der `mx5_can_bus_logging`-Memory; hier nur die Kurzfassung der Ergebnisse.

**Schlüsseleinsicht (Nutzer-Vorschlag):** das Handy-OBD-Fusion sendet seine Mode-1/Mode-22-
Anfragen über denselben physischen Bus, den auch der CANable-Adapter mitschneidet - immer
wenn das Y-Splitter-Kabel benutzt wird, stehen die `0x7E0`/`0x7E8`-Frames also direkt IM
CAN-Log. Geprüft: das betrifft inzwischen 4 von 14 nicht-leeren Logs (nicht nur die eine
bekannte Kalibrierfahrt) - das Y-Kabel ist offenbar seit dem 14.09. Standard bei jeder Fahrt.
Das ersetzt die bisherige Zeitsynchronisation zweier unabhängig getakteter Dateien (CAN-Log +
`.dlg`, per geschätztem Kreuzkorrelations-Lag) durch framegenaue Ground-Truth auf derselben
Pi-Uhr.

**Gebaute Werkzeuge (alle in `scripts/`, committed):**
- `obd_from_can.py` - dekodiert UDS-Request/Response-Paare direkt aus jedem CAN-Log.
- `map_obd_dids.py` - ordnet die gefundenen DIDs benannten OBD-Kanälen zu (per Lag-gesuchter
  Regression gegen die `.dlg`, einmaliger Bootstrap-Schritt - danach `.dlg`-unabhängig). 6
  DIDs über 4 Logs identisch bestätigt: `AFR_MZ`, `BFP_PRE_MZ`, `ETC_ACT`, `CPP_PER_MZ`, `FLI`
  (alle mit sauberer Fixed-Point-Formel, R²=0,99+), plus `TM_GEST` (vermutlich eine Bitmask).
- `can_byte_search.py` - das eigentliche wiederverwendbare Sweep-Tool: DBC-Coverage-Diff,
  Pearson+Spearman roh UND detrended (ein Kandidat zählt nur, wenn BEIDES die Schwelle r≥0,6
  übersteigt - Schutz gegen die bekannten Trend-Artefakte), abgeleitete Ereignis-Proxys
  (ABS-/DSC-Verdacht aus Radgeschwindigkeits-Divergenz bzw. Querbeschleunigung), Selbsttest.
- `can_retest_maybe_signals.py` - testet den bestehenden `_maybe`/`_related`-Rückstand gegen
  die neue OBD-Ground-Truth.
- `can_steering_angle_0x86.py` - nichtlineare Umrechnung für `SteeringAngle_related` (siehe
  unten).

**Sweep über alle 5 datenreichen Logs, konsolidiert:** mehrere Kandidaten 4-5/5 Logs
cross-log-bestätigt (`0x078`→Gierrate, `0x200`→Last-Proxy, `0x0FD`/`0x20A`→AFR-Proxy,
`0x086`→Lenkwinkel, `0x42B`→Drehzahl-verwandt). Drei davon nach Prüfung VERWORFEN und in der
DBC als "geprüft, nicht bestätigt" dokumentiert (0x0FD/0x20A: Rohwert zu grobkörnig bzw.
Vorzeichen-Artefakte; ein Radgeschwindigkeits-Cluster erwies sich bei Gegenprüfung an zwei
weiteren Logs als reines Kurzlog-Trendartefakt, r fiel von 0,97 auf -0,06).

**Drei neue Signale in die DBC übernommen:**
- `YawRate_related` (0x78 Byte2-3) - eigenständiges ABS-Modul-Gierratensignal, `≈0,0011·raw`
  deg/s, R²=0,91-0,96.
- `SteeringAngle_related_3` (0x86 Byte4-5) - zweites, unabhängiges Lenkwinkelsignal in
  derselben Botschaft, r=0,88 mit dem echten Winkel.
- `EngineRPM_related_3_maybe` (0x42B, vorher komplett leer) - drehzahlverwandt, aber nur
  R²=0,44, kein reines Duplikat des bekannten "Drehzahl×2"-Signals auf 0x130.

**Echter Formel-Fix:** `YawRate_Corr` (0x79) hatte eine nie überprüfte Formel direkt aus der
ursprünglichen Community-DBC - falsch skaliert. Per Regression gegen `YawRate_Raw` neu
kalibriert (`(0,133,-68,1)`, R²=0,89-0,92, cross-log r=0,94-1,00 über alle 4 Logs) - anders
als seine bekanntermaßen kaputten Geschwister `Longi_Acc_Corr`/`Lateral_Acc_Corr_maybe` war
dieses Signal nur falsch kalibriert, nicht grundsätzlich fehlerhaft.

**Zwei Cross-Validation-Lücken geschlossen:** `EngineLoad_or_Torque_pct_maybe` (0x167) und
`EngineLoad_related_maybe` (0x200) waren beide nur an einem einzigen Log kalibriert (explizit
als offener Punkt vermerkt) - jetzt an 3-4 von 4 Logs gegen das Gaspedal bestätigt.

**Größter Einzelfund: `SteeringAngle_related`-Rätsel gelöst.** Die seit Wochen offene Frage
"warum bleibt dieses Signal schwach korreliert (r≈0,56) trotz bestätigt korrekter
Bit-Position" ist ein Messmethoden-Artefakt, kein reales schwaches Signal. Mit Spearman statt
Pearson gegen den echten Lenkwinkel: r=0,987. Gebinnte Analyse zeigt eine klar monotone, aber
stark nichtlineare Kurve (sehr flach/grobauflösend um 0°, deutlich steiler außen) - derselbe
Effekt wie beim `ETC_ACT`-Sättigungsfund vom 12.09. (Pearson unterschätzt sättigende
Zusammenhänge systematisch), hier aber nie zurückangewendet. Bit-Position war die ganze Zeit
richtig, nur die Bewertungsmethode falsch.

Darauf aufbauend eine echte nichtlineare Umrechnung gebaut (Isotonic-Regression-Lookup-
Tabelle). Erster Versuch (nur der Lenkrad-Schwenk-Log als Training) scheiterte krachend
(Out-of-Sample-R²=-0,39) - der Schwenk-Log deckt zwar den vollen Winkelbereich ab, aber
extrem ungleichmäßig (lange Verweildauer an den Anschlägen, kaum Samples im
Übergangsbereich). Fix: Schwenk-Log (für die Extreme) plus zwei normale Fahrten (für dichte
Alltagsabdeckung) kombiniert trainiert. Out-of-Sample-Validierung auf einem komplett
unbeteiligten Log: **R²=0,90, RMSE=10,5°.**

**Datalake neu gebaut** mit der erweiterten/korrigierten DBC - alle 11 im Datalake genutzten
CAN-Logs neu decodiert, `data/datalake.duckdb` neu erstellt. `CAN_SIGNAL_MAP` (die kuratierte
Liste, die tatsächlich zu Datalake-Spalten wird) bewusst NICHT um die neuen/reparierten
Signale erweitert - folgt der bestehenden Konvention, dass ein Signal in der DBC dekodierbar
zu sein nicht automatisch heißt, dass es einen eigenen Datalake-Kanal verdient (z.B. wurde das
bestätigte "Drehzahl×2"-Duplikat auf 0x130 auch nie aufgenommen).

**Plan-Status:** alle Kernphasen (OBD-aus-CAN-Decoder, DID-Zuordnung, Sweep-Tool, Rückstands-
Check, Validierung/Einpflegen) abgeschlossen. Eine externe Mode-22-Formeltabelle wurde bewusst
übersprungen (Aufwand/Nutzen für die 5 seltenen DIDs nicht gerechtfertigt). Offen bleibt nur
eine gezielte Testfahrt für den ABS/DSC-Eingriffsindikator und die Reverse-Gang-Bestätigung -
das braucht echte neue Daten, keine weitere Log-Analyse.

Alle Zwischenergebnisse, Formeln und verworfenen Kandidaten sind zusätzlich direkt als
`CM_`-Kommentare in `data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc` dokumentiert. Commit
`f6b4ea2`.

## OBD-Fusion-App: wie viele Datenpunkte finden wir im CAN wieder? (2026-09-14)

Nutzerfrage: die App zeigt aktuell ~32 PIDs an – wie viele davon finden wir im CAN wieder?
Tatsächlich zeigt die App (konsistent in allen drei 09-14-Logs) **20 durchgehend
aktualisierte Fahrzeug-Kanäle**, nicht 32. Aufschlüsselung nach tatsächlichem Ursprung:

- **6 echte, häufig abgefragte Mode-22-DIDs**: AFR_MZ, BFP_PRE_MZ, ETC_ACT, CPP_PER_MZ,
  TM_GEST, FLI (siehe oben, "Bekannte offene Punkte" für den AFR_MZ/ETC_ACT/TM_GEST-Status).
- **2 echte, aber seltene DIDs** (~alle 10-30s): ActualEnginePercentTorque (0x032B, deckt
  sich mit `EngineLoad_or_Torque_pct_maybe`@0x167), ~~VehicleOdometerReading (0x1310)~~ - **2026-09-15
  WIDERLEGT, siehe unten: 0x1310 ist die OELTEMPERATUR.**
- **2 Kanäle, die GAR KEINE OBD-Anfrage sind** – die App liest hier nachweislich selbst
  nativ CAN mit, exakt wie wir: `VehicleSpeed` korreliert mit unserem `0x202.VehicleSpeed`
  bei r=0,9998, `STEER_ANGL_EPS` mit unserem `0x82.Steering_Wheel_Absolute_Angle` bei
  r=0,98. Bestätigt (auf Nutzer-Vermutung hin geprüft): OBD-Fusion hat für diese beiden
  eigenes, unabhängiges Reverse-Engineering des Mazda-CAN.
- **~9-10 clientseitig berechnete Werte, keine eigene Anfrage**: FuelRate,
  InstantFuelEconomy, TotalFuelEconomy, CO2Flow, InstantCO2Rate, TotalCO2,
  MassAirFlowRate, CommandEquivalenceRatio, TimingAdvance, vermutlich auch STEER_SPD_EPS
  (Ableitung von STEER_ANGL_EPS). Gegen **alle 147 uns bekannten nativen CAN-Signale**
  korreliert (nicht nur die OBD-Anker) – bester Treffer war nur 0,87-0,88 mit dem Gaspedal
  (physikalische Kausalität: Verbrauch hängt vom Gasgeben ab, kein gemeinsames Signal) bzw.
  0,97-0,99 mit dem Kilometerstand für die "Total"-Werte (Scheinkorrelation zweier über eine
  Fahrt hinweg monoton steigender Größen). Keiner kommt in die Nähe der r>0,95, die die
  echten Direktlesungen zeigen – erklärt nebenbei endgültig, warum frühere gezielte Suchen
  nach `CommandEquivalenceRatio`/`TimingAdvance` nie einen Treffer fanden: die existieren
  gar nicht unabhängig auf dem Bus.

**Nutzer-Einschätzung: "Problem noch nicht gelöst, weiter untersuchen"** – zu Recht.
**2026-09-15 aufgelöst, und zwar gegen die obige Analyse:** die App liest nichts nativ vom
CAN und berechnet auch nichts clientseitig. Alle diese Kanäle sind echte OBD-Anfragen – wir
konnten sie nur nicht sehen, weil `obd_from_can.py` (a) nur den Header `0x7E0/0x7E8`
dekodierte und den EPS-Verkehr auf `0x730/0x738` (10.820 Frames pro Log!) ignorierte und
(b) ISO-TP-Multiframe-Antworten verwarf, in denen die App fünf Mode-1-PIDs gebündelt
abfragt. Beides ist behoben; siehe Abschnitt "Deep-Search-Projekt" unten. Damit sind
VehicleSpeed, STEER_ANGL_EPS, STEER_SPD_EPS, MassAirFlowRate, CommandEquivalenceRatio
(Lambda), TimingAdvance und ActualEnginePercentTorque als echte Messwerte verfügbar.

## Externe Quelle github.com/gitgc/mx5-miata-nd2-obd-can (2026-09-15)

Community-Dokumentation für den ND2 MX-5 (2020-2021, RaceChrono-Konfiguration statt DBC) –
dokumentiert Rohbyte-Formeln statt einer DBC-Datei. Andere Baujahre als unser ND, aber
identische Botschaftsstruktur (PID-Zahlen sind einfach die Dezimaldarstellung unserer
bekannten CAN-IDs, z.B. PID 514 = 0x202). Alle Formeln gegen unsere eigenen Logs (nicht
blind übernommen) geprüft:

- **AmbientTemp (0x420, Byte6-7) – GELÖST, DBC korrigiert.** Bisherige DBC-Formel war
  unsigned `(0,25,-3200)` und lieferte durchgehend unplausible ~103-126°C. Externe Quelle
  zeigt: Rohwert muss SIGNED interpretiert werden. Neu kalibriert: `(0,0025, +32)` signed –
  über 3 Logs konsistent plausibel (3,8-25,9°C, meist konstant ~25,8°C). Gleicher Bug-Typ
  wie der frühere BrakePressure-Fix (unsigned statt signed plus falsche Konstanten).
- **Kupplungs-Flag (0x050 Byte3) – hochgestuft von Vermutung zu bestätigt.** Bereits am
  2026-09-11 gefunden (Byte3 korreliert binär mit Clutch_Pedal_Position_raw), damals aber
  der Verdacht "ist wahrscheinlich nur das schon bekannte StarterInterLockSW-Bit, kein
  Zusatzsignal". Externe Quelle dokumentiert Byte3 als GANZES als eigenständiges binäres
  Kupplungspedal-Flag (Rohwert 1=frei, 2=getreten) – über alle 3 verfügbaren Logs bestätigt
  (r=0,86-0,90 gegen die Analogposition, Werteverteilung durchgehend nur {1,2}). Kein
  eigenes `SG_` ergänzt (überlappt mit StarterInterLockSW@Bit25, cantools würde die
  Botschaft sonst nicht mehr dekodieren) – Fund als `CM_`-Kommentar dokumentiert.
- **Fuel Level (0x43F "Fuel_Related") – vielversprechend, NICHT übernommen.** Als volle
  16-Bit-Lesung (statt der bisherigen ungenutzten 10-Bit-Rohdefinition aus dem
  Original-Community-DBC) korreliert der Rohwert mit der OBD-Referenz FLI in allen 3
  Logs mit |r|=0,86-0,95 – aber das **Vorzeichen kippt zwischen den Logs** (+0,95 im
  ersten, -0,86/-0,88 in den anderen zwei). Klassisches Warnsignal für eine über kurze
  Fahrten (Tankstand ändert sich pro Fahrt kaum) instabile Korrelation, nicht zwingend
  ein falsches Signal. Byte-Lage vermutlich richtig, Kalibrierung aber noch offen –
  braucht Logs mit echter Tankstandsänderung (z.B. vor/nach dem Tanken).
- **Lenkwinkel (0x86) – externe lineare Formel getestet und verworfen, bestätigt unser
  eigenes Modell.** Gegen unsere Referenz (Steering_Wheel_Absolute_Angle@0x82): Spearman
  r=-0,99 (starke monotone Übereinstimmung, nur Vorzeichen gedreht), aber linearer Fit nur
  R²=0,25. Bestätigt (statt widerlegt) unseren eigenen Befund, dass dieses Signal stark
  nichtlinear ist – die externe lineare Formel taugt für unser Fahrzeug nicht, unser
  Isotonic-Regression-Modell (`scripts/can_steering_angle_0x86.py`) bleibt die richtige
  Lösung.
- **Wheel Speed (0x215) – unabhängige Bestätigung, nichts Neues.** Externe Formel nutzt
  Offset -10000 in Rohwert-Einheiten, was exakt unserem eigenen bestätigten Offset -100
  (in km/h-Einheiten, gleiche Struktur) entspricht – schöne Kreuzvalidierung unseres
  bereits bestätigten Fundes.
- **Gear (0x165 Byte6-7) – geprüft, nicht übernommen.** Nur r=-0,48 gegen EngineRPM, sehr
  hohe Kardinalität (2569 unterschiedliche Werte) – sieht nicht nach diskreten
  Gang-Stufen aus, eher eine andere, nicht identifizierte Größe. Externe Quelle nutzt es
  selbst nur indirekt (RPM-artiger Wert, manuell in Gang-Stufen gebucketed) – kein klarer
  Gewinn für uns, nicht weiterverfolgt.
- Nicht getestet (niedrige Priorität): Brems-Pedal-% als 12-Bit-Feld bei Bit 28 in 0x78
  (andere Größe als unsere BrakePressure, überschneidet sich vermutlich bitweise), TPMS-
  Druckformel `(raw·1,373)+20 kPa` (wir haben schon eine funktionierende TPMS-Kalibrierung).

## Deep-Search-Projekt: opendbc, referenzfreie Segmentierung, verworfener UDS-Verkehr (2026-09-15)

Systematische Online-Recherche mit dem Ziel, moeglichst viele weitere Daten aus den
vorhandenen Logs zu dekodieren. Vollstaendiger Plan, alle sechs Tracks und die
Detailergebnisse in [`mx5_can_deep_search_plan.md`](mx5_can_deep_search_plan.md); hier die
Kurzfassung. **Zwei bisher als abgeschlossen gefuehrte Befunde wurden dabei widerlegt.**

### 1. opendbc (comma.ai) ist die groesste bisher ungenutzte Fremdquelle
`mazda_2017.dbc` (CX-5/Mazda3 ab MY2017) nutzt **exakt dieselben CAN-IDs wie unser ND** -
gleiche SkyActiv-Busarchitektur, 84 von 102 IDs identisch. Bei **58 davon hat opendbc mehr
Signale als wir**, darunter bei uns komplett leere Botschaften: `0x415 TRACTION`
(Warnleuchten-Flags: ABS_MALFUNCTION, DSC_OFF, BRAKE_WARNING, TCS_DCS_MALFUNCTION),
`0x217 CURVE_CTRS`, `0x21F CRZ_EVENTS`, `0x340 SEATBELT`, `0x436 HVAC`, `0x242-0x246`
(Frontkamera/Spurdaten). Radar (`0x361-0x366`) hat unser Fahrzeug nicht.

Neues Werkzeug `scripts/can_opendbc_crosscheck.py` dekodiert jedes Fremdsignal aus unseren
eigenen Logs und bewertet es (Variabilitaet, Zaehler-/Checksummenverdacht, Bitueberlappung
mit unserer DBC, Korrelation gegen die validierten Anker) - nichts wird blind uebernommen.
Fremd-DBC liegt als `data/can/external/opendbc_mazda_2017.dbc` (nur Syntax gepatcht).

Nebenbei geprueft und erschoepft: `berumiya`-Upstream hat seit unserem Stand nichts Neues
(letzter Commit 2026-07-22); Racelogics VBOX-Datenblatt fuer den ND listet 9 Kanaele, alle
bereits bei uns.

### 2. WIDERLEGT: `SteeringAngle_related`@0x86 ist linear, nicht nichtlinear
Der Befund vom 2026-09-14 ("stark nichtlinear, Isotonic-Regression noetig, R²=0,90,
RMSE=10,5°") und die darauf gestuetzte Verwerfung der externen linearen Formel am
2026-09-15 waren **beide falsch, aus demselben Grund**.

Die Bitlage war immer richtig. Der Fehler: das oberste Bit ist **kein Teil des Zahlenwerts,
sondern ein Gueltigkeits-/Init-Flag des EPAS**. Es ist in 0,0-0,5% der Frames gesetzt -
ausschliesslich in einem Block am Logstart bei Stillstand, Rohwert konstant 16000 (= exakt
0°) plus 32768. Diese wenigen Frames springen um 32768 und druecken den Pearson ueber das
ganze Log von 1,00 auf 0,50. Spearman ist gegen solche Ausreisser robust - daher die
Fehldiagnose "monoton, aber nichtlinear".

Nach Abtrennen des Flags, ueber 6 Logs: **R² = 0,9997-0,99995, RMSE 0,65-0,94°** (altes
Isotonic-Modell: R²=0,90, RMSE=10,5° - Faktor 11 schlechter). DBC korrigiert:
`SteeringAngle_EPAS : 6|15@0+ (0.1,-1600) "deg"` plus `SteeringAngle_EPAS_Invalid : 7|1@0+`.
**`scripts/can_steering_angle_0x86.py` (Isotonic-Lookup) ist damit obsolet.**

Lehre: `can_re_toolkit.detect_extreme_outliers` haette das gefunden, wurde auf dieses Signal
aber nie angewendet, weil es als "erklaert" galt.

### 3. WIDERLEGT: OBD-Fusion liest nichts nativ vom CAN - wir haben nur nicht hingesehen
Der Befund vom 2026-09-14 ("2 Kanaele sind gar keine OBD-Anfrage, die App liest nativ CAN
mit; ~9-10 Kanaele clientseitig berechnet") war falsch. Unser `obd_from_can.py` hatte zwei
blinde Flecken:

- **Nur `0x7E0/0x7E8` wurde dekodiert.** Unsere Logs enthalten aber Verkehr auf drei
  weiteren Headern: `0x730/0x738` (EPS, **10.820 Frames pro Log**), `0x760/0x768` (DSC),
  `0x720/0x728` (unser eigener TPMS-Poller).
- **ISO-TP-Multiframe-Antworten wurden verworfen.** OBD-Fusion buendelt fuenf Mode-1-PIDs
  in eine Anfrage (`06 01 0D 10 44 0E 62`) - die Antwort passt nicht in einen Single Frame.

`obd_from_can.py` kann jetzt beides (`ECU_HEADERS`, `_reassemble()`, `_split_mode1_multi()`).
Damit stehen **sieben zusaetzliche echte Messkanaele** aus jedem Y-Kabel-Log zur Verfuegung:

| Quelle | Kanal | Formel | Bereich | Validierung |
|---|---|---|---|---|
| EPS DID 0x3302 | STEER_ANGL_EPS | - | - | R²=0,998 gegen 0x82, 3 Logs |
| EPS DID 0x3301 | STEER_SPD_EPS | - | 0-149 | r=0,82-0,94 gegen d(Winkel)/dt |
| Mode1 0x0D | VehicleSpeed | A | 0-218 km/h | r=1,0000 gegen CAN |
| Mode1 0x10 | MassAirFlowRate | A/100 | 0-152 g/s | plausibel |
| Mode1 0x44 | **Lambda (SOLL, commanded)** | A/32768 | 0,81-2,00 | plausibel (fett WOT / Schubabschaltung) |
| Mode1 0x0E | TimingAdvance | A/2-64 | -30..+53° | plausibel |
| Mode1 0x62 | ActualEnginePercentTorque | A-125 | 0-97 % | plausibel |
| DSC DID 0x2B0D | Bremspedalstellung | - | - | Zuordnung aus ND3-Quelle, noch nicht kalibriert |

Zu Lambda (0x44) eine **Praezisierung vom 2026-09-15**: das ist die *commanded* equivalence
ratio, also der SOLLWERT des Steuergeraets, kein Sondenmesswert. Das Fahrzeug hat zwei
Lambdasonden (vorn Breitband-Regelsonde, hinten Diagnosesonde hinter dem Kat, Nutzerangabe);
deren gemessene Werte liefern die Standard-PIDs 0x24/0x25 bzw. 0x34/0x35 - die fragt bisher
niemand ab, `uds_did_sweep.py --mode1-survey` holt sie. Das erklaert die Abweichung zu
AFR_MZ besser als die zuvor vermutete Abtastproblematik: Soll- und Ist-Lambda weichen im
Transienten und in der Regelschwingung systematisch voneinander ab. Und der
**gemessene MAF** relativiert unsere Schaetzformel `0,00019*(RPM*MAP)-8,08`: gegen den
echten Wert nur R²=0,87 (RMSE 7,4 g/s); neu gefittet `0,000163*(RPM*MAP)-3,63`, R²=0,90.

### 4. Root-Cause-Bug: beide Beschleunigungsanker fehlten in JEDEM bisherigen Sweep
`ANCHOR_SIGNALS` in `can_byte_search.py` hatte Laengs- und Querbeschleunigung vertauscht
(`Longitudinal_Acc_Raw` auf 0x75 statt 0x76, `Lateral_Acc_Raw` auf 0x76 statt 0x75).
`extract_anchors()` verwirft einen Anker **stillschweigend**, wenn der Signalname in der
Botschaft fehlt - beide fehlten dadurch in jedem Sweep, und der davon abhaengige
`PROXY_high_lat_g` wurde nie gebaut. Plausible Miterklaerung, warum der DSC-Eingriffs-
indikator nie gefunden wurde: die relevanteste Referenzgroesse war nie dabei.
Ankerzahl nach Fix plus den neuen OBD-Kanaelen: **29 statt 19**.

### 5. Neues Werkzeug: referenzfreie Feld-Segmentierung (READ)
`scripts/can_field_segmentation.py` nach Marchetti & Stabili (IEEE TIFS 2019). Schliesst die
strukturelle Luecke aller bisherigen Werkzeuge: es braucht **keine Referenzgroesse**, sondern
segmentiert Botschaften allein aus der Bit-Kipprate in Felder und klassifiziert sie
(CONST / COUNTER / CRC / PHYSICAL / FLAG). Ergaenzt um einen **Signedness-Verdachtstest** fuer
genau den Bug-Typ, der das Projekt zweimal getroffen hat (BrakePressure, AmbientTemp).

Validierung gegen unsere 147 bekannten Signale: findet Drehzahl, Geschwindigkeit, Gaspedal,
alle vier Radgeschwindigkeiten, Lenkwinkel, Gierrate und Querbeschleunigung bitgenau wieder.

Ausbeute ueber 11 Logs: 2059 Felder, davon **177 unbelegte PHYSICAL/FLAG-Felder** in
mindestens 6 Logs. Auffaelligste Kandidaten: `0x08A` (HS_DCDC, 100 Hz, fuenf analoge Felder,
in KEINER DBC - Verdacht i-ELOOP/Lichtmaschinenregelung, waere als parasitaerer
Widerstandsterm im Fahrleistungsmodell relevant), `0x45A`, `0x3D2`, `0x242/0x245/0x246`
(Kameradaten) und `0x200` (zwei 16-Bit-Felder um 32768 zentriert = klassische signierte
Sensoren).

### 6. Neues Werkzeug: Ereignis-Bit-Differenzanalyse
`scripts/can_event_bit_diff.py`. Korrelation ueber ein ganzes Log ist das falsche Werkzeug
fuer ein Flag, das pro Fahrt einmal 300 ms gesetzt wird - es geht im Rauschen unter.
Stattdessen: Setzquote jedes Bits innerhalb eines Ereignisfensters gegen ausserhalb.

Funktionsnachweis am bekannten Bremsereignis bestanden (findet `BrakePressure` und, mit
hoeherem Lift, `0x415` Bit 10 - bei uns bisher nur `BrakeRelated_weak_maybe`).

**ABS/DSC weiterhin nicht gefunden, aber jetzt aus nachweisbarem Grund:** in keinem der 11
Logs gibt es ein Ereignis, das die Schwellen erreicht (max. 154 Samples ueber 30 bar
Bremsdruck, keine nennenswerte Radgeschwindigkeits-Divergenz). Es fehlen die Rohdaten, nicht
die Methode - das Werkzeug steht fuer die gezielte Testfahrt bereit.

### 7. Neues Werkzeug: UDS-DID-Sweep (gebaut, noch nicht am Fahrzeug gelaufen)
`scripts/uds_did_sweep.py` probiert DID-Bereiche pro Steuergeraet durch und protokolliert,
was antwortet - braucht keine externe Quelle und keine Referenzgroesse, jede positive
Antwort ist der Fund. Nur Standardbibliothek (`socket.AF_CAN`), da python-can auf dem Pi im
Default-Interpreter fehlt. **Ausschliesslich lesender Dienst 0x22**; schreibende/aendernde
Dienste (0x2E, 0x31, 0x11, 0x28) sind bewusst nicht implementiert.
End-to-end gegen ein vcan-Fake-Steuergeraet auf dem Pi getestet.

Zielbereiche aus der ND3-Quelle (`drewid74/2024-nd3-mazda-obdii`, Range-Scan 2026-06-23):
`0x760/22 2B xx` (11 Treffer, Fahrwerk - **wahrscheinlichster Ort fuer den ABS/DSC-
Eingriffsindikator**), `0x760/22 20 xx` (4, Lenkung), `0x7E0/22 13 10` = **Oeltemperatur**
(`((A*256)+B)/100-40`), `0x7E0/22 F4 xx` (60 Treffer, groesster Block), `22 DA xx` (36),
`22 03 xx` (38), `22 09 xx` (8, Nockenwelle/VVT).

### 8. MS-CAN (Track 2): Recherche abgeschlossen, Empfehlung zurueckstellen
Zweiter Bus liegt bei Mazda auf **OBD-Pin 3/11 mit 125 kbit** (Pin 6/14 = HS-CAN 500 kbit).
`data/can/MX5ND_6thGenMazda_MSCAN.dbc` liegt bereits im Repo, wird aber nie geladen (wir
loggen nur `can0`): 34 Botschaften, nur 6 Signale - reines Geruest. Inhaltlich interessant
sind `MS_EATC` (Klima - **erklaert, warum die Klima-Standtests am 2026-09-12 groesstenteils
unbrauchbar waren**) und `MS_IC_BCMM` (Karosserie); viele `MS_IC_*`-IDs sind dagegen
gateway-gespiegelte HS-CAN-Botschaften (0x78, 0x79, 0x202, 0x215 mit denselben Nummern).
Ein zweiter Adapter (~30 EUR) plus Integration lohnt erst, wenn Klima-/Karosseriedaten
gebraucht werden - die 177 unbelegten HS-CAN-Felder sind der billigere Hebel.

### 9. `0x2A` ReadDataByPeriodicIdentifier: vermutlich Sackgasse
Der Dienst nimmt nur 1-Byte-periodicDataIdentifier aus dem Bereich `0xF2xx`. Unsere DIDs
(0xDA85, 0x093C, 0x2A0x, 0x33xx) liegen nicht darin. Billig mitzutesten, aber keine
Erwartung - der DID-Sweep ist der sichere Hebel.

## Nachtrag zum Deep-Search: Oeltemperatur gefunden, Lambda-PID praezisiert (2026-09-15)

Anlass war die Nutzerangabe, dass der ND2 G184 **zwei Lambdasonden** hat (vorn
Breitband-Regelsonde, hinten Diagnosesonde hinter dem Kat).

### Praezisierung: PID 0x44 ist das SOLL-Lambda, kein Messwert
`CommandEquivalenceRatio` ist per SAE J1979 die vom Steuergeraet **angeforderte**
Gemischzusammensetzung. Im Deep-Search-Abschnitt oben war das als "echter Messwert"
bezeichnet - falsch. Das aendert auch die Deutung der AFR_MZ-Abweichung: Soll- und
Ist-Lambda weichen im Transienten und in der Regelschwingung systematisch voneinander ab -
eine bessere Erklaerung als die zuvor vermutete reine Abtastproblematik.

**Die gemessenen Sondenwerte fehlen uns noch.** Sie liegen in den Standard-PIDs
0x24/0x25 (Lambda + Spannung je Sonde) bzw. 0x34/0x35 (Lambda + Strom); 0x13/0x1D sagen,
welche Sonden verbaut sind. Keiner davon wird vom Handy abgefragt, also stehen sie auch
nicht in unseren Logs. `uds_did_sweep.py --mode1-survey` holt sie: erst die
Support-Bitmasken (5 Anfragen -> vollstaendige Liste aller Standard-PIDs dieses
Fahrzeugs), dann jeden unterstuetzten PID einmal. End-to-end gegen ein vcan-Fake-
Steuergeraet getestet.

### DID 0xDA86 = zweite Sonde? UNENTSCHIEDEN
Naheliegende Hypothese: `0xDA85`/`0xDA86` sind die beiden Sonden. `0xDA86` liegt in seinen
20 Stichproben bei 125-129, also praktisch konstant bei Lambda~0,99 - genau, was eine
Sonde hinter dem Kat zeigen wuerde. **Gegenprobe im SELBEN Zeitfenster widerlegt das als
Beleg:** `0xDA85` liegt dort ebenfalls bei 126-130 (identische Streuung 0,85). Der schmale
Bereich kommt allein daher, dass die 20 Stichproben in eine ruhige Teillastphase fallen
(14-18% Gas, Regelbetrieb, beide Sonden bei Stoechiometrie). Erst `0xDA86`-Stichproben
waehrend Volllast oder Schubabschaltung koennten die Frage entscheiden.

### DID 0x1310 ist die OELTEMPERATUR, nicht der Kilometerstand
Die Zuordnung vom 2026-09-14 ("VehicleOdometerReading, deckt sich mit `C001_ODO`@0x40A")
ist falsch - ein weiterer Fall der Scheinkorrelation zweier ueber eine Fahrt monoton
steigender Groessen, genau des Artefakts, das im selben Abschnitt fuer die "Total"-Werte
schon beschrieben wurde. Belege:

- Der echte Kilometerstand (`C001_ODO`) steht in diesem Log bei **169.765-169.795 km**,
  `0x1310` bei **7053-7109**. Die Werte haben nichts miteinander zu tun.
- Mit der Formel der ND3-Quelle (`((A*256)+B)/100-40`) ergibt `0x1310` **30,5-31,1 °C**,
  waehrend das Kuehlwasser im selben Fenster bei **49-51 °C** liegt und ueber das Log von
  29 auf 92 °C steigt. Oel deutlich kaelter als Kuehlwasser und langsamer steigend - genau
  das Warmlaufverhalten, das man erwartet.
- Der Wert steigt im 22-Sekunden-Fenster gleichmaessig um ~0,03 °C/s (~1,8 °C/min).

**Oeltemperatur ist ein echter Neuzugang** und fuer das Fahrleistungsmodell relevant
(Oelviskositaet -> Reibleistung). Sie wird bisher nur vom Handy abgefragt, und zwar selten
(20 Stichproben pro Log); per `uds_did_sweep.py` bzw. einem eigenen Poller waere sie
durchgehend verfuegbar - alternativ liefert der Standard-PID 0x5C dasselbe, falls
unterstuetzt (im Mode-1-Survey mit abgedeckt).

## Nachgeholtes Log candump-2026-09-15_171047: zwei Bugs, Öltemperatur live, stärkste CAN-Kurve (2026-09-15)

Das Log fiel im automatischen Durchlauf komplett aus (Parser-Absturz) und trug
zusätzlich die falsche Uhrzeit. Beides aufgelöst, Log vollständig verarbeitet.

### Bug 1: abgeschnittene letzte Zeile bricht den ganzen Parser ab
Die Datei endet mitten im Frame (`(1789459376.467328) can0 076` – kein `#`, kein
Zeilenumbruch) und war als einzige nie gegzippt. Ursache: harter Stromverlust am
Fahrtende. Belegt über den Pi selbst – `uptime -s` = 17:49:26, also 3 Minuten nach
dem letzten Frame; der Pi bootete erst zu Hause wieder. `candump` wurde getötet,
bevor die Zeile fertig war und `session_logger.py` gzippen konnte.

`parse_candump()` überspringt defekte Zeilen jetzt (`try/except ValueError`) statt
den Lauf abzubrechen, und meldet die Anzahl auf stderr – stilles Verschlucken wäre
hier der gefährlichere Fehler, weil echte Korruption dann unsichtbar bliebe.
Ein Fix, neun Aufrufer (`can_bitsearch`, `can_byte_search`, `can_field_segmentation`,
`can_event_bit_diff`, `can_opendbc_crosscheck`, `can_retest_maybe_signals`,
`can_steering_angle_0x86`, `can_gps_yawrate_and_steering_offset`, das Skript selbst).
Test: `scripts/test_can_log_parser.py`.

Ergebnis: 1 Zeile übersprungen, **5.567.710 Frames**, 105 unique IDs, 104 davon per
DBC abgedeckt (nur `0x7df` fehlt), 9,34 Mio Signal-Samples.

### Bug 2: dritter Pi-Uhr-ohne-NTP-Fall (−7h43m15s)
Die Frame-Zeitstempel behaupteten 09:27:32–10:02:56. Widerlegt per
VehicleSpeed-Kreuzkorrelation (dieselbe Methode wie am 14.09.):

| Vergleich | bester Lag | r | gemeinsame Samples |
|---|---|---|---|
| `092732` vs. `2026-09-15 170941.dlg` | **+27795,0 s** (7h43m15s) | **0,99994** | 10032 (0,2s-Raster) |
| `084853` vs. `2026-09-15 085014.dlg` | −0,4 s | 0,99996 | 5888 |

Das Morgen-Log war also korrekt datiert, nur die Nachmittags-Session nicht. Die
Signatur ist eindeutig: der Pi startete die zweite Session mit einer Uhr bei
09:27:32 – 37 Sekunden **vor** dem Ende der Morgen-Session (09:28:09). Er hat die
Zeit also dort wieder aufgenommen, wo sie zuletzt bekannt war, statt sie zu
synchronisieren. Wahre Fahrtzeit: **17:10:47–17:46:11**.

Umbenannt lokal und auf dem Pi (`candump-2026-09-15_171047.log`),
`data/can_gps_pairs.json` nachgezogen, Rohinhalt unverändert.

### Folgefund: Umbenennen allein hat den Datalake nie mitkorrigiert
`log_start_epoch()` in `build_datalake.py` las die Startzeit aus dem **ersten Frame**.
Die am 14.09. umbenannten Logs standen deshalb bis heute mit dem alten
13.09.-Zeitstempel in `measurements.timestamp_local` – die Dateien hießen richtig,
der Datalake log weiter. Gefixt an dieser einen Stelle: weichen Dateiname und
Frame-Zeitstempel um >60s ab, gewinnt der Dateiname (er trägt im Projekt die per
Kreuzkorrelation ermittelte Wahrheit), mit Hinweis beim Build. Greift bei genau
den drei bekannten Fällen und lässt alle anderen Logs unberührt – verifiziert:

```
candump-2026-09-14_163711  13.09. 13:54 -> 14.09. 16:37
candump-2026-09-14_173057  13.09. 14:46 -> 14.09. 17:30
candump-2026-09-15_171047  15.09. 09:27 -> 15.09. 17:10
```

### Bug 3 (nebenbei): tpms_log_decode.py war seit der Öltemperatur kaputt
`tpms_poller.PIDS` bekam beim Oil-Temp-Umbau ein drittes Feld (Byte-Anzahl),
`tpms_log_decode.py` entpackte weiter zwei → `ValueError`. Einzeiler gefixt.

### Inhalt der Fahrt
- **Öltemperatur erstmals über eine ganze Fahrt** (180 Samples, erster Live-Lauf des
  10s-Pollers): **31,3 → 102,5 °C**. Das Kühlwasser steht nach ~12 min bei 89 °C und
  bleibt dort, das Öl zieht danach weiter und endet **~12 K darüber** – genau das
  erwartete Warmlauf-/Lastbild. `OilTemp_CAN` ist damit produktiv.
- **Stärkste je per CAN gemessene Kurven des Projekts**, beide in dieser Fahrt:
  **+1,09g rechts** (t=1876,7s, 120 km/h, 3. Gang) und **−1,00g links** (t=1916,1s).
  Der Rechts-Peak hält der Gegenprobe stand: `a_lat = v·ω` aus VehicleSpeed und
  Gierrate liefert im selben Sample **0,98g**, r=0,96 über das Kurvenfenster, das
  0,25s-Mittel um den Peak 0,92g (v·ω: 0,95g). Über 0,9g blieb es ~0,48s. Also kein
  Einzelsample-Ausreißer, sondern ein echter Grenzbereich – anders als die
  bekannten `a_lat_peak`-Überschätzungen beim Rutschen.
- Damit existiert **erstmals ein CAN-bestätigter Punkt an der unteren Kante des
  Lap-Sim-Brackets** (mu=1,0–1,3); bisher lag dort nur der GPS/Gyro-Referenzpunkt
  aus 170146. `corner_speed_model.py` warnt jetzt entsprechend (p90 aller Kurven
  weiterhin nur 0,59g).
- vmax 194,6 km/h, neue Schaltbestzeit 3→2 (downshift) 1,60s, TPMS unauffällig
  (Hinterachse wie gewohnt über der Vorderachse).

## Erster Fahrtlauf mit Oeltemperatur-Polling und Einmal-Erhebung (2026-09-15, Hinfahrt)

### Oeltemperatur: Formel dreifach unabhaengig bestaetigt
Hinfahrt `candump-2026-09-15_084853` (39,3 Min, 6,02 Mio Frames): **222 Stichproben im
exakten 10-Sekunden-Takt**, 11,7 -> 92,3 °C. Zum Vergleich: in allen 12 Logs davor
zusammen gab es 20 Stichproben, alle in einem einzigen Log.

| t [min] | Oel °C | Kuehlwasser °C | Diff |
|---|---|---|---|
| 1,1 | 11,66 | 12,0 | −0,3 |
| 4,6 | 13,96 | 42,0 | −28,0 |
| 6,6 | 31,12 | 70,0 | −38,9 |
| 10,3 | 70,29 | 87,0 | −16,7 |
| 16,4 | 87,98 | 88,0 | 0,0 |
| 23,9 | 90,82 | 89,0 | +1,8 |
| 39,1 | 92,30 | 93,0 | −0,7 |

Die Formel `((A*256)+B)/100-40` ist damit ohne jeden Anpassungsparameter bestaetigt:
(1) beim Kaltstart stimmen Oel, Kuehlwasser und Ansauglufttemperatur auf 0,3 °C ueberein,
(2) im Warmlauf haengt das Oel mit bis zu 39 °C hinterher (groessere Waermekapazitaet),
(3) im warmen Zustand liegt es 1,8 °C ueber dem Kuehlwasser. Alles physikalisch korrekt.

### Uhr-Absicherung hat am ersten Tag einen echten Fehler gefangen
`clockstate-20260915-092732.txt`: *"Uhr von 2026-09-13 13:54:00 auf gespeicherte
2026-09-15 09:27:11 vorgestellt (kein NTP)"*. Der Pi war neu gebootet (`uptime -s` bestaetigt
einen Boot heute; `last reboot` und das Journal liegen auf dem RAM-Overlay und zeigen
deshalb veraltete Staende) und kam mit dem Datum vom 13.09. hoch - genau der
fake-hwclock-Wert aus dem Overlay. Ohne den Fix waere das Log zwei Tage falsch datiert
gewesen, ohne erkennbaren Sprung.

**KORREKTUR (nachtraeglich, siehe Abschnitt "Nachgeholtes Log candump-2026-09-15_171047"
weiter unten): die Absicherung hat den Fehler nicht behoben, nur verkleinert - und die
erste Deutung der Nebenwirkung war in allen drei Punkten falsch.** Was die Absicherung
laut ihrem eigenen Protokoll getan hat: Uhr von 13.09. 13:54 auf den **gespeicherten
Anker** 15.09. 09:27:11 vorgestellt, *ohne* NTP. Der Anker ist aber nur die zuletzt
bekannte Zeit, also ungefaehr das Ende der Hinfahrt (09:28:09) - er stimmt nur, wenn der
Pi durchgehend lief. Hier lag dazwischen der halbe Tag im Stand. Aus einem 2-Tage-Fehler
wurde damit ein **7h43m-Fehler**, kein korrekter Wert.

Konkret falsch war:
- *"das Log heisst 171047"* - nein, der Pi hat es als `candump-2026-09-15_092732.log`
  angelegt (21 s nach der Uhrkorrektur, passend zum Anker). Der Name `171047` stammt aus
  der nachtraeglichen Umbenennung anhand der Kreuzkorrelation, nicht aus der Absicherung.
- *"Nebenwirkung kosmetisch, nur der Dateiname ist irrefuehrend"* - genau andersherum:
  der Dateiname traegt die **verifizierte** Zeit (17:10:47, r=0,99994 gegen das Handy-Log),
  die Frames sind um 7h43m15s daneben.
- *"`log_start_epoch()` liest ohnehin den ersten Frame-Epoch, der Datalake ist immun"* -
  das war der Bug, nicht die Immunitaet. Genau dadurch standen die am 14.09. umbenannten
  Logs einen Tag lang mit falschem Datum im Datalake. Seit 2026-09-15 gewinnt bei einer
  Abweichung > 60 s der Dateiname.

Richtig bleibt: **die Messdaten selbst sind in Ordnung** (kein Sprung > 60 s ueber 5,57 Mio
Frames), und die Datei blieb unkomprimiert, weil die Session hart beendet wurde und
`stop_logging()` nicht mehr zum gzip kam.

**Lehre fuer die Absicherung:** ein gespeicherter Zeit-Anker ersetzt kein NTP und keine RTC.
Er hilft gegen den fake-hwclock-Ruecksprung, aber jede Standzeit zwischen zwei Sessions
geht als Fehler direkt durch. Solange keine RTC verbaut ist, bleibt die Kreuzkorrelation
gegen ein Handy-Log der einzige belastbare Zeitbeleg.

### Einmal-Erhebung: statische Ergebnisse gueltig, dynamische wertlos
Die Erhebung lief - aber **bei stehendem Motor** (Drehzahl 0, Laufzeit seit Start 0, Last 0,
MAF 0). Ursache: die feste Wartezeit von 90 s reichte nicht, weil KeyState schon bei
Zuendung ACC ausloest. Behoben: der Ausloeser wartet jetzt auf Drehzahl > 400 (Standard-PID
0x0C), hoechstens 10 Minuten. Live gegen ein vcan-Fake-Steuergeraet geprueft.

Was trotzdem gesichert ist, weil es nicht vom Motorzustand abhaengt:

- **52 Standard-Mode-1-PIDs werden unterstuetzt** (vollstaendige Liste in
  `probe-20260915-084853.csv`).
- **PID 0x5C (Motoroeltemperatur) wird NICHT unterstuetzt.** Damit ist DID 0x1310 die
  einzige Quelle - die Entscheidung, sie selbst zu pollen, war richtig.
- **PID 0x34 WIRD unterstuetzt**: O2 Bank1 Sensor1, Breitband-Lambda + Sondenstrom. Das ist
  der gemessene Wert der vorderen Regelsonde, der uns bisher komplett fehlte (wir hatten nur
  das SOLL-Lambda aus PID 0x44). Bei stehendem Motor lieferte er den Ruhewert lambda=1,0000 /
  −0,47 mA - der echte Messwert kommt beim naechsten Lauf.
- **PID 0x13 = 0b00000011**: genau zwei Sonden, Bank 1 Sensor 1 und Sensor 2 - deckt sich
  mit der Nutzerangabe. Fuer Sensor 2 gibt es nur den Schmalband-PID 0x15, kein 0x25/0x35.
- **DSC-Bloecke bestaetigt**: `2B00-2BFF` liefert 11 Treffer, `2000-20FF` vier - exakt wie in
  der ND3-Quelle. Im Stand sind erwartungsgemaess fast alle null (2B0D Bremspedal = 0,
  2033/2034 Lenkwinkel/-rate = 0). `2B11 = 0xFFF5` (signed −11) und `2B05 = 0x40000000`
  sind die einzigen von null verschiedenen Werte. Ein ABS/DSC-Eingriffsindikator laesst sich
  daraus im Stand nicht identifizieren - dafuer braucht es dieselben DIDs waehrend der Fahrt.

## Broadcast-Gegenstuecke zu den OBD-Kanaelen gesucht (2026-09-15)

Nutzerfrage: die per Diagnose geholten Werte kommen mit 1-2 Hz. Werden dieselben Groessen
zusaetzlich nativ gebroadcastet, haetten wir sie mit voller Botschaftsrate und ohne eigene
Anfragen. Werkzeug: `scripts/can_find_native_counterpart.py` (Partialkorrelation gegen eine
Kontrollgroesse + **Uebertragungstest ueber zwei Fahrten** + Multiplex-Erkennung).

**Positivkontrolle bestanden:** mit `EngineRPM` als Referenz findet das Werkzeug `0x202`
(die echte Drehzahl) und `0x130` (das bekannte Drehzahl-mal-2-Duplikat) mit Transfer-R²
= +1,000, dazu die Radgeschwindigkeiten mit +0,70 (ueber die Uebersetzung gekoppelt).

| Referenz | bester Kandidat | R² je Fahrt | **Transfer** | Ergebnis |
|---|---|---|---|---|
| ActualEnginePercentTorque (PID 0x62) | `0x167` Byte4 | 0,967 / 0,965 | **+0,962** | **bestaetigt** |
| LambdaCommanded (PID 0x44) | `0x0FD` Byte4-5 | 0,874 / 0,887 | +0,881 | **kein Lambda** - siehe unten |
| Oeltemperatur (DID 0x1310) | `0x4DF` Byte5 | 0,811 / 0,941 | −0,369 | kein Gegenstueck |
| TimingAdvance (PID 0x0E) | `0x0FD` Byte6-7 | 0,453 / 0,235 | +0,202 | kein Gegenstueck |
| MassAirFlow (PID 0x10) | `0x167` Byte4-5 | 0,719 / 0,623 | +0,570 | kein Gegenstueck |

Der Uebertragungstest ist dabei das Entscheidende: bei der Oeltemperatur sahen **alle**
Kandidaten innerhalb einer Fahrt mit R²=0,81-0,94 hervorragend aus und lieferten uebertragen
−0,37 bis −0,66, also schlechter als der blosse Mittelwert. Reine "steigt auch ueber die
Fahrt"-Artefakte (Kilometerstand und Aehnliches). Zum Vergleich: die Kontrollbotschaft mit
dem echten Kuehlwasser uebertraegt mit +0,57, weil Kuehlwasser und Oel physikalisch wirklich
zusammenhaengen.

**Methodische Falle, in die ich zuerst gelaufen bin:** Botschaft `0x45B` belegte die gesamte
Trefferliste mit partial_r ~ 0,91 - bis auffiel, dass Byte0 dort 1..5 zykliert. Eine
multiplexte Botschaft mischt bei flacher Byte-Lesung Werte verschiedener Bedeutung, und das
blosse Rotationsmuster korreliert mit allem, was ueber die Fahrt monoton laeuft. Multiplexte
Botschaften werden jetzt pro Gruppe durchsucht.

### `ActualEnginePercentTorque` (0x167 Byte4) - bestaetigt und neu kalibriert
War `EngineLoad_or_Torque_pct_maybe`. Der Uebertragungstest gegen den Standard-PID 0x62 hebt
das von einer Vermutung zu einer Bestaetigung. Kalibrierung gemeinsam ueber beide Fahrten
neu gefittet (n=4410): **3,0242·raw − 177,849**, R²=0,962, RMSE 4,07 Prozentpunkte. Die
alte Formel (3,17, −185,3) stammte aus einem einzelnen Log; die neue ist auf **beiden**
Fahrten besser (RMSE 4,07 gegen 4,70). Einschraenkung: die Steigung schwankt zwischen den
Fahrten um 3,4 % - praeziser als ~4 Prozentpunkte wird die Formel nicht.

### Neuer Fund: `FuelCut` (0x0FD Byte5 Bit1) - Schubabschaltung
Der Lambda-Kandidat ist **kein Lambda**: der Rohwert von Byte4-5 springt nur zwischen 0x1E00
und 0x1E02, kennt also genau zwei Zustaende. Die Korrelation von 0,88 kam daher, dass die
Varianz des Soll-Lambdas von den Schubphasen dominiert wird. Das Bit selbst ist aber ein
echter, wertvoller Fund - ueber beide Fahrten bestaetigt:

- Ist das Soll-Lambda > 1,9 (Kraftstoff abgeschaltet), ist das Bit in **99,4 %** bzw.
  **98,8 %** der Faelle gesetzt.
- Gesetzt ist es zu **96-98 %** nur bei geschlossenem Gaspedal UND Drehzahl > 1200 1/min,
  ausserhalb dieser Bedingung nur zu 0,4 %.
- Es deckt rund 38 % aller Schubphasen ab - passend dazu, dass die Schubabschaltung erst
  oberhalb einer Drehzahlschwelle greift und vor dem Leerlauf wieder aufmacht.

**Relevanz fuers Fahrleistungsmodell:** das Schleppmoment unterscheidet sich grundlegend
zwischen abgeschalteter und weiterlaufender Einspritzung. Dieses Bit liefert den Zustand mit
voller Botschaftsrate statt mit 1-2 Hz Polling - siehe `scripts/engine_braking_analysis.py`.

**Lehre fuers Werkzeug:** ein bestandener Uebertragungstest heisst nicht automatisch
"dieselbe Groesse". Ein Flag mit zwei Zustaenden kann eine analoge Referenz gut vorhersagen,
wenn deren Varianz von genau diesem Zustand dominiert wird. Das Skript gibt die Kardinalitaet
jetzt mit aus und warnt bei weniger als 10 verschiedenen Rohwerten.
