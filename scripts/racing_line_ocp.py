"""
Zeitoptimale Ideallinie als Optimalsteuerungsproblem (freie Bahn), 08.11.2026

Nutzerauftrag: recherchieren, wie eine echte Ideallinie berechnet wird, und sie fuer Oschersleben rechnen. Stand der
Technik ist das Minimum-Lap-Time-Problem mit freier Bahn: Linie und Tempo sind EIN Optimierungsproblem, Zielfunktion ist
die Rundenzeit (Veneri/Massaro 2019, Christ/Heilmeier TUM 2021; Minimalkruemmung allein verliert bis 4 %, arXiv
2511.13522). Direkte Transkription, geloest mit IPOPT (CasADi). Die Linie entsteht, sie wird nicht vorgegeben.

Variablen je Punkt des Rasters: Querversatz n, Tempo v, Beschleunigen ap >= 0, Bremsen ab >= 0. Das Auto bleibt
unveraendert (nur gelesen):
  - Ellipse quer/laengs aus dem Vehicle, auf der Kruemmung in voller Rasteraufloesung UND auf der Fenster-Kruemmung
    von simulate_lap_full() (LAP_CURVATURE_WINDOW_M). Nur ein Fenster reicht nicht: Der erste Lauf (08.11., noch 15 m) fand
    113,09 s mit Knicken, die das Fenster nicht sah (16 g bei 3 m Aufloesung). Stetige Kruemmung wird belohnt (CURVATURE_RATE_WEIGHT).
  - Antrieb: Volllast-Huellkurve ueber die Gaenge (accel_true, inkl. Limiter, Luft, Traktion)
  - Querruck im 0,5-s-Mittel wie im Koordinatenabstieg (Leitplanke aus den NS-R2-Logs). Mittelwert ueber 0,5 s
    abgeleitet = (y(t+0,25) - y(t-0,25)) / 0,5; die Punktpaare kommen aus der vorigen Loesung, daher aeussere Iterationen.
Die Schaltluecken kennt das NLP nicht. Bewertet wird deshalb jede Loesung nur mit simulate_lap_full().

Aufruf: .venv/bin/python scripts/racing_line_ocp.py [--track oschersleben]
"""
import argparse
import json
import os
import time

import casadi as ca
import numpy as np
from scipy.ndimage import gaussian_filter1d

import spreewaldring_lap_time_optimizer as O
import spreewaldring_racing_line_editor as E
import spreewaldring_racing_line_optimal as SIM
from drivetrain_model_validation import GEAR_RATIOS, rpm_from_speed
from performance_simulation import REDLINE_RPM
from spreewaldring_lap_simulation import accel_true
from spreewaldring_racing_line_optimal import DEFAULT_TYRE, VEHICLES, simulate_lap_full
from spreewaldring_track import G, RESULTS_DIR

ENGINE_GRID_MS = np.arange(6.0, 60.01, 0.5)
ENGINE_SMOOTH_SIGMA_MS = 0.5   # die Huellkurve hat Knicke an den Schaltpunkten, IPOPT braucht sie glatt
CURVATURE_RATE_WEIGHT = 20.0   # Strafe auf die Kruemmungsaenderung je Rasterschritt, Groessenordnung 0,01-0,05 s je Runde
CONTROL_WEIGHT = 1e-5          # macht ap/ab eindeutig
JERK_MARGIN_START = 0.98       # IPOPT arbeitet auf Punktpaaren, die Pruefung auf 0,05-s-Raster
OUTER_ITERATIONS = 6
CONVERGED_S = 0.002
IPOPT_OPTIONS = {"ipopt.print_level": 0, "print_time": 0, "ipopt.max_iter": 1500, "ipopt.tol": 1e-5,
                 "ipopt.acceptable_tol": 1e-4, "ipopt.mu_strategy": "adaptive", "ipopt.sb": "yes"}


def engine_table(veh):
    """Volllast-Huellkurve ueber alle Gaenge unterhalb der Redline, geglaettet, auf ENGINE_GRID_MS."""
    v = np.arange(ENGINE_GRID_MS[0] - 4.0, ENGINE_GRID_MS[-1] + 4.0, 0.1)
    a = np.array([max(accel_true(x, g, veh.speed_factor)[0] for g in GEAR_RATIOS
                      if rpm_from_speed(x / veh.speed_factor, g) <= REDLINE_RPM) for x in v])
    return np.interp(ENGINE_GRID_MS, v, gaussian_filter1d(a, ENGINE_SMOOTH_SIGMA_MS / 0.1, mode="nearest"))


