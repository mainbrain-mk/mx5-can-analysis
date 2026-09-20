# Fahrleistungsmodell & IMU: aktueller Kenntnisstand

**Status:** lebendes Dokument, wird bei relevanten Änderungen in-place aktualisiert
(nicht mehr als Datums-Snapshot geführt, siehe `docs/README.md`).  
**Zweck:** Portierbare technische Zusammenfassung des im Agenten dokumentierten Modell- und IMU-Kenntnisstands.  
**Abgrenzung:** Das aktive Fahrleistungsmodell (inkl. Teillastkennfeld, Bereifung/Gewicht, 0-Vmax-Simulation und Gang-6-/Vmax-Validierung) ist mit Stand 30.08.2026 dokumentiert. Die IMU-Diagnosemethode (inkl. variabler Handyhalterung mit automatischer Pro-Log-Achsenerkennung) und die Schwingungsanalyse wurden bis 07.09.2026 fortgeschrieben. Diagnosebefunde ändern Fahrzeugparameter nicht automatisch.

## 1. Kurzfazit

- Das Fahrleistungsmodell verwendet einen dynamischen Radradius von **0,2985 m**, eine Achsübersetzung von **2,866**, eine Referenzmasse von **1180,705 kg**, **CdA = 0,647 m²**, einen gekoppelten Antriebswirkungsgrad von **0,93**, **Crr = 0,013** und **ρ = 1,18 kg/m³**. Leergewicht (ohne Fahrer/Tank) ist **1073 kg** und damit nicht mit der Referenzmasse identisch. Die Bereifung (**Nankang NS-R2 205/40/R17**) bestätigt den Radradius unabhängig über die Reifengeometrie (0,2979 m).
- Für maximale Fahrleistung gelten gangindividuelle ATTACK-Zugkraftunterbrechungen von **0,15 / 0,15 / 0,17 / 0,23 / 0,25 s** für 1→2 bis 5→6. Der frühere Pauschalwert **0,41 s** bleibt Diagnosewert, ist aber nicht mehr Default.
- Fahrer-WOT wird nicht allein über eine offene Drosselklappe erkannt. **APP > 90 %** kennzeichnet den Bereich oberhalb der haptischen Pedalraste; APP, ETC, Lambda, MAF, Drehzahl, Geschwindigkeit und Kupplung sind gemeinsam zu prüfen.
- Die Volllastkurve ist im Bereich **4250 bis 6750 min⁻¹** inter-log plausibilisiert. Niedrige Drehzahlen sowie Bereiche oberhalb 7000 min⁻¹ bleiben schwächer abgesichert. Seit 30.08.2026 gibt es zusätzlich ein eigenständiges, messwertbasiertes **Teillastkennfeld** (ETC × Drehzahl → Drehmoment-%, kreuzvalidiert RMSE 7,2 Prozentpunkte), siehe Abschnitt 5.
- **0-Vmax-Simulation und Gang-6-/Vmax-Validierung (29./30.08.2026):** in den 08.08.2026-Logs wurden zwei echte, mehrere Sekunden lange Vmax-Plateaus gefunden (226–236 km/h bzw. 202–230 km/h, Beschleunigung praktisch null). Dort trifft das **unkorrigierte (raw) Modell** die Realität deutlich besser als das sonst verwendete bias-korrigierte Modell (Faktor 0,92) — der Korrekturfaktor aus dem mittleren Geschwindigkeitsbereich extrapoliert nicht in den Vmax-Bereich, siehe Abschnitt 6. Zusätzlich stellte sich am 06.09.2026 heraus, dass ein Teil des früher CdA/η zugeschriebenen ~5–8 %-WOT-Bias auf einen Massenfehler (fehlender 75-kg-Beifahrer in zwei Logs) zurückgeht.
- Die IMU-Methode **MX5_IMU_METHOD_V1.0** ist eine Diagnosemethode. Gierachse und Vertikalrichtung sind hoch plausibilisiert; die absolute Längs- und Querskalierung bleibt diagnostisch. Seit 06.09.2026 kann die Handyhalterung **von Fahrt zu Fahrt wechseln**; die Achszuordnung (inkl. Vertikalachse und Gier-Kanal) wird seither **automatisch pro Log** erkannt (`scripts/imu_orientation.py`) statt über eine global fixe Matrix, siehe Abschnitt 7.
- Für die Halterungsresonanz ist **20,629 Hz** weiterhin nur ein fahrtspezifisch zu prüfender Startkandidat, **kein Notch-Filter freigegeben**. Die Logs vom 27.08.2026 lieferten keine geeigneten stabilen Segmente (**NOT_TESTABLE**). Für die seit 06./07.09.2026 verwendete **starre** Handyhalterung (kein Halterungsarm mehr) ist die bisherige Deutung als lose schwingende Halterungs-Eigenresonanz revidiert — die jetzt nur noch einachsige Signatur spricht eher für eine über den Befestigungspunkt übertragene Fahrzeugschwingung. Gilt ausdrücklich nur für diese konkrete Einbaulage, siehe Abschnitt 8.

