"""
Zaehler und Pruefsummen der PCM-Botschaften bitgenau pruefen (PCM-Bits-Plan, Strang A2,
docs/plans/pcm-bits-plan.md).

Eine Pruefsummen-Hypothese gilt erst, wenn sie in JEDEM Frame JEDES geprueften Logs stimmt
(Abschlusskriterium Klasse 3). Getestet wird je Kandidaten-Byte eine Familie von Funktionen
der uebrigen Bytes; die Konstante wird nicht geraten, sondern als (pruef - f(daten)) mod 256
bestimmt und muss dann ueberall gleich sein:
  - Summe ueber eine Byte-Teilmenge (+ const), auch negiert (const - Summe)
  - Summe der Nibbles (+ const)
  - XOR ueber die Bytes (^ const)
  - CRC-8 mit gaengigen Polynomen (Init 0x00/0xFF), Ausgabe ^ const
Zaehler: Schrittweite mod 2^n konstant zwischen aufeinanderfolgenden Frames.

Aufruf: .venv/bin/python scripts/pcm_counter_checksum.py [--logs N] [--self-test]
"""
import argparse
import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CRC_POLYS = [0x07, 0x1D, 0x2F, 0x31, 0x39, 0x49, 0x9B, 0xD5]


def _crc_table(poly):
    tab = np.zeros(256, np.uint8)
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ poly) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        tab[i] = c
    return tab


_TABS = {p: _crc_table(p) for p in CRC_POLYS}


def crc8(d, cols, poly, init):
    c = np.full(len(d), init, np.uint8)
    tab = _TABS[poly]
    for b in cols:
        c = tab[c ^ d[:, b]]
    return c


def candidates(d, cs_byte):
    """Funktionen f(daten) -> uint8 ueber die anderen Bytes, als (name, werte)."""
    others = [b for b in range(8) if b != cs_byte]
    d16 = d.astype(np.int64)
    # Summen ueber zusammenhaengende Teilmengen und alle anderen Bytes
    subsets = {tuple(others)}
    for lo in range(8):
        for hi in range(lo + 1, 8):
            s = tuple(b for b in range(lo, hi + 1) if b != cs_byte)
            if s:
                subsets.add(s)
    for s in sorted(subsets):
        sm = d16[:, list(s)].sum(1)
        yield f"sum{list(s)}", (sm & 0xFF).astype(np.uint8)
        yield f"-sum{list(s)}", ((-sm) & 0xFF).astype(np.uint8)
        nib = ((d16[:, list(s)] & 15) + (d16[:, list(s)] >> 4)).sum(1)
        yield f"nibsum{list(s)}", (nib & 0xFF).astype(np.uint8)
    x = np.zeros(len(d), np.uint8)
    for b in others:
        x ^= d[:, b]
    yield f"xor{others}", x
    for poly, init in itertools.product(CRC_POLYS, (0x00, 0xFF)):
        yield f"crc8(poly=0x{poly:02X},init=0x{init:02X}){others}", crc8(d, others, poly, init)


def find_checksum(d, cs_byte, mask=0xFF):
    """[(name, const, trefferquote)] fuer Funktionen, deren (pruef - f) bzw. (pruef ^ f) im
    Kandidaten-Byte (maskiert) in >= 99 % der Frames konstant ist."""
    cs = d[:, cs_byte] & mask
    hits = []
    for name, f in candidates(d, cs_byte):
        for op, diff in (("+", (cs.astype(np.int64) - f) & mask), ("^", (cs ^ f) & mask)):
            vals, cnt = np.unique(diff, return_counts=True)
            q = cnt.max() / len(diff)
            if q >= 0.99:
                hits.append((f"{name} {op} 0x{int(vals[cnt.argmax()]):02X}", q))
    return sorted(hits, key=lambda h: -h[1])


def counter_step(v, bits):
    """(schrittweite, anteil) der haeufigsten Differenz mod 2^bits."""
    dv = np.diff(v.astype(np.int64)) % (1 << bits)
    u, k = np.unique(dv, return_counts=True)
    return int(u[k.argmax()]), float(k.max() / len(dv))