def jerk_pairs(ds, v):
    """Fuer jeden Punkt die Punkte 0,25 s davor und danach, aus der Fahrzeit der vorigen Loesung."""
    n = len(ds)
    dt = ds / v
    t = np.r_[0.0, np.cumsum(np.r_[dt, dt, dt])][:3 * n]
    i0 = np.arange(n) + n
    return ((np.searchsorted(t, t[i0] + 0.25) % n), (np.searchsorted(t, t[i0] - 0.25) % n))


def solve_nlp(track, veh, engine, n_start, v_start, jerk_margin=JERK_MARGIN_START):
    """Ein NLP, Punktpaare fuer den Querruck aus der Startloesung. Liefert (n, v, IPOPT-Status)."""
    N = len(track.center)
    nv, vv = ca.SX.sym("n", N), ca.SX.sym("v", N)
    ap, ab = ca.SX.sym("ap", N), ca.SX.sym("ab", N)
    nxt, prv = np.roll(np.arange(N), -1).tolist(), np.roll(np.arange(N), 1).tolist()
    w = max(1, round(SIM.LAP_CURVATURE_WINDOW_M / float(np.median(np.hypot(*(np.roll(track.center, -1, axis=0) - track.center).T)))))
    wn, wp = np.roll(np.arange(N), -w).tolist(), np.roll(np.arange(N), w).tolist()
    x = ca.DM(track.center[:, 0]) + nv * ca.DM(track.perp[:, 0])
    y = ca.DM(track.center[:, 1]) + nv * ca.DM(track.perp[:, 1])

    ds = ca.sqrt((x[nxt] - x) ** 2 + (y[nxt] - y) ** 2 + 1e-9)
    side = lambda dx, dy: ca.sqrt(dx ** 2 + dy ** 2 + 1e-9)
    def menger(before, after):
        """Kruemmung mit Vorzeichen aus den Punkten before, i, after."""
        ux, uy, wx, wy = x - x[before], y - y[before], x[after] - x[before], y[after] - y[before]
        return 2 * (ux * wy - wx * uy) / (side(ux, uy) * side(x[after] - x, y[after] - y) * side(wx, wy))

    kappa, kappa_window = menger(prv, nxt), menger(wp, wn)
    a_lat = vv ** 2 * kappa
    ay, ax, bx = veh.lat_g * G, veh.accel_g * G, veh.brake_g * G
    rho2, rho2_window = (a_lat / ay) ** 2, (vv ** 2 * kappa_window / ay) ** 2
    a_eng = ca.vertcat(*[ca.pw_lin(vv[i], ca.DM(ENGINE_GRID_MS), ca.DM(engine)) for i in range(N)])

    g, lbg, ubg = [], [], []
    def add(expr, lo, hi):
        g.append(expr); lbg.extend([lo] * expr.shape[0]); ubg.extend([hi] * expr.shape[0])
    add(vv[nxt] ** 2 - vv ** 2 - 2 * (ap - ab) * ds, 0.0, 0.0)
    add(ap - a_eng, -ca.inf, 0.0)
    # Ellipse an beiden Enden des Abschnitts, die Simulation mischt die Punkte je nach Durchlauf
    for r in (rho2, rho2[nxt], rho2_window, rho2_window[nxt]):
        add((ap / ax) ** 2 + r, -ca.inf, 1.0)
        add((ab / bx) ** 2 + r, -ca.inf, 1.0)
    ds_start = np.hypot(*(np.roll(E.points_from_n_lat(track, n_start), -1, axis=0) - E.points_from_n_lat(track, n_start)).T)
    ip, im = jerk_pairs(ds_start, v_start)
    limit = jerk_margin * O.LAT_JERK_MAX_G_S * G * 0.5
    add(a_lat[ip.tolist()] - a_lat[im.tolist()], -limit, limit)

    time_s = ca.sum1(ds * 0.5 * (1 / vv + 1 / vv[nxt]))
    objective = time_s + CURVATURE_RATE_WEIGHT * ca.sum1((kappa[nxt] - kappa) ** 2) + CONTROL_WEIGHT * ca.sum1(ap + ab)

    margin_mid = (track.lo + track.hi) / 2
    n_lo, n_hi = np.minimum(track.lo + E.EDGE_MARGIN_M, margin_mid), np.maximum(track.hi - E.EDGE_MARGIN_M, margin_mid)
    a_start = (np.roll(v_start, -1) ** 2 - v_start ** 2) / (2 * ds_start)
    z = ca.vertcat(nv, vv, ap, ab)
    x0 = np.r_[np.clip(n_start, n_lo, n_hi), v_start, np.maximum(a_start, 0.0), np.maximum(-a_start, 0.0)]
    lbx = np.r_[n_lo, np.full(N, 5.0), np.zeros(2 * N)]
    ubx = np.r_[n_hi, np.full(N, float(ENGINE_GRID_MS[-1])), np.full(N, ax), np.full(N, bx)]
    solver = ca.nlpsol("ocp", "ipopt", {"x": z, "f": objective, "g": ca.vertcat(*g)}, IPOPT_OPTIONS)
    res = solver(x0=x0, lbx=lbx, ubx=ubx, lbg=lbg, ubg=ubg)
    sol = np.array(res["x"]).ravel()
    return sol[:N], sol[N:2 * N], solver.stats()["return_status"], float(res["f"])


