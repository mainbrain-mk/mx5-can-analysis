"""
Spreewaldring - alternierende Linie/Geschwindigkeits-Optimierung fuer den
Mazda MX-5 ND G184 (30.08.2026)

Nutzerauftrag: nach dem ersten Ideallinien-Versuch (reine Kruemmungs-
minimierung, `spreewaldring_racing_line.py`) wollte der Nutzer wissen, wie
aufwendig eine tatsaechliche "Bestzeitrunde" waere, die (a) die angenommene
Reifen-Haftgrenze ausnutzt, (b) die ganze Streckenbreite nutzt, (c) nicht
bremst, wo Vollgas moeglich ist. Antwort/Empfehlung: ein ALTERNIERENDES
Verfahren (Linie<->Geschwindigkeit abwechselnd nachjustieren, plus
kombinierter Reifenkraftkreis) statt einem vollen nichtlinearen
Optimalsteuerungsproblem - dieses Skript setzt genau das um.

WICHTIG: Punkt (c) ("nicht bremsen wenn Vollgas moeglich") war SCHON in der
alten Zwei-Pass-Rundenzeit-Simulation (`spreewaldring_lap_simulation.py`)
automatisch erfuellt - der Vorwaerts-/Rueckwaerts-Pass bremst nur, wenn eine
SPAETERE Kurve es zwingend verlangt, nie praeventiv. Neu ist hier:

1. **Kombinierter Reifenkraftkreis** (bisher: Laengs- und Quergrenze
   UNABHAENGIG behandelt, explizit als Einschraenkung dokumentiert). Jetzt:
   an jedem Punkt wird aus der aktuellen Geschwindigkeit und dem
   Kruemmungsradius die BEREITS genutzte Querbeschleunigung berechnet
   (a_lat = v^2/R), und die fuer Beschleunigen/Bremsen verbleibende
   Laengsbeschleunigung entsprechend reduziert:
   a_long_verfuegbar = sqrt(max(0, (mu*g)^2 - a_lat^2))
   (klassische Kraftkreis-/Kamm'scher-Kreis-Aufteilung). Reduziert sich bei
   a_lat=0 (Gerade) exakt auf das alte Modell.
2. **Alternierende Optimierung**: abwechselnd (a) Geschwindigkeitsprofil bei
   FIXIERTER Linie neu loesen (obiger Kraftkreis-Loeser), (b) Linie anhand
   der aktuellen Querbeschleunigungs-AUSLASTUNG lokal nachjustieren - Punkte,
   die bereits nahe der Haftgrenze fahren (a_lat nahe mu*g), werden staerker
   in Richtung groesserer Radius gezogen (elastic-band-Schritt mit hoeherem
   Schrittweite-Faktor), Punkte mit Reserve bleiben eher unangetastet. Das
   naehert sich einem "spaeteren Scheitelpunkt" an chirurgisch limitierten
   Stellen an, ohne ein echtes Optimalsteuerungsproblem zu loesen.

Das ist WEITERHIN eine Naeherung (siehe EINSCHRAENKUNGEN), kein Beweis fuer
die global optimale Linie - aber deutlich naeher an "Bestzeitrunde" als die
reine Kruemmungsminimierung.

Fuer die Visualisierung (Nutzerwunsch: "dem Optimierungsprozess zusehen"):
bei jeder AEUSSEREN Iteration wird ein Snapshot (Linie, Geschwindigkeit,
Rundenzeit) gespeichert - daraus baut eine separate HTML-Seite eine
abspielbare Animation. Da die gesamte Rechnung nur Sekunden dauert, ist eine
ECHTE Live-Ansicht waehrend der Rechenzeit nicht sinnvoll beobachtbar -
stattdessen wird der GESAMTE Verlauf einmal durchgerechnet und dann als
Animation/Schieberegler zum Durchscrubben aufbereitet.

EINSCHRAENKUNGEN:
  - Immer noch NICHT das volle Optimalsteuerungsproblem - die Linien-Update-
    Heuristik (Schrittweite gewichtet nach Querbeschleunigungs-Auslastung)
    ist ein plausibler, aber nicht mathematisch bewiesener Proxy fuer
    "Richtung minimaler Rundenzeit".
  - Kraftkreis nutzt EINEN mu-Wert fuer Laengs- UND Querrichtung
    (isotroper Reifen, keine getrennten Laengs-/Quer-mu-Werte gemessen).
  - Kein Gewichtstransfer, keine Aero - gleiche Einschraenkungen wie die
    vorherigen Rundenzeit-Modelle.
  - Fahrzeugbreite (1.74m) weiterhin Annahme.
  - Konvergenz wird ueber eine feste Anzahl aeusserer Iterationen erzwungen
    (kein formales Abbruchkriterium/Beweis lokaler Optimalitaet).

Aufruf: .venv/bin/python scripts/spreewaldring_racing_line_optimal.py

NACHTRAG (30.08.2026) - echte Schaltzeiten statt perfekter Gangwahl:

Bisher nutzte der Vorwaerts-Pass `accel_envelope()` - die beste Beschleunigung
UEBER ALLE 6 GAENGE gleichzeitig, was ein perfektes, verzoegerungsfreies
Schalten unterstellt. Neu: `simulate_lap_combined_friction()` fuehrt jetzt
einen TATSAECHLICHEN Gang mit und fuegt bei jedem Hochschalten die echte
ATTACK-Zugkraftunterbrechung ein (`performance_simulation.ATTACK_SHIFT_S`,
0.15-0.25s je nach Gangpaar, gleiche Werte wie in der 0-Vmax-Simulation) -
waehrend dieser Zeit rollt das Fahrzeug nur unter Luft-/Rollwiderstand
(`coast_accel`), keine Antriebskraft. Da die Simulation distanzbasiert
schrittet (nicht zeitbasiert), wird pro 3m-Schritt genau ausgerechnet, welcher
Anteil noch in den Schaltvorgang faellt (`forward_step_shifted()`, quadratische
Kinematik-Aufloesung) - ein Schaltvorgang kann sich damit korrekt ueber
mehrere Distanzschritte hinweg erstrecken, falls er bei hoher Geschwindigkeit
laenger dauert als die reine Schrittlaenge.

Nutzeranfrage dabei: koennte man statt der idealisierten Volllast-
Drehmomentkurve auch das Teillastkennfeld (`partial_load_model.py`) fuer
praezisere Werte NACH dem Schalten verwenden? EMPIRISCH GEPRUEFT (an den 47
WOT-Segmenten aus `drivetrain_model_validation_summary.json`, gleiche
Methodik wie beim urspruenglichen F_max-Fit): das verschlechtert die
Vorhersage tatsaechlich - RMSE 0.163 m/s² mit dem bisherigen rohen
Volllast-Modell + Traktionsdeckel (F_max=4450N) gegenueber 0.175-0.189 m/s²
mit dem Teillastkennfeld bei verschiedenen nahe-WOT-Drosselklappenwinkeln
(78-86°), jeweils ebenfalls mit Traktionsdeckel. Grund vermutlich: F_max
wurde SPEZIFISCH gegen genau diese Volllast-Segmente gefittet, waehrend das
Teillastkennfeld primaer auf Teilgas-Fahrverhalten trainiert ist und im
Extrapolationsbereich nahe 100% ETC weniger praezise ist. **Konsequenz:
Teillastkennfeld NICHT fuer die Beschleunigungs-Huellkurve uebernommen** -
das bisherige rohe-WOT-Modell + Traktionsdeckel bleibt die empirisch beste
verfuegbare Option, auch direkt nach einem Gangwechsel. Die Verbesserung
beschraenkt sich also auf die echten Schaltzeitverluste (siehe oben).

Vereinfachung: `coast_accel()` waehrend eines einzelnen Schaltvorgangs wird
als NAEHERUNGSWEISE KONSTANT angenommen (Wert bei Schaltbeginn) - bei
0.15-0.25s Dauer aendert sich der Luftwiderstand in dieser kurzen Zeit nur
unwesentlich.
"""
import os
import json
import math

