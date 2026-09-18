"""
Bremsereignis-Analyse + Achsenrotation ueber echten Bremsdruck
(MZ-CAN-PID BFP_PRE_MZ) (MX-5 Projekt)

Zweck: die Nachmittagslogs enthalten laut Fahrer definitiv starke
Bremsereignisse. Bisher wurde nur die grobe OBD-Geschwindigkeit
(VehicleSpeed, ~3Hz, 1 km/h-Aufloesung) als Referenz fuer Brems-
/Beschleunigungsphasen genutzt (siehe vibration_analysis.py,
estimate_axis_rotation.py) - das war zu verrauscht (R^2 ~0). Die .dlg
enthaelt aber einen viel besseren Kanal: `BFP_PRE_MZ` = "Brake Fluid
Pressure Sensor" [kPa], ein Mazda-spezifischer CAN-PID, der den
tatsaechlichen Bremsdruck liefert - eine echte, hochaufgeloeste,
eindeutige Referenz fuer "hier wird gerade real und stark gebremst".

Was das Skript macht:
  1. Fuer jeden Log: BFP_PRE_MZ laden, auf dasselbe Zeitraster wie die
     bereits gefilterten IMU-Daten (*_imu_filtered.csv, Hampel+Clipping+
     Lowpass bereits angewendet, siehe vibration_analysis.py) bringen.
  2. Bremsereignisse erkennen: zusammenhaengende Phasen mit
     Bremsdruck > BRAKE_PRESSURE_MIN_KPA, zu Einzelereignissen
     gruppiert (gleiche Logik wie group_events() in
     vibration_analysis.py).
  3. Pro Ereignis: Spitzendruck, Dauer, Zeitpunkt, und die IMU-Antwort
     (max. |Beschleunigung| auf allen 3 Achsen, lowpass-gefiltert,
     Resonanz bereits entfernt) im selben Zeitfenster.
  4. Achsenrotation (loest Punkt 4 aus docs/logs/projekt-stand.md ohne dedizierte
     Kalibrierfahrt): pro "echtem" Bremsereignis (Dauer 1-6s, Spitzen-
     druck >=1000 kPa, keine Standphase) wird der GEMITTELTE horizontale
     Beschleunigungsvektor (X,Z) ueber die Kernphase des Ereignisses
     (Druck > 70% des Spitzendrucks) gebildet - das ist deutlich
     robuster als ein Einzelsample am Druckmaximum (Ausprobiert: die
     Streuung der Einzelwinkel sinkt dadurch drastisch). Der Winkel
     dieses Vektors zeigt in "negative Laengsrichtung" (Bremsen =
     Verzoegerung). Ueber alle Ereignisse eines Logs ergibt der
     betragsgewichtete zirkulaere Mittelwert THETA_BRAKE_DEG die
     Bremsrichtung in der IMU-X-Z-Ebene; die Fahrzeug-Vorwaertsrichtung
     liegt bei THETA_BRAKE_DEG + 180°. Konzentrationsmass R (0=verstreut,
     1=perfekt konzentriert) zeigt die Zuverlaessigkeit.
     WICHTIG: theta wird PRO LOG separat berechnet (nicht global
     gemittelt) - Test zeigte R=0.84-0.92 pro Log (sehr konsistent
     innerhalb einer Fahrt), aber der Mittelwinkel schwankt zwischen
     Logs um bis zu ~28° (-110° bis -138°) - die Halterung ist also
     zwischen Fahrten nicht exakt gleich ausgerichtet (wie vom Nutzer
     erwartet), aber innerhalb einer Fahrt stabil genug fuer diese
     Methode. Die Streuung der EINZELNEN Ereigniswinkel innerhalb eines
     Logs wird u.a. auf Trail-Braking (Bremsen in der Kurve, mischt
     Laengs- und Querbeschleunigung) zurueckgefuehrt.

Aufruf: python brake_event_analysis.py [log ...]
(ohne Argument: alle *_imu_filtered.csv im Ordner, die eine passende
 .dlg-Datei mit BFP_PRE_MZ-Kanal haben. Mit einem oder mehreren Log-Namen
 (z.B. "2026-09-11 081200", mit oder ohne .dlg): nur diese Logs neu
 berechnen, Ergebnisse werden in brake_event_summary.json mit den
 bestehenden Eintraegen der UEBRIGEN Logs zusammengefuehrt, nicht
 ueberschrieben.)

NACHTRAG (06.09.2026): "X,Z" oben beschreibt die Halterung bis inkl.
2026-09-05 (Y=vertikal, fest verbaut). Die Halterung ist jetzt variabel
(kann sich von Fahrt zu Fahrt aendern) - welche zwei Achsen horizontal
sind, wird deshalb PRO LOG per `imu_orientation.detect_vertical_axis()`
bestimmt (Gravitationsvektor im Stillstand), nicht mehr fest angenommen.
Die obige Beschreibung/Formel gilt unveraendert, nur eben fuer das jeweils
erkannte Achsenpaar statt fest X/Z.
"""
import glob
import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd

from imu_orientation import detect_vertical_axis, horizontal_axes

RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"
RESULTS_DIR = "results"
from scipy.interpolate import interp1d

TICKS_OFFSET = 621355968000000000  # .NET-Ticks -> Unix-Referenz
BRAKE_PRESSURE_MIN_KPA = 300.0   # ab hier als "Bremsereignis" gewertet
                                  # (deutlich ueber Sensor-Nullpunkt-Rauschen)
MIN_EVENT_SAMPLES = 3            # kurze Einzel-Spikes ignorieren
IMU_WINDOW_S = 0.5               # Suchfenster um den Druck-Peak fuer die
                                  # IMU-Antwort (Bremsdruck fuehrt/folgt der
                                  # tatsaechlichen Verzoegerung leicht)

# --- Achsenrotation aus Bremsereignissen (siehe Docstring, Punkt 4) ---
THETA_EVENT_DUR_MIN_S = 1.0      # nur "echte" Bremsmanoever, keine kurzen
THETA_EVENT_DUR_MAX_S = 6.0      # Spikes und keine langen Standphasen
THETA_EVENT_MIN_KPA = 1000.0     # deutliche Bremsung, kein Antippen
THETA_CORE_PRESSURE_FRAC = 0.7   # Kernphase des Ereignisses: nur Samples mit
                                  # Druck > 70% des Spitzendrucks fuer den
                                  # gemittelten Vektor verwenden


def circular_weighted_mean(angles_deg, weights):
    """Betragsgewichteter zirkulaerer Mittelwert + Konzentrationsmass R
    (0=verstreut, 1=perfekt konzentriert)."""
    rad = np.radians(np.asarray(angles_deg))
    w = np.asarray(weights)
    sx, sy = np.sum(w * np.cos(rad)), np.sum(w * np.sin(rad))
    wsum = np.sum(w)
    if wsum <= 0:
        return None, None
    return float(np.degrees(np.arctan2(sy, sx))), float(np.hypot(sx, sy) / wsum)


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
    conn = sqlite3.connect(os.path.join(RAW_DIR, db_file))
    accel = load_channel(conn, ["AccelerationX", "AccelerationY", "AccelerationZ"])
    conn.close()
    return accel["datetime"].min()


