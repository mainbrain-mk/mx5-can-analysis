"""
Schaltzeit-Check aus CAN-Kupplungsdaten (MX-5 Projekt)

Ausgangspunkt: bei der Kraftkreis-Untersuchung (can_traction_circle.py) fiel
auf, dass der Radgeschwindigkeits-"Ruck" beim Wiedereinkuppeln nach einem
2.->3.-Gang-Wechsel ca. 0.6s dauerte - deutlich laenger als die in
`performance_simulation.py` hinterlegten `ATTACK_SHIFT_S`-Werte (0.15-0.25s,
laut dortigem Kommentar ein Literatur-/Spec-Default fuer "maximale
Fahrleistung", nicht aus diesem Auto gemessen). `ClutchPosition_CAN_raw`
(0x130, siehe mx5_can_bus_logging-Memory) ist die ERSTE echte Erfassung von
"Kupplung tritt aus dem eingerueckten Zustand aus, bis wieder vollstaendig
eingerueckt" in diesem Projekt - bisher gab es nur die grobe OBD-Ableitung
CPP_PER_MZ (2-4Hz, kein zeitlich sauberer Start/Ende-Punkt).

WICHTIGE EINSCHRAENKUNG (Nutzer-Hinweis): alle bisherigen CAN-Logs sind
normale Strassenfahrten, KEINE "Attack"-Schaltvorgaenge (kein Rennstrecken-
Hetzen zwischen den Gaengen) - die hier gemessenen Zeiten sind also KEIN
Ersatz fuer `ATTACK_SHIFT_S` (das fuer eine Hotlap-Simulation an einer
theoretischen "maximale Fahrleistung"-Schaltung gedacht ist), sondern ein
Referenzwert fuer entspanntes/alltaegliches Schalten mit diesem Auto/Fahrer.

Methodik:
  1. Pro CAN-Log: Kupplungs-Betaetigungen als zusammenhaengende Phasen
     `ClutchPosition_CAN_raw > CLUTCH_ACTIVE_RAW` erkennen (Luecken bis
     GAP_BRIDGE_S ueberbrueckt - kurze Signal-Dropouts, kein neuer Tritt).
  2. Gang vor/nach jeder Phase aus `Gear_CAN` (naechster Sample vor
     Phasenbeginn/nach Phasenende).
  3. Klassifikation, WEIL nicht jede Kupplungsbetaetigung ein sauberer
     Einzelgang-Hochschaltvorgang ist:
       - "standstill": Gang vor ODER nach = 0 (Neutral/Anfahren/Stillstand),
         ODER Dauer > STANDSTILL_MAX_S (so lange steht niemand mitten in
         einem Schaltvorgang) - kein Schaltvorgang, nur gezaehlt.
       - "upshift" (Gang+1): die fuer ATTACK_SHIFT_S-Vergleich relevante
         Kategorie.
       - "downshift" (Gang-1): nicht in ATTACK_SHIFT_S/der Sim modelliert,
         nur zur Vollstaendigkeit ausgegeben.
       - "multi_gear" (|Gang-Aenderung|>1): vermutlich ein durchgehend
         gekuppelter Mehrfach-Ruckschaltvorgang (z.B. Bremsen vor einer
         Kurve) - eigene Kategorie, nicht mit Einzelgang-Werten vermischt.
       - "no_change": Gang vor = Gang nach, kein Standstill-Fall - Kupplung
         wurde getreten, aber am Ende blieb derselbe Gang drin (kurzes
         Antippen, abgebrochener Schaltversuch, oder Gear_CAN selbst zu
         grob/verzoegert fuer einen sehr schnellen Wechsel). Nicht
         belastbar interpretierbar, nur gezaehlt/ausgegeben, NICHT fuer
         die Kalibrierung verwendet.

Aufruf: .venv/bin/python scripts/shift_time_analysis.py
"""
import json
import os
import sys

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(__file__))
from performance_simulation import ATTACK_SHIFT_S

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"

