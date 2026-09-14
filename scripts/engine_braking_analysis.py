"""
Motorbremskraft-Modell: APP=0, Kupplung geschlossen, Gang eingelegt (MX-5 Projekt)

Zweck (Nutzerhinweis 08.09.2026): das bisherige "coastAccel()" (siehe
performance_simulation.py) modelliert NUR Luft-/Rollwiderstand - das war
urspruenglich fuer die Rollphase WAEHREND eines Schaltvorgangs gedacht
(Kupplung/Kraftschluss kurz unterbrochen, siehe dortiger Docstring), wird
aber im neuen "natuerlichen Gasmodell" (spreewaldring_racing_line_editor.py)
faelschlich auch fuer STEADY-STATE Rollen bei geschlossener Kupplung UND
eingelegtem Gang verwendet - dort fehlt die zusaetzliche Verzoegerung durch
das Motorbremsmoment komplett.

Methodik:
  1. "Schub-Ereignisse" (Ausrollen im Gang statt Neutral) automatisch
     finden: Gang in 1-6 (TM_GEST gerundet), APP<APP_MAX, Kupplung
     (CPP_PER_MZ)<CLUTCH_MAX, keine Bremsung, v>=MIN_SPEED_KMH,
     Mindestdauer MIN_DURATION_S - ueber ALLE Logs mit den noetigen
     Kanaelen (nicht nur Spreewaldring-relevante). 594 Ereignisse aus 39
     Logs (n=397 mit der strengeren 3s-Mindestdauer unten).
  2. Pro Ereignis: mittlere Gesamtverzoegerung a_total=(v_end-v_start)/dt,
     bekannten Luft-/Rollwiderstandsanteil (Referenzwerte CDA_M2/CRR aus
     drivetrain_model_validation.py) bei der mittleren Geschwindigkeit
     abgezogen -> Rest = zusaetzliche Verzoegerung durch den Motor.

  ERSTER ANSATZ (verworfen): Rest gegen RPM auffuellen, nach Entkopplung
  von der Ganguebersetzung (Rest*R_DYN_M/(Gang*Achsuebersetzung) als
  "aequivalentes Motor-Reibmoment"), in der Annahme "Motorreibung ist eine
  reine RPM-Funktion, unabhaengig vom Gang". Ergebnis: R^2 nahe 0 (sogar
  leicht NEGATIV ohne Achsenabschnitt) UND weiterhin ein Gang-Trend in den
  Residuen - die Annahme traegt nicht (moeglicherweise Schubabschaltung
  nur oberhalb einer RPM-Schwelle, nicht linear; evtl. auch zu kurze/
  verrauschte Einzelereignis-Steigungen bei nur 1 km/h Aufloesung von
  VehicleSpeed).

  ZWEITER ANSATZ (haelt): Rest DIREKT gegen die Geschwindigkeit binnen
  (nicht RPM/Gang) - zeigt ein sauberes, monotones Bild: die zusaetzliche
  Verzoegerung ist bei niedrigem Tempo am groessten (~0.4 m/s^2 bei
  20-30 km/h) und nimmt zum hohen Tempo hin ab (~0.05-0.15 m/s^2 bei
  120-200 km/h). Physikalisch plausibel: bei diesem Fahrzeug/diesen
  Fahrern wird bei niedrigem Tempo eher in niedrigeren Gaengen ausgerollt
  (hohe Drehmomentwandlung Motor->Rad), bei hohem Tempo eher im
  hoechsten Gang (Wandlung ~1:1) - die geschwindigkeitsabhaengige Kurve
  bildet dieses TYPISCHE Gangwahlverhalten implizit mit ab, was fuer ein
  Rad-Ebenen-Modell (das ist alles, was die Streckensimulation braucht)
  genau richtig ist.

Aufruf: .venv/bin/python scripts/engine_braking_analysis.py
"""
import json
import os

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from drivetrain_model_validation import (
    MASS_KG, RHO_KG_M3, CDA_M2, CRR, G,
)

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"

APP_MAX = 1.0
CLUTCH_MAX = 1.0
BRAKE_MAX_KPA = 20.0
MIN_SPEED_KMH = 20.0
MIN_DURATION_S = 3.0

# Bin-Grenzen [km/h] fuer die empirische Kurve.
SPEED_BINS_KMH = [20, 30, 40, 50, 60, 70, 80, 100, 120, 150, 200, 260]


def load_channel(con, log_id, channel):
    df = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL ORDER BY t",
        [log_id, channel],
    ).fetchdf()
    return df["t"].values, df["value"].values


def group_runs(mask):
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return []
    splits = np.where(np.diff(idx) > 1)[0]
    return np.split(idx, splits + 1)


