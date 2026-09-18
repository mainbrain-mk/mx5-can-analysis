"""
Zugkraftunterbrechungs-Analyse fuer Hochschaltungen (MX-5 Projekt)

Ausgangspunkt: die Kupplungspedal-Dauer aus shift_time_analysis.py ist NICHT
dasselbe wie die fuer ATTACK_SHIFT_S relevante Groesse - die Zeit ohne
Antriebskraft ("Zugkraftunterbrechung", siehe coast_accel() in
performance_simulation.py: "Beschleunigung waehrend der Schaltpause, keine
Antriebskraft"). Manuelle Stichprobe an 4 Faellen (verschiedene Gangpaare,
verschiedene Logs, alle mit APP>10% vor dem Schalten) zeigte durchgehend:
die echte Zugkraftunterbrechung ist nur ca. 1/3 bis 3/5 der Kupplungszeit
(Faktor 1.7x-3.5x), weil der Fahrer die Kupplung oft schon VOR dem
messbaren Vortriebsabfall tritt und/oder erst NACH Wiedereinsetzen des
Vortriebs komplett loslaesst.

Methodik, Version 2 (nach Detailpruefung einzelner Faelle mit Nutzer, siehe
Docstring-Historie): gemittelte Radgeschwindigkeit der HINTERACHSE (Raeder 3+4,
die angetriebenen) statt aller 4 Raeder - die Vorderachse zeigt den
Drehmomentabriss zeitversetzt/gedaempft (ueber die Fahrzeugverzoegerung als
Ganzes), das verwaessert die Erkennung um bis zu 0.1s Verzoegerung + 33%
kuerzere Luecke (an einem Beispiel geprueft: Hinterachse allein 0.15s/
1051.08-1051.23, alle 4 Raeder 0.10s/1051.18-1051.28). Auf ein 0.05s-Raster
resampled, geglaettet (0.2s gleitendes Fenster - ungeglaettet ist die
Ableitung zu verrauscht fuer eine stabile Nulldurchgangs-Erkennung), daraus
die Beschleunigung per Gradient.

Schwelle relativ zur Vorschalt-Beschleunigung (Median im Fenster VOR dem
Kupplungstritt) statt fest: die alte feste 0.1 km/h/s-Schwelle erfasste nur
die Kernzone nahe Null, nicht die "Rampe runter/rauf" drumherum, in der die
Beschleunigung schon deutlich unter Vorschalt-Niveau liegt, aber noch nicht
bei Null ist - das unterschaetzte die Luecke an einem geprueften Fall um
Faktor ~2 (0.10s statt der optisch erkennbaren ~0.25-0.30s). Neue Schwelle:
RELATIVE_FRACTION * Vorschalt-Beschleunigung, mit DV_THRESHOLD_ABS_FLOOR als
Untergrenze (falls die Vorschalt-Beschleunigung selbst schon klein/negativ
ist - Ausrollen/sanftes Cruisen vor dem Schalten - waere ein rein relativer
Wert unsinnig klein oder negativ).

Nutzt die bereits von shift_time_analysis.py erkannten Upshift-Ereignisse
(results/shift_time_analysis_summary.json) als Eingabe, statt die Kupplungs-
Erkennung zu duplizieren - EIN Ort fuer "was ist ein Schaltvorgang".

Aufruf: .venv/bin/python scripts/shift_traction_gap_analysis.py
"""
import json
import os

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
SHIFT_TIME_SUMMARY = os.path.join(RESULTS_DIR, "shift_time_analysis_summary.json")

WHEEL_CHANNELS_REAR = ["WheelSpeed_CAN_3", "WheelSpeed_CAN_4"]  # angetriebene Achse (RWD)
GRID_STEP_S = 0.05
SMOOTH_WINDOW_SAMPLES = 4       # 0.2s bei 0.05s-Raster
DV_THRESHOLD_ABS_FLOOR = 0.1    # Mindestschwelle, falls Vorschalt-Beschleunigung klein/negativ ist
RELATIVE_FRACTION = 0.35        # Schwelle = dieser Anteil der Vorschalt-Beschleunigung
BASELINE_WINDOW_S = 0.4         # Fenster vor t_start, aus dem die Vorschalt-Beschleunigung kommt
SEARCH_MARGIN_S = 0.3           # Suchfenster um [t_start, t_end] der Kupplungsbetaetigung
GAP_BRIDGE_S = 0.06             # einzelne Rausch-Ausreisser ueber der Schwelle ueberbruecken


def _load(con, log_id, channel, t0, t1):
    df = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = ? "
        "AND t_elapsed_s BETWEEN ? AND ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel, t0, t1],
    ).fetchdf()
    return df["t"].values, df["value"].values


