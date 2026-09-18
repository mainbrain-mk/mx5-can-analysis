# Strecke & Querdynamik: aktueller Stand

**Herkunft:** neu verfasst 18.09.2026 aus dem Logbuch [`docs/logs/projekt-stand.md`](../logs/projekt-stand.md)
(vollständig durchgesehen bis zum letzten Eintrag vom 18.09.2026). Ab jetzt hier
direkt in-place aktualisieren, wenn sich der Stand ändert - das Logbuch bleibt
das chronologische Protokoll mit den Herleitungen.
**Zweck:** aktuell gültiger Stand zu Höhendaten/Streckengeometrie, Spreewaldring-
Ideallinie/Rundenzeit-Simulation, Lenkwinkel/Kurvenradius/Quergrip inkl.
CAN-Kurvenmodell, Kraftkreis, Bremsmodell und Schaltzeiten/Zugkraftunterbrechung.
Ergänzt [Performance-Modell-Status](performance-model.md) (Antriebsstrang/IMU) um
die streckenbezogenen Themen.

## Höhendaten & Streckengeometrie

- Primärquelle Berlin-Brandenburg-Kernschleife: LGB-Brandenburg-ALS-Punktwolken
  (`data.geobasis-bb.de`, EPSG:25833, 1×1km-Kacheln, LAS-Klasse-2-Bodenpunkte)
  plus seit 06.09.2026 zusätzlich **DGM-XYZ** - ein bereits fertig gerastertes
  1m-DGM derselben Quelle, kleiner und ohne Klassifizierungsschritt, füllt
  gezielt Lücken der ALS-Kacheln (ALS hat Vorrang, keine bestehende Kachel wird
  überschrieben). Siehe "Neue Hoehendaten-Quelle gefunden: DGM-XYZ (LGB
  Brandenburg) schliesst Luecken der ALS-Punktwolken (06.09.2026)".
- Kachelbasierte Speicherstruktur (`data/elevation/tiles/<e_km>_<n_km>.npz`,
  lazy LRU-Cache) statt einem großen dichten Gitter. Stand nach dem
  30.08.2026-Nachtrag ("spät nachts"): 150 Kacheln, **98,8 % der GPS-Punkte der
  Kernregion abgedeckt** (vorher 72,5 %).
- **Berlin-Fallback** (`gdi.berlin.de` WMS, Layer `c_dgm1`): automatisch
  eingebunden für Punkte ohne lokale Kachelabdeckung, persistent gecacht.
  Enthielt bis 30.08.2026 spät nachts einen Bug (NODATA-Sentinel `-9999` wurde
  als echte Höhe akzeptiert, 238 Cache-Einträge betroffen) - behoben, hat keine
  der bis dahin berichteten Kernaussagen verfälscht.
