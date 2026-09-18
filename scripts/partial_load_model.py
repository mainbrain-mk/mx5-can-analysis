"""
Teillast-Drehmomentmodell (MX-5 Projekt)

Bisher deckte drivetrain_model_validation.py/performance_simulation.py NUR
Volllast ab (Modell-Drehmoment kommt aus der Volllast-Kennlinie im externen
Dokument, gueltig nur bei APP>90% bzw. ETC_ACT>80%+fetter Gemischanreicherung).
Fuer alles darunter (der weit ueberwiegende Teil realen Fahrens) gab es kein
Modell - im externen Dokument (docs/status/performance-model.md,
Abschnitt "Naechste Schritte", Punkt 4) explizit als offen benannt:
"Teillastkennfeld, insbesondere ETC=60 Grad, direkt messen - benoetigt
thermisch stabile Segmente mit APP, ETC, Lambda, MAF, Drehzahl und klarer
Kupplungs-/Schaltabgrenzung."

Ueberraschender Fund bei der Datenpruefung: genau dafuer gibt es bereits einen
direkt vom Steuergeraet gemeldeten Kanal in 14 der 56 Logs -
`ActualEnginePercentTorque` ("Tatsaechlicher Motor-Drehmoment in Prozent",
CAN-PID, 64.211 Messwerte). Validierung: bei echtem APP-bestaetigtem Volllast
(APP>90%) liegt dieser Kanal ueber alle 7 geprueften Logs bei median 89-98%
(p10 81-97%) - unabhaengige Bestaetigung, dass er tatsaechlich Prozent des
Volllast-Drehmoments bei der jeweiligen Drehzahl darstellt (nicht z.B. Prozent
eines fixen Referenzwerts). Damit kann das Teillastkennfeld DIREKT aus
vorhandenen Logs gemessen werden, ohne neue Kalibrierfahrten.

Methodik:
  1. Datenbasis: alle Zeitpunkte mit ActualEnginePercentTorque + ETC_ACT +
     EngineRPM aus den 14 Logs, gefiltert auf Kupplung nicht getreten
     (CPP_PER_MZ<CPP_MAX_PCT, falls Kanal vorhanden), keine Bremsung
     (BFP_PRE_MZ<BFP_MAX_KPA, falls vorhanden), Drehzahl>1000 U/min,
     v>MIN_SPEED_MS, gueltiger Gang (TM_GEST oder RPM/Speed-Fallback wie in
     drivetrain_model_validation.py). Ergebnis: ~34.900 Punkte.
  2. Kennfeld (ETC_ACT, EngineRPM) -> ActualEnginePercentTorque: robuste
     Bin-Mediane (5 Grad x 250 U/min-Raster, nur Bins mit >=MIN_BIN_COUNT
     Punkten), darauf lineare Dreiecks-Interpolation (scipy
     LinearNDInterpolator) fuer Punkte innerhalb der Datenabdeckung,
     Nearest-Neighbor als Fallback ausserhalb (v.a. an den Raendern:
     ETC_ACT geht laut Dokument nie ueber ~86 Grad hinaus - "echtes WOT"
     wird separat ueber die Volllastkurve abgedeckt, nicht ueber dieses
     Kennfeld).
  3. Kreuzvalidierung: Leave-one-log-out (Kennfeld auf den uebrigen 13 Logs
     gefittet, Vorhersage auf dem ausgelassenen Log geprueft) - prueft, ob
     das Kennfeld ueber verschiedene Fahrten/Tage hinweg generalisiert statt
     nur Rauschen einer einzelnen Fahrt zu lernen.
  4. Physikalische Validierung ueber echte Teillast-Beschleunigungssegmente
     (alle 44 Logs mit ETC_ACT, nicht nur die 14 mit dem Drehmoment-Kanal):
     Segmente mit konstantem Gang, ETC im Teillastband (siehe
     ETC_TEILLAST_MIN/ETC_WOT_MIN), Kupplung/Bremse wie oben, Mindestdauer
     MIN_SEGMENT_DURATION_S. Gemessene Beschleunigung per linearer Regression
     von VehicleSpeed(t) (wie in top_speed_validation.py - robuster als
     Endpunktdifferenz bei kleinen Beschleunigungen). Drei Modellvarianten
     verglichen:
       - "naiv_volllast": tut so, als waere IMMER Volllast-Drehmoment
         verfuegbar (torque_nm(rpm) ohne Teillast-Korrektur) - zeigt, wie
         falsch diese (bisher implizite) Annahme bei Teillast ist.
       - "kennfeld": Drehmoment = Kennfeld-Vorhersage(ETC,RPM)/100 *
         torque_nm(rpm) - das eigentliche Teillastmodell, nutzt nur ETC_ACT
         (in 44 statt 14 Logs vorhanden, also breiter einsetzbar).
       - "direkt_gemessen": wie "kennfeld", aber mit dem tatsaechlich
         gemessenen ActualEnginePercentTorque statt der Kennfeld-Vorhersage -
         nur fuer die Teilmenge der Segmente moeglich, die aus einem der 14
         Logs mit diesem Kanal stammen. Prueft unabhaengig von der
         Kennfeld-Fitguete, ob die physikalische Kette (eta/CdA/Crr, bereits
         bei Volllast validiert) auch bei Teillast haelt.

WICHTIGE EINSCHRAENKUNGEN:
  - Datendichte ist bei mittlerem ETC (20-70 Grad) deutlich duenner als bei
    sehr niedrigem (<10 Grad, ueberwiegt zahlenmaessig: reales Fahren ist
    meist Cruisen oder kurze Vollgasstoesse, selten laengeres konstantes
    Teilgas) - manche (ETC,RPM)-Bins haben nur einzelne Punkte, siehe
    `n_robust_bins`/Coverage-Ausgabe. Kreuzvalidierung (Punkt 3) ist der
    verlaesslichste Gesamt-Genauigkeitsindikator.
  - Fahrbahnsteigung wird wie in drivetrain_model_validation.py NICHT
    beruecksichtigt (keine Steigungskorrektur fuer diese Segmente) - kann
    einzelne Segmente verzerren.
  - a_long aus der IMU wird bewusst NICHT verwendet (bekanntes Nick-/
    Squat-Problem bei Laengsbeschleunigung, siehe braking_model.py) -
    Referenzbeschleunigung kommt wie ueberall im Projekt aus OBD-
    VehicleSpeed.
  - ActualEnginePercentTorque-Semantik (Prozent von WELCHEM Referenzwert
    genau) ist nicht aus einer Mazda-Spezifikation verifiziert, sondern nur
    empirisch ueber den WOT-Abgleich (siehe oben) plausibilisiert.

Aufruf: .venv/bin/python scripts/partial_load_model.py
"""
import os
import json

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

