# CAN: neu identifizierte und noch offene Werte, mit Fahrzeugtest-Strategien

**Stand 2026-09-26.** In-place pflegen. Herleitungen stehen im Logbuch
[`logs/can-bus-status.md`](../logs/can-bus-status.md) (Abschnitt "Offline-Ausbeute…") und
in den `CM_`-Kommentaren der DBC (`data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc`). Plan und
Methoden: [`plans/can-offline-ausbeute-plan.md`](../plans/can-offline-ausbeute-plan.md).

Zweck: jeder variierende Wert auf dem Bus soll hier stehen, entweder zugeordnet (Teil A) oder
mit beobachtetem Verhalten und einem konkreten Test, der ihn am Auto klärt (Teil B). Teil C
bündelt die Tests zu einem Programm für den nächsten Fahrzeugtermin.

**Fortschritt:** im Referenzlog `candump-2026-09-18_093544` variieren 1977 Bits; ohne DBC-Signal
waren es vor dem 26.09. **1311**, jetzt **1124**. Über alle Logs sind noch 221 Felder offen,
die in mindestens der Hälfte der Logs variieren (`scripts/can_open_fields.py`,
`results/can_open_fields.csv`): 32 Messwert-Felder (419 Bit), 42 Zustandsfelder, 128 Flags,
10 Zähler, 9 Prüfsummen/Rauschen. Der Großteil der verbliebenen Messwert-Bits sitzt in
Kamera- (0x242-0x246) und Infotainment-Botschaften (0x3D2, 0x4FE) sowie in Zählern und
Prüfsummen.

## A. Neu identifiziert (26.09., nur vorhandene Logs)

"bestätigt" = physikalisch eindeutig belegt; `_maybe` = Deutung oder Skala offen.
Spalte "Test" verweist auf Teil C.

