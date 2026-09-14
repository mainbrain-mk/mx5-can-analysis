"""
Spreewaldring - Python-Port der Zeitoptimal-Physik aus
spreewaldring_racing_line_editor.py, fuer eine iterative, lokale CPU-Suche
nach einer Fixpunkt-Linie, die den Nutzer schlagen soll.

Nutzerauftrag (09.09.2026): "wenn ich dir erlaube alle von mir verschobenen
Punkte zu loeschen und eigene zu setzen, wirst du dann schneller als ich?
Darf iterativ in Python probiert werden. Token-effizient / moeglichst viel
lokal auf meiner CPU, ohne dass ich jede Ausgabe verarbeiten muss."

WICHTIG: reiner Port der ZEITOPTIMAL-Physik (nicht des experimentellen
natuerlichen Gasmodells) - das ist der objektive, formel-freie Benchmark
(nur "Vollgas sobald physikalisch moeglich"), gegen den auch der Nutzer sich
misst (101.065s mit seinen 25 Bearbeitungen, siehe PROJEKT_STAND.md
Nachtrag 09.09.2026).

Ablauf dieses Skripts:
  1. Laedt die Streckendaten direkt aus dem eingebetteten JSON in
     results/spreewaldring_racing_line_editor.html (kein Browser noetig).
  2. VALIDIERT den Port: spielt die 25 echten Bearbeitungen des Nutzers
     durch pinned_optimize() und vergleicht die Rundenzeit gegen den
     bekannten Referenzwert (101.06450049490951s, mehrfach im Browser
     bestaetigt). Weicht das um mehr als VALIDATION_TOL_S ab, bricht das
     Skript mit einer klaren "=== PORT-VALIDIERUNG FEHLGESCHLAGEN ==="-
     Meldung ab, statt mit einem stillschweigend falschen Modell
     weiterzusuchen.
  3. Greedy-Suche: startet bei 0 Bearbeitungen (= "alle geloeschten Punkte"),
     testet dann Zone fuer Zone (aus dem Grip-Excess-Profil der aktuellen
     Linie identifiziert) mehrere Wert/Radius-Kombinationen, behaelt nur
     echte Verbesserungen. Mehrere Durchgaenge bis Konvergenz oder Zeitlimit.
  4. Schreibt NUR kompakte Fortschrittszeilen (ein Marker pro neuem Bestwert)
     plus einen abschliessenden "=== ERGEBNIS ==="-Block, und speichert das
     beste Ergebnis als JSON (fuer einen spaeteren Re-Import in den
     interaktiven Editor).
"""

import json
import re
import time
import numpy as np

HTML_PATH = "results/spreewaldring_racing_line_editor.html"
OUT_JSON = "results/spreewaldring_racing_line_search_result.json"

USER_LAPTIME_ZEITOPTIMAL = 101.06450049490951
USER_EDITS = [
    {"index": 144, "value": -3.8009877908287146, "radius": 45},
    {"index": 314, "value": 4.379081228334963, "radius": 75},
    {"index": 387, "value": 4.1128485036821845, "radius": 75},
    {"index": 432, "value": -3.548108282717009, "radius": 75},
    {"index": 513, "value": -3.0850526727983265, "radius": 75},
    {"index": 468, "value": -4.100377144642209, "radius": 75},
    {"index": 613, "value": -2.5998771514771306, "radius": 75},
    {"index": 635, "value": -3.9473808247634983, "radius": 75},
    {"index": 714, "value": 2.549625511076419, "radius": 75},
    {"index": 807, "value": 4.350732826324758, "radius": 75},
    {"index": 13, "value": 3.3980194529286383, "radius": 120},
    {"index": 33, "value": 4.278277383977168, "radius": 120},
    {"index": 120, "value": -0.18612835730458144, "radius": 120},
    {"index": 297, "value": 3.0502273293187843, "radius": 60},
    {"index": 547, "value": 4.022644418335106, "radius": 60},
    {"index": 585, "value": -3.85913684656407, "radius": 60},
    {"index": 703, "value": -2.0314781374085102, "radius": 60},
    {"index": 681, "value": 0.21896041436266755, "radius": 60},
    {"index": 780, "value": 0.2834116392729722, "radius": 60},
    {"index": 562, "value": 3.826465097684138, "radius": 60},
    {"index": 354, "value": 1.2830645801871945, "radius": 60},
    {"index": 253, "value": -2.754118246949379, "radius": 60},
    {"index": 275, "value": 0.992125996558863, "radius": 60},
    {"index": 217, "value": 2.9502299155075145, "radius": 60},
    {"index": 232, "value": 0.623806707733642, "radius": 60},
]
VALIDATION_TOL_S = 0.05  # Sicherheitsnetz gegen einen stillen Portierungsfehler

