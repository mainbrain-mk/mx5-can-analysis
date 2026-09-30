"""
Byte-Sweep-Ergebnisse (can_byte_search.py, eine CSV je Log) ueber alle Logs zusammenfassen
und optional mit einem frueheren Lauf vergleichen (2026-09-29, vorher ad hoc).

Zusammenfassung je (can_id, Feld, Anker): pro Log das beste |r_detrend| ueber alle Encodings
(b3-4_BE_s, b3-4_LE_u, ... -> Feld "b3-4"), dann ueber die Logs n_logs, r_min, r_max, r_med.
Jede Zeile bekommt `jetzt_belegt`: ob das Feld nach der AKTUELLEN DBC schon (teilweise) einem
Signal gehoert. Wichtig beim Vergleich zweier Laeufe - am 29.09. waren fast alle
"verschwundenen" Treffer schlicht inzwischen in der DBC belegte Bytes, und ein "neuer"
Treffer war ein bekanntes Signal.

can_byte_search.py UEBERSCHREIBT results/can_byte_search_<log>.csv - vor einem Neulauf die
alten CSVs sichern, wenn verglichen werden soll.

Aufruf:
    .venv/bin/python scripts/can_sweep_consolidate.py --self-test
    .venv/bin/python scripts/can_sweep_consolidate.py                       # nur zusammenfassen
    .venv/bin/python scripts/can_sweep_consolidate.py --compare <alter_ordner_oder_konsolidierte.csv> \\
        [--anchor-prefix OBD_] [--min-logs 3]
Ausgabe: results/can_byte_search_consolidated_<JJJJ-MM-TT>.csv (+ Vergleich auf der Konsole)
"""
import argparse
import datetime
import glob
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KEY = ["can_id", "message", "b", "anchor"]


def consolidate(files):
    parts = [pd.read_csv(f).assign(log=os.path.basename(f)[len("can_byte_search_"):-len(".csv")])
             for f in files if os.path.getsize(f) > 60]
    if not parts:
        return pd.DataFrame(columns=KEY + ["n_logs", "r_min", "r_max", "r_med"])
    d = pd.concat(parts)
    d["b"] = d["byte"].str.split("_").str[0]
    d["r"] = d["r_detrend"].abs()
    best = d.groupby(["log"] + KEY)["r"].max().reset_index()
    return (best.groupby(KEY)["r"].agg(n_logs="count", r_min="min", r_max="max", r_med="median")
            .reset_index())


def field_bytes(b):
    """"b3" -> [3], "b3-4" -> [3, 4]"""
    lo, _, hi = b[1:].partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def mark_claimed(df, free):
    """free: {can_id(int): [freie Byte-Indizes]} aus can_byte_search.dbc_unclaimed_bytes."""
    df["jetzt_belegt"] = [
        any(x not in free.get(int(cid, 16), list(range(8))) for x in field_bytes(b))
        for cid, b in zip(df["can_id"], df["b"])]
    return df


def load_run(path):
    if os.path.isdir(path):
        return consolidate(glob.glob(os.path.join(path, "can_byte_search_candump-*.csv")))
    return pd.read_csv(path)


def compare(old, new, anchor_prefix="", min_logs=3):
    m = old[KEY + ["n_logs", "r_med"]].merge(new, on=KEY, how="outer", suffixes=("_alt", "_neu"))
    m = m[m["anchor"].str.startswith(anchor_prefix)].fillna({"n_logs_alt": 0, "n_logs_neu": 0})
    m["status"] = ["beide" if a and n else ("neu" if n else "weg")
                   for a, n in zip(m.n_logs_alt > 0, m.n_logs_neu > 0)]
    m = m[(m.n_logs_alt >= min_logs) | (m.n_logs_neu >= min_logs)]
    return m.sort_values(["status", "n_logs_neu", "r_med_neu"], ascending=[True, False, False])


def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        rows = "can_id,message,sender,byte,period_s,anchor,method,r_raw,r_detrend,raw_min,raw_max\n"
        for log, lines in {
            "candump-A": ["0x20A,M,S,b2-3_BE_s,0.01,OBD_X,pearson,0.7,0.65,0,1",
                          "0x20A,M,S,b2-3_BE_u,0.01,OBD_X,pearson,0.7,-0.72,0,1",   # bestes Encoding
                          "0x20A,M,S,b1_BE_u,0.01,OBD_X,pearson,0.7,0.61,0,1"],
            "candump-B": ["0x20A,M,S,b2-3_LE_s,0.01,OBD_X,pearson,0.7,0.60,0,1"],
        }.items():
            with open(os.path.join(tmp, f"can_byte_search_{log}.csv"), "w") as f:
                f.write(rows + "\n".join(lines) + "\n")
        c = consolidate(glob.glob(os.path.join(tmp, "*.csv")))
        r = c[c.b == "b2-3"].iloc[0]
        assert r.n_logs == 2 and abs(r.r_max - 0.72) < 1e-9 and abs(r.r_min - 0.60) < 1e-9, r
        assert field_bytes("b2-3") == [2, 3] and field_bytes("b7") == [7]
        c = mark_claimed(c, {0x20A: [2, 3, 5]})
        assert c.set_index("b").jetzt_belegt.to_dict() == {"b1": True, "b2-3": False}
        old = c[c.b == "b1"].drop(columns="jetzt_belegt")
        cmp = compare(old, c, min_logs=1).set_index("b").status.to_dict()
        assert cmp == {"b1": "beide", "b2-3": "neu"}, cmp
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", help="frueherer Lauf: Ordner mit Einzel-CSVs oder konsolidierte CSV")
    ap.add_argument("--anchor-prefix", default="", help="nur Anker mit diesem Praefix, z.B. OBD_")
    ap.add_argument("--min-logs", type=int, default=3)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return

    from can_byte_search import dbc_unclaimed_bytes
    from can_log_parser import load_db
    free = {cid: v[1] for cid, v in dbc_unclaimed_bytes(load_db()).items()}

    new = mark_claimed(consolidate(glob.glob("results/can_byte_search_candump-*.csv")), free)
    out = f"results/can_byte_search_consolidated_{datetime.date.today()}.csv"
    new.to_csv(out, index=False)
    print(f"{len(new)} Feld/Anker-Paare aus {new.n_logs.max() if len(new) else 0} Logs -> {out}")

    if args.compare:
        old = load_run(args.compare)
        m = mark_claimed(compare(old, new.drop(columns="jetzt_belegt"),
                                 args.anchor_prefix, args.min_logs), free)
        with pd.option_context("display.width", 220, "display.max_rows", 500):
            print(m[KEY + ["status", "n_logs_alt", "r_med_alt", "n_logs_neu", "r_med_neu",
                           "jetzt_belegt"]].round(3).to_string(index=False))
        weg = m[m.status == "weg"]
        print(f"\n'weg': {len(weg)}, davon inzwischen in der DBC belegt: {int(weg.jetzt_belegt.sum())}")


if __name__ == "__main__":
    main()