| Signal (DBC) | ID, Bits | Formel | Beleg (kurz) | Status | Test |
|---|---|---|---|---|---|
| `TCS_Active_maybe` | 0x211 Bit 40 | Flag | 6 Episoden in 5 Logs, alle am Kurvenausgang mit Hinterachsschlupf; Motormoment fällt bei konstantem Pedal | _maybe | C6 |
| `TCS_TorqueRequest_maybe` | 0x211 23\|16 BE | raw − 32768 (Einheit offen) | nur im Eingriff ≠ 0xFFFE, folgt dem Moment (r=0,77) | _maybe | C6 |
| `TCS_RequestActive_maybe` | 0x211 Bit 53 | Flag | deckungsgleich mit der Anforderung | _maybe | C6 |
| `HighDecel_maybe` | 0x211 Bit 43 | Flag | ab ≈0,55–0,6 g Verzögerung, 13 von 775 Bremsungen, unabhängig von v und Druck | _maybe | C7 |
| `DSC_Indicator_maybe` | 0x415 2\|2 | 3 = blinkt, 1 = Lampentest | 6 = Eingriff, 114 = Lampentest 2,5 s, 98 = Abstellen | _maybe | C1, C6 |
| `ReverseGear` | 0x445 Bit 7 | Flag | Gierrate/Lenkwinkel-Vorzeichen umgekehrt in 99,7 % (n=1195) | **bestätigt** | C2 |
| `ColdEngine_Indicator` | 0x4F7 5\|2 | 3 = kalt | schaltet in 21/21 Fällen exakt bei 55,0 °C Kühlwasser ab | bestätigt (Schwelle), Leuchte offen | C1 |
| `BrakeSwitch_PCM` (+`_Inv`, `_2`) | 0x167 Bit 19/18/16 | Flag | P=1,000 bei > 2 bar, 0,018 sonst | **bestätigt** | – |
| `BrakeLamp` | 0x43E Bit 53 | Flag | P=1,000 bei > 2 bar | **bestätigt** | – |
| `AnyDoorOpen` | 0x43E Bit 30 | Flag | P=1,000 bei Tür offen, 0,0002 sonst | **bestätigt** | – |
| `DCDC_Voltage` | 0x08A 19\|10 | 0,02 V/LSB | r=0,9986 gegen PID 0x42, Bitmuster passt | **bestätigt** | C1 |
| `DCDC_Voltage_2_maybe` | 0x08A 25\|10 | 0,02 V/LSB | Kopie von `DCDC_Voltage` | _maybe | – |
| `iELOOP_CapVoltage_maybe` (+`_2`) | 0x08A 13\|10, 53\|10 | 0,04 V/LSB (angenommen) | 13,8–24,6 V; steigt in Rekuperation, fällt sonst | _maybe (Skala) | C1 |
| `DCDC_Active_maybe` | 0x08A Bit 6 | Flag | = Motor läuft | _maybe | – |
| `BCM_SupplyVoltage` | 0x43F 19\|10 | 0,016 V/LSB | r=0,998, konstant 0,68 V unter PCM/DCDC | **bestätigt** | C1 |
| `BattSensor_Voltage_maybe` | 0x45A 45\|13 | 1/512 V/LSB | r=0,966, bricht beim Anlassen auf 9,65 V ein | _maybe | C1 |
| `BattSensor_Temp_maybe` | 0x45A Byte3 | raw − 40 °C | Kaltstarts r=0,966; in der Fahrt fast konstant | _maybe | C1 |
| `FuelConsumption_Counter` | 0x420 Byte2 (8 Bit) | ≈7,0 Schritte/g (≈0,14 g bzw. 0,19 ml je Schritt) | Rate r=0,99–0,999 gegen Luftmasse/Lambda; Byte3 ist konstant 51/52 und gehört nicht dazu | **bestätigt** (Einheit ±10 %) | C3 |
| `Travel_distance_related` | 0x420 Byte1 | 0,209 m/Schritt | r=1,000 gegen integrierte Geschwindigkeit | **bestätigt** | – |
| `AmbientTemp` (korrigiert) | 0x420 Byte7 | ≈0,35·raw − 6,3 °C | alte Formel war konstant 25,8 °C; Kaltstarts r=0,987 | Formel _maybe | C1 |
| `RCM_Temperature_maybe` | 0x075 Byte6 | raw − 103 °C | Kaltstarts r=0,980, steigt 10–24 K je Fahrt | _maybe | C1 |
| `EngineRunning` (+`_2`, `_3`) | 0x0FD Bit 0/6, 0x09F Bit 16 | Flag | P=1,000 / 0,03 über 97 i-stop-Stopps | **bestätigt** | – |
| `iStop_StopRequest` (+`_PCM`) | 0x130 Bit 30, 0x167 Bit 22 | Flag | 0,85 s vor jedem der 97 Stopps | **bestätigt** | – |
| `iStop_EngineStopped` (+`_2`), `iStop_Stopping_A/B` | 0x130 Bit 19/0/10, 0x050 Bit 20 | Flag | nur im automatischen Stopp | **bestätigt** | – |
| `iStop_Ready_maybe` | 0x130 Bit 20 | Flag | vor jedem Stopp gesetzt, in Warmlaufphase 0 | _maybe | C5 |
| `TSR_SpeedLimit` | 0x35F 4\|7 | km/h | Werte 30…120, passend zur gefahrenen v; 0 = kein Limit | **bestätigt** | – |
| `LaneCurvature_maybe` | 0x242 Byte3 | 0,3·(raw−127) 1/km | r=0,94–0,97 gegen Gierrate/v bei > 60 km/h | _maybe (Skala) | – |
| `LaneOffset_Line1/2_maybe` | 0x242 3\|10, 9\|10 | raw − 686, ≈1 cm/LSB | Sägezahn bei 233 Spurwechseln, Sprung 335 ≈ Spurbreite | _maybe (Skala) | C10 |
| `CAM581_Curvature2_raw_maybe`, `CAM581_LineDiff_raw_maybe` | 0x245 Byte4, Byte0 | roh | r=0,88 gegen Spurkrümmung bzw. Versatzdifferenz | _maybe | C10 |
| `RoadIncline_maybe` | 0x49C 63\|7 | (raw − 32) % | r=0,82–0,96 gegen abgeleitete Steigung in 16/19 Logs, ~1 % je Stufe, + = bergauf | _maybe (Skala) | C13 |
| `Service_DistanceRemaining_maybe` | 0x3D1 47\|14 | km | zählt 0,95/km herunter, über alle Logs lückenlos stetig (4008 → 3437 km) | _maybe (Deutung) | C11 |
| `BatteryVoltage_OBD` (PID 0x42) | Diagnose | /1000 V | Standard-PID, pollt `tpms_poller.py` seit 16.09. | bestätigt | – |