# ---------- Daten laden ----------
with open(HTML_PATH) as f:
    html = f.read()
m = re.search(r'<script type="application/json" id="viz-data">(.*?)</script>', html, re.S)
data = json.loads(m.group(1))

N = len(data["centerline"])
CENTER = np.array(data["centerline"])
PERP = np.array(data["perp"])
LO = np.array(data["corridor_lo_m"])
HI = np.array(data["corridor_hi_m"])
N_LAT_START = np.array(data["n_lat_start"])

P = data["physics"]
G = P["G"]
MU = P["MU"]
RESAMPLE_STEP_M = P["RESAMPLE_STEP_M"]
CURVATURE_WINDOW_M = P["CURVATURE_WINDOW_M"]
V_MIN_MS = P["V_MIN_MS"]
REDLINE_RPM = P["REDLINE_RPM"]
GEAR_RATIOS = {int(k): v for k, v in P["GEAR_RATIOS"].items()}
FINAL_DRIVE = P["FINAL_DRIVE"]
R_DYN_M = P["R_DYN_M"]
ETA = P["ETA"]
RHO = P["RHO_KG_M3"]
CDA_M2 = P["CDA_M2"]
CRR = P["CRR"]
MASS_KG = P["MASS_KG"]
TRACTION_MAX_FORCE_N = P["TRACTION_MAX_FORCE_N"]
RPM_TABLE = P["RPM_TABLE"]
TORQUE_TABLE = P["TORQUE_TABLE_NM"]
ATTACK_SHIFT_S = {k: v for k, v in P["ATTACK_SHIFT_S"].items()}
BRAKE_CAP_G = P["BRAKE_CAP_G"]

OPTP = data["optimizer"]
BASE_ALPHA = OPTP["base_alpha"]
INNER_STEPS = OPTP["inner_steps"]
N_OUTER_MAX = OPTP["n_outer_max"]
BRUSH_RADIUS_DEFAULT = OPTP["brush_radius_m_default"]

STEER_RATE_MAX_DEG_S = 240.0
EDGE_MARGIN_M = 0.10
SOFT_PIN_PULL = 0.3
MIN_ACCEL_HOLD_M = 40.0


# ---------- Physik (Port aus spreewaldring_racing_line_editor.py) ----------
def interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            t = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + t * (ys[i + 1] - ys[i])
    return ys[-1]


def rpm_from_speed(v, gear):
    return (v / (2 * np.pi * R_DYN_M)) * GEAR_RATIOS[gear] * FINAL_DRIVE * 60.0


def accel(v, gear):
    rpm = rpm_from_speed(v, gear)
    torque = interp(rpm, RPM_TABLE, TORQUE_TABLE)
    f_wheel = min(torque * GEAR_RATIOS[gear] * FINAL_DRIVE * ETA / R_DYN_M, TRACTION_MAX_FORCE_N)
    f_drag = 0.5 * RHO * CDA_M2 * v * v
    f_roll = CRR * MASS_KG * G
    return (f_wheel - f_drag - f_roll) / MASS_KG


def coast_accel(v):
    f_drag = 0.5 * RHO * CDA_M2 * v * v
    f_roll = CRR * MASS_KG * G
    return -(f_drag + f_roll) / MASS_KG


def sqrt_nonneg(x):
    return np.sqrt(x) if x > 0 else 0.0


def best_gear_for_speed(v):
    best, best_a = 6, -np.inf
    for gear in range(1, 7):
        if rpm_from_speed(v, gear) > REDLINE_RPM:
            continue
        a = accel(v, gear)
        if a > best_a:
            best_a, best = a, gear
    return best


