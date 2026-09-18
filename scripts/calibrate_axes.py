"""
Achsenkalibrierung fuer die IMU-Halterung (MX-5 Projekt)

Zweck: aus einer gezielten Kalibrierfahrt (Geradeausbeschleunigung +
Vollbremsung, je eine Links- und Rechtskurve, mit Pausen dazwischen)
per Sichtpruefung ermitteln, welche IMU-Achse (X/Y/Z) im Fahrzeug
laengs, quer und vertikal ist. Plottet AccelerationX/Y/Z + VehicleSpeed
ueber die Zeit, damit sich bekannte Fahrmanoever den Achsen zuordnen
lassen (z.B. "Bremsung -> welche Achse macht den grossen negativen
Ausschlag, waehrend VehicleSpeed gleichzeitig faellt?").

Aufruf: python calibrate_axes.py "<kalibrierfahrt>.dlg"
Ausgabe: <dlg-name>_axis_calibration.png
"""
import sys
import os
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d

TICKS_OFFSET = 621355968000000000  # .NET-Ticks -> Unix-Referenz
FS_UNIFORM = 50.0
STARTUP_SKIP_S = 1.5  # bekanntes Sensor-Fusion-Einschwingartefakt am Log-Anfang
                       # (siehe docs/logs/projekt-stand.md) - sonst taeuscht der
                       # Einschwinger eine grosse Beschleunigung vor


def load_channel(conn, pid_names):
    placeholders = ",".join("?" for _ in pid_names)
    q = f"""
        SELECT pde.Time AS raw_time, pme.PidName AS sensor_name, pde.Value AS value
        FROM PidDataEntry pde
        LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
        WHERE pme.PidName IN ({placeholders})
        ORDER BY pde.Time ASC
    """
    df = pd.read_sql_query(q, conn, params=pid_names)
    df["datetime"] = pd.to_datetime((df["raw_time"] - TICKS_OFFSET) / 10, unit="us")
    return df


def resample_uniform(df_channel, t_uniform, kind="linear"):
    sub = df_channel[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    f = interp1d(sub["t"].values, sub["value"].values, kind=kind,
                 bounds_error=False, fill_value=(sub["value"].values[0], sub["value"].values[-1]))
    return f(t_uniform)


RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"


def main():
    if len(sys.argv) < 2:
        print('Aufruf: python calibrate_axes.py "<kalibrierfahrt>.dlg"')
        sys.exit(1)
    db_file = os.path.basename(sys.argv[1])

    conn = sqlite3.connect(os.path.join(RAW_DIR, db_file))
    accel = load_channel(conn, ["AccelerationX", "AccelerationY", "AccelerationZ"])
    speed = load_channel(conn, ["VehicleSpeed"])
    conn.close()

    t0 = accel["datetime"].min()
    accel["t"] = (accel["datetime"] - t0).dt.total_seconds()
    speed["t"] = (speed["datetime"] - t0).dt.total_seconds()

    axes = {name: accel[accel.sensor_name == name][["t", "value"]] for name in
            ["AccelerationX", "AccelerationY", "AccelerationZ"]}
    t_min = max(a["t"].min() for a in axes.values())
    t_max = min(a["t"].max() for a in axes.values())
    t_uniform = np.arange(t_min, t_max, 1 / FS_UNIFORM)

    sig = {name: resample_uniform(df, t_uniform) for name, df in axes.items()}
    skip_mask = t_uniform >= (t_uniform[0] + STARTUP_SKIP_S)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    ax1.plot(t_uniform[skip_mask], sig["AccelerationX"][skip_mask], label="X", lw=0.8)
    ax1.plot(t_uniform[skip_mask], sig["AccelerationY"][skip_mask], label="Y", lw=0.8)
    ax1.plot(t_uniform[skip_mask], sig["AccelerationZ"][skip_mask], label="Z", lw=0.8)
    ax1.axhline(0, color="gray", lw=0.5)
    ax1.set_ylabel("Beschleunigung [m/s²]")
    ax1.legend()
    ax1.set_title(f"Achsenkalibrierung: {db_file}")

    speed_sub = speed[["t", "value"]].dropna().sort_values("t")
    ax2.plot(speed_sub["t"], speed_sub["value"], color="black", lw=1.0)
    ax2.set_ylabel("VehicleSpeed [km/h]")
    ax2.set_xlabel("Zeit [s]")
    ax2.axhline(0, color="gray", lw=0.5)

    plt.tight_layout()
    out_path = os.path.join(DERIVED_DIR, db_file.replace(".dlg", "_axis_calibration.png"))
    plt.savefig(out_path, dpi=130)
    print(f"Plot gespeichert: {out_path}")
    print("Bitte mit den waehrend der Fahrt gemerkten Manoever-Reihenfolge")
    print("(Bremsung, Rechtskurve, Linkskurve) abgleichen, um X/Y/Z den")
    print("Fahrzeugachsen (laengs/quer/vertikal) zuzuordnen.")


if __name__ == "__main__":
    main()
