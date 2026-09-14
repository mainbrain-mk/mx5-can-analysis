"""
Spreewaldring - erster Ideallinien-Versuch fuer den Mazda MX-5 ND G184
(30.08.2026)

Nutzerauftrag: "Moechtest du mal einen ersten Anlauf unternehmen fuer das
gruene Polygon eine Ideallinie ... zu berechnen?" Direkte Folgearbeit auf
`spreewaldring_track_surface.py` (Hauptschleifen-Fahrbahn als Flaeche,
linker/rechter Rand pro Streckenpunkt) und `spreewaldring_lap_simulation.py`
(Rundenzeit-Modell, bisher nur auf der geometrischen Mittellinie gefahren -
dort explizit als "systematisch pessimistisch" markiert, weil eine echte
Ideallinie in Kurven einen groesseren Radius nutzt als die Mittellinie).

METHODIK - GEOMETRISCHE KRUEMMUNGS-MINIMIERUNG ("elastic band"), nicht volles
Optimalsteuerungsproblem:
  Die Linie wird als seitlicher Versatz n(s) von der Streckenmittellinie
  dargestellt, begrenzt durch den Fahrbahnkorridor (linker/rechter Rand aus
  `spreewaldring_track_surface_summary.json`, siehe unten) minus einem festen
  Sicherheitsabstand fuer die halbe Fahrzeugbreite. Iterativ wird jeder Punkt
  in Weltkoordinaten ein Stueck in Richtung des Mittelpunkts seiner beiden
  Nachbarn gezogen (klassische Laplace-/"elastic band"-Glaettung, reduziert
  bei jedem Schritt die lokale Kruemmung), danach zurueck auf den Korridor
  geclippt. Nach vielen Iterationen (siehe `N_ITERATIONS`) konvergiert das
  zu einer glatten, kruemmungsarmen Linie, die genau dort an den Rand geht,
  wo es die Kruemmung verringert (klassisches "Kurve aufziehen" -
  Kurveneingang aussen, Scheitel innen, Kurvenausgang aussen), OHNE dass
  Kurvenein-/-ausgaenge explizit programmiert werden muessten - das ergibt
  sich rein aus der Glaettung unter den Korridor-Grenzen. Das ist NICHT das
  volle Minimalzeit-Optimalsteuerungsproblem (das wuerde zusaetzlich
  Geschwindigkeit/Beschleunigung/Bremsen direkt mit optimieren, siehe
  EINSCHRAENKUNGEN), sondern der uebliche, viel einfachere erste Schritt
  ("kuerzeste/kruemmungsaermste Linie im Korridor", verwandt mit dem Ansatz
  in bekannten Rennlinien-Tools wie TUM's trajectory_planning_helpers).

  Fahrzeugbreite MX-5 ND: ANNAHME 1.74m (Herstellerangabe fuer die
  Basiskarosserie, NICHT spezifisch fuer G184 verifiziert), davon die Haelfte
  (0.87m) als Sicherheitsabstand von JEDEM Rand abgezogen (Fahrzeug-
  MITTELPUNKT darf den Korridor [rand+0.87m, rand-0.87m] nutzen).

BEWERTUNG: die resultierende Linie wird auf gleichmaessige Bogenlaenge
resampled (wie die Mittellinie), Kruemmungsradius neu berechnet (gleiche
Methode wie in `spreewaldring_track.py`), und durch das UNVERAENDERTE
Rundenzeit-Modell aus `spreewaldring_lap_simulation.py` gejagt (gleiches
Traktionsmodell, gleiche mu-Bandbreite 1.0-1.3) - direkter Vergleich zur
bisherigen, auf der Mittellinie gefahrenen Rundenzeit (99.5-110.5s) moeglich,
da nur die Streckengeometrie (Radius pro Punkt), nicht das Fahrzeug-/
Reifenmodell, sich aendert.

EINSCHRAENKUNGEN:
  - Kein echtes Minimalzeit-Optimalsteuerungsproblem: Kruemmungsminimierung
    ist ein bekannter, aber NAEHERUNGSWEISER Proxy fuer minimale Rundenzeit -
    an Kurven mit sehr unterschiedlicher Vorher-/Nachher-Geschwindigkeit
    (z.B. Bremszone vor einer engen Kurve nach einer schnellen Geraden)
    waere eine zeitoptimale Linie tendenziell anders (spaeterer Scheitelpunkt
    "spaeter Apex" fuer bessere Kurvenausgangsbeschleunigung) als die reine
    Kruemmungsminimierung hier.
  - Kein kombinierter Reifenkraftkreis, kein Gewichtstransfer - gleiche
    Einschraenkungen wie in `spreewaldring_lap_simulation.py`.
  - Fahrzeugbreite ist eine Annahme (1.74m), nicht fuer die G184-Variante
    verifiziert.
  - Korridorgrenzen stammen aus der automatischen Randerkennung
    (`spreewaldring_track_surface.py`) - dort verbleibende Ungenauigkeiten
    (v.a. Boxengasse, hier nicht relevant, da nur Hauptschleife verwendet)
    pflanzen sich hier fort.
  - Keine Ideallinie fuer die Boxengasse/Verbindungsstuecke - nur die
    Hauptschleife (das gruene Polygon).

Aufruf: .venv/bin/python scripts/spreewaldring_racing_line.py
"""
import os
import json

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from spreewaldring_track import (
    RESULTS_DIR, RESAMPLE_STEP_M, CURVATURE_WINDOW_M,
    resample_closed_loop, compute_curvature_radius, crop_orthophoto, fetch_orthophoto,
)
from spreewaldring_track_surface import compute_tangents_closed
from spreewaldring_lap_simulation import simulate_lap, find_corner_min_speeds, TIRE_MU_RANGE