def forward_step_shifted(v, gear, shift_remaining, ds, radius, mu):
    if shift_remaining > 0:
        a_coast = coast_accel(v)
        v_full = sqrt_nonneg(v * v + 2 * a_coast * ds)
        t_full = 2 * ds / (v + v_full) if (v + v_full) > 1e-9 else 0
        if t_full <= shift_remaining:
            return v_full, gear, shift_remaining - t_full
        t = shift_remaining
        v_end = max(v + a_coast * t, 0)
        ds_used = v * t + 0.5 * a_coast * t * t
        return forward_step_shifted(v_end, gear + 1, 0, max(ds - ds_used, 0), radius, mu)
    a_lat = min(v * v / max(radius, 1e-6), mu * G)
    a_long_avail = sqrt_nonneg((mu * G) ** 2 - a_lat ** 2)
    if gear < 6:
        rpm = rpm_from_speed(v, gear)
        a_cur = min(accel(v, gear), a_long_avail)
        a_next = min(accel(v, gear + 1), a_long_avail)
        if rpm >= REDLINE_RPM or a_next > a_cur:
            dur = ATTACK_SHIFT_S[f"{gear}-{gear+1}"]
            return forward_step_shifted(v, gear, dur, ds, radius, mu)
    a_use = min(accel(v, gear), a_long_avail)
    v_new = sqrt_nonneg(v * v + 2 * a_use * ds)
    return v_new, gear, shift_remaining


def points_from_nlat(nlat):
    return CENTER + nlat[:, None] * PERP