from drivetrain_model_validation import (
    DB_PATH, RESULTS_DIR, MASS_KG, R_DYN_M, FINAL_DRIVE, GEAR_RATIOS, ETA,
    CDA_M2, RHO_KG_M3, CRR, G, APP_WOT_MIN, ETC_WOT_MIN, MIN_SPEED_MS,
    torque_nm, rpm_from_speed, model_accel, load_channel, group_runs,
    gear_channel_is_reliable, infer_gear_from_rpm_speed,
)

PT_CHANNEL = "ActualEnginePercentTorque"
CPP_MAX_PCT = 5.0       # Kupplungspedal-Schwelle "nicht getreten"
BFP_MAX_KPA = 50.0      # Bremsdruck-Schwelle "keine Bremsung"
RPM_FIT_MIN = 1000.0
ETC_TEILLAST_MIN = 5.0  # unterhalb: Leerlauf/Schub, kein aussagekraeftiges Teilgas

ETC_BIN_STEP = 5.0
RPM_BIN_STEP = 250.0
MIN_BIN_COUNT = 3       # Bins mit weniger Punkten gelten als nicht robust

MIN_SEGMENT_DURATION_S = 3.0
MIN_SEGMENT_POINTS = 5


# --- 1. Datenbasis laden -----------------------------------------------

