"""
Kreuzvalidierung: CAN-Querdynamik-Kanaele (Steering_Wheel_Absolute_Angle,
Lateral_Acc_Raw, YawRate_Raw - alle RCM/EPS ueber HS-CAN) gegen das bereits
validierte OBD-Lenkwinkel-Modell (steering_lateral_model.py, R²=0.950,
155 Kalibrierpunkte). Erster Baustein fuer die geplante Querdynamik-/
Kurvenmodell-Erweiterung (siehe docs/logs/can-bus-status.md, "Naechste Schritte").

Nutzt den bisher EINZIGEN Log mit gleichzeitiger CAN- UND OBD-Fusion-
Aufzeichnung (Y-Splitter-Kalibrierfahrt, selber OBD-Port, siehe
docs/logs/can-bus-status.md):
    CAN: candump-2026-09-12_211833  (20 min)
    OBD: 2026-09-12 211851.dlg      (selbe Fahrt)
Weil beide Logger gleichzeitig am selben Fahrzeug haengen, ist das eine
Punkt-fuer-Punkt-Kreuzvalidierung ohne den Umweg ueber GPS/Streckenabgleich
(wie es die schwache r=0.30-GPS-Pruefung fuer YawRate_Raw in
docs/logs/can-bus-status.md bisher war).

WICHTIGER FUND beim Zeitabgleich: die "timestamp_local"-Spalte, die
build_datalake.py's ingest_dlg() aus den .dlg-Rohzeiten (TICKS_OFFSET)
erzeugt, ist trotz des Namens tatsaechlich UTC, nicht lokale Zeit
(Deutschland im September = CEST = UTC+2) - der CAN-Log-Timestamp (der
ueber die candump-Unix-Epoche korrekt lokal ist, siehe log_start_epoch())
liegt dadurch ~2h VOR dem dlg-Timestamp. Bisher unsichtbar, weil alle
anderen Skripte Zeiten nur INNERHALB eines Quellformats vergleichen. Hier
kein Fix an build_datalake.py selbst - der Zeitversatz zwischen den beiden
Logs wird stattdessen per Kreuzkorrelation der beiden VehicleSpeed-Spuren
empirisch bestimmt (robuster als der rohe Metadaten-Wert, faengt auch
Toleranzen im Sekundenbereich zwischen candump-Start und OBD-Fusion-
App-Start ab).

Aufruf: python scripts/can_lateral_validation.py
"""
import os
import json
import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from steering_lateral_model import (
    logs_with_steering, load_zero_offsets, build_calibration_set,
    fit_k, apply_continuous, load_channel as load_obd_channel,
)

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
CAN_LOG_ID = "candump-2026-09-12_211833"
OBD_LOG_ID = "2026-09-12 211851"
GRID_DT_S = 0.1
OFFSET_SEARCH_S = 15.0  # Suchfenster um den Metadaten-Schaetzwert, in Sekunden


def load_can_channel(con, channel):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [CAN_LOG_ID, channel],
    ).fetchdf()


def raw_t0_offset_s(con):
    """Grobe Zeitversatz-Schaetzung aus den (fehlerhaften) timestamp_local-
    Metadaten, siehe Docstring oben - nur als Startpunkt fuer die
    Kreuzkorrelations-Suche, nicht als Endergebnis."""
    t0 = con.execute(
        "SELECT log_id, min(timestamp_local) FROM measurements "
        "WHERE log_id IN (?, ?) GROUP BY log_id", [CAN_LOG_ID, OBD_LOG_ID],
    ).fetchdf()
    t0 = dict(zip(t0["log_id"], t0["min(timestamp_local)"]))
    can_t0, obd_t0 = t0[CAN_LOG_ID], t0[OBD_LOG_ID]
    obd_t0_corrected = obd_t0 + np.timedelta64(2, "h")
    return (obd_t0_corrected - can_t0) / np.timedelta64(1, "s")


