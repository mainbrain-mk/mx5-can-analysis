"""
Detail-Plots fuer einzelne Schaltvorgaenge (MX-5 Projekt)

Ein PNG pro Ereignis (Speed/RPM/Kupplung uebereinander, gleiche Zeitachse),
zur manuellen Detailpruefung der Zugkraftlueckenerkennung aus
shift_traction_gap_analysis.py. Nimmt standardmaessig die 20 schnellsten
Schaltvorgaenge nach echter Zugkraftunterbrechung (results/shift_
traction_gap_summary.json, "fastest_by_gap") - per --clutch stattdessen
die 20 schnellsten nach Kupplungszeit ("fastest_by_clutch").

Aufruf: .venv/bin/python scripts/shift_detail_plots.py [--clutch] [--top N]
"""
import argparse
import json
import os

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/datalake.duckdb"
SUMMARY_PATH = "results/shift_traction_gap_summary.json"
OUT_DIR = "results/shift_detail_plots"
MARGIN_S = 1.2
WHEEL_CHANNELS = ["WheelSpeed_CAN_1", "WheelSpeed_CAN_2", "WheelSpeed_CAN_3", "WheelSpeed_CAN_4"]
WHEEL_LABELS = {"WheelSpeed_CAN_1": "VL", "WheelSpeed_CAN_2": "VR",
                "WheelSpeed_CAN_3": "HL", "WheelSpeed_CAN_4": "HR"}
WHEEL_COLORS = {"WheelSpeed_CAN_1": "tab:blue", "WheelSpeed_CAN_2": "navy",
                "WheelSpeed_CAN_3": "tab:cyan", "WheelSpeed_CAN_4": "steelblue"}
# gleiche Glaettung wie compute_drive_gap() in shift_traction_gap_analysis.py -
# der Plot zeigt damit genau das Signal, auf dem auch die Luecken-Erkennung sitzt.
SMOOTH_GRID_STEP_S = 0.05
SMOOTH_WINDOW_SAMPLES = 4


def load(con, log_id, channel, t0, t1):
    df = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = ? "
        "AND t_elapsed_s BETWEEN ? AND ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel, t0, t1],
    ).fetchdf()
    return df["t"].values, df["value"].values


def load_smoothed(con, log_id, channel, t0, t1):
    """Wie load(), aber auf ein festes Raster resampled und leicht geglaettet
    (0.2s gleitendes Fenster, randreplizierte Kanten statt Null-Padding)."""
    t, v = load(con, log_id, channel, t0, t1)
    if len(t) < 2:
        return t, v
    tg = np.arange(t0, t1, SMOOTH_GRID_STEP_S)
    vg = np.interp(tg, t, v)
    pad = SMOOTH_WINDOW_SAMPLES // 2
    padded = np.pad(vg, pad, mode="edge")
    kernel = np.ones(SMOOTH_WINDOW_SAMPLES) / SMOOTH_WINDOW_SAMPLES
    smooth = np.convolve(padded, kernel, mode="valid")[:len(vg)]
    return tg, smooth