def _interp_or_default(t_common, ch_df, default):
    if len(ch_df) >= 2:
        return np.interp(t_common, ch_df["t"].values, ch_df["value"].values)
    return np.full(len(t_common), default)


def load_fit_points(con, log_ids):
    """Baut den gefilterten (ETC, RPM) -> ActualEnginePercentTorque
    Datensatz ueber alle Logs mit diesem Kanal (siehe Docstring, Schritt 1)."""
    rows = []
    for log_id in log_ids:
        pt = load_channel(con, log_id, PT_CHANNEL)
        rp = load_channel(con, log_id, "EngineRPM")
        etc = load_channel(con, log_id, "ETC_ACT")
        if len(pt) < 10 or len(rp) < 10 or len(etc) < 10:
            continue
        sp = load_channel(con, log_id, "VehicleSpeed")
        ge = load_channel(con, log_id, "TM_GEST")
        cpp = load_channel(con, log_id, "CPP_PER_MZ")
        bfp = load_channel(con, log_id, "BFP_PRE_MZ")

        t = pt["t"].values
        rpm_i = np.interp(t, rp["t"].values, rp["value"].values)
        etc_i = np.interp(t, etc["t"].values, etc["value"].values)
        v_i = _interp_or_default(t, sp, 0.0) / 3.6
        cpp_i = _interp_or_default(t, cpp, 0.0)
        bfp_i = _interp_or_default(t, bfp, 0.0)

        if len(ge) >= 2 and gear_channel_is_reliable(ge["value"].values):
            g_i = np.round(np.interp(t, ge["t"].values, ge["value"].values))
        else:
            g_i = infer_gear_from_rpm_speed(v_i, rpm_i)

        ok = (
            (cpp_i < CPP_MAX_PCT) & (bfp_i < BFP_MAX_KPA) &
            (rpm_i > RPM_FIT_MIN) & (v_i > MIN_SPEED_MS) &
            (g_i >= 1) & (g_i <= 6) & ~np.isnan(g_i)
        )
        for etc_v, rpm_v, pt_v in zip(etc_i[ok], rpm_i[ok], pt["value"].values[ok]):
            rows.append((log_id, etc_v, rpm_v, pt_v))

    return rows


# --- 2. Kennfeld fitten --------------------------------------------------

def robust_bins(rows):
    """Bin-Mediane (ETC,RPM)->PercentTorque mit >=MIN_BIN_COUNT Punkten.
    Liefert Liste von (etc_center, rpm_center, median_pt, n)."""
    from collections import defaultdict
    buckets = defaultdict(list)
    for _log, etc_v, rpm_v, pt_v in rows:
        eb = round(etc_v / ETC_BIN_STEP) * ETC_BIN_STEP
        rb = round(rpm_v / RPM_BIN_STEP) * RPM_BIN_STEP
        buckets[(eb, rb)].append(pt_v)
    out = []
    for (eb, rb), vals in buckets.items():
        if len(vals) >= MIN_BIN_COUNT:
            out.append((eb, rb, float(np.median(vals)), len(vals)))
    return out


