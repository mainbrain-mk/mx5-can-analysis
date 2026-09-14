"""
Rundenzeit-Simulation auf der rekonstruierten Spreewaldring-Strecke (MX-5
Projekt)

Nutzerauftrag: "Jetzt wuerde ich tatsaechlich versuchen, ob wir eine
schnelle Runde auf der berechneten Strecke simulieren koennen." Erste
Zusammenfuehrung der bisherigen Bausteine (Streckengeometrie aus
`spreewaldring_track.py`, Volllast-/Traktionsmodell aus
`performance_simulation.py`, Reifen-Grip-Bandbreite aus Memory "mx5-tires")
zu einer Rundenzeit-Schaetzung fuer eine "schnelle Runde" (Hotlap, ans
Limit gefahren).

METHODIK: klassisches quasi-stationaeres Punktmassen-Rundenzeitmodell
(zwei Durchlaeufe entlang der Strecke, Standardansatz im Motorsport-
Engineering, KEIN neues physikalisches Modell - kombiniert nur bereits
vorhandene/validierte Bausteine):

  1. **Kurvengeschwindigkeits-Grenze** pro Streckenpunkt:
     v_kurve(s) = sqrt(mu_lat * g * R(s)), mit R(s) aus der
     orthofoto-verfeinerten, geglaetteten Streckenmittellinie
     (`spreewaldring_track.py`) und mu_lat aus der Reifen-Literatur-
     Bandbreite (Nankang NS-R2 Semi-Slick, siehe Memory "mx5-tires",
     TIRE_MU_RANGE).
  2. **Beschleunigungs-Einhuellende** a_accel(v) = das Maximum ueber alle
     6 Gaenge von `accel(v, gear, f_max=TRACTION_MAX_FORCE_N)` aus
     `performance_simulation.py` - genau das Modell, das dort als
     "traktionsbegrenzt" empfohlen wurde (trifft sowohl Startbeschleunigung
     als auch Vmax-Bereich am besten, siehe dortiger Docstring). Ignoriert
     Schaltzeit-Verluste (ATTACK_SHIFT_S) - Vereinfachung, siehe
     EINSCHRAENKUNGEN.
  3. **Brems-Verzoegerung** a_brems = mu_lat * g (KONSTANT, gleiche
     Reifen-Grip-Annahme wie bei Kurven - siehe EINSCHRAENKUNGEN: die
     echten geloggten Bremsungen aus `braking_model.py` erreichen nur bis
     0.48g, deutlich weniger als hier angenommen, ABER das war
     Alltagsfahrten auf oeffentlichen Strassen, keine Hotlap-Bremsungen ans
     Limit - fuer eine Rennstrecken-Simulation wird die volle Reifen-
     Grip-Bandbreite angenommen, NICHT die geloggten Strassen-Werte.
  4. **Zwei-Pass-Algorithmus** (Start am langsamsten Punkt der Strecke,
     das vermeidet die meisten Iterationen):
       a. Vorwaerts-Pass: v_vor(s) = min(v_kurve(s),
          sqrt(v_vor(s-1)^2 + 2*a_accel(v_vor(s-1))*ds)) - wie schnell
          koennte das Auto werden, wenn es ab hier voll beschleunigt.
       b. Rueckwaerts-Pass: v(s) = min(v_vor(s),
          sqrt(v(s+1)^2 + 2*a_brems*ds)) - wo muss spaetestens gebremst
          werden, um die naechste Kurve zu schaffen.
       c. 2 Wiederholungen fuer Konvergenz (geschlossene Schleife).
  5. Rundenzeit = Summe(ds / v(s)) ueber die ganze Runde.

Zwei Szenarien (wie bei performance_simulation.py): "optimistisch"
(mu=1.3) und "konservativ" (mu=1.0) - Bandbreite statt Einzelwert, da mu
nicht fuer DIESES Fahrzeug/DIESE Strecke gemessen ist.

EINSCHRAENKUNGEN (wichtig, nicht kleingedruckt):
  - **Ideallinie:** die Simulation faehrt die geometrische Streckenmittel-
    linie, NICHT eine optimierte Ideallinie (die haette in Kurven einen
    groesseren effektiven Radius, oft 30-50% mehr als die Mittellinie) -
    die Rundenzeit ist dadurch SYSTEMATISCH PESSIMISTISCH (zu langsam),
    vermutlich der groesste einzelne Fehlerfaktor in dieser Simulation.
  - **Kein Reifenkraftkreis:** Laengs- (Brems-/Beschleunigungs-) und
    Quergrenzen werden UNABHAENGIG behandelt, nicht als kombinierter
    Kraftkreis (in der Realitaet reduziert Bremsen in der Kurve die
    verfuegbare Querkraft und umgekehrt) - macht Kurvenein-/ausgaenge
    optimistischer als real moeglich.
  - **Kein Gewichtstransfer, keine Aero:** MX-5 hat keine nennenswerte
    Aero-Abtrieb, aber Gewichtstransfer (mehr Grip vorne beim Bremsen,
    hinten beim Beschleunigen) wird nicht modelliert.
  - **Schaltzeiten ignoriert:** die Beschleunigungs-Einhuellende nimmt
    perfektes, verzoegerungsfreies Schalten an (ATTACK_SHIFT_S aus
    performance_simulation.py hier NICHT angewendet).
  - **mu-Bandbreite ist Literaturschaetzung** fuer die Reifenklasse
    (Nankang NS-R2), nicht an diesem Auto/dieser Strecke gemessen - siehe
    Memory "mx5-tires" fuer Einschraenkungen (Reifenalter/Fahrbahntemperatur
    nicht beruecksichtigt).
  - **Boxengasse nicht beruecksichtigt** - reine Rundenzeit auf der
    Ideallinie (way 172927073), kein Boxenstopp.
  - Fahrerfehler/Konsistenz nicht modelliert (=theoretisches Maximum bei
    perfekter Ausfuehrung, kein realistischer Fahrer-Mittelwert).

Aufruf: .venv/bin/python scripts/spreewaldring_lap_simulation.py
"""
import os
import json

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from drivetrain_model_validation import MASS_KG, G, GEAR_RATIOS
from performance_simulation import accel, TRACTION_MAX_FORCE_N

