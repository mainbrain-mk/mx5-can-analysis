"""
Gemeinsame, schnelle Basis fuer die Offline-Auswertung aller CAN-Logs
(docs/plans/can-offline-ausbeute-plan.md).

- `frames(log)`: Rohframes je CAN-ID als numpy (t relativ zum ersten Frame, Payload n x 8),
  beim ersten Lauf aus dem candump geparst und als .npz unter data/can/cache/ abgelegt
  (Parsen eines 1-GB-Logs dauert sonst Minuten, jede Folgeanalyse braucht Sekunden).
- `sig(fr, name)`: ein DBC-Signal vektorisiert dekodieren (gleiche Bitlogik wie cantools,
  Selbsttest vergleicht gegen cantools).
- `field(fr, can_id, start, length, big_endian)`: beliebiges Rohfeld in DBC-Bitnummerierung
  (Bit n = Byte n//8, Bitposition n%8 mit LSB=0) - fuer noch nicht eingetragene Kandidaten.
- `obd(log)`: die OBD-Kanaele aus den Diagnoseantworten (can_log_parser.OBD_CHANNELS).
- `grid(fr, hz)` + `on_grid(t, v, g)`: gemeinsames Zeitraster (sample-and-hold).

Aufruf: .venv/bin/python scripts/can_offline_lab.py --self-test
"""
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CACHE_DIR = "data/can/cache"
MIN_BYTES = 20_000_000
_DB = None


def logs(min_bytes=MIN_BYTES):
    return [p for p in sorted(glob.glob("data/can/candump-*.log")) if os.path.getsize(p) >= min_bytes]


def db():
    global _DB
    if _DB is None:
        from can_log_parser import load_db
        _DB = load_db("hscan")
    return _DB


def _parse(path):
    ts, ids, data = [], [], []
    with open(path) as f:
        for line in f:
            try:
                a, _, rest = line.split(" ", 2)
                i, d = rest.strip().split("#", 1)
                if len(d) > 16 or len(d) % 2:
                    continue
                ts.append(float(a.strip("()")))
                ids.append(int(i, 16))
                data.append(bytes.fromhex(d).ljust(8, b"\x00"))
            except ValueError:
                continue
    t = np.array(ts)
    if not len(t):
        raise ValueError(f"{path}: keine Frames")
    return t - t[0], np.array(ids, dtype=np.int32), np.frombuffer(b"".join(data), np.uint8).reshape(-1, 8), t[0]