## 2. Statuslogik

| Status | Bedeutung im aktuellen Arbeitsstand |
|---|---|
| VALIDiert / aktive Primärevidenz | Für das jeweilige Modell- oder Diagnoseziel aktiv verwendbar |
| Plausibilisiert | Durch Daten gestützt, aber nicht zwingend unabhängig identifiziert |
| Modellannahme | Arbeitswert ohne vollständige empirische Trennung |
| Diagnostisch | Für Sichtung und Vergleich nutzbar, nicht für automatische Parameteränderung |
| Interpoliert | Zwischenwert ohne ausreichende direkte Messstützung |
| NOT_TESTABLE | Datensatz enthält keine ausreichenden Prüfsegmente; weder Bestätigung noch Widerlegung |

## 3. Aktive Fahrzeugparameter

| Parameter | Symbol | Wert | Einheit | Status / Hinweis |
|---|---:|---:|---|---|
| Referenzmasse | m | 1180,705 | kg | verwendet; spezifischer Beladungszustand (Fahrer+Tank), einzelne Logs dürfen eigene Massenannahmen tragen |
| Leergewicht (ohne Fahrer, ohne Benzin) | m_leer | 1073 | kg | Nutzerangabe (30.08.2026); nicht identisch mit der Referenzmasse |
| Dynamischer Radradius | r_dyn | 0,2985 | m | aktiv, datenbasiert plausibilisiert; unabhängig bestätigt über Reifengeometrie (0,2979 m, s. u.) |
| Historischer Radradius | r_dyn,alt | 0,2997 | m | abgelöst, als Referenz erhalten |
| Reifen-Referenzradius | r_ref | 0,2997 | m | verwendet |
| Bereifung | — | Nankang NS-R2, 205/40/R17 | — | Nutzerangabe (30.08.2026), Semi-Slick-Trackday-Reifen; geometrischer Radius 0,2979 m passt zu r_dyn/r_ref |
| Achsantrieb | i_final | 2,866 | 1 | validiert |
| Antriebswirkungsgrad | η | 0,93 | 1 | gekoppelt plausibilisiert, nicht unabhängig identifiziert |
| Luftwiderstandsfläche | CdA | 0,647 | m² | gekoppelt plausibilisiert, nicht unabhängig identifiziert |
| Referenz-Luftdichte | ρ | 1,18 | kg/m³ | Modellannahme |
| Rollwiderstandsbeiwert | Crr | 0,013 | 1 | Modellannahme |
| Erdbeschleunigung | g | 9,81 | m/s² | Konstante |
| Drosselklappe vollständig offen | ETC_open | ca. 86 | ° | validiert, aber kein alleiniger WOT-Nachweis |
| Haptische Pedalraste | APP_raste | 90 | % | aktive Nutzerdefinition |

## 4. Antriebsstrang und Übersetzungen

| Gang | Gangübersetzung | Gesamtübersetzung mit i_final = 2,866 | Status |
|---:|---:|---:|---|
| 1 | 5,087 | 14,579342 | validiert |
| 2 | 2,991 | 8,572206 | validiert |
| 3 | 2,035 | 5,832310 | validiert |
| 4 | 1,594 | 4,568404 | validiert |
| 5 | 1,286 | 3,685676 | validiert |
| 6 | 1,000 | 2,866000 | validiert |

### Schaltmodell

| Wechsel | ATTACK-Zugkraftunterbrechung | Verwendung |
|---|---:|---|
| 1→2 | 0,15 s | Default für maximale Fahrleistung |
| 2→3 | 0,15 s | Default für maximale Fahrleistung |
| 3→4 | 0,17 s | Default für maximale Fahrleistung |
| 4→5 | 0,23 s | Default für maximale Fahrleistung |
| 5→6 | 0,25 s | Default für maximale Fahrleistung |

Weitere Diagnosegrößen:

- Pauschale effektive Momentunterbrechung: **0,41 s**, als Default abgelöst.
- Vollständiger Schalttransient: **ca. 0,62 s**. Nicht zusätzlich zu 0,41 s addieren.
- Median Kupplungsaktivität: **0,49 s**.
- Median Nachstabilisierung: **0,11 s**.
- Gang-0-Dauer, Kupplungsdauer, mechanischer Gesamttransient und Zugkraftunterbrechung sind nicht gleichzusetzen.

## 5. Motor-, Last- und WOT-Modell

### Arbeitsregeln

1. **APP < 90 %:** Lambda liegt typischerweise nahe 1,00.
2. **APP > 90 %:** In passenden thermisch stabilen Lastzuständen erfolgt Anreicherung in Richtung Lambda ca. 0,85.
3. **ETC ≈ 86°:** Bedeutet offene Drosselklappe, nicht automatisch Fahrer-WOT.
4. **WOT-Klassifikation:** APP, ETC, Lambda, MAF, Drehzahl, Geschwindigkeit und Kupplungszustand gemeinsam bewerten.
5. Warmlauf, Übergänge, Schub und besondere Regelzustände müssen separat gefiltert werden.

