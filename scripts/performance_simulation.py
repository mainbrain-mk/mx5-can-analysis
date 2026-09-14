"""
0-Vmax Fahrleistungssimulation (MX-5 Projekt)

Baut direkt auf dem in drivetrain_model_validation.py validierten
Beschleunigungsmodell auf (Volllast-Drehmomentkurve, Getriebe-/
Achsuebersetzung, CdA, Crr, eta, Masse - alle Werte und deren
Validierungsstatus siehe MX5_Aktueller_Kenntnisstand_2026-08-29.md).
Neu ist hier NUR die Zeitintegration ueber eine komplette Fahrt inkl.
Schaltlogik - keine neuen physikalischen Annahmen gegenueber dem
bereits validierten Modell.

Schaltlogik: "optimaler Schaltpunkt" - es wird in den naechsten Gang
gewechselt, sobald dessen Beschleunigung bei aktueller Geschwindigkeit
die des aktuellen Gangs einholt (klassische Maximalbeschleunigungs-
Schaltstrategie), mit Sicherheitsnetz REDLINE_RPM (Tabellenoberkante
7500 U/min - laut Dokument oberhalb 7000 U/min "schwach abgesichert").
Waehrend des Schaltvorgangs (ATTACK-Zugkraftunterbrechung, gangspezifisch
aus dem Dokument) rollt das Fahrzeug ohne Antriebskraft (nur Luft-/
Rollwiderstand).

Drei Szenarien werden gerechnet:
  - "raw": reines Modell (Bias-Faktor 1.0)
  - "bias-korrigiert": Antriebskraft mit EMPIRICAL_BIAS_FACTOR skaliert,
    dem in drivetrain_model_validation.py ueber 44 echte Volllast-
    Segmente gemessenen Median-Verhaeltnis gemessen/Modell (~0.92) -
    siehe dortigen Docstring/PROJEKT_STAND.md fuer Herkunft.
  - "traktionsbegrenzt": NEU (30.08.2026) - Antriebskraft an der Radaufstands-
    kraft gedeckelt (F_wheel_effektiv = min(F_wheel_modell, TRACTION_MAX_FORCE_N)),
    statt eines pauschalen Faktors. Herkunft/Begruendung siehe
    TRACTION_MAX_FORCE_N unten - trifft die ueber 127 echte Volllast-
    Segmente (44 aus drivetrain_model_validation.py + 83 aus
    top_speed_validation.py, Gaenge 2-6, v=18-65 m/s) gemessenen
    Beschleunigungen besser (RMSE 0.158 m/s²) als sowohl raw (0.195) als
    auch der pauschale Bias-Faktor (0.169) - UND ist physikalisch
    plausibler: pro Gang aufgeschluesselt sinkt das Verhaeltnis
    F_erforderlich/F_modell klar mit steigendem Gang (Gang 2: 0.84, Gang 3:
    0.93, Gang 4: 0.93, Gang 5: 0.96, Gang 6: 0.97) - genau das Muster einer
    Kraft-OBERGRENZE (bei kleinen Gaengen/hoher Drehmoment-Multiplikation
    wird die Grenze erreicht, bei Gang 6/hoher Geschwindigkeit liegt die
    Modellkraft ohnehin meist darunter, daher kaum Korrektur noetig - deckt
    sich mit dem RAW-Modell, das die Vmax-Segmente gut trifft, siehe
    PROJEKT_STAND.md). EINSCHRAENKUNG: der gefittete Wert (implizites
    mu~0.38) ist niedriger als ein reiner Reifenhaftungs-Koeffizient
    (typisch 0.8-1.1+) - vermutlich eine Mischung aus echtem Traktionslimit,
    ECU-Drehmomentmanagement/Anfahrschlupfregelung und ggf. weiteren nicht
    einzeln modellierten Verlusten, KEIN sauber isolierter Reifen-mu.
    Bezieht sich NICHT auf den separaten historischen Launch-Wert
    (mu_eff=0.88 im externen Dokument) - eigene, unabhaengige Schaetzung.

BEKANNTE EINSCHRAENKUNG: kein dediziertes Traktions-/Launch-Modell mit
Lastwechsel-/Reifenmechanik (Reifenhaftung beim Start, Lastwechsel unter
Beschleunigung etc.) - der Kraft-Deckel oben ist eine EMPIRISCHE
Vereinfachung (konstante Kraftobergrenze, keine geschwindigkeits- oder
lastabhaengige Reifenkennlinie). Das externe Dokument nennt einen
historischen Launch-Wert (mu_eff=0.88, Traktionsfaktor Gang 1 = 0.90,
mittlere Launch-Beschleunigung 5.54 m/s²) explizit als "historische
Evidenz, nicht aus der aktuellen IMU-Methode neu bestaetigt" - beide
Werte unabhaengig, nicht verrechnet.

TEILLAST-ERWEITERUNG (NEU, 30.08.2026): `accel()` akzeptiert jetzt einen
optionalen `etc_deg`-Parameter. Ohne ihn (Default) unveraendertes Verhalten
(Volllast-Drehmomentkurve, wie oben). Mit `etc_deg` wird stattdessen das in
`partial_load_model.py` gefittete (ETC,RPM)->Prozent-Drehmoment-Kennfeld
verwendet (siehe dortiger Docstring fuer Methodik/Validierung: Kreuz-
validierungs-RMSE 7.2 Prozentpunkte, physikalisch ueber 2553 echte Teillast-
Segmente validiert, RMSE 0.41 m/s² gegenueber 1.70 m/s² bei der bisherigen
impliziten Volllast-Annahme). Neue Funktion `simulate_constant_throttle()`
simuliert damit einen Beschleunigungslauf bei KONSTANTEM Teillast-ETC (statt
Vollgas) mit derselben Schaltlogik wie `simulate()` - Endzustand ist eine
Gleichgewichts-/Cruise-Geschwindigkeit in Gang 6 (`find_equilibrium_speed()`,
Kraftgleichgewicht wie `find_vmax()`, nur bei festem `etc_deg` statt Volllast)
statt echtem Vmax. Kein zusaetzlicher Bias-/Traktions-Korrekturfaktor
angewendet (die vorhandenen Faktoren wurden spezifisch fuer die Volllast-
Kennlinie hergeleitet, nicht fuer Teillast - das Kennfeld selbst ist bereits
direkt an echten Beschleunigungsdaten validiert). EINSCHRAENKUNG: konstantes
ETC ueber die ganze Simulation ist eine Vereinfachung (reale Fahrer variieren
den Pedalweg kontinuierlich) - fuer eine echte Rundenzeit-/GPS-Track-
Simulation waere ein zeitlich variabler ETC-Verlauf noetig, hier bewusst
NICHT umgesetzt (Naechster-Schritt-Kandidat).

Aufruf: .venv/bin/python scripts/performance_simulation.py
"""
import os
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import brentq