def curvature_radius(points, window_m, step_m):
    n = len(points)
    w = max(1, round(window_m / step_m))
    p0 = np.roll(points, w, axis=0)
    p1 = points
    p2 = np.roll(points, -w, axis=0)
    a = np.hypot(p1[:, 0] - p0[:, 0], p1[:, 1] - p0[:, 1])
    b = np.hypot(p2[:, 0] - p1[:, 0], p2[:, 1] - p1[:, 1])
    c = np.hypot(p2[:, 0] - p0[:, 0], p2[:, 1] - p0[:, 1])
    area2 = np.abs((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        radii = (a * b * c) / (2 * area2)
    radii = np.where((area2 < 1e-6) | (a * b * c < 1e-6), np.inf, radii)
    return radii


def clamp_inset(val, i):
    mid = (LO[i] + HI[i]) / 2
    lo_eff = min(LO[i] + EDGE_MARGIN_M, mid)
    hi_eff = max(HI[i] - EDGE_MARGIN_M, mid)
    return min(max(val, lo_eff), hi_eff)


def clamp_inset_arr(val):
    mid = (LO + HI) / 2
    lo_eff = np.minimum(LO + EDGE_MARGIN_M, mid)
    hi_eff = np.maximum(HI - EDGE_MARGIN_M, mid)
    return np.clip(val, lo_eff, hi_eff)


def relax_once(nlat, alpha_arr, hard_targets):
    pts = points_from_nlat(nlat)
    prev = np.roll(pts, 1, axis=0)
    nxt = np.roll(pts, -1, axis=0)
    avg = (prev + nxt) / 2
    p_new = pts + alpha_arr[:, None] * (avg - pts)
    n_val = (p_new[:, 0] - CENTER[:, 0]) * PERP[:, 0] + (p_new[:, 1] - CENTER[:, 1]) * PERP[:, 1]
    n_new = clamp_inset_arr(n_val)
    if hard_targets:
        for idx, val in hard_targets:
            n_new[idx] = val
    return n_new


WHEELBASE_M = 2.31
STEERING_RATIO = 15.0


def compute_steer_rate_deg_s(pts, radius, v_final, ds_arr):
    # Exakter Port von computeSteerRateDegS() - siehe dort fuer die Begruendung
    # der Fensterbreite (CURVATURE_WINDOW_M statt Nachbarpunkte).
    n = len(pts)
    step_m = float(np.median(ds_arr))
    w = max(1, round(CURVATURE_WINDOW_M / step_m))
    idx = np.arange(n)
    prev_i = (idx - w) % n
    next_i = (idx + w) % n
    p0, p1, p2 = pts[prev_i], pts, pts[next_i]
    cross = (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1])
    turn_sign = np.where(cross > 0, -1.0, np.where(cross < 0, 1.0, 0.0))
    theta = np.arctan(WHEELBASE_M / np.maximum(radius, 1)) * (180 / np.pi) * STEERING_RATIO * turn_sign
    # Vektorisierte Fenster-Bogenlaenge (2w Segmente ab prev_i[i]) - Fenster ist
    # immer << n, ein einmal "verdoppeltes" ds reicht als Ringpuffer-Ersatz.
    csum_ext = np.concatenate([[0.0], np.cumsum(np.concatenate([ds_arr, ds_arr]))])
    span = csum_ext[prev_i + 2 * w] - csum_ext[prev_i]
    d_theta_ds = (theta[next_i] - theta[prev_i]) / np.maximum(span, 1e-6)
    return d_theta_ds * v_final


def smooth_wasted_accel_brake(v_bwd, v_fwd, v_corner_o, ds_o):
    n = len(v_bwd)
    braking = v_bwd < v_fwd - 1e-3
    v_out = v_bwd.copy()
    if not braking.any() or braking.all():
        return v_out
    start0 = int(np.argmax(braking))
    order2 = [(start0 + k) % n for k in range(n)]
    k = 0
    while k < n:
        idx = order2[k]
        if braking[idx]:
            k += 1
            continue
        j = k
        arc_len = 0.0
        while j < n and not braking[order2[j]]:
            arc_len += ds_o[order2[j]]
            j += 1
        prev_idx = order2[k - 1] if k > 0 else order2[n - 1]
        next_idx = order2[j] if j < n else order2[0]
        if arc_len < MIN_ACCEL_HOLD_M:
            v_start, v_end = v_bwd[prev_idx], v_bwd[next_idx]
            a_req = (v_end * v_end - v_start * v_start) / max(2 * arc_len, 1e-6)
            a_max = accel(v_start, best_gear_for_speed(v_start))
            a_min = coast_accel(v_start)
            if a_min <= a_req <= a_max:
                s_local = 0.0
                for mm in range(k, j):
                    s_local += ds_o[order2[mm]]
                    v_smooth = sqrt_nonneg(v_start * v_start + 2 * a_req * s_local)
                    i2 = order2[mm]
                    v_out[i2] = min(v_smooth, v_corner_o[i2])
        k = j
    return v_out


def smooth_short_brake_spikes(v_bwd, v_fwd, v_corner_o, ds_o, brake_cap_g):
    n = len(v_bwd)
    braking = v_bwd < v_fwd - 1e-3
    v_out = v_bwd.copy()
    if not braking.any() or braking.all():
        return v_out
    start0 = int(np.argmin(braking))
    order2 = [(start0 + k) % n for k in range(n)]
    k = 0
    while k < n:
        idx = order2[k]
        if not braking[idx]:
            k += 1
            continue
        j = k
        arc_len = 0.0
        while j < n and braking[order2[j]]:
            arc_len += ds_o[order2[j]]
            j += 1
        next_idx = order2[j] if j < n else order2[0]
        if arc_len < MIN_ACCEL_HOLD_M:
            v_end = v_bwd[next_idx]
            far_k, ext_len, best_far_k = k - 1, arc_len, k - 1
            while ext_len < MIN_ACCEL_HOLD_M and far_k >= 0 and not braking[order2[far_k]]:
                if v_bwd[order2[far_k]] < v_bwd[order2[best_far_k]]:
                    break
                ext_len += ds_o[order2[far_k]]
                best_far_k = far_k
                far_k -= 1
            far_idx = order2[best_far_k]
            v_start = v_bwd[far_idx]
            if v_start > v_end and ext_len > 1e-6:
                a_req = (v_start * v_start - v_end * v_end) / (2 * ext_len)
                if a_req <= brake_cap_g * G:
                    s_local = 0.0
                    # WICHTIG: JS nutzt farK+1 (das finale far_k NACH der
                    # while-Schleife), NICHT bestFarK+1 - beide sind nur
                    # gleich, wenn die Schleife mind. einmal durchlief und per
                    # break/Bedingung endete (dann gilt far_k==best_far_k-1).
                    # Portierungsfehler gefunden+behoben 09.09.2026: mit
                    # bestFarK+1 blieb der Ankerpunkt selbst faelschlich
                    # unveraendert (siehe PROJEKT_STAND.md).
                    mm = far_k + 1
                    while order2[mm] != next_idx:
                        s_local += ds_o[order2[mm]]
                        v_smooth = sqrt_nonneg(v_start * v_start - 2 * a_req * s_local)
                        i2 = order2[mm]
                        v_out[i2] = min(v_smooth, v_corner_o[i2])
                        mm += 1
        k = j
    return v_out


def simulate_lap(points, mu):
    n = len(points)
    nxt = np.roll(points, -1, axis=0)
    ds = np.hypot(nxt[:, 0] - points[:, 0], nxt[:, 1] - points[:, 1])
    step_m = float(np.median(ds))
    radius = curvature_radius(points, CURVATURE_WINDOW_M, step_m)
    v_corner = np.sqrt(np.maximum(mu * G * radius, V_MIN_MS ** 2))

    i0 = int(np.argmin(v_corner))
    order = [(i0 + k) % n for k in range(n)]
    v_corner_o = v_corner[order]
    ds_o = ds[order]
    radius_o = radius[order]

    v = v_corner_o.copy()
    for _pass in range(2):
        v_fwd = v.copy()
        gear = best_gear_for_speed(v_fwd[0])
        shift_remaining = 0.0
        for i in range(1, n):
            vv, gear, shift_remaining = forward_step_shifted(v_fwd[i - 1], gear, shift_remaining, ds_o[i - 1], radius_o[i], mu)
            v_fwd[i] = min(v_corner_o[i], vv)
        vv, gear, shift_remaining = forward_step_shifted(v_fwd[n - 1], gear, shift_remaining, ds_o[n - 1], radius_o[0], mu)
        v_fwd[0] = min(v_corner_o[0], vv, v_fwd[0])

        v_bwd = v_fwd.copy()
        for i in range(n - 2, -1, -1):
            a_lat = min(v_bwd[i + 1] ** 2 / max(radius_o[i], 1e-6), mu * G)
            a_brake = min(sqrt_nonneg((mu * G) ** 2 - a_lat ** 2), BRAKE_CAP_G * G)
            v_bwd[i] = min(v_bwd[i], np.sqrt(v_bwd[i + 1] ** 2 + 2 * a_brake * ds_o[i]))
        a_lat_last = min(v_bwd[0] ** 2 / max(radius_o[n - 1], 1e-6), mu * G)
        a_brake_last = min(sqrt_nonneg((mu * G) ** 2 - a_lat_last ** 2), BRAKE_CAP_G * G)
        v_bwd[n - 1] = min(v_bwd[n - 1], np.sqrt(v_bwd[0] ** 2 + 2 * a_brake_last * ds_o[n - 1]))
        v = v_bwd

    v = smooth_wasted_accel_brake(v, v_fwd, v_corner_o, ds_o)
    v = smooth_short_brake_spikes(v, v_fwd, v_corner_o, ds_o, BRAKE_CAP_G)

    v_final = np.empty(n)
    v_final[order] = v
    lap_time = float(np.sum(ds / v_final))
    return {"radius": radius, "v_final": v_final, "ds": ds, "lap_time": lap_time}


def pinned_optimize(nlat_start, targets, mu, max_outer=None):
    nlat = clamp_inset_arr(nlat_start.copy())
    hard = [(t["index"], t["value"]) for t in targets if t.get("hard")]
    soft = [t for t in targets if not t.get("hard")]
    for idx, val in hard:
        nlat[idx] = val
    prev_lap_time = np.inf
    prev_nlat = nlat.copy()
    compliant = False
    n_outer = max_outer or N_OUTER_MAX
    for _outer in range(n_outer):
        pts = points_from_nlat(nlat)
        sim = simulate_lap(pts, mu)
        steer_rate = compute_steer_rate_deg_s(pts, sim["radius"], sim["v_final"], sim["ds"])
        max_abs_rate = float(np.max(np.abs(steer_rate)))
        now_compliant = max_abs_rate <= STEER_RATE_MAX_DEG_S
        if compliant and now_compliant and sim["lap_time"] > prev_lap_time:
            nlat = prev_nlat
            break
        prev_lap_time = sim["lap_time"]
        prev_nlat = nlat.copy()
        compliant = now_compliant
        a_lat_req = sim["v_final"] ** 2 / np.maximum(sim["radius"], 1e-6)
        grip_excess = np.clip(a_lat_req / (mu * G), 0, 1)
        steer_excess = np.maximum(0, np.abs(steer_rate) / STEER_RATE_MAX_DEG_S - 1)
        alpha_arr = np.minimum(0.9, BASE_ALPHA * (0.1 + 0.9 * grip_excess) + 0.6 * steer_excess)
        for _s in range(INNER_STEPS):
            nlat = relax_once(nlat, alpha_arr, hard)
        for t in soft:
            idx = t["index"]
            pulled = nlat[idx] + SOFT_PIN_PULL * (t["value"] - nlat[idx])
            nlat[idx] = clamp_inset(pulled, idx)
    return smooth_single_point_notches(nlat)


K1_STEER = 0.023382
K2_STEER = -0.00003895
MIN_NOTCH_STEER_DEG = 0.3
MAX_NOTCH_RADIUS_M = 400.0
MAX_NOTCH_PASSES = 8


def steer_from_radius(r):
    r = np.maximum(r, 1e-6)
    target = (180 / np.pi) / r
    disc = np.maximum(K1_STEER ** 2 + 4 * K2_STEER * target, 0)
    return np.abs((-K1_STEER + np.sqrt(disc)) / (2 * K2_STEER))


def find_radius_notches(nlat):
    pts = points_from_nlat(nlat)
    nxt = np.roll(pts, -1, axis=0)
    ds = np.hypot(nxt[:, 0] - pts[:, 0], nxt[:, 1] - pts[:, 1])
    step_m = float(np.median(ds))
    radius = curvature_radius(pts, CURVATURE_WINDOW_M, step_m)
    steer_abs = steer_from_radius(radius)

    def sign(x):
        return 1 if x > 0 else (-1 if x < 0 else 0)

    flagged = []
    for i in range(N):
        if radius[i] > MAX_NOTCH_RADIUS_M:
            continue
        im2, im1, ip1 = (i - 2) % N, (i - 1) % N, (i + 1) % N
        s_before = sign(radius[im1] - radius[im2])
        s_into = sign(radius[i] - radius[im1])
        s_out = sign(radius[ip1] - radius[i])
        if s_before == 0 or s_into != -s_before or s_out != s_before:
            continue
        jump_deg = abs(steer_abs[i] - (steer_abs[im1] + steer_abs[ip1]) / 2)
        if jump_deg >= MIN_NOTCH_STEER_DEG:
            flagged.append(i)
    return flagged


def smooth_single_point_notches(nlat):
    cur = nlat
    for _pass in range(MAX_NOTCH_PASSES):
        flagged = find_radius_notches(cur)
        if not flagged:
            break
        pts_cur = points_from_nlat(cur)
        nxt = np.roll(pts_cur, -1, axis=0)
        ds_cur = np.hypot(nxt[:, 0] - pts_cur[:, 0], nxt[:, 1] - pts_cur[:, 1])
        w = max(1, round(CURVATURE_WINDOW_M / float(np.median(ds_cur))))
        alpha_arr = np.zeros(N)
        for i in flagged:
            for k in range(-w, w + 1):
                j = (i + k) % N
                t = abs(k) / w
                u = 1 - t
                wgt = u * u * u * (u * (u * 6 - 15) + 10)
                alpha_arr[j] = max(alpha_arr[j], wgt)
        cur = relax_once(cur, alpha_arr, None)
    return cur


def apply_brush(nlat_base, index, target_val, radius_m):
    nlat = nlat_base.copy()
    win = max(1, round(radius_m / RESAMPLE_STEP_M))
    delta = target_val - nlat_base[index]
    for k in range(-win, win + 1):
        i = (index + k) % N
        t = abs(k) / win
        u = 1 - t
        w = u * u * u * (u * (u * 6 - 15) + 10)
        val = nlat_base[i] + delta * w
        nlat[i] = min(max(val, LO[i]), HI[i])
    nlat[index] = target_val
    return nlat


def recompute_from_edits(nlat_baseline, edits, mu):
    n = nlat_baseline.copy()
    for e in edits:
        n = apply_brush(n, e["index"], e["value"], e.get("radius", BRUSH_RADIUS_DEFAULT))
    return pinned_optimize(n, edits, mu)


def lap_time_for_edits(nlat_baseline, edits, mu):
    nlat = recompute_from_edits(nlat_baseline, edits, mu)
    sim = simulate_lap(points_from_nlat(nlat), mu)
    return sim["lap_time"], nlat


# ---------- 1. Baseline + Portvalidierung ----------
print("=== START: Portvalidierung ===", flush=True)
t0 = time.time()
nlat_baseline = pinned_optimize(N_LAT_START, [], MU)
baseline_sim = simulate_lap(points_from_nlat(nlat_baseline), MU)
print(f"Baseline (0 Bearbeitungen): {baseline_sim['lap_time']:.3f}s ({time.time()-t0:.1f}s Rechenzeit)", flush=True)

t0 = time.time()
user_lap, user_nlat = lap_time_for_edits(nlat_baseline, USER_EDITS, MU)
dt = time.time() - t0
diff = abs(user_lap - USER_LAPTIME_ZEITOPTIMAL)
print(f"Nutzer-Linie (25 Bearbeitungen) im Port: {user_lap:.5f}s (Referenz: {USER_LAPTIME_ZEITOPTIMAL:.5f}s, Diff {diff*1000:.1f}ms, {dt:.1f}s Rechenzeit)", flush=True)

if diff > VALIDATION_TOL_S:
    print("=== PORT-VALIDIERUNG FEHLGESCHLAGEN ===", flush=True)
    print(f"Abweichung {diff:.3f}s > Toleranz {VALIDATION_TOL_S}s - Port stimmt nicht mit der echten JS-Physik ueberein.", flush=True)
    print("Suche wird NICHT gestartet (Ergebnis waere nicht vergleichbar).", flush=True)
    raise SystemExit(1)

print("=== PORT VALIDIERT === (Diff innerhalb Toleranz, Suche startet)", flush=True)


# ---------- 2. Iterative Suche ----------
# Nutzerauftrag: "alle deine Punkte loeschen, eigene setzen - wirst du dann
# schneller?" - Start bei 0 Bearbeitungen (= baseline), greedy Suche nach
# Fixpunkten, die die Rundenzeit verbessern. Kandidaten kommen aus dem
# Grip-Excess-Profil der aktuellen Linie (dieselbe Groesse, die
# pinnedOptimize() selbst zur Gewichtung nutzt) - das sind die Stellen, an
# denen ein Fahrer/eine Fahrerin die Linie intuitiv anfassen wuerde.

CANDIDATE_RADII = [45.0, 60.0, 75.0, 120.0]  # dieselben wie der Nutzer verwendet hat
MIN_IMPROVEMENT_S = 0.001  # 1ms - unterhalb dessen zaehlt ein Fund nicht als Verbesserung
MIN_PEAK_SEPARATION = 12  # Punkte - verhindert mehrere Kandidaten in derselben Kurve
GRIP_EXCESS_THRESHOLD = 0.75  # nur Stellen, die wirklich nah am Limit fahren
N_VALUE_CANDIDATES = 6  # Stichproben ueber die Korridorbreite je Kandidat
MAX_PASSES = 12
TIME_BUDGET_S = 900  # 15 Minuten Rechenzeit-Deckel


def find_candidate_indices(nlat, mu, exclude_near, k_max=15):
    pts = points_from_nlat(nlat)
    sim = simulate_lap(pts, mu)
    a_lat_req = sim["v_final"] ** 2 / np.maximum(sim["radius"], 1e-6)
    grip_excess = a_lat_req / (mu * G)
    order_by_excess = np.argsort(-grip_excess)
    chosen = []
    for i in order_by_excess:
        if grip_excess[i] < GRIP_EXCESS_THRESHOLD:
            break
        if any(min(abs(i - c), N - abs(i - c)) < MIN_PEAK_SEPARATION for c in chosen):
            continue
        if any(min(abs(i - c), N - abs(i - c)) < MIN_PEAK_SEPARATION for c in exclude_near):
            continue
        chosen.append(int(i))
        if len(chosen) >= k_max:
            break
    return chosen, sim["lap_time"]


def value_candidates(index):
    lo, hi = LO[index], HI[index]
    span = hi - lo
    fracs = np.linspace(0.12, 0.88, N_VALUE_CANDIDATES)
    return [lo + f * span for f in fracs]


def run_search():
    t_start = time.time()
    edits = []
    nlat_current = nlat_baseline.copy()
    sim = simulate_lap(points_from_nlat(nlat_current), MU)
    best_lap = sim["lap_time"]
    print(f"Start: {best_lap:.3f}s (0 Bearbeitungen)", flush=True)

    for pass_no in range(1, MAX_PASSES + 1):
        if time.time() - t_start > TIME_BUDGET_S:
            print(f"Zeitbudget ({TIME_BUDGET_S}s) erreicht, Suche beendet.", flush=True)
            break
        edited_indices = [e["index"] for e in edits]
        candidates, _ = find_candidate_indices(nlat_current, MU, edited_indices)
        if not candidates:
            print(f"Durchgang {pass_no}: keine Kandidaten mehr ueber der Grip-Schwelle - Konvergenz.", flush=True)
            break

        pass_improved = False
        for cand_idx in candidates:
            local_best_lap = best_lap
            local_best_edit = None
            for val in value_candidates(cand_idx):
                for radius_m in CANDIDATE_RADII:
                    trial_edits = edits + [{"index": cand_idx, "value": float(val), "radius": radius_m}]
                    lap, _ = lap_time_for_edits(nlat_baseline, trial_edits, MU)
                    if lap < local_best_lap - MIN_IMPROVEMENT_S:
                        local_best_lap = lap
                        local_best_edit = {"index": cand_idx, "value": float(val), "radius": radius_m}
            if local_best_edit is not None:
                edits.append(local_best_edit)
                nlat_current = recompute_from_edits(nlat_baseline, edits, MU)
                gain_ms = (best_lap - local_best_lap) * 1000
                best_lap = local_best_lap
                pass_improved = True
                print(f"[Durchgang {pass_no}] neuer Bestwert {best_lap:.3f}s (-{gain_ms:.1f}ms) - Punkt {cand_idx}, {len(edits)} Bearbeitungen, {time.time()-t_start:.0f}s Laufzeit", flush=True)
                _checkpoint(edits, best_lap, pass_no, time.time() - t_start)

        if not pass_improved:
            print(f"Durchgang {pass_no}: keine Verbesserung gefunden - Konvergenz.", flush=True)
            break

    return edits, best_lap, time.time() - t_start


def _checkpoint(edits, lap_time, pass_no, elapsed_s):
    with open(OUT_JSON, "w") as f:
        json.dump({
            "status": "running",
            "pass": pass_no,
            "elapsed_s": round(elapsed_s, 1),
            "lap_time_zeitoptimal": lap_time,
            "user_lap_time_zeitoptimal": USER_LAPTIME_ZEITOPTIMAL,
            "delta_ms": round((lap_time - USER_LAPTIME_ZEITOPTIMAL) * 1000, 1),
            "num_edits": len(edits),
            "edits": edits,
        }, f, indent=2)


final_edits, final_lap, elapsed = run_search()

with open(OUT_JSON, "w") as f:
    json.dump({
        "status": "done",
        "elapsed_s": round(elapsed, 1),
        "lap_time_zeitoptimal": final_lap,
        "user_lap_time_zeitoptimal": USER_LAPTIME_ZEITOPTIMAL,
        "delta_ms": round((final_lap - USER_LAPTIME_ZEITOPTIMAL) * 1000, 1),
        "num_edits": len(final_edits),
        "edits": final_edits,
    }, f, indent=2)

print("=== ERGEBNIS ===", flush=True)
print(f"Nutzer (25 Bearbeitungen):      {USER_LAPTIME_ZEITOPTIMAL:.3f}s", flush=True)
print(f"Suche ({len(final_edits)} Bearbeitungen):     {final_lap:.3f}s", flush=True)
diff_ms = (final_lap - USER_LAPTIME_ZEITOPTIMAL) * 1000
if diff_ms < 0:
    print(f"-> Suche ist {abs(diff_ms):.0f}ms SCHNELLER als der Nutzer.", flush=True)
else:
    print(f"-> Suche ist {diff_ms:.0f}ms LANGSAMER als der Nutzer.", flush=True)
print(f"Laufzeit: {elapsed:.0f}s. Ergebnis gespeichert in {OUT_JSON}", flush=True)
