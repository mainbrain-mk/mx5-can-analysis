# Deep-Search-Plan: mehr Daten aus den CAN-Logs (2026-09-15)

## Ausgangslage
147 native CAN-Signale dekodiert, Kalibrierung gegen OBD-Ground-Truth aus demselben Log
(Y-Splitter), `can_bitsearch.py`/`can_byte_search.py` als Sweep-Werkzeuge, Cross-Log-
Validierung etabliert. Vollständiger Stand: [`mx5_can_bus_status.md`](mx5_can_bus_status.md).

**Die strukturelle Grenze:** unsere Methode findet nur Signale, für die wir schon eine
*Referenzgröße* haben (OBD-DID, anderes DBC-Signal, IMU). Für die 107 komplett leeren
Botschaften gibt es per Definition keine Referenz. Dort sind wir blind. Der Plan setzt
genau dort an, nicht bei "noch ein Fremd-DBC".

## Tracks

### Track 1 — Referenzfreie Feld-Segmentierung (akademische Verfahren)
Segmentierung von Frames allein aus der Bit-Flip-Statistik in Felder: Konstante / Zähler /
CRC / physikalischer Wert / Multiplexer. Fahrzeugunabhängig, läuft auf vorhandenen Logs.
- READ (Marchetti & Stabili, IEEE TIFS 2019) — Bit-Flip-Rate -> Feldgrenzen
- LibreCAN (Pesé et al., CCS-Workshop 2019) — dreiphasig, koppelt an Telefon-IMU (haben wir)
- CAN-D (Oak Ridge National Lab) — 4-stufige Pipeline inkl. Endianness/Signedness
- CANMatch / ACTT — Match gegen bekannte Frame-Strukturen desselben Herstellers
- Nebengewinn: erkannte Zähler & CRCs aus dem Suchraum werfen = weniger Falsch-Positive
Ertrag: hoch. Aufwand: mittel.

### Track 2 — Der zweite Bus (MS-CAN / Body-CAN)
Wir loggen nur HS-CAN auf `can0`. Eine `MX5ND_6thGenMazda_MSCAN.dbc` liegt bereits ungenutzt
im Repo -> der zweite Bus ist dokumentiert, wir schneiden ihn nur nicht mit.
- OBD-Pinbelegung MX-5 ND: liegt MS-CAN auf Pin 3/11? Baudrate, Terminierung
- Gateway-Filterung (CMU/BCM)
- Zweiter Adapter am Pi (`can1`), `session_logger.py` auf zwei Interfaces
Ertrag: potenziell am höchsten. Aufwand: Hardware + halber Tag Integration.

### Track 3 — UDS über Polling hinaus
TPMS zeigt, dass der aktive Weg funktioniert; wir nutzen nur den simpelsten Teil.
- `0x2A` ReadDataByPeriodicIdentifier — ECU sendet DID selbstständig mit 25/100/1000 ms
  -> Polling-Kanäle (ETC_ACT, AFR, Bremsdruck) werden zu echter Telemetrie
- `0x10` DiagnosticSessionControl (Extended Session) — schaltet oft zusätzliche DIDs frei
- Vollständiger DID-Sweep: `0x22` über alle 65536 DIDs pro ECU-Adresse, im Stand, NRC-Auswertung
- `0x19` ReadDTCInformation / Freeze Frame — evtl. Rückweg zum ABS/DSC-Eingriff
- SICHERHEIT: Rate-Limits, Session-Timeouts; `0x2E`/`0x31`/`0x11` niemals anfassen
Ertrag: hoch, sofort nutzbar. Aufwand: niedrig (Infrastruktur `tpms_poller.py` existiert).

### Track 4 — Herstellerdokumentation als Signal-Katalog
Werkstatthandbuch-Kapitel "Multiplex Communication System [CAN]" listet pro Steuergerät,
welche Signale es überhaupt sendet -> Landkarte statt Raten.
- ND-Werkstatthandbuch / Wiring-Diagram; auch Mazda3/CX-5 derselben SkyActiv-Generation
- Mazda-Enhanced-PID-Listen (Torque Pro / OBD Fusion) -> Namen für `DA86`/`F40C`/`4028`
- DTC-Listen ABS/DSC
Ertrag: mittel-hoch, steuert die anderen Tracks. Aufwand: niedrig.