def fit_surface(bins):
    """Liefert predict(etc, rpm) -> percent_torque (0..101 geclippt), linear
    innerhalb der Konvexhuelle der Bin-Zentren, sonst Nearest-Neighbor.

    WICHTIG: ETC (0-90 Grad) und RPM (1000-7500) werden vor der Distanz-
    berechnung auf ihre jeweilige Bin-Schrittweite normiert (sonst dominiert
    die absolut viel groessere RPM-Skala die Nearest-Neighbor-Distanz
    vollstaendig - fuehrte ohne diese Normierung zu einem Artefakt: bei
    hoher Drehzahl UND niedrigem ETC, wo es in der Praxis kaum Messpunkte
    gibt [man haelt selten hohe Drehzahl bei geschlossener Drosselklappe],
    griff Nearest-Neighbor faelschlich auf einen nahen HOHEN ETC-Punkt bei
    aehnlicher Drehzahl zurueck und meldete dort faelschlich hohes
    Drehmoment)."""
    pts = np.array([(b[0], b[1]) for b in bins])
    pts_norm = pts / np.array([ETC_BIN_STEP, RPM_BIN_STEP])
    vals = np.array([b[2] for b in bins])
    lin = LinearNDInterpolator(pts_norm, vals)
    near = NearestNDInterpolator(pts_norm, vals)

    def predict(etc, rpm):
        etc = np.atleast_1d(np.asarray(etc, dtype=float))
        rpm = np.atleast_1d(np.asarray(rpm, dtype=float))
        q = np.column_stack([etc / ETC_BIN_STEP, rpm / RPM_BIN_STEP])
        out = lin(q)
        nan_mask = np.isnan(out)
        if nan_mask.any():
            out = np.array(out, dtype=float)
            out[nan_mask] = near(q[nan_mask])
        return np.clip(out, 0.0, 101.0)

    return predict


def cross_validate(rows, log_ids_with_pt):
    """Leave-one-log-out: Kennfeld auf den uebrigen Logs fitten, RMSE auf
    dem ausgelassenen Log messen."""
    results = []
    for held_out in log_ids_with_pt:
        train_rows = [r for r in rows if r[0] != held_out]
        test_rows = [r for r in rows if r[0] == held_out]
        if len(test_rows) < 20:
            continue
        bins = robust_bins(train_rows)
        if len(bins) < 10:
            continue
        predict = fit_surface(bins)
        etc_t = np.array([r[1] for r in test_rows])
        rpm_t = np.array([r[2] for r in test_rows])
        pt_t = np.array([r[3] for r in test_rows])
        pred = predict(etc_t, rpm_t)
        resid = pred - pt_t
        results.append({
            "log_id": held_out, "n": len(test_rows),
            "rmse_pct": float(np.sqrt(np.mean(resid ** 2))),
            "mean_bias_pct": float(np.mean(resid)),
            "median_abs_error_pct": float(np.median(np.abs(resid))),
        })
    return results


# --- 3. Physikalische Validierung ueber Teillast-Beschleunigungssegmente --