### Volllast-Drehmomentkurve

| Drehzahl [min⁻¹] | Drehmoment [N·m] | Einordnung |
|---:|---:|---|
| 1000 | 115 | Prüfbedarf |
| 1500 | 140 | Prüfbedarf |
| 2000 | 165 | Prüfbedarf |
| 2500 | 185 | Prüfbedarf |
| 3000 | 198 | Prüfbedarf |
| 3500 | 203 | Prüfbedarf |
| 4000 | 205 | Randzone |
| 4500 | 204 | inter-log plausibilisiert |
| 5000 | 202 | inter-log plausibilisiert |
| 5500 | 198 | inter-log plausibilisiert |
| 6000 | 193 | inter-log plausibilisiert |
| 6500 | 189 | inter-log plausibilisiert |
| 7000 | 184 | Randzone |
| 7200 | 178 | schwach abgesichert |
| 7400 | 172 | schwach abgesichert |
| 7500 | 169 | schwach abgesichert |

Die Volllast-Drehmomentkurve selbst bleibt numerisch unverändert (Tabelle oben). Direkte APP/ETC/Lambda/MAF-WOT-Segmente zur Absicherung ihrer Randbereiche fehlen weiterhin (siehe Abschnitt 12).

### Teillastkennfeld (ETC × Drehzahl → Drehmoment-%, NEU 30.08.2026)

Seit 30.08.2026 existiert ein eigenständiges, messwertbasiertes Teillastkennfeld, das die früher offene Frage "Teillastkennfeld, insbesondere ETC = 60°, direkt messen" über einen bis dahin ungenutzten Kanal statt über gezielte Kalibrierfahrten löst.

| Merkmal | Wert |
|---|---:|
| Datenquelle | `ActualEnginePercentTorque` (CAN-PID), in 14 von 56 Logs vorhanden (64.211 Messwerte) |
| Semantik-Check | über WOT-Segmente: median 89–98 % (p10 81–97 %) → Prozent des Volllast-Drehmoments bei der jeweiligen Drehzahl |
| Datenbasis Kennfeld | 35.331 gefilterte Punkte (Kupplung nicht getreten, keine Bremsung, Drehzahl > 1000 min⁻¹, v > 3 m/s, gültiger Gang) |
| Rasterung | Bin-Mediane, 5° × 250 min⁻¹-Raster, 218 robuste Bins (≥ 3 Punkte), lineare Interpolation / Nearest-Neighbor-Fallback |
| Kreuzvalidierung (leave-one-log-out, 14 Logs) | RMSE **7,2 Prozentpunkte** |
| Physikalische Validierung (2553 Teillast-Beschleunigungssegmente, ≥3 s, ETC 5–80°) | Kennfeld: Korrelation 0,57, RMSE 0,406 m/s² · naiv (Volllast-Annahme überall): Korrelation 0,22, RMSE 1,699 m/s² · direkt gemessen (Teilmenge n=631): Korrelation 0,79, RMSE 0,234 m/s² |

Status: **datenbasiert, kreuzvalidiert**, in `scripts/partial_load_model.py` (Kennfeld-Aufbau) und seither integriert in `scripts/performance_simulation.py` (`accel(etc_deg=...)`, `find_equilibrium_speed()`, `simulate_constant_throttle()`, siehe Abschnitt 6). Einschränkungen: Datendichte bei mittlerem ETC (20–70°) deutlich dünner als bei sehr niedrigem ETC; `ActualEnginePercentTorque`-Semantik nur empirisch über den WOT-Abgleich bestätigt, nicht aus einer Mazda-Spezifikation verifiziert; keine Steigungskorrektur in den Validierungssegmenten.

## 6. Fahrleistungs- und Hochgeschwindigkeitsmodell

- CdA und Wirkungsgrad sind weiterhin nur **gekoppelt** plausibilisiert; eine unabhängige Identifikation beider Größen steht aus. **Wichtige Einschränkung (06.09.2026):** ein Teil des bis dahin CdA/η zugeschriebenen ~5–8 %-WOT-Bias (Verhältnis gemessen/Modell) geht auf einen Massenfehler zurück, nicht auf CdA/η selbst — für zwei Logs vom 05.09.2026 war fälschlich eine SOLO-Fahrt statt eines 75-kg-Beifahrers angenommen worden. Nach Korrektur springt das Volllast-Verhältnis gemessen/Modell im Gesamtdatensatz von Median 0,97 auf **1,00** (n=115 Segmente, Korrelation weiterhin 0,98). Die CdA/η-Kopplungsfrage bleibt grundsätzlich offen, sollte aber nicht mehr unhinterfragt auf der alten 0,94–0,97-Zahl aufbauen.
- Eine 100–200-km/h-Auswertung darf nicht als primäre Kalibrierreferenz dienen, wenn GPS/OBD zeitlich versetzt sind oder ein Grenzpunkt in einer Schaltung liegt.
- Ein historischer Messwert von **222 km/h** wurde im früheren Modellstand als plausibel eingeordnet, weil zusammenhängende Phasen über 215 km/h vorlagen. Das ist inzwischen durch direkt gemessene, mehrere Sekunden lange Vmax-Plateaus überholt (s. u.).
- Historische Simulationsergebnisse von **0–100 km/h 6,29 s**, **100–200 km/h 19,95 s**, **0–200 km/h 26,24 s** und **Vmax 229,9 km/h** sind Entwicklungshistorie, keine aktuellen Messwerte und kein automatisches Kalibrierziel.

