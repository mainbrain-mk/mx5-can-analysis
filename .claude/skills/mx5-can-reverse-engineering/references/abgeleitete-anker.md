# Abgeleitete (synthetische) Anker — Katalog

Ein abgeleiteter Anker ist eine Referenzgröße, die nicht direkt gemessen, sondern aus
bereits validierten Signalen über eine physikalische Beziehung berechnet wird. Damit
lassen sich Signale finden, für die es weder OBD-DID noch direktes DBC-Gegenstück gibt.
Alle Anker werden **offline** aus vorhandenen Logs berechnet — keine Aufnahmen im Auto.

Fahrzeug: MX-5 ND RF G184, **Heckantrieb, Handschalter**. Vorderräder sind nicht
angetrieben → beim Beschleunigen die bessere Geschwindigkeitsreferenz; beim Bremsen
schlupfen alle vier.

**Parameter** (Spurweiten, Radstand, Getriebe-/Achsübersetzung, dynamischer
Rollradius, Masse, cw·A, Lenkübersetzung) aus dem Fahrleistungsmodell des Projekts
(`docs/status/performance-model.md`, `performance_simulation.py`) übernehmen, hier
bewusst keine Zahlen. `can_anchor_sweep.py` hat eigene Konstanten (`WHEELBASE_M`,
`TRACK_M`, Lenkübersetzung 15,5). Weicht etwas ab, gilt das Fahrleistungsmodell. Wo
ein Parameter unsicher ist: Anker nur für die **Form** nutzen.

**Viele Anker gibt es schon fertig** in `scripts/can_anchor_sweep.py` (`anchors(fr, g, o)`,
10-Hz-Raster): gemessene Kanäle (`ws1-4`, `ws1_acc` …, `yaw`, `lat`, `lon`, `steer`,
`rpm`, `clutch`, `gear`, Temperaturen, OBD-Werte wie `oiltemp`, `massairflow`,
`lambdacommanded`) und abgeleitete (`dvdt`, `slope`, `slip_rear_front`, `slip_ratio`,
`ws_front_lr`, `ws_rear_lr`, `yaw_wheels`, `yaw_model`, `yaw_dev`, `curvature`,
`lat_kin`, `gear_ratio`, `power_proxy`, `fuel_rate`, `inst_cons`, `fuel_cum`,
`dist_cum`, `trip_avg_speed`, `trip_avg_cons`, `t_since_start`, `t_engine_on`, `jerk`,
`abs_*`). Erst dort nachsehen, dann neu bauen. Neue Anker gehören als Erweiterung in
`anchors()`, damit Sweep und `can_field_inspect.py` sie automatisch nutzen.

**Geschwindigkeitsskala:** `VehicleSpeed` liest 2,9 % über GPS. Für Anker, die die
**Skala** tragen sollen (Strecke, Weg-/Verbrauchszähler, Beschleunigung absolut), ODO-
Inkremente (`C001_ODO`) oder GPS verwenden. `dvdt`/`dist_cum` im Sweep basieren auf
`VehicleSpeed`, taugen also für die Form, nicht für die letzte Skalenstelle.

## Inhalt

1. Allgemeine Regeln
2. Kinematik (A1–A3)
3. Querdynamik / Lenkung (A4–A5)
4. Antriebsstrang (A6–A8)
5. Schlupf & Regelsystem-Ereignisse (A9–A10)
6. Integrale & Zähler (A11)
7. GPS und Handy-IMU (A12)
8. Kombinationsmuster: Konsens, Residuum, Regime, Mehrfachregression

---

## 1. Allgemeine Regeln

- **Glätten vor Ableiten.** Ableitungen verstärken Quantisierungsrauschen der
  Raddrehzahlen massiv. Savitzky-Golay (Fenster ≈ 0,3–0,5 s, Ordnung 2) oder
  `filtfilt` (nullphasig → kein künstliches Lag). Kausale Filter erzeugen Lag → bei
  der Lag-Suche berücksichtigen.
- **Gültigkeitsmaske mitliefern** (bool-Serie gleicher Zeitbasis). Nur innerhalb
  korrelieren. Masken sind selbst wiederverwendbare Regime-Anker (Abschnitt 8).
- **Mindestgeschwindigkeit.** Alles mit Division durch v erst ab v ≳ 15–20 km/h
  (Startwert, an Raddrehzahl-Auflösung anpassen).
- **Provenienz notieren**: Formel + Eingangssignale + deren Status
  (vermutet/plausibel/bestätigt). Der Anker erbt den schwächsten Status.