from drivetrain_model_validation import (
    MASS_KG, R_DYN_M, FINAL_DRIVE, GEAR_RATIOS, ETA, CDA_M2, RHO_KG_M3,
    CRR, G, RPM_TABLE, torque_nm, rpm_from_speed,
)
from partial_load_model import build_kennfeld_predictor

RESULTS_DIR = "results"

# Gangindividuelle ATTACK-Zugkraftunterbrechung beim Hochschalten [s]
# (MX5_Aktueller_Kenntnisstand_2026-08-29.md, Abschnitt 4, Default fuer
# maximale Fahrleistung)
ATTACK_SHIFT_S = {
    (1, 2): 0.15,
    (2, 3): 0.15,
    (3, 4): 0.17,
    (4, 5): 0.23,
    (5, 6): 0.25,
}

REDLINE_RPM = 7500.0  # Tabellenoberkante Volllastkurve, siehe Docstring
DT_S = 0.002
MAX_SIM_TIME_S = 90.0
VMAX_STEADY_STATE_ACCEL = 0.02  # m/s^2 - darunter gilt Vmax als erreicht

# Empirischer Bias aus drivetrain_model_validation.py (44 Volllast-
# Segmente ueber 54 Logs, Stand 29.08.2026): Modell ueberschaetzt die
# reale Beschleunigung im Median um Faktor 0.92 (gemessen/Modell).
# Interpretation dort: CdA und/oder eta vermutlich leicht zu guenstig
# angesetzt (beide im Quelldokument als "gekoppelt, nicht unabhaengig
# identifiziert" markiert) - hier als pauschale Korrektur auf die
# Antriebskraft angewendet, kein neu hergeleiteter Wert.
EMPIRICAL_BIAS_FACTOR = 0.92