RESULTS_DIR = "results"
TRACK_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_track_summary.json")

TIRE_MU_RANGE = (1.0, 1.3)  # siehe Memory "mx5-tires" (Nankang NS-R2 Semi-Slick)
N_PASSES = 2                # Wiederholungen des Vorwaerts-/Rueckwaerts-Paars fuer Konvergenz
V_MIN_MS = 5.0               # numerische Untergrenze (vermeidet Division/sqrt-Problem bei v->0)


def load_track():
    with open(TRACK_SUMMARY_PATH, encoding="utf-8") as f:
        d = json.load(f)
    r = d["orthophoto_refinement"]
    s = np.array(r["s_m_refined"])
    radius = np.array([1e6 if x is None else x for x in r["radius_m_refined"]])
    points = np.array(r["track_points_utm33_refined"])
    total_len = r["total_length_m_refined"]
    return s, radius, points, total_len


def accel_envelope(v_ms):
    """Beste erreichbare Beschleunigung ueber alle 6 Gaenge (traktions-
    begrenztes Modell, siehe Docstring)."""
    return max(accel(v_ms, gear, f_max=TRACTION_MAX_FORCE_N)[0] for gear in GEAR_RATIOS)


def simulate_lap(s, radius, mu_lat, n_passes=N_PASSES):
    n = len(s)
    ds = np.full(n, np.median(np.diff(s)))  # gleichmaessiges Netz (RESAMPLE_STEP_M), siehe spreewaldring_track.py

    v_corner = np.sqrt(np.maximum(mu_lat * G * radius, V_MIN_MS ** 2))

    # am langsamsten Punkt starten (minimiert noetige Iterationen, siehe Docstring)
    i0 = int(np.argmin(v_corner))
    order = np.roll(np.arange(n), -i0)
    v_corner_o = v_corner[order]
    ds_o = ds[order]

    v = v_corner_o.copy()
    for _ in range(n_passes):
        # Vorwaerts (Beschleunigung)
        v_fwd = v.copy()
        for i in range(1, n):
            v_max_accel = np.sqrt(v_fwd[i - 1] ** 2 + 2 * accel_envelope(v_fwd[i - 1]) * ds_o[i - 1])
            v_fwd[i] = min(v_corner_o[i], v_max_accel)
        # Schlusspunkt -> Startpunkt schliessen (Rundkurs)
        v_max_accel = np.sqrt(v_fwd[-1] ** 2 + 2 * accel_envelope(v_fwd[-1]) * ds_o[-1])
        v_fwd[0] = min(v_corner_o[0], v_max_accel, v_fwd[0])

        # Rueckwaerts (Bremsen)
        v_bwd = v_fwd.copy()
        a_brake = mu_lat * G
        for i in range(n - 2, -1, -1):
            v_max_brake = np.sqrt(v_bwd[i + 1] ** 2 + 2 * a_brake * ds_o[i])
            v_bwd[i] = min(v_bwd[i], v_max_brake)
        v_max_brake = np.sqrt(v_bwd[0] ** 2 + 2 * a_brake * ds_o[-1])
        v_bwd[-1] = min(v_bwd[-1], v_max_brake)

        v = v_bwd

    # zurueck in urspruengliche Reihenfolge
    v_final = np.empty(n)
    v_final[order] = v
    lap_time_s = float(np.sum(ds / v_final))
    return v_final, lap_time_s