import numpy as np

from spreewaldring_track import (
    RESULTS_DIR, RESAMPLE_STEP_M, CURVATURE_WINDOW_M, G,
    resample_closed_loop, compute_curvature_radius,
)
from spreewaldring_track_surface import compute_tangents_closed
from spreewaldring_lap_simulation import simulate_lap, find_corner_min_speeds, TIRE_MU_RANGE, accel_envelope
from spreewaldring_racing_line import load_corridor, corridor_bounds, CAR_HALF_WIDTH_M
from drivetrain_model_validation import rpm_from_speed, GEAR_RATIOS
from performance_simulation import accel as accel_single_gear, coast_accel, ATTACK_SHIFT_S, REDLINE_RPM, TRACTION_MAX_FORCE_N

SURFACE_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_track_surface_summary.json")

MU = TIRE_MU_RANGE[1]     # optimistisches Szenario (mu=1.3) fuer die Optimierung selbst
N_OUTER = 24               # aeussere Iterationen (Geschwindigkeit<->Linie)
INNER_STEPS = 80           # innere Glaettungsschritte pro aeusserer Iteration
BASE_ALPHA = 0.15
V_MIN_MS = 5.0
# Haerteste real gemessene Bremsung (BFP_PRE_MZ Bremsdruck + OBD-Tempoabfall,
# 229 Bremsvorgaenge ueber 7 Logs, siehe braking_model.py/results/braking_
# model_summary.json) statt der rein theoretischen mu*g-Annahme. Nutzer
# bestaetigt (08.09.2026): sowohl in den Logs als auch real auf der Strecke
# am Ende der Zielgeraden wird bis ins ABS gebremst - dieser Wert ist damit
# die tatsaechliche, nicht nur theoretische Bremsgrenze. Gilt NUR fuer die
# Laengsverzoegerung beim Bremsen, nicht fuer die Kurven-Querbeschleunigung
# (die bleibt bei der Reifen-mu-Annahme, siehe simulate_lap_combined_friction).
BRAKE_CAP_G = 0.4772
# Kurze "Vollgas-dann-wieder-Vollbremsung"-Inseln zwischen zwei Bremszonen
# (z.B. bei eng verbundenen Kurven) sind fuer ein Punktmassen-Modell ohne
# Umschaltkosten zeit-optimal (bang-bang, Standardergebnis der
# Optimalsteuerungstheorie), aber in der Realitaet destabilisierend - ein
# abrupter Vollgas/Vollbremsung-Wechsel ist nicht sicher ausfuehrbar
# (Nutzerbeobachtung 08.09.2026, siehe smooth_wasted_accel_brake()). Inseln
# kuerzer als dieser Wert werden durch eine konstante Teillast-Beschleunigung
# ersetzt, die exakt auf die vom Rueckwaertspass ohnehin schon geforderte
# Einfahrtsgeschwindigkeit der naechsten Bremszone einschwenkt. ~40m
# entspricht bei typischem Kurvenausgangstempo (15-20 m/s) ca. 2-2.5s -
# grob die Zeit, die ein Fahrer braucht, um einen Pedalwechsel ueberhaupt
# sinnvoll zu committen, keine gemessene Groesse.
MIN_ACCEL_HOLD_M = 40.0