def find_teillast_segments(con, log_id):
    sp = load_channel(con, log_id, "VehicleSpeed")
    etc = load_channel(con, log_id, "ETC_ACT")
    rp = load_channel(con, log_id, "EngineRPM")
    ge = load_channel(con, log_id, "TM_GEST")
    if len(sp) < 2 or len(etc) < 10 or len(rp) < 10 or len(ge) < 2:
        return []

    t_common = etc["t"].values
    v_i = np.interp(t_common, sp["t"].values, sp["value"].values) / 3.6
    etc_i = etc["value"].values
    rpm_i = np.interp(t_common, rp["t"].values, rp["value"].values)

    if gear_channel_is_reliable(ge["value"].values):
        g_i = np.round(np.interp(t_common, ge["t"].values, ge["value"].values))
    else:
        g_i = infer_gear_from_rpm_speed(v_i, rpm_i)

    app = load_channel(con, log_id, "APP")
    if len(app) >= 10:
        app_i = np.interp(t_common, app["t"].values, app["value"].values)
        not_wot = app_i < APP_WOT_MIN
    else:
        not_wot = etc_i < ETC_WOT_MIN

    cpp = load_channel(con, log_id, "CPP_PER_MZ")
    clutch_ok = _interp_or_default(t_common, cpp, 0.0) < CPP_MAX_PCT

    bfp = load_channel(con, log_id, "BFP_PRE_MZ")
    no_brake = _interp_or_default(t_common, bfp, 0.0) < BFP_MAX_KPA

    pt = load_channel(con, log_id, PT_CHANNEL)
    has_pt = len(pt) >= 10
    pt_i = np.interp(t_common, pt["t"].values, pt["value"].values) if has_pt else None

    candidate = (
        (etc_i > ETC_TEILLAST_MIN) & not_wot &
        (g_i >= 1) & (g_i <= 6) & ~np.isnan(g_i) &
        (v_i > MIN_SPEED_MS) & clutch_ok & no_brake
    )

    results = []
    for start, end in group_runs(candidate):
        sub_start = start
        for i in range(start + 1, end + 2):
            if i > end or g_i[i] != g_i[sub_start]:
                sub_end = i - 1
                t_seg = t_common[sub_start:sub_end + 1]
                if (len(t_seg) >= MIN_SEGMENT_POINTS and
                        t_seg[-1] - t_seg[0] >= MIN_SEGMENT_DURATION_S):
                    v_seg = v_i[sub_start:sub_end + 1]
                    slope, _ = np.polyfit(t_seg, v_seg, 1)
                    gear_here = int(g_i[sub_start])
                    v_mean = float(np.mean(v_seg))
                    rpm_mean = float(np.mean(rpm_i[sub_start:sub_end + 1]))
                    etc_mean = float(np.mean(etc_i[sub_start:sub_end + 1]))
                    pt_mean = None
                    if has_pt:
                        pt_seg = pt_i[sub_start:sub_end + 1]
                        if not np.all(np.isnan(pt_seg)):
                            pt_mean = float(np.nanmean(pt_seg))
                    results.append({
                        "log_id": log_id, "t_start": float(t_seg[0]),
                        "t_end": float(t_seg[-1]), "duration_s": float(t_seg[-1] - t_seg[0]),
                        "gear": gear_here, "v_mean_ms": v_mean, "rpm_mean": rpm_mean,
                        "etc_mean": etc_mean, "pt_mean_measured": pt_mean,
                        "a_measured_ms2": float(slope),
                    })
                sub_start = i
    return results


def force_to_accel(f_wheel, v_ms, mass_kg=MASS_KG):
    f_drag = 0.5 * RHO_KG_M3 * CDA_M2 * v_ms ** 2
    f_roll = CRR * mass_kg * G
    return (f_wheel - f_drag - f_roll) / mass_kg


def wheel_force_from_percent(percent, rpm, gear):
    torque = percent / 100.0 * torque_nm(rpm)
    return torque * GEAR_RATIOS[gear] * FINAL_DRIVE * ETA / R_DYN_M


def evaluate_segments(segments, predict_fn):
    for seg in segments:
        a_naive, _, _ = model_accel(seg["v_mean_ms"], seg["gear"])
        seg["a_model_naiv_volllast_ms2"] = float(a_naive)

        pct_pred = float(predict_fn(seg["etc_mean"], seg["rpm_mean"])[0])
        f_kennfeld = wheel_force_from_percent(pct_pred, seg["rpm_mean"], seg["gear"])
        seg["percent_torque_kennfeld"] = pct_pred
        seg["a_model_kennfeld_ms2"] = float(force_to_accel(f_kennfeld, seg["v_mean_ms"]))

        if seg["pt_mean_measured"] is not None:
            f_direct = wheel_force_from_percent(seg["pt_mean_measured"], seg["rpm_mean"], seg["gear"])
            seg["a_model_direkt_gemessen_ms2"] = float(force_to_accel(f_direct, seg["v_mean_ms"]))
        else:
            seg["a_model_direkt_gemessen_ms2"] = None
    return segments


def stats_for(segments, model_key):
    a_meas = np.array([s["a_measured_ms2"] for s in segments if s[model_key] is not None])
    a_mod = np.array([s[model_key] for s in segments if s[model_key] is not None])
    if len(a_meas) < 3:
        return None
    return {
        "n": len(a_meas),
        "correlation": float(np.corrcoef(a_meas, a_mod)[0, 1]),
        "rmse_ms2": float(np.sqrt(np.mean((a_meas - a_mod) ** 2))),
        "mean_diff_ms2": float(np.mean(a_meas - a_mod)),
    }


# --- Plots ----------------------------------------------------------------

