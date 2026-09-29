---
name: mx5-can-reverse-engineering
description: >
  Unbekannte CAN-Signale im MX-5-ND-Projekt offline aus aufgezeichneten Logs
  reverse engineeren und in DBC und Datalake bringen. Gezielt: ID/Byte/Bit für eine
  Größe finden (Drehzahl, Bremsdruck, Lenkwinkel, Gang, ABS/DSC-Eingriff …),
  Startbit/Länge/Endianness/Signedness/Scale/Offset bestimmen, gegen OBD, bekannte
  DBC-Signale oder abgeleitete Anker (a_y = v·Gierrate, Gang aus Drehzahl/v, Schlupf)
  kalibrieren. Discovery: ganze Logs nach unbekannten Signalen durchsuchen
  (Feldtypen, Counter/Checksummen, Korrelationsnetz, Kausalketten, natürliche
  Ereignisse, Lampentest, Community-DBCs). Trigger: "reverse engineer X", "finde das
  CAN-Byte für X", "welche ID trägt Y", "was steckt noch im Log", "finde neue
  Signale", "ist das ein Counter", "bring das in den Datalake", oder ein unbelegtes
  Byte im DBC fällt auf. Im Zweifel nutzen, sobald es um unbekannte CAN-Bits geht.
  Nicht für bereits dekodierte Signale (docs/status/can-bus.md).
---

# MX-5 CAN Reverse Engineering

Eigene Version der Methodik aus
[CSS-Electronics/can-bus-reverse-engineering-skills](https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills),
auf unseren Stack reduziert (keine CANsub, keine Vision/OCR) und um Methoden aus der
Literatur erweitert (READ, LibreCAN, CAN-D, ACTT, Quellen am Ende). Führende
Referenzgrößen sind Daten, die bereits in den Logs stecken: OBD-Traffic im CAN-Log,
bereits validierte DBC-Signale, **daraus abgeleitete Anker** sowie GPS und Handy-IMU
aus den begleitenden Handy-Logs.

**Vorher lesen:** `docs/status/can-bus.md` (was ist schon dekodiert) und
`docs/status/can-open-fields.md` (Katalog offener Felder, zugleich unser
Kandidaten-Register). Viele Fragen sind dort schon beantwortet oder als offen mit
beobachtetem Verhalten vermerkt.

## Geltungsbereich: nur offline

Dieser Skill analysiert **ausschließlich vorhandene Logs**: candump-Logs vom Pi
(`data/can/candump-*.log`), teils mit OBD-Traffic, dazu die Handy-Logs (`.dlg`
mit GPS und IMU) und GPX-Tracks (`data/can/*.gpx`), die der Datalake seit 27.09.
an die CAN-Logs hängt. Er plant **keine** Testfahrten, Stimulus-Aufnahmen oder
Live-Tools im Auto und schlägt so etwas auch nicht vor. Reicht der Bestand für ein
Signal nicht, lautet das Ergebnis „mit vorhandenen Logs nicht bestimmbar". Das Feld
kommt dann mit beobachtetem Verhalten und der Art Ereignis, die im Bestand fehlt, in
Teil B von `docs/status/can-open-fields.md`. Den Fahrzeugtest-Katalog (Teil C) pflegt
der Nutzer.

## Zwei Modi

- **Gezielt** (Signal X ist gesucht): Schritte 1–11 unten.
- **Discovery** (was steckt noch Unbekanntes im Log?), 17 Techniken D1–D17 in fünf
  Gruppen: Struktur, Zusammenhänge, Zeitkontext, externe Offline-Quellen, Synthese.
  → **`references/discovery-techniken.md` lesen.** Discovery erzeugt nur Hypothesen.
  Jede Hypothese läuft danach ab Schritt 2 durch den gezielten Modus, bevor sie in
  DBC oder Datalake landet.
- Beide enden in Schritt 9 (DBC) und Schritt 11 (Datalake/Doku).

## Werkzeuge

Aufruf immer mit `.venv/bin/python scripts/<name>.py` (System-Python hat kein pandas).
Fast alle Skripte haben `--self-test`. Ausgaben landen in `results/` (nicht im Repo).

- **[vorhanden]** Skript existiert und ist getestet → direkt benutzen.
- **[geplant]** gibt es nicht → NICHT aufrufen. Schritt ad hoc in einem kurzen
  Python-Snippet umsetzen (Bausteine aus `can_offline_lab.py`) und dem Nutzer
  vorschlagen, daraus ein Skript zu bauen.

