"""
Spreewaldring - lokale Kurven-Nachoptimierung, alle Kurven nacheinander
(30.08.2026)

Nutzerauftrag: die Kurven sollen EINZELN NACHEINANDER lokal optimiert
werden (jede Kurve baut auf dem Ergebnis der vorherigen auf), OHNE einen
globalen Optimierungslauf ueber die ganze Strecke vorzuschalten (Nutzer-
Feedback: der vorherige Ansatz startete noch auf dem Ergebnis von
`spreewaldring_racing_line_optimal.py`, das war nicht mehr gewuenscht).
Start ist jetzt die unveraenderte Streckenmittellinie. Abbruch pro Kurve:
sobald eine Iteration schlechter wird als die bisherige Bestzeit dieser
Kurve, wird zur naechsten Kurve weitergegangen.

METHODIK: wie zuvor (Geschwindigkeit<->Linie abwechselnd, kombinierter
Reifenkraftkreis aus `spreewaldring_racing_line_optimal.py`), Linien-
Anpassung auf ein Fenster um die jeweilige Kurve begrenzt (Rest der Runde
eingefroren). Fenstergrenzen jetzt AUTOMATISCH aus der Kurve selbst
(s_start_m/s_end_m aus `corners_refined`) plus feste Puffer
(`BRAKE_BUFFER_M` vor der Kurve, `EXIT_BUFFER_M` danach) - keine
Hand-Justage mehr pro Kurve noetig. Fenster ist ZYKLISCH indiziert (wichtig
fuer Kurve 1, die genau am Schliesspunkt der Schleife liegt).

Aufruf: .venv/bin/python scripts/spreewaldring_racing_line_corner.py
"""
import os
import json
import math

import numpy as np

from spreewaldring_track import (
    RESULTS_DIR, RESAMPLE_STEP_M, CURVATURE_WINDOW_M, G,
    compute_curvature_radius, resample_closed_loop,
)
from spreewaldring_track_surface import compute_tangents_closed
from spreewaldring_lap_simulation import TIRE_MU_RANGE, simulate_lap
from spreewaldring_racing_line import load_corridor, corridor_bounds
from spreewaldring_racing_line_optimal import simulate_lap_combined_friction, MU

TRACK_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_track_summary.json")

BRAKE_BUFFER_M = 150.0   # Fenster beginnt so weit VOR Kurvenstart (Anfahrt/Bremszone)
EXIT_BUFFER_M = 50.0     # Fenster endet so weit NACH Kurvenende
N_OUTER_LOCAL = 40
INNER_STEPS = 60
BASE_ALPHA = 0.18


def hann_taper(n_window):
    """Cosinus-Rampe 0->1->0 (glatter Uebergang zur eingefrorenen Nachbarstrecke)."""
    x = np.linspace(0, np.pi, n_window)
    return (1 - np.cos(x)) / 2


def window_indices(i_apex, s_lo, s_hi, step_m, n_total):
    """Zyklische Indexliste um i_apex, mit Modulo-Wrap ueber den Schliesspunkt."""
    lo = int(round(s_lo / step_m))
    hi = int(round(s_hi / step_m))
    return [(i_apex + k) % n_total for k in range(lo, hi + 1)]