def frames(path):
    """{can_id: (t, payload[n,8])} plus '_t0' (Epoch des ersten Frames)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = os.path.join(CACHE_DIR, os.path.basename(path) + ".npz")
    if not os.path.exists(cp) or os.path.getmtime(cp) < os.path.getmtime(path):
        t, ids, d, t0 = _parse(path)
        np.savez(cp, t=t, ids=ids, d=d, t0=t0)
    z = np.load(cp)
    t, ids, d = z["t"], z["ids"], z["d"]
    out = {"_t0": float(z["t0"]), "_path": path}
    order = np.argsort(ids, kind="stable")
    ids_s = ids[order]
    for u, lo in zip(*np.unique(ids_s, return_index=True)):
        hi = np.searchsorted(ids_s, u, side="right")
        sel = order[lo:hi]
        out[int(u)] = (t[sel], d[sel])
    return out


def _u64(d):
    """Payload als Big-Endian-uint64 (Byte0 = hoechstwertiges Byte)."""
    return d.astype(np.uint64) @ (np.uint64(1) << (np.arange(7, -1, -1, dtype=np.uint64) * np.uint64(8)))


def _bit(u, n):
    """DBC-Bit n (Byte n//8, Position n%8 mit LSB=0) aus dem Big-Endian-uint64."""
    return (u >> np.uint64((7 - n // 8) * 8 + n % 8)) & np.uint64(1)


def bit_order(start, length, big_endian):
    """DBC-Bitindizes vom hoechst- zum niedrigstwertigen Bit."""
    if big_endian:
        idx, pos = [], start
        for _ in range(length):
            idx.append(pos)
            pos = pos + 15 if pos % 8 == 0 else pos - 1
        return idx
    return list(range(start + length - 1, start - 1, -1))


def field(fr, can_id, start, length, big_endian=True, signed=False):
    if can_id not in fr:
        return np.array([]), np.array([])
    t, d = fr[can_id]
    u = _u64(d)
    raw = np.zeros(len(u), dtype=np.int64)
    for b in bit_order(start, length, big_endian):
        raw = (raw << 1) | _bit(u, b).astype(np.int64)
    if signed:
        raw = np.where(raw >= 1 << (length - 1), raw - (1 << length), raw)
    return t, raw


def sig(fr, name, msg_id=None):
    """(t, Wert) eines DBC-Signals. msg_id nur noetig, wenn der Name mehrdeutig ist."""
    for m in db().messages:
        if msg_id is not None and m.frame_id != msg_id:
            continue
        for s in m.signals:
            if s.name == name:
                t, raw = field(fr, m.frame_id, s.start, s.length, s.byte_order == "big_endian", s.is_signed)
                return t, raw * s.scale + s.offset
    raise KeyError(name)


def obd(path):
    """{Kanal: (t, Wert)} aus den OBD-Antworten im Log (relativ zum ersten Frame)."""
    import pandas as pd
    from can_log_parser import decode_obd_channels
    fr = frames(path)
    rows = []
    for cid, v in fr.items():
        if isinstance(cid, int) and 0x700 <= cid <= 0x7FF:
            t, d = v
            rows += [(tt, cid, bytes(p)) for tt, p in zip(t, d)]
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["t", "can_id", "data"]).sort_values("t")
    out = decode_obd_channels(df)
    return {k: (g["t"].to_numpy(), g["value"].to_numpy()) for k, g in out.groupby("signal")}


def grid(fr, hz=10.0, max_gap_s=2.0):
    """Zeitraster ueber die Log-Dauer, ohne Luecken > max_gap_s (Uhrsprung der Pi-Uhr ohne RTC,
    z.B. candump-2026-09-11_201950: scheinbar 19 h Dauer, sonst Raster mit 680.000 leeren Punkten)."""
    ref = max((k for k in fr if isinstance(k, int)), key=lambda k: len(fr[k][0]))
    t = fr[ref][0]
    tmax = max(v[0][-1] for k, v in fr.items() if isinstance(k, int) and len(v[0]))
    g = np.arange(0, tmax, 1.0 / hz)
    i = np.searchsorted(t, g).clip(1, len(t) - 1)
    near = np.minimum(np.abs(t[i] - g), np.abs(t[i - 1] - g))
    return g[near <= max_gap_s]


def on_grid(t, v, g):
    """Sample-and-hold auf das Raster; vor dem ersten Sample NaN."""
    if len(t) == 0:
        return np.full(len(g), np.nan)
    i = np.searchsorted(t, g, side="right") - 1
    out = np.asarray(v, dtype=float)[i.clip(0)]
    out[i < 0] = np.nan
    return out


def self_test():
    import cantools  # noqa: F401
    path = logs()[0] if logs() else None
    assert path, "keine Logs"
    fr = frames(path)
    checked = 0
    for m in db().messages:
        if m.frame_id not in fr or not m.signals or m.is_multiplexed():
            continue
        t, d = fr[m.frame_id]
        for k in range(0, len(d), max(1, len(d) // 5))[:5]:
            try:
                ref = m.decode(bytes(d[k]), decode_choices=False, allow_truncated=True)
            except Exception:
                continue
            for s in m.signals:
                _, v = sig({m.frame_id: (t[k:k + 1], d[k:k + 1])}, s.name, m.frame_id)
                assert abs(float(v[0]) - float(ref[s.name])) < 1e-6, (hex(m.frame_id), s.name, v[0], ref[s.name])
                checked += 1
    print(f"self-test ok ({checked} Signalwerte gegen cantools)")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    elif "--build-cache" in sys.argv:
        for p in logs(1):
            try:
                frames(p)
            except ValueError as e:
                print(e)
                continue
            print("cache", os.path.basename(p), flush=True)
