"""
CAN-Suche nach Vertikalbeschleunigung, Wanken und Nicken mit der Handy-IMU als Anker
(Plan: docs/plans/can-vertikal-wank-nick-plan.md, Schritte 0-3).

Schritt 0  Paare CAN-Log <-> .dlg bilden und zeitlich ausrichten. Die Uhren sind beide nicht
           vertrauenswuerdig (dlg-Zeit im Datalake UTC-verschoben, Pi ohne RTC), deshalb:
           grob per Kreuzkorrelation der Geschwindigkeit (1 Hz, +-8 h), fein per Kreuzkorrelation
           der Querbeschleunigung (100 Hz, +-3 s) nach der Achsenumrechnung.
Schritt 1  Handy -> Fahrzeugachsen, nur aus ROH-Beschleunigung (AccelerationWithGravity): Hochachse =
           mittlere Richtung, Laengsachse per Regression gegen CAN Longitudinal_Acc_Raw (orthogonal
           zur Hochachse), Querachse als Kreuzprodukt mit Vorzeichen gegen Lateral_Acc_Raw.
           Kontrolle: gedrehte RotationRate-z gegen YawRate_Raw (27.09.: r=0,983, Steigung 0,99).
           NICHT "Acceleration" (gravitationsbereinigt): die Sensorfusion des Handys kippt den
           Schaetzwert fuer g in anhaltenden Kurven mit, Querbeschl. <0,5 Hz nur 27 % der CAN-Werte.
           Wank-/Nickwinkel sind aus Beschleunigungen grundsaetzlich nicht trennbar (RCM und Handy
           kippen mit der Karosserie mit) -> Wank-/Nickrate aus RotationRate (~5 Hz, °/s), Winkel =
           hochpassgefiltertes Integral.
Schritt 2  Anker gegen bekannte CAN-Signale bereinigen (Residuum), damit Felder, die nur
           Quer-/Laengsbeschleunigung kopieren, nicht als "Wankwinkel" gewinnen.
Schritt 3  Freie Felder aller IDs (can_anchor_sweep.candidates) gegen Anker korrelieren,
           gleiche Filter auf Feld und Anker, Aggregation ueber alle Paare.

Handy-RotationRate hat nur ~5 Hz: reicht fuer Karosseriebewegung (< 2 Hz), nicht fuer Vibration.

Ausgabe: data/can_phone_pairs.json, results/can_phone_imu_sweep_raw.csv, results/can_phone_imu_sweep.csv
Aufruf:  .venv/bin/python scripts/can_phone_imu_sweep.py [--pairs-only] [--pair CANLOG]
"""
import argparse
import datetime as dt
import json
import os
import sys

import duckdb
import numpy as np
import pandas as pd
from scipy import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab
from can_anchor_sweep import candidates, dbc_owner

HZ = 25.0            # Suchraster; Vertikalband bis 5 Hz passt unter Nyquist 12,5 Hz
MIN_OVERLAP_S = 120
MAX_LAG_S = 8 * 3600
PAIRS_JSON = "data/can_phone_pairs.json"
PHONE_CH = ["AccelerationWithGravityX", "AccelerationWithGravityY", "AccelerationWithGravityZ",
            "RotationRateX", "RotationRateY", "RotationRateZ",
            "GPS-Geschwindigkeit", "VehicleSpeed"]
KNOWN = [("Lateral_Acc_Raw", 117), ("Longitudinal_Acc_Raw", 118), ("YawRate_Raw", 117),
         ("VehicleSpeed", 514), ("BrakePressure", 120)]


# ----------------------------------------------------------------------------- Laden
def _dl():
    return duckdb.connect("data/datalake.duckdb", read_only=True)


def dlg_logs():
    return [r[0] for r in _dl().sql("select log_id from logs where source_format='dlg' "
                                     "and start_time_local>='2026-09-12' order by 1").fetchall()]


def phone(log_id):
    """{Kanal: (epoch, Wert)}; Startzeit aus dem Dateinamen (lokal), nicht aus timestamp_local."""
    t0 = dt.datetime.strptime(log_id, "%Y-%m-%d %H%M%S").timestamp()
    q = _dl().execute("select channel, t_elapsed_s, value from measurements where log_id=? and channel in ("
                      + ",".join("?" * len(PHONE_CH)) + ") order by channel, t_elapsed_s",
                      [log_id] + PHONE_CH).df()
    return {c: (g.t_elapsed_s.to_numpy() + t0, g.value.to_numpy()) for c, g in q.groupby("channel")}


