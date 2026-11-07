"""
Spreewaldring - interaktiver Ideallinien-Editor (NEU, 30.08.2026)

Nutzerauftrag: die Linie an einem beliebigen Punkt per Maus verschieben (Pinsel mit Falloff, der Punkt wird zum
Fixpunkt), danach automatisch neu optimieren (Abbruch sobald eine Iteration die Rundenzeit verschlechtert) und das
Ergebnis (Rundenzeit, Geschwindigkeit, Bremszonen, Schaltpunkte) sofort sehen.

SEIT 07.11.2026 (Nutzerentscheid) rechnet der Editor auf Python: Dieses Skript ist ein lokaler Dienst, der Browser
zeichnet und bedient nur noch. Die Physik ist simulate_lap_full() aus spreewaldring_racing_line_optimal.py, also
dieselbe Rechnung wie in den Skripten; der fruehere JavaScript-Port (samt Abgleich) ist entfallen. Ein Klick rechnet
~80 ms (natuerliches Gasmodell ~230 ms), eine Optimierung nach dem Loslassen wenige Sekunden.

INTERAKTION:
  - Ziehen: Pinsel mit Smootherstep-Falloff verschiebt die Nachbarschaft (/api/preview), keine Relaxation.
  - Loslassen: Der Punkt wird weicher Fixpunkt (oder hart, per Haken). Alternierende Optimierung Geschwindigkeit
    <-> Linie (/api/solve) bis eine Iteration die Rundenzeit verschlechtert, nur das Optimum bleibt.
  - Bremszonen sind physikalisch bestimmt: Der Rueckwaertspass hat das Tempo gegenueber dem Vorwaertspass reduziert.
  - Persistenz: Die Bearbeitungsliste liegt im Browser (localStorage), die Linie wird daraus immer neu berechnet.

Aufruf: .venv/bin/python scripts/spreewaldring_racing_line_editor.py [--port 8732]
Dann im Browser: http://127.0.0.1:8732/
"""
import argparse
import functools
import json
import math
import os
import re
import socket
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

from spreewaldring_track import RESULTS_DIR, CURVATURE_WINDOW_M, RESAMPLE_STEP_M, G, compute_curvature_radius
from spreewaldring_track_surface import compute_tangents_closed
from spreewaldring_racing_line import load_corridor, corridor_bounds, CAR_HALF_WIDTH_M
from spreewaldring_lap_simulation import (
    accel_true, coast_true, engine_controls, limiter_factor, literature_vehicle, throttle_trace,
)
from spreewaldring_racing_line_optimal import (
    BRAKE_CAP_G, DEFAULT_TYRE, VEHICLES, simulate_lap_full, steer_from_radius,
)
from performance_simulation import REDLINE_RPM

ANIMATION_HTML_PATH = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_animation.html")
START_LINE_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_optimal_summary.json")

DEFAULT_PORT = 8732
MAX_REQUEST_BYTES = 5_000_000
# Wiedergabe auf dem Dash im Auto (08.11.2026): beim Abspielen gehen die angezeigten Werte per UDP an
# can_backend.py (EDITOR_FEED_PORT) auf dem Pi, das Dash zeigt dann nur sie.
DASH_HOST = os.environ.get("MX5_DASH_HOST", "car")
DASH_FEED_PORT = 51235
DASH_FIELDS = ("speed_kmh", "rpm", "gear", "throttle_pct", "brake_pct", "steer_deg", "lap_t", "load_pct", "tp_pct")
_dash_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
MU_FIXED = 1.0           # Literatur-Vergleich im Fahrzeugmodell-Umschalter
BASE_ALPHA = 0.15        # wie in spreewaldring_racing_line_optimal.py
INNER_STEPS = 80         # wie in spreewaldring_racing_line_optimal.py
N_OUTER_MAX = 30         # Sicherheitsnetz - Abbruch bei Verschlechterung greift i.d.R. viel frueher
BRUSH_RADIUS_M_DEFAULT = 60.0

# Echte Start-Ziel-Linie (weisser Querstrich vor den aufgemalten Startboxen, vom Nutzer im Orthofoto
# markiert, 30.08.2026). Seit 07.11.2026 als Koordinate statt Punktindex, das Raster aendert sich mit dem
# Streckenmodell. Die fruehere Rand-Glaettung (27 m) gegen Rauschen der alten Raender ist entfallen.
START_FINISH_UTM33 = (409911.995, 5761804.257)

# Lenkgeschwindigkeit: Ackermann-Naeherung, Nutzervorgabe 30.08.2026: 240 Grad/s am Lenkrad NIE ueberschreiten
# (harte Randbedingung in pinned_optimize), reale kleine Korrekturen liegen eher um 70 Grad/s.
WHEELBASE_M, STEERING_RATIO = 2.31, 15
STEER_RATE_MAX_DEG_S = 240.0
# Abstand zur Korridorgrenze: Mehrere Punkte hart am Anschlag ergeben eine flache "Beule" statt einer runden Kurve.
EDGE_MARGIN_M = 0.10
# Zug eines weichen Fixpunkts je aeusserer Iteration; die Abbruchregel entscheidet, wie weit sich das lohnt.
SOFT_PIN_PULL = 0.3
# Nur Ausreisser, die ein Fahrer spueren wuerde, und nur in Kurven (Radius-Schaetzung auf Geraden ist extrem empfindlich)
MIN_NOTCH_STEER_DEG = 0.3
MAX_NOTCH_RADIUS_M = 400.0
MAX_NOTCH_PASSES = 8
STANDING_START_DT_S = 1 / 60

Track = namedtuple("Track", "center left right perp lo hi")


def load_reused_assets():
    """Kurvennummern + zugeschnittenes Orthofoto aus der bestehenden Animation (dort bereits verifiziert)."""
    with open(ANIMATION_HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r'<script type="application/json" id="viz-data">(.*?)</script>', html, re.S)
    old = json.loads(m.group(1))
    return {
        "origin_utm33": old["origin_utm33"],
        "corners_local": old["corners"],  # Nummern wie bisher, Position und Radius aktualisiert load_editor_data()
        "background_image_jpeg_b64": old["background_image_jpeg_b64"],
        "background_extent_local": old["background_extent_local"],
    }


def load_editor_data():
    """Strecke in lokalen Koordinaten, Index 0 an der Start-Ziel-Linie, und die Ausgangslinie."""
    center, left, right = load_corridor()
    tangents = compute_tangents_closed(center)
    lo, hi, perp = corridor_bounds(center, left, right, tangents)
    # Alternierend optimierte Linie (seit 07.11.2026, Nutzerentscheid, vorher Kurven nacheinander, rund 1,9 s langsamer):
    # beste Iteration, nicht die neu gerasterte Endlinie, nur sie hat dieselben Punkte wie der Korridor.
    with open(START_LINE_SUMMARY_PATH, encoding="utf-8") as f:
        optimal = json.load(f)
    start_points = np.array(min(optimal["snapshots"], key=lambda s: s["lap_time_s"])["points_utm33"])
    assert len(start_points) == len(center), "Startlinie und Korridor muessen dieselbe Punktanzahl haben"
    n_lat_start = np.clip(np.sum((start_points - center) * perp, axis=1), lo, hi)

    roll = int(np.argmin(np.linalg.norm(center - np.array(START_FINISH_UTM33), axis=1)))
    center, left, right, perp = (np.roll(a, -roll, axis=0) for a in (center, left, right, perp))
    lo, hi, n_lat_start = (np.roll(a, -roll, axis=0) for a in (lo, hi, n_lat_start))

    reused = load_reused_assets()
    origin = np.array(reused["origin_utm33"])
    # Kurvennummern bleiben (Bezug in Logbuch und Gespraechen), Marke und Radius kommen aus dem Modell
    radius = compute_curvature_radius(center, CURVATURE_WINDOW_M, RESAMPLE_STEP_M)
    corners = []
    for c in reused["corners_local"]:
        near = np.linalg.norm(center - (origin + [c["x"], c["y"]]), axis=1) < 30.0
        i = np.flatnonzero(near)[np.argmin(radius[near])]
        corners.append({"number": c["number"], "x": float(center[i, 0] - origin[0]),
                        "y": float(center[i, 1] - origin[1]), "radius_m": float(radius[i])})
    track = Track(center - origin, left - origin, right - origin, perp, lo, hi)
    return track, n_lat_start, corners, reused


# ---------- Linie: Punkte, Relaxation, Fixpunkte ----------
def points_from_n_lat(track, n_lat):
    return track.center + n_lat[:, None] * track.perp


def clamp_inset(track, n_lat):
    """Klemmt mit EDGE_MARGIN_M Abstand zur Grenze; ist der Korridor schmaler als 2x Puffer: Streckenmitte."""
    mid = (track.lo + track.hi) / 2
    return np.clip(n_lat, np.minimum(track.lo + EDGE_MARGIN_M, mid), np.maximum(track.hi - EDGE_MARGIN_M, mid))


def relax_once(track, n_lat, alpha, hard_targets=()):
    """Ein Glaettungsschritt in Weltkoordinaten. Harte Fixpunkte werden jeden Schritt exakt erzwungen."""
    p = points_from_n_lat(track, n_lat)
    avg = (np.roll(p, 1, axis=0) + np.roll(p, -1, axis=0)) / 2
    p_new = p + alpha[:, None] * (avg - p)
    n_new = clamp_inset(track, np.sum((p_new - track.center) * track.perp, axis=1))
    for e in hard_targets:
        n_new[e["index"]] = e["value"]
    return n_new


