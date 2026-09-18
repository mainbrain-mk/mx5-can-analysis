"""
Spreewaldring - Fahrbahn als Flaeche (nicht nur Mittellinie), 30.08.2026

Nutzerauftrag: "ich wuerde gerne versuchen mit dir das orthophoto zu
analysieren und die grenzen der Strecke zu erfassen und statt einer linie die
fahrbahn abzubilden." Baut auf `spreewaldring_track.py` auf (dort: Mittellinie
+ Kruemmung + Kurven aus OSM, orthofoto-verfeinert). Dieses Skript ergaenzt
die zweite Dimension: linker/rechter Fahrbahnrand als Polygon, fuer die
Hauptschleife UND fuer drei bisher nicht modellierte Nebenflaechen
(Boxengasse, zwei Pylon-abgesperrte Verbindungsstuecke).

KONZEPT-KLAERUNG MIT DEM NUTZER (siehe docs/logs/projekt-stand.md fuer die volle
Diskussion, hier nur das Ergebnis):

1. **Liniendetektion statt Asphaltfarben-Region.** Der Nutzer wies darauf
   hin: die Fahrbahn zeigt sich in VERSCHIEDENEN Grautoenen (nicht eine
   einheitliche Farbe), ist aber durchgehend durch eine WEISSE Linie
   begrenzt. Pixel-Stichproben (siehe unten) bestaetigen: Asphalt UND
   Linie haben durchweg NIEDRIGE Saettigung (Asphalt ~0.03-0.08, Linie
   ~0.05-0.11), waehrend das trockene Gras daneben deutlich hoeher liegt
   (~0.17-0.30). Klassifikation ueber Saettigung (statt Farbabstand zu
   einer gelernten Referenzfarbe wie im alten Skript) ist deshalb robuster
   gegen die Grauton-Variation UND erfasst automatisch die Linie mit als
   Teil der Fahrbahn (der bisherige Ansatz stoppte vermutlich schon an der
   Linie, siehe die 8.0m-vs-10m-Diskrepanz in spreewaldring_track.py).
   PIXEL-KALIBRIERUNG (an crop_straight.png, echte Fahrbahn):
     Asphalt:    RGB~(135,140,135)  Saettigung 0.04-0.08
     Weisse Linie: RGB~(175-190,...) Saettigung 0.05-0.11 (schmal, ~3-4 Pixel = 0.6-0.8m)
     Gras:       RGB~(110-160,...)  Saettigung 0.17-0.30
     Schatten (Hochspannungsmast-Kreuzung): Helligkeit stark reduziert,
       Saettigung uneindeutig (0.07-0.19) - bekannte Fehlerquelle, siehe
       Einschraenkungen unten.
   -> `PAVEMENT_SAT_THRESHOLD = 0.13` trennt beide Klassen mit Sicherheitsabstand.

2. **Boxengasse (way 172927499 + Boxenausfahrt-Verbindungsweg) wird jetzt
   als eigenes Flaechenpolygon erfasst** (bisher nur gepunktete Linie im
   Plot von spreewaldring_track.py).

3. **Zwei Pylon-abgesperrte Verbindungsstuecke** (way 243040316 = "A"/obere
   Abkuerzung, way 243040319 = "C"/untere Abkuerzung, bisher nur referenziert
   als SHORTCUT_WAY_IDS, nicht rekonstruiert) werden jetzt ebenfalls als
   Flaechenpolygone erfasst. WICHTIGE ERKENNTNIS aus der gemeinsamen
   Bildanalyse: die vom Nutzer markierten Pylonpositionen (aus
   `spreewaldring_mark.png`, digitalisiert in
   `data/track/spreewaldring_pylon_gates.json`) sind KEINE Fahrbahnraender
   entlang der Strecke, sondern PYLONEN-QUERABSPERRUNGEN an den beiden Enden
   jeder Abkuerzung (wo sie beim Betrieb der vollen Rennstrecke gesperrt
   werden) - jede Gate-Gruppe spannt die VOLLE Fahrbahnbreite an genau
   einem Querschnitt auf (12.6-15.1m, breiter als die ~8m Regelbreite, weil
   die Einmuendungen als Trichter/Aufweitung gebaut sind). Die automatische
   Saettigungs-Randerkennung laeuft trotzdem durchgehend entlang der
   OSM-Mittellinie (auch dort funktioniert das Kriterium "Asphalt vs. Gras",
   keine Linie noetig); die Gate-Punkte dienen NUR als unabhaengige
   Plausibilitaets-Referenz an den beiden Enden (siehe `validate_gates()`),
   NICHT als harte Vorgabe - Nutzer-Entscheidung: "die Einmuendungsbereiche
   sind fuer die Ideallinie ohnehin irrelevant, Interpolation/automatische
   Erkennung reicht."

METHODIK (`detect_pavement_edges()`): fuer jeden Streckenpunkt senkrechter
Farb-Scan wie in spreewaldring_track.py (`refine_centerline_with_orthophoto`),
aber Klassifikation jetzt per Saettigungsschwelle statt Farbabstand zu einer
gelernten Medianfarbe. Zusammenhaengender "Fahrbahn"-Lauf naechst der
Streckenmitte -> dessen beide Enden sind linker/rechter Fahrbahnrand. Luecken
(z.B. an Schattenkreuzungen oder wo keine Fahrbahn im Scan-Fenster gefunden
wird) werden wie im Basisskript per Savitzky-Golay ueberbrueckt/geglaettet
(fuer die Hauptschleife zyklisch, fuer die drei offenen Pfade -
Boxengasse/Verbindungsstuecke - randgepolstert statt zyklisch, siehe
`smooth_offset_open()`).

SEGMENTE:
  - Hauptschleife: nutzt die BEREITS orthofoto-verfeinerte+geglaettete
    Mittellinie aus `spreewaldring_track_summary.json`
    (`track_points_utm33_refined`), keine Neuberechnung der Mittellinie
    selbst - nur die neue Randerkennung wird hier angewendet.
  - Boxengasse: Mittellinie = way 172927499 + Boxenausfahrt-Stub (gleiche
    OSM-Quelle wie im Basisskript, dort nur fuers Plotten benutzt),
    resampled, KEIN geschlossener Loop (offener Pfad).
  - Verbindungsstueck A: way 243040316, offener Pfad.
  - Verbindungsstueck C: way 243040319, offener Pfad.

EINSCHRAENKUNGEN:
  - Saettigungsschwelle ist an EINER Stichprobe (crop_straight.png)
    kalibriert, nicht an allen 56 (bzw. hier: allen Spreewaldring-)
    Bildbereichen systematisch geprueft - Schattenkreuzungen der
    Hochspannungsleitung sind eine bekannte Grauzone (siehe oben), koennen
    lokal falsch klassifizieren, werden aber durch die Glaettung
    ueberbrueckt.
  - Boxenmauer (rot/weiss) auf der einen Seite der Boxengasse: nicht
    gesondert behandelt, sondern implizit ueber dieselbe
    Saettigungsschwelle erfasst (die Mauer hat vermutlich stellenweise
    hoehere Saettigung durch die rote Faerbung, was den erkannten Rand dort
    ggf. etwas nach innen verschiebt) - nicht gegen ein Referenzfoto
    verifiziert.
  - Verbindungsstuecke: die automatische Randerkennung an den beiden Enden
    (wo sich die Fahrbahn trichterartig aufweitet) ist unsicherer als in
    der Mitte - laut Nutzer fuer die Ideallinienfrage ohnehin irrelevant,
    daher keine gesonderte Behandlung (kein hartes Pinning auf die
    Gate-Breite), nur Validierungsausgabe zum Vergleich.
  - Kein kombiniertes Modell/Graph der Streckennetz-Topologie (wo genau
    Verbindungsstueck A/C an die Hauptschleife andockt) - die drei
    Zusatzflaechen werden unabhaengig voneinander berechnet und geplottet,
    nicht automatisch mit der Hauptschleife verschmolzen.

Aufruf: .venv/bin/python scripts/spreewaldring_track_surface.py
"""
import os
import json

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon

