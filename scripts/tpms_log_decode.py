"""
TPMS-Rohdaten (Reifendruck/-temperatur) aus candump-Logs dekodieren -
erster Live-Auswertungsschritt fuer tpms_poller.py (siehe mx5_tpms-Memory:
"gebaut+deployed, noch nicht live getestet").

tpms_poller.py sendet aktive UDS-Requests (0x720) und wertet die Antworten
(erwartet 0x728) selbst nur live/interaktiv aus (druckt auf stdout, landet
nicht im Datalake) - candump zeichnet als reiner Bus-Sniffer aber sowohl
die eigenen Request- als auch die Kombiinstrument-Antwort-Frames aufs
normale Log mit auf. Dieses Skript liest die Antwort-Frames direkt aus
einer *.log-Datei (kein Pipeline-Schritt, ad-hoc-Auswertung fuer den
ersten Blick auf echte Daten) und dekodiert sie mit denselben
PIDS/decode_response()-Formeln wie tpms_poller.py (Single Source of
Truth, kein zweites PID-Mapping gepflegt).

Antwort-ID 0x728 bestaetigt (genau wie in tpms_poller.py vermutet, ISO-
15765-Standardoffset Request+8) - in den ersten Logs mit aktiver TPMS-
Abfrage (2026-09-13/14) tauchen zuverlaessig ~192-200 Requests UND
Antworten pro Fahrt auf, exakt passend zur Anzahl Pollrunden
(Fahrtdauer/120s * 8 PIDs).

Aufruf: python scripts/tpms_log_decode.py <candump.log> [<candump.log> ...]
"""
import re
import sys
import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tpms_poller import PIDS, decode_response

# Radzuordnung (siehe mx5_tpms-Memory): Tire3=hinten links, Tire4=hinten
# rechts per gezieltem Test bestaetigt. Tire1/Tire2=Vorderachse, Reihenfolge
# noch NICHT bestaetigt (mit "?" markiert) - noch kein gezielter Luftablass-
# Test an einem einzelnen Vorderrad gemacht.
TIRE_LABELS = {
    "Tire1": "vorne ? (1)", "Tire2": "vorne ? (2)",
    "Tire3": "hinten links", "Tire4": "hinten rechts",
}

RESPONSE_ID = 0x728
LINE_RE = re.compile(r"^\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)$")


def decode_log(path):
    with open(path) as f:
        first_t0 = None
        rows = []
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            t_abs, can_id_hex, data_hex = m.groups()
            if int(can_id_hex, 16) != RESPONSE_ID:
                continue
            t_abs = float(t_abs)
            if first_t0 is None:
                first_t0 = t_abs
            data = bytes.fromhex(data_hex)
            for did, (name, formula) in PIDS.items():
                raw = decode_response(did, data)
                if raw is not None:
                    rows.append({"t": t_abs - first_t0, "signal": name, "value": formula(raw)})
                    break
    return rows


def summarize(rows, log_name):
    by_signal = {}
    for r in rows:
        by_signal.setdefault(r["signal"], []).append(r["value"])
    print(f"\n=== {log_name} ({len(rows)} dekodierte TPMS-Samples) ===")
    for name in sorted(by_signal):
        vals = by_signal[name]
        unit = "bar" if "Pressure" in name else "°C"
        print(f"  {name}: n={len(vals)}  min={min(vals):.2f}{unit}  max={max(vals):.2f}{unit}  "
              f"mean={sum(vals)/len(vals):.2f}{unit}")


def plot_log(rows, log_name, out_path):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    colors = {"Tire1": "tab:blue", "Tire2": "tab:orange", "Tire3": "tab:green", "Tire4": "tab:red"}
    for tire, color in colors.items():
        pts = sorted((r["t"], r["value"]) for r in rows if r["signal"] == f"{tire}_Pressure_bar")
        if pts:
            t, v = zip(*pts)
            ax1.plot([x / 60 for x in t], v, "o-", color=color, label=TIRE_LABELS[tire])
        pts = sorted((r["t"], r["value"]) for r in rows if r["signal"] == f"{tire}_Temp_C")
        if pts:
            t, v = zip(*pts)
            ax2.plot([x / 60 for x in t], v, "o-", color=color, label=TIRE_LABELS[tire])
    ax1.set_ylabel("Reifendruck [bar]")
    ax1.set_title(f"TPMS (UDS 0x720/0x728) — {log_name}")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    ax2.set_ylabel("Reifentemperatur [°C]")
    ax2.set_xlabel("Zeit [min]")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Aufruf: python scripts/tpms_log_decode.py <candump.log> [...]")
    all_rows = {}
    os.makedirs("results", exist_ok=True)
    for path in sys.argv[1:]:
        rows = decode_log(path)
        log_name = os.path.splitext(os.path.basename(path))[0]
        all_rows[log_name] = rows
        summarize(rows, log_name)
        if rows:
            png_path = f"results/tpms_{log_name}.png"
            plot_log(rows, log_name, png_path)
            print(f"  Plot: {png_path}")

    out_path = "results/tpms_decoded.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {out_path}")


if __name__ == "__main__":
    main()