def optimize_corner(center, lo_bound, hi_bound, perp, baseline_points, idx_window,
                     mu=MU, n_outer=N_OUTER_LOCAL, inner_steps=INNER_STEPS, base_alpha=BASE_ALPHA):
    """Optimiert nur die Punkte in idx_window, Rest bleibt eingefroren. Bricht
    ab, sobald eine Iteration schlechter wird als die bisherige Bestzeit
    (Nutzervorgabe) - liefert den seitlichen Versatz der BESTEN gefundenen
    Iteration zurueck, nicht den letzten."""
    n_total = len(center)
    n_lat = np.sum((baseline_points - center) * perp, axis=1)
    alpha_mask = np.zeros(n_total)
    alpha_mask[idx_window] = base_alpha * hann_taper(len(idx_window))

    snapshots = []
    best_lap_time, best_n_lat = None, n_lat.copy()
    for outer in range(n_outer):
        points = center + n_lat[:, None] * perp
        v, lap_time, radius = simulate_lap_combined_friction(points, mu)
        snapshots.append({
            "iteration": outer, "lap_time_s": lap_time,
            "points_utm33": points.tolist(), "v_kmh": (v * 3.6).tolist(),
            "radius_m": [None if not math.isfinite(r) else r for r in radius.tolist()],
        })
        print(f"    Iteration {outer:2d}: Rundenzeit={lap_time:.3f}s")

        if best_lap_time is not None and lap_time > best_lap_time:
            print(f"    -> schlechter als Bestzeit ({best_lap_time:.3f}s), Kurve beendet")
            break
        best_lap_time, best_n_lat = lap_time, n_lat.copy()

        a_lat_req = v ** 2 / np.maximum(radius, 1e-6)
        excess = np.clip(a_lat_req / (mu * G), 0.0, 1.0)
        alpha_i = alpha_mask * (0.1 + 0.9 * excess)
        for _ in range(inner_steps):
            p = center + n_lat[:, None] * perp
            p_avg = (np.roll(p, 1, axis=0) + np.roll(p, -1, axis=0)) / 2
            p_new = p + alpha_i[:, None] * (p_avg - p)
            n_new = np.sum((p_new - center) * perp, axis=1)
            n_lat[idx_window] = np.clip(n_new, lo_bound, hi_bound)[idx_window]

    return best_n_lat, best_lap_time, snapshots


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # KEIN globaler Optimierungslauf mehr vorgeschaltet (Nutzerfeedback) - Start
    # direkt auf der unveraenderten Streckenmittellinie, alle Verbesserungen
    # kommen ausschliesslich aus den einzelnen Kurven-Fenstern unten.
    center, left, right = load_corridor()
    tangents = compute_tangents_closed(center)
    lo_bound, hi_bound, perp = corridor_bounds(center, left, right, tangents)
    n_total = len(center)
    baseline_points = center
    _, baseline_lap_time, _ = simulate_lap_combined_friction(baseline_points, MU)
    print(f"Basis (Streckenmittellinie, keine Vorab-Optimierung): "
          f"Rundenzeit {baseline_lap_time:.3f}s")

    with open(TRACK_SUMMARY_PATH, encoding="utf-8") as f:
        track_summary = json.load(f)
    corners = sorted(track_summary["orthophoto_refinement"]["corners_refined"], key=lambda c: c["s_apex_m"])
    total_len_track = track_summary["orthophoto_refinement"]["total_length_m_refined"]
    # Kurve am Schliesspunkt der Schleife kann als zwei Eintraege auftauchen
    # (letzte + erste, <30m auseinander ueber den Wrap) - dieselbe physische
    # Kurve, sonst wuerde sie zweimal optimiert.
    wrap_dist = (total_len_track - corners[-1]["s_apex_m"]) + corners[0]["s_apex_m"]
    if wrap_dist < 30.0:
        corners = [min(corners[0], corners[-1], key=lambda c: c["radius_min_m"])] + corners[1:-1]

    current_points = baseline_points
    current_lap_time = baseline_lap_time
    per_corner_results = []
    all_snapshots = []

    for i, corner in enumerate(corners, start=1):
        i_apex = int(round(corner["s_apex_m"] / RESAMPLE_STEP_M)) % n_total
        s_lo = (corner["s_start_m"] - corner["s_apex_m"]) - BRAKE_BUFFER_M
        s_hi = (corner["s_end_m"] - corner["s_apex_m"]) + EXIT_BUFFER_M
        idx_window = window_indices(i_apex, s_lo, s_hi, RESAMPLE_STEP_M, n_total)
        print(f"\nKurve {i} (R={corner['radius_min_m']:.1f}m): Fenster s=[{s_lo:+.0f},{s_hi:+.0f}]m, "
              f"{len(idx_window)} Punkte")

        best_n_lat, best_lap_time, snapshots = optimize_corner(
            center, lo_bound, hi_bound, perp, current_points, idx_window)
        gain = current_lap_time - best_lap_time
        print(f"  Kurve {i}: {current_lap_time:.3f}s -> {best_lap_time:.3f}s ({gain:+.3f}s)")

        for s in snapshots:
            s["corner"] = i
        all_snapshots.extend(snapshots)
        per_corner_results.append({
            "corner_number": i, "radius_m": corner["radius_min_m"],
            "window_s_range_m": [s_lo, s_hi], "window_indices": idx_window,
            "lap_time_before_s": current_lap_time, "lap_time_after_s": best_lap_time,
            "gain_s": gain, "n_snapshots": len(snapshots),
        })
        current_points = center + best_n_lat[:, None] * perp
        current_lap_time = best_lap_time

    print(f"\n=== Gesamt: {baseline_lap_time:.3f}s -> {current_lap_time:.3f}s "
          f"({baseline_lap_time-current_lap_time:+.3f}s, mu={MU}) ===")

    # gleichmaessige Bogenlaenge fuer die Endauswertung (Punktabstand im Fenster
    # ist nach der Optimierung nicht mehr exakt RESAMPLE_STEP_M)
    points_final, _, total_len_final, _ = resample_closed_loop(current_points, RESAMPLE_STEP_M)
    radius_final = compute_curvature_radius(points_final, CURVATURE_WINDOW_M, RESAMPLE_STEP_M)
    s_line = np.arange(len(points_final)) * RESAMPLE_STEP_M

    with open(os.path.join(RESULTS_DIR, "spreewaldring_lap_simulation_summary.json"), encoding="utf-8") as f:
        centerline_summary = json.load(f)

    results = {}
    for label, mu_s in [("konservativ (mu=1.0)", TIRE_MU_RANGE[0]),
                         ("optimistisch (mu=1.3)", TIRE_MU_RANGE[1])]:
        v_final, lap_time_final = simulate_lap(s_line, radius_final, mu_s)
        t_before = centerline_summary["scenarios"][label]["lap_time_s"]
        print(f"--- {label}: Mittellinie {t_before:.2f}s -> nachher {lap_time_final:.2f}s "
              f"({t_before-lap_time_final:+.2f}s) ---")
        results[label] = {"lap_time_before_s": t_before, "lap_time_after_s": lap_time_final,
                           "v_max_kmh": float(v_final.max() * 3.6)}

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_corners_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "baseline_lap_time_s": baseline_lap_time,
            "brake_buffer_m": BRAKE_BUFFER_M, "exit_buffer_m": EXIT_BUFFER_M,
            "n_outer": N_OUTER_LOCAL, "inner_steps": INNER_STEPS, "base_alpha": BASE_ALPHA,
            "per_corner": per_corner_results,
            "racing_line_utm33": points_final.tolist(),
            "s_m": s_line.tolist(),
            "radius_m": [None if not math.isfinite(r) else r for r in radius_final.tolist()],
            "scenarios": results,
            "snapshots": all_snapshots,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {out_json}")


if __name__ == "__main__":
    main()
