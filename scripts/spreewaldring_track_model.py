"""
Spreewaldring - Streckenmodell aus Orthofoto (PhotoCraft) und DGM, 07.11.2026

Nutzerauftrag: Streckengrenzen mit PhotoCraft moeglichst praezise aus dem Orthofoto bestimmen und
daraus mit den Hoehendaten ein moeglichst genaues Streckenmodell bauen. Ersetzt nichts: die Summary
von spreewaldring_track.py und spreewaldring_track_surface.py bleibt, das Modell liegt daneben.

Unterschied zu spreewaldring_track_surface.py: dort wird jeder Querschnitt einzeln per Saettigungs-
schwelle klassifiziert und danach geglaettet, die Raender wackeln dadurch sichtbar. Hier wird die
weisse Randlinie selbst verfolgt: Viterbi ueber die ganze Runde, also eine durchgehende Spur statt
vieler Einzelentscheidungen. An Einmuendungen ohne Linie ueberbrueckt die Glattheit, solche Stellen
sind im Modell markiert (`*_line_seen`).

Eingaben (von PhotoCraft aus ortho_crop.png erzeugt, siehe docs/logs/projekt-stand.md):
  data/orthophoto/spreewaldring_crop_{linemap,asphalt,gray}.png
Der Ausschnitt beginnt bei CROP_COL/CROP_ROW der Kachel dop_33409-5761 und hat dieselbe Aufloesung.

Ausgaben: results/spreewaldring_track_model.json und .csv, results/spreewaldring_track_model_edges.png
(Overlay fuer PhotoCraft) sowie die Vergleiche auf der Konsole. `left`/`right` sind Linienmitten,
`left_outer`/`right_outer` die Aussenkanten der Linie. `bank` > 0: linker Rand hoeher als rechter.

Nutzerskizze (data/track/spreewaldring_skizze_nutzer_2026-11-07.png, Maus, also ungenau): blau Boxengasse,
rot Hauptstrecke an der Boxenausfahrt. Sie gibt nur einen Korridor vor (`hint_targets()`), die Kante selbst
kommt weiter aus dem Bild; ohne Bildmerkmal folgt die Spur der Skizze (markiert: `*_hinted`).

Boxengasse: Strecke und Boxengasse trennt die rot/weisse Mauer, ihr Schatten faellt auf die Fahrbahn der
Boxengasse. Rand dort ist deshalb der Sprung Mauer hell -> Schatten dunkel (`wall_step()`), nicht das Schattenende.

Aufruf: .venv/bin/python scripts/spreewaldring_track_model.py
"""
import csv
import json
import os

import numpy as np
from PIL import Image
from scipy.interpolate import splev, splprep
from scipy.ndimage import gaussian_filter1d, map_coordinates
from scipy.signal import argrelmin

from elevation_model import ElevationModel
from spreewaldring_track import (
    CORNER_MERGE_GAP_M, CORNER_RADIUS_MAX_M, ORTHOPHOTO_DIR, ORTHOPHOTO_TILE, RESULTS_DIR,
    compute_curvature_radius, find_corners,
)

Image.MAX_IMAGE_PIXELS = None

CROP_COL, CROP_ROW = 2700, 300
LINEMAP_PATH = os.path.join(ORTHOPHOTO_DIR, "spreewaldring_crop_linemap.png")
ASPHALT_PATH = os.path.join(ORTHOPHOTO_DIR, "spreewaldring_crop_asphalt.png")
GRAY_PATH = os.path.join(ORTHOPHOTO_DIR, "spreewaldring_crop_gray.png")
SKETCH_PATH = "data/track/spreewaldring_skizze_nutzer_2026-11-07.png"
SURFACE_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_track_surface_summary.json")
PYLON_GATES_PATH = "data/track/spreewaldring_pylon_gates.json"
MODEL_JSON = os.path.join(RESULTS_DIR, "spreewaldring_track_model.json")
MODEL_CSV = os.path.join(RESULTS_DIR, "spreewaldring_track_model.csv")
OVERLAY_PNG = os.path.join(RESULTS_DIR, "spreewaldring_track_model_edges.png")

