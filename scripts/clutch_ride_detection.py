"""
Kupplungs-Schleifen-Erkennung ("Clutch Riding") - MX-5 Projekt

Ausgangspunkt: der Fahrer erwischte sich bei candump-2026-09-12_211833
(5->6 @ t=1048s) beim unvollstaendigen Einkuppeln - ein Vollgas-Run wurde
wegen einer Verkehrssituation bei ~3940 RPM abgebrochen, schnell in den 6.
Gang geschaltet, die Kupplung fiel nach dem schnellen Lupfen zuegig auf
raw~20 (nahe am Reibpunkt), blieb dort aber fuer ~1.7s haengen statt zuegig
auf 0 durchzulaufen - laengeres Schleifen bei Drehzahldifferenz, erhoehter
Verschleiss. Stichprobe ueber alle Gangwechsel zeigt: das war kein
Einzelfall, siehe unten.

Methodik: pro Gangwechsel-Ereignis (upshift/downshift aus shift_time_
analysis.py, "all_events") den Zeitpunkt finden, ab dem die Kupplungs-
position nach ihrem Maximum erstmals unter RIDE_ZONE_RAW faellt ("fast
oben, kurz vor dem endgueltigen Loslassen"). Die Restdauer von dort bis
zum tatsaechlichen Ereignisende (Unterschreiten von CLUTCH_ACTIVE_RAW,
identisch zur Schwelle in shift_time_analysis.py) ist die "Schleifzeit".
Verteilung ueber alle 313 Gangwechsel-Ereignisse zeigt eine klare Luecke:
97% liegen unter 0.58s, die restlichen 6 Faelle bei 1.16-1.78s - RIDE_
DURATION_THRESHOLD_S liegt dazwischen.

Aufruf: .venv/bin/python scripts/clutch_ride_detection.py
"""
import json
import os

import duckdb
import numpy as np

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
SHIFT_TIME_SUMMARY = os.path.join(RESULTS_DIR, "shift_time_analysis_summary.json")

RIDE_ZONE_RAW = 30              # "fast oben" - deutlich unter Vollausschlag (199), nah am Reibpunkt
RIDE_DURATION_THRESHOLD_S = 0.8  # zwischen dem 97%-Perzentil (~0.58s) und dem naechsten Ausreisser (1.16s)


def compute_ride_duration(con, log_id, t_start, t_end):
    """Zeit zwischen "Kupplung faellt nach dem Maximum erstmals unter
    RIDE_ZONE_RAW" und Ereignisende (t_end, Unterschreiten der Aktiv-
    Schwelle). None, falls keine Kupplungsdaten oder die Kupplung nie unter
    RIDE_ZONE_RAW faellt (kann bei sehr kurzen/unvollstaendigen Betaetigungen
    vorkommen)."""
    df = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? "
        "AND channel = 'ClutchPosition_CAN_raw' AND t_elapsed_s BETWEEN ? AND ? "
        "AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, t_start - 0.1, t_end + 0.1],
    ).fetchdf()
    if df.empty:
        return None
    t, v = df["t"].values, df["value"].values
    peak_idx = int(np.argmax(v))
    after_peak = np.where((np.arange(len(v)) > peak_idx) & (v < RIDE_ZONE_RAW))[0]
    if len(after_peak) == 0:
        return None
    t_ride_start = float(t[after_peak[0]])
    return t_end - t_ride_start


def main():
    with open(SHIFT_TIME_SUMMARY, encoding="utf-8") as f:
        shift_data = json.load(f)
    events = [e for e in shift_data["all_events"] if e["kind"] in ("upshift", "downshift")]
    print(f"{len(events)} Gangwechsel-Ereignisse (upshift+downshift) aus {SHIFT_TIME_SUMMARY}")

    con = duckdb.connect(DB_PATH, read_only=True)
    results = []
    for e in events:
        rd = compute_ride_duration(con, e["log_id"], e["t_start"], e["t_end"])
        if rd is None:
            continue
        results.append({
            "log_id": e["log_id"], "t_start": e["t_start"], "t_end": e["t_end"],
            "kind": e["kind"], "gear_before": e["gear_before"], "gear_after": e["gear_after"],
            "gear_pair": f"{e['gear_before']:.0f}-{e['gear_after']:.0f}",
            "ride_duration_s": rd, "flagged": rd > RIDE_DURATION_THRESHOLD_S,
        })

    flagged = [r for r in results if r["flagged"]]
    durs = np.array([r["ride_duration_s"] for r in results])
    print(f"\nn={len(results)}  median={np.median(durs):.2f}s  p95={np.percentile(durs,95):.2f}s  "
          f"max={durs.max():.2f}s")
    print(f"\n{len(flagged)} Ereignisse ueber der Schwelle ({RIDE_DURATION_THRESHOLD_S}s) - "
          "Kupplung blieb ungewoehnlich lange nahe dem Reibpunkt haengen:")
    for r in sorted(flagged, key=lambda r: -r["ride_duration_s"]):
        print(f"  {r['ride_duration_s']:.2f}s  {r['gear_pair'].replace('-', '->')}  "
              f"{r['log_id']}  t={r['t_start']:.1f}s")

    out = {
        "n_events": len(results), "n_flagged": len(flagged),
        "ride_duration_threshold_s": RIDE_DURATION_THRESHOLD_S,
        "ride_zone_raw": RIDE_ZONE_RAW,
        "flagged": sorted(flagged, key=lambda r: -r["ride_duration_s"]),
        "all_results": results,
    }
    out_json = os.path.join(RESULTS_DIR, "clutch_ride_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {out_json}")


if __name__ == "__main__":
    main()