from spreewaldring_track import (
    RESULTS_DIR, RESAMPLE_STEP_M,
    fetch_raceways, fetch_orthophoto, to_utm33, utm_to_pixel, sample_bilinear,
    group_runs, crop_orthophoto, arc_length_cumulative,
    resample_closed_loop, smooth_offset_circular, build_pit_connector_latlon,
    PIT_LANE_WAY_ID, LOOP_START_NODE_IDX, MAIN_LOOP_WAY_IDS,
)

TRACK_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_track_summary.json")
PYLON_GATES_PATH = "data/track/spreewaldring_pylon_gates.json"

CONNECTOR_A_WAY_ID = 243040316  # obere Abkuerzung ("orange"), siehe Docstring
CONNECTOR_C_WAY_ID = 243040319  # untere Abkuerzung ("rot")

PAVEMENT_SAT_THRESHOLD = 0.13   # siehe Docstring, Pixel-Kalibrierung
MAIN_LOOP_SCAN_HALF_WIDTH_M = 8.0
OPEN_PATH_SCAN_HALF_WIDTH_M = 12.0  # Verbindungsstuecke koennen an den Enden
                                    # bis ~15m breit sein (Gate-Messung)
# Boxengasse bekommt ein SCHMALERES Scanfenster als die Verbindungsstuecke:
# das direkt angrenzende Boxengebaeude (weisses Wellblechdach) hat aehnlich
# niedrige Saettigung UND aehnliche Helligkeit wie die Boxengassen-Fahrbahn
# selbst (kein sauberer Helligkeitssprung wie beim Kiesbett, siehe
# GRAVEL_BRIGHTNESS_MARGIN-Docstring) - die Kies-Korrektur greift dort also
# NICHT zuverlaessig. Ein engeres Scanfenster (echte Breite ~7-8m) verhindert
# wenigstens, dass der Scan das Gebaeudedach ueberhaupt erreichen kann.
# NUTZER-FEEDBACK (30.08.2026): Boxengassen-Polygon wuchs an mehreren
# Stellen sichtbar in das Gebaeude hinein (Skizzen-Korrektur) - mit 12m
# Scanhalbbreite traten Ausreisser bis 24m Breite auf, mit 6m sinkt das
# Maximum auf 12m. Verbleibende Ungenauigkeit siehe Docstring/Chat -
# schwierigerer Fall als die Kiesbetten, nicht vollstaendig geloest.
PIT_LANE_SCAN_HALF_WIDTH_M = 6.0