def find_time_offset(can_speed, obd_speed, guess_s):
    """offset_s so, dass t_can = t_obd + offset_s dieselbe reale Fahrsituation
    trifft. Bestimmt per Kreuzkorrelation der beiden VehicleSpeed-Spuren
    (physikalisch identische Groesse, in beiden Logs vorhanden) statt der
    rohen Zeitstempel-Differenz zu vertrauen."""
    t_min = max(can_speed["t"].min(), obd_speed["t"].min() + guess_s - OFFSET_SEARCH_S)
    t_max = min(can_speed["t"].max(), obd_speed["t"].max() + guess_s + OFFSET_SEARCH_S)
    grid = np.arange(t_min, t_max, GRID_DT_S)
    can_v = np.interp(grid, can_speed["t"], can_speed["value"])

    offsets = np.arange(guess_s - OFFSET_SEARCH_S, guess_s + OFFSET_SEARCH_S, GRID_DT_S)
    best_r, best_off = -np.inf, guess_s
    for off in offsets:
        obd_v = np.interp(grid, obd_speed["t"] + off, obd_speed["value"])
        r = np.corrcoef(can_v, obd_v)[0, 1]
        if r > best_r:
            best_r, best_off = r, off
    return float(best_off), float(best_r)


def sign_corrected_fit(obd_vals, can_vals):
    """Bestimmt per Vorzeichenvergleich + Regression ohne Achsenabschnitt
    (beide Groessen sind bei Geradeausfahrt physikalisch Null), ob CAN und
    OBD-Modell gleiches oder entgegengesetztes Vorzeichen verwenden, und
    liefert Guetemasse fuer die (vorzeichenkorrigierte) Uebereinstimmung."""
    r_raw = float(np.corrcoef(obd_vals, can_vals)[0, 1])
    sign = 1.0 if r_raw >= 0 else -1.0
    can_corr = sign * can_vals
    slope = float(np.sum(obd_vals * can_corr) / np.sum(can_corr ** 2))
    rmse = float(np.sqrt(np.mean((obd_vals - can_corr) ** 2)))
    r_corr = float(np.corrcoef(obd_vals, can_corr)[0, 1])
    return {"sign": sign, "r_raw": r_raw, "r": r_corr, "slope_obd_per_can": slope, "rmse": rmse}