def math_sqrt_nonneg(x):
    return math.sqrt(x) if x > 0 else 0.0


def best_gear_for_speed(v):
    """Gang mit der hoechsten Beschleunigung bei dieser Geschwindigkeit -
    Startannahme fuer den Vorwaerts-Pass (siehe Docstring, Nachtrag
    Schaltzeiten): der Fahrer hat die Runde schon oft genug gefahren, um an
    jedem Punkt im "richtigen" Gang zu sein.
    WICHTIG: Gaenge, die bei dieser Geschwindigkeit schon ueber Redline
    drehen wuerden, werden ausgeschlossen - torque_nm() extrapoliert
    ausserhalb der Tabelle sonst flach weiter, was bei stark ueberhoehter
    Drehzahl (z.B. Gang 1 bei 90 km/h) einen numerisch hohen, aber
    physikalisch unmoeglichen Beschleunigungswert vortaeuschen kann (Bug
    gefunden 30.08.2026: das liess die Simulation bei jeder Kurvengeschwin-
    digkeits-Deckelung faelschlich in Gang 1 "zurueckfallen")."""
    candidates = {g: accel_single_gear(v, g, f_max=TRACTION_MAX_FORCE_N)[0]
                  for g in GEAR_RATIOS if rpm_from_speed(v, g) <= REDLINE_RPM}
    if not candidates:
        return max(GEAR_RATIOS)  # selbst der hoechste Gang liegt schon ueber Redline
    return max(candidates, key=candidates.get)