# Glaettungsfenster fuer die RAND-Offsets (offset_left_m/offset_right_m).
# NUTZER-FEEDBACK (30.08.2026): mit dem von spreewaldring_track.py
# uebernommenen SMOOTH_WINDOW_M=90m (dort fuer die MITTELLINIEN-Position
# kalibriert) wirkten die Polygone an Kurven/Einmuendungen sichtbar zu grob
# (User hat die echten weissen Linien in zwei Kurven markiert, deutliche
# Abweichung zum Polygonrand). Diagnose: die Randbreite aendert sich an
# Kurven/Einmuendungen auf viel kuerzerer Distanz als die Mittellinien-
# Position jemals wackelt - ein 90m-Fenster mittelt echte, kurzwellige
# Breitenaenderungen (z.B. an der Verbindungsstueck-C-Einmuendung: Rohbreite
# springt innerhalb von ~15m zwischen 8.2m und 16.0m, physikalisch real, da
# dort die Hauptschleife am aufgeweiteten Verbindungsstueck-Rand vorbeilaeuft)
# einfach weg. Test verschiedener Fenster gegen die Rohbreite (Residuum):
# 90m -> std 0.70m/max 4.73m, 15m -> std 0.45m/max 2.69m, kleiner als 15m
# bringt keine weitere Verbesserung mehr (Rauschboden erreicht). Deshalb
# eigenes, viel kleineres Fenster nur fuer die Randerkennung.
EDGE_SMOOTH_WINDOW_M = 15.0

