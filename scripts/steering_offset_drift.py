"""
Prueft, ob der STEER_ANGL_EPS-Nullpunkt INNERHALB einer Fahrt driftet
(MX-5 Projekt)

Hintergrund (siehe docs/logs/projekt-stand.md, "Nachtrag: STEER_ANGL_EPS-Nullpunkt per
OSM-Geradeausfahrt korrigiert", 02.09.2026): `steering_zero_offset.py`
schaetzt EINEN Nullpunkt-Offset pro Log (Median von STEER_ANGL_EPS ueber
alle per OSM bestaetigten Geradeausfahrt-Fenster). Dort blieb explizit
offen, ob dieser Offset ueber die Fahrt hinweg KONSTANT ist oder driftet -
ein Drift wuerde die Einzelkonstante systematisch falsch machen (zu Beginn
zu klein, am Ende zu gross oder umgekehrt) und damit sowohl die
Kalibrierpunkte als auch die kontinuierliche a_lat-Spur verzerren.

Methodik: dieselbe, vom Lenkwinkel UNABHAENGIGE Geradeausfahrt-Erkennung
wie in `steering_zero_offset.py` (OSM-Strassengeometrie + GPS-Match, alle
Konstanten von dort importiert - kein zweiter, abweichender Schwellwert-
Satz). Statt eines einzigen Medians ueber die ganze Fahrt werden die
qualifizierten Samples in feste Zeit-Buckets (BUCKET_S) einsortiert und
pro Bucket ein eigener Offset-Median gebildet. Das ist bewusst feiner als
die Fenster-Gruppierung des Basisskripts: ein einzelnes "Fenster" kann dort
ueber 900s lang sein (Autobahnetappe) und wuerde einen Drift in sich selbst
verstecken.

Ausgewertet wird dann:
  1. Gewichtete lineare Regression Offset(t) ueber die Bucket-Mediane
     (Gewicht = Bucket-Samplezahl), Steigung in °/h, mit Bootstrap-
     Konfidenzintervall ueber die Buckets (kein Normalverteilungs-
     Annahme noetig bei wenigen Buckets).
  2. Vergleich Drift-Modell gegen Konstant-Modell: gewichtete
     Residuenstreuung mit/ohne Steigung. Sinkt sie kaum, ist der Drift
     auch bei formal signifikanter Steigung praktisch bedeutungslos.
  3. Bester Sprung-/Stufenpunkt (Suche ueber alle Bucket-Grenzen,
     minimiert gewichtete Fehlerquadrate) - falls der Sensor sich
     WAEHREND der Fahrt neu genullt haette, saehe man eher eine Stufe als
     eine Rampe.
  4. Korrelation Offset(Bucket) gegen Bucket-Mediangeschwindigkeit -
     Kontrolle gegen eine naheliegende Scheinursache: unterschiedliche
     Strassen/Fahrbahnquerneigung bei unterschiedlichem Tempo koennten
     eine Zeitstruktur vortaeuschen, die gar kein Sensordrift ist.
  5. Praktische Auswirkung: ein Offset-Fehler d wirkt sich im Modell aus
     `steering_lateral_model.py` (a_lat = k*d*v^2, siehe dortiger
     Docstring) quadratisch mit der Geschwindigkeit aus - deshalb wird
     der gefundene Drift direkt in g bei typischen Geschwindigkeiten
     umgerechnet, statt nur in Grad berichtet zu werden.

Aufruf:
    python scripts/steering_offset_drift.py                  # alle Lenkwinkel-Logs
    python scripts/steering_offset_drift.py "2026-09-02 150720"

Ergebnis: results/steering_offset_drift.json,
          results/steering_offset_drift_<log>.png
"""
import os
import sys
import json

import numpy as np
import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.spatial import cKDTree

from steering_zero_offset import (
    DB_PATH, RESULTS_DIR, CORRIDOR_BBOX,
    GPS_MAX_HORZ_ACC_M, MATCH_TOLERANCE_M, MIN_SPEED_MS, MIN_RUN_S,
    build_straight_point_cloud, get_or_build_road_cache, group_runs,
    load_channel, logs_with_steering, make_projector,
)

OUT_PATH = os.path.join(RESULTS_DIR, "steering_offset_drift.json")

BUCKET_S = 120.0          # Zeitaufloesung der Drift-Suche
MIN_SAMPLES_PER_BUCKET = 15
MIN_BUCKETS = 4           # weniger erlaubt keine sinnvolle Trendaussage
N_BOOTSTRAP = 2000
RNG_SEED = 20260902

# aus results/steering_lateral_model_summary.json, nur fuer die Umrechnung
# Grad -> g (Auswirkungsabschaetzung, siehe Docstring Punkt 5)
K_FALLBACK = 0.016833
G = 9.81
IMPACT_SPEEDS_KMH = (50.0, 100.0, 150.0)