def evaluate(track, veh, n_lat):
    """Bewertung nur mit der unveraenderten Simulation, dazu die Grenzen wie im Koordinatenabstieg."""
    pts = E.points_from_n_lat(track, n_lat)
    sim = simulate_lap_full(pts, veh)
    jerk, yaw = O.transients(pts, sim)
    return sim, {"lap_time_s": sim["lap_time_s"], "lat_jerk_max": jerk, "yaw_acc_max": yaw,
                 "steer_rate_max": float(np.max(np.abs(E.steer_rate_deg_s(pts, sim))))}


def solve_from(track, veh, engine, n_start, label):
    """Aeussere Iterationen ab einer Startlinie; die Querruck-Marge folgt der Pruefung. Beste zulaessige nach Simulation."""
    sim, info = evaluate(track, veh, n_start)
    best = (np.inf, None, None)
    n_cur, v_cur, margin = n_start, sim["v"], JERK_MARGIN_START
    print(f"[{label}] Start: {info['lap_time_s']:.3f} s", flush=True)
    for it in range(OUTER_ITERATIONS):
        t0 = time.time()
        n_cur, v_cur, status, obj = solve_nlp(track, veh, engine, n_cur, v_cur, margin)
        sim, info = evaluate(track, veh, n_cur)
        feasible = (info["lat_jerk_max"] <= O.LAT_JERK_MAX_G_S and info["yaw_acc_max"] <= O.YAW_ACC_MAX_DEG_S2
                    and info["steer_rate_max"] <= E.STEER_RATE_MAX_DEG_S)
        print(f"[{label}] Iteration {it}: {status}, NLP {obj:.3f} s, Simulation {info['lap_time_s']:.3f} s, Querruck {info['lat_jerk_max']:.2f} (Marge {margin:.3f}), Gier "
              f"{info['yaw_acc_max']:.1f}, Lenkrate {info['steer_rate_max']:.1f}, {time.time() - t0:.0f} s", flush=True)
        converged = feasible and best[1] is not None and abs(info["lap_time_s"] - best[0]) < CONVERGED_S
        if feasible and info["lap_time_s"] < best[0]:
            best = (info["lap_time_s"], n_cur.copy(), info)
        if converged:
            break
        if not feasible:
            margin *= 0.995 * O.LAT_JERK_MAX_G_S / info["lat_jerk_max"]
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--track", default="oschersleben", choices=list(E.TRACKS))
    args = parser.parse_args()
    veh = VEHICLES[DEFAULT_TYRE]
    editor = E.Editor(args.track)
    track = editor.track
    engine = engine_table(veh)
    starts = {"Mittellinie": np.zeros(len(track.center)), "Editor-Start": editor.n_lat_start}
    user_path = f"data/track/{args.track}_nutzerlinie_2026-11-08.json"
    if os.path.exists(user_path):
        with open(user_path, encoding="utf-8") as f:
            edits = editor.parse_edits(json.load(f))
        starts["Nutzerlinie"] = E.recompute_from_edits(track, editor.baseline(DEFAULT_TYRE), edits, veh)

    results = {label: solve_from(track, veh, engine, n, label) for label, n in starts.items()}
    label = min(results, key=lambda k: results[k][0])
    lap, n_lat, info = results[label]
    if n_lat is None:
        raise RuntimeError("Keine Loesung haelt die Grenzen ein")
    out = os.path.join(RESULTS_DIR, f"{args.track}_ideal_line.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"method": "free-trajectory minimum lap time, IPOPT", "vehicle": DEFAULT_TYRE, "lap_time_s": lap,
                   "best_start": label, "limits": info, "n_lat": [round(float(x), 6) for x in n_lat],
                   "by_start": {k: v[0] for k, v in results.items()}}, f, indent=1, ensure_ascii=False)
    print(f"Beste Loesung ab {label}: {lap:.3f} s -> {out}", flush=True)
    print({k: round(v[0], 3) for k, v in results.items()}, flush=True)


if __name__ == "__main__":
    main()
