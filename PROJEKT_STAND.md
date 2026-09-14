# MX-5 ND RF G184 Fahrsimulationsprojekt — Projektstand

Stand: 28.08.2026, geschrieben fuer die Fortsetzung in Claude Code
(vorher in Cowork/Claude Desktop bearbeitet)

## Kontext

Wir entwickeln schrittweise ein Fahrleistungs-/Fahrdynamikmodell fuer einen
Mazda MX-5 ND RF G184, Ziel ist perspektivisch eine Bestzeit-Berechnung
aus einem GPS-Track. Ein bereits weit entwickeltes Laengsdynamik-Modell
(0-100/Vmax/Schaltpunkte etc., urspruenglich mit MS Copilot gebaut)
existiert separat und ist NICHT Gegenstand der aktuellen Arbeit, ausser
gezielt danach gefragt wird.

Aktuell im Fokus: Schwingungsanalyse der IMU-Daten aus dem Handy
(Saugnapf-Halterung, rechts vom Infotainment-Display), um daraus
verlaessliche Quer-/Laengsbeschleunigung fuer eine spaetere
Quergrip-Schaetzung zu gewinnen.

## Datenzugang

- Google Drive Ordner "Loggs": enthaelt OBD-Fusion-Logs (CSV-Exporte und
  die zugrundeliegenden .dlg-SQLite-Datenbanken). Wurde in dieser Phase
  nicht mehr gebraucht, da die .dlg-Rohdaten ohnehin lokal landen.
- Lokaler Ordner: `/home/manuel/claude`

  **Verzeichnisstruktur (NEU, 29.08.2026 — Ordner umorganisiert, da
  weitere/aeltere Logs mit weniger Kanaelen erwartet werden):**
  ```
  claude/
  ├── PROJEKT_STAND.md
  ├── MX5_Aktueller_Kenntnisstand_2026-08-29.md   (externes Fahrleistungsmodell)
  ├── .venv/                  (lokale Python-Umgebung, siehe unten)
  ├── scripts/                (alle .py-Analyseskripte)
  ├── data/
  │   ├── raw/                (.dlg-Rohdateien, alt + neu)
  │   └── derived/            (pro Log erzeugte _imu_filtered.csv,
  │                            _vibration_summary.json, _gg_diagram.png, ...)
  └── results/                (fahrtuebergreifende Auswertungen: JSON-
                               Summaries + Diagramme aus allen Skripten
                               ab brake_event_analysis.py)
  ```
  **WICHTIG: alle Skripte werden vom Projekt-Wurzelverzeichnis
  (`claude/`) aus aufgerufen**, z.B.
  `.venv/bin/python scripts/vibration_analysis.py "<datei>.dlg"`
  (Dateiname ohne Pfad, Skript sucht automatisch in `data/raw/`).
  Alle Skripte wurden nach der Umorganisation gegen die vollen 7 Logs
  neu getestet - identische Ergebnisse wie vorher, keine Regression.

  Aktuell 7 Roh-Logs in `data/raw/` mit je einer `_vibration_summary.json`
  und `_imu_filtered.csv` in `data/derived/`:
    - 2026-08-25 170729.dlg (34,8 min)
    - 2026-08-26 082756.dlg (51,6 min)
    - 2026-08-26 154244.dlg (40,6 min)
    - 2026-08-27 081620.dlg (35,5 min)
    - 2026-08-27 170339.dlg (40,2 min)
    - 2026-08-28 143138.dlg (7,7 min)
    - 2026-08-28 151851.dlg (6,4 min)

  `vib_spectrogram.png` (in `results/`) — Spektrogramm aus einer
  frueheren Auswertung.

  Skripte in `scripts/` (Kurzueberblick, Details jeweils im Docstring):
  - `dlg_database.py` — Basis-Export .dlg -> kategorisierte CSV
  - `vibration_analysis.py` — Schwingungsanalyse (Hampel/Clip/Lowpass/
    Resonanz), Kernstueck der Pipeline
  - `calibrate_axes.py`, `estimate_axis_rotation.py` — fruehe/ueberholte
    Kalibrierversuche, nicht mehr fuer die Praxis gebraucht (siehe Punkt 4)
  - `brake_event_analysis.py` — Bremsereignisse + Achsenrotation (theta
    pro Log)
  - `grip_estimation.py` — Quer-/Laengsgrip-Schaetzung (a_lat-Kontinuum
    NICHT verlaesslich, siehe Punkt 3/4)
  - `corner_event_analysis.py` — isolierte, GPS+Gyro-bestaetigte Kurven
  - `braking_model.py` — Bremsmodell (Punkt 5 Roadmap)
  - `drivetrain_model_validation.py` — Validierung des externen
    Fahrleistungsmodells gegen echte Logs

  ERLEDIGT (29.08.2026): Alle 7 Logs sind mit der AKTUELLEN
  Skriptversion (inkl. Hampel-Filter) neu berechnet, JSON/CSV ueberall
  im neuen Format. Lokale venv unter `.venv/` angelegt (System-Python
  war "externally managed", pandas/scipy fehlten).

  **ERWEITERT (29.08.2026): gemeinsamer Datalake fuer alle Logs.**
  Nutzer hat 21 historische Logs als CSV bereitgestellt (`data/raw_csv/`,
  zwei Namenskonventionen: "YYYY-MM-DD HHMMSS.csv" und
  "CSVLog_YYYYMMDD_HHMMSS.csv", Zeitraum 17.-25.08.2026). Diese haben
  TEILWEISE andere Spaltennamen fuer dieselben Groessen (deutsch/
  englisch gemischt, z.B. "Motordrehzahl" vs. "Engine Revolutions Per
  Minute" vs. "EngineRPM") und teils andere Einheiten (Bremsdruck in
  bar statt kPa, GPS-Hoehe/-Genauigkeit in ft statt m).

  Neues Skript `scripts/build_datalake.py` normalisiert ALLE Logs
  (7x .dlg + 20x CSV, ein CSV uebersprungen als nachgewiesenes Duplikat
  einer .dlg-Datei - siehe unten) in ein gemeinsames Langformat-Schema
  (log_id, channel, channel_original, unit, t_elapsed_s, timestamp_local,
  value) in `data/datalake.duckdb` (DuckDB, neue Abhaengigkeit in
  `.venv`). Vollstaendige Namens-/Einheiten-Zuordnungstabelle (65
  beobachtete CSV-Spalten) im Skript-Docstring/Code dokumentiert.

  Vom Nutzer BESTAETIGTE Kanal-Gleichsetzungen (trotz unterschiedlicher
  Namen):
  - `Brake Fluid Pressure Sensor` = `Brake Fluid Line Hydraulic Pressure
    (Raw Value)` = `BFP_PRE_MZ` — ACHTUNG unterschiedliche Einheit (bar
    vs. kPa), wird auf kPa umgerechnet (x100).
  - `Air fuel ratio` = `Actual (AFR)` = `AFR_MZ`.
  - `Tatsaechlicher Gangstatus des Getriebes` = `Unterstuetzter
    tatsaechlicher Gangstatus des Getriebes` = `Transmission Actual Gear
    Status` = `TM_GEST`.

  EIGENE (nicht vom Nutzer bestaetigte) Entscheidung: `Getriebe
  Tatsaechliches Uebersetzungsverhaeltnis` (numerisches Verhaeltnis wie
  2.035) wurde NICHT mit TM_GEST zusammengelegt, sondern als eigener
  neuer Kanal `TransmissionActualGearRatio` gefuehrt (semantisch ein
  Verhaeltnis, kein Gang-Index) - bei Bedarf pruefen. Dieser Kanal ist
  potenziell wertvoll: eine DIREKT gemessene Ganguebersetzung zum
  Abgleich mit den Getriebeuebersetzungen aus
  `MX5_Aktueller_Kenntnisstand_2026-08-29.md`.

  Weitere neue Kanaele (nicht in den bisherigen 7 .dlg-Logs vorhanden):
  Reifendruck (4 Raeder), Tankfuellstand, Oeltemperatur, Batteriezustand,
  Kraftstoffdruck (Rail, commanded+actual), Katalysatortemperatur,
  Steuermodul-Spannung, Zuendungs-/Ventilzeitpunkt-Diagnosewerte.

  `2026-08-25 170729.csv` wurde beim Import uebersprungen — per
  Stichprobe nachgewiesenes Duplikat von `data/raw/2026-08-25 170729.dlg`
  (identische Dauer ~2080s, identische Hoechstgeschwindigkeit 219 km/h).

  Ergebnis: 27 Logs, ~12 Mio. Messwerte, 0 nicht zugeordnete Kanaele
  nach Bugfix (ein Parsing-Sonderfall: "Actual (AFR)" wurde zunaechst
  falsch als Name="Actual"+Einheit="AFR" zerlegt, da die generische
  "letzte Klammer = Einheit"-Heuristik hier versagt - per Sonderfall-
  Liste `RAW_HEADER_OVERRIDES` behoben). Einheiten-Umrechnung stichpro-
  benartig verifiziert (Bremsdruck CSV nach Umrechnung in derselben
  Groessenordnung wie .dlg: bis 5617 kPa; Hoehe nach Umrechnung 57-127m,
  plausibel fuer die Berliner Region, vorher waere es in ft ~190-420
  gewesen).

  Beobachtung (nicht untersucht): zwei grosse CSV-Logs (2026-08-22
  141753 und 154521, je >1,2 Mio. Messwerte) zeigen Bremsdruck-Maximum
  0.0 - moeglicherweise einfach keine Bremsung waehrend der Fahrt,
  nicht als Fehler bestaetigt.

  NOCH NICHT GEMACHT: die bestehenden Analyseskripte
  (vibration_analysis.py, brake_event_analysis.py, drivetrain_model_
  validation.py, ...) lesen weiterhin direkt aus einzelnen .dlg-Dateien,
  NICHT aus dem neuen Datalake. Eine Umstellung (oder zumindest
  `drivetrain_model_validation.py` um die zusaetzlichen CSV-Logs zu
  erweitern, gerade die kurzen "CSVLog_*"-Dateien mit nur RPM/Speed/
  Gang/ETC sehen sehr geeignet fuer die WOT-Validierung aus) waere ein
  guter naechster Schritt, aber noch offen.

  **Naechster Schritt laut Nutzer: ggf. werden noch weitere historische
  Logs nachgereicht** - einfach als .dlg nach `data/raw/` oder als .csv
  nach `data/raw_csv/` legen (Nutzer nennt diesen Ordner weiterhin
  "csv_old" - das ist derselbe Ordner, nur umbenannt/einsortiert im Zuge
  der Restrukturierung, dorthin sollen weitere historische CSV-Funde),
  dann `python scripts/build_datalake.py` erneut laufen lassen (baut die
  Datenbank jedes Mal komplett neu auf, keine inkrementellen Dubletten-
  Risiken). Bei bisher unbekannten Spaltennamen bricht das Skript NICHT
  ab, sondern fuehrt sie als `UNMAPPED:<name>` und warnt in der Konsolen-
  ausgabe - vor einer echten Analyse mit neuen Logs immer auf UNMAPPED-
  Warnungen pruefen.

  **ERWEITERT (29.08.2026): `drivetrain_model_validation.py` liest jetzt
  aus dem Datalake statt nur aus den 7 urspruenglichen .dlg-Dateien.**
  Damit fliessen automatisch alle 27 Logs ein (12 davon mit ausreichend
  RPM/Speed/Gang-Daten fuer den kinematischen Check, 7 mit APP-Kanal fuer
  den Volllast-Check plus 5 neue aus den historischen CSVs). Ergebnis
  deutlich robuster als zuvor:
  - Kinematischer Check (Drehzahl aus Geschwindigkeit+Gang): jetzt 12
    statt 7 Logs, u.a. zwei neue mit sehr grossen Stichproben (n=1301,
    n=1182) und sehr niedrigem Fehler (~0.00%, -0.05%) - starke
    zusaetzliche Bestaetigung von r_dyn/Getriebe-/Achsuebersetzung.
  - Volllast-Beschleunigung: jetzt 32 statt 23 Segmente (aus 7 Logs).
    Kernergebnis unveraendert bestaetigt: Korrelation 0.99, Modell
    ueberschaetzt weiterhin systematisch um ~8% (Median-Verhaeltnis
    gemessen/Modell jetzt 0.92, vorher ebenfalls 0.92) - mit mehr Daten
    stabil reproduziert, kein Zufallsbefund.

  **NEU (29.08.2026): automatische Google-Drive-Ueberwachung
  eingerichtet.** Nutzer moechte wissen, wenn im Drive-Ordner "Loggs"
  neue .dlg-Dateien auftauchen. Cloud-Routine (`schedule`-Skill) war
  NICHT geeignet - Cloud-Agenten haben keinen Zugriff auf lokale Dateien/
  Skripte. Stattdessen lokale Scheduled-Task eingerichtet (laeuft nur,
  waehrend die Desktop-App offen ist - bei geschlossener App holt sie den
  Check beim naechsten Start nach):
  - Task-ID: `check-drive-for-new-dlg-logs`, taeglich 21:07 Uhr lokal.
  - Ablauf pro Lauf: Drive-Ordner nach .dlg-Dateien durchsuchen, mit
    `data/raw/` abgleichen, neue Dateien herunterladen + speichern,
    `vibration_analysis.py` je neuer Datei, danach `build_datalake.py`
    neu aufbauen, Nutzer ueber Funde informieren.
  - Bearbeitet NUR .dlg (CSV/ZIP im selben Drive-Ordner werden ignoriert,
    das verwaltet der Nutzer manuell ueber `data/raw_csv/`).
  - Vollstaendiger Prompt in
    `/home/manuel/.claude/scheduled-tasks/check-drive-for-new-dlg-logs/SKILL.md`.
  - Bei Ersteinrichtung: einmal manuell "Jetzt ausfuehren" empfohlen, um
    die Google-Drive-Tool-Berechtigung zu erteilen, bevor der erste
    automatische Lauf ansteht.
  - `2026-08-25 081538.csv` (55 Kanaele, vom Nutzer nachgefragt) wurde
    per Hand aus Drive nachgeladen (`data/raw_csv/`), Datalake auf 28
    Logs / 12,4 Mio. Messwerte aktualisiert, 0 UNMAPPED. Drivetrain-
    Validierung mit dieser Datei erneut gelaufen: jetzt 34 Volllast-
    Segmente (vorher 32), Korrelation weiterhin 0.98, Verhaeltnis
    gemessen/Modell Median jetzt 0.93 (vorher 0.92) - Bias-Befund bleibt
    stabil.
  - OFFEN: die "Jetzt ausfuehren"-Empfehlung fuer die neue Scheduled Task
    ist eine UI-Aktion (Sidebar "Scheduled") - es gibt kein Tool dafuer,
    der Nutzer muss das selbst anstossen, bevor der erste automatische
    Lauf (taeglich 21:07 Uhr) ansteht, sonst pausiert der erste Lauf
    ggf. auf eine Berechtigungsabfrage fuer die Drive-Tools.

  Ergebnis der Neuberechnung: X-Achse (laengs) bleibt bei allen 7 Logs
  konsistent bei ~21-23 Hz (Resonanz bestaetigt). Neuer Auffaelligkeits-
  Fall: 2026-08-27 17:03:39, Y-Achse, Peak bei 0,54 Hz statt der
  ueblichen ~18-19 Hz — gleiches Muster wie der bekannte 15:18-Ausreisser
  (kurzzeitige Fahrdynamik dominiert globalen PSD-Peak), noch nicht
  einzeln untersucht. Betrifft Punkt 2 unten (Mehrfachpeak-Erkennung).

## Stand der Schwingungsanalyse (`vibration_analysis.py`)

Datenbasis: GPS-Rate ist hart auf ~1,0 Hz limitiert (aus .dlg-Rohzeit-
stempeln verifiziert). IMU-Beschleunigung laeuft mit ~50-60 Hz, wird im
Skript auf 50 Hz uniform resampled.

### Bestaetigter Befund: feste mechanische Halterungsresonanz bei ~21-23 Hz

Ueber alle 7 Logs verglichen (siehe Analyse vom 28.08.): auf der
X-Achse (laengs) liegt der dominante PSD-Peak in 6 von 7 Fahrten
konsistent zwischen 21,0 und 23,3 Hz (Mittelwert 21,96 Hz, Streuung nur
±0,87 Hz), bei durchweg niedriger Korrelation zur Zuendfrequenz (max.
0,31, weit unter der 0,5-Motorordnung-Schwelle). Das ist ein robuster
Beleg fuer eine feste mechanische Resonanz der Halterung (nicht
Motoranregung) — stabil ueber vier Tage und verschiedene Fahrten, die
Frequenz "wandert" nicht. Y und Z waren zunaechst weniger sauber, weil
die Resonanz dort energetisch schwaecher ist und teils ein
trip-spezifischer niederfrequenter Peak (echte Fahrdynamik) das globale
PSD-Maximum gewann. NACHTRAG 29.08.2026: behoben durch Bandbeschraenkung
der Resonanzsuche, siehe "Naechste Schritte", Punkt 2 — Y liegt jetzt in
fast allen Logs ebenfalls im ~18-23 Hz-Bereich.

### Ausreisser-Log 2026-08-28 15:18:51

Faellt aus dem Muster: dominanter Peak liegt auf allen 3 Achsen bei
~0,9-1,1 Hz statt ~21 Hz, mit auffaellig hoher Streuung der lokalen
Peak-Frequenz und moderater negativer Korrelation (~-0,5) zur
Zuendfrequenz. Zur Kontrolle wurde VehicleSpeed/RPM direkt aus der .dlg
gezogen: keine Rangier-/Standfahrt, sondern normale Fahrt mit
Autobahnanteil (0-98 km/h, Ø 40,5 km/h). Im Rohsignal gibt es zwei
auffaellige Cluster: t≈92-131s (Beschleunigungsphase, betrifft v.a.
Y/Z) und t≈330-346s (Ausrollen/Anhalten am Fahrtende, X bis -17,2
m/s²). Kurze Fahrten (dieses Log: 187 Mittelungsfenster, 14:31-Log: 229)
haben deutlich weniger Mittelung als lange Fahrten (1000+ Fenster) —
wenige grosse Stoesse bzw. reale, kraeftige Tempowechsel (0→98→0 km/h
in 6,4 min) koennen das gesamte Welch-Spektrum dominieren und den
echten Resonanz-Peak ueberdecken.

### NEU umgesetzt: Hampel-Filter + physikalisches Clipping + Kreuzvalidierung

Vor jeglicher Frequenzanalyse durchlaeuft das resamplte Rohsignal jetzt:

1. **Hampel-Filter** (gleitender Median/MAD, Fenster 0,3s = 15 Samples
   @50Hz, Schwelle 4σ, `HAMPEL_*`-Konstanten im Skript) — robuste,
   frequenzunabhaengige Ausreissererkennung, ersetzt Ausreisser durch
   lokalen Median (keine Sprungstellen).
2. **Physikalisches Clipping** bei ±12 m/s² (`PHYSICAL_CLIP_MS2`) als
   hartes Sicherheitsnetz danach, mit vollstaendigem Event-Logging
   (Zeitpunkt, Rohwert, Dauer) statt stillem Verwerfen.
3. **Kreuzvalidierung**: jedes erkannte Hampel-/Clip-Ereignis wird gegen
   eine grobe dv/dt-Schaetzung aus dem OBD-Kanal VehicleSpeed (~2Hz,
   lineare Ausgleichsgerade im ±1s-Fenster) geprueft. Passt die
   Geschwindigkeitsaenderung nicht zum IMU-Ausschlag -> Flag
   "IMU-only (moeglich Halterungs-/Sensorartefakt)".

Ergebnisse & Grenzen des Ansatzes (validiert an den beiden 28.08.-Logs):

- **14:31-Log** (der bekannte ~17 m/s²-Restausreisser nach Tiefpass):
  sank auf 12,66 m/s² (nah an der Clip-Grenze, Rest ist Filter-Ringing
  an der Clipping-Kante). Alle grossen Clip-Events bei t≈168-180s und
  t≈213s zeigen OBD-dv/dt ≈ 0 -> sauber als Halterungsartefakt
  bestaetigt. Resonanzerkennung blieb korrekt bei ~21-23 Hz.
- **15:18-Log**: Die grossen Einzelspitzen am Fahrtende (t≈330-346s,
  OBD-Geschwindigkeit praktisch konstant) wurden ebenfalls klar als
  Artefakt erkannt. ABER: der dominante Peak bleibt trotzdem bei ~1 Hz.
  Der Hampel-Filter flaggt dort zusaetzlich ungewoehnlich viele (372)
  sehr kurze Einzelereignisse rund um t≈170-195s (die ~90-97 km/h
  Autobahn-Passage) — das sieht eher nach einer echten, zeitweise
  staerkeren Schwingung aus, die in viele Mikro-Ereignisse zerlegt wird,
  statt nach Einzelstoessen. Fazit: der Filter behebt zuverlaessig
  Zeitbereichs-Ausreisser (validiert durch OBD-Kreuzcheck), aber NICHT
  die Peak-Fehlklassifikation bei kurzen/dynamischen Fahrten — dafuer
  braeuchte es eine Mehrfachpeak-Suche oder eine Bandbeschraenkung der
  Resonanzsuche auf >5 Hz (siehe naechste Schritte).

## Naechste Schritte (offen, Prioritaet in dieser Reihenfolge)

1. ~~Die uebrigen 5 Logs mit der aktuellen Skriptversion neu durchrechnen~~
   ERLEDIGT am 29.08.2026, siehe oben.
2. ~~Mehrfachpeak-Erkennung bzw. Bandbeschraenkung der Resonanzsuche~~
   ERLEDIGT am 29.08.2026 (Kombination aus beiden vorgeschlagenen
   Optionen):
   - Neue Konstante `RESONANCE_SEARCH_MIN_HZ = 5.0`: die fuer Notch-
     Entscheidung und Motorordnungs-Korrelation massgebliche
     "Resonanz-Peak"-Suche laeuft jetzt nur noch oberhalb 5 Hz (sowohl
     im Gesamtspektrum als auch pro Fenster in
     `resonance_vs_engine_order`). Der bisherige unbeschraenkte Peak
     bleibt als "global_peak_hz" separat im JSON erhalten (rein
     diagnostisch), mit Flag `global_vs_resonance_peak_mismatch`, falls
     beide auseinanderfallen.
   - Neue Funktion `find_top_peaks()`: echte Mehrfachpeak-Suche
     (Top-5, min. 1 Hz Abstand), als `top_peaks_hz` im JSON.
   - Alle 7 Logs damit neu durchgerechnet. Ergebnis: die vorher
     "unsauberere" Y-Achse liegt jetzt in fast allen Logs im selben
     ~18-23 Hz-Bereich wie X (Resonanz axenuebergreifend bestaetigt).
     Der bekannte 15:18-Ausreisser zeigt jetzt korrekt 21-22 Hz auf
     allen 3 Achsen (vorher faelschlich ~0,9-1,1 Hz), mit
     `global_vs_resonance_peak_mismatch=true` als Hinweis auf die
     kurze/dynamische Fahrt. Gleiches beim vorher unauffaelligen
     17:03-Log: Y-Achse jetzt 22,51 Hz statt der falschen 0,54 Hz.
   - Venv-Hinweis: Aufrufe laufen ueber `.venv/bin/python
     vibration_analysis.py "<datei>.dlg"` (siehe oben, Punkt 1).
3. **ERSTE VERSION FERTIG (29.08.2026):** Quergrip-/Laengsgrip-Schaetzung
   (urspruenglich aus GPS-Kruemmung geplant, bei 1Hz-GPS unzuverlaessig —
   direkte IMU-Messung ist der bessere Weg). Skript: `grip_estimation.py`.

   Vorarbeit (davor erledigt):
   - Geprueft: die IMU-Werte sind bereits gravitationskompensierte
     "lineare Beschleunigung" (Mittelwert ueber die volle Fahrt liegt bei
     allen 7 Logs auf allen 3 Achsen nahe 0, keine Tilt-Kompensation
     noetig).
   - Gefunden: jeder Log-Anfang hat ein ~1,5s-Einschwingartefakt (z.B.
     Y laeuft von -9,35 auf ~0 herunter) — bekanntes Verhalten der
     Android-Sensor-Fusion (Rotation-Vector-Filter braucht kurz zum
     Konvergieren nach Sensor-Start), kein echtes Fahrsignal. Wird jetzt
     in `grip_estimation.py` per `STARTUP_SKIP_S` weggeschnitten.

   Methodik: `a_long`/`a_lat` per Rotation (theta = Fahrzeug-Vorwaerts-
   Winkel PRO LOG aus `brake_event_summary.json`, siehe Punkt 4) aus dem
   lowpass-gefilterten X/Z gebildet, dann in g umgerechnet. Kennzahlen:
   Maximum, Perzentile (p50/90/95/99), Zeit oberhalb 0.3/0.5/0.7/0.9g,
   getrennt fuer quer/laengs/kombiniert (Traction-Circle-Betrag). Zusaetz-
   lich ein g-g-Diagramm (Scatter) pro Log als PNG.

   Wichtiger Zwischenschritt: die ersten "max_g"-Werte waren unplausibel
   hoch (bis 1.79g quer/kombiniert — kein Strassenreifen schafft das).
   Stichprobe zeigte: der Maximalwert eines Logs fiel EXAKT mit einem
   bereits von `vibration_analysis.py` als "IMU-only /
   Halterungsartefakt" geflaggten physical-clip-Ereignis zusammen
   (Rohwerte bis 30 m/s², von der OBD-Kreuzvalidierung bereits als nicht
   plausibel markiert). Fix: `grip_estimation.py` schliesst jetzt alle
   Zeitfenster um bekannte physical-clip-Ereignisse (beide Achsen X/Z)
   aus, bevor die Grip-Kennzahlen berechnet werden (deutliche
   Verbesserung, z.B. ein Log von 1.57g auf 0.65g max korrigiert).
   VERBLEIBENDE EINSCHRAENKUNG: einige Logs zeigen weiterhin max-Werte
   um 1.3-1.4g (fuer Strassenreifen an der Glaubwuerdigkeitsgrenze) —
   moeglicherweise Restartefakte, die nicht in den (auf Top-25 pro
   Achse begrenzten) geloggten Clip-Events erfasst sind. **p95/p99 sind
   die verlaesslicheren Kennzahlen, "max" nur mit Vorsicht verwenden.**

   WEITERE EINSCHRAENKUNGEN (siehe Docstring in `grip_estimation.py`):
   - Vorzeichen von a_lat (links/rechts) ist NICHT validiert — die
     Achsenkalibrierung kam nur aus Bremsereignissen (reine
     Laengsrichtung). Fuer den Grip-BETRAG irrelevant, fuer eine
     spaetere Links/Rechts-Unterscheidung waere ein zusaetzlicher
     Abgleich noetig (z.B. Lenkwinkel-PID falls vorhanden, oder GPS-Spur).
   - theta stammt aus bremseignisbasierter Kalibrierung PRO LOG - bei
     Logs mit wenigen Bremsereignissen (z.B. 151851: nur 3) entsprechend
     unsicherer (R als Guetemass mit ausgegeben, siehe Punkt 4).
   - g-g-Diagramme visuell geprueft (2026-08-27 170339): plausible
     Form, dichter Kern um 0 mit natuerlicher Streuung nach aussen,
     keine auffaelligen Artefaktmuster.

   **NACHTRAG 29.08.2026 — Links/Rechts-Vorzeichen-Klaerung deckte
   tieferes Problem auf, a_lat grundlegend ueberarbeitet:**

   Bei dem Versuch, das Vorzeichen von a_lat zu validieren, zeigte sich:
   a_lat aus der X/Z-Rotation korreliert bei KEINEM der 7 Logs mit
   echter Kurvenfahrt (getestet gegen GPS-Kursaenderung UND Gyroskop
   RotationRateY, beide unabhaengig, Korrelationen durchweg ~0, teils
   leicht negativ). Arbeitshypothese: die Halterung reagiert unter
   Seitenlast (Kurve) anders als unter Laengslast (Bremsen) - das aus
   Bremsereignissen gewonnene theta beschreibt nur die Laengsachse
   zuverlaessig. a_long selbst bleibt unberuehrt (weiterhin ueber viele
   Bremsereignisse validiert).

   Als Ersatz wurde a_lat = v*omega (kinematisch, "coordinated turn")
   probiert, mit omega aus RotationRateY. Die Kalibrierung (Vorzeichen +
   Skalierung) wurde erst gegen GPS-Kursaenderung geprueft und war dort
   stark (Korrelation -0.54 bis -0.90 ueber alle 7 Logs, konsistent).
   ABER: als KONTINUIERLICHES Signal ueber die ganze Fahrt angewendet,
   ergaben sich absurde Werte (bis 7.76g, t>0.5g fuer hunderte Sekunden).
   Ursache gefunden: bei einer eindeutig GERADEN Autobahnfahrt (143 km/h,
   stabile Geschwindigkeit) schwankt das rohe Gyroskop trotzdem zwischen
   -30 und +35 deg/s (Std=14) — die Halterung "wackelt" bei normaler
   Fahrt staerker als die tatsaechliche (kleine) Fahrzeug-Gierrate, das
   Rauschen dominiert das Signal. Die vorherige GPS-Validierung hatte
   nur funktioniert, weil sie gezielt auf Segmente mit SEHR STARKER,
   eindeutiger Kurvenfahrt beschraenkt war (|heading_rate|>6-8°/s,
   v>12m/s) - dort ist das echte Signal gross genug, um das Wackel-
   Rauschen zu dominieren.

   **Konsequenz: eine kontinuierliche Quergrip-/g-g-Auswertung ueber
   die ganze Fahrt ist mit der aktuellen Halterung NICHT zuverlaessig
   moeglich** (weder per X/Z-Rotation noch per Gyroskop). Die fruehere
   Version von `grip_estimation.py` (kontinuierliches a_lat) ist damit
   ueberholt, `grip_estimation.py` selbst enthaelt jetzt die kinematische
   Variante mit vollem Hintergrund im Docstring, wird aber fuer die
   Praxis durch `corner_event_analysis.py` ersetzt/ergaenzt (naechster
   Absatz).

   **Loesung (Nutzerentscheidung): nur isolierte, klar bestaetigte
   Kurven auswerten** — neues Skript `corner_event_analysis.py`, gleiches
   Prinzip wie `brake_event_analysis.py`: nur Ereignisse werten, bei
   denen GPS-Kursaenderung UND Gyroskop im Vorzeichen uebereinstimmen
   UND beide klar ueber ihrer Rauschschwelle liegen
   (|heading_rate|>8°/s, v>10m/s). Ergebnis ueber alle 7 Logs: nur
   **6 bestaetigte Kurven** (bei insgesamt ~4.5h Fahrzeit!) — zeigt, wie
   selten das Signal das Halterungsrauschen dominiert:
   - 2026-08-25 170729: 1 (rechts)
   - 2026-08-26 082756: 0
   - 2026-08-26 154244: 3 (1 rechts, 2 links)
   - 2026-08-27 081620: 0
   - 2026-08-27 170339: 2 (beide rechts)
   - 2026-08-28 143138 / 151851: 0
   |a_lat_peak| ueber diese 6 Ereignisse: median 1.12g, max 2.47g -
   **Betraege mit Vorsicht zu geniessen** (nur 2-5 GPS-Samples/Ereignis,
   "peak" ist ein Einzelsample, "mean" pro Ereignis etwas robuster,
   Details in `corner_event_summary.json`).

   **Was jetzt gesichert ist:** das Vorzeichen von a_lat (positiv =
   Rechtskurve, negativ = Linkskurve) ist ueber zwei unabhaengige
   Sensoren (GPS-Kurs, Gyroskop) kreuzvalidiert und damit deutlich
   verlaesslicher als die exakte Groessenordnung.

   OFFEN: Quergrip als durchgehende Zeitreihe bräuchte entweder (a)
   eine steifere/andere Handyhalterung (aktuelle Kugelkopf-Klemmung
   scheint die Ursache des Wackel-Rauschens), oder (b) einen echten
   Lenkwinkel-/Querbeschleunigungs-Kanal vom Fahrzeug selbst (aehnlich
   wie BFP_PRE_MZ fuer die Laengsrichtung) - in den aktuellen 7 Logs
   nicht vorhanden (PID-Liste geprueft, kein Steering-Kanal).

   **NACHTRAG (getestet, verworfen): Hampel/Clip/Lowpass auf die
   Rotationsachsen anwenden (wie bei den Beschleunigungsachsen).**
   Macht keinen Sinn - Begruendung + Test:
   - Anders als bei den Beschleunigungsachsen (scharfe, klar abtrennbare
     Resonanz bei ~21-23Hz) liegt das Gyro-Wackelrauschen BREITBANDIG
     im selben Frequenzband wie die echte Gierrate (<6Hz) - ein
     Tiefpass aendert die Standardabweichung praktisch nicht (bereits
     getestet, siehe oben). Hampel ist fuer kurze, stossartige
     Ausreisser gebaut, nicht fuer kontinuierliches Wackeln ueber
     Sekunden/Minuten - waere hier wirkungslos.
   - Alternativ getestete Theorie: quasi-statische Verformung der
     Halterung proportional zur aktuellen Beschleunigungslast (statt
     Vibration) - schnell getestet per Korrelation |Gyro| vs.
     |a_long|/|a_horiz| ueber alle 3 Rotationsachsen und alle 7 Logs:
     Korrelationen durchweg nur 0.03-0.23 (Rauschniveau, keine
     belastbare Kopplung) - Theorie verworfen. Das Wackeln ist
     vermutlich echtes, lastunabhaengiges Vibrationsrauschen (Motor,
     Fahrbahn, mechanisches Spiel im Kugelgelenk), keine per Software
     korrigierbare Groesse.
4. **Achsenausrichtung des Handys relativ zum Fahrzeug — AKTUELL DRAN,
   TEILWEISE GEKLAERT (29.08.2026).**

   Halterungsgeometrie (Nutzerangabe): Saugnapf an Scheibe -> ca. 10cm
   fester Arm -> Kugelkopf mit Rendelschraube geklemmt -> Schale (leicht
   Richtung Fahrer gedreht) -> Handy steht hochkant in der Schale, unten
   aufliegend, von 2 Backen links/rechts geklemmt.

   **Y-Achse = vertikal: BESTAETIGT.** Geprueft ueber die rohe
   `AccelerationWithGravityX/Y/Z` (inkl. Erdbeschleunigung, im Gegensatz
   zu `AccelerationX/Y/Z` NICHT gravitationskompensiert) waehrend echter
   Stillstandsphasen (VehicleSpeed=0, >=3s, gefunden in allen 7
   bestehenden Logs). Ergebnis: |g|~9.86 m/s^2 liegt fast vollstaendig
   auf Y (9.83-9.86), X/Z-Restkomponenten unter ±0.5 m/s^2 (~<3°
   Restneigung). Bestaetigt die Nutzerbeschreibung direkt aus den Daten.
   Die Restneigung variiert leicht zwischen Logs (X-Rest z.B. -0.33 bis
   +0.51) — Hinweis, dass die Halterung zwischen Fahrten nicht 100%
   identisch ausgerichtet ist (wie vom Nutzer erwartet).

   **X/Z-Aufteilung (laengs vs. quer in der Horizontalebene): GELOEST
   (29.08.2026), ueber echten Bremsdruck statt dedizierter
   Kalibrierfahrt.**

   Erste zwei Versuche (Regression gegen OBD-dv/dt, Gyro-basiert) waren
   nicht erfolgreich — Details siehe Git-History/vorherige Version
   dieses Abschnitts falls interessant, kurz: OBD-Geschwindigkeit zu
   grob (R^2~0), Gyro-Ansatz unklar (Halterung evtl. nicht perfekt
   starr). Der Durchbruch kam durch einen bisher ungenutzten Kanal in
   den .dlg-Dateien: **`BFP_PRE_MZ`** ("Brake Fluid Pressure Sensor",
   Mazda-CAN-PID, kPa) — echter, hochaufgeloester Bremsdruck direkt vom
   Fahrzeug, viel besser als die grobe OBD-Geschwindigkeit.

   Methode (siehe `brake_event_analysis.py`): fuer jedes klare
   Bremsereignis (Dauer 1-6s, Spitzendruck >=1000 kPa) wird der
   GEMITTELTE horizontale Beschleunigungsvektor (X,Z, lowpass-gefiltert)
   ueber die Kernphase des Ereignisses (Druck > 70% des Spitzenwerts)
   gebildet. Dieser Vektor zeigt in Bremsrichtung (= negative
   Fahrzeug-Laengsrichtung). Ueber alle Ereignisse eines Logs ergibt der
   betragsgewichtete zirkulaere Mittelwert die Fahrzeug-Vorwaertsrichtung
   in der IMU-X-Z-Ebene.

   **Ergebnis pro Log** (Fahrzeug-vorwaerts-Winkel in der X-Z-Ebene,
   0°=+X-Achse, 90°=+Z-Achse; R=Konzentrationsmass, 1=perfekt):
   | Log | Winkel | R | n Ereignisse |
   |---|---|---|---|
   | 2026-08-25 170729 | 64.2° | 0.908 | 18 |
   | 2026-08-26 082756 | 69.8° | 0.916 | 26 |
   | 2026-08-26 154244 | 67.8° | 0.923 | 29 |
   | 2026-08-27 081620 | 41.8° | 0.841 | 19 |
   | 2026-08-27 170339 | 48.3° | 0.851 | 28 |
   | 2026-08-28 143138 | 63.3° | 0.855 | 6 |
   | 2026-08-28 151851 | 64.3° | 0.919 | 3 |

   Spanne 41.8°-69.8° (Median ~64°), Streuung Std=9.8° ueber die 7 Logs.
   **WICHTIG: theta ist PRO LOG zu verwenden, nicht global gemittelt** —
   bestaetigt die Nutzererwartung, dass die Halterung zwischen Fahrten
   nicht exakt gleich ausgerichtet ist, aber R=0.84-0.92 zeigt: INNERHALB
   einer Fahrt ist die Ausrichtung sehr stabil, die Methode also
   zuverlaessig. Die Streuung der EINZELNEN Ereigniswinkel innerhalb
   eines Logs (vor der Mittelung) wird u.a. auf Trail-Braking
   zurueckgefuehrt (Bremsen in der Kurve mischt Laengs-/Queranteil).

   Transformation: `a_long = X*cos(theta) + Z*sin(theta)`,
   `a_lat = -X*sin(theta) + Z*cos(theta)` mit theta = Vorwaerts-Winkel
   aus obiger Tabelle (log-spezifisch!). Rohdaten/Details:
   `brake_event_summary.json`.

   Alte Tools (Regression-Versuche, dedizierte Kalibrierfahrt) bleiben
   nutzbar/verfuegbar, werden aber fuer die weitere Arbeit nicht mehr
   gebraucht: `estimate_axis_rotation.py`, `calibrate_axes.py`.

   OFFEN / NICHT WEITER GEPRUEFT: ob theta auch bei Logs OHNE
   ausreichend viele/klare Bremsereignisse (z.B. sehr kurze Fahrten)
   zuverlaessig ist — dort ggf. auf den zuletzt bekannten Wert
   zurueckfallen oder eine dedizierte Kalibrierfahrt nachholen.

   Tool bereitgestellt: `calibrate_axes.py "<kalibrierfahrt>.dlg"` ->
   plottet AccelerationX/Y/Z + VehicleSpeed uebereinander (erste 1,5s
   automatisch weggeschnitten, siehe Punkt 3), als
   `<datei>_axis_calibration.png`. Damit laesst sich per Sichtpruefung
   ablesen, welche Achse bei welchem Manoever ausschlaegt.

   Vorgeschlagenes Fahrprotokoll (an sicherem, ruhigem Ort, z.B. leerer
   Parkplatz/wenig befahrene Strecke):
   1. Vor Start ~5s still stehen (Puffer vor dem Einschwingartefakt).
   2. Kurz geradeaus beschleunigen, dann eine kontrollierte, deutliche
      Bremsung bis fast zum Stillstand (Laengsachse isolieren).
   3. ~3-5s Pause/Stillstand (erleichtert spaeter die Zuordnung im Plot).
   4. Geradeaus anfahren, eine sauberer Rechtskurve mit moderatem Tempo
      (Querachse isolieren).
   5. ~3-5s Pause.
   6. Gleiches nochmal als Linkskurve (Vorzeichen der Querachse
      pruefen/bestaetigen).
   7. Aufzeichnung stoppen.

   Nach der Fahrt: `.venv/bin/python calibrate_axes.py "<neue-datei>.dlg"`
   ausfuehren, PNG ansehen und mit der obigen Manoever-Reihenfolge
   abgleichen (z.B. "grosser negativer Ausschlag zeitgleich mit
   fallendem VehicleSpeed" -> das ist die Laengsachse und ihr Bremsvor-
   zeichen). Ergebnis hier im Dokument festhalten, danach Punkt 3
   fortsetzen.
5. Urspruengliche Roadmap (Holdout-Struktur fuers Laengsmodell,
   Kruemmungspipeline fuer GPS-Tracks, Bremsmodell, Rundenzeit-
   Zusammenfuehrung):

   **Bremsmodell: ERSTE VERSION FERTIG (29.08.2026).** Neues Skript
   `braking_model.py`, baut auf `brake_event_summary.json` auf
   (BFP_PRE_MZ-Ereignisse aus `brake_event_analysis.py`).

   Wichtiger Zwischenbefund: der geglaettete IMU-a_long bleibt bei
   ALLEN Bremsereignissen nahe 0 (Median 0.01g), obwohl OBD im selben
   Fenster klare Verzoegerung zeigt (Median 0.15g, bis 0.48g bei harten
   Bremsungen) - Korrelation OBD vs. IMU nur ~0.31. Ursache: waehrend
   harter Bremsungen zeigt a_long eine ausgepraegte Nick-Schwingung
   (Bremsen: Nase runter, Loesen: Nase rauf/Rebound, ~2.5-3Hz, deutlich
   unterhalb der bekannten 21-23Hz-Halterungsresonanz) - am erhoehten
   Sensorort (weit vom Fahrzeugschwerpunkt/Nickpol) durch Hebelarm-
   Effekt stark verstaerkt. Ein einfacher Fenster-Mittelwert/Peak
   ueber a_long faengt Brems- und Loesephase gemeinsam ein und mittelt
   sich damit fast zu Null. Sichtbar auch im Streudiagramm
   `braking_pressure_vs_decel.png`: OBD-Seite zeigt klaren Zusammenhang
   Druck->Verzoegerung, IMU-Seite bleibt flach bei ~0 unabhaengig vom
   Druck. WICHTIG: die RICHTUNG/das Vorzeichen von a_long (und damit
   die theta-Kalibrierung aus Punkt 4) ist davon nicht betroffen - nur
   die Betragsgroesse ist unzuverlaessig. **`obd_avg_decel_g` (aus dem
   OBD-Geschwindigkeitsabfall) ist deshalb die einzige verlaessliche
   Verzoegerungs-Kennzahl in diesem Modell, IMU-Werte nur zum Vergleich.**

   Ausserdem gefunden: sehr kurze Druck-Ereignisse (<1s) koennen wegen
   der 1 km/h-OBD-Quantisierung absurd hohe Scheinverzoegerungen
   vortaeuschen (Beispiel: 3 km/h Abfall in 0.14s -> "0.6g", tatsaechlich
   Rauschen, a_long war sogar positiv) - deshalb `EVENT_MIN_DURATION_S
   = 1.0` als Filter ergaenzt.

   Ergebnis (229 auswertbare Bremsmanoever ueber alle 7 Logs, nach
   Filterung auf Dauer 1-12s und Geschwindigkeitsabfall >=3 km/h):
   - Mittlere Verzoegerung: p50=0.15g, p95=0.31g, max=0.48g (haerteste
     Bremsung: 123->63 km/h, 2026-08-27 170339).
   - 4 echte Vollbremsungen bis zum Stillstand erfasst (v_start
     20-95 km/h) mit Bremsstrecke/-zeit, z.B. 95 km/h -> Stillstand in
     6.78s ueber 93m bei 0.40g - intern konsistent mit v²=2*a*s-Physik.
   - Klarer Zusammenhang Bremsdruck -> Verzoegerung sichtbar (steigt von
     ~0.1g bei 500 kPa auf ~0.35-0.48g bei 3500-5500 kPa), mit
     realistischer Streuung.

   Offen/nicht gemacht: Holdout-Struktur fuers (separate, pausierte)
   Laengsmodell, Kruemmungspipeline fuer GPS-Tracks (nach den Erfahrungen
   mit 1Hz-GPS-Rauschen bei den Kurven-Ereignissen vermutlich nicht
   vielversprechend), Rundenzeit-Zusammenfuehrung.

## Weitere historische Logs nachgeladen (29.08.2026, Abend)

Nutzer hatte 27 weitere historische CSVs in Google Drive ("Loggs"-Ordner)
abgelegt (26 davon neu, `CSVLog_YYYYMMDD_HHMMSS.csv` im Zeitraum
29.07.-13.08.2026, plus eine bisher übersehene ältere
`2026-08-19 164751.csv` im Bare-Namensformat). Alle per Google-Drive-Tool
heruntergeladen (grosse Dateien ueber einen Subagenten, um den
Hauptkontext nicht mit Base64-Daten zu fluten) und in `data/raw_csv/`
gespeichert (jetzt 48 CSVs, vorher 21).

`build_datalake.py` neu gelaufen: **54 Logs, 12.889.736 Messwerte, 0
UNMAPPED**. Die neuen CSVLog_*-Dateien sind durchweg kurz (4-8 Kanäle,
ueberwiegend nur Zeit/Geschwindigkeit/Gang/Bremsdruck/ETC/RPM/GPS) -
passend zur bereits dokumentierten Einschätzung, dass diese schlanken
Logs vor allem für die Volllast-/WOT-Validierung des
Antriebsstrangmodells geeignet sind (siehe Cross-Validierung unten).

**ERLEDIGT: `drivetrain_model_validation.py` mit dem 54-Log-Datalake neu
gelaufen.**

Kinematischer Check: jetzt 25 statt 12 Logs mit ausreichend Daten (viele
der neuen kurzen CSVLog_*-Logs haben genau die dafuer noetigen Kanaele
RPM/Speed/Gang). Fehler durchweg <10% Streuung, mit EINER Ausnahme:
**`2026-08-25 081538` zeigt 65% Streuung / 32% Median-Fehler**, deutlich
schlechter als alle anderen Logs. NUTZER-ENTSCHEIDUNG: erstmal ignorieren,
**Erinnerung: gemeinsam nochmal anschauen** (noch nicht untersucht, ob
z.B. Gangerkennung oder Datenqualitaet in diesem Log fehlerhaft ist).

**NACHTRAG 29.08.2026 — Ausreisser-Log 2026-08-25 081538 gemeinsam
untersucht und geloest.** Visualisierung zeigte: die beiden TM_GEST-
Quellspalten widersprechen sich zu 96,5% (eine meldet konstant Gang 3,
die andere konstant Gang 5, statt normal durch 1-6 zu laufen). Noch
tiefer geprueft: **auch der dritte, direkt gemessene Kanal
`TransmissionActualGearRatio` ist fuer dieses Log eingefroren**
(fast durchgehend 1.286 = Gang 5) - alle drei unabhaengigen Gang-Signale
sind fuer dieses Log unbrauchbar, vermutlich ein PID-/Kommunikations-
fehler dieser einen Aufzeichnungssession.

Nutzer-Loesung: Gang aus Drehzahl+Geschwindigkeit rueckrechnen (implizite
Uebersetzung = RPM*2*pi*r_dyn/(60*v*Achsuebersetzung), naechstliegender
der 6 bekannten Gaenge, >3% Abweichung -> verworfen). **Wichtige
Einschraenkung dabei erkannt und beruecksichtigt:** fuer den
KINEMATISCHEN Check waere das zirkulaer (die genau zu pruefende
RPM-aus-Speed+Gang-Beziehung wuerde als Annahme eingehen) - deshalb NUR
fuer den Volllast-/Beschleunigungscheck eingesetzt, wo eine unabhaengige
Groesse (OBD-Beschleunigung) geprueft wird. Log 2026-08-25 081538 bleibt
also weiterhin zurecht aus dem kinematischen Check ausgeschlossen
(nicht "repariert", sondern als grundsaetzlich ungeeignet fuer DIESE
eine Pruefung erkannt) - Erinnerungs-Memory dazu aktualisiert.

Umgesetzt in `drivetrain_model_validation.py`:
`infer_gear_from_rpm_speed()` + `gear_channel_is_reliable()` (Heuristik:
<3 verschiedene Gaenge im Log -> TM_GEST gilt als unplausibel). In
`wot_segments()` automatisch als Fallback aktiv, wenn TM_GEST unplausibel
ist. Ergebnis: 2 neue Volllast-Segmente aus 2026-08-25 081538 (Gang 5,
115-146 und 134-199 km/h, sehr nah am Modell: 1.71 vs 1.44 m/s² und 1.10
vs 1.11 m/s²). Die Erweiterung deckte dabei NEBENBEI zwei weitere Logs
mit demselben TM_GEST-Problem auf (`2026-08-22 141753`,
`2026-08-22 154521`) - fuer diese ergaben sich aber keine zusaetzlichen
Volllast-Segmente (kein APP>90% im Datenbestand).

**Ergebnis: 44 statt 38 Volllast-Segmente** (40 ueber APP [inkl.
rueckgerechnetem Gang wo noetig] + 4 ueber ETC+Lambda-Fallback),
Korrelation jetzt 0.99, Verhaeltnis gemessen/Modell Median 0.92 (vorher
0.94) - Bias-Befund bleibt stabil, keine Verzerrung durch die neue
Methode.

Volllast-Check (Historie, vor obigem Fund): die neuen CSVLog_*-Logs haben KEINEN APP-Kanal, daher
zunaechst keine zusaetzlichen Segmente (weiterhin 34). Nutzer-Hinweis
dazu: **ETC_ACT allein ist KEIN verlaesslicher Volllast-Indikator** -
ETC_ACT kann bereits ~86° erreichen, deutlich bevor APP 80% erreicht
(vermutlich nichtlineares Pedal-Drosselklappen-Mapping). Verlaesslich ist
erst **fette Gemischanreicherung (Lambda/AFR_MZ < 0.9)**, ein bekanntes
Bauteilschutz-Verhalten bei echter Volllast. `AFR_MZ` ist im Datalake
Lambda-artig um 1.0 skaliert (nicht die absolute AFR).

Umgesetzt: `drivetrain_model_validation.py` hat jetzt einen Fallback -
fehlt der APP-Kanal, wird stattdessen `ETC_ACT>80% UND AFR_MZ<0.9`
(konstanter Gang, Mindestdauer 1.5s) verwendet, mit `ETC_WOT_MIN`/
`LAMBDA_WOT_MAX`-Konstanten im Skript. Nur 3 der neuen Logs haben
ueberhaupt den dafuer noetigen AFR_MZ-Kanal
(`CSVLog_20260813_165235/172140`, `CSVLog_20260817_082405`) - davon
lieferte nur `CSVLog_20260813_172140` tatsaechlich qualifizierende
Segmente. **Ergebnis: 38 statt 34 Volllast-Segmente** (34 APP + 4
ETC+Lambda-Fallback), Korrelation weiterhin 0.98, Verhaeltnis
gemessen/Modell Median jetzt 0.94 (vorher 0.93) - die neuen
Fallback-Segmente bestaetigen den bekannten ~6-7%-Bias, keine
Ausreisser. Jede Segment-Zeile im JSON traegt jetzt ein `detection`-Feld
("APP" oder "ETC+Lambda") zur Nachvollziehbarkeit.

## Cross-Validierung mit externem Fahrleistungsmodell (29.08.2026)

Der Nutzer hat `MX5_Aktueller_Kenntnisstand_2026-08-29.md` bereitgestellt
(separates, unabhaengiges Analysedokument: Antriebsstrang-/Fahrleistungs-
modell + eine eigene IMU-Methode "MX5_IMU_METHOD_V1.0"). Wichtigste
Punkte daraus:

- Enthaelt Masse (1180.705 kg), Radradius (0.2985 m), Achsuebersetzung
  (2.866), alle 6 Gangverhaeltnisse, Volllast-Drehmomentkurve
  (1000-7500 U/min), CdA (0.647 m²), Crr (0.013), eta (0.93) - CdA/eta
  dort explizit als "gekoppelt, nicht unabhaengig identifiziert"
  markiert (deren eigener offener Punkt 1).
- Deren IMU-Methode nutzt `yaw_right = -RotationRateY` - **identisch**
  mit unserer heute empirisch (via GPS) hergeleiteten Konvention, gute
  gegenseitige Bestaetigung. Resonanz-Startkandidat 20.629 Hz liegt in
  unserem gefundenen Bereich (21.0-23.3 Hz).
- ABER: deren Achstransformation ergibt einen Vorwaerts-Winkel in der
  X-Z-Ebene von ~17.4° - unsere aus 229 Bremsereignissen kalibrierten
  Werte liegen bei 42°-70° je nach Log. Deutliche Diskrepanz, NICHT
  aufgeloest (Nutzer-Entscheidung: unser Bremsdruck-kalibriertes Modell
  gilt als Referenz, die Diskrepanz wurde nicht weiter verfolgt).
- Deren Prioritaet 2 ("Absolute IMU-Laengs-/Querskalierung validieren")
  deckt sich mit unserer eigenen Einschaetzung: braucht gezielte
  Kalibrierfahrten.

**Durchgefuehrt: Validierung von deren Antriebsstrang-/Aero-Modell
gegen unsere 7 echten Logs** (Skript `drivetrain_model_validation.py`,
nutzt APP, ETC_ACT, EngineRPM, TM_GEST, VehicleSpeed - alle Kanaele
in den Logs vorhanden und wertegleich mit der Dokumentbeschreibung,
z.B. ETC_ACT max 86.26° ≈ dortiges "ETC_open ca. 86°"):

1. **Kinematischer Check** (Drehzahl aus Geschwindigkeit+Gang+
   Radradius+Uebersetzungen vs. gemessene EngineRPM, unabhaengig von
   Motor/Aero): ueber alle 7 Logs mittlerer Fehler nur -0.6% bis -1.3%,
   Median|Fehler| 0.4-0.7%. **Bestaetigt r_dyn/Getriebe-/Achsuebersetzung
   mit echten Daten** (im Dokument als "validiert" markiert - jetzt
   unabhaengig gegengeprueft).
2. **Dynamischer Check** (Volllast-Beschleunigung, APP>90%, konstanter
   Gang, >=1.5s, 23 Segmente aus 5 Logs): Korrelation gemessen vs.
   Modell **0.99** (sehr stark!). Verhaeltnis gemessen/Modell im Median
   **0.92** - das Modell UEBERSCHAETZT die reale Beschleunigung
   systematisch um ca. 8-9% (Gaenge 2-5 konsistent bei 0.91-0.94, Gang 6
   nur n=3, weniger belastbar). Deutet darauf hin, dass CdA und/oder eta
   (oder die Volllast-Drehmomentkurve selbst) etwas zu guenstig
   angesetzt sind - genau der im Fremd-Dokument offene Punkt 1.
   Streudiagramm: `drivetrain_model_vs_measured.png`.

Damit ist ein konkreter, datengestuetzter Beitrag zur Fahrleistungs-
modell-Fortentwicklung geleistet, ohne neue Messfahrten zu brauchen.
Eine Aufteilung des 8-9%-Bias auf CdA vs. eta vs. Drehmomentkurve ist
mit den vorhandenen Daten NICHT moeglich (bleibt weiterhin "gekoppelt"),
dafuer braeuchte es die im Fremd-Dokument selbst genannten kontrollierten
Hochgeschwindigkeits-/Ausrollmessungen.

## Höhendaten / Digitales Geländemodell (NEU, 29.08.2026)

Nutzer hat Höhendaten für ausgewählte Hochgeschwindigkeitspassagen bereitgestellt:
`höhendaten/` mit 11 ZIP-Kacheln (`als_<E_km>-<N_km>.zip`), je eine
ALS-Punktwolke (Airborne Laserscanning, LAS 1.4/.laz, ~1x1km, ~9-12 Mio.
Bodenpunkte/Kachel) von der LGB (Landesvermessung und Geobasisinformation
Brandenburg). CRS bestätigt: EPSG:25833 (ETRS89/UTM33N), Bodenpunktdichte
weit über dem für <1m-Auflösung nötigen Niveau. Kacheln liegen verstreut
(E 378-382km, N 5833-5842km, ~Region A11 nördlich Berlin), passend zu den
GPS-Koordinaten (`Breite`/`Länge`) der bestehenden Logs.

Neue Abhängigkeiten in `.venv`: `laspy`, `lazrs` (LAZ-Dekompression, reines
Python), `pyproj` (WGS84<->UTM33-Transformation).

**Neues Skript `scripts/elevation_model.py`:** liest alle Kacheln, filtert
Bodenpunkte (LAS-Klasse 2), rastert auf gemeinsames 1m-Gitter (Mittelwert/
Zelle), cached als `data/elevation/dgm_grid.npz` (9000x4000 Zellen, 30%
Abdeckung der Bounding Box - erwartungsgemäß, da die Kacheln gezielt
einzelne Passagen abdecken, kein zusammenhängendes Gebiet). Stellt
`ElevationModel` bereit mit `get_elevation(e, n)` (UTM33, bilinear
interpoliert), `get_elevation_latlon(lat, lon)` (WGS84, inkl.
Koordinatentransformation) für die Nutzung durch andere Skripte (z.B.
Steigungskorrektur, spätere Streckensimulation - noch nicht umgesetzt,
Modul ist bewusst allgemein gehalten). Aufruf zum (Neu-)Aufbau:
`.venv/bin/python scripts/elevation_model.py --rebuild`.

**Plausibilitätscheck (im Skript integriert) gegen geloggte GPS-Höhe**
(Kanal `Höhe` im Datalake, 3285 Punkte aus 10 Logs innerhalb der
Kachelabdeckung): GPS-Höhe liegt systematisch **~38,2m ueber** der
DGM-Höhe (Median), Streuung nur ±2,2m. Interpretiert als bekannter
geodätischer Effekt, kein Fehler: Handy-GPS meldet vermutlich
**ellipsoidische Höhe (WGS84)**, das ALS-DGM ist **orthometrische Höhe
(DHHN2016)** - die Differenz entspricht der für die Region typischen
Geoidundulation (~40m). Die geringe Streuung NACH Abzug dieses
Konstant-Offsets bestätigt eine gute räumliche Passung von DGM und
gefahrener Strecke. **Für Höhenänderungen/Steigungen entlang der Strecke
ist das DGM deutlich verlässlicher als die geloggte GPS-Höhe** (Phone-GPS-
Höhe ist bekannt verrauscht) - bei Bedarf spätere Analysen auf DGM-Werte
stützen, GPS-Höhe nur als groben Vergleich nutzen.

**NACHTRAG 29.08.2026 — Brücken-Problem geklärt und gelöst.**

Nutzer-Arbeitshypothese war: die Höhendaten erfassen die Fahrbahn OBEN auf
der Brücke. **Empirisch widerlegt.** Test an echten Brücken (Rohpunktwolke,
nicht nur das gemittelte Gitter):

- An einer Bahnbrücke mit klarer Geländestufe zeigen die als "Boden"
  (Klasse 2) klassifizierten Punkte im Brückenbereich ZWEI getrennte
  Höhen-Cluster (~44,7m Umgebung/Damm vs. ~33,8m Talsohle darunter, Sprung
  über nur ~70m - physikalisch unmöglich als Brückengefälle). Die
  Boden-Klassifizierung folgt dem GELÄNDE UNTER der Brücke, nicht der
  Fahrbahnoberkante. Direkt über der Brücke selbst meist gar keine
  Boden-Punkte (Lücke im Gitter), da die Fahrbahn als künstliches Bauwerk
  aus der Boden-Klasse ausgeschlossen wird.
- Relevanzprüfung gegen die echten Logs: die A111/A10-Anschlussstelle
  (Berlin-Reinickendorf-Bereich) mit Brücken-/Rampenbauwerk wird in 9 von
  21 Logs tatsächlich befahren (GPS-Spur bis auf 8,1m an die
  Brückengeometrie). Dort kleinerer Effekt (kein tiefes Tal), aber
  trotzdem grossteils Lücken (NaN) im Höhenprofil direkt über der Rampe.

**Lösung (Nutzerentscheidung): automatische Brückenerkennung + Interpolation.**
`scripts/elevation_model.py` erweitert:
- `fetch_osm_bridges()`/`build_bridge_cache()`: lädt befahrbare Brücken
  (`highway`+`bridge`=*) über die Overpass-API für die Bounding Box des
  DGM-Gitters, cached als `data/elevation/osm_bridges.json` (aktuell 53
  Brücken-Wege). Wird automatisch bei `--rebuild` mit aufgebaut.
- Neue Methode `ElevationModel.get_elevation_along_track(e, n)`: markiert
  Punkte im 15m-Puffer (`BRIDGE_BUFFER_M`) um eine OSM-Brücke sowie normale
  Datenlücken, interpoliert linear nach zurückgelegter Strecke zwischen
  den nächsten gültigen Nachbarpunkten (kein Extrapolieren an den
  Trajektorien-Rändern), gibt zusätzlich eine `is_interpoliert`-Maske
  zurück. **Für Höhenprofile entlang einer Fahrt ist diese Funktion zu
  verwenden, NICHT `get_elevation()` direkt** (liefert an Brücken sonst
  Lücken oder falsche, zu tiefe Werte) - im Modul-Docstring so vermerkt.
- End-to-End getestet an der A111-Rampe mit echten Log-Koordinaten
  (2026-08-27 170339): rohe Werte an 3 von 4 Punkten NaN, korrigiertes
  Profil glatt und plausibel (42.71 -> 42.04 -> 41.55 -> 41.05 m).

**NACHTRAG 29.08.2026 — zwei offene Lücken bei der Anschlussstelle Stolpe
gefunden (Nutzer hat per Kartenausschnitt nachgefragt, ob Unter-/
Überführungen dort erfasst sind).**

Nutzer fuhr per Screenshot fort: A111 führt an der Anschlussstelle Stolpe
(Ausfahrt 2b) sowie an den Rampen zum Rasthof Stolper Heide über/unter
weitere Straßen. Prüfung ergab **zwei getrennte, beide noch offene
Probleme** - relevant, da **8 von 21 Logs** die Anschlussstelle Stolpe
tatsächlich befahren (GPS-Punkte bis auf 274m an das geschätzte
Interchange-Zentrum E=380900/N=5834800 heran):

1. **Echte Kachel-/Datenlücke (kein Brücken-Problem):** in `höhendaten/`
   fehlen die Kacheln **`33380-5834`, `33380-5835`, `33381-5833`** - genau
   im Bereich der Anschlussstelle/des Rasthofs. Dort existieren aktuell
   GAR KEINE ALS-Rohdaten (weder Boden- noch sonstige Punkte), unabhängig
   von der Brücken-Logik. **Nutzer laedt diese 3 Kacheln bereits nach**
   (von https://data.geobasis-bb.de/geobasis/daten/als/laz/, siehe
   Abschnitt "Quelle für weitere Kacheln" oben) - Downloads liefen beim
   Verfassen dieser Notiz noch, Ergebnis steht noch aus.
2. **Lücke in der automatischen OSM-Brücken-Erkennung:** im
   `osm_bridges.json`-Cache sind ALLE Brücken mit `ref=A 111` auf der
   HAUPTFAHRBAHN (highway=motorway, nicht -link) ausschließlich bei der
   weiter nördlich gelegenen A111/A10-Anschlussstelle (Reinickendorf-
   Bereich, N≈5837300-5840960) - KEINE bei Stolpe (N≈5834800). Nur zwei
   RAMPEN dort sind als Brücke getaggt (`motorway_link`, OSM-IDs
   23391758 und 23391759). Falls die Hauptfahrbahn an der Anschlussstelle
   Stolpe tatsächlich über eine Querstraße führt (im Screenshot als
   Kreuzungsbauwerk bei "2b"/Hennigsdorfer Chaussee erkennbar), fehlt
   dafür in OpenStreetMap ein `bridge`-Tag auf dem Hauptfahrbahn-Way -
   unsere automatische Erkennung (`fetch_osm_bridges`, sucht
   `way["highway"]["bridge"]`) würde diese Stelle daher NICHT erkennen
   und `get_elevation_along_track()` könnte dort einen falschen
   Höhenwert statt einer sauberen Interpolation liefern.

**Nächster Schritt, sobald die 3 Kacheln vorliegen:**
- `höhendaten/` ergänzen, `python scripts/elevation_model.py --rebuild`
  (baut DGM-Gitter UND OSM-Brücken-Cache neu auf).
- Dann gezielt pruefen, ob an der Stolpe-Anschlussstelle (bei der
  Hauptfahrbahn-Kreuzung) im ROHEN Punktwolken-Profil ein aehnlicher
  Bodensprung wie beim Bahnbruecken-Testfall (siehe oben, ~11m Sprung)
  auftritt. Falls ja: die betroffene Stelle fehlt in OSM und muss der
  Bruecken-Liste manuell hinzugefuegt werden (z.B. als zusaetzlicher
  Eintrag in `data/elevation/osm_bridges.json` oder ueber eine
  projektspezifische Ergaenzungsliste im Skript), da sich nicht
  automatisch auf vollstaendiges OSM-Tagging verlassen werden kann.

**NACHTRAG 29.08.2026 — vollstaendige Kachel-Abdeckung ueber ALLE Logs
ermittelt, Speicherstruktur auf kachelbasiert umgestellt, neue Datenluecke
(Berlin) gefunden.**

Nutzer-Auftrag: alle Logs durchsuchen (nicht nur B96/Germendorf) und
fehlende Kacheln nachladen, danach eine erweiterbare, performante
Speicherstruktur bauen.

**Ergebnis der Log-Analyse:** die "ueblichen" Fahrten sind KEIN Ausreisser-
Einzeltrip, sondern eine wiederkehrende grosse Schleife ueber ~30x25km
(E 360-389 km, N 5821-5850 km) - B96/Germendorf ist nur ein Teilstueck
davon. Insgesamt **101 Kacheln** werden gebraucht, um die GPS-Spuren aller
21 Logs mit gueltigen Koordinaten (98.251 Punkte) abzudecken.

**Download-Status:** 74 von 101 Kacheln erfolgreich geladen (Brandenburg-
Quelle, siehe oben). **27 Kacheln liefern eine 404-Fehlerseite statt
Daten** (alle exakt 876 Byte HTML statt ZIP) - geografisch geprueft: lat
52.53-52.62, lon 13.25-13.37, das liegt in **BERLIN** (Nordost-Bereich,
etwa Pankow/Weissensee/Marzahn-Hellersdorf), nicht in Brandenburg. Die
LGB-Quelle deckt nur Brandenburg ab, Berlin ist eigenstaendig.
**Berlin hat ein eigenes, ebenfalls offenes ALS/DGM-Angebot** (bestaetigt
per Websuche): Berlin Geoportal (gdi.berlin.de), gleiches CRS EPSG:25833,
DGM1/DOM1 seit Ende 2021 frei verfuegbar, Punktdichte 5P/m² (aktuell) bzw.
1P/m² (historisch 2008-2012), ABER **andere Kachelgroesse (2x2km statt
1x1km)** und vermutlich ein anderer Download-Mechanismus (Geoportal/
FIS-Broker statt flacher Verzeichnis-Index) - noch NICHT integriert,
braucht eigene Recherche zur genauen Download-URL/Struktur, wenn
gewuenscht. Die 27 Fehlerseiten wurden aus `höhendaten/` entfernt (waren
keine echten Kacheln).
Kleiner Hinweis am Rande: eine vom Nutzer manuell angestossene Kachel
(`33380-5857`) taucht in keinem Log auf - vermutlich Zahlendreher, kein
Blocker.

**Neue Speicherstruktur (kachelbasiert statt ein grosses dichtes Gitter):**
`scripts/elevation_model.py` grundlegend umgebaut:
- Jede Kachel wird EINZELN zu einem 1000x1000-Gitter gerastert und als
  eigene Datei `data/elevation/tiles/<e_km>_<n_km>.npz` gespeichert (statt
  einem gemeinsamen dichten Array ueber die komplette Bounding Box - waere
  bei 101 verstreuten Kacheln unbrauchbar gross geworden, siehe
  Performance-Diskussion oben).
- `build_all_tiles()`: rein additiv/inkrementell - baut nur Kacheln, die
  noch nicht in `tiles/` liegen. Neue Kachel-ZIP dazulegen + `--rebuild`
  reicht, kein Neuaufbau bestehender Kacheln.
- Fehlertolerant: unvollstaendige/beschaedigte ZIP-Dateien (z.B. noch
  laufender Download oder wie hier eine 404-Seite) werden uebersprungen
  und geloggt statt das ganze Skript abzubrechen.
- `ElevationModel` laedt Kacheln jetzt lazy on-demand mit LRU-Cache
  (`tile_cache_size`, Standard 64 Kacheln ~256MB) statt beim Start alles
  ins RAM zu laden - Init jetzt <1ms statt ~0.8s vorher, RAM skaliert mit
  tatsaechlich abgefragten statt allen vorhandenen Kacheln.
- Bilineare Interpolation jetzt pro Kachel (an Kachel-Raendern wird auf
  die naechste Zelle INNERHALB der Kachel geklemmt statt echter
  kachel-uebergreifender Interpolation - Fehler <1m, vernachlaessigbar).
- Oeffentliche API (`get_elevation`, `get_elevation_latlon`,
  `get_elevation_along_track`, Bruecken-Handling) unveraendert - end-to-end
  gegen die alte Implementierung verifiziert (identisches Ergebnis am
  bekannten A111-Bruecken-Testfall).
- Alter Cache `data/elevation/dgm_grid.npz` entfernt (durch `tiles/`
  ersetzt).

**ABSCHLUSS Downloads (29.08.2026, 19:xx Uhr):** 76 von 101 benoetigten
Brandenburg-Kacheln erfolgreich geladen und in `data/elevation/tiles/`
eingebaut (210MB verarbeitete Gitter aus 7,2GB Rohdaten in `höhendaten/`).
Bruecken-Cache final ueber alle 76 Kacheln neu aufgebaut: **209 befahrbare
Bruecken-Wege** (vorher 53 bei nur 15 Kacheln). Init von `ElevationModel`
bei voller Groesse weiterhin 2ms (bestaetigt: Lazy-Loading skaliert wie
gewollt).

**Finaler Plausibilitaetscheck ueber alle Logs:** 71.235 von 98.251
gueltigen GPS-Punkten (72,5%, vorher nur 3,3% mit den ersten 11 Kacheln)
liegen jetzt in der Kachelabdeckung, 20 von 21 Logs betroffen (nur ein Log
faellt komplett in die Berlin-Luecke). Median-Differenz GPS-Hoehe minus
DGM-Hoehe weiterhin stabil bei ~40,8m (vorher 38,2m bei kleinerer
Stichprobe) mit geringer Streuung (±2,9m trotz 6x groesserer, geografisch
verteilterer Stichprobe) - bestaetigt den vermuteten Ellipsoid/
Orthometrisch-Versatz (siehe oben) und die raeumliche Konsistenz des
gesamten Datensatzes.

3 Kacheln (`33380-5832`, `33380-5838`, `33381-5833`) haetten laut
Nutzer-Angabe manuell nachgeladen werden sollen, sind aber noch nicht in
`höhendaten/` angekommen (Stand dieser Notiz) - bei Gelegenheit
nachpruefen/nachlegen + `--rebuild`.

OFFEN:
- Berlin-Luecke (27 Kacheln, siehe oben) schliessen, falls gewuenscht -
  eigene Recherche zur Berlin-Geoportal-Download-URL/Kachelschema (2x2km,
  anderer Mechanismus als Brandenburgs flacher Verzeichnis-Index) noetig.
- Die 3 oben genannten Kacheln nachpruefen, sobald verfuegbar.
- konkrete fachliche Anwendung (Steigungskorrektur fuer a_long im
  Vergleich mit dem Antriebsstrangmodell, spaetere Streckensimulation)
  weiterhin offen, Modul jetzt aber vollstaendig fuer den Massstab
  vorbereitet.

**Quelle für weitere Kacheln:** https://data.geobasis-bb.de/geobasis/daten/als/laz/
- Offener, flacher Verzeichnis-Index, ~500+ Kacheln für ganz Brandenburg,
  gleiches Namensschema (`als_<E_km>-<N_km>.zip`) wie die bereits
  vorhandenen 11. Lizenz: Data Lizenz Deutschland - Namensnennung 2.0.
- Bei Bedarf (neue Logs/Passagen außerhalb der aktuellen Abdeckung):
  benötigten Kachelnamen aus UTM33-Koordinaten ableiten (E/N auf km
  abrunden, Format wie bei den Bestandskacheln, z.B. E=378500,
  N=5839200 -> `als_33378-5839.zip`), Datei von obiger URL laden und
  nach `höhendaten/` legen, dann `elevation_model.py --rebuild`.

## Bereifung (NEU, 30.08.2026)

**Nankang NS-R2, 205/40/R17** (semi-slick Strecken-/Trackday-Reifen,
Nutzerangabe). Cross-Check ueber die Reifengroesse: statischer Radius aus
Breite/Seitenverhaeltnis/Felge = 82,0mm Seitenwand + 431,8mm Felgendurchmesser
=> **0,2979 m** - passt sehr gut zu den im Modell verwendeten Werten
(r_dyn=0,2985m in `drivetrain_model_validation.py`, r_ref=0,2997m im
externen Dokument, Abweichung jeweils nur wenige mm) - unabhaengige
Bestaetigung der Radradius-Annahme.

Relevant fuer die offene Kurvengeschwindigkeits-/Quergrip-Frage (siehe
"Naechste Schritte" Diskussion 30.08.2026): ein Semi-Slick wie der NS-R2
hat deutlich hoehere Grenz-Querbeschleunigung als ein normaler Strassenreifen
(grobe Literatur-Einordnung dieser Reifenklasse: oft 1,0-1,3g im Trockenen,
je nach Fahrbahn/Temperatur/Reifenzustand) - falls eine grobe, literaturbasierte
Kurvengeschwindigkeitsgrenze (Plan-Option 2/4) umgesetzt wird, sollte sie auf
dieser Reifenklasse basieren, NICHT auf generischen Strassenreifen-Werten wie
bisher pauschal angenommen. Reifenzustand (Alter/Profiltiefe) und Fahrbahn-
temperatur zum Zeitpunkt der Fahrten sind nicht dokumentiert - bei Bedarf
beim Nutzer nachfragen, bevor ein konkreter mu-Wert festgelegt wird.

**Reifenzustand/Betrieb (Nutzerangabe, 30.08.2026):**
- DOT-Alter: 2 Jahre.
- Profiltiefe: ca. 4mm.
- Kaltdruck: 1,8-1,85 bar, pendelt sich im Betrieb auf 2,0-2,1 bar ein.
  Ueber 2,1 bar wird nachjustiert, da der Grip in Kurven dann spuerbar
  nachlaesst.
- Cross-Check gegen die geloggten `TirePressureWheel1-4`-Kanaele (8 Logs):
  Mittelwerte 189-202 kPa (1,89-2,02 bar), Maxima 211-220 kPa (2,11-2,20 bar)
  - deckt sich gut mit der beschriebenen Praxis (vereinzelt leicht ueber
  2,1 bar, bevor nachjustiert wird).
- Fahrbahntemperatur zu den einzelnen Fahrten weiterhin nicht dokumentiert.

## Fahrzeuggewicht

**Leergewicht (ohne Fahrer, ohne Benzin): 1073 kg.** Das im externen
Fahrleistungsmodell verwendete Gesamtgewicht (1180.705 kg, siehe
"Cross-Validierung" oben) ist demnach ein spezifischer Beladungszustand
(Fahrer+Tank) und NICHT identisch mit dem Leergewicht.

**WICHTIG fuer neue Logs: bei Eintreffen eines neuen Logs (egal ob
automatisch per Drive-Ueberwachung oder manuell nachgereicht) beim
Nutzer nach dem tatsaechlichen Gesamtgewicht bei dieser Fahrt fragen**
(Fahrer + Tankfuellstand/Zuladung), bevor gewichtsabhaengige Berechnungen
(z.B. `drivetrain_model_validation.py`, Beschleunigungsvergleiche) mit
diesem Log durchgefuehrt werden — Gewicht schwankt je nach Fahrer/
Tankstand und ist nicht automatisch aus den Logs ablesbar.

## 0-Vmax Fahrleistungssimulation (NEU, 29.08.2026)

Neues Skript `scripts/performance_simulation.py` - Nutzeranfrage: "haben
wir ein Modell um einen Vollgas-Run zu simulieren, 0 bis Vmax?". Baut
NICHT auf einer neuen Physik auf, sondern integriert das bereits in
`drivetrain_model_validation.py` validierte Beschleunigungsmodell
(Volllast-Drehmomentkurve, Getriebe-/Achsuebersetzung, CdA, Crr, eta,
Masse - Werte importiert, nicht dupliziert) ueber die Zeit, inkl.
Schaltlogik (optimaler Schaltpunkt = naechster Gang beschleunigt
staerker, mit Redline-Sicherheitsnetz bei 7500 U/min) und den
gangspezifischen ATTACK-Zugkraftunterbrechungen aus dem externen
Dokument (0.15/0.15/0.17/0.23/0.25s).

Rechnet zwei Szenarien: "raw" (reines Modell) und "bias-korrigiert"
(Antriebskraft skaliert mit dem in `drivetrain_model_validation.py`
gemessenen Median-Verhaeltnis gemessen/Modell = 0.92, aus 44 echten
Volllast-Segmenten).

Erste Ergebnisse:
- raw: 0-100 5.80s, 0-200 24.95s, Vmax 231.7 km/h
- bias-korrigiert: 0-100 6.31s, 0-200 28.46s, Vmax 222.8 km/h

Bemerkenswert: die bias-korrigierte 0-100-Zeit (6.31s) liegt sehr nah
an der im externen Dokument genannten historischen Simulation (6.29s,
aus separatem Excel-Modell, nicht als Kalibrierziel gedacht) - gute
Plausibilisierung, auch wenn methodisch unabhaengig entstanden.

BEKANNTE EINSCHRAENKUNG (bewusst nicht eingebaut): kein Traktions-/
Launch-Modell (Reifenhaftung bei niedriger Geschwindigkeit in Gang 1).
Das externe Dokument nennt einen historischen Launch-Wert (mu_eff=0.88,
Traktionsfaktor Gang 1 = 0.90, mittlere Launch-Beschleunigung 5.54 m/s²)
explizit als "historische Evidenz, nicht aus der aktuellen IMU-Methode
neu bestaetigt" - um Diagnose- und Simulationslogik nicht zu vermischen,
NICHT eingebaut. 0-100-Zeit dadurch ggf. leicht optimistisch.

Outputs: `results/performance_simulation.png` (v-t-Kurven beide
Szenarien mit Schaltmarkern), `results/performance_simulation_summary.json`
(Kennzahlen + Schaltpunkte), `results/performance_simulation_raw.csv` /
`_bias-korrigiert.csv` (volle Zeitreihen).

Aufruf: `.venv/bin/python scripts/performance_simulation.py`

OFFEN: kein Vergleich gegen echte volle 0-Vmax-Fahrten (die realen Logs
enthalten keine durchgehende Vollgasfahrt von 0 bis Vmax ueber alle
Gaenge, nur einzelne Volllast-Segmente) - Validierung bleibt indirekt
ueber die bereits geprueften Einzelsegmente.

## Gang-6-/Vmax-Validierung mit Gefaellekorrektur (NEU, 29.08.2026)

Neues Skript `scripts/top_speed_validation.py` - Nutzeranfrage: "suche in
den Logs nach Situationen, bei denen wir im 6. Gang bis Vmax fahren,
validiere den Bereich im Modell, beachte ggf. das Gefaelle." Anders als
der bestehende Volllast-Check (kurze Segmente, alle Gaenge, Gang 6 dort
nur n=3) sucht dieses Skript gezielt LANGE Gang-6-WOT-Phasen (>=3s,
muss mind. einmal 170 km/h erreichen), ohne die Anforderung "Geschwindigkeit
steigt monoton" (fuer Vmax-Naehe irrelevant). Gemessene Beschleunigung
per linearer Regression von VehicleSpeed(t) ueber das ganze Segment
(robuster als Endpunkt-Differenz bei kleinen Beschleunigungen).

Steigungskorrektur nutzt das bestehende DGM (`elevation_model.py`,
bruecken-sicheres `get_elevation_along_track`): GPS-Fixe im Segment
(gefiltert auf `Horz Genauigkeit` <=20m), mittlere Steigung aus
Hoehenaenderung/GPS-Strecke, als Gravitationsterm
`a_gravity = -g*grade_frac` zur Modellbeschleunigung addiert.

**Ergebnis: 6 Segmente in 3 Logs gefunden** (2026-08-21 160418: 2x,
2026-08-21 211326: 3x, 2026-08-26 154244: 1x), Dauer 7-24s, Tempo 135-201
km/h, alle innerhalb der DGM-Kachelabdeckung (Steigung -1.36% bis +0.89%).
**Kein Segment zeigt tatsaechlich Stillstand der Beschleunigung (echtes
Vmax/Plateau)** - alle noch mit +0.46 bis +0.90 m/s² im Bereich 135-201
km/h unterwegs, die Logs enthalten also keine sustained Top-Speed-Phase
nahe des geschaetzten Vmax (~215-225 km/h bias-korrigiert). Modell bleibt
damit indirekt validiert (ueber die Beschleunigungsrate in diesem
Geschwindigkeitsbereich), Vmax selbst weiterhin nicht direkt durch einen
Plateau-Messwert bestaetigt.

Bias-korrigiertes Modell trifft die gemessene Beschleunigung in diesem
Bereich gut (RMSE ohne Gefaellekorrektur 0.195 m/s², **mit
Gefaellekorrektur 0.164 m/s² - 16% Verbesserung**). Bei staerkerem
Gefaelle (-1.36% beim 211326-Segment) ist der Effekt am deutlichsten:
Modell ohne Korrektur unterschaetzt dort die Beschleunigung deutlich
(0.481 vs. gemessen 0.844 m/s²), mit Korrektur naeher dran (0.615 m/s²).
Grade-adjustierte Vmax-Schaetzungen liegen je nach Segment zwischen 215.9
und 232.9 km/h (bias-korrigiert) - Streuung spiegelt die
Steigungsabhaengigkeit wider, nicht Modellunsicherheit.

Outputs: `results/top_speed_validation.png` (Streudiagramm Modell vs.
Messung, mit/ohne Gefaellekorrektur), `results/top_speed_validation_summary.json`
(alle Segmente inkl. Steigung, Vmax-Schaetzungen raw+bias-korrigiert).

Aufruf: `.venv/bin/python scripts/top_speed_validation.py`

OFFEN: keine echten Plateau-Segmente vorhanden, um Vmax selbst direkt zu
pruefen - dafuer waere eine laengere Autobahn-Vollgasfahrt noetig, bei der
die Geschwindigkeit ueber mehrere Sekunden nicht mehr steigt.

## Echte Vmax-Plateaus gefunden - 08.08.2026-Logs (NEU, 30.08.2026)

Nutzer-Vermutung: "in den Logs vom 08.08.2026 waren Vmax-Fahrten dabei"
- bestaetigt. Die 4 Logs dieses Tages (`CSVLog_20260808_142621/145611/
214117/232259`) gehoeren zum kurzen CSVLog_*-Format (nur 7 Kanaele:
BFP_PRE_MZ/Breite/ETC_ACT/EngineRPM/Laenge/TM_GEST/VehicleSpeed - KEIN
APP, KEIN AFR_MZ) und wurden deshalb vom bisherigen `top_speed_validation.py`
komplett uebersprungen (WOT-Erkennung brauchte bisher APP oder AFR_MZ).

**Fix:** `top_speed_validation.py` um dritten WOT-Fallback "ETC_only"
erweitert (ETC_ACT>=85°, strenger als der 80°-Schwellwert des
ETC+Lambda-Fallbacks, da hier keine Lambda-Bestaetigung moeglich ist -
Segmente klar als "Diagnosewert, nicht unabhaengig bestaetigt"
gekennzeichnet, gemaess bestehender Nutzer-Vorgabe "ETC allein ist kein
verlaesslicher WOT-Nachweis"). Damit von 6 auf **68 Segmente in 13 Logs**
(vorher nur Logs mit APP/AFR_MZ).

**Zwei echte Vmax-Plateaus gefunden** (Beschleunigung ueber viele Sekunden
praktisch null, nicht nur ein weiteres Beschleunigungssegment):
- `CSVLog_20260808_145611`, t=875-922s (**47s**), v=226-236 km/h,
  a_gemessen=+0.037 m/s² (praktisch flach).
- `CSVLog_20260808_214117`, t=861-954s (**93s!**), v=202-230 km/h,
  a_gemessen=+0.052 m/s².

Beide gegengeprueft: ETC_ACT=86° (voll offen, exakt der dokumentierte
ETC_open-Wert) durchgehend, TM_GEST=6, EngineRPM 5900-6050 (mitten im
gut abgesicherten Drehmomentkurven-Bereich, keine Randextrapolation),
danach klarer Gaswegnahme-Abfall (ETC->~10-11°) am Ende - eindeutig ein
gezielter Vmax-Testlauf, kein Zufallsbefund. Zusaetzlich per GPS-Positions-
differenzierung gegengeprueft (unabhaengig von OBD): mittlere GPS-Speed
229.3 bzw. 219.9 km/h vs. OBD-Mittel 232.5 bzw. 224.0 km/h - im Rahmen
des erwarteten 1Hz-GPS-Rauschens konsistent, kein Hinweis auf OBD-
Messfehler/Artefakt.

**Wichtiger, ungeloester Befund:** diese sustained 226-236 km/h liegen
UEBER dem RAW-Modell-Vmax (231.7 km/h flach, aus `performance_simulation.py`)
und deutlich ueber dem bias-korrigierten Vmax (~222-223 km/h) - also
GENAU ENTGEGENGESETZT zum bekannten ~8%-Bias aus den mittleren
Geschwindigkeitsbereichen (dort ueberschaetzt das Modell die Beschleunigung).
Der pauschale Bias-Korrekturfaktor 0.92 extrapoliert demnach NICHT
zuverlaessig in den extremen Vmax-Bereich (>220 km/h) - moeglicherweise
skaliert der zugrundeliegende Fehler (CdA/eta/Drehmomentkurve) nicht
gleichfoermig mit v². OFFENER PUNKT, nicht weiter aufgeloest.

**Einschraenkung:** beide Plateaus liegen GENAU in der bekannten
Berlin-DGM-Luecke (145611: E382-385/N5839-5840, Pankow/Weissensee-Bereich;
214117: E395-400/N5830-5832, weiter oestlich Richtung Marzahn-Hellersdorf/
Kaulsdorf) - keine Steigungskorrektur moeglich, ein Gefaelle-Beitrag zur
hohen Geschwindigkeit kann NICHT ausgeschlossen werden. Bei 47s bzw. 93s
Dauer ueber mehrere Kilometer waere ein einzelnes, gleichmaessiges Gefaelle
ueber die GESAMTE Strecke zwar möglich aber nicht der einzig plausible
Erklaerungsansatz - bleibt offen, haengt am bekannten offenen Punkt
"Berlin-Luecke schliessen" (siehe Hoehendaten-Abschnitt oben).

Weitere Beobachtung: `CSVLog_20260731_151724` allein enthaelt 24 der 68
Segmente - offenbar eine dedizierte, laengere Autobahn-Session mit
wiederholten Volllastphasen bei 180-230 km/h (keine DGM-Abdeckung dort
auch, gleiche Einschraenkung).

Outputs: `results/top_speed_validation_summary.json` (jetzt 68 statt 6
Segmente), `results/top_speed_20260808_plateaus.png` (v(t) + ETC_ACT(t)
fuer die zwei Plateau-Logs, Ad-hoc-Plot, nicht Teil eines Skripts).

## Nachtrag: Gefaellekorrektur der 08.08.2026-Plateaus + wichtiger Bias-Befund bei Vmax (30.08.2026)

Nutzer-Nachfrage: "schau nochmal ins Webverzeichnis, ob es fuer die Bereiche
wirklich keine Kacheln gibt" (Zweifel an der vorherigen "Berlin-Luecke"-
Einschaetzung fuer die beiden Vmax-Plateau-Logs). **Berechtigter Zweifel:**
direkte HTTP-Pruefung (curl -I) gegen `data.geobasis-bb.de` zeigte, dass
**9 von 13 fuer die beiden Plateaus benoetigten Kacheln tatsaechlich
existieren** und nur schlicht noch nicht heruntergeladen waren - nur 4
Kacheln (`33395-5832`, `33396-5831`, `33396-5832`, `33397-5831`, echtes
Berlin-Gebiet) sind tatsaechlich nicht vorhanden. Die vorherige pauschale
Einschaetzung "beide Plateaus liegen in der Berlin-Luecke" war also nur
fuer einen Teil zutreffend.

Alle 9 verfuegbaren Kacheln nachgeladen (parallele Downloads noetig, sehr
langsame Verbindung zum LGB-Server an diesem Abend, ~1-2 Stunden fuer
~640MB), `elevation_model.py --rebuild` gelaufen (jetzt 88 statt 79
Kacheln, 618 statt 209 Bruecken-Wege), `top_speed_validation.py` erneut
gelaufen: **18 statt 13 Segmente jetzt mit Gefaellekorrektur** (RMSE
0.161 statt 0.177 m/s², 9% Verbesserung).

**Beide 08.08.2026-Plateaus jetzt (teilweise) gefaellekorrigiert:**
- `CSVLog_20260808_145611`, t=875-922s (47s, 226-236 km/h): Gefaelle
  -0.42% (leicht abschuessig). Gemessen +0.037 m/s².
- `CSVLog_20260808_214117`, t=861-954s (93s, 202-230 km/h): Gefaelle
  +0.11% (praktisch eben). Gemessen +0.052 m/s².
  (Einzelne Teilabschnitte dieses langen Laufs, z.B. t=660-701s, liegen
  weiterhin in der echten Berlin-Restluecke - "unbekannt".)

**WICHTIGER, UEBERGREIFENDER BEFUND:** Bei beiden Plateaus (und
systematisch ueber alle 33 Segmente mit v_max>=215 km/h) trifft das
**RAW-Modell (Bias-Faktor 1.0, KEINE Korrektur) die Realitaet deutlich
besser** als das bias-korrigierte Modell (Faktor 0.92):
- RAW: mittlere Abweichung gemessen-Modell nur -0.018 m/s², RMSE 0.083 m/s².
- Bias-korrigiert (0.92): mittlere Abweichung +0.101 m/s² (Modell
  UNTERSCHAETZT jetzt systematisch), RMSE 0.130 m/s² - schlechter als roh!

**Der 8-9%-Bias-Korrekturfaktor aus den mittleren Geschwindigkeitsbereichen
(130-200 km/h, `drivetrain_model_validation.py`) extrapoliert NICHT in
den Vmax-Bereich (>215 km/h) - dort ist das unkorrigierte Modell die
bessere Vorhersage.** Wahrscheinliche Erklaerung: der zugrunde liegende
Fehler (vermutlich CdA und/oder eta, siehe Cross-Validierungs-Abschnitt
oben) skaliert nicht gleichfoermig mit v² ueber den gesamten
Geschwindigkeitsbereich - ein einzelner globaler Korrekturfaktor ist also
eine Vereinfachung, die im Hochgeschwindigkeitsbereich nicht mehr passt.
**Konsequenz fuer `performance_simulation.py`:** die dortige
"bias-korrigiert"-Kurve ist im Bereich nahe Vmax vermutlich zu
pessimistisch - die "raw"-Kurve duerfte dort naeher an der Realitaet
liegen. Nicht weiter aufgeloest (kein geschwindigkeitsabhaengiger
Korrekturfaktor implementiert), aber wichtiger Caveat fuer jede
Vmax-Aussage aus dem Modell.

**Nebenrecherche: gibt es ALS-Daten auch fuer Berlin?** Ja, aber anders
organisiert. Offiziell dokumentiert (fbinter.stadt-berlin.de/fb_daten/
beschreibung/dgm1.html): DGM1, XYZ-Format (nicht LAZ-Punktwolke wie
Brandenburg), 2x2km-Kacheln, EPSG:25833, Hoehenbezug DHHN2016 (gleiches
System wie Brandenburg - kompatibel). Der dokumentierte "Downloaddienst
(Atom)" liess sich per automatisierter Suche NICHT auffinden (mehrere
plausible URLs probiert, alle 404, auch der interaktive Karten-Viewer
gdi.berlin.de/view/dgm1 zeigt keinen offensichtlichen Bulk-Download-Button).
**Gefunden und live getestet: WMS-Dienst
`https://gdi.berlin.de/services/wms/dgm1` (Layer `c_dgm1`)
mit funktionierender GetFeatureInfo-Abfrage** - liefert echte Hoehenwerte
per Einzelpunkt-Abfrage (getestet: 48.04m bei E=395500/N=5830500, mitten
in der bekannten Luecke). Waere als Live-Fallback fuer einzelne
GPS-Punkte in der Berlin-Luecke nutzbar (kein Bulk-Download noetig, aber
Netzwerkaufruf pro Punkt/Batch statt lokaler Daten) - NICHT implementiert,
nur als moegliche Option dokumentiert, falls die verbleibende
Rest-Luecke (4 Kacheln um E395-397/N5831-5832) spaeter geschlossen werden
soll.

Outputs: `results/top_speed_validation_summary.json`/`.png` aktualisiert
(68 Segmente, 18 mit Gefaellekorrektur).

## Berlin-Hoehendaten-Fallback implementiert (30.08.2026)

Nutzer-Auftrag: "nutze zukuenftig, wenn noetig, die Moeglichkeit auch
Hoehendaten aus Berlin abzufragen. speicher Abfragen aus Berlin ab und
pruefe vor Abfragen, ob Daten fuer die Koordinaten schon im Speicher
sind." Umgesetzt in `scripts/elevation_model.py`:

- Neue Funktion `query_berlin_wms(e, n)`: Live-GetFeatureInfo-Abfrage
  gegen `https://gdi.berlin.de/services/wms/dgm1` (Layer `c_dgm1`,
  siehe Nebenrecherche oben) fuer einen einzelnen UTM33-Punkt. Faengt
  Netzwerkfehler/Timeouts ab (liefert NaN statt Absturz).
- Neuer persistenter Cache `data/elevation/berlin_wms_cache.json`
  (Key: auf Meter gerundete "E,N"-Koordinate). **Vor jeder Live-Abfrage
  wird zuerst der Cache geprueft** - auch NaN-Ergebnisse (Punkt liegt
  auch ausserhalb der Berliner Abdeckung) werden gecacht, um sinnlose
  wiederholte Abfragen dafuer zu vermeiden.
- Automatisch eingebunden in `ElevationModel.get_elevation()`: fuer
  jeden Punkt, der nach der lokalen Kachel-Abfrage NaN bleibt, wird
  (innerhalb eines groben Sicherheitsrahmens um Berlin/Umgebung,
  `BERLIN_WMS_BBOX_E/N`) automatisch der Berlin-Fallback versucht - keine
  Aenderung an aufrufendem Code noetig, `get_elevation_along_track()`
  profitiert automatisch mit (Bruecken-Handling bleibt unveraendert
  wirksam).

Getestet: Live-Abfrage an einem bekannten Luecken-Punkt (E=395500,
N=5830500) liefert 48.06m (0.23s), zweite Abfrage aus Cache (0.000s,
persistiert ueber Prozessgrenzen). `top_speed_validation.py` erneut
gelaufen: **20 statt 18 Segmente jetzt mit Gefaellekorrektur** (RMSE
0.158 statt 0.161 m/s², kleine weitere Verbesserung). Cache enthaelt
nach diesem Lauf 199 abgefragte Punkte, davon 81 mit echtem Berlin-
Hoehenwert (Rest NaN - liegt ausserhalb auch der Berliner Abdeckung,
z.B. andere, noch unbekannte Brandenburg-Luecken wie die Kacheln um
E387-390/N5838 in dieser Route, die weder lokal noch bei Berlin
vorliegen und ein eigener, unabhaengiger offener Punkt bleiben).

## Zwei weitere 31.07.2026-Logs nachgeladen (30.08.2026)

Nutzer-Hinweis: "ich habe dir weitere csv vom 31.07. ins Googledrive
gelegt". Gefunden: `CSVLog_20260731_090658.csv` (945KB, ~5:06h Fahrzeit,
Start 09:06:59) und `CSVLog_20260731_141854.csv` (69KB, Start 14:18:55) -
beide vorher nicht lokal vorhanden (4 andere 31.07.-Logs waren es schon).
Per Google-Drive-Tool heruntergeladen - **Stolperstein: `download_file_content`
liefert den Inhalt base64-kodiert, nicht als Klartext** (trotz
`text/comma-separated-values`-MIME-Type) - beim ersten Versuch versehentlich
direkt als Text gespeichert (0 Zeilen, eine lange Base64-Zeile), per
Nachtraeglichem `base64.b64decode()` korrigiert, Dateigroessen stimmen jetzt
exakt mit den Drive-Originalen ueberein. **Fuer kuenftige Downloads ueber
dieses Tool beachten: IMMER base64-dekodieren.**

`build_datalake.py` neu gelaufen: **56 Logs (vorher 54), 13.024.577
Messwerte, 0 UNMAPPED**. `drivetrain_model_validation.py`: unveraendert 44
Volllast-Segmente (die 2 neuen Logs liefern keine APP/AFR_MZ-Daten fuer
diesen Check). `top_speed_validation.py`: **83 statt 68 Segmente** (15
neue, v.a. aus dem 5-stuendigen 090658-Log) - RMSE weiterhin stabil
(0.158 m/s² mit Gefaellekorrektur).

## Vollstaendige Kachel-Luecken-Analyse (30.08.2026)

Nutzerfrage: "welche Kacheln fehlen jetzt noch?" Alle GPS-Punkte im
gesamten Datalake (56 Logs, ~146.000 Punkte) gegen die 88 lokal
vorhandenen Kacheln geprueft.

**Wichtiger Nebenbefund:** ein Teil der Logs (`CSVLog_20260731_151724`,
`_175702`, `_061727`, `_070928`, `CSVLog_20260729_171909`) faehrt weit
ausserhalb der ueblichen Berlin-Brandenburg-Schleife - GPS-Koordinaten
reichen bis lat~46.4/lon~11.2 (**Suedtirol/Alpenraum**) ueber Bayern
zurueck nach Brandenburg. Das war vorher als "eine lange Autobahn-Session"
missgedeutet - tatsaechlich eine laengere Urlaubsfahrt. Fuer diesen Bereich
waeren komplett andere Geodatenquellen noetig (Bayern/Oesterreich/Suedtirol
haben eigene Portale) - AUSSERHALB des Betrachtungsbereichs dieser Analyse,
nicht weiter verfolgt ausser bei Bedarf.

**Kernregion (E350-410km/N5810-5855km, die eigentliche Berlin-Brandenburg-
Schleife):** 116 von insgesamt benoetigten Kacheln fehlen noch lokal.
Automatisch gegen den neuen Berlin-WMS-Fallback geprueft: **54 davon sind
durch die Berlin-Integration bereits automatisch abgedeckt** (kein
Download noetig, wird live+gecacht abgefragt, siehe Abschnitt oben).
**Ergebnis der Pruefung gegen den LGB-Brandenburg-Server
(`data.geobasis-bb.de`, HTTP-HEAD auf alle 62): ALLE 62 sind HTTP 200 -
verfuegbar, nur noch nicht heruntergeladen. 0 echte 404 in der
Kernregion.** Vollstaendiges Bild ueber alle 116 fehlenden Kacheln der
Kernregion:
- 54 durch den neuen Berlin-WMS-Fallback bereits automatisch abgedeckt
  (kein Download noetig).
- 62 bei der LGB Brandenburg verfuegbar, aber noch nicht heruntergeladen
  (Liste: 383-5850, 383-5847, 382-5848, 384-5850, 379-5840, 381-5846,
  382-5849, 387-5838, 388-5838, 380-5840, 381-5840, 389-5838, 381-5848,
  382-5846, 381-5849, 386-5839, 361-5820, 361-5816, 361-5817, 361-5819,
  363-5832, 365-5838, 362-5826, 365-5837, 381-5850, 359-5813, 360-5815,
  362-5825, 364-5834, 359-5810, 359-5812, 361-5818, 362-5827, 364-5835,
  365-5841, 386-5838, 359-5811, 361-5823, 362-5829, 362-5830, 365-5839,
  365-5840, 363-5833, 362-5828, 362-5822, 383-5848, 360-5814, 390-5838,
  365-5836, 382-5847, 362-5821, 383-5849, 361-5824, 362-5831, 363-5831,
  359-5814, 362-5824, 361-5815, 361-5821, 364-5836, 364-5833, 361-5822 -
  Format `<e_km>-<n_km>`, Praefix `als_33` + Suffix `.zip` fuer die
  Download-URL).
- **0 Kacheln echt fehlend (404) in der Kernregion** - Brandenburg +
  Berlin zusammen decken die gesamte Kernregion lueckenlos ab.

Noch nicht heruntergeladen (Nutzer-Entscheidung offen, ob gewuenscht -
waere ~6-7GB bei den bisher beobachteten Kachelgroessen, langsame
Verbindung an diesem Abend beachten).

## Suedtirol-Rueckfahrt 31.07.2026 - Vollanalyse ueber Nacht (30.08.2026)

Nutzer-Auftrag (Nacht-Session, Nutzer nicht verfuegbar, "folge deiner
Empfehlung" bei offenen Fragen): Hochgeschwindigkeitsabschnitte der
Suedtirol-Heimfahrt analysieren, dafuer wenn moeglich echte Hoehendaten
beschaffen (mit Quellenangabe, keine erfundenen Werte), untersuchen warum
das Antriebsstrangmodell unter 200 km/h zu optimistisch ist, und das
Modell an die Realdaten annaehern.

### Fahrzeugmasse fuer diese Fahrt

**1299 kg** = Leergewicht 1073kg + Fahrer **86kg** (nutzerbestaetigt
30.08.2026, ab jetzt Standard-Fahrergewicht bis anderslautende Angabe,
siehe Memory) + Beifahrer 70kg (nutzerangabe) + Gepaeck 50kg
(Nutzerangabe) + Tank **~20kg** (ANNAHME: halb voll im Mittel ueber die
~1600km-Fahrt, NICHT bestaetigt - groesste verbleibende Unsicherheit
dieser Massenschaetzung). Betrifft alle 7 Logs dieser Fahrt (29./31.07.2026:
`CSVLog_20260729_171909`, `CSVLog_20260731_061727/070928/090658/141854/
151724/175702`) - in `top_speed_validation.py` als `SUEDTIROL_TRIP_LOGS`/
`SUEDTIROL_TRIP_MASS_KG` hinterlegt, `performance_simulation.accel()` jetzt
mit optionalem `mass_kg`-Parameter.

### Streckenverlauf (aus GPS rekonstruiert)

Suedtirol/Bozen-Gebiet (29.-31.07. vormittags, lat~46.4-46.6, nur Landstrasse/
Ortsverkehr, v_max 106 km/h) -> Brenner/Inn-Tal Oesterreich (keine Segmente
gefunden - 130 km/h-Limit macht hohe WOT-Geschwindigkeiten dort unplausibel/
nicht vorhanden) -> Bayern (ab Kufstein-Grenze, lat>47.6) -> Thueringen ->
Sachsen-Anhalt -> Brandenburg/Berlin. **Alle 58 gefundenen Hochgeschwindig-
keits-WOT-Segmente liegen in Deutschland** (lat>=47.6), keine in Oesterreich/
Suedtirol.

### Hoehendaten-Quellen fuer die neuen Bundeslaender (NEU in `elevation_model.py`)

Erweiterung um `get_elevation_germany_wide(lat, lon)` - Dispatcher, der der
Reihe nach Brandenburg-Kacheln, Berlin-WMS (bestehend), dann NEU Bayern,
Thueringen, Sachsen-Anhalt probiert, liefert `(Hoehe, Quelle)`:

| Bundesland | Mechanismus | Format/Aufloesung | CRS | Quelle/Lizenz |
|---|---|---|---|---|
| Bayern | Datei-Download on-demand (`load_bavaria_tile`), 1km-Kacheln, lokal in `data/elevation/bayern_tiles/` | GeoTIFF, DGM1 (1m) | EPSG:25832 | `download1.bayernwolke.de/a/dgm/dgm1/<e>_<n>.tif`, Bayerische Vermessungsverwaltung, frei ohne Anmeldung. Tile-Liste per Metalink verifiziert: `geodaten.bayern.de/odd/a/dgm/dgm1/meta/metalink/09.meta4` |
| Thueringen | Datei-Download on-demand (`load_thueringen_tile`), 1km-Kacheln, lokal in `data/elevation/thueringen_tiles/` | ZIP mit XYZ-Text, DGM2 (2m, KEIN DGM1 oeffentlich per Direktlink gefunden), Jahrgang 2010-2013 | EPSG:25832 | `geoportal.geoportal-th.de/hoehendaten/DGM/dgm_2010-2013/dgm2_<e>_<n>_1_th_2010-2013.zip`, gefunden ueber den Download-Client `geoportal.geoportal-th.de/gaialight-th/_apps/dladownload/dl-dhm.html` (Netzwerk-Requests inspiziert), Datenlizenz Deutschland - Namensnennung 2.0 |
| Sachsen-Anhalt | LIVE Web Coverage Service (WCS 2.0.1), gecacht in `data/elevation/sachsen_anhalt_wcs_cache.json` | GeoTIFF (Multipart-MIME-Antwort, manuell geparst), DGM1 (1m) | EPSG:25832 | `geodatenportal.sachsen-anhalt.de/ows_INSPIRE_LVermGeo_ATKIS_EL_DGM_WCS`, Coverage "Coverage1", LVermGeo Sachsen-Anhalt. **Kein praktikabler Bulk-Download gefunden** (DGM1 nur als 4 Gesamt-Kacheln je 8-11GB fuer das ganze Land - deshalb WCS statt Datei-Download) |
| Brandenburg/Berlin | wie bisher | LAZ-Punktwolke / WMS | EPSG:25833 | siehe oben im Dokument |

Fuer Bayern/Thueringen: 30 bzw. 25 (von 27, 2 lagen tatsaechlich noch in
Bayern) Kacheln gezielt fuer die benoetigten Streckenabschnitte
heruntergeladen (nicht das ganze Bundesland). rasterio neu in `.venv`
(fuer GeoTIFF-Lesezugriff).

### Bruecken-/Ueberfuehrungsproblem NEU aufgetreten - nur teilweise geloest

Wie schon in Brandenburg bekannt (Anschlussstelle Stolpe): das DGM zeigt an
Bruecken/Talquerungen das GELAENDE UNTER der Bruecke, nicht die Fahrbahn.
Fuer Bayern/Thueringen/Sachsen-Anhalt gibt es KEINE dedizierte Kachel-eigene
Bruecken-Logik wie fuer Brandenburg - stattdessen in `top_speed_validation.py`
zwei generische Schutzmassnahmen ergaenzt:
1. **OSM-Bruecken-Cache fuer den ganzen Fahrtkorridor** (lat/lon-basiert,
   bundeslandunabhaengig, `data/elevation/osm_bridges_trip_corridor.json`,
   75.134 Bruecken-Wege, bbox lat 47.5-52.9/lon 11.0-13.2) - deckt aber NUR
   in OSM als `bridge=yes` getaggte Wege ab (bekanntlich unvollstaendig).
2. **Physikalischer Plausibilitaetsfilter** (`STEP_GRADE_MAX=15%`): ein
   Hoehensprung zwischen zwei aufeinanderfolgenden ~1Hz-GPS-Punkten, der
   einer Steigung >15% entspraeche, ist bei einer echten Autobahnfahrt
   physikalisch ausgeschlossen -> wird als Bruecken-Artefakt erkannt,
   Segment wird am Sprung getrennt und nur der laengste zusammenhaengende
   Abschnitt behalten (`MIN_CLEAN_RUN_FRACTION=60%` sonst komplett verworfen).
   Empirisch bestaetigt: ein Sprung 669.75m->612.48m innerhalb von nur
   ~40m Fahrstrecke (Bayern, t=10854-10855s in `CSVLog_20260731_090658`) -
   eindeutig eine Talbruecke, keine reale Steigung.

**Trotz beider Massnahmen bleibt Bayern-DGM1 in diesem Datensatz
unzuverlaessig**: RMSE mit Gefaellekorrektur 0.256 m/s² vs. 0.146 m/s² ohne
(also schlechter!) ueber 12 Segmente, grade range weiterhin -5.9% bis +5.1%
- vermutlich weitere, von unseren Filtern nicht erkannte Bruecken/
Ueberfuehrungen ODER genuin steile, aber kurze Alpenvorland-Abschnitte, die
sich mit den verfuegbaren Mitteln nicht zuverlaessig von Artefakten trennen
lassen (das Bayern-Streckenstueck liegt in echtem Mittelgebirgs-/
Alpenvorland-Gelaende, anders als das flache Brandenburg/Sachsen-Anhalt).
**Ergebnis gemaess Nutzer-Vorgabe ("keine Werte erfinden"): Bayern-Segmente
werden NICHT fuer die Gefaellekorrektur verwendet** (im JSON weiterhin mit
Rohwerten sichtbar, aber explizit als unzuverlaessig gekennzeichnet/aus der
Gesamt-RMSE-Bewertung ausgeschlossen). Thueringen (hilft: RMSE 0.187->0.125)
und Sachsen-Anhalt (neutral: 0.154->0.156) werden verwendet.

**Gesamtergebnis (60 von 83 Top-Speed-Segmenten mit vertrauenswuerdiger
Gefaellekorrektur, Bayern ausgeschlossen):** RMSE 0.171 -> 0.154 m/s² (~10%
Verbesserung) - Gefaellekorrektur hilft insgesamt, aber bescheiden.

### Warum ist das Modell unter 200 km/h zu optimistisch? (Kernfrage beantwortet)

Kombinierte Analyse aller 127 verfuegbaren echten Volllast-Segmente (44 aus
`drivetrain_model_validation.py` + 83 aus `top_speed_validation.py`, Gaenge
2-6, v=18-65 m/s): das Verhaeltnis `F_erforderlich/F_modell`
(F_erforderlich = aus gemessener Beschleunigung zurueckgerechnete
Antriebskraft, F_modell = Drehmoment x Gang- x Achsuebersetzung x eta/r_dyn)
faellt klar systematisch MIT dem Gang:

| Gang | n | F_erforderlich/F_modell (Median) |
|---:|---:|---:|
| 2 | 3 | 0.84 |
| 3 | 7 | 0.93 |
| 4 | 8 | 0.93 |
| 5 | 16 | 0.96 |
| 6 | 93 | 0.97 |

**Das ist genau die Signatur einer Kraft-OBERGRENZE, nicht eines
pauschalen Modellfehlers** (ein pauschaler eta-/Drehmomentkurven-Fehler
waere bei jedem Gang gleich gross relativ zur Radkraft - ist es nicht).
Bei kleinen Gaengen (hohe Drehmoment-Multiplikation) wird eine Kraftgrenze
erreicht/ueberschritten; bei Gang 6/hoher Geschwindigkeit liegt die
Modell-Radkraft ohnehin fast immer darunter, daher kaum Abweichung - deckt
sich mit dem bereits dokumentierten Befund, dass das ROHE (unkorrigierte)
Modell die Vmax-Segmente am besten trifft.

**Empirischer Fit:** `F_erforderlich = min(F_modell, F_max)` mit
**F_max=4450N** (Suche 3000-8000N in 50N-Schritten, minimales RMSE) trifft
alle 127 Segmente besser (RMSE 0.158 m/s²) als sowohl das rohe Modell
(0.195) als auch der bisherige pauschale Bias-Faktor 0.92 (0.169).
Impliziertes mu = F_max/(m*g) = 0.38 - niedriger als ein reiner
Reifenhaftungs-Koeffizient (typisch 0.8-1.1+), daher NICHT als sauber
isolierter Reifen-mu zu interpretieren, sondern vermutlich eine Mischung
aus echtem Traktionslimit, ECU-Drehmomentmanagement/Anfahrschlupfregelung
und ggf. weiteren nicht einzeln modellierten Verlusten. Unabhaengig vom
bereits dokumentierten historischen Launch-Wert (mu_eff=0.88 im externen
Dokument) - beide NICHT verrechnet/gleichgesetzt.

**Umgesetzt in `performance_simulation.py`:** neues drittes Szenario
"traktionsbegrenzt" (`TRACTION_MAX_FORCE_N=4450`, `accel()` hat jetzt
optionalen `f_max`-Parameter). Ergebnis-Vergleich:

| Szenario | 0-100 | 0-200 | Vmax |
|---|---:|---:|---:|
| raw | 5.80s | 24.95s | 231.7 km/h |
| bias-korrigiert (0.92) | 6.31s | 28.46s | 222.8 km/h |
| **traktionsbegrenzt (NEU)** | **8.16s** | **27.31s** | **231.7 km/h** |

Das neue Modell trifft **beide** bekannten Befunde gleichzeitig: langsamerer
Start (Traktionslimit in Gang 1/2, deutlich langsamer als beide bisherigen
Varianten) UND korrektes Vmax (identisch mit raw, da die Kraftgrenze bei
Gang 6/hoher Geschwindigkeit nie erreicht wird) - der pauschale Bias-Faktor
konnte das nie beides gleichzeitig leisten (druecke Vmax faelschlich mit
herunter). **Empfehlung: "traktionsbegrenzt" als neuen Standard fuer
Fahrleistungs-Vorhersagen verwenden statt des pauschalen Bias-Faktors.**

### Zusammenfassung aller in dieser Session gespeicherten/verwendeten externen Quellen

- Bayern-Hoehendaten: `download1.bayernwolke.de` (Bayerische Vermessungsverwaltung, oeffentlich, ohne Lizenzangabe auf der Kachel-Ebene selbst einsehbar - Portal nennt keine Registrierungspflicht)
- Thueringen-Hoehendaten: `geoportal.geoportal-th.de` (Kompetenzzentrum GDI-Th, Datenlizenz Deutschland - Namensnennung 2.0)
- Sachsen-Anhalt-Hoehendaten: `geodatenportal.sachsen-anhalt.de` (LVermGeo Sachsen-Anhalt, INSPIRE-WCS, oeffentlich)
- Berlin-Hoehendaten (bereits dokumentiert): `gdi.berlin.de` (Senatsverwaltung fuer Stadtentwicklung)
- Brandenburg-Hoehendaten (bereits dokumentiert): `data.geobasis-bb.de` (LGB Brandenburg)
- OSM-Bruecken-Daten: Overpass API (`overpass-api.de`), OpenStreetMap-Mitwirkende
- Alle uebrigen Werte (Fahrzeugmasse-Komponenten, Modellkonstanten) stammen aus Nutzerangaben oder den bereits dokumentierten Projektdateien (siehe jeweilige Abschnitte oben) - keine weiteren externen Quellen in dieser Session.

Outputs: `results/top_speed_validation_summary.json`/`.png` (83 Segmente),
`results/performance_simulation_summary.json`/`.png`/`.csv` (3 Szenarien).

OFFEN: Sachsen-Anhalt/Thueringen-Bruecken theoretisch moeglich aber nicht
einzeln verifiziert (nur der physikalische Plausibilitaetsfilter greift
dort); Bayern-Gefaellekorrektur weiterhin ungeloest; Tankfuellstand-Annahme
(20kg) fuer die Suedtirol-Fahrt unbestaetigt; F_max=4450N ist eine
empirische Anpassung an DIESEN Datensatz, keine unabhaengig hergeleitete
physikalische Konstante - bei deutlich mehr/anderen Daten ggf. neu fitten.

## Nachtrag: Kernregion komplett + Bug im Berlin-WMS-Fallback gefunden und behoben (30.08.2026, spaet nachts)

Die 62 Brandenburg-Kacheln aus dem "welche Kacheln fehlen" Auftrag sind
fertig geladen (Hintergrund-Task, waehrend an der Suedtirol-Analyse
gearbeitet wurde). `elevation_model.py --rebuild`: **150 Kacheln gesamt**
(vorher 88), 3588 Bruecken-Wege. Abdeckung der Kernregion jetzt **98,8%**
der GPS-Punkte (98038/99189, vorher 72533/98251 zu Sessionbeginn) - die
Berlin-Brandenburg-Schleife ist damit praktisch lueckenlos abgedeckt.

**Beim Rebuild-Plausibilitaetscheck fiel ein Bug auf:** StdAbw der
GPS-Hoehe-vs-DGM-Differenz sprang von ~2,9m auf 535,7m (Mean von 40,3m auf
69,0m) - deutliches Warnsignal. Ursache gefunden: **der Berlin-WMS-
Fallback (`query_berlin_wms`, aus dem Berlin-Abschnitt oben) hat den
NODATA-Sentinel-Wert `-9999` des Dienstes ungefiltert als echte Hoehe
akzeptiert und gecacht** (Regex matchte "-9999.00" anstandslos). Betraf
Punkte ausserhalb auch der Berliner eigenen Abdeckung (z.B. lat~52.65-52.68,
noerdliches Berlin/Grenzbereich) - 238 von damals ~10.900 gecachten
Eintraegen betroffen.

**Behoben:** `query_berlin_wms()` verwirft jetzt Werte <= -1000 als NaN.
Cache bereinigt (238 Eintraege entfernt). `top_speed_validation.py`
danach erneut gelaufen - **Kernbefunde bleiben unveraendert** (60->63
vertrauenswuerdige Segmente, RMSE-Verbesserung durch Gefaellekorrektur
weiterhin ~8-10%; die Traktionsgrenzen-Diagnose F_max=4450N war ohnehin
unabhaengig von Hoehendaten und daher nicht betroffen) - der Bug hat also
KEINE der bereits berichteten Kernaussagen verfaelscht, nur einzelne
Zwischenwerte im Cache waren vor der Bereinigung falsch.

**Lehre/Reminder:** bei jeder neuen Live-Hoehenquelle (WMS/WCS) pruefen,
ob der Dienst NODATA-Sentinel-Werte verwendet, BEVOR sie produktiv genutzt
wird - Sachsen-Anhalt-WCS wurde stichprobenartig gegengeprueft (siehe
gefundene Werte oben, alle plausibel im 100-700m-Bereich), aber nicht
systematisch auf Sentinel-Werte getestet - bei Auffaelligkeiten dort
gleiche Pruefung wie hier durchfuehren.

## Teillastmodell (NEU, 30.08.2026)

Nutzerfrage: "haben wir ein Teillastmodell?" - Antwort war bis dahin nein
(sowohl `drivetrain_model_validation.py` als auch `performance_simulation.py`
kennen nur Volllast; auch das externe Dokument fuehrt das Teillastkennfeld
als offenen Punkt 4 - "insbesondere ETC=60 Grad direkt messen", da dort
keine passenden Segmente vorlagen). Auftrag: vorhandene Daten pruefen und
ein Teillastmodell bauen.

**Ueberraschender Fund:** ein bisher ungenutzter Kanal `ActualEnginePercentTorque`
("Tatsaechlicher Motor-Drehmoment in Prozent", CAN-PID) ist in **14 von 56
Logs** vorhanden (64.211 Messwerte). Validierung ueber echte APP-bestaetigte
Volllast-Phasen (7 Logs): der Kanal liegt dort bei median 89-98% (p10
81-97%) - bestaetigt empirisch, dass er Prozent des Volllast-Drehmoments BEI
DER JEWEILIGEN DREHZAHL angibt. Damit laesst sich das im externen Dokument
gewuenschte Teillastkennfeld DIREKT aus vorhandenen Logs messen, ohne neue
Kalibrierfahrten.

**Neues Skript `scripts/partial_load_model.py`:**

1. **Datenbasis:** 35.331 gefilterte Punkte (Kupplung nicht getreten, keine
   Bremsung, Drehzahl>1000, v>3m/s, gueltiger Gang) aus den 14 Logs mit
   `ActualEnginePercentTorque`.
2. **Kennfeld** (ETC_ACT, Drehzahl) -> Prozent-Drehmoment: robuste Bin-Mediane
   (5°x250 U/min-Raster, nur Bins mit >=3 Punkten, 218 robuste Bins), darauf
   lineare Interpolation (Nearest-Neighbor-Fallback ausserhalb der
   Datenabdeckung). WICHTIG: ETC- und RPM-Achse muessen vor der Distanz-
   berechnung auf ihre Bin-Schrittweite normiert werden - ohne diese
   Normierung dominierte die absolut groessere RPM-Skala die Nearest-
   Neighbor-Distanz komplett, was bei (hohe Drehzahl, niedriges ETC) - ein
   Bereich ohne echte Messpunkte, da man selten hohe Drehzahl bei
   geschlossener Drossel haelt - zu einem deutlichen Artefakt fuehrte
   (faelschlich hohes Drehmoment gemeldet). Nach dem Fix sieht das Kennfeld
   physikalisch plausibel aus: Drehmoment steigt mit ETC, faellt zu sehr
   hohen Drehzahlen hin leicht ab (konsistent mit der Volllastkurve).
3. **Kreuzvalidierung** (leave-one-log-out, 14 Logs): Gesamt-RMSE **7.2
   Prozentpunkte** ueber alle ausgelassenen Logs - das Kennfeld generalisiert
   also ueber verschiedene Tage/Fahrten hinweg, kein reines Ueberfitten auf
   eine einzelne Fahrt.
4. **Physikalische Validierung** ueber echte Teillast-Beschleunigungssegmente
   (2553 Segmente aus allen 56 Logs mit ETC_ACT, >=3s, ETC 5-80°, konstanter
   Gang, keine Bremsung/Kupplung, gemessene Beschleunigung per linearer
   Regression von VehicleSpeed(t)) - drei Modellvarianten verglichen:

   | Variante | n | Korrelation | RMSE | mittl. Diff (Mess-Modell) |
   |---|---:|---:|---:|---:|
   | naiv (immer Volllast-Drehmoment) | 2553 | 0.22 | 1.699 m/s² | -1.432 m/s² |
   | **Teillast-Kennfeld (ETC+RPM)** | 2553 | **0.57** | **0.406 m/s²** | -0.166 m/s² |
   | direkt gemessen (Teilmenge mit Drehmoment-Kanal) | 631 | 0.79 | 0.234 m/s² | -0.023 m/s² |

   Klare Bestaetigung: die bisher implizite Annahme "Volllast-Drehmoment
   ueberall" ist bei Teilgas grob falsch (RMSE 1.7 m/s², ueberschaetzt
   systematisch). Das neue Kennfeld verbessert das um Faktor ~4 (RMSE 0.41
   statt 1.70). Die verbleibende Luecke zum "direkt gemessen"-Fall (0.23
   m/s², nahezu unverzerrt: -0.023 m/s²) zeigt, dass die physikalische Kette
   (eta/CdA/Crr, bereits bei Volllast validiert) auch bei Teillast gut haelt
   - die Restungenauigkeit des Kennfeld-Ansatzes kommt vor allem aus der
   ETC->Drehmoment-Zuordnung selbst (Datenluecken bei mittlerem ETC, siehe
   unten), nicht aus falschen Fahrzeugparametern.

**Outputs:** `results/partial_load_kennfeld.png` (Kennfeld-Heatmap),
`results/partial_load_model_vs_measured.png` (Streudiagramm naiv vs.
Kennfeld vs. Messung), `results/partial_load_model_summary.json` (Kennfeld-
Bins, Kreuzvalidierung, alle Segmente).

**Aufruf:** `.venv/bin/python scripts/partial_load_model.py`

**EINSCHRAENKUNGEN:**
- Datendichte bei mittlerem ETC (20-70°) deutlich duenner als bei sehr
  niedrigem ETC (<10°, ueberwiegt zahlenmaessig: reales Fahren ist meist
  Cruisen oder kurze Vollgasstoesse) - manche Bins basieren auf wenigen
  Punkten, Kreuzvalidierung (Punkt 3) ist der verlaesslichste
  Gesamtgenauigkeits-Indikator.
- Keine Steigungskorrektur fuer die Validierungssegmente (anders als
  `top_speed_validation.py`) - koennte einzelne Segmente verzerren.
- `ActualEnginePercentTorque`-Semantik (exakter Referenzwert) nur empirisch
  ueber den WOT-Abgleich plausibilisiert, nicht aus einer Mazda-Spezifikation
  verifiziert.
- NOCH NICHT gemacht (Stand vor der Integration, siehe naechster Abschnitt):
  Nutzung in `performance_simulation.py`.

## Teillastmodell integriert in performance_simulation.py (NEU, 30.08.2026)

Nutzerauftrag im Anschluss an das Teillastmodell: "integriere das in
performance_simulation.py".

**Umgesetzt:**
- `accel()` (bisher nur Volllast) hat jetzt einen optionalen `etc_deg`-
  Parameter. Ohne ihn (Default `None`) unveraendertes Verhalten - die drei
  bestehenden Volllast-Szenarien ("raw", "bias-korrigiert",
  "traktionsbegrenzt") sind exakt reproduziert (0-100/0-200/Vmax-Werte
  identisch zum Stand vor der Aenderung, per Testlauf verifiziert). Mit
  `etc_deg` wird stattdessen `partial_load_model.build_kennfeld_predictor()`
  (neu exportierte Funktion, faengt Datalake-Query+Fit einmal pro Prozess ab,
  danach gecacht) fuer das Drehmoment verwendet, statt der vollen
  Volllast-Kennlinie.
- Neue Funktion `find_equilibrium_speed(gear, etc_deg, ...)` - wie
  `find_vmax()`, aber bei konstantem Teillast-ETC statt Volllast (liefert
  die Cruise-/Gleichgewichtsgeschwindigkeit in diesem Gang bei diesem
  Drosselklappenwinkel).
- Neue Funktion `simulate_constant_throttle(etc_deg, label, mass_kg=...)` -
  wie `simulate()`, aber bei konstantem Teillast-ETC statt Vollgas, gleiche
  Schaltlogik (naechster Gang, sobald staerkere Beschleunigung). KEIN
  zusaetzlicher Bias-/Traktionsfaktor (die bestehenden Faktoren wurden
  spezifisch fuer die Volllast-Kennlinie hergeleitet - das Teillast-Kennfeld
  ist bereits direkt an echten Beschleunigungsdaten validiert, siehe
  `partial_load_model.py`). Sicherheitsnetz: bricht ab und meldet
  "kommt nicht von der Stelle", falls die Kraft in Gang 1 bei diesem ETC
  nicht mal ausreicht, um ueber 1 m/s zu beschleunigen.
- `main()` rechnet zusaetzlich drei Demo-Szenarien bei konstantem ETC (30°,
  50°, 70° - Spanne innerhalb des gut abgedeckten Teillastbands, klar
  unterhalb der ~80° Volllastschwelle):

  | ETC | 0-100 | 0-200 | Gleichgewicht Gang 6 |
  |---|---:|---:|---:|
  | 30° | 9.73s | nicht erreicht (v_ende 195.8 km/h nach 90s) | 200.4 km/h |
  | 50° | 7.82s | 44.58s | 214.1 km/h |
  | 70° | 7.12s | 34.01s | 218.9 km/h |

  Physikalisch plausibel: monoton steigend mit ETC, naehert sich bei 70° schon
  deutlich dem raw-WOT-Vmax (231.7 km/h) an, bei 30° sehr langsame
  asymptotische Annaeherung an die Gleichgewichtsgeschwindigkeit (realistisch
  - Beschleunigung klingt nahe dem Kraftgleichgewicht exponentiell ab).

**Outputs:** `results/performance_simulation_partial_load.png` (v-t-Kurven
der drei Teillast-Szenarien, separat vom bestehenden Volllast-Plot),
`results/performance_simulation_summary.json`/CSVs jetzt mit allen 6
Szenarien (3 Volllast + 3 Teillast).

**EINSCHRAENKUNG (bewusst nicht geloest):** konstantes ETC ueber die ganze
Simulation ist eine Vereinfachung - reale Fahrer variieren den Pedalweg
kontinuierlich (z.B. Kurvenausfahrt mit zunehmendem Gas). Fuer eine echte
Rundenzeit-/GPS-Track-Simulation waere ein zeitlich variabler ETC-Verlauf
(aus einem Fahrmodell oder einer Optimierung) noetig - das ist der
naheliegende naechste Schritt in Richtung des urspruenglichen Projektziels
("Bestzeit-Berechnung aus GPS-Track"), aber bewusst NICHT Teil dieser
Aenderung.

## Spreewaldring Training Center - Streckenrekonstruktion aus OSM (NEU, 30.08.2026)

Nutzerauftrag: "kannst du aus den OSM Daten das Spreewaldring Training
Center als Track rekonstruieren?" - erster Schritt weg von den bisher
GPS-rausch-limitierten Kurvendaten (nur 6 bestaetigte Kurven aus echten
Fahrten) hin zu einer PRAEZISE VERMESSENEN Streckengeometrie, die
unabhaengig von den 56 bisherigen Fahrt-Logs ist (die Logs decken alle die
Berlin-Brandenburg-Alltagsschleife/Suedtirol-Rueckfahrt ab, NICHT den
Spreewaldring - eigenstaendige Vorarbeit, falls dort spaeter tatsaechlich
gefahren/geloggt wird).

**Fund:** Standort in OSM als `way 110163355` (leisure=sports_centre,
Waldhaus 2, 15910 Schoenwald/Waldow-Brand, website stc-motodrom.de)
verzeichnet - nur die Gelaendeflaeche. Die eigentliche Streckenfuehrung
liegt separat als `highway=raceway`-Wege vor. Das Gelaende ist MODULAR:
- **Kartbahn** (sport=karting, mehrere Ways, oestlicher Gelaendeteil) -
  nicht Teil dieser Rekonstruktion.
- **Hauptschleife fuer Motorsport** (sport=motor): way 172927499 (26
  Knoten) + way 172927073 (95 Knoten) teilen sich einen exakten Knoten
  (0,0m Abstand, beide `oneway=yes`) - eindeutig eine durchgehende
  Fahrtrichtung, zusammen 2873m OSM-Original-Laenge.
- **Alternative kuerzere Streckenfuehrung** (sport=motor): way 243040316 +
  243040319, quert die Hauptschleife als Sehne/Abkuerzung in der Mitte -
  nur referenziert, NICHT separat rekonstruiert.

**Neues Skript `scripts/spreewaldring_track.py`:**
1. Laedt die Hauptschleife per Overpass-API (gecacht in
   `data/track/spreewaldring_osm_raceways.json`), transformiert nach
   UTM33 (EPSG:25833).
2. **WICHTIGE EINSCHRAENKUNG:** die Hauptschleife ist selbst kein
   geschlossener Weg - zwischen ihrem Ende und Anfang klafft eine 209m
   Luecke (vermutlich Start-/Zielgerade bzw. Boxengasseneinfahrt). Nahe
   `service`/`driveway`-Wege (Boxengasse/Paddock) liegen an beiden
   Luecken-Enden (30-70m entfernt), verbinden sie aber nicht saubere durch
   einen einzelnen kartierten Weg. Die Rekonstruktion schliesst die Luecke
   mit einer GERADEN (nicht aus OSM, Annahme - im Plot/JSON explizit als
   `closing_segment_approx` markiert).
3. Resampling auf 3m Bogenlaenge, Kruemmungsradius per 3-Punkt-Kreis
   (Menger-Kruemmung, 15m Fenster vor/hinter jedem Punkt - robuster gegen
   OSM-Knotenrauschen als reine Nachbarpunkt-Kruemmung).
4. Kurven = Abschnitte mit Radius <100m (eng beieinanderliegende
   Kandidaten werden zu einer Kurve zusammengefasst, sonst zerlegt eine
   Schwellwert-Ueberschreitung eine S-Kurven-Sequenz kuenstlich in mehrere
   "Kurven").
5. GROBE Kurvengeschwindigkeits-Bracket v=sqrt(mu*g*r) mit mu=1,0-1,3
   (Literatur-Bandbreite fuer den Nankang NS-R2 Semi-Slick, siehe Memory
   "mx5-tires") - NICHT validiert, reine Groessenordnung.

**Ergebnis (ERSTE Version, ueberholt):** Gesamtlaenge 3086,5m (davon 209,1m
angenommene Schliessgerade), 14 Kurven.

**NUTZER-KORREKTUR (30.08.2026, per Google-Maps-Satellitenbild + OSM-
Standardkartenansicht):** die kleine enge Schleife oben rechts (way
172927499), die in der ersten Version faelschlich als Fortsetzung der
Ideallinie behandelt wurde (exakter gemeinsamer Knoten mit 172927073s
Start liess das wie eine durchgehende Fahrtrichtung aussehen), ist
tatsaechlich die **Boxengasse** - klassisches Layout: parallel zur
Start-/Zielgerade, muendet exakt an der Stelle wieder ein, wo 172927073
beginnt. Damit war auch die 209m-Schliessgerade der ersten Version zu lang/
falsch konstruiert.

**Zweite Nutzer-Korrektur (Satellitenbild-Ausschnitt der Boxenausfahrt,
Gelb/Gruen-Markierung):** noch nicht zufrieden - der Nutzer zeigte, dass
Start-Ziel-Gerade UND Boxengasse VOR der grossen Kurve zusammentreffen
("beide muenden in Kurve 1"), nicht exakt an ihr. Genauere Analyse der
rohen Knotenkoordinaten von way 172927073 ergab den eigentlichen Fund:
**Knoten-Index 3 und der allerletzte Knoten (Index 94) sind EXAKT derselbe
OSM-Punkt** (auf 7 Nachkommastellen identisch). Die Rundstrecke schliesst
sich also bereits VOLLSTAENDIG in den OSM-Daten selbst - **keine
Verbindungsgerade/Annahme mehr noetig.** Knoten 0-1-2-3 (123,5m) sind ein
separater Boxenausfahrt-Verbindungsweg, der bei Index 3 auf die
geschlossene Runde trifft (ein Auto ohne Boxenstopp durchfaehrt ihn nie).
Nebenbefund: way 172927073 enthaelt selbst einen einzelnen 134,7m-Abschnitt
ohne Zwischenknoten (Index 91->92, schnurgerade) - vermutlich die
tatsaechliche Start-Ziel-Gerade, von OSM nur mit 2 Endknoten kartiert.

**Finales Ergebnis:** Rundstrecke = way 172927073, Knoten 3 bis 94(=3),
**vollstaendig aus OSM, keine Annahme mehr**. Boxengasse + Ausfahrt-
Verbindung weiterhin im Plot gezeigt (gepunktet), aber nicht Teil der
Rundenlaenge. **Gesamtlaenge 2488,3m** (realistischer als beide vorherigen
Versuche mit erfundener Gerade). **11 Kurven**, Radien 11,1m bis ~73m,
Geschwindigkeitsbraetter 38-110 km/h. Keine verdaechtigen Artefakte mehr am
Schliesspunkt (letzte Kante ist eine reale, kurze 21,3m-OSM-Kante statt
einer erfundenen Geraden) - Kurve 1 (s=0m, R=24,6m) liegt jetzt genau dort,
wo laut Satellitenbild Start-Ziel-Gerade und Boxenausfahrt zusammentreffen
und in die erste Kurve fuehren.

**Outputs:** `results/spreewaldring_track.png` (Streckenplot, farbcodiert
nach Kruemmungsradius, Boxengasse gepunktet), `results/spreewaldring_track_summary.json`
(vollstaendige Punktliste UTM33 + Radius + Kurvenliste).

**Aufruf:** `.venv/bin/python scripts/spreewaldring_track.py`

## Orthofoto-Verfeinerung der Streckengeometrie (NEU, 30.08.2026)

Nutzerfrage: "Laut Betreiber hat die Strecke eine Breite von 10m. Kannst du
mit Hilfe Satellitenbild den Streckenverlauf feiner aufgeloest
interpolieren?" Die 91 OSM-Knoten sind an manchen Stellen weit auseinander
(einzelne Kanten bis 167m ohne Zwischenpunkt) - fuer eine praezise
Kruemmung zu grob.

**Fund:** Brandenburg (LGB) stellt wie beim Hoehenmodell auch amtliche,
exakt georeferenzierte Orthofotos oeffentlich bereit (20cm Bodenaufloesung,
`data.geobasis-bb.de/geobasis/daten/dop/rgb_jpg/`, gleiches Kachelschema
wie die ALS-Hoehendaten: `dop_<E_km>-<N_km>.zip`, JPEG + .jgw World-File).
Der komplette Spreewaldring passt in eine einzige Kachel (`dop_33409-5761`)
- anders als beim Hoehenmodell, wo an dieser Stelle keine Kachel verfuegbar
war.

**Methodik** (in `scripts/spreewaldring_track.py` integriert):
1. Kachel herunterladen/cachen (`data/orthophoto/`).
2. Referenzfarbe der Fahrbahn adaptiv aus dem Bild selbst gelernt (Median+
   Streuung an allen 829 vorhandenen OSM-Streckenpunkten abgetastet, RGB
   Median [141, 147, 141]).
3. Pro Streckenpunkt: senkrechter Scan zur lokalen Tangente (±8m, 0,2m/Pixel-
   Schritte), zusammenhaengender Fahrbahn-Abschnitt naechst am Nulldurchgang
   wird genommen, dessen Mittelpunkt = verfeinerter Punkt, Laenge = lokale
   Breite.
4. Kruemmung/Kurven auf der verfeinerten Linie neu berechnet.

**Ergebnis:** 829/829 Punkte (100%) erfolgreich verfeinert. Visuell folgt
die neue Linie im Plot sehr praezise dem tatsaechlichen Asphalt ueber die
gesamte Runde (keine Kontamination durch Nachbarstrecken). Streckenlaenge
jetzt **2529,5m** (OSM-Naeherung: 2488,3m). **13 statt 11 Kurven** (die
feinere Aufloesung findet zusaetzliche Kurvenkomplexe, die die grobe
OSM-Linie geglaettet hatte). Gemessene Fahrbahnbreite: **Median 8,0m**
(Betreiberangabe: 10m) - Farbschwellwert dafuer von anfangs 2,5 auf 4,0
Standardabweichungen gelockert (Median stieg von 6,6m auf 8,0m), dann NICHT
weiter Richtung 10m gedrueckt, um keine Kontamination/Verzerrung des
Farbklassifikators zu riskieren nur um eine Zielzahl zu treffen.

**EINSCHRAENKUNG:** die verbleibende Luecke zur Betreiberangabe (8,0m vs.
10m) ist NICHT aufgeloest - plausible Erklaerung: der bildbasierte Ansatz
erfasst vermutlich nur den farblich einheitlichen "Kern" der Asphaltflaeche
(exklusive Aufhellungen an Fahrbahnmarkierungen/Uebergaengen zum Bankett),
waehrend die Betreiberangabe die volle befahrbare Breite inkl. Kerbs/
Uebergangsbereich meinen koennte - NICHT verifiziert. Fuer die
Kruemmungsberechnung (Zentrallinie) ist diese Differenz unkritisch, fuer
eine spaetere Streckenrand-/Randstein-Modellierung waere sie relevant.

### Nachtrag: Glaettung der Mittellinie (NEU, 30.08.2026)

Nutzer-Feedback: "ignoriere die 10m Betreiberangabe, ich bin mit der
Verteilung der Punkte auf Start-Ziel- und Gegengerade noch nicht ganz
zufrieden. Entwickle ein Verfahren, wie wir das gemeinsam verbessern
koennen."

**Erste Diagnose war ein Fehlalarm:** Ein Test auf s=476-563m (per
"Radius>100m"-Heuristik als "Gerade" identifiziert) zeigte 4,7m Abweichung
von einer Ausgleichsgeraden - stellte sich aber als eine ECHTE sanfte
Kurve heraus (R~210m ueber 87m Bogenlaenge erklaert genau diese Abweichung
rechnerisch, `d ~ L^2/(8R)`). Kein Rauschen, sondern korrekt erkannte
Kruemmung.

**Sauberer Test:** stattdessen ein Abschnitt gewaehlt, der laut den ROHEN
OSM-Knoten selbst GARANTIERT gerade ist (way 172927073, Original-Index
91->92, einzelne 134,7m-Kante ohne Zwischenknoten, s=2255-2389m nach der
Umindizierung). Dort zeigte sich echtes Rauschen: **1,98m max. Abweichung,
Std 0,86m** - das Problem war real, nur die erste Messstelle war falsch
gewaehlt.

**Ursache:** jeder Streckenpunkt wurde bisher UNABHAENGIG von seinen
Nachbarn per einzelnem Farb-Scan bestimmt - kleinste Bildstoerungen
(Schatten, Reifenabrieb, einzelne Pixel) lassen die Linie punktweise
wackeln, ohne Kontinuitaetszwang.

**Loesung:** Savitzky-Golay-Glaettung auf dem senkrechten Versatz-Signal
(nicht direkt auf x/y - numerisch sauberer), zyklisch gepolstert fuer die
geschlossene Schleife. Fenstergroesse **empirisch** bestimmt: an 6 bekannten
engen Kurven (Radius 11-25m) blieb die Radius-Aenderung selbst bei einem
200m-Fenster unter 1m - Kurven sind also ueberraschend robust gegen grosse
Glaettungsfenster, waehrend das Geraden-Rauschen kontinuierlich sinkt.
Gewaehlt: **90m-Fenster** (Kompromiss). Ergebnis am Referenzabschnitt:
Abweichung von 1,98m auf **0,44m**, Streuung von 0,86m auf **0,20m**
reduziert.

Zoom-Kontrollen (`results/spreewaldring_zoom_start_ziel_gerade.png`,
`results/spreewaldring_zoom_gegengerade.png`) zeigen: die geglaettete Linie
folgt jetzt sichtbar praezise dem echten Asphalt auf beiden Geraden,
deutlich besser als die grobe OSM-Linie. `spreewaldring_track_summary.json`
enthaelt weiterhin sowohl die rohe (`track_points_utm33_refined_raw`) als
auch die geglaettete Linie sowie die einzelnen linken/rechten
Rand-Versaetze (`offset_left_m`/`offset_right_m`) zum Vergleich.

**OFFEN:** Nutzer hat angeboten, beim Auffinden der Fahrbahngrenzen direkt
mitzuhelfen (z.B. per Markierung auf Bildausschnitten) - noch nicht
umgesetzt, da die automatische Glaettung das urspruengliche Problem
groesstenteils geloest zu haben scheint. Rueckfrage an den Nutzer, ob damit
zufrieden oder ob es noch konkrete Problemstellen gibt, bei denen eine
manuelle Korrektur sinnvoll waere.

**Outputs:** `results/spreewaldring_track_refined.png` (Orthofoto-Hintergrund
mit alter OSM-Linie + neuer verfeinerter Linie), `results/spreewaldring_track_summary.json`
Feld `orthophoto_refinement` (Referenzfarbe, gemessene Breiten pro Punkt,
verfeinerte Kurvenliste/Punkte).

**EINSCHRAENKUNGEN (verbleibend):**
- Kruemmung basiert auf der kartierten StreckenMITTELLINIE, nicht auf einer
  idealen Rennlinie (die haette in Kurven einen groesseren Radius).
- Keine Hoehendaten: Spreewaldring liegt bei E~409,8km/N~5761,6km,
  deutlich ausserhalb der bestehenden Brandenburg-DGM-Kachelabdeckung
  (E350-410km/N5810-5855km, das ist die Berlin-Brandenburg-Stammschleife).
  Versuch, die naheliegende Kachel `als_33409-5761.zip` direkt zu laden,
  ergab 404 - offen, ob echte Datenluecke oder falsche Kachel, nicht
  weiter verfolgt.
- Kurvengeschwindigkeits-Braetter sind reine Literaturschaetzung fuer die
  Reifenklasse (siehe Memory "mx5-tires"), NICHT aus echten Messungen
  dieses Fahrzeugs/dieser Strecke.
- Die exakte Lage des 134,7m-Straight-Abschnitts (Index 91->92 innerhalb
  von 172927073) wurde nicht gesondert validiert - plausibel als
  Start-Ziel-Gerade eingeordnet, aber nicht durch ein zweites Satellitenbild
  bestaetigt.

## Erste Rundenzeit-Simulation Spreewaldring (NEU, 30.08.2026)

Nutzerauftrag: "Jetzt wuerde ich tatsaechlich versuchen, ob wir eine
schnelle Runde auf der berechneten Strecke simulieren koennen." Erste
Zusammenfuehrung aller bisherigen Bausteine: Streckengeometrie
(orthofoto-verfeinerte, geglaettete Mittellinie), Volllast-/Traktionsmodell
(`performance_simulation.py`, "traktionsbegrenzt"-Variante), Reifen-Grip-
Bandbreite (Nankang NS-R2, siehe Memory "mx5-tires").

**Neues Skript `scripts/spreewaldring_lap_simulation.py`:** klassisches
quasi-stationaeres Punktmassen-Rundenzeitmodell (Standardansatz im
Motorsport-Engineering, kein neues physikalisches Modell):
1. Kurvengeschwindigkeits-Grenze v=sqrt(mu*g*R) pro Streckenpunkt.
2. Beschleunigungs-Einhuellende = Maximum ueber alle 6 Gaenge des
   traktionsbegrenzten Modells aus `performance_simulation.py`.
3. Brems-Verzoegerung = mu*g (konstant, gleiche Reifen-Grip-Annahme wie
   bei Kurven - NICHT die 0,48g aus den geloggten Alltagsbremsungen,
   siehe Einschraenkungen).
4. Zwei-Pass-Algorithmus (Vorwaerts=Beschleunigung, Rueckwaerts=Bremsen,
   Start am langsamsten Punkt, 2 Iterationen fuer Konvergenz).

**Ergebnis (zwei Szenarien, mu=1.0/1.3):**

| Szenario | Rundenzeit | Topspeed | Schnitt |
|---|---:|---:|---:|
| konservativ (mu=1.0) | 110,52 s | 162,5 km/h | 81,1 km/h |
| optimistisch (mu=1.3) | 99,54 s | 172,9 km/h | 90,0 km/h |

Geschwindigkeitsprofil physikalisch sehr plausibel: Topspeed exakt auf der
Gegengerade (s~800m), staerkste Verzoegerung vor der enges ten Kurve
(s=1633m, R=11,5m, v_min 38-44 km/h je nach Szenario).

**Outputs:** `results/spreewaldring_lap_speed_map.png` (Strecke
geschwindigkeits-eingefaerbt), `results/spreewaldring_lap_speed_trace.png`
(v(s)-Verlauf beide Szenarien), `results/spreewaldring_lap_simulation_summary.json`.

**Aufruf:** `.venv/bin/python scripts/spreewaldring_lap_simulation.py`

**WICHTIGSTE EINSCHRAENKUNG:** die Simulation faehrt die geometrische
Streckenmittellinie, NICHT eine optimierte Ideallinie (die haette in
Kurven einen groesseren effektiven Radius) - die Rundenzeit ist dadurch
SYSTEMATISCH PESSIMISTISCH (zu langsam), vermutlich der groesste einzelne
Fehlerfaktor. Weitere Einschraenkungen: kein kombinierter Reifenkraftkreis
(Laengs-/Quergrenzen unabhaengig behandelt), kein Gewichtstransfer,
Schaltzeiten ignoriert, mu-Bandbreite ist Literaturschaetzung fuer die
Reifenklasse (nicht an diesem Auto/dieser Strecke gemessen), keine
Boxengasse, kein realistischer Fahrerfehler (=theoretisches Maximum bei
perfekter Ausfuehrung).

**Nutzer moechte sich spaeter noch einmal mit der Frage beschaeftigen, wie
die Strecke/Ideallinie berechnet werden soll** (vertagt, siehe vorheriger
Abschnitt zur Punkte-Glaettung) - diese Rundenzeit-Simulation ist ein
erster Wurf darauf aufbauend, kein finales Ergebnis.

## Fahrbahn als Flaeche statt Mittellinie (NEU, 30.08.2026)

Nutzerauftrag: "ich wuerde gerne versuchen mit dir das orthophoto zu
analysieren und die grenzen der Strecke zu erfassen und statt einer linie die
fahrbahn abzubilden." Ausfuehrliche gemeinsame Bildanalyse (mehrere
Zoomstufen, User-Annotationen) vor der Umsetzung:

- Nutzer-Beobachtung: Fahrbahn zeigt sich in verschiedenen Grautoenen, ist
  aber durchgehend durch eine WEISSE Linie begrenzt. Pixel-Stichproben
  (an `crop_straight.png`) bestaetigten: Asphalt UND Linie haben durchweg
  NIEDRIGE Saettigung (Asphalt 0.03-0.08, Linie 0.05-0.11), das trockene
  Gras deutlich hoehere (0.17-0.30) - **Saettigungsschwelle statt
  Farbabstand zu einer gelernten Referenzfarbe** (wie im alten
  `refine_centerline_with_orthophoto()`) ist robuster gegen die
  Grauton-Variation UND erfasst die Linie automatisch mit als Teil der
  Fahrbahn.
- Ueber mehrere von Nutzer bereitgestellte annotierte Bilder aufgeklaert:
  zwei bisher nur referenzierte, nicht rekonstruierte OSM-Wege
  (`SHORTCUT_WAY_IDS` 243040316 + 243040319) sind zwei separate,
  Pylon-abgesperrte Verbindungsstuecke ("A"=obere Abkuerzung,
  "C"=untere Abkuerzung) - im Rennstreckenbetrieb (volle Runde)
  abgesperrt, koennen aber auch als zwei kleinere Teilstrecken/
  Trainingsvarianten genutzt werden. Nutzer hat die Pylon-Positionen
  (20 Punkte, aus 4 Gate-Gruppen) im Bild markiert und digitalisiert
  (`data/track/spreewaldring_pylon_gates.json`, per Farberkennung + LGB-
  World-File-Transformation aus dem Bild extrahiert). WICHTIGE ERKENNTNIS:
  diese Pylon-Reihen sind KEINE Fahrbahnraender entlang der Strecke,
  sondern Quer-Absperrungen an den beiden Enden jeder Abkuerzung (volle
  Fahrbahnbreite an einem Querschnitt, 12.6-15.1m - breiter als die
  Regelbreite, weil die Einmuendungen trichterartig aufgeweitet sind).
  Boxengasse (way 172927499) wird jetzt ebenfalls als eigenes
  Flaechenpolygon erfasst (bisher nur gepunktete Linie im Plot).
- Nutzer-Entscheidung: automatische Randerkennung soll durchgehend
  versucht werden (auch fuer die beiden Verbindungsstuecke, die Gate-
  Punkte dienen nur als Plausibilitaets-Validierung an den beiden Enden,
  kein hartes Pinning) - "die Einmuendungsbereiche sind fuer die
  Ideallinie ohnehin irrelevant, Interpolation reicht".

**Neues Skript `scripts/spreewaldring_track_surface.py`:**
`detect_pavement_edges()` - gleicher senkrechter Scan wie im Basisskript,
aber Klassifikation per `PAVEMENT_SAT_THRESHOLD = 0.13`. Vier Segmente
verarbeitet: Hauptschleife (nutzt die BEREITS orthofoto-verfeinerte+
geglaettete Mittellinie aus `spreewaldring_track_summary.json`, keine
Neuberechnung der Mittellinie selbst), Boxengasse, Verbindungsstueck A,
Verbindungsstueck C (letzte drei: neue Funktionen `resample_open_path()`/
`smooth_offset_open()` fuer offene, nicht-geschlossene Pfade, randgepolstert
statt zyklisch geglaettet).

**Ergebnis (alle 4 Segmente, 100% Erkennungsrate):**
| Segment | Breite Median/Mittel | Laenge |
|---|---:|---:|
| Hauptschleife | 9.60m / 9.83m | 2489.9m |
| Boxengasse | 11.80m / 11.03m | 389.1m |
| Verbindungsstueck A | 9.90m / 12.18m | 57.2m |
| Verbindungsstueck C | 9.40m / 11.66m | 84.7m |

Hauptschleifen-Breite jetzt 9.60-9.83m (vorher 8.0m mit der alten
Farbabstands-Methode) - deutlich naeher an der Betreiberangabe (10m),
bestaetigt die Vermutung, dass die alte Methode an der weissen Linie
stoppte statt sie mit einzuschliessen.

**Gate-Validierung** (automatisch erkannte Breite an der naechstgelegenen
Streckenstelle vs. Pylon-Spannweite): an allen 4 Querschnitten liegt die
Automatik (15-18m) etwas ueber der Pylon-Spannweite (12.6-15.1m) - plausibel
(Automatik rutscht an den Einmuendungstrichtern tendenziell zu breit), aber
richtige Groessenordnung. Laut Nutzer fuer die Ideallinie irrelevant, daher
nicht weiter optimiert.

**Outputs:** `results/spreewaldring_track_surface.png` (vier Flaechen-
polygone auf Orthofoto-Hintergrund), `results/spreewaldring_track_surface_summary.json`
(Mittellinie, linker/rechter Rand, Polygon, Breiten-Array pro Punkt, Gate-
Validierung je Segment).

**Aufruf:** `.venv/bin/python scripts/spreewaldring_track_surface.py`

**NACHTRAG (30.08.2026) - Glaettungsfenster fuer Randbreite korrigiert:**
Nutzer markierte die echten weissen Linien in zwei Kurven (Screenshot-
Annotation) und bemerkte: Polygone wirken zu grob, folgen der Kurve nicht
eng genug. Diagnose: `offset_left_m`/`offset_right_m` wurden mit dem von
`spreewaldring_track.py` uebernommenen `SMOOTH_WINDOW_M=90m` geglaettet -
das war fuer die MITTELLINIEN-Position kalibriert (Rauschen durch
Farbklassifikation auf Geraden), nicht fuer die RANDBREITE, die sich an
Kurven/Einmuendungen auf viel kuerzerer Distanz aendert (an der
Verbindungsstueck-C-Einmuendung z.B. real 8.2m->16.0m Breitensprung
innerhalb von ~15m Bogenlaenge - ein 90m-Fenster mittelt das komplett weg).
Test verschiedener Fenster gegen die Rohbreite (Residuum-Statistik):
90m -> std 0.70m/max 4.73m, 15m -> std 0.45m/max 2.69m, kleiner als 15m
keine weitere Verbesserung (Rauschboden erreicht, da 15m bereits die
kleinste vom bisherigen Code erlaubte Fenstergroesse war). Neue eigene
Konstante `EDGE_SMOOTH_WINDOW_M = 15.0`, nur fuer die Randerkennung -
`SMOOTH_WINDOW_M=90m` bleibt unveraendert fuer die Mittellinien-Position
(dort weiterhin richtig). Ergebnis visuell bestaetigt: Polygon folgt jetzt
eng am echten Asphalt, auch direkt an der Verbindungsstueck-C-Einmuendung.

**NACHTRAG (30.08.2026) - Kiesbett-/Auslaufzonen-Korrektur:** Nutzer
markierte mehrere Kurven-Kiesbetten (Randbereiche, Uebergang zu heller
Kies-/Sandflaeche), wo die Randerkennung ungenau war (Polygon "schnitt
Ecken" bzw. wuchs zu weit ins Kiesbett). DIAGNOSE (an einer konkreten
Scanlinie): Kiesbett hat AEHNLICH NIEDRIGE Saettigung wie Asphalt
(0.08-0.14), ist aber deutlich HELLER (~180-220 vs. ~135-155 auf echtem
Asphalt) - reine Saettigung kann beide nicht trennen. Reine
Helligkeitsobergrenze wuerde aber auch die weisse Randlinie selbst
abschneiden (Peak-Helligkeit ~160-190, im selben Bereich wie Kies) -
Unterscheidung stattdessen ueber die AUSDEHNUNG: Linie nur ~0.6-0.8m breit
(3-4 Pixel), Kiesbett durchgehend ueber mehrere Meter hell. Neue Funktion
`_trim_sustained_bright_run()`: beim Auswaertslaufen vom Streckenmittelpunkt
wird der Rand am Beginn eines ANHALTEND hellen Laufs abgeschnitten, kurze
helle Spitzen (Linie) bleiben unangetastet. Schwellwert ADAPTIV pro
Scanlinie (lokale Asphalt-Referenzhelligkeit + `GRAVEL_BRIGHTNESS_MARGIN`
= 30) statt fest (ein fester Wert von 160 erwies sich als zu aggressiv -
schnitt an manchen Kurven auch legitim etwas helleren Feldrand/Boden mit
ab). Visuell an allen vom Nutzer markierten Stellen bestaetigt: Polygon
folgt jetzt exakt der Asphalt-Kies-Grenze.

**Boxengasse am Boxengebaeude - haerterer Fall, NICHT vollstaendig
geloest:** Nutzer skizzierte zusaetzlich Korrekturen an der Boxengasse
(Polygon wuchs an mehreren Stellen ins Gebaeude hinein). Diagnose: das
weisse Wellblechdach des Boxengebaeudes hat AEHNLICH niedrige Saettigung
UND aehnliche Helligkeit wie die Boxengassen-Fahrbahn selbst (kein
sauberer Helligkeitssprung wie beim Kiesbett) - die Kies-Korrektur greift
hier nicht zuverlaessig (Ausreisser bis 24m "Breite" blieben bestehen).
Workaround: eigenes, schmaleres Scanfenster nur fuer die Boxengasse
(`PIT_LANE_SCAN_HALF_WIDTH_M = 6.0`, vorher gemeinsam mit den
Verbindungsstuecken bei 12.0) - verhindert wenigstens, dass der Scan das
Dach ueberhaupt erreichen kann (Ausreisser sanken auf max. 12m). Ergebnis
an den vom Nutzer markierten Stellen deutlich besser, Boxengasse insgesamt
aber weiterhin unruhiger/ungenauer als die Hauptschleife - offene Frage an
den Nutzer, ob weitere Verfeinerung (z.B. dachspezifische Helligkeits-
heuristik oder manuelle Korrektur wie bei den Pylon-Gates) gewuenscht ist.

**EINSCHRAENKUNGEN:**
- Saettigungsschwelle nur an EINER Stichprobe kalibriert, nicht
  systematisch an allen Bildbereichen geprueft - Schattenkreuzungen der
  Hochspannungsleitung sind eine bekannte Grauzone (Saettigung dort
  uneindeutig 0.07-0.19), werden aber durch die Glaettung ueberbrueckt.
- Boxenmauer (rot/weiss) auf der Boxengassen-Seite: nicht gesondert
  behandelt, implizit ueber dieselbe Saettigungsschwelle erfasst, nicht
  gegen ein Referenzfoto verifiziert.
- Kein Graph-Modell der Streckennetz-Topologie (wo genau Verbindungsstueck
  A/C an die Hauptschleife andockt) - die drei Zusatzflaechen werden
  unabhaengig berechnet/geplottet, nicht automatisch mit der Hauptschleife
  verschmolzen.
- Randerkennung an den Verbindungsstueck-Enden (Einmuendungstrichter)
  unsicherer als in der Mitte, siehe Gate-Validierung oben - laut Nutzer
  fuer die Ideallinie ohnehin irrelevant.

## Erster Ideallinien-Versuch fuer den MX-5 ND (NEU, 30.08.2026)

Nutzerauftrag: "Moechtest du mal einen ersten Anlauf unternehmen fuer das
gruene Polygon eine Ideallinie ... zu berechnen?" Direkte Folgearbeit auf
der neuen Fahrbahn-Flaeche (`spreewaldring_track_surface.py`) und der
bestehenden Rundenzeit-Simulation (`spreewaldring_lap_simulation.py`, dort
schon als "systematisch pessimistisch" markiert, weil bisher nur die
Mittellinie gefahren wurde).

**Neues Skript `scripts/spreewaldring_racing_line.py`:** Kruemmungs-
minimierung ("elastic band") statt vollem Minimalzeit-Optimalsteuerungs-
problem - Linie als seitlicher Versatz n(s) von der Mittellinie, begrenzt
durch den Fahrbahnkorridor (linker/rechter Rand aus
`spreewaldring_track_surface_summary.json`) minus Sicherheitsabstand fuer
die halbe MX-5-Breite (ANNAHME 1.74m, nicht G184-spezifisch verifiziert).
Iterativ (3000 Iterationen) wird jeder Punkt in Weltkoordinaten Richtung
Mittelpunkt seiner Nachbarn gezogen, dann auf den Korridor zurueckgeclippt -
konvergiert zu einer glatten Linie, die klassisches "Aussen-Innen-Aussen"
zeigt, OHNE dass Kurvenein-/-ausgaenge explizit programmiert wurden (reines
Resultat der Kruemmungsminimierung unter den Korridor-Grenzen).

**Bewertung:** Ergebnis auf gleichmaessige Bogenlaenge resampled,
Kruemmungsradius neu berechnet (gleiche Methode wie ueberall im Projekt),
durch das UNVERAENDERTE Rundenzeit-Modell aus `spreewaldring_lap_
simulation.py` gejagt (gleiches Traktionsmodell, gleiche mu-Bandbreite
1.0-1.3) - direkter Vergleich zur Mittellinie moeglich.

**Ergebnis:**
| Szenario | Rundenzeit Mittellinie | Rundenzeit Ideallinie | Verbesserung |
|---|---:|---:|---:|
| konservativ (mu=1.0) | 110.52s | 100.51s | 10.0s (9.1%) |
| optimistisch (mu=1.3) | 99.54s | 90.99s | 8.55s (8.6%) |

Ideallinien-Laenge 2369.1m (Mittellinie: 2490.0m) - kuerzer, wie erwartet
durch Kurvenschneiden. Plot (`results/spreewaldring_racing_line.png`)
zeigt visuell plausibles Aussen-Innen-Aussen an allen Kurven, keine
Selbstueberschneidungen/Oszillationen.

**Outputs:** `results/spreewaldring_racing_line.png` (Ideallinie
kruemmungsradius-eingefaerbt auf Orthofoto), `results/spreewaldring_racing_
line_speed_trace.png` (Geschwindigkeitsvergleich Mittellinie/Ideallinie),
`results/spreewaldring_racing_line_summary.json`.

**Aufruf:** `.venv/bin/python scripts/spreewaldring_racing_line.py`

**EINSCHRAENKUNGEN:**
- Kruemmungsminimierung ist ein bekannter, aber NAEHERUNGSWEISER Proxy fuer
  minimale Rundenzeit, kein echtes Optimalsteuerungsproblem - an Kurven mit
  sehr unterschiedlicher Geschwindigkeit vorher/nachher waere eine
  zeitoptimale Linie tendenziell anders (spaeterer Scheitelpunkt fuer
  bessere Kurvenausgangsbeschleunigung).
- Fahrzeugbreite (1.74m) ist eine Annahme, nicht fuer G184 verifiziert.
- Gleiche Einschraenkungen wie `spreewaldring_lap_simulation.py` (kein
  kombinierter Reifenkraftkreis, kein Gewichtstransfer, mu-Bandbreite ist
  Literaturschaetzung).
- Nur Hauptschleife (gruenes Polygon) - keine Ideallinie fuer Boxengasse/
  Verbindungsstuecke.
- Geschwindigkeitsvergleich-Plot legt beide Kurven ueber dieselbe
  s-Achse, obwohl Ideallinie (2369m) und Mittellinie (2490m)
  unterschiedliche Gesamtlaenge haben - Positionen sind daher nur
  naeherungsweise vergleichbar, kein Punkt-fuer-Punkt-Abgleich.

## Alternierende Linie/Geschwindigkeits-Optimierung + interaktive Visualisierung (NEU, 30.08.2026)

Nutzerfrage: "Wie aufwendig wird das, wenn ich dich bitte, eine tatsaechliche
Bestzeitrunde ... zu berechnen." Empfehlung (vor Umsetzung mit Nutzer
abgestimmt): kein volles Optimalsteuerungsproblem, sondern ein alternierendes
Verfahren (Linie<->Geschwindigkeit abwechselnd, plus kombinierter
Reifenkraftkreis) - Nutzer stimmte zu, bat zusaetzlich um eine visuelle
Darstellung des Optimierungsprozesses zum Zusehen.

**Neues Skript `scripts/spreewaldring_racing_line_optimal.py`:**
- `simulate_lap_combined_friction()`: wie das bisherige Zweipass-Rundenzeit-
  modell, aber mit KOMBINIERTEM Reifenkraftkreis (a_long_verfuegbar =
  sqrt((mu*g)^2 - a_lat^2), a_lat=v^2/R) statt unabhaengiger Laengs-/
  Quergrenze - reduziert sich bei a_lat=0 exakt auf das alte Modell. "Nicht
  bremsen wenn Vollgas moeglich" war schon im alten Zweipass-Modell erfuellt
  (keine neue Arbeit noetig).
- `optimize_alternating()`: 24 aeussere Iterationen (Geschwindigkeit bei
  fixierter Linie loesen -> Linie anhand Querbeschleunigungs-Auslastung
  lokal nachjustieren, hoeher ausgelastete Punkte staerker Richtung
  groesserem Radius gezogen). Bei jeder Iteration wird ein Snapshot
  gespeichert (fuer die Visualisierung).
- WICHTIGER BEFUND: das Verfahren konvergiert NICHT monoton - Rundenzeit
  verbessert sich zunaechst stark (Iteration 0->3: 99.6s->88.4s bei
  mu=1.3), driftet danach ueber weitere Iterationen wieder leicht nach oben
  (bis 91.8s bei Iteration 23). Fix: beste ueber alle Iterationen gefundene
  Linie wird verwendet (`best_snapshot`), nicht blind die letzte.

**Ergebnis:**
| Modell | mu=1.0 | mu=1.3 |
|---|---:|---:|
| Mittellinie | 110.52s | 99.54s |
| Kruemmungsminimierung | 100.51s | 90.99s |
| Alternierend optimiert | 96.03s | 86.98s |

Nochmal ~4.5% schneller als reine Kruemmungsminimierung (Iteration 3 von 24,
mu=1.3 waehrend der Optimierung selbst verwendet).

**Interaktive Visualisierung** (Nutzerwunsch "dem Optimierungsprozess
zusehen"): da die gesamte Rechnung nur ~3.5s dauert, ist eine ECHTE
Live-Ansicht waehrend der Rechenzeit nicht sinnvoll - stattdessen alle 24
Iterations-Snapshots gespeichert und als abspielbare/scrubbare Animation
aufbereitet (Canvas-Zeichnung, kein Orthofoto-Hintergrund - bewusst
schematische Streckendarstellung statt Foto, siehe unten). Als Artifact
veroeffentlicht: https://claude.ai/code/artifact/7e889184-86c6-4e0d-b8fd-bdf83798accc
(Titel "Ideallinie Spreewaldring") - Track-Ribbon + nach Geschwindigkeit
eingefaerbte Linie je Iteration, Konvergenz-Sparkline mit Markierung der
besten Iteration, Play/Pause/Scrub-Steuerung, Endergebnis-Vergleichstabelle
beider mu-Szenarien.

Design: dunkles "Telemetrie-Konsolen"-Thema (bewusst einthemig, kein Hell/
Dunkel-Umschalten - passend zum Motorsport-Telemetrie-Referenzpunkt),
Big Shoulders Display/IBM Plex Sans/IBM Plex Mono. Beim Bau zwei Bugs
gefunden+behoben: fehlendes `<meta charset="utf-8">` (Sonderzeichen wie
"·"/"ü" wurden falsch dargestellt), und eine zu weit gefasste CSS-Regel
(`canvas{position:absolute...}` traf ALLE Canvas-Elemente statt nur die
Hauptkarte, riss die kleine Konvergenz-Sparkline aus ihrem Panel) - beide
lokal per Preview-Server nachgestellt und verifiziert, bevor final
veroeffentlicht.

**Outputs:** `results/spreewaldring_racing_line_optimal_summary.json`
(finale Linie, alle 24 Snapshots, Vergleichszahlen beider mu-Szenarien).

**Aufruf:** `.venv/bin/python scripts/spreewaldring_racing_line_optimal.py`

**EINSCHRAENKUNGEN:**
- Weiterhin NICHT das volle Optimalsteuerungsproblem - die Linien-Update-
  Heuristik ist ein plausibler, nicht mathematisch bewiesener Proxy fuer
  "Richtung minimaler Rundenzeit".
- Kraftkreis nutzt EINEN mu-Wert fuer Laengs- UND Querrichtung (isotroper
  Reifen).
- Kein Gewichtstransfer, keine Aero - gleiche Einschraenkungen wie die
  vorherigen Rundenzeit-Modelle.
- Fahrzeugbreite (1.74m) weiterhin Annahme.
- Konvergenz ueber feste Iterationsanzahl (24), kein formales
  Abbruchkriterium - "beste gefundene Iteration" ist ein Sicherheitsnetz,
  kein Beweis lokaler Optimalitaet.
- Animation nutzt nur das mu=1.3-Szenario (mit dem optimiert wurde) fuer
  die Snapshot-Sequenz; die Vergleichstabelle zeigt aber beide Szenarien
  (mu=1.0 separat auf der finalen Linie berechnet, nicht animiert).

## Lokale Kurven-Nachoptimierung + Orthofoto/Kurvennummern in der Animation (NEU, 30.08.2026)

Nutzerfragen: (1) "koennen wir ... auch einzelne kurven optimieren lassen?
also die optimierung nicht auf den gesamten kurs anwenden?", (2) "kannst du
mir das luftbild hinter die strecke legen und die kurven nummerieren?",
(3) "kannst du mir das auch lokal starten lassen ohne artifact?"

**Lokale Kopie ohne Artifact:** `results/spreewaldring_racing_line_animation.html`
ist eine vollstaendig eigenstaendige Kopie derselben Seite (alle Daten
eingebettet) - kann direkt per Doppelklick/Drag&Drop in einen Browser
geoeffnet werden, kein Server noetig (einzige externe Abhaengigkeit: Google
Fonts, faellt bei fehlender Verbindung auf System-Schrift zurueck). Wird bei
jeder Aktualisierung mit-aktualisiert.

**Orthofoto-Hintergrund + Kurvennummern:** Crop der LGB-Orthofoto-Kachel
(Fahrbahn-Bounding-Box + 20m Rand, auf 1400px Kantenlaenge verkleinert,
JPEG q=82, ~330KB) als Base64 in die Seite eingebettet, per Canvas
`drawImage()` hinter der Streckenkontur gezeichnet (dezentes Scrim
rgba(18,23,27,0.32) darueber fuer Lesbarkeit von Linie/Zahlen). Fahrbahn-
Ribbon-Fill entfernt (nur noch Konturlinie), damit das Foto durchscheint.
Kurvennummern aus der bereits vorhandenen Kurvenerkennung
(`corners_refined` in `spreewaldring_track_summary.json`), nach s_apex_m
sortiert, durchnummeriert - EINE Korrektur noetig: die letzte Kurve (s=2487m)
und die erste (s=0m) sind wegen des zyklischen Schliesspunkts dieselbe
physische Kurve (nur 2.8m auseinander, die lineare Merge-Logik in
`find_corners()` erkennt das nicht, da sie nicht zyklisch ueber den
Array-Rand hinweg prueft) - manuell in der Export-Logik zusammengefuehrt
(11 statt 12 Kurven). Kurve 1 liegt korrekt am Start-Ziel-/Boxengassen-
Bereich (top-rechts, UTM ~409975/5761665) - passt zur urspruenglichen
Streckenrekonstruktion.

**Neues Skript `scripts/spreewaldring_racing_line_corner.py`:** lokale
Nachoptimierung EINER Kurve, aufbauend auf der besten globalen Iteration
(3) aus `spreewaldring_racing_line_optimal.py`. Nur Punkte in einem
Fenster um die Kurve werden bewegt (Hann-Fenster/Cosinus-Rampe fuer
sanften Uebergang zur eingefrorenen Restrecke), der Rest bleibt exakt
eingefroren - Geschwindigkeitsloesung laeuft weiterhin ueber die GESAMTE
Runde (Bremszonen haengen von der naechsten Kurve ab).

WICHTIGE BESONDERHEIT Kurve 1: liegt genau am Schliesspunkt der Schleife
(Index 0 = Index 829) - das Fenster muss ZYKLISCH ueber den Schliesspunkt
gebildet werden, sonst fehlt die Anfahrt von der Start-Ziel-Geraden
(Nutzerwunsch: "Kurve 1 wirklich von aussen anfahren, nahe der
Boxenmauer"). Geloest ueber Modulo-Indizierung (Fenster s=[-150m,+216.5m]
relativ zum Apex, wickelt sich um Index 0 herum in die letzten ~150m der
Start-Ziel-Geraden) - OHNE die globale Streckenparametrisierung
(LOOP_START_NODE_IDX) zu aendern, die in allen anderen Skripten/JSON-
Dateien verwendet wird.

BUG GEFUNDEN+BEHOBEN waehrend der Entwicklung: erster Versuch der
Endauswertung vergass, die lokal veraenderte Linie vor der Rundenzeit-
Berechnung auf gleichmaessige Bogenlaenge zu resamplen (die Punktabstaende
im Fenster sind nach der Optimierung nicht mehr exakt 3m) - `simulate_lap()`
nimmt aber Gleichverteilung an, dadurch zunaechst absurde Ergebnisse
(Rundenzeit schien um 5s schlechter zu werden, obwohl die interne,
ds-korrekte Kraftkreis-Simulation eine leichte Verbesserung zeigte). Fix:
wie im globalen Skript vor der Endauswertung `resample_closed_loop()`
anwenden.

**Ergebnis Kurve 1** (R=24.0m, moderate Kurve, war bereits Teil der
globalen Optimierung): Verbesserung nur marginal (+0.04s bei mu=1.0,
-0.02s/leicht schlechter bei mu=1.3 - im Rauschbereich der Diskretisierung).
Nachvollziehbar: Kurve 1 ist keine besonders enge Kurve, die globale
Optimierung hatte sie schon gut erfasst. Fuer eine deutlichere Demonstration
waere eine engere Kurve (z.B. Kurve 8, R=11.5m) ein besserer Kandidat -
noch nicht probiert.

**Animation erweitert um zweite Phase:** `results/spreewaldring_racing_line_animation.html`
zeigt jetzt Phase 1 (Iterationen 0-23, globale Optimierung) UND Phase 2
(ab Iteration 24, lokale Kurve-1-Nachoptimierung) in derselben Zeitleiste/
demselben Scrubber - Phasenbezeichnung im Readout, gestrichelte Trennlinie
in der Konvergenz-Sparkline, aktives Optimierungsfenster wird auf der
Karte dezent hervorgehoben (nur waehrend Phase 2 sichtbar). "Beste
gefundene Iteration" wird jetzt dynamisch ueber BEIDE Phasen berechnet
(nicht mehr aus der alten, nur-global gueltigen `best_iteration`-Kennzahl).

**Aufruf:** `.venv/bin/python scripts/spreewaldring_racing_line_corner.py`
(optimiert aktuell Kurve 1, siehe `CORNER_WINDOWS_M` fuer weitere Kurven -
Fenstergrenzen muessen pro Kurve manuell festgelegt werden, kein
automatisches Fenster-Sizing).

**Outputs:** `results/spreewaldring_racing_line_corner1_summary.json`.

## Zwei neue Logs vom 31.08.2026 + Ausrollversuch-Fund (31.08.2026)

Zwei neue .dlg-Logs aus Google Drive nachgeladen: `2026-08-31 081916.dlg`
(morgens, 08:19 Uhr) und `2026-08-31 152038.dlg` (nachmittags, 15:20 Uhr).
Masse pro Log NICHT pauschal angenommen, sondern aus dem geloggten
Tankfuellstand-Kanal `FLI` bestimmt (Nutzerangabe: Tankvolumen 45l, nur
Fahrer 86kg, kein Beifahrer/Gepaeck): Mittel aus Start-/End-FLI-Wert je
Fahrt * 45l * ~0.745 kg/l Benzindichte. Ergebnis: 081916 -> 1176.5 kg
(FLI 54.3%->50.0%), 152038 -> 1174.8 kg (FLI 50.0%->44.1%). Als
`LOG_MASS_OVERRIDE_KG`-Dict in `drivetrain_model_validation.py` UND
`top_speed_validation.py` hinterlegt (gleiches Muster wie der bestehende
Suedtirol-Sonderfall), alle massenabhaengigen Berechnungen fuer diese
beiden Logs nutzen jetzt diesen Wert statt der pauschalen MASS_KG=1180.705.

**Volle Standard-Pipeline auf beiden Logs durchlaufen:**
- `vibration_analysis.py` (IMU): beide bestaetigen die bekannte
  Halterungsresonanz sauber auf allen 3 Achsen (081916: 20.46 Hz;
  152038: 20.85-21.39 Hz) - kein Ausreisser, passt ins etablierte
  21-23 Hz-Muster.
- `brake_event_analysis.py` (Achsenrotation theta): 081916 -> 65.2°
  (R=0.789, n=14, im ueblichen Bereich). **152038 -> 80.4° (R=0.671,
  n=29)** - liegt am oberen Rand der bisherigen Spanne (41.8-80.4°) UND
  mit spuerbar niedrigerer Konzentration (R) als sonst ueblich
  (0.84-0.92) - moeglicherweise die Halterung zwischendurch leicht
  verstellt oder mehr Trail-Braking/gemischte Ereignisse in dieser
  Fahrt. Nicht weiter untersucht, aber bei spaeterer a_lat/a_long-Nutzung
  dieses Logs im Hinterkopf behalten.
- `corner_event_analysis.py` (Kurven): 081916 -> 1 bestaetigte Kurve
  (links, 60.9 km/h, |a_lat| peak 0.36g). 152038 -> 3 bestaetigte Kurven
  (alle rechts, 42-56 km/h, |a_lat| peak 0.82-1.07g). Beide unauffaellig,
  passend zur bisherigen Beobachtung "wenige eindeutige Kurven pro Fahrt".
- `partial_load_model.py` (Teillast): beide Logs liefern brauchbare
  `ActualEnginePercentTorque`-Daten (jetzt 16 statt 14 Logs Kennfeld-Basis),
  Leave-one-log-out-RMSE fuer beide im ueblichen Rahmen (081916: 6.3%,
  152038: 7.0% - Median-Fehler beider unter dem Gesamt-RMSE von 7.1
  Prozentpunkten).
- `drivetrain_model_validation.py` (Vollast): kinematischer Check beider
  Logs unauffaellig (Median|Fehler| 0.40% / 0.44%, wie alle Kern-Logs).
  Volllast-Segmente: 081916 nur 1 (kurze Fahrt/wenig Vollgas), 152038
  gleich 8 Segmente. Gesamtstatistik jetzt 53 statt 44 Segmente,
  Korrelation weiterhin 0.99, Verhaeltnis gemessen/Modell Median 0.94 -
  der bekannte ~6-8%-Bias bleibt stabil, keine Verzerrung durch die
  neuen Logs.
- `top_speed_validation.py`: ein neues Gang-6-WOT-Segment aus 152038
  (154->181 km/h, 11.3s, +0.96% Steigung) - Modell trifft mit
  Gefaellekorrektur gut (gemessen 0.668 vs. Modell bias-korr. 0.500 m/s²,
  im ueblichen Streubereich).

**Besonderer Fund (Nutzeranfrage): echter Ausrollversuch bei 160-175 km/h
im Nachmittagslog.** Neues Skript `scripts/coastdown_analysis.py`
gebaut, um das systematisch zu erfassen (findet automatisch jede Phase
mit Gang=0, APP=0, Kupplung=0, keine Bremsung, in JEDEM Log - nicht nur
diesem einen). Ergebnis ueber alle 58 Logs: **3 solche Ereignisse**, davon
das mit Abstand aussagekraeftigste exakt das vom Nutzer vermutete:

**`2026-08-31 152038`, t=1756.8-1767.3s (10.5s), 166->144 km/h.** Ablauf
(aus den Rohdaten rekonstruiert): bei ~168 km/h im 6. Gang Gas
weggenommen, ausgekuppelt, in Neutral geschaltet (t=1756.2s), Motor
faellt von ~3520 U/min frei (nicht mehr an Antriebsstrang gekoppelt) auf
Leerlaufdrehzahl (~1050-1250 U/min, stabilisiert sich bereits bei 160
km/h) - ab da bis 144 km/h ein sauberes, durchgaengiges Leerlauf-Ausrollen
ueber 10.5s, danach wieder eingekuppelt/Gang eingelegt.

Physikalisch besonders wertvoll: OHNE Motoreingriff wirkt NUR Luft- +
Rollwiderstand auf die Verzoegerung - im Gegensatz zu allen bisherigen
Volllast-/Teillast-Checks ist das Ergebnis **komplett unabhaengig vom
Wirkungsgrad eta** (der bisher mit CdA "gekoppelt, nicht unabhaengig
identifiziert" im externen Referenzdokument als offener Punkt gilt).

**Gefaelle/Steigung UND Wind geprueft (beides Nutzer-Nachfragen) und
beruecksichtigt:** `coastdown_analysis.py` nutzt fuer die Steigung
dieselbe DGM-Infrastruktur wie `top_speed_validation.py`
(`segment_grade()`, bruecken-sicher). Ergebnis fuer dieses Segment:
**-0.18% Gefaelle** ueber 375m (10 GPS-Punkte, Quelle Brandenburg-ALS/
Berlin-WMS) - praktisch eben, nur -21N Gravitationsanteil, erklaert die
Diskrepanz NICHT.

**Wind war der eigentlich grosse Faktor.** Nutzer-Nachfrage "nicht dass
wir Rueckenwind hatten" (31.08.2026) - berechtigt: `coastdown_analysis.py`
fragt jetzt historische Stundenwinddaten (Open-Meteo Archive-API, 10m
Hoehe, gecacht in `data/weather/openmeteo_wind_cache.json`) fuer Ort+
Zeit jedes Ereignisses ab und berechnet die Rueckenwind-Komponente
entlang der Fahrtrichtung (Kanal `Lager` = GPS-Kurs). Fuer dieses
Segment (31.08.2026, 15:50 Uhr lokal, ~52.648°N/13.243°E, noerdlich
Berlin/Kremmen-Korridor, Fahrtrichtung ~5°=Nord): **Wind 16.2 km/h aus
224° (Suedwest)**, das ergibt eine **Rueckenwind-Komponente von +12.6
km/h** (effektive Anstroemgeschwindigkeit v_air also 142 statt 155 km/h
ueber Grund) - ein erheblicher Teil der urspruenglich gefundenen
Diskrepanz war schlicht Wind, kein CdA/Crr-Fehler.

Least-Squares-Fit von CdA/Crr an die gemessene, gefaelle- UND
windkorrigierte v(t)-Kurve (RK4-Integration, Luftwiderstand haengt jetzt
von v_air statt v_ground ab, Details im Skript-Docstring):

| | CdA [m²] | Crr | RMSE v(t) |
|---|---|---|---|
| gefittet (mit Wind+Gefaelle) | 0.444 | 0.0248 | 0.26 km/h |
| Projekt-Referenz, MIT Windkorrektur | 0.647 | 0.0130 | 1.02 km/h |
| Projekt-Referenz, OHNE Windkorrektur (alter Stand) | 0.647 | 0.0130 | 2.89 km/h |

Bei mittlerer Geschwindigkeit (155 km/h, Gefaelleanteil -21N bereits
herausgerechnet): **gemessene Gesamt-Widerstandskraft ~694 N (Luft 408 +
Roll 286). Referenzmodell MIT Windkorrektur sagt 745 N voraus (Luft 595 +
Roll 150) - Ueberschaetzung nur noch ~7%** (vorher, ohne Windkorrektur,
faelschlich auf 855N/~23% geschaetzt - der Wind erklaert also den
GROSSTEIL der urspruenglichen Diskrepanz, aber nicht alles).

**Wichtige Einordnung fuer den bekannten ~6-8%-Bias:** die verbleibende
~7%-Ueberschaetzung des Widerstands (nach Wind+Gefaelle-Korrektur) liegt
GENAU in der Groessenordnung des bereits bekannten WOT-Bias (gemessen/
Modell=0.94). Das ist auf den ersten Blick verlockend als "beide Effekte
sind derselbe CdA/Crr-Fehler" zu lesen - stimmt aber bei genauer
Vorzeichenbetrachtung NICHT: ein zu hoch angesetzter Widerstand wuerde
das WOT-Modell tendenziell UNTERschaetzen lassen (mehr Kraft geht
"verloren"), der beobachtete WOT-Bias geht aber in die GEGENTEILIGE
Richtung (Modell UEBERschaetzt die Beschleunigung). Damit die beiden
Effekte (zu hoher Modell-Widerstand UND Modell-Overshoot bei WOT)
gleichzeitig stimmen, muss der Fehler bei eta (Wirkungsgrad, aktuell
0.93 angenommen) sogar GROESSER sein als der rohe 6-8%-Bias vermuten
laesst - die Ausroll-Messung wirkt dem WOT-Bias entgegen, nicht mit ihm.
Diese Schlussfolgerung ist nach der Windkorrektur inhaltlich UNVERAENDERT
(nur die Groessenordnung der CdA/Crr-Abweichung selbst ist jetzt viel
moderater: ~7% statt der urspruenglich faelschlich ermittelten ~23%).
NICHT weiter aufgeloest (eta selbst wurde nicht neu gefittet).

**Einschraenkung:** ein einzelnes ~22 km/h breites Ausrollfenster
constrained CdA und Crr nur schwach gegeneinander (beide wirken im selben
Geschwindigkeitsbereich aehnlich stark) - die GESAMT-Widerstandskraft bei
der gemittelten Geschwindigkeit ist die verlaesslichere Kennzahl als die
einzelnen gefitteten CdA/Crr-Werte. Die Windkorrektur selbst hat eigene
Unsicherheiten (Open-Meteo-Stundenwerte auf 0.1°-Ortsraster interpoliert,
10m-Referenzhoehe statt tatsaechlicher Fahrzeughoehe, als ueber die
~10.5s konstant angenommen) - bei einer verbleibenden Diskrepanz von nur
~7% ist Restunsicherheit aus der Windabschaetzung selbst nicht
auszuschliessen. Die zwei anderen gefundenen Ereignisse (2026-08-27
170339 bei 43-52 km/h mit sehr starkem Rueckenwind +15.8 km/h, und ein
kuerzeres 4.6s-Fenster 144->136 km/h im selben 152038-Log) sind zu
kurz/langsam fuer einen belastbaren CdA-Fit (CdA lief beim Fit an die
untere Grenze 0.1 m² - Geschwindigkeitsbereich zu schmal, um Luft- von
Rollwiderstand zu trennen) - nur das Hauptereignis (166->144 km/h) ist
belastbar genug fuer die obige Einordnung.

Outputs: `results/coastdown_analysis_summary.json`,
`results/coastdown_2026-08-31_152038_1757s.png` (Hauptereignis, jetzt mit
Wind-Referenzlinien), plus zwei weitere PNGs fuer die schwaecheren
Ereignisse. Wind-Rohdaten gecacht in `data/weather/openmeteo_wind_cache.json`
(ein API-Aufruf pro Tag+Ort, weitere Analysen brauchen kein erneutes Netz).

**NAECHSTER SCHRITT (Empfehlung, nicht umgesetzt):** falls der Nutzer
gezielt weitere Ausrollversuche faehrt (Vollgas freikuppeln von hoher
Geschwindigkeit moeglichst bis zum Stillstand, mehrfach), liesse sich CdA
und Crr sauber getrennt bestimmen und der eta-Verdacht direkt pruefen -
waere die naechste sinnvolle Messfahrt fuer dieses Projekt.

## Vier neue Logs vom 01.09.2026: Lenkwinkel-Kanal endlich da + zweiter Ausrollversuch-Fund

Vier neue .dlg-Logs: `2026-09-01 070451.dlg`, `152031.dlg`, `162514.dlg`,
`164308.dlg`. Masse wie etabliert aus `FLI` bestimmt, diesmal robuster
(Median der ersten/letzten 10 Samples statt Einzelwert - noetig, weil
`162514` sehr verrauschte FLI-Werte zeigt, vermutlich Tanksloshing bei
einer dynamischeren Fahrt): 1172.6 / 1171.3 / 1168.3 / 1169.4 kg.

**GROSSER FUND: `STEER_ANGL_EPS` (Lenkwinkel) + `STEER_SPD_EPS`
(Lenkgeschwindigkeit) sind in allen 4 Logs vorhanden!** Der Nutzer hatte
einen gezielten Versuch angekuendigt, das ist das Ergebnis - erfolgreich.
Werteplausibilisiert: Bereich ca. ±390 bis ±527°, Nullpunkt nahe 0
(Mittelwerte -55 bis +8° je Log) - passt zu einem Lenkradwinkel bei einem
Auto mit ~2.5-3 Lenkradumdrehungen von Anschlag zu Anschlag (MX-5 ND).

**Sofort-Kreuzvalidierung gegen die bereits bestaetigten Kurven aus
`corner_event_analysis.py`** (5 Rechtskurven in `2026-09-01 152031`):
4 von 5 zeigen waehrend des Kurvenfensters klar NEGATIVE
`STEER_ANGL_EPS`-Werte (bis -150°) - konsistentes Vorzeichen
(negativ=rechts in dieser PID-Konvention) ueber alle vier. Die fuenfte
(t=1602-1604s, v=71.7 km/h, nur 0.61g peak - die schwaechste der 5,
vermutlich sanfte Autobahn-Ausfahrt) zeigt nur 0.2-3.7° - physikalisch
plausibel (bei hoher Geschwindigkeit reicht ein kleiner Lenkwinkel fuer
denselben Kurvenradius). **Erste Einschaetzung: das Signal sieht sauber
und belastbar aus** - im Gegensatz zum bisherigen Gyroskop-Ansatz
(Halterungswackeln dominiert dort das Signal bei normaler Fahrt, siehe
"Quergrip-/g-g-Auswertung"-Abschnitt oben) sollte ein direkter
Lenkwinkelsensor davon UNABHAENGIG und damit potenziell fuer eine
KONTINUIERLICHE Kurvenerkennung/-analyse geeignet sein - noch nicht
weiter ausgebaut (Kalibrierung Lenkuebersetzung -> Radeinschlag,
Korrelation gegen GPS-Kurs bei schwachen Kurven, Vorzeichenkonvention
end-to-end pruefen), aber ein sehr vielversprechender naechster Schritt
fuer die Quergrip-Frage.

**Standard-Pipeline durchgelaufen** (IMU/Kurven/Bremsereignisse/
Teillast/Vollast), alle 4 Logs unauffaellig:
- IMU: 17.8-22.4 Hz Resonanz auf allen Achsen, passt ins bekannte Muster.
- Achsenrotation theta: 78.2°/59.0°/40.8°/83.1° (R=0.75-0.96) - im
  bisherigen Streubereich (jetzt [40.8, 83.1]° ueber 13 Logs).
- Kurven: 0/5/0/0 bestaetigte Kurven (152031 die einzige mit laengerem
  kurvigem Streckenabschnitt).
- Teillast-Kennfeld: jetzt 20 statt 16 Logs mit Drehmoment-Kanal,
  Gesamt-RMSE 7.0pp (stabil). Alle 4 neuen Logs unauffaellig (RMSE
  5.8-7.1%). NEBENBEFUND (nicht abschliessend geklaert): `2026-08-25
  081538` zeigt in dieser Kreuzvalidierungsrunde nur noch n=242 statt
  vorher n=4453 bei aehnlichem RMSE - vermutlich verschiebt sich die
  Menge der "robusten" (ETC,RPM)-Bins durch die neuen Logs, noch nicht
  tiefer untersucht, keine Aenderung am Kennfeld-Ergebnis selbst.
- Vollast: 57 statt 53 Segmente (4 neue aus 152031 (3x) und 164308
  (1x)), Korrelation 0.98, Verhaeltnis gemessen/Modell Median weiterhin
  0.94 - Bias bleibt stabil.
- Kein neues Gang-6-Vmax-Segment (keine der 4 Fahrten mit sustained
  >=170 km/h in Gang 6).

**Zweiter, WEIT BESSERER Ausrollversuch gefunden:
`2026-09-01 152031`, t=2464.3-2487.2s, 145->104 km/h, 23.0s.** Mit
Abstand das breiteste bisher gefundene Fenster (41 km/h Spanne
statt vorher max. 22 km/h) - deutlich bessere CdA/Crr-Trennschaerfe.
Gefaelle -0.17% (vernachlaessigbar), Fahrtrichtung sehr stabil bei 338°
(Std nahezu 0 ueber die ganze Ereignisdauer, siehe Rohdaten-Check).

**Ueberraschung: das Referenzmodell (CdA=0.647 m², Crr=0.013) trifft
die Messung hier PERFEKT, aber NUR OHNE Windkorrektur** (RMSE ohne Wind
0.40 km/h, MIT Wind-Korrektur 1.97 km/h - deutlich schlechter). Open-Meteo
meldete fuer diesen Zeitpunkt/Ort einen Gegenwind-Anteil von -7.0 km/h
(Wind 20.7 km/h aus 268°, Fahrtrichtung 338°) - dieser scheint fuer das
konkrete 23s-Fenster nicht zuzutreffen (Stundenaufloesung von Open-Meteo
kann kurzfristige Boeen/lokale Windverhaeltnisse nicht abbilden, siehe
Boen bis 40+ km/h an manchen Tagen dieser Woche). Gefitteter CdA=0.531 m²/
Crr=0.0163 liegt nahe an den Referenzwerten - der Fit “kompensiert”
teilweise den fehlerhaften Windterm, ist also selbst nicht 100% sauber,
aber die Kernaussage (Referenzmodell OHNE Wind passt hier fast perfekt)
ist robust, weil sie direkt aus den Rohdaten ablesbar ist (Plot, siehe
unten), nicht nur aus dem Fit.

Physikalische Gegenprobe ohne jedes Modell: Δv=41 km/h=11.39 m/s in
23.0s -> a=0.495 m/s² -> F=580 N bei 1171.3 kg. Referenzmodell (CdA=0.647,
Crr=0.013, ohne Wind) bei v_mean=124.5 km/h sagt 607 N voraus - Differenz
nur 27N (~4.5%), passt gut zur obigen Einschaetzung.

**Einordnung im Vergleich zum ersten Ausrollversuch (31.08., siehe oben):
das Bild ist jetzt weniger eindeutig als zunaechst gedacht.** Der erste
(schmalere, 22 km/h) Versuch zeigte eine ~7%-Widerstandsueberschaetzung
NACH Windkorrektur. Der zweite (breitere, statistisch deutlich
belastbarere, 41 km/h) Versuch zeigt dagegen eine nahezu PERFEKTE
Uebereinstimmung mit dem Referenzmodell, aber nur OHNE Windkorrektur -
und die Windkorrektur-Qualitaet selbst (Open-Meteo Stundenwerte) ist bei
kurzen Ereignissen (Sekunden bis Minuten) offenkundig nicht praezise
genug, um verlaesslich zwischen "kleiner CdA/Crr-Fehler" und
"Windschaetzungs-Unsicherheit" zu unterscheiden. **Vorlaeufiges Fazit:
CdA=0.647/Crr=0.013 sind wahrscheinlich naeher an der Realitaet als der
erste Versuch (mit seiner grossen Windkorrektur) vermuten liess** - die
eta-Hypothese fuer den ~6-8%-WOT-Bias bleibt die einfachste Erklaerung
(sauberer sogar: wenn CdA/Crr korrekt sind, muss der GESAMTE WOT-Bias auf
eta zurueckgehen, keine Gegenrechnung mehr noetig), ist aber weiterhin
NICHT abschliessend bewiesen. Fuer eine robuste Antwort waeren mehrere
breite (>=30 km/h) Ausrollversuche noetig, idealerweise bei WENIG Wind
(Wetterbericht vorher pruefen) oder mit einer praeziseren lokalen
Windmessung als stuendlichen Reanalyse-Daten.

Zwei weitere, schmalere Ausrollfenster im selben Log (116->109 km/h,
5.3s und 103->94 km/h, 8.0s) sind wie erwartet zu schmal fuer einen
belastbaren CdA-Fit (Crr lief beim Fit an die untere Grenze 0) - nicht
weiter verwertet.

Outputs: `results/coastdown_2026-09-01_152031_2464s.png` (das breite,
aussagekraeftige Ereignis), zwei weitere PNGs fuer die schmalen
Ereignisse, `results/coastdown_analysis_summary.json` aktualisiert (6
Ereignisse gesamt ueber 62 Logs).

**NACHTRAG (Nutzer-Einwand, berechtigt): Windschatten-Effekt (Draften
hinter einem Vordermann) nicht ausschliessbar - macht die Sache eher
UNSICHERER, nicht sicherer.** Position des breiten Ereignisses:
52.726-52.732°N / 13.216-13.212°E (noerdlich Berlin, Richtung 338°=NNW,
vermutlich A10-Ring oder B-Strasse im ueblichen Fahrgebiet). Die Logs
enthalten KEINEN Abstands-/Radarsensor oder sonstigen Kanal, der die
Anwesenheit eines Vorderfahrzeugs pruefen koennte - weder bestaetigbar
noch ausschliessbar mit den vorhandenen Daten.

Wichtig fuer die Einordnung: Windschatten wuerde den gemessenen
Luftwiderstand KUENSTLICH SENKEN - genau in dieselbe Richtung wie ein zu
klein angesetztes CdA. Das bedeutet: die vorherige Schlussfolgerung "das
breite Ereignis bestaetigt CdA=0.647/Crr=0.013 als vermutlich korrekt"
ist NICHT robust gegen diesen Effekt - waere waehrend dieses Ereignisses
tatsaechlich gedraftet worden, wuerde das echte (windschatten-freie) CdA
sogar HOEHER als 0.647 liegen, nicht niedriger, und die "perfekte
Uebereinstimmung ohne Windkorrektur" waere dann eher Zufall (zwei sich
teilweise aufhebende Effekte: echtes CdA hoeher als angenommen,
Windschatten senkt den gemessenen Wert wieder auf den angenommenen
Referenzwert) statt eine echte Bestaetigung.

**Konsequenz: das Gesamtbild aus den bisherigen 2 aussagekraeftigen
Ausrollversuchen bleibt uneindeutig - nicht nur wegen der
Windschaetzungs-Genauigkeit (siehe oben), sondern jetzt zusaetzlich wegen
des nicht kontrollierbaren Windschatten-Risikos.** Fuer eine wirklich
belastbare CdA/Crr-Bestimmung braucht es weiterhin mehrere breite
Ausrollversuche - UND dabei nach Moeglichkeit auf freier Strecke ohne
Vorderfahrzeug in Sichtweite (Nutzer kann das beim Fahren selbst
sicherstellen/vermerken, auch ohne zusaetzliche Sensorik). Bis dahin
sind BEIDE bisherigen CdA/Crr-Ablesungen aus den Ausrollversuchen mit
Vorsicht zu behandeln, die eta-Hypothese fuer den WOT-Bias bleibt eine
plausible, aber nicht bewiesene Arbeitshypothese.

## Kontinuierliche Quergrip-Schaetzung aus Lenkwinkel — erste Version (01.09.2026)

Nutzeranfrage nach der Gyro/Lenkwinkel-Korrelationspruefung (siehe oben):
"kannst du das bauen, bitte" - gemeint war ein Modell, das den neuen
Lenkwinkel-Kanal (`STEER_ANGL_EPS`) nutzt, um KONTINUIERLICH (nicht nur
in den paar bestaetigten Kurvenereignissen) eine Querbeschleunigung zu
schaetzen. Neues Skript `scripts/steering_lateral_model.py`.

**Methodik:** einfaches kinematisches Modell ohne Schwimmwinkel-Korrektur,
`Giergeschwindigkeit = k * Lenkwinkel * Geschwindigkeit`, `a_lat = v *
Giergeschwindigkeit`. k (fasst Lenkuebersetzung und Radstand in einer
Konstante zusammen, beide nicht einzeln bekannt) wird gegen die bereits
GPS+Gyro-kreuzvalidierten Kurven aus `corner_event_analysis.py` kalibriert
(lineare Regression ohne Achsenabschnitt).

**Wichtiger Nebenfund/Bugfix beim Kalibrieren:** der erste Kalibrierlauf
zeigte einen deutlichen Ausreisser (Lenkwinkel nahezu 0° bei angeblich
+18.9°/s bestaetigter Gierrate, physikalisch nicht plausibel) -
Ueberpruefung der Rohdaten (`2026-09-01 152031`, t=1602-1604s) zeigte:
GPS-Genauigkeit (`Horz Genauigkeit`) lag kurz vor Fensterbeginn bei
**200m** (vermutlich frisch wiedererlangter GPS-Empfang), noch bei
8.7m am Fensterbeginn - `corner_event_analysis.py` hatte bisher GAR
KEINEN Genauigkeitsfilter (nur eine Mindestbasisstrecke als grobe
Plausibilitaets-Annahme). **Fix in `corner_event_analysis.py`:** neuer
Filter `GPS_MAX_HORZ_ACC_M = 20.0` (gleicher Schwellwert wie in
`top_speed_validation.py`), verwirft GPS-Fixe mit schlechterer Genauigkeit
VOR der Kursberechnung. Ergebnis nach Neulauf: **13 statt 15 bestaetigte
Kurven insgesamt** - der beschriebene Ausreisser ist weg (152031: 4 statt
5 Kurven), UND eine zusaetzliche, vermutlich ebenfalls unsichere Kurve in
`2026-08-31 081916` (war ohnehin die schwaechste dort, 0.36g peak) fiel
ebenfalls raus. **Direkter Nutzen des neuen Lenkwinkel-Kanals: deckt einen
echten methodischen Fehler in einem etablierten Skript auf**, nicht nur
ein neues Feature.

**Kalibrierergebnis (nach dem Fix):** n=4 Kalibrierpunkte (alle aus
`2026-09-01 152031`, ALLE Rechtskurven - keine Linkskurve in einem
Lenkwinkel-Log verfuegbar), k=0.01727, R²=-0.04 (schwach - siehe
Kalibrierplot `results/steering_lateral_calibration.png`: zwei
erkennbare Geschwindigkeits-Cluster (~40 und ~65 km/h), innerhalb derer
die Punkte grob konsistent sind, zwischen denen die einfache lineare
Form aber nicht perfekt passt - klassisches Untersteuerungs-Signatur-
Muster, das ein einfaches `k*Lenkwinkel*v`-Modell ohne
geschwindigkeitsabhaengigen Nenner nicht abbilden kann). Leave-one-out
RMSE 5.03°/s bei gemessenen Werten von 12-22°/s (~25-40% relativer
Fehler) - **k selbst ist noch UNSICHER, dringend mehr/breitere
Kalibrierpunkte noetig (v.a. Linkskurven!), sobald weitere Logs mit
Lenkwinkel UND bestaetigten Kurven vorliegen.**

**Aber: die eigentliche Staerke zeigt sich unabhaengig von der k-Praezision.**
Kontinuierliche a_lat-Spur ueber die ganze `2026-09-01 152031`-Fahrt
(46 Minuten) sieht sauber aus (`results/steering_lateral_2026-09-01_152031_trace.png`):
klarer Nulldurchgang bei Geradeausfahrt, deutliche, plausible Ausschlaege
bei echten Manoevern (Stadtverkehr 0-10 min, Kurvenpassagen 17-25 min,
Autobahnabschnitt ab 35 min ruhig), die 4 Kalibrierpunkte fallen sichtbar
mit den groessten Peaks zusammen. Rauschboden bei bestaetigter
Geradeausfahrt (|Lenkwinkel|<10°) ueber alle 4 Lenkwinkel-Logs: p95
zwischen 0.002g und 0.16g, std 0.001-0.06g - **deutlich sauberer als
der alte Gyro-Ansatz** (dort ~14°/s Wackel-Rauschen bei ebenfalls
bestaetigter Geradeausfahrt, siehe Abschnitt oben). Das ist der
eigentliche Fortschritt: nicht die absolute Praezision (die haengt noch
an der unsicheren k-Kalibrierung), sondern dass zum ersten Mal ein
DURCHGEHEND rauscharmes, plausibles Signal ueber eine ganze Fahrt
vorliegt statt nur einer Handvoll isolierter Ereignisse.

Pro Log Kennzahlen (|a_lat| ueber Fahrtabschnitte mit v>3m/s):
| Log | p50 | p90 | p99 | max | Rauschboden p95 (Geradeaus) |
|---|---|---|---|---|---|
| 070451 | 0.04g | 0.12g | 0.25g | 0.34g | 0.151g |
| 152031 | 0.03g | 0.14g | 0.35g | 0.79g | 0.160g |
| 162514 | 0.12g | 0.22g | 0.35g | 0.41g | 0.046g |
| 164308 | 0.19g | 0.56g | 1.49g | 1.57g | 0.002g |

(164308 faellt auf: deutlich hoehere Werte UND gleichzeitig der
sauberste Rauschboden - passt zu einer kurzen, dynamischen Fahrt mit
vielen echten Kurvenmanoevern, siehe auch die auffaellig verrauschten
FLI-Werte in diesem Log oben, moeglicherweise dieselbe "sportliche"
Fahrweise als gemeinsame Ursache. NICHT weiter untersucht.)

Outputs: `results/steering_lateral_model_summary.json`,
`results/steering_lateral_calibration.png`, vier
`results/steering_lateral_<log>_trace.png`.

**OFFEN / naechste Schritte:**
1. Mehr Kalibrierdaten sammeln (v.a. Linkskurven! aktuell 0 vorhanden)
   sobald weitere Lenkwinkel-Logs mit bestaetigten Kurven vorliegen -
   `steering_lateral_model.py` neu laufen lassen, kein Codeaenderung
   noetig (liest automatisch aus dem aktuellen Datalake/JSON).
2. Geschwindigkeitsabhaengigen Nenner (Untersteuerungsgradient) ins
   Modell aufnehmen, sobald genug Kalibrierpunkte ueber einen breiteren
   Geschwindigkeitsbereich vorliegen (aktuell nur 2 Cluster ~40/~65 km/h -
   zu wenig fuer einen zweiten freien Parameter).
3. Vorzeichen-/Symmetrieannahme (Links=Rechts) noch nicht empirisch
   geprueft - siehe Punkt 1.
4. Sobald k besser abgesichert ist: g-g-Diagramme, Kombination mit
   a_long aus der bestehenden Bremsdruck-Kalibrierung fuer ein
   vollstaendiges Traction-Circle-Bild ueber die GANZE Fahrt statt nur
   Bremsereignisse+Kurvenereignisse getrennt.

## Nachtrag: Kreisverkehre uebersehen, Geschwindigkeitsschwelle korrigiert - Kalibrierung springt auf R²=0.91 (01.09.2026)

Nutzer-Nachfrage: "hast du dir die kurzen logs mal angeschaut? ich bin
zumindest links um einen oder zwei Kreisverkehre gefahren" - berechtigt,
hatte ich nicht (die beiden kurzen 01.09.-Logs `162514`/`164308` zeigten
0 bestaetigte Kurven trotz eindeutiger Lenkmanoever).

**Ursache gefunden:** `corner_event_analysis.py` hatte `SPEED_MIN_MS =
10.0` (36 km/h) - Kreisverkehre werden aber typischerweise deutlich
langsamer gefahren (in den Logs: 11-34 km/h). Rohdaten-Check bestaetigte
das direkt: beide kurzen Logs zeigen mehrfach Lenkwinkel-Ausschlaege bis
ueber ±500° (praktisch Vollausschlag) bei 8-35 km/h - eindeutig echte
Kurvenmanoever, die die alte Schwelle komplett ausblendete.

**Fix:** `SPEED_MIN_MS` auf 3.0 m/s (10.8 km/h) gesenkt. Begruendung: der
eigentliche Verlaesslichkeits-Schutz ist ohnehin
`GPS_MIN_DISPLACEMENT_M=15m` ueber das 4s-Kursfenster (impliziert
rechnerisch bereits eine Mindestgeschwindigkeit von ~3.75 m/s) - der
zusaetzliche 36 km/h-Cutoff war unbegruendet konservativ. Vor der
Uebernahme verifiziert: alle neu auftauchenden Kurven in den beiden
kurzen Logs haben durchweg saubere GPS-Genauigkeit (~7.6m, weit unter der
20m-Schwelle) - kein Wiederauftreten des 200m-Fehlalarm-Musters von oben.

**Ergebnis nach Neulauf ueber alle 62 Logs: 80 statt 13 bestaetigte
Kurven** - deutlich reichhaltiger UND jetzt mit gesunder Links/Rechts-
Mischung ueberall (vorher stark rechtslastig, u.a. weil die schnelleren
Autobahn-Passagen im Datensatz zufaellig mehr Rechtskurven enthielten).
Alle a_lat-Werte bleiben physikalisch plausibel (0.03g bis 2.47g).

**Direkte Auswirkung auf `steering_lateral_model.py`:** Kalibrierbasis
springt von 4 auf **25 Punkte** (alle 4 Lenkwinkel-Logs jetzt vertreten,
9 Rechts- + 16 Linkskurven statt vorher 4x Rechts+0x Links). Kalibrierung
jetzt **k=0.01631, R²=0.906** (vorher R²=-0.04!) - siehe
`results/steering_lateral_calibration.png`: Links- und Rechtskurven
liegen sauber symmetrisch auf derselben Ursprungsgeraden, bestaetigt die
Symmetrie-Annahme (dieselbe Konstante fuer beide Richtungen) erstmals
empirisch. LOO-RMSE weiterhin ~5°/s, aber jetzt gegen einen viel breiteren
Wertebereich (10-24°/s) und mit deutlich hoeherer Gesamt-Erklaerungskraft.
**Die Kreisverkehr-Frage des Nutzers war also nicht nur berechtigt,
sondern hat direkt die Kalibrierungsqualitaet des neuen Modells geloest.**

Aktualisierte pro-Log-Kennzahlen (kaum veraendert gegenueber der
schwachen Kalibrierung vorher - erwartungsgemaess, da k sich nur wenig
verschoben hat: 0.01727 -> 0.01631):
| Log | p50 | p90 | p99 | max | Rauschboden p95 |
|---|---|---|---|---|---|
| 070451 | 0.04g | 0.12g | 0.24g | 0.32g | 0.142g |
| 152031 | 0.03g | 0.14g | 0.33g | 0.74g | 0.151g |
| 162514 | 0.11g | 0.21g | 0.33g | 0.39g | 0.043g |
| 164308 | 0.18g | 0.53g | 1.40g | 1.48g | 0.002g |

Outputs aktualisiert: `results/corner_event_summary.json` (80 Ereignisse),
`results/steering_lateral_model_summary.json`,
`results/steering_lateral_calibration.png`, alle vier
`results/steering_lateral_<log>_trace.png`.

**Bleibt offen:** mit n=25 (immer noch alles aus den 4 Logs vom 01.09.)
ist die Kalibrierung deutlich besser abgesichert als vorher, aber
weiterhin nicht unabhaengig ueber mehrere Tage/Bedingungen geprueft -
bei neuen Lenkwinkel-Logs erneut laufen lassen und beobachten, ob k
stabil bleibt.

## Nutzer-Bestaetigung zur 2.47g-Kurve (27.08.2026, A111-Auffahrt) — wichtige methodische Einordnung

Nutzer erkannte die Kurve sofort (Koordinaten + GPS-Plot vorgelegt): "Das
ist die Auffahrt zur A111. Ja, die Kurve bin ich durchaus an der
Haftgrenze gefahren. Teils auch an der Grenze, wo das Heck schon quer
stand."

**Das erklaert eine bisher unbemerkte methodische Schwaeche:** das
Kernereignis hat `a_lat_mean=+1.21g` aber `a_lat_peak=+2.47g` - eine
grosse Luecke zwischen Mittel- und Spitzenwert, die bei den anderen
bestaetigten Kurven so nicht auftritt (naechstgroesster mean/peak-Abstand
liegt deutlich enger beieinander, siehe `corner_event_summary.json`).
**Erklaerung: `a_lat = v*omega` (kinematische "coordinated turn"-Formel,
verwendet in `corner_event_analysis.py` UND `steering_lateral_model.py`)
setzt voraus, dass kein nennenswerter Schwimmwinkel vorliegt** (Fahrzeug-
Geschwindigkeitsvektor = Fahrzeug-Laengsachse). Genau das gilt beim vom
Nutzer beschriebenen Manoever NICHT mehr - wenn das Heck quersteht, dreht
sich das Fahrzeug (hohe Giergeschwindigkeit vom Gyro gemessen) staerker,
als es der reinen Kurvenkruemmung/dem Radius entspraeche. Die Formel
verwechselt in diesem Moment einen Teil der Drehbewegung (Gieren durch
Schleudern) mit "echter" Kurvenbeschleunigung und ueberschaetzt dadurch
den Spitzenwert.

**Konsistenzcheck, der das stuetzt:** `a_lat_mean=1.21g` (gemittelt ueber
die ganzen 4s des Ereignisses, weniger anfaellig fuer einen kurzen
Schwimmwinkel-Spike) liegt GENAU im per Literatur erwarteten Bereich fuer
die Nankang NS-R2 Semi-Slicks (~1.0-1.3g, siehe Bereifung-Abschnitt) -
plausibel als echte, an der Haftgrenze gefahrene Kurve. Der Peak-Wert
(2.47g) ist dagegen physikalisch nicht als reine Reifenhaftungsgrenze
zu interpretieren, sondern zu einem Teil ein Methodenartefakt des
Schleuderns.

**Konsequenz fuer die gesamte a_lat-Datenbasis (`corner_event_analysis.py`,
`steering_lateral_model.py`, alle darauf basierenden Percentile/Max-
Kennzahlen):** `a_lat_peak` ist bei Ereignissen NAHE oder UEBER der
Haftgrenze (Schleudern/Uebersteuern) tendenziell ueberschaetzt, waehrend
`a_lat_mean` robuster bleibt. Bereits in `PROJEKT_STAND.md` an anderer
Stelle als generelle Vorsichtsmassnahme vermerkt ("peak nur ein
Einzelsample, mean etwas robuster") - jetzt mit einer konkreten,
nutzerbestaetigten physikalischen Begruendung dahinter, nicht nur einer
statistischen Vermutung. **Fuer jede kuenftige Reifengrenzwert-/
mu-Schaetzung: `a_lat_mean` bevorzugen, `a_lat_peak` nur zur groben
Einordnung ("war das ueberhaupt eine harte Kurve") nutzen, nicht als
prezise g-Zahl.**

OFFEN: keine systematische Korrektur umgesetzt (z.B. Schwimmwinkel-
Schaetzung, um a_lat waehrend eines Schleuderns korrekt von der reinen
Rotation zu trennen) - waere mit den vorhandenen Sensoren (kein direkter
Schwimmwinkel-/Beschleunigungssensor am Schwerpunkt) ohnehin nur grob
moeglich. Bei Bedarf spaeter vertiefen.

## Zwei neue Logs vom 02.09.2026: fuenf weitere Ausrollereignisse + STEER_ANGL_EPS-Nullpunkt-Bug entdeckt (automatischer Lauf, 02.09.2026)

Automatische Verarbeitung von `2026-09-02 102031` (34.6 Min, FLI
~32.4%->24.8%) und `2026-09-02 150720` (39.4 Min, FLI ~27.7%->20.3%).
Masse ueber FLI-Median-Methode: 1168.6 kg bzw. 1167.1 kg. **Annahme
(unbeaufsichtigter Lauf, nicht pruefbar): nur Fahrer (86kg), keine
Passagiere/Zuladung** - bei Bedarf vom Nutzer korrigieren.

IMU-Resonanz beider Logs unauffaellig (18.9-21.8 Hz, alle 3 Achsen).
Achsenrotation: 150720 theta=64.1°/R=0.806 unauffaellig; 102031
theta=87.1°/R=0.837 - liegt knapp ausserhalb der bisherigen Spanne
(40.8-83.1°), aber R gesund, kein weiterer Handlungsbedarf. Keine neuen/
unbekannten Kanaltypen, keine UNMAPPED-Kanaele. 8 neue bestaetigte Kurven
(88 gesamt). Teillast-RMSE beider Logs (5.8%/7.1%) im ueblichen Rahmen.
Kinematischer Fehler <0.5% bei beiden. 11 neue Volllast-Segmente, Verhaeltnis
gemessen/Modell weiterhin Median 0.95 - kein Ausreisser. Keine neuen
Gang-6-Segmente.

**Auffaelligkeit 1 - Kurve mit hohem peak/mean-Verhaeltnis:** `102031`
t=1990.2-1993.2s, a_lat mean=+0.75g, peak=+2.40g (Verhaeltnis 3.2x) -
gleiches Muster wie die nutzerbestaetigte A111-Schleuderkurve (27.08.,
siehe oben). Vermutlich wieder ein Schwimmwinkel-Effekt (Peak durch
Drehbewegung ueberschaetzt), mean-Wert (0.75g) plausibel fuer eine harte,
aber kontrollierte Kurve. Nicht weiter geprueft (keine Nutzerbestaetigung
in diesem automatischen Lauf moeglich).

**Auffaelligkeit 2 - fuenf neue Ausrollereignisse** (bisher nur 6 ueber 62
Logs, jetzt 11 ueber 64 Logs - ungewoehnlich ergiebiger Lauf):
- `102031` t=1111.5-1130.7s (19.2s, 82->65 km/h): Referenzmodell
  (CdA=0.647/Crr=0.013) trifft nahezu perfekt (306-307N vs. gefittet 309N,
  Wind vernachlaessigbar bei diesem Ereignis) - bestaetigt erneut die
  Referenzwerte.
- `102031` t=1179.9-1216.5s (36.5s, 93->56 km/h): mit 37 km/h Spanne das
  zweitbreiteste Fenster ueberhaupt (nach dem 41 km/h-Fund vom 01.09.).
  Gefittetes CdA/Crr (0.272/0.0241) weicht von der Referenz ab, aber die
  Gesamtwiderstandskraft trifft sich gut (340N gefittet vs. 299-312N
  Referenz, ~10% Differenz) - stuetzt tendenziell die Referenzwerte,
  nicht den Einzelfit (gleiches Muster wie beim 41km/h-Fund: CdA/Crr
  einzeln unsicher, Gesamtkraft robust).
- `150720`: drei weitere Ereignisse (12.0s/111->92km/h, 12.9s/70->62km/h,
  21.6s/44->30km/h) - alle deutlich schmaler, das 12.9s-Fenster liefert
  einen schlechten Fit (Crr an unterer Grenze 0.1 m² CdA) und ist nicht
  verwertbar. Die anderen beiden liegen tendenziell nahe an der Referenz.
  Insgesamt bestaetigt dieser Lauf das bisherige, vorsichtige Fazit
  (Referenzwerte plausibel, Windkorrektur bei kurzen Fenstern weiterhin
  unsicher) eher, als es zu aendern.

**Auffaelligkeit 3 (wichtig) - STEER_ANGL_EPS-Nullpunkt-Offset in beiden
neuen Logs entdeckt:** Kalibrierung von `steering_lateral_model.py`
verschlechterte sich deutlich (k: 0.01631->0.015045, R²: 0.906->0.732,
n=25->33). Nach Vorgabe (siehe Abschnitt "Kalibrierung springt auf
R²=0.91") Rohdaten des staerksten Ausreissers geprueft: `150720`
t=1008-1012s, als "rechts" klassifizierte Kurve mit *positivem*
Lenkwinkel (+92 bis +150°) - **entgegengesetztes Vorzeichen** zum in
allen bisherigen 4 Lenkwinkel-Logs empirisch bestaetigten Muster
(rechts=negativ, links=positiv). Ursache gefunden: **`STEER_ANGL_EPS`
hat in `150720` ueber fast die gesamte Fahrt einen Offset von ca. +148°
statt eines Nullpunkts um 0°** (Bucket-Mittelwerte 120s-weise ueberwiegend
142-151°, teils mit sehr niedriger Streuung <2° bei vermutlich echten
Geradeausfahrt-Abschnitten - das ist NICHT Rauschen, sondern ein
systematischer Versatz). `102031` zeigt denselben Effekt schwaecher
(Offset ca. +20° in ruhigen Fahrtabschnitten). Die vorherigen 4
Lenkwinkel-Logs (070451, 152031, 162514, 164308) hatten diesen Offset
nicht (Mittelwert nahe 0°) - **neues Phaenomen, vermutlich weil der
Lenkwinkelsensor bei diesen beiden Fahrten nicht neu genullt/gelernt
wurde** (z.B. nach Batterie-Trennung, Werkstattbesuch, oder ECU-Reset -
nicht rekonstruierbar aus den Logs). Erklaert vollstaendig sowohl das
Vorzeichenproblem der Kalibrierpunkte als auch die physikalisch
unmoeglichen a_lat-Kennzahlen der kontinuierlichen Spur fuer `150720`
(p50=1.14g, p99=8.09g, max=9.35g - siehe `results/steering_lateral_model_summary.json`).
**Nicht automatisch korrigiert** (`steering_lateral_model.py` unveraendert
gelassen, da eine Korrektur - z.B. dynamische Nullpunkt-Schaetzung pro
Log aus Geradeausfahrt-Perioden - eine Modellaenderung waere, die ausserhalb
dieser Routine-Verarbeitung liegt). **Empfehlung fuer naechste manuelle
Session:** pro Log einen Offset-Korrekturschritt einbauen (z.B. Median
von `STEER_ANGL_EPS` waehrend bestaetigter, langsamer Geradeausfahrt
subtrahieren) BEVOR kalibriert wird, und/oder `150720` (grosser Offset)
vorerst aus der Kalibrierbasis ausschliessen.

Outputs: neue `data/derived/*_vibration_summary.json` fuer beide Logs,
`results/corner_event_summary.json` (88 Ereignisse), `results/coastdown_analysis_summary.json`
(11 Ereignisse), `results/partial_load_model_summary.json`,
`results/drivetrain_model_validation_summary.json`,
`results/top_speed_validation_summary.json`,
`results/steering_lateral_model_summary.json` (alle aktualisiert),
diverse neue PNGs in `results/`.

## Nachtrag: STEER_ANGL_EPS-Nullpunkt per OSM-Geradeausfahrt korrigiert (02.09.2026, Nutzeranfrage)

Nutzeranfrage nach dem obigen Offset-Fund: "Kannst du mal schauen, ob du
mit hilfe von osm gerade strecken auszumachen und darueber eine
nullpunkt korrektur vornehmen?" - genau umgesetzt.

**Neues Skript `scripts/steering_zero_offset.py`:** laedt OSM-
Strassengeometrie (Overpass, alle Ways `highway` in
motorway/trunk/primary/secondary/tertiary(+_link)/unclassified) fuer
eine Box um alle bisherigen Lenkwinkel-Logs (52.45-52.85°N/13.10-13.45°E,
gecacht unter `data/track/osm_roads_steering_corridor.json`, 25083
Ways). Jede Strassenlinie wird lokal in Meter projiziert, mit 5m-Schritt
resampelt, und pro Punkt die Kursaenderung ueber ein 60m-Fenster
geprueft (<4° = "gerade"). Die gefahrene GPS-Spur (nach 20m-Genauigkeits-
filter) wird per KD-Baum gegen diese "geraden" Punkte gematcht
(Toleranz 12m) UND muss dabei >8 m/s (29 km/h) schnell sein (schliesst
Rangieren/Ampeln aus); zusammenhaengende Treffer-Fenster >=3s werden
gruppiert. Der Median von `STEER_ANGL_EPS` ueber alle so bestaetigten
Geradeausfahrt-Fenster ist die Nullpunkt-Schaetzung pro Log.

**Ergebnis, WICHTIGER ZUSATZFUND: nicht nur die zwei neuen Logs haben
einen Offset, sondern 4 von 6 Lenkwinkel-Logs insgesamt:**

| Log | Offset (Median) | n Samples | Fenster |
|---|---|---|---|
| 070451 | +3.6° | 2294 | 37 |
| 152031 | +2.9° | 2219 | 37 |
| 162514 | -29.1° | 406 | 12 |
| 164308 | -43.0° | 455 | 9 |
| 102031 | +20.1° | 2019 | 34 |
| 150720 | +147.8° | 2706 | 28 |

Die -29.1°/-43.0°-Offsets von `162514`/`164308` waren VORHER unbemerkt
(die alte R²=0.906-Kalibrierung vom 01.09. war also selbst schon leicht
verzerrt, nur weniger dramatisch, weil diese zwei Logs nur 10 von 25
Kalibrierpunkten stellten und kein einzelner Offset so extrem war wie
bei `150720`). Nur `070451`/`152031` waren tatsaechlich nahe am echten
Nullpunkt. Per Zeit-Bucket-Check gegengeprueft (30s-Fenster): `162514`
haelt ueber mehrere Minuten stabil -26 bis -34° (std teils <1°), `164308`
stabil -42 bis -43° (std teils <1°) - eindeutig ein systematischer
Versatz, kein Rauschen.

**Korrektur eingebaut in `steering_lateral_model.py`:** neue Funktion
`load_zero_offsets()` liest `results/steering_zero_offset.json` (nur
Logs mit `trusted=True`, d.h. >=50 Samples - hier alle 6) und zieht den
Offset sowohl vor der Kalibrierpunkt-Mittelung (`build_calibration_set`)
als auch vor der kontinuierlichen a_lat-Berechnung (`apply_continuous`)
vom Rohwert ab. Fehlt die Offset-Datei, verhaelt sich das Skript wie
zuvor (Offset=0).

**Ergebnis nach Korrektur: k=0.016833, R²=0.920 (vorher unkorrigiert:
k=0.015045, R²=0.732; alte Kalibrierung vor den zwei neuen Logs:
k=0.01631, R²=0.906) - BESSER als je zuvor, trotz (eigentlich wegen)
mehr Kalibrierpunkten (n=33 statt 25).** LOO-RMSE faellt von 8.36 auf
4.54 °/s. Alle `150720`-Kalibrierpunkte haben jetzt das erwartete
Vorzeichen (rechts=negativ). Die kontinuierliche a_lat-Spur von `150720`
ist jetzt physikalisch plausibel (p50=0.01g, p99=0.22g, max=0.45g -
vorher p50=1.14g, p99=8.09g, max=9.35g). Alle a_lat-Kennzahlen aller 6
Logs in `results/steering_lateral_model_summary.json` sind mit dieser
Korrektur aktualisiert (durchweg niedriger/plausibler als vorher, da
auch `070451`/`152031`/`162514`/`164308` jetzt die kleinen, aber realen
Offsets abgezogen bekommen).

**Einordnung/Grenzen:** die OSM-Methode ist unabhaengig vom Lenkwinkel-
Kanal selbst (nutzt nur GPS+Strassengeometrie) und liefert fuer alle 6
Logs `trusted=True` (227-1449s Geradeausfahrt-Basis je Log) - deutlich
robuster als der vorherige Ad-hoc-Bucket-Check. Nicht geprueft: ob der
Offset INNERHALB einer Fahrt driftet (aktuell EIN Median pro ganzem
Log) - fuer `150720` deutete der fruehere Bucket-Check (Std bis 45° in
einzelnen 120s-Fenstern trotz meist stabiler ~148°) auf gelegentliche
kurze Abweichungen hin, vermutlich echte kurze Lenkmanoever zwischen den
laengeren geraden Abschnitten, nicht auf Offset-Drift - aber nicht
explizit ausgeschlossen. Bei neuen Logs ausserhalb der aktuellen Box
(52.45-52.85°N/13.10-13.45°E) muesste `CORRIDOR_BBOX` erweitert und der
OSM-Cache geloescht werden.

Outputs: `results/steering_zero_offset.json`,
`data/track/osm_roads_steering_corridor.json` (OSM-Cache, 25083 Ways),
`results/steering_lateral_model_summary.json` + alle
`results/steering_lateral_*.png` (aktualisiert).

## Nachtrag: Offset-Drift INNERHALB einer Fahrt geprueft - kein Drift (02.09.2026, Nutzeranfrage)

Nutzeranfrage zum offenen Punkt "Offset-Drift innerhalb einer Fahrt nicht
ausgeschlossen" (siehe vorheriger Abschnitt): gezielt im Nachmittagslog
`2026-09-02 150720` pruefen - dem Log mit dem groessten Offset (+147.8°),
also dem Fall, in dem ein Drift die groessten Folgen haette.

**Neues Skript `scripts/steering_offset_drift.py`:** nutzt exakt dieselbe,
vom Lenkwinkel unabhaengige OSM-Geradeausfahrt-Erkennung wie
`steering_zero_offset.py` (alle Konstanten von dort importiert, kein
zweiter Schwellwertsatz), bildet aber statt EINES Medians pro Log einen
Median pro 120s-Zeitbucket (min. 15 Samples/Bucket). Das ist bewusst
feiner als die Fenster-Gruppierung des Basisskripts - dort umfasst ein
einzelnes "Fenster" in diesem Log 908s (Autobahnetappe) und wuerde einen
Drift in sich selbst verstecken. Ausgewertet werden: gewichtete lineare
Regression (Bootstrap-KI ueber die Buckets), bester Stufenpunkt (ein
Neu-Nullen waehrend der Fahrt saehe eher wie eine Stufe als eine Rampe
aus), Kontrollkorrelation gegen die Bucket-Geschwindigkeit, und die
Umrechnung des gefundenen Drifts in a_lat-Fehler (wirkt quadratisch mit
v, siehe `steering_lateral_model.py`).

**Ergebnis fuer `2026-09-02 150720`: KEIN Drift.** 16 auswertbare Buckets
ueber t=184-2222s (deckt die Fahrt bis auf die ersten ~3 und letzten ~2
Minuten ab), 2706 Geradeaus-Samples:
- Bucket-Mediane liegen zwischen **+146.9° und +149.0°** (Std nur 0.5°)
  um den verwendeten Einzel-Offset von +147.8° - kein erkennbarer Verlauf.
- Linearer Trend **+0.17°/h, 95%-KI [-1.53, +2.21]°/h** - nicht
  signifikant. Ueber die erfassten 34 Minuten waeren das +0.1°.
- Residuenstreuung sinkt durch den Trendterm nicht (0.5° -> 0.5°), der
  Trend erklaert also praktisch nichts.
- Bester Stufenpunkt: +0.5° bei t=32 min - im Rauschen, keine Stufe.
- Kontrolle Offset vs. Bucket-Tempo: r=+0.22 (33-138 km/h) - kein
  relevanter Geschwindigkeits-/Fahrbahnquerneigungs-Zusammenhang.

**Power-Check (wichtig, damit "kein Drift gefunden" nicht mit "Methode zu
stumpf" verwechselt wird):** kuenstliche Rampen auf dieselben Bucket-
Mediane addiert und neu gefittet - +2.0°/h wird als +2.17°/h [KI
+0.47…+4.21] erkannt, +5°/h und +10°/h ebenso sauber. Die Methode wuerde
einen Drift ab ca. 2°/h also zuverlaessig sehen; der gemessene liegt bei
0.17°/h.

**Praktische Groessenordnung:** die volle beobachtete Bucket-Spanne (2.1°,
also die Obergrenze inkl. allem Rauschen) entspricht im a_lat-Modell
0.012g bei 50 km/h, 0.049g bei 100 km/h, 0.109g bei 150 km/h. Der
eigentliche Trend-Drift (0.1°) liegt bei 0.001-0.005g - vernachlaessigbar
gegenueber der Kalibrierunsicherheit von k (LOO-RMSE 4.54°/s).

**Alle 6 Lenkwinkel-Logs gegengeprueft** (gleicher Lauf ohne Argument):
| Log | Offset | Bucket-Spanne | Trend | signifikant? |
|---|---|---|---|---|
| 070451 | +3.6° | +2.5…+4.8° | +1.43°/h [+0.23,+2.90] | ja (aber 0.8° gesamt) |
| 152031 | +2.9° | +1.8…+4.8° | +0.02°/h [-2.19,+2.19] | nein |
| 162514 | -29.1° | -29.4…-28.9° | -1.66°/h [-15.97,+5.22] | nein (nur 4 Buckets) |
| 164308 | -43.0° | -43.1…-42.6° | +0.38°/h [-4.62,+26.50] | nein (nur 4 Buckets) |
| 102031 | +20.1° | +19.3…+20.3° | +0.89°/h [+0.21,+1.67] | ja (aber 0.5° gesamt) |
| **150720** | **+147.8°** | **+146.9…+149.0°** | **+0.17°/h [-1.53,+2.21]** | **nein** |

Zwei Logs (070451, 102031) zeigen einen formal signifikanten, aber
winzigen Trend (0.5-0.8° ueber die ganze Fahrt, entspricht <0.02g bei 100
km/h) - unterhalb jeder praktischen Relevanz und moeglicherweise ohnehin
nur Strecken-/Fahrbahneffekt statt Sensordrift (070451 zeigt r=-0.49
gegen die Bucket-Geschwindigkeit).

**Fazit: der Ein-Offset-pro-Log-Ansatz in `steering_zero_offset.py` ist
gerechtfertigt** - der Offset ist innerhalb einer Fahrt stabil (auch und
gerade im Extremfall +148°), er entsteht offenbar EINMALIG beim
Sensor-/ECU-Start und bleibt dann konstant. Der bisher offene Punkt ist
damit geschlossen; kein Codeaenderungsbedarf an
`steering_lateral_model.py` oder `steering_zero_offset.py`.

**Nebenbefund zur Einordnung des frueheren Verdachts:** der urspruengliche
Drift-Verdacht kam aus einem Ad-hoc-Bucket-Check ueber ALLE Samples (dort
Std bis 45° in einzelnen 120s-Fenstern). Beschraenkt auf die
OSM-bestaetigten Geradeausfahrt-Samples faellt die Streuung IM Bucket auf
typisch 1.4° - die grosse Streuung waren also tatsaechlich echte
Lenkmanoever zwischen den geraden Abschnitten, wie damals vermutet, kein
Offset-Wackeln.

**Einschraenkungen:** (a) die beiden kurzen Logs `162514`/`164308` haben
nur 4 Buckets ueber 5-6 Minuten - dort ist die Drift-Aussage schwach
(breite KIs), aber bei so kurzen Fahrten waere ein Drift ohnehin klein;
(b) geprueft ist Drift ueber die Fahrtdauer, NICHT ein moeglicher Sprung
in den ersten ~3 Minuten vor dem ersten Geradeaus-Fenster (Stadtverkehr,
keine bestaetigte Gerade); (c) die Buckets sind ungleich ueber die Fahrt
verteilt (mehr Samples in den Autobahnabschnitten), die Gewichtung nach
Samplezahl beruecksichtigt das, kann es aber nicht vollstaendig ausgleichen.

**Aufruf:** `.venv/bin/python scripts/steering_offset_drift.py` (alle
Lenkwinkel-Logs) oder mit Log-ID als Argument fuer ein einzelnes Log.
**Outputs:** `results/steering_offset_drift.json`, sechs
`results/steering_offset_drift_<log>.png`.

## Kurvenradius aus dem Lenkwinkel - Genauigkeitscheck (02.09.2026, Nutzerfrage)

Nutzerfrage: "kannst du aus dem lenkwinkel daten den kurvenradius
abschaetzen, oder reicht dafuer die genauigkeit nicht?"

**Herleitung:** aus dem bestehenden kinematischen Modell in
`steering_lateral_model.py` (`omega[deg/s] = k*Lenkwinkel[deg]*v[m/s]`)
folgt fuer eine stationaere Kurve (omega[rad/s]=v/R):
`R = v/omega_rad = 1/radians(k*Lenkwinkel)`. **Wichtig: in diesem Modell
haengt R NICHT von v ab** - reine Folge davon, dass omega linear in v
angesetzt ist (Ackermann-/Bicycle-Modell ohne Schwimmwinkel-/
Untersteuerungsterm). Geometrisch plausibel, aber die bereits
dokumentierte "Untersteuerungs-Signatur" (siehe Kalibrierungs-Abschnitt
oben, zwei Geschwindigkeitscluster passten nicht perfekt auf eine
Gerade) deutet an, dass reale Radien bei hoeherer Geschwindigkeit fuer
denselben Lenkwinkel groesser sind (Reifenschlupf) - das kann dieses
einfache Modell nicht abbilden.

**Neues Skript `scripts/steering_radius_estimate.py`:** beantwortet die
Frage empirisch statt nur theoretisch. Nutzt dieselben 33
Kalibrierpunkte wie `steering_lateral_model.py` (bestaetigte Kurven aus
`corner_event_analysis.py`, GPS+Gyro-kreuzvalidiert, UNABHAENGIG vom
Lenkwinkel) und vergleicht:
- `R_gemessen = v/radians(|heading_rate_mean_deg_s|)` (GPS+Gyro, Ground
  Truth)
- `R_lenkwinkel = 1/radians(k*|Lenkwinkel|)` (nur Lenkwinkel)

Keine neue Kalibrierung - derselbe Datensatz, nur in Radius- statt
Gierraten-Einheiten ausgedrueckt (relevant, weil R=1/omega nichtlinear
ist: kleine Gierraten/grosse Radien vergroessern den Fehler
ueberproportional).

**Ergebnis: Median-Fehler 19.8%, p90 45.3%, Einzelfaelle bis 160%**
(n=33). Sensor-Aufloesung ist mit 0.1° NICHT der limitierende Faktor
(gepr. an 6 Logs). Keine klare Korrelation des Fehlers mit Geschwindigkeit
(r=-0.12) oder Radius (r=-0.14) - der Fehler kommt also nicht primaer aus
der (noch unbestaetigten) Untersteuerung, sondern aus der grundsaetzlichen
Kalibrierunsicherheit von k selbst (LOO-RMSE 4.54°/s gegen Gierraten von
10-24°/s, siehe Kalibrierungs-Abschnitt) - schwache Signale (kleiner
Lenkwinkel/kleine Gierrate) sind dabei anteilig staerker betroffen
(corr(|Fehler|, Lenkwinkel)=-0.28).

**Wichtigste Einschraenkung: Abdeckungsluecke.** Die Kalibrierbasis deckt
nur R=10.8-81.7m und v=11.2-71.8 km/h ab - ausschliesslich enge,
langsame Kurven/Kreisverkehre (siehe Kalibrierungs-Abschnitt oben, kein
einziger bestaetigter Highway-Sweeper in der aktuellen Basis). Fuer
GROESSERE Radien (Autobahnkurven, Rennstrecken-Kurven bei Tempo, genau
der fuer die Rundenzeit-Simulation interessante Bereich) liegt **keine
einzige unabhaengige Validierung vor** - dort waere die Extrapolation
zusaetzlich durch die unbestaetigte Untersteuerungs-Nichtlinearitaet
gefaehrdet (bei hoher Geschwindigkeit vermutlich systematisch zu kleiner
Radius, siehe Herleitung oben), nicht nur durch die hier gemessene
20%-Grundunsicherheit.

**Fazit: eine grobe Radius-Schaetzung (Faktor ~1.2, also z.B. "eher 20m
als 40m") ist mit den aktuellen Daten moeglich, eine praezise
Streckengeometrie-Rekonstruktion (wie sie z.B. `spreewaldring_track.py`
aus OSM+Orthofoto liefert) NICHT** - dafuer waere die Genauigkeit zu
gering und der validierte Bereich zu schmal. Fuer den urspruenglichen
Zweck des Lenkwinkelmodells (kontinuierliche a_lat-Abschaetzung) ist das
unproblematisch (dort wird a_lat direkt aus Lenkwinkel*v berechnet, ohne
den Umweg ueber Radius), aber als eigenstaendiger Radius-Sensor sollte
man es nicht verwenden, ohne den grossen Unsicherheitsbereich zu
kommunizieren.

**Aufruf:** `.venv/bin/python scripts/steering_radius_estimate.py`.
**Outputs:** `results/steering_radius_estimate.json`,
`results/steering_radius_estimate.png` (Streudiagramm R_gemessen vs.
R_lenkwinkel, plus Fehler-vs-Radius mit deutlicher Kennzeichnung der
fehlenden Abdeckung oberhalb 82m).

## Automatischer Lauf: 3 neue Logs verarbeitet (03.09.2026)

Scheduled Task, unbeaufsichtigt. Neue Logs: `2026-09-03 081930` (~37min),
`2026-09-03 163254` (~21min), `2026-09-03 165821` (~23min).

**Masse (FLI, SOLO-Annahme):** 081930=1165.1kg (FLI ~20.3%->15.8%),
163254=1164.1kg (FLI ~15.6%->14.8%), 165821=1189.3kg (FLI ~90.2%->90.2%,
zwischen 163254 und 165821 offensichtlich aufgetankt). In
`LOG_MASS_OVERRIDE_KG` in `drivetrain_model_validation.py` und
`top_speed_validation.py` ergaenzt.

**Kernzahlen:** IMU-Resonanz 081930/163254 unauffaellig (18.6-21.0 Hz X/Y/Z);
165821 AUFFAELLIG s.u. Achsenrotation theta 66.9-72.3 Grad (R=0.701-0.858,
081930 mit R=0.701 knapp an der 0.7-Grenze). 16 neue bestaetigte Kurven
(081930: 7, 163254: 2, 165821: 7) - Gesamtzahl jetzt 104. UNMAPPED-Kanaele:
0. Teillast-RMSE der 3 Logs 5.9-6.9% (Referenz ~6.9%). Kinematischer Fehler
Median|Fehler| 0.36-0.83% (normal). 12 neue Volllast-Segmente (081930: 3,
165821: 9). Vollast-Verhaeltnis gemessen/Modell gesamt jetzt Median=0.96
(vorher 0.94, kein deutlicher Sprung). Keine neuen Gang-6/Top-Speed-Segmente.

**Auffaellig 1 - IMU-Resonanz 165821 Z-Achse:** AccelerationZ zeigt
7.52 Hz statt der ueblichen 18-23 Hz (X/Y in diesem Log normal bei
21.0 Hz). Ursache nicht untersucht (ausserhalb Routine-Scope) - bei
naechster manueller Session Rohdaten pruefen.

**Auffaellig 2 - neuer Ausrollversuch-Fund:** `2026-09-03 081930`
t=1132.1-1145.8s, v=101->83 km/h (13.8s), Masse=1165.1kg. Gefittet:
CdA=0.606 m² Crr=0.0205 (RMSE=0.27 km/h) vs. Referenz CdA=0.647/Crr=0.0130
- CdA nah an Referenz (-6%), Crr etwas hoeher (typisch fuer kurze
Ereignisse, siehe [[mx5_coastdown_analysis]]). Gegenwind-korrigiert
(+2.5 km/h Rueckenwind). Jetzt 12 Ausrollereignisse insgesamt (vorher 11).
Plot: `results/coastdown_2026-09-03_081930_1132s.png`.

**Auffaellig 3 - neuer Kanalsatz in 081930 (18 bisher unbekannte
PidNames):** `AFR_ACT_MZ, BATT_SOC, BRK_F_P_R, CatalystTemperatureBank1Sensor1,
CommandedFuelRailPressureA, ControlModuleVoltage,
FuelPressureControlSystemSupported, FuelRailPressureA, FuelRailTemperatureA,
OIL_TEMP_MZ, Rpm, TransmissionActualGearRatio, TransmissionActualGearStatus,
TransmissionActualGearStatusSupported, WU1-4_P_TPM_IC` (Reifendruck pro Rad).
Aber: jeweils nur ~5 Samples (ControlModuleVoltage nur 1) - vermutlich
einmaliger OBD-Scan-Tool-Abruf waehrend der Fahrt, keine durchgaengige neue
Logging-Konfiguration. WU1-4_P_TPM_IC (Reifendruck, roh 166-178) und
TransmissionActualGearRatio (1.286 = Gang 5, plausibel) koennten bei
Bedarf spaeter als Kanaele erschlossen werden, aktuell zu duenn fuer
Analysen. build_datalake.py meldet dafuer kein UNMAPPED (Identitaets-
Mapping fuer neue .dlg-PidNames), keine Code-Aenderung noetig.

**Auffaellig 4 - Lenkwinkelmodell:** alle 3 neuen Logs haben
STEER_ANGL_EPS (Gesamtzahl Logs mit Kanal jetzt 9 statt 6).
`steering_lateral_model.py` liefert danach k=0.017143, R²=0.930, n=49
Kalibrierpunkte (vorher k=0.016833, R²=0.920, n=33) - Verbesserung, kein
Fehlalarm, daher gemaess Routine-Vorgabe KEINE Rohdaten-Tiefenpruefung
noetig. Ad-hoc-Bucket-Check (nicht Teil der Routine, aber zur Einordnung
der auffaellig hohen "Rauschboden Geradeausfahrt"-Werte von 081930
[p95=0.286g] und 165821 [p95=0.551g] gegenueber sonst ueblichen 0.03-0.04g
gemacht): Median-STEER_ANGL_EPS liegt bei 081930 ~-11.5°, 163254 ~-17.1°,
165821 ~+9.5° (Referenz 070451 nahe Null: ~+3.9°) - alle 3 neuen Logs haben
also moderate, ueber die Fahrt stabile Nullpunkt-Offsets (deutlich kleiner
als der Extremfall 150720 mit +148°, aber in der gleichen Groessenordnung
wie die zuvor gefundenen -29/-43°-Faelle). `steering_zero_offset.py`
wurde bewusst NICHT erneut ausgefuehrt (nicht Teil dieser Routine) - die
3 neuen Logs sind also NICHT offset-korrigiert in der aktuellen
`steering_lateral_model_summary.json`. Empfehlung: bei naechster
manueller Session `steering_zero_offset.py` fuer die jetzt 9 Logs neu
laufen lassen und `steering_lateral_model.py` danach erneut ausfuehren.

## Nachtrag: Nullpunkt-Korrektur fuer die 3 neuen 03.09.2026-Logs nachgezogen (03.09.2026)

Offener Punkt aus dem automatischen Lauf vom 03.09.2026 (siehe oben,
Auffaelligkeit 4) abgearbeitet: `steering_zero_offset.py` und danach
`steering_lateral_model.py` fuer alle jetzt 9 Lenkwinkel-Logs neu
gelaufen.

**Offsets fuer die 3 neuen Logs** (per OSM-Geradeausfahrt-Methode,
bestaetigen die vorherige Ad-hoc-Schaetzung aus dem automatischen Lauf
in der richtigen Groessenordnung):
| Log | Offset (Median) | n Samples | Fenster |
|---|---|---|---|
| 081930 | -11.7° | 2033 | 33 |
| 163254 | -17.3° | 539 | 12 |
| 165821 | +9.6° | 1673 | 25 |

**Kalibrierung nach Korrektur: k=0.016968, R²=0.935 (vorher
unkorrigiert: k=0.017143, R²=0.930; letzte vollstaendig korrigierte
Kalibrierung mit 6 Logs: k=0.016833, R²=0.920, n=33).** LOO-RMSE=3.92°/s
(vorher 4.54°/s) bei n=49 Kalibrierpunkten (33 Rechts-/Linkskurven aus 6
alten + 16 aus den 3 neuen Logs) - weitere Verbesserung, bestaetigt den
bisherigen Trend "mehr Kalibrierpunkte + korrekte Nullpunkte = bessere
Kalibrierung".

Alle a_lat-Kennzahlen in `results/steering_lateral_model_summary.json`
und alle 9 `results/steering_lateral_<log>_trace.png` sind aktualisiert.
`165821` (die auffaellig hohen Rauschboden-Werte aus dem automatischen
Lauf, p95=0.551g) zeigt nach Korrektur einen deutlich plausibleren
Rauschboden (p95=0.144g) - noch nicht auf dem sehr sauberen Niveau der
laengsten/ruhigsten Logs (070451: 0.075g), aber im erwartbaren Rahmen.

Damit ist der offene Punkt "Nullpunkt-Korrektur fuer die 3 neuen Logs
nachziehen" aus dem letzten automatischen Lauf erledigt.

## Nachtrag: Ursache der 18 duennen Zusatz-PIDs in 081930 geklaert - Datenrate-Effekt bestaetigt (03.09.2026)

Nutzer bestaetigte: die 18 zusaetzlichen, nur ~5s lang geloggten PIDs in
`2026-09-03 081930` (siehe "Auffaellig 3" oben) stammen daher, dass er
morgens kurz das OBD-Fusion-Dashboard geoeffnet hatte, um den Reifendruck
zu pruefen, und es nach ca. 5s wieder geschlossen hat. Fusion loggt alle
gelesenen PIDs in die Datenbank, auch nur kurzzeitig/interaktiv
abgefragte.

**Nutzerfrage: sinkt in diesen ~5s die Datenrate der bekannten OBD-PIDs
messbar?** Direkte SQL-Analyse der rohen `.dlg`-SQLite-Datei (nicht ueber
den Datalake, der die genauen Rohzeitstempel aggregiert): der
Dashboard-Abruf umfasst 86 Datenpunkte ueber **5.33s** (passt exakt zur
Nutzerschaetzung "ca. 5s").

**Ergebnis: ja, deutlich messbar.** Abtastrate der regulaeren, ueber
denselben OBD-Bus geloggten Kanaele (VehicleSpeed, EngineRPM, ETC_ACT,
BFP_PRE_MZ, TM_GEST, STEER_ANGL_EPS):
| Phase | mittleres Intervall | Rate |
|---|---:|---:|
| davor (Baseline, ~10s) | 0.50s | ~2.0 Hz |
| waehrend des Dashboard-Abrufs | 1.07s | ~0.94 Hz |
| danach (erste ~9s, Erholphase) | 0.60s | ~1.67 Hz |
| danach (~30-40s spaeter) | ~0.50s | ~2.0 Hz |

Rate halbiert sich fast genau waehrend der 5.3s, braucht danach noch
~10-15s Nachlauf bis zur vollen Erholung auf die ~2Hz-Baseline.
**Gegencheck:** `AccelerationX` (Handy-eigener IMU-Sensor, nicht ueber
den OBD-Bus) bleibt in diesem Fenster unveraendert bei ~50Hz (263 vs. 264
vs. 263 Samples in den drei Fenstern) - der Effekt betrifft eindeutig nur
den seriellen OBD-Adapter/Bus (Bandbreite wird mit den 16-18
Dashboard-PIDs geteilt), nicht die Aufzeichnung insgesamt. Bestaetigt:
die anderen beiden 03.09.-Logs (`163254`, `165821`) haben keine dieser
Zusatz-PIDs.

**Praktische Einordnung:** fuer die bestehenden Analysen irrelevant (nur
86 von tausenden Samples im 37-Minuten-Log betroffen, kein Skript wurde
deswegen geaendert), aber als generelle methodische Erkenntnis notiert:
**laufende Dashboard-Nutzung waehrend der Aufzeichnung verschlechtert
kurzzeitig (Dauer + kurzer Nachlauf) die Datendichte der Kernkanaele um
etwa Faktor 2** - bei kuenftigen kurzen/duennen Log-Abschnitten mit
auffaellig geringer Datenrate als moegliche Erklaerung im Hinterkopf
behalten.

## Tankstand-Kalibrierung: FLI-Sensor liest nichtlinear, unterschaetzt v.a. nahe voll (03.09.2026)

Nutzer-Info zum Nachtanken zwischen den beiden Nachmittagslogs (`2026-09-03
163254` und `165821`): 35,89l vollgetankt, Restreichweite laut Bordanzeige
am Ende von `163254` nur 4km, tatsaechliche Restmenge laut Zapfsaeule
**9,11l**.

**Erster echter Ground-Truth-Abgleichspunkt fuer die FLI-basierte
Massenschaetzung** (bisher immer nur die Annahme "level_pct/100 * 45l"
verwendet, nie gegen eine echte Zapfsaeulen-Messung geprueft):

Rohdaten direkt aus den .dlg-Dateien (Median der letzten/ersten 10 Samples,
etablierte Methode):
- Ende `163254`: FLI=14,84% -> lineare Annahme sagt 6,68l voraus,
  **tatsaechlich 9,11l** (Zapfsaeule) -> Sensor unterschaetzt um 2,43l
  (~26,7% relativ, ~5,4 Prozentpunkte).
- Start `165821` (direkt nach dem Volltanken): FLI=90,23% -> lineare
  Annahme sagt 40,61l voraus, **tatsaechlich 45,00l** (= 9,11l + 35,89l,
  bemerkenswert glatte Zahl - bestaetigt nebenbei die angenommene
  45l-Tankkapazitaet sehr gut) -> Sensor unterschaetzt um 4,40l (~9,8%
  relativ, ~9,8 Prozentpunkte).

**Kein einfacher konstanter Offset** (2,43l bei ~15% FLI vs. 4,40l bei
~90% FLI) - die Unterschaetzung waechst zum vollen Tank hin. Klassisches,
bei Kfz-Tankgebern haeufiges Verhalten: der Schwimmer-/Geberweg pro Liter
ist nahe der Fuellstandsgrenzen (voll UND je nach Bauform auch leer)
geringer als im mittleren Bereich (Tankform, Ausdehnungsraum,
konservative Volltank-Anzeige) - kein Fehler in unserer Methodik, sondern
eine echte Geber-Nichtlinearitaet.

**Konsequenz:** alle bisherigen FLI-basierten `LOG_MASS_OVERRIDE_KG`-Werte
(alle Logs seit 31.08.2026) unterschaetzen die tatsaechliche Kraftstoffmasse
leicht, staerker bei vollerem Tank. Groessenordnung des Effekts (2,4-4,4l
=> 1,8-3,3kg bei 0.745kg/l) ist klein gegenueber der Gesamtmasse (~1170kg,
<0,3%) und liegt deutlich unter der Genauigkeit der ohnehin bekannten
Modellunsicherheiten (5-8% WOT-Bias) - **kein Grund, die 60+ bestehenden
Logs rueckwirkend zu korrigieren**, nur die zwei direkt betroffenen mit
echten Messwerten:
- `2026-09-03 163254`: Endstand jetzt mit 9,11l statt 6,68l verrechnet
  (Startschaetzung unveraendert, dafuer keine Ground Truth) -> Masse
  1164,1kg -> **1165,0kg**.
- `2026-09-03 165821`: Startstand jetzt mit 45,00l statt 40,61l verrechnet
  (Endschaetzung unveraendert) -> Masse 1189,3kg -> **1190,9kg**.
Beide Werte in `LOG_MASS_OVERRIDE_KG` (`drivetrain_model_validation.py`
UND `top_speed_validation.py`) aktualisiert, beide Skripte neu gelaufen
(Ergebnis-Aenderung erwartungsgemaess vernachlaessigbar, <0,2kg-Effekt auf
Masse-abhaengige Kennzahlen).

**OFFEN:** mit nur diesen 2 Ankerpunkten (~15% und ~90%) ist keine
vollstaendige nichtlineare FLI->Liter-Kurve rekonstruierbar (fehlt v.a.
der Bereich nahe leer/0% und mittlere Fuellstaende 30-70%) - falls der
Nutzer kuenftig oefter vor dem Volltanken die Restmenge an der Zapfsaeule
mitteilt, liesse sich eine echte Kalibrierkurve aufbauen. Bis dahin bleibt
die lineare Annahme die beste verfuegbare Naeherung fuer alle anderen Logs.

## Automatischer Lauf: 2 neue Logs verarbeitet (05.09.2026)

Scheduled Task, unbeaufsichtigt. Neue Logs: `2026-09-05 102714` (~98.3min),
`2026-09-05 144153` (~103.7min).

**Masse (FLI, SOLO-Annahme):** 102714=1183.2kg (FLI ~90.2%->54.3%),
144153=1171.1kg (FLI ~54.3%->18.0%) - Verlauf konsistent mit dem
Volltanken vor `165821` (03.09., 90.2%) und seitdem fortlaufendem
Verbrauch. In `LOG_MASS_OVERRIDE_KG` (`drivetrain_model_validation.py`
UND `top_speed_validation.py`) ergaenzt.

**Kernzahlen:** IMU-Resonanz beider Logs unauffaellig (18.3-21.7 Hz X/Y/Z).
14 neue bestaetigte Kurven (102714: 7, 144153: 7) - Gesamtzahl jetzt 118.
UNMAPPED-Kanaele: 0. Keine wirklich neuen Kanaltypen (das duenne
Dashboard-PID-Set in `144153` ist bereits aus `081930` vom 03.09. bekannt).
Teillast-RMSE der 2 Logs 4.7-5.1% (Referenz-Gesamt-RMSE 6.7%, beide neuen
Logs also ueberdurchschnittlich gut). Kinematischer Fehler
Median|Fehler|=0.21%/0.24% (sehr gut). 34 neue Volllast-Segmente (102714:
23, 144153: 11). Vollast-Verhaeltnis gemessen/Modell gesamt jetzt
Median=0.97 (vorher 0.94-0.96, kein deutlicher Sprung). Keine neuen
Gang-6/Top-Speed-Segmente. Kein neues Ausrollereignis (weiterhin 12
insgesamt).

**Auffaellig 1 - Achsenrotation `102714` mit R=0.504:** deutlich unter der
0.7-Grenze (theta=62.1 Grad, n=13 Bremsereignisse). Liegt aber innerhalb
der ueblichen theta-Spanne (40.8-87.1 Grad ueber alle Logs) - vermutlich
schlicht wenige/verstreute Bremsereignisse in diesem Log, nicht weiter
untersucht (ausserhalb Routine-Scope, Praezedenzfall siehe `152038` mit
R=0.671).

**Auffaellig 2 - a_lat_peak/a_lat_mean-Verhaeltnis >2x bei mehreren
Kurven:** in `102714` zwei Kurven bei Autobahntempo (t=5383-5392s,
v=74.5km/h, mean=0.47g/peak=1.43g, Verhaeltnis=3.0x; t=5550-5554s,
v=56.7km/h, mean=0.23g/peak=0.62g, Verhaeltnis=2.7x) - moeglich kurze
Lastwechsel/Unebenheiten in schneller Kurve, kein automatischer Fehler
(siehe Praezedenzfall 2.47g-Kurve in PROJEKT_STAND.md). In `144153` zwei
weitere mit Verhaeltnis>2x, aber auf niedrigem Niveau (0.20g/0.43g und
0.16g/0.33g) - eher Rauschen als echtes Schleudern.

**Auffaellig 3 - Lenkwinkelmodell, R² gesunken:** beide neuen Logs haben
STEER_ANGL_EPS (Gesamtzahl Logs mit Kanal jetzt 11 statt 9).
`steering_zero_offset.py` liefert Offsets -18.2° (102714) und -2.7°
(144153), im ueblichen Rahmen. `steering_lateral_model.py` danach:
k=0.016947, R²=0.921, n=63 Kalibrierpunkte (vorher k=0.016968, R²=0.935,
n=49) - k praktisch unveraendert, R² aber um 0.014 gesunken. Gemaess
Routine-Vorgabe Rohdaten des staerksten Ausreissers geprueft: staerkster
LOO-Fehler (+15.18°/s) gehoert zu `144153` t=6199-6201s (v=19km/h,
gemittelter Lenkwinkel=-297.4°). Rohe STEER_ANGL_EPS-Werte in diesem
Fenster (unkorrigiert) zeigen einen sehr schnellen Richtungswechsel
(+214° auf -341° innerhalb von ~1s, danach wieder auf +393° elf Sekunden
spaeter) - klassisches Muster eines Wende-/Einparkmanoevers bei
Schrittgeschwindigkeit, KEIN GPS-Genauigkeits-Fehlalarm wie am 01.09.
(dort war die Ursache eine andere). Das stationaere Lenkwinkel-Modell
(konstanter Winkel -> proportionale Gierrate) passt bei so schnellen
Lenkbewegungen strukturell schlecht, weil die 2s-Kalibrierfenster-Mittelung
den tatsaechlich stark schwankenden Winkel verwischt. Einordnung: erklaerte,
nachvollziehbare Verschlechterung durch einen einzelnen schwierigen
Kalibrierpunkt, keine neue Fehlerklasse - keine weitere Aktion noetig.

## Nachtrag: SOLO-Annahme fuer die 05.09.2026-Logs war falsch - Beifahrer 75kg ergaenzt (06.09.2026)

Nutzer-Korrektur: fuer beide Logs vom 05.09.2026 (`102714`, `144153`) war
tatsaechlich ein Beifahrer (75kg) an Bord - die vom automatischen Lauf
gemaess Routine-Vorgabe getroffene SOLO-Annahme (nur Fahrer, siehe
Memory "mx5-weight-check-on-new-logs") war fuer diese beiden Logs falsch.
Genau der Fall, fuer den die Routine explizit den SOLO-Hinweis in ihrem
Bericht mitgibt, damit der Nutzer ihn bei Bedarf korrigieren kann - hat
hier funktioniert wie vorgesehen.

**Korrektur in `LOG_MASS_OVERRIDE_KG`** (`drivetrain_model_validation.py`
UND `top_speed_validation.py`): einfach +75kg auf die bisherigen
FLI-basierten Werte:
- `2026-09-05 102714`: 1183.2kg -> **1258.2kg**
- `2026-09-05 144153`: 1171.1kg -> **1246.1kg**

`drivetrain_model_validation.py`, `top_speed_validation.py` UND
`partial_load_model.py` (nutzt Masse ebenfalls fuer die
Beschleunigungs-Validierungssegmente, anders als bei der kleinen
Tankstand-Korrektur vom 03.09. hier keine vernachlaessigbare Groessenordnung:
+75kg ist ~6.3% der Fahrzeugmasse) neu gelaufen.

**WICHTIGES ERGEBNIS:** `drivetrain_model_validation.py` Vollast-Verhaeltnis
gemessen/Modell springt von Median **0.97 auf 1.00** (Korrelation weiterhin
0.98, jetzt 115 statt 34 Segmente betroffen zaehlend). Die beiden
05.09.-Logs stellen 34 der 115 Vollast-Segmente (~30%) - die falsche
SOLO-Masse hatte das gemessene/Modell-Verhaeltnis in genau diesem
Teildatensatz nach unten verzerrt (zu wenig angenommene Masse -> zu wenig
noetige Kraft -> zu niedriges gemessen/Modell-Verhaeltnis). **Einordnung:
der seit Ende August dokumentierte ~5-8%-WOT-Bias ("Modell ueberschaetzt
die Beschleunigung") ist damit zu einem relevanten Teil auf einen
Massenfehler zurueckzufuehren, nicht zwingend auf CdA/eta wie bisher
vermutet** - die bestehende Diskussion zu CdA/eta/Drehmomentkurve
(Cross-Validierungs-Abschnitt, Ausrollversuche) bleibt zwar grundsaetzlich
gueltig, sollte aber nicht mehr unhinterfragt auf der alten 0.94-0.97-Zahl
aufbauen. `partial_load_model.py` (Gesamt-RMSE weiterhin 6.7pp) und
`top_speed_validation.py` (RMSE 0.177 statt 0.173 m/s², im Rauschen, keine
Gang-6-Segmente in den betroffenen Logs) zeigen keine vergleichbare
Verschiebung.

**Lehre fuer die Routine:** ein einzelner falscher Massen-Wert (hier: SOLO-
Annahme statt Beifahrer) kann bei ausreichend vielen Segmenten eine
projektweite Kennzahl sichtbar verschieben - die Nutzer-Korrekturmoeglichkeit
fuer die SOLO-Annahme (siehe Memory "mx5-weight-check-on-new-logs") ist
damit kein Nice-to-have, sondern fuer die Aussagekraft des WOT-Bias-Befunds
direkt relevant.

## Neue Hoehendaten-Quelle gefunden: DGM-XYZ (LGB Brandenburg) schliesst Luecken der ALS-Punktwolken (06.09.2026)

Nutzerfrage: "hast du dir die benoetigten Hoehendaten geholt?" - Anlass:
`top_speed_validation.py` zeigte 5 Top-Speed-Segmente ohne
Steigungskorrektur (13 insgesamt, 5 davon in neuem, bisher unkartiertem
Gebiet). Geprueft: 4 der 5 betroffenen Segmente (`2026-09-02 102031`,
`2026-09-05 102714` 2x, `2026-09-05 144153` 2x) liegen deutlich suedlich
der bisherigen Kernregion (lat 51.84-52.68°N statt der ueblichen
52.3-52.9°N) - Richtung Lübbenau/Spreewald bzw. Koenigs-Wusterhausen,
offenbar eine neue Fahrtroute.

**4 benoetigte Kacheln identifiziert, davon nur 1 bei der bisherigen
ALS-Punktwolken-Quelle verfuegbar:**
- `als_33417-5807.zip`: HTTP 200 - heruntergeladen, normal verarbeitet.
- `als_33426-5743.zip`, `als_33407-5772.zip`, `als_33407-5776.zip`: HTTP 404
  (auch bei +-1 Nachbarkacheln getestet, kein Rundungsfehler - echte
  Luecke der ALS-Quelle, trotz eindeutiger Lage in Brandenburg).

**Fund: die LGB bietet PARALLEL ein bereits fertig gerastertes 1m-DGM als
XYZ-Text an** (`https://data.geobasis-bb.de/geobasis/daten/dgm/xyz/
dgm_<E_km><zone>-<N_km>.zip`, "E N Z" pro Zeile, 1000x1000 Punkte, exakt
gleiches Gitter/CRS wie die ALS-Rasterung). Alle 3 vorher fehlenden
Kacheln dort verfuegbar (HTTP 200) - vermutlich publiziert die LGB das
fertige DGM flaechendeckender als die rohen Punktwolken. Deutlich kleiner
(~4MB statt ~180MB) UND kein Klassifizierungsschritt noetig (schon
fertiges Gitter, keine Bodenpunkt-Filterung wie bei ALS-Punktwolken).

**Umgesetzt in `scripts/elevation_model.py`:** neue Funktion
`build_tile_from_xyz()` + `DGM_XYZ_TILE_NAME_RE`, `build_all_tiles()`
verarbeitet jetzt zusaetzlich `hoehendaten/dgm_*.zip` (gleicher Ordner wie
die ALS-Zips) fuer Kacheln OHNE ALS-Abdeckung (ALS hat Vorrang, keine
bestehende Kachel wird ueberschrieben) - erzeugt identisches npz-Schema,
fuer den Rest von `ElevationModel` voellig transparent. Bugfix waehrend
der Umsetzung: eine der 3 Kacheln nutzte Komma statt Leerzeichen als
Trennzeichen (offenbar je nach Erstellungsjahr/Exportlauf unterschiedlich)
- Parser jetzt robust gegen beide Varianten (Komma vor `np.loadtxt` durch
Leerzeichen ersetzt).

**Ergebnis (direkt an den 5 betroffenen Segmenten getestet, nicht ueber
den vollen Skriptlauf - siehe Einschraenkung unten): 4 von 5 bekommen
jetzt eine echte Steigungskorrektur** (vorher alle `None`):
- `102714` t=1767-1774s: -0.87% (leicht abschuessig)
- `102714` t=4542-4555s: +0.21% (praktisch eben)
- `144153` t=2517-2524s: -0.01% (eben)
- `144153` t=2607-2612s: +0.47%
Das 5. Segment (`102031` t=587-591s) bleibt `None` - war aber NIE ein
Kachel-Problem (Quelle war schon vorher vorhanden), sondern ein
unabhaengiger, nicht weiter untersuchter Filter-Edge-Case (nur n=4 Punkte/
132m Strecke - vermutlich zu kurz fuer eine robuste Steigungsschaetzung).

**Bruecken-Cache neu aufgebaut** (`build_bridge_cache()`, jetzt bbox lat
51.82-52.80° statt vorher enger gefasst): 6807 statt vorher ~618
Bruecken-Wege (deutlich mehr, da die neue Suedregion viel mehr Flaeche
abdeckt).

**NACHTRAG: dritter Lauf erfolgreich durchgelaufen (~4 Minuten statt der
vorherigen 15-25+ Minuten)** - bestaetigt die Vermutung oben: die beiden
vorherigen, abgebrochenen Laeufe hatten die Live-Abfrage-Caches
(`berlin_wms_cache.json`, `sachsen_anhalt_wcs_cache.json`) bereits
groesstenteils aufgewaermt (Caches werden inkrementell gespeichert, auch
bei Abbruch bleibt der bis dahin erreichte Fortschritt erhalten), daher
lief der dritte Versuch schnell durch. **`results/top_speed_validation_summary.json`
ist jetzt aktuell (06.09.2026, 13:24 Uhr):** 85 statt vorher 81 Segmente
mit Steigungskorrektur, nur noch 9 statt 13 ohne DGM-Abdeckung. RMSE mit
Gefaellekorrektur (73 Segmente ohne die unzuverlaessigen Bayern-Segmente):
0.170 m/s², weiterhin ~5% besser als ohne Korrektur - stabil, keine
Verzerrung durch die neuen Kacheln/den groesseren Bruecken-Cache.

**Naechster Schritt (Empfehlung, nicht umgesetzt):** die DGM-XYZ-Quelle
ist generell attraktiv (kleiner, schneller, evtl. vollstaendigere
Brandenburg-Abdeckung als ALS) - bei Gelegenheit pruefen, ob sie
systematisch als PRIMAERE Quelle statt nur als Luecken-Fallback taugt,
das waere fuer neue Kacheln in Zukunft schneller.

## Variable Handyhalterung: automatische Pro-Log-Achsenerkennung gebaut (06.09.2026)

Nutzer-Ansage: zwei neue Logs (`2026-09-06 142931`, `145116`), Telefon
diesmal ANDERS verbaut - liegt "auf dem Ruecken" (Display nach oben) in
der Mittelkonsole links vom Schalthebel, obere Kante leicht zur
Fahrtrichtung verdreht. WICHTIG: die Halterungsposition kann sich ab jetzt
**von Fahrt zu Fahrt aendern** - keine feste Konvention mehr.

**Bestaetigt per Gravitationsvektor im Stillstand (spaetes Fenster, nicht
das erste - das ist oft durch Montage/Handling verunreinigt): Z ist jetzt
die Vertikalachse** (Z≈9.84-9.85 m/s², X/Y≈0, Std 0.15-0.38) - vorher
immer Y. Zweite Konsequenz bestaetigt: die Gierrate kommt jetzt aus
`RotationRateZ` statt `RotationRateY` (Korrelation zur echten
GPS-Kursaenderung: -0.76/-0.80, exakt dasselbe Muster wie zuvor bei Y).

**Neues, zentrales Modul [scripts/imu_orientation.py](scripts/imu_orientation.py):**
`detect_vertical_axis(db_path)` bestimmt PRO LOG automatisch die
Vertikalachse (Gravitationsvektor-Methode, faellt auf Y zurueck falls kein
AccelerationWithGravity*-Kanal oder keine eindeutige Standphase existiert -
korrekt fuer alle 70+ Logs vor der neuen Halterung). `horizontal_axes()`
liefert die zwei uebrigen Achsen in fester Reihenfolge X<Y<Z, `yaw_channel()`
den zugehoerigen RotationRate-Kanal.

**Umgebaut, um die Achsen PRO LOG statt fest anzunehmen:**
- `brake_event_analysis.py`: Achsenrotation (theta) nutzt jetzt das per Log
  erkannte Horizontalachsenpaar statt fest X/Z. Ergebnis-JSON traegt jetzt
  `vertical_axis`/`horizontal_axes` fuer Transparenz.
- `corner_event_analysis.py`: Gierraten-Kanal jetzt `RotationRate<erkannte
  Achse>` statt fest RotationRateY.
- `grip_estimation.py`: beides zusammen (theta-Rotation + Gierraten-Kanal).

**Regressionsfrei fuer alle Logs vor 2026-09-06 verifiziert:** z.B.
`2026-08-27 170339` liefert nach dem Umbau exakt denselben Wert wie vorher
dokumentiert (48.3°, R=0.851, n=28) - die Refaktorierung aendert nichts an
bestehenden Ergebnissen, nur neue Logs mit anderer Halterung werden jetzt
korrekt behandelt statt (unbemerkt) falsch in die alte X/Z-Formel gepresst
zu werden.

**Ergebnis fuer die 2 neuen Logs (Ebene=X/Y statt X/Z):**
| Log | theta (Vorwaerts) | R | n Bremsereignisse | n Kurven |
|---|---|---|---|---|
| 142931 | 227.6° | 0.79 | 6 | 7 |
| 145116 | 281.1° | 0.96 | 8 | 5 |

Beide plausibel (R>0.75). `grip_estimation.py` liefert fuer beide jetzt
physikalisch plausible Werte (max 0.78g/1.00g Quer) statt der Absturz-
/Garbage-Werte, die die alte, fest verdrahtete X/Z-Formel produziert haette.

**Performance-Bug gefunden+behoben waehrend der Umsetzung:** die erste
Version von `detect_vertical_axis()` lud jeden Gravitationskanal NEU pro
Kandidaten-Standfenster (bei Logs mit vielen Ampel-/Stauphasen also
mehrfach denselben ~100k-Zeilen-Kanal) - fuehrte zu stark aufgeblaehter
Laufzeit. Fix: jeder Kanal wird jetzt EINMAL pro Log geladen, Fenster nur
noch in-memory gefiltert. Isolierter Test bestaetigt: einzelne Logs
brauchen 6-25s (vorher teils 35s+ durch die Mehrfachladung). Trotz Fix
liefen die vollen Skript-Durchlaeufe heute ungewoehnlich lange (teils 20-60
Minuten statt ueblicher 1-3) - Ursache NICHT im eigenen Code, sondern
massive Fremdlast auf der Maschine (mehrere parallele Claude-Sessions,
Firefox, PyCharm etc. gleichzeitig aktiv, load average ~3.3) - per
isoliertem Timing-Test an mehreren "haengend wirkenden" Logs bestaetigt
(jeweils nur 12-25s bei direktem Aufruf).

**Masse fuer die 2 neuen Logs:** 1165.6kg (142931, solo, FLI 20.3%->19.1%)
und 1239.5kg (145116, +75kg Beifahrer nutzerbestaetigt, FLI 18.4%->14.6%)
- in `LOG_MASS_OVERRIDE_KG` ergaenzt.

**Volle Pipeline durchgelaufen:** `build_datalake.py` (71 Logs, 0
UNMAPPED), `drivetrain_model_validation.py` (122 Segmente, Verhaeltnis
weiterhin Median 1.00), `partial_load_model.py` (RMSE weiterhin 6.7pp),
`coastdown_analysis.py` (kein neues Ereignis, weiterhin 12), alle drei
oben umgebauten Skripte, `steering_zero_offset.py` (13 Logs mit
STEER_ANGL_EPS, neue Offsets 142931=+4.3°/145116=+4.2°, beide klein/
plausibel), `steering_lateral_model.py` (k=0.01630, R²=0.917, n=75 statt
63 - kleine, unauffaellige Verschiebung von k=0.01695/R²=0.921).

**OFFEN:** die Annahme "GYRO_SIGN_SCALE=-1.0 gilt fuer JEDE erkannte
Vertikalachse" ist bisher nur fuer Y und Z empirisch bestaetigt, NICHT
fuer X (kein solcher Log bisher aufgetreten) - falls ein Log mit X=vertikal
auftaucht, Korrelation gegen GPS-Kursaenderung nachpruefen statt blind zu
vertrauen.

## Kurvendetektion fuer schnelle Grossradius-Kurven ("Autobahn-Sweeper") repariert (06.09.2026, Nutzeranfrage)

Anlass: Nutzer fuhr 5 Kurven mit Tempomat (konstant 92-94 km/h, R=100-376m)
im letzten Log des Tages und fragte, ob diese Daten fuer ein
"Kurvenfahrmodell"/Rennrunden-Simulationen taugen. Antwort: ja, gerade weil
Tempomat a_long≈0 garantiert (sauberste Isolation des reinen Lenkwinkel-
Gierraten-Zusammenhangs) UND weil diese Kurven genau die bisher
unvalidierte Radius-/Geschwindigkeitsluecke fuellen (siehe
[[mx5_steering_angle]]: bisherige Kalibrierbasis nur R=11-82m/v=11-72km/h).
Voraussetzung: `corner_event_analysis.py` musste diese Kurven erst
zuverlaessig ERKENNEN - tat es nicht (0 von 5 im Katalog).

**Root Cause 1 - Fenster-Verwaesserung:** das feste ±4s-GPS-Kursfenster
(`HEADING_WINDOW_S`) mittelt bei hoher Geschwindigkeit (kurze Kurvendauer
relativ zum Fenster) die wahre Spitzen-Gierrate weg - ein Sweeper mit
echtem Peak ~13-15°/s zeigte im gemessenen Kurs nur 7.99°/s, hauchduenn
unter der Schwelle `HEADING_RATE_MIN_DEG_S=8.0`. Fix: Fenster jetzt
adaptiv pro Sample, `window_s = clip(GPS_MIN_DISPLACEMENT_M / v, 2.0, 4.0)`
- bei niedriger Geschwindigkeit (Kreisverkehre etc.) unveraendert bei 4.0s,
bei hoher Geschwindigkeit schrumpft es auf minimal 2.0s.

**Root Cause 2 - Luecken zerreissen scharfe Kurven:** das kuerzere Fenster
kann bei SEHR scharfen Kurven (z.B. der ESP-Kurve aus log 145116,
t=1037-1041s) fuer ein einzelnes ~1Hz-GPS-Sample die Mindestbasisstrecke
knapp verfehlen (Sehnen- statt Bogenlaenge bei schneller Kursaenderung) -
das zerreisst ein durchgehendes Ereignis in zwei zu kurze Fragmente, die
BEIDE unter `MIN_EVENT_DURATION_S` fallen und komplett verschwinden (genau
das ist der ESP-Kurve zunaechst passiert). Fix: `group_events()` ueber-
brueckt jetzt Luecken bis 2.5s (`MAX_GAP_S`) zwischen zwei Kandidaten-
Laufen, bevor die Mindestdauer geprueft wird.

**Root Cause 3 - Mindestdauer zu hoch fuer 1Hz-GPS:** `MIN_EVENT_DURATION_S`
war 1.5s - bei ~1Hz-Sampling verlangt das de facto 3 Samples, obwohl 2
aufeinanderfolgende, cross-validierte Samples (GPS-Kurs UND Gyroskop
stimmen im Vorzeichen ueberein) bereits ein plausibles, kurzes Ereignis
sind und nur ~1.0s aufspannen. Auf 0.9s gesenkt - laesst den 2-Sample-Fall
zu, verwirft aber weiterhin echte Einzelsample-Ausreisser (duration=0).

**Nebenbug gefunden+behoben:** Richtungsbestimmung nutzte `np.mean()` statt
`np.nanmean()` ueber das (jetzt teils ueberbrueckte, NaN-haltige) Segment -
ein einzelner NaN-Wert in der Luecke kippte `np.mean()` auf NaN, was
`> 0` immer als False auswertet und die Kurve faelschlich als "links"
statt "rechts" markierte (an der ESP-Kurve entdeckt).

**Ergebnis:** 130 -> 234 bestaetigte Kurven ueber alle 22 Logs (Regressions-
check: der bekannte 2.47g-Referenzwert aus log `170339` bleibt exakt
erhalten, nur jetzt als EIN durchgehendes statt zwei fragmentierte
Ereignisse; keine neuen Ausreisser - max. Peak unter den neu hinzu-
gekommenen Ereignissen 1.06g bei 61km/h, plausibel). 3 der 5 Tempomat-
Sweeper jetzt erfasst (die 2 sehr sanften bleiben bewusst unterhalb der
Schwelle, siehe Projekt-Philosophie "nur eindeutige Kurven").

**Lenkwinkel-Kalibrierung neu gerechnet mit erweitertem Katalog:**
`steering_lateral_model.py`: n=75->128 Kalibrierpunkte, **k=0.016305->
0.017706, R²=0.917->0.936, LOO-RMSE=4.28->3.91°/s**. Mehrere neue Punkte
bei v=82-92 km/h / R=75-150m (z.B. `145116` t=164s: v=92km/h, R≈114m;
`144153` t=5517s: v=84km/h, R≈150m) liegen jetzt klar ueber dem alten
Maximum (v=72km/h/R=82m) - die dokumentierte "Autobahn-Sweeper"-Luecke ist
damit nicht mehr komplett unvalidiert, auch wenn die Stichprobe dort noch
duenn ist (Einzelpunkte, kein systematisches Sweep).

**Nachtrag: `top_speed_validation.py` fuer die 2 neuen 06.09.-Logs nachgeholt**
(war beim urspruenglichen Lauf uebersprungen worden, Zeitstempel-Check
zeigte den Skript-Output noch auf dem Stand vor deren Verarbeitung).
Ergebnis: 0 neue Gang-6-Segmente (beide Logs blieben unter 93 km/h, weit
unter der 170 km/h-Schwelle - erwartungsgemaess). `check_dgm_coverage_gaps.py`
zeigt weiterhin nur die 9 bekannten Luecken (2026-09-02 102031 + diverse
CSVLog_*-Segmente Bayern/Sachsen-Anhalt) - keines der 2 neuen Logs traegt
dazu bei. Damit sind fuer diese 2 Logs jetzt alle 14 Routine-Schritte
durchlaufen.

## Zwei neue Logs vom 07.09.2026 (automatischer Routine-Lauf)

**Logs:** `2026-09-07 082100` (49.6 Min) und `2026-09-07 123731` (36.7 Min).
**Masse (SOLO-Annahme, unbestaetigt):** 1189.3kg (082100, FLI ~90.2%->90.2%)
und 1189.0kg (123731, FLI ~90.2%->88.7%) - beide nahe volltanken.

**Kernzahlen:** 29 neue bestaetigte Kurven (9+20, jetzt 263 gesamt), 5 neue
Volllast-Segmente (kinematischer Fehler 0.46%/0.39%, unauffaellig), 0
UNMAPPED-Kanaele (73 Logs), keine wirklich neuen Kanaltypen, 0 neue Gang-6-
Segmente, keine neue DGM-Luecke. Teillast-RMSE weiterhin ~6.6pp.
Lenkwinkelmodell: n=128->156, k=0.017706->0.018064, R²=0.936->0.929 (leichter
Ruckgang, kein Alarmsprung).

**Auffaelligkeit 1 - IMU-Resonanz deutlich ausserhalb 18-23 Hz:** beide neuen
Logs zeigen nur auf EINER Achse eine Resonanz im gewohnten Fenster (082100:
Y=21.1Hz, X/Z=7.2Hz; 123731: Z=18.3Hz, X=24.5Hz, Y=6.5Hz) statt wie bisher
ueblich auf allen drei Achsen. Faellt zeitlich mit der seit 06.09. variablen
Handyhalterung zusammen (vertikale Achse jetzt Z statt Y) - moegliche
Erklaerung, aber nicht abschliessend geklärt.

**Auffaelligkeit 2 - neuer Ausrollversuch-Fund:** `2026-09-07 123731`
t=1566.9-1572.3s, v=121->111km/h (5.4s, nur 6 GPS-Punkte, keine DGM-
Abdeckung). Jetzt 13 Ausrollereignisse insgesamt (vorher 12). Gefittetes
CdA=1.116m² weicht stark vom Referenzwert (0.647m²) ab - Fenster ist kurz
und schmal, passt zum bekannten Muster "Windkorrektur/CdA-Fit fuer kurze
Ereignisse unzuverlaessig" (siehe [[mx5_coastdown_analysis]]).

**Sonstiges:** Achsenrotation (theta) beider neuen Logs im X/Y-Ebenenpaar
(274.3°/R=0.776 und 267.3°/R=0.837, wie schon bei den beiden 06.09.-Logs) -
die alte 40.8-83.1°-Spanne gilt nur fuer X/Z und ist hier nicht anwendbar.
Zero-Offset fuer `2026-09-07 123731` mit +155.9° neuer Hoechstwert (bisher
max. +147.8° bei `2026-09-02 150720`) - Korrektur greift, aber der groesste
LOO-Ausreisser im Lenkwinkelmodell stammt aus genau diesem Log (t=1355-1356s,
sehr kleiner korrigierter Winkel +4.6° bei gemessener Gierrate -14.8°/s).

## Nachtrag: Auffaelligkeit 1 vom 07.09. aufgeloest - Halterungsresonanz-Theorie revidiert (07.09.2026)

Nutzer bestaetigt: das Handy ist jetzt starr mit dem Interieur verbunden,
optisch ist kein Schwingen einer Halterung mehr zu erkennen (vorher schon
sichtbar). Damit faellt die urspruengliche Erklaerung fuer die seit
28.08. bekannte ~21-23Hz-Resonanz (siehe "Bestaetigter Befund: feste
mechanische Halterungsresonanz", oben) als reine Halterungs-Eigenresonanz
weg - die tri-axiale Signatur passte zu einer lose schwingenden Halterung,
die in alle drei Achsen einkoppelt. Ohne separates nachgiebiges
Halterungssystem ist die jetzt nur noch EINACHSIGE Resonanz (082100:
Y=21.1Hz, 123731: Z=18.3Hz) plausibler eine echte, raeumlich lokalisierte
**Fahrzeugschwingung**, die direkt ueber den Befestigungspunkt im
Interieur uebertragen wird. Einzelachsigkeit ist fuer eine reale
Struktur-Eigenmode plausibel (regt nicht alle Richtungen gleich an),
fuer eine frei schwingende Masse eher nicht. Siehe [[mx5_vibration_analysis]]
fuer Details. **Gilt ausdruecklich nur fuer genau diese am 06./07.09.
erfasste Einbaulage** (starr, Handy fest mit dem Interieur verbunden,
kein Halterungsarm) - keine allgemeine Regel fuer beliebige zukuenftige
Mounts. Aendert sich die Einbaulage wieder (neue Position, neue
Befestigungsart, wieder ein Halterungsarm), muss die Einzelachsen- vs.
tri-axial-Frage fuer diesen neuen Mount erneut geprueft werden, bevor sie
als unauffaellig gilt - nicht automatisch uebernehmen. Konsequenz fuer
kuenftige Routine-Laeufe: eine Einzelachsen-Resonanz im 18-23Hz-Fenster
ist NUR bei Fortbestehen dieser konkreten starren Einbaulage nicht mehr
automatisch als Auffaelligkeit zu melden - bei jeder erkennbaren
Aenderung der Einbaulage (z.B. andere Handyposition/-orientierung als am
06./07.09.) gilt die Vermutung nicht mehr automatisch, dann wieder
pruefen statt annehmen.

## Nachtrag: groesster LOO-Ausreisser im Lenkwinkelmodell (123731, t=1355-1356s) aufgeklaert - GPS-Tunnelartefakt (07.09.2026)

GPS-Track ±30s um den Ausreisser visualisiert (Leaflet/OSM,
`results/gps_track_123731_t1355_anomaly.html`) - der Punkt liegt exakt
an der Ausfahrt des Forsthaustunnels auf der A111 bei Tegel
(Nutzer-bestaetigt). Rohes `RotationRateZ` ist dort ruhig (+1.3 bis
+1.9°/s, passt zum kleinen korrigierten Lenkwinkel +4.6°) - die
gemeldeten -14.8°/s stammen aus der GPS-Heading-Rate-basierten
"gemessenen" Gierrate in `corner_event_analysis.py`, nicht aus dem
Gyro. Klassisches Symptom eines GPS-Fixes nach Tunnelausfahrt
(Reakquise/Multipath), keine reale Fahrzeugbewegung. Siehe
[[mx5_steering_angle]] fuer Details. Noch nicht als automatischer
Filter umgesetzt - bei weiteren Faellen (Tunnel/Unterfuehrung in
Streckennaehe + grosse GPS-vs-Gyro-Gierraten-Diskrepanz) lohnt sich
ein generischer Ausschluss-Check in `corner_event_analysis.py`.

**Umgesetzt:** Ereignis in `steering_lateral_model.py` per
`EXCLUDED_CALIBRATION_EVENTS`-Set ausgeschlossen und neu kalibriert:
n=156->155, k=0.018064->0.018053, R²=0.929->0.933, LOO-RMSE=3.90°/s.
Der bisher groesste LOO-Ausreisser ist aus der Fehlerliste verschwunden
(bestaetigt den GPS-Tunnelartefakt als Ursache statt Modellschwaeche).

## Nachtrag: Modellschwaeche des Lenkwinkelmodells behoben - saettigender Gain statt fixem k (07.09.2026)

Die bereits dokumentierte Schwaeche ("unterschaetzt Gierrate bei
schnellen Lenkbewegungen") genauer untersucht: Modell/GPS-Verhaeltnis
zeigt ueber ALLE 15 Lenkwinkel-Logs (nicht nur die neuen) einen klaren,
monotonen Trend mit dem Lenkwinkel - ~0.93 bei grossen Lenkwinkeln
(>150°, Parkmanoever) bis ~1.3-1.4 bei kleinen Lenkwinkeln (<20°,
Autobahn-Wedler). Getestet gegen Gewichtungsartefakte (Median-Ratio,
Mean-Ratio statt OLS) - der Trend bleibt in allen Varianten bestehen,
also echte Reifenkraft-Saettigung, kein Fit-Artefakt.

**Fix:** `k(Lenkwinkel) = k1 + k2*|Lenkwinkel|` (k2<0) statt fixem k, in
`steering_lateral_model.py` per 2-Parameter-Kleinste-Quadrate (weiterhin
ohne Achsenabschnitt in x) umgesetzt. Ergebnis: R²=0.933->0.950,
LOO-RMSE 3.90->3.40°/s (~13% besser, kreuzvalidiert, keine Ueberanpassung).
k1=0.023276 (Gain nahe Geradeausfahrt) faellt auf k(314°)=0.01160 bei
maximalem beobachtetem Lenkwinkel - fast Faktor 2 Spreizung. Neuer
zweigeteilter Kalibrierplot (`results/steering_lateral_calibration.png`):
links Kalibriergerade nach Lenkwinkel eingefaerbt, rechts der
empirische Gain y/x direkt gegen |Lenkwinkel| mit Saettigungskurve.
Siehe [[mx5_steering_angle]] fuer Details.

## Interaktiver Ideallinien-Editor fuer Spreewaldring: Lenkgeschwindigkeit als echte Randbedingung, Fixpunkt-Bearbeitung (07.09.2026)

Neues Werkzeug `scripts/spreewaldring_racing_line_editor.py`, generiert eine
einzelne, autarke `results/spreewaldring_racing_line_editor.html` - reines
Client-seitiges JS (kompletter Port des Fahrzeug-/Rundenzeit-Modells aus
`performance_simulation.py`/`spreewaldring_racing_line_optimal.py` nach
JavaScript), kein Server noetig, entspricht dem bisherigen Projekt-Grundsatz
"keine Backend-Abhaengigkeit fuer interaktive Tools". Ausgangslinie: bester
Snapshot aus `spreewaldring_racing_line_corners_summary.json` (11 Kurven
sequenziell lokal optimiert, ~100.7s bei mu=1.0). Startlinie wird auf die vom
Nutzer im Orthofoto markierte ECHTE Start-Ziel-Linie rotiert (`START_FINISH_
ROLL`), nicht mehr Kurve 1.

**Funktionsumfang:** Punkt auf der Linie anklicken und ziehen (Pinsel mit
Smootherstep-Falloff), Zoom/Pan, Echtzeit-Auto-Animation mit Fahrer-Dashboard
(Bremse/Gas-Balken, Lenkrad-Grafik, Gang/Drehzahl), Schaltpunkt-Liste,
v(s)/Drehzahl-Trace, Hover-Tooltip mit naechstgelegenen Fixpunkten, sowie
Einzelschritt-Buttons ("Punkt vor/zurueck") fuer die Feininspektion im
Pausenmodus.

**Kernbefund 1 - die urspruengliche Lenkgeschwindigkeits-Messung war ein
Diskretisierungsartefakt:** Nutzer forderte eine harte physikalische
Randbedingung (Lenkrad darf 240°/s nie ueberschreiten, realistische kleine
Korrekturen eher ~70°/s). Erste Messung ergab bis zu 934°/s auf der
UNVERAENDERTEN Basislinie - Ursache war die Ableitung dTheta/ds ueber
unmittelbar benachbarte Punkte (~6m Basis). Test mit progressiv breiterem
Differenzfenster auf derselben, unveraenderten Linie: w=1 -> 934°/s, w=2 ->
479°/s, w=5 -> 154°/s, w=10 -> 116°/s. Mit derselben Fensterbreite wie die
bereits bestehende Kruemmungsberechnung (`CURVATURE_WINDOW_M=15m`, also
w=round(15/3)=5 Punkte) ist die UNVERAENDERTE Basislinie bereits konform -
keine Geometrieaenderung noetig, nur die Messmethode war falsch
(`computeSteerRateDegS()`).

**Kernbefund 2 - derselbe Diskretisierungsfehler steckte in einer zweiten,
separaten Formel:** Die Lenkrad-Dashboard-Anzeige (`drawCar()`) bestimmt
Links-/Rechtseinschlag ueber unmittelbare Nachbarpunkte und wurde bei der
obigen Korrektur nicht mitgezogen. Nutzer-Report 08.09.2026: Lenkrad
durchlief bei Punkt 512/513 kurz die Nullachse, obwohl die tatsaechliche
(fensterbasierte) Lenkrate dort durchgehend glatt und einseitig blieb (0.8
bis 12°/s, kein Vorzeichenwechsel). Fix: dieselbe `CURVATURE_WINDOW_M`-
Fensterbreite auch fuer die Vorzeichenbestimmung im Dashboard verwendet.
Live in der Simulation nachgeprueft (Punkt-fuer-Punkt durch 500-520
geschaltet): Lenkrad faellt jetzt durchgehend von -32.5° auf -10.5°, kein
Nulldurchgang mehr.

**Pinsel-Falloff-Kernel:** raised-cosine ersetzt durch Smootherstep (Perlin)
- raised-cosine ist nur in der Steigung, nicht in der Kruemmung glatt am
Fensterrand, das erzeugte einen echten (nicht nur kosmetischen)
Kruemmungssprung genau am Rand des Bearbeitungsbereichs.

## Nachtrag: mehrere nahe Fixpunkte - drei Anlaeufe verworfen, vierter (geschwindigkeitsbasiert) haelt (07.09.2026, Nutzeranfrage)

Nutzer-Report: mehrere kurz hintereinander gesetzte Fixpunkte ergaben keine
fliessende Linie, sondern "klebten" an Korridorrand oder Streckenmitte in
den Luecken dazwischen, bzw. zeigten wild schwankende Kruemmungsradien
(35m -> 5000m -> 35m) trotz optisch glattem seitlichen Versatz n(s).

**Versuch 1 (verworfen) - Smootherstep-Kette:** je zwei nahe Fixpunkte
direkt per Smootherstep verbunden. Problem: Smootherstep erzwingt an JEDEM
Knoten Steigung=0 - bei 3+ verketteten Punkten entsteht eine Kette
einzelner "Beulen" mit Wendepunkt an jedem Knoten statt einer durchgehenden
Kurve.

**Versuch 2 (verworfen) - kubischer Hermite-Spline mit Catmull-Rom-
Tangenten:** Steigung an jedem Knoten = Sekante durch die Nachbarn statt 0
erzwungen - deutlich glatter (kein erzwungener Wendepunkt an jedem
Fixpunkt), aber weiterhin reine Geometrie ohne Korridor-Nebenbedingung IM
Loeser (nachtraeglicher Clip auf [LO,HI] erzeugt selbst wieder eine
Wand-Kante).

**Versuch 3 (verworfen) - Minimum-Kruemmungs-QP mit Korridor als
Box-Nebenbedingung:** Recherche zu professioneller Ideallinien-Theorie
(Kapania/Subosits/Gerdes 2019, Stanford; TUM-Minimum-Curvature-Verfahren)
ergab: schnelle Rennlinien werden ueber ein Kruemmungsprofil kappa(s)
beschrieben, Klothoiden haben ein LINEARES Kruemmungsprofil, professionelle
Loeser haben die Korridorbreite als lineare Nebenbedingung IM SELBEN
Optimierungsproblem (nicht nachtraeglich). Umsetzung: Minimierung von
Sum((n[i-1]-2n[i]+n[i+1])^2) (Standardmass fuer Kruemmung) per projiziertem
Gauss-Seidel, Korridor-Clip IN jeder Iteration. Loeste das Korridorproblem
sauber (0 Verletzungen), aber: **reine Geometrie kennt weder Geschwindigkeit
noch Kraftreserve** - Nutzer-Report zeigte einen echten "Doppelscheitel"
(Lenkwinkel kippt von +34° auf -20° und zurueck mitten in einer
durchgehenden Rechtskurve, mit Radius-Aufblaehung auf 322m) als
mathematisch "glatteste" (aber fahrerisch falsche) Loesung fuer die
gegebenen Randwerte.

**Ursache fuer den Fehlschlag, vom Nutzer als Fahrer erklaert:** ein Fahrer
plant nicht Punkt-fuer-Punkt-isoliert, sondern waehlt die Ausfahrt an jedem
Scheitelpunkt im Hinblick auf den naechsten Punkt - bleibt innen, bis die
Querkraft das Oeffnen erzwingt, und der Winkel an Punkt A wird so gewaehlt,
dass er unter Volllast den schnellstmoeglichen Radius zu Punkt B ermoeglicht.
Das ist ein GESCHWINDIGKEITS-Optimierungsproblem, kein reines
Geometrieproblem.

**Versuch 4 (haelt) - gemeinsame querkraftbasierte Relaxation statt
Geometrie:** die bereits vorhandene, querkraftgewichtete Relaxation
(`relaxOnce`/`pinnedOptimize`, `alpha ~ aLatErforderlich/(mu*g)`) implementiert
dieses Prinzip bereits von Haus aus - sie existierte nur getrennt von der
neuen Ketten-Sondermaschinerie. Umbau:
- Jeder Fixpunkt ist jetzt ein WEICHES Ziel (Feder-Analogie: pro aeusserer
  Iteration ein Stueck `SOFT_PIN_PULL=0.3` Richtung Zielwert gezogen statt
  hart erzwungen). Die bestehende "brich ab, wenn Rundenzeit schlechter
  wird"-Regel entscheidet dadurch selbst, wie weit der Zug in Richtung
  Zielwert Zeit kostet - "Punkt darf geringfuegig verfehlt werden, wenn das
  schneller ist" (Nutzervorgabe), ohne Sonderfall fuer nahe Punkte.
- ALLE aktuellen Fixpunkte werden GEMEINSAM relaxiert statt nacheinander mit
  unabhaengigen harten Pins - macht Sonderbehandlung eng benachbarter Punkte
  strukturell unnoetig (vorher: `CHAIN_GAP_M`-Kettenerkennung + automatisches
  Loeschen zu naher Punkte beim erneuten Ziehen - beides jetzt entfernt,
  Nutzer wollte explizit beliebig viele dicht stehende Punkte setzen
  koennen).
- Pro-Fixpunkt-Schalter `hard: true/false` in der Datenstruktur und UI
  (Checkbox "hart" in der Bearbeitungsliste) - hart erzwingt den Punkt exakt
  (fuer eine spaeter geplante Fahrfehler-Analyse: Realaufnahme vs.
  zeitoptimale Linie vergleichen). Bit-genau getestet.
- Bearbeitungsliste zeigt jetzt Ziel- vs. erreichten Wert, wenn sie
  abweichen (`(erreicht X.XXm, +/-Y.YYm)`) - Datengrundlage fuer besagte
  Fahrfehler-Analyse.
- `EDGE_MARGIN_M=0.10`: die Relaxation klemmt nicht mehr hart auf
  [LO,HI], sondern laesst 10cm Puffer zur Korridorgrenze (Nutzervorgabe:
  Innenlinie darf nicht durch die Streckenbegrenzung "eingebeult" werden,
  ein paar cm weiter aussen fuer eine saubere Rundung sind in Ordnung).
  Dabei einen echten Bug gefunden+behoben: die "brich ab bei
  Verschlechterung"-Sicherung (`pinnedOptimize`) konnte auf den
  ungepufferten Rohzustand (direkt aus `applyBrush()`, randscharf) zurueck-
  fallen, wenn schon der allererste Relaxationsversuch schlechter wurde -
  jetzt wird der Puffer VOR dem ersten Sicherungs-Snapshot erzwungen.

**Verifiziert am konkreten Nutzer-Testfall** (5 verkettete Fixpunkte 218-289):
Lenkwinkel-Vorzeichenwechsel bei Punkt 210 (vorher -19.8° mitten in der
Rechtskurve) verschwunden, bleibt durchgehend positiv 18-42° von Punkt 200
bis 224. Rundenzeit mit 20 realen Nutzer-Fixpunkten: 99.15s (schneller als
die reine Basislinie 100.85s, da weiche Ziele der Physik Spielraum lassen,
echte Verbesserungen zu finden statt sie zu erzwingen), 0
Korridorverletzungen, Randabstand ueberall exakt 10cm, Lenkrate 121-128°/s
(unter der 240°/s-Grenze). Basislinie kostet durch den neuen Randabstand
minimal mehr (100.72s -> 100.85s, +0.13s) - das ist der akzeptierte Preis
fuer "nicht an die Wand gepresst".

**EINSCHRAENKUNGEN:**
- `SOFT_PIN_PULL=0.3` und `EDGE_MARGIN_M=0.10` sind erste sinnvolle
  Standardwerte, nicht durchgetestet/kalibriert - bei Bedarf anpassen.
- Der "hart"-Modus pro Punkt ist gebaut und funktionsfaehig, die eigentliche
  Fahrfehler-Analyse (Realfahrt vs. Optimum systematisch vergleichen) ist
  als Anwendungsfall benannt, aber noch nicht als eigenes Feature umgesetzt.
- Lenkrad-Vorzeichenformel bleibt eine grobe Ackermann-Naeherung (Radstand
  2.31m = oeffentliche Herstellerangabe, nicht G184-vermessen) nur fuer die
  Dashboard-Anzeige, keine physikalische Grundlage fuer andere Berechnungen
  im Tool.

## Nachtrag: reales Bremsmodell uebernommen, Bang-Bang-Glaettung fuer beide Pedale, systematisch auf der ganzen Runde verifiziert (08.09.2026)

**Reales Bremsmodell statt Reifenkraftkreis-Theorie:** Nutzerfrage, ob es
schon ein datenbasiertes Bremsmodell gibt - ja,
[scripts/braking_model.py](scripts/braking_model.py)/`brake_event_analysis.py`
(229 reale Bremsvorgaenge aus BFP_PRE_MZ + OBD-Tempoabfall,
`results/braking_model_summary.json`). Auswirkung auf die Rundenzeit per
neuem Skript
[scripts/spreewaldring_braking_model_comparison.py](scripts/spreewaldring_braking_model_comparison.py)
verglichen: theoretisch (Reifenkraftkreis) 100.67s, real gemessener
Median (0.154g) +12.70s, p95 (0.309g) +5.26s, Maximum (0.477g, ABS) +2.07s.
Nutzerentscheidung: das reale Maximum uebernehmen, da in den Logs UND real
auf der Strecke tatsaechlich bis ins ABS gebremst wird - `BRAKE_CAP_G=0.4772`
ist jetzt der Standard-Default in `simulate_lap_combined_friction()` (gilt
nur fuer die Laengsverzoegerung, nicht die Kurven-Querbeschleunigung;
`brake_cap_g=None` erzwingt weiterhin das alte theoretische Verhalten fuer
Vergleiche).

**Bang-Bang-Problem 1 (Vollgas-Inseln): kurze Vollgas-Abschnitte zwischen
zwei Bremszonen.** Nutzerbeobachtung als Fahrer: bei Punktmassen-Loesern
zeitoptimal (Bang-Bang, Standardergebnis der Optimalsteuerungstheorie), aber
real destabilisierend, da ein Fahrer stattdessen mit Teillast exakt an der
Haftgrenze bleibt statt Gas 0 zu geben (Hinterachse wird sonst leicht, RWD).
Fix: `smooth_wasted_accel_brake()`/`smoothWastedAccelBrake()` erkennt
Vollgas-Inseln kuerzer als `MIN_ACCEL_HOLD_M=40m` (grobe Abschaetzung:
Committing-Zeit fuer einen Pedalwechsel, keine gemessene Groesse) und
ersetzt sie durch eine konstante Teillast-Beschleunigung, die exakt auf die
schon vom Rueckwaertspass geforderte Einfahrtsgeschwindigkeit der naechsten
Bremszone einschwenkt - inhaltlich an [[mx5_partial_load_model]]/das
Teillastmodell angelehnt (`accel_envelope()` als Vollgas-Obergrenze).

**Corner-1-Rippel: Wurzelbehebung (Option B) getestet, funktioniert NICHT,
Teillast-Glaettung (Option A) bleibt der richtige Hebel.** Nutzerauftrag:
pruefen, ob sich ein bestehender kleiner Radius-Rippel in Kurve 1
(Array-Nahtstelle des Optimierungsfensters, `window_indices` wickelt durch
Index 0/830) durch direkte Geometrie-Glaettung statt Teillast-Symptombehand-
lung beheben laesst. Drei Ansaetze getestet, alle schlechter oder
wirkungslos: (a) mehr Optimierer-Iterationen - Kurve 1 ist ein stabiles
lokales Optimum, bricht auch mit `n_outer=60` nach 2 Iterationen ab; (b)
globale Minimum-Kruemmungsvariations-Relaxation - macht die Rundenzeit
progressiv schlechter (102.7s->109.3s), da geometrieblind, ohne Griff-
/Geschwindigkeitsbezug; (c) lokal/verankerte Variante derselben Relaxation -
ebenfalls schlechter (103.3s) und erzeugt chaotischere Radiuswerte statt
sauberere. **Fazit, das auch fuer aehnliche Faelle gilt:** ein Werkzeug fuer
"Fixpunkte an ein Ziel heranfuehren" (die Editor-Relaxation) loest nicht
automatisch "geringes Restrauschen in einer bereits optimierten Linie
aufraeumen" - andere Aufgabe, anderes Werkzeug. Option A (Teillast-
Glaettung) bleibt der richtige Hebel fuer diese Symptomklasse.

**Import/Export-Funktion fuer Bearbeitungslisten** (UI in "Bearbeitete
Punkte"): `exportBtn` kopiert die aktuelle Bearbeitungsliste als JSON
("fuer Support"), `importBtn`/`importBox`/`importApplyBtn` laden eine
eingefuegte JSON-Liste ueber eine gemeinsame `validateEditsArray()`-Pruefung
(auch von `loadEditsFromStorage` genutzt). Grund: eine Nutzerbeobachtung
("Bremsung bei 492-493") liess sich zunaechst nicht reproduzieren, weil ich
die reine Basislinie testete statt des tatsaechlichen Bearbeitungsstands des
Nutzers - die Import-Funktion macht den exakten Datenaustausch fuer solche
Faelle jetzt moeglich statt auf Beschreibungen angewiesen zu sein.

**Bang-Bang-Problem 2 (Spiegelfall): kurze, isolierte Bremsinseln zwischen
zwei nicht-bremsenden Abschnitten.** Mit dem importierten 20-Punkte-Nutzer-
Datensatz gefunden: eine einzelne Vollbremsung an Punkt 492 (v: 83.2->83.2->
81.7->82.7 km/h) zwischen zwei frei rollenden Punkten. Ursache in diesem
konkreten Fall: zwei Bearbeitungen (Index 468 und 513, beide 75m Pinselradius,
nur 135m auseinander) ueberlappen sich in der gemeinsamen Relaxation und
hinterlassen genau dort eine kleine "Delle" - anders als Kurve 1 also
bearbeitungsdichte-getrieben, kein fester Streckenfehler. Fix (Spiegelbild zu
Problem 1): `smooth_short_brake_spikes()`/`smoothShortBrakeSpikes()` verlegt
den Bremsbeginn bei kurzen Bremsinseln (< `MIN_ACCEL_HOLD_M`) rueckwaerts in
den vorherigen freien Abschnitt hinein vor, bis mindestens 40m mit
konstanter, sanfterer Verzoegerung erreicht sind - die Zielgeschwindigkeit am
Ende der Insel (harte Randbedingung der naechsten Kurve) bleibt exakt
erhalten, nur der Weg dorthin wird geglaettet. Bremspedal-Anzeige zeigt jetzt
`brakeFrac` als echten Teilbrems-Prozentsatz statt immer 100%.

**Systematisch auf der GANZEN Runde verifiziert, nicht nur am Einzelbeispiel**
(explizite Nutzeranforderung: "es war wieder nur ein Beispiel"). Wichtige
Lektion dabei: das rohe Brems-Flag (`v_bwd < v_fwd`) als Inselkriterium
erzeugt viele FALSCH-POSITIVE - es flackert schon bei ganz normalem,
durchgehend glattem Kurvenradius-Verlauf (z.B. Ein-/Ausgang einer Kurve,
Scheitelpunkt-Uebergang), ohne dass ein echtes Pedal-Problem vorliegt. Die
verlaessliche Methode: echte lokale Extrema in v(s) suchen (Vorzeichenwechsel
der Steigung) und deren Bogenlaenge zum naechsten Extremum pruefen - nur DAS
ist die tatsaechliche Bang-Bang-Signatur. Ergebnis auf der Basislinie UND dem
realen 20-Punkte-Nutzerdatensatz: mehrere echte Fundstellen ueber die ganze
Strecke verteilt (u.a. bei s~250m, 1690m, eine technische Schikane bei
s~2100-2250m mit mehreren dicht aufeinanderfolgenden Scheiteln) - an jeder
greift die Glaettung bereits korrekt mit abgestuften Pedalwerten (8-96%
Bremse, 57% Gas) statt hartem Ein/Aus. Kein unbehandelter harter Spike mehr
gefunden. Punkt 492 selbst: Geschwindigkeit laeuft jetzt glatt 81.8->81.7
(Plateau in den Scheitel) statt der isolierten Bremsspitze.

**Teillast-Prozentzahl war radkraftphysikalisch falsch: 0% Nettobeschleuni-
gung wurde als 0% Gas angezeigt.** Nutzerbeobachtung: "Geschwindigkeit
sauber halten" sollte Teillast > 0% zeigen, nicht 0% - real braucht das
exakte Halten der Geschwindigkeit positive Radkraft, um Luft-/Rollwiderstand
auszugleichen. Ursache: `throttleFrac` wurde bisher als
`a_erforderlich / a_max` (Anteil der NETTO-Beschleunigung) berechnet - bei
a_erforderlich=0 ergibt das faelschlich 0%. Fix: Prozentzahl jetzt als
Anteil der RADKRAFT relativ zum Bereich [`coastAccel()` = Schubabschaltung,
`accel_envelope()` = Vollgas]: `frac = (a_erforderlich - coastAccel) /
(a_max - coastAccel)`. Zusaetzlich akzeptiert `smooth_wasted_accel_brake()`
jetzt auch leicht negative Zielbeschleunigungen bis `coastAccel()` (statt nur
>= 0) - kurze Inseln, die eigentlich sanftes Verzoegern statt Beschleunigen
brauchen, bleiben so am Gaspedal statt unbehandelt zu bleiben. Verifiziert:
alle 18 von der Glaettung erfassten Punkte in der Basislinie zeigen jetzt
echte Teillastwerte zwischen 11% und 57% statt 0%, Rundenzeit praktisch
unveraendert (103.58s -> 103.64s).

**EINSCHRAENKUNGEN:**
- `MIN_ACCEL_HOLD_M=40m` gilt fuer beide Bang-Bang-Richtungen (Gas- und
  Bremsinseln), ist weiterhin eine grobe Abschaetzung, keine gemessene
  Groesse.
- Die Import-Funktion validiert Struktur/Typen, aber nicht, ob die
  importierten Indizes zur aktuell geladenen Streckenversion passen (bei
  einer Streckenaenderung koennten Indizes auf falsche Punkte zeigen).
- Der Corner-1-Rippel selbst besteht als bekanntes, sehr kleines Restrauschen
  weiter (siehe oben) - keine der beiden Bang-Bang-Glaettungen greift dort,
  da es keine kurze Insel im definierten Sinn ist, sondern durchgehend
  eigenstaendige Kruemmung.

## Automatischer Lauf: 2 neue Logs verarbeitet (08.09.2026)

Scheduled Task, unbeaufsichtigt. Neue Logs: `2026-09-08 082050` (~36.5 Min),
`2026-09-08 165347` (~44.0 Min).

**Masse (FLI, SOLO-Annahme, unbestaetigt):** 082050=1187.5kg (FLI
~87.5%->82.8%), 165347=1185.8kg (FLI ~82.8%->77.1%). In
`LOG_MASS_OVERRIDE_KG` (`drivetrain_model_validation.py` UND
`top_speed_validation.py`) ergaenzt.

**Kernzahlen:** 30 neue bestaetigte Kurven (082050: 14, 165347: 16),
Gesamtzahl jetzt 293. UNMAPPED-Kanaele: 0. Keine neuen Kanaltypen (beide
Logs identisches 44-Kanal-Set wie zuletzt). Teillast-RMSE beider Logs
6.5%/6.5% (Gesamt-RMSE unveraendert 6.6pp). Kinematischer Fehler
Median|Fehler|=0.39%/0.42% (gut). 1 neues Volllast-Segment (nur 082050).
Kein neues Gang-6/Top-Speed-Segment. Keines der beiden Logs traegt zu einer
DGM-Luecke bei. Kein neues Ausrollereignis (weiterhin 13 insgesamt).
Lenkwinkelmodell: n=155->185 Kalibrierpunkte, k1=0.023276->0.023382,
R²=0.950->0.945, LOO-RMSE=3.40->3.56°/s (kleine, unauffaellige
Verschiebung).

**Auffaellig 1 - IMU-Resonanz `082050` bei nur 6.93 Hz auf allen 3 Achsen
(statt erwarteten 18-23 Hz):** deutlich abweichend, siehe Details unten.
`165347` dagegen unauffaellig (19.3-21.5 Hz).

**Auffaellig 2 - a_lat_peak/a_lat_mean-Verhaeltnis >2x bei je einer Kurve
pro neuem Log:** `082050` t=1276-1280s (28.7km/h, mean=0.20g/peak=0.47g,
2.35x), `165347` t=2470-2474s (28.1km/h, mean=0.24g/peak=0.59g, 2.46x) -
beide auf niedrigem Niveau, eher Rauschen als Schleudern (Praezedenzfall
siehe oben).

**Auffaellig 3 - Vollast-Verhaeltnis gemessen/Modell jetzt Median=1.01**
(bisheriger Referenzwert 0.94, +7 Prozentpunkte) ueber jetzt 127
Volllast-Segmente (vorher 126, nur 1 neues von `082050`). Der Sprung
stammt also nicht primaer von den beiden neuen Logs, sondern ist erst mit
dem einen zusaetzlichen Segment ueber die 5pp-Meldeschwelle gerutscht -
naeher betrachten/beobachten, ob sich das mit weiteren Logs bestaetigt.

**Achsenrotation:** beide neuen Logs im Achsenpaar X/Y (gleiche Halterung
wie seit 06.09.2026), theta=273.8°/273.7°, R=0.908/0.884 (beide >0.7,
vertical_axis nicht defaulted) - unauffaellig, da nicht das X/Z-Achsenpaar.



Nicht bei null anfangen, sondern auf diesem Stand aufbauen. Das Skript
`vibration_analysis.py` im Ordner ist die aktuelle, funktionsfaehige
Version inkl. Hampel-Filter — keine alte Version daraus rekonstruieren.

`scripts/spreewaldring_racing_line_editor.py` ist nach mehreren verworfenen
Zwischenversionen (siehe oben: Smootherstep-Kette, Catmull-Rom-Spline,
Minimum-Kruemmungs-QP) jetzt bei der geschwindigkeitsbasierten,
gemeinsamen Fixpunkt-Relaxation - NICHT auf einen der geometrischen
Zwischenstaende zurueckfallen, die produzieren nachweislich falsche
Doppelscheitel/Wandkanten.

Das reale Bremsmodell (`BRAKE_CAP_G`) und die Bang-Bang-Glaettung fuer beide
Pedale (`smooth_wasted_accel_brake`/`smoothWastedAccelBrake` fuer Vollgas-
Inseln, `smooth_short_brake_spikes`/`smoothShortBrakeSpikes` fuer den
Spiegelfall bei Bremsinseln) sind jetzt der Standard fuer alle
Rundenzeit-Berechnungen in beiden Skripten - nicht auf die rein
theoretische Reifenkraftkreis-Bremsung oder unbehandelte Bang-Bang-Uebergaenge
zurueckfallen.

## Nachtrag (laufend): "Natuerliches Gasmodell" (experimentell) + Radius-Einzelpunkt-Ausreisser - Zwischenstand (08.09.2026)

**"Natuerliches Gasmodell"** (Toggle "Natuerliches Gasmodell" im Editor,
`naturalThrottleMode`, Default AUS - das produktive zeitoptimale Modell
bleibt unveraendert Standard): APP% aus einer Lenkrate-Formel
(`APP_STEER_A/B`, aus EINER echten Strassenkurve kalibriert, siehe
partial_throttle_calibration.py) statt Vollgas-sobald-moeglich. Fixe in
dieser Reihenfolge gefunden+behoben:
1. Domain-Gatter noetig (`NATURAL_THROTTLE_MIN_G`, jetzt 0.9): Formel
   ausserhalb ihres Kalibrierbereichs (nahezu Geradeausfahrt) angewendet
   ergab wildes Fladdern 0%/100%.
2. Vorausschauendes Fenster (`NATURAL_THROTTLE_LOOKAHEAD_N=10`,
   `..._MAX_G=1.0`): unterhalb der Schwelle bleibt Vollgas, AUSSER die
   naechsten 10 Punkte wuerden bei gleichbleibendem Tempo schon >1g
   verlangen - verhindert lokales Gas-Wegnehmen durch Kruemmungs-
   Vorwaerts-Differenzierung ueber ein zu KURZES Fenster (jetzt
   CURVATURE_WINDOW_M-breit wie computeSteerRateDegS).
3. Motorbremskraft bei APP=0/Kupplung zu/Gang drin (`coastAccelInGear()`,
   Tabelle aus 397 realen "Schub im Gang"-Ereignissen, siehe
   engine_braking_analysis.py) - `coastAccel()` allein war fuer die
   Schaltpause gedacht (Kupplung kurz offen), nicht fuer stetiges Rollen
   im Gang.
4. ECHTER BUG in `smoothShortBrakeSpikes` gefunden+behoben: die
   Rueckwaerts-Verlaengerung einer kurzen Bremsinsel lief blind bis 40m,
   auch wenn das in eine VORANGEHENDE Beschleunigungsrampe zurueckfuehrte
   (Geschwindigkeit faellt beim Rueckwaertsgehen) - verletzte dann
   vStart>vEnd und brach die ganze Glaettung ab, obwohl die Insel selbst
   laengst machbar gewesen waere. Fix: Rueckwaertsverlaengerung bricht ab,
   sobald die Geschwindigkeit dort sinken wuerde. Betrifft BEIDE Modelle
   (die Funktion laeuft unconditional), nicht nur das natuerliche.

**Radius-Einzelpunkt-Ausreisser (NOCH NICHT GELOEST, laufende Arbeit):**
Nutzerbeobachtung (Excel-Export, Punkte 236-238: Radius 107.8->110.8->106.5,
eine einzelne Kuppe mitten in einer sonst durchgehenden Verengung).
Nutzervorgabe: NICHTS an pinnedOptimize()/relaxOnce() selbst aendern,
stattdessen ein separater Nachbearbeitungsschritt danach, der einzelne
Punkte korrigiert, wo der Lenkwinkel/Radius-Verlauf fuer genau einen
Punkt die Trendrichtung umkehrt und direkt danach wieder zurueckkehrt
(`findRadiusNotches()`/`smoothSinglePointNotches()`, eingehaengt am Ende
von `pinnedOptimize()`).

Bisher zwei Anlaeufe, beide unzureichend:
1. nLat-Werte der Nachbarn direkt gemittelt - FALSCH, weil nLat ein
   Versatz entlang einer sich drehenden Projektionsrichtung (PERP[i]) ist,
   keine echte kartesische Distanz - Mittelung dort trifft nicht den
   echten geometrischen Nachbar-Mittelpunkt.
2. Korrigiert auf `relaxOnce()` mit alpha=1 NUR am Ausreisser-Punkt (echte
   kartesische Nachbar-Mittelung), bis zu 8x wiederholt - KONVERGIERT AN
   DIESEM BEISPIEL NICHT. Ursache identifiziert: `curvatureRadius()`
   nutzt ein Fenster von ±w (~5) Punkten, nicht die unmittelbaren
   Nachbarn - radius[237] haengt von Punkt 232 und 242 ab (nicht 236/238),
   waehrend radius[236] von 231/241 und radius[238] von 233/243 abhaengen.
   Diese drei Berechnungen haben KEINEN gemeinsamen Punkt - eine
   Verschiebung von NUR Punkt 237 kann daher radius[237] veraendern, aber
   nicht garantiert "zwischen" radius[236] und radius[238] bringen, weil
   deren Werte gar nicht von Punkt 237 abhaengen. Zusaetzlich stellte sich
   heraus: in diesem Beispiel ist Punkt 232 selbst ein Bearbeitungs-
   Fixpunkt - die Delle bei 237 haengt strukturell an dessen Position als
   Fensterrand-Referenz, nicht an 237 selbst (bestaetigt: Entfernen von
   Edit 232 UND 247 zusammen behebt die Delle komplett, einzeln nur
   teilweise).

**UMGESETZT UND VERIFIZIERT (08.09.2026, Abschluss dieses Nachtrags):**
`smoothSinglePointNotches()` weitet die Korrektur jetzt auf eine
Nachbarschaft von +/-w Punkten um jeden Ausreisser aus (w = dieselbe
Fensterbreite wie curvatureRadius(), per Smootherstep-Kernel wie
applyBrush() - bewaehrtes Werkzeug wiederverwendet statt neu erfunden),
alpha-Werte ueberlappender Ausreisser werden per Max kombiniert, bis zu
8x wiederholt. Verifiziert am gemeldeten Beispiel (Punkte 234-238,
Radius 119.3->112.3->107.8->110.8->106.5): nach dem Fix durchgehend
glatt (117.1->115.4->115.8->119.5->...->119.1->...), keine Kuppe mehr.

**Zusaetzlicher Fund dabei:** ohne Mindest-Winkelschwelle flaggt der
Detektor auch auf GERADEN (Radius im drei- bis vierstelligen Meterbereich)
faelschlich Ausreisser - dort kippt die Radius-SCHAETZUNG schon bei
winzigstem Positionsrauschen relativ stark, obwohl der tatsaechliche
Lenkwinkel < 1 Grad bleibt (kein spuerbarer Sprung). Erste Schwelle
`MIN_NOTCH_STEER_DEG=0.3` eingebaut, aber NOCH NICHT fein genug - laesst
~15 solcher Geraden-Fundstellen noch durch (siehe verbleibende Liste beim
naechsten Test). Nutzer hat zugestimmt, das in einer NEUEN Session
weiterzumachen (Kontext-Grenze war bei 82% erreicht) - beim
Fortsetzen: Schwelle hochsetzen und/oder direkt gegen den absoluten
Radius filtern (z.B. nur Punkte mit Radius < ~300-500m ueberhaupt
pruefen), dann erneut auf der ganzen Strecke verifizieren.

**GELOEST (08.09.2026, Fortsetzung):** zweite Bedingung `MAX_NOTCH_RADIUS_M
= 400` in `findRadiusNotches()` ergaenzt (Punkte mit Radius > 400m werden
gar nicht erst geprueft) - direkter als die Gradschwelle allein, weil sie
strukturell trifft: eine echte Kuppe kann nur INNERHALB einer Kurve liegen,
400m liegt mit Sicherheitsabstand ueber der groessten bekannten echten
Kurve (322m Radius-Aufblaehung, siehe weiter oben in diesem Dokument).
MIN_NOTCH_STEER_DEG=0.3 bleibt als zweite, unabhaengige Bedingung bestehen.

Verifiziert am frischen Baseline-Ergebnis (830 Punkte, volle Physik-
Optimierung via `pinnedOptimize()`, Test-Zugriff auf den Vor-Glaettung-
Zustand ueber neuen `window.__editorDebug.testNotches()`-Hook): vorher
(nur Gradschwelle) 10 Fundstellen, davon 8 auf Geraden mit Radius
882m-18776m (jumpDeg 0.40-2.23deg trotz kaum spuerbarem Lenkwinkel) -
alle 8 durch die Radius-Bedingung sauber entfernt. Die 2 echten
Kurven-Ausreisser (Radius 17.6m/17.8m, jumpDeg 5.6-6.0deg, klar
groesser als das urspruengliche 236-238-Beispiel mit 0.82deg) bleiben
korrekt erkannt. Keine neuen Falschmeldungen auf Geraden, kein
verlorener echter Ausreisser - Nachtrag abgeschlossen.

## Nachtrag (09.09.2026): "Natuerliches Gasmodell" ueberholt "Zeitoptimal" -
Vorausschau-Fix, Formel-Sicherheitsnetz, Linienglaettung Kurve 3

Fortsetzung der Feinabstimmung des experimentellen natuerlichen Gasmodells
(`naturalThrottleMode`, siehe Nachtrag 08.09.2026 oben). Alle Aenderungen
in `scripts/spreewaldring_racing_line_editor.py`, verifiziert am
25-Bearbeitungen-Testdatensatz (Punkte 13/33/120/144/217/232/253/275/297/
314/354/387/432/468/513/547/562/585/613/635/681/703/714/780/807, siehe
`results/spreewaldring_simulierte_runde.xlsx`).

**1. Vorausschau-Bug (Bang-Bang-Chattern, Punkte 172-174):** Nutzerbeobachtung
im Excel-Export - Gas springt 0%/100%/0% ueber 3 aufeinanderfolgende Punkte.
Ursache: die Vorausschau-Pruefung ("wird die Kurve in den naechsten 10
Punkten zu eng?") nahm KONSTANTE Geschwindigkeit an, obwohl der Wagen durch
Luft-/Rollwiderstand + Motorbremse (`coastAccelInGear`) beim Ausrollen
spuerbar abbaut. An einer Kurve, deren Scheitel-Querbeschleunigung
hauchduenn (0.996g vs. 1.002g, nur 0.2 km/h Unterschied!) um die
1.0g-Schwelle pendelte, kippte die binaere Entscheidung von Punkt zu Punkt.
Fix: die Vorausschau simuliert das Ausrollen jetzt vor (pessimistischstes
Szenario: appFrac=0 ab hier), statt v konstant zu halten - eliminiert das
Chattern lap-weit (0 von 830 Punkten geflaggt bei einem Scan auf
>50-Prozentpunkt-Spruenge zu BEIDEN Nachbarn).

**2. Formel-Sicherheitsnetz (Punkte 194-198, 240-242, Kurve 3):**
Nutzer-Realitaetscheck (~30 reale Runden auf dieser Strecke, ohne Logs):
"zwischen 194 und 300 fahre ich mit dieser Linie alles voll, Bremspunkt fuer
Kurve 4 ist das Ende der Vollgasphase". Das Modell zeigte dort aber Teillast
(30-80%), obwohl bei Vollgas nachweislich (im echten Solver gegengerechnet)
nirgends mehr als 0.948g anlagen - weit unter der 1.0g-Grenze. Ursache: die
Teillast-Formel (`APP_STEER_A/B`, aus NUR EINER anderen echten Kurve
kalibriert, R^2=0.76) ist ein Fahrgefuehl-Proxy, kein direkter
Physik-Check, und kann konservativer sein als noetig. Fix: derselbe
Ausroll-Vorausschau-Mechanismus wie oben sucht jetzt per Bisektion die
MAXIMALE appFrac, bei der die 1g-Grenze im Vorausschaufenster nirgends
gerissen wird, und nimmt das Maximum aus Formel-Wert und diesem sicheren
Wert (`Math.max`) - kann appFrac dadurch NUR erhoehen, nie absenken, macht
also KONSTRUKTIV keine Stelle im Lap langsamer als vorher. (Erste Version
war ein harter 0/1-Schalter statt einer Bisektion - erzeugte selbst wieder
Bang-Bang-Chattern an der Kippschwelle, Fund bei Punkt 757-759, dann per
Bisektion behoben.)

**3. Linienglaettung Kurve 3:** Nutzer korrigierte 4 der 25 Bearbeitungen
(217, 232, 275 neu bewertet, 247 entfernt, 253 neu) direkt im Editor - der
Radius-Verlauf war zuvor eine wellige Zickzack-Form (mehrere kleine
Auf-und-Ab-Bewegungen zwischen 115m und 157m), danach ein glatter
Doppelbogen (ein Bogen weitet sich sauber monoton auf 293m, der zweite
schliesst sauber auf 119-121m). Allein dadurch: Zeitoptimal 101.173s ->
101.065s (-108ms).

**Gesamtergebnis natuerliches Gasmodell:** 101.328s (Ausgangspunkt dieses
Nachtrags) -> 100.846s, also **-482ms**, bei durchgehend exakt eingehaltener
1g-Grenze (verifiziert per Scan ueber alle 830 Punkte).

**Nebenfund (kein Bug, separater Befund):** beim Vergleich Zeitoptimal
vs. natuerliches Modell auf der IDENTISCHEN Linie fiel auf, dass das
natuerliche Modell in einem Abschnitt (Punkte 800-826, Ausgang einer
Haarnadel) SCHNELLER ist als Zeitoptimal, obwohl Zeitoptimal per Definition
"Vollgas sobald physikalisch moeglich" ist. Neuer Debug-Export
`getSim().gearInternal` (der TATSAECHLICH intern benutzte Gang, nicht der
fuer die Anzeige separat aus vFinal(s) rekonstruierte, siehe
`deriveGearTrace()`-Kommentar) zeigte die Ursache: waehrend der
Bremszone/dem Scheitel ist die Geschwindigkeit bei beiden Modellen
BIT-IDENTISCH (Reifen, nicht Motor, ist der limitierende Faktor - der Gang
spielt keine Rolle). Zeitoptimal war aber schon VOR der Bremszone (bei
Vollgas Richtung Haarnadel) in Gang 3 hochgeschaltet, das natuerliche
Modell blieb wegen seines fruehen Lifts in Gang 2. Sobald sich die Kurve
nach dem Scheitel wieder oeffnet und der MOTOR statt der Reifen limitiert,
liefert Gang 2 mehr Vortrieb - und BEIDE Modelle haben KEINE
Rueckschalt-Logik (nur Hochschalten, siehe `if (gear < 6){...}` in
`naturalStep()`/`forwardStepShifted()`), Zeitoptimal bleibt fuer den Rest
der Haarnadel im (hier suboptimalen) Gang 3 gefangen. Zeigt: "Zeitoptimal"
ist trotz des Namens keine echte obere Schranke, sondern eine gierige
Heuristik ohne vorausschauende Schaltstrategie - ein natuerliches Kandidat
fuer eine spaetere Rueckschalt-Logik in BEIDEN Modellen, unabhaengig von der
Gas-Modell-Arbeit oben.

**Einordnung durch den Nutzer (wichtig fuers Verstaendnis, kein
Widerspruch):** "Zeitoptimal" war von Anfang an nur eine erste Naeherung
und in der Realitaet so nicht fahrbar (kein Fahrer schafft eine perfekt
gierige Vollgas-Strategie ohne Ruecksicht auf Anschlussfaehigkeit) - es
diente bisher als das zu schlagende Referenzmodell. Mit dem Nutzer als
Fahrer (reale Streckenerfahrung als Kalibrierungsquelle) und Claude als
Dateningenieur (Modell-Diagnose/-Fixes) wurde dieses Referenzmodell jetzt
ueberholt - laut Nutzer erwartbar und kein Fehlerzeichen.

## Automatischer Lauf: 3 neue Logs verarbeitet (09.09.2026)

Scheduled Task, unbeaufsichtigt. Neue Logs: `2026-09-09 081840` (~36.4 Min),
`2026-09-09 170146` (~36.0 Min), `2026-09-09 174113` (~3.8 Min).

**Masse (FLI, SOLO-Annahme, unbestaetigt):** 081840=1183.5kg (FLI
~76.2%->70.1%), 170146=1183.8kg (FLI ~71.9%->76.2%, Rohkanal in den
letzten 10 Samples auffaellig verrauscht, Streuung 38-90%), 174113=1180.9kg
(FLI ~65.2%->65.2%, nur 3.8 Min Fahrzeit). In `LOG_MASS_OVERRIDE_KG`
(`drivetrain_model_validation.py` UND `top_speed_validation.py`) ergaenzt.

**Kernzahlen:** 27 neue bestaetigte Kurven (081840: 11, 170146: 15,
174113: 5), Gesamtzahl jetzt 324. UNMAPPED-Kanaele: 0. Keine neuen
Kanaltypen (alle drei identisches 44-Kanal-Set wie zuletzt). Teillast-RMSE
081840=6.5%/170146=8.3%/174113=5.5% (Gesamt-RMSE unveraendert 6.6pp).
Kinematischer Fehler Median|Fehler|=0.43%/0.42%/0.92% (gut, 174113 mit
n=149 kleine Stichprobe). 17 neue Volllast-Segmente (081840: 5, 170146:
12, 174113: 0), Gesamtzahl jetzt 144, Verhaeltnis gemessen/Modell weiter
Median=1.01 (siehe Auffaelligkeit unten). 6 neue Gang-6/Top-Speed-Segmente
(081840: 3, 170146: 3, 174113: 0). Keines der drei Logs traegt zu einer
DGM-Luecke bei. Kein neues Ausrollereignis (weiterhin 13 insgesamt).
Lenkwinkelmodell: n=185->216 Kalibrierpunkte, k1=0.023382->0.023179,
R²=0.945->0.948, LOO-RMSE=3.56->3.46°/s (kleine, unauffaellige
Verschiebung, kein neuer Ausreisser unter den groessten LOO-Fehlern).

**Auffaellig 1 - IMU-Resonanz bei allen drei neuen Logs uneinheitlich pro
Achse statt dem sonst ueblichen gemeinsamen 18-23 Hz-Peak auf allen 3
Achsen:** `081840` X=8.59 Hz / Y=22.36 Hz / Z=15.23 Hz, `170146` X=20.07 Hz
/ Y=20.02 Hz / Z=16.60 Hz, `174113` X=19.29 Hz / Y=13.67 Hz / Z=13.28 Hz.
Bisher zeigte die Halterungsresonanz meist alle drei Achsen im selben
18-23 Hz-Band (Ausnahme 08.09. `082050` mit 6.93 Hz auf allen 3 Achsen
einheitlich). Hier dagegen driften die Achsen unterschiedlich weit
auseinander (besonders `081840` X mit nur 8.59 Hz und `174113` Y/Z mit
13.3-13.7 Hz) - moeglicher Hinweis auf eine lockerere/andere Halterung an
diesem Tag, aber keine eindeutige Diagnose moeglich ohne weitere Logs zum
Vergleich.

**Auffaellig 2 - KORRIGIERT (09.09.2026, Nutzer-Nachfrage): war KEIN
Ausreisser-Sample, sondern ein echtes, doppelt bestaetigtes S-Manoever
mit einem echten, user-bestaetigten Leistungsuebersteuern + ESP-Fang.**
Die urspruengliche Einordnung oben (naiver Mittelwert ueber das ganze,
von `MAX_GAP_S=2.5s` zusammengefuehrte Ereignisfenster) war irrefuehrend:
Mittelwert und Vorzeichen kippen nur, weil das Fenster eine Rechts- UND
eine Linkskurve enthaelt, die sich im Mittel aufheben - Sample-fuer-Sample
(GPS-Kurs UND Gyro stimmen ueberein) sind beide Teilkurven echt.

Ort: `trunk_link`-Autobahnverbindungsrampe (Overpass-Abfrage: Ways
4941901/4941902/1160481466, `motorroad=yes`, Richtung
Oranienburg/Velten/Leegebruch, ~52.738/13.2096) - die OSM-Wegpunkte
reproduzieren unabhaengig von den Sensordaten exakt dieselbe
Rechts-dann-Links-S-Form. Drei unabhaengige Quellen (Strassennetz-
Geometrie, GPS-Kurs, Gyroskop) stimmen ueberein.

**Rechtskurve (t=2000.6-2005.8s, 101-117km/h) - vom Nutzer bestaetigt als
echtes Leistungsuebersteuern mit ESP-Eingriff, Chronologie sample-genau
gegen die Rohdaten verifiziert:**
1. t=2003.75s: `ETC_ACT` (Drosselklappe) springt auf 86.2% - zu viel Gas.
2. t=2003.69s: rohes (ungefiltertes) Gyro-a_lat (1.14g) UEBERSTEIGT das
   Lenkwinkel-Modell (1.09g, kein Schlupf angenommen) - das Heck dreht
   real mehr, als der (zu diesem Zeitpunkt schon zurueckgenommene)
   Lenkwinkel erklaert = die eigentliche Uebersteuer-Signatur (Gegenteil
   der sonst dokumentierten Untersteuer-Faelle).
3. `ETC_ACT` faellt auf 33.5% (t=2004.26s) - ESP regelt ein (Kontrollprobe:
   in einem 22.5s durchgehenden Volllast-Geradeausstueck im selben Log
   liegt `ETC_ACT` stabil bei 86.15%±0.03%, also kein Messrauschen).
4. t=2004.09s: Gyro-a_lat bricht auf 0.37g ein, waehrend das Modell (Basis:
   der noch recht offene Lenkwinkel -20.1°) 0.79g erwarten wuerde - die
   Hinterachse hat schneller wieder Grip bekommen, als der Fahrer die
   Lenkung (die er zur Korrektur der Rotation bereits aufgemacht hatte,
   -38° -> -18.8° zwischen t=2003.1-2004.0s) zurueckgenommen hat.
5. Lenkwinkel geht danach wieder zu (-18.8° -> -27.5°, t=2004.0-2004.5s) -
   Fahrer steuert zurueck auf Kurvenradius.
Dritter user-bestaetigter ESP-/Schlupf-Fall im Projekt nach dem
2.47g-A111-Ereignis und dem `145116`-Fall, siehe [[mx5_steering_angle]]
fuer die volle Herleitung inkl. Methodik-Lektionen (Mittelwert ueber ein
gap-gebruecktes Ereignis kann ein echtes Kurz-Uebersteuern verschlucken;
`ActualEnginePercentTorque` blieb hier glatt, nur `ETC_ACT` zeigte den
Eingriff; `RotationRateZ` hat nur ~4.94Hz native Rate mit ~5% exakten
Duplikat-Werten - Feinstruktur <1s beim rohen Gyro-Wert vorsichtig
behandeln).

Linkskurve (t=2005.8-2010.6s, 88-105km/h, Gegenrichtung derselben
S-Rampe) wurde nicht in derselben Tiefe untersucht - kein Grund zur
Annahme, dass sie kein echtes Kurvenfahren war (S-Form OSM-bestaetigt),
aber kein Uebersteuer-Nachweis wie rechts erhoben.

Das separate, raeumlich UNABHAENGIGE Ereignis bei t=2138.7-2145.7s
(v=25.7km/h, andere Location ~52.7468/13.189, naeher an einer
tertiary/secondary-Kreuzung laut OSM, aber nur mit ~100-130m Abstand -
Abdeckung dort lueckenhaft) bleibt unkorrigiert von dieser Analyse
unberuehrt - vermutlich ebenfalls ein echtes S-Manoever (gleiches
Vorzeichen-Kipp-Muster durch dieselbe `MAX_GAP_S`-Zusammenfuehrung), aber
nicht mit derselben Tiefe/OSM-Bestaetigung geprueft.

**Achsenrotation:** alle drei neuen Logs im Achsenpaar X/Y (gleiche
Halterung wie seit 06.09.2026), theta=274.2°/272.8°/300.3°, R=0.931/
0.821/0.925 (alle >0.7, vertical_axis nicht defaulted) - unauffaellig, da
nicht das X/Z-Achsenpaar. `174113` mit nur 6 Bremsereignissen (kurzes Log)
und theta=300.3° etwas abseits der uebrigen X/Y-Logs (267-281°), aber
R=0.925 spricht gegen einen Erkennungsfehler, eher Stichprobengroesse.

## Nachtrag (09.09.2026, Fortsetzung): Rueckschalt-Logik nachgeruestet -
1.58s/1.21s schneller, zwei echte Bugs beim Bauen gefunden

Fortsetzung des Nachtrags weiter oben (natuerliches Gasmodell). Nutzer
hinterfragte die Aussage "beide Modelle koennen nur hochschalten" anhand
der Excel-Gang-Spalte ("bei mir stimmt die Anzeige") - zu Recht: die
ANZEIGE-Gangspur (`sim.gear`, `deriveGearTrace()`) wird bewusst separat aus
dem fertigen Geschwindigkeitsverlauf rekonstruiert und zeigt schon lange
plausible Rueckschaltungen. Das eigentliche Problem sitzt eine Ebene
tiefer: die INTERNE, tatsaechlich fuer die Beschleunigungsberechnung
genutzte Gangvariable (`gearO`/neu freigelegt als `getSim().gearInternal`)
schaltet nachweislich (0 von 830 Punkten) nie zurueck - Beleg per direktem
Vergleich beider Spuren am selben Datensatz. An den 6 Stellen, wo die
Anzeige plausibel zurueckschaltet, rechnete das Modell weiterhin mit dem zu
hohen Gang (teils 2 Gaenge daneben, z.B. Anzeige Gang 2 vs. intern Gang 4
bei 94 km/h) - das kostet an der Stelle nachweislich bis zu 62% der
moeglichen Beschleunigung (`accel()` direkt nachgerechnet).

**Fix in `scripts/spreewaldring_racing_line_editor.py`** (`forwardStepShifted()`
und `naturalStep()`, beide spiegelbildlich): bidirektionale Gangwahl analog
`deriveGearTrace()` (dieselbe `GEAR_HYSTERESIS`/`REDLINE_DOWNSHIFT_MARGIN`,
nur Nachbargaenge, echte Schaltzeit aus `ATTACK_SHIFT_S` statt
instantanem Wechsel wie bei der Anzeige). Der Schalt-Zustand
(`shiftRemaining`) musste um `shiftTarget` erweitert werden, weil die
Coast-waehrend-des-Schaltens-Rekursion bisher hart `gear+1` annahm - fuer
Rueckschaltungen muss das Ziel variabel sein.

**Zwei echte Bugs beim ersten Anlauf gefunden, nicht nur die fehlende
Funktion selbst:**
1. Erste Version verglich die Kandidaten-Gaenge mit der bereits
   grip-gedeckelten Beschleunigung (`Math.min(accel(v,gear), aLongAvail)`,
   copy-paste aus der bestehenden Hochschalt-Logik). Mitten in einer Kurve
   ist `aLongAvail` aber fuer JEDEN Gang gleich klein - dadurch sehen alle
   Kandidaten gleich "schlecht" aus und es wurde nie zurueckgeschaltet,
   ausgerechnet dort wo es fuer den Kurvenausgang am meisten zaehlt (0
   Aenderung trotz Fix, per Test bestaetigt). Fix: Vergleich mit der ROHEN
   Motorbeschleunigung (wie `deriveGearTrace()` es schon immer macht) - die
   Grip-Kappung greift erst danach bei der tatsaechlich genutzten
   Beschleunigung, nicht bei der Gangwahl selbst.
2. Nach Fix 1 tauchten 7 neue "Chatter"-Ausreisser im natuerlichen
   Gasmodell auf (0%/100%/0%-Spruenge). Ursache: `appFracNaturalO[iTarget]`
   wird nur in der finalen "kein Schaltvorgang"-Verzweigung von
   `naturalStep()` gesetzt - bei einem Schaltvorgang wird diese Zeile
   uebersprungen, das Array behaelt seinen `.fill(1)`-Anfangswert (100%) an
   GENAU diesem einen Punkt, ein Phantomwert ohne jeden Bezug zur echten
   Physik. Fix: `appFracNaturalO[iTarget] = 0` explizit an beiden
   Schalt-Ausloese-Stellen setzen (Kupplung offen waehrend des
   Schaltvorgangs = keine Kraftuebertragung, physikalisch korrekt). Nach
   dem Fix liegen alle verbleibenden 0%-Einzelpunkte exakt auf einem
   Gangwechsel (verifiziert an allen 7 Fundstellen) - kein Bug mehr,
   sondern ein realistisches Detail (kurzer Kraftunterbruch beim Schalten,
   jetzt sichtbar in der Gas-Spalte).

**Ergebnis** (25-Bearbeitungen-Testdatensatz, s. `results/
spreewaldring_simulierte_runde.xlsx`): Zeitoptimal 100.771s -> **99.194s**
(-1.58s), natuerliches Gasmodell 100.547s -> **99.337s** (-1.21s). Sicherheit
gegengeprueft: 1g-Grenze weiterhin exakt eingehalten (`maxALat` = 1.0 in
beiden Modellen), Lenkgeschwindigkeit weiterhin unter der 240°/s-Grenze
(max. 95°/s). Anzeige-vs-intern-Abweichungen von 630 auf 114 gesunken, davon
90 in Bremszonen (dort ist der Gang physikalisch irrelevant, siehe oben -
kein Fix noetig) und 24 im kurzen, erwartbaren Uebergangsfenster am
Schaltpunkt selbst (Anzeige schaltet instantan, die interne Physik braucht
die echte Schaltzeit aus `ATTACK_SHIFT_S`) - keine der verbleibenden
Abweichungen zeigt noch das urspruengliche Muster (mehrere Gaenge daneben
ueber viele Punkte hinweg).

**Offen:** der Python-Port (`scripts/spreewaldring_racing_line_search.py`,
siehe vorheriger Nachtrag "wirst du schneller als ich") hat diese
Rueckschalt-Logik NICHT nachgezogen - bei einer erneuten Suche muesste der
Port zuerst wieder gegen eine frische JS-Referenzzahl validiert werden,
bevor man sich auf die Ergebnisse verlassen kann.

## Automatischer Lauf: 2 neue Logs verarbeitet (10.09.2026)

Scheduled Task, unbeaufsichtigt. Neue Logs: `2026-09-10 070750` (~31.0 Min),
`2026-09-10 165628` (~40.2 Min).

**Masse (FLI, SOLO-Annahme, unbestaetigt):** 070750=1179.3kg (FLI
~65.2%->56.1%), 165628=1178.0kg (FLI ~58.2%->55.3%, Endwerte im Rohkanal
stark verrauscht 43.8-76.2%, Median-Methode noetig). In
`LOG_MASS_OVERRIDE_KG` (`drivetrain_model_validation.py` UND
`top_speed_validation.py`) ergaenzt.

**IMU-Resonanz uneinheitlich pro Achse** (wie schon bei den drei Logs vom
09.09.): `070750` X=7.32Hz / Y=6.10Hz / Z=18.95Hz, `165628` X=6.69Hz /
Y=21.39Hz / Z=16.31Hz - beide deutlich ausserhalb des sonst ueblichen
gemeinsamen 18-23Hz-Bands auf allen Achsen, setzt das Muster vom 09.09.
fort (moegliche lockerere/wechselnde Halterung).

**Kernzahlen:** 30 neue bestaetigte Kurven (070750: 9, 165628: 21),
Gesamtzahl jetzt 354. UNMAPPED-Kanaele: 0. Keine neuen Kanaltypen (beide
identisches 44-Kanal-Set wie zuletzt). Teillast-RMSE 070750=6.5%/
165628=6.7% (Gesamt-RMSE unveraendert 6.6pp). Kinematischer Fehler
Median|Fehler|=0.39%/0.41% (gut). 9 neue Volllast-Segmente (070750: 2,
165628: 7), Gesamt-Verhaeltnis gemessen/Modell weiter Median=1.01. 2 neue
Gang-6/Top-Speed-Segmente (beide aus 070750). Keines der zwei Logs traegt
zu einer DGM-Luecke bei. Achsenrotation beide im X/Y-Achsenpaar (gleiche
Halterung wie seit 06.09.2026), theta=287.1°/277.5°, R=0.787/0.861 (beide
>0.7, vertical_axis nicht defaulted) - unauffaellig.

**Auffaellig 1 - neues Ausrollereignis (14. insgesamt, vorher 13):**
`2026-09-10 070750`, t=1215.3-1237.9s (22.6s), v=102->75 km/h,
Masse=1179.3kg. Gefittet CdA=0.636 m² / Crr=0.0213 (RMSE=0.29 km/h) -
liegt nahe am Referenzwert CdA=0.647 m² (Crr dort 0.0130, aus dem
etablierten eta-unabhaengigen Referenzmodell). Windkorrektur (12.0 km/h
aus 272°, Rueckenwind-Komponente +11.2 km/h) verschlechtert hier die
Uebereinstimmung mit der Referenz (RMSE mit Wind=3.32 km/h vs. ohne
Windkorrektur=1.33 km/h) - deckt sich mit der bekannten Einschraenkung,
dass die Windkorrektur bei kurzen Ereignissen (hier 22.6s) unzuverlaessig
ist, siehe [[mx5_coastdown_analysis]]. Kein Widerspruch zum Modell, nur ein
Hinweis, dass fuer dieses Ereignis der windkorrigierte Referenzwert nicht
das beste Vergleichsmass ist.

**Auffaellig 2 - Lenkwinkelmodell R² leicht gesunken:** n=216->246,
k1=0.023179->0.023048 (k2 nahezu unveraendert, -0.0000370), R²=0.948->0.938,
LOO-RMSE=3.46->3.77°/s - kleiner, aber spuerbarer Ruecksetzer (kein
GPS-Bug-Ausmass wie am 01.09., aber pruefwuerdig). Groesster LOO-Ausreisser:
`2026-09-10 165628` t=1563-1564s, links, v=74 km/h, Lenkwinkel nur +3.7°,
gemessene Gierrate -20.5°/s, LOO-Fehler +18.75°/s. Gegenprobe im
Kurvenkatalog (Schritt 5) zeigt fuer genau dieses 1.0s-Fenster a_lat
mean=-0.03g / peak=-0.04g - praktisch keine messbare Querbeschleunigung
trotz der behaupteten hohen Gierrate. Das ist die bekannte Signatur eines
kurzen rohen Gyro-Ausreissers (<1s, RotationRateZ hat nur ~4.94Hz native
Rate), keine echte Lenkbewegung, siehe [[mx5_steering_angle]]. 7 der
groessten LOO-Ausreisser stammen aus `165628` (u.a. auch ein echter,
plausibler Fall: t=2132-2134s, rechts, v=110km/h, a_lat mean=+0.82g/
peak=+0.89g, LOO-Fehler +14.16°/s - hier duerfte eher die Saettigung des
k(Winkel)-Modells bei hoher Querbeschleunigung die Ursache sein). Die
Konzentration mehrerer grosser Residuen in einem einzelnen, dynamischeren
Log erklaert den Grossteil der R²/LOO-Verschiebung; kein neuer
systematischer Fehler wie beim GPS-Bug erkennbar.

## Google-Drive-Download ueber rclone statt MCP-Tool (11.09.2026)

Der Google-Drive-MCP-Connector (`download_file_content`) bricht bei
Dateien >10MB ab (base64-Antwort ueber eine einzelne Tool-Antwort) - die
.dlg-Logs sind aber 45-65MB, daher bisher manueller Download noetig.
Loesung: `rclone` (lokal installiert, `~/.local/bin/rclone`, kein sudo
noetig) mit Remote "gdrive" laedt direkt per Drive-API in Chunks, keine
Groessenbeschraenkung - per Checksumme gegen bereits lokal vorhandene
Dateien verifiziert (bit-identisch).

Auth-Ansatz GEAENDERT waehrend der Einrichtung: zuerst per OAuth-Nutzer-
Token versucht (`rclone authorize "drive"`), aber die geteilte rclone-
Standard-Client-ID wird laut Warnung 2026 abgeschaltet - eigene Google-
Cloud-OAuth-Client-ID haette aber eine volle App-Verifizierung durch
Google gebraucht (Logo-Upload + Veroeffentlichung erzwingt Review,
Wochen-Wartezeit, fuer ein Ein-Personen-Skript unverhaeltnismaessig).
STATTDESSEN: Google-Cloud-**Dienstkonto** (Service Account) angelegt,
JSON-Key liegt in `/home/manuel/claude/schlüssel/` (chmod 600, nicht Teil
des Git-Repos - siehe .gitignore). Der Drive-Ordner "Loggs" wurde dem
Dienstkonto direkt als Betrachter freigegeben (kein OAuth-Consent-Flow,
kein Token-Ablauf, keine Nutzerinteraktion mehr noetig - dauerhaft stabil).
rclone-Config (`~/.config/rclone/rclone.conf`, Remote "gdrive"):
`service_account_file` zeigt auf den Key, zusaetzlich ein `root_folder_id`
gesetzt, weil ein Dienstkonto fuer es freigegebene (nicht selbst besessene)
Ordner KEINE Namenssuche im eigenen (leeren) Drive-Stamm machen kann - der
Ordnerinhalt haengt dadurch direkt am Remote-Stamm (`gdrive:`, OHNE
"Loggs" im Pfad).
Zunaechst eingebaut in die separate Aufgabe `check-drive-for-new-dlg-logs`
(SKILL.md dort aktualisiert: Schritt 1 `rclone lsf "gdrive:" --files-only`,
Schritt 4a `rclone copy "gdrive:" "data/raw" --include ...` statt
MCP-Tools). settings.local.json um die beiden rclone-Befehlsformen
ergaenzt.

NACHTRAG (11.09.2026, noch am selben Tag): beim Pruefen festgestellt,
dass `check-drive-for-new-dlg-logs` NIE aktiv geplant war (die vom
29.08. dokumentierte Einrichtung - siehe oben, "automatische
Google-Drive-Ueberwachung eingerichtet" - fehlte in der tatsaechlichen
Scheduled-Tasks-Konfiguration, vermutlich weil der offene Punkt von
damals, einmal manuell "Jetzt ausfuehren" anzustossen, nie erledigt
wurde). Auf Nutzerwunsch daher NICHT als eigene Aufgabe reaktiviert,
sondern der Drive-Download als neuer **Schritt 0** in
`process-new-mx5-logs` integriert (die taeglich 18:00 Uhr laeuft) - EINE
Aufgabe statt zwei, Schritt 1 dort profitiert direkt von den frisch
heruntergeladenen Dateien in data/raw/. Der Ordner
`.claude/scheduled-tasks/check-drive-for-new-dlg-logs/` wurde geloescht.

## Deterministische Pipeline gebaut und end-to-end verifiziert (11.09.2026)

Umgesetzt gemaess Plan `/home/manuel/.claude/plans/recursive-conjuring-minsky.md`:
`data/log_mass_overrides.json` (ersetzt die beiden inline
LOG_MASS_OVERRIDE_KG-Dicts), `build_datalake.py`/
`check_dgm_coverage_gaps.py` schreiben jetzt zusaetzlich strukturierte
JSON-Summaries, `scripts/pipeline_checks.py` (Schwellwert-Findings,
9 Selbsttests gruen) und `scripts/run_daily_pipeline.py` (Orchestrator:
Drive-Download -> neue Logs -> Masse -> alle Analysen -> Findings ->
Report unter `data/runs/<datum>/`) - alles ohne LLM-Beteiligung.
End-to-end mit einem real reprozessierten Log getestet: Masse und
Lenkwinkel-Modell (k1/R²) reproduzierten exakt die vorher bekannten,
manuell verifizierten Werte; brake/corner/coastdown/datalake-Outputs
byte-identisch zur Baseline.

NEBENBEFUND (nicht Teil dieses Umbaus, separat vermerkt): beim
Verifizieren aufgefallen, dass `drivetrain_model_validation.py` und
`partial_load_model.py` bereits VOR diesem Umbau nicht-deterministisch
waren (zwei direkt aufeinanderfolgende, unveraenderte Laeufe liefern
unterschiedliche Segment-/Punktzahlen, betrifft u.a. das ohnehin als
Sonderfall bekannte Log `2026-08-25 081538` mit kaputten Gang-Kanaelen) -
vermutlich fehlende Sortierung einer DuckDB-Abfrage. Als eigene Aufgabe
ausgelagert, nicht Teil dieser Pipeline-Aenderung.

## Automatischer Lauf: 2 neue Logs verarbeitet (2026-09-11)

- `2026-09-11 140908`, Dauer=9min, Masse=1176.3kg (FLI ~52.3%->~50.8%, automatisch berechnet (SOLO-Annahme))
- `2026-09-11 142830`, Dauer=8min, Masse=1175.6kg (FLI ~54.3%->~44.5%, automatisch berechnet (SOLO-Annahme))

Auffaelligkeiten:
- vibration: 2026-09-11 140908: Resonanz auf AccelerationX bei 6.5 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-11 140908: Resonanz auf AccelerationY bei 5.8 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-11 140908: Resonanz auf AccelerationZ bei 5.8 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-11 142830: Resonanz auf AccelerationY bei 5.2 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-11 142830: Resonanz auf AccelerationZ bei 16.7 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- brake_axis: 2026-09-11 142830: Konzentrationsmass R=0.69 < 0.7 bei der Achsenrotation.
- corner_event: 2026-09-11 140908 t=450-457s: a_lat_peak/mean-Verhaeltnis 3.0 > 2.0 (peak=-0.35g, mean=-0.12g) - moegliches Schleudern/Uebersteuern.
- corner_event: 2026-09-11 140908 t=211-216s: a_lat_peak/mean-Verhaeltnis 2.2 > 2.0 (peak=+0.32g, mean=+0.14g) - moegliches Schleudern/Uebersteuern.
- corner_event: 2026-09-11 142830 t=439-442s: a_lat_peak/mean-Verhaeltnis 2.8 > 2.0 (peak=-1.10g, mean=-0.40g) - moegliches Schleudern/Uebersteuern.
- drivetrain_ratio: Median-Verhaeltnis gemessen/Modell der neuen Logs 0.86 weicht um mehr als 0.05 vom Referenzwert 0.94 ab (n=2).

## Neuer paralleler Projektstrang: CAN-Bus-Logging + Pi-Touchdisplay (2026-09-11/12)

Separat vom OBD/GPS-basierten Fahrleistungsmodell (oben) laeuft seit 2026-09-11 ein zweiter
Strang: rohes CAN-Bus-Logging ueber einen USB-CAN-Adapter + Raspberry Pi im Auto, Ziel
hoeher aufgeloeste Rohdaten als per OBD-Fusion-Polling (u.a. fuer die geplante
Querdynamik/Kurvenmodell-Erweiterung). Eigener, ausfuehrlicher Projektstand dafuer in
**`mx5_can_bus_status.md`** (separates Dokument im Projekt-Wurzelverzeichnis, nicht Teil
dieser Datei) - dort dokumentiert:

- Hardware/Verkabelung des CAN-Adapters, Pi-Infrastruktur (KeyState-getriggertes Logging,
  Overlayroot-Absicherung gegen Stromausfall, USB-Stick als einziger persistenter Mount)
- DBC-Reverse-Engineering der ersten echten Fahrt (neue/bestaetigte Signale, siehe
  `data/can/CAN_unbekannte_signale_bericht_2026-09-11.md` und die erweiterte DBC
  `data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc`)
- Touchdisplay-GUI (`scripts/status_gui.py`) mit Testmodus-Checkliste, Live-CAN-Werte-Anzeige
  und grafischen Pedal-/Lenkwinkel-/Speed-Gauges, inkl. SSH-Zugriffs-/Deployment-Referenz fuer
  den Pi und Lasttest-Ergebnissen
- Testplan fuer gezielte Verifikationsfahrten: `data/can/test_plan_2026-09-12.md`

Beruehrungspunkt zum Fahrleistungsmodell: perspektivisch sollen verifizierte CAN-Signale
(Drehzahl, Gas, Bremse, Lenkwinkel, Kupplung, Radgeschwindigkeiten) die OBD-Kanaele fuer die
Querdynamik/Kurvenmodell-Erweiterung ergaenzen oder ersetzen - noch nicht begonnen, aktuell
volle Konzentration auf CAN-Signal-Verifikation.

## steering_lateral_model.py: Trace-Plots nur noch fuer neue Logs (12.09.2026)

Nutzer bemerkte, dass der automatische Lauf taeglich ALLE
`steering_lateral_*_trace.png` (eines pro Log mit Lenkwinkel-Kanal, 24
Stueck) neu erzeugt hat, nicht nur fuer die neuen Logs - dieselbe
"laeuft ueber ALLE statt nur neue Logs"-Kategorie wie letzte Woche bei
brake/corner/coastdown, hier bei `steering_lateral_model.py` noch nicht
behoben. Die Plots werden von keinem Auswertungsschritt gelesen (rein
visuelle Kontrolle), daher unnoetiger taeglicher Aufwand.
Fix: `steering_lateral_model.py [log ...]` beschraenkt NUR die
PNG-Erzeugung auf die uebergebenen Logs (leer = wie bisher alle) - die
Kalibrierung (k/R²) UND die JSON-Kennzahlen pro Log werden weiterhin
IMMER fuer alle Logs neu berechnet, weil sich k durch jedes neue Log
leicht verschieben kann. `run_daily_pipeline.py` uebergibt jetzt
`new_logs`. Verifiziert: Einzelaufruf mit einem Log-Namen hat nur dessen
PNG neu geschrieben, die uebrigen 23 blieben mtime-unveraendert.

## Automatischer Lauf: 1 neue Logs verarbeitet (2026-09-12)

- `2026-09-12 211851`, Dauer=20min, Masse=1173.4kg (FLI ~47.7%->~38.5%, automatisch berechnet (SOLO-Annahme))

Auffaelligkeiten:
- vibration: 2026-09-12 211851: Resonanz auf AccelerationX bei 9.3 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-12 211851: Resonanz auf AccelerationY bei 16.6 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-12 211851: Resonanz auf AccelerationZ bei 16.9 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- unmapped_channels: 110238 nicht zugeordnete Messwerte insgesamt, unbekannte Original-Spalten: ['Actual (AFR)', 'Brake Fluid Line Hydraulic Pressure (Raw Value) (bar)', 'Engine Revolutions Per Minute (RPM)', 'Unterstützter tatsächlicher Gangstatus des Getriebes', 'Vehicle Speed (km/h)'].

## Kurvenmodell, erster Baustein: CAN-Querdynamik-Kanaele gegen OBD-Lenkwinkelmodell validiert (2026-09-13)

Auftakt der laut `mx5_can_bus_status.md` bisher "noch nicht begonnenen"
Querdynamik-/Kurvenmodell-Erweiterung. Nutzerentscheidung (Multiple-Choice):
erst die neuen CAN-Kanaele (`Steering_Wheel_Absolute_Angle`, `Lateral_Acc_Raw`,
`YawRate_Raw`, alle RCM/EPS ueber HS-CAN) gegen das bereits gut validierte
OBD-Lenkwinkelmodell (`steering_lateral_model.py`, R²=0.950/LOO=3.40°/s,
155 Kalibrierpunkte) kreuzvalidieren, bevor irgendein neues Kurvengeschwindig-
keitsmodell gebaut wird - CAN-Kurvendaten sind bisher duenn (nur 2 echte
Fahrten, keine trackfokussiert), waehrend die OBD-Kalibrierung breit
abgesichert ist.

**Methodik:** einziger Log mit gleichzeitiger CAN- UND OBD-Fusion-Aufzeichnung
(Y-Splitter-Kalibrierfahrt, selber OBD-Port, schon fuer Kupplung/Tank/
Bremsdruck genutzt, siehe `mx5_can_bus_status.md`): `candump-2026-09-12_211833`
(CAN, 20min) + `2026-09-12 211851.dlg` (OBD, dieselbe Fahrt). Neues Skript
`scripts/can_lateral_validation.py`.

**Zeitabgleich-Fund:** die `timestamp_local`-Spalte, die `build_datalake.py`s
`ingest_dlg()` aus den .dlg-Rohzeiten (TICKS_OFFSET, .NET-Ticks) erzeugt, ist
trotz des Namens tatsaechlich UTC statt lokal (Deutschland im September =
CEST = UTC+2) - der CAN-Log-Timestamp (korrekt lokal ueber die candump-
Unix-Epoche) lag dadurch ~2h vor dem (falsch benannten) dlg-Timestamp. Bisher
unsichtbar, weil alle anderen Skripte Zeiten nur INNERHALB eines Quellformats
vergleichen. Kein Fix an `build_datalake.py` (der Bug ist dort harmlos) -
stattdessen wird der exakte Zeitversatz in `can_lateral_validation.py` per
Kreuzkorrelation der beiden `VehicleSpeed`-Spuren bestimmt: **17,88s** (Meta-
daten-Schaetzwert 17,58s als Startpunkt, Kreuzkorrelation r=0,9997 bei bestem
Offset - eindeutiger, sehr scharfer Peak, hohe Vertrauenswuerdigkeit).

**Ergebnis (n=10708 Samples, 18,6min fahrend):**
- **Lenkwinkel:** `SteeringAngle_CAN` vs. `STEER_ANGL_EPS` (nullpunktkorrigiert):
  r=0,999, slope=0,977, RMSE=1,96°, gleiches Vorzeichen. Praktisch perfekte
  Uebereinstimmung - beide Sensoren (EPS-Kanal ueber OBD und SSU-Kanal ueber
  CAN) lesen denselben physikalischen Lenkwinkel, nur der CAN-Kanal ganz ohne
  die vom OBD-Kanal bekannte Nullpunkt-Offset-Problematik (siehe
  `mx5_steering_angle.md` - dort teils bis +148° Offset).
- **Querbeschleunigung:** `LateralAcc_CAN` (RCM, 0x76) vs. Lenkwinkelmodell:
  r=0,910, slope=1,045 (nach Vorzeichenkorrektur), RMSE=0,069g. Gute
  Uebereinstimmung in Groessenordnung UND Trend, sichtbare Abweichung genau an
  den scharfen Spitzen - konsistent mit der bereits dokumentierten
  Modellschwaeche bei schnellen Lenkumkehrungen (`mx5_steering_angle.md`,
  "underestimates during fast steering reversals").
- **Gierrate:** `YawRate_CAN` (RCM, 0x75) vs. Lenkwinkelmodell: r=0,950,
  slope=0,775, RMSE=2,08°/s. Der Scatterplot zeigt bei hohen Gierraten
  (>15-20°/s) eine klare Abflachung des Modells relativ zu CAN - das CAN-Gyro
  faengt schnelle Transienten ein, die das statische kinematische
  Lenkwinkelmodell per Konstruktion nicht kann (kein Phasenverzug Lenkung->
  Gierantwort). Unabhaengige Bestaetigung eines bereits bekannten Caveats mit
  einem komplett anderen Sensor.
- **CAN-interne Konsistenz** (kinematisch v*`YawRate_CAN` vs. roh
  `LateralAcc_CAN`, beide aus dem RCM, unabhaengig vom OBD-Modell): r=0,965,
  slope=1,021, RMSE=0,044g - die beiden RCM-Signale sind untereinander
  hochgradig konsistent, ein gutes Zeichen fuer die grundsaetzliche
  DBC-Skalierung beider Kanaele.

**Vorzeichen-Fund UND Fix:** `Lateral_Acc_Raw`/`YawRate_Raw` kamen beide mit
umgekehrtem Vorzeichen relativ zur restlichen Projekt-Konvention (durchgaengig
+ = Rechtskurve in `steering_lateral_model.py`/`grip_estimation.py`/
`corner_event_analysis.py`) - die DBC nutzt SAE-Konvention (+ = links), wie
bereits in `mx5_can_bus_status.md` fuer `YawRate_Raw` vermutet (dort nur
schwach per GPS bestaetigt, r=0,30). Jetzt mit n=10708 Samples und r=0,91-0,95
eindeutig bestaetigt UND direkt in `build_datalake.py`s `ingest_can()`
korrigiert (beide Kanaele werden beim Einlesen negiert, mit Verweis auf
diesen Log als Beleg im Kommentar) - Datalake neu gebaut, Validierung danach
erneut gelaufen: alle drei Vergleiche zeigen jetzt `sign=+1`.

**Einordnung:** CAN-Lenkwinkel ist der klar bessere Kanal (kein Offset-
Problem, gleiche Praezision) und sollte den OBD-Lenkwinkel fuer zukuenftige
CAN-Logs ersetzen. CAN-Querbeschleunigung/-Gierrate (RCM, direkt gemessen,
nicht wie beim OBD-Modell aus Lenkwinkel+Geschwindigkeit abgeleitet) sind
jetzt das erste Mal quantitativ (nicht nur per schwacher GPS-Korrelation)
gegen ein unabhaengiges, gut kalibriertes Modell bestaetigt - fuer ein
kuenftiges Kurvengeschwindigkeitsmodell ist das RCM die vertrauenswuerdigere
Quelle (direkt gemessene Beschleunigung statt kinematisch aus Lenkwinkel
hergeleitet), sobald genug CAN-Kurvendaten (insbesondere echte Streckenfahrten)
vorliegen. **Einschraenkung:** nur EIN Log, keine Cross-Validation ueber
mehrere Fahrten/Bedingungen - bei jedem neuen simultanen CAN+OBD-Log erneut
laufen lassen.

Outputs: `results/can_lateral_validation_summary.json`,
`results/can_lateral_validation_traces.png`,
`results/can_lateral_validation_scatter.png`.

**Naechste Schritte fuer das Kurvenmodell:** sobald mehr CAN-Logs mit echten
Kurvenfahrten vorliegen (testplan Punkt "Kurven beidseitig", siehe
`mx5_can_bus_status.md`), das jetzt validierte RCM-Signal direkt als
Kalibrierbasis fuer ein v_max(R)-Modell nutzen (Alternative zum bisherigen
Literatur-Bracket μ=1,0-1,3 in `spreewaldring_lap_simulation.py`) - die
uebrigen drei in der urspruenglichen Scope-Frage diskutierten Optionen
(empirisches μ/a_lat_max(v), durchgehendes Traction-Circle, direktes
v_max(R)-Modell) bleiben offen fuer die naechste Session.

## Kurvenmodell, Fortsetzung: Nutzer-Review deckt 2 echte Bugs auf, CAN-nativer Kurvendetektor gebaut (2026-09-13, selber Tag)

Nutzer hat die 21 Kurven aus der Y-Splitter-Fahrt (Karte oben) manuell
gegen die eigene Erinnerung geprueft - direkt 2 echte Detektor-Probleme
gefunden (dieselbe Art Feedback wie schon mehrfach bei
`corner_event_analysis.py`, siehe `mx5_steering_angle.md`):

1. **Kurven 12-15 sind fragmentiert - eine einzige Kurve, LKW voraus.**
   Der alte GPS+Gyro-Detektor teilte eine durchgehende ~25s-Kurve in 4
   Fragmente (Luecken 2,9-4s zwischen den Teilstuecken, ueber dem
   `MAX_GAP_S=2.5s`-Bridging) - plausibel durch Geschwindigkeitsschwankungen
   beim Hinterherfahren hinter einem LKW.
2. **Eine ganze Kurve fehlte komplett** (`52.74538, 13.196868`, Nutzer:
   "in westlicher Richtung auch nicht langsam"). Per CAN-RCM-Signal direkt
   nachgeprueft (unabhaengig vom alten Detektor): echte, klare Kurve in
   BEIDEN Richtungen - ostwaerts +0,33g bei ~90km/h, westwaerts **-0,54g
   bei bis zu 131km/h** - genau die Art "schneller Sweeper" Corner, die
   `corner_event_analysis.py`s GPS-Kursraten-Ansatz schon vorher als
   Schwachpunkt bekannt war (Grenzwert-Problem bei kurzen/schnellen Kurven).

**Root-Cause-Fix statt Symptom-Flicken:** anstatt die GPS/Gyro-Heuristik
weiter zu tunen, wurde ein neuer, CAN-nativer Detektor gebaut
(`scripts/can_corner_event_analysis.py`) - da `LateralAcc_CAN` jetzt
quantitativ validiert ist (siehe Eintrag oben), braucht es die ganze
GPS+Gyro-Kreuzvalidierungs-Maschinerie fuer CAN-Logs gar nicht mehr, nur
noch eine einfache Schwellwert-Erkennung (|a_lat|>0,15g) direkt auf dem
durchgehenden Signal. **Ergebnis: 34 statt 21 Kurven fuer denselben Log**
- Problem 1 UND 2 sind beide automatisch geloest (keine GPS-Fensterei mehr,
die fragmentieren oder Sweeper verpassen koennte). Neue Karte:
`results/track_corners_2026-09-12_211851_can.html`.

**Bonus-Fund waehrend der Untersuchung: `VehicleSpeed`-Dual-Column-Bug in
`ingest_can()`.** Beim Pruefen der fehlenden Kurve fiel ein 227/236-km/h-
Sprungmuster im CAN-`VehicleSpeed`-Kanal auf - erst als Datenfehler
vermutet (227 km/h erschien physikalisch nicht plausibel), dann per
Dreifach-Cross-Check (CAN `HS_PCM` 0x202, OBD-Fusion-eigener Speed-Kanal,
UND unabhaengige Handy-GPS-Geschwindigkeit) bestaetigt: **die 227-228km/h
sind real** (bias-korrigiertes Modell-Vmax liegt bei ~222-223km/h, siehe
"Vmax-Validierung" oben - ein bisher undokumentierter Vmax-naher Abschnitt
in dieser Fahrt). Das eigentliche Problem war ein anderes: die DBC definiert
das Signal `VehicleSpeed` ZWEIMAL (`BO_514 HS_PCM`, die echte, seit jeher
genutzte Geschwindigkeit, UND `BO_606 HS_IC`, die auf ganze km/h gerundete
TACHO-ANZEIGE, systematisch ~4% hoeher) - exakt dasselbe Bug-Muster wie
[[mx5_build_datalake_dual_column_bug]], nur in `ingest_can()` statt
`ingest_csv()`, bisher unentdeckt weil beide Signale beim Interpolieren
unbemerkt durcheinandergemischt wurden. Fix: `HS_IC`-Variante wird vor dem
Mapping umbenannt (`VehicleSpeed_Display` -> eigener Kanal
`DisplaySpeed_CAN`), Datalake neu gebaut, Jitter verifiziert weg.

**Noch offen (Nutzer-Feedback, nicht automatisiert):**
- Kurven 1/20/21 (alte Nummerierung) sind "zu langsam" - keine echte
  Kalibrierungs-relevante Kurve, eher Rangieren/Kriechen. Keine
  automatische Filterschwelle gebaut, da Relevanz eine Ermessensfrage ist
  (weder Speed noch g allein trennt zuverlaessig, siehe naechster Punkt).
- Kurven 9/10/18 sind "Abbiegen" (Kreuzung), keine echte Kurve - physikalisch
  nicht von einer echten Kurve unterscheidbar ohne Strassennetz-Kontext
  (OSM-Abgleich analog `steering_zero_offset.py` waere der naechste
  Schritt, wenn das gebraucht wird - noch nicht gebaut).
- CAN-native Kurvenliste hat jetzt 34 Ereignisse fuer nur EINEN Log -
  Nutzer-Review der vollstaendigen neuen Liste (inkl. der 13 neuen)
  steht noch aus.

**Update (selber Tag, direkte Antwort auf die Review):** Nutzer bestaetigte
den Rest der 34er-Liste, aber zwei weitere Praezisierungen:
- **Kurve 30 ist ein Geradeaus-Fehlalarm** (+0,20g bei ~122-126km/h, 3,2s -
  vermutlich Spurwechsel/Fahrbahnkamber). Per manueller Ausschlussliste
  `EXCLUDED_EVENTS` in `can_corner_event_analysis.py` entfernt (analog
  `EXCLUDED_CALIBRATION_EVENTS`-Muster in `steering_lateral_model.py`).
- **S-Kurven/Schikanen wurden faelschlich als EIN Ereignis mit nur einer
  Richtung erkannt** (Nutzer-Beispiele: alte Kurven 11 und 26). Root Cause:
  die Luecken-Ueberbrueckung (`MAX_GAP_S`) ignorierte bisher das Vorzeichen -
  eine Rechtskurve, die nahtlos (oder mit kurzer Luecke) in eine Linkskurve
  uebergeht, wurde zu einem Blob mit dem Vorzeichen der staerksten Teilkurve
  verschmolzen. Bestaetigt an Rohdaten: alte Kurve 11 war in Wahrheit
  rechts(7,3s,+0,42g)->links(7,4s,-0,57g)->rechts(6,5s,+0,64g)->
  links(2,0s,-0,31g), alte Kurve 26 rechts(5,3s,+0,74g)->links(4,7s,-0,81g) -
  alles klare, mehrsekuendige Teilkurven, keine Rauschartefakte.
  **Fix:** `detect_events()` bridged Luecken jetzt nur noch INNERHALB
  desselben Vorzeichens, ein Vorzeichenwechsel beendet immer das laufende
  Ereignis. **Ergebnis: 34 -> 43 Ereignisse** fuer denselben Log (9 zusaetzliche
  durch die S-Kurven-Aufspaltung, minus 1 durch den Geradeaus-Ausschluss).
  Karte aktualisiert: `results/track_corners_2026-09-12_211851_can.html`.
  Diese 43 Kurven gelten jetzt als nutzerbestaetigte Kalibrierbasis fuer den
  naechsten Schritt (v_max(R)- bzw. a_lat_max(v)-Modell).

## Erstes empirisches Kurvengeschwindigkeitsmodell (2026-09-13, selber Tag)

Neues Skript `scripts/corner_speed_model.py`: aus den 43 nutzerbestaetigten
CAN-Kurven pro Kurve v_peak (Geschwindigkeit exakt am a_lat-Spitzenwert-
Sample, dafuer `can_corner_event_analysis.py` um `speed_at_peak_kmh`
ergaenzt) und impliziten Radius R=v²/a_lat berechnet (kinematische
Definition, kein Fit - beide Groessen direkt aus dem validierten CAN-RCM-
Signal gemessen, selber Zeitpunkt).

**Ergebnis:** v_peak 7-223km/h, a_lat_peak 0,18-0,82g (max = staerkste
sicher gefahrene Kurve im Datensatz), R_implied 1-2238m. Moderater
NEGATIVER Trend Geschwindigkeit vs. Grip-Nutzung (r=-0,38) - bei hohem
Tempo wird tendenziell weniger vom verfuegbaren Grip genutzt, plausibel als
Sicherheitsmarge auf offener Strasse (keine Limit-Fahrt), nicht zwingend
ein Reifen-Effekt.

**Vergleich mit dem bestehenden Lap-Sim-Bracket (`TIRE_MU_RANGE=(1.0,1.3)`
in `spreewaldring_lap_simulation.py`): konsistent, keine Anpassung noetig.**
Alle 43 Kurven bleiben unter der unteren Grenze (0,82g max < 1,0g) - das
Bracket bleibt eine gueltige Obergrenze. **Wichtig: das ist eine
UNTERGRENZE fuer den echten Reifengrip, keine Grenzwertmessung** - normale
Landstrassen-/Autobahnauffahrt-Kurven, keine Rennstrecke, das Auto hat
diese Werte nachweislich SICHER gehalten, koennte am echten Limit mehr.
Eine echte Obergrenzen-Messung braucht eine Grenzbereichsfahrt mit
CAN-Logging (Testplan-Punkt "Kurven beidseitig"/Track-Tag, noch offen).

**Einordnung:** der bestehende Literatur-Bracket wird durch echte
Telemetrie bestaetigt statt widerlegt - fuer die Rundenzeitsimulation
besteht aktuell kein Handlungsbedarf, das Kurvenmodell-Projekt hat aber
jetzt zum ersten Mal einen empirischen, nicht nur literaturbasierten
Datenpunkt. Naechster sinnvoller Schritt: dieselbe Analyse auf mehr/andere
CAN-Logs anwenden (v.a. eine echte Grenzbereichsfahrt), um von "Untergrenze
bestaetigt Bracket" zu "echte Obergrenze gemessen" zu kommen.

Outputs: `results/corner_speed_model.png`, `results/corner_speed_model_summary.json`.

## Kurvenmodell: Referenzpunkt "am Limit gefahren" ergaenzt (2026-09-13, selber Tag)

Nutzerwunsch: dieselbe Kreuzung/S-Kurve wie CAN-Kurven 32/33 (Autobahnauffahrt
52.738/13.2096) wurde "letzte Woche" (2026-09-09, Log `170146`) deutlich
schneller/am Limit gefahren - ob auch ohne CAN (nur GPS/Gyro) vergleichbar,
sollte trotzdem gegen das Modell gelegt werden.

**Beim Nachrechnen aufgefallen: derselbe S-Kurven-Merge-Bug wie bei CAN
11/26 existiert auch im alten GPS/Gyro-Detektor** - `corner_event_summary.json`
fuehrt diesen Abschnitt als EIN Ereignis ("links", a_lat_mean=-0,09g,
irrefuehrend niedrig durch Phasenmittelung ueber Rechts+Links). Das deckt
sich mit einer bereits frueher dokumentierten Beobachtung zu genau diesem
Fall (siehe `mx5_steering_angle.md`, "phase-averaged mean can hide a
genuine oversteer window") - jetzt als generelles Strukturproblem erkannt,
nicht nur ein Einzelfall. **Nicht in `corner_event_analysis.py` gefixt**
(betrifft nur diese eine Ad-hoc-Auswertung, keine Aenderung an der Kern-Pipeline
in dieser Session) - stattdessen direkt aus den Rohkanaelen (RotationRateZ,
VehicleSpeed, kinematisch v*omega wie im Skript selbst, Yaw-Achse Z per
`imu_orientation.detect_vertical_axis()`) neu extrahiert, mit dem exakten
Peak-Sample statt Fenstermittel:
- **Rechtskurve (der dokumentierte ESP-Fall):** t=2003,69s, v=111,0km/h,
  a_lat=+1,143g, R_implied=85m.
- **Linkskurve (Ausgang derselben S-Kurve):** t=2008,74s, v=89,0km/h,
  a_lat=-1,015g, R_implied=61m.

Beide als `REFERENCE_EVENTS` in `corner_speed_model.py` ergaenzt (eigene
Marker/Farbe im Plot, deutlich als "GPS/Gyro, keine CAN-Bestaetigung"
gekennzeichnet). **Ergebnis:** beide liegen ÜBER allen 43 CAN-Kurven UND
ueber/nahe der unteren Lap-Sim-Grenze (mu=1,0) - zum ersten Mal ein echter
Datenpunkt NAHE am vermuteten Grip-Limit, nicht nur eine Untergrenze.
**Wichtiger Vorbehalt bleibt:** der Rechtskurven-Peak faellt exakt mit dem
dokumentierten ESP-Eingriff zusammen - laut `mx5_tires`-Memory kann
a_lat_peak waehrend Uebersteuern/Schleudern den nachhaltig haltbaren Grip
UEBERSCHAETZEN, dieser Wert ist also eher eine obere Range-Grenze als ein
sauberer Grip-Messwert. Der Linkskurven-Wert (kein dokumentierter
ESP-Eingriff) ist vertrauenswuerdiger als reiner "nahe am Limit"-Punkt.

Outputs aktualisiert: `results/corner_speed_model.png`,
`results/corner_speed_model_summary.json` (neues Feld
`reference_events_gps_gyro_only`).

## Kurvenmodell auf alle CAN-Logs erweitert (2026-09-14)

`corner_speed_model.py` liest jetzt ALLE `results/can_corner_event_summary_
candump-*.json` (Glob statt fest verdrahteter Einzel-Log-Pfad) -
`can_corner_event_analysis.py` laeuft inzwischen automatisch im taeglichen
Pipeline-Lauf fuer jedes CAN-Log, dadurch waren neben 211833 bereits 6
weitere Logs mit Kurvendaten vorhanden (u.a. die 3 frisch vom Pi
nachgeholten von heute). Die urspruengliche Nutzer-Review (S-Kurven-Trennung,
Geradeaus-Ausschluss) steckt in `can_corner_event_analysis.py` selbst und
gilt automatisch fuer alle Logs mit.

**Ergebnis: 220 Kurven aus 7 Fahrten** (vorher 43 aus 1 Fahrt) - max
weiterhin 0,83g (kaum veraendert gegenueber 0,82g vorher), Trend
Geschwindigkeit-vs-Grip-Nutzung schwaecht sich mit mehr Daten leicht ab
(r=-0,38 -> -0,16, "kein klarer Trend"). **Weiterhin konsistent mit dem
Lap-Sim-Bracket mu=1,0-1,3**, keine Aenderung an der Kernaussage - die
groessere Datenbasis macht die Untergrenze aber deutlich robuster (7
unabhaengige Fahrten statt 1). Der ESP-Referenzpunkt aus 170146 bleibt der
einzige Punkt oberhalb der unteren Bracket-Grenze.

Outputs aktualisiert: `results/corner_speed_model.png`,
`results/corner_speed_model_summary.json` (`source_logs`-Feld statt
`source_events`).

## Automatischer Lauf: 1 neue CAN-Logs uebernommen (2026-09-14)

- `candump-2026-09-11_200735`

Auffaelligkeiten:
- unmapped_channels: 110238 nicht zugeordnete Messwerte insgesamt, unbekannte Original-Spalten: ['Actual (AFR)', 'Brake Fluid Line Hydraulic Pressure (Raw Value) (bar)', 'Engine Revolutions Per Minute (RPM)', 'Unterstützter tatsächlicher Gangstatus des Getriebes', 'Vehicle Speed (km/h)'].

## Kurvenmodell-Diskussion + Kraftkreis-Check aus CAN-Daten (2026-09-14)

**Konzeptionelle Frage (Nutzer): sollte das Kurvenmodell mehrdimensional
aufgebaut werden (g-Kraefte, Kurvenradius/Lenkwinkel, Geschwindigkeit)?**
Ergebnis der Diskussion:
1. `a_lat = v^2/R` ist eine kinematische Identitaet - (a_lat, R, v) sind
   keine 3 unabhaengigen Achsen, `corner_speed_model.py` berechnet R ja
   bereits nur aus (v, a_lat). Radius als eigene Modell-Dimension bringt
   nichts Neues.
2. Lenkwinkel ist die einzige echte unabhaengige Groesse (Schraeglauf/
   Verstaerkungssaettigung, siehe `mx5_steering_angle`-Memory), aber fuer
   die REKONSTRUKTION von R nutzlos - die Streckengeometrie (OSM+Orthofoto)
   liefert R bereits genauer als jede Lenkwinkel-Rueckrechnung (siehe
   `steering_radius_estimate.py`: 19.8% Medianfehler, unvalidiert >82m).
3. **Aus Sicht der Rundenzeit-Simulation** (`spreewaldring_lap_simulation.py`)
   ist das ohnehin die falsche Frage - die Sim braucht kein besseres
   R (hat sie schon), sondern haengt an der dort bereits dokumentierten
   Einschraenkung "Kein Reifenkraftkreis" (Zeile ~59-63): Quer- und
   Laengsgrenze (Kurve/Bremse) werden unabhaengig behandelt, obwohl real
   gekoppelt. Das ist der einzige Punkt, an dem "mehrdimensional" fuer die
   Simulation tatsaechlich etwas aendern wuerde - hoehere Prioritaet als
   Ideallinie-Fahren laut Docstring selbst nicht, aber vor einem
   Lenkwinkel-Ausbau.

**Direkt umgesetzt: `scripts/can_traction_circle.py`** - Kraftkreis-Check
aus den CAN-Daten (RCM liefert `LateralAcc_CAN` UND `LongitudinalAcc_CAN`
zeitgleich vom selben Sensor, keine Rueckrechnung noetig). Datenbasis:
alle 7 CAN-Logs mit dem Kanal, 273280 Samples.

**Ergebnis:** die beobachtete Huelle ist bereits sichtbar NICHT
kreisfoermig - max -a_lon (Bremsen) = 0.74g, max a_lon (Beschleunigen)
= 0.57g, max |a_lat| (Quer) = 0.82g. Bremsen nutzt alle 4 Reifen,
Beschleunigen beim RWD-MX-5 nur die Hinterachse - eine niedrigere
Beschleunigungs- als Bremsgrenze ist physikalisch plausibel. **Wichtige
Einschraenkung: alles klar unter dem echten Reifenlimit (Alltagsfahrten,
wie schon bei `corner_speed_model.py`)** - das ist eine Formindikation/
Untergrenze, keine Grenzwertmessung. Trotzdem bereits jetzt ein Hinweis,
dass die Sim-Vereinfachung "eine gemeinsame mu-Konstante fuer Kurve und
Bremse" die Asymmetrie zwischen Bremsen und Beschleunigen verschenkt.

**Bonus-Check: Radschlupf-Kandidaten aus den 4 WheelSpeed_CAN-Kanaelen**
(Geradeausfahrt, v>20km/h, |a_lon|>0.3g, Abweichung eines Rads vom Median
der 4). 7 Kandidaten gefunden, staerkste Abweichung 4.5km/h - aber **nicht
von reinem Interpolations-/Zeitversatz-Rauschen zwischen den CAN-IDs zu
unterscheiden**: derselbe Rauschtest bei unbelasteter Geradeausfahrt zeigt
bereits bis zu 4.5km/h Abweichung ohne jede Beschleunigung. Kein
verwertbarer Befund, nur als Kandidatenliste im JSON dokumentiert.
`WheelSpeed_CAN_1-4` sind zudem nicht Achse/Seite zugeordnet (dieselbe
Einschraenkung wie bei den TPMS-Reifendrucksensoren, siehe `mx5_tpms`-
Memory) - ein Treffer sagt nur "irgendein Rad weicht ab", nicht welches.

**Naechste Schritte:** kein Handlungsbedarf an `spreewaldring_lap_simulation.py`
selbst in dieser Session (bewusst nicht umgesetzt - Datengrundlage ist noch
sub-Limit, siehe oben). Sinnvoll bei mehr/aggressiveren CAN-Logs: (1) die
Asymmetrie-Zahlen weiterverfolgen, sobald naeher am Limit gefahren wird,
(2) fuer den Radschlupf-Check den Zeitversatz zwischen den CAN-IDs (0x75/
0x76 vs. WheelSpeed-Botschaft) sauber beheben statt nur linear zu
interpolieren, bevor kleine Abweichungen als echt gewertet werden.

Outputs: `results/can_traction_circle.png`, `results/can_traction_circle_summary.json`.

## Kraftkreis-Check, Korrektur: der staerkste Radschlupf-Kandidat war ein Schaltvorgang, kein Reifenschlupf (2026-09-14, selber Tag)

**Nutzer-Nachfrage, ob der Beschleunigungsabschnitt vor dem oestlichen
Vmax-Lauf (candump-2026-09-12_211833, siehe `mx5_bidirectional_wot_validation`-
Memory) sich das genauer angeschaut wurde - war er nicht, nur pauschal ueber
alle Logs gerechnet.** Direkt nachgeholt: der staerkste Radschlupf-Kandidat
aus dem vorigen Eintrag (Rad #3, 4.5km/h Abweichung, t=709.1s) faellt exakt
mit dem globalen Maximum von `LongitudinalAcc_CAN` (0.57g) im gesamten
Datensatz zusammen - und beides faellt exakt mit einem 2.->3.-Gang-Schaltvorgang
zusammen (Kupplung `ClutchPosition_CAN_raw` springt auf 199, RPM faellt von
~7430 auf ~5590, `Gear_CAN` wechselt 2->3, alles im selben ~0.6s-Fenster).

**Rohdaten-Check bestaetigt einen echten, physikalischen Effekt, keinen
Sensor-/Interpolationsartefakt:** Raeder 1+2 (vermutlich eine Achse) steigen
im fraglichen Fenster glatt/monoton an, waehrend Raeder 3+4 (vermutlich die
andere Achse) im selben Zeitfenster synchron miteinander oszillieren (mehrere
km/h Ausschlag, mehrere Zyklen in <0.5s) - genau das Signaturmuster eines
Antriebsstrang-/Radschlupf-Schlags beim Wiedereinkuppeln unter Volllast
(APP=100% durchgehend), nicht ambientes Messrauschen (das wuerde nicht zwei
Kanaele synchron und die anderen zwei glatt lassen). **Aber:** das ist ein
Schaltvorgang-Effekt (Kupplung/Antriebsstrang-Schlag beim Wiedereinkuppeln),
kein Beleg fuer das reine Reifen-Traktionslimit bei Dauervolllast - genau
die Art Ereignis, die `spreewaldring_lap_simulation.py`s bereits dokumentierte
Vereinfachung "Schaltzeiten ignoriert" komplett uebersieht.

**Konsequenz: der bisherige max-Beschleunigungswert (0.57g) im Kraftkreis-
Plot war durch diesen Schaltvorgang verunreinigt, keine saubere Dauer-
Volllast-Messung.** Fix in `can_traction_circle.py`: neue Kupplungs-basierte
Ausschlussmaske (`CLUTCH_ACTIVE_RAW=15`, plus `SHIFT_SETTLE_S=1.0` Nachlauf,
da das Wiedereinkuppeln in der Radgeschwindigkeit noch ~0.3-0.4s nachschwingt)
entfernt Schaltfenster aus Kraftkreis-Statistik UND Radschlupf-Check, bevor
beide berechnet werden. **Ergebnis nach Fix:** max a_lon (Beschleunigen)
0.57g -> 0.51g, Verhaeltnis Bremsen/Beschleunigen 1.18x -> 1.32x (Asymmetrie
wird nach Bereinigung sogar STAERKER sichtbar, nicht schwaecher). Der
verunreinigte Radschlupf-Kandidat verschwindet aus der Liste, verbleibende
Kandidaten liegen alle im vorher etablierten Rausch-Rahmen (1.3-3.5km/h).

**Allgemeine Lehre (nicht nur dieser eine Fall):** jeder kuenftige CAN-
basierte Antriebsstrang-/Traktions-Check sollte Schaltfenster (Kupplung
betaetigt + kurzer Nachlauf) grundsaetzlich ausschliessen, bevor Extremwerte
interpretiert werden - dieselbe Art Fehler wie schon mehrfach in diesem
Projekt (ein auffaelliger Extremwert, der sich bei genauerem Hinsehen als
Artefakt eines Nebenereignisses statt des untersuchten Phaenomens entpuppt,
vgl. GPS-Tunnel-Ausreisser in `mx5_steering_angle`-Memory).

Outputs aktualisiert: `results/can_traction_circle.png`, `results/can_traction_circle_summary.json`.

## Schaltzeit-Check aus CAN-Kupplungsdaten: reale Schaltzeiten 4.5-10.7x langsamer als ATTACK_SHIFT_S (2026-09-14, selber Tag)

**Direkter Nutzer-Auftrag im Anschluss an den Schaltruck-Fund oben:** alle
anderen Kupplungsbetaetigungen im selben Log (und projektweit) auf Dauer
pruefen - erstmals echte Erfassung von "Kupplung tritt aus, bis wieder
vollstaendig eingerueckt" ueber `ClutchPosition_CAN_raw` (bisher gab es nur
die grobe 2-4Hz-OBD-Ableitung `CPP_PER_MZ` ohne sauberen Start/Ende-Zeitpunkt).

**Neues Skript `scripts/shift_time_analysis.py`:** erkennt zusammenhaengende
Kupplungs-Betaetigungsphasen (`ClutchPosition_CAN_raw > 15`, Luecken bis
0.15s ueberbrueckt) ueber alle 7 CAN-Logs, ordnet jeder Phase Gang-vorher/
-nachher aus `Gear_CAN` zu und klassifiziert: Standstill (Gang=0 beteiligt
oder Dauer >3s, 28 Faelle), Einzelgang-Hochschaltung (26), Einzelgang-
Rueckschaltung (21), Mehrfachgang-Ruckschaltung (12, vermutlich Blip-
Rueckschaltungen vor Kurven/Bremsungen), und "kein Gangwechsel" (48 Faelle -
Kupplung getreten, aber am Ende blieb derselbe Gang drin; nicht belastbar
interpretierbar, nur gezaehlt).

**Ergebnis (26 Einzelgang-Hochschaltungen, alle 5 Gangpaare abgedeckt):**
reale Kupplungs-Betaetigungsdauer 0.96-2.40s (Median je Gangpaar 1.12-1.61s)
gegen `ATTACK_SHIFT_S` (`performance_simulation.py`, 0.15-0.25s, laut
dortigem Kommentar ein Literatur-Default fuer "maximale Fahrleistung", nicht
aus diesem Auto gemessen): **4.5x (5.->6. Gang) bis 10.7x (1.->2. Gang)
langsamer.**

**Einordnung (Nutzer hatte das vorab richtig eingeschaetzt: "waren
vermutlich noch nicht attack"): bestaetigt.** Diese Werte sind KEIN Ersatz
fuer `ATTACK_SHIFT_S` - das ist explizit fuer eine Hotlap-Simulation "ans
Limit gefahren" gedacht, waehrend alle bisherigen CAN-Logs normale
Alltagsfahrten sind. Sie zeigen nur, wie weit entspanntes Kupplungspedal-
Schalten mit diesem Auto/Fahrer ueber einem theoretischen Best-Case liegt -
plausibel fuer ein Fussgekuppeltes Getriebe ohne Renn-Eile, im Gegensatz
zu den sequentiellen/paddle-Systemen, fuer die solche Zahlen oft gelten.
Passt damit ins selbe wiederkehrende Muster wie `corner_speed_model.py`
und der Kraftkreis-Check: normale Fahrt liefert eine Referenz/Untergrenze,
keine Grenzwert-/Best-Case-Messung - fuer eine echte `ATTACK_SHIFT_S`-
Kalibrierung braeuchte es Schaltvorgaenge aus einer tatsaechlichen
Renn-/Attack-Fahrt.

**Fuer die Simulation:** kein Handlungsbedarf an `ATTACK_SHIFT_S` selbst
(bleibt die richtige Annahme fuer den Hotlap-Anwendungsfall). Falls
irgendwann eine "realistische Alltagsfahrt"-Variante der Rundenzeit-
Simulation gewuenscht wird (im Unterschied zur aktuellen "theoretisches
Maximum"-Variante), waeren diese Zahlen die richtige Kalibrierbasis dafuer.

Outputs: `results/shift_time_analysis.png`, `results/shift_time_analysis_summary.json`.

**Erweiterung, selber Tag (Nutzerauftrag): Bestzeiten-Tracking je Gangpaar
UND Richtung + Einbindung in die automatische Pipeline.** `build_best_times()`
haelt jetzt fuer jedes Gangpaar BEIDE Richtungen getrennt (z.B. 2->3 UND
3->2) die je bisher schnellste gemessene Kupplungs-Betaetigung fest (Dauer,
Log, Zeitstempel, Anzahl Messungen), geschrieben nach
`results/shift_time_best.json`. Keine eigene Merge-Logik noetig: das Skript
berechnet ohnehin bei jedem Lauf ueber den kompletten Datalake-Stand neu,
alte Logs verschwinden nie daraus, also kann ein Bestwert nur dazukommen,
nie verlorengehen. Aktuelle Bestzeiten (2026-09-14): 1->2 1.22s, 2->3 0.96s,
3->4 1.02s, 4->5 1.06s, 5->6 0.98s (Hochschaltungen); 3->2 2.44s (nur 1
Messung), 4->3 1.20s, 5->4 0.98s, 6->5 0.98s (Rueckschaltungen).

**In `scripts/run_daily_pipeline.py` eingehaengt:** laeuft automatisch bei
jedem Pipeline-Durchlauf, sobald neue CAN-Logs vorliegen (`if new_can_logs:`,
direkt nach dem Datalake-Rebuild) - kuenftige Logs aktualisieren die
Bestzeiten-Datei also von selbst, kein manueller Re-Run noetig.

**Nutzerauftrag, selber Tag: neue Bestzeit soll im Pipeline-Report auftauchen.**
Analog zum bestehenden `check_new_coastdown_events`-Muster: Orchestrator
liest `results/shift_time_best.json` VOR und NACH dem `shift_time_analysis.py`-
Lauf, neuer Check `pipeline_checks.check_new_shift_records()` vergleicht
beide Staende je Gangwechsel+Richtung und meldet jede Verbesserung (oder
Erstmessung) als eigenes Finding (severity "info", da kein Problem, sondern
ein positiver Fund) - erscheint automatisch im "Auffaelligkeiten"-Abschnitt
des Reports (ℹ️-Marker), da `render_report.py` Findings generisch rendert,
keine Aenderung dort noetig.

**Nutzerauftrag, selber Tag: dasselbe fuer Kurven-Spitzenwerte (Querbeschleunigung
"am Limit").** Vorher war `can_corner_event_analysis.py` (Kurvenerkennung per
CAN-Schwellwert) ueberhaupt nicht in der Pipeline verdrahtet, nur manuell pro
Log aufrufbar - erstmal nachgeholt: alle 7 bisherigen CAN-Logs rueckwirkend
durchlaufen (Backfill, damit die Historie vollstaendig ist, bevor der
automatische Vergleich anfaengt - sonst haette der naechste Pipeline-Lauf
faelschlich "neue" Rekorde aus bereits bekannten Logs gemeldet).

**Neues Skript `scripts/corner_peak_tracker.py`:** liest ALLE
`results/can_corner_event_summary_*.json` (ein File pro CAN-Log), haelt je
Richtung (rechts/links) die staerkste je gesehene Kurve fest
(`results/corner_peak_best.json`). Bewusst NICHT auf `corner_speed_model.py`s
kuratierte 43-Kurven-Basis aufgesetzt - die ist per Nutzer-Review bestaetigt
und soll das auch bleiben, waehrend der Pipeline-Tracker automatisch/
ungeprueft ueber jeden neuen CAN-Log laufen soll. Ein neuer Rekord hier ist
deshalb ein KANDIDAT fuer eine echte Grenzbereichs-Kurve, kein verifizierter
Messwert (analog zum bestehenden `check_corner_events`-Befund/a_lat_peak-
Mean-Verhaeltnis, der genauso nur ein Hinweis fuer manuelle Durchsicht ist).

**In `run_daily_pipeline.py` eingehaengt** (gleiches Vorher/Nachher-Muster
wie bei den Schaltzeiten): `can_corner_event_analysis.py` laeuft jetzt fuer
jeden neuen CAN-Log, danach `corner_peak_tracker.py`; neuer Check
`pipeline_checks.check_new_corner_peak_record()` vergleicht die Bestwerte
vorher/nachher je Richtung und meldet jede Verbesserung als "info"-Finding
im Report. Aktueller Stand (2026-09-14, alle 7 Logs): rechts 0.81g, links
0.82g (beide aus dem Y-Splitter-Log `candump-2026-09-12_211833`).

## Automatischer Lauf: 3 neue CAN-Logs uebernommen (2026-09-14)

- `candump-2026-09-13_135400`
- `candump-2026-09-13_144611`
- `candump-2026-09-14_081105`

Auffaelligkeiten:
- unmapped_channels: 110238 nicht zugeordnete Messwerte insgesamt, unbekannte Original-Spalten: ['Actual (AFR)', 'Brake Fluid Line Hydraulic Pressure (Raw Value) (bar)', 'Engine Revolutions Per Minute (RPM)', 'Unterstützter tatsächlicher Gangstatus des Getriebes', 'Vehicle Speed (km/h)'].
- script_error: gunzip candump-2026-09-13_135440.log.gz: Command '['gzip', '-dk', '-f', 'data/can/candump-2026-09-13_135440.log.gz']' returned non-zero exit status 1.
- script_error: .venv/bin/python scripts/build_datalake.py: exit code 1: 
  File "/home/manuel/claude/scripts/build_datalake.py", line 670, in main
    con = duckdb.connect(DB_PATH)
          ^^^^^^^^^^^^^^^^^^^^^^^
_duckdb.IOException: IO Error: Could not set lock on file "/home/manuel/claude/data/datalake.duckdb": Conflicting lock is held in /usr/bin/python3.12 (PID 755850) by user manuel. However, you would be able to open this database in read-only mode, e.g. by using the -readonly parameter in the CLI. See also https://duckdb.org/docs/stable/connect/concurrency

- script_error: .venv/bin/python scripts/can_corner_event_analysis.py candump-2026-09-13_135400: exit code 1: Kein LateralAcc_CAN fuer log_id='candump-2026-09-13_135400'

- script_error: .venv/bin/python scripts/can_corner_event_analysis.py candump-2026-09-13_144611: exit code 1: Kein LateralAcc_CAN fuer log_id='candump-2026-09-13_144611'

- script_error: .venv/bin/python scripts/can_corner_event_analysis.py candump-2026-09-14_081105: exit code 1: Kein LateralAcc_CAN fuer log_id='candump-2026-09-14_081105'


## Automatischer Lauf: 3 neue Logs verarbeitet (2026-09-14)

- `2026-09-14 081132`, Dauer=48min, Masse=1171.2kg (FLI ~38.3%->~34.4%, automatisch berechnet (SOLO-Annahme))
- `2026-09-14 163745`, Dauer=48min, Masse=1168.5kg (FLI ~34.4%->~22.1%, automatisch berechnet (SOLO-Annahme))
- `2026-09-14 173044`, Dauer=3min, Masse=1167.1kg (FLI ~23.8%->~24.4%, automatisch berechnet (SOLO-Annahme))

Auffaelligkeiten:
- vibration: 2026-09-14 081132: Resonanz auf AccelerationX bei 7.0 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-14 163745: Resonanz auf AccelerationZ bei 16.1 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-14 173044: Resonanz auf AccelerationX bei 5.7 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-14 173044: Resonanz auf AccelerationY bei 5.7 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- vibration: 2026-09-14 173044: Resonanz auf AccelerationZ bei 15.2 Hz, ausserhalb des erwarteten Bereichs 18-23 Hz.
- corner_event: 2026-09-14 081132 t=236-241s: a_lat_peak/mean-Verhaeltnis 2.0 > 2.0 (peak=-0.19g, mean=-0.10g) - moegliches Schleudern/Uebersteuern.
- corner_event: 2026-09-14 163745 t=2817-2820s: a_lat_peak/mean-Verhaeltnis 2.1 > 2.0 (peak=+0.63g, mean=+0.29g) - moegliches Schleudern/Uebersteuern.
- unmapped_channels: 110238 nicht zugeordnete Messwerte insgesamt, unbekannte Original-Spalten: ['Actual (AFR)', 'Brake Fluid Line Hydraulic Pressure (Raw Value) (bar)', 'Engine Revolutions Per Minute (RPM)', 'Unterstützter tatsächlicher Gangstatus des Getriebes', 'Vehicle Speed (km/h)'].
- script_error: .venv/bin/python scripts/drivetrain_model_validation.py: Exception: Command '['.venv/bin/python', 'scripts/drivetrain_model_validation.py']' timed out after 300 seconds
- script_error: .venv/bin/python scripts/top_speed_validation.py: Exception: Command '['.venv/bin/python', 'scripts/top_speed_validation.py']' timed out after 300 seconds