### Track 5 — Fremd-DBCs derselben Plattform
- opendbc (comma.ai), `mazda_2017.dbc` u.a. — gepflegt, produktiv, Lenkmoment/LKAS/ACC/Radar
- RaceChrono-/RaceCapture-Community-Threads zum ND, Foren
- CX-5/Mazda3/CX-30-DBCs als Ersatzreferenz
Grundregel: nicht blind übernehmen, jede Fremdformel gegen unsere Logs prüfen.
Ertrag: mittel. Aufwand: niedrig.

### Track 6 — Bessere Methodik für Ereignis-Signale (ABS/DSC)
Korrelation über einen ganzen Log ist das falsche Werkzeug für ein Flag, das 300 ms lang
einmal pro Fahrt gesetzt wird.
- Fenster-Differenzanalyse: Baseline-Bitverteilung vs. 2-s-Fenster um ein Ereignis
- Provozierte Ereignisse im Stand (ABS-Selbsttest, Handbremse, DSC-Taster, Lenkanschlag)
- Kombiinstrument/HUD als Oracle: jede Kontrollleuchte muss als Bit auf dem Bus liegen
Ertrag: mittel, schliesst einen namentlich offenen Punkt. Aufwand: niedrig-mittel.

## Reihenfolge
Schreibtischarbeit zuerst (steuert alles Weitere), Fahrzeug-/Hardware-abhängiges zuletzt:
1. T5 + T4 (reine Recherche, billig, liefert die Landkarte)
2. T2-Recherche (MSCAN-DBC ist schon da -> Frage ist nur Pinout/Hardware)
3. T1 (Implementierung auf vorhandenen Logs — grösster analytischer Hebel)
4. T6 (Analyse auf vorhandenen Logs)
5. T3 (Werkzeug bauen; Ausführung braucht Fahrzeug mit Zündung ON)

## Ergebnis-Log
Wird während der Abarbeitung unten fortgeschrieben.

---

# Ergebnisse der Abarbeitung (2026-09-15)

Reihenfolge wie geplant: Schreibtischarbeit zuerst. Track 5 wurde vorgezogen und lieferte
sofort so viel, dass Track 1 und ein ungeplanter Track 3b daran anschlossen. Track 2 blieb
Recherche (Hardware-Entscheidung offen), Track 3 ist gebaut aber nicht ausgefuehrt
(Fahrzeug war aus).

## Track 5 - Fremd-DBCs: opendbc ist die bisher groesste ungenutzte Quelle

**`commaai/opendbc` -> `mazda_2017.dbc`** (CX-5/Mazda3 ab MY2017) benutzt **exakt dieselben
CAN-IDs wie unser MX-5 ND** - dieselbe SkyActiv-Busarchitektur. 84 von 102 IDs sind
identisch. Bei **58 davon hat opendbc mehr Signale als wir**, darunter Botschaften, die bei
uns komplett leer sind: `0x415 TRACTION`, `0x217 CURVE_CTRS`, `0x21F CRZ_EVENTS`,
`0x340 SEATBELT`, `0x340`, `0x436 HVAC`, `0x242-0x246` (Frontkamera/Spurdaten).

Nicht vorhanden bei uns (erwartet): `0x361-0x366` Radar/MRCC - unser Fahrzeug hat es nicht.

Gebaut: **`scripts/can_opendbc_crosscheck.py`** - dekodiert jedes Fremdsignal aus unseren
eigenen Logs und bewertet Variabilitaet, Zaehler-/Checksummen-Verdacht, Bitueberlappung mit
unserer DBC und Korrelation gegen unsere validierten Anker (gleiche Doppelschwelle wie
`can_byte_search.py`). Nichts wird blind uebernommen.

Upstream-Check nebenbei: `berumiya/CAN_DBC_6thGenMazda` hat seit unserem Stand nichts
Neues (letzter Commit 2026-07-22), Racelogics VBOX-Datenblatt fuer den ND listet nur
9 Kanaele, alle bereits bei uns - beide Quellen erschoepft.

## Groesster Einzelfund: `SteeringAngle_related`@0x86 ist LINEAR, nicht nichtlinear