# Kiesbett/Auslaufzonen-Korrektur (NUTZER-FEEDBACK 30.08.2026): Nutzer
# markierte mehrere Stellen an Kurven-Kiesbetten (Randbereiche, wo die
# Strecke in eine helle Kies-/Sandflaeche uebergeht, z.B. an der oberen und
# der unteren linken Haarnadelkurve), wo die Randerkennung zu ungenau war.
# DIAGNOSE (an einer konkreten Scanlinie, s. Chat): Kiesbett-Pixel haben
# AEHNLICH NIEDRIGE Saettigung wie Asphalt (0.08-0.14, teils unter der
# PAVEMENT_SAT_THRESHOLD=0.13), sind aber deutlich HELLER (~180-200 vs.
# ~135-155 auf echtem Asphalt) - reines Saettigungskriterium haengt sich
# deshalb an manchen Kurven ins Kiesbett statt an der echten Asphaltkante
# zu stoppen (Sprung asphalt->kies: Helligkeit 137->193 bei einer Kurve,
# waehrend die Saettigung erst viel spaeter, mitten im Kiesbett, ueber die
# Schwelle steigt). Reine Helligkeitsobergrenze wuerde aber auch die
# weisse Randlinie selbst abschneiden (deren Peak-Helligkeit mit ~160-190
# in denselben Bereich wie Kies faellt) - Unterscheidung stattdessen ueber
# die AUSDEHNUNG: die Linie ist nur ~0.6-0.8m breit (3-4 Pixel), das
# Kiesbett dagegen durchgehend ueber mehrere Meter hell. Deshalb: beim
# Auswaertslaufen vom Streckenmittelpunkt wird der Rand am Beginn eines
# ANHALTEND hellen Laufs abgeschnitten, statt dem urspruenglichen
# (saettigungsbasierten) Lauf bis zum Ende zu folgen - kurze helle Spitzen
# (=Linie) werden dabei nicht faelschlich abgeschnitten, nur ausgedehnte
# helle Flaechen (=Kies/Sand). NACHTRAG: ein FESTER Helligkeits-Schwellwert
# (160) erwies sich als zu aggressiv - an manchen Kurven ist der Untergrund
# NEBEN der Strecke (Feldrand, aufgehellter Boden) selbst schon maessig
# hell, ohne ein echtes Kiesbett zu sein, und wurde faelschlich mit
# abgeschnitten (Breite fiel unter den plausiblen ~9.6m-Median). Stattdessen
# ADAPTIVER Schwellwert pro Scanlinie: lokale Asphalt-Referenzhelligkeit
# (Median in einem kleinen Fenster um den Streckenmittelpunkt) plus fester
# Sicherheitsabstand `GRAVEL_BRIGHTNESS_MARGIN` - reagiert auf
# Beleuchtungsunterschiede zwischen Scanlinien (Schatten, Sonnenstand),
# erkennt aber weiterhin zuverlaessig den ECHTEN Sprung zum Kiesbett.
GRAVEL_MIN_SUSTAIN_M = 1.2
GRAVEL_BRIGHTNESS_MARGIN = 30.0
GRAVEL_LOCAL_REF_WINDOW_M = 1.0


def saturation(rgb):
    """HSV-Saettigung (0..1) aus RGB, NaN bleibt NaN (ausserhalb des Bilds)."""
    mx = np.nanmax(rgb, axis=-1)
    mn = np.nanmin(rgb, axis=-1)
    return np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)


def compute_tangents_open(points):
    """Tangenten per zentraler Differenz, an den Enden einseitig (offener
    Pfad, KEIN zyklischer Anschluss - Unterschied zu compute_tangents_closed)."""
    n = len(points)
    tangents = np.zeros((n, 2))
    for i in range(n):
        p_prev = points[i - 1] if i > 0 else points[i]
        p_next = points[i + 1] if i < n - 1 else points[i]
        t = p_next - p_prev
        norm = np.hypot(*t)
        tangents[i] = t / norm if norm > 1e-9 else np.array([1.0, 0.0])
    return tangents