def find_events(con, log_id):
    t_sp, v_kmh = load_channel(con, log_id, "VehicleSpeed")
    t_ge, ge = load_channel(con, log_id, "TM_GEST")
    t_ap, ap = load_channel(con, log_id, "APP")
    t_cp, cp = load_channel(con, log_id, "CPP_PER_MZ")
    if len(t_sp) < 5 or len(t_ge) < 5 or len(t_ap) < 5 or len(t_cp) < 5:
        return []
    t_bf, bf = load_channel(con, log_id, "BFP_PRE_MZ")
    t_rp, rp = load_channel(con, log_id, "EngineRPM")
    if len(t_rp) < 5:
        return []

    t = t_sp
    v_ms = v_kmh / 3.6
    g_i = np.round(np.interp(t, t_ge, ge))
    app_i = np.interp(t, t_ap, ap)
    cpp_i = np.interp(t, t_cp, cp)
    brake_i = np.interp(t, t_bf, bf) if len(t_bf) >= 2 else np.zeros_like(t)
    rpm_i = np.interp(t, t_rp, rp)

    mask = ((g_i >= 1) & (g_i <= 6) & (app_i < APP_MAX) & (cpp_i < CLUTCH_MAX)
            & (brake_i < BRAKE_MAX_KPA) & (v_kmh >= MIN_SPEED_KMH))

    events = []
    for run in group_runs(mask):
        if len(run) < 3:
            continue
        dur = t[run[-1]] - t[run[0]]
        if dur < MIN_DURATION_S:
            continue
        v_start, v_end = v_ms[run[0]], v_ms[run[-1]]
        a_total = (v_end - v_start) / dur
        if a_total >= 0:
            continue
        events.append({
            "log_id": log_id, "gear": int(g_i[run[0]]), "duration_s": float(dur),
            "v_mean_ms": float(np.mean(v_ms[run])), "rpm_mean": float(np.mean(rpm_i[run])),
            "a_total_ms2": float(a_total),
        })
    return events


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    logs = con.execute("SELECT DISTINCT log_id FROM measurements WHERE channel='CPP_PER_MZ'").fetchdf()["log_id"].tolist()

    all_events = []
    for log_id in logs:
        all_events.extend(find_events(con, log_id))
    con.close()
    print(f"{len(all_events)} Schub-im-Gang-Ereignisse (>= {MIN_DURATION_S}s) aus {len(logs)} Logs")

    v = np.array([e["v_mean_ms"] for e in all_events])
    a_total = np.array([e["a_total_ms2"] for e in all_events])
    a_aero_roll = -(0.5 * RHO_KG_M3 * CDA_M2 * v**2 + CRR * MASS_KG * G) / MASS_KG
    a_engine = np.minimum(a_total - a_aero_roll, 0)

    v_kmh = v * 3.6
    centers, medians, ns = [], [], []
    for i in range(len(SPEED_BINS_KMH) - 1):
        lo, hi = SPEED_BINS_KMH[i], SPEED_BINS_KMH[i + 1]
        m = (v_kmh >= lo) & (v_kmh < hi)
        if m.sum() >= 5:
            centers.append(float((lo + hi) / 2))
            medians.append(float(np.median(a_engine[m])))
            ns.append(int(m.sum()))
            print(f"v={lo:3d}-{hi:3d}km/h: n={m.sum():4d}, a_engine median={np.median(a_engine[m]):+.3f} m/s^2")

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(v_kmh, a_engine, s=8, alpha=0.25, label="Einzelereignisse")
    ax.plot(centers, medians, "ro-", lw=2, label="Bin-Median (-> Interpolationstabelle)")
    ax.set_xlabel("Geschwindigkeit [km/h]")
    ax.set_ylabel("zusaetzliche Verzoegerung durch Motor [m/s^2]")
    ax.set_title(f"Motorbremskraft vs. Geschwindigkeit (n={len(all_events)})")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "engine_braking_analysis.png")
    fig.savefig(out_png, dpi=130)
    print(f"\nPlot gespeichert: {out_png}")

    out_json = os.path.join(RESULTS_DIR, "engine_braking_model.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "n_events": len(all_events), "n_logs": len(logs),
            "note": "v-basierte Interpolationstabelle (Bin-Median), RPM/Gang-basiertes Modell verworfen (R^2~0), siehe Docstring",
            "table_v_kmh": centers, "table_a_engine_ms2": medians, "table_n": ns,
        }, f, indent=2, ensure_ascii=False)
    print(f"Modell gespeichert: {out_json}")


if __name__ == "__main__":
    main()