- **Südtirol-Rückfahrt-Korridor** (Bayern/Thüringen/Sachsen-Anhalt, für die
  31.07.2026-Heimfahrt-Logs): eigene Quellen je Bundesland (siehe "Suedtirol-
  Rueckfahrt 31.07.2026 - Vollanalyse ueber Nacht (30.08.2026)" für Details/URLs).
  **Bayern-DGM1 bleibt in diesem Datensatz unzuverlässig** (RMSE mit
  Steigungskorrektur schlechter als ohne) - Bayern-Segmente sind bewusst von
  der Gefällekorrektur ausgeschlossen. Thüringen und Sachsen-Anhalt werden
  verwendet.
- GPS-Höhe liegt systematisch **~40 m über** der DGM-Höhe (ellipsoidisch WGS84
  vs. orthometrisch DHHN2016, bekannter Geoid-Effekt) - für Steigungen/Gefälle
  immer die DGM-Werte verwenden, GPS-Höhe nur als groben Vergleich.
- Brücken-Problem: das DGM zeigt an Brücken/Unterführungen das Gelände
  UNTER der Brücke, nicht die Fahrbahn. Lösung: automatische OSM-Brücken-
  Erkennung (`fetch_osm_bridges`) + lineare Interpolation entlang der
  Fahrtstrecke (`get_elevation_along_track()` - **diese Funktion verwenden,
  nicht `get_elevation()` direkt**). Für Regionen ohne dedizierte
  Brücken-Kachel-Logik (Bayern/Thüringen/Sachsen-Anhalt) zusätzlich ein
  generischer Plausibilitätsfilter (`STEP_GRADE_MAX=15%`, verwirft
  physikalisch unmögliche Sprünge zwischen GPS-Punkten als Brücken-Artefakt).
- Effekt auf das Fahrleistungsmodell (aktuellster Stand, 06.09.2026): RMSE mit
  Gefällekorrektur **0,170 m/s²** (73 von 94 Top-Speed-Segmenten mit
  vertrauenswürdiger Korrektur, Bayern ausgeschlossen), ~5 % besser als ohne
  Korrektur.
- Spreewaldring selbst liegt außerhalb der Brandenburg-DGM-Kernabdeckung
  (E~409,8/N~5761,6km) - für die Rundenzeit-Simulation liegen **keine
  Höhendaten** vor (bekannte, nicht behobene Einschränkung).

## Spreewaldring Racing-Line & Rundenzeit

- **Streckengeometrie:** vollständig aus OSM rekonstruiert (way `172927073`,
  Knoten 3 bis 94, die dieselben OSM-Punkte sind - Runde schließt sich in den
  Rohdaten selbst, **keine erfundene Schließgerade mehr nötig**). Siehe
  "Spreewaldring Training Center - Streckenrekonstruktion aus OSM (NEU,
  30.08.2026)". Länge OSM-roh 2488,3 m.
- **Orthofoto-Verfeinerung** (LGB-Orthofoto, 20 cm Bodenauflösung) + Savitzky-
  Golay-Glättung (90 m Fenster) der Mittellinie: Länge **2529,5 m**, 13 Kurven,
  Radien 11–73 m. Siehe "Orthofoto-Verfeinerung der Streckengeometrie (NEU,
  30.08.2026)".
- **Fahrbahn als Fläche statt Mittellinie** (Sättigungsschwelle statt
  Farbabstand, erfasst die weiße Randlinie korrekt mit): Hauptschleife Breite
  Median **9,60 m** (nahe Betreiberangabe 10 m), Boxengasse 11,8 m, zwei
  Verbindungsstücke A/C. Siehe "Fahrbahn als Flaeche statt Mittellinie (NEU,
  30.08.2026)".
- **Ideallinie/Rundenzeit-Fortschritt** (quasi-stationäres
  Zweipass-Punktmassenmodell, Reifen-mu-Bandbreite 1,0–1,3 aus Literatur für
  Nankang NS-R2):

  | Modellstufe | mu=1,0 | mu=1,3 |
  |---|---:|---:|
  | Mittellinie (keine Ideallinie) | 110,52 s | 99,54 s |
  | Krümmungsminimierung ("elastic band") | 100,51 s | 90,99 s |
  | Alternierend optimiert (Linie↔Geschwindigkeit, kombinierter Kraftkreis) | 96,03 s | 86,98 s |

  Siehe "Erste Rundenzeit-Simulation Spreewaldring", "Erster Ideallinien-Versuch
  fuer den MX-5 ND", "Alternierende Linie/Geschwindigkeits-Optimierung ..."
  (alle NEU, 30.08.2026).
- **Interaktiver Ideallinien-Editor** (`results/spreewaldring_racing_line_editor.html`,
  reines Client-JS, kompletter Port des Fahrzeug-/Rundenzeitmodells, kein
  Server nötig): Lenkgeschwindigkeit ist jetzt eine echte physikalische
  Randbedingung (**max. 240°/s**, realistische Korrekturen ~70°/s). Nach drei
  verworfenen Fixpunkt-Ansätzen (Smootherstep-Kette, Hermite-Spline,
  Minimum-Krümmungs-QP - alle rein geometrisch, ignorieren Geschwindigkeit)
  hält der vierte: gemeinsame **querkraftbasierte Relaxation**, Fixpunkte als
  weiche Federziele (`SOFT_PIN_PULL=0,3`), 10 cm Randpuffer
  (`EDGE_MARGIN_M`). Siehe "Interaktiver Ideallinien-Editor fuer
  Spreewaldring ..." und "Nachtrag: mehrere nahe Fixpunkte ..." (beide
  07.09.2026).
