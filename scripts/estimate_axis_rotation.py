"""
Achsenrotation aus vorhandenen Fahrten schaetzen (MX-5 Projekt)

Zweck: ohne dedizierte Kalibrierfahrt (siehe calibrate_axes.py) grob
schaetzen, wie die IMU X/Z-Achsen in der Horizontalebene gegenueber der
Fahrzeug-Laengsachse verdreht sind. Hintergrund (Nutzerangabe zur
Halterungsgeometrie): Saugnapf -> Arm -> Kugelkopf mit Rendelschraube ->
Schale (leicht Richtung Fahrer gedreht) -> Handy steht hochkant in der
Schale. Die Handy-Y-Achse zeigt damit nahezu senkrecht nach oben/unten,
X und Z liegen naeherungsweise in der Horizontalebene, aber durch die
Drehung der Schale nicht parallel zur Fahrzeuglaengsachse.

Nutzt die bereits durch vibration_analysis.py erzeugten
*_imu_filtered.csv (lowpass-gefilterte X/Z, Resonanz+Ausreisser schon
entfernt) und die rohe OBD-Geschwindigkeit als grobe, unabhaengige
Referenz fuer die tatsaechliche Laengsbeschleunigung.

Methodik:
  1. a_obd(t) = dv/dt aus VehicleSpeed (~1-2Hz), auf das 50Hz-Raster der
     gefilterten IMU-Daten interpoliert. Nur eine grobe Referenz (wie
     schon in vibration_analysis.py fuer die Kreuzvalidierung genutzt).
  2. Nur Zeitpunkte mit |a_obd| > A_OBD_MIN_MS2 werden fuer die
     Regression verwendet (klare Brems-/Beschleunigungsphasen -> gutes
     SNR, Rauschen bei Konstantfahrt faellt raus).
  3. Lineare Regression ohne Achsenabschnitt: a_obd ~ b1*X + b2*Z
     (X, Z = lowpass-gefiltertes Signal). Der Winkel der gefundenen
     Richtung (b1,b2) in der X-Z-Ebene ist die gesuchte Rotation der
     Fahrzeug-Laengsachse relativ zu den IMU-Achsen X und Z. Das
     Vorzeichen wird durch die Regression gegen das VORZEICHENBEHAFTETE
     a_obd automatisch mitbestimmt (Bremsen = negativ, Beschleunigen =
     positiv).
  4. a_long = X*cos(theta) + Z*sin(theta), a_lat = -X*sin(theta) + Z*cos(theta)
  5. Guete: R^2 zwischen a_long und a_obd (nur auf den Regressionspunkten),
     zum Vergleich auch R^2 der unrotierten Rohachsen X und Z allein.

WICHTIG (Nutzerhinweis): die Halterungsausrichtung ist moeglicherweise
nicht in allen Logs identisch (Kugelkopf mit Rendelschraube kann sich
zwischen Fahrten leicht verstellt haben) - daher wird theta PRO LOG
separat geschaetzt, nicht global gemittelt. Die Ergebnistabelle zeigt,
ob die Werte ueber die Logs stabil sind oder streuen.

Aufruf: python estimate_axis_rotation.py
(verarbeitet automatisch alle *_imu_filtered.csv im aktuellen Ordner,
 die eine passende .dlg-Datei haben)
"""
import glob
import os
import json
import sqlite3
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

TICKS_OFFSET = 621355968000000000  # .NET-Ticks -> Unix-Referenz
A_OBD_MIN_MS2 = 1.0  # nur Phasen mit klarer OBD-Beschleunigung/-Bremsung nutzen
MIN_SAMPLES = 20      # zu wenige Punkte -> Log als nicht auswertbar melden


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


def accel_t0(db_file):
    """Gleiche Referenzzeit wie in vibration_analysis.py (t0 = fruehester
    Zeitstempel ueber alle 3 Beschleunigungsachsen), damit die 't'-Spalte
    der *_imu_filtered.csv exakt passt."""
    conn = sqlite3.connect(db_file)
    accel = load_channel(conn, ["AccelerationX", "AccelerationY", "AccelerationZ"])
    conn.close()
    return accel["datetime"].min()


