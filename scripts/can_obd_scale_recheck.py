"""
Nachrechnung aller CAN-Skalen, die gegen OBD-Werte bestimmt wurden (2026-09-29).

Anlass: die Sweep-Werkzeuge (`can_byte_search._resample`, `can_find_native_counterpart`) haben
die duenne OBD-Referenz linear auf ein festes Raster interpoliert, statt den schnellen
CAN-Kandidaten an den Referenz-Zeitstempeln abzulesen. Am bekannten Feld 0x082 gegen
STEER_SPD_EPS kostete das R2 0,990 -> 0,886 und 10 % Skala (Logbuch 2026-09-29).

Je Signal und Log zwei Fits `ref = a * cand + b`, beide mit Lag-Suche +-1 s:
  alt   - Kandidat UND Referenz linear auf ein 5-Hz-Raster (wie die Y-Splitter-Kalibrierung
          vom 12.09. und die Sweeps)
  neu   - Kandidat an den Referenz-Zeitstempeln (+Lag) abgelesen, Referenz unveraendert
Rohwert-Formeln der Mode-22-DIDs am 29.09. exakt aus der gepaarten .dlg bestimmt (R2=1,000,
candump-2026-09-12_211833 gegen 2026-09-12 211851.dlg): BFP_PRE_MZ kPa = raw,
CPP_PER_MZ % = raw*100/65535, FLI % = raw*100/256. STEER_SPD_EPS bleibt im Rohwert.
Alle Fits beziehen sich auf den BIT-Rohwert des CAN-Feldes (nicht den DBC-skalierten Wert).

Ausgabe: results/can_obd_scale_recheck.csv (je Log) + Konsole (gepoolt je Signal).
Aufruf:  .venv/bin/python scripts/can_obd_scale_recheck.py [--self-test] [--signal NAME ...]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab
from obd_from_can import decode_obd_traffic, extract_did_series

LAGS = np.arange(-1.0, 1.0001, 0.02)
OLD_HZ = 5.0
MAX_CAND_GAP_S = 0.1   # Referenzpunkt nur werten, wenn ein CAN-Frame so nah dran liegt

# name: (can_id, dbc_signal, (mode, id), ref_formel oder None=Rohwert, (a_doc, b_doc), Quelle)
# a_doc/b_doc: dokumentierte Beziehung ref = a*cand_raw + b (in Referenz-Einheit).
SPECS = {
    "BrakePressure": (0x078, "BrakePressure", ("mode22", 0x280A), lambda r: r / 100,
                      (0.0012413, 32.7986), "DBC 12.09., bar gegen BFP_PRE_MZ (dlg), 5-Hz-Raster"),
    "Clutch_Pedal_Position_raw": (0x130, "Clutch_Pedal_Position_raw", ("mode22", 0x0478),
                                  lambda r: r * 100 / 65535, (0.4665, 0.56),
                                  "Logbuch 12.09., % gegen CPP_PER_MZ (dlg)"),
    # dokumentiert war FLI% = 2,486 * Fuel_Tank_raw, dort aber der DBC-Wert (0,2 je Bit)
    "Fuel_Tank": (0x09E, "Fuel_Tank", ("mode22", 0xF42F), lambda r: r * 100 / 256,
                  (2.486 * 0.2, -0.02), "Logbuch 12.09., % gegen FLI (dlg), 15-s-Median"),
    "ActualEnginePercentTorque": (0x167, "ActualEnginePercentTorque", ("mode1", 0x62),
                                  lambda r: r - 125, (3.0242, -177.849),
                                  "DBC 15.09., % gegen PID 0x62, can_find_native_counterpart"),
    "DCDC_Voltage": (0x08A, "DCDC_Voltage", ("mode1", 0x42), lambda r: r / 1000, (0.0199, 0.08),
                     "DBC 26.09., V gegen PID 0x42 (DBC-Skala 0,02)"),
    "BCM_SupplyVoltage": (0x43F, "BCM_SupplyVoltage", ("mode1", 0x42), lambda r: r / 1000,
                          (0.016, 0.68), "DBC 26.09., 0,016 V/LSB + 0,68 V"),
    "BattSensor_Voltage_maybe": (0x45A, "BattSensor_Voltage_maybe", ("mode1", 0x42),
                                 lambda r: r / 1000, (0.00196, 0.19), "DBC 26.09."),
    # dokumentiert: EPS_raw = 0,241-0,247 * Kanal - 1, Kanal = 0,5 deg/s je Bit
    "SteeringRate_Abs_maybe": (0x082, "SteeringRate_Abs_maybe", ("mode22", 0x3301), None,
                               (0.244 * 0.5, -1.0), "DBC 26.09., EPS-Rohwert"),
}


def _fit(x, y):
    if len(x) < 30 or np.std(x) < 1e-9 or np.std(y) < 1e-9:
        return None
    a, b = np.polyfit(x, y, 1)
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    return a, b, r2


def fit_new(tc, vc, tr, vr):
    """Kandidat an den Referenz-Zeitstempeln ablesen (bestes Lag nach R2)."""
    best = None
    for lag in LAGS:
        tq = tr + lag
        i = np.clip(np.searchsorted(tc, tq), 1, len(tc) - 1)
        near = np.minimum(np.abs(tq - tc[i - 1]), np.abs(tc[i] - tq)) <= MAX_CAND_GAP_S
        f = _fit(np.interp(tq[near], tc, vc), vr[near])
        if f and (best is None or f[2] > best[2]):
            best = (*f, lag, int(near.sum()))
    return best


def fit_old(tc, vc, tr, vr):
    """Beide Reihen linear auf ein 5-Hz-Raster, dort Lag-Suche und Fit (bisherige Methode)."""
    g = np.arange(max(tc[0], tr[0]) + 1, min(tc[-1], tr[-1]) - 1, 1 / OLD_HZ)
    yr = np.interp(g, tr, vr)
    best = None
    for lag in LAGS[::5]:   # 0,1-s-Schritte reichen bei 5 Hz
        f = _fit(np.interp(g + lag, tc, vc), yr)
        if f and (best is None or f[2] > best[2]):
            best = (*f, lag, len(g))
    return best


def series(fr, obd_dec, spec):
    cid, sig_name, (mode, id_), formula, _, _ = spec
    s = next(s for m in lab.db().messages if m.frame_id == cid for s in m.signals if s.name == sig_name)
    tc, raw = lab.field(fr, cid, s.start, s.length, s.byte_order == "big_endian", s.is_signed)
    ref = extract_did_series(obd_dec, id_, mode=mode)
    tr = ref["t"].to_numpy(float)
    vr = ref["raw_value"].to_numpy(float)
    if formula is not None:
        vr = formula(vr)
    o = np.argsort(tr)
    return tc.astype(float), raw.astype(float), tr[o], vr[o]


def obd_decoded(fr):
    rows = [(tt, cid, bytes(p)) for cid, v in fr.items()
            if isinstance(cid, int) and 0x700 <= cid <= 0x7FF for tt, p in zip(*v)]
    if not rows:
        return None
    return decode_obd_traffic(pd.DataFrame(rows, columns=["t", "can_id", "data"]).sort_values("t"))


def run(names):
    rows, pooled = [], {n: [] for n in names}
    for path in lab.logs():
        fr = lab.frames(path)
        dec = obd_decoded(fr)
        if dec is None or dec.empty:
            continue
        for n in names:
            spec = SPECS[n]
            if spec[0] not in fr:
                continue
            tc, vc, tr, vr = series(fr, dec, spec)
            if len(tr) < 30:
                continue
            new, old = fit_new(tc, vc, tr, vr), fit_old(tc, vc, tr, vr)
            if not new or not old:
                continue
            lag = new[3]
            tq = tr + lag
            i = np.clip(np.searchsorted(tc, tq), 1, len(tc) - 1)
            near = np.minimum(np.abs(tq - tc[i - 1]), np.abs(tc[i] - tq)) <= MAX_CAND_GAP_S
            pooled[n].append((np.interp(tq[near], tc, vc), vr[near]))
            rows.append(dict(signal=n, log=os.path.basename(path)[8:25], n_ref=len(tr),
                             ref_dt_s=float(np.median(np.diff(tr))),
                             a_new=new[0], b_new=new[1], r2_new=new[2], lag_new=new[3],
                             a_old=old[0], b_old=old[1], r2_old=old[2], lag_old=old[3]))
        print(f"  {os.path.basename(path)}: {sum(r['log'] == os.path.basename(path)[8:25] for r in rows)} Signale",
              flush=True)
    df = pd.DataFrame(rows)
    os.makedirs("results", exist_ok=True)
    df.to_csv("results/can_obd_scale_recheck.csv", index=False)
    return df, pooled


def report(df, pooled):
    print("\n=== gepoolt je Signal (neu = an Referenz-Zeitstempeln, je Log bestes Lag) ===")
    for n, parts in pooled.items():
        if not parts:
            print(f"{n}: keine Logs mit dieser Referenz")
            continue
        x = np.concatenate([p[0] for p in parts])
        y = np.concatenate([p[1] for p in parts])
        a_n, b_n, r2_n = _fit(x, y)
        d = df[df.signal == n]
        a_doc, b_doc = SPECS[n][4]
        print(f"\n{n}  ({SPECS[n][5]})")
        print(f"  Logs {len(d)}, Referenzabstand median {d.ref_dt_s.median():.2f} s, Lag neu "
              f"{d.lag_new.min():+.2f}..{d.lag_new.max():+.2f} s")
        print(f"  je Log: a_neu {d.a_new.min():.5g}..{d.a_new.max():.5g} (R2 {d.r2_new.min():.3f}.."
              f"{d.r2_new.max():.3f}) | a_alt {d.a_old.min():.5g}..{d.a_old.max():.5g} "
              f"(R2 {d.r2_old.min():.3f}..{d.r2_old.max():.3f})")
        ratio = (d.a_new / d.a_old)
        print(f"  Verhaeltnis a_neu/a_alt je Log: {ratio.min():.4f}..{ratio.max():.4f} (median {ratio.median():.4f})")
        print(f"  dokumentiert: {a_doc:.6g}*raw{b_doc:+.4g}  ->  neu gepoolt: {a_n:.6g}*raw{b_n:+.6g} "
              f"(R2 {r2_n:.4f}, n={len(x)}, {(a_n / a_doc - 1) * 100:+.1f} % Skala)")


def self_test():
    # schnelles Signal (100 Hz) mit Dynamik, Referenz = 2*Signal, nur alle 0,5 s abgetastet
    t = np.arange(0, 600, 0.01)
    rng = np.random.default_rng(0)
    x = np.convolve(rng.normal(size=len(t)), np.ones(30) / 30, mode="same") * 50
    tr = np.arange(1, 599, 0.5) + rng.uniform(0, 0.1, 1196)
    vr = 2 * np.interp(tr, t, x) + 3
    new, old = fit_new(t, x, tr, vr), fit_old(t, x, tr, vr)
    assert abs(new[0] - 2) < 0.01 and new[2] > 0.999 and abs(new[3]) < 0.03, new
    assert old[0] < 1.9 and old[2] < 0.95, old     # alte Methode verzerrt nachweislich
    print(f"self-test ok (neu a={new[0]:.3f} R2={new[2]:.3f}, alt a={old[0]:.3f} R2={old[2]:.3f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--signal", action="append", choices=sorted(SPECS))
    args = ap.parse_args()
    if args.self_test:
        self_test()
        sys.exit(0)
    df, pooled = run(args.signal or list(SPECS))
    report(df, pooled)