| Zweck | Skript | Status |
|---|---|---|
| Log lesen (klein, cantools-genau) | `can_log_parser.py` (`parse_candump`, `load_db`) | [vorhanden] |
| Log lesen (schnell, mit `.npz`-Cache in `data/can/cache/`): `frames`, `field`, `sig`, `obd`, `grid`, `logs()` | `can_offline_lab.py` | [vorhanden] |
| OBD/UDS aus dem CAN-Log (alle 4 UDS-Header, Multiframe) | `obd_from_can.py` (`decode_obd_traffic`, `extract_did_series`) | [vorhanden] |
| Rest-Budget: unbelegte variierende Felder über alle Logs, Counter/CRC-Verdacht | `can_open_fields.py` | [vorhanden] |
| Referenzfreie Feldgrenzen (READ), Klassen CONST/COUNTER/CRC/PHYSICAL/FLAG, Signedness-Verdacht | `can_field_segmentation.py` | [vorhanden] |
| Byte-Sweep gegen Messanker + DIDs | `can_byte_search.py <log>` | [vorhanden] |
| Sweep gegen ~30 abgeleitete Anker (Schlupf, Gierrate aus Rädern, Steigung, Kraftstoff, …), über alle Logs aggregiert | `can_anchor_sweep.py` (`anchors()`) | [vorhanden] |
| Ein Rohfeld gegen alle Anker in mehreren Logs | `can_field_inspect.py 0xID:Start:Len:BE\|LE[:s]` | [vorhanden] |
| Bitgenaue Feinsuche für EINE ID | `can_bitsearch.py` | [vorhanden] |
| Kalibrierung, Sentinels, Plausibilität | `can_re_toolkit.py` | [vorhanden] |
| Unbekannt↔unbekannt/bekannt clustern (Korrelationsnetz) | `can_field_families.py` | [vorhanden] |
| Natürliche Ereignisse gegen gleichartige Kontrolle (Rückwärts, Motorstart/-stopp, Kupplung, Bremse, Blinker, Tür, Kaltlauf …) | `can_natural_events.py --event NAME` | [vorhanden] |
| Ereignisfenster vs. Baseline je Bit (ABS/DSC/Bremse, eigene Fenster) | `can_event_bit_diff.py --event/--window/--baseline/--hz/--pad` | [vorhanden] |
| Seltene Bits vom Bit aus katalogisieren, mit Fahrkontext | `can_rare_bits.py` | [vorhanden] |
| Natives Gegenstück zu einem OBD-Wert (Partialkorrelation, Übertragungstest) | `can_find_native_counterpart.py --ref X --control Y` | [vorhanden] |
| Handy-IMU als Anker (Paare CAN↔dlg, Zeitabgleich) | `can_phone_imu_sweep.py` | [vorhanden] |
| Fremd-DBC gegen unsere Logs (opendbc `mazda_2017`) | `can_opendbc_crosscheck.py` | [vorhanden] |
| Zähler/Uhr-Kandidaten mit variabler Rate | `can_find_clock_candidate.py` | [vorhanden] |
| `_maybe`/`_related`-Signale gegen neue Anker nachprüfen | `can_retest_maybe_signals.py` | [vorhanden] |
| Skala eines DBC-Signals gegen OBD nachrechnen (an Referenz-Zeitstempeln, Lag-Suche, alt/neu-Vergleich) | `can_obd_scale_recheck.py --signal NAME` (Spec in `SPECS` ergänzen) | [vorhanden] |
| Zirkularverschiebungs-Nulltest | `can_null_test.py` | [geplant] |
| Log-Katalog (welche DIDs/Ereignisse/Wertebereiche je Log) | `log_coverage.py` | [geplant] |
| Clock-Skew-Sendergruppen (D9) | `can_timing.py` | [geplant] |
| Steckbrief/Evidenz-Score (D16) | `can_profile.py` | [geplant] |

## Entscheidungsbaum: welche Methode für welchen Signaltyp?