SURFACE_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_track_surface_summary.json")
LAP_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_lap_simulation_summary.json")

CAR_WIDTH_M = 1.74     # ANNAHME (Mazda MX-5 ND Basiskarosserie), siehe Docstring
CAR_HALF_WIDTH_M = CAR_WIDTH_M / 2

N_ITERATIONS = 3000
RELAXATION_ALPHA = 0.2


def load_corridor():
    with open(SURFACE_SUMMARY_PATH, encoding="utf-8") as f:
        summ = json.load(f)
    main = summ["segments"]["main_loop"]
    center = np.array(main["centerline_utm33"])
    left = np.array(main["left_edge_utm33"])
    right = np.array(main["right_edge_utm33"])
    return center, left, right


def corridor_bounds(center, left, right, tangents):
    """Projiziert linken/rechten Fahrbahnrand auf die lokale Quer-Richtung
    (perp) relativ zur Mittellinie -> (n_lo, n_hi) pro Punkt, Korridor fuer
    den Fahrzeug-MITTELPUNKT (bereits um CAR_HALF_WIDTH_M eingezogen)."""
    perp = np.column_stack([-tangents[:, 1], tangents[:, 0]])
    offset_left = np.sum((left - center) * perp, axis=1)
    offset_right = np.sum((right - center) * perp, axis=1)
    lo = np.minimum(offset_left, offset_right) + CAR_HALF_WIDTH_M
    hi = np.maximum(offset_left, offset_right) - CAR_HALF_WIDTH_M
    # Sicherheitsnetz: falls der Korridor schmaler als das Auto ist
    # (z.B. lokaler Ausreisser der Randerkennung), auf Mittellinie zurueckfallen.
    bad = hi < lo
    lo[bad] = 0.0
    hi[bad] = 0.0
    return lo, hi, perp


