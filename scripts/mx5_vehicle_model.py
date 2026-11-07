"""
MX-5 Fahrzeugmodell fuer die Streckensimulation, aus den eigenen Logs, 07.11.2026

Nutzerauftrag: Die Simulation auf der Strecke soll die aus den Logs berechneten Modelle nutzen. Dieses Skript
fasst zusammen, was Rundenzeit-Simulation, Ideallinien und Editor gemeinsam lesen
(results/mx5_vehicle_model.json), damit die Werte nicht doppelt gepflegt werden. Motor, Uebersetzungen, Traegheit,
Fahrwiderstaende und Traktionsgrenze kommen unveraendert aus drivetrain_model_validation.py/performance_simulation.py.

Neu bestimmt, je Reifensatz (NS-R2 bis candump-2026-10-01_142300, danach Hankook Winter):

- Quergrenze: Sensor LateralAcc_CAN, 0,3 s geglaettet, 1 s lang mindestens gehalten. Einzelspitzen (bis 1,15 g) sind
  keine Grenze, Gierrate*v ist in Rutschern hoeher als die echte Querbeschleunigung.
- Bremsgrenze NS-R2: BRAKE_CAP_G aus spreewaldring_racing_line_optimal.py (Nutzerentscheid 02.10., haerteste
  ABS-Bremsung >= 1,5 s), in den wahren Rahmen umgerechnet. Winter: staerkste 1,5 s gehaltene Verzoegerung aus dem
  Tempoabfall, ohne ABS-Eingriff also nur Untergrenze.
- Beschleunigen: Reifengrenze aus TRACTION_MAX_FORCE_N. Die gemessene Beschleunigungs-Huelle ist bis auf Gang 1 die
  Motorkraft, nicht der Reifen.
- Pruefung der Ellipse: Anteil der Punkte ausserhalb und Lage der TCS-Eingriffe (e nahe 1 = Reifen nahe am Limit).

Zugkraftunterbrechung je Gangpaar: p10 der gemessenen Luecken bei Schaltungen unter Last (obere Haelfte der
Vorschalt-Beschleunigung, shift_traction_gap_analysis.py). Das ist die eigene zuegige Schaltung, nicht die
Literatur-ATTACK-Zeit (0,15-0,25 s), die in den Logs nicht vorkommt.

Rahmen: alle Modelle rechnen in VehicleSpeed, der gegen GPS zu hoch liest (performance-model.md, Punkt 9). Die Strecke
ist in echten Metern, die Simulation rechnet deshalb v_wahr = speed_true_factor * VehicleSpeed. Die Parameter selbst
bleiben unveraendert, das ersetzt nicht den offenen Neufit.

Grenzen: alle Logs sind Alltagsfahrten. NS-R2-Grip ist eine Untergrenze (ein Phone-Ereignis mit ESP lag bei 1,0-1,1 g),
Winter liegt laut TCS nah am Limit. Temperatur, Reifendruck und Abnutzung sind nicht getrennt.

Aufruf: .venv/bin/python scripts/mx5_vehicle_model.py
"""
import json
import os

import duckdb
import numpy as np
import pandas as pd

from datalake_channels import log_key
from drivetrain_model_validation import G, MASS_KG
from performance_simulation import TRACTION_MAX_FORCE_N

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
OUT_PATH = os.path.join(RESULTS_DIR, "mx5_vehicle_model.json")
SHIFT_GAP_SUMMARY = os.path.join(RESULTS_DIR, "shift_traction_gap_summary.json")

LAST_NSR2_LOG = "candump-2026-10-01_142300"
SPEED_TRUE_FACTOR = {"NS-R2": 0.971, "Winter": 1 / 1.0286}  # GPS/VehicleSpeed: 70 dlg-Logs 09/2026; Handy-GPS 07.11.
BRAKE_CAP_NSR2_VS_G = 0.762  # BRAKE_CAP_G, aus dem Tempoabfall in VehicleSpeed bestimmt
DEFAULT_TYRE = "Winter"      # aktuell montiert