1. **Erst Struktur (Schritt 1)**, sonst weiß man nicht, wonach man sucht.
2. Dann nach vermutetem Signaltyp:
   - **Kontinuierlich** (Drehzahl, Druck, Winkel, Beschleunigung) → Korrelation
     gegen OBD / DBC-Signal / abgeleiteten Anker (Schritte 3–5).
   - **Flag / Zustand** (ABS aktiv, Bremslicht, Kupplung, DSC-Leuchte) →
     Ereignis-Matching (Schritt 6), NICHT Pearson.
   - **Enum** (Gang, Fahrmodus) → Kontingenztabelle gegen einen diskreten Anker
     (z.B. Gang-Cluster aus Drehzahl/v), keine Regression.
   - **Integral/Zähler mit Bedeutung** (Strecke, Kraftstoff) → Ableitung des
     Kandidaten gegen den Anker testen (`d/dt Kandidat ~ v`), Wrap vorher entfalten.
     Vorbild: `FuelConsumption_Counter`/`Travel_distance_related` auf 0x420.
   - **Langsame Monotonie** (Temperaturen) → `can_find_native_counterpart.py` oder
     Kaltstart-Methode (Werte beim Start gegen Ansaugluft/Wetter). Die Doppelschwelle
     von `can_byte_search.py` (roh UND detrended) verwirft solche Felder systematisch.
   - **Rolling Counter / Checksumme** → als solche identifizieren und aus allen
     weiteren Suchen ausschließen (Schritt 1).
   - **Gar kein Anker ableitbar** → natürliche Ereignisse im Bestand suchen
     (Schritt 2b), sonst ehrlich melden. Nichts erzwingen.

## Ablauf

### 1. Struktur: Felder, Counter, Checksummen

- `can_open_fields.py`: welche Bits variieren ohne DBC-Signal (Rest-Budget,
  Stand und Zahlen in `can-open-fields.md`).
- `can_field_segmentation.py --log … --id 0x…`: Feldgrenzen nach READ
  (Größenordnung der Bitkipprate), Klassen CONST/COUNTER/CRC/PHYSICAL/FLAG und
  Signedness-Verdacht. Das Skript prüft sich an bekannten Botschaften (0x202,
  0x215, 0x82) selbst gegen die DBC.
- Ergänzend ad hoc, wenn READ unsicher ist (CAN-D): Grenze vermuten, wenn
  `P(F_i+1|F_i) < 0.01` oder `P(F_i+2|F_i+1) − P(F_i+1|F_i) > 0.5`. Beide
  Byte-Reihenfolgen prüfen.
- **Rolling Counter** (+1 mod 2^n) und **Checksumme** (Kipprate ≈ 0,5 auf allen Bits,
  keine Korrelation, oft letztes Byte) als `Counter`/`Checksum` ins DBC, damit sie in
  keinem späteren Scan mehr als „unbelegt" auftauchen. Algorithmus bei Bedarf mit
  [CRC RevEng](https://reveng.sourceforge.io/) (`reveng -w 8 -s <frames>`), sonst
  Summe/XOR über Bytes 0–6 (+ Counter, + ggf. CAN-ID) durchprobieren.
- **Multiplex** prüfen, wenn eine ID „verrauscht" wirkt (Discovery D7; 0x45B und
  0x3D2 sind schon als Multiplex erkannt).
- **Überlappung mit bestehenden DBC-Signalen**: cantools meldet sie beim Laden.
  Ein Kandidat, der mit einem bekannten Signal überlappt, ist oft dessen
  Fortsetzung (so bei 0x086: 15-Bit-Erweiterung statt eigener Größe).

### 2. Referenz bestimmen

Bevorzugt in dieser Reihenfolge:

1. **OBD aus demselben Log** (`obd_from_can.py`). Welche DID in welchem Log steht,
   ist sehr ungleich verteilt: Die EPS-DIDs (`STEER_SPD_EPS`/`STEER_ANGL_EPS`)
   stecken nur in 4 Logs (`12_211833`, `14_081105`, `14_163711`, `14_173057`),
   `OilTemp` wird erst seit 15.09. gepollt, und seit der Drosselung am 26.09. ist
   die Abfragerate niedriger. Bis `log_coverage.py` existiert, vorher per grep
   prüfen, z.B. für DID 0x3301:
   `grep -lE "#[0-9A-F]{2,4}623301" data/can/candump-*.log`. Bekannte Mode-22-DIDs:
   `KNOWN_DIDS` in `can_byte_search.py`. Rohwert-Formeln (29.09. exakt gegen die `.dlg`):
   `BFP_PRE_MZ` kPa = raw, `CPP_PER_MZ` % = raw·100/65535, `FLI` % = raw·100/256.
   Mode-1-Formeln: `OBD_CHANNELS` in `can_log_parser.py`.
