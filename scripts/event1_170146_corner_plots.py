"""
Detailplots fuer Event 1 (S-Kurve, `2026-09-09 170146`, t~2000.6-2010.6s),
im selben Stil wie der Nutzer-Chart vom 08.09.2026 ("Rechtskurve 1
17:33:24-17:33:31"): Gas / Bremse / g-Kraft (Gyro- vs. Lenkwinkelmodell,
plus a_lon) / Lenkwinkel, mit Schattierung des jeweiligen Kurvenfensters.

Kontext: siehe docs/logs/projekt-stand.md "Auffaellig 2" (Korrektur) und
[[mx5_steering_angle]] - dies ist die Rechts- und die Linkskurve der auf
einer Autobahn-Verbindungsrampe (OSM trunk_link) gefahrenen S-Kurve.

Aufruf: .venv/bin/python scripts/event1_170146_corner_plots.py
"""
import json
from datetime import datetime, timedelta

import duckdb
import numpy as np
from scipy import signal
from scipy.interpolate import interp1d
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
LOG_ID = "2026-09-09 170146"
LOG_START = datetime(2026, 9, 9, 17, 1, 46)

GYRO_SIGN_SCALE = -1.0
LOWPASS_CUTOFF_HZ = 6.0
FS_UNIFORM = 50.0
G = 9.81

PHASES = [
    ("Rechtskurve", 2000.6, 2005.8, 1997.5, 2008.5),
    ("Linkskurve", 2005.8, 2010.6, 2003.0, 2013.0),
]


def load(con, channel, lo, hi):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id=? AND channel=? "
        "AND value IS NOT NULL AND t_elapsed_s BETWEEN ? AND ? ORDER BY t_elapsed_s",
        [LOG_ID, channel, lo, hi],
    ).fetchdf()


def wall_time(t_elapsed):
    return (LOG_START + timedelta(seconds=t_elapsed)).strftime("%H:%M:%S")


def main():
    with open(f"{RESULTS_DIR}/steering_lateral_model_summary.json", encoding="utf-8") as f:
        k1, k2 = json.load(f)["k"]
    with open(f"{RESULTS_DIR}/steering_zero_offset.json", encoding="utf-8") as f:
        offset = json.load(f)[LOG_ID]["offset_deg_median"]

    con = duckdb.connect(DB_PATH, read_only=True)
    lo_all, hi_all = 1990.0, 2018.0
    rot = load(con, "RotationRateZ", lo_all, hi_all)
    speed = load(con, "VehicleSpeed", lo_all, hi_all)
    steer = load(con, "STEER_ANGL_EPS", lo_all, hi_all)
    app = load(con, "APP", lo_all, hi_all)
    brake = load(con, "BFP_PRE_MZ", lo_all, hi_all)
    con.close()

    f_rot = interp1d(rot["t"].values, rot["value"].values, kind="linear", bounds_error=False,
                      fill_value=(rot["value"].values[0], rot["value"].values[-1]))
    t_u = np.arange(rot["t"].values[0], rot["t"].values[-1], 1 / FS_UNIFORM)
    b, a = signal.butter(4, LOWPASS_CUTOFF_HZ / (FS_UNIFORM / 2), btype="low")
    gyro_lp = signal.filtfilt(b, a, f_rot(t_u))
    omega_deg_s = GYRO_SIGN_SCALE * gyro_lp

    v_u = np.interp(t_u, speed["t"].values, speed["value"].values) / 3.6
    a_lat_kin_g = v_u * np.radians(omega_deg_s) / G
    a_lon_g = np.gradient(v_u, t_u) / G

    steer_u = np.interp(t_u, steer["t"].values, steer["value"].values) - offset
    yaw_model = (k1 + k2 * np.abs(steer_u)) * (-steer_u) * v_u
    a_lat_model_g = v_u * np.radians(yaw_model) / G

    app_u = np.interp(t_u, app["t"].values, app["value"].values)
    brake_u = np.interp(t_u, brake["t"].values, brake["value"].values)

    for label, t_lo, t_hi, plot_lo, plot_hi in PHASES:
        m = (t_u >= plot_lo) & (t_u <= plot_hi)
        t_plot = t_u[m]

        fig, axes = plt.subplots(4, 1, figsize=(11, 12), sharex=True)
        title = (f"{label} (Event 1, S-Kurve auf Autobahnrampe) - Gas / Bremse / "
                 f"g-Kraft (Gyro vs Lenkwinkelmodell) / Lenkwinkel\n"
                 f"{LOG_ID}, {wall_time(t_lo)}-{wall_time(t_hi)}")

        ax = axes[0]
        ax.plot(t_plot, app_u[m], "o-", color="green", label="Gas (APP) %")
        ax.set_ylabel("Gaspedal %")
        ax.set_title(title)
        ax.legend()
        ax.grid(alpha=0.3)

        ax = axes[1]
        ax.plot(t_plot, brake_u[m], "o-", color="red", label="Bremse (BFP_PRE_MZ) kPa")
        ax.set_ylabel("Bremsdruck kPa")
        ax.legend()
        ax.grid(alpha=0.3)

        ax = axes[2]
        ax.plot(t_plot, a_lat_kin_g[m], color="tab:blue",
                 label="a_lat Gyroskop (RotationRateZ, tiefpassgefiltert, v*omega)")
        ax.plot(t_plot, a_lat_model_g[m], color="tab:orange", label="a_lat (Lenkwinkelmodell) (v*k(w)*Lenkwinkel)")
        ax.plot(t_plot, a_lon_g[m], color="tab:purple", label="a_lon (Laengs, dv/dt)")
        ax.axhline(0, color="grey", lw=0.6)
        ax.set_ylabel("g")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)

        ax = axes[3]
        ax.plot(t_plot, steer_u[m], "o-", color="brown", label=f"Lenkwinkel korrigiert (Offset +{offset:.1f} deg)")
        ax.axhline(0, color="grey", lw=0.6)
        ax.set_ylabel("Lenkwinkel [deg]\n(neg. = rechts)")
        ax.set_xlabel("t seit Logstart [s]")
        ax.legend()
        ax.grid(alpha=0.3)

        for ax in axes:
            ax.axvspan(t_lo, t_hi, color="orange", alpha=0.15)

        fig.tight_layout()
        out_path = f"{RESULTS_DIR}/event1_170146_{label.lower()}_trace.png"
        fig.savefig(out_path, dpi=120)
        print(f"gespeichert: {out_path}")


if __name__ == "__main__":
    main()