STEP_M = 1.0
SCAN_DR_M = 0.1
EDGE_SEARCH_M = (1.5, 11.0)      # Abstand Rand <-> Mittellinie, Hauptschleife real um 5 m
MAX_EDGE_RATE = 0.5              # Rand darf je Meter Strecke hoechstens so weit seitlich laufen
RATE_PENALTY = 3.0               # Bestrafung seitlicher Spruenge (Score-Einheiten bei MAX_EDGE_RATE)
ALONG_SIGMA_M = 1.5              # Score laengs mitteln: Risse und Flecken queren die Linie nur kurz
TRANSITION_M = 0.8               # Fenster Asphalt innen / kein Asphalt aussen
RIDGE_WEIGHT, STEP_WEIGHT = 1.0, 0.7
LINE_SEEN_MIN = 0.25             # Mindestkontrast der Linie (0..1) fuer "Linie gesehen"
REFINE_HALF_M = 0.6              # Sub-Pixel-Schwerpunkt um die Viterbi-Spur
EDGE_SMOOTH_M = 2.0
CENTER_SMOOTH_RMS_M = 0.05       # zugelassene Abweichung des Mittellinien-Splines von (L+R)/2
Z_SMOOTH_M = 5.0                 # DGM 1 m: laengs glaetten, sonst dominiert Rasterrauschen die Steigung
CURVATURE_WINDOW_M = 15.0        # wie spreewaldring_track.py, damit Radien vergleichbar bleiben
GAP_STOP_M, GAP_ASPHALT_MAX, GAP_PENALTY = 1.0, 0.25, 2.0  # siehe penalize_beyond_gap(), nur Boxengasse
HINT_CORRIDOR_M, HINT_PULL = 1.0, 0.5  # Abzug HINT_PULL*(Abstand/Korridor)^2: Bildmerkmal (Score um 1) gewinnt nur nah an der Skizze
WALL_WINDOW_M, WALL_WEIGHT = 0.4, 2.0  # Sprung Mauer -> Schatten, siehe wall_step()
SHADOW_MAX = 0.40                 # Helligkeit 0..1: Schatten (gemessen 0,25-0,37), Fahrbahn um 0,6
LINE_WIDTH_M = 0.52              # weisse Linie, Halbwertsbreite im mittleren Querprofil (links 0,55, rechts 0,50);
                                 # enthaelt die Bildunschaerfe, echte Breite vermutlich etwas kleiner


def read_geo():
    """(origin_e, origin_n, px) der Kachel aus dem World-File, wie fetch_orthophoto()."""
    with open(os.path.join(ORTHOPHOTO_DIR, f"dop_{ORTHOPHOTO_TILE}.jgw")) as f:
        vals = [float(x) for x in f.read().split()]
    return vals[4], vals[5], vals[0]


def load_map(path):
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32) / 255.0


def load_hints(path, geo):
    """Skizzenpixel als UTM-Punkte: {"pit_lane": blau, "main_loop": rot}."""
    a = np.asarray(Image.open(path).convert("RGBA"))
    drawn = a[..., 3] > 128
    def to_utm(mask):
        rows, cols = np.where(mask)
        return np.c_[geo[0] + (cols + CROP_COL) * geo[2], geo[1] - (rows + CROP_ROW) * geo[2]]
    return {"pit_lane": to_utm(drawn & (a[..., 2] > 150)), "main_loop": to_utm(drawn & (a[..., 0] > 150))}


def hint_targets(points, nrm, hints, side, closed):
    """Sollversatz je Station aus der Skizze, zwischen den Strichen linear, ausserhalb NaN."""
    n = len(points)
    target = np.full(n, np.nan)
    j = np.linalg.norm(points[:, None] - hints[None], axis=2).argmin(axis=0)
    off = ((hints - points[j]) * nrm[j]).sum(axis=1)
    keep = off * side > 0
    if not keep.any():
        return target
    j, off = j[keep], off[keep]
    if closed and j.max() - j.min() > n / 2:  # Skizze liegt ueber der Rundennaht
        j = np.where(j < n / 2, j + n, j)
    stations = np.unique(j)
    medians = np.array([np.median(off[j == st]) for st in stations])
    span = np.arange(stations[0], stations[-1] + 1)
    target[span % n] = np.interp(span, stations, medians)
    return target