def _interp(t, v, g):
    m = np.isfinite(v)
    if m.sum() < 2:
        return np.full(len(g), np.nan)
    out = np.interp(g, t[m], v[m])
    out[(g < t[m][0]) | (g > t[m][-1])] = np.nan
    return out


def _bp(x, lo, hi, hz):
    """Butterworth-Band-/Tief-/Hochpass, NaN-tolerant (NaN -> 0 fuer den Filter, danach wieder NaN)."""
    m = np.isfinite(x)
    y = np.where(m, x - np.nanmean(x), 0.0)
    if lo and hi:
        sos = signal.butter(2, [lo, hi], "bandpass", fs=hz, output="sos")
    elif hi:
        sos = signal.butter(2, hi, "lowpass", fs=hz, output="sos")
    else:
        sos = signal.butter(2, lo, "highpass", fs=hz, output="sos")
    y = signal.sosfiltfilt(sos, y)
    y[~m] = np.nan
    return y


def _xcorr_lag(a, b, hz, max_lag_s):
    """Lag (s), um den b gegen a verschoben ist (a(t) ~ b(t+lag)), und normiertes r."""
    m = np.isfinite(a) & np.isfinite(b)
    a = np.where(m, a - np.nanmean(a), 0)
    b = np.where(m, b - np.nanmean(b), 0)
    c = signal.correlate(b, a, mode="full", method="fft")
    lags = np.arange(-len(a) + 1, len(b))
    k = np.abs(lags) <= max_lag_s * hz
    i = np.argmax(c[k])
    return lags[k][i] / hz, c[k][i] / np.sqrt((a ** 2).sum() * (b ** 2).sum())


# ----------------------------------------------------------------------------- Schritt 0+1
def can_t0(path):
    """Startzeit aus dem Dateinamen. fr["_t0"] (Epoch des ersten Frames) ist bei No-RTC-Logs teils
    Tage daneben (26.09.: ~6,5 d), der Dateiname wurde von der Pipeline korrigiert."""
    return dt.datetime.strptime(os.path.basename(path)[8:25], "%Y-%m-%d_%H%M%S").timestamp()


def align_pair(can_path, dlg_id):
    fr = lab.frames(can_path)
    ct0 = can_t0(can_path)
    ph = phone(dlg_id)
    sp = ph.get("VehicleSpeed") or ph.get("GPS-Geschwindigkeit")
    if sp is None or 0x202 not in fr:
        return None
    t_vs, vs = lab.sig(fr, "VehicleSpeed", 514)
    # grob: 1 Hz ueber gemeinsames Fenster, Suche +-MAX_LAG_S
    lo = min(ct0 + t_vs[0], sp[0][0]) - 10
    hi = max(ct0 + t_vs[-1], sp[0][-1]) + 10
    g = np.arange(lo, hi, 1.0)
    a = _interp(ct0 + t_vs, vs, g)
    b = _interp(sp[0], sp[1], g)
    # Luecken ausserhalb des Logs als 0 behandeln, damit FFT-Korrelation Stillstand nicht bevorzugt
    lag_x, r = _xcorr_lag(np.nan_to_num(a), np.nan_to_num(b), 1.0, MAX_LAG_S)
    # zwei Kandidaten: Korrelationsspitze und "Uhren stimmen" (lag 0); die Gierraten-Pruefung entscheidet
    # (27.09. 112539: Spitze bei +256 s falsch, weil Log mit langem Stillstand beginnt)
    best = None
    for lag in {lag_x, 0.0}:
        res = _align_at(fr, ph, can_path, dlg_id, ct0, t_vs, sp, lag, r)
        if best is None or (res.get("r_yaw") or -1) > (best.get("r_yaw") or -1):
            best = res
    return best