# Traktions-/Kraftobergrenze, empirisch gefittet (30.08.2026) ueber 127 echte
# Volllast-Segmente (Gaenge 2-6): min. RMSE bei F_max=4450N (Suche in 50N-
# Schritten 3000-8000N). Siehe Docstring oben fuer Herleitung/Einordnung.
TRACTION_MAX_FORCE_N = 4450.0


_partial_load_predict = None


def get_partial_load_predict():
    """Lazy-gefittetes (ETC,RPM)->Prozent-Drehmoment-Kennfeld aus
    partial_load_model.py (einmal pro Prozess gefittet, danach gecacht -
    Fit selbst braucht ~1-2s fuer DB-Zugriff + Interpolator-Aufbau)."""
    global _partial_load_predict
    if _partial_load_predict is None:
        _partial_load_predict, _ = build_kennfeld_predictor()
    return _partial_load_predict


def accel(v_ms, gear, bias=1.0, mass_kg=MASS_KG, f_max=None, etc_deg=None):
    """Wie model_accel() in drivetrain_model_validation.py, nur mit
    zusaetzlichem Bias-Faktor auf die Antriebskraft (siehe
    EMPIRICAL_BIAS_FACTOR), optional abweichender Masse (Default:
    Projekt-Referenzmasse MASS_KG - fuer Fahrten mit bekannt abweichender
    Beladung, z.B. Suedtirol-Rueckfahrt 31.07.2026, expliziten mass_kg
    uebergeben, siehe PROJEKT_STAND.md/Memory "Fahrzeuggewicht"), und
    optionaler Kraftobergrenze f_max (siehe TRACTION_MAX_FORCE_N) - wird
    NACH dem Bias-Faktor angewendet (fuer den "traktionsbegrenzt"-Modus
    bias=1.0 UND f_max=TRACTION_MAX_FORCE_N verwenden, nicht beides
    gleichzeitig mit bias!=1.0 - waeren zwei ueberlappende Korrekturen).

    NEU: optionaler `etc_deg` (Drosselklappenwinkel) - ohne ihn (Default
    None) unveraendertes Volllast-Verhalten wie bisher. Mit `etc_deg` wird
    das Teillast-Kennfeld aus partial_load_model.py verwendet statt der
    vollen Volllast-Drehmomentkurve (siehe Docstring oben, Abschnitt
    "TEILLAST-ERWEITERUNG")."""
    rpm = rpm_from_speed(v_ms, gear)
    if etc_deg is None:
        torque = torque_nm(rpm)
    else:
        percent = float(get_partial_load_predict()(etc_deg, rpm)[0])
        torque = percent / 100.0 * torque_nm(rpm)
    f_wheel = torque * GEAR_RATIOS[gear] * FINAL_DRIVE * ETA / R_DYN_M * bias
    if f_max is not None:
        f_wheel = min(f_wheel, f_max)
    f_drag = 0.5 * RHO_KG_M3 * CDA_M2 * v_ms ** 2
    f_roll = CRR * mass_kg * G
    return (f_wheel - f_drag - f_roll) / mass_kg, rpm