def obd_accel_on_grid(db_file, t0, t_grid):
    """Grobe dv/dt-Referenz aus VehicleSpeed, linear auf t_grid interpoliert."""
    conn = sqlite3.connect(db_file)
    speed = load_channel(conn, ["VehicleSpeed"])
    conn.close()
    speed["t"] = (speed["datetime"] - t0).dt.total_seconds()
    sub = speed[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    t = sub["t"].values
    v_ms = sub["value"].values / 3.6
    if len(t) < 3:
        return np.zeros_like(t_grid)
    a = np.gradient(v_ms, t)
    f = interp1d(t, a, kind="linear", bounds_error=False, fill_value=(a[0], a[-1]))
    return f(t_grid)


def estimate_rotation(csv_path, db_file):
    df = pd.read_csv(csv_path)
    t = df["t"].values
    x = df["AccelerationX_lowpass"].values
    z = df["AccelerationZ_lowpass"].values

    t0 = accel_t0(db_file)
    a_obd = obd_accel_on_grid(db_file, t0, t)

    mask = np.abs(a_obd) > A_OBD_MIN_MS2
    n_used = int(mask.sum())
    if n_used < MIN_SAMPLES:
        return {"file": db_file, "error": "zu wenige klare Brems-/Beschleunigungsphasen",
                "n_used": n_used, "n_total": len(t)}

    A = np.column_stack([x[mask], z[mask]])
    (b1, b2), *_ = np.linalg.lstsq(A, a_obd[mask], rcond=None)
    theta = float(np.degrees(np.arctan2(b2, b1)))
    th = np.radians(theta)

    a_long = x * np.cos(th) + z * np.sin(th)

    ss_tot = np.sum((a_obd[mask] - a_obd[mask].mean()) ** 2)
    if ss_tot <= 0:
        return {"file": db_file, "error": "OBD-Referenz ohne Varianz im Auswahlfenster",
                "n_used": n_used, "n_total": len(t)}

    r2_rot = 1 - np.sum((a_obd[mask] - a_long[mask]) ** 2) / ss_tot
    r2_x = 1 - np.sum((a_obd[mask] - x[mask]) ** 2) / ss_tot
    r2_z = 1 - np.sum((a_obd[mask] - z[mask]) ** 2) / ss_tot

    return {
        "file": db_file,
        "theta_deg": theta,
        "n_used": n_used,
        "n_total": len(t),
        "r2_rotated": float(r2_rot),
        "r2_raw_x": float(r2_x),
        "r2_raw_z": float(r2_z),
    }


RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"
RESULTS_DIR = "results"


def main():
    results = []
    for csv_path in sorted(glob.glob(f"{DERIVED_DIR}/*_imu_filtered.csv")):
        db_file = os.path.join(RAW_DIR, os.path.basename(csv_path).replace("_imu_filtered.csv", ".dlg"))
        try:
            res = estimate_rotation(csv_path, db_file)
        except Exception as e:
            res = {"file": db_file, "error": str(e)}
        results.append(res)
        if "error" in res:
            print(f"{db_file}: uebersprungen - {res['error']} (n_used={res.get('n_used', '?')})")
        else:
            print(f"{db_file}: theta={res['theta_deg']:7.1f} deg | "
                  f"R^2 rotiert={res['r2_rotated']:.3f} (roh X={res['r2_raw_x']:.3f}, "
                  f"roh Z={res['r2_raw_z']:.3f}) | n={res['n_used']}/{res['n_total']}")

    thetas = [r["theta_deg"] for r in results if "theta_deg" in r]
    if thetas:
        print()
        print(f"theta ueber {len(thetas)} Logs: Mittelwert={np.mean(thetas):.1f} deg, "
              f"Streuung (Std)={np.std(thetas):.1f} deg, Spanne=[{min(thetas):.1f}, {max(thetas):.1f}] deg")

    with open(os.path.join(RESULTS_DIR, "axis_rotation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nDetails: results/axis_rotation_summary.json")


if __name__ == "__main__":
    main()