def compute_tangents_closed(points):
    n = len(points)
    tangents = np.zeros((n, 2))
    for i in range(n):
        t = points[(i + 1) % n] - points[(i - 1) % n]
        norm = np.hypot(*t)
        tangents[i] = t / norm if norm > 1e-9 else np.array([1.0, 0.0])
    return tangents


def resample_open_path(points_utm, step_m):
    """Wie resample_closed_loop in spreewaldring_track.py, aber OHNE die
    Schleife zu schliessen (letzter != erster Punkt) - fuer Boxengasse und
    Verbindungsstuecke, die keine Rundstrecken sind. Liefert
    (resampled_points, s, total_length_m)."""
    seg_len = np.hypot(*np.diff(points_utm, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = cum[-1]
    n_samples = max(2, int(round(total / step_m)) + 1)
    s_new = np.linspace(0, total, n_samples)
    e_new = np.interp(s_new, cum, points_utm[:, 0])
    n_new = np.interp(s_new, cum, points_utm[:, 1])
    return np.column_stack([e_new, n_new]), s_new, total


def _trim_sustained_bright_run(start, end, zero_idx, brightness, pixel_size,
                                bright_threshold, min_sustain_m=GRAVEL_MIN_SUSTAIN_M):
    """Schneidet einen (saettigungsbasierten) Lauf [start,end] dort ab, wo -
    vom Zentrum aus auswaerts gesehen - ein ANHALTEND heller Abschnitt
    beginnt (Kiesbett), laesst aber kurze helle Spitzen (Randlinie) intakt.
    `bright_threshold` wird PRO SCANLINIE adaptiv vom Aufrufer bestimmt
    (lokale Asphalt-Referenzhelligkeit + Sicherheitsabstand) - siehe
    Docstring bei GRAVEL_BRIGHTNESS_MARGIN fuer die Herleitung."""
    min_sustain_samples = max(1, int(round(min_sustain_m / pixel_size)))
    zc = min(max(zero_idx, start), end)

    new_end = end
    run_len = 0
    for k in range(zc, end + 1):
        if brightness[k] > bright_threshold:
            run_len += 1
            if run_len >= min_sustain_samples:
                new_end = k - min_sustain_samples
                break
        else:
            run_len = 0

    new_start = start
    run_len = 0
    for k in range(zc, start - 1, -1):
        if brightness[k] > bright_threshold:
            run_len += 1
            if run_len >= min_sustain_samples:
                new_start = k + min_sustain_samples
                break
        else:
            run_len = 0

    new_start = max(new_start, start)
    new_end = min(new_end, end)
    if new_end <= new_start:
        return start, end  # Sicherheitsnetz: bei widerspruechlichem Ergebnis unveraendert lassen
    return new_start, new_end


def detect_pavement_edges(points_utm, tangents, image, geo,
                           scan_half_width_m, sat_threshold=PAVEMENT_SAT_THRESHOLD):
    """Wie refine_centerline_with_orthophoto() in spreewaldring_track.py,
    aber Klassifikation ueber Saettigungsschwelle statt Farbabstand zu einer
    gelernten Referenzfarbe (siehe Docstring oben) - erfasst dadurch die
    weisse Randlinie als Teil der Fahrbahn statt als Abbruchkriterium."""
    n = len(points_utm)
    pixel_size = geo[2]
    scan_offsets = np.arange(-scan_half_width_m, scan_half_width_m + pixel_size, pixel_size)
    zero_idx = len(scan_offsets) // 2
    ref_n = max(1, int(round(GRAVEL_LOCAL_REF_WINDOW_M / pixel_size)))

    offset_l = np.full(n, np.nan)
    offset_r = np.full(n, np.nan)
    widths = np.full(n, np.nan)
    ok = np.zeros(n, dtype=bool)

    for i in range(n):
        tangent = tangents[i]
        perp = np.array([-tangent[1], tangent[0]])
        scan_pts = points_utm[i] + np.outer(scan_offsets, perp)
        cols, rows = utm_to_pixel(scan_pts[:, 0], scan_pts[:, 1], geo)
        colors = np.array([sample_bilinear(image, c, r) for c, r in zip(cols, rows)])
        valid = ~np.isnan(colors).any(axis=1)
        sat = np.full(len(scan_offsets), np.nan)
        sat[valid] = saturation(colors[valid])
        brightness = np.nanmean(colors, axis=1)
        is_pavement = valid & (sat < sat_threshold)

        runs = group_runs(is_pavement)
        if not runs:
            continue
        best = min(runs, key=lambda r: min(abs(r[0] - zero_idx), abs(r[1] - zero_idx)))
        local_ref_bright = np.nanmedian(brightness[max(0, zero_idx - ref_n):zero_idx + ref_n + 1])
        bright_threshold = local_ref_bright + GRAVEL_BRIGHTNESS_MARGIN
        best = _trim_sustained_bright_run(best[0], best[1], zero_idx, brightness, pixel_size,
                                           bright_threshold)
        offset_axis = np.arange(len(scan_offsets))
        offset_l[i] = np.interp(best[0], offset_axis, scan_offsets)
        offset_r[i] = np.interp(best[1], offset_axis, scan_offsets)
        widths[i] = (best[1] - best[0]) * pixel_size
        ok[i] = True

    return {"offset_left_m": offset_l, "offset_right_m": offset_r, "width_m": widths, "ok": ok}


def offsets_to_points(points_utm, tangents, offset_m):
    perp = np.column_stack([-tangents[:, 1], tangents[:, 0]])
    return points_utm + offset_m[:, None] * perp


def smooth_offset_open(offset_m, ok, window_m, step_m):
    """Wie smooth_offset_circular() in spreewaldring_track.py, aber
    randgepolstert (Randwert wiederholt) statt zyklisch - fuer offene Pfade
    (Boxengasse, Verbindungsstuecke), die keine geschlossene Schleife sind."""
    from scipy.signal import savgol_filter

    n = len(offset_m)
    idx = np.arange(n)
    if ok.all():
        filled = offset_m.copy()
    else:
        filled = np.interp(idx, idx[ok], offset_m[ok])

    w = max(5, int(round(window_m / step_m)))
    if w % 2 == 0:
        w += 1
    if w >= n:
        w = n - 1 if (n - 1) % 2 == 1 else n - 2
        w = max(w, 3)
    pad = w
    padded = np.concatenate([np.full(pad, filled[0]), filled, np.full(pad, filled[-1])])
    smoothed = savgol_filter(padded, window_length=w, polyorder=2)
    return smoothed[pad:-pad]


def build_segment(name, points_utm, closed, image, geo, scan_half_width_m,
                   smooth_window_m=EDGE_SMOOTH_WINDOW_M, step_m=RESAMPLE_STEP_M):
    """Gemeinsame Pipeline fuer ein Segment (Hauptschleife ODER offener
    Pfad): resample -> Tangenten -> Randerkennung -> Luecken fuellen +
    glaetten -> linke/rechte Randlinie in Weltkoordinaten -> Polygon."""
    if closed:
        points, _, total_len, _ = resample_closed_loop(points_utm, step_m)
        tangents = compute_tangents_closed(points)
        smooth_fn = lambda off, ok: smooth_offset_circular(off, ok, smooth_window_m, step_m)
    else:
        points, s, total_len = resample_open_path(points_utm, step_m)
        tangents = compute_tangents_open(points)
        smooth_fn = lambda off, ok: smooth_offset_open(off, ok, smooth_window_m, step_m)

    det = detect_pavement_edges(points, tangents, image, geo, scan_half_width_m)
    ok = det["ok"]
    frac_ok = ok.sum() / len(ok) if len(ok) else 0.0
    print(f"  [{name}] {ok.sum()}/{len(ok)} Punkte erkannt ({100*frac_ok:.0f}%), "
          f"Breite Median={np.nanmedian(det['width_m']):.2f}m "
          f"Mittel={np.nanmean(det['width_m']):.2f}m Laenge={total_len:.1f}m")

    if not ok.any():
        print(f"  [{name}] WARNUNG: keine Fahrbahn erkannt, Segment wird uebersprungen.")
        return None

    offset_l_smooth = smooth_fn(np.nan_to_num(det["offset_left_m"], nan=0.0), ok)
    offset_r_smooth = smooth_fn(np.nan_to_num(det["offset_right_m"], nan=0.0), ok)

    left_pts = offsets_to_points(points, tangents, offset_l_smooth)
    right_pts = offsets_to_points(points, tangents, offset_r_smooth)
    polygon = np.vstack([left_pts, right_pts[::-1]])

    return {
        "name": name,
        "closed": closed,
        "total_length_m": total_len,
        "n_points": len(points),
        "n_detected": int(ok.sum()),
        "detection_fraction": frac_ok,
        "width_m_median": float(np.nanmedian(det["width_m"])),
        "width_m_mean": float(np.nanmean(det["width_m"])),
        "centerline_utm33": points.tolist(),
        "left_edge_utm33": left_pts.tolist(),
        "right_edge_utm33": right_pts.tolist(),
        "polygon_utm33": polygon.tolist(),
        "point_detected_mask": ok.tolist(),
        "width_m_per_point": [None if np.isnan(w) else float(w) for w in det["width_m"].tolist()],
    }


def load_pylon_gates():
    with open(PYLON_GATES_PATH, encoding="utf-8") as f:
        return json.load(f)["gates"]


def gate_span_m(points):
    pts = np.array(points)
    return float(np.linalg.norm(pts[0] - pts[-1]))


def validate_gates(segments, gates):
    """Vergleicht die automatisch erkannte Breite nahe jedem Pylon-Gate mit
    der aus den digitalisierten Pylon-Punkten direkt gemessenen Spannweite -
    reine Plausibilitaetsausgabe (siehe Docstring: kein hartes Pinning)."""
    print("\n=== Gate-Validierung (Pylon-Absperrungen vs. automatische Erkennung) ===")
    checks = [
        ("connector_A", "connector_A_gate_start"), ("connector_A", "connector_A_gate_end"),
        ("connector_C", "connector_C_gate_east"), ("connector_C", "connector_C_gate_west"),
    ]
    results = []
    for seg_name, gate_name in checks:
        seg = segments.get(seg_name)
        gate = gates[gate_name]
        gate_pts = np.array(gate["points_utm33"])
        gate_center = gate_pts.mean(axis=0)
        span = gate_span_m(gate["points_utm33"])
        if seg is None:
            print(f"  {gate_name}: Segment {seg_name} nicht verfuegbar, uebersprungen.")
            continue
        centerline = np.array(seg["centerline_utm33"])
        dists = np.linalg.norm(centerline - gate_center, axis=1)
        i_nearest = int(np.argmin(dists))
        dist_to_center = float(dists[i_nearest])
        detected_width = seg["width_m_per_point"][i_nearest]
        width_str = f"{detected_width:.1f}m" if detected_width is not None else "n/a (nicht erkannt)"
        print(f"  {gate_name}: Gate-Spannweite={span:.1f}m vs. automatisch erkannte Breite "
              f"an der naechstgelegenen Stelle={width_str} "
              f"(Streckenpunkt {dist_to_center:.1f}m vom Gate-Mittelpunkt entfernt, "
              f"Index {i_nearest}/{seg['n_points']})")
        results.append({
            "gate": gate_name, "segment": seg_name, "gate_span_m": span,
            "nearest_centerline_point_idx": i_nearest,
            "nearest_centerline_dist_m": dist_to_center,
            "detected_width_m_at_nearest_point": detected_width,
        })
    return results


def plot_surface(segments, ortho_crop, ortho_extent, out_path):
    fig, ax = plt.subplots(figsize=(11, 11))
    ax.imshow(ortho_crop, extent=ortho_extent, origin="upper")

    colors = {
        "main_loop": ("limegreen", "Hauptschleife"),
        "pit_lane": ("deepskyblue", "Boxengasse"),
        "connector_A": ("orange", "Verbindungsstueck A (obere Abkuerzung)"),
        "connector_C": ("red", "Verbindungsstueck C (untere Abkuerzung)"),
    }
    for key, (color, label) in colors.items():
        seg = segments.get(key)
        if seg is None:
            continue
        poly = np.array(seg["polygon_utm33"])
        patch = MplPolygon(poly, closed=True, facecolor=color, edgecolor=color,
                            alpha=0.45, linewidth=1.0, label=label)
        ax.add_patch(patch)

    ax.set_xlabel("UTM33 Ost [m]")
    ax.set_ylabel("UTM33 Nord [m]")
    ax.set_aspect("equal")
    ax.set_title("Spreewaldring - Fahrbahn als Flaeche (Saettigungs-basierte Randerkennung)")
    ax.legend(fontsize=8, loc="upper right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close(fig)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("Lade Hauptschleifen-Mittellinie (bereits orthofoto-verfeinert+geglaettet) ...")
    with open(TRACK_SUMMARY_PATH, encoding="utf-8") as f:
        track_summary = json.load(f)
    main_loop_centerline = np.array(track_summary["orthophoto_refinement"]["track_points_utm33_refined"])

    print("Lade Raceway-Geometrien (OSM, gecacht) fuer Boxengasse + Verbindungsstuecke ...")
    raceways = fetch_raceways()
    ways = {el["id"]: el for el in raceways["elements"]}

    from spreewaldring_track import build_pit_connector_latlon
    pit_lane_latlon = build_pit_connector_latlon(raceways)
    pit_lane_utm = to_utm33(pit_lane_latlon)

    def way_latlon(way_id):
        geo = ways[way_id]["geometry"]
        return np.array([(p["lat"], p["lon"]) for p in geo])

    connector_a_utm = to_utm33(way_latlon(CONNECTOR_A_WAY_ID))
    connector_c_utm = to_utm33(way_latlon(CONNECTOR_C_WAY_ID))

    print("Lade Orthofoto ...")
    image, geo = fetch_orthophoto()

    print("\n=== Randerkennung (Saettigungsschwelle, siehe Docstring) ===")
    segments = {}
    segments["main_loop"] = build_segment("main_loop", main_loop_centerline, True,
                                           image, geo, MAIN_LOOP_SCAN_HALF_WIDTH_M)
    segments["pit_lane"] = build_segment("pit_lane", pit_lane_utm, False,
                                          image, geo, PIT_LANE_SCAN_HALF_WIDTH_M)
    segments["connector_A"] = build_segment("connector_A", connector_a_utm, False,
                                             image, geo, OPEN_PATH_SCAN_HALF_WIDTH_M)
    segments["connector_C"] = build_segment("connector_C", connector_c_utm, False,
                                             image, geo, OPEN_PATH_SCAN_HALF_WIDTH_M)

    gates = load_pylon_gates()
    gate_validation = validate_gates(segments, gates)

    all_points = np.vstack([np.array(seg["centerline_utm33"]) for seg in segments.values() if seg])
    e_min, n_min = all_points.min(axis=0)
    e_max, n_max = all_points.max(axis=0)
    ortho_crop, ortho_extent = crop_orthophoto(image, geo, (e_min, n_min, e_max, n_max))

    out_png = os.path.join(RESULTS_DIR, "spreewaldring_track_surface.png")
    plot_surface(segments, ortho_crop, ortho_extent, out_png)
    print(f"\nFlaechen-Plot: {out_png}")

    summary = {
        "pavement_saturation_threshold": PAVEMENT_SAT_THRESHOLD,
        "segments": {k: v for k, v in segments.items() if v is not None},
        "gate_validation": gate_validation,
    }
    out_json = os.path.join(RESULTS_DIR, "spreewaldring_track_surface_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