def turn_sign(pts, w):
    """Vorzeichen der Kurve ueber die Fensterbreite des Radius, Nachbarpunkte waeren ein Diskretisierungsartefakt."""
    p0, p2 = np.roll(pts, w, axis=0), np.roll(pts, -w, axis=0)
    cross = (pts[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - (p2[:, 0] - p0[:, 0]) * (pts[:, 1] - p0[:, 1])
    return -np.sign(cross)


def steer_rate_deg_s(pts, sim):
    """Lenkrad-Winkelgeschwindigkeit je Punkt (Ackermann-Naeherung), Ableitung ueber CURVATURE_WINDOW_M."""
    ds, n = sim["ds"], len(pts)
    w = max(1, round(CURVATURE_WINDOW_M / float(np.median(ds))))
    theta = np.degrees(np.arctan(WHEELBASE_M / np.maximum(sim["radius"], 1.0))) * STEERING_RATIO * turn_sign(pts, w)
    cum = np.r_[0.0, np.cumsum(np.r_[ds, ds, ds])]
    ip = (np.arange(n) - w) % n
    span = cum[ip + 2 * w] - cum[ip]
    return (np.roll(theta, -w) - np.roll(theta, w)) / np.maximum(span, 1e-6) * sim["v"]


def find_radius_notches(track, n_lat, veh):
    """Einzelpunkt-Ausreisser im Radiusverlauf (Nutzerbeobachtung 08.09.2026, Punkte 236-238): Die Trendrichtung kehrt
    fuer genau einen Schritt um und danach zurueck. Ein echter Kurvenscheitel kehrt nur einmal dauerhaft um."""
    pts = points_from_n_lat(track, n_lat)
    ds = np.hypot(*(np.roll(pts, -1, axis=0) - pts).T)
    radius = compute_curvature_radius(pts, CURVATURE_WINDOW_M, float(np.median(ds)))
    steer = steer_from_radius(radius, veh)
    s_before = np.sign(np.roll(radius, 1) - np.roll(radius, 2))
    s_into = np.sign(radius - np.roll(radius, 1))
    s_out = np.sign(np.roll(radius, -1) - radius)
    jump = np.abs(steer - (np.roll(steer, 1) + np.roll(steer, -1)) / 2)
    flagged = (radius <= MAX_NOTCH_RADIUS_M) & (s_before != 0) & (s_into == -s_before) & (s_out == s_before)
    return np.flatnonzero(flagged & (jump >= MIN_NOTCH_STEER_DEG))


def smootherstep(t):
    u = 1 - t
    return u * u * u * (u * (u * 6 - 15) + 10)


def smooth_single_point_notches(track, n_lat, veh):
    """Glaettet Ausreisser ueber +-w Punkte: Der Radius haengt an einem w breiten Fenster, ein einzelner Punkt kann den
    Radiusverlauf nicht sicher glaetten (Bugfund 08.09.2026). Reine Nachbearbeitung, die Optimierung bleibt unberuehrt."""
    n = len(n_lat)
    cur = n_lat
    for _ in range(MAX_NOTCH_PASSES):
        flagged = find_radius_notches(track, cur, veh)
        if len(flagged) == 0:
            break
        pts = points_from_n_lat(track, cur)
        w = max(1, round(CURVATURE_WINDOW_M / float(np.median(np.hypot(*(np.roll(pts, -1, axis=0) - pts).T)))))
        alpha = np.zeros(n)
        for i in flagged:
            for k in range(-w, w + 1):
                j = (i + k) % n
                alpha[j] = max(alpha[j], smootherstep(abs(k) / w))
        cur = relax_once(track, cur, alpha)
    return cur


def pinned_optimize(track, n_lat_start, targets, veh, natural=False, brake_cap_g=None):
    """Alternierende Optimierung mit allen Fixpunkten gemeinsam, querkraftgewichtet. targets: {index, value, hard}. Hart:
    jeden Schritt exakt erzwungen. Weich (Standard): einmal je aeusserer Iteration ein Stueck hingezogen, die Abbruchregel
    entscheidet, wie weit sich das lohnt. Solange die Lenkgeschwindigkeit verletzt ist, wird nicht abgebrochen: Das ist
    eine harte Randbedingung, keine Praeferenz. Ohne Fixpunkte reine Durchsetzung dieser Grenze."""
    n_lat = clamp_inset(track, np.array(n_lat_start, dtype=float))
    hard = [e for e in targets if e["hard"]]
    soft = [e for e in targets if not e["hard"]]
    for e in hard:
        n_lat[e["index"]] = e["value"]
    prev_lap, prev_n_lat, compliant = math.inf, n_lat.copy(), False
    for _ in range(N_OUTER_MAX):
        pts = points_from_n_lat(track, n_lat)
        sim = simulate_lap_full(pts, veh, natural=natural, brake_cap_g=brake_cap_g)
        rate = steer_rate_deg_s(pts, sim)
        now_compliant = float(np.max(np.abs(rate))) <= STEER_RATE_MAX_DEG_S
        if compliant and now_compliant and sim["lap_time_s"] > prev_lap:
            n_lat = prev_n_lat
            break
        prev_lap, prev_n_lat, compliant = sim["lap_time_s"], n_lat.copy(), now_compliant
        grip = np.clip(sim["v"] ** 2 / np.maximum(sim["radius"], 1e-6) / (veh.lat_g * G), 0.0, 1.0)
        steer_excess = np.maximum(0.0, np.abs(rate) / STEER_RATE_MAX_DEG_S - 1)
        alpha = np.minimum(0.9, BASE_ALPHA * (0.1 + 0.9 * grip) + 0.6 * steer_excess)
        for _ in range(INNER_STEPS):
            n_lat = relax_once(track, n_lat, alpha, hard)
        for e in soft:
            pulled = n_lat[e["index"]] + SOFT_PIN_PULL * (e["value"] - n_lat[e["index"]])
            mid = (track.lo[e["index"]] + track.hi[e["index"]]) / 2
            n_lat[e["index"]] = min(max(pulled, min(track.lo[e["index"]] + EDGE_MARGIN_M, mid)),
                                    max(track.hi[e["index"]] - EDGE_MARGIN_M, mid))
    return smooth_single_point_notches(track, n_lat, veh)


def apply_brush(track, n_lat_base, index, target, radius_m):
    """Smootherstep statt Raised-Cosine: Auch die Kruemmung ist am Fensterrand 0, sonst entsteht dort ein Knick."""
    n = len(n_lat_base)
    n_lat = n_lat_base.copy()
    win = max(1, round(radius_m / RESAMPLE_STEP_M))
    delta = target - n_lat_base[index]
    for k in range(-win, win + 1):
        i = (index + k) % n
        n_lat[i] = min(max(n_lat_base[i] + delta * smootherstep(abs(k) / win), track.lo[i]), track.hi[i])
    n_lat[index] = target
    return n_lat


def recompute_from_edits(track, baseline, edits, veh, natural=False, brake_cap_g=None):
    """Linie immer aus der Bearbeitungsliste neu: Warm-Start per Pinsel, dann alle Fixpunkte gemeinsam relaxieren."""
    n_lat = baseline.copy()
    for e in edits:
        n_lat = apply_brush(track, n_lat, e["index"], e["value"], e["radius"])
    return pinned_optimize(track, n_lat, edits, veh, natural, brake_cap_g)


def standing_start_trace(sim, veh):
    """Reine Anzeige-Zugabe (Nutzerwunsch 09.09.2026): stehender Start bei der ersten Wiedergabe, Vollgas-Sprint bis zum
    Rundentempo an derselben Stelle. Greift nicht in Rundenzeit oder Linie ein."""
    f = veh.speed_factor
    s_arr, v_final = sim["s"], sim["v"]
    s, v, gear, shift_t = 0.0, 0.0, 1, 0.0
    rows = [(s, v, gear, False)]
    while True:
        dt = STANDING_START_DT_S
        shifting = shift_t > 0
        if shifting:
            v = max(0.0, v + coast_true(v, f) * dt)
            shift_t = max(0.0, shift_t - dt)
        elif gear < 6:
            a_cur, rpm = accel_true(v, gear, f)
            if rpm >= REDLINE_RPM or accel_true(v, gear + 1, f)[0] > a_cur:
                gear += 1
                shift_t = veh.shift_s[(gear - 1, gear)]
            else:
                v += max(a_cur, 0.0) * dt
        else:
            v += max(accel_true(v, gear, f)[0], 0.0) * dt
        s += v * dt
        i = int(np.searchsorted(s_arr, min(s, s_arr[-1]), side="right")) - 1
        if v >= v_final[i] or s >= s_arr[-1]:
            rpm = [accel_true(r[1], r[2], f)[1] for r in rows]
            frac = [0.0 if r[3] else limiter_factor(n, r[2]) for r, n in zip(rows, rpm)]
            load, tp, _ = engine_controls(frac, rpm, [not r[3] for r in rows])
            return {"dt": dt, "s": [r[0] for r in rows], "v": [r[1] for r in rows], "gear": [r[2] for r in rows],
                    "rpm": rpm, "load": round_list(load, 1), "tp": round_list(tp, 1),
                    "pedal": [0 if r[3] else 100 for r in rows], "joinT": float(sim["t"][i])}
        rows.append((s, v, gear, shifting))


# ---------- Dienst ----------
def round_list(a, digits=6):
    return [round(float(x), digits) for x in a]


def sim_json(sim, pts, veh):
    load, tp, pedal = throttle_trace(sim["v"], sim["gear"], sim["ds"], veh)
    return {
        "loadPct": round_list(load, 1), "tpPct": round_list(tp, 1), "pedalPct": round_list(pedal, 1),
        "vFinal": round_list(sim["v"]), "vFwd": round_list(sim["v_fwd"]), "gear": [int(g) for g in sim["gear"]],
        "rpm": round_list(sim["rpm"], 2), "s": round_list(sim["s"], 4), "t": round_list(sim["t"]),
        "radius": round_list(np.minimum(sim["radius"], 1e9), 4), "ds": round_list(sim["ds"]),
        "lapTime": sim["lap_time_s"], "coasting": [int(c) for c in sim["coasting"]],
        "throttleFrac": round_list(sim["throttle_frac"]), "brakeFrac": round_list(sim["brake_frac"]),
        "throttleFracNatural": round_list(sim["throttle_frac_natural"]), "naturalMode": sim["natural"],
        "steerRate": round_list(steer_rate_deg_s(pts, sim), 3),
    }


class Editor:
    """Haelt Strecke und Ausgangslinie je Fahrzeugmodell. Der Dienst bearbeitet eine Anfrage nach der anderen."""

    def __init__(self):
        self.track, self.n_lat_start, self.corners, self.reused = load_editor_data()
        lit = literature_vehicle(MU_FIXED)
        # Name -> (Vehicle, zusaetzliche Bremsgrenze, Beschriftung)
        self.vehicles = {name: (veh, None, f"{name} (aus den Logs)") for name, veh in VEHICLES.items()}
        self.vehicles["Literatur"] = (lit, BRAKE_CAP_G, f"Literatur (mu={MU_FIXED:.1f}, ATTACK-Schaltzeiten)")
        self.baselines = {}

    def baseline(self, name):
        """Die Ausgangslinie kann an engen Scheiteln die Lenkgeschwindigkeits-Grenze verletzen, daher einmal bereinigt."""
        if name not in self.baselines:
            veh, cap, _ = self.vehicles[name]
            self.baselines[name] = pinned_optimize(self.track, self.n_lat_start, [], veh, brake_cap_g=cap)
        return self.baselines[name]

    def parse_edits(self, raw):
        n = len(self.track.center)
        edits = []
        for e in raw:
            index, value = int(e["index"]), float(e["value"])
            if not (0 <= index < n and math.isfinite(value)):
                raise ValueError(f"Bearbeitung ungueltig: {e}")
            radius = float(e.get("radius", BRUSH_RADIUS_M_DEFAULT))
            edits.append({"index": index, "value": value, "hard": bool(e.get("hard")),
                          "radius": radius if math.isfinite(radius) and radius > 0 else BRUSH_RADIUS_M_DEFAULT})
        return edits

    def parse_n_lat(self, raw):
        n_lat = np.array(raw, dtype=float)
        if n_lat.shape != (len(self.track.center),) or not np.isfinite(n_lat).all():
            raise ValueError("nLat passt nicht zur Strecke")
        return n_lat

    def vehicle(self, body):
        name = body.get("vehicle")
        if name not in self.vehicles:
            raise ValueError(f"Unbekanntes Fahrzeugmodell: {name}")
        return name, *self.vehicles[name][:2]

    def result(self, n_lat, veh, cap, natural):
        pts = points_from_n_lat(self.track, n_lat)
        sim = simulate_lap_full(pts, veh, natural=natural, brake_cap_g=cap)
        return {"nLat": round_list(n_lat, 9), "sim": sim_json(sim, pts, veh)}, sim

    def solve(self, body):
        name, veh, cap = self.vehicle(body)
        natural = bool(body.get("natural"))
        baseline = self.baseline(name)
        n_lat = recompute_from_edits(self.track, baseline, self.parse_edits(body.get("edits", [])), veh, natural, cap)
        out, sim = self.result(n_lat, veh, cap, natural)
        pts = points_from_n_lat(self.track, baseline)
        out["baselineLapTime"] = simulate_lap_full(pts, veh, natural=natural, brake_cap_g=cap)["lap_time_s"]
        out["standingStart"] = standing_start_trace(sim, veh)
        return out

    def simulate(self, body):
        name, veh, cap = self.vehicle(body)
        return self.result(self.parse_n_lat(body["nLat"]), veh, cap, bool(body.get("natural")))[0]

    def preview(self, body):
        name, veh, cap = self.vehicle(body)
        n_lat = self.parse_n_lat(body["nLat"])
        index = int(body["index"])
        target, radius = float(body["target"]), float(body["radius"])
        if not (0 <= index < len(n_lat) and math.isfinite(target) and math.isfinite(radius) and radius > 0):
            raise ValueError("Pinsel ungueltig")
        brushed = apply_brush(self.track, n_lat, index, target, radius)
        return self.result(brushed, veh, cap, bool(body.get("natural")))[0]

    def page(self):
        track = self.track
        data = {
            "origin_utm33": self.reused["origin_utm33"],
            "centerline": track.center.tolist(), "left_edge": track.left.tolist(), "right_edge": track.right.tolist(),
            "perp": track.perp.tolist(), "corridor_lo_m": track.lo.tolist(), "corridor_hi_m": track.hi.tolist(),
            "corners": self.corners,
            "background_image_jpeg_b64": self.reused["background_image_jpeg_b64"],
            "background_extent_local": self.reused["background_extent_local"],
            "physics": {
                "G": G, "RESAMPLE_STEP_M": RESAMPLE_STEP_M, "CURVATURE_WINDOW_M": CURVATURE_WINDOW_M,
                "REDLINE_RPM": REDLINE_RPM, "DEFAULT_VEHICLE": DEFAULT_TYRE,
                "VEHICLES": {name: {"label": label, "lat_g": veh.lat_g, "accel_g": veh.accel_g, "brake_g": veh.brake_g,
                                    "speed_factor": veh.speed_factor}
                             for name, (veh, _, label) in self.vehicles.items()},
            },
            "brush_radius_m_default": BRUSH_RADIUS_M_DEFAULT,
        }
        return HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))