def mean_smoothed_accel(con, log_id, t0, t1):
    """Gibt (tg, accel) zurueck: Zeitraster + geglaettete Beschleunigung der
    ueber die Hinterachse (angetriebene Raeder) gemittelten Geschwindigkeit.
    None, falls ein Rad fehlt."""
    tg = np.arange(t0, t1, GRID_STEP_S)
    if len(tg) < SMOOTH_WINDOW_SAMPLES + 2:
        return None, None
    mean_v = np.zeros_like(tg)
    for ch in WHEEL_CHANNELS_REAR:
        t, v = _load(con, log_id, ch, t0, t1)
        if len(t) < 2:
            return None, None
        mean_v += np.interp(tg, t, v)
    mean_v /= len(WHEEL_CHANNELS_REAR)
    # Rand-Padding mit Kantenwerten statt implizitem Zero-Padding (mode="same"
    # wuerde am Fensterrand einen falschen Geschwindigkeitsabfall vortaeuschen,
    # der als Luecke fehlinterpretiert wird - siehe Selbsttest-Historie).
    pad = SMOOTH_WINDOW_SAMPLES // 2
    padded = np.pad(mean_v, pad, mode="edge")
    kernel = np.ones(SMOOTH_WINDOW_SAMPLES) / SMOOTH_WINDOW_SAMPLES
    smooth_v = np.convolve(padded, kernel, mode="valid")[:len(mean_v)]
    accel = np.gradient(smooth_v, tg)
    return tg, accel


def find_longest_run(tg, mask, gap_bridge_s):
    """Laengster zusammenhaengender True-Bereich in `mask` (kleine Luecken
    bis gap_bridge_s ueberbrueckt). Gibt (start_idx, end_idx) oder None."""
    runs, start = [], None
    for i in range(len(mask)):
        if mask[i]:
            if start is None:
                start = i
            last = i
        elif start is not None and (tg[i] - tg[last]) > gap_bridge_s:
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    if not runs:
        return None
    return max(runs, key=lambda r: tg[r[1]] - tg[r[0]])


def compute_drive_gap(con, log_id, t_start, t_end):
    """Zugkraftunterbrechung fuer EIN Kupplungs-Ereignis (t_start/t_end aus
    shift_time_analysis.py). Gibt dict mit drive_gap_duration_s zurueck
    (0.0 + no_gap_detected=True, falls die Beschleunigung nie unter die
    Schwelle faellt - z.B. bei sehr sanftem/kurzem Antippen)."""
    t0 = t_start - SEARCH_MARGIN_S
    t1 = t_end + SEARCH_MARGIN_S
    tg, accel = mean_smoothed_accel(con, log_id, t0, t1)
    if tg is None:
        return None

    baseline_mask = (tg >= t_start - BASELINE_WINDOW_S) & (tg <= t_start - GRID_STEP_S)
    baseline_accel = float(np.median(accel[baseline_mask])) if baseline_mask.any() else 0.0
    threshold = max(DV_THRESHOLD_ABS_FLOOR, RELATIVE_FRACTION * baseline_accel)

    mask = accel <= threshold
    run = find_longest_run(tg, mask, GAP_BRIDGE_S)
    clutch_duration_s = t_end - t_start
    if run is None:
        return {
            "clutch_duration_s": clutch_duration_s,
            "drive_gap_start_s": None, "drive_gap_end_s": None,
            "drive_gap_duration_s": 0.0, "no_gap_detected": True,
            "baseline_accel_kmh_s": baseline_accel, "threshold_kmh_s": threshold,
        }
    s, e = run
    gap_duration = float(tg[e] - tg[s])
    return {
        "clutch_duration_s": clutch_duration_s,
        "drive_gap_start_s": float(tg[s]), "drive_gap_end_s": float(tg[e]),
        "drive_gap_duration_s": gap_duration, "no_gap_detected": False,
        "baseline_accel_kmh_s": baseline_accel, "threshold_kmh_s": threshold,
    }


