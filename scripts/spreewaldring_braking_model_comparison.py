"""Vergleich: Rundenzeit mit dem bisherigen theoretischen Reifenkraftkreis-
Bremsmodell (a_brake = sqrt((mu*g)^2 - a_lat^2)) gegen eine Bremsverzoegerung,
die auf den ECHTEN gemessenen Bremsvorgaengen basiert. Seit 02.10.2026 aus den
CAN-Bremsereignissen der NS-R2-Fahrten (can_brake_event_analysis.py, ~970 Bremsungen,
Tempoabfall je Ereignis), vorher 229 Handy/OBD-Bremsungen (braking_model.py).

WICHTIG (Nutzerfrage 08.09.2026, "Auswirkung auf die Rundenzeit"): das reale
Bremsmodell stammt aus ALLTAeGLICHEM Strassenfahren, nicht aus gezieltem
Renn-/Trackday-Bremsen - der beobachtete Maximalwert (~0.48g) ist deshalb
eher eine UNTERGRENZE dessen, was der Fahrer/das Fahrzeug am Limit koennte,
nicht die tatsaechliche Reifenhaftungsgrenze. Dieser Vergleich beantwortet
daher NICHT "wie schnell waere die Bestzeit wirklich", sondern "wie viel
Rundenzeit wuerde es kosten, wenn nur so hart gebremst wird wie bisher im
Alltag beobachtet, statt am theoretischen Reifenlimit". (02.10.: mit CAN-Daten liegt die
haerteste ABS-Bremsung bei 0,76 g - die alte ~0,48-g-Grenze war vor allem ein Messproblem.)
Ersetzt NICHT die
bisherige mu*g-Annahme als Standard - beide Szenarien werden nebeneinander
berichtet, wie schon bei den mu=1.0/1.3-Szenarien ueblich.

Aufruf: .venv/bin/python scripts/spreewaldring_braking_model_comparison.py
"""

import glob
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from spreewaldring_track import RESULTS_DIR
from spreewaldring_lap_simulation import TIRE_MU_RANGE
from spreewaldring_racing_line_optimal import BRAKE_CAP_G, simulate_lap_combined_friction

CORNERS_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_corners_summary.json")


def load_best_racing_line():
    with open(CORNERS_SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)
    best = min(summary["snapshots"], key=lambda s: s["lap_time_s"])
    return np.array(best["points_utm33"])


CAN_BRAKE_SUMMARY_GLOB = "results/can_brake_event_summary_candump-*.json"
LAST_NSR2_LOG = "candump-2026-10-01_142300"  # Reifenwechsel auf Winterreifen danach

def load_real_brake_caps():
    """Seit 02.10.2026 aus den CAN-Bremsereignissen der NS-R2-Fahrten (siehe BRAKE_CAP_G-Kommentar in
    spreewaldring_racing_line_optimal.py), vorher aus braking_model_summary.json (Handy/OBD)."""
    events = []
    for path in glob.glob(CAN_BRAKE_SUMMARY_GLOB):
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        if entry["log_id"] <= LAST_NSR2_LOG:
            events += entry["events"]
    decel_g = np.array([e["decel_from_speed_g"] for e in events])
    return {
        "median (p50)": float(np.percentile(decel_g, 50)),
        "p95": float(np.percentile(decel_g, 95)),
        "Standard BRAKE_CAP_G (haerteste ABS-Bremsung >= 1,5 s)": BRAKE_CAP_G,
    }, len(decel_g)


def main():
    points = load_best_racing_line()
    brake_caps, n_events = load_real_brake_caps()
    mu = TIRE_MU_RANGE[0]  # konservativ, gleicher Standard wie der interaktive Editor

    print(f"Reale Bremsvorgaenge (n={n_events}, CAN-Bremsdruck + Tempoabfall, NS-R2):")
    for label, g in brake_caps.items():
        print(f"  {label}: {g:.3f}g")

    print(f"\nRundenzeit-Vergleich bei mu={mu} (Kurven weiterhin am theoretischen "
          f"Reifenlimit, nur die Bremsverzoegerung wird begrenzt):\n")

    # brake_cap_g=None erzwingt das alte, rein theoretische Verhalten -
    # BRAKE_CAP_G ist inzwischen der Standard-Default der Funktion (Nutzer-
    # Entscheidung 08.09.2026: real gemessene, ABS-limitierte Bremsung statt
    # Theorie), dieser Vergleich hier zeigt bewusst noch den alten Wert.
    v_baseline, t_baseline, _ = simulate_lap_combined_friction(points, mu, brake_cap_g=None)
    print(f"  Vorher (rein theoretisch, a_brake=sqrt((mu*g)^2-a_lat^2)): {t_baseline:.2f}s")

    results = {"baseline_s": t_baseline, "scenarios": {}}
    v_by_label = {"Theoretisches Reifenlimit": v_baseline}
    for label, g in brake_caps.items():
        v_capped, t_capped, _ = simulate_lap_combined_friction(points, mu, brake_cap_g=g)
        delta = t_capped - t_baseline
        print(f"  Bremscap {label} ({g:.3f}g): {t_capped:.2f}s  (+{delta:.2f}s, "
              f"{100*delta/t_baseline:.1f}%)")
        results["scenarios"][label] = {"brake_cap_g": g, "lap_time_s": t_capped,
                                        "delta_s": delta}
        if label.startswith("Standard BRAKE_CAP_G"):
            v_by_label[f"Real begrenzt ({g:.2f}g)"] = v_capped

    ds = np.hypot(*(np.roll(points, -1, axis=0) - points).T)
    s = np.concatenate([[0], np.cumsum(ds)])[:-1]

    fig, ax = plt.subplots(figsize=(11, 5))
    for label, v in v_by_label.items():
        ax.plot(s, v * 3.6, label=label, linewidth=1.3)
    ax.set_xlabel("Streckenposition s [m]")
    ax.set_ylabel("Geschwindigkeit [km/h]")
    ax.set_title(f"Geschwindigkeitsprofil: theoretisches vs. real gemessenes Bremsvermoegen (mu={mu})")
    ax.legend()
    ax.grid(alpha=0.3)
    out_png = os.path.join(RESULTS_DIR, "spreewaldring_braking_model_comparison.png")
    fig.savefig(out_png, dpi=130, bbox_inches="tight")
    print(f"\nPlot gespeichert: {out_png}")

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_braking_model_comparison.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Zusammenfassung gespeichert: {out_json}")


if __name__ == "__main__":
    main()