Der Befund vom 2026-09-14 ("stark nichtlinear, Isotonic-Regression noetig, R²=0,90,
RMSE=10,5°") und die daraus folgende Verwerfung der externen linearen Formel am 2026-09-15
waren **beide falsch** - aus demselben Grund.

Bitlage war die ganze Zeit richtig (`7|16@0+`, identisch mit opendbc). Der Fehler: das
oberste Bit ist **kein Teil des Zahlenwerts, sondern ein Gueltigkeits-/Init-Flag** des EPAS.
Es ist in 0,0-0,5% der Frames gesetzt - ausschliesslich in einem Block am Logstart bei
Stillstand, mit konstantem Rohwert 16000 (= exakt 0°) plus 32768. Diese wenigen Frames
springen um 32768 und druecken den Pearson ueber das ganze Log von 1,00 auf 0,50.

Spearman war gegen diese Ausreisser robust - daher die Fehldiagnose "monoton aber
nichtlinear". Tatsaechlich:

| | altes Isotonic-Modell | neue lineare Formel |
|---|---|---|
| R² (out-of-sample) | 0,90 | **0,9997-0,99995** (6 Logs) |
| RMSE | 10,5° | **0,65-0,94°** |

DBC korrigiert: `SteeringAngle_EPAS : 6|15@0+ (0.1,-1600) "deg"` plus
`SteeringAngle_EPAS_Invalid : 7|1@0+`. Damit ist `scripts/can_steering_angle_0x86.py`
(Isotonic-Lookup) obsolet.

**Lehre:** `can_re_toolkit.detect_extreme_outliers` haette das gefunden, wurde auf dieses
Signal aber nie angewendet, weil es als "erklaert" galt. Ein 0,1%-Ausreisser-Cluster kann
eine Korrelationsbewertung komplett kippen.

## Track 1 - referenzfreie Feld-Segmentierung (READ)

Gebaut: **`scripts/can_field_segmentation.py`** nach Marchetti & Stabili (IEEE TIFS 2019).
Braucht keine Referenzgroesse: segmentiert Botschaften allein aus der Bit-Kipprate in
Felder und klassifiziert sie (CONST / COUNTER / CRC / PHYSICAL / FLAG). Ergaenzt um einen
**Signedness-Verdachtstest** fuer den Bug-Typ, der das Projekt schon zweimal getroffen hat
(BrakePressure, AmbientTemp - beide unsigned statt signed gelesen).

Validierung gegen unsere 147 bekannten Signale: READ findet Drehzahl, Geschwindigkeit,
Gaspedal, alle vier Radgeschwindigkeiten, Lenkwinkel, Gierrate und Querbeschleunigung
bitgenau wieder. Verfahren bestaetigt.

Ausbeute ueber 11 Logs: 2059 Felder, davon **177 unbelegte PHYSICAL/FLAG-Felder** in
mindestens 6 Logs - vor allem `0x08A` (HS_DCDC, 100 Hz, 5 analoge Felder, in KEINER DBC),
`0x45A`, `0x3D2`, `0x242/0x245/0x246` (Kameradaten), `0x200` (zwei 16-Bit-Felder um 32768
zentriert = klassische signierte Sensoren).

## Track 3b (ungeplant, groesster praktischer Gewinn): der UDS-Verkehr, den wir verworfen haben

Beim Recherchieren der ND3-Quelle fiel auf, dass unsere eigenen Logs UDS-Verkehr auf
**drei Headern enthalten, die `obd_from_can.py` nie angefasst hat** - es dekodierte nur
`0x7E0/0x7E8`:

- **`0x730/0x738` (EPS), 10.820 Frames pro Log**: DIDs `0x3301` und `0x3302`, mit voller
  Rate vom Handy gepollt. Identifiziert: `0x3302` = **STEER_ANGL_EPS** (Lenkwinkel,
  R²=0,998 ueber 3 Logs), `0x3301` = **STEER_SPD_EPS** (Lenkgeschwindigkeit,
  r=0,82-0,94 gegen die Ableitung des Winkels).
- **`0x760/0x768` (DSC)**: DID `0x2B0D` = Bremspedalstellung (Zuordnung aus der
  ND3-Quelle).
- **ISO-TP-Multiframe-Antworten** wurden komplett verworfen. OBD-Fusion buendelt fuenf
  Mode-1-PIDs in eine Anfrage (`06 01 0D 10 44 0E 62`); die Antwort passt nicht in einen
  Single Frame.

Damit **kippt der zweite "abgeschlossene" Befund vom 2026-09-14**: die Annahme, OBD-Fusion
lese `VehicleSpeed`/`STEER_ANGL_EPS` nativ vom CAN mit und berechne
`MassAirFlowRate`/`CommandEquivalenceRatio`/`TimingAdvance` clientseitig. Alle sind echte,
gemessene OBD-Anfragen - wir konnten sie nur nicht sehen.

Fuenf zusaetzliche echte Messkanaele, ab sofort aus jedem Y-Kabel-Log, mit
SAE-Standardformeln plausibel:

| PID | Kanal | Formel | Bereich im Log |
|---|---|---|---|
| 0x0D | VehicleSpeed | A | 0-218 km/h (r=1,0000 gegen CAN) |
| 0x10 | MassAirFlowRate | A/100 | 0-152 g/s |
| 0x44 | **CommandEquivalenceRatio (Lambda)** | A/32768 | 0,81-2,00 |
| 0x0E | TimingAdvance | A/2-64 | -30 bis +53° |
| 0x62 | ActualEnginePercentTorque | A-125 | 0-97 % |

Besonders wertvoll: **Lambda**. Fuer AFR gibt es nachweislich kein natives CAN-Signal
(rigoroser Bitsearch 2026-09-14) - jetzt liegt der echte Messwert mit 2 Hz vor.
Und der **echte MAF** entlarvt unsere bisherige Schaetzformel
`0,00019*(RPM*MAP)-8,08`: gegen den gemessenen Wert nur R²=0,87 (RMSE 7,4 g/s), neu
gefittet 0,90 - brauchbar, aber kein Ersatz fuer die Messung.

## Root-Cause-Bug: beide Beschleunigungsanker fehlten in JEDEM bisherigen Sweep

`ANCHOR_SIGNALS` in `can_byte_search.py` hatte Laengs- und Querbeschleunigung vertauscht
(`Longitudinal_Acc_Raw` auf 0x75 statt 0x76, `Lateral_Acc_Raw` auf 0x76 statt 0x75).
`extract_anchors()` verwirft einen Anker **stillschweigend**, wenn der Signalname in der
Botschaft fehlt - beide Anker fehlten also in jedem Sweep, und der davon abhaengige
`PROXY_high_lat_g` wurde nie gebaut. Das ist eine plausible Miterklaerung, warum der
DSC-Eingriffsindikator nie gefunden wurde: die relevanteste Referenzgroesse war nie dabei.

Anker-Zahl nach Fix und Erweiterung: **29 statt 19**.

## Track 6 - Ereignis-Bit-Differenzanalyse

Gebaut: **`scripts/can_event_bit_diff.py`**. Korrelation ueber ein ganzes Log ist das
falsche Werkzeug fuer ein Flag, das pro Fahrt einmal 300 ms gesetzt wird. Stattdessen:
Setzquote jedes Bits innerhalb eines Ereignisfensters gegen ausserhalb.

Funktionsnachweis am bekannten Bremsereignis bestanden - findet `BrakePressure` und,
staerker, `0x415` Bit 10 (bei uns nur als `BrakeRelated_weak_maybe` gefuehrt, Lift 0,82-0,86).

ABS/DSC: **kein Ergebnis, aber jetzt aus einem klaren Grund** - in keinem der 11 Logs gibt
es ein Ereignis, das die Schwellen erreicht (max. 154 Samples ueber 30 bar Bremsdruck, keine
nennenswerte Radgeschwindigkeits-Divergenz). Das bestaetigt die bisherige Einschaetzung: es
fehlen die Rohdaten, nicht die Methode. Das Werkzeug steht jetzt fuer die gezielte Testfahrt bereit.

## Track 2 - MS-CAN: Recherche abgeschlossen, Hardware-Entscheidung offen

- Zweiter Bus liegt bei Mazda auf **OBD-Pin 3/11 mit 125 kbit** (Pin 6/14 = HS-CAN 500 kbit).
- `data/can/MX5ND_6thGenMazda_MSCAN.dbc` liegt bereits im Repo, wird aber nie geladen
  (wir loggen nur `can0`): 34 Botschaften, nur 6 Signale - reines Geruest.
- Inhaltlich interessant sind vor allem `MS_EATC` (Klima - **erklaert, warum die
  Klima-Standtests am 2026-09-12 groesstenteils unbrauchbar waren**) und `MS_IC_BCMM`
  (Karosserie). Viele `MS_IC_*`-IDs sind allerdings gateway-gespiegelte HS-CAN-Botschaften
  (0x78, 0x79, 0x202, 0x215 tauchen dort mit denselben Nummern auf) - der echte Zugewinn
  ist also kleiner als die 34 IDs suggerieren.
- Racelogic dokumentiert fuer den ND ausdruecklich nur Pin 6/14 - kein Gegenbeweis, aber
  ein Hinweis, dass die interessanten Fahrdynamikdaten auf HS-CAN liegen.

**Empfehlung: zurueckstellen.** Ein zweiter Adapter (~30 EUR) plus Integration lohnt erst,
wenn Klima-/Karosseriedaten wirklich gebraucht werden. Die 177 unbelegten Felder auf dem
HS-CAN sind der billigere Hebel.

## Track 3 - UDS ausreizen: Recherche fertig, Ausfuehrung braucht das Fahrzeug

- **`0x2A` ReadDataByPeriodicIdentifier ist vermutlich eine Sackgasse**: der Dienst nimmt
  nur 1-Byte-periodicDataIdentifier aus dem Bereich `0xF2xx`. Unsere DIDs (0xDA85, 0x093C,
  0x2A0x, 0x33xx) liegen nicht darin. Billig zu testen, aber keine Erwartung.
- **Vollstaendiger DID-Sweep ist der sichere Hebel.** Externe Referenz
  (`drewid74/2024-nd3-mazda-obdii`, Range-Scan 2026-06-23 am ND3) zeigt Machbarkeit und
  Ausbeute: 11 Bloecke a 256 DIDs in 388 s, u.a. 60 Treffer im PCM-Block `22 F4 xx`,
  36 in `22 DA xx`, 38 in `22 03 xx`, 8 in `22 09 xx` (Nockenwelle/VVT), `22 13 10` =
  **Oeltemperatur** (`((A*256)+B)/100-40`).
- **Wichtigster Zielheader fuer unsere offene Frage: `0x760` (DSC).** Dort fanden sie
  `22 2B xx` (11 Treffer, Fahrwerk) und `22 20 xx` (4 Treffer, Lenkung). Ein
  ABS/DSC-Eingriffsindikator, falls per UDS abfragbar, liegt mit hoher Wahrscheinlichkeit
  in diesen Bloecken.
- Sicherheitsregeln fuer den Sweep: nur `0x22` (ReadDataByIdentifier) und `0x19`
  (ReadDTCInformation), **niemals** `0x2E` (Write), `0x31` (RoutineControl), `0x11`
  (ECUReset), `0x28` (CommunicationControl). Fahrzeug im Stand, Motor an.
- Status: Pi (`car`, 192.168.0.247) erreichbar, aber kein CAN-Traffic - Fahrzeug aus.
  Ausfuehrung bei der naechsten Gelegenheit.

## Offene Punkte / naechste Schritte

1. DID-Sweep ausfuehren, sobald das Fahrzeug an ist - Prioritaet auf `0x760/22 2B xx` und
   `0x760/22 20 xx` (ABS/DSC), dann `0x7E0/22 13 xx` (Oeltemperatur!) und `22 F4 xx`.
2. Die 177 unbelegten READ-Felder abarbeiten - der Korrelationslauf gegen die jetzt 29
   Anker laeuft; danach die staerksten Kandidaten einzeln pruefen.
3. `0x240` Byte0 (opendbc `STEER_TORQUE_SENSOR`, signed, Offset -127): r=0,63-0,81 gegen
   den Lenkwinkel ueber 7 Logs, ueberlappt kein bekanntes Signal - sehr wahrscheinlich das
   **EPAS-Lenkmoment**. Braucht eine eigene Kalibrierung.
4. `0x08A` (HS_DCDC, 100 Hz): fuenf analoge Felder, in keiner DBC. Verdacht i-ELOOP /
   Lichtmaschinenregelung - waere als parasitaerer Widerstandsterm im Fahrleistungsmodell
   relevant.
5. `scripts/can_steering_angle_0x86.py` als obsolet markieren oder loeschen.
6. Alle frueheren Sweeps mit den korrigierten/erweiterten Ankern wiederholen - sie liefen
   ohne Beschleunigung, ohne Lambda, ohne MAF und ohne die EPS-Kanaele.
7. ISO-TP-Multiframe gilt jetzt fuer Antworten; **Requests** mit mehr als 7 Byte werden
   weiterhin nicht zusammengesetzt (bisher nicht noetig).