def _align_at(fr, ph, can_path, dlg_id, ct0, t_vs, sp, lag, r):
    ov = min(ct0 + t_vs[-1], sp[0][-1] - lag) - max(ct0 + t_vs[0], sp[0][0] - lag)
    if ov < MIN_OVERLAP_S or not r > 0.3:
        return {"can": os.path.basename(can_path), "dlg": dlg_id, "coarse_lag_s": lag, "coarse_r": r,
                "overlap_s": ov, "ok": False}
    # gemeinsames 100-Hz-Raster in CAN-Zeit (relativ zu ct0)
    g100 = np.arange(max(t_vs[0], sp[0][0] - lag - ct0), min(t_vs[-1], sp[0][-1] - lag - ct0), 0.01)
    lat = _interp(*lab.sig(fr, "Lateral_Acc_Raw", 117), g100)
    lon = _interp(*lab.sig(fr, "Longitudinal_Acc_Raw", 118), g100)
    R, lag_f, r_f, drift = _rotation_and_fine(ph, ct0, lag, g100, lat, lon)
    yaw = _interp(*lab.sig(fr, "YawRate_Raw", 117), g100)
    wz = _gyro(ph, g100 + ct0 + lag_f) @ np.asarray(R)[2]
    m = np.isfinite(yaw) & np.isfinite(wz)
    r_yaw = float(np.corrcoef(yaw[m], wz[m])[0, 1]) if m.sum() > 1000 else np.nan
    return {"can": os.path.basename(can_path), "dlg": dlg_id, "coarse_lag_s": lag, "coarse_r": r,
            "overlap_s": float(g100[-1] - g100[0]), "lag_s": lag_f, "fine_r_lat": r_f, "drift_s": drift,
            "r_yaw": r_yaw, "R": R.tolist(), "ok": bool(r_yaw > 0.9)}


def _phone_vec(ph, pre, g_abs):
    return np.column_stack([_interp(*ph[pre + ax], g_abs) for ax in "XYZ"])


def _mid_lag(ph, ct0, lag, g, lat, lon, hz=100.0):
    """Zwischenstufe: die 1-Hz-Geschwindigkeitskorrelation liegt oft 2-4 s daneben (27.09.: -4 s statt
    -1,1 s), die Achsenschaetzung braucht aber < ~1 s. Daher Lag-Scan +-6 s mit einer lageunabhaengigen
    Guete: Regression der CAN-Laengs-/Querbeschl. auf alle drei Handy-Achsen (0,5-Hz-Tiefpass)."""
    step = 5
    gs, las, los = g[::step], lat[::step], lon[::step]
    h = hz / step
    Y = np.column_stack([_bp(los, None, 0.5, h), _bp(las, None, 0.5, h)])
    best = (-1, lag)
    for d in np.arange(-6, 6.01, 0.2):
        acc = _phone_vec(ph, "AccelerationWithGravity", gs + ct0 + lag + d)
        A = np.column_stack([_bp(acc[:, k], None, 0.5, h) for k in range(3)] + [np.ones(len(gs))])
        m = np.isfinite(A).all(axis=1) & np.isfinite(Y).all(axis=1)
        if m.sum() < 1000:
            continue
        q = np.mean([np.corrcoef(A[m] @ np.linalg.lstsq(A[m], Y[m, j], rcond=None)[0], Y[m, j])[0, 1]
                     for j in range(2)])
        best = max(best, (q, lag + d))
    return best[1]


def _gyro(ph, g_abs, max_dps=60.0):
    """RotationRate (°/s) mit Ausreisser-Maske: Einzelspitzen bis ~160 °/s sind Sensor-Glitches
    (eine Karosserie wankt/nickt nie so schnell; Gieren im Alltag < 60 °/s)."""
    out = []
    for ax in "XYZ":
        t, v = ph["RotationRate" + ax]
        v = np.where(np.abs(v) > max_dps, np.nan, v)
        out.append(_interp(t, v, g_abs))
    return np.column_stack(out)