def load_k():
    path = os.path.join(RESULTS_DIR, "steering_lateral_model_summary.json")
    if not os.path.exists(path):
        return K_FALLBACK
    with open(path, encoding="utf-8") as f:
        return float(json.load(f).get("k", K_FALLBACK))


def straight_samples_for_log(con, log_id, straight_tree, project):
    """Alle STEER_ANGL_EPS-Samples in per OSM bestaetigten Geradeausfahrt-
    Fenstern, samt Zeit und Geschwindigkeit. Identische Filterkette wie
    `steering_zero_offset.estimate_offset_for_log()`."""
    gps_lat = load_channel(con, log_id, "Breite")
    gps_lon = load_channel(con, log_id, "Länge")
    speed = load_channel(con, log_id, "VehicleSpeed")
    acc = load_channel(con, log_id, "Horz Genauigkeit")
    steer = load_channel(con, log_id, "STEER_ANGL_EPS")
    if len(gps_lat) < 10 or len(steer) < 10:
        return None

    t = gps_lat["t"].values
    lat = gps_lat["value"].values
    lon = np.interp(t, gps_lon["t"].values, gps_lon["value"].values)

    if len(acc) >= 2:
        acc_i = np.interp(t, acc["t"].values, acc["value"].values)
        good = acc_i <= GPS_MAX_HORZ_ACC_M
        t, lat, lon = t[good], lat[good], lon[good]
    if len(t) < 10:
        return None

    v_ms = np.interp(t, speed["t"].values, speed["value"].values) / 3.6
    x, y = project(lat, lon)
    dist, _ = straight_tree.query(np.column_stack([x, y]))
    candidate = (dist < MATCH_TOLERANCE_M) & (v_ms > MIN_SPEED_MS)
    runs = group_runs(t, candidate, MIN_RUN_S)
    if not runs:
        return None

    steer_t = steer["t"].values
    steer_v = steer["value"].values
    steer_speed = np.interp(steer_t, speed["t"].values, speed["value"].values)

    keep = np.zeros(len(steer_t), dtype=bool)
    for i0, i1 in runs:
        keep |= (steer_t >= t[i0]) & (steer_t <= t[i1])
    if keep.sum() == 0:
        return None
    return {"t": steer_t[keep], "steer": steer_v[keep], "speed_kmh": steer_speed[keep]}


def build_buckets(samples):
    t, steer, spd = samples["t"], samples["steer"], samples["speed_kmh"]
    edges = np.arange(t.min(), t.max() + BUCKET_S, BUCKET_S)
    buckets = []
    for lo in edges:
        m = (t >= lo) & (t < lo + BUCKET_S)
        if m.sum() < MIN_SAMPLES_PER_BUCKET:
            continue
        vals = steer[m]
        med = float(np.median(vals))
        buckets.append({
            "t_center_s": float(t[m].mean()),
            "n": int(m.sum()),
            "offset_deg_median": med,
            # MAD auf Standardabweichung skaliert (robust gegen echte
            # Lenkmanoever, die in einem Bucket immer mit drinstecken)
            "spread_deg_mad": float(1.4826 * np.median(np.abs(vals - med))),
            "speed_kmh_median": float(np.median(spd[m])),
        })
    return buckets


def weighted_linfit(t, y, w):
    """Gewichtete Regression y = a + b*t. Gibt (a, b, gewichtete Residuen-Std)."""
    W = w / w.sum()
    tm = np.sum(W * t)
    ym = np.sum(W * y)
    var_t = np.sum(W * (t - tm) ** 2)
    if var_t <= 0:
        return ym, 0.0, float(np.sqrt(np.sum(W * (y - ym) ** 2)))
    b = np.sum(W * (t - tm) * (y - ym)) / var_t
    a = ym - b * tm
    resid = y - (a + b * t)
    return float(a), float(b), float(np.sqrt(np.sum(W * resid ** 2)))


def bootstrap_slope_ci(t, y, w, n_boot=N_BOOTSTRAP, seed=RNG_SEED):
    rng = np.random.default_rng(seed)
    n = len(t)
    slopes = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        if len(np.unique(t[idx])) < 2:
            slopes[i] = np.nan
            continue
        _, b, _ = weighted_linfit(t[idx], y[idx], w[idx])
        slopes[i] = b
    slopes = slopes[np.isfinite(slopes)]
    return float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))


