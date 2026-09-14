"""
Kurvenradius aus dem Lenkwinkel abschaetzen - Genauigkeitscheck (MX-5 Projekt)

Nutzerfrage: "kannst du aus dem lenkwinkel daten den kurvenradius
abschaetzen, oder reicht dafuer die genauigkeit nicht?"

Herleitung (aus dem bestehenden kinematischen Modell in
`steering_lateral_model.py`, siehe dortiger Docstring):
    Giergeschwindigkeit omega[deg/s] = k * Lenkwinkel[deg] * v[m/s]
Fuer eine stationaere Kurve (coordinated turn, kein Schwimmwinkel) gilt
omega[rad/s] = v/R, also:
    R = v / omega_rad = v / radians(k*Lenkwinkel*v) = 1 / radians(k*Lenkwinkel)
**Der Radius haengt in diesem Modell NICHT von v ab** - eine direkte Folge
davon, dass omega linear in v angesetzt wurde (reines Ackermann-/
Bicycle-Modell ohne Schwimmwinkel-/Untersteuerungsterm). Das ist
plausibel (Radius ist bei reinem Kinematik-Modell eine geometrische
Groesse), aber die bereits dokumentierte "Untersteuerungs-Signatur"
(zwei Geschwindigkeitscluster ~40/~65 km/h passen nicht perfekt auf
dieselbe Gerade, siehe PROJEKT_STAND.md) deutet an, dass reale Autos bei
gleichem Lenkwinkel mit steigender Geschwindigkeit einen GROESSEREN
Radius fahren (Reifenschlupf) - dieses einfache Modell kann das nicht
abbilden.

Dieses Skript beantwortet die Genauigkeitsfrage EMPIRISCH statt nur
theoretisch: fuer alle 33 Kalibrierpunkte aus
`steering_lateral_model.py` (bestaetigte Kurven aus
`corner_event_analysis.py`, GPS+Gyro-kreuzvalidiert - UNABHAENGIG vom
Lenkwinkel) wird
    R_gemessen = v / radians(|heading_rate_mean_deg_s|)     (GPS+Gyro)
gegen
    R_lenkwinkel = 1 / radians(k*|Lenkwinkel|)               (nur Lenkwinkel)
verglichen. Das ist derselbe Datensatz, mit dem k kalibriert wurde (keine
neue Kalibrierung), aber in Radius- statt Gierraten-Einheiten ausgedrueckt
- fuer die eigentliche Anwendungsfrage ("wie gut ist der Radius") ist das
die relevante Groesse, nicht die Gierraten-Genauigkeit selbst (der
Zusammenhang ist nichtlinear: R=1/omega, kleine Gierraten -> ueberproportional
grosser Radius-Fehler).

Zusaetzlich geprueft:
  - Aufloesung/Quantisierung von STEER_ANGL_EPS (waere bei sehr flachen
    Kurven/grossen Radien der limitierende Faktor, falls grob).
  - Restkorrelation des Radius-Fehlers mit Geschwindigkeit und mit dem
    Lenkwinkel selbst (Untersteuerungs-Hinweis, Rauschcharakteristik).
  - Abdeckungsbereich: welche Radien/Geschwindigkeiten sind durch die
    Kalibrierbasis ueberhaupt geprueft (Extrapolationswarnung fuer alles
    ausserhalb).

Aufruf: python scripts/steering_radius_estimate.py
Ergebnis: results/steering_radius_estimate.json,
          results/steering_radius_estimate.png
"""
import os
import json

import numpy as np
import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS_DIR = "results"
STEERING_SUMMARY_PATH = os.path.join(RESULTS_DIR, "steering_lateral_model_summary.json")
DB_PATH = "data/datalake.duckdb"


def check_resolution(con, log_ids):
    """Kleinster von Null verschiedener Wertabstand von STEER_ANGL_EPS -
    Quantisierungsstufe des Sensors/PIDs."""
    steps = []
    for log_id in log_ids:
        df = con.execute(
            "SELECT DISTINCT value FROM measurements "
            "WHERE log_id = ? AND channel = 'STEER_ANGL_EPS' ORDER BY value",
            [log_id],
        ).fetchdf()
        v = np.sort(df["value"].values)
        d = np.diff(v)
        d = d[d > 1e-6]
        if len(d):
            steps.append(float(d.min()))
    return {"resolution_deg": min(steps) if steps else None, "checked_logs": len(steps)}


def radius_comparison(k, calibration_points):
    rows = []
    for p in calibration_points:
        v_ms = p["v_mean_ms"]
        steer = abs(p["steer_mean_deg"])
        omega_meas_deg_s = abs(p["heading_rate_mean_deg_s"])
        if steer <= 0 or omega_meas_deg_s <= 0:
            continue
        r_meas = v_ms / np.radians(omega_meas_deg_s)
        r_steer = 1.0 / np.radians(k * steer)
        rel_err = (r_steer - r_meas) / r_meas
        rows.append({
            "log_id": p["log_id"], "direction": p["direction"],
            "v_kmh": v_ms * 3.6, "steer_deg": steer,
            "r_measured_m": r_meas, "r_from_steering_m": r_steer,
            "rel_error": rel_err,
        })
    return rows