- **Unabhängigkeit prüfen**: Liegt ein Eingangssignal in derselben CAN-ID wie der
  Kandidat, ist ein Treffer weniger aussagekräftig (evtl. dieselbe Quelle, ggf.
  sogar dasselbe Feld in anderer Skalierung).

## 2. Kinematik

**A1 – Längsbeschleunigung kinematisch**
`a_x,kin = d/dt v_VA`, mit `v_VA = (v_VL + v_VR)/2` (Vorderachse).
- Maske: kein ABS-Verdacht (A10), v > v_min.
- Findet: Längsbeschleunigungssensor, Bremsdruck (nur Maske „bremst"),
  Motormoment-Größen (Maske „eingekuppelt & Gang konstant").
- Hinweis: Ein Beschleunigungs*sensor* misst `a_x,kin + g·sin(θ)` (Steigung) —
  auf Straßen mit Gefälle bleibt ein Offset-/Driftanteil, siehe A12/Residuen.

**A2 – Gierrate aus Raddrehzahldifferenz**
`ψ̇_rad = (v_VR − v_VL) / b_V` (Vorderachs-Spurweite; Linkskurve positiv).
- Maske: v > v_min, |Lenkwinkel| klein (bei großem Einschlag verfälscht die
  Radgeometrie die Differenz), kein Schlupf.
- Findet/prüft: Gierrate (falls `YawRate_Raw` fehlt oder verifiziert werden soll).
- Hinterachse nur mit Schlupf-Maske (angetrieben, Differenzial/Schlupf).
  **Achtung:** `yaw_wheels` in `can_anchor_sweep.py` nutzt die **Hinterachse**
  (`ws3 − ws4`); die Vorderachs-Variante ad hoc bilden (`ws1 − ws2`).

**A3 – Querbeschleunigung kinematisch** (Sweep: `lat_kin`, in g)
`a_y,kin = v · ψ̇` (ψ̇ aus `YawRate_Raw` oder A2).
- Maske: quasi-stationäre Kurvenfahrt (|dψ̇/dt| klein), kein Drift.
- Findet: Querbeschleunigungssensor (DSC-Sensorcluster sitzt meist in derselben
  Nachricht wie die Gierrate → Nachbarbytes zuerst prüfen). `Lateral_Acc_Raw`/
  `YawRate_Raw` sind bereits gegen das OBD-Lenkmodell bestätigt.

## 3. Querdynamik / Lenkung

**A4 – Lenkradwinkel aus Einspurmodell** (Sweep: `yaw_model`, `yaw_dev`)
`δ_H ≈ i_L · ( L·ψ̇/v + EG·a_y )` (L Radstand, EG Eigenlenkgradient, i_L Lenkübersetzung).
- Maske: |a_y| < ~3 m/s² (linearer Bereich), v > v_min, stationär.
- Ohne EG: nur `L·ψ̇/v` → Form stimmt, Skala nicht exakt → nur Feldfindung.
- Das kalibrierte Lenkmodell steht in `steering_lateral_model.py`
  (sättigende Verstärkung k(Winkel)), Lenkwinkel selbst ist bekannt
  (`Steering_Wheel_Absolute_Angle`, 0x082). `yaw_dev` (Ist − Modell) ist der
  Kandidat für DSC-interne Über-/Untersteuergrößen.
- Vollausschlag/Vorzeichen: Rangier-Episoden im Bestand suchen (Stillstand/
  Schrittgeschwindigkeit mit großem Einschlag); Vorzeichen zusätzlich aus A2/A3
  in Kurven (Linkskurve → ψ̇ > 0).

**A5 – Lenkwinkelgeschwindigkeit**
`d/dt δ_H` aus einem gefundenen Lenkwinkel → oft existiert ein separates
Geschwindigkeitsfeld in derselben Nachricht; gleich mitsuchen. So gefunden:
`SteeringRate_Abs_maybe` (0x082, Betrag 12 Bit + Richtungsbit), r=0,988–0,998 in
allen 28 Logs. Gegen `|d/dt δ_H|` testen, nicht vorzeichenbehaftet.

## 4. Antriebsstrang

**A6 – Gesamtübersetzung / Gangerkennung** (Sweep: `gear_ratio` = rpm/v)
`k = (n_mot · 2π/60 · r_dyn) / v_HA`
- Histogramm von k zeigt pro Gang ein scharfes Cluster (= i_Gang · i_Achse).
- Daraus: **Gang-Enum-Anker** (Cluster-Zuordnung), **„eingekuppelt"-Maske**
  (k liegt in einem Cluster), **„Kupplung/Leerlauf"-Maske** (k liegt dazwischen
  oder n_mot ≈ Leerlauf bei v > 0).
- Findet: Gangsignal/Schaltempfehlung, Kupplungsschalter, Neutralschalter.
  Matching per Kontingenztabelle, nicht Pearson.
- Bekannt: `MT_Gear_Actual`/`MT_Gear_Status` (0x165, 0x0FD), `ReverseGear` (0x445
  Bit 7), `Clutch_Pedal_Position_raw`. Achtung, `MT_Gear_Actual`=7 (Rückwärts)
  erscheint nur bei voll eingekuppeltem Gang.

**A7 – Motordrehzahl aus Raddrehzahl**
`n_mot,kin = v_HA · k_Gang / (2π/60 · r_dyn)` innerhalb „eingekuppelt".
- Prüft ein vermutetes Drehzahlfeld unabhängig von OBD (hohe Rate, kein Polling-Lag).

**A8 – Rad-/Motormoment aus Fahrwiderstand**
`F_x = m·a_x,kin + F_Roll + ½·ρ·cw·A·v² + m·g·sin(θ)`,
`M_mot ≈ F_x · r_dyn / (k_Gang · η)`.
- Direkt aus dem bestehenden Fahrleistungsmodell nutzbar (Teillastmodell aus
  `ActualEnginePercentTorque`, `partial_load_model.py`).
- Warnung aus 0x200 Byte4-5: R² 0,5–0,6 gegen kinematisches Moment, aber 0,9 gegen
  OBD-%-Moment → es war KEIN Motormoment. Das kinematische Moment reicht nicht als
  alleiniger Beleg.
- Maske: eingekuppelt, Gang konstant, keine Bremse, kein Schlupf.
- η und θ unsicher → Anker für die **Form** von Moment-/Last-Feldern
  (Ist-Moment, Soll-Moment, Last); Skala aus OBD-Last/-Moment, falls vorhanden.

## 5. Schlupf & Regelsystem-Ereignisse

**A9 – Antriebsschlupf (TCS/DSC-Kandidat)** (Sweep: `slip_rear_front`, `slip_ratio`)
`s_A = (v_HA − v_VA) / max(v_VA, v_min)`
- Ereignismaske: `s_A > s_krit` für ≥ 2 Frames (Startwert s_krit ≈ 0,05–0,10, tunen).
- Findet: TCS-/DSC-Eingriffsflag, Momentenreduzier-Anforderung (dann oft ein
  kontinuierliches Feld, das in genau diesen Fenstern vom Fahrerwunsch abweicht).
- Schon gefunden: `TCS_Active_maybe` (0x211 Bit 40), `TCS_TorqueRequest_maybe`
  (0x211 23|16), `TCS_RequestActive_maybe` (Bit 53). Diese jetzt als Masken nutzen.
- Nicht jeder Schlupf ist ein Eingriff: Heimfahrt `15_171047` t=708–745 s hatte
  echten Hinterradschlupf (bis 9 km/h) ohne jedes Eingriffsbit.

**A10 – Bremsschlupf je Rad (ABS-Kandidat)**
`v_ref = max(v_i)` (plausibilisiert gegen integriertes a_x),
`λ_i = (v_ref − v_i) / v_ref`
- Ereignismaske: `λ_i > λ_krit` (Startwert ≈ 0,10–0,15) während Bremsmaske.
- Findet: ABS-aktiv-Flag, ggf. je Rad ein Bit (dann vier Masken, vier Bits).
- Schon gefunden: `ABS_Active` (0x211 Bit 42), `HighDecel_maybe` (0x211 Bit 43,
  ~0,6 g). Gegen den Rest **derselben** Bremsung vergleichen
  (`can_event_bit_diff.py --window/--baseline`).
- Seltene Ereignisse → Ereignis-Matching (F1/Jaccard) statt Korrelation.
- Nur auswerten, wenn solche Ereignisse im Log-Bestand vorkommen.

## 6. Integrale & Zähler

**A11 – Strecke / Verbrauch** (Sweep: `dist_cum`, `fuel_cum`, `fuel_rate`)
`s = ∫ v dt`, `V_Kraftstoff = ∫ Kraftstoffrate dt` (falls Rate bekannt).
- Umgekehrt testen: `Δ(Kandidat)/Δt` gegen v bzw. Rate — monoton steigende Felder,
  die mit Überlauf umlaufen, sind klassische Weg-/Verbrauchszähler.
- Überlauf vor dem Differenzieren entfalten (unwrap mod 2^n).
- Vorbilder: `Travel_distance_related` (0x420 Byte1, 0,1992 m/Schritt gegen ODO),
  `FuelConsumption_Counter` (0x420 Byte2, 4834 Schritte/l über Voll-bis-Voll-Tanken),
  `DistanceToService_related` (0x3D1, zählt 1/km herunter).

## 7. GPS und Handy-IMU

**A12 – GPS-/IMU-Anker.** Quellen: GPX-Tracks (`data/can/*.gpx`) und die Handy-`.dlg`
(GPS + IMU), die der Datalake seit 27.09. an die CAN-Logs hängt (`ingest_gps()`).
Paare und Zeitabgleich: `can_phone_imu_sweep.py` (grob über Geschwindigkeit, fein über
Querbeschleunigung). Die Handy-Halterung wechselt je Fahrt, Achsen per
`imu_orientation.py` bestimmen. Startzeiten der CAN-Logs über
`build_datalake.log_start_epoch()` (berücksichtigt `data/can_clock_offsets.json`).
- `v_GPS` → unabhängige Skala für Raddrehzahlen/Tacho (Tacho-Voreilung sichtbar).
- `ψ̇_GPS = d/dt Kurs` → Gierraten-Anker (nur bei v > v_min, Kurs sonst verrauscht).
- Höhe → Steigung `θ ≈ arcsin(Δh / Δs)` → trennt Sensor-a_x von Kinematik (A1).
  Alternative ohne GPS: `slope` im Sweep (Längssensor − dv/dt), damit gefunden
  `RoadIncline_maybe` (0x49C).
- Handy-IMU: Vertikalbeschleunigung, Wanken, Nicken (Plan
  `docs/plans/can-vertikal-wank-nick-plan.md`).
- **Lag immer suchen** (GPS-Latenz typ. 0,1–0,3 s) und Zeitbasis (PPS/Systemuhr)
  dokumentieren.

## 8. Kombinationsmuster

**Konsens-Anker** — zwei unabhängige Herleitungen derselben Größe mitteln, wo sie
übereinstimmen: z.B. ψ̇ aus `YawRate_Raw` und aus A2. Die Übereinstimmung selbst
ist eine hochwertige Gültigkeitsmaske („keine Schlupf-/Driftphase").

**Residuen-Anker** — Differenz zweier Anker trägt eigene Information:
| Residuum | Bedeutung | findet |
|---|---|---|
| `a_x,Sensor − a_x,kin` | g·sin(θ) + Nickwinkeleinfluss | Längsneigung, Sensor-Offset |
| `a_y,Sensor − v·ψ̇` | Schwimmwinkeländerung/Drift, Rollwinkel | Driftphasen-Maske |
| `n_mot − n_mot,kin` (A7) | Kupplungsschlupf, Schaltvorgang | Kupplungsschalter, Schaltphase |
| `v_HA − v_VA` | Antriebsschlupf | TCS/DSC (A9) |
| `Fahrerwunsch-Moment − Ist-Moment` | Eingriff | Momentenreduktion durch DSC |

**Regime-Anker** — Masken als binäre Referenz für Flags:
Stillstand, Leerlauf, eingekuppelt, bremst (`a_x < −x` & Gaspedal 0), Kurvenfahrt
links/rechts, Schlupfphase. Ein unbekanntes Bit, das exakt einer Maske folgt, ist
ein starker Kandidat (z.B. Bremslichtschalter ↔ Bremsmaske).

**Mehrfachregression** — manche Felder sind Kombinationen:
`Kandidat ≈ a·Anker1 + b·Anker2 + c` (z.B. Beschleunigungssensor ≈ a_x,kin + g·sinθ;
Soll-Moment ≈ f(Gaspedal, Drehzahl)). R² der Mehrfachregression gegen die beste
Einzelregression vergleichen; nur akzeptieren, wenn jeder Koeffizient physikalisch
plausibel (Vorzeichen, Größenordnung) und über Logs stabil ist — sonst Overfitting.

**Transformations-Kandidaten** — neben dem Rohfeld auch `d/dt Kandidat`,
`∫ Kandidat` und `|Kandidat|` gegen die Anker testen (Zähler, Raten, betragsmäßig
kodierte Größen mit separatem Richtungsbit).
