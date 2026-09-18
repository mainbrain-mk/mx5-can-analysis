"""
Teillast-Gas-Kalibrierung an der Haftgrenze (MX-5 Projekt)

Zweck: erste, GROBE Kalibrierung des Zusammenhangs zwischen Gaspedal-
stellung (APP %) und genutzter Querkraft (a_lat / a_lat_max), aus zwei
real gefahrenen, schnellen Kurven im Nachmittagslog vom 08.09.2026
(`data/raw/2026-09-08 165347.dlg`, Fenster t=2368-2394s ab Logstart -
Nutzer-Screenshot "Rechtskurve 1 (17:33:24-17:33:31)" + direkt
anschliessende zweite, sehr enge Kurve, vom Nutzer bestaetigt).

WICHTIG (Nutzerentscheidung 08.09.2026): dies ist eine grobe Kalibrierung
mit NUR diesen zwei Kurven, KEINE systematische Aggregation ueber alle
Logs (das waere die naechste, robustere Ausbaustufe, analog
braking_model.py mit 229 Ereignissen - hier bewusst nicht gemacht).
Ergebnis ist ein erster Anker-/Richtwert fuer eine spaetere Verfeinerung
des Teillast-Modells in spreewaldring_racing_line_editor.py
(smoothWastedAccelBrake/coastAccel), NICHT eine belastbare Funktion.

Datenquellen:
- a_lat aus dem Lenkwinkel-Modell (STEER_ANGL_EPS, k1/k2 aus
  results/steering_lateral_model_summary.json, Nullpunkt-Offset aus
  results/steering_zero_offset.json) - EPS-Kanal, kein
  Halterungswackel-Problem, siehe steering_lateral_model.py.
- a_lat_max als mu*G (mu=1.0, konservative Reifenannahme wie im
  interaktiven Editor, siehe [[mx5_tires]]).
- GPS (Laenge/Breite) nur zur Kurvenradius-Gegenprobe (v^2/a_lat vs.
  geometrischer Radius aus der GPS-Spur) - kein OSM-Abgleich noetig, da
  der Nutzer die zweite Kurve bereits bestaetigt hat.

WICHTIGER NACHTRAG (Nutzerhinweis 08.09.2026): Kurve 2 (t~2387-2391s) ist
KEIN normales Kurvenfahren, sondern ein Abbiegevorgang am absoluten Limit,
bei dem die Datenrate zu gering ist, um die tatsaechliche Dynamik
aufzuloesen - UND am Ende des Abbiegens musste ein Haftungsverlust an der
Hinterachse (Uebersteuern) abgefangen werden. Das Gegenlenken dabei ist
ein REAKTIVES Lenksignal (Reaktion auf das Ausbrechen des Hecks), kein
bewusstes Oeffnen der Linie wie in Kurve 1 - die Lenkrate-Gas-Kopplung aus
Kurve 1 gilt fuer dieses Manoever nicht und darf nicht mit Kurve 1 gepoolt
werden. Kurve 2 bleibt in den Rohdaten/im Plot sichtbar, wird aber NICHT
mehr in die eigentliche Kalibrierungsaussage einbezogen - siehe
CLEAN_CORNER_WINDOW unten. Nur Kurve 1 gilt als repraesentatives Beispiel
fuer "bewusstes Oeffnen der Lenkung als Gas-Freigabe".

Ausserdem (Nutzerhinweis 08.09.2026): der Ausreisser bei t~2372.75s
(APP=72% bei praktisch Geradeausfahrt) ist ein Zwischengas-Blip beim
Runterschalten, keine kurvenbezogene Gasgabe - siehe
EXCLUDED_DOWNSHIFT_BLIP_WINDOW unten, wird aus der Analyse ausgeschlossen.

Aufruf: .venv/bin/python scripts/partial_throttle_calibration.py
"""
import json
import os

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
LOG_ID = "2026-09-08 165347"
T_START, T_END = 2368.0, 2394.0

# Kurve 1 (t~2378-2384s) ist ein "sauberes" Kurvenfahren am Limit ohne
# Korrektur - die einzige Stelle in diesem Fenster, die als Kalibrier-
# Beispiel fuer die Lenkrate-Gas-Kopplung taugt (siehe Modul-Docstring,
# Nutzerhinweis 08.09.2026 zu Kurve 2: Abbiegen am Limit + Hinterachs-
# Rutscher, reaktives Gegenlenken, nicht vergleichbar).
CLEAN_CORNER_WINDOW = (2378.0, 2385.0)

STEER_MODEL_PATH = os.path.join(RESULTS_DIR, "steering_lateral_model_summary.json")
ZERO_OFFSET_PATH = os.path.join(RESULTS_DIR, "steering_zero_offset.json")