CLUTCH_ACTIVE_RAW = 15   # siehe can_traction_circle.py, gleiche Konvention
GAP_BRIDGE_S = 0.15      # kurze Signal-Luecken innerhalb einer Betaetigung ueberbruecken
STANDSTILL_MAX_S = 3.0   # laenger als das ist kein Schaltvorgang mehr, sondern Halt/Leerlauf


def load_channel(con, log_id, channel):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = ? "
        "AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()


def detect_clutch_events(t, clutch_raw, gear_t, gear_v):
    active = clutch_raw > CLUTCH_ACTIVE_RAW
    events, start, last_true_t = [], None, None
    for i in range(len(t)):
        if active[i]:
            if start is None:
                start = i
            last_true_t = t[i]
        elif start is not None and (t[i] - last_true_t) > GAP_BRIDGE_S:
            events.append((start, i - 1))
            start = None
    if start is not None:
        events.append((start, len(t) - 1))

    out = []
    for s, e in events:
        t0, t1 = t[s], t[e]
        dur = t1 - t0
        before = gear_v[gear_t < t0]
        after = gear_v[gear_t > t1]
        if len(before) == 0 or len(after) == 0:
            continue
        gb, ga = float(before[-1]), float(after[0])
        if gb == 0 or ga == 0 or dur > STANDSTILL_MAX_S:
            kind = "standstill"
        elif ga == gb + 1:
            kind = "upshift"
        elif ga == gb - 1:
            kind = "downshift"
        elif ga == gb:
            kind = "no_change"
        else:
            kind = "multi_gear"
        out.append({"t_start": float(t0), "t_end": float(t1), "duration_s": float(dur),
                     "gear_before": gb, "gear_after": ga, "kind": kind})
    return out