def brake_pressure_on_grid(db_file, t0, t_grid):
    conn = sqlite3.connect(os.path.join(RAW_DIR, db_file))
    brake = load_channel(conn, ["BFP_PRE_MZ"])
    conn.close()
    if len(brake) == 0:
        return None
    brake["t"] = (brake["datetime"] - t0).dt.total_seconds()
    sub = brake[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    f = interp1d(sub["t"].values, sub["value"].values, kind="linear",
                 bounds_error=False, fill_value=(0.0, 0.0))
    return f(t_grid)


def group_events(t, pressure, mask, max_events=500):
    events = []
    in_event = False
    start_idx = None
    n = len(mask)
    for i in range(n):
        if mask[i] and not in_event:
            in_event = True
            start_idx = i
        elif not mask[i] and in_event:
            in_event = False
            seg = slice(start_idx, i)
            if (i - start_idx) >= MIN_EVENT_SAMPLES:
                peak_i = start_idx + int(np.argmax(pressure[seg]))
                events.append({"t_start": float(t[start_idx]), "t_end": float(t[i - 1]),
                               "t_peak": float(t[peak_i]), "peak_kpa": float(pressure[peak_i]),
                               "duration_s": float(t[i - 1] - t[start_idx])})
    if in_event and (n - start_idx) >= MIN_EVENT_SAMPLES:
        seg = slice(start_idx, n)
        peak_i = start_idx + int(np.argmax(pressure[seg]))
        events.append({"t_start": float(t[start_idx]), "t_end": float(t[n - 1]),
                       "t_peak": float(t[peak_i]), "peak_kpa": float(pressure[peak_i]),
                       "duration_s": float(t[n - 1] - t[start_idx])})
    events.sort(key=lambda e: e["peak_kpa"], reverse=True)
    return events[:max_events]


def analyze_log(csv_path, db_file):
    df = pd.read_csv(csv_path)
    t = df["t"].values

    orient = detect_vertical_axis(os.path.join(RAW_DIR, db_file))
    v_axis = orient["axis"]
    h1, h2 = horizontal_axes(v_axis)
    v_vals = df[f"Acceleration{v_axis}_lowpass"].values   # nur diagnostisch (Nick-/Wank-Antwort)
    h1_vals = df[f"Acceleration{h1}_lowpass"].values
    h2_vals = df[f"Acceleration{h2}_lowpass"].values

    t0 = accel_t0(db_file)
    pressure = brake_pressure_on_grid(db_file, t0, t)
    if pressure is None:
        return {"file": db_file, "error": "kein BFP_PRE_MZ-Kanal in diesem Log"}

    mask = pressure > BRAKE_PRESSURE_MIN_KPA
    events = group_events(t, pressure, mask)

    for ev in events:
        wmask = (t >= ev["t_peak"] - IMU_WINDOW_S) & (t <= ev["t_peak"] + IMU_WINDOW_S)
        if wmask.sum() == 0:
            continue
        ih1 = np.argmax(np.abs(h1_vals[wmask]))
        ev[f"max_abs_accel_{h1.lower()}"] = float(h1_vals[wmask][ih1])
        ev[f"max_abs_accel_{v_axis.lower()}"] = float(v_vals[wmask][np.argmax(np.abs(v_vals[wmask]))])
        ev[f"max_abs_accel_{h2.lower()}"] = float(h2_vals[wmask][np.argmax(np.abs(h2_vals[wmask]))])
        # Winkel des horizontalen Vektors am Zeitpunkt der staerksten H1-Antwort
        # (nur diagnostisch/zur Anzeige - fuer die Achsenrotation unten wird der
        # robustere, ueber die Kernphase gemittelte Vektor verwendet)
        h1i, h2i = h1_vals[wmask][ih1], h2_vals[wmask][ih1]
        ev["horizontal_vector_angle_deg"] = float(np.degrees(np.arctan2(h2i, h1i)))
        ev["horizontal_magnitude_ms2"] = float(np.hypot(h1i, h2i))

        # --- Achsenrotation: gemittelter Vektor ueber die Kernphase ---
        if (THETA_EVENT_DUR_MIN_S <= ev["duration_s"] <= THETA_EVENT_DUR_MAX_S
                and ev["peak_kpa"] >= THETA_EVENT_MIN_KPA):
            core = ((t >= ev["t_start"]) & (t <= ev["t_end"])
                    & (pressure > THETA_CORE_PRESSURE_FRAC * ev["peak_kpa"]))
            if core.sum() >= 3:
                m1, m2 = float(h1_vals[core].mean()), float(h2_vals[core].mean())
                ev["core_vector_angle_deg"] = float(np.degrees(np.arctan2(m2, m1)))
                ev["core_vector_magnitude_ms2"] = float(np.hypot(m1, m2))

    theta_events = [ev for ev in events if "core_vector_angle_deg" in ev]
    theta_deg, theta_r = None, None
    if theta_events:
        theta_deg, theta_r = circular_weighted_mean(
            [ev["core_vector_angle_deg"] for ev in theta_events],
            [ev["core_vector_magnitude_ms2"] for ev in theta_events],
        )

    return {
        "file": db_file,
        "vertical_axis": orient["axis"],
        "vertical_axis_defaulted": orient["defaulted"],
        "horizontal_axes": [h1, h2],
        "n_events": len(events),
        "events": events,
        "theta_brake_deg": theta_deg,
        "theta_r": theta_r,
        "theta_n_events": len(theta_events),
        "forward_direction_deg": (theta_deg + 180) if theta_deg is not None else None,
    }


def merge_results(path, new_results, key="file"):
    """Bestehende Eintraege behalten, nur die neu verarbeiteten Logs ersetzen -
    damit ein Aufruf mit einzelnen Log-Namen (taegliche Routine) nicht die
    Historie der uebrigen, nicht neu verarbeiteten Logs ueberschreibt."""
    existing = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = {r[key]: r for r in json.load(f)}
    for r in new_results:
        existing[r[key]] = r
    merged = [existing[k] for k in sorted(existing)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    return merged


def main():
    target_logs = [os.path.basename(a).removesuffix(".dlg") for a in sys.argv[1:]]
    if target_logs:
        csv_paths = sorted(f"{DERIVED_DIR}/{t}_imu_filtered.csv" for t in target_logs)
    else:
        csv_paths = sorted(glob.glob(f"{DERIVED_DIR}/*_imu_filtered.csv"))

    new_results = []
    for csv_path in csv_paths:
        db_file = os.path.basename(csv_path).replace("_imu_filtered.csv", ".dlg")
        res = analyze_log(csv_path, db_file)
        new_results.append(res)
        if "error" in res:
            print(f"{db_file}: {res['error']}")
            continue
        defaulted_str = " [Vertikalachse per Fallback=Y angenommen, keine Standphase erkannt]" if res["vertical_axis_defaulted"] else ""
        print(f"\n=== {db_file}: {res['n_events']} Bremsereignis(se) > {BRAKE_PRESSURE_MIN_KPA:.0f} kPa "
              f"(vertikal={res['vertical_axis']}, horizontal={'/'.join(res['horizontal_axes'])}){defaulted_str} ===")
        for ev in res["events"]:
            if "max_abs_accel_x" not in ev:
                continue
            core_str = (f"  | Kernvektor={ev['core_vector_angle_deg']:7.1f} deg "
                        f"(|{ev['core_vector_magnitude_ms2']:.2f}|)"
                        if "core_vector_angle_deg" in ev else "")
            print(f"  t={ev['t_peak']:7.1f}s  Spitzendruck={ev['peak_kpa']:6.0f} kPa  "
                  f"Dauer={ev['duration_s']:.2f}s  |  accel X={ev['max_abs_accel_x']:+6.2f}  "
                  f"Y={ev['max_abs_accel_y']:+6.2f}  Z={ev['max_abs_accel_z']:+6.2f} m/s^2{core_str}")

        if res["theta_brake_deg"] is not None:
            print(f"  --> Achsenrotation aus {res['theta_n_events']} Ereignissen: "
                  f"Bremsrichtung={res['theta_brake_deg']:.1f} deg, "
                  f"Fahrzeug-vorwaerts={res['forward_direction_deg']:.1f} deg "
                  f"(R={res['theta_r']:.3f})")
        else:
            print("  --> zu wenige klare Bremsereignisse fuer eine Achsenrotations-Schaetzung")

    summary_path = os.path.join(RESULTS_DIR, "brake_event_summary.json")
    all_results = merge_results(summary_path, new_results)

    valid = [r for r in all_results if r.get("theta_brake_deg") is not None]
    if valid:
        print("\n=== Achsenrotation pro Log (Fahrzeug-vorwaerts-Richtung in der jeweiligen "
              "IMU-Horizontalebene - Achsenpaar variiert je nach Halterung, siehe horizontal_axes) ===")
        for r in valid:
            print(f"  {r['file']}: {r['forward_direction_deg']:7.1f} deg  (R={r['theta_r']:.3f}, "
                  f"n={r['theta_n_events']}, Ebene={'/'.join(r['horizontal_axes'])})")
        fwd = np.array([r["forward_direction_deg"] for r in valid])
        print(f"  Spanne über {len(valid)} Logs: [{fwd.min():.1f}, {fwd.max():.1f}] deg "
              f"(Streuung Std={fwd.std():.1f} deg) -> PRO LOG kalibrieren, nicht global mitteln "
              f"(siehe Docstring).")

    print(f"\nDetails: {summary_path} ({len(all_results)} Log(s) insgesamt, {len(new_results)} davon in diesem Lauf neu berechnet)")


if __name__ == "__main__":
    main()