def optimize_racing_line(center, lo, hi, perp, n_iterations=N_ITERATIONS, alpha=RELAXATION_ALPHA):
    """Iterative Kruemmungsminimierung ('elastic band') mit Korridor-Clipping,
    siehe Docstring. Liefert n(s) (seitlicher Versatz von der Mittellinie)."""
    n = np.zeros(len(center))
    cost_history = []
    for it in range(n_iterations):
        p = center + n[:, None] * perp
        p_avg = (np.roll(p, 1, axis=0) + np.roll(p, -1, axis=0)) / 2
        p_new = p + alpha * (p_avg - p)
        n_new = np.sum((p_new - center) * perp, axis=1)
        n_new = np.clip(n_new, lo, hi)
        n = n_new
        if it % 200 == 0 or it == n_iterations - 1:
            p_cur = center + n[:, None] * perp
            second_diff = np.roll(p_cur, 1, axis=0) - 2 * p_cur + np.roll(p_cur, -1, axis=0)
            cost = float(np.sum(np.linalg.norm(second_diff, axis=1) ** 2))
            cost_history.append((it, cost))
    return n, cost_history


def plot_racing_line(center, left, right, racing_line, radius, ortho_crop, ortho_extent, out_path):
    fig, ax = plt.subplots(figsize=(11, 11))
    ax.imshow(ortho_crop, extent=ortho_extent, origin="upper")
    ax.plot(left[:, 0], left[:, 1], "-", color="white", lw=0.8, alpha=0.6)
    ax.plot(right[:, 0], right[:, 1], "-", color="white", lw=0.8, alpha=0.6)
    ax.plot(center[:, 0], center[:, 1], "--", color="dimgray", lw=1.0, alpha=0.8, label="Mittellinie")
    r_capped = np.clip(radius, 0, 300)
    sc = ax.scatter(racing_line[:, 0], racing_line[:, 1], c=r_capped, cmap="RdYlGn", s=6, vmin=0, vmax=300,
                     label="Ideallinie (Kruemmungsminimierung)")
    plt.colorbar(sc, ax=ax, label="Kruemmungsradius [m] (auf 300m gedeckelt)")
    ax.set_xlabel("UTM33 Ost [m]")
    ax.set_ylabel("UTM33 Nord [m]")
    ax.set_aspect("equal")
    ax.set_title("Spreewaldring - erster Ideallinien-Versuch (MX-5 ND, Kruemmungsminimierung)")
    ax.legend(fontsize=8, loc="upper right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_speed_comparison(s_center, v_center, s_line, v_line, out_path, label):
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(s_center, v_center * 3.6, label=f"Mittellinie ({label})", alpha=0.8)
    ax.plot(s_line, v_line * 3.6, label=f"Ideallinie ({label})", alpha=0.8)
    ax.set_xlabel("Streckenposition s [m]")
    ax.set_ylabel("Geschwindigkeit [km/h]")
    ax.set_title("Spreewaldring - Geschwindigkeitsprofil: Mittellinie vs. Ideallinie")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close(fig)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("Lade Fahrbahn-Korridor (Hauptschleife) aus spreewaldring_track_surface_summary.json ...")
    center, left, right = load_corridor()
    tangents = compute_tangents_closed(center)
    lo, hi, perp = corridor_bounds(center, left, right, tangents)
    print(f"Korridorbreite (fuer Fahrzeugmittelpunkt, nach Abzug von {CAR_WIDTH_M}m Fahrzeugbreite): "
          f"Median={np.median(hi - lo):.2f}m Min={np.min(hi - lo):.2f}m Max={np.max(hi - lo):.2f}m")

    print(f"\nOptimiere Ideallinie (Kruemmungsminimierung, {N_ITERATIONS} Iterationen, "
          f"alpha={RELAXATION_ALPHA}) ...")
    n, cost_history = optimize_racing_line(center, lo, hi, perp)
    print("Konvergenz (Iteration, Kruemmungskosten):")
    for it, cost in cost_history:
        print(f"  {it:5d}  {cost:.1f}")

    racing_line_raw = center + n[:, None] * perp

    # Auf gleichmaessige Bogenlaenge resampled (wie die Mittellinie) - fuer
    # konsistente Kruemmungsberechnung und Rundenzeit-Simulation.
    racing_line, _, total_len_line, _ = resample_closed_loop(racing_line_raw, RESAMPLE_STEP_M)
    s_line = np.arange(len(racing_line)) * RESAMPLE_STEP_M
    radius_line = compute_curvature_radius(racing_line, CURVATURE_WINDOW_M, RESAMPLE_STEP_M)
    print(f"\nIdeallinien-Laenge: {total_len_line:.1f} m (Mittellinie: "
          f"{RESAMPLE_STEP_M * len(center):.1f} m)")

    with open(LAP_SUMMARY_PATH, encoding="utf-8") as f:
        lap_summary = json.load(f)

    print("\n=== Rundenzeit-Vergleich Mittellinie vs. Ideallinie ===")
    results = {}
    for label, mu in [("konservativ (mu=1.0)", TIRE_MU_RANGE[0]),
                       ("optimistisch (mu=1.3)", TIRE_MU_RANGE[1])]:
        v_line, lap_time_line = simulate_lap(s_line, radius_line, mu)
        scenario_key = label
        lap_time_center = lap_summary["scenarios"][scenario_key]["lap_time_s"]
        gain_s = lap_time_center - lap_time_line
        gain_pct = 100 * gain_s / lap_time_center
        print(f"\n--- Szenario: {label} ---")
        print(f"Rundenzeit Mittellinie:  {lap_time_center:.2f} s")
        print(f"Rundenzeit Ideallinie:   {lap_time_line:.2f} s")
        print(f"Verbesserung:            {gain_s:.2f} s ({gain_pct:.1f}%)")

        corners_line = find_corner_min_speeds(s_line, radius_line, v_line)
        results[label] = {
            "mu": mu, "lap_time_center_s": lap_time_center, "lap_time_racing_line_s": lap_time_line,
            "gain_s": gain_s, "gain_pct": gain_pct,
            "v_max_kmh": float(v_line.max() * 3.6),
            "v_mean_kmh": float(total_len_line / lap_time_line * 3.6),
            "corners": corners_line, "v_kmh": (v_line * 3.6).tolist(),
        }

    # Plots
    image, geo = fetch_orthophoto()
    e_min, n_min = racing_line.min(axis=0)
    e_max, n_max = racing_line.max(axis=0)
    ortho_crop, ortho_extent = crop_orthophoto(image, geo, (e_min, n_min, e_max, n_max))
    plot_racing_line(center, left, right, racing_line, radius_line, ortho_crop, ortho_extent,
                      os.path.join(RESULTS_DIR, "spreewaldring_racing_line.png"))
    print(f"\nIdeallinien-Plot: {RESULTS_DIR}/spreewaldring_racing_line.png")

    s_center, radius_center, _, _ = (
        np.arange(len(center)) * RESAMPLE_STEP_M,
        compute_curvature_radius(center, CURVATURE_WINDOW_M, RESAMPLE_STEP_M),
        None, None,
    )
    v_center_opt, _ = simulate_lap(s_center, radius_center, TIRE_MU_RANGE[1])
    v_line_opt, _ = simulate_lap(s_line, radius_line, TIRE_MU_RANGE[1])
    plot_speed_comparison(s_center, v_center_opt, s_line, v_line_opt,
                          os.path.join(RESULTS_DIR, "spreewaldring_racing_line_speed_trace.png"),
                          "mu=1.3")
    print(f"Geschwindigkeitsvergleich: {RESULTS_DIR}/spreewaldring_racing_line_speed_trace.png")

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "car_width_m_assumed": CAR_WIDTH_M,
            "n_iterations": N_ITERATIONS,
            "relaxation_alpha": RELAXATION_ALPHA,
            "racing_line_length_m": total_len_line,
            "centerline_length_m": RESAMPLE_STEP_M * len(center),
            "racing_line_utm33": racing_line.tolist(),
            "lateral_offset_m": n.tolist(),
            "corridor_lo_m": lo.tolist(),
            "corridor_hi_m": hi.tolist(),
            "scenarios": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
