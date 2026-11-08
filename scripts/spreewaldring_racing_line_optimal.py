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

NACHTRAG (07.11.2026) - Fahrzeugmodell aus den Logs: Grip-Ellipse je Reifensatz, gemessene Zugkraftluecken und
wahres Tempo kommen aus mx5_vehicle_model.py (Vehicle), der Gangplan fuer die ganze Runde waegt jede Schaltung gegen ihre
Zugkraftluecke ab (plan_gears), in Bremszonen ist Schalten kostenlos. simulate_lap_full()
ist die einzige Rundensimulation, auch der Editor rechnet damit. Die
Einschraenkung "ein mu fuer laengs und quer" unten gilt nur noch fuer die Literatur-Szenarien.

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
from spreewaldring_lap_simulation import (
    simulate_lap, find_corner_min_speeds, TIRE_MU_RANGE, accel_true, coast_true, Vehicle,
    load_vehicles, literature_vehicle,
)
from spreewaldring_racing_line import load_corridor, corridor_bounds, CAR_HALF_WIDTH_M
from drivetrain_model_validation import rpm_from_speed, GEAR_RATIOS
from performance_simulation import REDLINE_RPM


VEHICLES, DEFAULT_TYRE = load_vehicles()
VEHICLE = VEHICLES[DEFAULT_TYRE]  # aus den Logs, aktuell montierter Reifensatz, auch fuer die Optimierung selbst
REDLINE_DOWNSHIFT_MARGIN = 0.97   # nicht bis direkt unter die Redline zurueckschalten, sonst flattert der Gang
SHIFT_MIN_GAIN_S = 0.01           # kleinerer Vorteil rechtfertigt keinen Gangwechsel, siehe plan_gears()
FREE_SHIFT_PENALTY_S = 0.001      # auch kostenlose Wechsel nicht ohne Nutzen
ACCEL_TABLE_DV, ACCEL_TABLE_N = 0.25, 360  # bis 90 m/s, siehe accel_fast()
_ACCEL_TABLES = {}
# Natuerliches Gasmodell, siehe natural_throttle_step(). Lenkrate-Regression aus partial_throttle_calibration.py
APP_STEER_A, APP_STEER_B = 38.4, 2.4
NATURAL_THROTTLE_MIN_G = 0.9
NATURAL_THROTTLE_LOOKAHEAD_N = 10
with open(os.path.join(RESULTS_DIR, "engine_braking_model.json"), encoding="utf-8") as _f:
    _engine_brake = json.load(_f)
ENGINE_BRAKE_V_MS = np.array(_engine_brake["table_v_kmh"]) / 3.6
ENGINE_BRAKE_A_MS2 = np.array(_engine_brake["table_a_engine_ms2"])
with open(os.path.join(RESULTS_DIR, "steering_lateral_model_summary.json"), encoding="utf-8") as _f:
    STEER_K1, STEER_K2 = json.load(_f)["k"]
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
# 02.10.2026 (Nutzerentscheid: Bremsmodell auf den performanteren Semis NS-R2, die Winterreifen
# seit 01.10. bleiben vermutlich dahinter zurueck): aus den CAN-Bremsereignissen
# (can_brake_event_analysis.py, LongitudinalAcc_CAN/Tempoabfall r=0,964) der NS-R2-Fahrten bis
# einschliesslich candump-2026-10-01_142300 - haerteste ABS-Bremsung >= 1,5 s:
# candump-2026-09-15_171047 t=1962 s, 112->57 km/h, 0,762 g (Tempoabfall; IMU 0,79 g). Das formale
# Maximum 0,802 g ist ein 0,78-s-Ereignis bei 45 km/h, als Dauerwert fuer Bremszonen zu kurz.
# Vorher 0,4772 g aus Handy/OBD-Daten (deutlich unterschaetzt).
BRAKE_CAP_G = 0.762
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