def forward_step_shifted(v, gear, shift_remaining_s, ds, radius, mu):
    """Ein distanzbasierter Vorwaerts-Schritt MIT tatsaechlicher Gangwahl und
    echten ATTACK-Schaltzeiten (siehe Modul-Docstring, Nachtrag Schaltzeiten).
    Liefert (v_neu, gear_neu, shift_remaining_neu) OHNE die Kurvengeschwin-
    digkeits-Deckelung - die wendet der Aufrufer wie bisher separat an."""
    if shift_remaining_s > 0:
        a_coast = coast_accel(v)
        v_full = math_sqrt_nonneg(v ** 2 + 2 * a_coast * ds)
        t_full = 2 * ds / (v + v_full) if (v + v_full) > 1e-9 else 0.0
        if t_full <= shift_remaining_s:
            # Schaltvorgang dauert laenger als dieser Schritt - Fahrzeug
            # rollt fuer die GESAMTE Schrittdistanz noch ohne Antriebskraft
            return v_full, gear, shift_remaining_s - t_full
        # Schaltvorgang endet MITTEN in diesem Schritt (konstante
        # Rollverzoegerung angenommen, siehe Docstring)
        t = shift_remaining_s
        v_end = max(v + a_coast * t, 0.0)
        ds_used = v * t + 0.5 * a_coast * t * t
        return forward_step_shifted(v_end, gear + 1, 0.0, max(ds - ds_used, 0.0), radius, mu)

    a_lat = min(v ** 2 / max(radius, 1e-6), mu * G)
    a_long_avail = math_sqrt_nonneg((mu * G) ** 2 - a_lat ** 2)

    if gear < 6:
        # Schaltentscheidung auf Basis der durch den Reifenkraftkreis bereits
        # GEDECKELTEN Beschleunigung (nicht der rohen Motorwerte) - sonst
        # wuerde die Simulation mitten in einer langsamen, kraftschluss-
        # limitierten Kurve staendig unnoetig hoch-/wieder hochschalten
        # wollen, weil Gang n+1 auf dem Papier immer mehr Kraft liefert,
        # obwohl der Reifen ohnehin schon die Grenze vorgibt (Bug gefunden
        # 30.08.2026: fuehrte zu dutzenden sinnlosen Schaltvorgaengen pro
        # Kurve statt EINEM Schaltvorgang, siehe Nachtrag im Modul-Docstring).
        rpm = rpm_from_speed(v, gear)
        a_cur = min(accel_single_gear(v, gear, f_max=TRACTION_MAX_FORCE_N)[0], a_long_avail)
        a_next = min(accel_single_gear(v, gear + 1, f_max=TRACTION_MAX_FORCE_N)[0], a_long_avail)
        if rpm >= REDLINE_RPM or a_next > a_cur:
            return forward_step_shifted(v, gear, ATTACK_SHIFT_S[(gear, gear + 1)], ds, radius, mu)

    a_use = min(accel_single_gear(v, gear, f_max=TRACTION_MAX_FORCE_N)[0], a_long_avail)
    v_new = math_sqrt_nonneg(v ** 2 + 2 * a_use * ds)
    return v_new, gear, shift_remaining_s