def resample(points, step, closed):
    pts = np.vstack([points, points[:1]]) if closed else points
    keep = np.r_[True, np.hypot(*np.diff(pts, axis=0).T) > 1e-6]
    pts = pts[keep]
    s = np.r_[0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
    n = int(round(s[-1] / step))
    si = np.linspace(0, s[-1], n, endpoint=not closed)
    return np.c_[np.interp(si, s, pts[:, 0]), np.interp(si, s, pts[:, 1])]


def normals(points, closed):
    """Links zur Fahrtrichtung, geglaettet: Knicke der Fuehrungslinie sollen den Scan nicht verdrehen."""
    mode = "wrap" if closed else "nearest"
    smooth = gaussian_filter1d(points, 3.0, axis=0, mode=mode)
    t = np.roll(smooth, -1, axis=0) - np.roll(smooth, 1, axis=0) if closed else np.gradient(smooth, axis=0)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    return np.c_[-t[:, 1], t[:, 0]]


def sample_across(image, points, nrm, offsets, geo):
    """Bild ueber (Stationen, Querversatz) abtasten, bilinear, Koordinaten in UTM."""
    e = points[:, 0, None] + nrm[:, 0, None] * offsets[None]
    n = points[:, 1, None] + nrm[:, 1, None] * offsets[None]
    col = (e - geo[0]) / geo[2] - CROP_COL
    row = (geo[1] - n) / geo[2] - CROP_ROW
    return map_coordinates(image, [row, col], order=1, mode="nearest")


def wall_step(gray, offsets, side, closed):
    """Score fuer aussen hell, innen Schatten. Zwei Fenster je WALL_WINDOW_M um jeden Querversatz."""
    k = int(round(WALL_WINDOW_M / (offsets[1] - offsets[0])))
    c = np.cumsum(np.c_[np.zeros(len(gray)), gray], axis=1)
    up = (c[:, 2 * k:] - c[:, k:-k]) / k
    down = (c[:, k:-k] - c[:, :-2 * k]) / k
    outer, inner = (up, down) if side > 0 else (down, up)
    out = np.zeros_like(gray)
    out[:, k:gray.shape[1] - k + 1][:, :up.shape[1]] = np.clip(outer - inner, 0, None) * (inner < SHADOW_MAX)
    return gaussian_filter1d(out, ALONG_SIGMA_M, axis=0, mode="wrap" if closed else "nearest")


def edge_score(line, asphalt, offsets, side, closed):
    """Score(Station, Querversatz) einer Seite: Linienkamm plus Stufe Asphalt innen -> nichts aussen."""
    dr = offsets[1] - offsets[0]
    k = int(round(TRANSITION_M / dr))
    step = np.zeros_like(asphalt)
    outer = np.cumsum(np.c_[np.zeros(len(asphalt)), asphalt], axis=1)
    mean_up = (outer[:, 2 * k:] - outer[:, k:-k]) / k     # [i, i+k): Richtung groesserer Versaetze
    mean_down = (outer[:, k:-k] - outer[:, :-2 * k]) / k  # [i-k, i)
    step[:, k:-k + 1 or None][:, :mean_up.shape[1]] = (
        (mean_down - mean_up) if side > 0 else (mean_up - mean_down))
    score = RIDGE_WEIGHT * line + STEP_WEIGHT * np.clip(step, -1, 1)
    return gaussian_filter1d(score, ALONG_SIGMA_M, axis=0, mode="wrap" if closed else "nearest")


def viterbi(score, dr, step, closed):
    """Beste durchgehende Spur je Station. Geschlossen: mit Vorlauf und Nachlauf. Die Naht kommt an die Stelle
    mit dem staerksten Signal, dort finden beide Enden dieselbe Linie (sonst Sprung in Stuecken ohne Linie)."""
    n, m = score.shape
    if closed:
        seam = int(np.argmax(gaussian_filter1d(score.max(axis=1), 15.0, mode="wrap")))
        return np.roll(viterbi_open(np.roll(score, -seam, axis=0), dr, step, min(n // 3, 150)), seam)
    return viterbi_open(score, dr, step, 0)


def viterbi_open(score, dr, step, pad):
    n, m = score.shape
    sc = np.vstack([score[-pad:], score, score[:pad]]) if pad else score
    reach = max(1, int(round(MAX_EDGE_RATE * step / dr)))
    jumps = np.arange(-reach, reach + 1)
    penalty = RATE_PENALTY * (jumps / reach) ** 2
    back = np.zeros(sc.shape, dtype=np.int16)
    total = sc[0].copy()
    for i in range(1, len(sc)):
        cand = np.full((len(jumps), m), -np.inf)
        for j, jump in enumerate(jumps):
            lo, hi = max(0, jump), min(m, m + jump)
            cand[j, lo:hi] = total[lo - jump:hi - jump] - penalty[j]
        best = cand.argmax(axis=0)
        back[i] = np.arange(m) - jumps[best]
        total = cand[best, np.arange(m)] + sc[i]
    path = np.zeros(len(sc), dtype=int)
    path[-1] = total.argmax()
    for i in range(len(sc) - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    return path[pad:pad + n] if pad else path


def penalize_beyond_gap(score, asphalt, side, closed):
    """Hinter der ersten laengeren Asphaltluecke (von der Mitte aus) gibt es keinen Rand dieser Fahrbahn mehr.
    Noetig in der Boxengasse: hinter der Barriere liegt sonst die Randlinie der Hauptstrecke."""
    a = gaussian_filter1d(asphalt, ALONG_SIGMA_M, axis=0, mode="wrap" if closed else "nearest")
    a = a if side > 0 else a[:, ::-1]        # Spalten von der Mitte nach aussen
    need = int(round(GAP_STOP_M / SCAN_DR_M))
    out = score.copy()
    for i, row in enumerate(a):
        low = row < GAP_ASPHALT_MAX
        started = np.logical_or.accumulate(~low)
        run = 0
        for j in range(len(row)):
            run = run + 1 if low[j] and started[j] else 0
            if run >= need:
                stop = j  # erst GAP_STOP_M hinter dem Lueckenbeginn, die Kante selbst (Mauer) bleibt erreichbar
                if side > 0:
                    out[i, stop:] -= GAP_PENALTY
                else:
                    out[i, :len(row) - stop] -= GAP_PENALTY
                break
    return out


def trace_side(points, nrm, line_img, asphalt_img, geo, side, closed, search=EDGE_SEARCH_M, stop_at_gap=False,
               gray_img=None, hints=None, wall=False):
    """Randversatz je Station fuer eine Seite (side=+1 links, -1 rechts): Linienmitte, Kontrast, Skizzen-Maske."""
    offsets = side * np.arange(search[0], search[1] + SCAN_DR_M / 2, SCAN_DR_M)
    grid = np.sort(offsets)
    line = sample_across(line_img, points, nrm, grid, geo)
    asphalt = sample_across(asphalt_img, points, nrm, grid, geo)
    score = edge_score(line, asphalt, grid, side, closed)
    mode = "wrap" if closed else "nearest"
    gray = None if gray_img is None else sample_across(gray_img, points, nrm, grid, geo)
    if stop_at_gap:  # Schatten und Personen darauf gehoeren zur Fahrbahn
        road = asphalt if gray is None else np.maximum(asphalt, gray < SHADOW_MAX)
        score = penalize_beyond_gap(score, road, side, closed)
    feature = gaussian_filter1d(line, ALONG_SIGMA_M, axis=0, mode=mode)
    if wall:
        step = wall_step(gray, grid, side, closed)
        score = score + WALL_WEIGHT * step
        feature = np.where(step.max(axis=1, keepdims=True) > LINE_SEEN_MIN, step, feature)
    target = np.full(len(points), np.nan) if hints is None else hint_targets(points, nrm, hints, side, closed)
    hinted = np.isfinite(target)
    score[hinted] -= HINT_PULL * ((grid[None] - target[hinted, None]) / HINT_CORRIDOR_M) ** 2
    idx = viterbi(score, SCAN_DR_M, STEP_M, closed)
    k = int(round(REFINE_HALF_M / SCAN_DR_M))
    offset = np.empty(len(idx))
    contrast = np.empty(len(idx))
    for i, j in enumerate(idx):
        lo, hi = max(0, j - k), min(len(grid), j + k + 1)
        w = np.clip(feature[i, lo:hi] - feature[i, lo:hi].min(), 0, None)
        offset[i] = grid[j] if w.sum() <= 0 else (w * grid[lo:hi]).sum() / w.sum()
        contrast[i] = feature[i, lo:hi].max()
    return offset, contrast, hinted


def trace_edges(center, line_img, asphalt_img, geo, closed, search=EDGE_SEARCH_M, iterations=2, stop_at_gap=False,
                gray_img=None, hints=None, wall_side=0):
    """Beide Raender. Die Mittellinie wird einmal nachgezogen, damit die Scans senkrecht zur Fahrbahn liegen."""
    points = resample(center, STEP_M, closed)
    for it in range(iterations):
        nrm = normals(points, closed)
        off_l, con_l, hint_l = trace_side(points, nrm, line_img, asphalt_img, geo, +1, closed, search, stop_at_gap,
                                          gray_img, hints, wall_side > 0)
        off_r, con_r, hint_r = trace_side(points, nrm, line_img, asphalt_img, geo, -1, closed, search, stop_at_gap,
                                          gray_img, hints, wall_side < 0)
        mode = "wrap" if closed else "nearest"
        left = points + nrm * gaussian_filter1d(off_l, EDGE_SMOOTH_M, mode=mode)[:, None]
        right = points + nrm * gaussian_filter1d(off_r, EDGE_SMOOTH_M, mode=mode)[:, None]
        if it < iterations - 1:
            points = resample((left + right) / 2, STEP_M, closed)
    return dict(points=points, left=left, right=right, contrast_left=con_l, contrast_right=con_r,
                hinted_left=hint_l, hinted_right=hint_r)


def fit_centerline(left, right, closed):
    """Glatte Mittellinie als Spline, 1-m-Schritte. Ein Spline statt Mittelung im Fenster: Krümmung bleibt stetig."""
    mid = (left + right) / 2
    pts = np.vstack([mid, mid[:1]]) if closed else mid
    tck, u = splprep([pts[:, 0], pts[:, 1]], s=len(pts) * CENTER_SMOOTH_RMS_M ** 2, per=1 if closed else 0)
    dense = np.array(splev(np.linspace(0, 1, 20 * len(pts)), tck)).T
    return resample(dense[:-1] if closed else dense, STEP_M, closed)


def project_edge(center, edge, closed):
    """Rand auf die Stationen der Mittellinie umlegen, damit jede Zeile ein Querschnitt ist."""
    nrm = normals(center, closed)
    m = len(edge)
    # naechster Randpunkt je Station; ponytail: O(n*m), bei < 3000 Stationen voellig ausreichend
    d = np.linalg.norm(center[:, None] - edge[None], axis=2)
    j = d.argmin(axis=1)
    a, b = edge[(j - 1) % m if closed else np.clip(j - 1, 0, m - 1)], edge[(j + 1) % m if closed else np.clip(j + 1, 0, m - 1)]
    # Schnitt Normale mit dem lokalen Randstueck
    seg = b - a
    denom = nrm[:, 0] * seg[:, 1] - nrm[:, 1] * seg[:, 0]
    rel = a - center
    off = (rel[:, 0] * seg[:, 1] - rel[:, 1] * seg[:, 0]) / np.where(np.abs(denom) < 1e-9, 1e-9, denom)
    return center + nrm * off[:, None], off, j


def build_segment(name, guide, line_img, asphalt_img, geo, em, closed, search=EDGE_SEARCH_M, stop_at_gap=False,
                  gray_img=None, hints=None, wall_side=0):
    tr = trace_edges(guide, line_img, asphalt_img, geo, closed, search, iterations=3 if wall_side else 2,
                     stop_at_gap=stop_at_gap, gray_img=gray_img, hints=hints, wall_side=wall_side)
    center = fit_centerline(tr["left"], tr["right"], closed)
    if closed:  # Start wie im Altstand: naechster Punkt zum ersten Fuehrungspunkt
        center = np.roll(center, -int(np.argmin(np.linalg.norm(center - guide[0], axis=1))), axis=0)
    left, off_l, j_l = project_edge(center, tr["left"], closed)
    right, off_r, j_r = project_edge(center, tr["right"], closed)
    seen_l = tr["contrast_left"][j_l] >= LINE_SEEN_MIN
    seen_r = tr["contrast_right"][j_r] >= LINE_SEEN_MIN

    mode = "wrap" if closed else "nearest"
    z = [gaussian_filter1d(em.get_elevation(p[:, 0], p[:, 1]), Z_SMOOTH_M, mode=mode)
         for p in (center, left, right)]
    width = off_l - off_r
    nrm = normals(center, closed)
    left_outer = left + nrm * LINE_WIDTH_M / 2
    right_outer = right if wall_side < 0 else right - nrm * LINE_WIDTH_M / 2  # an der Mauer gibt es keine Linie
    ds =np.hypot(*np.diff(np.vstack([center, center[:1]]) if closed else center, axis=0).T)
    grade = np.gradient(z[0], STEP_M) if not closed else (np.roll(z[0], -1) - np.roll(z[0], 1)) / (2 * STEP_M)
    bank = (z[1] - z[2]) / width
    radius = compute_curvature_radius(center, CURVATURE_WINDOW_M, STEP_M) if closed else None
    s = np.r_[0, np.cumsum(ds)][:len(center)]
    length_2d = float(ds.sum())
    dz = np.diff(np.r_[z[0], z[0][:1]] if closed else z[0])
    length_3d = float(np.hypot(ds, dz).sum())
    return dict(name=name, closed=closed, s_m=s, center=center, left=left, right=right,
                left_outer=left_outer, right_outer=right_outer, line_width_m=LINE_WIDTH_M,
                z_center=z[0], z_left=z[1], z_right=z[2], width_m=width, offset_left_m=off_l,
                offset_right_m=off_r, grade=grade, bank=bank, radius_m=radius, left_line_seen=seen_l,
                right_line_seen=seen_r, left_hinted=tr["hinted_left"][j_l], right_hinted=tr["hinted_right"][j_r], length_2d_m=length_2d, length_3d_m=length_3d)


def write_outputs(segments, overlay_size, geo):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = {"source": "dop_33409-5761 (PhotoCraft-Karten) + DGM dgm_33409-5761", "crs": "EPSG:25833",
           "height_system": "DHHN2016 m", "step_m": STEP_M, "segments": {}}
    for seg in segments:
        d = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in seg.items()}
        if seg["closed"]:
            corners = find_corners(seg["radius_m"], seg["s_m"], CORNER_RADIUS_MAX_M, STEP_M, CORNER_MERGE_GAP_M)
            d["corners"] = corners
            # find_corners() fasst zusammen, solange R < 100 m bleibt: Auf der glatten Linie sind das ganze Kurvenfolgen.
            # Die einzelnen Scheitel stehen deshalb hier.
            r = np.minimum(seg["radius_m"], 500.0)
            d["apexes"] = [{"s_m": float(seg["s_m"][i]), "radius_m": float(r[i])}
                           for i in argrelmin(r, order=25, mode="wrap")[0] if r[i] < CORNER_RADIUS_MAX_M]
            d["radius_m"] = [None if not np.isfinite(r) else r for r in seg["radius_m"].tolist()]
        out["segments"][seg["name"]] = d
    with open(MODEL_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    main = segments[0]
    with open(MODEL_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["s_m", "e_m", "n_m", "z_m", "e_left_m", "n_left_m", "z_left_m", "e_right_m", "n_right_m",
                    "z_right_m", "width_m", "radius_m", "grade", "bank", "left_line_seen", "right_line_seen"])
        for i in range(len(main["s_m"])):
            r = main["radius_m"][i]
            w.writerow([f"{main['s_m'][i]:.2f}", *[f"{x:.3f}" for x in main["center"][i]], f"{main['z_center'][i]:.3f}",
                        *[f"{x:.3f}" for x in main["left"][i]], f"{main['z_left'][i]:.3f}",
                        *[f"{x:.3f}" for x in main["right"][i]], f"{main['z_right'][i]:.3f}",
                        f"{main['width_m'][i]:.2f}", "" if not np.isfinite(r) else f"{r:.1f}",
                        f"{main['grade'][i]:.5f}", f"{main['bank'][i]:.5f}",
                        int(main["left_line_seen"][i]), int(main["right_line_seen"][i])])

    from PIL import ImageDraw
    img = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    def to_px(p):
        return [((e - geo[0]) / geo[2] - CROP_COL, (geo[1] - n) / geo[2] - CROP_ROW) for e, n in p]
    colors = {"main_loop": (255, 40, 40, 255), "pit_lane": (40, 170, 255, 255),
              "connector_A": (255, 170, 0, 255), "connector_C": (255, 0, 255, 255)}
    for seg in segments:
        c = colors[seg["name"]]
        for key in ("left", "right"):
            pts = to_px(seg[key])
            draw.line(pts + pts[:1] if seg["closed"] else pts, fill=c, width=1)
    img.save(OVERLAY_PNG)


def straight_jitter(points):
    """Abweichung von der Ausgleichsgeraden auf dem laut OSM geraden Stueck (Test wie spreewaldring_track.py)."""
    p = points - points.mean(axis=0)
    u = np.linalg.svd(p, full_matrices=False)[2][0]
    dev = p @ np.array([-u[1], u[0]])
    return float(np.abs(dev).max()), float(dev.std())


def main():
    geo = read_geo()
    line_img, asphalt_img = load_map(LINEMAP_PATH), load_map(ASPHALT_PATH)
    assert line_img.shape == asphalt_img.shape
    with open(SURFACE_SUMMARY_PATH, encoding="utf-8") as f:
        old = json.load(f)["segments"]
    em = ElevationModel(use_berlin_fallback=False)

    guide = np.array(old["main_loop"]["centerline_utm33"], dtype=float)
    gray_img = load_map(GRAY_PATH)
    hints = load_hints(SKETCH_PATH, geo)
    segments = [build_segment("main_loop", guide, line_img, asphalt_img, geo, em, True, hints=hints["main_loop"])]
    for name, search in (("connector_A", (1.5, 12.0)), ("connector_C", (1.5, 12.0))):
        segments.append(build_segment(name, np.array(old[name]["centerline_utm33"], dtype=float), line_img,
                                      asphalt_img, geo, em, False, search))
    segments.append(build_segment("pit_lane", np.array(old["pit_lane"]["centerline_utm33"], dtype=float), line_img,
                                  asphalt_img, geo, em, False, (0.5, 10.0), True, gray_img, hints["pit_lane"], -1))
    write_outputs(segments, line_img.shape[::-1], geo)

    for seg in segments:
        w = seg["width_m"]
        seen = (seg["left_line_seen"].mean() * 100, seg["right_line_seen"].mean() * 100)
        print(f"{seg['name']}: Laenge 2D {seg['length_2d_m']:.1f} m, 3D {seg['length_3d_m']:.1f} m, Breite Median "
              f"{np.median(w):.2f} m (p5 {np.percentile(w, 5):.2f}, p95 {np.percentile(w, 95):.2f}), Linie gesehen "
              f"links {seen[0]:.0f} %, rechts {seen[1]:.0f} %, Hoehe {seg['z_center'].min():.2f}-{seg['z_center'].max():.2f} m")
    main_seg = segments[0]
    print(f"Hauptschleife: Steigung |p50| {np.median(np.abs(main_seg['grade'])) * 100:.2f} %, |p95| "
          f"{np.percentile(np.abs(main_seg['grade']), 95) * 100:.2f} %, Querneigung |p50| "
          f"{np.median(np.abs(main_seg['bank'])) * 100:.2f} %, |p95| {np.percentile(np.abs(main_seg['bank']), 95) * 100:.2f} %")
    c = main_seg["center"]
    a, b = np.array(old["main_loop"]["centerline_utm33"])[[752, 797]]  # laut OSM gerade Kante, siehe Logbuch
    i0, i1 = sorted(int(np.argmin(np.linalg.norm(c - q, axis=1))) for q in (a, b))
    for key in ("center", "left", "right"):
        print(f"Gerade s={i0}-{i1} m, {key}: max {straight_jitter(main_seg[key][i0:i1 + 1])[0]:.2f} m, "
              f"Streuung {straight_jitter(main_seg[key][i0:i1 + 1])[1]:.3f} m")


if __name__ == "__main__":
    main()