**Korrigierte Fehldeutungen:** `AmbientTemp` (war konstant 25,8 °C), `DSC_Status` (ist eine
Kontrollleuchte, die nur beim Lampentest leuchtet; 0 = DSC aktiv), `Reverse_Flag_maybe` (0x9F,
in keinem Log je gesetzt, tot), `MT_Gear_Actual` zeigt beim Rückwärtsfahren 0 statt 7,
`Mileage` (0x3D1 39|22, lieferte Müllwerte) ist in Wahrheit die 14-Bit-Service-Restdistanz plus ein fremdes Byte - ersetzt,
`EngineState_raw_maybe` (0x20A) auf 6 Bit gekürzt (die unteren 2 Bit gehören zu `PCM_20A_Ramp_raw_maybe`).

## B. Offene Werte mit beobachtetem Verhalten

Nur Felder, die in vielen Logs variieren. Zähler (Schrittweite konstant) und Prüfsummen
(Bits kippen zufällig) sind nicht einzeln aufgeführt, `results/can_open_fields.csv` listet sie.

### Motorsteuerung (PCM)
| Feld | Verhalten | Hypothese | Test |
|---|---|---|---|
| 0x200 Byte4-5 (raw−32768) | Last-Domäne, r 0,72–0,94 gegen Pedal/Moment; gleiche Kodierung wie `TCS_TorqueRequest_maybe`, mit ihr r=0,84 | Fahrerwunsch- bzw. indiziertes Moment | C6 (Nm-Bezug über die TCS-Anforderung), C3 |
| `PCM_TorqueLoss_raw_maybe` 0x200 Byte2-3 (raw−32768) | meist −40…+10, im Mittel −19…−26; in der Rekuperation −30…−36, bei kaltem Motor bis −49; keine Korrelation mit Pedal/Moment | Verlust-/Nebenaggregate-Lastmoment (gleiche Einheit wie Byte4-5) | C1: im Leerlauf Klima/Heckscheibenheizung schalten |
| `PCM_20A_Ramp_raw_maybe` 0x20A 1\|8 | steigt stufenweise bei Volllast und im Schub, fällt bei Teillast; bisheriges `EngineState_raw_maybe` überdeckte zwei seiner Bits (jetzt 7\|6) | Adaption/Integrator | C9 |
| 0x20A Byte2-3 | analog, keine Korrelation mit den ~80 Ankern | unbekannt | C9 (Zusatz-PIDs) |
| 0x4DA Byte0/1/2/4 | Start bei 50, sinken in Schubphasen, steigen unter Last langsam | Katalysator-Modell (Temperatur/O₂-Speicher) | C9: PID 0x3C (Kat-Temperatur) mitloggen |
| 0x4DA Byte3 Bit 6 | 1,4 % der Zeit, im Schub, nicht deckungsgleich mit FuelCut | Kat-Spül-/Diagnosezustand | C9 |
| 0x42B B4-6 ≈ 0x4FA B0-2 | Duplikat, Zustandsbytes, wechseln bei Anfahren/Halt | Lastzuordnung/Leerlaufregelung | C1 (Verbraucher schalten) |
| 0x4FA Byte1 | 34–40, r=−0,71 gegen Ansaugluft | Temperatur-Kompensation | C9 |
| 0x165 Byte7, 0x42B Byte0 | ändern selten, schwach mit Gang/Schub | unbekannt | – |
| 0x202 Bit 63, 0x45A Bit 15 | Zustandsbits, an Motorbetrieb gekoppelt | unbekannt | – |
| 0x09D Bits 17/20/22 | kurze Episoden (0,07–0,1 s) bei 87–114 km/h im 6. Gang, Pedal zu, in 8–25 Logs | Tempomat-/Schubzustand? | C8 |
| 0x0FD Bit 4 | 41 Episoden in 22 Logs, ~2 s, oft Leerlauf-nah | unbekannt | – |