def as_vehicle(mu):
    """Vehicle aus mx5_vehicle_model.py, oder eine Zahl als fruehere isotrope mu-Annahme."""
    return mu if isinstance(mu, Vehicle) else literature_vehicle(mu)


def lon_avail(a_lat, veh, lon_g):
    """Verbleibende Laengsbeschleunigung auf der Ellipse quer/laengs, bei lat_g == lon_g der klassische Kraftkreis."""
    ratio = min(a_lat / (veh.lat_g * G), 1.0)
    return lon_g * G * math_sqrt_nonneg(1.0 - ratio * ratio)


def best_gear_for_speed(v, veh):
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
    candidates = {g: accel_true(v, g, veh.speed_factor)[0] for g in GEAR_RATIOS
                  if rpm_from_speed(v / veh.speed_factor, g) <= REDLINE_RPM}
    if not candidates:
        return max(GEAR_RATIOS)  # selbst der hoechste Gang liegt schon ueber Redline
    return max(candidates, key=candidates.get)


def accel_fast(v, gear, speed_factor):
    """accel_true() fuer den Gangplan: Tabelle je Rahmen, ~50x schneller, Abweichung unter 0,1 %."""
    table = _ACCEL_TABLES.get(speed_factor)
    if table is None:
        table = {g: [accel_true(k * ACCEL_TABLE_DV, g, speed_factor)[0] for k in range(ACCEL_TABLE_N + 1)]
                 for g in GEAR_RATIOS}
        _ACCEL_TABLES[speed_factor] = table
    x = min(v / ACCEL_TABLE_DV, ACCEL_TABLE_N - 1e-9)
    k = int(x)
    row = table[gear]
    return row[k] + (x - k) * (row[k + 1] - row[k])


