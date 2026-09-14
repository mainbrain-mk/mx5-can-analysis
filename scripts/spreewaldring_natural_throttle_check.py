"""
Plausibilitaetspruefung: Lenkrate->Gas-Modell (aus partial_throttle_calibration.py,
EINE reale Kurve, R^2=0.76, n=11) auf die 11 Spreewaldring-Kurven angewendet -
BEVOR es zur primaeren Gas-Vorgabe der Streckensimulation wird
(Nutzerentscheidung 08.09.2026: "auf natuerliches Fahrverhalten umstellen",
aber "erst Plausibilitaet pruefen").

Methodik:
1. Aktuelle Bestlinie (spreewaldring_racing_line_corners_summary.json) laden,
   Kruemmung/Radius wie im Hauptmodell berechnen (CURVATURE_WINDOW_M).
2. Aus dem Radius per Lenkwinkel-Modell (k1/k2, invertiert) einen aequivalenten
   Lenkwinkel ableiten - siehe steer_from_radius() fuer die Herleitung.
3. Lenkrate = d|Lenkwinkel|/ds * v(s) (v aus der bestehenden Zweipass-
   Simulation), mit Vorzeichenkonvention "positiv = Kurve oeffnet sich"
   (unabhaengig von Links/Rechts - siehe Docstring dort).
4. APP%(s) = clip(38.4 + 2.4*Lenkrate, 0, 100) - die Kurve-1-Formel.
5. Pro Kurve: Kennzahlen (min/mean/max APP%, max betragliche Lenkrate) UND
   ein Extrapolations-Warnhinweis, wenn die dortige Lenkrate deutlich ausserhalb
   des kalibrierten Bereichs liegt (Kurve 1: -14 bis +24 deg/s).

Aufruf: .venv/bin/python scripts/spreewaldring_natural_throttle_check.py
"""
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from spreewaldring_track import RESULTS_DIR, RESAMPLE_STEP_M, CURVATURE_WINDOW_M, compute_curvature_radius
from spreewaldring_racing_line_optimal import simulate_lap_combined_friction

CORNERS_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_corners_summary.json")

K1, K2 = 0.023382, -0.00003895   # results/steering_lateral_model_summary.json
APP_A, APP_B = 38.4, 2.4          # Kurve-1-Regression, siehe partial_throttle_calibration.py
CALIBRATED_RATE_RANGE_DEG_S = (-13.8, 24.3)  # beobachteter Bereich in Kurve 1
MU = 1.0


def steer_from_radius(radius_m):
    """Loest (180/(pi*R)) = (k1+k2*|steer|)*|steer| nach |steer| [deg] auf -
    Umkehrung des Lenkwinkel->Giergeschwindigkeit-Modells (siehe
    steering_lateral_model.py), v-unabhaengig (siehe Docstring dort:
    v kuerzt sich beim Gleichsetzen mit der kinematischen Beziehung
    yaw_rate=v/R heraus). Quadratische Gleichung K2*s^2+K1*s-target=0."""
    target = np.degrees(1.0 / np.maximum(radius_m, 1e-6))
    # k2*s^2 + k1*s - target = 0, positive Loesung
    disc = K1**2 - 4 * K2 * (-target)
    s = (-K1 + np.sqrt(np.maximum(disc, 0))) / (2 * K2)
    return np.abs(s)