### Spannungswandler / i-ELOOP / Batteriesensor
| Feld | Verhalten | Hypothese | Test |
|---|---|---|---|
| `DCDC_Field45_raw_maybe` 0x08A 45\|8 | 0 bei Motor aus, bis 255 im Betrieb, in Rekuperation niedriger | Wandlerstrom oder Tastverhältnis | C1 Stromzange + Verbraucher |
| `DCDC_State_maybe` Wert 11 | selten, mit Byte4 = 248 | Start-/Sonderzustand | C1 |
| `BattSensor_Current_raw_maybe` 0x45A 3\|12 | ~3665 Motor aus, ~3730 lädt, ~2900 beim Anlassen | Batteriestrom, Nullpunkt ~3690 | C1 Stromzange |
| `BattSensor_Byte4_maybe` | 55–76, Kaltstart r=0,98 gegen IAT, steigt je Fahrt | zweite Temperatur oder SOC | C1, Ladegerät |
| `BattSensor_Byte7_maybe` | 134–158, sinkt langsam je Fahrt | SOC/SOH | Ladegerät |
| 0x4DF Byte0, Byte5 (DCDC, ~1,6 Hz) | beide steigen je Fahrt (z. B. 64→70, 25→37), beim Kaltstart eng an der Ansauglufttemperatur (r=0,980 bzw. 0,994), aber Steigung 1,44 bzw. 2,24 K/Schritt - keine Standard-Temperaturkodierung | Wandler- bzw. Kondensatortemperatur oder temperaturabhängige Grenzwerte | C1: Infrarot-Thermometer an Kondensator/Wandler nach Standzeit und nach Fahrt |
| 0x4DF Byte2-4, Byte6 | wenige Werte, wechseln zyklisch (Byte6 6/134) | Multiplex-Seiten | – |

### Frontkamera (FSC)
| Feld | Verhalten | Hypothese | Test |
|---|---|---|---|
| 0x244 Bytes 0-2 | seltene Episoden bei Konstantfahrt ~83 km/h | Abstand/Objekt vorn (opendbc `CAM_DISTANCE`) | C10: hinter einem Fahrzeug mit wechselndem Abstand |
| 0x246 (24 Bit + 12 Bit) | viel Variation, nur schwach mit v | Objekt- oder Linienmodell | C10 |
| 0x35F übrige Bits (`FORWARD_COLLISION`, `STOP_SIGN` laut opendbc) | selten | Warnungen | C10 |