### 0-Vmax-Simulation (`scripts/performance_simulation.py`, NEU 29.08.2026)

Integriert das bereits validierte Beschleunigungsmodell (Volllast-Drehmomentkurve, Getriebe-/Achsübersetzung, CdA, Crr, η, Masse) über die Zeit, inkl. Schaltlogik (nächster Gang bei stärkerer Beschleunigung, Redline-Sicherheitsnetz 7500 min⁻¹) und den gangspezifischen ATTACK-Zugkraftunterbrechungen.

| Szenario | 0–100 km/h | 0–200 km/h | Vmax |
|---|---:|---:|---:|
| raw (unkorrigiert) | 5,80 s | 24,95 s | 231,7 km/h |
| bias-korrigiert (Faktor 0,92, aus 44 Volllast-Segmenten) | 6,31 s | 28,46 s | 222,8 km/h |

Kein Traktions-/Launch-Modell (Reifenhaftung bei niedriger Geschwindigkeit in Gang 1) enthalten, bewusst nicht eingebaut (der historische Launch-Wert μ_eff = 0,88 gilt als Diagnosewert, nicht als aktuell bestätigte IMU-Evidenz) — 0–100-Zeiten dadurch ggf. leicht optimistisch. Kein Vergleich gegen eine durchgehende reale 0-Vmax-Fahrt möglich (liegt in den Logs nicht vor; Validierung bleibt indirekt über einzelne Volllast-Segmente).

### Gang-6-/Vmax-Validierung mit Geländekorrektur (`scripts/top_speed_validation.py`, NEU 29./30.08.2026)

6 lange Gang-6-WOT-Segmente (≥ 3 s, muss mind. einmal 170 km/h erreichen) in 3 Logs (135–201 km/h) zeigten durchgehend noch positive Beschleunigung (+0,46 bis +0,90 m/s²) — in diesem Teildatensatz kein Geschwindigkeits-Plateau. Geländekorrektur (DGM-basierte mittlere Steigung entlang GPS-Track, `elevation_model.py`) verbessert die RMSE gegenüber unkorrigiert um 16 % (0,164 statt 0,195 m/s²); am deutlichsten bei stärkerem Gefälle (−1,36 %: Modell ohne Korrektur 0,481 m/s² vs. gemessen 0,844 m/s², mit Korrektur 0,615 m/s²).

**Echte Vmax-Plateaus** wurden separat in den 08.08.2026-Logs gefunden (Fund über neuen WOT-Fallback "ETC_only", ETC_ACT ≥ 85°, nur für die kurzen `CSVLog_*`-Logs ohne APP/AFR_MZ nötig; als Diagnosewert ohne unabhängige Lambda-Bestätigung gekennzeichnet, gemäß bestehender Vorgabe "ETC allein ist kein verlässlicher WOT-Nachweis"):

| Log | Zeitfenster | Dauer | v-Bereich | a_gemessen | Geländekorrektur (30.08.2026 nachgezogen) |
|---|---|---:|---|---:|---|
| `CSVLog_20260808_145611` | t=875–922 s | 47 s | 226–236 km/h | +0,037 m/s² | −0,42 % (leicht abschüssig) |
| `CSVLog_20260808_214117` | t=861–954 s | 93 s | 202–230 km/h | +0,052 m/s² | +0,11 % (praktisch eben) |

Beide gegengeprüft: ETC_ACT=86° (voll offen) durchgehend, TM_GEST=6, Drehzahl 5900–6050 min⁻¹ (gut abgesicherter Drehmomentkurven-Bereich), danach klarer Gaswegnahme-Abfall; zusätzlich per unabhängiger GPS-Positionsdifferenzierung bestätigt (229,3/219,9 km/h GPS vs. 232,5/224,0 km/h OBD, im Rahmen des 1-Hz-GPS-Rauschens konsistent).

**Wichtiger, weiterhin offener Befund:** Bei diesen und weiteren Segmenten mit v_max ≥ 215 km/h (33 Segmente insgesamt) trifft das **unkorrigierte (raw) Modell die Realität deutlich besser** als das sonst verwendete bias-korrigierte Modell:

| Modellvariante | mittlere Abweichung (gemessen − Modell) | RMSE |
|---|---:|---:|
| raw (Faktor 1,0) | −0,018 m/s² | 0,083 m/s² |
| bias-korrigiert (Faktor 0,92) | +0,101 m/s² (Modell unterschätzt jetzt) | 0,130 m/s² (schlechter als roh) |