def _rotation_and_fine(ph, ct0, lag, g, lat, lon, hz=100.0):
    lag = _mid_lag(ph, ct0, lag, g, lat, lon, hz)

    def est(lag_):
        gabs = g + ct0 + lag_
        acc = _phone_vec(ph, "AccelerationWithGravity", gabs)
        ev = np.nanmean(acc, axis=0)
        ev /= np.linalg.norm(ev)
        # Laengsachse: Regression tiefpass-gefiltert (robust gegen Restversatz)
        A = np.column_stack([_bp(acc[:, k], None, 1.0, hz) for k in range(3)])
        y = _bp(lon, None, 1.0, hz)
        m = np.isfinite(A).all(axis=1) & np.isfinite(y)
        w = np.linalg.lstsq(A[m], y[m], rcond=None)[0]
        ex = w - (w @ ev) * ev
        ex /= np.linalg.norm(ex)
        ey = np.cross(ev, ex)
        yl = _bp(lat, None, 1.0, hz)
        if np.nansum(_bp(acc @ ey, None, 1.0, hz) * yl) < 0:
            ey = -ey
        return np.vstack([ex, ey, ev]), acc
    R, acc = est(lag)
    # fein: Querbeschleunigung 0,2-5 Hz, +-3 s
    a = _bp(lat, 0.2, 5.0, hz)
    b = _bp(acc @ R[1], 0.2, 5.0, hz)
    d, r = _xcorr_lag(np.nan_to_num(a), np.nan_to_num(b), hz, 3.0)
    lag_f = lag + d
    R, acc = est(lag_f)
    # Drift: Feinlag in erster vs. zweiter Haelfte
    half = len(g) // 2
    lags = []
    for sl in (slice(0, half), slice(half, None)):
        a = _bp(lat[sl], 0.2, 5.0, hz)
        b = _bp((acc @ R[1])[sl], 0.2, 5.0, hz)
        lags.append(_xcorr_lag(np.nan_to_num(a), np.nan_to_num(b), hz, 1.0)[0])
    a = _bp(lat, 0.2, 5.0, hz)
    b = _bp(acc @ R[1], 0.2, 5.0, hz)
    _, r = _xcorr_lag(np.nan_to_num(a), np.nan_to_num(b), hz, 0.05)
    return R, float(lag_f), float(r), float(lags[1] - lags[0])


def build_pairs(can_filter=None):
    dl = _dl()
    cans = [r[0] for r in dl.sql("select log_id from logs where source_format like 'can%' "
                                  "and start_time_local>='2026-09-12' order by 1").fetchall()]
    dlgs = dlg_logs()
    starts = {d: dt.datetime.strptime(d, "%Y-%m-%d %H%M%S") for d in dlgs}
    out = []
    for c in cans:
        if can_filter and can_filter not in c:
            continue
        path = f"data/can/{c}.log"
        if not os.path.exists(path):
            continue
        cday = dt.datetime.strptime(c[8:25], "%Y-%m-%d_%H%M%S")
        # Kandidaten: gleicher Tag +-1 (Uhrfehler bis ~7 h beobachtet)
        for d in dlgs:
            if abs((starts[d] - cday).total_seconds()) > 12 * 3600:
                continue
            res = align_pair(path, d)
            if res and res["overlap_s"] > 0:
                out.append(res)
                print(f"{c} <-> {d}: coarse r={res['coarse_r']:.2f} lag={res['coarse_lag_s']:+.0f}s "
                      f"ov={res['overlap_s']:.0f}s" + (f"  yaw r={res['r_yaw']:.3f} lat r={res['fine_r_lat']:.2f} lag={res['lag_s']:+.3f}s "
                                                        f"drift={res['drift_s']*1000:+.0f}ms" if 'lag_s' in res else "")
                      + ("  OK" if res["ok"] else ""), flush=True)
    return out


# ----------------------------------------------------------------------------- Schritt 1-3
ANCHOR_FILTERS = {           # Anker: (lo, hi) Hz; gleicher Filter auf Kandidaten
    "a_z":   (0.5, 5.0),
    "phi":   (0.05, 2.0),
    "theta": (0.05, 2.0),
    "p":     (0.2, 2.0),
    "q":     (0.2, 2.0),
}
FAST = {"a_z", "p", "q"}     # nur IDs >= 20 Hz


def anchors(pair, fr, g):
    ph = phone(pair["dlg"])
    R = np.array(pair["R"])
    gabs = g + can_t0(fr["_path"]) + pair["lag_s"]
    f = _phone_vec(ph, "AccelerationWithGravity", gabs) @ R.T / 9.81
    w = _gyro(ph, gabs) @ R.T
    p, q = w[:, 0], w[:, 1]

    def integ(x):
        return np.cumsum(np.nan_to_num(_bp(x, 0.03, None, HZ))) / HZ

    return {"a_z": f[:, 2] - np.nanmean(f[:, 2]), "p": p, "q": q, "phi": integ(p), "theta": integ(q)}


def known_matrix(fr, g):
    cols = []
    for name, mid in KNOWN:
        try:
            cols.append(_interp(*lab.sig(fr, name, mid), g))
        except KeyError:
            pass
    return np.column_stack(cols)


def _resid(y, X):
    m = np.isfinite(y) & np.isfinite(X).all(axis=1)
    out = np.full_like(y, np.nan)
    if m.sum() < 100:
        return out
    Xm = np.column_stack([X[m], np.ones(m.sum())])
    out[m] = y[m] - Xm @ np.linalg.lstsq(Xm, y[m], rcond=None)[0]
    return out