- **"Natürliches Gasmodell"** (experimentell, Default AUS; Vollgas-sobald-möglich
  "Zeitoptimal" bleibt Standard): APP% aus einer an einer echten Kurve
  kalibrierten Lenkrate-Formel statt Bang-Bang, mit Ausroll-Vorausschau als
  Sicherheitsnetz (Bisektion, kann appFrac nur erhöhen, nie senken - macht das
  Modell konstruktiv nie langsamer als die reine Formel). Nach Nachrüsten
  einer **Rückschalt-Logik** in beiden Modellen (vorher konnten beide nur
  hochschalten - ein echter Bug, kostete an Kurvenausgängen bis zu 62 % der
  möglichen Beschleunigung):

  | Modell | Rundenzeit (25-Bearbeitungen-Testdatensatz, 1g-Grenze) |
  |---|---:|
  | Zeitoptimal | 99,194 s |
  | Natürliches Gasmodell | 99,337 s |

  Beide unter der 240°/s-Lenkraten-Grenze (max. 95°/s gemessen). Siehe
  "Nachtrag (09.09.2026): 'Natuerliches Gasmodell' ueberholt 'Zeitoptimal' ..."
  und "Nachtrag (09.09.2026, Fortsetzung): Rueckschalt-Logik nachgeruestet ...".
- **Wichtige Einordnung (Nutzer):** "Zeitoptimal" ist trotz des Namens keine
  echte obere Schranke, sondern eine gierige Heuristik ohne vorausschauende
  Schaltstrategie - beide Modelle sind Arbeitsmodelle, kein bewiesenes
  Optimum. In der Realität ist eine perfekt gierige Vollgas-Strategie ohnehin
  nicht fahrbar.
- **Bremsmodell in der Rundenzeit-Simulation** integriert (reales, nicht
  theoretisches Kraftkreis-Modell) - Details siehe Abschnitt Bremsmodell unten.
- **Offen/bekannte Einschränkungen:** kein Gewichtstransfer, keine Aero; die
  Editor-Simulation nutzt weiterhin einen isotropen Reifen (ein mu für Längs-
  UND Querrichtung), obwohl der CAN-Kraftkreis-Check eine reale Asymmetrie
  zeigt (siehe Kraftkreis-Abschnitt); Fahrzeugbreite 1,74 m und Radstand
  2,31 m sind Annahmen (Herstellerangabe bzw. Literatur, nicht G184-vermessen);
  der separate Python-Port (`spreewaldring_racing_line_search.py`) hat die
  Rückschalt-Logik **noch nicht** nachgezogen.

## Lenkwinkel/Kurvenradius/Quergrip

- OBD-Kanal `STEER_ANGL_EPS` (Lenkwinkel) + `STEER_SPD_EPS` seit 01.09.2026
  verfügbar. Kinematisches Modell (kein Schwimmwinkel-Term):
  `omega = k(Lenkwinkel) * Lenkwinkel * v`, `a_lat = v * omega`.
- **Nullpunkt-Offset-Problem gelöst:** 8 von 9 Lenkwinkel-Logs hatten einen
  systematischen Offset (bis +148°, siehe "Zwei neue Logs vom 02.09.2026 ...").
  Korrektur per OSM-Geradeausfahrt-Methode (`steering_zero_offset.py`, GPS+
  Straßengeometrie, unabhängig vom Lenkwinkel-Kanal selbst). Offset-Drift
  INNERHALB einer Fahrt explizit geprüft und **ausgeschlossen** (Power-Check
  bestätigt: die Methode würde ab ca. 2°/h Drift zuverlässig erkennen,
  gemessen wurden <0,2°/h) - ein Offset pro Log ist ausreichend, entsteht
  vermutlich einmalig beim Sensor-/ECU-Start.
