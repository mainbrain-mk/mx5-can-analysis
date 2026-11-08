"""
Spreewaldring: Ideallinie direkt auf die Rundenzeit optimiert, 08.11.2026

Der alternierende Optimierer (spreewaldring_racing_line_optimal.py, Editor-Ausgangslinie) glaettet die Linie dort, wo
der Grip knapp wird, und nutzt die Rundenzeit nur als Abbruchregel. Eine Nutzerlinie aus dem Editor war damit 1,41 s
schneller (102,94 statt 104,35 s), verteilt ueber fast alle Abschnitte. Dieses Skript minimiert die Rundenzeit von
simulate_lap_full() direkt.

Linie = Startlinie + periodischer kubischer Spline durch Querversaetze an Stuetzpunkten alle SPACING Punkte (~12 m).
Suche: Koordinatenabstieg, je Stuetzpunkt +Schritt/-Schritt, nur Verbesserungen bleiben; Schritte STEPS_M, je Schritt
Durchgaenge bis keiner mehr MIN_GAIN_S bringt (hoechstens MAX_SWEEPS). Grenzen wie im Editor (Korridor mit
EDGE_MARGIN_M, Lenkgeschwindigkeit STEER_RATE_MAX_DEG_S) plus die in den NS-R2-Logs gezeigten Uebergaenge als
Leitplanke (Logbuch "Umsetzen und enge Kehren", 08.11.): Querruck und Gierbeschleunigung im 0,5-s-Mittel.
Streckenrand: Nutzerentscheid 08.11., wie bisher innerhalb der weissen Linie, keine Curbs.

Ausgabe results/spreewaldring_lap_time_optimizer.json nach jedem Durchgang: Linie (nLat), Rundenzeit, Verlauf, und
harte Fixpunkte an allen Punkten fuer den Editor-Import (Rundenzeit im Editor wird am Ende nachgerechnet).

Aufruf: .venv/bin/python scripts/spreewaldring_lap_time_optimizer.py [--start edits.json] [--spacing 4]
"""
import argparse
import json
import os
import time

import numpy as np
from scipy.interpolate import CubicSpline

import spreewaldring_racing_line_editor as E
from spreewaldring_racing_line_optimal import simulate_lap_full
from spreewaldring_track import CURVATURE_WINDOW_M, G, RESULTS_DIR

OUT_PATH = os.path.join(RESULTS_DIR, "spreewaldring_lap_time_optimizer.json")
EDITOR_IMPORT_PATH = os.path.join(RESULTS_DIR, "spreewaldring_lap_time_optimizer_editor_import.json")  # nur die Liste
SPACING = 4
STEPS_M = (0.4, 0.2, 0.1, 0.05)
MAX_SWEEPS = 4
MIN_GAIN_S = 0.002          # ein Durchgang, der weniger bringt, beendet den Schritt
ACCEPT_GAIN_S = 1e-4        # einzelne Verschiebung: darunter ist es Rauschen des Gangplans
LAT_JERK_MAX_G_S = 2.7      # hoechster Querruck je NS-R2-Fahrt (0,5-s-Mittel)
YAW_ACC_MAX_DEG_S2 = 108.0  # hoechste Gierbeschleunigung (0,5-s-Mittel)
TRANSIENT_DT_S, TRANSIENT_WINDOW_S = 0.05, 0.5


def transients(pts, sim):
    """Hoechster Querruck [g/s] und hoechste Gierbeschleunigung [Grad/s^2] der Runde, 0,5-s-Mittel wie in den Logs."""
    w = max(1, round(CURVATURE_WINDOW_M / float(np.median(sim["ds"]))))
    sign = E.turn_sign(pts, w)
    t = np.arange(0.0, sim["lap_time_s"], TRANSIENT_DT_S)
    k = int(round(TRANSIENT_WINDOW_S / TRANSIENT_DT_S))
    out = []
    for x in (sign * sim["v"] ** 2 / np.maximum(sim["radius"], 1e-6) / G,
              np.degrees(sign * sim["v"] / np.maximum(sim["radius"], 1e-6))):
        y = np.interp(t, sim["t"], x)
        y = np.convolve(np.r_[y[-k:], y, y[:k]], np.ones(k) / k, mode="same")[k:-k]  # geschlossene Runde
        out.append(float(np.max(np.abs(np.gradient(y, TRANSIENT_DT_S)))))
    return out