def plan_gears(limit, radius, ds, veh):
    """Gangplan fuer die ganze Runde: je Punkt und Gang die beste Zeit, Bleiben und jeder Wechsel sind Uebergaenge
    (dynamische Programmierung). Sieht, was eine einzelne Schaltung spaeter kostet: Vorher kostete der Wechsel in
    Gang 1 mehr als er brachte (Befund 07.11.2026: 31 Schaltungen, 253 m Gang 1, ohne Gang 1 schneller). Verzoegert das
    Auto ohnehin, ist Schalten kostenlos. Je Punkt und Gang bleiben alle Ankuenfte, die keine andere zugleich frueher
    und schneller schlaegt (Pareto-Front Zeit/Tempo). Mit nur der fruehesten Ankunft (bis 08.11.2026) gewann im
    naechsten Gang immer die gerade erst geschaltete Variante knapp, die frueher geschaltete und schon schnellere fiel
    raus: mit dem Drehzahl-Limiter blieb die Simulation so im 3. Gang bei 137 km/h haengen. limit: Tempogrenze je Punkt,
    rotiert ab dem langsamsten Punkt (dort ist der Gang frei). Liefert (Startgang, {Punkt: (neuer Gang, Luecke)})."""
    n = len(limit)
    f = veh.speed_factor
    gears = sorted(GEAR_RATIOS)
    lat_max, lon_max = veh.lat_g * G, veh.accel_g * G
    labels = [{} for _ in range(n + 1)]  # labels[i][gear] = [(Bewertung, v, Punkt, Gang, Vorgaenger-Label), ...]
    for g in gears:
        if rpm_from_speed(limit[0] / f, g) < REDLINE_RPM:
            labels[0][g] = [(0.0, limit[0], 0, g, None)]

    def relax(i, g, score, v, prev):
        front = labels[i].setdefault(g, [])
        if any(s <= score and w >= v for s, w, *_ in front):
            return
        front[:] = [lab for lab in front if not (score <= lab[0] and v >= lab[1])]
        front.append((score, v, i, g, prev))

    def drive_a(v, g, i_next):
        ratio = min(v * v / max(radius[i_next % n], 1e-6) / lat_max, 1.0)
        return min(accel_fast(v, g, f), lon_max * math.sqrt(1.0 - ratio * ratio))

    for i in range(n):
        seg, lim_next = ds[i], limit[(i + 1) % n]
        for sweep in (gears, gears[::-1]):  # kostenlose Wechsel am selben Punkt, auch ueber mehrere Gaenge
            for g in sweep:
                for lab in list(labels[i].get(g, ())):
                    if lim_next >= lab[1] - 1e-6:
                        continue
                    for cand in (g - 1, g + 1):
                        if cand in GEAR_RATIOS and rpm_from_speed(lab[1] / f, cand) <= REDLINE_RPM * REDLINE_DOWNSHIFT_MARGIN:
                            relax(i, cand, lab[0] + FREE_SHIFT_PENALTY_S, lab[1], lab)
        for g, front in list(labels[i].items()):
            for lab in front:
                score, v = lab[0], lab[1]
                v_new = min(math_sqrt_nonneg(v * v + 2 * drive_a(v, g, i + 1) * seg), lim_next)
                if g == 6 or rpm_from_speed(v_new / f, g) <= REDLINE_RPM:  # sonst muss vorher hochgeschaltet sein
                    relax(i + 1, g, score + 2 * seg / max(v + v_new, 1e-6), v_new, lab)
                for cand in (g + 1, g - 1):
                    if cand not in GEAR_RATIOS or (cand < g and rpm_from_speed(v / f, cand) > REDLINE_RPM * REDLINE_DOWNSHIFT_MARGIN):
                        continue
                    # Zugkraftluecke rollen, dann im neuen Gang weiter
                    remaining, s, j, vv = veh.shift_s[(g, cand)], score + SHIFT_MIN_GAIN_S, i, v
                    while remaining > 0 and j < n:
                        seg_j = ds[j]
                        seg_t = seg_j / max(vv, 1.0)
                        frac = min(1.0, remaining / seg_t)
                        a = frac * coast_true(vv, f) + (1 - frac) * drive_a(vv, cand, j + 1)
                        v2 = min(math_sqrt_nonneg(vv * vv + 2 * a * seg_j), limit[(j + 1) % n])
                        s += 2 * seg_j / max(vv + v2, 1e-6)
                        remaining -= seg_t
                        vv = v2
                        j += 1
                    relax(j, cand, s, vv, lab)

    lab = min((lab for front in labels[n].values() for lab in front), key=lambda lab: lab[0])
    shifts = {}
    while lab[4] is not None:
        prev = lab[4]
        # Mehrere kostenlose Wechsel am selben Punkt: Rueckwaerts zuerst gefunden ist der Endgang
        if prev[3] != lab[3] and prev[2] not in shifts:
            shifts[prev[2]] = (lab[3], 0.0 if prev[2] == lab[2] else veh.shift_s[(prev[3], lab[3])])
        lab = prev
    return lab[3], shifts