def find_corner_min_speeds(s, radius, v, corner_radius_max=100.0, merge_gap_m=30.0, step_m=3.0):
    mask = radius < corner_radius_max
    runs = []
    in_run = False
    for i, m in enumerate(mask):
        if m and not in_run:
            in_run, start = True, i
        elif not m and in_run:
            in_run = False
            runs.append((start, i - 1))
    if in_run:
        runs.append((start, len(mask) - 1))
    gap_steps = merge_gap_m / step_m
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= gap_steps:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    out = []
    for a, b in merged:
        i_min = a + int(np.argmin(v[a:b + 1]))
        out.append({"s_apex_m": float(s[i_min]), "radius_m": float(radius[i_min]),
                     "v_min_kmh": float(v[i_min] * 3.6)})
    return out


def plot_speed_map(points, v, out_path):
    fig, ax = plt.subplots(figsize=(9, 9))
    sc = ax.scatter(points[:, 0], points[:, 1], c=v * 3.6, cmap="turbo", s=8)
    plt.colorbar(sc, ax=ax, label="Geschwindigkeit [km/h]")
    ax.set_xlabel("UTM33 Ost [m]")
    ax.set_ylabel("UTM33 Nord [m]")
    ax.set_aspect("equal")
    ax.set_title("Spreewaldring - simuliertes Geschwindigkeitsprofil (Hotlap)")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_speed_trace(s, v_by_scenario, out_path):
    fig, ax = plt.subplots(figsize=(11, 5))
    for label, v in v_by_scenario.items():
        ax.plot(s, v * 3.6, label=label)
    ax.set_xlabel("Streckenposition s [m]")
    ax.set_ylabel("Geschwindigkeit [km/h]")
    ax.set_title("Spreewaldring - Geschwindigkeitsprofil ueber die Runde")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close(fig)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    s, radius, points, total_len = load_track()
    print(f"Strecke: {total_len:.1f} m, {len(s)} Punkte (orthofoto-verfeinert, geglaettet)")
    print(f"Reifen-mu-Bandbreite: {TIRE_MU_RANGE} (siehe Memory 'mx5-tires', Nankang NS-R2)")

    results = {}
    for label, mu in [("konservativ (mu=1.0)", TIRE_MU_RANGE[0]),
                       ("optimistisch (mu=1.3)", TIRE_MU_RANGE[1])]:
        v, lap_time = simulate_lap(s, radius, mu)
        v_max = v.max() * 3.6
        v_mean = total_len / lap_time * 3.6
        print(f"\n--- Szenario: {label} ---")
        print(f"Rundenzeit: {lap_time:.2f} s")
        print(f"Topspeed: {v_max:.1f} km/h | Schnittgeschwindigkeit: {v_mean:.1f} km/h")

        corners = find_corner_min_speeds(s, radius, v)
        print(f"{'#':>3s} {'s_apex':>8s} {'R':>6s} {'v_min':>8s}")
        for i, c in enumerate(corners):
            print(f"{i+1:3d} {c['s_apex_m']:7.0f}m {c['radius_m']:5.1f}m {c['v_min_kmh']:7.1f} km/h")

        results[label] = {"mu": mu, "lap_time_s": lap_time, "v_max_kmh": v_max,
                           "v_mean_kmh": v_mean, "corners": corners, "v_kmh": (v * 3.6).tolist()}

    v_by_scenario = {label: np.array(r["v_kmh"]) / 3.6 for label, r in results.items()}
    plot_speed_trace(s, v_by_scenario, os.path.join(RESULTS_DIR, "spreewaldring_lap_speed_trace.png"))
    print(f"\nGeschwindigkeitsprofil: {RESULTS_DIR}/spreewaldring_lap_speed_trace.png")

    v_optimistic, _ = simulate_lap(s, radius, TIRE_MU_RANGE[1])
    plot_speed_map(points, v_optimistic, os.path.join(RESULTS_DIR, "spreewaldring_lap_speed_map.png"))
    print(f"Geschwindigkeits-Streckenkarte: {RESULTS_DIR}/spreewaldring_lap_speed_map.png")

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_lap_simulation_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"track_length_m": total_len, "scenarios": results}, f, indent=2, ensure_ascii=False)
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