G = 9.81
MU = 1.0                 # konservative Reifenannahme, wie im interaktiven Editor
BRAKE_THRESH_KPA = 100.0  # darunter gilt der Punkt als "nicht bremsend"


def load_channel(con, channel):
    df = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND t_elapsed_s BETWEEN ? AND ? "
        "AND value IS NOT NULL ORDER BY t_elapsed_s",
        [LOG_ID, channel, T_START, T_END],
    ).fetchdf()
    return df["t"].values, df["value"].values


def main():
    with open(STEER_MODEL_PATH, encoding="utf-8") as f:
        k1, k2 = json.load(f)["k"]
    with open(ZERO_OFFSET_PATH, encoding="utf-8") as f:
        offsets = json.load(f)
    offset = offsets[LOG_ID]["offset_deg_median"]
    print(f"Lenkwinkelmodell: k1={k1:.6f}, k2={k2:.8f}, Nullpunkt-Offset={offset}deg")

    con = duckdb.connect(DB_PATH, read_only=True)
    t_app, app = load_channel(con, "APP")
    t_brk, brk = load_channel(con, "BFP_PRE_MZ")
    t_steer, steer_raw = load_channel(con, "STEER_ANGL_EPS")
    t_v, v_kmh = load_channel(con, "VehicleSpeed")
    t_lat, lat = load_channel(con, "Breite")
    t_lon, lon = load_channel(con, "Länge")
    con.close()

    # Gemeinsames Zeitraster: die APP-Zeitstempel (grobste Aufloesung),
    # alle anderen Kanaele per linearer Interpolation darauf gezogen.
    t = t_app
    v_ms = np.interp(t, t_v, v_kmh) / 3.6
    steer = np.interp(t, t_steer, steer_raw) - offset
    brake_kpa = np.interp(t, t_brk, brk)

    yaw_rate_deg_s = (k1 + k2 * np.abs(steer)) * (-steer) * v_ms
    a_lat_ms2 = v_ms * np.radians(yaw_rate_deg_s)
    a_lat_g = a_lat_ms2 / G
    a_lat_max_g = MU

    used_grip = np.abs(a_lat_g) / a_lat_max_g
    not_braking = brake_kpa < BRAKE_THRESH_KPA
    gassing = app > 0.5

    # Nutzerhinweis 08.09.2026: der Ausreisser bei t~2372.75s (APP=72% bei
    # praktisch Geradeausfahrt, Lenkwinkel -1.9deg) ist ein Zwischengas-
    # Blip beim Runterschalten, keine kurvenbezogene Gasgabe - gehoert
    # nicht in dieses Modell und wird ausgeschlossen.
    EXCLUDED_DOWNSHIFT_BLIP_WINDOW = (2372.0, 2373.5)
    not_shift_blip = ~((t >= EXCLUDED_DOWNSHIFT_BLIP_WINDOW[0]) & (t <= EXCLUDED_DOWNSHIFT_BLIP_WINDOW[1]))

    mask = not_braking & gassing & not_shift_blip
    n = int(mask.sum())
    print(f"Punkte mit Gas>0 und ohne Bremsung im Fenster: {n} (von {len(t)})")

    x = used_grip[mask]
    y = app[mask]
    for ti, xi, yi, vi in zip(t[mask], x, y, v_ms[mask]):
        print(f"  t={ti:8.2f}s  v={vi*3.6:5.1f}km/h  genutzte Querkraft={xi:5.2f}  APP={yi:5.1f}%")

    # Einfache lineare Regression APP% ~ a + b*used_grip (mit Achsenabschnitt,
    # bewusst kein erzwungener Nulldurchgang - bei nur zwei Kurven ist das
    # nicht zu rechtfertigen).
    A = np.vstack([np.ones_like(x), x]).T
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    y_pred = a + b * x
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    app_at_limit = a + b * 1.0
    print(f"\nRegression: APP% = {a:.1f} + {b:.1f} * genutzte_Querkraft   (R^2={r2:.2f}, n={n})")
    print(f"APP% bei voll genutzter Querkraft (Faktor 1.0): {app_at_limit:.1f}%")

    # NACHTRAG (Nutzerhinweis 08.09.2026): die momentane Querkraft allein
    # erklaert nicht, warum Gas beim Zudrehen (Halten) niedrig, beim
    # Rausbeschleunigen (Oeffnen) trotz aehnlich hoher Querkraft sofort
    # hoch ist - das war schon beim Verbinden der Fixpunkte die Kernidee:
    # "wir bleiben innen, bis uns die Kraefte zwingen, die Lenkung zu
    # oeffnen" (docs/logs/projekt-stand.md, "Nachtrag: mehrere nahe Fixpunkte").
    # Die eigentliche Freigabe fuer Gas ist demnach nicht der Momentanwert
    # von a_lat, sondern die LENKRATE (d Lenkwinkel/dt) - solange der
    # Fahrer noch zudreht oder haelt (Rate ~0 oder weiter zudrehend), bleibt
    # die Kurve eng und das Gas partiell; sobald er zurueckdreht (Rate
    # deutlich positiv in einer Rechtskurve = Lenkwinkel wird weniger
    # negativ), oeffnet sich der Radius und damit die verfuegbare
    # Laengskraft - unabhaengig davon, ob a_lat in diesem Moment noch hoch
    # ist (die Gierrate/a_lat reagiert traege auf die Lenkbewegung).
    steer_rate_deg_s = np.gradient(steer, t)

    def fit_steer_rate(sub_mask, label):
        xs, ys = steer_rate_deg_s[sub_mask], app[sub_mask]
        if len(xs) < 3:
            print(f"\n{label}: zu wenig Punkte (n={len(xs)})")
            return None
        A_ = np.vstack([np.ones_like(xs), xs]).T
        (a_, b_), *_ = np.linalg.lstsq(A_, ys, rcond=None)
        pred = a_ + b_ * xs
        r2_ = 1 - np.sum((ys - pred) ** 2) / np.sum((ys - ys.mean()) ** 2)
        print(f"\n{label}: APP% = {a_:.1f} + {b_:.1f} * Lenkrate[deg/s]   (R^2={r2_:.2f}, n={len(xs)})")
        return {"intercept_app_pct": float(a_), "slope_app_pct_per_deg_s": float(b_), "r2": float(r2_), "n": len(xs)}

    print("(Lenkrate >0 in einer Rechtskurve = Lenkung wird zurueckgedreht/geoeffnet)")
    for ti, si, ri, yi in zip(t[mask], steer[mask], steer_rate_deg_s[mask], app[mask]):
        tag = "  [Kurve 2, Abbiegen+Rutscher - NICHT repraesentativ]" if 2387 <= ti <= 2391 else ""
        print(f"  t={ti:8.2f}s  Lenkwinkel={si:6.1f}deg  Lenkrate={ri:7.1f}deg/s  APP={yi:5.1f}%{tag}")

    clean_mask = mask & (t >= CLEAN_CORNER_WINDOW[0]) & (t <= CLEAN_CORNER_WINDOW[1])
    reg_pooled = fit_steer_rate(mask, "Regression (gepoolt, beide Kurven - NICHT die Kalibrierungsaussage)")
    reg_clean = fit_steer_rate(clean_mask, "Regression (NUR Kurve 1, sauber - eigentliche Kalibrierungsaussage)")

    # GPS-Radius-Gegenprobe: v^2/a_lat als grober Kurvenradius, verglichen
    # mit dem geometrischen Radius aus drei GPS-Punkten je Scheitelbereich.
    lat_i = np.interp(t, t_lat, lat)
    lon_i = np.interp(t, t_lon, lon)
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * np.cos(np.radians(np.mean(lat_i)))
    x_m = (lon_i - lon_i[0]) * m_per_deg_lon
    y_m = (lat_i - lat_i[0]) * m_per_deg_lat

    def circle_radius(i0, i1):
        xs, ys = x_m[i0:i1], y_m[i0:i1]
        if len(xs) < 5:
            return None
        A_ = np.vstack([xs, ys, np.ones_like(xs)]).T
        b_ = xs**2 + ys**2
        sol, *_ = np.linalg.lstsq(A_, b_, rcond=None)
        cx, cy = sol[0] / 2, sol[1] / 2
        r = np.sqrt(sol[2] + cx**2 + cy**2)
        return float(r)

    apex1 = (t > 2379) & (t < 2384)
    apex2 = (t > 2387) & (t < 2390)
    r_corner1 = circle_radius(*np.where(apex1)[0][[0, -1]]) if apex1.sum() >= 5 else None
    r_corner2 = circle_radius(*np.where(apex2)[0][[0, -1]]) if apex2.sum() >= 5 else None
    v1 = np.mean(v_ms[apex1]) if apex1.sum() else float("nan")
    v2 = np.mean(v_ms[apex2]) if apex2.sum() else float("nan")
    print(f"\nGPS-Radius Kurve 1 (Scheitel ~2379-2384s): R~{r_corner1}m, v_mean={v1*3.6:.1f}km/h, "
          f"a_lat=v^2/R={v1**2/r_corner1/G if r_corner1 else float('nan'):.2f}g" if r_corner1 else "\nGPS-Radius Kurve 1: zu wenig Punkte")
    print(f"GPS-Radius Kurve 2 (Scheitel ~2387-2390s): R~{r_corner2}m, v_mean={v2*3.6:.1f}km/h, "
          f"a_lat=v^2/R={v2**2/r_corner2/G if r_corner2 else float('nan'):.2f}g" if r_corner2 else "GPS-Radius Kurve 2: zu wenig Punkte")

    # Plot
    fig, axes = plt.subplots(4, 1, figsize=(10, 12), sharex=False)
    ax = axes[0]
    ax.scatter(x, y, c=t[mask], cmap="viridis")
    xs_fit = np.linspace(0, max(x.max(), 1.0), 50)
    ax.plot(xs_fit, a + b * xs_fit, "r--", label=f"APP%={a:.0f}+{b:.0f}*x (R^2={r2:.2f})")
    ax.set_xlabel("genutzte Querkraft |a_lat|/(mu*g)")
    ax.set_ylabel("Gaspedal APP %")
    ax.set_title(f"Teillast-Kalibrierung, {LOG_ID}, t={T_START}-{T_END}s (n={n})")
    ax.legend()
    ax.grid(alpha=0.3)

    ax2 = axes[1]
    ax2.plot(t, a_lat_g, label="a_lat (Lenkwinkelmodell) [g]")
    ax2.plot(t, app / 100 * a_lat_max_g, label="APP/100 * mu*g (zum Vergleich)", alpha=0.6)
    ax2b = ax2.twinx()
    ax2b.plot(t, app, color="green", alpha=0.4, label="APP %")
    ax2.set_xlabel("t seit Logstart [s]")
    ax2.set_ylabel("a_lat [g]")
    ax2b.set_ylabel("APP %")
    ax2.legend(loc="upper left")
    ax2b.legend(loc="upper right")
    ax2.grid(alpha=0.3)

    # Phasenbild: Lenkwinkel + Lenkrate vs. Gaspedal ueber der Zeit - zeigt
    # direkt, ob das Gas mit dem ZURUECKDREHEN (Oeffnen) der Lenkung
    # zusammenfaellt statt mit dem Momentanwert von a_lat (siehe Nachtrag
    # oben).
    ax3 = axes[2]
    ax3.plot(t, steer, color="brown", label="Lenkwinkel korrigiert [deg]")
    ax3b = ax3.twinx()
    ax3b.plot(t, app, color="green", alpha=0.5, label="APP %")
    ax3.axhline(0, color="grey", lw=0.6)
    ax3.set_xlabel("t seit Logstart [s]")
    ax3.set_ylabel("Lenkwinkel [deg] (neg.=rechts)")
    ax3b.set_ylabel("APP %")
    ax3.legend(loc="upper left")
    ax3b.legend(loc="upper right")
    ax3.grid(alpha=0.3)

    ax4 = axes[3]
    ax4.plot(t, steer_rate_deg_s, color="purple", label="Lenkrate [deg/s] (>0 = Rechtskurve oeffnet sich)")
    ax4.axhline(0, color="grey", lw=0.6)
    ax4b = ax4.twinx()
    ax4b.plot(t, app, color="green", alpha=0.5, label="APP %")
    ax4.set_xlabel("t seit Logstart [s]")
    ax4.set_ylabel("Lenkrate [deg/s]")
    ax4b.set_ylabel("APP %")
    ax4.legend(loc="upper left")
    ax4b.legend(loc="upper right")
    ax4.grid(alpha=0.3)

    fig.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "partial_throttle_calibration.png")
    fig.savefig(out_png, dpi=130)
    print(f"\nPlot gespeichert: {out_png}")

    out_json = os.path.join(RESULTS_DIR, "partial_throttle_calibration.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "log_id": LOG_ID, "t_start": T_START, "t_end": T_END, "n_points": n,
            "regression": {"intercept_app_pct": float(a), "slope_app_pct_per_grip": float(b),
                            "r2": float(r2), "app_pct_at_full_grip": float(app_at_limit)},
            "regression_steer_rate_pooled_NOT_representative": reg_pooled,
            "regression_steer_rate_clean_corner1": reg_clean,
            "clean_corner_window": CLEAN_CORNER_WINDOW,
            "corner2_excluded_reason": "Abbiegen am Limit, zu geringe Datenrate, "
                                        "Hinterachs-Haftungsverlust mit reaktivem Gegenlenken "
                                        "am Ende - nicht repraesentativ fuer bewusstes Lenkungs-"
                                        "Oeffnen (Nutzerhinweis 08.09.2026)",
            "gps_radius_check": {"corner1_r_m": r_corner1, "corner2_r_m": r_corner2},
            "points": [{"t": float(ti), "used_grip": float(xi), "app_pct": float(yi),
                        "steer_rate_deg_s": float(ri), "steer_deg": float(si)}
                       for ti, xi, yi, ri, si in zip(t[mask], x, y, steer_rate_deg_s[mask], steer[mask])],
        }, f, indent=2, ensure_ascii=False)
    print(f"Zusammenfassung gespeichert: {out_json}")


if __name__ == "__main__":
    main()