def _corr_cols(F, y):
    m = np.isfinite(y)
    Fm = F[m] - np.nanmean(F[m], axis=0)
    ym = y[m] - y[m].mean()
    Fm = np.nan_to_num(Fm)
    den = np.sqrt((Fm ** 2).sum(axis=0) * (ym ** 2).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        return (Fm * ym[:, None]).sum(axis=0) / den


def analyze_pair(pair, own):
    path = f"data/can/{pair['can']}"
    fr = lab.frames(path)
    g = lab.grid(fr, HZ)
    A = anchors(pair, fr, g)
    valid = np.isfinite(A["a_z"]) & np.isfinite(A["p"])
    g = g[valid]
    A = {k: v[valid] for k, v in A.items()}
    K = known_matrix(fr, g)
    cands = candidates(fr, own)
    rate = {cid: len(fr[cid][0]) / (fr[cid][0][-1] - fr[cid][0][0] + 1e-9) for cid in {c[0] for c in cands}}
    rows = []
    for an, (lo, hi) in ANCHOR_FILTERS.items():
        y = _bp(A[an], lo, hi, HZ)
        Kf = np.column_stack([_bp(K[:, j], lo, hi, HZ) for j in range(K.shape[1])])
        yr = _resid(y, Kf)
        sel = [c for c in cands if an not in FAST or rate[c[0]] >= 20]
        for i0 in range(0, len(sel), 200):
            chunk = sel[i0:i0 + 200]
            F = np.column_stack([_bp(lab.on_grid(t, raw, g).astype(float), lo, hi, HZ) for _, _, t, raw in chunk])
            r_raw = _corr_cols(F, y)
            r_res = _corr_cols(F, yr)
            for j, (cid, name, _, _) in enumerate(chunk):
                rows.append((pair["can"][8:25], f"0x{cid:03X}", name, an, rate[cid], r_raw[j], r_res[j]))
        # Gegenprobe: bekannte Signale selbst
        for j, (name, _) in enumerate(KNOWN[:K.shape[1]]):
            rows.append((pair["can"][8:25], "known", name, an, np.nan, _corr_cols(Kf[:, [j]], y)[0], np.nan))
    return pd.DataFrame(rows, columns=["log", "can_id", "field", "anchor", "rate_hz", "r_raw", "r_res"])


def aggregate(raw):
    x = raw[raw.can_id != "known"].copy()
    g = x.groupby(["can_id", "field", "anchor"])
    a = g.agg(pairs=("log", "nunique"), rate_hz=("rate_hz", "median"),
              r_raw_med=("r_raw", "median"), r_res_med=("r_res", "median"),
              r_res_absmin=("r_res", lambda s: s.abs().min()),
              sign_consistent=("r_res", lambda s: (np.sign(s) == np.sign(s.median())).mean())).reset_index()
    a["score"] = a.r_res_med.abs() * a.sign_consistent
    return a.sort_values("score", ascending=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs-only", action="store_true")
    ap.add_argument("--pair", help="nur CAN-Logs, deren Name dies enthaelt")
    ap.add_argument("--reuse-pairs", action="store_true", help="data/can_phone_pairs.json nicht neu bauen")
    a = ap.parse_args()
    if a.reuse_pairs and os.path.exists(PAIRS_JSON):
        pairs = json.load(open(PAIRS_JSON))
    else:
        pairs = build_pairs(a.pair)
        json.dump(pairs, open(PAIRS_JSON, "w"), indent=1)
    ok = [p for p in pairs if p["ok"]]
    print(f"{len(ok)} brauchbare Paare von {len(pairs)} Kandidaten")
    if a.pairs_only:
        return
    own = dbc_owner()
    raw = pd.concat([analyze_pair(p, own) for p in ok if not a.pair or a.pair in p["can"]])
    os.makedirs("results", exist_ok=True)
    raw.to_csv("results/can_phone_imu_sweep_raw.csv", index=False)
    agg = aggregate(raw)
    agg.to_csv("results/can_phone_imu_sweep.csv", index=False)
    kn = raw[raw.can_id == "known"].groupby(["anchor", "field"]).r_raw.median().unstack()
    print("\nGegenprobe bekannte Signale (median r, roh):\n", kn.round(2))
    for an in ANCHOR_FILTERS:
        print(f"\n== {an}")
        print(agg[agg.anchor == an].head(12).to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
