"""
Cross-Log-Persistenz-Check fuer die Zaehler-Kandidaten aus can_find_clock_candidate.py.

Der entscheidende Filter fuer "koennte die Pi-Uhr stellen": der Zaehler darf NICHT bei
jeder Zuendung/jedem neuen Log auf 0 zurueckspringen (das waere nur ein Betriebszeit-
Zaehler wie das schon bekannte BF00_IG_ON_Timer). Er muss stattdessen ueber mehrere,
zeitlich weit auseinanderliegende Logs hinweg monoton weiterlaufen.

Fuer jedes Kandidatenfeld aus results/clock_candidates_aggregated.csv (n_logs>=3):
1. Erster/letzter Rohwert je Log extrahieren (nach Log-Startzeit sortiert).
2. Prueft, ob der Wert zwischen aufeinanderfolgenden Logs (chronologisch) MONOTON
   weiterlaeuft (kein Reset auf ~0) - toleriert normale Wraps (Wert faellt auf ~0 UND
   der vorherige Wert war nahe am Maximum -> echter Wrap, kein Reset).
3. Fuer die "kein Reset"-Kandidaten: waechst der Wert zwischen Logs proportional zur
   ECHTEN Kalenderzeit-Luecke (aus den Dateinamen, die dafuer als grob vertrauenswuerdig
   gelten - siehe Caveat unten) oder nur zur Betriebszeit (wie C000_TOTAL_TIME)?
"""
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from can_log_parser import parse_candump

RESET_THRESHOLD_FRAC = 0.05  # "nahe 0" = unter 5% der beobachteten Spannweite


def bit_matrix(payloads, dlc=8):
    arr = np.frombuffer(b"".join(p.ljust(dlc, b"\x00")[:dlc] for p in payloads), dtype=np.uint8)
    return np.unpackbits(arr.reshape(-1, dlc), axis=1)


def field_first_last(raw_df, fields_by_id):
    """fields_by_id: {can_id: [(start,length), ...]} -> {(can_id,start,length): (t0,v0,t1,v1)}"""
    out = {}
    for can_id, specs in fields_by_id.items():
        sub = raw_df[raw_df["can_id"] == can_id]
        if sub.empty:
            continue
        t = sub["t"].to_numpy()
        order = np.argsort(t)
        t = t[order]
        payloads = [sub["data"].iloc[i] for i in order]
        dlc = max(len(p) for p in payloads)
        bits = bit_matrix(payloads, dlc)
        for start, length in specs:
            if start + length > dlc * 8:
                continue
            seg = bits[:, start:start + length]
            weights = (1 << np.arange(length - 1, -1, -1)).astype(np.int64)
            vals = seg.astype(np.int64) @ weights
            out[(can_id, start, length)] = (t[0], int(vals[0]), t[-1], int(vals[-1]))
    return out


def log_sort_key(path):
    m = re.search(r"candump-(\d{4}-\d{2}-\d{2})_(\d{6})", path)
    return m.group(1) + m.group(2) if m else path


def main():
    agg = pd.read_csv("results/clock_candidates_aggregated.csv")
    cands = agg[agg["n_logs"] >= 3].copy()
    print(f"{len(cands)} Kandidaten (n_logs>=3) fuer den Persistenz-Check")

    fields_by_id = {}
    spec_list = []
    for _, row in cands.iterrows():
        can_id = int(row["can_id"], 16)
        spec = (int(row["start"]), int(row["length"]))
        fields_by_id.setdefault(can_id, []).append(spec)
        spec_list.append((can_id, *spec))
    spec_list = sorted(set(spec_list))

    logs = sorted(glob.glob("data/can/candump-*.log"), key=log_sort_key)
    logs = [p for p in logs if os.path.getsize(p) >= 500_000]

    # {(can_id,start,length): [(log, t0,v0,t1,v1), ...]} in chronologischer Reihenfolge
    per_field = {spec: [] for spec in spec_list}
    for path in logs:
        raw_df = parse_candump(path)
        res = field_first_last(raw_df, fields_by_id)
        for spec, (t0, v0, t1, v1) in res.items():
            per_field[spec].append((os.path.basename(path), t0, v0, t1, v1))

    rows = []
    for spec, entries in per_field.items():
        if len(entries) < 3:
            continue
        can_id, start, length = spec
        span = 1 << length
        vmax_seen = max(max(v0, v1) for _, _, v0, _, v1 in entries)
        resets = 0
        continuations = 0
        for i in range(1, len(entries)):
            prev_log, _, _, _, prev_end = entries[i - 1]
            cur_log, _, cur_start, _, _ = entries[i]
            near_zero = cur_start <= RESET_THRESHOLD_FRAC * span
            prev_was_high = prev_end >= (1 - RESET_THRESHOLD_FRAC) * span
            if near_zero and not prev_was_high:
                resets += 1
            elif cur_start >= prev_end - 1:  # monotone Fortsetzung (Gleichstand/Anstieg)
                continuations += 1
            elif near_zero and prev_was_high:
                continuations += 1  # echter Wrap, kein Reset
            else:
                pass  # weder klarer Reset noch klare Fortsetzung (z.B. leichter Ruecksprung)
        n_transitions = len(entries) - 1
        rows.append(dict(
            can_id=f"0x{can_id:03X}", start=start, length=length, n_logs=len(entries),
            vmax_seen=vmax_seen, n_transitions=n_transitions, resets=resets,
            continuations=continuations, reset_frac=resets / n_transitions if n_transitions else np.nan,
            first_log=entries[0][0], first_val=entries[0][2],
            last_log=entries[-1][0], last_val=entries[-1][4],
        ))

    out = pd.DataFrame(rows).sort_values("reset_frac")
    out.to_csv("results/clock_candidates_persistence.csv", index=False)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 300)

    never_reset = out[out["reset_frac"] == 0]
    print(f"\n=== {len(never_reset)} Kandidaten OHNE einen einzigen erkannten Reset ueber alle Logs ===")
    print(never_reset[["can_id", "start", "length", "n_logs", "vmax_seen", "n_transitions",
                        "first_log", "first_val", "last_log", "last_val"]].to_string(index=False))

    print(f"\n=== Vollstaendige Reset-Statistik (alle {len(out)} Kandidaten) ===")
    print(out[["can_id", "start", "length", "n_logs", "vmax_seen", "resets", "continuations", "reset_frac"]].to_string(index=False))


if __name__ == "__main__":
    main()
