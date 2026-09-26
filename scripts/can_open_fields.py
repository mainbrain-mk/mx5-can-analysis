"""
Rest-Budget (Plan-Schritt 0, docs/plans/can-offline-ausbeute-plan.md): welche Bits variieren
in den Logs, ohne dass die DBC sie einem Signal zuordnet - zusammengefasst zu Feldern.

Ein Feld = zusammenhaengender Lauf unbelegter, variierender Bits innerhalb einer Botschaft
(DBC-Motorola-Reihenfolge: innerhalb eines Bytes von Bit 7 abwaerts, dann ins naechste Byte).
Je Feld: in wie vielen Logs es variiert, Wertevielfalt, Aenderungsrate, Zaehler-/Pruefsummen-
Verdacht (Bitkipprate nahe 0,5 bzw. konstante Schrittweite). Das ist der Fortschrittszaehler
fuer alle weiteren Suchen und die Grundlage des Katalogs offener Werte
(docs/status/can-open-fields.md).

Aufruf: .venv/bin/python scripts/can_open_fields.py [--self-test]
Ausgabe: results/can_open_fields.csv
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab
from can_anchor_sweep import dbc_owner
from can_rare_bits import dbc_bits

MOTOROLA = [b * 8 + p for b in range(8) for p in range(7, -1, -1)]   # Lesereihenfolge


def segments(bits_sorted):
    """Zusammenhaengende Laeufe in Motorola-Reihenfolge -> [(startbit, laenge)]."""
    pos = {b: i for i, b in enumerate(MOTOROLA)}
    runs, cur = [], []
    for b in sorted(bits_sorted, key=pos.get):
        if cur and pos[b] != pos[cur[-1]] + 1:
            runs.append(cur)
            cur = []
        cur.append(b)
    if cur:
        runs.append(cur)
    return [(r[0], len(r)) for r in runs]


def classify(raw, length, flip):
    if length >= 4 and np.median(flip) > 0.3:
        dif = np.diff(raw) % (1 << length)
        vals, cnt = np.unique(dif, return_counts=True)
        if cnt.max() / max(len(dif), 1) > 0.8 and vals[np.argmax(cnt)] != 0:
            return "ZAEHLER"
        return "PRUEFSUMME/RAUSCHEN"
    n = len(np.unique(raw))
    if n <= 2:
        return "FLAG"
    if n <= 16:
        return "ZUSTAND"
    return "WERT"


def analyze(paths):
    own = dbc_owner()
    rows = []
    for p in paths:
        fr = lab.frames(p)
        log = os.path.basename(p)[8:25]
        for cid, (t, d) in ((k, v) for k, v in fr.items() if isinstance(k, int)):
            if cid >= 0x700 or len(t) < 50:
                continue
            B = dbc_bits(d)
            var = [b for b in range(64) if 0 < B[:, b].mean() < 1 and b not in own.get(cid, set())]
            for st, ln in segments(var):
                _, raw = lab.field(fr, cid, st, ln, True)
                bits = lab.bit_order(st, ln, True)
                flip = np.array([np.mean(np.diff(B[:, b]) != 0) for b in bits])
                rows.append(dict(log=log, can_id=f"0x{cid:03X}", start=st, length=ln,
                                 n_unique=len(np.unique(raw)), change_rate=float(np.mean(np.diff(raw) != 0)),
                                 flip_med=float(np.median(flip)), kind=classify(raw, ln, flip),
                                 vmin=int(raw.min()), vmax=int(raw.max()), hz=len(t) / max(t[-1] - t[0], 1e-3)))
    return pd.DataFrame(rows)


def consolidate(df):
    g = df.groupby(["can_id", "start", "length"], as_index=False).agg(
        logs=("log", "nunique"), kind=("kind", lambda s: s.mode().iloc[0]),
        n_unique_max=("n_unique", "max"), change_rate_med=("change_rate", "median"),
        vmin=("vmin", "min"), vmax=("vmax", "max"), hz=("hz", "median"))
    return g.sort_values(["can_id", "start"])


def self_test():
    assert segments([7, 6, 5, 15, 14]) == [(7, 3), (15, 2)], segments([7, 6, 5, 15, 14])
    assert segments([0, 15]) == [(0, 2)]          # Bit0 -> naechstes Byte Bit7 ist zusammenhaengend
    raw = np.arange(1000) % 16
    assert classify(raw, 4, np.full(4, 0.5)) == "ZAEHLER"
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    df = analyze(lab.logs())
    c = consolidate(df)
    os.makedirs("results", exist_ok=True)
    c.to_csv("results/can_open_fields.csv", index=False)
    n_logs = df["log"].nunique()
    stable = c[c.logs >= n_logs // 2]
    print(f"{len(c)} offene Felder, davon {len(stable)} in >= {n_logs // 2} Logs; nach Art:")
    print(stable.groupby("kind")["length"].agg(["count", "sum"]).rename(columns={"sum": "bits"}))


if __name__ == "__main__":
    main()
