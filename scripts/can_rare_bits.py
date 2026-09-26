"""
Katalog seltener Bits: jede Episode, in der ein Bit seinen seltenen Zustand annimmt, mit
Fahrzustand drumherum - ueber alle Logs.

Warum: die bisherige Ereignissuche (can_event_bit_diff.py, Rarity-Scan vom 15.09.) geht vom
Ereignis aus und verlangt, dass ein Bit einen GROSSEN Teil des Fensters gesetzt ist. Ein
Eingriffsflag, das 0,26 s in einem 37-s-Fenster steht, faellt da durch (so geschehen:
0x211 Byte5 Bit0 um t=726 s in candump-2026-09-15_171047). Hier umgekehrt: vom Bit aus.
Jede Episode wird mit Kontext festgehalten, auch wenn wir sie (noch) nicht deuten koennen.

Selten = der Minderheitszustand eines Bits liegt ueber das Log bei <= RARE_MAX (2 %).
Episoden = zusammenhaengende Laeufe im Minderheitszustand, Luecken < MERGE_S verschmolzen.

Ausgabe:
  results/can_rare_bit_episodes.csv  - eine Zeile pro Episode inkl. Kontext
  results/can_rare_bit_summary.csv   - pro (ID, Bit, Zustand): Logs, Episoden, typischer Kontext

Aufruf: .venv/bin/python scripts/can_rare_bits.py [--self-test]
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab

RARE_MAX = 0.02
MERGE_S = 1.0
MIN_FRAMES = 200
CTX_HZ = 20.0

CONTEXT = {  # Kurzname -> DBC-Signal
    "speed": "VehicleSpeed", "rpm": "EngineRPM", "app": "APP_Accelerator_Pedal_Position",
    "brake": "BrakePressure", "lat": "Lateral_Acc_Raw", "lon": "Longitudinal_Acc_Raw",
    "yaw": "YawRate_Raw", "steer": "Steering_Wheel_Absolute_Angle", "gear": "MT_Gear_Actual",
    "clutch": "Clutch_Pedal_Position_raw", "key": "KeyState", "abs": "ABS_Active",
    "fuelcut": "FuelCut", "coolant": "CoolantTemp",
}


def context_grid(fr):
    g = lab.grid(fr, CTX_HZ)
    ctx = {}
    for k, name in CONTEXT.items():
        try:
            t, v = lab.sig(fr, name)
        except KeyError:
            t, v = np.array([]), np.array([])
        ctx[k] = lab.on_grid(t, v, g)
    ws = []
    for i in range(1, 5):
        t, v = lab.sig(fr, f"WheelSpeed_{i}")
        v = np.where(v > 400, np.nan, v)   # Sentinel 0xFFFF
        ws.append(lab.on_grid(t, v, g))
    ws = np.array(ws)
    ctx["wspread"] = np.nanmax(ws, axis=0) - np.nanmin(ws, axis=0)
    ctx["rear_minus_front"] = np.nanmean(ws[2:], axis=0) - np.nanmean(ws[:2], axis=0)
    return g, ctx


def episodes(t, state_mask):
    """[(t_start, t_end, n_frames)] zusammenhaengender Laeufe, Luecken < MERGE_S verschmolzen."""
    idx = np.flatnonzero(state_mask)
    if not len(idx):
        return []
    out = []
    s = e = idx[0]
    n = 1
    for i in idx[1:]:
        if t[i] - t[e] < MERGE_S:
            e = i
            n += 1
        else:
            out.append((t[s], t[e], n))
            s = e = i
            n = 1
    out.append((t[s], t[e], n))
    return out


def known_owner():
    owner = {}
    for m in lab.db().messages:
        for s in m.signals:
            for b in lab.bit_order(s.start, s.length, s.byte_order == "big_endian"):
                owner[(m.frame_id, b)] = s.name
    return owner


def dbc_bits(d):
    """(n x 64) Bitmatrix in DBC-Nummerierung (Spalte n = Byte n//8, Position n%8, LSB=0)."""
    b = np.unpackbits(d, axis=1)             # Spalte byte*8 + (7-pos)
    perm = [(n // 8) * 8 + (7 - n % 8) for n in range(64)]
    return b[:, perm]


def analyze_log(path, owner):
    fr = lab.frames(path)
    g, ctx = context_grid(fr)
    tend = g[-1] if len(g) else 0
    rows = []
    for cid, v in fr.items():
        if not isinstance(cid, int) or cid >= 0x700:   # Diagnoseverkehr ist kein Broadcast
            continue
        t, d = v
        if len(t) < MIN_FRAMES:
            continue
        bits = dbc_bits(d)
        p = bits.mean(axis=0)
        for b in range(64):
            if p[b] == 0 or p[b] == 1:
                continue
            state = 1 if p[b] <= 0.5 else 0
            frac = p[b] if state == 1 else 1 - p[b]
            if frac > RARE_MAX:
                continue
            for ts, te, n in episodes(t, bits[:, b] == state):
                i0 = int(np.searchsorted(g, ts - 0.2))
                i1 = max(i0 + 1, int(np.searchsorted(g, te + 0.2)))
                r = dict(log=os.path.basename(path)[8:25], can_id=f"0x{cid:03X}", bit=b, state=state,
                         frac_log=frac, t_start=round(ts, 3), dur=round(te - ts, 3), n_frames=n,
                         t_to_end=round(tend - te, 1), known=owner.get((cid, b), ""))
                for k, arr in ctx.items():
                    seg = arr[i0:i1]
                    r[k] = float(np.nanmedian(seg)) if np.isfinite(seg).any() else np.nan
                r["abs_lat_max"] = float(np.nanmax(np.abs(ctx["lat"][i0:i1]))) if np.isfinite(ctx["lat"][i0:i1]).any() else np.nan
                r["wspread_max"] = float(np.nanmax(ctx["wspread"][i0:i1])) if np.isfinite(ctx["wspread"][i0:i1]).any() else np.nan
                rows.append(r)
    return rows


def summarize(ep):
    ep = ep.copy()
    ep["at_start"] = ep["t_start"] < 10
    ep["at_end"] = ep["t_to_end"] < 10
    ep["driving"] = ep["speed"] > 5
    g = ep.groupby(["can_id", "bit", "state", "known"], as_index=False).agg(
        logs=("log", "nunique"), episodes=("log", "size"), dur_med=("dur", "median"),
        frac_log_med=("frac_log", "median"), share_start=("at_start", "mean"),
        share_end=("at_end", "mean"), share_driving=("driving", "mean"),
        speed_med=("speed", "median"), app_med=("app", "median"), brake_med=("brake", "median"),
        lat_absmax_med=("abs_lat_max", "median"), wspread_max_med=("wspread_max", "median"),
        rpm_med=("rpm", "median"), gear_med=("gear", "median"), clutch_med=("clutch", "median"),
        key_med=("key", "median"))
    return g.sort_values(["logs", "episodes"], ascending=False)


def self_test():
    t = np.arange(0, 10, 0.1)
    m = np.zeros(len(t), bool)
    m[10:13] = True
    m[15] = True        # Luecke 0,3 s -> verschmilzt
    m[60:62] = True
    ep = episodes(t, m)
    assert len(ep) == 2 and ep[0][2] == 4 and abs(ep[0][0] - 1.0) < 1e-9, ep
    d = np.zeros((2, 8), np.uint8)
    d[1, 5] = 0b00000100          # Byte5 Position2 = DBC-Bit 42
    assert dbc_bits(d)[1, 42] == 1 and dbc_bits(d)[1].sum() == 1
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    owner = known_owner()
    rows = []
    for p in lab.logs():
        r = analyze_log(p, owner)
        print(f"{os.path.basename(p)}: {len(r)} Episoden", flush=True)
        rows += r
    ep = pd.DataFrame(rows)
    os.makedirs("results", exist_ok=True)
    ep.to_csv("results/can_rare_bit_episodes.csv", index=False)
    s = summarize(ep)
    s.to_csv("results/can_rare_bit_summary.csv", index=False)
    print(f"{len(ep)} Episoden, {len(s)} seltene (ID, Bit, Zustand) -> results/can_rare_bit_*.csv")


if __name__ == "__main__":
    main()