def best_step(t, y, w):
    """Bester Stufenpunkt (zwei konstante Niveaus) per gewichteter
    Fehlerquadratsuche ueber alle inneren Bucket-Grenzen."""
    best = None
    for i in range(2, len(t) - 1):
        for lo, hi in ((slice(0, i), slice(i, None)),):
            w1, w2 = w[lo], w[hi]
            m1 = np.sum(w1 * y[lo]) / w1.sum()
            m2 = np.sum(w2 * y[hi]) / w2.sum()
            sse = np.sum(w1 * (y[lo] - m1) ** 2) + np.sum(w2 * (y[hi] - m2) ** 2)
            if best is None or sse < best["sse"]:
                best = {"sse": float(sse), "split_index": i,
                        "t_split_s": float((t[i - 1] + t[i]) / 2),
                        "level_before_deg": float(m1), "level_after_deg": float(m2),
                        "step_deg": float(m2 - m1)}
    return best


def deg_to_g(delta_deg, k, speed_kmh):
    """Auswirkung eines Nullpunkt-Fehlers auf a_lat (siehe
    steering_lateral_model.py: yaw_rate = k*steer*v, a_lat = v*yaw_rate)."""
    v = speed_kmh / 3.6
    return abs(k * delta_deg * v * v * np.pi / 180.0 / G)


def analyse_log(con, log_id, straight_tree, project, k):
    samples = straight_samples_for_log(con, log_id, straight_tree, project)
    if samples is None:
        return {"log_id": log_id, "error": "keine bestaetigten Geradeausfahrt-Samples"}

    buckets = build_buckets(samples)
    overall_median = float(np.median(samples["steer"]))
    res = {
        "log_id": log_id,
        "n_straight_samples": int(len(samples["t"])),
        "duration_covered_s": float(samples["t"].max() - samples["t"].min()),
        "offset_deg_median_whole_log": overall_median,
        "bucket_s": BUCKET_S,
        "n_buckets": len(buckets),
        "buckets": buckets,
    }
    if len(buckets) < MIN_BUCKETS:
        res["error"] = f"nur {len(buckets)} auswertbare Buckets (<{MIN_BUCKETS})"
        return res

    t = np.array([b["t_center_s"] for b in buckets])
    y = np.array([b["offset_deg_median"] for b in buckets])
    w = np.array([b["n"] for b in buckets], dtype=float)

    a, b, resid_std = weighted_linfit(t, y, w)
    ci_lo, ci_hi = bootstrap_slope_ci(t, y, w)
    # Referenz: Residuenstreuung des Konstant-Modells (nur gewichteter Mittelwert)
    const_std = float(np.sqrt(np.sum((w / w.sum()) * (y - np.sum(w * y) / w.sum()) ** 2)))
    span_s = t.max() - t.min()

    step = best_step(t, y, w)
    drift_total = b * span_s

    res.update({
        "bucket_offset_spread_deg_std": float(np.std(y)),
        "bucket_offset_range_deg": [float(y.min()), float(y.max())],
        "median_within_bucket_spread_deg": float(np.median([bk["spread_deg_mad"] for bk in buckets])),
        "trend": {
            "slope_deg_per_h": b * 3600.0,
            "slope_ci95_deg_per_h": [ci_lo * 3600.0, ci_hi * 3600.0],
            "significant": bool(ci_lo * ci_hi > 0),
            "intercept_deg": a,
            "drift_over_covered_span_deg": float(drift_total),
            "covered_span_s": float(span_s),
            "resid_std_deg_linear": resid_std,
            "resid_std_deg_constant": const_std,
            "variance_explained_by_trend": float(1 - (resid_std / const_std) ** 2) if const_std > 0 else None,
        },
        "best_step": step,
        "speed_control": {
            "corr_offset_vs_speed": float(np.corrcoef(y, [bk["speed_kmh_median"] for bk in buckets])[0, 1]),
            "speed_range_kmh": [float(min(bk["speed_kmh_median"] for bk in buckets)),
                                 float(max(bk["speed_kmh_median"] for bk in buckets))],
        },
        "impact_a_lat_g": {
            f"{v:.0f}kmh": {
                "from_trend_drift": deg_to_g(drift_total, k, v),
                "from_bucket_range": deg_to_g(y.max() - y.min(), k, v),
            } for v in IMPACT_SPEEDS_KMH
        },
    })
    return res