2. **Bereits validiertes DBC-Signal** (ohne `_maybe`/`_related`, z.B. `WheelSpeed_1-4`,
   `YawRate_Raw`, `Steering_Wheel_Absolute_Angle`, `ABS_Active`), per
   `can_offline_lab.sig()` oder `msg.decode()`.
3. **Abgeleiteter Anker**: `can_anchor_sweep.anchors()` liefert ~30 fertige Anker
   (`slip_rear_front`, `yaw_wheels`, `lat_kin`, `slope`, `gear_ratio`, `fuel_rate`,
   `dist_cum`, …). Katalog, Formeln und Gültigkeitsbereiche:
   **`references/abgeleitete-anker.md` lesen, bevor ein neuer Anker gebaut wird.**
   Besonders ergiebig: eine physikalische Invariante aus einem **Nachbarsignal
   derselben Botschaft** (Ableitung, Summe). So wurde `SteeringRate_Abs_maybe`
   gegen `d(Winkel)/dt` aus 0x082 in allen 28 Logs geprüft statt nur in den 4
   OBD-Logs. Das findet Felder gut, ist aber keine unabhängige Bestätigung.
4. **GPS und Handy-IMU** aus der zugehörigen `.dlg`/GPX (`can_phone_imu_sweep.py`
   baut die Paare und richtet sie zeitlich aus).
5. **Natürliche Ereignisse** aus vorhandenen Logs (Schritt 2b).
6. Nichts davon → ehrlich melden.

**Pflichtangaben zu jedem Anker** (im Ergebnis mitschreiben): Herkunft (Signale,
Formel), Gültigkeitsmaske und ob die Parameter gut genug bekannt sind, um auch die
**Skala** zu tragen oder nur die **Form**. Beispiel für eine falsche Skala:
`VehicleSpeed` liest 2,9 % über GPS. Die erste Wegzähler-Skala (0,209 m) kam
daher, richtig sind 0,1992 m gegen die ODO-Inkremente. Skalenreferenz für
Geschwindigkeit/Strecke daher ODO oder GPS, nicht `VehicleSpeed`.

### 2b. Logs auswählen und natürliche Ereignisse nutzen

Statt Aufnahmen zu planen, wird aus dem Bestand das Passende herausgesucht.

- **Log-Auswahl**: `can_offline_lab.logs()` liefert die reichhaltigen Logs.
  Wertebereiche/Ereignisse je Log ad hoc prüfen (`log_coverage.py` [geplant]).
  Referenzlog für Rest-Budget-Zahlen: `candump-2026-09-18_093544`.
- **Wertebereich ausgereizt?** Ist das oberste Bit eines Feldes im Log nie gesetzt,
  **prüft `can_bitsearch.py` das Feld gar nicht erst**: Es erzeugt nur Kandidaten,
  deren letztes Bit sich ändert. Beispiel 0x082 in `14_173057`: Maximum 1304 von
  4095, Bit 55 nie gesetzt, das echte 12-Bit-Feld `44|12@1+` fehlt in der Liste,
  auch mit `--min-len 12`. Deshalb Logs mit großem Wertebereich wählen, mehrere
  Logs zusammen auswerten oder die längere Variante per `can_field_inspect.py`
  bzw. ad hoc gegenprüfen. Ein im ganzen Bestand nie gesetztes MSB im
  DBC-Kommentar als „Länge = untere Schranke" vermerken.
- **Natürliche Ereignisse**: `can_natural_events.py` vergleicht je Bit die Setzquote
  im Ereignis gegen eine **gleichartige** Kontrolle (Rückwärts vs. Vorwärts-Rangieren,
  Motorstart vs. Zündung an, Kupplung, Neutral, Bremslicht, Blinker, Licht, Tür,
  Kaltlauf, Hochdrehzahl, Stillstand).
- **Seltene Ereignisse** (ABS/TCS/DSC): Zwei Lehren aus dem ABS-Fund (0x211 Bit 42):
  1. Gibt es im Bestand kein echtes Ereignis, findet kein Verfahren etwas.
  2. Verglichen wird gegen den Rest **derselben** Bremsung bzw. Kurve, nicht gegen
     das ganze Log. Fenster auf die echte Ereignisdauer eingrenzen und das Raster
     erhöhen (`can_event_bit_diff.py --window/--baseline/--hz/--pad`).

  Umgekehrt vom Bit aus: `can_rare_bits.py` findet kurze Flags, die in einem langen
  Fenster untergehen (so gefunden: TCS 0x211 Bit 40). Weniger als ~10 Ereignisse
  heißt höchstens `_maybe`.