def smooth_wasted_accel_brake(v_bwd, v_fwd, v_corner_o, ds_o, mu, min_hold_m=MIN_ACCEL_HOLD_M):
    """Ersetzt kurze Vollgas-Inseln, die von Bremszonen eingerahmt sind
    (Laenge < min_hold_m), durch eine konstante Teillast-Beschleunigung, die
    exakt auf die bereits vom Rueckwaertspass berechnete Einfahrtsgeschwindigkeit
    der naechsten Bremszone einschwenkt - siehe MIN_ACCEL_HOLD_M fuer den
    Hintergrund. Laesst laengere (echte) Beschleunigungszonen unangetastet.
    Die erforderliche Beschleunigung darf auch leicht NEGATIV sein (bis
    coast_accel(), also bis zum voelligen Schliessen der Drosselklappe) -
    das deckt kurze Inseln ab, die eigentlich ein sanftes Verzoegern statt
    eines Beschleunigens brauchen, weiterhin rein ueber das Gaspedal statt
    ueber die Bremse (Nutzerbeobachtung 08.09.2026: "0% Gas" ist real nicht
    dasselbe wie "keine Kraft am Rad" - siehe Teillast-Prozentrechnung
    unten/im JS-Port)."""
    n = len(v_bwd)
    braking = v_bwd < v_fwd - 1e-3
    if not braking.any() or braking.all():
        return v_bwd  # keine oder durchgehende Bremsung - nichts zu tun
    v_out = v_bwd.copy()
    start0 = int(np.argmax(braking))  # garantiert bremsender Punkt als Rotationsanker
    order = [(start0 + k) % n for k in range(n)]
    k = 0
    while k < n:
        idx = order[k]
        if braking[idx]:
            k += 1
            continue
        j = k
        arc_len = 0.0
        while j < n and not braking[order[j]]:
            arc_len += ds_o[order[j]]
            j += 1
        prev_idx = order[k - 1] if k > 0 else order[n - 1]
        next_idx = order[j] if j < n else order[0]
        if arc_len < min_hold_m:
            v_start, v_end = v_bwd[prev_idx], v_bwd[next_idx]
            a_req = (v_end ** 2 - v_start ** 2) / max(2 * arc_len, 1e-6)
            a_max = accel_envelope(v_start)
            a_min = coast_accel(v_start)
            if a_min <= a_req <= a_max:
                s_local = 0.0
                for m in range(k, j):
                    s_local += ds_o[order[m]]
                    v_smooth = math_sqrt_nonneg(v_start ** 2 + 2 * a_req * s_local)
                    v_out[order[m]] = min(v_smooth, v_corner_o[order[m]])
        k = j
    return v_out


def smooth_short_brake_spikes(v, v_fwd, v_corner_o, ds_o, mu, min_hold_m=MIN_ACCEL_HOLD_M,
                               brake_cap_g=BRAKE_CAP_G):
    """Spiegelfall zu smooth_wasted_accel_brake(): eine kurze, isolierte
    Bremsinsel (Laenge < min_hold_m, oft nur ein einzelner Punkt), die von
    Nicht-Bremsen (Rollen/Beschleunigen) eingerahmt ist, wird nicht durch
    einen abrupten Bremsstoss erzeugt, sondern durch frueheres, sanfteres
    Anbremsen: der Bremsbeginn wird rueckwaerts in den vorherigen,
    unveraenderten (nicht-bremsenden) Abschnitt hinein vorverlegt, bis die
    Bremszone mindestens min_hold_m lang ist, mit konstanter Verzoegerung.
    Die Geschwindigkeit am ENDE der urspruenglichen Insel bleibt exakt
    erhalten (das ist die vom naechsten Kurvenradius geforderte harte
    Randbedingung) - nur der WEG dorthin wird geglaettet.
    Nutzerbeobachtung 08.09.2026, Punkt 492: eine einzelne Vollbremsung
    zwischen zwei frei rollenden Punkten ist real nicht so ausfuehrbar."""
    n = len(v)
    braking = v < v_fwd - 1e-3
    if not braking.any() or braking.all():
        return v.copy()
    v_out = v.copy()
    start0 = int(np.argmax(~braking))  # garantiert NICHT-bremsender Punkt als Anker
    order = [(start0 + k) % n for k in range(n)]
    k = 0
    while k < n:
        idx = order[k]
        if not braking[idx]:
            k += 1
            continue
        j = k
        arc_len = 0.0
        while j < n and braking[order[j]]:
            arc_len += ds_o[order[j]]
            j += 1
        next_idx = order[j] if j < n else order[0]
        if arc_len < min_hold_m:
            v_end = v[next_idx]  # harte Randbedingung, bleibt unveraendert
            far_k = k - 1
            ext_len = arc_len
            while ext_len < min_hold_m and far_k >= 0 and not braking[order[far_k]]:
                ext_len += ds_o[order[far_k]]
                far_k -= 1
            far_idx = order[far_k + 1]
            v_start = v[far_idx]
            if v_start > v_end and ext_len > 1e-6:
                a_req = (v_start ** 2 - v_end ** 2) / (2 * ext_len)
                if a_req <= brake_cap_g * G:
                    s_local = 0.0
                    m = far_k + 1
                    while order[m] != next_idx:
                        s_local += ds_o[order[m]]
                        v_smooth = math_sqrt_nonneg(v_start ** 2 - 2 * a_req * s_local)
                        v_out[order[m]] = min(v_smooth, v_corner_o[order[m]])
                        m += 1
        k = j
    return v_out