- **Sättigender Gain statt fixem k** (07.09.2026, finaler Stand des
  OBD-Modells vor der CAN-Ablösung): `k(Lenkwinkel) = k1 + k2*|Lenkwinkel|`
  (k2<0) - echte Reifenkraft-Sättigung, kein Fit-Artefakt (Gain fällt von
  ~1,3–1,4 bei kleinen Lenkwinkeln <20° auf ~0,93 bei großen >150°).
  **R²=0,950, LOO-RMSE=3,40°/s, 155 Kalibrierpunkte, k1=0,023276.**
- **Kurvenradius aus dem Lenkwinkel:** nur grobe Schätzung (Median-Fehler
  19,8 %, p90 45,3 %) - **nicht** für präzise Streckengeometrie geeignet
  (dafür OSM+Orthofoto verwenden, siehe oben). Kalibrierbasis nur für
  R=10,8–81,7 m / v=11,2–71,8 km/h validiert, keine unabhängige Bestätigung
  für größere Radien (Autobahn-/Rennstrecken-Bereich). Siehe "Kurvenradius aus
  dem Lenkwinkel - Genauigkeitscheck (02.09.2026, Nutzerfrage)".
- **Methodische Einordnung a_lat_peak vs. a_lat_mean:** bei Kurven nahe/über
  der Haftgrenze (Schleudern/Übersteuern, Schwimmwinkel-Effekt) überschätzt
  `a_lat_peak` die reine Kurvenbeschleunigung deutlich - nutzerbestätigt an
  der 2,47g-Kurve (A111-Auffahrt, mean nur 1,21g). **Für Grip-/mu-Schätzungen
  `a_lat_mean` bevorzugen**, `a_lat_peak` nur zur groben Einordnung nutzen.
  Siehe "Nutzer-Bestaetigung zur 2.47g-Kurve (27.08.2026, A111-Auffahrt) ...".
- **CAN-Ablösung des OBD-Modells** (13.09.2026): `SteeringAngle_CAN` vs.
  nullpunktkorrigiertem `STEER_ANGL_EPS`: r=0,999, slope=0,977 - praktisch
  identisch, aber der CAN-Kanal hat **kein** Offset-Problem.
  `LateralAcc_CAN`/`YawRate_CAN` (RCM, direkt gemessen statt kinematisch
  hergeleitet) gegen das validierte OBD-Modell: r=0,910/0,950, RMSE
  0,069g/2,08°/s. **CAN-Lenkwinkel/RCM-Signale sind seither die bevorzugte
  Quelle für alle neuen CAN-Logs.** Siehe "Kurvenmodell, erster Baustein:
  CAN-Querdynamik-Kanaele gegen OBD-Lenkwinkelmodell validiert
  (2026-09-13)".
- **CAN-nativer Kurvendetektor** (`can_corner_event_analysis.py`) ersetzt für
  CAN-Logs die alte GPS+Gyro-Heuristik: einfache Schwellwerterkennung
  (`|a_lat|>0,15g`) direkt auf dem validierten RCM-Signal, keine
  Fensterei/Kreuzvalidierungs-Maschinerie mehr nötig. Löste zwei vom Nutzer
  gefundene Bugs des alten Ansatzes (LKW-Verkehr fragmentierte eine
  durchgehende Kurve; ein schneller Sweeper wurde komplett übersehen) sowie
  einen S-Kurven/Schikanen-Merge-Bug (Vorzeichenwechsel beendet jetzt immer
  das laufende Ereignis). Siehe "Kurvenmodell, Fortsetzung: Nutzer-Review
  deckt 2 echte Bugs auf, CAN-nativer Kurvendetektor gebaut (2026-09-13,
  selber Tag)".