- **Wissen des Nutzers über vergangene Ereignisse** ist ein zulässiger Offline-Anker
  (z.B. „bei Log X Tempomat benutzt", Ort/Zeit eines Rutschers). Nur verwenden, wenn
  der Nutzer es nennt.
- **Zeitstempel sind nicht immer wahr** (Pi ohne RTC bis 28.09.). Für den Abgleich
  mit externen Quellen (GPS, dlg, Wetter, Nutzerangaben mit Uhrzeit) die Startzeit
  über `build_datalake.log_start_epoch()` holen, das die Versätze aus
  `data/can_clock_offsets.json` berücksichtigt. Nie aus dem Dateinamen allein.

### 3. Zeitbasis angleichen und Lag prüfen

- **Resampling-Richtung**: den schnellen CAN-Kandidaten auf die Zeitstempel der
  langsamen Referenz interpolieren, nicht umgekehrt (ACTT/CAN-D). Für Flags/Enums
  „nearest"/„previous" statt linearer Interpolation.
- **Seit 29.09. in den Werkzeugen umgesetzt**: `can_byte_search._resample` (auch von
  `can_bitsearch.py`, `can_field_segmentation.py`, `can_opendbc_crosscheck.py`,
  `can_retest_maybe_signals.py` genutzt) behält nur Rasterpunkte mit echter
  Stützstelle in ±0,05 s. Eine dünne Referenz zählt damit nur an ihren eigenen
  Zeitpunkten. `can_find_native_counterpart.py` vergleicht direkt an den
  Referenz-Zeitstempeln. Vorher wurde die Referenz linear interpoliert (0x082 gegen
  `STEER_SPD_EPS`: R² 0,990 → 0,886, Steigung −10 %). **Ergebnisse aus Läufen vor dem
  29.09. sind gegen OBD-Referenzen entsprechend verzerrt.** Nachgerechnete Skalen:
  `scripts/can_obd_scale_recheck.py`, Logbuch 29.09.
- `can_anchor_sweep.py` hält OBD-Anker dagegen weiter per Sample-and-hold auf dem
  10-Hz-Raster (bis 10 s bei PID 0x42). Für die Rangkorrelation langsamer Größen ist
  das vertretbar, Skalen daraus aber nicht übernehmen, sondern mit
  `can_obd_scale_recheck.py` (Spec ergänzen) nachrechnen.
- Sehr dünne Referenzen (≤ 0,1 Hz, z.B. PID 0x42) bekommen in `can_byte_search.py`
  keinen trendbereinigten Wert mehr (zu wenige Punkte im 20-s-Fenster) und fallen dort
  durch die Doppelschwelle. Dafür `can_anchor_sweep.py` oder
  `can_find_native_counterpart.py` nehmen.
- **Lag-Suche** (Pflicht bei abgeleiteten Ankern, OBD und GPS; in keinem Skript
  eingebaut, also ad hoc): Kreuzkorrelation über ±1 s, bestes Lag mitberichten.
  Erwartbar: OBD-Request/Response (≈ 1 Abfrageperiode), kausale Filter,
  GPS (typ. 0,1–0,3 s). Ein deutlich größeres Lag ist ein Warnsignal.

### 4. Grober Scan

- `can_byte_search.py <log>`: unbelegte Bytes/Byte-Paare (BE/LE, signed/unsigned)
  gegen Messanker + DIDs, Pearson **und** Spearman, roh **und** detrended.
  Ausgabe `results/can_byte_search_<log>.csv`.
- `can_anchor_sweep.py [--log …]`: dasselbe bitgenau (Bytes, Nibbles, Byte-Paare)
  gegen die abgeleiteten Anker, über alle Logs aggregiert (Treffer zählt nur mit
  gleichem Vorzeichen in vielen Logs). Das ist der Weg für abgeleitete Anker;
  `can_bitsearch.py` kann keine.
- Counter/Checksummen aus Schritt 1 vorher ausschließen.

### 5. Feinsuche

`can_bitsearch.py <can_id> --log <log> (--ref-did <NAME> | --ref-signal <NAME> --ref-id <hex>)`
exhaustive Startbit×Länge×Endianness×Sign-Suche mit Parsimonie-Regel.

- `--ref-did` nur für die Namen in `KNOWN_DIDS`, `--ref-signal` nur für rohe
  DBC-Signale. Für abgeleitete Anker `can_field_inspect.py` oder ad hoc.
- Parsimonie plus fehlendes Resolution-Refinement kann eine **Teilspanne** gewinnen
  lassen: Im Test 0x082 gewann 7 Bit ab Bit 48 statt 12 Bit ab Bit 44, weil die
  OBD-Referenz die unteren Bits nicht auflöst. Der `[Auflösungshinweis]` nennt dann
  nur die nächstlängere Alternative, nicht zwingend die richtige. Längere Varianten
  mit einer fein aufgelösten Referenz (z.B. eine Ableitung aus derselben Botschaft)
  gegenprüfen.

### 6. Flag-/Ereignis-Matching (für binäre Signale)

Pearson ist für seltene Ereignisse irreführend. Stattdessen:

- `can_event_bit_diff.py`: Lift = P(Bit | Fenster) − P(Bit | Baseline). Funktions-
  nachweis mit `--event brake` (muss das bekannte Bremslicht-Bit finden).
- `can_rare_bits.py`: vom Bit aus, jede Episode mit Fahrkontext.
- Für eine eigene Ereignismaske aus einem Anker (z.B. Bremsschlupf, siehe
  Referenzdatei) ad hoc Präzision, Recall, F1/Jaccard je Kandidaten-Bit mit
  Toleranzfenster (±2–3 Frame-Perioden) rechnen, ergänzend Mutual Information
  (erfasst auch invertierte Logik).
- Mindestens ~10 unabhängige Ereignisse, bevor ein Flag ohne `_maybe` gilt.
- Ein DBC-Name ist kein Beleg: `DSC_Status` stand ganze Logs auf „Off" ohne
  Schalterdruck und ist in Wahrheit eine Kontrollleuchte.

### 7. Kalibrierung prüfen/verfeinern

`can_re_toolkit.py`:
- `detect_extreme_outliers`/`describe_extreme_outliers`: Sentinel-Erkennung VOR
  jeder Regression.
- `propose_round_calibration`/`propose_anchor_calibration`: Scale/Offset nur
  anpassen, wenn der Bias klein bleibt (nicht auf R²/Spearman verlassen).
- `plausibility`: referenzfreier Score als zusätzliche Rangier-Dimension.
- **Physikalische Skalenprüfung**: Auflösung sollte „rund" sein (1, 0.5, 0.25, 0.1,
  0.02, 1/16, 1/512 …) und `2^Länge × Scale + Offset` zum physikalischen Bereich
  passen.