Der 8–9 %-Bias-Korrekturfaktor aus dem mittleren Geschwindigkeitsbereich (130–200 km/h) **extrapoliert nicht in den Vmax-Bereich (> 215 km/h)** — der zugrunde liegende Fehler (vermutlich CdA und/oder η) skaliert vermutlich nicht gleichförmig mit v² über den gesamten Geschwindigkeitsbereich. Konsequenz: die "bias-korrigiert"-Kurve von `performance_simulation.py` ist nahe Vmax vermutlich zu pessimistisch, die "raw"-Kurve dürfte dort näher an der Realität liegen. Nicht weiter aufgelöst (kein geschwindigkeitsabhängiger Korrekturfaktor implementiert), weiterhin offener Punkt (siehe Abschnitt 12). Grade-adjustierte Vmax-Schätzungen aus den einzelnen Gang-6-Segmenten streuen zwischen 215,9 und 232,9 km/h (bias-korrigiert) — spiegelt die Steigungsabhängigkeit, nicht Modellunsicherheit.

**Einschränkung:** ein Teil der ursprünglichen Plateau-Fenster lag zunächst in einer DGM-Datenlücke im Berlin-Randgebiet; nach Nachladen der verfügbaren Kacheln (30.08.2026) sind beide Plateaus jetzt (teilweise) geländekorrigiert, einzelne Teilabschnitte langer Läufe liegen weiterhin in einer echten Rest-Datenlücke.

### Teillast-Simulation bei konstantem ETC (`simulate_constant_throttle()`, NEU 30.08.2026)

Nutzt das Teillastkennfeld (Abschnitt 5) statt der Volllast-Kennlinie, gleiche Schaltlogik, ohne zusätzlichen Bias-/Traktionsfaktor (das Kennfeld ist bereits direkt an echten Beschleunigungsdaten validiert):

| ETC | 0–100 km/h | 0–200 km/h | Gleichgewicht Gang 6 |
|---|---:|---:|---:|
| 30° | 9,73 s | nicht erreicht (195,8 km/h nach 90 s) | 200,4 km/h |
| 50° | 7,82 s | 44,58 s | 214,1 km/h |
| 70° | 7,12 s | 34,01 s | 218,9 km/h |

Konstantes ETC über die ganze Simulation ist eine bewusste Vereinfachung (reale Fahrer variieren den Pedalweg kontinuierlich, z. B. in Kurvenausfahrten); für eine echte Rundenzeit-/GPS-Track-Simulation wäre ein zeitlich variabler ETC-Verlauf nötig (nicht Teil dieser Änderung).

## 7. IMU-Methode MX5_IMU_METHOD_V1.0

### Zeitraster und Altersgrenzen

| Größe | Wert |
|---|---:|
| Ausgaberaster | 50 Hz |
| Zeitschritt | 0,02 s |
| Beschleunigung maximal alt | 0,05 s |
| Rotation maximal alt | 0,35 s |
| OBD-Kontext maximal alt | 0,65 s |
| GPS-Kontext maximal alt | 1,60 s |

Das 50-Hz-Raster ist eine gemeinsame Ausgabetaktung. Daraus darf keine native 50-Hz-Messrate für OBD oder GPS abgeleitet werden.

### Achstransformation Smartphone → Fahrzeug

**Gilt als Referenz-/Defaultfall für die feste Y-vertikal-Halterung (alle Logs vor 2026-09-06).** Seit 06.09.2026 kann die Handyhalterung **von Fahrt zu Fahrt wechseln** (bestätigt: eine neue Halterung mit Z statt Y als Vertikalachse trat auf). Die Achszuordnung erfolgt seither **automatisch pro Log** über das neue Modul `scripts/imu_orientation.py` (`detect_vertical_axis()` per Gravitationsvektor im Stillstand, mit Fallback auf Y falls nicht eindeutig bestimmbar; `horizontal_axes()` liefert die beiden übrigen Achsen in fester Reihenfolge X<Y<Z; `yaw_channel()` den zugehörigen `RotationRate`-Kanal) — nicht mehr über eine global fixe Matrix. Für alle Logs vor 2026-09-06 reproduziert die neue Pro-Log-Erkennung regressionsfrei exakt die bisherigen Ergebnisse; Verbraucher-Skripte (`brake_event_analysis.py`, `corner_event_analysis.py`, `grip_estimation.py`) wurden entsprechend umgebaut. Die unten abgedruckte Matrix und `yaw_right = -RotationRateY` bleiben für den Y-vertikal-Fall gültig; das Vorzeichen `GYRO_SIGN_SCALE = -1,0` ist bisher nur für Y und Z als erkannte Vertikalachse empirisch bestätigt, **nicht für X** (bei einem künftigen X-vertikal-Log die Korrelation gegen die GPS-Kursänderung erneut prüfen statt annehmen).

```text
a_long = +0.954017155*AccelerationX
         -0.007262847*AccelerationY
         +0.299664011*AccelerationZ

a_lat  = -0.299740966*AccelerationX
         -0.031697045*AccelerationY
         +0.953493918*AccelerationZ

a_vert = -0.002573383*AccelerationX
         +0.999471134*AccelerationY
         +0.032416497*AccelerationZ

yaw_right = -RotationRateY
```