def coast_accel(v_ms):
    """Beschleunigung waehrend der Schaltpause (keine Antriebskraft)."""
    f_drag = 0.5 * RHO_KG_M3 * CDA_M2 * v_ms ** 2
    f_roll = CRR * MASS_KG * G
    return -(f_drag + f_roll) / MASS_KG


def find_vmax(gear, bias=1.0, f_max=None):
    """Loest accel(v, gear, bias, f_max=f_max) = 0 (Kraftgleichgewicht
    Antrieb vs. Luft-/Rollwiderstand)."""
    vs = np.linspace(1.0, 130.0, 260)
    a_vals = np.array([accel(v, gear, bias, f_max=f_max)[0] for v in vs])
    sign_changes = np.where(np.diff(np.sign(a_vals)) < 0)[0]
    if len(sign_changes) == 0:
        raise RuntimeError("Kein Kraftgleichgewicht im gesuchten Geschwindigkeitsbereich gefunden")
    i = sign_changes[0]
    return brentq(lambda v: accel(v, gear, bias, f_max=f_max)[0], vs[i], vs[i + 1])


def find_equilibrium_speed(gear, etc_deg, mass_kg=MASS_KG, v_lo=1.0, v_hi=130.0, n=260):
    """Wie find_vmax(), aber bei konstantem Teillast-ETC statt Volllast -
    liefert die Gleichgewichts-/Cruise-Geschwindigkeit in `gear` bei diesem
    Drosselklappenwinkel, oder None, wenn im gesuchten Bereich kein
    Kraftgleichgewicht existiert (z.B. ETC zu niedrig, um in diesem Gang
    ueberhaupt gegen Roll-/Luftwiderstand anzukommen)."""
    vs = np.linspace(v_lo, v_hi, n)
    a_vals = np.array([accel(v, gear, mass_kg=mass_kg, etc_deg=etc_deg)[0] for v in vs])
    sign_changes = np.where(np.diff(np.sign(a_vals)) < 0)[0]
    if len(sign_changes) == 0:
        return None
    i = sign_changes[0]
    return brentq(lambda v: accel(v, gear, mass_kg=mass_kg, etc_deg=etc_deg)[0], vs[i], vs[i + 1])


def interp_crossing_time(t0, v0, t1, v1, v_target):
    if v1 == v0:
        return t1
    frac = (v_target - v0) / (v1 - v0)
    return t0 + frac * (t1 - t0)