@functools.lru_cache(maxsize=1)
def dash_address():
    return socket.gethostbyname(DASH_HOST), DASH_FEED_PORT  # einmal aufloesen, der Dienst ist einfaedig


def send_to_dash(req):
    msg = {k: float(req[k]) for k in DASH_FIELDS if req.get(k) is not None}
    try:
        _dash_sock.sendto(json.dumps(msg).encode("utf-8"), dash_address())
    except OSError as err:  # Pi aus oder nicht im Netz: Editor laeuft weiter
        return {"ok": False, "error": str(err)}
    return {"ok": True}


def make_handler(editor):
    routes = {"/api/solve": editor.solve, "/api/simulate": editor.simulate, "/api/preview": editor.preview,
              "/api/dash": send_to_dash}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, content_type, payload):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path != "/":
                self.reply(404, "text/plain; charset=utf-8", b"nicht gefunden")
                return
            self.reply(200, "text/html; charset=utf-8", editor.page().encode("utf-8"))

        def do_POST(self):
            route = routes.get(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if route is None or self.headers.get("Content-Type") != "application/json" or length > MAX_REQUEST_BYTES:
                self.reply(400, "text/plain; charset=utf-8", b"ungueltige Anfrage")
                return
            try:
                result = route(json.loads(self.rfile.read(length)))
            except (KeyError, TypeError, ValueError) as err:
                self.reply(400, "text/plain; charset=utf-8", str(err).encode("utf-8"))
                return
            self.reply(200, "application/json", json.dumps(result).encode("utf-8"))

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Ideallinien-Editor als lokaler Dienst")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    print("Lade Strecke und Fahrzeugmodelle ...", flush=True)
    editor = Editor()
    server = HTTPServer(("127.0.0.1", args.port), make_handler(editor))
    print(f"Ideallinien-Editor: http://127.0.0.1:{args.port}/ (rechnet in Python, Abbruch mit Strg+C)", flush=True)
    server.serve_forever()


HTML_TEMPLATE = r"""<meta charset="utf-8">
<title>Ideallinien-Editor Spreewaldring</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;700;800&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root{
    --bg:#12171b;
    --surface:#1a2126;
    --surface-2:#212a30;
    --border:#313c43;
    --text:#eef2f3;
    --text-muted:#8fa2ab;
    --accent-amber:#e8a33d;
    --accent-teal:#45d6c4;
    --accent-rose:#d97a72;
    --accent-brake:#e0483c;
  }
  *{box-sizing:border-box;}
  html,body{height:100%;}
  body{
    margin:0;
    background:var(--bg);
    color:var(--text);
    font-family:"IBM Plex Sans", system-ui, sans-serif;
    display:flex;
    flex-direction:column;
    min-height:100vh;
  }
  .eyebrow{
    font-family:"IBM Plex Mono", ui-monospace, monospace;
    font-size:0.72rem;
    letter-spacing:0.12em;
    text-transform:uppercase;
    color:var(--text-muted);
  }
  header{
    padding:1.1rem 1.5rem 0.9rem;
    border-bottom:1px solid var(--border);
    display:flex;
    align-items:baseline;
    gap:0.9rem;
    flex-wrap:wrap;
  }
  h1{
    font-family:"Big Shoulders Display", sans-serif;
    font-weight:800;
    font-size:1.7rem;
    letter-spacing:0.01em;
    margin:0;
    text-transform:uppercase;
  }
  header .sub{
    font-size:0.85rem;
    color:var(--text-muted);
  }
  main{
    flex:1;
    min-height:0; /* flex-item-Default ist ebenfalls min-height:auto (content-
      basiert) - ohne das hier ignoriert main die 100vh-Deckelung von body
      genauso wie .rail/.stage weiter unten dieselbe Grid-Variante des Bugs */
    display:grid;
    grid-template-columns:minmax(0,1fr) 22rem;
    grid-template-rows:minmax(0,1fr); /* sonst waechst die (einzige) Auto-Zeile
      mit dem Inhalt der Seitenleiste mit, statt sich auf die verfuegbare
      Hoehe zu begrenzen - siehe .rail/.stage min-height:0 fuer die andere
      Haelfte desselben Grid-Blowout-Bugs */
    gap:1px;
    background:var(--border);
  }
  @media (max-width:900px){
    main{grid-template-columns:1fr;}
  }
  .stage{
    background:var(--bg);
    display:flex;
    flex-direction:column;
    min-width:0;
    min-height:0;
  }
  .canvas-wrap{
    position:relative;
    flex:1;
    min-height:420px;
  }
  .canvas-wrap canvas#trackCanvas{
    position:absolute;
    inset:0;
    width:100%;
    height:100%;
    display:block;
    touch-action:none;
    cursor:grab;
  }
  #hoverTooltip{
    position:absolute;
    pointer-events:none;
    background:rgba(10,14,17,0.92);
    border:1px solid rgba(238,242,243,0.25);
    border-radius:6px;
    padding:0.4rem 0.6rem;
    font-size:0.75rem;
    line-height:1.4;
    color:#eef2f3;
    white-space:nowrap;
    z-index:5;
    display:none;
  }
  #hoverTooltip .nearest{ color:#9aa4a9; }
  #hoverTooltip .chainable{ color:#45d6c4; }
  .legend{
    display:flex;
    align-items:center;
    gap:1.4rem;
    padding:0.7rem 1.2rem;
    border-top:1px solid var(--border);
    font-family:"IBM Plex Mono", monospace;
    font-size:0.72rem;
    color:var(--text-muted);
    flex-wrap:wrap;
  }
  .legend .speedscale{
    display:flex;
    align-items:center;
    gap:0.6rem;
  }
  .legend .bar{
    width:11rem;
    height:8px;
    border-radius:2px;
    background:linear-gradient(90deg,#3a6dd1 0%,#45d6c4 33%,#e8a33d 66%,#e0483c 100%);
  }
  .legend .swatch{
    display:inline-block;
    width:0.9rem;
    height:0.9rem;
    border-radius:2px;
    vertical-align:middle;
    margin-right:0.35rem;
  }
  .pedals-row{
    display:flex;
    align-items:flex-end;
    gap:0.7rem;
  }
  .pedal-bar{
    width:1.7rem;
    height:4.2rem;
    border:2px solid;
    border-radius:3px;
    display:flex;
    align-items:flex-end;
    overflow:hidden;
    flex-shrink:0;
  }
  .pedal-bar.brake{border-color:var(--accent-brake);}
  .pedal-bar.throttle{border-color:#4ade80;}
  .pedal-fill{width:100%; height:0%;}
  .pedal-bar.brake .pedal-fill{background:var(--accent-brake);}
  .pedal-bar.throttle .pedal-fill{background:#4ade80;}
  .wheel{
    width:4.2rem;
    height:4.2rem;
    border:2px solid var(--text-muted);
    border-radius:50%;
    position:relative;
    flex-shrink:0;
    margin-left:0.2rem;
  }
  .wheel-rotor{position:absolute; inset:0;}
  .wheel-dot{
    position:absolute;
    top:0.15rem;
    left:50%;
    width:0.55rem;
    height:0.55rem;
    transform:translateX(-50%);
    background:var(--accent-brake);
    border-radius:50%;
  }
  .trace-wrap{
    height:180px;
    border-top:1px solid var(--border);
    flex-shrink:0;
  }
  .trace-wrap canvas{
    width:100%;
    height:100%;
    display:block;
  }
  .rail{
    background:var(--surface);
    display:flex;
    flex-direction:column;
    gap:1.1rem;
    padding:1.2rem 1.3rem 1.4rem;
    overflow-y:auto;
    min-height:0; /* Grid-Item-Default ist min-height:auto (=content-basiert) -
                     ohne das hier sprengt eine lange Editier-/Schaltliste die
                     ganze Zeile statt selbst zu scrollen, das verschiebt dann
                     die Karte + Canvas-Aufloesung auseinander (Klick-Offset) */
  }
  .panel{
    background:var(--surface-2);
    border:1px solid var(--border);
    border-radius:6px;
    padding:0.9rem 1rem;
  }
  .panel h2{
    font-family:"IBM Plex Mono", monospace;
    font-size:0.7rem;
    letter-spacing:0.1em;
    text-transform:uppercase;
    color:var(--text-muted);
    margin:0 0 0.6rem;
    font-weight:500;
  }
  .readout-grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:0.7rem 0.9rem;
  }
  .readout{
    display:flex;
    flex-direction:column;
    gap:0.15rem;
  }
  .readout .label{
    font-size:0.68rem;
    color:var(--text-muted);
    letter-spacing:0.04em;
  }
  .readout .value{
    font-family:"IBM Plex Mono", monospace;
    font-variant-numeric:tabular-nums;
    font-size:1.5rem;
    font-weight:600;
    line-height:1.1;
  }
  .readout .value.accent{color:var(--accent-teal);}
  .readout .value.small{font-size:1.05rem;}
  .badge{
    display:inline-block;
    margin-top:0.3rem;
    font-family:"IBM Plex Mono", monospace;
    font-size:0.66rem;
    letter-spacing:0.06em;
    color:var(--accent-amber);
    border:1px solid var(--accent-amber);
    border-radius:3px;
    padding:0.15rem 0.4rem;
  }
  .row{
    display:flex;
    gap:0.5rem;
    flex-wrap:wrap;
  }
  button.action{
    background:var(--surface);
    border:1px solid var(--border);
    color:var(--text);
    border-radius:5px;
    padding:0.5rem 0.8rem;
    font-family:"IBM Plex Sans", sans-serif;
    font-size:0.8rem;
    cursor:pointer;
    transition:background 0.15s, border-color 0.15s;
  }
  button.action:hover{border-color:var(--accent-teal);}
  button.action:disabled{opacity:0.4; cursor:default;}
  button.action:focus-visible{outline:2px solid var(--accent-teal); outline-offset:1px;}
  #shiftsList{
    max-height:190px;
    overflow-y:auto;
  }
  .edit-row{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:0.5rem;
    padding:0.4rem 0.1rem;
    border-bottom:1px solid var(--border);
    font-family:"IBM Plex Mono", monospace;
    font-size:0.76rem;
  }
  .edit-row:last-child{border-bottom:none;}
  .edit-row .marker-dot{
    display:inline-block;
    width:0.6rem;
    height:0.6rem;
    border-radius:2px;
    background:var(--accent-teal);
    transform:rotate(45deg);
    margin-right:0.5rem;
  }
  .note-inline{
    color:var(--text-muted);
    font-size:0.7rem;
  }
  .hard-toggle{
    display:flex;
    align-items:center;
    gap:0.25rem;
    font-size:0.7rem;
    color:var(--text-muted);
    white-space:nowrap;
  }
  .hard-toggle input{ margin:0; }
  button.action.del{
    padding:0.15rem 0.5rem;
    border-radius:4px;
    font-size:0.8rem;
    line-height:1.2;
    color:var(--accent-rose);
  }
  button.action.del:hover{border-color:var(--accent-rose);}
  .slider-row{
    display:flex;
    flex-direction:column;
    gap:0.35rem;
  }
  .slider-row .slabel{
    display:flex;
    justify-content:space-between;
    font-size:0.74rem;
    color:var(--text-muted);
    font-family:"IBM Plex Mono", monospace;
  }
  input[type="range"]{
    width:100%;
    accent-color:var(--accent-teal);
  }
  .note{
    font-size:0.74rem;
    color:var(--text-muted);
    line-height:1.5;
  }
  #statusLine{
    font-family:"IBM Plex Mono", monospace;
    font-size:0.72rem;
    color:var(--accent-teal);
    min-height:1.1em;
  }
  footer{
    padding:0.7rem 1.5rem;
    border-top:1px solid var(--border);
    font-size:0.7rem;
    color:var(--text-muted);
  }
</style>

<header>
  <h1>Ideallinien-Editor Spreewaldring</h1>
  <div class="sub">Mazda MX-5 ND · Linie per Drag bearbeiten · <span id="vehicleSub"></span></div>
</header>

<main>
  <div class="stage">
    <div class="canvas-wrap">
      <canvas id="trackCanvas"></canvas>
      <div id="hoverTooltip"></div>
    </div>
    <div class="legend">
      <div class="speedscale">
        <span>langsam</span>
        <span class="bar"></span>
        <span>schnell (km/h)</span>
      </div>
      <div><span class="swatch" style="background:rgba(224,72,60,0.55);"></span>Bremszone</div>
      <div><span class="swatch" style="background:rgba(69,214,196,0.92); transform:rotate(45deg);"></span>bearbeiteter Punkt</div>
      <div><span class="swatch" style="background:rgba(197,150,255,0.95);"></span>Schaltpunkt</div>
    </div>
    <div class="trace-wrap">
      <canvas id="traceCanvas"></canvas>
    </div>
  </div>

  <div class="rail">
    <div class="panel">
      <h2>Ergebnis</h2>
      <div class="readout-grid">
        <div class="readout">
          <span class="label">Rundenzeit</span>
          <span class="value accent" id="roLapTime">&ndash;</span>
        </div>
        <div class="readout">
          <span class="label">Top-Speed</span>
          <span class="value small" id="roTopSpeed">&ndash;</span>
        </div>
        <div class="readout">
          <span class="label">&Delta; zur Ausgangslinie</span>
          <span class="value small" id="roDelta">&ndash;</span>
        </div>
        <div class="readout">
          <span class="label">Streckenlaenge</span>
          <span class="value small" id="roLength">&ndash;</span>
        </div>
      </div>
      <div id="baselineBadge" class="badge" style="display:none;">Ausgangslinie (unbearbeitet)</div>
    </div>

    <div class="panel">
      <h2>Fahrsimulation</h2>
      <div class="row">
        <button class="action" id="simPrevBtn" title="einen Punkt zurueck">&#8592; Punkt</button>
        <button class="action" id="simPlayBtn">&#9654; Start</button>
        <button class="action" id="simNextBtn" title="einen Punkt vor">Punkt &#8594;</button>
      </div>
      <div class="pedals-row">
        <div class="pedal-bar brake"><div class="pedal-fill" id="brakeFill"></div></div>
        <div class="pedal-bar throttle"><div class="pedal-fill" id="throttleFill"></div></div>
        <div class="wheel"><div class="wheel-rotor" id="wheelRotor"><div class="wheel-dot"></div></div></div>
      </div>
      <div class="slider-row" style="margin-top:0.7rem;">
        <div class="slabel"><span>Geschwindigkeit</span><span id="simSpeedLabel">1.0&times; (Echtzeit)</span></div>
        <input type="range" id="simSpeed" min="0.25" max="8" step="0.25" value="1">
      </div>
      <div class="readout-grid" style="margin-top:0.7rem;">
        <div class="readout">
          <span class="label">Zeit</span>
          <span class="value small" id="simTime">0.0 s</span>
        </div>
        <div class="readout">
          <span class="label">Tempo</span>
          <span class="value small" id="simSpeedKmh">-</span>
        </div>
        <div class="readout">
          <span class="label">Gang</span>
          <span class="value small" id="simGear">-</span>
        </div>
        <div class="readout">
          <span class="label">Drehzahl</span>
          <span class="value small" id="simRpm">-</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <h2>Schaltvorgaenge</h2>
      <div id="shiftsList"></div>
    </div>

    <div class="panel">
      <h2>Ansicht</h2>
      <div class="row">
        <button class="action" id="zoomInBtn" title="Reinzoomen">+</button>
        <button class="action" id="zoomOutBtn" title="Rauszoomen">&minus;</button>
        <button class="action" id="zoomResetBtn">Ansicht zuruecksetzen</button>
      </div>
      <p class="note" style="margin-top:0.5rem;">
        Mausrad zum Zoomen, Doppelklick zoomt an dieser Stelle rein, im leeren
        Bereich (ausserhalb der Linie) ziehen zum Verschieben.
      </p>
    </div>

    <div class="panel">
      <h2>Physik (experimentell)</h2>
      <label class="hard-toggle" style="display:flex; align-items:center; gap:0.5rem;" title="Ersetzt Vollgas-sobald-moeglich durch eine lenkraten-basierte Gasvorgabe, kalibriert an EINER echten Kurve (R^2=0.76). WICHTIG: nicht verlaesslich fuer Streckenkurven validiert - Plausibilitaetspruefung und eine breitere Log-Aggregation zeigten schlechte Generalisierung (siehe docs/logs/projekt-stand.md). Nur zum Anschauen/Vergleichen gedacht.">
        <input type="checkbox" id="naturalThrottleCb">
        <span>Natuerliches Gasmodell (unkalibriert fuer Streckenkurven!)</span>
      </label>
      <label class="hard-toggle" id="vehicleRow" style="display:flex; align-items:center; gap:0.5rem; margin-top:0.5rem;">
        <span>Fahrzeugmodell</span>
        <select id="vehicleSelect"></select>
      </label>
    </div>

    <div class="panel">
      <h2>Bearbeiten</h2>
      <div class="slider-row">
        <div class="slabel"><span>Pinsel-Radius</span><span id="brushLabel">60 m</span></div>
        <input type="range" id="brushRadius" min="15" max="150" step="5" value="60">
      </div>
      <div class="row" style="margin-top:0.9rem;">
        <button class="action" id="undoBtn" disabled>&#8630; Rueckgaengig</button>
        <button class="action" id="resetBtn">Zuruecksetzen</button>
      </div>
      <div id="statusLine" style="margin-top:0.6rem;"></div>
    </div>

    <div class="panel">
      <h2>Bearbeitete Punkte</h2>
      <div id="editsList"></div>
      <div class="row" style="margin-top:0.7rem;">
        <button class="action" id="exportBtn">Bearbeitungen kopieren (fuer Support)</button>
        <button class="action" id="importBtn">Bearbeitungen importieren</button>
      </div>
      <textarea id="exportBox" readonly style="display:none; width:100%; margin-top:0.5rem; height:5rem; font-family:monospace; font-size:0.75rem;"></textarea>
      <textarea id="importBox" style="display:none; width:100%; margin-top:0.5rem; height:5rem; font-family:monospace; font-size:0.75rem;" placeholder="JSON-Liste hier einfuegen, z.B. von einer anderen Sitzung oder von Claude, dann auf 'Uebernehmen' klicken"></textarea>
      <div class="row" id="importActions" style="display:none; margin-top:0.4rem;">
        <button class="action" id="importApplyBtn">Uebernehmen</button>
        <button class="action" id="importCancelBtn">Abbrechen</button>
      </div>
    </div>

    <p class="note">
      Klicke auf die Linie und ziehe sie an eine neue Position (Pinsel mit
      Falloff bewegt die Nachbarschaft mit). Beim Loslassen wird genau dieser
      Punkt zum festen Fixpunkt (bleibt an dieser Stelle, wie ein
      Scheitelpunkt), die Strecke drumherum relaxiert automatisch
      (Geschwindigkeit&nbsp;&harr;&nbsp;Linie, kombinierter Reifenkraftkreis,
      Traktionsgrenze aus dem Antriebsstrangmodell) und bricht ab, sobald eine
      Iteration die Rundenzeit verschlechtert - nur die beste wird behalten.
      Alles laeuft direkt im Browser (kein Server), Aenderungen bleiben lokal
      in diesem Browser gespeichert.
    </p>
  </div>
</main>

<footer>Spreewaldring Training Center · Streckenmodell aus Orthofoto und DGM (LGB Brandenburg) · MX-5-Breite 1.74m angenommen · Traktionsmodell aus performance_simulation.py</footer>

<script type="application/json" id="viz-data">__DATA_JSON__</script>
<script>
(function(){
  const data = JSON.parse(document.getElementById('viz-data').textContent);
  const P = data.physics;
  const N = data.centerline.length;
  const CENTER = data.centerline;
  const PERP = data.perp;
  const LO = data.corridor_lo_m;
  const HI = data.corridor_hi_m;
  const STORAGE_KEY = 'spreewaldring_racing_line_editor_edits_v3_modell';  // Bearbeitungen haengen am Punktindex, v2 gehoert zur alten Geometrie
  // Nur Anzeige (Lenkrad): Ackermann-Naeherung. Alle Rechnung liegt im Python-Dienst.
  const WHEELBASE_M = 2.31, STEERING_RATIO = 15;
  const NEARBY_HINT_M = 80.0;   // nur fuer den Hover-Tooltip: "diese Fixpunkte beeinflussen sich spuerbar"
  let naturalThrottleMode = false;
  let vehicleName = P.DEFAULT_VEHICLE;
  function veh(){ return P.VEHICLES[vehicleName]; }

  function pointsFromNLat(nLat){
    const pts = new Array(N);
    for (let i=0;i<N;i++){
      pts[i] = [CENTER[i][0]+nLat[i]*PERP[i][0], CENTER[i][1]+nLat[i]*PERP[i][1]];
    }
    return pts;
  }

  async function api(path, body){
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  // ---------- Zustand ----------
  // Statt der reinen Punktposition wird die BEARBEITUNGSHISTORIE als geordnete Liste von Fixpunkten gespeichert
  // (ein erneutes Ziehen desselben Punktes rueckt ihn ans Ende). Der Dienst erzeugt die Linie daraus immer neu,
  // dadurch laesst sich jede einzelne Bearbeitung loeschen.
  let baselineLapTime = 0;
  let edits = loadEditsFromStorage();
  let nLat = null;
  let dragging = false, dragIndex = null, dragTarget = 0;
  let solveToken = 0;

  function validateEditsArray(arr){
    if (!Array.isArray(arr)) return null;
    const valid = arr.filter(e => e && Number.isInteger(e.index) && e.index>=0 && e.index<N
                            && typeof e.value === 'number' && isFinite(e.value));
    return valid.map(e => ({
      index: e.index, value: e.value,
      radius: (typeof e.radius === 'number' && isFinite(e.radius)) ? e.radius : data.brush_radius_m_default,
      hard: !!e.hard,
    }));
  }

  function loadEditsFromStorage(){
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return validateEditsArray(arr) || [];
    } catch(e) { return []; }
  }
  function saveEditsToStorage(){
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(edits)); } catch(e) {}
  }

  function applyResult(res){
    nLat = res.nLat;
    lastSim = res.sim;
    lastPts = pointsFromNLat(nLat);
  }

  // Volle Neuberechnung im Dienst (Optimierung mit allen Fixpunkten). Nur die juengste Anfrage zaehlt.
  async function solve(status, doneStatus){
    const token = ++solveToken;
    setStatus(status);
    try {
      const res = await api('/api/solve', {edits, vehicle: vehicleName, natural: naturalThrottleMode});
      if (token !== solveToken) return;
      applyResult(res);
      baselineLapTime = res.baselineLapTime;
      startTrace = res.standingStart;
      render();
      setStatus(doneStatus || '');
    } catch(e) {
      if (token === solveToken) setStatus('Python-Dienst nicht erreichbar: ' + e.message);
    }
  }

  // ---------- Rendering ----------
  const trackCanvas = document.getElementById('trackCanvas');
  const ctx = trackCanvas.getContext('2d');

  function computeShiftEvents(sim){
    // Ein Schaltpunkt ist ein Index, an dem sich der Gang aus der Simulation (plan_gears) gegenueber
    // dem VORHERIGEN Index aendert - liefert (index, fromGear, toGear, s_m,
    // up) je Ereignis, BEIDE Richtungen. Bewusst NICHT zyklisch
    // verglichen (Index 0 vs. n-1): an der Start/Ziel-Naht wuerde ein
    // legitimer Neustart der Gangwahl sonst als Schaltvorgang erscheinen.
    const n = sim.gear.length;
    const events = [];
    for (let i=1;i<n;i++){
      if (sim.gear[i] !== sim.gear[i-1]){
        events.push({index: i, fromGear: sim.gear[i-1], toGear: sim.gear[i], s_m: sim.s[i],
                     up: sim.gear[i] > sim.gear[i-1]});
      }
    }
    return events;
  }

  function speedColor(v){
    const stops = [[40,[58,109,209]],[90,[69,214,196]],[140,[232,163,61]],[190,[224,72,60]]];
    if (v <= stops[0][0]) return stops[0][1];
    if (v >= stops[stops.length-1][0]) return stops[stops.length-1][1];
    for (let i=0;i<stops.length-1;i++){
      const [s0,c0]=stops[i], [s1,c1]=stops[i+1];
      if (v>=s0 && v<=s1){
        const t=(v-s0)/(s1-s0);
        return [0,1,2].map(k=>Math.round(c0[k]+t*(c1[k]-c0[k])));
      }
    }
    return stops[stops.length-1][1];
  }

  function fitBounds(){
    const allX = data.left_edge.map(p=>p[0]).concat(data.right_edge.map(p=>p[0]));
    const allY = data.left_edge.map(p=>p[1]).concat(data.right_edge.map(p=>p[1]));
    return { minX:Math.min(...allX), maxX:Math.max(...allX), minY:Math.min(...allY), maxY:Math.max(...allY) };
  }
  const bounds = fitBounds();
  const PAD = 28;

  // Zoom/Pan-Zustand: zoom=1/pan=0 entspricht der urspruenglichen
  // "auf den Korridor eingepassten" Ansicht. project()/unproject() wenden
  // diese Transformation zusaetzlich zur festen Basis-Einpassung an, damit
  // Treffertest, Zeichnen und Maus-/Touch-Koordinaten immer konsistent
  // bleiben, egal wie weit reingezoomt/verschoben wurde.
  const view = {zoom: 1, panX: 0, panY: 0};
  const ZOOM_MIN = 1, ZOOM_MAX = 25;

  function baseScaleFor(w, h){
    const spanX=bounds.maxX-bounds.minX, spanY=bounds.maxY-bounds.minY;
    return Math.min((w-2*PAD)/spanX, (h-2*PAD)/spanY);
  }
  function project(p, w, h){
    const scale = baseScaleFor(w,h);
    const bx = PAD+(p[0]-bounds.minX)*scale;
    const by = h-PAD-(p[1]-bounds.minY)*scale;
    return [bx*view.zoom+view.panX, by*view.zoom+view.panY];
  }
  function unproject(x, y, w, h){
    const scale = baseScaleFor(w,h);
    const bx = (x-view.panX)/view.zoom;
    const by = (y-view.panY)/view.zoom;
    return [bounds.minX+(bx-PAD)/scale, bounds.minY+(h-PAD-by)/scale];
  }
  function zoomAt(px, py, factor){
    const newZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.zoom*factor));
    if (newZoom <= ZOOM_MIN){
      // ganz rausgezoomt: IMMER auf die komplette, zentrierte Einpassung
      // zurueck (unabhaengig vom bisherigen Pan) - sonst koennte man beim
      // Rauszoomen "haengenbleiben", ohne wieder die volle Strecke zu sehen.
      view.zoom = ZOOM_MIN; view.panX = 0; view.panY = 0;
    } else {
      const actual = newZoom/view.zoom;
      view.panX = px - (px-view.panX)*actual;
      view.panY = py - (py-view.panY)*actual;
      view.zoom = newZoom;
    }
    draw();
  }

  let bgImage=null, bgReady=false;
  if (data.background_image_jpeg_b64){
    bgImage = new Image();
    bgImage.onload = ()=>{ bgReady=true; render(); };
    bgImage.src = "data:image/jpeg;base64," + data.background_image_jpeg_b64;
  }

  function resizeCanvas(){
    const wrap = trackCanvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    trackCanvas.width = wrap.clientWidth*dpr;
    trackCanvas.height = wrap.clientHeight*dpr;
    ctx.setTransform(dpr,0,0,dpr,0,0);
    resizeTraceCanvas();
    render();
  }

  // ---------- Geschwindigkeit/Drehzahl ueber die Runde (Trace-Diagramm) ----------
  const traceCanvas = document.getElementById('traceCanvas');
  const tctx = traceCanvas.getContext('2d');

  function resizeTraceCanvas(){
    const wrap = traceCanvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    traceCanvas.width = wrap.clientWidth*dpr;
    traceCanvas.height = wrap.clientHeight*dpr;
    tctx.setTransform(dpr,0,0,dpr,0,0);
  }

  function drawTrace(){
    if (!lastSim) return;
    const sim = lastSim;
    const n = sim.s.length;
    const w = traceCanvas.clientWidth, h = traceCanvas.clientHeight;
    const padL=40, padR=40, padT=10, padB=20;
    tctx.clearRect(0,0,w,h);
    tctx.fillStyle = '#12171b';
    tctx.fillRect(0,0,w,h);

    const totalLen = sim.s[n-1] + sim.ds[n-1];
    const vMax = Math.max(...sim.vFinal)*3.6*1.08;
    const rpmMax = P.REDLINE_RPM*1.05;
    const xOf = s => padL + (s/totalLen)*(w-padL-padR);
    const yOfV = v => h-padB - (v/vMax)*(h-padT-padB);
    const yOfR = r => h-padB - (r/rpmMax)*(h-padT-padB);

    // Bremszonen-Hintergrund (dieselbe Definition wie auf der Karte). Punkte,
    // die smooth_wasted_accel_brake() auf eine kontrollierte Teillast-
    // Beschleunigung umgestellt hat, zaehlen NICHT als Bremsen (siehe dort).
    for (let i=0;i<n;i++){
      if (!sim.coasting[i] && sim.vFinal[i] < sim.vFwd[i]-1e-3){
        const x0=xOf(sim.s[i]), x1=xOf(sim.s[i]+sim.ds[i]);
        tctx.fillStyle = 'rgba(224,72,60,0.16)';
        tctx.fillRect(x0, padT, Math.max(x1-x0,1.2), h-padT-padB);
      }
    }

    // Redline-Referenzlinie
    tctx.save();
    tctx.setLineDash([3,3]);
    tctx.strokeStyle = 'rgba(238,242,243,0.25)';
    tctx.lineWidth = 1;
    tctx.beginPath();
    tctx.moveTo(padL, yOfR(P.REDLINE_RPM));
    tctx.lineTo(w-padR, yOfR(P.REDLINE_RPM));
    tctx.stroke();
    tctx.restore();
    tctx.fillStyle = 'rgba(238,242,243,0.4)';
    tctx.font = '500 9px "IBM Plex Mono", monospace';
    tctx.textAlign = 'left'; tctx.textBaseline = 'bottom';
    tctx.fillText('Redline', padL+2, yOfR(P.REDLINE_RPM)-2);

    // Schaltpunkte als vertikale Linien
    computeShiftEvents(sim).forEach(ev=>{
      const x = xOf(ev.s_m);
      tctx.strokeStyle = 'rgba(197,150,255,0.55)';
      tctx.lineWidth = 1.2;
      tctx.beginPath();
      tctx.moveTo(x, padT); tctx.lineTo(x, h-padB);
      tctx.stroke();
    });

    // Geschwindigkeitskurve (teal, linke Achse)
    tctx.strokeStyle = '#45d6c4';
    tctx.lineWidth = 1.6;
    tctx.beginPath();
    for (let i=0;i<=n;i++){
      const k = i%n;
      const x = xOf(sim.s[k] + (i===n ? totalLen-sim.s[k] : 0));
      const y = yOfV(sim.vFinal[k]*3.6);
      if (i===0) tctx.moveTo(x,y); else tctx.lineTo(x,y);
    }
    tctx.stroke();

    // Drehzahlkurve (amber, rechte Achse)
    tctx.strokeStyle = '#e8a33d';
    tctx.lineWidth = 1.6;
    tctx.beginPath();
    for (let i=0;i<=n;i++){
      const k = i%n;
      const x = xOf(sim.s[k] + (i===n ? totalLen-sim.s[k] : 0));
      const y = yOfR(sim.rpm[k]);
      if (i===0) tctx.moveTo(x,y); else tctx.lineTo(x,y);
    }
    tctx.stroke();

    // Achsen/Beschriftung
    tctx.strokeStyle = 'rgba(238,242,243,0.3)';
    tctx.lineWidth = 1;
    tctx.beginPath();
    tctx.moveTo(padL, padT); tctx.lineTo(padL, h-padB); tctx.lineTo(w-padR, h-padB);
    tctx.stroke();
    tctx.font = '500 9px "IBM Plex Mono", monospace';
    tctx.textBaseline = 'middle';
    tctx.fillStyle = '#45d6c4'; tctx.textAlign = 'right';
    [0, vMax/2, vMax].forEach(v=>tctx.fillText(Math.round(v)+'', padL-4, yOfV(v)));
    tctx.fillStyle = '#e8a33d'; tctx.textAlign = 'left';
    [0, rpmMax/2, rpmMax].forEach(r=>tctx.fillText(Math.round(r)+'', w-padR+4, yOfR(r)));
    tctx.fillStyle = 'rgba(238,242,243,0.5)'; tctx.textAlign = 'center'; tctx.textBaseline = 'top';
    [0, totalLen/2, totalLen].forEach(s=>tctx.fillText(Math.round(s)+'m', xOf(s), h-padB+3));
    tctx.save();
    tctx.textAlign = 'left'; tctx.textBaseline = 'top';
    tctx.fillStyle = '#45d6c4'; tctx.fillText('km/h', 2, 2);
    tctx.fillStyle = '#e8a33d'; tctx.textAlign = 'right'; tctx.fillText('U/min', w-2, 2);
    tctx.restore();
  }

  function drawPolyline(points, w, h, close){
    ctx.beginPath();
    points.forEach((p,i)=>{
      const [x,y]=project(p,w,h);
      if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    });
    if (close) ctx.closePath();
  }

  let lastSim = null, lastPts = null;

  function draw(){
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    ctx.clearRect(0,0,w,h);
    ctx.fillStyle='#12171b';
    ctx.fillRect(0,0,w,h);

    if (bgReady && data.background_extent_local){
      const [ex0,ey0,ex1,ey1] = data.background_extent_local;
      const [px0,py0] = project([ex0,ey1], w, h);
      const [px1,py1] = project([ex1,ey0], w, h);
      ctx.drawImage(bgImage, px0, py0, px1-px0, py1-py0);
      ctx.fillStyle='rgba(18,23,27,0.32)';
      ctx.fillRect(0,0,w,h);
    }

    const ribbon = data.left_edge.concat([...data.right_edge].reverse());
    drawPolyline(ribbon, w, h, true);
    ctx.strokeStyle='rgba(238,242,243,0.55)';
    ctx.lineWidth=1;
    ctx.stroke();
    if (!bgReady){ ctx.fillStyle='#3a4148'; ctx.fill(); }

    ctx.save();
    ctx.setLineDash([5,6]);
    ctx.strokeStyle='rgba(238,242,243,0.35)';
    ctx.lineWidth=1;
    drawPolyline(data.centerline, w, h, true);
    ctx.stroke();
    ctx.restore();

    const pts = lastPts, sim = lastSim;
    if (!sim) return;

    // Bremszonen als farbiges Band UNTER der Ideallinie (siehe Docstring:
    // physikalisch aus vFinal < vFwd abgeleitet, kein Schwellwert-Rateversuch).
    // Teillast-geglaettete Punkte (siehe smooth_wasted_accel_brake) zaehlen nicht
    // als Bremsen.
    for (let i=0;i<N;i++){
      const j=(i+1)%N;
      const braking = !sim.coasting[i] && sim.vFinal[i] < sim.vFwd[i]-1e-3;
      if (!braking) continue;
      const p0=project(pts[i],w,h), p1=project(pts[j],w,h);
      ctx.strokeStyle='rgba(224,72,60,0.5)';
      ctx.lineWidth=9;
      ctx.beginPath(); ctx.moveTo(p0[0],p0[1]); ctx.lineTo(p1[0],p1[1]); ctx.stroke();
    }

    // Ideallinie, nach Geschwindigkeit eingefaerbt
    for (let i=0;i<N;i++){
      const j=(i+1)%N;
      const p0=project(pts[i],w,h), p1=project(pts[j],w,h);
      const [r,g,b] = speedColor(sim.vFinal[i]*3.6);
      ctx.strokeStyle=`rgb(${r},${g},${b})`;
      ctx.lineWidth=3.2;
      ctx.beginPath(); ctx.moveTo(p0[0],p0[1]); ctx.lineTo(p1[0],p1[1]); ctx.stroke();
    }

    // aktiver Pinsel-Radius waehrend des Ziehens
    if (dragging && dragIndex != null){
      const win = Math.max(1, Math.round(brushRadiusM/P.RESAMPLE_STEP_M));
      const idxs=[]; for (let k=-win;k<=win;k++) idxs.push(((dragIndex+k)%N+N)%N);
      ctx.save();
      ctx.strokeStyle='rgba(232,163,61,0.7)';
      ctx.lineWidth=6;
      ctx.beginPath();
      idxs.forEach((idx,k)=>{ const [x,y]=project(pts[idx],w,h); if(k===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
      ctx.stroke();
      ctx.restore();
    }

    // Schaltpunkte: kurzer Querstrich genau an der Stelle, an der ein
    // Gangwechsel beginnt, plus "3→4"-Beschriftung leicht versetzt daneben.
    computeShiftEvents(sim).forEach(ev=>{
      const [x,y] = project(pts[ev.index], w, h);
      const j = (ev.index+1)%N;
      const [x2,y2] = project(pts[j], w, h);
      const dx = x2-x, dy = y2-y;
      const len = Math.hypot(dx,dy) || 1;
      const nx = -dy/len, ny = dx/len; // senkrecht zur Fahrtrichtung
      ctx.strokeStyle = 'rgba(197,150,255,0.95)';
      ctx.lineWidth = 2.4;
      ctx.beginPath();
      ctx.moveTo(x-nx*7, y-ny*7);
      ctx.lineTo(x+nx*7, y+ny*7);
      ctx.stroke();
      ctx.fillStyle = 'rgba(197,150,255,0.95)';
      ctx.font = '600 10px "IBM Plex Mono", monospace';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(`${ev.fromGear}→${ev.toGear}`, x+nx*11+4, y+ny*11);
    });

    // Marker fuer manuell bearbeitete Punkte (Raute, Teal - unterscheidbar von
    // den amberfarbenen Kurvennummern-Kreisen)
    edits.forEach((e,i)=>{
      const [x,y] = project(pts[e.index], w, h);
      ctx.save();
      ctx.translate(x,y);
      ctx.rotate(Math.PI/4);
      ctx.fillStyle = 'rgba(69,214,196,0.92)';
      ctx.strokeStyle = '#0d1a18';
      ctx.lineWidth = 1.3;
      ctx.fillRect(-5,-5,10,10);
      ctx.strokeRect(-5,-5,10,10);
      ctx.restore();
      ctx.fillStyle = '#0d1a18';
      ctx.font = '700 9px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(i+1), x, y+0.5);
    });

    if (data.corners){
      data.corners.forEach(c=>{
        const [x,y]=project([c.x,c.y],w,h);
        ctx.beginPath(); ctx.arc(x,y,9,0,Math.PI*2);
        ctx.fillStyle='rgba(18,23,27,0.85)'; ctx.fill();
        ctx.strokeStyle='#e8a33d'; ctx.lineWidth=1.4; ctx.stroke();
        ctx.fillStyle='#e8a33d';
        ctx.font='600 11px "IBM Plex Mono", monospace';
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(String(c.number), x, y+0.5);
      });
    }

    drawCar(pts, w, h);
  }

  // ---------- Fahrsimulation (Echtzeit-Wiedergabe entlang der Runde) ----------
  const sim_ = {playing:false, t:0, speed:1, lastTs:null};
  let dashState = null, dashBusy = false, dashSentAt = 0;  // siehe sendDash()
  const DASH_SEND_MS = 50;

  // Reine Anzeige-Zugabe (Nutzerwunsch 09.09.2026): die allererste Wiedergabe
  // nach einem Seiten-Reload zeigt einen stehenden Start (v=0 an der
  // Start/Ziel-Linie, Vollgas-Sprint) statt direkt mit Rundentempo
  // loszufahren. WICHTIG: greift NICHT in lastSim/nLat/edits/lapTime/den
  // Export ein - reiner lokaler Anzeige-Zustand (nur s/v/gear/shiftT unten),
  // die eigentliche Rundensimulation ("Grundlage aller Berechnungen", im
  // Nutzerbild bereits "Runde 2") bleibt komplett unveraendert. Sobald das
  // Launch-Tempo das reale Rundentempo an DERSELBEN Streckenposition
  // erreicht, uebernimmt ab da nahtlos die normale, unveraenderte Wiedergabe
  // (Uebergang per sim_.t = sim.t[i], danach exakt der bisherige Codepfad).
  // Initial (vor dem ersten Play) schon gesetzt, nicht erst bei Play erzeugt
  // - dadurch steht der Wagen auch im Standbild vor dem ersten Play still.
  // Der Verlauf kommt aus dem Dienst (standing_start_trace), hier wird er nur abgespielt.
  let standingStart = {s: 0, v: 0, gear: 1, rpm: 0, load: 0, tp: null, pedal: 0, clock: 0};
  let startTrace = null;

  function stepStandingStart(dt){
    if (!startTrace) return;
    const clock = standingStart.clock + dt;
    const i = Math.floor(clock/startTrace.dt);
    if (i >= startTrace.s.length){
      standingStart = null;
      sim_.t = startTrace.joinT;
      return;
    }
    standingStart = {s: startTrace.s[i], v: startTrace.v[i], gear: startTrace.gear[i], rpm: startTrace.rpm[i],
                     load: startTrace.load[i], tp: startTrace.tp[i], pedal: startTrace.pedal[i], clock};
  }

  function positionFromArcLength(pts, sim, sNow){
    const i = findSimIndex(sim.s, Math.min(sNow, sim.s[N-1]));
    const j = (i+1)%N;
    const segLen = sim.ds[i];
    const frac = segLen>1e-9 ? Math.max(0, Math.min(1, (sNow-sim.s[i])/segLen)) : 0;
    const x = pts[i][0]+(pts[j][0]-pts[i][0])*frac;
    const y = pts[i][1]+(pts[j][1]-pts[i][1])*frac;
    const heading = Math.atan2(pts[j][1]-pts[i][1], pts[j][0]-pts[i][0]);
    return {x, y, i, heading};
  }

  function findSimIndex(tArr, tNow){
    const n = tArr.length;
    for (let i=1;i<n;i++){ if (tArr[i] > tNow) return i-1; }
    return n-1;
  }

  function carPositionAt(pts, sim, tNow){
    const n = pts.length;
    const i = findSimIndex(sim.t, tNow);
    const j = (i+1)%n;
    const segT = (j===0 ? sim.lapTime : sim.t[j]) - sim.t[i];
    const frac = segT>1e-9 ? (tNow-sim.t[i])/segT : 0;
    const x = pts[i][0]+(pts[j][0]-pts[i][0])*frac;
    const y = pts[i][1]+(pts[j][1]-pts[i][1])*frac;
    const heading = Math.atan2(pts[j][1]-pts[i][1], pts[j][0]-pts[i][0]);
    return {x, y, i, heading};
  }

  function drawCar(pts, w, h){
    if (!lastSim) return;
    const {x, y, i, heading} = standingStart
      ? positionFromArcLength(pts, lastSim, standingStart.s)
      : carPositionAt(pts, lastSim, sim_.t);
    const [sx, sy] = project([x,y], w, h);
    // Bildschirm-Blickwinkel (Nord=oben, Weltwinkel invertiert) fuer die Rotation beruecksichtigen
    const screenHeading = -heading;
    ctx.save();
    ctx.translate(sx, sy);
    ctx.rotate(screenHeading);
    ctx.fillStyle = '#eef2f3';
    ctx.strokeStyle = '#12171b';
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(9,0); ctx.lineTo(-6,5); ctx.lineTo(-3,0); ctx.lineTo(-6,-5);
    ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.restore();

    document.getElementById('simTime').textContent = standingStart ? 'Start' : (sim_.t.toFixed(1)+' s');
    document.getElementById('simSpeedKmh').textContent = Math.round((standingStart ? standingStart.v : lastSim.vFinal[i])*3.6)+' km/h';
    document.getElementById('simGear').textContent = standingStart ? standingStart.gear : lastSim.gear[i];
    document.getElementById('simRpm').textContent = Math.round(standingStart ? standingStart.rpm : lastSim.rpm[i])+' U/min';

    // Bremse: voll am Limit bremsend (Bremszone, gleiche Definition wie das rote Kartenband), AUSSER an den von
    // smooth_short_brake_spikes() geglaetteten Stellen, dort die berechnete Teilbremsung. Gas: seit 08.11.2026 das
    // Pedal aus throttle_trace() (Tempoverlauf rueckwaerts ueber Fahrleistungsmodell und Teillastkennfeld).
    const isCoasting = !standingStart && !!lastSim.coasting[i];
    const braking = !standingStart && !isCoasting && lastSim.vFinal[i] < lastSim.vFwd[i]-1e-3;
    const brakePct = braking ? Math.round((lastSim.brakeFrac[i] ?? 1)*100) : 0;
    document.getElementById('brakeFill').style.height = brakePct + '%';
    // Im experimentellen "natuerlichen Gasmodell" kommt die Gaspedal-
    // Stellung IMMER aus der Lenkrate-Formel (nicht nur an geglaetteten
    // Stellen) - siehe naturalThrottleMode. ABER: die Formel wird im
    // Vorwaertspass berechnet, BEVOR der Rueckwaertspass die Geschwindigkeit
    // fuer eine spaetere Bremszone kappt - an einem Bremspunkt darf die
    // Anzeige den (dann ueberholten) Vorwaerts-Wert nicht mehr zeigen: man
    // kann nicht gleichzeitig bremsen und Gas geben (Nutzerhinweis
    // 08.09.2026).
    // Sonst: Gaspedal rueckwaerts aus dem Tempoverlauf ueber das Teillastkennfeld (Dienst, throttle_trace()).
    const throttlePct = standingStart ? standingStart.pedal : (lastSim.naturalMode
      ? (braking ? 0 : Math.round((lastSim.throttleFracNatural[i] ?? 0)*100))
      : Math.round(lastSim.pedalPct[i]));
    document.getElementById('throttleFill').style.height = throttlePct + '%';

    // Lenkwinkel: im Projekt gibt es kein Lenkmodell (nur Kruemmungsradius) -
    // grobe Ackermann-Naeherung (Radstand oeffentliche Herstellerangabe MX-5
    // ND, KEIN gemessener/validierter Wert wie der Rest des Fahrzeugmodells)
    // nur fuer diese Anzeige, keine physikalische Grundlage fuer irgendeine
    // andere Berechnung im Tool. Vorzeichen (Links-/Rechtskurve) ueber
    // dieselbe Fensterbreite wie compute_curvature_radius()/steer_rate_deg_s()
    // bestimmt (CURVATURE_WINDOW_M) statt ueber unmittelbare Nachbarpunkte -
    // Nutzer-Report 2026-09-08: bei Nachbarpunkten (~3m Basis) durchlief die
    // Lenkradanzeige mitten in einer durchgehenden Kurve kurz die Nullachse
    // (z.B. Punkt 513 einer Linkskurve bis 517), obwohl die tatsaechliche
    // (fensterbasierte) Lenkrate dort glatt und einseitig blieb - derselbe
    // Diskretisierungsartefakt wie bei der urspruenglichen 934°/s-Messung,
    // nur in dieser separaten Rohformel nie mitkorrigiert.
    const wSign = Math.max(1, Math.round(P.CURVATURE_WINDOW_M/P.RESAMPLE_STEP_M));
    const p0 = pts[(i-wSign+N)%N], p1 = pts[i], p2 = pts[(i+wSign)%N];
    const cross = (p1[0]-p0[0])*(p2[1]-p0[1]) - (p2[0]-p0[0])*(p1[1]-p0[1]);
    const turnSign = cross > 0 ? -1 : (cross < 0 ? 1 : 0);
    const roadWheelDeg = Math.atan(WHEELBASE_M/Math.max(lastSim.radius[i],1)) * 180/Math.PI * turnSign;
    const wheelDeg = Math.max(-150, Math.min(150, roadWheelDeg*STEERING_RATIO));
    document.getElementById('wheelRotor').style.transform = `rotate(${wheelDeg}deg)`;
    dashState = {speed_kmh: (standingStart ? standingStart.v : lastSim.vFinal[i])*3.6,
                 rpm: standingStart ? standingStart.rpm : lastSim.rpm[i],
                 gear: standingStart ? standingStart.gear : lastSim.gear[i],
                 throttle_pct: throttlePct, brake_pct: brakePct,
                 load_pct: standingStart ? standingStart.load : lastSim.loadPct[i],
                 tp_pct: standingStart ? standingStart.tp : lastSim.tpPct[i],
                 steer_deg: -wheelDeg,  // Dash: links positiv, hier rechts positiv
                 lap_t: standingStart ? null : sim_.t};
  }

  // Waehrend der Wiedergabe gehen die angezeigten Werte ueber den Dienst ans Dash im Auto (/api/dash).
  // Eine Anfrage zugleich, hoechstens DASH_SEND_MS-Takt; bei Pause verstummt das Dash nach 1 s von selbst.
  function sendDash(ts){
    if (!dashState || dashBusy || ts - dashSentAt < DASH_SEND_MS) return;
    dashBusy = true; dashSentAt = ts;
    api('/api/dash', dashState).catch(()=>{}).finally(()=>{ dashBusy = false; });
  }

  function simFrame(ts){
    if (!sim_.playing) return;
    if (sim_.lastTs != null){
      const dt = (ts-sim_.lastTs)/1000 * sim_.speed;
      if (standingStart){
        stepStandingStart(dt); // setzt bei Abschluss selbst sim_.t (siehe dort)
      } else {
        sim_.t += dt;
        if (lastSim && sim_.t >= lastSim.lapTime) sim_.t %= lastSim.lapTime;
      }
    }
    sim_.lastTs = ts;
    draw();
    sendDash(ts);
    requestAnimationFrame(simFrame);
  }

  document.getElementById('simPlayBtn').addEventListener('click', ()=>{
    sim_.playing = !sim_.playing;
    const btn = document.getElementById('simPlayBtn');
    if (sim_.playing){
      btn.innerHTML = '&#10074;&#10074; Pause';
      sim_.lastTs = null;
      requestAnimationFrame(simFrame);
    } else {
      btn.innerHTML = '&#9654; Start';
    }
  });
  function pauseSim(){
    if (!sim_.playing) return;
    sim_.playing = false;
    document.getElementById('simPlayBtn').innerHTML = '&#9654; Start';
  }
  // Punktweises Vor-/Zurueckspringen fuer die Feininspektion im Pausenmodus -
  // springt direkt auf die Zeit des Nachbarpunkts in lastSim.t, statt eine
  // kontinuierliche Zeitspanne zu addieren (siehe findSimIndex/carPositionAt).
  function stepSimPoint(delta){
    if (!lastSim) return;
    pauseSim();
    standingStart = null; // manuelles Springen beendet den Start-Sprint, siehe oben
    const i = findSimIndex(lastSim.t, sim_.t);
    const j = ((i+delta)%N+N)%N;
    sim_.t = lastSim.t[j];
    draw();
  }
  document.getElementById('simPrevBtn').addEventListener('click', ()=>stepSimPoint(-1));
  document.getElementById('simNextBtn').addEventListener('click', ()=>stepSimPoint(1));
  document.getElementById('simSpeed').addEventListener('input', (e)=>{
    sim_.speed = parseFloat(e.target.value);
    document.getElementById('simSpeedLabel').textContent =
      sim_.speed.toFixed(2).replace(/\.?0+$/,'') + 'x' + (sim_.speed===1 ? ' (Echtzeit)' : '');
  });

  function updateReadouts(){
    const sim = lastSim;
    document.getElementById('roLapTime').textContent = sim.lapTime.toFixed(2) + ' s';
    document.getElementById('roTopSpeed').textContent = Math.round(Math.max(...sim.vFinal)*3.6) + ' km/h';
    const delta = sim.lapTime - baselineLapTime;
    document.getElementById('roDelta').textContent = (delta>=0?'+':'') + delta.toFixed(2) + ' s';
    const totalLen = sim.ds.reduce((a,b)=>a+b,0);
    document.getElementById('roLength').textContent = totalLen.toFixed(0) + ' m';
    document.getElementById('baselineBadge').style.display = edits.length===0 ? 'inline-block' : 'none';
    document.getElementById('undoBtn').disabled = edits.length===0;
  }

  function renderEditsList(){
    const container = document.getElementById('editsList');
    container.innerHTML = '';
    if (edits.length===0){
      const p = document.createElement('div');
      p.className = 'note';
      p.textContent = 'Noch keine Bearbeitungen - klicke auf die Linie und ziehe sie.';
      container.appendChild(p);
      return;
    }
    edits.forEach((e,i)=>{
      const row = document.createElement('div');
      row.className = 'edit-row';
      // Bei weichen Fixpunkten kann der tatsaechlich erreichte Wert vom
      // gezogenen Zielwert abweichen (siehe pinned_optimize: Zeitvorteil vs.
      // Zielnaehe) - das direkt anzuzeigen macht sichtbar, wo die Physik eine
      // andere Linie fuer schneller haelt, und ist die Datengrundlage fuer
      // die spaeter geplante Fahrfehler-Analyse im "hart"-Modus.
      const achieved = nLat[e.index];
      const delta = achieved - e.value;
      const deltaTxt = (!e.hard && Math.abs(delta) > 0.02)
        ? ` <span class="note-inline">(erreicht ${achieved.toFixed(2)}m, ${delta>0?'+':''}${delta.toFixed(2)}m)</span>`
        : '';
      const lbl = document.createElement('span');
      lbl.innerHTML = `<span class="marker-dot"></span>#${i+1} &middot; Punkt ${e.index}${deltaTxt}`;
      const hardLbl = document.createElement('label');
      hardLbl.className = 'hard-toggle';
      hardLbl.title = 'muss exakt getroffen werden (fuer spaetere Fahrfehler-Analyse) statt weicher, zeitoptimaler Zielwert';
      const hardCb = document.createElement('input');
      hardCb.type = 'checkbox';
      hardCb.checked = !!e.hard;
      hardCb.addEventListener('change', ()=>{
        e.hard = hardCb.checked;
        solve('neu berechnen ...');
      });
      hardLbl.appendChild(hardCb);
      hardLbl.appendChild(document.createTextNode('hart'));
      const del = document.createElement('button');
      del.className = 'action del';
      del.textContent = '×';
      del.title = 'diese Bearbeitung entfernen';
      del.addEventListener('click', ()=>deleteEdit(i));
      row.appendChild(lbl);
      row.appendChild(hardLbl);
      row.appendChild(del);
      container.appendChild(row);
    });
  }

  function deleteEdit(i){
    edits.splice(i, 1);
    solve('neu berechnen ...');
  }

  function renderShiftsList(){
    const container = document.getElementById('shiftsList');
    container.innerHTML = '';
    const events = computeShiftEvents(lastSim);
    if (events.length === 0){
      const p = document.createElement('div');
      p.className = 'note';
      p.textContent = 'Keine Schaltvorgaenge auf dieser Linie.';
      container.appendChild(p);
      return;
    }
    events.forEach(ev=>{
      const row = document.createElement('div');
      row.className = 'edit-row';
      const lbl = document.createElement('span');
      lbl.innerHTML = `<span class="marker-dot" style="background:rgba(197,150,255,0.95); transform:none; border-radius:1px; width:0.7rem; height:0.35rem;"></span>`
        + `Gang ${ev.fromGear}→${ev.toGear} bei s=${Math.round(ev.s_m)}m`;
      row.appendChild(lbl);
      container.appendChild(row);
    });
  }

  function render(){
    if (!lastSim) return;
    draw();
    drawTrace();
    updateReadouts();
    renderEditsList();
    renderShiftsList();
    saveEditsToStorage();
  }

  document.getElementById('naturalThrottleCb').addEventListener('change', (e)=>{
    naturalThrottleMode = e.target.checked;
    solve(naturalThrottleMode ? 'natuerliches Gasmodell ...' : 'zeitoptimales Gasmodell ...');
  });

  // Fahrzeugmodell umschalten: Der Dienst optimiert die Linie fuer das gewaehlte Modell neu.
  {
    const sel = document.getElementById('vehicleSelect');
    const sub = document.getElementById('vehicleSub');
    const m = P.VEHICLES;
    sel.innerHTML = Object.keys(m).map(k=>`<option value="${k}">${m[k].label}</option>`).join('');
    sel.value = vehicleName;
    function describe(){
      const v = veh();
      sub.textContent = `${v.label}: quer ${v.lat_g.toFixed(2)} g, bremsen ${v.brake_g.toFixed(2)} g`;
      document.getElementById('vehicleRow').title = `Grip-Ellipse quer ${v.lat_g.toFixed(2)} g / beschleunigen `
        + `${v.accel_g.toFixed(2)} g / bremsen ${v.brake_g.toFixed(2)} g, wahres Tempo = ${v.speed_factor.toFixed(3)} x VehicleSpeed. `
        + `Aus Alltagsfahrten: Untergrenze, kein gemessenes Reifenlimit (mx5_vehicle_model.py).`;
    }
    describe();
    sel.addEventListener('change', (e)=>{
      vehicleName = e.target.value;
      describe();
      solve(`${veh().label}: optimiere ...`);
    });
  }

  // ---------- Interaktion ----------
  let brushRadiusM = data.brush_radius_m_default;
  const brushSlider = document.getElementById('brushRadius');
  brushSlider.value = brushRadiusM;
  document.getElementById('brushLabel').textContent = brushRadiusM.toFixed(0) + ' m';
  brushSlider.addEventListener('input', (e)=>{
    brushRadiusM = parseFloat(e.target.value);
    document.getElementById('brushLabel').textContent = brushRadiusM.toFixed(0) + ' m';
  });

  function nearestIndex(worldX, worldY){
    let best=-1, bestD=Infinity;
    for (let i=0;i<N;i++){
      const dx=lastPts[i][0]-worldX, dy=lastPts[i][1]-worldY;
      const d=dx*dx+dy*dy;
      if (d<bestD){ bestD=d; best=i; }
    }
    return {index:best, dist:Math.sqrt(bestD)};
  }

  function setStatus(msg){ document.getElementById('statusLine').textContent = msg; }

  function targetLatOffset(index, worldX, worldY){
    const dx=worldX-CENTER[index][0], dy=worldY-CENTER[index][1];
    let val = dx*PERP[index][0] + dy*PERP[index][1];
    return Math.min(Math.max(val, LO[index]), HI[index]);
  }

  function pixelToHitRadiusM(){
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    return 16/(baseScaleFor(w,h)*view.zoom); // 16px Trefferradius, in Meter umgerechnet
  }

  let nLatAtDragStart = null;
  let panning = false;
  let panStart = null;

  function pointerPos(e){
    const rect = trackCanvas.getBoundingClientRect();
    return [e.clientX-rect.left, e.clientY-rect.top];
  }

  trackCanvas.addEventListener('pointerdown', (e)=>{
    const [px,py] = pointerPos(e);
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    const [wx,wy] = unproject(px,py,w,h);
    if (!lastPts) return;
    const {index,dist} = nearestIndex(wx,wy);
    const hitR = pixelToHitRadiusM();
    trackCanvas.setPointerCapture(e.pointerId);
    hideHoverTooltip();
    if (dist <= hitR){
      dragging = true; dragIndex = index;
      nLatAtDragStart = nLat.slice();
      dragTarget = nLat[index];
      setStatus('ziehen ...');
    } else {
      // Klick auf leeren Bereich (nicht nah genug an der Linie) -> Verschieben
      // der Ansicht statt Punkt-Bearbeitung.
      panning = true;
      panStart = {px, py, panX: view.panX, panY: view.panY};
      trackCanvas.style.cursor = 'grabbing';
    }
  });

  trackCanvas.addEventListener('pointermove', (e)=>{
    const [px,py] = pointerPos(e);
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    if (dragging){
      const [wx,wy] = unproject(px,py,w,h);
      dragTarget = targetLatOffset(dragIndex, wx, wy);
      requestPreview();
      return;
    }
    if (panning){
      view.panX = panStart.panX + (px-panStart.px);
      view.panY = panStart.panY + (py-panStart.py);
      draw();
      return;
    }
    if (!lastPts) return;
    // reine Hover-Rueckmeldung (Cursor + Tooltip mit naechsten Fixpunkten),
    // keine Interaktion aktiv
    const [wx,wy] = unproject(px,py,w,h);
    const {index, dist} = nearestIndex(wx,wy);
    if (dist <= pixelToHitRadiusM()){
      trackCanvas.style.cursor = 'pointer';
      showHoverTooltip(px, py, index);
    } else {
      trackCanvas.style.cursor = 'grab';
      hideHoverTooltip();
    }
  });
  trackCanvas.addEventListener('pointerleave', hideHoverTooltip);

  // Vorschau beim Ziehen: immer nur eine Anfrage unterwegs, danach die jeweils neueste Mausposition.
  let previewBusy = false;
  function requestPreview(){
    if (previewBusy) return;
    previewBusy = true;
    const target = dragTarget;
    api('/api/preview', {nLat: nLatAtDragStart, index: dragIndex, target, radius: brushRadiusM,
                         vehicle: vehicleName, natural: naturalThrottleMode})
      .then(res=>{
        previewBusy = false;
        if (!dragging) return;
        applyResult(res);
        draw();
        drawTrace();
        updateReadouts();
        if (dragTarget !== target) requestPreview();
      })
      .catch(e=>{ previewBusy = false; setStatus('Python-Dienst nicht erreichbar: ' + e.message); });
  }

  // Distanz zweier Punktindizes entlang der geschlossenen Strecke in Metern.
  function circularGapM(i, j){
    const d = Math.abs(i-j);
    return Math.min(d, N-d) * P.RESAMPLE_STEP_M;
  }

  function showHoverTooltip(px, py, index){
    const tip = document.getElementById('hoverTooltip');
    const sVal = lastSim.s[index];
    const nearby = edits
      .filter(e => e.index !== index)
      .map(e => ({ index: e.index, gapM: circularGapM(index, e.index) }))
      .sort((a,b) => a.gapM - b.gapM)
      .slice(0, 3);
    let html = `Punkt ${index} &middot; s=${sVal.toFixed(0)}m`;
    if (nearby.length){
      html += '<br>' + nearby.map(nb => {
        const cls = nb.gapM <= NEARBY_HINT_M ? 'chainable' : 'nearest';
        const note = nb.gapM <= NEARBY_HINT_M ? ' (beeinflusst sich)' : '';
        return `<span class="${cls}">Fixpunkt #${nb.index} &middot; ${nb.gapM.toFixed(0)}m${note}</span>`;
      }).join('<br>');
    }
    tip.innerHTML = html;
    tip.style.display = 'block';
    const wrap = trackCanvas.parentElement;
    let left = px + 14, top = py + 14;
    if (left + tip.offsetWidth > wrap.clientWidth) left = px - tip.offsetWidth - 14;
    if (top + tip.offsetHeight > wrap.clientHeight) top = py - tip.offsetHeight - 14;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }

  function hideHoverTooltip(){
    document.getElementById('hoverTooltip').style.display = 'none';
  }

  trackCanvas.addEventListener('wheel', (e)=>{
    e.preventDefault();
    const [px,py] = pointerPos(e);
    const factor = Math.exp(-e.deltaY*0.0015);
    zoomAt(px, py, factor);
  }, {passive:false});

  trackCanvas.addEventListener('dblclick', (e)=>{
    const [px,py] = pointerPos(e);
    zoomAt(px, py, 1.8);
  });

  function endDrag(e){
    if (panning){
      panning = false;
      trackCanvas.style.cursor = 'grab';
      return;
    }
    if (!dragging) return;
    dragging = false;
    const finalTarget = dragTarget;
    const idx = dragIndex;
    // erneutes Ziehen eines bereits bearbeiteten Punktes: Wert aktualisieren
    // und ans Ende der Historie ruecken, statt einen zweiten Eintrag fuer
    // denselben Punkt anzulegen. Nah beieinanderliegende, aber verschiedene
    // Fixpunkte werden NICHT mehr automatisch geloescht/zusammengelegt - die
    // gemeinsame querkraftbasierte Relaxation (siehe pinned_optimize) kommt
    // mit beliebig dicht stehenden weichen Zielpunkten klar, das war nur eine
    // Notloesung fuer die inzwischen abgeloeste rein geometrische Kettenglaettung.
    const existing = edits.findIndex(e2 => e2.index === idx);
    const hardFlag = existing >= 0 ? !!edits[existing].hard : false;
    if (existing >= 0) edits.splice(existing, 1);
    edits.push({index: idx, value: finalTarget, radius: brushRadiusM, hard: hardFlag});
    dragIndex = null;
    solve('optimiere ...');
  }
  trackCanvas.addEventListener('pointerup', endDrag);
  trackCanvas.addEventListener('pointercancel', endDrag);

  document.getElementById('undoBtn').addEventListener('click', ()=>{
    if (edits.length===0) return;
    edits.pop();
    solve('neu berechnen ...');
  });
  document.getElementById('resetBtn').addEventListener('click', ()=>{
    edits = [];
    solve('zuruecksetzen ...');
  });

  document.getElementById('exportBtn').addEventListener('click', async ()=>{
    const json = JSON.stringify(edits);
    const box = document.getElementById('exportBox');
    try {
      await navigator.clipboard.writeText(json);
      setStatus('Bearbeitungen in Zwischenablage kopiert');
    } catch (e) {
      box.style.display = 'block';
      box.value = json;
      box.select();
    }
  });

  document.getElementById('importBtn').addEventListener('click', ()=>{
    document.getElementById('exportBox').style.display = 'none';
    const box = document.getElementById('importBox');
    box.style.display = 'block';
    box.value = '';
    document.getElementById('importActions').style.display = 'flex';
    setStatus('');
    box.focus();
  });
  document.getElementById('importCancelBtn').addEventListener('click', ()=>{
    document.getElementById('importBox').style.display = 'none';
    document.getElementById('importActions').style.display = 'none';
    setStatus('');
  });
  document.getElementById('importApplyBtn').addEventListener('click', ()=>{
    const raw = document.getElementById('importBox').value.trim();
    if (!raw){ setStatus('Fehler: Feld ist leer'); return; }
    let parsed;
    try { parsed = JSON.parse(raw); }
    catch(e){ setStatus('Fehler: kein gueltiges JSON'); return; }
    const valid = validateEditsArray(parsed);
    if (!valid){ setStatus('Fehler: erwarte ein JSON-Array von Punkten'); return; }
    if (valid.length === 0){ setStatus('Fehler: keine gueltigen Punkte gefunden (index/value pruefen)'); return; }
    edits = valid;
    document.getElementById('importBox').style.display = 'none';
    document.getElementById('importActions').style.display = 'none';
    solve('optimiere ...', `${edits.length} Punkte importiert`);
  });

  document.getElementById('zoomInBtn').addEventListener('click', ()=>{
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    zoomAt(w/2, h/2, 1.4);
  });
  document.getElementById('zoomOutBtn').addEventListener('click', ()=>{
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    zoomAt(w/2, h/2, 1/1.4);
  });
  document.getElementById('zoomResetBtn').addEventListener('click', ()=>{
    view.zoom = 1; view.panX = 0; view.panY = 0;
    draw();
  });

  window.addEventListener('resize', resizeCanvas);
  resizeCanvas();
  solve('berechne Ausgangslinie in Python ...');
  if (document.fonts && document.fonts.ready){
    document.fonts.ready.then(()=>requestAnimationFrame(render));
  }

  // Read-only Debug-Zugriff (keine Seiteneffekte) - nuetzlich, um den
  // internen Zustand ohne Umwege ueber die Browser-Konsole zu pruefen.
  window.__editorDebug = {
    getPoints: () => lastPts,
    getEdits: () => edits,
    getNLat: () => Array.from(nLat),
    getSim: () => lastSim,
    getSteerRate: () => lastSim.steerRate,
    getCenterline: () => data.centerline,
    getPerp: () => data.perp,
    getLoHi: () => ({lo: LO, hi: HI}),
    project: (p) => project(p, trackCanvas.clientWidth, trackCanvas.clientHeight),
    clientRect: () => trackCanvas.getBoundingClientRect(),
    // Pro-Punkt-Tabelle fuer den Excel-Export (spreewaldring_simulierte_runde.xlsx)
    // - dieselbe Gas/Bremse/Lenkwinkel-Logik wie updateReadouts() (siehe dort),
    // nur fuer ALLE Punkte statt nur den aktuellen Zeitpunkt.
    getTelemetryTable: () => {
      const sim = lastSim, pts = lastPts;
      const wSign = Math.max(1, Math.round(P.CURVATURE_WINDOW_M/P.RESAMPLE_STEP_M));
      const rows = [];
      for (let i=0;i<N;i++){
        const isCoasting = !!sim.coasting[i];
        const braking = !isCoasting && sim.vFinal[i] < sim.vFwd[i]-1e-3;
        const brakePct = braking ? Math.round((sim.brakeFrac[i] ?? 1)*100) : 0;
        const throttlePct = sim.naturalMode
          ? (braking ? 0 : Math.round((sim.throttleFracNatural[i] ?? 0)*100))
          : Math.round(sim.pedalPct[i]);
        const p0 = pts[(i-wSign+N)%N], p1 = pts[i], p2 = pts[(i+wSign)%N];
        const cross = (p1[0]-p0[0])*(p2[1]-p0[1]) - (p2[0]-p0[0])*(p1[1]-p0[1]);
        const turnSign = cross > 0 ? -1 : (cross < 0 ? 1 : 0);
        const lenkwinkelDeg = Math.atan(WHEELBASE_M/Math.max(sim.radius[i],1)) * 180/Math.PI * turnSign;
        const aLatG = (sim.vFinal[i]**2 / Math.max(sim.radius[i],1e-6)) / P.G;
        rows.push({i, s: sim.s[i], vKmh: sim.vFinal[i]*3.6, gasPct: throttlePct, bremsePct: brakePct, lenkwinkelDeg, gang: sim.gear[i], aLatG});
      }
      return { lapTime: sim.lapTime, naturalMode: sim.naturalMode, rows };
    },
    tickStandingStart: (dtSeconds, steps) => {
      for (let k=0; k<(steps||1) && standingStart; k++) stepStandingStart(dtSeconds);
      return standingStart ? {...standingStart, active:true} : {active:false, t: sim_.t};
    },
  };
})();
</script>
"""


if __name__ == "__main__":
    main()