def build_best_times(by_kind):
    """Bestzeit (schnellste Kupplungs-Betaetigung) je Gangpaar UND Richtung
    (z.B. 2->3 UND 3->2 getrennt) - aus dem vollstaendigen Datalake-Stand
    neu berechnet bei jedem Lauf, dadurch automatisch "aktualisiert" ohne
    eigene Merge-Logik: alte Logs verschwinden nie aus dem Datalake, ein
    neuer Bestwert kann also nur dazukommen, nie durch einen fehlenden
    frueheren Lauf verlorengehen."""
    best = {}
    for kind in ("upshift", "downshift"):
        for ev in by_kind.get(kind, []):
            key = f"{ev['gear_before']:.0f}-{ev['gear_after']:.0f}"
            cur = best.get(key)
            if cur is None or ev["duration_s"] < cur["best_s"]:
                best[key] = {"best_s": ev["duration_s"], "log_id": ev["log_id"],
                             "t_start": ev["t_start"], "kind": kind}
    for key, v in best.items():
        n = sum(1 for ev in by_kind.get(v["kind"], [])
                if f"{ev['gear_before']:.0f}-{ev['gear_after']:.0f}" == key)
        v["n"] = n
    return best


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    log_ids = con.execute(
        "SELECT DISTINCT log_id FROM measurements WHERE channel = 'ClutchPosition_CAN_raw' ORDER BY log_id"
    ).fetchdf()["log_id"].tolist()
    print(f"{len(log_ids)} CAN-Logs mit ClutchPosition_CAN_raw: {log_ids}")

    all_events = []
    for log_id in log_ids:
        clutch = load_channel(con, log_id, "ClutchPosition_CAN_raw")
        gear = load_channel(con, log_id, "Gear_CAN")
        if clutch.empty or gear.empty:
            continue
        events = detect_clutch_events(clutch["t"].values, clutch["value"].values,
                                       gear["t"].values, gear["value"].values)
        for ev in events:
            ev["log_id"] = log_id
        all_events.extend(events)

    by_kind = {}
    for ev in all_events:
        by_kind.setdefault(ev["kind"], []).append(ev)
    print(f"\n{len(all_events)} Kupplungs-Betaetigungen total ueber {len(log_ids)} Logs:")
    for kind, evs in sorted(by_kind.items()):
        durs = [e["duration_s"] for e in evs]
        print(f"  {kind:12s} n={len(evs):3d}  dauer median={np.median(durs):5.2f}s  "
              f"min={min(durs):5.2f}s  max={max(durs):5.2f}s")

    print("\nEinzelgang-Hochschaltungen (upshift) nach Gangpaar, vs. ATTACK_SHIFT_S (Literatur-Default):")
    upshift_stats = {}
    for (g_from, g_to), attack_s in sorted(ATTACK_SHIFT_S.items()):
        matching = [e for e in by_kind.get("upshift", []) if e["gear_before"] == g_from and e["gear_after"] == g_to]
        if not matching:
            print(f"  ({g_from}->{g_to}): keine Messung in den vorhandenen Logs")
            continue
        durs = [e["duration_s"] for e in matching]
        median_s = float(np.median(durs))
        upshift_stats[f"{g_from}-{g_to}"] = {
            "n": len(durs), "median_s": median_s, "min_s": float(min(durs)), "max_s": float(max(durs)),
            "attack_shift_s": attack_s, "ratio_real_over_attack": median_s / attack_s,
            "durations_s": durs,
        }
        print(f"  ({g_from}->{g_to}): n={len(durs)}  median={median_s:.2f}s  "
              f"(ATTACK-Default={attack_s:.2f}s, {median_s / attack_s:.1f}x langsamer)")

    print("\nWICHTIG: das sind normale Strassenfahrten, keine 'Attack'-Schaltvorgaenge (siehe Docstring) - "
          "diese Werte ersetzen ATTACK_SHIFT_S NICHT, sie zeigen nur, wie weit ueber dem Attack-Default "
          "entspanntes Schalten mit diesem Auto/Fahrer liegt.")

    # Plot: Boxplot je Gangpaar (upshift) + Referenzlinie ATTACK_SHIFT_S
    pairs = list(upshift_stats.keys())
    fig, ax = plt.subplots(figsize=(8, 5))
    if pairs:
        data = [upshift_stats[p]["durations_s"] for p in pairs]
        ax.boxplot(data, tick_labels=pairs, showmeans=True)
        attack_vals = [upshift_stats[p]["attack_shift_s"] for p in pairs]
        ax.plot(range(1, len(pairs) + 1), attack_vals, "rD", label="ATTACK_SHIFT_S (Literatur-Default)")
        ax.set_ylabel("Kupplungs-Betaetigungsdauer [s]")
        ax.set_xlabel("Gangwechsel")
        ax.set_title("Reale Schaltzeiten (Alltagsfahrt) vs. ATTACK_SHIFT_S-Default")
        ax.legend()
        ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "shift_time_analysis.png")
    plt.savefig(out_png, dpi=130)
    plt.close(fig)

    out = {
        "log_ids": log_ids, "n_events_total": len(all_events),
        "counts_by_kind": {k: len(v) for k, v in by_kind.items()},
        "upshift_vs_attack": upshift_stats,
        "all_events": all_events,
    }
    out_json = os.path.join(RESULTS_DIR, "shift_time_analysis_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    best_times = build_best_times(by_kind)
    print("\nBestzeiten je Gangpaar/Richtung (schnellste je bisher gemessene Kupplungs-Betaetigung):")
    for key, v in sorted(best_times.items()):
        arrow = key.replace("-", "->")
        print(f"  {arrow} ({v['kind']}): {v['best_s']:.2f}s  ({v['log_id']} @ t={v['t_start']:.1f}s, n={v['n']})")
    best_path = os.path.join(RESULTS_DIR, "shift_time_best.json")
    with open(best_path, "w", encoding="utf-8") as f:
        json.dump(best_times, f, indent=2, ensure_ascii=False)

    print(f"\nPlot: {out_png}")
    print(f"Details: {out_json}")
    print(f"Bestzeiten: {best_path}")


if __name__ == "__main__":
    main()