def summarize(rows):
    err = np.array([r["rel_error"] for r in rows])
    r_meas = np.array([r["r_measured_m"] for r in rows])
    v = np.array([r["v_kmh"] for r in rows])
    steer = np.array([r["steer_deg"] for r in rows])
    return {
        "n": len(rows),
        "median_abs_rel_error": float(np.median(np.abs(err))),
        "p90_abs_rel_error": float(np.percentile(np.abs(err), 90)),
        "max_abs_rel_error": float(np.max(np.abs(err))),
        "r_measured_range_m": [float(r_meas.min()), float(r_meas.max())],
        "v_range_kmh": [float(v.min()), float(v.max())],
        "corr_error_vs_speed": float(np.corrcoef(err, v)[0, 1]),
        "corr_error_vs_radius": float(np.corrcoef(err, r_meas)[0, 1]),
        "corr_abs_error_vs_steer_angle": float(np.corrcoef(np.abs(err), steer)[0, 1]),
    }


def plot(rows, summary, out_path):
    r_meas = np.array([r["r_measured_m"] for r in rows])
    r_steer = np.array([r["r_from_steering_m"] for r in rows])
    v = np.array([r["v_kmh"] for r in rows])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.5))

    sc = ax1.scatter(r_meas, r_steer, c=v, cmap="viridis", s=40, zorder=3)
    lim = max(r_meas.max(), r_steer.max()) * 1.1
    ax1.plot([0, lim], [0, lim], "--", color="gray", lw=1, label="perfekte Uebereinstimmung")
    ax1.set_xlabel("R gemessen (GPS+Gyro, unabhaengig) [m]")
    ax1.set_ylabel("R aus Lenkwinkel [m]")
    ax1.set_title(f"Radius-Vergleich (n={summary['n']}, "
                  f"Median-Fehler {summary['median_abs_rel_error']*100:.0f}%)")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    cb = plt.colorbar(sc, ax=ax1)
    cb.set_label("v [km/h]")

    err_pct = np.array([r["rel_error"] for r in rows]) * 100
    ax2.scatter(r_meas, err_pct, c=v, cmap="viridis", s=40, zorder=3)
    ax2.axhline(0, color="gray", lw=1)
    ax2.set_xlabel("R gemessen [m]")
    ax2.set_ylabel("relativer Fehler R_lenkwinkel [%]")
    ax2.set_title("Fehler vs. Radius (keine Kalibrierdaten > "
                  f"{summary['r_measured_range_m'][1]:.0f}m!)")
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    with open(STEERING_SUMMARY_PATH, encoding="utf-8") as f:
        steering_data = json.load(f)
    k = steering_data["k"]
    calibration_points = steering_data["calibration_points"]

    con = duckdb.connect(DB_PATH, read_only=True)
    log_ids = sorted({p["log_id"] for p in calibration_points})
    resolution = check_resolution(con, log_ids)
    print(f"STEER_ANGL_EPS Aufloesung: {resolution['resolution_deg']}° "
          f"(geprueft an {resolution['checked_logs']} Logs)")

    rows = radius_comparison(k, calibration_points)
    summary = summarize(rows)

    print(f"\nk = {k:.6f}")
    print(f"{'log':22s} {'v[km/h]':>8s} {'steer[deg]':>10s} {'R_meas[m]':>10s} "
          f"{'R_steer[m]':>10s} {'rel.err':>8s}")
    for r in rows:
        print(f"{r['log_id']:22s} {r['v_kmh']:8.1f} {r['steer_deg']:10.1f} "
              f"{r['r_measured_m']:10.1f} {r['r_from_steering_m']:10.1f} {r['rel_error']*100:7.1f}%")

    print(f"\nn={summary['n']}  Median |rel.Fehler|={summary['median_abs_rel_error']*100:.1f}%  "
          f"p90={summary['p90_abs_rel_error']*100:.1f}%  max={summary['max_abs_rel_error']*100:.1f}%")
    print(f"Abgedeckter Radiusbereich (Kalibrierbasis): "
          f"{summary['r_measured_range_m'][0]:.1f} - {summary['r_measured_range_m'][1]:.1f} m")
    print(f"Abgedeckter Geschwindigkeitsbereich: "
          f"{summary['v_range_kmh'][0]:.1f} - {summary['v_range_kmh'][1]:.1f} km/h")
    print(f"corr(Fehler, v) = {summary['corr_error_vs_speed']:+.2f}  "
          f"corr(Fehler, R) = {summary['corr_error_vs_radius']:+.2f}  "
          f"corr(|Fehler|, Lenkwinkel) = {summary['corr_abs_error_vs_steer_angle']:+.2f}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = os.path.join(RESULTS_DIR, "steering_radius_estimate.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"k": k, "resolution": resolution, "rows": rows, "summary": summary}, f,
                   indent=2, ensure_ascii=False)
    out_png = os.path.join(RESULTS_DIR, "steering_radius_estimate.png")
    plot(rows, summary, out_png)
    print(f"\nDetails: {out_json}")
    print(f"Plot: {out_png}")


if __name__ == "__main__":
    main()