def plot_kennfeld(bins, predict_fn, out_path):
    etc_grid = np.linspace(0, 90, 91)
    rpm_grid = np.linspace(1000, 7500, 66)
    EG, RG = np.meshgrid(etc_grid, rpm_grid)
    pred = predict_fn(EG.ravel(), RG.ravel()).reshape(EG.shape)

    fig, ax = plt.subplots(figsize=(9, 6))
    cf = ax.contourf(EG, RG, pred, levels=20, cmap="viridis")
    plt.colorbar(cf, ax=ax, label="Modelliertes Drehmoment [% von Volllast bei dieser Drehzahl]")
    ex = np.array([b[0] for b in bins])
    rx = np.array([b[1] for b in bins])
    ax.scatter(ex, rx, s=8, color="white", alpha=0.4, edgecolors="black", linewidths=0.3,
               label=f"robuste Bin-Mediane (n>={MIN_BIN_COUNT}), {len(bins)} Punkte")
    ax.set_xlabel("ETC_ACT (Drosselklappenwinkel) [Grad]")
    ax.set_ylabel("Motordrehzahl [U/min]")
    ax.set_title("Teillast-Kennfeld: gemessenes Drehmoment (% von Volllast)")
    ax.legend(loc="lower right", fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_validation(segments, out_path):
    fig, ax = plt.subplots(figsize=(7, 7))
    a_meas_all = np.array([s["a_measured_ms2"] for s in segments])
    a_naiv = np.array([s["a_model_naiv_volllast_ms2"] for s in segments])
    a_kf = np.array([s["a_model_kennfeld_ms2"] for s in segments])
    ax.scatter(a_naiv, a_meas_all, s=12, alpha=0.35, color="firebrick",
               label="naiv (immer Volllast-Drehmoment)")
    ax.scatter(a_kf, a_meas_all, s=12, alpha=0.5, color="seagreen",
               label="Teillast-Kennfeld (ETC+RPM)")
    lim_lo = min(a_meas_all.min(), a_naiv.min(), a_kf.min()) - 0.2
    lim_hi = max(a_meas_all.max(), a_naiv.max(), a_kf.max()) + 0.2
    ax.plot([lim_lo, lim_hi], [lim_lo, lim_hi], "--", color="gray", lw=1, label="perfekte Uebereinstimmung")
    ax.set_xlabel("Modell-Beschleunigung [m/s²]")
    ax.set_ylabel("Gemessene Beschleunigung (OBD) [m/s²]")
    ax.set_title("Teillast-Segmente: Modell vs. Messung")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def build_kennfeld_predictor(db_path=DB_PATH):
    """Baut das (ETC,RPM)->Prozent-Drehmoment-Kennfeld aus dem Datalake und
    liefert (predict_fn, bins) - fuer die Wiederverwendung durch andere
    Skripte (siehe performance_simulation.py), ohne die komplette
    Validierung/Plots von main() erneut auszufuehren."""
    con = duckdb.connect(db_path, read_only=True)
    pt_log_ids = con.execute(
        f"SELECT DISTINCT log_id FROM measurements WHERE channel = ? ORDER BY log_id", [PT_CHANNEL]
    ).fetchdf()["log_id"].tolist()
    rows = load_fit_points(con, pt_log_ids)
    con.close()
    bins = robust_bins(rows)
    return fit_surface(bins), bins


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    con = duckdb.connect(DB_PATH, read_only=True)
    all_log_ids = con.execute("SELECT log_id FROM logs ORDER BY log_id").fetchdf()["log_id"].tolist()
    pt_log_ids = con.execute(
        f"SELECT DISTINCT log_id FROM measurements WHERE channel = ? ORDER BY log_id", [PT_CHANNEL]
    ).fetchdf()["log_id"].tolist()

    print(f"=== 1. Kennfeld-Datenbasis: {len(pt_log_ids)} Logs mit {PT_CHANNEL} ===")
    rows = load_fit_points(con, pt_log_ids)
    print(f"Gefilterte Datenpunkte (Kupplung/Bremse/Gang/Drehzahl/Speed OK): {len(rows)}")

    bins = robust_bins(rows)
    print(f"Robuste (ETC,RPM)-Bins (>= {MIN_BIN_COUNT} Punkte, {ETC_BIN_STEP}° x {RPM_BIN_STEP} U/min-Raster): {len(bins)}")
    predict_fn = fit_surface(bins)

    print(f"\n=== 2. Kreuzvalidierung (leave-one-log-out, {len(pt_log_ids)} Logs) ===")
    cv_results = cross_validate(rows, pt_log_ids)
    for r in cv_results:
        print(f"{r['log_id']}: n={r['n']:5d}  RMSE={r['rmse_pct']:.1f}%  "
              f"Bias={r['mean_bias_pct']:+.1f}%  Median|Fehler|={r['median_abs_error_pct']:.1f}%")
    if cv_results:
        overall_rmse = np.sqrt(np.mean([r["rmse_pct"] ** 2 for r in cv_results]))
        print(f"Gesamt-RMSE ueber alle ausgelassenen Logs (ungewichtet): {overall_rmse:.1f} Prozentpunkte")

    plot_kennfeld(bins, predict_fn, os.path.join(RESULTS_DIR, "partial_load_kennfeld.png"))
    print(f"\nKennfeld-Plot: {RESULTS_DIR}/partial_load_kennfeld.png")

    print(f"\n=== 3. Physikalische Validierung ueber Teillast-Beschleunigungssegmente ({len(all_log_ids)} Logs) ===")
    all_segments = []
    for log_id in all_log_ids:
        segs = find_teillast_segments(con, log_id)
        all_segments.extend(segs)
    con.close()
    print(f"Gefundene Teillast-Segmente (>= {MIN_SEGMENT_DURATION_S}s, ETC {ETC_TEILLAST_MIN}-{ETC_WOT_MIN} Grad, "
          f"konstanter Gang, keine Bremsung/Kupplung): {len(all_segments)}")
    n_with_pt = sum(1 for s in all_segments if s["pt_mean_measured"] is not None)
    print(f"(davon {n_with_pt} mit direkt gemessenem {PT_CHANNEL} zum Gegenchecken)")

    all_segments = evaluate_segments(all_segments, predict_fn)

    print("\n--- Vergleich Modellvarianten ueber alle Teillast-Segmente ---")
    for key, label in [
        ("a_model_naiv_volllast_ms2", "naiv (immer Volllast)"),
        ("a_model_kennfeld_ms2", "Teillast-Kennfeld (ETC+RPM)"),
        ("a_model_direkt_gemessen_ms2", "direkt gemessen (nur Teilmenge)"),
    ]:
        st = stats_for(all_segments, key)
        if st is None:
            print(f"{label:35s}: zu wenige Datenpunkte")
            continue
        print(f"{label:35s}: n={st['n']:4d}  Korrelation={st['correlation']:.2f}  "
              f"RMSE={st['rmse_ms2']:.3f} m/s²  mittl.Diff={st['mean_diff_ms2']:+.3f} m/s²")

    plot_validation(all_segments, os.path.join(RESULTS_DIR, "partial_load_model_vs_measured.png"))
    print(f"\nValidierungs-Plot: {RESULTS_DIR}/partial_load_model_vs_measured.png")

    summary = {
        "n_fit_points": len(rows),
        "n_robust_bins": len(bins),
        "kennfeld_bins": [{"etc": b[0], "rpm": b[1], "median_percent_torque": b[2], "n": b[3]} for b in bins],
        "cross_validation": cv_results,
        "segments": all_segments,
        "aggregate_stats": {
            key: stats_for(all_segments, key)
            for key in ["a_model_naiv_volllast_ms2", "a_model_kennfeld_ms2", "a_model_direkt_gemessen_ms2"]
        },
    }
    with open(os.path.join(RESULTS_DIR, "partial_load_model_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Details: {RESULTS_DIR}/partial_load_model_summary.json")


if __name__ == "__main__":
    main()