def plot_event(con, rank, ev, out_dir, per_wheel=False, show_wheels=False, show_app=False):
    """per_wheel: ERSETZT das Speed-Panel durch Raddrehzahlen (alter Modus).
    show_wheels: ERGAENZT ein eigenes Raddrehzahl-Panel zusaetzlich zu Speed.
    show_app: ERGAENZT ein Gaspedal-Panel (APP)."""
    log_id, t_start, t_end = ev["log_id"], ev["t_start"], ev["t_end"]
    t0, t1 = t_start - MARGIN_S, t_end + MARGIN_S

    t_rpm, v_rpm = load(con, log_id, "EngineRPM", t0, t1)
    t_clu, v_clu = load(con, log_id, "ClutchPosition_CAN_raw", t0, t1)

    n_panels = 3 + (1 if show_wheels and not per_wheel else 0) + (1 if show_app else 0)
    fig, axes = plt.subplots(n_panels, 1, figsize=(5.5, 2.15 * n_panels), sharex=True)
    axes = list(axes)

    for ax in axes:
        ax.axvspan(t_start, t_end, color="tab:orange", alpha=0.15, label="Kupplung getreten")
        if ev.get("drive_gap_start_s") is not None:
            ax.axvspan(ev["drive_gap_start_s"], ev["drive_gap_end_s"],
                       color="tab:red", alpha=0.25, label="Zugkraftlücke")
        ax.grid(alpha=0.3)

    ax_spd = axes.pop(0)
    if per_wheel:
        for ch in WHEEL_CHANNELS:
            t_w, v_w = load_smoothed(con, log_id, ch, t0, t1)
            ax_spd.plot(t_w, v_w, color=WHEEL_COLORS[ch], lw=1.2, label=WHEEL_LABELS[ch])
        ax_spd.set_ylabel("Raddrehzahl [km/h] (geglättet)")
        ax_spd.legend(loc="lower right", fontsize=7, framealpha=0.9, ncol=4)
    else:
        t_spd, v_spd = load(con, log_id, "DisplaySpeed_CAN", t0, t1)
        ax_spd.plot(t_spd, v_spd, color="tab:blue", lw=1.3)
        ax_spd.set_ylabel("Speed [km/h]")
        ax_spd.legend(loc="lower right", fontsize=7, framealpha=0.9)

    if show_wheels and not per_wheel:
        ax_wheel = axes.pop(0)
        for ch in WHEEL_CHANNELS:
            t_w, v_w = load_smoothed(con, log_id, ch, t0, t1)
            ax_wheel.plot(t_w, v_w, color=WHEEL_COLORS[ch], lw=1.2, label=WHEEL_LABELS[ch])
        ax_wheel.set_ylabel("Raddrehzahl [km/h]\n(geglättet)")
        ax_wheel.legend(loc="lower right", fontsize=7, framealpha=0.9, ncol=4)

    ax_rpm = axes.pop(0)
    ax_rpm.plot(t_rpm, v_rpm, color="tab:green", lw=1.3)
    ax_rpm.set_ylabel("RPM")

    if show_app:
        ax_app = axes.pop(0)
        t_app, v_app = load(con, log_id, "APP", t0, t1)
        ax_app.plot(t_app, v_app, color="tab:brown", lw=1.3)
        ax_app.set_ylabel("APP [%]")
        ax_app.set_ylim(-5, 105)

    ax_clu = axes.pop(0)
    ax_clu.plot(t_clu, v_clu, color="tab:purple", lw=1.3)
    ax_clu.axhline(15, color="grey", ls=":", lw=1, label="Schwelle (15/199)")
    ax_clu.set_ylabel("Kupplung (raw 0–199)")
    ax_clu.set_xlabel("t [s]")
    ax_clu.legend(loc="lower right", fontsize=7, framealpha=0.9)

    gap_str = f"{ev['drive_gap_duration_s']:.2f}s" if not ev.get("no_gap_detected") else "n/a"
    fig.suptitle(
        f"#{rank}  {ev['gear_pair'].replace('-', '→')}   {log_id}  t≈{t_start:.1f}s\n"
        f"Kupplung {ev['clutch_duration_s']:.2f}s · Zugkraftlücke {gap_str}",
        fontsize=9.5,
    )
    plt.tight_layout(rect=(0, 0, 1, 0.95))

    suffix = ("_wheels" if per_wheel else "") + ("_extrawheels" if show_wheels and not per_wheel else "") + ("_app" if show_app else "")
    fname = f"{rank:02d}_{ev['gear_pair']}_{log_id}_t{t_start:.0f}{suffix}.png"
    out_path = os.path.join(out_dir, fname)
    plt.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clutch", action="store_true",
                         help="nach Kupplungszeit statt Zugkraftlücke sortieren")
    parser.add_argument("--top", type=int, default=20, help="Anzahl Plots (default 20)")
    parser.add_argument("--wheels", action="store_true",
                         help="einzelne Raddrehzahlen statt DisplaySpeed_CAN im oberen Panel")
    parser.add_argument("--per-gear", action="store_true",
                         help="ein Plot pro Gangpaar (jeweils schnellste Zugkraftlücke) statt --top N insgesamt")
    parser.add_argument("--show-wheels", action="store_true",
                         help="zusaetzliches Raddrehzahl-Panel (neben Speed, nicht ersetzend)")
    parser.add_argument("--show-app", action="store_true", help="zusaetzliches Gaspedal-Panel (APP)")
    parser.add_argument("--gear", help="nur dieses Gangpaar, z.B. 5-6 (nur mit --per-gear)")
    args = parser.parse_args()

    with open(SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)

    os.makedirs(OUT_DIR, exist_ok=True)
    con = duckdb.connect(DB_PATH, read_only=True)

    if args.per_gear:
        by_pair = {}
        for r in summary["all_results"]:
            if r["no_gap_detected"]:
                continue
            by_pair.setdefault(r["gear_pair"], []).append(r)
        pairs = [args.gear] if args.gear else sorted(by_pair, key=lambda p: [int(x) for x in p.split("-")])
        print(f"Ein Plot je Gangpaar (schnellste Zugkraftlücke) -> {OUT_DIR}/")
        for pair in pairs:
            fastest = min(by_pair[pair], key=lambda r: r["drive_gap_duration_s"])
            out_path = plot_event(con, 1, fastest, OUT_DIR, per_wheel=args.wheels,
                                   show_wheels=args.show_wheels, show_app=args.show_app)
            print(f"  {pair}: {out_path}")
        return

    events = (summary["fastest_by_clutch"] if args.clutch else summary["fastest_by_gap"])[:args.top]
    label = "Kupplungszeit" if args.clutch else "Zugkraftlücke"

    print(f"{len(events)} Plots (sortiert nach {label}) -> {OUT_DIR}/")
    for rank, ev in enumerate(events, start=1):
        out_path = plot_event(con, rank, ev, OUT_DIR, per_wheel=args.wheels,
                               show_wheels=args.show_wheels, show_app=args.show_app)
        print(f"  {out_path}")


if __name__ == "__main__":
    main()