class Problem:
    def __init__(self, track, n_start, veh, cap, spacing):
        self.track, self.n_start, self.veh, self.cap = track, n_start, veh, cap
        n = len(n_start)
        self.knots = np.arange(0, n, spacing)
        self.x = np.r_[self.knots, n].astype(float)
        self.evals = 0

    def line(self, delta):
        spline = CubicSpline(self.x, np.r_[delta, delta[0]], bc_type="periodic")
        return E.clamp_inset(self.track, self.n_start + spline(np.arange(len(self.n_start))))

    def evaluate(self, delta):
        """(Rundenzeit, Linie, Kennwerte); Rundenzeit inf, wenn eine Grenze verletzt ist."""
        self.evals += 1
        n_lat = self.line(delta)
        pts = E.points_from_n_lat(self.track, n_lat)
        sim = simulate_lap_full(pts, self.veh, brake_cap_g=self.cap)
        steer = float(np.max(np.abs(E.steer_rate_deg_s(pts, sim))))
        jerk, yaw_acc = transients(pts, sim)
        ok = steer <= E.STEER_RATE_MAX_DEG_S and jerk <= LAT_JERK_MAX_G_S and yaw_acc <= YAW_ACC_MAX_DEG_S2
        info = {"lap_time_s": sim["lap_time_s"], "steer_rate_max": steer, "lat_jerk_max": jerk, "yaw_acc_max": yaw_acc}
        return (sim["lap_time_s"] if ok else np.inf), n_lat, info


def save(problem, delta, best, info, history, start_info, done):
    n_lat = problem.line(delta)
    # Harte Fixpunkte an JEDEM Punkt: nur so baut der Editor exakt dieselbe Linie (nur an den Stuetzpunkten glaettet
    # er dazwischen anders, Test 08.11.: bis 0,84 m und 0,2 s Abweichung). Import dauert im Editor ~6 s.
    pins = [{"index": i, "value": round(float(x), 6), "radius": E.BRUSH_RADIUS_M_DEFAULT, "hard": True}
            for i, x in enumerate(n_lat)]
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"lap_time_s": best, "start": start_info, "best": info, "done": done, "evaluations": problem.evals,
                   "spacing": int(problem.knots[1] - problem.knots[0]), "history": history,
                   "n_lat": [round(float(x), 6) for x in n_lat], "editor_edits_hard": pins}, f, indent=1)
    with open(EDITOR_IMPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(pins, f)


def optimize(problem, rng):
    delta = np.zeros(len(problem.knots))
    best, _, info = problem.evaluate(delta)
    assert np.isfinite(best), f"Startlinie verletzt eine Grenze: {info}"
    start_info, history, t0 = info, [], time.time()
    print(f"Start: {best:.3f} s  {info}", flush=True)
    for step in STEPS_M:
        for sweep in range(MAX_SWEEPS):
            before = best
            for j in rng.permutation(len(delta)):
                for sign in (1.0, -1.0):
                    trial = delta.copy()
                    trial[j] += sign * step
                    lap, _, trial_info = problem.evaluate(trial)
                    if lap < best - ACCEPT_GAIN_S:
                        delta, best, info = trial, lap, trial_info
                        break
            history.append({"step_m": step, "sweep": sweep, "lap_time_s": best, "evals": problem.evals,
                            "minutes": round((time.time() - t0) / 60, 1)})
            print(f"Schritt {step} m, Durchgang {sweep}: {best:.3f} s (-{before - best:.3f}), "
                  f"{problem.evals} Rechnungen, {(time.time() - t0) / 60:.1f} min", flush=True)
            save(problem, delta, best, info, history, start_info, done=False)
            if before - best < MIN_GAIN_S:
                break
    save(problem, delta, best, info, history, start_info, done=True)
    return delta, best


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--start", help="Editor-Bearbeitungen (JSON-Liste) als Startlinie, sonst Editor-Ausgangslinie")
    parser.add_argument("--spacing", type=int, default=SPACING)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    editor = E.Editor()
    name, veh, cap = editor.vehicle({"vehicle": E.DEFAULT_TYRE})
    base = editor.baseline(name)
    n_start = base
    if args.start:
        with open(args.start, encoding="utf-8") as f:
            n_start = E.recompute_from_edits(editor.track, base, editor.parse_edits(json.load(f)), veh, False, cap)
    problem = Problem(editor.track, n_start, veh, cap, args.spacing)
    delta, best = optimize(problem, np.random.default_rng(args.seed))
    # Gegenprobe: dieselbe Linie ueber den Editor-Weg
    with open(OUT_PATH, encoding="utf-8") as f:
        out = json.load(f)
    n_editor = E.recompute_from_edits(editor.track, base, editor.parse_edits(out["editor_edits_hard"]), veh, False, cap)
    lap_editor = simulate_lap_full(E.points_from_n_lat(editor.track, n_editor), veh, brake_cap_g=cap)["lap_time_s"]
    out["editor_lap_time_s"] = lap_editor
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"Fertig: {best:.3f} s (Start {out['start']['lap_time_s']:.3f} s), im Editor mit harten Fixpunkten "
          f"{lap_editor:.3f} s -> {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
