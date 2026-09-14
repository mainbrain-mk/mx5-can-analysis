"""
Breite Kalibrierung: Lenkrate -> Gaspedal (APP%), aggregiert ueber ALLE
16 Logs mit STEER_ANGL_EPS (MX-5 Projekt).

Zweck: partial_throttle_calibration.py fand an EINER sauberen Strassenkurve
(R=44.5m, 75km/h) einen starken Zusammenhang zwischen Lenkrate (Oeffnen
der Lenkung) und Gaspedalstellung (R^2=0.76). Eine Plausibilitaetspruefung
gegen die Spreewaldring-Bestlinie (spreewaldring_natural_throttle_check.py)
zeigte aber: die dortigen Kurven brauchen Lenkraten weit ausserhalb des
kalibrierten Bereichs (bis 392 deg/s vs. kalibrierte -14 bis +24 deg/s) -
die Ein-Kurven-Formel darf nicht extrapoliert werden.

Nutzerentscheidung 08.09.2026: KEINE Spreewaldring-eigenen Logs verwenden
(zwei Logs mit GPS in der Track-Bbox sind ungeklaert, siehe
[[mx5_spreewaldring_gps_bbox_mystery]] - der Nutzer hat nach eigener
Aussage nie dort geloggt). Stattdessen: Kalibrierbasis verbreitern, indem
ALLE Strassen-Logs ausgewertet werden statt nur der einen Kurve - deckt
hoffentlich auch schnellere/engere Lenkbewegungen ab, die naeher an
Streckenkurven-Anforderungen liegen.

Methodik: pro Log Lenkwinkel (Nullpunkt-korrigiert), Lenkrate (dLenkwinkel/dt),
Gas% und Bremsdruck auf ein gemeinsames Zeitraster (APP-Zeitstempel)
gezogen, gefiltert auf "nicht bremsend, Gas>0, Geschwindigkeit>MIN_SPEED_MS"
(schliesst Parken/Ampelstopps aus). ANDERS als bei der Einzelkurven-
Analyse gibt es HIER keine manuelle Pruefung jedes Punktes auf Blips/
Rutscher/Abbiegevorgaenge - bei n>>1000 gehen einzelne Ausreisser in der
Regression unter, werden aber im Plot sichtbar bleiben.

Aufruf: .venv/bin/python scripts/throttle_steering_rate_aggregate.py
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
ZERO_OFFSET_PATH = os.path.join(RESULTS_DIR, "steering_zero_offset.json")

MIN_SPEED_MS = 3.0
BRAKE_THRESH_KPA = 100.0
GPS_MAX_HORZ_ACC_M = 20.0
G = 9.81
K1, K2 = 0.023382, -0.00003895  # results/steering_lateral_model_summary.json
HARD_CORNERING_MIN_G = 0.5   # Schwelle "genuine sportliche Kurvenfahrt", vgl. Kurve 1 (0.84-1.05g)


def load_channel(con, log_id, channel):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()


def process_log(con, log_id, offset):
    t_steer, steer_raw = load_channel(con, log_id, "STEER_ANGL_EPS").values.T
    t_app, app = load_channel(con, log_id, "APP").values.T
    t_brk, brk = load_channel(con, log_id, "BFP_PRE_MZ").values.T
    t_v, v_kmh = load_channel(con, log_id, "VehicleSpeed").values.T
    if len(t_app) < 10 or len(t_steer) < 10:
        return None

    t = t_app
    steer = np.interp(t, t_steer, steer_raw) - offset
    brake_kpa = np.interp(t, t_brk, brk)
    v_ms = np.interp(t, t_v, v_kmh) / 3.6
    steer_rate = np.gradient(steer, t)

    yaw_rate_deg_s = (K1 + K2 * np.abs(steer)) * (-steer) * v_ms
    a_lat_g = (v_ms * np.radians(yaw_rate_deg_s)) / G

    mask = (brake_kpa < BRAKE_THRESH_KPA) & (app > 0.5) & (v_ms > MIN_SPEED_MS)
    if mask.sum() < 3:
        return None
    return {
        "t": t[mask], "steer_rate": steer_rate[mask], "app": app[mask],
        "v_ms": v_ms[mask], "steer": steer[mask], "a_lat_g": a_lat_g[mask], "log_id": log_id,
    }


def main():
    with open(ZERO_OFFSET_PATH, encoding="utf-8") as f:
        offsets_raw = json.load(f)
    offsets = {log: res["offset_deg_median"] for log, res in offsets_raw.items()
               if "error" not in res and res.get("trusted")}

    con = duckdb.connect(DB_PATH, read_only=True)
    logs = con.execute(
        "SELECT DISTINCT log_id FROM measurements WHERE channel = 'STEER_ANGL_EPS' ORDER BY log_id"
    ).fetchdf()["log_id"].tolist()

    per_log = []
    for log_id in logs:
        if log_id not in offsets:
            print(f"{log_id}: kein vertrauenswuerdiger Nullpunkt-Offset, uebersprungen")
            continue
        res = process_log(con, log_id, offsets[log_id])
        if res is None:
            print(f"{log_id}: zu wenig Daten, uebersprungen")
            continue
        per_log.append(res)
        print(f"{log_id}: {len(res['t'])} Punkte (Gas>0, keine Bremsung, v>{MIN_SPEED_MS}m/s)")
    con.close()

    all_rate = np.concatenate([r["steer_rate"] for r in per_log])
    all_app = np.concatenate([r["app"] for r in per_log])
    all_alat = np.concatenate([r["a_lat_g"] for r in per_log])
    all_log = np.concatenate([np.full(len(r["t"]), i) for i, r in enumerate(per_log)])
    n = len(all_rate)
    print(f"\nGesamt: {n} Punkte aus {len(per_log)} Logs")

    A = np.vstack([np.ones_like(all_rate), all_rate]).T
    (a, b), *_ = np.linalg.lstsq(A, all_app, rcond=None)
    pred = a + b * all_rate
    r2 = 1 - np.sum((all_app - pred) ** 2) / np.sum((all_app - all_app.mean()) ** 2)
    print(f"\nRegression (alle Logs gepoolt, JEDE Fahrsituation): "
          f"APP% = {a:.1f} + {b:.2f} * Lenkrate[deg/s]  (R^2={r2:.2f}, n={n})")

    # NACHTRAG: normales Fahren (Stadt, Landstrasse gemuetlich) verwaesserte
    # das Signal komplett (R^2~0.00 oben) - der Lenkrate-Gas-Zusammenhang aus
    # Kurve 1 war spezifisch fuer SPORTLICHES Kurvenfahren am Limit
    # (0.84-1.05g), nicht fuer Fahren allgemein. Filter auf echte, harte
    # Kurvenfahrt (analog Kurve 1s Querkraft-Niveau) vor der Regression.
    hard = np.abs(all_alat) > HARD_CORNERING_MIN_G
    n_hard = int(hard.sum())
    print(f"\nDavon mit |a_lat|>{HARD_CORNERING_MIN_G}g (sportliche Kurvenfahrt): {n_hard} Punkte")
    if n_hard > 5:
        Ah = np.vstack([np.ones(n_hard), all_rate[hard]]).T
        (ah, bh), *_ = np.linalg.lstsq(Ah, all_app[hard], rcond=None)
        predh = ah + bh * all_rate[hard]
        r2h = 1 - np.sum((all_app[hard] - predh) ** 2) / np.sum((all_app[hard] - all_app[hard].mean()) ** 2)
        print(f"Regression (NUR |a_lat|>{HARD_CORNERING_MIN_G}g): "
              f"APP% = {ah:.1f} + {bh:.2f} * Lenkrate[deg/s]  (R^2={r2h:.2f}, n={n_hard})")

    rate_p1, rate_p99 = np.percentile(all_rate, [1, 99])
    print(f"Lenkrate 1./99. Perzentil: {rate_p1:.1f} / {rate_p99:.1f} deg/s "
          f"(zum Vergleich: Spreewaldring braucht bis zu ~390 deg/s, siehe "
          f"spreewaldring_natural_throttle_check.py)")

    hard_rate_p1, hard_rate_p99 = (np.percentile(all_rate[hard], [1, 99]) if n_hard > 5
                                    else (float("nan"), float("nan")))
    print(f"Lenkrate 1./99. Perzentil (nur harte Kurvenfahrt): "
          f"{hard_rate_p1:.1f} / {hard_rate_p99:.1f} deg/s")

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    ax = axes[0]
    ax.scatter(all_rate, all_app, c=all_log, cmap="tab20", s=6, alpha=0.3)
    xs = np.linspace(np.percentile(all_rate, 0.5), np.percentile(all_rate, 99.5), 100)
    ax.plot(xs, a + b * xs, "r--", lw=2, label=f"APP%={a:.0f}+{b:.2f}*x (R^2={r2:.2f})")
    ax.set_xlabel("Lenkrate [deg/s]")
    ax.set_ylabel("Gaspedal APP %")
    ax.set_title(f"ALLE Fahrsituationen (n={n})")
    ax.legend()
    ax.grid(alpha=0.3)

    ax2 = axes[1]
    if n_hard > 5:
        ax2.scatter(all_rate[hard], all_app[hard], c=all_log[hard], cmap="tab20", s=12, alpha=0.6)
        xs2 = np.linspace(np.percentile(all_rate[hard], 0.5), np.percentile(all_rate[hard], 99.5), 100)
        ax2.plot(xs2, ah + bh * xs2, "r--", lw=2, label=f"APP%={ah:.0f}+{bh:.2f}*x (R^2={r2h:.2f})")
        ax2.legend()
    ax2.set_xlabel("Lenkrate [deg/s]")
    ax2.set_ylabel("Gaspedal APP %")
    ax2.set_title(f"NUR |a_lat|>{HARD_CORNERING_MIN_G}g (n={n_hard})")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "throttle_steering_rate_aggregate.png")
    fig.savefig(out_png, dpi=130)
    print(f"\nPlot gespeichert: {out_png}")

    out_json = os.path.join(RESULTS_DIR, "throttle_steering_rate_aggregate.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "n_logs": len(per_log), "n_points": n,
            "regression_all_driving": {"intercept_app_pct": float(a), "slope_app_pct_per_deg_s": float(b),
                                        "r2": float(r2), "note": "durch normales Fahren verwaessert, siehe Docstring"},
            "regression_hard_cornering": ({"intercept_app_pct": float(ah), "slope_app_pct_per_deg_s": float(bh),
                                            "r2": float(r2h), "n": n_hard,
                                            "min_a_lat_g": HARD_CORNERING_MIN_G} if n_hard > 5 else None),
            "steer_rate_p1_p99_deg_s_all": [float(rate_p1), float(rate_p99)],
            "steer_rate_p1_p99_deg_s_hard_cornering": [float(hard_rate_p1), float(hard_rate_p99)],
            "per_log_n": {r["log_id"]: len(r["t"]) for r in per_log},
        }, f, indent=2, ensure_ascii=False)
    print(f"Zusammenfassung gespeichert: {out_json}")


if __name__ == "__main__":
    main()
