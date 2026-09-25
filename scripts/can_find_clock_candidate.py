"""
Suche nach einem ratenunabhaengigen Zaehler/Zeitstempel in den unbekannten Feldern
(MX-5 Projekt, No-RTC-Problem am Raspberry Pi).

Zweck: Cluster C aus dem Feld-Clustering (data/can/can_field_clustering_2026-09-20.md)
systematisch nach etwas durchsuchen, das sich als Uhr eignen koennte - ein Feld, das
UNABHAENGIG von der Botschafts-Sendefrequenz monoton hoch- oder runterzaehlt. Bewusst
KEINE Annahme ueber die Taktrate (kann schneller als 1Hz sein, exakt 1Hz, oder auch nur
alle 60s einmal hochzaehlen) - der bestehende can_field_segmentation.py::is_counter()
findet nur Zaehler, die bei JEDEM Frame um genau 1 hochzaehlen (also mit der Sendefrequenz
der Botschaft selbst getaktet sind). Ein Zaehler, der SELTENER tickt als die Botschaft
gesendet wird, sieht dort aus wie ein "PHYSICAL"-Feld mit vielen Plateaus - oder, wenn er
SCHNELL genug durch seinen ganzen Wertebereich rotiert, sogar wie "CRC" (nahezu jedes Bit
kippt mit hoher Rate, viele unterschiedliche Werte).

Methodik pro Kandidatenfeld (can_id, start, length), ueber ALLE Logs, in denen es als
unbelegt (covered=False) und nicht CONST/FLAG klassifiziert ist:
1. Rohwert-Zeitreihe extrahieren (bit_matrix, wie can_field_segmentation.py).
2. Unwrap: ein Rollover (Wert faellt um mehr als die halbe Spannweite) wird als
   Fortsetzung eines steigenden Zaehlers interpretiert (kumulative +2^length-Korrektur) -
   in beide Richtungen probiert (steigend und fallend), das bessere Ergebnis gewinnt.
3. Monotonie-Score: Anteil der Schritte, die nach dem Unwrap das erwartete Vorzeichen
   haben (>=0 fuer steigend, <=0 fuer fallend). Ein echter Zaehler liegt nahe 100%.
4. Tick-Rate schaetzen: bei jedem tatsaechlichen Wertwechsel Zeitabstand seit dem letzten
   Wechsel notieren, normiert auf "Schritte pro Sekunde" (delta_wert/delta_t). Konsistenz
   ueber den Variationskoeffizienten (Std/Median) der so geschaetzten Rate.
5. Cross-Log-Aggregation: nur Felder behalten, die dieses Verhalten in MEHREREN Logs
   zeigen (kein Einzelfund).
6. Zusatz-Scan (unabhaengig von 1-5): jedes Feld mit Breite >=28 Bit auf Werte im
   plausiblen Unix-Epoch-Sekundenbereich pruefen (aktuell ~1.79 Mrd, siehe die
   candump-eigenen Zeitstempel) - waere ein direkter Absolutzeitstempel-Volltreffer.

Aufruf:
    .venv/bin/python scripts/can_find_clock_candidate.py [--jobs N]
"""
import argparse
import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from can_log_parser import parse_candump, load_db
from can_opendbc_crosscheck import signal_bit_indices

MIN_FRAMES = 200
MONO_MIN = 0.97          # Anteil "richtiges Vorzeichen" nach Unwrap, ab dem es zaehlt
MIN_STEPS = 8            # mindestens so viele echte Wertwechsel noetig fuer eine Ratenschaetzung
MAX_CV = 0.5             # Variationskoeffizient der geschaetzten Tick-Rate, ab dem "instabil"
EPOCH_MIN, EPOCH_MAX = 1_500_000_000, 2_000_000_000  # Unix-Sekunden, grosszuegiges Fenster


def our_bit_owner(our_db):
    owner = {}
    for msg in our_db.messages:
        o = {}
        for sig in msg.signals:
            for b in signal_bit_indices(sig):
                o[b] = sig.name
        owner[msg.frame_id] = o
    return owner


