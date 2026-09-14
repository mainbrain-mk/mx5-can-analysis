"""
Kontinuierliche Quergrip-Schaetzung aus dem Lenkwinkel-Kanal
(STEER_ANGL_EPS/STEER_SPD_EPS, MX-5 Projekt)

Zweck: seit 01.09.2026 liegt in neuen Logs ein echter Lenkradwinkel-Kanal
vom Fahrzeug vor (siehe PROJEKT_STAND.md "Vier neue Logs vom 01.09.2026").
Bisher war eine KONTINUIERLICHE Querbeschleunigungsschaetzung nicht
moeglich (siehe grip_estimation.py/corner_event_analysis.py-Docstrings):
weder die IMU X/Z-Rotation noch das rohe Gyroskop (RotationRateY) sind
als Dauersignal brauchbar, weil das Halterungswackeln (~14 deg/s Std auf
dem Gyro, JETZT per Lenkwinkel-Ground-Truth bestaetigt - siehe unten) das
kleine echte Signal bei normaler Fahrt dominiert. corner_event_analysis.py
umgeht das, indem NUR wenige, eindeutig starke Kurven ausgewertet werden
(GPS-Kurs UND Gyro muessen uebereinstimmen UND deutlich ueber der
Rauschschwelle liegen) - pro mehrstuendiger Fahrt oft nur 0-5 Ereignisse.

Der Lenkwinkel kommt direkt vom Fahrzeug-EPS-System, nicht vom
Handy-Sensor - er hat KEIN Halterungswackel-Problem. Idee: ein einfaches
kinematisches (Ackermann-)Modell ohne Schwimmwinkel/Reifenschlupf-
Korrektur (fuer Strassenfahrt bei moderaten Querbeschleunigungen eine
uebliche erste Naeherung):

    Giergeschwindigkeit omega [rad/s] ~ k(Lenkwinkel) * Lenkwinkel[deg] * v[m/s]
    a_lat [m/s^2] = v * omega

k fasst Lenkuebersetzung UND Radstand (1/(Lenkuebersetzung*L)) in EINER
Groesse zusammen - beide sind aus den Logs nicht einzeln bekannt, aber
das Produkt laesst sich empirisch kalibrieren.

KALIBRIERUNG: gegen die bereits GPS+Gyro-kreuzvalidierten Kurven aus
`corner_event_analysis.py` (`results/corner_event_summary.json`),
eingeschraenkt auf Logs mit STEER_ANGL_EPS-Kanal. Lineare Regression
OHNE Achsenabschnitt (Lenkwinkel=0 -> Giergeschwindigkeit=0 ist
physikalisch zwingend) von `heading_rate_mean_deg_s` gegen
`-steer_mean_deg * v_mean_ms` (Minus, weil in diesem Fahrzeug negativer
Lenkwinkel = Rechtskurve, siehe Kreuzvalidierung in PROJEKT_STAND.md).

REFINEMENT 2026-09-07: k ist KEINE Konstante, sondern
`k(Lenkwinkel) = k1 + k2*|Lenkwinkel|` (k2 < 0). Ausgeloest durch die
Beobachtung, dass ein einzelnes globales k bei kleinen Lenkwinkeln/hohem
Tempo (Autobahn-Wedler) systematisch zu NIEDRIGE Gierrate vorhersagt,
bei grossen Lenkwinkeln/niedrigem Tempo (Parkmanoever) dagegen ganz gut
passt (Modell/GPS-Verhaeltnis wandert von ~0.93 bei >150° auf ~1.3-1.4
bei <20°, ueber ALLE Logs hinweg, nicht nur die neuen) - klassische
Reifenkraft-Saettigung: der effektive Lenk-zu-Gier-Gain sinkt mit
steigendem Lenkeinschlag/Querkraftbedarf. Kreuzvalidiert per LOO:
RMSE 3.90 -> 3.40 deg/s (~13% besser) gegenueber dem alten 1-Parameter-
Modell - eine echte, keine eingebildete Verbesserung. Siehe
[[mx5_steering_angle]] fuer die Herleitung.

EINSCHRAENKUNG (wichtig): aktuell nur EIN Log (`2026-09-01 152031`) hat
sowohl STEER_ANGL_EPS als auch bestaetigte Kurvenereignisse - macht
GENAU 5 Kalibrierpunkte, ALLE Rechtskurven (keine bestaetigte
Linkskurve in einem Lenkwinkel-Log verfuegbar). Die Symmetrie-Annahme
(dieselbe Konstante k gilt fuer Links- wie Rechtskurven) ist bei einem
EPS-Winkelsensor physikalisch gut begruendet (kein Grund fuer
Linksschief), aber NICHT empirisch geprueft. k selbst ist mit n=5 aus
einem einzigen Log entsprechend unsicher (siehe Kreuzvalidierungs-
Kennzahlen in der Ausgabe) - eine Neukalibrierung sobald weitere Logs
mit STEER_ANGL_EPS UND bestaetigten Kurven (idealerweise auch Links-
kurven) vorliegen wird dringend empfohlen.

Aufruf: python scripts/steering_lateral_model.py [log ...]
(Kalibrierung UND die JSON-Kennzahlen pro Log werden IMMER fuer ALLE
Logs mit Lenkwinkel-Kanal neu berechnet - k/R² koennen sich durch jedes
neue Log leicht verschieben, alte Werte waeren sonst inkonsistent. NUR
die Trace-PNG-Erzeugung (teuer, rein zur visuellen Kontrolle, wird von
keinem Auswertungsschritt gelesen) wird bei angegebenen Log-Namen auf
GENAU DIESE beschraenkt - ohne Argumente: wie bisher fuer alle Logs.)
"""
import os
import sys
import json
import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
CORNER_SUMMARY_PATH = "results/corner_event_summary.json"
ZERO_OFFSET_PATH = "results/steering_zero_offset.json"