DT = 0.1
SMOOTH_S, LAT_HOLD_S, BRAKE_HOLD_S = 0.3, 1.0, 1.5
MIN_V_MS = 3.0
ELLIPSE_SMOOTH_S = 0.5
CHANNELS = ["VehicleSpeed", "LateralAcc_CAN", "LongitudinalAcc_CAN", "TCS_Active_CAN", "ABS_Active_CAN"]
LOAD_QUANTILE, GAP_QUANTILE = 0.5, 0.10


def tyre_of(log_id):
    return "NS-R2" if log_id <= LAST_NSR2_LOG else "Winter"


def load_log(con, log_id):
    df = con.execute(
        f"SELECT channel, t_elapsed_s t, value FROM measurements WHERE log_key=? AND log_id=? "
        f"AND channel IN ({','.join('?' * len(CHANNELS))}) AND value IS NOT NULL ORDER BY t",
        [log_key(log_id), log_id, *CHANNELS]).fetchdf()
    channels = {c: d for c, d in df.groupby("channel")}
    if not all(c in channels for c in CHANNELS[:3]) or len(channels["VehicleSpeed"]) < 100:
        return None
    t = np.arange(channels["VehicleSpeed"].t.min(), channels["VehicleSpeed"].t.max(), DT)
    out = {c: np.interp(t, d.t.values, d.value.values) if c in channels else None for c, d in channels.items()}
    for c in CHANNELS[3:]:
        out.setdefault(c, np.zeros(len(t)))
    v = out["VehicleSpeed"] / 3.6 * SPEED_TRUE_FACTOR[tyre_of(log_id)]
    smooth_v = pd.Series(v).rolling(5, center=True, min_periods=1).mean().values
    return pd.DataFrame({"t": t, "v": v, "ay": np.abs(out["LateralAcc_CAN"]), "ax": np.gradient(smooth_v, DT) / G,
                         "tcs": out["TCS_Active_CAN"], "abs": out["ABS_Active_CAN"]})


def held_max(a, v, hold_s):
    """Groesster Wert, der hold_s lang mindestens gehalten wurde (bei v > MIN_V_MS)."""
    smooth = pd.Series(a).rolling(int(round(SMOOTH_S / DT)), center=True).mean()
    held = smooth.rolling(int(round(hold_s / DT)), center=True).min().values
    held = np.where(v > MIN_V_MS, held, np.nan)
    if not np.isfinite(held).any():
        return 0.0, None
    return float(np.nanmax(held)), int(np.nanargmax(held))


def measure_grip(con, log_ids):
    tyres = {}
    for tyre in ("NS-R2", "Winter"):
        tyres[tyre] = {"lat_g": 0.0, "brake_g": 0.0, "points": [], "tcs": [], "abs_s": 0.0, "km": 0.0}
    for log_id in log_ids:
        d = load_log(con, log_id)
        if d is None:
            continue
        rec = tyres[tyre_of(log_id)]
        lat, i_lat = held_max(d.ay.values, d.v.values, LAT_HOLD_S)
        brake, i_brake = held_max(-d.ax.values, d.v.values, BRAKE_HOLD_S)
        if lat > rec["lat_g"]:
            rec.update(lat_g=lat, lat_event=[log_id, float(d.t[i_lat]), float(d.v[i_lat] * 3.6)])
        if brake > rec["brake_g"]:
            rec.update(brake_g=brake, brake_event=[log_id, float(d.t[i_brake]), float(d.v[i_brake] * 3.6)])
        win = int(round(ELLIPSE_SMOOTH_S / DT))
        ax = d.ax.rolling(win, center=True).mean().values
        ay = d.ay.rolling(win, center=True).mean().values
        keep = (d.v.values > 4.0) & np.isfinite(ax) & np.isfinite(ay)
        rec["points"].append(np.c_[ay[keep], ax[keep]])
        rec["tcs"].append(np.c_[ay[keep & (d.tcs.values > 0)], ax[keep & (d.tcs.values > 0)]])
        rec["abs_s"] += float((d["abs"].values > 0).sum() * DT)
        rec["km"] += float(d.v.sum() * DT / 1000)
    return tyres