### Kombiinstrument / Infotainment (IC, CMU)
| Feld | Verhalten | Hypothese | Test |
|---|---|---|---|
| 0x4D4 Bytes 0-5 | ab Zündung 0, erscheinen nach Minuten, Byte0 steigt auf der Autobahn, Byte1 wächst bei Stau | Fahrbewertung (i-DM) bzw. Eco-Monitor | C4: Anzeigen fotografieren |
| 0x4D9 Byte7 | meist 0, in 0,8 % der Zeit 1-21 (2544 Einsätze); Einsatz bei doppeltem Ruck (0,21 gegen 0,11 g/s) und höherer Längsbeschleunigung als zufällige Zeitpunkte; in allen ABS-, TCS- und Starkbrems-Ereignissen gesetzt | Fahrstil-Bewertung von G-Wechseln (i-DM-artig) | C4: Anzeige beobachten, bewusst ruckartig/sanft fahren |
| 0x3D2 | Multiplex (Byte0 = Seite 80–82/104–107), 16-Bit-Wertepaare | Verbrauchshistorie oder Navigation | C4 |
| 0x3D0/0x3D1, 0x4F2 Byte2 | seltene Zustandswechsel | HUD/CMU-Einstellungen | – |
| 0x21D | 50 Hz, Bytes ändern sich selten, Episoden im Stand bei Kupplung | Rangier-/Einparkzustand? | C2 |
| 0x4FE | 10 Hz Tabellenübertragung: Byte0 (0-4) + Byte1 = 10-Bit-Adresse, +1 je 0,3 s, durchläuft ~1280 Einträge; Byte2-7 sechs Werte je Adresse (oft identisch, z. B. 50/90/100/127/190). opendbc `MILAGE_MAYBE` passt nicht | Verlaufsdaten (Verbrauchs-/Eco-Historie) von IC/CMU | C4 (Eco-Anzeige mit Zeitstempel fotografieren) |
| 0x45B (Multiplex, Byte0 = Seite 1-5) | nur Seite 1 Byte2 (0-255, z. B. 61 → 243 über eine Fahrt) und Seite 2 Byte3/4 (147-255 bzw. 161-255) variieren; Korrelationen wechseln das Vorzeichen zwischen Logs | Bordcomputer-/Wartungswerte? | C3, C11 |
| 0x09B Bit 2 | ~10-s-Episoden alle 50–100 s (11–29 % der Zeit), v. a. im Stand; im Leerlauf sinkt dabei `BattSensor_Current_raw_maybe` in 6/6 Logs um ~100 Schritte (mehr Entladung) und die Spannung leicht | großer el. Verbraucher, vermutlich Kühlerlüfter | C1: Lüfter hören/sehen, Zeit notieren |

### Karosserie / Insassen
| Feld | Verhalten | Hypothese | Test |
|---|---|---|---|
| 0x340 Bits 26/28/29/31, 0x344 Bits 6/7, 0x09F Bit 40 | je Log konstant, teilen die Logs in zwei Gruppen zu 14 (Pendelfahrten morgens vs. Mittags-/Nachmittags-/Wochenendfahrten); wechseln innerhalb eines Logs nur im Stand bei offener Fahrertür bzw. kurz nach Zündung AUS oder Verdeckbewegung. opendbc nennt 0x340 `SEATBELT` | Beifahrer-Belegung/-Gurt bzw. Airbag-Abschaltanzeige | C12 |

### Fahrwerk / sonstige
| Feld | Verhalten | Hypothese | Test |
|---|---|---|---|
| `SteeringTorque_related` (0x240 Byte1), `SteeringTorque_maybe` | Betrag 0–59 bzw. Moment ohne Einheit | Servounterstützung | – |
| 0x086 22\|12, 41\|12 | ändern jeden Frame, fast Gleichverteilung | Prüfsummen/Zähler | – |
| 0x082 Byte4 | ändert in 87 % der Frames | Prüfsumme? | – |

## C. Fahrzeugtest-Programm (nächster Termin, nach Nutzen sortiert)

Jeder Test braucht nur das laufende CAN-Logging und eine Zeitnotiz (Handy-Stoppuhr oder Foto
mit Uhrzeit). Nichts davon verlangt Eingriffe in Steuergeräte.

1. **Stand, Zündung EIN, dann Motor an (10 min):** Lampentest beobachten (welche Leuchten wie
   lange). Außentemperatur-Anzeige ablesen. Multimeter an der Batterie (Motor aus / an) und,
   falls vorhanden, Stromzange am Batteriekabel. Nacheinander mit 20 s Abstand schalten und die
   Uhrzeit notieren: Abblendlicht, Heckscheibenheizung, Gebläse max., Klima an/aus, Sitzheizung.
   Klärt Spannungen/Strom (0x08A, 0x43F, 0x45A), Außentemperatur-Skala, i-ELOOP-Wandlerfeld,
   Lampentest-Bits, 0x42B/0x4FA, 0x09B.
