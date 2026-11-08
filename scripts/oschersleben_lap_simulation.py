"""
Motorsport Arena Oschersleben - Rundenzeit-Simulation, 08.11.2026

Gleiche Physik und gleiches Fahrzeugmodell wie am Spreewaldring (simulate_lap_full(), NS-R2 aus den Logs), Strecke aus
oschersleben_track_model.py. Rechnet Mittellinie, alternierende Linie und mit --optimize die Linie direkt auf die
Rundenzeit (spreewaldring_lap_time_optimizer.py, Ausgabepfade hier umgebogen). Steigung und Querneigung sind in der
Simulation nicht drin, wie am Spreewaldring.

Ausgabe: results/oschersleben_lap_simulation.json, _optimizer.json, _speed_map.png
Aufruf: .venv/bin/python scripts/oschersleben_lap_simulation.py [--optimize]
"""
import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import spreewaldring_lap_time_optimizer as O
import spreewaldring_racing_line_editor as E
from oschersleben_track_model import LAP_SIM_JSON, MODEL_JSON
from spreewaldring_racing_line import corridor_bounds, load_corridor
from spreewaldring_racing_line_optimal import DEFAULT_TYRE, VEHICLES, optimize_alternating, simulate_lap_full
from spreewaldring_track import RESULTS_DIR
from spreewaldring_track_surface import compute_tangents_closed

OUT_JSON = LAP_SIM_JSON
OUT_MAP = os.path.join(RESULTS_DIR, "oschersleben_lap_simulation_speed_map.png")
O.OUT_PATH = os.path.join(RESULTS_DIR, "oschersleben_lap_simulation_optimizer.json")
O.EDITOR_IMPORT_PATH = os.path.join(RESULTS_DIR, "oschersleben_lap_simulation_optimizer_editor_import.json")


def corner_speeds(sim, s):
    """Scheitel = lokale Tempominima, wie sie die Simulation faehrt."""
    v = sim["v"] * 3.6
    idx = [i for i in range(len(v)) if v[i] == v[max(0, i - 15):i + 16].min() and v[i] < v.max() - 10]
    return [{"s_m": float(s[i]), "v_kmh": float(v[i]), "radius_m": float(sim["radius"][i])} for i in idx]


def report(label, pts, veh):
    sim = simulate_lap_full(pts, veh)
    print(f"{label}: {sim['lap_time_s']:.2f} s, v {sim['v'].min() * 3.6:.1f}-{sim['v'].max() * 3.6:.1f} km/h", flush=True)
    return sim


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--optimize", action="store_true", help="Linie direkt auf die Rundenzeit optimieren (rund 1 h)")
    args = parser.parse_args()
    center, left, right = load_corridor(MODEL_JSON)
    lo, hi, perp = corridor_bounds(center, left, right, compute_tangents_closed(center))
    veh = VEHICLES[DEFAULT_TYRE]
    s = np.arange(len(center)) * float(np.median(np.hypot(*(np.roll(center, -1, axis=0) - center).T)))

    sim_center = report(f"Mittellinie ({DEFAULT_TYRE})", center, veh)
    _, snapshots = optimize_alternating(center, lo, hi, perp, veh)
    best = min(snapshots, key=lambda snap: snap["lap_time_s"])
    n_alt = np.clip(np.sum((np.array(best["points_utm33"]) - center) * perp, axis=1), lo, hi)
    origin = center.mean(axis=0)
    track = E.Track(center - origin, left - origin, right - origin, perp, lo, hi)
    # wie Editor.baseline(): beseitigt Verletzungen der Lenkgeschwindigkeit an engen Scheiteln
    n_base = E.pinned_optimize(track, n_alt, [], veh)
    sim_base = report("Alternierend, bereinigt", E.points_from_n_lat(track, n_base), veh)

    n_best, sim_best, label = n_base, sim_base, "alternierend"
    if args.optimize:
        problem = O.Problem(track, n_base, veh, None, O.SPACING)
        delta, _ = O.optimize(problem, np.random.default_rng(1))
        n_best, label = problem.line(delta), "direkt auf Rundenzeit"
        sim_best = report("Direkt auf Rundenzeit", E.points_from_n_lat(track, n_best), veh)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"vehicle": DEFAULT_TYRE, "track_length_m": float(np.hypot(*(np.roll(center, -1, axis=0) - center).T).sum()), "best_line": label,
                   "lap_time_center_s": sim_center["lap_time_s"], "lap_time_alternating_s": sim_base["lap_time_s"],
                   "lap_time_best_s": sim_best["lap_time_s"], "v_max_kmh": float(sim_best["v"].max() * 3.6),
                   "corners": corner_speeds(sim_best, s), "n_lat": n_best.tolist(),
                   "v_kmh": (sim_best["v"] * 3.6).tolist()}, f, indent=1, ensure_ascii=False)
    pts = E.points_from_n_lat(track, n_best)
    fig, ax = plt.subplots(figsize=(13, 7))
    for edge in (track.left, track.right):
        ax.plot(*np.vstack([edge, edge[:1]]).T, color="0.6", lw=0.7)
    sc = ax.scatter(pts[:, 0], pts[:, 1], c=sim_best["v"] * 3.6, cmap="turbo", s=5)
    fig.colorbar(sc, label="km/h")
    ax.set_aspect("equal")
    ax.set_title(f"Oschersleben A-Kurs, {sim_best['lap_time_s']:.2f} s ({label}, {DEFAULT_TYRE})")
    fig.savefig(OUT_MAP, dpi=130, bbox_inches="tight")
    print(f"Fertig: {OUT_JSON}")


if __name__ == "__main__":
    main()
