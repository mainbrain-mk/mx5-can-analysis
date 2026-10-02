"""
Bremsereignisse aus einem CAN-Log (02.10.2026) - CAN-natives Gegenstueck zu brake_event_analysis.py.

brake_event_analysis.py arbeitet auf der Handy-.dlg und muss erst die Einbaulage des Handys
herausrechnen (Achsenrotation, theta/R), bevor die Verzoegerung brauchbar ist. Im CAN-Log
liegen alle Groessen fahrzeugfest und direkt vor: Bremsdruck (BrakePressure_CAN, bar, Skala
29.09. nachkalibriert), Laengsbeschleunigung (LongitudinalAcc_CAN, Airbag-Steuergeraet, 100 Hz,
+ = vorwaerts) und der ABS-Eingriff (ABS_Active_CAN, 0x211 Bit 42).

Ereignis: Bremsdruck > BRAKE_PRESSURE_MIN_BAR (wie bisher 300 kPa), Luecken bis MAX_GAP_S
ueberbrueckt, mindestens MIN_DURATION_S lang und mit Geschwindigkeitsabfall >= MIN_SPEED_DROP_KMH
(sonst "Fuss auf der Bremse im Stand", vgl. braking_model.py).

Aufruf: python scripts/can_brake_event_analysis.py <can_log_id>   (ohne Argument: Selbsttest)
"""
import json
import os
import sys

import duckdb
import numpy as np

from datalake_channels import load_channel

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
G = 9.81

BRAKE_PRESSURE_MIN_BAR = 3.0
MAX_GAP_S = 0.3
MIN_DURATION_S = 0.5
MIN_SPEED_DROP_KMH = 3.0


def detect_events(t, p_bar, v_kmh, ax_g, abs_on):
    """t/p_bar: Bremsdruck-Zeitachse; v_kmh, ax_g, abs_on bereits auf t interpoliert."""
    idx = np.flatnonzero(p_bar > BRAKE_PRESSURE_MIN_BAR)
    if not len(idx):
        return []
    splits = np.flatnonzero(np.diff(t[idx]) > MAX_GAP_S) + 1
    events = []
    for run in np.split(idx, splits):
        s, e = run[0], run[-1]
        dur = t[e] - t[s]
        drop = v_kmh[s] - v_kmh[e]
        if dur < MIN_DURATION_S or drop < MIN_SPEED_DROP_KMH:
            continue
        seg = slice(s, e + 1)
        events.append({
            "t_start": float(t[s]), "t_end": float(t[e]), "duration_s": float(dur),
            "pressure_max_bar": float(p_bar[seg].max()),
            "speed_start_kmh": float(v_kmh[s]), "speed_end_kmh": float(v_kmh[e]),
            "decel_mean_g": float(-ax_g[seg].mean()),
            "decel_peak_g": float(-ax_g[seg].min()),
            "decel_from_speed_g": float(drop / 3.6 / dur / G),
            "abs_active": bool(abs_on[seg].max() > 0.5),
        })
    return events


def main():
    log_id = sys.argv[1]
    con = duckdb.connect(DB_PATH, read_only=True)
    p = load_channel(con, log_id, "BrakePressure_CAN")
    speed = load_channel(con, log_id, "VehicleSpeed")
    ax = load_channel(con, log_id, "LongitudinalAcc_CAN")
    abs_ = load_channel(con, log_id, "ABS_Active_CAN")
    if p.empty or speed.empty or ax.empty:
        raise SystemExit(f"Bremsdruck/Speed/Laengsbeschleunigung fehlt fuer log_id={log_id!r}")

    t = p["t"].to_numpy()
    interp = lambda df: np.interp(t, df["t"].to_numpy(), df["value"].to_numpy())  # noqa: E731
    abs_on = interp(abs_) if len(abs_) else np.zeros_like(t)
    events = detect_events(t, p["value"].to_numpy(), interp(speed), interp(ax), abs_on)

    print(f"{log_id}: {len(events)} Bremsereignisse (> {BRAKE_PRESSURE_MIN_BAR} bar, "
          f"Abfall >= {MIN_SPEED_DROP_KMH} km/h)")
    for i, ev in enumerate(events, 1):
        print(f"{i:2d}  t={ev['t_start']:7.1f}-{ev['t_end']:7.1f}s  p_max={ev['pressure_max_bar']:5.1f}bar  "
              f"v={ev['speed_start_kmh']:5.0f}->{ev['speed_end_kmh']:5.0f}km/h  "
              f"a={ev['decel_mean_g']:.2f}g (peak {ev['decel_peak_g']:.2f}g)"
              f"{'  ABS' if ev['abs_active'] else ''}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"can_brake_event_summary_{log_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"log_id": log_id, "threshold_bar": BRAKE_PRESSURE_MIN_BAR, "events": events}, f,
                  indent=2, ensure_ascii=False)
    print(f"\nDetails: {out_path}")


def _selftest():
    t = np.arange(0, 3, 0.1)
    p = np.where((t > 1) & (t < 2), 20.0, 0.0)
    v = 100 - np.clip(t - 1, 0, 1) * 30
    ev = detect_events(t, p, v, np.full_like(t, -0.8), np.zeros_like(t))
    assert len(ev) == 1 and abs(ev[0]["decel_mean_g"] - 0.8) < 1e-9 and not ev[0]["abs_active"], ev
    assert detect_events(t, p, np.zeros_like(t), np.full_like(t, -0.8), np.zeros_like(t)) == []  # Stand
    print("can_brake_event_analysis: Selbsttest OK")


if __name__ == "__main__":
    _selftest() if len(sys.argv) < 2 else main()