def plot_log(res, out_path):
    buckets = res["buckets"]
    t = np.array([b["t_center_s"] for b in buckets]) / 60.0
    y = np.array([b["offset_deg_median"] for b in buckets])
    e = np.array([b["spread_deg_mad"] for b in buckets])
    n = np.array([b["n"] for b in buckets], dtype=float)

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.errorbar(t, y, yerr=e, fmt="o", color="steelblue", ecolor="lightsteelblue",
                 capsize=3, ms=4, lw=1, label=f"Bucket-Median ± MAD ({res['bucket_s']:.0f}s-Buckets)")
    sizes = 12 + 60 * (n - n.min()) / max(1e-9, (n.max() - n.min()))
    ax.scatter(t, y, s=sizes, color="steelblue", alpha=0.35, zorder=3)
    ax.axhline(res["offset_deg_median_whole_log"], color="darkgreen", ls="-", lw=1.4,
               label=f"aktuell verwendeter Einzel-Offset ({res['offset_deg_median_whole_log']:+.1f}°)")

    tr = res["trend"]
    tt = np.linspace(t.min(), t.max(), 50)
    ax.plot(tt, tr["intercept_deg"] + (tr["slope_deg_per_h"] / 3600.0) * (tt * 60.0),
            color="tomato", ls="--", lw=1.6,
            label=f"linearer Trend: {tr['slope_deg_per_h']:+.2f}°/h "
                  f"(95%-KI {tr['slope_ci95_deg_per_h'][0]:+.2f}…{tr['slope_ci95_deg_per_h'][1]:+.2f})")

    ax.set_xlabel("Zeit im Log [min]")
    ax.set_ylabel("STEER_ANGL_EPS bei bestaetigter Geradeausfahrt [°]")
    ax.set_title(f"Nullpunkt-Drift-Pruefung — {res['log_id']}")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    targets = sys.argv[1:]

    print(f"Lade OSM-Strassengeometrie (Cache wie steering_zero_offset.py) ...")
    ways = get_or_build_road_cache(CORRIDOR_BBOX)
    lat0 = (CORRIDOR_BBOX[0] + CORRIDOR_BBOX[2]) / 2
    project = make_projector(lat0)
    straight_points = build_straight_point_cloud(ways, project)
    print(f"{len(straight_points)} 'gerade' OSM-Strassenpunkte")
    straight_tree = cKDTree(straight_points)

    k = load_k()
    print(f"k (fuer die Grad->g-Umrechnung) = {k:.6f}\n")

    con = duckdb.connect(DB_PATH, read_only=True)
    logs = targets if targets else logs_with_steering(con)

    results = {}
    for log_id in logs:
        res = analyse_log(con, log_id, straight_tree, project, k)
        results[log_id] = res
        if "error" in res and "trend" not in res:
            print(f"{log_id}: {res['error']}")
            continue
        tr = res["trend"]
        sig = "SIGNIFIKANT" if tr["significant"] else "nicht signifikant"
        print(f"{log_id}:")
        print(f"  Einzel-Offset (ganzes Log)      {res['offset_deg_median_whole_log']:+.1f}°  "
              f"aus {res['n_straight_samples']} Geradeaus-Samples")
        print(f"  Bucket-Mediane ({res['n_buckets']} Stk.)      "
              f"{res['bucket_offset_range_deg'][0]:+.1f}° … {res['bucket_offset_range_deg'][1]:+.1f}°  "
              f"(Std {res['bucket_offset_spread_deg_std']:.1f}°, "
              f"Streuung IM Bucket typ. {res['median_within_bucket_spread_deg']:.1f}°)")
        print(f"  Linearer Trend                  {tr['slope_deg_per_h']:+.2f}°/h  "
              f"[95%-KI {tr['slope_ci95_deg_per_h'][0]:+.2f} … {tr['slope_ci95_deg_per_h'][1]:+.2f}]  -> {sig}")
        print(f"  Drift ueber die erfasste Spanne  {tr['drift_over_covered_span_deg']:+.1f}° "
              f"in {tr['covered_span_s']/60:.0f} min; "
              f"Residuenstreuung {tr['resid_std_deg_constant']:.1f}° (konstant) -> "
              f"{tr['resid_std_deg_linear']:.1f}° (mit Trend)")
        st = res["best_step"]
        if st:
            print(f"  Bester Stufenpunkt              t={st['t_split_s']/60:.0f} min, "
                  f"Sprung {st['step_deg']:+.1f}° ({st['level_before_deg']:+.1f}° -> {st['level_after_deg']:+.1f}°)")
        sc = res["speed_control"]
        print(f"  Kontrolle Offset vs. Tempo      r={sc['corr_offset_vs_speed']:+.2f} "
              f"(Bucket-Tempo {sc['speed_range_kmh'][0]:.0f}-{sc['speed_range_kmh'][1]:.0f} km/h)")
        imp = res["impact_a_lat_g"]
        print("  Auswirkung auf a_lat (Trend-Drift / Bucket-Spanne): " + ", ".join(
            f"{s}: {imp[s]['from_trend_drift']:.3f}g / {imp[s]['from_bucket_range']:.3f}g" for s in imp))
        out_png = os.path.join(RESULTS_DIR, f"steering_offset_drift_{log_id.replace(' ', '_')}.png")
        plot_log(res, out_png)
        print(f"  Plot: {out_png}\n")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Details: {OUT_PATH}")


if __name__ == "__main__":
    main()