def forward_step_shifted(v, gear, shift_remaining_s, shift_target, ds, radius, veh):
    """Ein distanzbasierter Vorwaerts-Schritt im Gang, mit Zugkraftluecke und Hochschalten an der Redline (siehe
    Modul-Docstring, Nachtrag Schaltzeiten). Den Gangplan macht plan_gears(). Liefert (v_neu, gear_neu,
    shift_remaining_neu, shift_target) OHNE die Kurvengeschwindigkeits-Deckelung - die wendet der Aufrufer an."""
    factor = veh.speed_factor
    if shift_remaining_s > 0:
        a_coast = coast_true(v, factor)
        v_full = math_sqrt_nonneg(v ** 2 + 2 * a_coast * ds)
        t_full = 2 * ds / (v + v_full) if (v + v_full) > 1e-9 else 0.0
        if t_full <= shift_remaining_s:
            # Schaltvorgang dauert laenger als dieser Schritt - Fahrzeug
            # rollt fuer die GESAMTE Schrittdistanz noch ohne Antriebskraft
            return v_full, gear, shift_remaining_s - t_full, shift_target
        # Schaltvorgang endet MITTEN in diesem Schritt (konstante
        # Rollverzoegerung angenommen, siehe Docstring)
        t = shift_remaining_s
        v_end = max(v + a_coast * t, 0.0)
        ds_used = v * t + 0.5 * a_coast * t * t
        return forward_step_shifted(v_end, shift_target, 0.0, shift_target, max(ds - ds_used, 0.0), radius, veh)

    a_cur, rpm = accel_true(v, gear, factor)
    if rpm >= REDLINE_RPM and gear < 6:  # Sicherheitsnetz, der Gangplan schaltet vorher
        gap = veh.shift_s[(gear, gear + 1)]
        if gap > 0:
            return forward_step_shifted(v, gear, gap, gear + 1, ds, radius, veh)
        gear, a_cur = gear + 1, accel_true(v, gear + 1, factor)[0]

    a_lat = min(v ** 2 / max(radius, 1e-6), veh.lat_g * G)
    a_use = min(a_cur, lon_avail(a_lat, veh, veh.accel_g))
    return math_sqrt_nonneg(v ** 2 + 2 * a_use * ds), gear, 0.0, gear


def smooth_wasted_accel_brake(v_bwd, v_fwd, v_corner_o, ds_o, veh, min_hold_m=MIN_ACCEL_HOLD_M):
    """Ersetzt kurze Vollgas-Inseln, die von Bremszonen eingerahmt sind
    (Laenge < min_hold_m), durch eine konstante Teillast-Beschleunigung, die
    exakt auf die bereits vom Rueckwaertspass berechnete Einfahrtsgeschwindigkeit
    der naechsten Bremszone einschwenkt - siehe MIN_ACCEL_HOLD_M fuer den
    Hintergrund. Laesst laengere (echte) Beschleunigungszonen unangetastet.
    Die erforderliche Beschleunigung darf auch leicht NEGATIV sein (bis
    coast_true(), also bis zum voelligen Schliessen der Drosselklappe) -
    das deckt kurze Inseln ab, die eigentlich ein sanftes Verzoegern statt
    eines Beschleunigens brauchen, weiterhin rein ueber das Gaspedal statt
    ueber die Bremse (Nutzerbeobachtung 08.09.2026: "0% Gas" ist real nicht
    dasselbe wie "keine Kraft am Rad"). Liefert (v, coasting, throttle_frac):
    veraenderte Punkte und deren Anteil der Radkraft zwischen Rollen und Vollgas."""
    n = len(v_bwd)
    braking = v_bwd < v_fwd - 1e-3
    v_out = v_bwd.copy()
    coasting = np.zeros(n, dtype=bool)
    throttle_frac = np.ones(n)
    if not braking.any() or braking.all():
        return v_out, coasting, throttle_frac  # keine oder durchgehende Bremsung - nichts zu tun
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
            a_max = accel_true(v_start, best_gear_for_speed(v_start, veh), veh.speed_factor)[0]
            a_min = coast_true(v_start, veh.speed_factor)
            if a_min <= a_req <= a_max:
                frac = (a_req - a_min) / (a_max - a_min) if a_max > a_min else 0.0
                s_local = 0.0
                for m in range(k, j):
                    i2 = order[m]
                    s_local += ds_o[i2]
                    v_smooth = math_sqrt_nonneg(v_start ** 2 + 2 * a_req * s_local)
                    v_out[i2] = min(v_smooth, v_corner_o[i2])
                    coasting[i2] = True
                    throttle_frac[i2] = min(1.0, max(0.0, frac))
        k = j
    return v_out, coasting, throttle_frac