MIN_SPEED_MS = 3.0
STRAIGHT_STEER_MAX_DEG = 10.0   # Schwelle fuer "Geradeausfahrt" (Rausch-Check)
G = 9.81

# Bestaetigte Kurven-Ereignisse, die trotz Kreuzvalidierung KEIN echtes
# Lenkmanoever sind und daher aus der Kalibrierung ausgeschlossen werden.
# (log_id, t_start) identifiziert das Ereignis in corner_event_summary.json.
# 2026-09-07 123731 t=1354.92-1355.92s: GPS-Fix-Sprung direkt nach Ausfahrt
# aus dem Forsthaustunnel (A111) erzeugte eine "Gierrate" von -14.8 deg/s
# aus der GPS-Heading-Rate, waehrend das rohe Gyro (RotationRateZ) und der
# korrigierte Lenkwinkel (+4.6 Grad) fast Geradeausfahrt zeigen - siehe
# results/gps_track_123731_t1355_anomaly.html und [[mx5_steering_angle]].
EXCLUDED_CALIBRATION_EVENTS = {
    ("2026-09-07 123731", 1354.9212065),
}


def load_zero_offsets(path=ZERO_OFFSET_PATH):
    """Pro-Log-Nullpunkt-Offset aus `steering_zero_offset.py`
    (OSM-Geradeausfahrt-Methode, siehe dortiger Docstring und
    PROJEKT_STAND.md "STEER_ANGL_EPS-Nullpunkt-Offset"). Nur Logs mit
    `trusted=True` (genug Samples) werden korrigiert - fehlt die Datei
    oder ein Log darin, wird Offset=0 angenommen (unveraendertes
    Verhalten)."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        log_id: res["offset_deg_median"]
        for log_id, res in data.items()
        if "error" not in res and res.get("trusted")
    }


def load_channel(con, log_id, channel):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()


def logs_with_steering(con):
    return con.execute(
        "SELECT DISTINCT log_id FROM measurements WHERE channel = 'STEER_ANGL_EPS' ORDER BY log_id"
    ).fetchdf()["log_id"].tolist()


def build_calibration_set(con, steering_logs, zero_offsets):
    """Kalibrierpunkte: bestaetigte Kurven (corner_event_analysis.py) aus
    Logs, die AUCH einen Lenkwinkel-Kanal haben. Fuer jedes Ereignis wird
    der mittlere Lenkwinkel im selben Zeitfenster direkt aus dem Datalake
    nachgezogen (nicht aus dem JSON, das den Lenkwinkel nicht kennt).
    Pro-Log-Nullpunkt-Offset (falls vorhanden) wird VOR der Mittelung
    abgezogen - siehe load_zero_offsets()."""
    with open(CORNER_SUMMARY_PATH, encoding="utf-8") as f:
        corner_data = json.load(f)

    points = []
    for entry in corner_data:
        log_id = entry["file"].removesuffix(".dlg")
        if log_id not in steering_logs:
            continue
        steer = load_channel(con, log_id, "STEER_ANGL_EPS")
        if len(steer) < 2:
            continue
        offset = zero_offsets.get(log_id, 0.0)
        for ev in entry["events"]:
            if (log_id, ev["t_start"]) in EXCLUDED_CALIBRATION_EVENTS:
                continue
            m = (steer["t"] >= ev["t_start"]) & (steer["t"] <= ev["t_end"])
            if m.sum() < 1:
                continue
            steer_mean = float(steer.loc[m, "value"].mean()) - offset
            v_ms = ev["speed_mean_kmh"] / 3.6
            points.append({
                "log_id": log_id, "t_start": ev["t_start"], "t_end": ev["t_end"],
                "direction": ev["direction"], "steer_mean_deg": steer_mean,
                "v_mean_ms": v_ms, "x": -steer_mean * v_ms,
                "heading_rate_mean_deg_s": ev["heading_rate_mean_deg_s"],
                "a_lat_mean_g_reference": ev["a_lat_mean_g"],
            })
    return points


def fit_k(points):
    """Gain als Funktion des Lenkwinkels, OHNE Achsenabschnitt in x:
    y = (k1 + k2*|Lenkwinkel|) * x. k1 ist der Gain nahe Geradeausfahrt,
    k2 (< 0 erwartet) die Saettigung: bestaetigt am 07.09.2026 (siehe
    PROJEKT_STAND.md / [[mx5_steering_angle]]), dass eine einzelne
    Konstante systematisch zu niedrige Gierrate bei kleinen Lenkwinkeln/
    hohem Tempo UND zu hohe bei grossen Lenkwinkeln/Parkmanoevern
    vorhersagt (Modell/GPS-Verhaeltnis wandert von ~0.93 bei >150°
    Lenkwinkel auf ~1.3-1.4 bei <20° Lenkwinkel) - klassische
    Reifenkraft-Saettigung bei groesserem Lenkeinschlag. Kreuzvalidiert
    ~13% niedrigeres LOO-RMSE als das alte 1-Parameter-Modell (3.90 ->
    3.40 deg/s)."""
    x = np.array([p["x"] for p in points])
    steer_abs = np.array([abs(p["steer_mean_deg"]) for p in points])
    y = np.array([p["heading_rate_mean_deg_s"] for p in points])
    X = np.column_stack([x, x * steer_abs])
    (k1, k2), *_ = np.linalg.lstsq(X, y, rcond=None)
    y_pred = k1 * x + k2 * x * steer_abs
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    return (float(k1), float(k2)), r2


def loo_cross_validate(points):
    """Leave-one-out: (k1, k2) jeweils ohne den zu testenden Punkt neu fitten."""
    errors = []
    for i in range(len(points)):
        rest = points[:i] + points[i + 1:]
        (k1_i, k2_i), _ = fit_k(rest)
        p = points[i]
        pred = (k1_i + k2_i * abs(p["steer_mean_deg"])) * p["x"]
        errors.append(pred - p["heading_rate_mean_deg_s"])
    errors = np.array(errors)
    return {
        "n": len(points),
        "rmse_deg_s": float(np.sqrt(np.mean(errors ** 2))),
        "mean_error_deg_s": float(np.mean(errors)),
        "errors_deg_s": errors.tolist(),
    }


def apply_continuous(con, log_id, k, zero_offsets):
    k1, k2 = k
    steer = load_channel(con, log_id, "STEER_ANGL_EPS")
    speed = load_channel(con, log_id, "VehicleSpeed")
    if len(steer) < 2 or len(speed) < 2:
        return None
    t = steer["t"].values
    steer_v = steer["value"].values - zero_offsets.get(log_id, 0.0)
    v_ms = np.interp(t, speed["t"].values, speed["value"].values) / 3.6

    yaw_rate_deg_s = (k1 + k2 * np.abs(steer_v)) * (-steer_v) * v_ms
    a_lat_ms2 = v_ms * np.radians(yaw_rate_deg_s)
    a_lat_g = a_lat_ms2 / G

    moving = v_ms > MIN_SPEED_MS
    straight = moving & (np.abs(steer_v) < STRAIGHT_STEER_MAX_DEG)

    return {
        "log_id": log_id, "t": t, "v_ms": v_ms, "steer_deg": steer_v,
        "yaw_rate_deg_s": yaw_rate_deg_s, "a_lat_g": a_lat_g,
        "moving_mask": moving, "straight_mask": straight,
    }


def summarize(trace):
    a = trace["a_lat_g"][trace["moving_mask"]]
    a_abs = np.abs(a)
    straight_a = np.abs(trace["a_lat_g"][trace["straight_mask"]])
    return {
        "log_id": trace["log_id"],
        "n_moving": int(trace["moving_mask"].sum()),
        "a_lat_abs_g_p50": float(np.percentile(a_abs, 50)),
        "a_lat_abs_g_p90": float(np.percentile(a_abs, 90)),
        "a_lat_abs_g_p95": float(np.percentile(a_abs, 95)),
        "a_lat_abs_g_p99": float(np.percentile(a_abs, 99)),
        "a_lat_abs_g_max": float(a_abs.max()) if len(a_abs) else None,
        "frac_time_above_0.3g": float(np.mean(a_abs > 0.3)),
        "frac_time_above_0.5g": float(np.mean(a_abs > 0.5)),
        "frac_time_above_0.7g": float(np.mean(a_abs > 0.7)),
        "straight_noise_floor_g_p95": float(np.percentile(straight_a, 95)) if len(straight_a) else None,
        "straight_noise_floor_g_std": float(straight_a.std()) if len(straight_a) else None,
    }


def plot_trace(trace, calibration_points, out_path):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    t_min = trace["t"] / 60.0
    ax1.plot(t_min, trace["v_ms"] * 3.6, color="steelblue", lw=0.8)
    ax1.set_ylabel("Geschwindigkeit [km/h]")
    ax1.grid(alpha=0.3)
    ax1.set_title(f"Kontinuierliche Quergrip-Schaetzung aus Lenkwinkel — {trace['log_id']}")

    ax2.plot(t_min, trace["a_lat_g"], color="darkorange", lw=0.6, label="a_lat (Lenkwinkel-Modell)")
    ax2.axhline(0, color="gray", lw=0.5)
    for p in calibration_points:
        if p["log_id"] == trace["log_id"]:
            ax2.axvspan(p["t_start"] / 60.0, p["t_end"] / 60.0, color="green", alpha=0.25)
    ax2.set_ylabel("a_lat [g]")
    ax2.set_xlabel("Zeit [min]")
    ax2.legend(loc="upper right")
    ax2.grid(alpha=0.3)
    if calibration_points:
        ax2.text(0.01, 0.02, "gruen schattiert = bestaetigte Kurve (Kalibrierpunkt)",
                  transform=ax2.transAxes, fontsize=8, color="dimgray")

    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_calibration(points, k, r2, out_path):
    k1, k2 = k
    x = np.array([p["x"] for p in points])
    y = np.array([p["heading_rate_mean_deg_s"] for p in points])
    steer_abs = np.array([abs(p["steer_mean_deg"]) for p in points])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))

    sc = ax1.scatter(x, y, c=steer_abs, cmap="viridis", s=40, zorder=3)
    fig.colorbar(sc, ax=ax1, label="|Lenkwinkel| [deg]")
    xs = np.linspace(0, max(x) * 1.1, 50)
    for s_ref, style in [(0, ":"), (steer_abs.max(), "--")]:
        ax1.plot(xs, (k1 + k2 * s_ref) * xs, style, color="tomato",
                  label=f"|Lenkwinkel|={s_ref:.0f}° (gain={k1 + k2 * s_ref:.5f})")
    ax1.set_xlabel("-Lenkwinkel[deg] * v[m/s]")
    ax1.set_ylabel("Gierrate GPS+Gyro-bestaetigt [deg/s]")
    ax1.set_title(f"Kalibrierung (R²={r2:.2f}, n={len(points)})")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    ratio = y / x
    ax2.scatter(steer_abs, ratio, color="seagreen", s=30, alpha=0.7, zorder=3)
    s_line = np.linspace(0, steer_abs.max(), 50)
    ax2.plot(s_line, k1 + k2 * s_line, "--", color="tomato",
              label=f"gain(|Lenkwinkel|) = {k1:.5f} + {k2:.7f}*|Lenkwinkel|")
    ax2.set_xlabel("|Lenkwinkel| [deg]")
    ax2.set_ylabel("empirischer Gain y/x [1/(deg*s/m)]")
    ax2.set_title("Saettigung: Gain sinkt mit Lenkwinkel")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    plot_only = set(sys.argv[1:])  # leer = alle plotten (bisheriges Verhalten)
    con = duckdb.connect(DB_PATH, read_only=True)
    steering_logs = logs_with_steering(con)
    print(f"Logs mit STEER_ANGL_EPS-Kanal: {len(steering_logs)}")
    for l in steering_logs:
        print(f"  {l}")

    zero_offsets = load_zero_offsets()
    if zero_offsets:
        print(f"\nNullpunkt-Korrektur (aus {ZERO_OFFSET_PATH}) fuer {len(zero_offsets)} Log(s):")
        for l in steering_logs:
            off = zero_offsets.get(l)
            print(f"  {l}: {off:+.1f}°" if off is not None else f"  {l}: kein Offset (0.0°)")
    else:
        print(f"\nKeine Nullpunkt-Offset-Datei gefunden ({ZERO_OFFSET_PATH}) - "
              "unkorrigierte Rohwerte werden verwendet.")

    points = build_calibration_set(con, steering_logs, zero_offsets)
    print(f"\nKalibrierpunkte (bestaetigte Kurven in Lenkwinkel-Logs): {len(points)}")
    if len(points) < 3:
        print("Zu wenige Kalibrierpunkte - Abbruch.")
        return

    k, r2 = fit_k(points)
    loo = loo_cross_validate(points)
    print(f"Kalibrierung: k1={k[0]:.6f}  k2={k[1]:.7f}  R²={r2:.3f}")
    print(f"Leave-one-out Kreuzvalidierung: RMSE={loo['rmse_deg_s']:.2f} deg/s "
          f"(mittlerer Fehler={loo['mean_error_deg_s']:+.2f} deg/s), n={loo['n']}")
    for p, err in sorted(zip(points, loo["errors_deg_s"]), key=lambda pe: -abs(pe[1])):
        print(f"  {p['log_id']} t={p['t_start']:.0f}-{p['t_end']:.0f}s {p['direction']:6s} "
              f"v={p['v_mean_ms']*3.6:.0f}km/h  Lenkwinkel={p['steer_mean_deg']:+.1f}°  "
              f"Gierrate_gemessen={p['heading_rate_mean_deg_s']:+.1f}°/s  LOO-Fehler={err:+.2f}°/s")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    plot_calibration(points, k, r2, os.path.join(RESULTS_DIR, "steering_lateral_calibration.png"))

    all_summaries = []
    for log_id in steering_logs:
        trace = apply_continuous(con, log_id, k, zero_offsets)
        if trace is None:
            print(f"\n{log_id}: keine ausreichenden Daten (Lenkwinkel/Speed)")
            continue
        summary = summarize(trace)
        all_summaries.append(summary)
        print(f"\n{log_id}: n_fahrend={summary['n_moving']}  "
              f"|a_lat| p50={summary['a_lat_abs_g_p50']:.2f}g p90={summary['a_lat_abs_g_p90']:.2f}g "
              f"p99={summary['a_lat_abs_g_p99']:.2f}g max={summary['a_lat_abs_g_max']:.2f}g")
        print(f"  Rauschboden Geradeausfahrt (|Lenkwinkel|<{STRAIGHT_STEER_MAX_DEG:.0f}°): "
              f"p95={summary['straight_noise_floor_g_p95']:.3f}g  std={summary['straight_noise_floor_g_std']:.3f}g")
        if plot_only and log_id not in plot_only:
            continue
        plot_path = os.path.join(RESULTS_DIR, f"steering_lateral_{log_id.replace(' ', '_').replace(':', '')}_trace.png")
        plot_trace(trace, points, plot_path)
        print(f"  Plot: {plot_path}")

    out = {
        "k": k, "r2": r2, "loo_cross_validation": loo,
        "calibration_points": points, "log_summaries": all_summaries,
    }
    with open(os.path.join(RESULTS_DIR, "steering_lateral_model_summary.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: results/steering_lateral_model_summary.json")
    print("Kalibrierplot: results/steering_lateral_calibration.png")


if __name__ == "__main__":
    main()