**Bekannte Fallstricke aus unseren Funden**, bei schwacher oder seltsamer Korrelation
zuerst prüfen:
- **signed/unsigned verwechselt** → scheinbare „Rollover"-Sprünge (BrakePressure
  12.09., AmbientTemp 15.09.). `can_field_segmentation.py` hat einen Verdachtstest.
- **Endianness**: `b5-6_LE` heißt Byte6 = High-Byte.
- **Betrag vs. Vorzeichen**: Felder mit getrenntem Richtungsbit gegen `|Anker|`
  testen (`SteeringRate_Abs`/`_Dir`).
- **Modulo-Wrap / Sägezahn** zeigt sich als schwache Pearson-Korrelation.
  Entfalten, dann erneut testen.
- **Konstante statt Formel**: Eine Fremdformel kann einen konstanten Wert liefern
  (alte AmbientTemp-Formel: immer 25,8 °C). Wertebereich über alle Logs ansehen.

### 8. Zufallstreffer ausschließen (Nulltest)

Bei tausenden Kandidaten × Encodings × Ankern ist die *beste* Korrelation auch bei
reinem Zufall hoch. `can_anchor_sweep.py` mildert das über die Aggregation über
viele Logs, eine echte Mehrfachtest-Korrektur hat kein Skript
(`can_null_test.py` [geplant], bis dahin ad hoc):
- Anker zirkulär um zufällige Offsets verschieben (≥ 30 s, größer als die
  Autokorrelationslänge), Scan N-mal (100–200) wiederholen, jeweils den
  **Maximal**-Score notieren.
- Fund nur ernst nehmen, wenn er über dem 95-%-Quantil dieser Maxima liegt.
- Einsetzen vor allem bei Einzel-Log-Funden und bei Ankern mit starkem Trend.

### 9. In die Master-DBC eintragen