def bit_matrix(payloads, dlc=8):
    arr = np.frombuffer(b"".join(p.ljust(dlc, b"\x00")[:dlc] for p in payloads), dtype=np.uint8)
    return np.unpackbits(arr.reshape(-1, dlc), axis=1)


def flip_rates(bits):
    if len(bits) < 2:
        return np.zeros(bits.shape[1])
    return (bits[1:] != bits[:-1]).mean(axis=0)


def segment(rates, dlc=8):
    n = dlc * 8
    with np.errstate(divide="ignore"):
        mag = np.floor(np.log10(rates[:n]))
    mag[rates[:n] == 0] = -99
    zero = rates[:n] == 0
    cuts = sorted({i for i in range(n - 1) if mag[i] > mag[i + 1] or zero[i] != zero[i + 1]})
    fields, start = [], 0
    for c in cuts:
        fields.append((start, c - start + 1))
        start = c + 1
    if start < n:
        fields.append((start, n - start))
    return fields


def field_values(bits, start, length):
    seg = bits[:, start:start + length]
    weights = (1 << np.arange(length - 1, -1, -1)).astype(np.int64)
    return seg.astype(np.int64) @ weights


def unwrap_and_score(t, v, length):
    """Versucht, v (0..2^length-1) als monotonen Zaehler zu lesen - steigend UND fallend.
    Rueckgabe: bestes Ergebnis als dict mit direction/mono_score/unwrapped, oder None
    wenn beide Richtungen schlecht sind."""
    span = 1 << length
    order = np.argsort(t)
    t, v = t[order], v[order].astype(np.int64)
    if len(v) < MIN_FRAMES:
        return None

    results = []
    for direction in ("up", "down"):
        vv = v.copy()
        if direction == "down":
            vv = -vv  # Countdown wie eine steigende Zaehlung mit umgekehrtem Vorzeichen behandeln
        d = np.diff(vv)
        # Rollover: ein Sprung von > halber Spannweite in die "falsche" Richtung wird als
        # Wrap gewertet und korrigiert (kumulative Summe der Korrekturen).
        wrap = np.where(d < -span // 2, span, 0) - np.where(d > span // 2, span, 0)
        correction = np.cumsum(wrap)
        unwrapped = vv.copy()
        unwrapped[1:] = vv[1:] + correction
        dd = np.diff(unwrapped)
        mono = float((dd >= 0).mean()) if len(dd) else 0.0
        results.append((direction, mono, unwrapped))

    direction, mono, unwrapped = max(results, key=lambda r: r[1])
    if mono < MONO_MIN:
        return None

    # Tick-Rate: nur an Stellen schaetzen, wo sich der (unwrapped) Wert tatsaechlich aendert.
    dd = np.diff(unwrapped)
    dt = np.diff(t)
    changed = dd > 0
    if changed.sum() < MIN_STEPS:
        return dict(direction=direction, mono=mono, n_steps=int(changed.sum()),
                    rate_hz=None, rate_cv=None, t=t, unwrapped=unwrapped, v_raw=v)
    step_dt = dt[changed]
    step_dv = dd[changed]
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = step_dv / step_dt  # counts/s fuer jeden beobachteten Schritt
    rate = rate[np.isfinite(rate) & (rate > 0)]
    if len(rate) < MIN_STEPS:
        return dict(direction=direction, mono=mono, n_steps=int(changed.sum()),
                    rate_hz=None, rate_cv=None, t=t, unwrapped=unwrapped, v_raw=v)
    med = float(np.median(rate))
    cv = float(np.std(rate) / med) if med > 0 else np.inf
    return dict(direction=direction, mono=mono, n_steps=int(changed.sum()),
                rate_hz=med, rate_cv=cv, t=t, unwrapped=unwrapped, v_raw=v)


def analyze_log(path, only_ids=None):
    db = load_db("hscan")
    owners = our_bit_owner(db)
    raw_df = parse_candump(path)
    rows = []
    epoch_hits = []

    for can_id, sub in raw_df.groupby("can_id"):
        if only_ids is not None and can_id not in only_ids:
            continue
        if len(sub) < MIN_FRAMES:
            continue
        payloads = list(sub["data"])
        dlc = max(len(p) for p in payloads)
        if dlc == 0:
            continue
        bits = bit_matrix(payloads, dlc)
        rates = flip_rates(bits)
        owner = owners.get(can_id, {})
        t_arr = sub["t"].to_numpy(dtype=float)

        for start, length in segment(rates, dlc):
            known = {owner[b] for b in range(start, start + length) if b in owner}
            if known:
                continue  # bereits bekanntes Signal - nicht Ziel dieser Suche
            if length < 2:
                continue  # 1-Bit-Flags koennen kein mehrstufiger Zaehler sein
            vals = field_values(bits, start, length)
            if len(np.unique(vals)) < 3:
                continue  # praktisch konstant, kein Zaehler

            res = unwrap_and_score(t_arr, vals, length)
            if res is not None:
                rows.append(dict(
                    log=os.path.basename(path), can_id=f"0x{can_id:03X}", start=start,
                    length=length, direction=res["direction"], mono=res["mono"],
                    n_steps=res["n_steps"], rate_hz=res["rate_hz"], rate_cv=res["rate_cv"],
                    vmin=int(vals.min()), vmax=int(vals.max()), n=len(vals),
                ))

            # Epoch-Scan unabhaengig vom Counter-Test, nur fuer breite Felder
            if length >= 28:
                in_range = (vals >= EPOCH_MIN) & (vals <= EPOCH_MAX)
                if in_range.any():
                    epoch_hits.append(dict(
                        log=os.path.basename(path), can_id=f"0x{can_id:03X}", start=start,
                        length=length, n_in_range=int(in_range.sum()), n_total=len(vals),
                        example=int(vals[in_range][0]),
                    ))

    return pd.DataFrame(rows), pd.DataFrame(epoch_hits)


def _worker(path):
    return analyze_log(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--min-bytes", type=int, default=500_000)
    ap.add_argument("--log", action="append")
    args = ap.parse_args()

    logs = args.log or [p for p in sorted(glob.glob("data/can/candump-*.log"))
                        if os.path.getsize(p) >= args.min_bytes]
    print(f"{len(logs)} Logs, {args.jobs} parallele Prozesse")

    jobs = max(1, min(args.jobs, len(logs)))
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(_worker, logs))
    else:
        results = [analyze_log(p) for p in logs]

    counter_df = pd.concat([r[0] for r in results if len(r[0])], ignore_index=True)
    epoch_df = pd.concat([r[1] for r in results if len(r[1])], ignore_index=True) if any(len(r[1]) for r in results) else pd.DataFrame()

    os.makedirs("results", exist_ok=True)
    counter_df.to_csv("results/clock_candidates_raw.csv", index=False)
    if len(epoch_df):
        epoch_df.to_csv("results/clock_candidates_epoch_hits.csv", index=False)

    print(f"\n{len(counter_df)} Zaehler-Kandidat-Log-Paare -> results/clock_candidates_raw.csv")

    # Cross-Log-Aggregation
    agg = counter_df.groupby(["can_id", "start", "length", "direction"]).agg(
        n_logs=("log", "nunique"),
        mono_min=("mono", "min"),
        mono_mean=("mono", "mean"),
        rate_hz_median=("rate_hz", "median"),
        rate_cv_max=("rate_cv", "max"),
        n_steps_total=("n_steps", "sum"),
        vmax_seen=("vmax", "max"),
    ).reset_index().sort_values(["n_logs", "mono_min"], ascending=False)

    agg.to_csv("results/clock_candidates_aggregated.csv", index=False)
    print(f"{len(agg)} eindeutige Kandidatenfelder -> results/clock_candidates_aggregated.csv\n")

    stable = agg[(agg["n_logs"] >= 3) & (agg["mono_min"] >= MONO_MIN)]
    print(f"=== {len(stable)} Kandidaten mit Zaehler-Verhalten in >=3 Logs ===")
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(stable.to_string(index=False))

    if len(epoch_df):
        print(f"\n=== {len(epoch_df)} Treffer im Unix-Epoch-Wertebereich ({EPOCH_MIN}-{EPOCH_MAX}) ===")
        with pd.option_context("display.width", 200):
            print(epoch_df.to_string(index=False))
    else:
        print("\nKeine Treffer im Unix-Epoch-Wertebereich.")


if __name__ == "__main__":
    main()