Einordnung:

- `yaw_right`: hoch plausibilisiert gegen GPS-Kursrate.
- Vertikalachse: hoch plausibilisiert über Gravitationsrichtung, nahe Smartphone +Y.
- `a_long` und `a_lat`: diagnostisch; absolute Skalierung noch nicht validiert.
- `a_lat_LP4` darf nicht als Haftgrenze verwendet werden.
- `a_long_LP4` darf keine automatische Drehmoment- oder Massenanpassung auslösen.

### Filterpipeline

| Kanal | Filter | Parameter | Freigabe |
|---|---|---|---|
| Beschleunigung, Fahrdynamik | Butterworth-Tiefpass | 4. Ordnung, 4 Hz, nullphasig offline | aktiv |
| Gier | Butterworth-Tiefpass | 4. Ordnung, 2 Hz, nullphasig offline | aktiv |
| Halterungsresonanz | IIR-Notch | f0 fahrtspezifisch, Startkandidat 20,629 Hz, Q=15 | nur bei CONFIRMED oder SHIFTED |

Rohdaten und Residuen sind für Rückverfolgbarkeit und Qualitätskontrolle aufzubewahren. Residuen sind nicht als Fahrzeugdynamik zu interpretieren.

Hinweis: Die Bezeichnung „Halterungsresonanz" ist der historische Sammelbegriff aus den Logs vor 06.09.2026 (lose Halterung, tri-axiale Signatur). Für die seit 06./07.09.2026 verwendete starre Einbaulage ist die Deutung revidiert, siehe Abschnitt 8.

## 8. Resonanz- und Schwingungsanalyse

### Prüfverfahren

Geeignete 2-s-Referenzfenster erfordern:

- Geschwindigkeit ≥ 20 km/h
- Betrag des Geschwindigkeitstrends ≤ 0,5 km/h/s
- Geschwindigkeitsspanne ≤ 2 km/h
- Betrag der GPS-Gierrate ≤ 1,5 °/s
- konstanten Gang

Zusammenhängende Segmente müssen mindestens 8 s dauern. Danach wird je Achse eine Welch-PSD berechnet und das Band **19,0 bis 22,5 Hz** geprüft.

### Aktueller Befund

- Legacy-Log 25.08.2026: 8 stabile Segmente, 20,629 Hz als Referenzkandidat.
- Logs 26.08.2026: 15 stabile Segmente, 74 überlappende 4-s-Fenster und 30 Filterversuche. Arbeitsklassifikation: 7 `WEAK_OR_BROAD`, 7 `MODERATE_CANDIDATE`, 1 `STRONG_CANDIDATE`.
- Trotz einzelner Kandidaten ist kein Notch freigegeben; produktive Störsubtraktion bleibt gesperrt.
- Log 27.08.2026 08:16:20: 0 stabile Segmente, Status `NOT_TESTABLE`.
- Log 27.08.2026 17:03:39: 0 stabile Segmente, Status `NOT_TESTABLE`.
- **Ab 06.09.2026 variable Handyhalterung** (siehe Abschnitt 7): neue Position mit Z statt Y als Vertikalachse. Resonanzsuche läuft seither über die pro Log automatisch erkannten Achsen.
- **Logs 07.09.2026** (`082100`, `123731`): Resonanz nur noch auf **einer** statt wie zuvor auf allen drei Achsen (082100: Y=21,1 Hz; 123731: Z=18,3 Hz), zeitlich passend zur seit 06.09.2026 geänderten Halterung.
- **Theorie revidiert (07.09.2026, Nutzer-bestätigt):** das Handy ist bei dieser Einbaulage jetzt starr mit dem Interieur verbunden, optisch ist kein Schwingen einer Halterung mehr erkennbar (vorher schon sichtbar). Die bisherige Deutung als lose schwingende Halterungs-Eigenresonanz (tri-axiale Signatur, da eine frei schwingende Masse alle Richtungen anregt) passt für diese Einbaulage nicht mehr. Die jetzt einachsige Resonanz ist für eine reale, räumlich lokalisierte **Fahrzeug-Struktureigenmode**, die direkt über den Befestigungspunkt übertragen wird, plausibler (regt nicht alle Richtungen gleich an). **Gilt ausdrücklich nur für genau diese seit 06./07.09.2026 verwendete starre Einbaulage** (kein Halterungsarm) — keine allgemeine Regel für beliebige zukünftige Mounts. Bei jeder erkennbaren Änderung der Einbaulage (andere Handyposition/-orientierung, erneuter Halterungsarm) ist die Einzelachsen- vs. tri-axial-Frage für diesen neuen Mount erneut zu prüfen, nicht automatisch zu übernehmen. Notch-Freigabekriterien und Freigabestatus (Abschnitt 7, Filterpipeline) sind davon unberührt: weiterhin **kein Notch freigegeben**, keine ausreichenden stabilen Testsegmente vorhanden.
- Die Methode (Filterpipeline, Prüfverfahren) blieb unverändert; es erfolgte keine Fahrzeugparameteränderung.