def ellipse_value(ay, ax, lat_g, accel_g, brake_g):
    return (ay / lat_g) ** 2 + (np.where(ax < 0, ax / brake_g, ax / accel_g)) ** 2


def build_tyre_model(tyre, rec):
    accel_g = TRACTION_MAX_FORCE_N / (MASS_KG * G)
    factor = SPEED_TRUE_FACTOR[tyre]
    if tyre == "NS-R2":
        brake_g, brake_note = BRAKE_CAP_NSR2_VS_G * factor, "ABS-Grenze (BRAKE_CAP_G, wahrer Rahmen)"
    else:
        brake_g, brake_note = rec["brake_g"], "kein ABS-Eingriff in den Logs: Untergrenze"
    pts, tcs = np.vstack(rec["points"]), np.vstack(rec["tcs"])
    e_all = ellipse_value(pts[:, 0], pts[:, 1], rec["lat_g"], accel_g, brake_g)
    e_tcs = ellipse_value(tcs[:, 0], tcs[:, 1], rec["lat_g"], accel_g, brake_g)
    return {
        "lat_g": rec["lat_g"], "accel_g": accel_g, "brake_g": brake_g, "brake_note": brake_note,
        "speed_true_factor": factor, "lat_event": rec.get("lat_event"), "brake_event": rec.get("brake_event"),
        "km": rec["km"], "abs_s": rec["abs_s"], "tcs_samples": len(tcs),
        "ellipse_outside_pct": float((e_all > 1).mean() * 100),
        "ellipse_tcs_p50": float(np.median(e_tcs)), "ellipse_tcs_p90": float(np.percentile(e_tcs, 90)),
    }


def measure_shift_gaps():
    """Zugkraftluecke je Gangpaar bei Schaltungen unter Last, Raster der Erkennung 0,05 s."""
    with open(SHIFT_GAP_SUMMARY, encoding="utf-8") as f:
        r = pd.DataFrame(json.load(f)["all_results"])
    r = r[~r.no_gap_detected]
    out = {}
    for pair, d in r.groupby("gear_pair"):
        loaded = d[d.baseline_accel_kmh_s >= d.baseline_accel_kmh_s.quantile(LOAD_QUANTILE)]
        out[pair] = {"gap_s": float(loaded.drive_gap_duration_s.quantile(GAP_QUANTILE)),
                     "median_s": float(loaded.drive_gap_duration_s.median()), "n": int(len(loaded))}
    return out


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    log_ids = con.execute("SELECT log_id FROM logs WHERE source_format LIKE 'can%' AND duration_s >= 60 "
                          "ORDER BY start_time_local").fetchdf().log_id.tolist()
    grip = measure_grip(con, log_ids)
    con.close()
    model = {
        "default_tyre": DEFAULT_TYRE,
        "tyres": {tyre: build_tyre_model(tyre, rec) for tyre, rec in grip.items()},
        "shift_gaps": measure_shift_gaps(),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, ensure_ascii=False)
    for tyre, m in model["tyres"].items():
        print(f"{tyre}: quer {m['lat_g']:.3f} g, bremsen {m['brake_g']:.3f} g ({m['brake_note']}), beschl. "
              f"{m['accel_g']:.3f} g; {m['km']:.0f} km, ABS {m['abs_s']:.1f} s; Ellipse: {m['ellipse_outside_pct']:.3f} % "
              f"ausserhalb, TCS e p50 {m['ellipse_tcs_p50']:.2f}, p90 {m['ellipse_tcs_p90']:.2f} (n={m['tcs_samples']})")
    for pair, s in model["shift_gaps"].items():
        print(f"Schaltung {pair}: {s['gap_s']:.2f} s (Median unter Last {s['median_s']:.2f} s, n={s['n']})")
    print(f"Geschrieben: {OUT_PATH}")


if __name__ == "__main__":
    main()