def simulate(bias=1.0, label="raw", f_max=None):
    """Integriert 0 -> Vmax (Volllast, alle Gaenge), gibt Zeitreihe +
    Kennzahlen zurueck."""
    t = 0.0
    v = 0.01  # m/s, Start knapp ueber 0 (vermeidet rpm=0 als Sonderfall)
    dist = 0.0
    gear = 1
    vmax = find_vmax(6, bias, f_max)

    rows = [(t, v, gear, 0.0, rpm_from_speed(v, gear))]
    shift_events = []
    milestones = {}
    targets_ms = {"100": 100 / 3.6, "200": 200 / 3.6}
    reached = set()

    while t < MAX_SIM_TIME_S:
        a, rpm = accel(v, gear, bias, f_max=f_max)

        do_shift = False
        if gear < 6:
            a_next, _ = accel(v, gear + 1, bias, f_max=f_max)
            if rpm >= REDLINE_RPM or a_next >= a:
                do_shift = True

        if do_shift:
            shift_dur = ATTACK_SHIFT_S[(gear, gear + 1)]
            t_shift_start, v_shift_start = t, v
            n_sub = max(1, int(round(shift_dur / DT_S)))
            sub_dt = shift_dur / n_sub
            for _ in range(n_sub):
                v = max(v + coast_accel(v) * sub_dt, 0.0)
                t += sub_dt
                dist += v * sub_dt
                rows.append((t, v, gear, coast_accel(v), rpm_from_speed(v, gear)))
            shift_events.append({
                "from_gear": gear, "to_gear": gear + 1,
                "t_start_s": t_shift_start, "v_kmh": v_shift_start * 3.6,
                "duration_s": shift_dur,
            })
            gear += 1
            continue

        v_new = v + a * DT_S
        t_new = t + DT_S
        dist += v * DT_S

        for key, v_target in targets_ms.items():
            if key not in reached and v < v_target <= v_new:
                milestones[key] = interp_crossing_time(t, v, t_new, v_new, v_target)
                reached.add(key)

        v, t = v_new, t_new
        rows.append((t, v, gear, a, rpm_from_speed(v, gear)))

        if gear == 6 and abs(a) < VMAX_STEADY_STATE_ACCEL:
            break

    t_100 = milestones.get("100")
    t_200 = milestones.get("200")
    return {
        "label": label, "bias": bias, "vmax_ms": vmax, "vmax_kmh": vmax * 3.6,
        "t_100_s": t_100, "t_200_s": t_200,
        "t_100_200_s": (t_200 - t_100) if (t_100 is not None and t_200 is not None) else None,
        "t_sim_end_s": t, "v_sim_end_kmh": v * 3.6, "distance_m": dist,
        "shift_events": shift_events, "rows": rows,
    }


STUCK_CHECK_S = 5.0     # nach dieser Zeit gilt Gang 1 als "kommt nicht von der Stelle"
STUCK_SPEED_MS = 1.0    # bei diesem ETC nicht genug Kraft fuers Anfahren


def simulate_constant_throttle(etc_deg, label, mass_kg=MASS_KG):
    """Wie simulate(), aber bei KONSTANTEM Teillast-ETC statt Vollgas (siehe
    Docstring oben, "TEILLAST-ERWEITERUNG"). Kein bias-/f_max-Parameter -
    das Teillast-Kennfeld ist bereits direkt an echten Beschleunigungsdaten
    validiert (siehe partial_load_model.py), eine zusaetzliche pauschale
    Korrektur waere unbegruendet. Endzustand ist eine Gleichgewichts-/
    Cruise-Geschwindigkeit in Gang 6 (bzw. None, falls auch dort keine
    erreichbar ist) statt echtem Vmax."""
    t = 0.0
    v = 0.01
    dist = 0.0
    gear = 1
    eq_speed = find_equilibrium_speed(6, etc_deg, mass_kg)

    rows = [(t, v, gear, 0.0, rpm_from_speed(v, gear))]
    shift_events = []
    milestones = {}
    targets_ms = {"100": 100 / 3.6, "200": 200 / 3.6}
    reached = set()
    stuck = False

    while t < MAX_SIM_TIME_S:
        a, rpm = accel(v, gear, mass_kg=mass_kg, etc_deg=etc_deg)

        if gear == 1 and t >= STUCK_CHECK_S and v < STUCK_SPEED_MS:
            stuck = True
            break

        do_shift = False
        if gear < 6:
            a_next, _ = accel(v, gear + 1, mass_kg=mass_kg, etc_deg=etc_deg)
            if rpm >= REDLINE_RPM or a_next >= a:
                do_shift = True

        if do_shift:
            shift_dur = ATTACK_SHIFT_S[(gear, gear + 1)]
            t_shift_start, v_shift_start = t, v
            n_sub = max(1, int(round(shift_dur / DT_S)))
            sub_dt = shift_dur / n_sub
            for _ in range(n_sub):
                v = max(v + coast_accel(v) * sub_dt, 0.0)
                t += sub_dt
                dist += v * sub_dt
                rows.append((t, v, gear, coast_accel(v), rpm_from_speed(v, gear)))
            shift_events.append({
                "from_gear": gear, "to_gear": gear + 1,
                "t_start_s": t_shift_start, "v_kmh": v_shift_start * 3.6,
                "duration_s": shift_dur,
            })
            gear += 1
            continue

        v_new = max(v + a * DT_S, 0.0)
        t_new = t + DT_S
        dist += v * DT_S

        for key, v_target in targets_ms.items():
            if key not in reached and v < v_target <= v_new:
                milestones[key] = interp_crossing_time(t, v, t_new, v_new, v_target)
                reached.add(key)

        v, t = v_new, t_new
        rows.append((t, v, gear, a, rpm_from_speed(v, gear)))

        if gear == 6 and abs(a) < VMAX_STEADY_STATE_ACCEL:
            break

    t_100 = milestones.get("100")
    t_200 = milestones.get("200")
    return {
        "label": label, "etc_deg": etc_deg,
        "vmax_ms": eq_speed, "vmax_kmh": (eq_speed * 3.6) if eq_speed is not None else None,
        "t_100_s": t_100, "t_200_s": t_200,
        "t_100_200_s": (t_200 - t_100) if (t_100 is not None and t_200 is not None) else None,
        "t_sim_end_s": t, "v_sim_end_kmh": v * 3.6, "distance_m": dist,
        "stuck_in_gear_1": stuck,
        "shift_events": shift_events, "rows": rows,
    }