def simulate_lap_combined_friction(points, mu, n_passes=2, model_shifts=True, brake_cap_g=BRAKE_CAP_G):
    """Wie simulate_lap() in spreewaldring_lap_simulation.py, aber mit
    KOMBINIERTEM Reifenkraftkreis (siehe Docstring) statt unabhaengiger
    Laengs-/Quergrenze, und mit tatsaechlichem (nicht angenommenem
    gleichmaessigem) Punktabstand ds, da sich die Linie waehrend der
    Optimierung innerhalb des Korridors bewegt. Mit model_shifts=True (Default,
    siehe Modul-Docstring "Nachtrag Schaltzeiten") wird ein echter Gang
    mitgefuehrt und bei jedem Hochschalten die ATTACK-Schaltzeit als
    Rollphase eingerechnet, statt der reinen envelope-max-ueber-alle-Gaenge-
    Annahme.

    brake_cap_g: zusaetzliche Obergrenze fuer die Laengsverzoegerung beim
    Bremsen (Rueckwaertspass), zusaetzlich zum Reifenkraftkreis. Default ist
    BRAKE_CAP_G (siehe dort) - die real gemessene, ABS-limitierte Bremsung
    statt der rein theoretischen mu*g-Annahme. brake_cap_g=None erzwingt das
    alte, rein theoretische Verhalten (nur noch fuer Vergleiche relevant,
    siehe spreewaldring_braking_model_comparison.py)."""
    n = len(points)
    ds = np.hypot(*(np.roll(points, -1, axis=0) - points).T)
    step_m = float(np.median(ds))
    radius = compute_curvature_radius(points, CURVATURE_WINDOW_M, step_m)
    v_corner = np.sqrt(np.maximum(mu * G * radius, V_MIN_MS ** 2))

    i0 = int(np.argmin(v_corner))
    order = np.roll(np.arange(n), -i0)
    v_corner_o = v_corner[order]
    ds_o = ds[order]
    radius_o = radius[order]

    def accel_kinematic(v_prev, r, ds_step):
        if model_shifts:
            raise RuntimeError("nur fuer model_shifts=False Pfad")
        a_lat = min(v_prev ** 2 / max(r, 1e-6), mu * G)
        a_long_avail = math_sqrt_nonneg((mu * G) ** 2 - a_lat ** 2)
        a_use = min(accel_envelope(v_prev), a_long_avail)
        return math_sqrt_nonneg(v_prev ** 2 + 2 * a_use * ds_step)

    v = v_corner_o.copy()
    for _ in range(n_passes):
        v_fwd = v.copy()
        gear = best_gear_for_speed(v_fwd[0]) if model_shifts else None
        shift_remaining = 0.0
        for i in range(1, n):
            if model_shifts:
                v_kin, gear, shift_remaining = forward_step_shifted(
                    v_fwd[i - 1], gear, shift_remaining, ds_o[i - 1], radius_o[i], mu)
            else:
                v_kin = accel_kinematic(v_fwd[i - 1], radius_o[i], ds_o[i - 1])
            v_fwd[i] = min(v_corner_o[i], v_kin)
        if model_shifts:
            v_kin0, gear, shift_remaining = forward_step_shifted(
                v_fwd[-1], gear, shift_remaining, ds_o[-1], radius_o[0], mu)
        else:
            v_kin0 = accel_kinematic(v_fwd[-1], radius_o[0], ds_o[-1])
        v_fwd[0] = min(v_corner_o[0], v_kin0, v_fwd[0])

        v_bwd = v_fwd.copy()
        for i in range(n - 2, -1, -1):
            a_lat = min(v_bwd[i + 1] ** 2 / max(radius_o[i], 1e-6), mu * G)
            a_brake = math_sqrt_nonneg((mu * G) ** 2 - a_lat ** 2)
            if brake_cap_g is not None:
                a_brake = min(a_brake, brake_cap_g * G)
            v_max_brake = np.sqrt(v_bwd[i + 1] ** 2 + 2 * a_brake * ds_o[i])
            v_bwd[i] = min(v_bwd[i], v_max_brake)
        a_lat_last = min(v_bwd[0] ** 2 / max(radius_o[-1], 1e-6), mu * G)
        a_brake_last = math_sqrt_nonneg((mu * G) ** 2 - a_lat_last ** 2)
        if brake_cap_g is not None:
            a_brake_last = min(a_brake_last, brake_cap_g * G)
        v_bwd[-1] = min(v_bwd[-1], np.sqrt(v_bwd[0] ** 2 + 2 * a_brake_last * ds_o[-1]))

        v = v_bwd

    # Einmalig NACH der Konvergenz ueber n_passes anwenden (nicht innerhalb
    # der Schleife) - sonst wuerde der naechste Vorwaerts-Pass wieder mit
    # Vollgas von den geglaetteten Werten aus starten und dieselbe Spitze
    # neu erzeugen. Siehe MIN_ACCEL_HOLD_M/smooth_wasted_accel_brake().
    v = smooth_wasted_accel_brake(v, v_fwd, v_corner_o, ds_o, mu)
    v = smooth_short_brake_spikes(v, v_fwd, v_corner_o, ds_o, mu,
                                   brake_cap_g=brake_cap_g if brake_cap_g is not None else mu)

    v_final = np.empty(n)
    v_final[order] = v
    lap_time_s = float(np.sum(ds / v_final))
    return v_final, lap_time_s, radius