def smooth_short_brake_spikes(v, v_fwd, v_corner_o, ds_o, min_hold_m=MIN_ACCEL_HOLD_M,
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
    zwischen zwei frei rollenden Punkten ist real nicht so ausfuehrbar.
    Liefert (v, brake_frac), 1 = unveraendert."""
    n = len(v)
    braking = v < v_fwd - 1e-3
    v_out = v.copy()
    brake_frac = np.ones(n)
    if not braking.any() or braking.all():
        return v_out, brake_frac
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
            # Rueckwaerts nur soweit, wie das Tempo dort nicht faellt: sonst laeuft die Verlaengerung in die
            # vorige Beschleunigungsrampe und v_start > v_end bricht die Glaettung ab (Bugfund 08.09.2026)
            far_k = k - 1
            best_far_k = k - 1
            ext_len = arc_len
            while ext_len < min_hold_m and far_k >= 0 and not braking[order[far_k]]:
                if v[order[far_k]] < v[order[best_far_k]]:
                    break
                ext_len += ds_o[order[far_k]]
                best_far_k = far_k
                far_k -= 1
            v_start = v[order[best_far_k]]
            if v_start > v_end and ext_len > 1e-6:
                a_req = (v_start ** 2 - v_end ** 2) / (2 * ext_len)
                if a_req <= brake_cap_g * G:
                    s_local = 0.0
                    for m in range(far_k + 1, j):  # j ist das Inselende, auch wenn es ueber die Rotationsnaht geht
                        i2 = order[m]
                        s_local += ds_o[i2]
                        v_smooth = math_sqrt_nonneg(v_start ** 2 - 2 * a_req * s_local)
                        v_out[i2] = min(v_smooth, v_corner_o[i2])
                        brake_frac[i2] = min(1.0, a_req / (brake_cap_g * G)) if a_req > 1e-6 else 0.0
        k = j
    return v_out, brake_frac


def coast_in_gear(v, veh):
    """Rollen mit geschlossener Kupplung und eingelegtem Gang: coast_true() plus Motorbremse
    (Tabelle aus 397 realen Schub-Ereignissen, engine_braking_analysis.py), in VehicleSpeed bestimmt."""
    f = veh.speed_factor
    return coast_true(v, f) + float(np.interp(v / f, ENGINE_BRAKE_V_MS, ENGINE_BRAKE_A_MS2)) * f


def steer_from_radius(radius_m, veh):
    """Umkehrung des Lenkmodells: Lenkwinkel-Betrag [Grad] fuer einen Radius, unabhaengig vom Tempo
    (omega = (k1+k2*|d|)*|d|*v_VehicleSpeed, R = v_wahr/omega)."""
    target = (180 / math.pi) * veh.speed_factor / np.maximum(radius_m, 1e-6)
    disc = np.maximum(STEER_K1 ** 2 + 4 * STEER_K2 * target, 0.0)
    return np.abs((-STEER_K1 + np.sqrt(disc)) / (2 * STEER_K2))


def natural_throttle_step(v, gear, shift_remaining_s, shift_target, ds, i_target, veh, ctx):
    """Wie forward_step_shifted(), nur statt Vollgas die Lenkrate-Teillast ("natuerliches Gasmodell").
    EXPERIMENTELL und ausserhalb der einen Kalibrierkurve nicht validiert (spreewaldring_natural_throttle_check.py,
    throttle_steering_rate_aggregate.py: bei harter Kurvenfahrt R^2=0,04), Vergleichswerkzeug, kein Ersatz.
    Die Vorausschau rollt ab dem Punkt aus (appFrac=0) statt konstantes Tempo anzunehmen; die Bisektion sucht das
    meiste Gas, bei dem die Haftgrenze im Fenster nirgends reisst, und kann die Formel nur anheben (Nutzer 09.09.2026)."""
    radius_o, ds_o, d_steer_ds_o, app_frac_o = ctx
    n = len(radius_o)
    factor = veh.speed_factor
    if shift_remaining_s > 0:
        a_coast = coast_true(v, factor)
        v_full = math_sqrt_nonneg(v ** 2 + 2 * a_coast * ds)
        t_full = 2 * ds / (v + v_full) if (v + v_full) > 1e-9 else 0.0
        if t_full <= shift_remaining_s:
            return v_full, gear, shift_remaining_s - t_full, shift_target
        t = shift_remaining_s
        v_end = max(v + a_coast * t, 0.0)
        ds_used = v * t + 0.5 * a_coast * t * t
        return natural_throttle_step(v_end, shift_target, 0.0, shift_target, max(ds - ds_used, 0.0), i_target, veh, ctx)

    a_lat_max = veh.lat_g * G
    a_lat = min(v ** 2 / max(radius_o[i_target], 1e-6), a_lat_max)
    a_long_avail = lon_avail(a_lat, veh, veh.accel_g)
    a_cur, rpm = accel_true(v, gear, factor)
    if rpm >= REDLINE_RPM and gear < 6:
        app_frac_o[i_target] = 0.0  # Kupplung offen, sonst bliebe hier der Anfangswert 1 stehen (Bugfund 09.09.2026)
        return natural_throttle_step(v, gear, veh.shift_s[(gear, gear + 1)], gear + 1, ds, i_target, veh, ctx)

    def overloads_ahead(v_start):
        v_test = v_start
        for k in range(1, NATURAL_THROTTLE_LOOKAHEAD_N + 1):
            j, j_prev = (i_target + k) % n, (i_target + k - 1) % n
            v_test = math_sqrt_nonneg(v_test ** 2 + 2 * coast_in_gear(v_test, veh) * ds_o[j_prev])
            if v_test ** 2 / max(radius_o[j], 1e-6) > a_lat_max:
                return True
        return False

    a_max = min(a_cur, a_long_avail)
    a_min = coast_in_gear(v, veh)
    app_frac = 1.0
    if a_lat / a_lat_max > NATURAL_THROTTLE_MIN_G or overloads_ahead(v):
        opening_rate = -d_steer_ds_o[i_target] * v  # >0 = Kurve oeffnet sich
        app_frac = min(1.0, max(0.0, (APP_STEER_A + APP_STEER_B * opening_rate) / 100))

        def safe_at(frac):
            return not overloads_ahead(math_sqrt_nonneg(v ** 2 + 2 * (a_min + frac * (a_max - a_min)) * ds))

        if safe_at(1.0):
            app_frac = 1.0
        else:
            lo, hi = 0.0, 1.0
            for _ in range(12):
                mid = (lo + hi) / 2
                if safe_at(mid):
                    lo = mid
                else:
                    hi = mid
            app_frac = max(app_frac, lo)
    app_frac_o[i_target] = app_frac
    return math_sqrt_nonneg(v ** 2 + 2 * (a_min + app_frac * (a_max - a_min)) * ds), gear, 0.0, gear


def simulate_lap_full(points, mu, natural=False, n_passes=2, brake_cap_g=None):
    """Die eine Rundensimulation fuer Skripte und Editor: Zweipass-Loeser mit Grip-Ellipse, echtem Gang und
    Zugkraftluecken (siehe Modul-Docstring). mu: Vehicle (aus den Logs, mx5_vehicle_model.py) oder eine Zahl
    (fruehere isotrope Annahme). brake_cap_g: zusaetzliche Obergrenze fuers Bremsen, bei einem Vehicle steckt sie
    schon in brake_g. Liefert alle Groessen je Punkt in Streckenreihenfolge."""
    veh = as_vehicle(mu)
    n = len(points)
    ds = np.hypot(*(np.roll(points, -1, axis=0) - points).T)
    step_m = float(np.median(ds))
    radius = compute_curvature_radius(points, CURVATURE_WINDOW_M, step_m)
    a_lat_max = veh.lat_g * G
    v_corner = np.sqrt(np.maximum(a_lat_max * radius, V_MIN_MS ** 2))

    i0 = int(np.argmin(v_corner))
    order = np.roll(np.arange(n), -i0)
    v_corner_o = v_corner[order]
    ds_o = ds[order]
    radius_o = radius[order]

    app_frac_o = np.ones(n)
    natural_ctx = None
    if natural:
        steer_o = steer_from_radius(radius_o, veh)
        w = max(1, round(CURVATURE_WINDOW_M / float(np.median(ds_o))))
        ds_twice = np.r_[ds_o, ds_o, ds_o]
        cum = np.r_[0.0, np.cumsum(ds_twice)]
        ip = (np.arange(n) - w) % n
        span = cum[ip + 2 * w] - cum[ip]  # Bogenlaenge ip..ip+2w, zyklisch
        d_steer_ds_o = (np.roll(steer_o, -w) - np.roll(steer_o, w)) / np.maximum(span, 1e-6)
        natural_ctx = (radius_o, ds_o, d_steer_ds_o, app_frac_o)

    ds_list, radius_list = ds_o.tolist(), radius_o.tolist()

    def forward(v_prev, limit, shifts, gear, remaining, target, i_target, i_prev):
        """Fahrt vom vorigen zu diesem Punkt nach Gangplan. Der Gang folgt dem echten Tempo: aus dem Vorlauf begrenzt,
        sonst wuerde er in Bremszonen ein Tempo sehen, das das Auto nicht faehrt."""
        v_now = min(v_prev, limit[i_prev])
        planned = shifts.get(i_prev)
        if planned is not None and remaining <= 0 and planned[0] != gear:
            if natural:
                app_frac_o[i_target] = 0.0  # Kupplung offen, siehe natural_throttle_step()
            if planned[1] > 0:
                remaining, target = planned[1], planned[0]
            else:
                gear, target = planned[0], planned[0]
        if natural:
            return natural_throttle_step(v_now, gear, remaining, target, ds_o[i_prev], i_target, veh, natural_ctx)
        return forward_step_shifted(v_now, gear, remaining, target, ds_o[i_prev], radius_o[i_target], veh)

    v = v_corner_o.copy()
    gear_o = np.ones(n, dtype=int)

    def brake_back(v_in):
        """Rueckwaertspass: bremsen bis zur Tempogrenze des naechsten Punkts."""
        v_out = v_in.copy()
        for i in range(n - 2, -1, -1):
            a_lat = min(v_out[i + 1] ** 2 / max(radius_o[i], 1e-6), a_lat_max)
            a_brake = lon_avail(a_lat, veh, veh.brake_g)
            if brake_cap_g is not None:
                a_brake = min(a_brake, brake_cap_g * G)
            v_out[i] = min(v_out[i], math.sqrt(v_out[i + 1] ** 2 + 2 * a_brake * ds_o[i]))
        a_lat_last = min(v_out[0] ** 2 / max(radius_o[-1], 1e-6), a_lat_max)
        a_brake_last = lon_avail(a_lat_last, veh, veh.brake_g)
        if brake_cap_g is not None:
            a_brake_last = min(a_brake_last, brake_cap_g * G)
        v_out[-1] = min(v_out[-1], math.sqrt(v_out[0] ** 2 + 2 * a_brake_last * ds_o[-1]))
        return v_out

    # Kurven und Bremsen allein, ohne die eigenen Schaltungen: sonst pendelt der Plan von Durchlauf zu Durchlauf
    limit = brake_back(v_corner_o).tolist()
    start_gear, shifts = plan_gears(limit, radius_list, ds_list, veh)
    for _ in range(n_passes):
        v_fwd = v.copy()
        gear = start_gear
        gear_o[0] = gear
        shift_remaining, shift_target = 0.0, gear
        for i in range(1, n):
            v_kin, gear, shift_remaining, shift_target = forward(v_fwd[i - 1], limit, shifts, gear, shift_remaining,
                                                                  shift_target, i, i - 1)
            v_fwd[i] = min(v_corner_o[i], v_kin)
            gear_o[i] = gear
        v_kin0, *_ = forward(v_fwd[-1], limit, shifts, gear, shift_remaining, shift_target, 0, n - 1)
        v_fwd[0] = min(v_corner_o[0], v_kin0, v_fwd[0])

        v = brake_back(v_fwd)

    # Einmalig NACH der Konvergenz ueber n_passes anwenden (nicht innerhalb
    # der Schleife) - sonst wuerde der naechste Vorwaerts-Pass wieder mit
    # Vollgas von den geglaetteten Werten aus starten und dieselbe Spitze
    # neu erzeugen. Siehe MIN_ACCEL_HOLD_M/smooth_wasted_accel_brake().
    v, coasting_o, throttle_frac_o = smooth_wasted_accel_brake(v, v_fwd, v_corner_o, ds_o, veh)
    v, brake_frac_o = smooth_short_brake_spikes(v, v_fwd, v_corner_o, ds_o,
                                                 brake_cap_g=brake_cap_g if brake_cap_g is not None else veh.brake_g)

    def natural_order(values):
        out = np.empty(n, dtype=values.dtype)
        out[order] = values
        return out

    v_final = natural_order(v)
    gear_trace = natural_order(gear_o)
    t_nat = np.r_[0.0, np.cumsum(ds / v_final)][:n]
    # Schaltungen der letzten Vorwaertsfahrt: (Beginn, Luecke, von, nach), fuer die Kupplungsanzeige
    shift_events = sorted((float(t_nat[order[i]]), gap, int(gear_o[i]), int(g))
                          for i, (g, gap) in shifts.items() if gear_o[i] != g)
    return {
        "v": v_final, "v_fwd": natural_order(v_fwd), "radius": radius, "ds": ds,
        "lap_time_s": float(np.sum(ds / v_final)), "gear": gear_trace,
        "rpm": np.array([rpm_from_speed(v_i / veh.speed_factor, g) for v_i, g in zip(v_final, gear_trace)]),
        "s": np.r_[0.0, np.cumsum(ds)][:n], "t": t_nat,
        "coasting": natural_order(coasting_o), "throttle_frac": natural_order(throttle_frac_o),
        "brake_frac": natural_order(brake_frac_o), "throttle_frac_natural": natural_order(app_frac_o),
        "natural": natural, "shift_events": shift_events,
    }


def simulate_lap_combined_friction(points, mu, n_passes=2, brake_cap_g=None):
    """Kurzform von simulate_lap_full(): (v, Rundenzeit, Radius)."""
    sim = simulate_lap_full(points, mu, n_passes=n_passes, brake_cap_g=brake_cap_g)
    return sim["v"], sim["lap_time_s"], sim["radius"]


def optimize_alternating(center, lo, hi, perp, veh=VEHICLE, n_outer=N_OUTER,
                          inner_steps=INNER_STEPS, base_alpha=BASE_ALPHA):
    n_lat = np.zeros(len(center))
    snapshots = []

    for outer in range(n_outer):
        points = center + n_lat[:, None] * perp
        v, lap_time, radius = simulate_lap_combined_friction(points, veh)

        a_lat_req = v ** 2 / np.maximum(radius, 1e-6)
        excess = np.clip(a_lat_req / (veh.lat_g * G), 0.0, 1.0)
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
          f"innere Glaettungsschritte, {VEHICLE.name} aus den Logs ...")
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

    for name, veh in VEHICLES.items():
        _, lap_time_veh, _ = simulate_lap_combined_friction(line_resampled, veh)
        print(f"\n--- gemessen {name} (Ellipse, Schaltluecken, wahres Tempo): {lap_time_veh:.2f} s ---")
        results[f"gemessen {name}"] = {"lap_time_optimal_s": lap_time_veh}

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_optimal_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "vehicle_used_for_optimization": VEHICLE.name,
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
