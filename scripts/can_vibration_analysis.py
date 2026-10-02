"""
Schwingungen aus einem CAN-Log (02.10.2026) - CAN-natives Gegenstueck zu vibration_analysis.py.

Befund 28.09. (docs/logs/projekt-stand.md "CAN-IMU statt Handy-IMU?"): die dominierende Spitze in
Laengs- und Querbeschleunigung ist die Raddrehfrequenz (1. Ordnung, Verhaeltnis 0,999), keine
Struktur-Eigenmode - das feste 18-23-Hz-Band der Handy-Auswertung misst eine geschwindigkeits-
abhaengige Rad-Unwucht/Ungleichfoermigkeit. Hier deshalb ordnungsbezogen:

  Raddrehfrequenz f_rad = VehicleSpeed / (2*pi*R_DYN_M). VehicleSpeed rechnet das Steuergeraet mit
  festem Umfang aus der Raddrehzahl; R_DYN_M (0,2985 m) ist genau dieser Bezugsradius (= 0,290 m
  echter Radius * 1/0,971 Tacho-Voreilung) - gilt damit unabhaengig vom Reifensatz.

Je 4-s-Fenster konstanter Fahrt (> MIN_SPEED_KMH, Schwankung < MAX_SPEED_SPREAD_KMH, ohne
Datenluecke): Welch-PSD von LateralAcc_CAN/LongitudinalAcc_CAN (Airbag-Steuergeraet, 100 Hz),
Spitzenfrequenz im Band 4-30 Hz und die Amplitude (RMS, mg) im Band f_rad +-0,5 Hz.

Aufruf: python scripts/can_vibration_analysis.py <can_log_id>   (ohne Argument: Selbsttest)
"""
import json
import os
import sys

import duckdb
import numpy as np
from scipy.signal import welch

from datalake_channels import load_channel
from drivetrain_model_validation import R_DYN_M

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
FS_HZ = 100.0
WINDOW_S = 4.0
MIN_SPEED_KMH = 40.0
MAX_SPEED_SPREAD_KMH = 3.0
BAND_HZ = (4.0, 30.0)
ORDER_HALF_WIDTH_HZ = 0.5
ON_ORDER_TOLERANCE = 0.03
SPEED_BANDS_KMH = [(40, 70), (70, 100), (100, 130), (130, 200)]
AXES = {"quer": "LateralAcc_CAN", "laengs": "LongitudinalAcc_CAN"}


def wheel_hz(v_kmh):
    return v_kmh / 3.6 / (2 * np.pi * R_DYN_M)


def analyze_window(acc_g, v_kmh):
    """(Spitzenfrequenz Hz, RMS in mg um die Raddrehfrequenz) fuer ein gleichmaessig abgetastetes Fenster."""
    f, psd = welch(acc_g - acc_g.mean(), fs=FS_HZ, nperseg=len(acc_g) // 2)
    band = (f >= BAND_HZ[0]) & (f <= BAND_HZ[1])
    peak = float(f[band][np.argmax(psd[band])])
    fw = wheel_hz(v_kmh)
    order = np.abs(f - fw) <= ORDER_HALF_WIDTH_HZ
    rms_mg = float(np.sqrt(np.sum(psd[order]) * (f[1] - f[0]))) * 1000
    return peak, rms_mg


def analyze(series, speed):
    """series: {Achse: (t, Wert in g)}, speed: (t, km/h). Liefert Fenster-Ergebnisse je Achse."""
    n = int(WINDOW_S * FS_HZ)
    t_end = min(s[0][-1] for s in series.values())
    windows = []
    for t0 in np.arange(max(s[0][0] for s in series.values()), t_end - WINDOW_S, WINDOW_S):
        grid = t0 + np.arange(n) / FS_HZ
        v = np.interp(grid, *speed)
        if v.min() < MIN_SPEED_KMH or v.max() - v.min() > MAX_SPEED_SPREAD_KMH:
            continue
        row = {"t_start": float(t0), "speed_kmh": float(v.mean()), "wheel_hz": float(wheel_hz(v.mean()))}
        for axis, (t, a) in series.items():
            inside = (t >= t0) & (t < t0 + WINDOW_S)
            if inside.sum() < 0.9 * n:  # Datenluecke (z.B. 1,6-s-Aussetzer 27.09.)
                break
            peak, rms = analyze_window(np.interp(grid, t, a), v.mean())
            row[axis] = {"peak_hz": peak, "order_rms_mg": rms}
        else:
            windows.append(row)
    return windows


def summarize(windows):
    out = {"n_windows": len(windows)}
    for axis in AXES:
        ratios = np.array([w[axis]["peak_hz"] / w["wheel_hz"] for w in windows])
        bands = {}
        for lo, hi in SPEED_BANDS_KMH:
            rms = [w[axis]["order_rms_mg"] for w in windows if lo <= w["speed_kmh"] < hi]
            if rms:
                bands[f"{lo}-{hi}"] = {"n": len(rms), "order_rms_mg_median": float(np.median(rms))}
        out[axis] = {
            "peak_to_wheel_ratio_median": float(np.median(ratios)) if len(ratios) else None,
            "frac_peak_on_wheel_order": float(np.mean(np.abs(ratios - 1) <= ON_ORDER_TOLERANCE))
            if len(ratios) else None,
            "order_rms_by_speed_kmh": bands,
        }
    return out


def main(log_id):
    con = duckdb.connect(DB_PATH, read_only=True)
    speed = load_channel(con, log_id, "VehicleSpeed")
    series = {}
    for axis, ch in AXES.items():
        df = load_channel(con, log_id, ch)
        if df.empty:
            raise SystemExit(f"{ch} fehlt fuer log_id={log_id!r}")
        series[axis] = (df["t"].to_numpy(), df["value"].to_numpy())
    windows = analyze(series, (speed["t"].to_numpy(), speed["value"].to_numpy()))
    summary = {"log_id": log_id, **summarize(windows)}

    print(f"{log_id}: {summary['n_windows']} Fenster konstanter Fahrt")
    for axis in AXES:
        s = summary[axis]
        if s["peak_to_wheel_ratio_median"] is None:
            continue
        bands = ", ".join(f"{k} km/h {b['order_rms_mg_median']:.1f} mg"
                          for k, b in s["order_rms_by_speed_kmh"].items())
        print(f"  {axis}: Spitze/Raddrehfrequenz Median {s['peak_to_wheel_ratio_median']:.3f}, "
              f"{s['frac_peak_on_wheel_order']:.0%} der Fenster auf der Radordnung; Amplitude {bands}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"can_vibration_summary_{log_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {out_path}")


def _selftest():
    t = np.arange(0, 60, 1 / FS_HZ)
    v_kmh = 110.0
    sig = 0.02 * np.sin(2 * np.pi * wheel_hz(v_kmh) * t)  # 20 mg Amplitude -> 14,1 mg RMS
    w = analyze({a: (t, sig) for a in AXES}, (t, np.full_like(t, v_kmh)))
    s = summarize(w)
    assert s["n_windows"] == 14, s
    assert abs(s["quer"]["peak_to_wheel_ratio_median"] - 1) < 0.04, s
    assert abs(s["quer"]["order_rms_by_speed_kmh"]["100-130"]["order_rms_mg_median"] - 14.1) < 1.5, s
    assert analyze({a: (t, sig) for a in AXES}, (t, np.full_like(t, 30.0))) == []  # zu langsam
    print("can_vibration_analysis: Selbsttest OK")


if __name__ == "__main__":
    _selftest() if len(sys.argv) < 2 else main(sys.argv[1])