`data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc`, von Hand. Kein automatischer Merge,
eine einzige dokumentierte Datei. Die DBC geht per Git-Deploy auch auf den Pi.

**Status steckt im Signalnamen** (Projektkonvention, rund 70 Signale):
- `…_maybe`: Deutung oder Skala offen (auch Funde aus nur einem Log)
- `…_related`: hängt nachweislich mit einer Größe zusammen, Bedeutung/Formel unklar
- ohne Suffix: bestätigt (Schritt 10 bestanden)

Hochstufen = umbenennen (Suffix weg). Das zieht eine Änderung an `CAN_SIGNAL_MAP`
nach sich (Schritt 11).

**Kommentar** (`CM_ SG_`) nach dem Muster der bestehenden Einträge, beginnend mit
„Eigene Reverse-Engineering-Ergaenzung (JJJJ-MM-TT)", Inhalt:
- Anker (Signal/DID/abgeleitet: Formel)
- Anzahl Logs, r/R², Steigung/Offset je Log, Bias, Lag, Nulltest falls gemacht
- Sentinel-Werte, „Länge = untere Schranke" falls MSB nie gesetzt
- Ersetzt der Eintrag einen älteren: „ERSETZT (Datum) das am … eingetragene …"

Counter und Checksummen ebenfalls eintragen (`…Counter…`/`…Checksum…`, mit
Algorithmus falls bekannt).

### 10. Cross-Log-Validierung

Mindestens 2–4 unabhängige Logs, bevor ein Fund ohne Suffix heißt
(`docs/logs/can-bus-status.md`). **Übertragungstest**: Scale/Offset aus Log A
fitten und auf Log B **nur anwenden**, nicht neu fitten. Der Bias auf B ist der
ehrliche Test. Hat die Referenz nur wenige Logs (EPS-DIDs: 4), mit einem Anker aus
Schritt 2.3 auf den ganzen Bestand ausweiten.

### 11. In den Datalake übernehmen und dokumentieren

Der Datalake dekodiert **nicht** automatisch alles aus der DBC. `build_datalake.py`
übernimmt nur Signale, die in `CAN_SIGNAL_MAP` stehen:

1. Eintrag `"<DBC-Signal>": ("<Kanal>", "<Einheit>")` in `CAN_SIGNAL_MAP`, mit
   Kommentar zur Herleitung (Muster siehe bestehende Einträge). Namensschema:
   eigener Kanal mit Suffix `_CAN`; `_CAN_raw`, solange nur der Rohwert
   durchgereicht wird (z.B. `FuelTank_CAN_raw`). Auf einen bestehenden OBD-Kanal
   nur mappen, wenn Skala und Bedeutung bestätigt sind (wie `EngineRPM`, `C001_ODO`).
   `_maybe`-Signale dürfen in den Datalake, aber unter eigenem `_CAN`-Namen, nie
   gleichgesetzt mit einem OBD-Kanal.
2. Sentinel-Werte in `CAN_SENTINELS` eintragen (werden zu NaN).
3. `SCHEMA_VERSIONS["can"]` hochzählen, damit alle CAN-Logs neu eingelesen werden
   (Backfill über den ganzen Bestand).
4. **Duplikate**: Gibt es eine Größe in mehreren IDs, eine Vorzugsquelle festlegen
   (höchste Rate/Auflösung). Nie zwei Rohspalten unter einen Kanalnamen legen
   (vgl. `TM_GEST`-Dual-Column-Bug, `VehicleSpeed_Display` getrennt von `VehicleSpeed`).
5. Build laufen lassen und den neuen Kanal gegen die Anker aus Schritt 2 plausibel
   prüfen.

**Doku** (CLAUDE.md-Konvention): datierten Abschnitt an
`docs/logs/can-bus-status.md` anhängen, `docs/status/can-bus.md` bzw.
`docs/status/can-open-fields.md` (Teil A neu identifiziert, Teil B offen) in-place
nachziehen.

## Prinzipien

- **Werkzeug vor Ad-hoc-Eyeballing.** Die Rankings/Bias-Gates der Skripte nicht mit
  „sieht glatt aus" überstimmen. Deren bekannte Schwächen (Schritte 2b, 3, 5) aber
  kennen und nachrechnen.
- **Referenzfreie Plausibilität ergänzt, ersetzt aber nicht** die Korrelation gegen
  eine echte Referenz.