def optimize_alternating(center, lo, hi, perp, mu=MU, n_outer=N_OUTER,
                          inner_steps=INNER_STEPS, base_alpha=BASE_ALPHA):
    n_lat = np.zeros(len(center))
    snapshots = []

    for outer in range(n_outer):
        points = center + n_lat[:, None] * perp
        v, lap_time, radius = simulate_lap_combined_friction(points, mu)

        a_lat_req = v ** 2 / np.maximum(radius, 1e-6)
        excess = np.clip(a_lat_req / (mu * G), 0.0, 1.0)
        alpha_i = base_alpha * (0.1 + 0.9 * excess)

        snapshots.append({
            "iteration": outer,
            "lap_time_s": lap_time,
            "points_utm33": points.tolist(),
            "v_kmh": (v * 3.6).tolist(),
            "radius_m": [None if not math.isfinite(r) else r for r in radius.tolist()],
        })
        print(f"  Iteration {outer:2d}: Rundenzeit={lap_time:.2f}s  "
              f"mittlere Auslastung={excess.mean():.2f}  max v={v.max()*3.6:.1f}km/h")

        for _ in range(inner_steps):
            p = center + n_lat[:, None] * perp
            p_avg = (np.roll(p, 1, axis=0) + np.roll(p, -1, axis=0)) / 2
            p_new = p + alpha_i[:, None] * (p_avg - p)
            n_new = np.sum((p_new - center) * perp, axis=1)
            n_lat = np.clip(n_new, lo, hi)

    return n_lat, snapshots


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("Lade Fahrbahn-Korridor (Hauptschleife) ...")
    center, left, right = load_corridor()
    tangents = compute_tangents_closed(center)
    lo, hi, perp = corridor_bounds(center, left, right, tangents)

    print(f"\nAlternierende Optimierung: {N_OUTER} aeussere Iterationen x {INNER_STEPS} "
          f"innere Glaettungsschritte, mu={MU} ...")
    n_lat, snapshots = optimize_alternating(center, lo, hi, perp)

    # Das Verfahren konvergiert NICHT monoton (siehe Docstring) - typischerweise
    # verbessert sich die Rundenzeit zunaechst deutlich, driftet dann ueber
    # weitere Iterationen wieder leicht nach oben. Deshalb: beste ueber alle
    # Iterationen gefundene Linie verwenden, nicht blind die letzte.
    best_snapshot = min(snapshots, key=lambda snap: snap["lap_time_s"])
    print(f"\nBeste Iteration: {best_snapshot['iteration']} "
          f"(Rundenzeit dort: {best_snapshot['lap_time_s']:.2f}s, "
          f"letzte Iteration: {snapshots[-1]['lap_time_s']:.2f}s)")
    points_final = np.array(best_snapshot["points_utm33"])
    line_resampled, _, total_len, _ = resample_closed_loop(points_final, RESAMPLE_STEP_M)
    s_line = np.arange(len(line_resampled)) * RESAMPLE_STEP_M
    radius_final = compute_curvature_radius(line_resampled, CURVATURE_WINDOW_M, RESAMPLE_STEP_M)

    print("\n=== Endergebnis, beide mu-Szenarien (finale Linie, Standard-Zweipass-Modell) ===")
    with open(os.path.join(RESULTS_DIR, "spreewaldring_lap_simulation_summary.json"), encoding="utf-8") as f:
        lap_summary_center = json.load(f)
    with open(os.path.join(RESULTS_DIR, "spreewaldring_racing_line_summary.json"), encoding="utf-8") as f:
        lap_summary_curv = json.load(f)

    results = {}
    for label, mu_s in [("konservativ (mu=1.0)", TIRE_MU_RANGE[0]),
                         ("optimistisch (mu=1.3)", TIRE_MU_RANGE[1])]:
        v_final, lap_time_final = simulate_lap(s_line, radius_final, mu_s)
        t_center = lap_summary_center["scenarios"][label]["lap_time_s"]
        t_curv = lap_summary_curv["scenarios"][label]["lap_time_racing_line_s"]
        print(f"\n--- {label} ---")
        print(f"Mittellinie:              {t_center:.2f} s")
        print(f"Kruemmungsminimierung:    {t_curv:.2f} s")
        print(f"Alternierend optimiert:   {lap_time_final:.2f} s")
        corners = find_corner_min_speeds(s_line, radius_final, v_final)
        results[label] = {
            "mu": mu_s, "lap_time_center_s": t_center, "lap_time_curvature_min_s": t_curv,
            "lap_time_optimal_s": lap_time_final,
            "v_max_kmh": float(v_final.max() * 3.6),
            "v_mean_kmh": float(total_len / lap_time_final * 3.6),
            "corners": corners, "v_kmh": (v_final * 3.6).tolist(),
        }

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_optimal_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "mu_used_for_optimization": MU,
            "n_outer": N_OUTER, "inner_steps": INNER_STEPS, "base_alpha": BASE_ALPHA,
            "car_half_width_m": CAR_HALF_WIDTH_M,
            "best_iteration": best_snapshot["iteration"],
            "final_length_m": total_len,
            "racing_line_utm33": line_resampled.tolist(),
            "s_m": s_line.tolist(),
            "radius_m": [None if not math.isfinite(r) else r for r in radius_final.tolist()],
            "scenarios": results,
            "snapshots": snapshots,
            "corridor_lo_m": lo.tolist(), "corridor_hi_m": hi.tolist(),
            "centerline_utm33": center.tolist(),
            "left_edge_utm33": left.tolist(), "right_edge_utm33": right.tolist(),
        }, f, indent=2, ensure_ascii=False)
    print(f"\nDetails + Iterations-Snapshots: {out_json}")


if __name__ == "__main__":
    main()