def print_summary(res):
    if "bias" in res:
        header = f"Szenario: {res['label']} (Bias-Faktor {res['bias']:.2f})"
    else:
        header = f"Szenario: {res['label']} (konstantes ETC={res['etc_deg']:.0f}°)"
    print(f"\n--- {header} ---")
    for ev in res["shift_events"]:
        print(f"  Schaltung {ev['from_gear']}->{ev['to_gear']} bei "
              f"t={ev['t_start_s']:.2f}s, v={ev['v_kmh']:.1f} km/h "
              f"(Zugkraftunterbrechung {ev['duration_s']:.2f}s)")

    if res.get("stuck_in_gear_1"):
        print(f"  HINWEIS: bei diesem ETC reicht die Kraft in Gang 1 nicht aus, "
              f"um ueber {STUCK_SPEED_MS:.1f} m/s zu beschleunigen - Simulation abgebrochen.")
        return

    def fmt(x):
        return f"{x:.2f}s" if x is not None else "nicht erreicht"

    print(f"  0-100 km/h:   {fmt(res['t_100_s'])}")
    print(f"  0-200 km/h:   {fmt(res['t_200_s'])}")
    print(f"  100-200 km/h: {fmt(res['t_100_200_s'])}")
    vmax_label = "Vmax (Kraftgleichgewicht Gang 6)" if "bias" in res else "Gleichgewichts-/Cruise-Geschwindigkeit Gang 6"
    if res["vmax_kmh"] is not None:
        print(f"  {vmax_label}: {res['vmax_kmh']:.1f} km/h")
    else:
        print(f"  {vmax_label}: kein Kraftgleichgewicht im gesuchten Bereich gefunden")
    if res["t_sim_end_s"] >= MAX_SIM_TIME_S:
        print(f"  HINWEIS: Simulation nach {MAX_SIM_TIME_S}s abgebrochen, "
              f"Endzustand evtl. nicht erreicht (v_ende={res['v_sim_end_kmh']:.1f} km/h)")


