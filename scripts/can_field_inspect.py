"""
Ein einzelnes Rohfeld gegen alle Anker (gemessen + abgeleitet, siehe can_anchor_sweep.anchors)
in mehreren Logs stellen: Spearman roh und Pearson trendbereinigt, beste Anker je Log.

Aufruf: .venv/bin/python scripts/can_field_inspect.py 0x420:23:8:BE [0x45A:44:13:BE ...]
        (ID:Startbit:Laenge:BE|LE[:s fuer signed])
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab
from can_anchor_sweep import anchors, correlate

LOGS = ["candump-2026-09-15_171047", "candump-2026-09-17_081218", "candump-2026-09-18_093544",
        "candump-2026-09-19_163755", "candump-2026-09-14_081105", "candump-2026-09-18_170350"]
warnings.filterwarnings("ignore")


def parse(spec):
    p = spec.split(":")
    return int(p[0], 16), int(p[1]), int(p[2]), p[3] == "BE", len(p) > 4 and p[4] == "s"


def inspect(specs, logs=LOGS, top=6):
    res = []
    for log in logs:
        path = f"data/can/{log}.log"
        fr = lab.frames(path)
        g = lab.grid(fr, 10)
        A = anchors(fr, lab.obd(path), g)
        names = list(A)
        Am = np.column_stack([A[k] for k in names])
        for spec in specs:
            cid, st, ln, be, sg = parse(spec)
            t, raw = lab.field(fr, cid, st, ln, be, sg)
            if not len(t):
                continue
            x = lab.on_grid(t, raw, g)
            m = np.isfinite(x)
            rs, rd = correlate(x[m][:, None], Am[m])
            for a, an in enumerate(names):
                res.append((spec, log[8:], an, rs[0, a], rd[0, a]))
    df = pd.DataFrame(res, columns=["field", "log", "anchor", "r_spear", "r_detr"])
    for spec, g in df.groupby("field"):
        agg = g.groupby("anchor").agg(spear=("r_spear", "median"), detr=("r_detr", "median"),
                                      spear_min=("r_spear", lambda s: s.abs().min()))
        agg["best"] = agg[["spear", "detr"]].abs().max(axis=1)
        print(f"== {spec}")
        print(agg.sort_values("best", ascending=False).head(top).round(3).to_string())
    return df


if __name__ == "__main__":
    inspect(sys.argv[1:])