def main():
    con = duckdb.connect(DB_PATH, read_only=True)

    # 1. OBD-Modell (bereits kalibriert gegen 234 GPS+Gyro-bestaetigte Kurven)
    #    fuer den gemeinsamen Log anwenden - keine Neukalibrierung hier.
    steering_logs = logs_with_steering(con)
    zero_offsets = load_zero_offsets()
    points = build_calibration_set(con, steering_logs, zero_offsets)
    k, r2 = fit_k(points)
    print(f"OBD-Modell (steering_lateral_model.py): k1={k[0]:.6f} k2={k[1]:.7f} "
          f"R²={r2:.3f} (n={len(points)}, unveraendert uebernommen)")
    obd = apply_continuous(con, OBD_LOG_ID, k, zero_offsets)
    if obd is None:
        raise SystemExit(f"Keine OBD-Lenkwinkel/Speed-Daten fuer {OBD_LOG_ID}")

    # 2. CAN-Kanaele laden
    can_steer = load_can_channel(con, "SteeringAngle_CAN")
    can_lat = load_can_channel(con, "LateralAcc_CAN")
    can_yaw = load_can_channel(con, "YawRate_CAN")
    can_speed = load_can_channel(con, "VehicleSpeed")
    print(f"CAN-Log {CAN_LOG_ID}: {len(can_steer)} Lenkwinkel-, {len(can_lat)} "
          f"Querbeschl.-, {len(can_yaw)} Gierraten-Samples")

    # 3. Zeitversatz bestimmen (Metadaten-Schaetzwert nur als Startpunkt)
    guess_s = raw_t0_offset_s(con)
    obd_speed = load_obd_channel(con, OBD_LOG_ID, "VehicleSpeed")
    offset_s, offset_r = find_time_offset(can_speed, obd_speed, guess_s)
    print(f"\nZeitversatz t_can = t_obd + {offset_s:.2f}s "
          f"(Metadaten-Schaetzwert: {guess_s:.2f}s, Speed-Kreuzkorrelation r={offset_r:.4f} "
          f"bei bestem Offset - {'plausibel, klarer Peak' if offset_r > 0.9 else 'SCHWACH, mit Vorsicht behandeln'})")

    # 4. Gemeinsames Zeitraster (OBD-Zeitbasis), alles darauf interpolieren
    t_lo = max(obd["t"].min(), can_steer["t"].min() - offset_s, can_lat["t"].min() - offset_s)
    t_hi = min(obd["t"].max(), can_steer["t"].max() - offset_s, can_lat["t"].max() - offset_s)
    grid = np.arange(t_lo, t_hi, GRID_DT_S)

    obd_steer_g = np.interp(grid, obd["t"], obd["steer_deg"])
    obd_lat_g = np.interp(grid, obd["t"], obd["a_lat_g"])
    obd_yaw_g = np.interp(grid, obd["t"], obd["yaw_rate_deg_s"])
    obd_v_g = np.interp(grid, obd["t"], obd["v_ms"])
    can_steer_g = np.interp(grid, can_steer["t"] - offset_s, can_steer["value"])
    can_lat_g = np.interp(grid, can_lat["t"] - offset_s, can_lat["value"])
    can_yaw_g = np.interp(grid, can_yaw["t"] - offset_s, can_yaw["value"])
    can_v_g = np.interp(grid, can_speed["t"] - offset_s, can_speed["value"]) / 3.6

    moving = (obd_v_g > 3.0) & (can_v_g > 3.0)
    grid, obd_steer_g, obd_lat_g, obd_yaw_g = grid[moving], obd_steer_g[moving], obd_lat_g[moving], obd_yaw_g[moving]
    can_steer_g, can_lat_g, can_yaw_g = can_steer_g[moving], can_lat_g[moving], can_yaw_g[moving]

    # 5. CAN-interner Konsistenz-Check: a_lat kinematisch (v*omega) vs. RCM-Roh-a_lat
    can_lat_kinematic_g = can_v_g[moving] * np.radians(can_yaw_g) / 9.81
    can_internal = sign_corrected_fit(can_lat_kinematic_g, can_lat_g)

    # 6. Hauptvergleich: OBD-Modell vs. CAN
    steer_fit = sign_corrected_fit(obd_steer_g, can_steer_g)
    lat_fit = sign_corrected_fit(obd_lat_g, can_lat_g)
    yaw_fit = sign_corrected_fit(obd_yaw_g, can_yaw_g)

    def report(name, fit, unit):
        print(f"\n{name}: sign={fit['sign']:+.0f}  r={fit['r']:.3f}  "
              f"slope(OBD/CAN)={fit['slope_obd_per_can']:.3f}  RMSE={fit['rmse']:.3f}{unit}")

    print("\n=== CAN vs. OBD-Modell (n={} Samples, fahrend, {:.1f} min) ===".format(
        len(grid), (grid[-1] - grid[0]) / 60.0 if len(grid) else 0))
    report("Lenkwinkel (SteeringAngle_CAN vs. STEER_ANGL_EPS)", steer_fit, "deg")
    report("Querbeschleunigung (LateralAcc_CAN vs. Lenkwinkel-Modell)", lat_fit, "g")
    report("Gierrate (YawRate_CAN vs. Lenkwinkel-Modell)", yaw_fit, "deg/s")
    print(f"\nCAN-interne Konsistenz (v*YawRate_CAN kinematisch vs. LateralAcc_CAN roh): "
          f"sign={can_internal['sign']:+.0f}  r={can_internal['r']:.3f}  "
          f"slope={can_internal['slope_obd_per_can']:.3f}  RMSE={can_internal['rmse']:.3f}g")

    # 7. Plots
    os.makedirs(RESULTS_DIR, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True)
    t_min = grid / 60.0
    axes[0].plot(t_min, obd_steer_g, color="steelblue", lw=0.7, label="OBD STEER_ANGL_EPS (nullpunktkorrigiert)")
    axes[0].plot(t_min, steer_fit["sign"] * can_steer_g, color="darkorange", lw=0.7, alpha=0.8,
                 label=f"CAN SteeringAngle_CAN (Vorzeichen x{steer_fit['sign']:+.0f})")
    axes[0].set_ylabel("Lenkwinkel [deg]")
    axes[0].legend(fontsize=8, loc="upper right")
    axes[0].grid(alpha=0.3)
    axes[0].set_title(f"CAN vs. OBD-Lenkwinkelmodell — {CAN_LOG_ID} / {OBD_LOG_ID} "
                       f"(Zeitversatz {offset_s:.2f}s, r_speed={offset_r:.3f})")

    axes[1].plot(t_min, obd_lat_g, color="steelblue", lw=0.7, label="OBD-Modell a_lat")
    axes[1].plot(t_min, lat_fit["sign"] * can_lat_g, color="darkorange", lw=0.7, alpha=0.8,
                 label=f"CAN LateralAcc_CAN (Vorzeichen x{lat_fit['sign']:+.0f})")
    axes[1].set_ylabel("a_lat [g]")
    axes[1].legend(fontsize=8, loc="upper right")
    axes[1].grid(alpha=0.3)

    axes[2].plot(t_min, obd_yaw_g, color="steelblue", lw=0.7, label="OBD-Modell Gierrate")
    axes[2].plot(t_min, yaw_fit["sign"] * can_yaw_g, color="darkorange", lw=0.7, alpha=0.8,
                 label=f"CAN YawRate_CAN (Vorzeichen x{yaw_fit['sign']:+.0f})")
    axes[2].set_ylabel("Gierrate [deg/s]")
    axes[2].set_xlabel("Zeit [min]")
    axes[2].legend(fontsize=8, loc="upper right")
    axes[2].grid(alpha=0.3)
    plt.tight_layout()
    trace_path = os.path.join(RESULTS_DIR, "can_lateral_validation_traces.png")
    plt.savefig(trace_path, dpi=130)
    plt.close(fig)

    fig, axs = plt.subplots(1, 3, figsize=(15, 5))
    for ax, (name, obd_v, can_v, fit, unit) in zip(axs, [
        ("Lenkwinkel", obd_steer_g, can_steer_g, steer_fit, "deg"),
        ("a_lat", obd_lat_g, can_lat_g, lat_fit, "g"),
        ("Gierrate", obd_yaw_g, can_yaw_g, yaw_fit, "deg/s"),
    ]):
        ax.scatter(fit["sign"] * can_v, obd_v, s=3, alpha=0.15, color="steelblue")
        lim = max(np.abs(obd_v).max(), np.abs(can_v).max()) * 1.05
        ax.plot([-lim, lim], [-lim, lim], "--", color="gray", lw=0.8, label="y=x")
        ax.set_xlabel(f"CAN {name} (vorzeichenkorrigiert) [{unit}]")
        ax.set_ylabel(f"OBD-Modell {name} [{unit}]")
        ax.set_title(f"r={fit['r']:.3f}  slope={fit['slope_obd_per_can']:.2f}")
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    plt.tight_layout()
    scatter_path = os.path.join(RESULTS_DIR, "can_lateral_validation_scatter.png")
    plt.savefig(scatter_path, dpi=130)
    plt.close(fig)
    print(f"\nPlots: {trace_path}, {scatter_path}")

    out = {
        "can_log_id": CAN_LOG_ID, "obd_log_id": OBD_LOG_ID,
        "time_offset_s": offset_s, "time_offset_metadata_guess_s": guess_s,
        "time_offset_speed_correlation_r": offset_r,
        "n_samples_moving": int(len(grid)), "duration_min": float((grid[-1] - grid[0]) / 60.0) if len(grid) else 0.0,
        "steering_angle": steer_fit, "lateral_accel": lat_fit, "yaw_rate": yaw_fit,
        "can_internal_consistency_kinematic_vs_raw_lat_accel": can_internal,
    }
    with open(os.path.join(RESULTS_DIR, "can_lateral_validation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("Details: results/can_lateral_validation_summary.json")


if __name__ == "__main__":
    main()
