"""
Anker-Sweep mit synthetischen Referenzgroessen (Plan-Schritt 2, docs/plans/can-offline-ausbeute-plan.md).

Unterschied zu can_byte_search.py:
- Referenzen sind nicht nur gemessene Kanaele, sondern auch daraus ABGELEITETE Groessen, wie
  sie Steuergeraete intern rechnen: Kraftstoffstrom und -verbrauch, Bordcomputer-Mittelwerte,
  Fahrbahnsteigung, Gierrate aus den Radgeschwindigkeiten, Gierabweichung, Schlupf,
  Radbeschleunigung, Leistung, Uebersetzung rpm/v, Zeit seit Start, Batteriespannung.
- Freie Bits werden bitgenau aus der DBC bestimmt (nicht byteweise).
- Kandidaten: Bytes, Nibbles, Byte-Paare (BE/LE), jeweils unsigned und signed.
- Zwei Korrelationsarten je Paar: Spearman roh (fuer langsame Groessen: Temperaturen,
  Zaehler, Mittelwerte) und Pearson auf trendbereinigten Reihen (schnelle Dynamik).
- Aggregation ueber alle Logs: ein Treffer zaehlt nur, wenn er in vielen Logs mit gleichem
  Vorzeichen haelt.

Ausgabe: results/can_anchor_sweep_raw.csv (je Log), results/can_anchor_sweep.csv (aggregiert)
Aufruf:  .venv/bin/python scripts/can_anchor_sweep.py [--self-test] [--log PFAD ...]
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy.stats import rankdata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab

HZ = 10.0
DETREND_S = 20.0
HIT_R = 0.8
MIN_UNIQUE = 3
WHEELBASE_M, TRACK_M = 2.31, 1.50
warnings.filterwarnings("ignore", category=RuntimeWarning)


# ----------------------------------------------------------------------------- Anker
def _smooth(x, n):
    if n <= 1:
        return x
    k = np.ones(n) / n
    y = np.convolve(np.nan_to_num(x, nan=np.nanmean(x) if np.isfinite(x).any() else 0), k, "same")
    y[~np.isfinite(x)] = np.nan
    return y


def _ddt(x, hz=HZ, n=5):
    return np.gradient(_smooth(x, n)) * hz


def _s(fr, g, name):
    try:
        return lab.on_grid(*lab.sig(fr, name), g)
    except KeyError:
        return np.full(len(g), np.nan)


def anchors(fr, obd, g):
    A = {}
    v = _s(fr, g, "VehicleSpeed")
    ws = np.array([_s(fr, g, f"WheelSpeed_{i}") for i in range(1, 5)])
    ws[ws > 400] = np.nan
    rpm = _s(fr, g, "EngineRPM")
    lat = _s(fr, g, "Lateral_Acc_Raw")
    lon = _s(fr, g, "Longitudinal_Acc_Raw")
    yaw = _s(fr, g, "YawRate_Raw")
    steer = _s(fr, g, "Steering_Wheel_Absolute_Angle")
    app = _s(fr, g, "APP_Accelerator_Pedal_Position")
    mapk = _s(fr, g, "MAP_Manifold_absolute_pressure_sensor")
    tq = _s(fr, g, "ActualEnginePercentTorque")
    fuelcut = _s(fr, g, "FuelCut")
    o = {k: lab.on_grid(t, val, g) for k, (t, val) in obd.items()}

    A.update(speed=v, rpm=rpm, app=app, brake=_s(fr, g, "BrakePressure"), lat=lat, lon=lon, yaw=yaw,
             steer=steer, clutch=_s(fr, g, "Clutch_Pedal_Position_raw"), gear=_s(fr, g, "MT_Gear_Actual"),
             coolant=_s(fr, g, "CoolantTemp"), iat=_s(fr, g, "IAT_Sensor_No1"), ambient=_s(fr, g, "AmbientTemp"),
             fuel_level=_s(fr, g, "Fuel_Tank"), map=mapk, baro=_s(fr, g, "BARO_Barometric_pressure"),
             torque_pct=tq, steer_rate=_s(fr, g, "SteeringRate_Abs_maybe"), fuelcut=fuelcut,
             steer_torque=_s(fr, g, "SteeringTorque_maybe"))
    for i in range(4):
        A[f"ws{i + 1}"] = ws[i]
        A[f"ws{i + 1}_acc"] = _ddt(ws[i] / 3.6) / 9.81
    for k in ("OilTemp_OBD", "MassAirFlow_OBD", "LambdaCommanded_OBD", "TimingAdvance_OBD",
              "KnockRetard_OBD", "ThrottlePosition_OBD", "BatteryVoltage_OBD"):
        A[k.replace("_OBD", "").lower()] = o.get(k, np.full(len(g), np.nan))

    # --- abgeleitet
    dvdt = _ddt(v / 3.6, n=10) / 9.81
    A["dvdt"] = dvdt
    A["slope"] = _smooth(lon - dvdt, 20)
    front, rear = np.nanmean(ws[:2], 0), np.nanmean(ws[2:], 0)
    A["slip_rear_front"] = rear - front
    A["slip_ratio"] = (rear - front) / np.clip(front, 5, None)
    A["ws_front_lr"] = ws[0] - ws[1]
    A["ws_rear_lr"] = ws[2] - ws[3]
    A["yaw_wheels"] = np.degrees((ws[2] - ws[3]) / 3.6 / TRACK_M)
    vs = v / 3.6
    kin = vs * np.radians(steer) / 15.5 / WHEELBASE_M          # kinematische Gierrate (rad/s)
    ok = np.isfinite(kin) & np.isfinite(yaw) & (np.abs(lat) < 0.3) & (v > 20)
    gain = np.polyfit(np.degrees(kin[ok]), yaw[ok], 1)[0] if ok.sum() > 100 else 1.0
    A["yaw_model"] = np.degrees(kin) * gain
    A["yaw_dev"] = yaw - A["yaw_model"]
    A["curvature"] = np.where(v > 10, np.radians(yaw) / np.clip(vs, 3, None), np.nan)
    A["lat_kin"] = np.radians(yaw) * vs / 9.81
    A["jerk"] = _ddt(lon, n=3)
    A["abs_lat"] = np.abs(lat)
    A["abs_steer"] = np.abs(steer)
    A["abs_yaw"] = np.abs(yaw)
    A["gear_ratio"] = np.where(v > 5, rpm / np.clip(v, 5, None), np.nan)
    A["power_proxy"] = np.clip(tq, 0, None) * rpm
    A["airflow_proxy"] = rpm * mapk
    lam = A["lambdacommanded"]
    lam = np.where(np.isfinite(lam), lam, 1.0)
    maf = np.where(np.isfinite(A["massairflow"]), A["massairflow"], 0.00019 * rpm * mapk - 8.08)
    fuel = np.clip(maf, 0, None) / (14.7 * np.clip(lam, 0.7, 2.0))
    fuel = np.where(fuelcut > 0, 0, fuel)                    # g/s
    A["fuel_rate"] = fuel
    A["fuel_cum"] = np.nancumsum(fuel) / HZ
    dist = np.nancumsum(np.nan_to_num(vs)) / HZ / 1000       # km
    A["dist_cum"] = dist
    A["t_since_start"] = g
    A["t_engine_on"] = np.cumsum(rpm > 400) / HZ
    A["trip_avg_speed"] = np.where(g > 60, dist / (g / 3600), np.nan)
    A["trip_avg_cons"] = np.where(dist > 1, A["fuel_cum"] / 745 / dist * 100, np.nan)   # l/100 km
    A["inst_cons"] = np.where(v > 5, fuel / 745 * 3600 / np.clip(v, 5, None) * 100, np.nan)
    A["inst_cons_lph"] = fuel / 745 * 3600
    A["coolant_rate"] = _ddt(A["coolant"], n=300)
    return A


# ----------------------------------------------------------------------------- Kandidaten
def dbc_owner():
    own = {}
    for m in lab.db().messages:
        for s in m.signals:
            for b in lab.bit_order(s.start, s.length, s.byte_order == "big_endian"):
                own.setdefault(m.frame_id, set()).add(b)
    return own


def candidates(fr, own):
    """[(can_id, name, t, raw)] freier Felder mit genug Variation, ohne Zaehler."""
    out = []
    for cid, val in fr.items():
        if not isinstance(cid, int) or cid >= 0x700 or len(val[0]) < 100:
            continue
        t, d = val
        used = own.get(cid, set())
        specs = []
        for k in range(8):
            specs.append((f"b{k}", k * 8 + 7, 8, True))
            specs.append((f"b{k}hi", k * 8 + 7, 4, True))
            specs.append((f"b{k}lo", k * 8 + 3, 4, True))
            if k < 7:
                specs.append((f"b{k}-{k + 1}BE", k * 8 + 7, 16, True))
                specs.append((f"b{k}-{k + 1}LE", k * 8, 16, False))
        for name, start, length, be in specs:
            bits = lab.bit_order(start, length, be)
            if sum(b in used for b in bits) > length // 2:
                continue
            _, raw = lab.field(fr, cid, start, length, be)
            if len(np.unique(raw[: 20000])) < MIN_UNIQUE and len(np.unique(raw)) < MIN_UNIQUE:
                continue
            dif = np.diff(raw) % (1 << length)
            vals, cnt = np.unique(dif, return_counts=True)
            top = vals[np.argmax(cnt)]
            if top != 0 and cnt.max() / len(dif) > 0.8:     # Rollzaehler
                continue
            out.append((cid, name, t, raw))
            if length in (8, 16):
                sr = np.where(raw >= 1 << (length - 1), raw - (1 << length), raw)
                if np.ptp(sr) < np.ptp(raw):                 # signed nur, wenn es den Wertebereich verkleinert
                    out.append((cid, name + "s", t, sr))
    return out


# ----------------------------------------------------------------------------- Korrelation
def _detrend(x, n):
    return x - _smooth(x, n)


def correlate(F, A):
    """F: (n x f), A: (n x a) -> (r_spearman, r_detrended) je (f x a).

    Die Raenge von F werden einmal ueber alle Zeilen gebildet (nicht je Anker-Maske neu) -
    bei Ankern mit Luecken ist das eine Naeherung an Spearman, dafuer ~50x schneller."""
    res_s = np.full((F.shape[1], A.shape[1]), np.nan)
    res_d = np.full_like(res_s, np.nan)
    Fr = np.apply_along_axis(rankdata, 0, F)
    Fd = np.column_stack([_detrend(F[:, j], int(DETREND_S * HZ)) for j in range(F.shape[1])])
    for a in range(A.shape[1]):
        m = np.isfinite(A[:, a])
        if m.sum() < 300 or np.nanstd(A[m, a]) == 0:
            continue
        res_s[:, a] = _corr(Fr[m], rankdata(A[m, a]))
        ad = _detrend(np.where(m, A[:, a], np.nanmean(A[:, a])), int(DETREND_S * HZ))[m]
        if np.std(ad) > 0:
            res_d[:, a] = _corr(Fd[m], ad)
    return res_s, res_d


def _corr(X, y):
    X = X - X.mean(axis=0)
    y = y - y.mean()
    den = np.sqrt((X ** 2).sum(axis=0) * (y ** 2).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        return (X * y[:, None]).sum(axis=0) / den


def analyze_log(path, own):
    fr = lab.frames(path)
    g = lab.grid(fr, HZ)
    A = anchors(fr, lab.obd(path), g)
    names = list(A)
    Am = np.column_stack([A[k] for k in names])
    cands = candidates(fr, own)
    F = np.column_stack([lab.on_grid(t, raw, g) for _, _, t, raw in cands])
    # Log-Anfang vor dem ersten Frame jeder Botschaft abschneiden
    valid = np.isfinite(F).all(axis=1)
    rs, rd = correlate(F[valid], Am[valid])
    rows = []
    log = os.path.basename(path)[8:25]
    for i, (cid, name, _, _) in enumerate(cands):
        for a, an in enumerate(names):
            if np.isfinite(rs[i, a]) or np.isfinite(rd[i, a]):
                rows.append((log, f"0x{cid:03X}", name, an, rs[i, a], rd[i, a]))
    return pd.DataFrame(rows, columns=["log", "can_id", "field", "anchor", "r_spear", "r_detr"])


def aggregate(raw):
    out = []
    for method in ("r_spear", "r_detr"):
        x = raw.dropna(subset=[method]).copy()
        x["hit"] = x[method].abs() >= HIT_R
        g = x.groupby(["can_id", "field", "anchor"])
        a = g.agg(logs=("log", "nunique"), hits=("hit", "sum"), r_med=(method, "median"),
                  r_absmin=(method, lambda s: s.abs().min()),
                  sign_consistent=(method, lambda s: (np.sign(s) == np.sign(s.median())).mean()))
        a["method"] = method
        out.append(a.reset_index())
    agg = pd.concat(out)
    agg["hit_share"] = agg["hits"] / agg["logs"]
    return agg.sort_values(["hit_share", "hits"], ascending=False)


def self_test():
    rng = np.random.default_rng(1)
    n = 3000
    a = np.cumsum(rng.normal(size=n))
    F = np.column_stack([a * 2 + 1, rng.normal(size=n), np.exp(a / 20)])
    rs, rd = correlate(F, a[:, None])
    assert rs[0, 0] > 0.99 and rs[2, 0] > 0.99 and abs(rs[1, 0]) < 0.1, rs
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    paths = [a for a in sys.argv[1:] if a.endswith(".log")] or lab.logs()
    own = dbc_owner()
    frames = []
    for p in paths:
        df = analyze_log(p, own)
        print(f"{os.path.basename(p)}: {df[['can_id', 'field']].drop_duplicates().shape[0]} Felder", flush=True)
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    os.makedirs("results", exist_ok=True)
    raw.to_csv("results/can_anchor_sweep_raw.csv", index=False)
    agg = aggregate(raw)
    agg.to_csv("results/can_anchor_sweep.csv", index=False)
    print("-> results/can_anchor_sweep.csv")


if __name__ == "__main__":
    main()