def self_test():
    rng = np.random.default_rng(3)
    n = 5000
    d = rng.integers(0, 256, (n, 8)).astype(np.uint8)
    d[:, 6] = (np.arange(n) % 16).astype(np.uint8)
    d[:, 7] = ((0x43 + d[:, :7].astype(np.int64).sum(1)) & 0xFF).astype(np.uint8)
    names = [h[0] for h in find_checksum(d, 7)]
    assert any(n_.startswith("sum[0, 1, 2, 3, 4, 5, 6] + 0x43") for n_ in names), names[:5]
    d[:, 7] = crc8(d, range(7), 0x1D, 0xFF) ^ 0x5A
    names = [h[0] for h in find_checksum(d, 7)]
    assert any("crc8(poly=0x1D,init=0xFF)" in n_ and "^ 0x5A" in n_ for n_ in names), names[:5]
    d[:, 7] = rng.integers(0, 256, n)
    assert find_checksum(d, 7) == []
    assert counter_step(d[:, 6], 4) == (1, 1.0)
    print("self-test ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()


def gf2_affine_fit(x_bits, y_bit):
    """Loest y = A.x ^ c ueber GF(2) (affin, deckt jede CRC/XOR-Pruefsumme ueber die
    Eingangsbits ab). x_bits: (n, k) 0/1, y_bit: (n,) 0/1. Gibt (konsistent, koeffizienten)
    zurueck; koeffizienten[-1] ist die Konstante. Nur eindeutige Zeilen werden verwendet."""
    a = np.concatenate([x_bits, np.ones((len(x_bits), 1), np.uint8), y_bit[:, None]], 1).astype(np.uint8)
    a = np.unique(a, axis=0)
    k = a.shape[1] - 1
    row = 0
    piv = []
    for col in range(k):
        r = np.nonzero(a[row:, col])[0]
        if not len(r):
            continue
        r = r[0] + row
        a[[row, r]] = a[[r, row]]
        m = a[:, col].astype(bool)
        m[row] = False
        a[m] ^= a[row]
        piv.append(col)
        row += 1
        if row == len(a):
            break
    consistent = not np.any((a[:, :k].sum(1) == 0) & (a[:, k] == 1))
    coef = np.zeros(k, np.uint8)
    for i, col in enumerate(piv):
        coef[col] = a[i, k]
    return consistent, coef


def bits_of(d, byte_cols):
    """(n, 8*len) Bitmatrix, je Byte MSB zuerst."""
    return np.unpackbits(d[:, byte_cols], axis=1)


# --- Hypothesen (22.11., aus candump-2026-11-21_190853 abgeleitet, je ueber ALLE Logs geprueft) ---
def _u16(d, a):
    return d[:, a].astype(np.int64) * 256 + d[:, a + 1]


def _bytesum(v):
    return (v >> 8) + (v & 0xFF)


CHECKSUMS = {
    # name: (can_id, funktion(d) -> erwartetes Pruefbyte, pruef_byte)
    "0x0FE B7 = B6 + 0x43 (B0..B5 konstant mitgezaehlt?)":
        (0x0FE, lambda d: (d[:, 6].astype(np.int64) + 0x43) & 0xFF, 7),
    "0x0FE B7 = 6 + sum(B0..B6)":
        (0x0FE, lambda d: (6 + d[:, :7].astype(np.int64).sum(1)) & 0xFF, 7),
    "0x200 B7 = 0xFF - (B0 + B1 + B6)":
        (0x200, lambda d: (0xFF - d[:, [0, 1, 6]].astype(np.int64).sum(1)) & 0xFF, 7),
    "0x202 B7 = 0x93 - (bytes(B0B1>>3) + B2 + B3 + bytes(B4B5>>6))":
        (0x202, lambda d: (0x93 - _bytesum(_u16(d, 0) >> 3) - d[:, 2] - d[:, 3]
                           - _bytesum(_u16(d, 4) >> 6)) & 0xFF, 7),
    "0x21F B7 = 0x7E - sum(B0..B6)":
        (0x21F, lambda d: (0x7E - d[:, :7].astype(np.int64).sum(1)) & 0xFF, 7),
}

# 0x4FA (22.11.): B6 Bit7..5 = Seite 1..7 (ohne 0), je Seite ein konstanter 7-Bit-Wert in
# B6 Bit4..0 + B7 Bit7..6, B7 Bit5..0 = 4. Abgeleitet aus candump-2026-11-21_190853.
MUX_4FA = {1: 63, 2: 39, 3: 60, 4: 48, 5: 62, 6: 58, 7: 117}
_MUX_LUT = np.array([MUX_4FA.get(k, -1) for k in range(8)])


def _mux4fa_b6(d):
    pg = d[:, 6] >> 5
    return np.where(_MUX_LUT[pg] >= 0, (pg.astype(np.int64) << 5) | (_MUX_LUT[pg] >> 2), -1)


def _mux4fa_b7(d):
    """Erwartetes B7 OHNE Bit 2 (das ist ein Zustandsbit, siehe FLAGS) - Vergleich maskiert."""
    pg = d[:, 6] >> 5
    return np.where(_MUX_LUT[pg] >= 0, ((_MUX_LUT[pg] & 3) << 6) | (d[:, 7] & 4), -1)


CHECKSUMS["0x4FA B6 = Seite<<5 | Tabelle[Seite]>>2"] = (0x4FA, _mux4fa_b6, 6)
CHECKSUMS["0x4FA B7 = (Tabelle[Seite]&3)<<6, Bit5..3+1..0 = 0"] = (0x4FA, _mux4fa_b7, 7)



def _flag_4fa_ready(fr, tol_s=6.0):
    """0x4FA B7 Bit2 gegen 'Motor laeuft (0x202 > 400/min) ODER i-stop-Stopp (0x130 Bit 19
    iStop_EngineStopped)'. Erste Fassung 'seit Logbeginn angesprungen' scheiterte 22.11. an
    3 Logs mit Motor-AUS bei Zuendung EIN (Tankstopp): dort faellt Bit 2 ~4 s spaeter auf 0.
    Frames bis tol_s um jeden Wechsel der Erwartung zaehlen nicht (Uebergang/Verzoegerung)."""
    if 0x4FA not in fr or 0x202 not in fr or 0x130 not in fr:
        return None
    t, d = fr[0x4FA]
    tr, dr = fr[0x202]
    o = np.argsort(tr)
    tr, dr = tr[o], dr[o]
    ti, di = fr[0x130]
    o = np.argsort(ti)
    ti, di = ti[o], di[o]
    rpm = (dr[:, 0].astype(np.int64) * 256 + dr[:, 1]) * 0.25
    stop = (di[:, 2] >> 3) & 1                       # Bit 19 = Byte 2 Bit 3
    run_at = rpm[np.clip(np.searchsorted(tr, t, side="right") - 1, 0, len(tr) - 1)] > 400
    stop_at = stop[np.clip(np.searchsorted(ti, t, side="right") - 1, 0, len(ti) - 1)] == 1
    exp = (run_at | stop_at).astype(np.uint8)
    # Wechselzeitpunkte der Erwartung auf einem dichten Raster (beide Quellen)
    grid = np.union1d(tr, ti)
    g_run = rpm[np.clip(np.searchsorted(tr, grid, side="right") - 1, 0, len(tr) - 1)] > 400
    g_stop = stop[np.clip(np.searchsorted(ti, grid, side="right") - 1, 0, len(ti) - 1)] == 1
    g = (g_run | g_stop).astype(np.int8)
    edges = grid[1:][np.diff(g) != 0]
    if len(edges):
        k = np.clip(np.searchsorted(edges, t), 1, len(edges)) - 1
        near = np.minimum(np.abs(t - edges[k]), np.abs(t - edges[np.minimum(k + 1, len(edges) - 1)]))
    else:
        near = np.full(len(t), np.inf)
    o = np.argsort(t)
    bit = ((d[:, 7] >> 2) & 1)
    return bit[o], exp[o], (near > tol_s)[o]


# Flag-Regeln: name -> (can_id, funktion(fr) -> (bitwerte, erwartet, gueltig) | None)
FLAGS = {
    "0x4FA B7 Bit2 = Motor laeuft oder i-stop-Stopp": (0x4FA, _flag_4fa_ready),
}

COUNTERS = {
    # name: (can_id, funktion(d) -> zaehlerwert, modulus, schritt)
    "0x0FD B2 Bit7..4": (0x0FD, lambda d: d[:, 2] >> 4, 16, 1),
    "0x0FE B6 Bit3..0": (0x0FE, lambda d: d[:, 6] & 15, 16, 1),
    "0x167 B3 Bit7..5": (0x167, lambda d: d[:, 3] >> 5, 8, 1),
    "0x200 B6 Bit3..1": (0x200, lambda d: (d[:, 6] >> 1) & 7, 8, 1),
    "0x21F B6 Bit3..0": (0x21F, lambda d: d[:, 6] & 15, 16, 1),
    # Seitenzaehler 1..7 (ohne 0): Zyklus der Laenge 7, also modulo 7 auf Seite-1 rechnen
    "0x4FA B6 Bit7..5 (Seite 1..7)": (0x4FA, lambda d: (d[:, 6] >> 5).astype(np.int64) - 1, 7, 1),
}


def verify_log(fr):
    """Je Hypothese (CHECKSUMS + COUNTERS) fuer ein Log: (frames, verstoesse) bzw. fuer die
    Zaehler zusaetzlich (schritte0, schritte2). Zaehler-Spruenge zaehlen nur ohne Luecke
    (Zeitabstand < 1,5 Perioden); erlaubt sind 0/1/2 (Rechentakt rutscht gegen Sendeplan)."""
    out = {}
    for k, (cid, f, b) in CHECKSUMS.items():
        if cid in fr:
            d = fr[cid][1]
            out[k] = {"frames": len(d), "bad": int(np.sum(f(d) != d[:, b]))}
    for k, (cid, f) in FLAGS.items():
        r = f(fr)
        if r is not None:
            bit, exp, valid = r
            out[k] = {"frames": int(valid.sum()), "bad": int(np.sum((bit != exp) & valid))}
    for k, (cid, f, modulus, step) in COUNTERS.items():
        if cid not in fr:
            continue
        t, d = fr[cid]
        v = f(d).astype(np.int64)
        dt = np.diff(t)
        ok_gap = (dt > 0) & (dt < 1.5 * np.median(dt))
        st = np.diff(v) % modulus
        out[k] = {"frames": len(d), "bad": int(np.sum(~np.isin(st, (0, 1, 2)) & ok_gap)),
                  "step0": int(np.sum((st == 0) & ok_gap)), "step2": int(np.sum((st == 2) & ok_gap))}
    return out


def _verify_one(path):
    import can_offline_lab as lab
    return os.path.basename(path)[8:25], verify_log(lab.frames(path))


def verify_all(paths, workers=None):
    """verify_log ueber viele Logs, parallel (Nutzerwunsch 22.11.: mehrere Kerne)."""
    from concurrent.futures import ProcessPoolExecutor
    res = {}
    with ProcessPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 2) - 2)) as ex:
        for i, (log, r) in enumerate(ex.map(_verify_one, paths)):
            for k, v in r.items():
                agg = res.setdefault(k, {"frames": 0, "bad": 0, "logs_bad": [], "step0": 0, "step2": 0})
                agg["frames"] += v["frames"]
                agg["bad"] += v["bad"]
                agg["step0"] += v.get("step0", 0)
                agg["step2"] += v.get("step2", 0)
                if v["bad"]:
                    agg["logs_bad"].append((log, v["bad"]))
            print(f"{i + 1}/{len(paths)} {log}", flush=True)
    return res