## 9. Kurven- und Ereignislogik

| Klasse | Primärregel | Zusatzbedingung / Hinweis |
|---|---|---|
| Geradeaus | `abs(yaw_right_LP2) < 2 °/s` | GPS-Kursrate plausibel; 2-s-Fenster |
| Rechtskurve | `yaw_right_LP2 > 3 °/s` | GPS-Kursrate > 0 |
| Linkskurve | `yaw_right_LP2 < -3 °/s` | GPS-Kursrate < 0 |
| Zügige Kurve | `abs(a_lat_ref) ≥ 0,12 g` | alternativ v ≥ 80 km/h und abs(GPS-Gier) ≥ 4 °/s |
| Gerade Beschleunigung | `a_long_LP4 > 0,35 m/s²` | abs(yaw) < 2 °/s, Speed steigend, Bremse niedrig |
| Gerade Verzögerung | `a_long_LP4 < -0,35 m/s²` | abs(yaw) < 2 °/s; Schub, Bremse und Schaltung trennen |

Querbeschleunigungsreferenz:

```text
a_lat_ref = (GPS_speed_kmh / 3.6) * radians(GPS_yaw_rate_deg_s)
```

GPS-Ereignisse sollen nur bei horizontaler Ungenauigkeit ≤ 20 m verwendet werden.

## 10. Qualitäts- und Freigabestufen

| Stufe | Mindestanforderung | Zulässige Nutzung |
|---|---|---|
| CANDIDATE | ≥ 1 gültiges 2-s-Fenster | Sichtung |
| PLAUSIBLE | ≥ 2 gleichgerichtete Fenster | qualitativer Vergleich |
| USABLE | ≥ 3 Fenster und Dauer ≥ 6 s | Kurvenvergleich |
| CALIBRATION_REFERENCE | langes sauberes Ereignis, hohe Gierkorrelation, gute GPS-Genauigkeit, Wiederholung in weiterem Log | Methodenvalidierung nach manueller Freigabe |
| REJECTED | unplausibel, Datenlücke oder schlechte Referenz | keine Modellwirkung |

Auch `USABLE` erlaubt keine automatische Fahrzeugparameteränderung.

## 11. Bekannte Daten, Annahmen und Grenzen

### Bekannte bzw. aktiv verwendete Daten

- Gang- und Achsübersetzungen.
- Gangindividuelle ATTACK-Schaltzeiten für maximale Fahrleistung.
- APP-Pedalraste bei 90 % als Nutzerdefinition.
- Gier-Vorzeichen und Vertikalrichtung der IMU.
- Methodische Altersgrenzen, Filterparameter und Ereignisregeln.
- Datenbasiertes Teillastkennfeld (ETC × Drehzahl → Drehmoment-%) aus `ActualEnginePercentTorque`.
- Leergewicht 1073 kg (ohne Fahrer/Tank) und Bereifung Nankang NS-R2 205/40/R17 als Nutzerangaben.
- Automatische Pro-Log-Erkennung der IMU-Vertikalachse und des Gier-Kanals (`scripts/imu_orientation.py`) für die seit 06.09.2026 variable Handyhalterung.

### Modellannahmen oder gekoppelte Größen

- Luftdichte 1,18 kg/m³.
- Rollwiderstand 0,013.
- CdA 0,647 m² und η 0,93 sind gekoppelt, nicht unabhängig identifiziert.
- Historischer Launch: μ_eff = 0,88, Traktionsfaktor im ersten Gang 0,90 und gemittelte Launch-Beschleunigung 5,54 m/s² gelten als starke historische Evidenz, sind aber nicht aus der aktuellen IMU-Methode neu bestätigt.
- Kupplungs-Anfahrmodell mit Exponent p = 0,5 bleibt Annahme; historischer Reibpunkt 54 % ist als Evidenz dokumentiert.

### Harte methodische Grenzen

- Keine automatische Fahrzeugparameteränderung aus IMU-Diagnosen.
- Keine künstliche native 50-Hz-Rate für OBD oder GPS behaupten.
- Kein Notch ohne erneute Resonanzprüfung pro Log.
- Keine Haftgrenze aus `a_lat_LP4`.
- Keine Drehmoment- oder Massenanpassung aus `a_long_LP4` ohne gezielte Kalibrierfahrt.
- Physikalisch unplausible 20-ms-Spitzen verwerfen bzw. über geeignete 2-s-Fenster aggregieren.
- Den 0,92-Bias-Korrekturfaktor aus dem 130–200-km/h-Bereich nicht auf den Vmax-Bereich (> 215 km/h) anwenden — dort ist er nachweislich schlechter als unkorrigiert (siehe Abschnitt 6).
- Bei neuem Log vor gewichtsabhängigen Berechnungen das tatsächliche Gesamtgewicht (Fahrer + Zuladung) erfragen statt pauschal SOLO anzunehmen.

## 12. Priorisierte offene Punkte

