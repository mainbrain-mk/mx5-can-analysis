"""
ECU-Soft-Limiter: alle Vollgaszuege ueber 6900/min in allen CAN-Logs, 08.11.2026

Die ECU zieht ab einer gangabhaengigen Drehzahl das Moment (0x167 ActualEnginePercentTorque) trotz APP 100 % in
einer Rampe zurueck und haelt es bis zum Gaswegnehmen (Logbuch can-bus-status.md, "Soft-Limiter im 4. Gang" und
"Limiter in der Rundensimulation"). Dieses Skript liefert die Werte fuer LIMITER_* in spreewaldring_lap_simulation.py:
Beginn je Gang (Median) und Momentanteil ueber der Drehzahl seit Beginn.

Zug = zusammenhaengend APP >= 99, nur bis zum Schalten (Drehzahl > 150/min unter ihrem bisherigen Maximum),
Volllast-Niveau = Median des Moments bei 6000-6800/min (>= 70 %, sonst kein sauberer Zug). Eingriff = Moment
<= 80 % des Niveaus ueber 6900/min, Beginn = letzter Punkt davor mit >= 95 %. Motorverhalten, gilt fuer beide
Reifensaetze.

Aufruf: .venv/bin/python scripts/ecu_limiter_events.py
"""
import os

import duckdb
import numpy as np
import pandas as pd

from datalake_channels import log_key

DB_PATH = "data/datalake.duckdb"
OUT_PATH = os.path.join("results", "ecu_limiter_events.csv")
CH = ["EngineRPM", "APP", "ActualEnginePercentTorque_CAN", "Gear_CAN"]
RAMP_BINS = [0, 20, 40, 60, 80, 100, 130, 160, 200]


def pulls(con, log_id):
    df = con.execute(f"SELECT channel, t_elapsed_s t, value FROM measurements WHERE log_key=? AND log_id=? "
                     f"AND channel IN ({','.join('?' * len(CH))}) AND value IS NOT NULL ORDER BY t",
                     [log_key(log_id), log_id, *CH]).fetchdf()
    g = {c: d for c, d in df.groupby("channel")}
    if any(c not in g for c in CH):
        return
    t, trq = g["ActualEnginePercentTorque_CAN"].t.values, g["ActualEnginePercentTorque_CAN"].value.values
    rpm = np.interp(t, g["EngineRPM"].t, g["EngineRPM"].value)
    app = np.interp(t, g["APP"].t, g["APP"].value)
    gear = np.interp(t, g["Gear_CAN"].t, g["Gear_CAN"].value)
    edges = np.flatnonzero(np.diff(np.r_[0, (app >= 99).astype(int), 0]))
    for a, b in zip(edges[::2], edges[1::2]):
        drop = np.flatnonzero(rpm[a:b] < np.maximum.accumulate(rpm[a:b]) - 150)
        b = a + (drop[0] if len(drop) else b - a)
        r, q = rpm[a:b], trq[a:b]
        band = (r > 6000) & (r < 6800)
        if b - a < 5 or r.max() < 6900 or band.sum() < 3 or np.median(q[band]) < 70:
            continue
        lvl = np.median(q[band])
        rec = {"log_id": log_id, "gear": int(round(np.median(gear[a:b][r > 6000]))), "max_rpm": r.max(),
               "full_load_pct": lvl, "event": False}
        hit = np.flatnonzero((q <= 0.8 * lvl) & (r > 6900))
        if len(hit):
            j = hit[0]
            while j > 0 and q[j - 1] < 0.95 * lvl:
                j -= 1
            rec.update(event=True, t_onset=t[a + j], onset_rpm=r[j], held_s=t[b - 1] - t[a + j])
            rec["ramp"] = list(zip(r[j:] - r[j], q[j:] / lvl))
        yield rec


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    logs = con.execute("SELECT log_id FROM logs WHERE source_format LIKE 'can%' ORDER BY start_time_local").fetchdf()
    res = pd.DataFrame([p for log_id in logs.log_id for p in pulls(con, log_id)])
    con.close()
    res.drop(columns="ramp").to_csv(OUT_PATH, index=False)
    ev = res[res.event]
    print(f"{len(res)} Vollgaszuege ueber 6900/min, {len(ev)} mit Eingriff -> {OUT_PATH}")
    print(ev.groupby("gear").onset_rpm.describe()[["count", "50%", "min", "max"]].round(0).to_string())
    print("ohne Eingriff, hoechste Drehzahl:", res[~res.event].groupby("gear").max_rpm.max().round(0).to_dict())
    ramp = pd.DataFrame([(g, dr, f) for g, rp in zip(ev.gear, ev.ramp) for dr, f in rp], columns=["gear", "dr", "f"])
    ramp["bin"] = pd.cut(ramp.dr, RAMP_BINS, include_lowest=True)
    print("Momentanteil ueber Drehzahl seit Beginn (Median):")
    print(ramp.pivot_table(index="bin", columns="gear", values="f", aggfunc="median", observed=True).round(2).to_string())


if __name__ == "__main__":
    main()