def main():
    with open(CORNERS_SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)
    best = min(summary["snapshots"], key=lambda s: s["lap_time_s"])
    points = np.array(best["points_utm33"])
    n = len(points)

    ds = np.hypot(*(np.roll(points, -1, axis=0) - points).T)
    s_arr = np.concatenate([[0], np.cumsum(ds)])[:-1]
    radius = compute_curvature_radius(points, CURVATURE_WINDOW_M, RESAMPLE_STEP_M)

    v, lap_time, _ = simulate_lap_combined_friction(points, MU)
    print(f"Rundenzeit Referenzlinie (mu={MU}): {lap_time:.2f}s")

    steer_abs = steer_from_radius(radius)
    # d|steer|/ds per zentrale Differenz (zyklisch), dann *v = d|steer|/dt.
    ds_fwd = np.roll(s_arr, -1) - s_arr
    ds_fwd[ds_fwd < 0] += s_arr[-1] + ds[-1]
    ds_bwd = s_arr - np.roll(s_arr, 1)
    ds_bwd[ds_bwd < 0] += s_arr[-1] + ds[-1]
    d_steer_ds = (np.roll(steer_abs, -1) - np.roll(steer_abs, 1)) / (ds_fwd + ds_bwd)

    opening_rate_deg_s = -d_steer_ds * v  # positiv = Kurve oeffnet sich (Lenkwinkel-Betrag sinkt)
    app_pct = np.clip(APP_A + APP_B * opening_rate_deg_s, 0, 100)

    # Kurvenerkennung: Radius unter Schwelle = "in einer Kurve" (grobe
    # Gruppierung fuer die Pro-Kurve-Kennzahlen, analog corner_event_analysis.py)
    in_corner = radius < 150.0
    groups = []
    i = 0
    while i < n:
        if in_corner[i]:
            j = i
            while j < n and in_corner[j]:
                j += 1
            groups.append((i, j))
            i = j
        else:
            i += 1

    print(f"\n{len(groups)} Kurven erkannt (Radius<150m):\n")
    out_of_range_count = 0
    for gi, (i0, i1) in enumerate(groups):
        seg = slice(i0, i1)
        rmin = radius[seg].min()
        rate_max = np.max(np.abs(opening_rate_deg_s[seg]))
        app_mean = app_pct[seg].mean()
        app_min = app_pct[seg].min()
        oor = rate_max > max(abs(x) for x in CALIBRATED_RATE_RANGE_DEG_S) * 1.5
        if oor:
            out_of_range_count += 1
        flag = "  <-- Lenkrate weit ausserhalb des kalibrierten Bereichs!" if oor else ""
        print(f"Kurve {gi+1}: s={s_arr[i0]:.0f}-{s_arr[(i1-1)%n]:.0f}m, R_min={rmin:.1f}m, "
              f"max|Lenkrate|={rate_max:.1f}deg/s, APP% min/mean={app_min:.0f}/{app_mean:.0f}{flag}")

    print(f"\n{out_of_range_count} von {len(groups)} Kurven haben Lenkraten deutlich ausserhalb "
          f"des kalibrierten Bereichs ({CALIBRATED_RATE_RANGE_DEG_S[0]:.0f} bis {CALIBRATED_RATE_RANGE_DEG_S[1]:.0f} deg/s aus Kurve 1).")

    frac_full = float(np.mean(app_pct[in_corner] > 95))
    frac_low = float(np.mean(app_pct[in_corner] < 20))
    print(f"\nInnerhalb aller Kurvenbereiche: {frac_full*100:.0f}% der Punkte mit APP%>95, "
          f"{frac_low*100:.0f}% mit APP%<20.")

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(s_arr, radius.clip(0, 300))
    axes[0].set_ylabel("Radius [m] (auf 300 gekappt)")
    axes[0].grid(alpha=0.3)
    axes[1].plot(s_arr, opening_rate_deg_s)
    axes[1].axhline(0, color="grey", lw=0.6)
    axes[1].axhline(CALIBRATED_RATE_RANGE_DEG_S[1], color="red", ls="--", lw=0.8, label="kalibrierter Bereich (Kurve 1)")
    axes[1].axhline(CALIBRATED_RATE_RANGE_DEG_S[0], color="red", ls="--", lw=0.8)
    axes[1].set_ylabel("Oeffnungsrate [deg/s]")
    axes[1].legend()
    axes[1].grid(alpha=0.3)
    axes[2].plot(s_arr, app_pct)
    axes[2].set_ylabel("vorhergesagtes APP %")
    axes[2].set_xlabel("Streckenposition s [m]")
    axes[2].grid(alpha=0.3)
    fig.suptitle("Plausibilitaetspruefung: Lenkrate->Gas-Modell auf Spreewaldring-Bestlinie")
    fig.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "spreewaldring_natural_throttle_check.png")
    fig.savefig(out_png, dpi=130)
    print(f"\nPlot gespeichert: {out_png}")


if __name__ == "__main__":
    main()