def plot_results(results, out_path, title="0-Vmax Simulation (Volllast, alle Gaenge)",
                  colors=None):
    if colors is None:
        colors = {"raw": "steelblue", "bias-korrigiert": "seagreen", "traktionsbegrenzt": "darkorange"}
    fig, ax = plt.subplots(figsize=(9, 6))
    for res in results:
        rows = np.array([(r[0], r[1] * 3.6) for r in res["rows"]])
        color = colors.get(res["label"], "gray")
        ax.plot(rows[:, 0], rows[:, 1], label=res["label"], color=color)
        for ev in res["shift_events"]:
            ax.axvline(ev["t_start_s"], color=color, alpha=0.15, lw=1)
    ax.set_xlabel("Zeit [s]")
    ax.set_ylabel("Geschwindigkeit [km/h]")
    ax.set_title(title)
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def print_parameters():
    print("=== Verwendete Fahrzeugparameter (aus MX5_Aktueller_Kenntnisstand_2026-08-29.md) ===")
    print(f"Masse: {MASS_KG} kg | r_dyn: {R_DYN_M} m | Achsuebersetzung: {FINAL_DRIVE}")
    print(f"Gangverhaeltnisse: {GEAR_RATIOS}")
    print(f"eta: {ETA} | CdA: {CDA_M2} m^2 | rho: {RHO_KG_M3} kg/m^3 | Crr: {CRR}")
    print(f"ATTACK-Schaltzeiten [s]: {ATTACK_SHIFT_S}")
    print(f"Volllast-Drehmomentkurve: {len(RPM_TABLE)} Stuetzstellen, "
          f"{RPM_TABLE[0]}-{RPM_TABLE[-1]} U/min")
    print(f"Empirischer Bias-Korrekturfaktor (aus drivetrain_model_validation.py): "
          f"{EMPIRICAL_BIAS_FACTOR}")


# Demo-Drosselklappenwinkel fuer die Teillast-Szenarien: Spanne ueber das
# gut abgedeckte Teillastband (siehe partial_load_model.py Kennfeld-Plot),
# klar unterhalb ETC_WOT_MIN (=80 Grad, dort beginnt die separate
# Volllast-Behandlung oben).
PARTIAL_LOAD_DEMO_ETC_DEG = [30.0, 50.0, 70.0]
PARTIAL_LOAD_COLORS = {"Teillast ETC=30°": "purple", "Teillast ETC=50°": "goldenrod",
                        "Teillast ETC=70°": "crimson"}


def main():
    print_parameters()
    results = [
        simulate(bias=1.0, label="raw"),
        simulate(bias=EMPIRICAL_BIAS_FACTOR, label="bias-korrigiert"),
        simulate(bias=1.0, label="traktionsbegrenzt", f_max=TRACTION_MAX_FORCE_N),
    ]
    for res in results:
        print_summary(res)

    print("\n=== Teillast-Szenarien (konstantes ETC, Kennfeld aus partial_load_model.py) ===")
    partial_results = [
        simulate_constant_throttle(etc, label=f"Teillast ETC={etc:.0f}°")
        for etc in PARTIAL_LOAD_DEMO_ETC_DEG
    ]
    for res in partial_results:
        print_summary(res)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    plot_results(results, os.path.join(RESULTS_DIR, "performance_simulation.png"))
    print(f"\nDiagramm: {RESULTS_DIR}/performance_simulation.png")

    plot_results(partial_results, os.path.join(RESULTS_DIR, "performance_simulation_partial_load.png"),
                 title="Teillast-Beschleunigungslauf (konstantes ETC, alle Gaenge)",
                 colors=PARTIAL_LOAD_COLORS)
    print(f"Diagramm: {RESULTS_DIR}/performance_simulation_partial_load.png")

    all_results = results + partial_results
    for res in all_results:
        df = pd.DataFrame(res["rows"], columns=["t_s", "v_ms", "gear", "accel_ms2", "rpm"])
        df["v_kmh"] = df["v_ms"] * 3.6
        safe_label = res["label"].replace(" ", "_").replace("°", "deg").replace("=", "")
        csv_path = os.path.join(RESULTS_DIR, f"performance_simulation_{safe_label}.csv")
        df.to_csv(csv_path, index=False)

    summary = [{k: v for k, v in res.items() if k != "rows"} for res in all_results]
    with open(os.path.join(RESULTS_DIR, "performance_simulation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Details: {RESULTS_DIR}/performance_simulation_summary.json")


if __name__ == "__main__":
    main()