2. **Rückwärtsgang im Stand** mit voll getretener Kupplung ein-/auslegen (3×). Bestätigt
   `ReverseGear`, klärt `MT_Gear_Actual=7`, 0x21D.
3. **Bordcomputer:** Trip und Durchschnittsverbrauch zurücksetzen, 20-30 km fahren, Anzeige
   (Verbrauch, Reichweite, Ø-Geschwindigkeit) fotografieren. Legt die Einheit von
   `FuelConsumption_Counter` fest und prüft 0x4DF.
4. **Eco-/i-DM-Anzeige** in MZD Connect während der Fahrt 2-3× fotografieren (mit Uhrzeit).
   Für 0x4D4 und 0x3D2.
5. **i-stop-Leuchte** beim Halten beobachten (grün/aus) gegen `iStop_Ready_maybe`.
6. **Traktionseingriff:** auf abgesperrter, rutschiger Fläche (nasser Parkplatz, Kiesweg) im
   1./2. Gang Gas geben, bis die DSC-Leuchte blinkt; einmal mit gedrückter DSC-OFF-Taste
   wiederholen. Kalibriert `TCS_TorqueRequest_maybe`, bestätigt `TCS_Active_maybe`,
   `DSC_Indicator_maybe`, den DSC-OFF-Leuchtenbit (heutiges `DSC_Status`).
7. **Vollbremsung** aus 60-80 km/h auf freier Strecke (> 0,6 g), Warnblinker beobachten.
   Bestätigt `HighDecel_maybe` und die Notbremssignal-Logik.
8. **Tempomat** einmal setzen, +/- tippen, per Bremse abbrechen. Für 0x21F (`CRZ_EVENTS`),
   `CC_SetSpeed`, 0x09D, 0x0FD Bit 4.
9. **Zusätzliches Polling** (Code in `scripts/tpms_poller.py` und die Offline-Dekoder sind seit
   26.09. fertig, **nur noch auf den Pi kopieren und den Poller neu starten**, siehe
   `status/pi-runtime-state.md`):
   PID 0x3C (Katalysatortemperatur B1S1) für 0x4DA; PID 0x34 (gemessenes Lambda, schon als
   unterstützt bekannt); PID 0x2F (Tankfüllstand) als zweite FLI-Referenz. Alle drei stehen in
   `data/can/probe-20260916-081915.csv` als unterstützt.
10. **Kamera:** auf gerader Straße mit bekannter Spurbreite bewusst an die linke, dann die
    rechte Linie fahren; hinter einem vorausfahrenden Auto Abstand ändern. Für
    `LaneOffset_*`, 0x244, 0x246.

11. **Wartungsanzeige** im MZD-Menü (Einstellungen > Fahrzeug > Wartung) ablesen: Restdistanz
    gegen `Service_DistanceRemaining_maybe` (0x3D1).

12. **Beifahrer/Gurte:** im Stand nacheinander Fahrergurt, Beifahrersitz belegen (Tasche ab
    ~ 10 kg genügt oft nicht, besser Person), Beifahrergurt stecken - jeweils Uhrzeit notieren.
    Für 0x340/0x344/0x09F Bit 40.

13. **Steigung:** an einer ausgeschilderten Steigung (z. B. 8 %) anhalten und langsam anfahren -
    `RoadIncline_maybe` (0x49C) muss den Schildwert zeigen.

Aus dem vorherigen Stand weiterhin offen (siehe `status/can-bus.md`): Auslöser des
ECU-Soft-Limiters (Vollgaszüge Gang 2-4), TPMS-Vorderachs-Zuordnung.
