"""
Quergrip-/Laengsgrip-Schaetzung aus kalibrierten IMU-Daten (MX-5 Projekt)

Zweck: fuer jeden Log die Quer- und Laengsbeschleunigung im
Fahrzeug-Koordinatensystem berechnen und daraus eine grobe Grip-Nutzung
("wie nah am Reifenlimit") ableiten.

Voraussetzungen (siehe PROJEKT_STAND.md, Punkt 4 UND Nachtrag 06.09.2026):
  - Vertikalachse wird PRO LOG per `imu_orientation.detect_vertical_axis()`
    erkannt (bis inkl. 2026-09-05 war das immer Y bei fest verbauter
    Halterung; seit 06.09.2026 ist die Halterung variabel, z.B. Z bei
    liegendem Handy - siehe imu_orientation.py Docstring).
  - Fahrzeug-Vorwaertsrichtung liegt in der jeweils erkannten
    Horizontalebene (die zwei Nicht-Vertikal-Achsen) bei einem Winkel
    theta, der PRO LOG unterschiedlich ist (Halterung nicht 100% stabil
    zwischen Fahrten) - kommt aus `brake_event_summary.json`
    (`forward_direction_deg`, erzeugt von brake_event_analysis.py, das
    dieselbe Achsenerkennung nutzt).

METHODIK-WECHSEL (29.08.2026) - WICHTIG:
  Urspruenglich wurde a_lat wie a_long per Rotation aus X/Z gebildet
  (a_lat = -X*sin(theta) + Z*cos(theta)). Der Wunsch, das Links/Rechts-
  Vorzeichen zu klaeren, deckte ein tieferes Problem auf: a_lat aus
  dieser Rotation korreliert bei KEINEM der 7 Logs mit echter
  Kurvenfahrt (getestet gegen zwei unabhaengige Referenzen: GPS-Kurs-
  aenderung und Gyroskop RotationRateY, Korrelationen praktisch bei 0,
  z.T. sogar leicht negativ - siehe PROJEKT_STAND.md). Arbeitshypothese:
  die Halterung (Kugelkopf mit Rendelschraube) reagiert unter Seitenlast
  (Kurve) anders als unter Laengslast (Bremsen) - das aus Bremsereignissen
  gewonnene theta beschreibt also nur die Laengsachse zuverlaessig, NICHT
  die dazu orthogonale Richtung in der X-Z-Ebene.
  a_long bleibt von diesem Problem unberuehrt (weiterhin ueber viele
  unabhaengige Bremsereignisse validiert, siehe brake_event_analysis.py).

  LOESUNG: a_lat wird jetzt NICHT mehr aus X/Z berechnet, sondern
  KINEMATISCH aus Geschwindigkeit x Gierrate:
      a_lat = v * omega
  (klassische Naeherung fuer eine "coordinated turn", vernachlaessigt
  Schwimmwinkel/Drift - fuer normales bis moderat sportliches Fahren
  eine uebliche Approximation).
  - v = VehicleSpeed (OBD, km/h -> m/s)
  - omega = kalibrierte Gierrate aus RotationRateY (Y=vertikal):
    omega[deg/s] = -1.0 * RotationRateY_lowpass
    Das Vorzeichen (-1.0) UND die Groessenordnung wurden ueber alle 7
    Logs gegen die GPS-Kursaenderung geprueft: Korrelation konsistent
    stark negativ (-0.54 bis -0.90), Regressionssteigung -0.44 bis -0.83
    (gewichtetes Mittel -0.74). Das Vorzeichen ist damit gut abgesichert;
    die Skalierung (Faktor 1.0 statt z.B. 0.74) beruht auf der Annahme,
    dass der Gyro-Sensor selbst korrekt in deg/s skaliert ist und die
    GPS-Kursaenderung (wegen der noetigen 4s-Glaettung bei 1Hz-GPS,
    siehe PROJEKT_STAND.md) die wahre Rate eher UNTERSCHAETZT statt der
    Gyro sie UEBERSCHAETZT - nicht 100% zweifelsfrei, aber die deutlich
    plausiblere Annahme. Siehe WICHTIGE EINSCHRAENKUNGEN.
  - RotationRateY wird wie die Beschleunigungsachsen mit
    LOWPASS_CUTOFF_HZ=6Hz gefiltert (gleiche Vehicle-Dynamics-Bandbreite
    wie in vibration_analysis.py).
  - Praktischer Vorteil dieser Methode: bei Stillstand/sehr geringer
    Geschwindigkeit (v~0) wird a_lat automatisch ~0, unabhaengig davon,
    was das Gyroskop gerade misst - das macht den Ansatz robust gegen
    das beobachtete Artefakt "Handy wird im Stand angefasst" (fuehrte
    bei der Kalibrierungspruefung zu grossen, aber irrelevanten
    Gyro-Ausschlaegen bei v=0).

Restliche Methodik (unveraendert):
  1. Lowpass-gefiltertes Signal aus *_imu_filtered.csv verwenden
     (Resonanz + Ausreisser bereits entfernt, siehe
     vibration_analysis.py) - NICHT das rohe Signal.
  2. Die ersten STARTUP_SKIP_S Sekunden verwerfen (bekanntes
     Sensor-Fusion-Einschwingartefakt, siehe PROJEKT_STAND.md Punkt 3).
  3. a_long = X*cos(theta) + Z*sin(theta) (positiv = beschleunigen),
     a_lat = v*omega (positiv = Rechtskurve, siehe oben - Vorzeichen
     jetzt ueber GPS validiert).
  4. In g umrechnen (/ 9.81), Kennzahlen bilden: Maximum, Perzentile,
     sowie Anteil der Fahrzeit oberhalb mehrerer Schwellen (0.3/0.5/0.7/
     0.9g) als grobes Mass fuer "wie oft nahe am Grip-Limit". Gleiches
     fuer den kombinierten Betrag sqrt(a_long^2+a_lat^2) (Reifen-
     "Traction Circle"-Nutzung, unabhaengig von Brems-/Kurvenanteil).
  5. g-g-Diagramm (Scatter a_lat vs a_long) pro Log als PNG, mit
     Referenzkreisen bei 0.5g/0.8g/1.0g NUR als visuelle Orientierung
     (kein validierter Reifengrenzwert fuer dieses Fahrzeug/diese
     Reifen - rein zur Einordnung der Groessenordnung).

WICHTIGE EINSCHRAENKUNGEN:
  - a_lat ist eine kinematische NAEHERUNG (coordinated turn, kein
    Schwimmwinkel/Drift beruecksichtigt) - bei echtem Uebersteuern/
    Untersteuern oder Drift weicht der wahre Reifen-Querschlupf von
    dieser Schaetzung ab.
  - Die Gyro-Skalierung (Faktor 1.0) ist die plausiblere, aber nicht
    zweifelsfrei bewiesene Annahme (siehe oben) - eine dedizierte
    Kalibrierfahrt mit bekanntem Kurvenradius/Geschwindigkeit koennte
    das praezisieren.
  - theta (fuer a_long) wurde aus Bremsereignissen JEDES Logs SEPARAT
    bestimmt - bei Logs mit wenigen Bremsereignissen (z.B.
    2026-08-28 151851: nur 3) ist die Kalibrierung entsprechend
    unsicherer (R als Guetemass mit ausgegeben).
  - Kein Tiefpass-Cutoff-Bias-Check: LOWPASS_CUTOFF_HZ=6Hz aus
    vibration_analysis.py entfernt ggf. sehr schnelle Grip-Transienten
    (z.B. Kerbside-Stoesse) - fuer die hier interessierende quasi-
    statische Grip-Nutzung unkritisch, aber nicht fuer Spitzenanalyse
    einzelner Stoss-Ereignisse geeignet.

Aufruf: python grip_estimation.py
(verarbeitet automatisch alle *_imu_filtered.csv mit passendem Eintrag
 in brake_event_summary.json)
"""
import glob
import os
import json
import sqlite3
import numpy as np
import pandas as pd
from scipy import signal
from scipy.interpolate import interp1d
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from imu_orientation import detect_vertical_axis, horizontal_axes, yaw_channel, GYRO_SIGN_SCALE

RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"
RESULTS_DIR = "results"

G = 9.81
STARTUP_SKIP_S = 1.5
LOWPASS_CUTOFF_HZ = 6.0
FS_UNIFORM = 50.0
GRIP_THRESHOLDS_G = [0.3, 0.5, 0.7, 0.9]
REFERENCE_CIRCLES_G = [0.5, 0.8, 1.0]  # rein visuelle Orientierung, siehe Docstring
TICKS_OFFSET = 621355968000000000  # .NET-Ticks -> Unix-Referenz


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


def yaw_rate_and_speed_on_grid(db_file, t_grid, v_axis):
    """Liefert (omega_deg_s, v_ms) auf t_grid, kalibriert/gefiltert wie im
    Docstring beschrieben. t_grid muss auf demselben t0 basieren wie
    *_imu_filtered.csv (t0 = fruehester Zeitstempel ueber alle 3
    Beschleunigungsachsen, siehe vibration_analysis.py). v_axis = die per
    imu_orientation.detect_vertical_axis() erkannte Vertikalachse dieses
    Logs (bestimmt den Gierraten-Kanal, siehe dortigen Docstring)."""
    conn = sqlite3.connect(os.path.join(RAW_DIR, db_file))
    accel = load_channel(conn, ["AccelerationX", "AccelerationY", "AccelerationZ"])
    rot = load_channel(conn, [yaw_channel(v_axis)])
    speed = load_channel(conn, ["VehicleSpeed"])
    conn.close()
    t0 = accel["datetime"].min()
    rot["t"] = (rot["datetime"] - t0).dt.total_seconds()
    speed["t"] = (speed["datetime"] - t0).dt.total_seconds()

    rot_sub = rot[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    f_rot = interp1d(rot_sub["t"].values, rot_sub["value"].values, kind="linear",
                      bounds_error=False, fill_value=(rot_sub["value"].values[0],
                                                       rot_sub["value"].values[-1]))
    gyro_u = f_rot(t_grid)
    b, a = signal.butter(4, LOWPASS_CUTOFF_HZ / (FS_UNIFORM / 2), btype="low")
    gyro_lp = signal.filtfilt(b, a, gyro_u)
    omega_deg_s = GYRO_SIGN_SCALE * gyro_lp

    speed_sub = speed[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    v_ms = np.interp(t_grid, speed_sub["t"].values, speed_sub["value"].values) / 3.6

    return omega_deg_s, v_ms


def stats_block(values_g, dt_s):
    return {
        "max_g": float(np.max(values_g)),
        "p50_g": float(np.percentile(values_g, 50)),
        "p90_g": float(np.percentile(values_g, 90)),
        "p95_g": float(np.percentile(values_g, 95)),
        "p99_g": float(np.percentile(values_g, 99)),
        "time_above_s": {
            f"{thr:.1f}g": float(np.sum(values_g > thr) * dt_s)
            for thr in GRIP_THRESHOLDS_G
        },
    }


def plot_gg_diagram(a_long_g, a_lat_g, db_file, out_path):
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(a_lat_g, a_long_g, s=2, alpha=0.15, color="steelblue")
    theta_circ = np.linspace(0, 2 * np.pi, 200)
    for r in REFERENCE_CIRCLES_G:
        ax.plot(r * np.cos(theta_circ), r * np.sin(theta_circ), "--", lw=0.8,
                color="gray", alpha=0.7)
        ax.annotate(f"{r:.1f}g", (0, r), color="gray", fontsize=8)
    ax.axhline(0, color="black", lw=0.5)
    ax.axvline(0, color="black", lw=0.5)
    ax.set_xlabel("a_lat [g]  (+ = Rechtskurve, ueber GPS validiert)")
    ax.set_ylabel("a_long [g]  (+ = beschleunigen, - = bremsen)")
    ax.set_title(f"g-g-Diagramm: {db_file}")
    ax.set_aspect("equal")
    lim = max(1.2, np.percentile(np.hypot(a_long_g, a_lat_g), 99.5) * 1.2)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def clip_exclusion_mask(t, vib_summary_path, axes, pad_s=0.2):
    """Markiert Zeitfenster um bereits bekannte physical_clip-Ereignisse
    (siehe vibration_analysis.py) - das sind harte Halterungs-/Sensor-
    Artefakte (Rohwerte weit ausserhalb ±12 m/s^2), keine echte
    Fahrdynamik. Stichprobe ergab: der hoechste Kombi-g-Wert eines Logs
    fiel exakt mit so einem Ereignis zusammen (siehe PROJEKT_STAND.md).
    Ohne diesen Ausschluss waeren "max_g"-Werte irrefuehrend."""
    mask = np.zeros(len(t), dtype=bool)
    try:
        with open(vib_summary_path, encoding="utf-8") as f:
            summary = json.load(f)
    except FileNotFoundError:
        return mask
    for axis in axes:
        for ev in summary.get("axes", {}).get(axis, {}).get("physical_clip", {}).get("events", []):
            lo = ev["t"] - ev["duration_s"] / 2 - pad_s
            hi = ev["t"] + ev["duration_s"] / 2 + pad_s
            mask |= (t >= lo) & (t <= hi)
    return mask


def analyze_log(csv_path, db_file, theta_deg, theta_r, theta_n):
    df = pd.read_csv(csv_path)
    t = df["t"].values

    orient = detect_vertical_axis(os.path.join(RAW_DIR, db_file))
    v_axis = orient["axis"]
    h1, h2 = horizontal_axes(v_axis)
    h1_vals = df[f"Acceleration{h1}_lowpass"].values
    h2_vals = df[f"Acceleration{h2}_lowpass"].values

    keep = t >= (t[0] + STARTUP_SKIP_S)
    vib_summary_path = os.path.join(DERIVED_DIR, db_file.replace(".dlg", "_vibration_summary.json"))
    excl = clip_exclusion_mask(t, vib_summary_path, axes=(f"Acceleration{h1}", f"Acceleration{h2}"))
    n_excluded = int((keep & excl).sum())
    keep = keep & ~excl

    omega_deg_s, v_ms = yaw_rate_and_speed_on_grid(db_file, t, v_axis)

    t, h1_vals, h2_vals = t[keep], h1_vals[keep], h2_vals[keep]
    omega_deg_s, v_ms = omega_deg_s[keep], v_ms[keep]
    dt_s = np.median(np.diff(t))

    # theta/H1/H2 muessen zum selben Achsenpaar UND derselben Reihenfolge
    # gehoeren, die brake_event_analysis.py fuer dieses Log verwendet hat
    # (imu_orientation.horizontal_axes() liefert deterministisch dieselbe
    # Reihenfolge X<Y<Z fuer dieselbe Vertikalachse - konsistent per Konstruktion).
    phi = np.radians(theta_deg)
    a_long = h1_vals * np.cos(phi) + h2_vals * np.sin(phi)
    a_lat = v_ms * np.radians(omega_deg_s)
    a_comb = np.hypot(a_long, a_lat)

    a_long_g, a_lat_g, a_comb_g = a_long / G, a_lat / G, a_comb / G

    png_path = os.path.join(DERIVED_DIR, db_file.replace(".dlg", "_gg_diagram.png"))
    plot_gg_diagram(a_long_g, a_lat_g, db_file, png_path)

    return {
        "file": db_file,
        "vertical_axis": v_axis,
        "vertical_axis_defaulted": orient["defaulted"],
        "horizontal_axes": [h1, h2],
        "theta_forward_deg": theta_deg,
        "theta_r": theta_r,
        "theta_n_brake_events": theta_n,
        "duration_s": float(t[-1] - t[0]),
        "n_samples": len(t),
        "n_samples_excluded_clip_artifact": n_excluded,
        "lateral_g": stats_block(np.abs(a_lat_g), dt_s),
        "longitudinal_g": stats_block(np.abs(a_long_g), dt_s),
        "combined_g": stats_block(a_comb_g, dt_s),
        "gg_diagram_png": png_path,
    }


def main():
    with open(os.path.join(RESULTS_DIR, "brake_event_summary.json"), encoding="utf-8") as f:
        brake_data = {r["file"]: r for r in json.load(f)}

    results = []
    for csv_path in sorted(glob.glob(f"{DERIVED_DIR}/*_imu_filtered.csv")):
        db_file = os.path.basename(csv_path).replace("_imu_filtered.csv", ".dlg")
        brec = brake_data.get(db_file)
        if brec is None or brec.get("forward_direction_deg") is None:
            print(f"{db_file}: keine Achsenkalibrierung vorhanden - uebersprungen")
            continue
        res = analyze_log(csv_path, db_file, brec["forward_direction_deg"],
                           brec["theta_r"], brec["theta_n_events"])
        results.append(res)

        lat, lon, comb = res["lateral_g"], res["longitudinal_g"], res["combined_g"]
        print(f"\n=== {db_file} (theta={res['theta_forward_deg']:.1f}° R={res['theta_r']:.2f} "
              f"aus {res['theta_n_brake_events']} Bremsereignissen, "
              f"{res['n_samples_excluded_clip_artifact']} Samples wg. bekannter "
              f"Halterungsartefakte ausgeschlossen) ===")
        print(f"  Quer (kinematisch v*omega): max={lat['max_g']:.2f}g  p95={lat['p95_g']:.2f}g  "
              f"p99={lat['p99_g']:.2f}g  t>0.5g={lat['time_above_s']['0.5g']:.1f}s  "
              f"t>0.7g={lat['time_above_s']['0.7g']:.1f}s")
        print(f"  Laengs: max={lon['max_g']:.2f}g  p95={lon['p95_g']:.2f}g  "
              f"p99={lon['p99_g']:.2f}g  t>0.5g={lon['time_above_s']['0.5g']:.1f}s  "
              f"t>0.7g={lon['time_above_s']['0.7g']:.1f}s")
        print(f"  Kombiniert (Traction Circle): max={comb['max_g']:.2f}g  "
              f"p95={comb['p95_g']:.2f}g  p99={comb['p99_g']:.2f}g  "
              f"t>0.7g={comb['time_above_s']['0.7g']:.1f}s  t>0.9g={comb['time_above_s']['0.9g']:.1f}s")
        print(f"  g-g-Diagramm: {res['gg_diagram_png']}")

    with open(os.path.join(RESULTS_DIR, "grip_estimation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nDetails: results/grip_estimation_summary.json")


if __name__ == "__main__":
    main()