- **Empirisches Kurvengeschwindigkeitsmodell:** v_peak 7–223 km/h,
  a_lat_peak 0,18–0,83g über **220 nutzerbestätigte Kurven aus 7 Fahrten**
  (Stand 14.09.2026, siehe "Kurvenmodell auf alle CAN-Logs erweitert
  (2026-09-14)"). Alles Alltagsfahrten, keine Grenzbereichsfahrt - **bestätigt
  als Untergrenze das bisherige Literatur-mu-Bracket 1,0–1,3**, ändert nichts
  daran. Ein einzelner GPS/Gyro-only-Referenzpunkt "am Limit" (ESP-Eingriff
  dokumentiert, daher eher obere Range-Grenze als sauberer Messwert) liegt als
  einziger Punkt über der unteren Bracket-Grenze.

## Kraftkreis

- `can_traction_circle.py`: Kraftkreis-Check direkt aus RCM-Signalen
  (`LateralAcc_CAN` + `LongitudinalAcc_CAN`, zeitgleich vom selben Sensor,
  keine Rückrechnung), 7 CAN-Logs, 273.280 Samples.
- Hülle ist erkennbar **nicht kreisförmig** (nach Bereinigung, siehe Korrektur
  unten): max. Bremsen **0,74g**, max. Beschleunigen **0,51g**, max. Quer
  **0,82g**. Physikalisch plausibel (Bremsen nutzt 4 Räder, Beschleunigen bei
  RWD nur die Hinterachse).
- **Korrektur (wichtig):** der ursprüngliche max-Beschleunigungswert (0,57g)
  war durch einen Schaltvorgang verunreinigt (Kupplungs-/Antriebsstrang-Schlag
  beim Wiedereinkuppeln unter Volllast, kein reines Reifentraktionslimit) -
  nach Ausschluss von Schaltfenstern (Kupplung aktiv + 1,0s Nachlauf) sinkt der
  Wert auf 0,51g, die Bremsen/Beschleunigen-Asymmetrie wird dadurch sogar
  **deutlicher** sichtbar (1,18x → 1,32x). Siehe "Kraftkreis-Check, Korrektur:
  der staerkste Radschlupf-Kandidat war ein Schaltvorgang, kein Reifenschlupf
  (2026-09-14, selber Tag)".
- Alles klar unter dem echten Reifenlimit (Alltagsfahrten) - Form-/
  Untergrenzen-Indikation, keine Grenzwertmessung. Zeigt aber, dass die
  Rundenzeit-Simulation mit EINEM gemeinsamen mu für Kurve und Bremse die
  reale Asymmetrie verschenkt (bisher nicht umgesetzt, siehe Spreewaldring-
  Abschnitt).
- Radschlupf-Kandidaten aus den 4 `WheelSpeed_CAN`-Kanälen sind **nicht
  belastbar** - dasselbe Rauschniveau (bis 4,5 km/h Abweichung) tritt auch bei
  unbelasteter Geradeausfahrt ohne jede Beschleunigung auf.

## Bremsmodell

- Reales, datenbasiertes Bremsmodell (229 echte Bremsvorgänge aus
  `BFP_PRE_MZ` + OBD-Tempoabfall, `scripts/braking_model.py`) ersetzt die
  reine Reifenkraftkreis-Theorie für die Längsverzögerung in der
  Rundenzeit-Simulation.
- **`BRAKE_CAP_G=0,4772`** (reales Maximum, ABS-Bremsung) ist Standard-Default
  in `simulate_lap_combined_friction()` - gilt nur für die
  Längsverzögerung, nicht die Kurven-Querbeschleunigung
  (`brake_cap_g=None` erzwingt weiterhin das alte theoretische Verhalten für
  Vergleiche). Effekt: theoretisch (Reifenkraftkreis) 100,67 s, real Median
  (0,154g) +12,70 s, p95 (0,309g) +5,26 s, Maximum (0,477g) +2,07 s.
- Zwei "Bang-Bang"-Glättungen für realistischeres, fahrbares Pedalverhalten:
  1. **Vollgas-Inseln** kürzer als `MIN_ACCEL_HOLD_M=40m` zwischen zwei
     Bremszonen werden zu konstanter Teillast geglättet
     (`smooth_wasted_accel_brake`).
  2. **Isolierte kurze Bremsinseln** (Spiegelfall) werden rückwärts in eine
     sanftere Verzögerung vorverlegt (`smooth_short_brake_spikes`) - die
     Zielgeschwindigkeit am Ende bleibt exakt erhalten.
- Verlässliches Kriterium für eine echte Bang-Bang-Stelle: lokale Extrema in
  v(s) (Vorzeichenwechsel der Steigung), **nicht** das rohe Brems-Flag (das
  flackert normal an jedem Kurvenscheitel, viele Falsch-Positive).
  Systematisch über die GANZE Runde verifiziert, nicht nur am Einzelbeispiel.
- Teillast-Prozentanzeige korrigiert: `throttleFrac` jetzt als Anteil der
  Radkraft im Bereich [`coastAccel()`, `accel_envelope()`] statt Anteil der
  Netto-Beschleunigung (vorher zeigte reines Geschwindigkeit-Halten
  fälschlich 0 % Gas).
- Bekannte Grenzen: `MIN_ACCEL_HOLD_M=40m` ist eine grobe Schätzung (Committing-
  Zeit für Pedalwechsel), kein gemessener Wert. Ein kleines Restrauschen in
  Kurve 1 (Array-Nahtstelle des Optimierungsfensters) bleibt bestehen - drei
  reine Geometrie-Glättungsansätze getestet und verworfen (machten die
  Rundenzeit alle schlechter), Teillast-Glättung bleibt der richtige Hebel für
  diese Fehlerklasse. Siehe "Nachtrag: reales Bremsmodell uebernommen,
  Bang-Bang-Glaettung fuer beide Pedale, systematisch auf der ganzen Runde
  verifiziert (08.09.2026)".

## Schaltzeiten/Zugkraftunterbrechung

- **Erster CAN-Kupplungsdaten-Check** (14.09.2026): reale
  Kupplungsbetätigungsdauer 0,96–2,40 s (Median je Gangpaar 1,12–1,61 s)
  gegen die `ATTACK_SHIFT_S`-Defaults aus [Performance-Modell-Status](performance-model.md)
  (0,15/0,15/0,17/0,23/0,25 s für 1→2 bis 5→6, Literatur-Default für maximale
  Fahrleistung) - **4,5x (5→6) bis 10,7x (1→2) langsamer**. Einordnung: kein
  Widerspruch zu `ATTACK_SHIFT_S`, da alle bisherigen CAN-Logs entspannte
  Alltagsfahrten sind, keine Attack-Fahrt. Siehe "Schaltzeit-Check aus
  CAN-Kupplungsdaten: reale Schaltzeiten 4.5-10.7x langsamer als
  ATTACK_SHIFT_S (2026-09-14, selber Tag)".
- **Neuester Stand, höchste Priorität (18.09.2026):** Kupplungsbetätigungsdauer
  ist nur ein Proxy, **kein direktes Maß für den tatsächlichen
  Vortriebsverlust**. Neues Skript `shift_traction_gap_analysis.py` misst die
  echte Zugkraftlücke direkt aus der Hinterachs-Radgeschwindigkeit
  (angetriebene Räder; die Vorderachse dippt ~0,10s später - Hecktriebler-
  Signatur, Vorderachse spürt den Drehmomentabriss nur zeitversetzt über die
  Fahrzeugverzögerung als Ganzes).

  | Gangpaar | n | Kupplungszeit (Median) | Zugkraftlücke (Median) | Verhältnis |
  |---|---:|---:|---:|---:|
  | 1→2 | 64 | 1,61 s | 0,45 s | 3,6x |
  | 2→3 | 93 | 1,40 s | 0,55 s | 2,6x |
  | 3→4 | 63 | 1,30 s | 0,80 s | 1,6x |
  | 4→5 | 20 | 1,37 s | 1,40 s | 0,98x |
  | 5→6 | 9 | 1,18 s | 0,60 s | 2,0x |

  (249 Hochschaltungen gesamt.)
- **Klarstellung zum Verhältnis mit `ATTACK_SHIFT_S`/dem alten 0,41s-Wert:**
  dieser neueste Eintrag **bestätigt die `ATTACK_SHIFT_S`-Defaults
  (0,15–0,25 s) nicht und aktualisiert sie auch nicht** - auch die
  physikalisch sauberere Zugkraftlücken-Messung bleibt eine Messung an
  entspannter Alltagsfahrt, keine Attack-/Grenzbereichsfahrt, und liegt mit
  0,45–1,40 s weiterhin deutlich über den `ATTACK_SHIFT_S`-Werten.
  `ATTACK_SHIFT_S` bleibt unverändert der Literatur-Default für die
  Hotlap-Simulation, jetzt aber mit einer methodisch saubereren
  (wenn auch weiterhin nicht-Attack-) Referenzgröße als der reinen
  Kupplungszeit. Der frühere pauschale Diagnosewert **0,41 s** (siehe
  [Performance-Modell-Status](performance-model.md)) liegt größenordnungsmäßig
  innerhalb der neuen Zugkraftlücken-Spanne, ist damit aber ebenfalls **nicht
  bestätigt** - andere Messgrundlage (ein Pauschalwert über alle
  Gänge/Situationen vs. Alltagsfahrt-Zugkraftlücke je Gangpaar), keine
  Gleichsetzung.
- **Nebenfund - selbst ertapptes Kupplungsschleifen:**
  `candump-2026-09-12_211833` (5→6 @ t=1048.0s): Kupplungszeit 2,40 s bei nur
  0,40 s echter Zugkraftlücke (Verhältnis 6,0x) - Pedal hing ~1,7 s bei ~10 %
  Restweg fest, obwohl der Schaltvorgang mechanisch längst fertig war.
  Nutzerbestätigt als echter Fahrfehler bei einem hastig abgebrochenen
  Vollgas-Run. Neues Skript `clutch_ride_detection.py` fand **6 ähnliche
  Ausreißer** (Schleifzeit 1,16–1,78 s) über 313 Gangwechsel-Ereignisse - der
  Rest der Verteilung liegt sauber unter 0,58 s.
- Beide neuen Skripte (`shift_traction_gap_analysis.py`,
  `clutch_ride_detection.py`) laufen automatisch in der täglichen Pipeline.
  Bestzeiten-Tracking je Gangpaar+Richtung (`shift_time_best.json`) sowie
  Kurven-Spitzenwert-Tracking (`corner_peak_best.json`, Stand 14.09.2026:
  rechts 0,81g/links 0,82g) laufen ebenfalls automatisch mit, neue Rekorde
  erscheinen als Info-Findings im Pipeline-Report.

## Tankstand

- **FLI-Sensor liest nichtlinear**, unterschätzt systematisch stärker nahe
  vollem Tank. Ground-Truth-Abgleich (03.09.2026, echte Zapfsäulen-Messung):
  bei FLI~15 % Unterschätzung um 2,43 l (~26,7 % relativ), bei FLI~90 %
  Unterschätzung um 4,40 l (~9,8 % relativ) - **kein konstanter Offset**,
  sondern wachsende Unterschätzung zum vollen Tank hin (typisches
  Schwimmer-/Geberverhalten).
- Tankkapazität **45 l** durch den Ground-Truth-Punkt bestätigt (9,11 l
  Restmenge + 35,89 l Nachfüllmenge = 45,00 l, exakt passend).
- Konsequenz: nur die zwei Logs mit echtem Ground-Truth-Wert
  (`2026-09-03 163254`/`165821`) wurden rückwirkend korrigiert (Masse
  +0,9 kg/+1,6 kg) - alle anderen ~60+ Logs bleiben auf der linearen Annahme
  (`level_pct/100*45l`), da der Effekt (<0,3 % der Gesamtmasse) deutlich
  unter der ohnehin bekannten WOT-Modellunsicherheit (5–8 %) liegt.
- **Offen:** nur 2 Ankerpunkte (~15 %, ~90 %) - keine vollständige
  nichtlineare FLI→Liter-Kurve rekonstruierbar, v.a. der Bereich nahe leer
  und 30–70 % fehlen noch. Siehe "Tankstand-Kalibrierung: FLI-Sensor liest
  nichtlinear, unterschaetzt v.a. nahe voll (03.09.2026)".
