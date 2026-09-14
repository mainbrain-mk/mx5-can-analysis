"""
Ereignis-Bit-Differenzanalyse: Bits finden, die NUR waehrend eines seltenen Ereignisses kippen.

Motivation: der ABS/DSC-Eingriffsindikator ist seit Wochen offen und wurde bisher immer mit
Korrelation ueber ein ganzes Log gesucht (can_byte_search.py, can_bitsearch.py). Das ist das
falsche Werkzeug: ein Flag, das pro Fahrt einmal 300 ms lang gesetzt wird, hat ueber 40
Minuten Log eine Einschaltquote von 0,01% - jede Korrelation ertrinkt darin, egal wie gut
der Anker ist.

Stattdessen hier die in der Fahrzeug-RE uebliche Gegenprobe: Ereignisfenster definieren,
dann fuer jedes einzelne Bit der Botschaft vergleichen, wie oft es INNERHALB der Fenster
gesetzt ist gegenueber DRAUSSEN. Ein echter Ereignisindikator ist drinnen fast immer und
draussen fast nie gesetzt - das faellt auch dann auf, wenn das Ereignis 0,01% des Logs
ausmacht.

Ereignisquellen (`--event`):
  abs      - ABS-Verdacht: starkes Bremsen UND auseinanderlaufende Radgeschwindigkeiten
  dsc      - DSC-Verdacht: hohe Querbeschleunigung (Rutschen/Eingriff wahrscheinlich)
  brake    - jede kraeftige Bremsung (Kontrolle: hier MUSS das bekannte Bremslicht-Bit
             auftauchen - eingebauter Funktionsnachweis des Verfahrens)
  standstill / moving - Kontrollereignisse mit garantiert bekannter Antwort

Bewertung je Bit: Lift = P(Bit=1 | im Fenster) - P(Bit=1 | ausserhalb), plus die absoluten
Quoten. Ein Kandidat zaehlt nur, wenn er drinnen deutlich haeufiger ist UND draussen selten -
sonst findet man jedes Bit, das ohnehin die meiste Zeit gesetzt ist.

Aufruf:
    .venv/bin/python scripts/can_event_bit_diff.py --self-test
    .venv/bin/python scripts/can_event_bit_diff.py --event brake      # Funktionsnachweis
    .venv/bin/python scripts/can_event_bit_diff.py --event abs
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from can_log_parser import parse_candump, load_db
from can_field_segmentation import bit_matrix

OUT_CSV = "results/can_event_bit_diff.csv"
MIN_LIFT = 0.5            # Bit muss im Fenster mind. 50 Prozentpunkte haeufiger sein
MAX_OUTSIDE = 0.10        # ... und ausserhalb hoechstens 10% der Zeit gesetzt
PAD_S = 0.5               # Ereignisfenster vorne/hinten verbreitern (Latenz der Sender)


def anchor_series(raw_df, db, can_id, sig_name):
    msg = db.get_message_by_frame_id(can_id)
    sub = raw_df[raw_df["can_id"] == can_id]
    t, v = [], []
    for tt, data in sub[["t", "data"]].itertuples(index=False):
        try:
            dec = msg.decode(data, allow_truncated=True)
        except Exception:
            continue
        if sig_name in dec:
            t.append(tt)
            v.append(float(dec[sig_name]))
    return np.array(t), np.array(v, dtype=float)


def event_windows(raw_df, db, kind, hz=20.0):
    """(grid, bool-Maske) der Ereigniszeitpunkte."""
    t_bp, bp = anchor_series(raw_df, db, 0x78, "BrakePressure")
    t_ws = [anchor_series(raw_df, db, 0x215, f"WheelSpeed_{i}") for i in range(1, 5)]
    t_lat, lat = anchor_series(raw_df, db, 0x75, "Lateral_Acc_Raw")
    if len(t_bp) < 100 or any(len(t) < 100 for t, _ in t_ws):
        return None, None

    t0 = max([t_bp.min()] + [t.min() for t, _ in t_ws])
    t1 = min([t_bp.max()] + [t.max() for t, _ in t_ws])
    if t1 - t0 < 60:
        return None, None
    grid = np.arange(t0, t1, 1.0 / hz)
    BP = np.interp(grid, t_bp, bp)
    WS = np.array([np.interp(grid, t, v) for t, v in t_ws])
    spread = WS.std(axis=0)
    speed = WS.mean(axis=0)

    if kind == "brake":
        mask = BP > 20
    elif kind == "abs":
        mask = (BP > 30) & (spread > np.quantile(spread[BP > 30], 0.95) if (BP > 30).any() else False)
    elif kind == "dsc":
        if len(t_lat) < 100:
            return None, None
        LAT = np.abs(np.interp(grid, t_lat, lat))
        mask = LAT > np.quantile(LAT, 0.999)
    elif kind == "standstill":
        mask = speed < 0.5
    elif kind == "moving":
        mask = speed > 30
    else:
        raise ValueError(kind)

    mask = np.asarray(mask, dtype=bool)
    if mask.sum() < 20 or (~mask).sum() < 20:
        return None, None
    # Fenster um PAD_S verbreitern - die Sender reagieren nicht framegenau
    pad = int(PAD_S * hz)
    if pad:
        mask = np.convolve(mask, np.ones(2 * pad + 1), mode="same") > 0
    return grid, mask


def bit_lift(raw_df, grid, mask, min_frames=200):
    """Pro (can_id, bit): Setzquote im Fenster vs. ausserhalb."""
    rows = []
    for can_id, sub in raw_df.groupby("can_id"):
        if len(sub) < min_frames:
            continue
        payloads = list(sub["data"])
        dlc = max(len(p) for p in payloads)
        if dlc == 0:
            continue
        bits = bit_matrix(payloads, dlc)
        t = sub["t"].to_numpy(dtype=float)
        # jedem Frame zuordnen, ob er in ein Ereignisfenster faellt
        idx = np.searchsorted(grid, t).clip(0, len(grid) - 1)
        inside = mask[idx]
        if inside.sum() < 10 or (~inside).sum() < 10:
            continue
        p_in = bits[inside].mean(axis=0)
        p_out = bits[~inside].mean(axis=0)
        for b in range(dlc * 8):
            if p_in[b] == p_out[b]:
                continue
            rows.append(dict(can_id=f"0x{can_id:03X}", bit=b,
                             p_inside=float(p_in[b]), p_outside=float(p_out[b]),
                             lift=float(p_in[b] - p_out[b]),
                             n_inside=int(inside.sum()), n_outside=int((~inside).sum())))
    return pd.DataFrame(rows)


def annotate_known(df, db):
    from can_opendbc_crosscheck import signal_bit_indices
    owner = {}
    for msg in db.messages:
        for sig in msg.signals:
            for b in signal_bit_indices(sig):
                owner[(msg.frame_id, b)] = sig.name
    df["known_signal"] = [owner.get((int(c, 16), b), "") for c, b in zip(df["can_id"], df["bit"])]
    return df


def self_test():
    """Synthetisch: ein Bit ist nur im Ereignisfenster gesetzt, ein anderes immer."""
    n, hz = 4000, 20.0
    grid = np.arange(n) / hz
    mask = (grid > 50) & (grid < 55)
    rng = np.random.default_rng(0)
    payloads = []
    for i in range(n):
        b = bytearray(8)
        if mask[i]:
            b[0] |= 0x80                      # Ereignisbit -> Bit 0
        b[1] = 0xFF                           # immer gesetzt -> darf NICHT gewinnen
        b[2] = int(rng.integers(0, 256))      # Rauschen
        payloads.append(bytes(b))
    df = pd.DataFrame({"t": grid, "can_id": [0x123] * n, "data": payloads})
    res = bit_lift(df, grid, mask)
    top = res.sort_values("lift", ascending=False).iloc[0]
    assert top["bit"] == 0 and top["lift"] > 0.9, res.sort_values("lift", ascending=False).head()
    always = res[(res.bit >= 8) & (res.bit < 16)]
    assert (always["lift"].abs() < 0.05).all(), "Dauer-Bit faelschlich als Ereignis gewertet"
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="brake",
                    choices=["brake", "abs", "dsc", "standstill", "moving"])
    ap.add_argument("--log", action="append")
    ap.add_argument("--min-bytes", type=int, default=2_000_000)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    logs = args.log or [p for p in sorted(glob.glob("data/can/candump-*.log"))
                        if os.path.getsize(p) >= args.min_bytes]
    db = load_db("hscan")
    frames = []
    for path in logs:
        raw_df = parse_candump(path)
        grid, mask = event_windows(raw_df, db, args.event)
        if grid is None:
            print(f"  {os.path.basename(path)}: kein brauchbares Ereignisfenster")
            continue
        res = bit_lift(raw_df, grid, mask)
        if res.empty:
            continue
        res["log"] = os.path.basename(path)
        frames.append(res)
        print(f"  {os.path.basename(path)}: {mask.sum()/20:.1f}s Ereignis, {len(res)} Bits bewertet")

    if not frames:
        print("nichts auswertbar")
        return
    df = annotate_known(pd.concat(frames, ignore_index=True), db)
    os.makedirs("results", exist_ok=True)
    out = OUT_CSV.replace(".csv", f"_{args.event}.csv")
    df.to_csv(out, index=False)

    g = df.groupby(["can_id", "bit"], as_index=False).agg(
        logs=("log", "nunique"),
        lift_min=("lift", "min"), lift_med=("lift", "median"),
        p_in_med=("p_inside", "median"), p_out_max=("p_outside", "max"),
        known_signal=("known_signal", "first"))
    n_logs = df["log"].nunique()
    hits = g[(g.lift_min >= MIN_LIFT) & (g.p_out_max <= MAX_OUTSIDE) & (g.logs == n_logs)]
    print(f"\n=== Ereignis '{args.event}': {len(hits)} Bits in ALLEN {n_logs} Logs "
          f"ereignisspezifisch (Lift>={MIN_LIFT}, ausserhalb<={MAX_OUTSIDE}) -> {out}")
    with pd.option_context("display.width", 200, "display.max_rows", 100):
        print(hits.sort_values("lift_min", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