- **Parsimonie:** Bei gleich gut fittenden, verschachtelten Kandidaten gewinnt der
  kürzere (vgl. `TM_GEST`-Dual-Column-Bug, `mx5_build_datalake_dual_column_bug.md`),
  solange die Referenz die unteren Bits überhaupt auflöst (Schritt 5).
- **Bias-Budget statt R²-Gate** für Scale/Offset.
- **Sentinel-Erkennung agnostisch.**
- **Anker-Provenienz / keine Zirkelschlüsse:** Ein Anker ist nur so gut wie seine
  schwächste Quelle. Aus `_maybe`-Signalen abgeleitet → das gefundene Signal ist
  höchstens `_maybe`. Zwei unbekannte Felder, die sich gegenseitig bestätigen, sind
  keine Bestätigung. Ein Anker aus derselben CAN-ID wie der Kandidat taugt zum
  Finden, aber nicht als unabhängige Bestätigung (gemeinsame Quelle).
- **Gültigkeitsmaske ist Pflicht:** Jede physikalische Beziehung gilt nur in einem
  Regime (eingekuppelt, kein Schlupf, stationäre Kurvenfahrt, v > v_min …).
- **Vergleich gegen Gleichartiges:** Ereignisse gegen dieselbe Situation ohne das
  Merkmal vergleichen, nicht gegen „alles andere".
- **Form vs. Skala trennen:** Ein Anker mit unsicheren Parametern taugt zur
  Feldfindung, aber nicht zur Kalibrierung.
- **Signaltyp bestimmt die Metrik:** kontinuierlich → Korrelation+Regression;
  Flag → Ereignis-Matching; Enum → Kontingenz; Zähler → Ableitung.

## Bekannte Grenzen unserer Werkzeuge

- **Resampling** seit 29.09. korrigiert (Schritt 3), ältere Sweep-Ergebnisse gegen OBD
  sind verzerrt. `can_anchor_sweep.py` hält OBD-Anker weiter per Sample-and-hold.
- **`can_bitsearch.py` überspringt Felder mit nie gesetztem MSB** (Schritt 2b) und hat
  kein Resolution-Refinement (Schritt 5).
- **Keine Lag-Suche** in den Skripten, ad hoc (Schritt 3).
- **Keine Mehrfachtest-Korrektur**, Nulltest ad hoc (Schritt 8).
- **`--ref-did` nur für `KNOWN_DIDS`**, `--ref-signal` nur für rohe DBC-Signale.

## Weiterführend

- `references/discovery-techniken.md`: Discovery-Modus (D1–D17), Pipeline,
  Kandidaten-Register. **Lesen, wenn nicht ein bestimmtes Signal gesucht ist.**
- `references/abgeleitete-anker.md`: Anker-Katalog (Formeln, Masken, vorhandene
  Anker-Namen), Kombinations-/Residuen-Muster. **Lesen, bevor ein Anker gebaut wird.**
- `docs/plans/can-offline-ausbeute-plan.md`, `can-deep-search-plan.md`,
  `can-vertikal-wank-nick-plan.md`: bisherige Suchkampagnen und ihre Ergebnisse.
- `data/can/external/README.md`: Regeln für Fremd-DBCs.

## Quellen (Methodik)

- Marchetti & Stabili, *READ*, IEEE TIFS 2019 — Bit-Flip-Raten, Counter/Checksum-Klassen.
- Pesé et al., *LibreCAN*, ACM CCS 2019 — OBD/IMU-Kreuzkorrelation, Differenzaufnahme für Karosseriesignale.
- Verma et al., *CAN-D*, IEEE TVT 2021 (arXiv:2006.05993) — Grenz-Heuristik, Endianness-Optimierung, Signedness-Heuristik, Interpolation auf DID-Zeitstempel.
- Verma et al., *ACTT*, 2018 — Regression aller Kandidaten gegen DIDs.
- Lin et al., *ByCAN*, arXiv:2408.09265 — Byte-Level-Clustering, Feldtypen, DTW.
- Buscemi et al., *CANMatch*, IEEE TVT 2021 — Frame-Wiederverwendung über Modelle.
- Buscemi et al., *Multiplexed CAN Frames*, VehicleSec 2024.
- Kulandaivel et al., *CANvas*, USENIX Security 2019 — Sender-Zuordnung über Timing.
- Ruan et al., *PicaCAN*, IEEE TMC 2025 — Semantik über physikalische Kausalität.
- CRC RevEng (reveng.sourceforge.io) — CRC-Parameter aus Nachricht/CRC-Paaren.