def main():
    with open(SHIFT_TIME_SUMMARY, encoding="utf-8") as f:
        shift_data = json.load(f)
    upshifts = [e for e in shift_data["all_events"] if e["kind"] == "upshift"]
    print(f"{len(upshifts)} Hochschaltungen aus {SHIFT_TIME_SUMMARY}")

    con = duckdb.connect(DB_PATH, read_only=True)
    results = []
    skipped = 0
    for e in upshifts:
        r = compute_drive_gap(con, e["log_id"], e["t_start"], e["t_end"])
        if r is None:
            skipped += 1
            continue
        r.update({
            "log_id": e["log_id"], "t_start": e["t_start"], "t_end": e["t_end"],
            "gear_before": e["gear_before"], "gear_after": e["gear_after"],
            "gear_pair": f"{e['gear_before']:.0f}-{e['gear_after']:.0f}",
        })
        r["ratio_clutch_over_gap"] = (r["clutch_duration_s"] / r["drive_gap_duration_s"]
                                       if r["drive_gap_duration_s"] > 0 else None)
        results.append(r)
    if skipped:
        print(f"  {skipped} Ereignisse ohne vollstaendige Raddaten uebersprungen")

    no_gap = [r for r in results if r["no_gap_detected"]]
    with_gap = [r for r in results if not r["no_gap_detected"]]
    print(f"\n{len(with_gap)} mit erkannter Zugkraftunterbrechung, {len(no_gap)} ohne "
          "(Beschleunigung fiel nie unter die Schwelle)")

    print(f"\n{'Gangpaar':8s} {'n':>4s} {'Kupplung (median)':>18s} {'Zugkraftlücke (median)':>22s} {'Verhältnis':>10s}")
    by_pair = {}
    for r in with_gap:
        by_pair.setdefault(r["gear_pair"], []).append(r)
    pair_stats = {}
    for pair, rs in sorted(by_pair.items()):
        clutch_med = float(np.median([r["clutch_duration_s"] for r in rs]))
        gap_med = float(np.median([r["drive_gap_duration_s"] for r in rs]))
        pair_stats[pair] = {"n": len(rs), "clutch_median_s": clutch_med, "gap_median_s": gap_med,
                             "ratio_median": clutch_med / gap_med if gap_med else None}
        print(f"{pair:8s} {len(rs):4d} {clutch_med:16.2f}s {gap_med:20.2f}s {clutch_med / gap_med:9.2f}x")

    print("\n20 schnellste Hochschaltungen nach echter Zugkraftunterbrechung:")
    fastest_by_gap = sorted(with_gap, key=lambda r: r["drive_gap_duration_s"])[:20]
    for r in fastest_by_gap:
        print(f"  {r['gear_pair']:5s} Lücke={r['drive_gap_duration_s']:.2f}s  "
              f"(Kupplung={r['clutch_duration_s']:.2f}s)  {r['log_id']}  "
              f"t={r['drive_gap_start_s']:.1f}-{r['drive_gap_end_s']:.1f}s")

    print("\n20 schnellste Hochschaltungen nach Kupplungspedal-Zeit (zum Vergleich):")
    fastest_by_clutch = sorted(results, key=lambda r: r["clutch_duration_s"])[:20]
    for r in fastest_by_clutch:
        gap_str = f"{r['drive_gap_duration_s']:.2f}s" if not r["no_gap_detected"] else "n/a"
        print(f"  {r['gear_pair']:5s} Kupplung={r['clutch_duration_s']:.2f}s  "
              f"(Lücke={gap_str})  {r['log_id']}  t={r['t_start']:.1f}-{r['t_end']:.1f}s")

    # Plot: Streudiagramm Kupplungszeit vs. Zugkraftluecke, Farbe = Gangpaar
    fig, ax = plt.subplots(figsize=(7, 6))
    pairs_sorted = sorted(by_pair.keys())
    cmap = plt.get_cmap("tab10")
    for i, pair in enumerate(pairs_sorted):
        rs = by_pair[pair]
        ax.scatter([r["clutch_duration_s"] for r in rs], [r["drive_gap_duration_s"] for r in rs],
                   label=pair, color=cmap(i % 10), alpha=0.7, s=28)
    lims = [0, max(r["clutch_duration_s"] for r in with_gap) * 1.05]
    ax.plot(lims, lims, "k--", alpha=0.3, label="1:1 (Kupplung = Lücke)")
    ax.set_xlabel("Kupplungspedal-Dauer [s]")
    ax.set_ylabel("Zugkraftunterbrechung [s]")
    ax.set_title("Kupplungszeit vs. tatsächliche Zugkraftunterbrechung (Hochschaltungen)")
    ax.legend(title="Gangpaar", fontsize=8)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "shift_traction_gap_analysis.png")
    plt.savefig(out_png, dpi=130)
    plt.close(fig)

    out = {
        "n_upshifts": len(upshifts), "n_with_gap": len(with_gap), "n_no_gap_detected": len(no_gap),
        "pair_stats": pair_stats,
        "fastest_by_gap": fastest_by_gap,
        "fastest_by_clutch": fastest_by_clutch,
        "all_results": results,
    }
    out_json = os.path.join(RESULTS_DIR, "shift_traction_gap_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\nPlot: {out_png}")
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