1. **CdA und Wirkungsgrad entkoppeln**  
   Benötigt kontrollierte Hochgeschwindigkeits- oder Ausrollmessungen mit belastbaren Umwelt- und Streckendaten. Ein Teil des früher hierfür verantwortlich gemachten ~5–8 %-WOT-Bias erwies sich am 06.09.2026 als reiner Massenfehler in zwei Logs (fehlender 75-kg-Beifahrer, siehe Abschnitt 6) — die CdA/η-Kopplung selbst bleibt trotzdem ungelöst.

2. **Absolute IMU-Längs- und Querskalierung validieren**  
   Benötigt gezielte Geradeaus-Beschleunigungs-/Bremsfahrten sowie wiederholbare Kurven mit guter GPS-Referenz.

3. **Halterungsresonanz-/Fahrzeugschwingungs-Hypothese reproduzierbar bestätigen oder verwerfen, pro Halterungskonfiguration**  
   Die Deutung wurde am 07.09.2026 für die aktuell starre Einbaulage (kein Halterungsarm, seit 06./07.09.2026) revidiert — plausibler jetzt eine einachsige Fahrzeug-Struktureigenmode statt einer Halterungs-Eigenresonanz (siehe Abschnitt 8). Das bleibt aber im strengen Sinn ungetestet: keine der bisherigen Fahrten lieferte genug stabile Geradeaussegmente (Logs 27.08.2026 waren `NOT_TESTABLE`). Da die Halterung seit 06.09.2026 von Fahrt zu Fahrt wechseln kann, ist die Frage nicht mehr einmalig zu klären, sondern bei jeder neuen Einbaulage erneut zu prüfen. Bis dahin weiterhin kein Notch freigegeben.

4. **Teillastkennfeld, insbesondere ETC = 60° — erledigt (30.08.2026), Restlücken bleiben**  
   Datenbasiertes ETC × Drehzahl-Kennfeld aus `ActualEnginePercentTorque` (14/56 Logs) umgesetzt und kreuzvalidiert (RMSE 7,2 Prozentpunkte), siehe Abschnitt 5. Offen bleibt: dünnere Datendichte bei mittlerem ETC (20–70°) und keine unabhängige Bestätigung der Kanal-Semantik aus einer Mazda-Spezifikation.

5. **Volllastkurve an den Rändern absichern**  
   Benötigt saubere WOT-Fenster unter 4000 min⁻¹ und oberhalb 7000 min⁻¹.

6. **Multi-Rate-Synchronisation weiter absichern**  
   GPS-, OBD- und IMU-Signale kanalweise auf Zeitversatz, Altersgrenzen und Schaltgrenzpunkte prüfen.

7. **Aktuellen Modellstand und IMU-Master konsolidieren**  
   Die Fahrzeugmodell-Datei und die beiden v1.1-Schwingungsfortschreibungen sollten in einem eindeutigen, versionierten Master mit Changelog zusammengeführt werden.

8. **Geschwindigkeitsabhängigen Bias-Korrekturfaktor statt eines globalen Faktors entwickeln**  
   Der pauschale 8–9 %-Korrekturfaktor aus dem 130–200-km/h-Bereich trifft den gemessenen Vmax-Bereich (> 215 km/h) nicht — dort ist das unkorrigierte Modell näher an der Realität (siehe Abschnitt 6). Benötigt eine geschwindigkeitsabhängige statt einer konstanten Korrektur.

## 13. Empfohlene Verwendung außerhalb des Agenten

- Dieses Dokument als lesbare Einstiegsebene nutzen.
- Für Berechnungen weiterhin die numerischen Masterdateien verwenden.
- Jede Änderung mit Datum, Ausgangswert, neuem Wert, Begründung, erwarteter Wirkung und Validierungsnachweis dokumentieren.
- Diagnoseausgaben, Annahmen und aktive Modellparameter strikt getrennt halten.
- Primärlogs unverändert archivieren; Ableitungen und Filterausgaben versionieren.

## 14. Quellen und Rückverfolgbarkeit

Diese Markdown-Datei wurde aus folgenden internen Arbeitsständen konsolidiert:

1. `MX5_Modellstand.xlsx`, aktives Fahrzeug- und Fahrleistungsmodell, Referenz-ID `turn1search52`.
2. `MX5_IMU_Auswertungsmethodik_v1.0.xlsx`, normative IMU-Diagnosemethode, Referenz-ID `turn1search54`.
3. `MX5_IMU_Schwingungsanalyse_Gesamtstand_v1.1 3.xlsx`, Fortschreibung für Log 27.08.2026 08:16:20, Referenz-ID `turn1search59`.
4. `MX5_IMU_Schwingungsanalyse_Gesamtstand_v1.1 4.xlsx`, Fortschreibung für Log 27.08.2026 17:03:39, Referenz-ID `turn1search60`.

> Hinweis: Die Referenz-IDs dienen der Herkunftsdokumentation im Agentenkontext. Außerhalb dieses Kontexts sind die Dateinamen die primären Such- und Archivschlüssel.
