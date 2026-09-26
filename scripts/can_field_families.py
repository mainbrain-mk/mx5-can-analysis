"""
Familien-Clusterung unbelegter CAN-Bytes: welche unbekannten Felder tragen dieselbe Groesse?

Referenzfrei im Sinne von "keine DID": alle unbelegten Bytes / Byte-Paare der DBC werden auf ein
gemeinsames 10-Hz-Raster gelegt und per Spearman-Rang gegeneinander UND gegen alle bekannten
Datalake-Kanaele desselben Logs korreliert. Zwei Kanten-Sorten:

  1. unbekannt <-> bekannt : benennt ein Feld ueber seinen naechsten bekannten Nachbarn
  2. unbekannt <-> unbekannt (verschiedene CAN-IDs): Duplikate/Familien derselben Groesse; ist ein
     Mitglied bekannt, sind alle benannt.

Nur Kanten, die in mindestens MIN_LOGS Logs mit Median-|r| >= MIN_R halten, zaehlen (die
Cross-Log-Regel des Projekts: Zufallskorrelation haelt selten ueber Logs).

Aufruf:
    .venv/bin/python scripts/can_field_families.py            # 6 grosse Logs, ~10 min
    .venv/bin/python scripts/can_field_families.py --log data/can/candump-....log
"""
import argparse
import os
import sys
from collections import defaultdict

import duckdb
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d
from scipy.stats import rankdata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from can_log_parser import parse_candump, load_db
from can_byte_search import dbc_unclaimed_bytes

LOGS = ["2026-09-14_081105", "2026-09-14_163711", "2026-09-15_084853",
        "2026-09-16_083214", "2026-09-17_084511", "2026-09-18_093544"]
GRID_HZ = 10.0
MIN_R, MIN_LOGS = 0.90, 4
OUT = "results/can_field_families.csv"


def words_of(sub, free):
    """{(name): array} fuer jedes freie Byte und jedes benachbarte freie Paar (BE), je Frame."""
    lens = sub["data"].str.len()
    sub = sub[lens == lens.mode().iloc[0]]
    a = np.stack(sub["data"].apply(lambda d: np.frombuffer(d, dtype=np.uint8)).to_numpy()).astype(np.int64)
    out = {}
    for i in free:
        if i < a.shape[1]:
            out[f"b{i}"] = a[:, i]
        if i + 1 in free and i + 1 < a.shape[1]:
            out[f"b{i}-{i+1}"] = (a[:, i] << 8) | a[:, i + 1]
    return sub["t"].to_numpy(), out


def grid_series(t, v, grid):
    i = np.searchsorted(t, grid, side="right") - 1          # last-value-hold, vor dem 1. Sample erster Wert
    return v[np.clip(i, 0, len(v) - 1)]


def rank_matrix(cols):
    """Zeilen = Serien; Rang je Serie, NaN-Zeitpunkte gemeinsam entfernt (Grid ist fuer alle gleich)."""
    return np.vstack([rankdata(c) for c in cols])


def one_log(path, db, dbc_free, con):
    lid = os.path.basename(path)[:-4]
    raw = parse_candump(path)
    t0 = raw["t"].min()
    raw["t"] -= t0
    grid = np.arange(5.0, raw["t"].max() - 5.0, 1.0 / GRID_HZ)
    names, cols = [], []
    for can_id, sub in raw.groupby("can_id"):
        free = dbc_free.get(can_id)
        if not free or len(sub) < 200 or sub["t"].max() - sub["t"].min() < 30:
            continue
        t, w = words_of(sub, set(free[1]))
        for nm, v in w.items():
            if len(np.unique(v)) < 8:                        # Flags/Konstanten: fuer Familien zu wenig Signal
                continue
            names.append(("U", f"0x{can_id:03X}:{nm}"))
            cols.append(grid_series(t, v.astype(float), grid))
    k = con.execute("select channel, t_elapsed_s t, value v from measurements where log_id=? "
                    "and channel not like 'UNMAPPED%' order by 1,2", [lid]).df()
    for ch, g in k.groupby("channel"):
        if len(g) < 500 or g.v.nunique() < 8:
            continue
        names.append(("K", ch))
        cols.append(grid_series(g.t.to_numpy(), g.v.to_numpy(), grid))
    X = np.vstack(cols)
    # monotone Zaehler/Kilometerstand raus (korrelieren mit allem, was driftet), Rest hochpassfiltern
    tt = np.arange(X.shape[1], dtype=float)
    keep = [i for i in range(len(X)) if abs(np.corrcoef(rankdata(X[i]), tt)[0, 1]) < 0.95]
    names, X = [names[i] for i in keep], X[keep]
    X = X - uniform_filter1d(X, int(20 * GRID_HZ), axis=1, mode="nearest")   # 20-s-Trend weg
    print(f"  {lid}: {sum(n[0]=='U' for n in names)} unbekannte Worte, "
          f"{sum(n[0]=='K' for n in names)} bekannte Kanaele, {X.shape[1]} Rasterpunkte", flush=True)
    R = rank_matrix(X)
    R = (R - R.mean(axis=1, keepdims=True)) / (R.std(axis=1, keepdims=True) + 1e-12)
    C = R @ R.T / R.shape[1]
    return [n[1] for n in names], [n[0] for n in names], C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="append")
    args = ap.parse_args()
    logs = args.log or [f"data/can/candump-{l}.log" for l in LOGS]
    db = load_db("hscan")
    dbc_free = dbc_unclaimed_bytes(db)
    con = duckdb.connect("data/datalake.duckdb", read_only=True)
    acc = defaultdict(list)                                  # (a,b) -> [r je Log]
    kind = {}
    for p in logs:
        names, kinds, C = one_log(p, db, dbc_free, con)
        kind.update(dict(zip(names, kinds)))
        iu = np.triu_indices(len(names), 1)
        for a, b, r in zip(iu[0], iu[1], np.abs(C[iu])):
            if r >= 0.5:
                acc[(names[a], names[b])].append(r)
    rows = []
    for (a, b), rs in acc.items():
        if len(rs) < min(MIN_LOGS, len(logs)):
            continue
        # Nachbarschaft zu BEKANNTEN Kanaelen schon ab 0,7 melden (Namensgebung), UU-Familien erst ab MIN_R
        if np.median(rs) < (MIN_R if kind[a] == kind[b] else 0.7):
            continue
        if kind[a] == "U" and kind[b] == "U" and a.split(":")[0] == b.split(":")[0]:
            continue                                         # gleiche Botschaft: nur Nachbarbytes desselben Feldes
        if kind[a] == "K" and kind[b] == "K":
            continue
        rows.append(dict(a=a, b=b, kind=kind[a] + kind[b], n_logs=len(rs),
                         r_med=round(float(np.median(rs)), 3), r_min=round(float(min(rs)), 3)))
    df = pd.DataFrame(rows, columns=["a", "b", "kind", "n_logs", "r_med", "r_min"]).sort_values(["kind", "r_med"], ascending=[True, False])
    os.makedirs("results", exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"{len(df)} Kanten -> {OUT}")


if __name__ == "__main__":
    main()
