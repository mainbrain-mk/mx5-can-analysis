# CAN-Bus-Logging: aktueller Stand

**Herkunft:** ausgelagert aus dem "Kurzüberblick"-Abschnitt von [`docs/logs/can-bus-status.md`](../logs/can-bus-status.md) (Reorg 18.09.2026, Inhalt unveraendert uebernommen). Ab jetzt hier direkt in-place aktualisieren, wenn sich der Stand aendert - das Logbuch bleibt das chronologische Protokoll mit den Herleitungen.

## Kurzüberblick: aktueller Stand (2026-09-20, Nachmittag)

**Status 2026-09-20 Nachmittag — ECU-Soft-Limiter-Zone an 2 weiteren Logs bestätigt (5
Eingriffe insgesamt), Klopfen UND Radschlupf/DSC als Auslöser ausgeschlossen, Live-Erkennung
im Renncockpit deployt (Shiftlights blitzen blau).** Details im Logbuch unten ("ECU-Soft-
Limiter…").

1. **5 von 7 Volllast-Zügen in den letzten beiden Logs vom 19.09. (163857 nachmittags,
   233619 abends) sind echte ECU-Eingriffe** (Drosselklappe/`ETC_ACT` schließt trotz
   `APP`=100%): 7273/7281/7439/7429/7307 U/min, in Gang 2 und 3. Die anderen 2 Züge
   (7215/7297 U/min) sind saubere, fahrerinitiierte Schaltvorgänge ohne ECU-Eingriff — `APP`
   fällt dort selbst, bevor die Drehzahl ihren Peak erreicht.
2. **`KnockRetard_CAN` springt bei keinem der 5 Eingriffe an** — Werte bleiben im üblichen
   Nahe-Null-Band (bzw. Datenlücke bei 2 der 5 Events), während echte Ausreißer im selben Log
   (bis −3,5) zu völlig anderen Zeitpunkten auftreten. Klopfen scheidet damit erneut als
   Auslöser aus (deckt sich mit dem Befund an Log `150438`).
3. **Radschlupf/Traktionskontrolle ebenfalls ausgeschlossen:** max. Spread zwischen den 4
   `WheelSpeed_CAN`-Kanälen bei allen 5 Events ≤2,3 km/h — klar unter dem dokumentierten
   Rauschboden von 4,5 km/h (selbst bei unbeschleunigter Geradeausfahrt). `ABS_Active_CAN`
   und `DSC_Status_CAN` durchgehend 0. **Der tatsächliche Auslöser des Soft-Limiters bleibt
   offen** — weder Klopfen noch Traktionsverlust erklären ihn.
4. **Live-Erkennung im Dash implementiert:** `dash_gui.py` prüft jetzt RPM>7000 & `APP`≥99%
   & `ETC_ACT`<90 (alle drei bereits live vorhanden, kein zusätzliches Polling nötig — siehe
   `status/pi-runtime-state.md` zur `tpms_poller.py`-Kette, die `ETC_ACT` liefert) und lässt
   die Shiftlight-Reihe bei Treffer mit 8 Hz komplett blau blinken statt der normalen
   RPM-Zonenfarbe. Deployt und auf dem Pi neu gestartet, `dash_gui.py`+`test_dash_gui.py`
   md5-identisch zum Repo (siehe `status/pi-runtime-state.md`).

<details>
<summary>Vorheriger Stand (2026-09-20, Vormittag)</summary>

**Status 2026-09-20 — KnockRetard-PID gefunden (DID 0x03EC, Mode 0x22, herstellerspezifisch),
jetzt live gepollt und rückwirkend aus jedem CAN-Log dekodierbar.** Details im Logbuch unten
("KnockingRetard-PID gefunden…").

1. **`KnockRetard` = DID `0x03EC`** (PCM, 0x7E0/0x7E8), Formel `signed_int16(raw)/512` —
   per Korrelation gegen ein Y-Splitter-Log identifiziert (73% der App-Werte bitgenau,
   R²=0,958). Keine SAE-J1979-Standard-PID, wie die gestrige Websuche schon vermuten ließ.
2. `scripts/tpms_poller.py` pollt die DID jetzt in derselben schnellen, intervallfreien
   Gruppe wie Lambda+Drosselklappe — deployt auf den Pi (Dienst lief beim Deploy nicht).
3. `scripts/can_log_parser.py`/`build_datalake.py` dekodieren die DID jetzt generisch aus
   JEDEM CAN-Log (neuer Kanal `KnockRetard_CAN`) — `SCHEMA_VERSION` hochgezählt, voller
   Re-Ingest aller Logs angestoßen.
4. ~~Vorzeichen/physikalische Bedeutung weiterhin ungeklärt~~ – **zweite unabhängige
   Bestätigung erhalten** (siehe Punkt 5): 81% bitgenau statt 73%, r=0,997. Vorzeichen selbst
   (Werte überwiegend ≤0) bleibt weiterhin ungeklärt.
5. **Vierter No-RTC-Fall gefunden+behoben:** `candump-2026-09-19_165600` war tatsächlich der
   fehlende Y-Splitter-Log für den Abendtest (`2026-09-19 233619`) — Dateiname UND
   Frame-Zeitstempel gleichermassen falsch (kein Widerspruch, den `log_start_epoch()` fangen
   könnte), nur per externem Abgleich gegen das dlg auffindbar. Umbenannt auf
   `candump-2026-09-19_233436` (wahre Startzeit 23:34:36), Datalake neu gebaut. **Bonus:**
   der dritte WOT-Zug an diesem Tag lässt sich jetzt mit echtem RPM/APP/Gear statt
   Speed-Modell prüfen — bestätigt die 7150-7300-U/min-Limiterzone aus Punkt weiter unten
   ("ECU-Soft-Limiter") als reales Fahrerschaltverhalten knapp an der Grenze, kein
   Widerspruch. Siehe Logbuch "Viertes No-RTC-Vorkommnis…".

</details>

<details>
<summary>Vorheriger Stand (2026-09-19)</summary>

**Status 2026-09-19 — ECU-Soft-Limiter im 3. Gang gefunden (vor dem nominellen Redline),
`tpms_poller.py` um Drosselklappen-PID + schnelle Poll-Gruppe erweitert, KnockingRetard-PID
weiterhin unbekannt.** Details im Logbuch unten ("Drosselklappen-PID ergänzt…" und "ECU
begrenzt im 3. Gang…").

1. **Drosselklappenstellung (Mode-1-PID 0x11) neu in `tpms_poller.py`**, zusammen mit Lambda
   (0x44) in einer neuen schnellen Poll-Gruppe (`OBD1_FAST_PIDS`) ohne Intervall-Gate — Takt
   nur noch durch die ECU-Antwortzeit begrenzt. Batteriespannung bleibt bewusst langsam
   (10s-Takt wie Öl). Deployt auf den Pi, aber der Dienst lief beim Deploy nicht (kein
   CAN-Adapter dran) — aktiv erst beim nächsten Start.
2. **ECU-Eingriff im 3. Gang gefunden:** Log `2026-09-19 150438`, Volllast-Zug 77→137,5 km/h.
   Ab modellierter (nicht gemessener — kein Drehzahlkanal in diesem Log) Drehzahl von
   ~7100-7150 U/min schließt die ECU selbst die Drosselklappe (`ETC_ACT` bricht von 86° auf
   ~11° ein, Drehmoment 91%→0%) — spürbar vor dem nominellen 7500er-Redline.
3. **Klopfen als Auslöser nicht belegbar:** `TimingAdvance` steigt bis zum Cut sauber
   monoton, keine Klopf-typische Einbruch-Signatur davor. Ein echter Klopf-Kanal
   (`KnockingRetard`) fehlt in diesem Log komplett.
4. **`KnockingRetard`-PID weiterhin unbekannt** — keine SAE-J1979-Standard-PID, herstellerspezifisch
   (siehe "Bekannte offene Punkte" unten). Nutzer pollt sie jetzt selbst per Handy-App parallel
   zu einer Wiederholung des Drehzahl-Max-Tests; Auswertung ab 2026-09-20.

</details>

<details>
<summary>Vorheriger Stand (2026-09-16)</summary>

**Status 2026-09-16 — Renncockpit-Ansicht für den Pi gebaut, ein echter Zuverlässigkeits-
und ein echter Performance-Bug gefunden und gefixt.** Details im Logbuch unten
("Renncockpit-Ansicht implementiert…" bis "…TPMS zurück in die Ribbon + Performance-
Untersuchung"), volle Design-Historie inkl. Mockup-Vergleich im Chat-Verlauf der Nacht.

1. **Renncockpit-Ansicht (`DriveDashPanel`)** ersetzt den Status-/Testmodus-Screen auf dem
   Touchdisplay automatisch, sobald `EngineRPM > 300` bei laufendem Logging — RPM-Rundinstrument
   (Skala bis 8, Redline 7000–7400 bestätigt), Gas/Bremse/Kupplung als vertikale Balken,
   Lenkwinkel- und G-Kreis-Box, TPMS als 2×2-Raster neben Öl/Kühlwasser/Tank. Über mehrere
   Runden nach Nutzer-Feedback iteriert (Web-Mockup als Vorlage, dann eine handgezeichnete
   Korrektur-Skizze umgesetzt).
2. **Zuverlässigkeitsbug gefunden und gefixt:** ein angeschnittenes Ecken-Design (Chamfer) ließ
   auf dem Pi (Wayland/labwc) wiederholt und unvorhersehbar Panels leer — lokal auf X11 nie
   reproduzierbar, zwei Techniken probiert, beide fragil. Zurückgebaut auf einfache,
   nicht überlappende `tk.Frame`-Panels (`make_panel()`) — seither stabil.
3. **Performance-Bug mit echtem Profiling gefunden (`py-spy`, nicht geraten):**
   `StatusGui.update_state()` blendete beim Screen-Wechsel alte Panels nicht zuverlässig aus,
   die liefen mit eigenem 300ms-Timer unsichtbar weiter mit. Gefixt — Refresh-Kadenz spürbar
   verbessert, Median trifft jetzt die 300ms-Vorgabe exakt.
4. **Offen, noch nicht bei echter Fahrt verifiziert:** ein Rest an Latenz-Ausreißern (bis ~1s)
   blieb auch nach dem Fix — Verdacht auf ein Artefakt der Test-Simulation (`vcan0` kennt anders
   als der echte 500kbit-Bus keine Bandbreitenbremse), nicht bewiesen. Bei der nächsten echten
   Fahrt gezielt auf Ruckeln achten.

Diagnose-Instrumentierung (`MX5_PERF_DEBUG=1`, No-Op im Normalbetrieb) bleibt dauerhaft im Code.

</details>

<details>
<summary>Vorheriger Stand (2026-09-15)</summary>

**Status 2026-09-15 — drei Kernpunkte gelöst.** Details jeweils im Logbuch unten,
Gesamtplan in [`can-deep-search-plan.md`](../plans/can-deep-search-plan.md).

1. **ABS-Eingriffsindikator gefunden** (`ABS_Active`, 0x211 Bit 42) — das seit Wochen
   offene Kernziel. Möglich wurde es durch die Heimfahrt vom 15.09., das erste Log mit
   echten Regeleingriffen.
2. **Motoröltemperatur erschlossen** (DID 0x1310) — wird nicht gebroadcastet und vom Handy
   nur sporadisch abgefragt; seit 15.09. pollt der Pi sie selbst alle 10 s. Formel dreifach
   unabhängig bestätigt. Vom Nutzer ausdrücklich als wichtiger Kanal markiert.
3. **Der verworfene UDS-Verkehr** — `obd_from_can.py` las nur 1 von 4 Headern und keine
   ISO-TP-Multiframes. Behoben: sieben zusätzliche echte Messkanäle aus jedem Y-Kabel-Log.

Dabei wurden **drei früher als abgeschlossen geführte Befunde widerlegt** (0x86-Lenkwinkel
ist linear statt nichtlinear; OBD-Fusion liest nichts nativ mit; DID 0x1310 ist die
Öltemperatur, nicht der Kilometerstand) und ein Root-Cause-Bug gefunden, durch den in
**jedem** früheren Sweep beide Beschleunigungsanker stillschweigend fehlten.

Neue Werkzeuge: `can_opendbc_crosscheck.py`, `can_field_segmentation.py` (READ,
referenzfrei), `can_event_bit_diff.py`, `can_find_native_counterpart.py`, `uds_did_sweep.py`.

</details>

<details>
<summary>Vorheriger Stand (2026-09-14)</summary>


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

</details>

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
  **kalibriert:** `FLI% ≈ 2,486·raw-0,02`), `ActualEnginePercentTorque`✓ (0x167 Byte4, bis
  2026-09-15 `EngineLoad_or_Torque_pct_maybe`; ersetzte seinerzeit die Fehlannahme
  "MassAirFlowRate", 2026-09-14 cross-log bestätigt, 2026-09-15 per Übertragungstest gegen
  den Standard-PID 0x62 endgültig bestätigt und neu kalibriert – Details unten),
  EngineLoad_related_maybe (0x200,
  ebenfalls 2026-09-14 cross-log bestätigt, gleiche Last-Domäne, kein reines Duplikat),
  **`FuelCut`✓ (0x0FD Byte5 Bit1, NEU 2026-09-15)** – Schubabschaltung; bei Soll-Lambda>1,9
  zu 99% gesetzt, außerhalb des Schubbetriebs zu 0,4%. Relevant fürs Schleppmoment, und zwar
  mit voller Botschaftsrate statt 1-2 Hz OBD-Polling.
  **`ActualEnginePercentTorque` (0x167 Byte4, 2026-09-15 von `_maybe` hochgestuft)** –
  Übertragungstest gegen den Standard-PID 0x62 über zwei Fahrten: R²=0,962. Kalibrierung neu
  gefittet `3,0242·raw−177,849` (RMSE 4,07 %p, auf beiden Fahrten besser als die alte Formel).
  EngineRPM_related_3_maybe (0x42B, **neu 2026-09-14**, Byte1-2, bisher komplett leere
  Botschaft – korreliert mit EngineRPM aber nur R²=0,44, kein reines Duplikat des
  bekannten "Drehzahl×2"-Signals auf 0x130, Rohwert-Durchreichung ohne Formel).
- **Fahrdynamik (IMU, RCM):** Longitudinal_Acc_Raw✓ (G), Lateral_Acc_Raw✓ (G), YawRate_Raw✓
  (deg/s) – **2026-09-13: Vorzeichen korrigiert** (DBC nutzt SAE-Konvention +=links, Datalake
  jetzt durchgängig +=Rechtskurve wie der Rest des Projekts, siehe docs/logs/projekt-stand.md
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
- **Bremse/ABS:** **`ABS_Active`✓ (0x211 HS_ABS Bit42, NEU 2026-09-15)** – der
  ABS-Eingriffsindikator, siehe Logbuch. Die Botschaft 0x211 war bis dahin komplett leer.
  BrakePressure✓ (bar) – Vorzeichen- UND Offsetfehler der Community-DBC
  gefunden und behoben (2026-09-12, jetzt `(0,0012413, +32,7986)`, R²=0,986 gegen
  echten OBD-Referenzkanal), kein Rollover-Sonderfall mehr nötig. BBP_Brake_Booster_Pressure_2/
  BARO_Barometric_pressure (kPa), WheelSpeed_1-4✓ (km/h, Sentinel 0xFFFF gefiltert),
  DSC_Status (nur System an/aus, kein Regelungseingriff), VehicleSpeed✓ (km/h).
- **Lenkung:** Steering_Wheel_Absolute_Angle✓ (deg) – Nullpunkt per GPS bestätigt (0,0°
  Median-Offset), Lock-to-Lock-Range ±490° bestätigt. **`SteeringRate_Abs_maybe`/`SteeringRate_Dir_maybe`
  (0x082 Byte5-7, NEU 2026-09-26):** Betrag (12 Bit, `44|12@1+`, 0,5 deg/s je LSB, nicht kalibriert)
  und Vorzeichen (Byte7 Bit0) der Lenkgeschwindigkeit; r=0,988-0,998 gegen die Ableitung des
  Winkels in allen 28 Logs, siehe Logbuch "Byte-Sweep-Neulauf…". `SteeringAngle_EPAS` (0x86, EPAS, früher
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
  Zweitübertragung desselben Winkels, keine eigene Größe. Byte4-5 war Teil des Kanals (2026-09-26 aufgelöst: 15-Bit-Erweiterung, `SteeringAngle_EPAS_Abs_maybe`, nullpunktstabil, siehe Logbuch "0x086 Byte4-5 aufgelöst…").
  **`SteeringTorque_maybe` (0x240 Byte0, neu 2026-09-15)** – vermutlich das EPAS-Lenkmoment:
  Median exakt 0 im Stand und geradeaus, r gegen den Lenkwinkel nur 0,23-0,52 über das ganze
  Log aber 0,78-0,86 gefiltert auf >40 km/h (bei Parkiergeschwindigkeit dominiert der
  Reifenscrub), Streuung im Stand doppelt so hoch, und es läuft dem Winkel voraus.
  Einschränkung: nach Herausrechnen des Winkels bleiben nur r=0,14 gegen die
  Querbeschleunigung – keine unabhängige Querkraftmessung. Keine physikalische Einheit
  kalibrierbar, es gibt im Fahrzeug keinen Referenz-Momentenkanal.
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
- **Motoröltemperatur (DID 0x1310, NEU 2026-09-15):** `OilTemp_CAN`✓ (°C),
  Formel `((A·256)+B)/100−40`. Wird **nicht** gebroadcastet (Broadcast-Suche negativ) und der
  Standard-PID 0x5C wird vom Fahrzeug nicht unterstützt – deshalb pollt `tpms_poller.py` sie
  seit 15.09. selbst alle 10 s. Formel dreifach unabhängig bestätigt (Kaltstart Öl = Kühlwasser
  = Ansaugluft auf 0,3 °C; Warmlauf-Nachlauf bis 39 °C; warm 1,8 °C über dem Kühlwasser).
- **OBD-Kanäle direkt aus dem CAN-Log (neu 2026-09-14, 2026-09-15 stark erweitert):**
  Seit 15.09. werden **alle vier** UDS-Header dekodiert (vorher nur 0x7E0/0x7E8) und
  ISO-TP-Multiframes zusammengesetzt. Dadurch zusätzlich verfügbar: `MassAirFlow_CAN`✓ (g/s),
  `LambdaCommanded_CAN`✓ (SOLL-Lambda, kein Sondenmesswert!), `TimingAdvance_CAN`✓ (°),
  `EnginePercentTorque_CAN`✓ (%), sowie STEER_ANGL_EPS/STEER_SPD_EPS vom EPS-Modul (0x730).
  **`KnockRetard_CAN`✓ (°, NEU 2026-09-20):** DID `0x03EC` (PCM, herstellerspezifisch, keine
  SAE-Standard-PID), `signed_int16(raw)/512`, per Korrelation gegen ein Y-Splitter-Log
  identifiziert (73% bitgenau, R²=0,958) — Vorzeichen/Bedeutung noch nicht unabhängig
  bestätigt, siehe "Bekannte offene Punkte".
  Die gemessenen Sondenwerte liefert PID 0x34 – unterstützt, aber noch nicht abgefragt. wann immer das Y-Splitter-Kabel
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
- ~~**`KnockingRetard`-PID unbekannt**~~ – **GELÖST 2026-09-20**: DID `0x03EC` (Mode 0x22,
  PCM-Header 0x7E0/0x7E8), Formel `signed_int16(raw)/512`, per Korrelation gegen ein
  Y-Splitter-Log identifiziert (73% der App-Werte bitgenau, R²=0,958) — siehe Logbuch
  "KnockingRetard-PID gefunden…". Läuft jetzt in `tpms_poller.py`s schneller Poll-Gruppe und
  wird generisch aus jedem CAN-Log dekodiert (`KnockRetard_CAN`). Zweite unabhängige
  Bestätigung am 2026-09-20 dazugekommen (81% bitgenau, r=0,997 — siehe Logbuch "Viertes
  No-RTC-Vorkommnis…"). **Vorzeichen 2026-09-25 per Indizien geklärt:** negativ = Zündrücknahme
  (Anteil <-1° steigt mit Drehmoment von 0 % auf 28 %, Sägezahn-Signatur bei stationärer
  Hochlast) — `TimingAdvance` (PID 0x0E) zeigt die Rücknahme aber nicht, siehe Logbuch
  "KnockRetard-Vorzeichen…".
- **ECU-Soft-Limiter im 2./3. Gang (2026-09-19/20):** schließt die Drosselklappe trotz
  `APP`=100% schon deutlich vor dem nominellen 7500er-Redline. **2026-09-20 Nachmittag an
  5 Eingriffen über beide letzten Logs vom 19.09. bestätigt** (7273/7281/7439/7429/7307
  U/min, Gang 2+3 — siehe Logbuch "ECU-Soft-Limiter…"), plus 2 klar unterschiedene
  fahrerinitiierte Schaltvorgänge ohne Eingriff (7215/7297 U/min) als Negativkontrolle.
  **Klopfen ausgeschlossen** (`KnockRetard_CAN` bleibt bei allen 5 Events im Nahe-Null-Band).
  **Radschlupf/DSC-Traktionseingriff ebenfalls ausgeschlossen** (Radgeschwindigkeits-Spread
  ≤2,3 km/h, unter dem 4,5-km/h-Rauschboden; `ABS_Active_CAN`/`DSC_Status_CAN` durchgehend 0).
  **Der tatsächliche Auslöser bleibt offen (2026-09-26: Ereignis-Bitdiff, Anstiegs- und Zeitgeber-Hypothese offline geprüft, ohne Erfolg — Logbuch "Soft-Limiter offline…"; neue gezielte Vollgaszüge in Gang 2-4 nötig)** — auffällig ist die Gangabhängigkeit der
  Cut-Schwelle (Gang 2 ~7350-7440 U/min, Gang 3 ~7200-7310 U/min), die gegen einen simplen
  festen RPM-Trigger spricht. **Live-Erkennung jetzt im Dash implementiert** (`dash_gui.py`:
  RPM>7000 & `APP`≥99% & `ETC_ACT`<90 → Shiftlights blinken blau, 8 Hz), deployt auf dem Pi.
  Frühere Einzelbefunde (Log `150438`/`163857`, dritter Zug bei 7269 U/min ohne Eingriff)
  weiterhin gültig, siehe Logbuch "ECU begrenzt im 3. Gang…" und "Viertes
  No-RTC-Vorkommnis…".
- **Sweep-Neulauf 2026-09-26 (alle 28 Logs, korrigierte Anker):** offene Kandidaten `0x20A` (22 Logs);
  `0x086` Byte4-5 ist als zweite Winkelspur aufgelöst; `0x4DB` HS_DCDC ist als i-ELOOP-Rekuperationszustand
  geklärt (siehe Logbuch "0x4DB…"). Tabelle im Logbuch "Byte-Sweep-Neulauf…". Der alte Konsolidierungsstand vom 14.09. ist überholt.
- Reverse-Gang (`MT_Gear_Actual=7`) registriert bisher nur bei stabiler, nicht rutschender
  Kupplung – Hypothese noch nicht durch eine gezielte Testfahrt bestätigt.
- ~~**ABS/DSC-Eingriffsindikator nicht gefunden**~~ – **GELÖST 2026-09-15**:
  `ABS_Active` = 0x211 (HS_ABS) Bit 42. Über ein 35-Minuten-Log zu 0,052 % gesetzt, in genau
  den zwei Phasen mit echter ABS-Modulation; Negativkontrolle 0,000 % in der Hinfahrt
  desselben Tages. Siehe Logbuch, Abschnitt "ABS-EINGRIFFSINDIKATOR GEFUNDEN". Es fehlte
  beides gleichzeitig: ein echtes Ereignis (in allen früheren Logs blieb die Radspreizung
  beim Bremsen unter 2,2 km/h) **und** das richtige Vergleichsfenster (Regelphase gegen den
  Rest DERSELBEN Bremsung, nicht gegen den Rest des Logs).
  **Weiterhin offen:** ob dasselbe Bit auch bei einem reinen DSC-/Traktionseingriff ohne
  Bremsung gesetzt wird – dafür fehlt noch ein Ereignis. **Erster Kandidat 2026-09-15 geprüft
  und verworfen** (Vollgas-Pull mit echtem Hinterradschlupf, Heimfahrt t=708–745 s): es gab
  dort gar keinen Eingriff – weder `ABS_Active`, noch Bremsdruck, noch ein Momenteneinbruch,
  und ein Rarity-Scan über alle 105 IDs × 64 Bits findet kein Ereignisbit. Siehe Logbuch,
  letzter Abschnitt. Nebenbefund: `DSC_Status` (0x415 Bit 4) ist als Zustandsbeleg unbrauchbar
  (dauerhaft "Off" bei nie gedrücktem `DSC_OFF_Switch`).
- AFR/Lambda, CommandEquivalenceRatio, TimingAdvance, ETC_ACT (echte Drosselklappenstellung):
  bestätigt nicht periodisch auf HS-CAN broadcastet (nur Mode-22-Polling) – seit 2026-09-14
  aber direkt aus dem CAN-Log dekodierbar, ohne Handy/`.dlg` (siehe oben). **2026-09-14,
  gezielter `can_bitsearch.py`-Lauf (3 Logs) gegen AFR_MZ/ETC_ACT als Referenz:** bestätigt
  dies nochmal deutlich rigoroser. Bester AFR_MZ-Kandidat (`0x0FD` Byte 5) fittet zwar über
  alle 3 Logs auf dieselbe Bitlage, aber R² fällt 0,86→0,66→0,63 UND der Rohwert nimmt nur
  3 diskrete Werte {0,1,2} an – kein analoges Lambda-Signal, eher ein Status-Flag (z.B.
  Closed-Loop/Warmlauf-Zustand), das nur zufällig mit dem AFR-Trend mitläuft.
  **NACHTRAG 2026-09-15: dieses Status-Flag ist jetzt identifiziert** – es ist die
  **Schubabschaltung** (`FuelCut`, 0x0FD Byte5 Bit1). Die Einschätzung von damals war also
  richtig, nur die Bedeutung fehlte. Beleg: bei Soll-Lambda > 1,9 (Kraftstoff abgeschaltet)
  ist das Bit zu 99,4 % bzw. 98,8 % gesetzt, außerhalb von Schubbetrieb nur zu 0,4 %. Zweiter
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
  (`SteeringAngle_EPAS_Coarse`); Byte4-5 ist seit 2026-09-26 aufgelöst (`SteeringAngle_EPAS_Abs_maybe`, 15 Bit, nullpunktstabile zweite Winkelspur), die frühere Einstufung als
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
   docs/logs/projekt-stand.md, 220 Kurven aus 7 Fahrten).
6. Pi-GUI-Lag-Fix bei der nächsten Fahrt live prüfen (soll den >2s-Gauge-Lag behoben haben).
7. **Gezielte Testfahrt** – der ABS-Teil hat sich am 2026-09-15 von selbst erledigt (die
   Heimfahrt enthielt zwei echte Eingriffe, Indikator gefunden). Offen bleiben: Rückwärtsgang
   mit vollständig durchgetretener statt rutschender Kupplung, und ein **reiner DSC-/
   Traktionseingriff ohne Bremsung** – nur damit lässt sich klären, ob `ABS_Active` auch
   dafür gesetzt wird oder ob es ein zweites Flag gibt.
8. ~~Nichtlineare Umrechnung für `SteeringAngle_related_3`~~ – entfällt (2026-09-15, siehe oben:
   falsche Bitlage; der echte Kanal ist linear).
9. Den CAN-Byte-Sweep (`scripts/can_byte_search.py`) bei künftigen neuen Logs erneut laufen
   lassen. **Wichtig (2026-09-15):** alle früheren Sweeps liefen mit unvollständigem Ankersatz –
   beide Beschleunigungsanker und `PROXY_high_lat_g` fehlten wegen vertauschter CAN-IDs
   stillschweigend. Jetzt 29 statt 19 Anker; ein Wiederholungslauf über die bestehenden Logs
   lohnt sich unabhängig von neuen Fahrten.
10. **Die Heimfahrt vom 15.09. ab t=1880 s als Referenzdatensatz nutzen** – sie ist mit ±1,0 g
    quer, −0,98 g längs und zwei ABS-Eingriffen das mit Abstand dynamischste Material im
    Bestand. Naheliegend: Bremsmodell gegen die Verzögerung *während* der Regelung prüfen
    (das ist die tatsächliche Haftgrenze), und den Grip-Schätzer gegen die ±1,0-g-Kurven.
11. **Mode-1-Bestandsaufnahme mit laufendem Motor wiederholen** (`RUN_PROBE` ist scharf, der
    Auslöser wartet jetzt auf Drehzahl > 400). Der erste Lauf fiel bei stehendem Motor an;
    die statischen Ergebnisse gelten, die gemessenen Lambdawerte (PID 0x34) fehlen noch.
12. **Der 0x0FD-Byte4-5-Fund zeigt, dass sich ein zweiter Blick lohnt:** dasselbe Byte war
    am 2026-09-14 schon als "Status-Flag" aufgefallen und mangels Deutung liegengeblieben.
    Die übrigen damals verworfenen Kandidaten (`0x20A`, `0x200`) sind unter demselben
    Gesichtspunkt nochmal anzusehen – nicht als analoge Messwerte, sondern als Zustandsbits.
13. ~~KnockRetard-PID identifizieren~~ – **erledigt 2026-09-20**, siehe oben (DID 0x03EC).
14. ~~KnockRetard-Formel an einem zweiten Log bestätigen~~ – **erledigt 2026-09-20** (81%
    bitgenau, r=0,997). **2026-09-25 per Indizien geklärt** (negativ = Zündrücknahme, siehe Logbuch
    "KnockRetard-Vorzeichen…"); ein direkter Nachweis über einen Winkel nach Korrektur fehlt noch.
15. **Bei jedem CAN-only-Log ohne begleitendes dlg/GPS: Dateiname NICHT blind vertrauen, wenn
    kein Widerspruch zu den Frame-Zeitstempeln vorliegt** — der 2026-09-20 gefundene Fall
    (`candump-2026-09-19_165600`→`_233436`) zeigt, dass Dateiname und Frame-Zeitstempel auch
    GEMEINSAM falsch sein können (beide unter derselben nie synchronisierten Uhr entstanden).
    `log_start_epoch()` kann das strukturell nicht erkennen — nur ein externer Abgleich
    (dlg/GPS) deckt es auf. Bei Verdacht (z.B. Nutzer erinnert sich an einen Stromausfall/
    Neustart) aktiv per Kreuzkorrelation gegenprüfen, nicht auf die automatische Erkennung
    verlassen.

## Zusätzliche Notizen (Claude-Memory)
Ergänzend zu diesem Dokument gepflegt, überlebt Kontext-Resets:
- `mx5_can_bus_logging.md` — laufendes Erkenntnis-Log (Bugfixes, Byte-Suchen, offene Fragen wie Reverse-Gang, das komplette 2026-09-14-CAN-Byte-Search-Projekt in voller Detailtiefe)
- `mx5_nd3_obdii_repo_reference.md` — externe Referenz (ND3-Repo), Parallele zu Gear_CAN/MT_Gear_Recommend
- `mx5_datalake_can_channels.md` — aktueller Stand von `CAN_SIGNAL_MAP` in `build_datalake.py`, DuckDB-Viewer-Setup
- `mx5_tpms.md` — TPMS-Historie (Aufbau, Live-Test, Vorderachsen-Frage)
- `mx5_pi_status_gui_lag_fix.md` — Diagnose/Fix des Touchdisplay-Gauge-Lags
- `feedback_background_wait_loops.md` — Session-Mechanik-Lehre (nicht CAN-Projekt-Inhalt): Warteschleifen brauchen eine konkrete PID, kein Namensmuster
- `mx5_knock_retard_pid.md` — KnockingRetard-PID-Recherche (negativ), Nutzer-Plan für den nächsten Log

## Hardware
- **USB-CAN-Adapter:** DSD TECH SH-C31A, basierend auf CANable 2.0 (STM32, candleLight-Firmware)
  - USB-ID im Work Mode: `1d50:606f` (OpenMoko/Geschwister Schneider)
  - USB-ID im DFU-Bootloader: `0483:df11`
  - Schalter: **BOOT = OFF** (Work Mode), **R120 = OFF** (Adapter hängt nur als Stich am OBD-Port, kein Busende – R120=ON würde die Terminierung verfälschen)
- **Verkabelung:** selbst zusammengelötet, Cat5e-Patchkabel (grün, Adern orange/blau/gelb/weiß) zwischen Adapter und OBD-Stecker, ca. 30 cm lang (Verdrillung bei dieser kurzen Länge unkritisch)
- **OBD2-Pinbelegung (relevant):** Pin 6 = CAN_H, Pin 14 = CAN_L, Pin 4/5 = GND, Pin 16 = +12V
- Angeschlossen an einen Raspberry Pi, der die Logs schreibt; Übertragung per `scp`/`rsync` auf den Rechner
