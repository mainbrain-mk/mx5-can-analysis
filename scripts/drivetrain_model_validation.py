"""
Validierung des Antriebsstrang-/Fahrleistungsmodells gegen echte
Beschleunigungsdaten (MX-5 Projekt)

Zweck: der Nutzer hat ein separat entwickeltes Fahrleistungsmodell
("MX5_Aktueller_Kenntnisstand_2026-08-29.md") zur Verfuegung gestellt -
Getriebe-/Achsuebersetzungen, Volllast-Drehmomentkurve, Masse, CdA,
Rollwiderstand, Wirkungsgrad. CdA und Wirkungsgrad sind dort explizit
als "gekoppelt plausibilisiert, nicht unabhaengig identifiziert"
markiert - eigener offener Punkt 1 in diesem Dokument.

Wir haben die noetigen Kanaele (APP, ETC_ACT, EngineRPM, TM_GEST,
VehicleSpeed), um dieses Modell gegen echte Volllast-Beschleunigungs-
phasen zu pruefen, OHNE neue Daten aufnehmen zu muessen.

ERWEITERT (29.08.2026): liest jetzt aus `data/datalake.duckdb` (siehe
build_datalake.py) statt nur aus den 7 urspruenglichen .dlg-Dateien -
damit fliessen automatisch auch die 20 vom Nutzer bereitgestellten
historischen CSV-Logs mit ein (insgesamt 27 Logs, davon 19 mit
APP-Kanal fuer den Volllast-Check). Kanalnamen sind im Datalake bereits
ueber alle Quellformate normalisiert (deutsche/englische Original-
Spaltennamen, bar/kPa, ft/m - siehe build_datalake.py), dieses Skript
muss sich um Quellformat-Unterschiede nicht mehr kuemmern.

Zwei unabhaengige Checks:
  1. KINEMATISCH (rein geometrisch, unabhaengig von Motor/Aero/Reibung):
     Drehzahl aus (Geschwindigkeit, Gang, Radradius, Uebersetzungen)
     vorhersagen und mit der gemessenen EngineRPM vergleichen. Prueft
     nur r_dyn/Getriebeuebersetzungen/Achsuebersetzung - diese sind im
     Dokument als "validiert" markiert, hier zusaetzlich empirisch
     gegengeprueft.
  2. DYNAMISCH: fuer erkannte Volllast-Segmente (APP>90%, siehe
     Dokument-Definition der "haptischen Pedalraste", konstanter Gang,
     Geschwindigkeit steigend) die tatsaechliche mittlere Beschleunigung
     (aus OBD-Geschwindigkeitsanstieg) mit der vom Modell vorhergesagten
     Beschleunigung vergleichen:
       F_motor = Drehmoment(RPM) * Ganguebersetzung * Achsuebersetzung
                 * eta / r_dyn
       F_luft  = 0.5 * rho * CdA * v^2
       F_roll  = Crr * m * g
       a_modell = (F_motor - F_luft - F_roll) / m
     Das Drehmoment kommt aus der im Dokument gegebenen
     Volllast-Kennlinie (linear interpoliert). Damit wird effektiv
     genau der im Dokument offene Punkt 1 (CdA/eta-Entkopplung)
     getestet: stimmt die Vorhersage insgesamt, oder gibt es einen
     systematischen Bias (der auf zu niedriges/hohes CdA oder eta
     hindeuten wuerde)?

WICHTIGE EINSCHRAENKUNGEN:
  - Die Teillast-Drehmomentkurve ist nicht Teil des Dokuments -
    validierbar sind nur echte Volllast-Segmente (APP>90%).
  - Fahrbahnsteigung wird nicht berücksichtigt (kein Neigungssensor/
    Streckendaten) - unebene/abschuessige Strecken verzerren einzelne
    Segmente.
  - Windverhaeltnisse (Gegenwind/Rueckenwind) unbekannt - koennen den
    Luftwiderstandsterm pro Fahrt leicht verschieben.
  - a_long aus der IMU wird hier bewusst NICHT verwendet (siehe
    braking_model.py: a_long zeigt bei starken Laengsbeschleunigungen
    vermutlich eine aehnliche Nick-/Squat-Schwingung wie beim Bremsen) -
    die Referenzbeschleunigung kommt ausschliesslich aus dem OBD-
    Geschwindigkeitsverlauf (robuster gegen dieses Problem, wenn auch
    durch 1 km/h-Aufloesung/~3Hz limitiert - fuer Segmente von mehreren
    Sekunden Dauer ausreichend).

Aufruf: python drivetrain_model_validation.py
"""
import os
import json
import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
MASS_OVERRIDE_PATH = "data/log_mass_overrides.json"

# --- Fahrzeugparameter aus MX5_Aktueller_Kenntnisstand_2026-08-29.md ---
MASS_KG = 1180.705

# Log-spezifische Massen-Overrides (Leergewicht 1073kg + Fahrer + Tankstand
# weichen vom MASS_KG-Referenzwert ab, siehe PROJEKT_STAND.md
# "Fahrzeuggewicht"). Fahrer 86kg (nutzerbestaetigt). Tankstand aus
# geloggtem FLI-Kanal (Mittel Start/Ende-Wert je Fahrt, Tankvolumen 45l
# nutzerbestaetigt, Dichte Benzin ~0.745 kg/l angenommen).
# SEIT 12.09.2026: aus MASS_OVERRIDE_PATH geladen statt hier fest verdrahtet
# (siehe dortige "note"-Felder fuer die frueheren Inline-Kommentare) - damit
# koennen neue Massen per JSON-Update eingetragen werden, ohne Source-Code
# in zwei Dateien synchron zu editieren.
with open(MASS_OVERRIDE_PATH, encoding="utf-8") as _f:
    LOG_MASS_OVERRIDE_KG = {k: v["mass_kg"] for k, v in json.load(_f).items()}

R_DYN_M = 0.2985
FINAL_DRIVE = 2.866
GEAR_RATIOS = {1: 5.087, 2: 2.991, 3: 2.035, 4: 1.594, 5: 1.286, 6: 1.000}
ETA = 0.93
CDA_M2 = 0.647
RHO_KG_M3 = 1.18
CRR = 0.013
G = 9.81

RPM_TABLE = np.array([1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500,
                       5000, 5500, 6000, 6500, 7000, 7200, 7400, 7500])
TORQUE_TABLE_NM = np.array([115, 140, 165, 185, 198, 203, 205, 204,
                             202, 198, 193, 189, 184, 178, 172, 169])

APP_WOT_MIN = 90.0       # Dokument: "haptische Pedalraste"
ETC_WOT_MIN = 80.0       # Fallback-Kriterium wenn APP-Kanal fehlt, siehe unten
LAMBDA_WOT_MAX = 0.9     # Nutzer-Vorgabe (29.08.2026): ETC_ACT allein ist KEIN
                         # verlaesslicher WOT-Indikator - ETC_ACT kann schon
                         # ~86 deg erreichen, deutlich bevor APP 80% erreicht.
                         # Erst fette Gemischanreicherung (Lambda/AFR_MZ < 0.9,
                         # Bauteilschutz bei echter Volllast) bestaetigt WOT
                         # zuverlaessig. AFR_MZ ist im Datalake bereits als
                         # Lambda-aehnlicher Wert um 1.0 skaliert (siehe
                         # Wertebereich in den Logs, nicht die absolute AFR).
MIN_SEGMENT_DURATION_S = 1.5
MIN_SPEED_MS = 3.0        # sehr geringe Geschwindigkeit ausschliessen (Anfahren/Kupplung)


def torque_nm(rpm):
    return np.interp(rpm, RPM_TABLE, TORQUE_TABLE_NM,
                      left=TORQUE_TABLE_NM[0], right=TORQUE_TABLE_NM[-1])


def rpm_from_speed(v_ms, gear):
    wheel_rps = v_ms / (2 * np.pi * R_DYN_M)
    return wheel_rps * GEAR_RATIOS[gear] * FINAL_DRIVE * 60.0


def model_accel(v_ms, gear, mass_kg=MASS_KG):
    rpm = rpm_from_speed(v_ms, gear)
    torque = torque_nm(rpm)
    f_wheel = torque * GEAR_RATIOS[gear] * FINAL_DRIVE * ETA / R_DYN_M
    f_drag = 0.5 * RHO_KG_M3 * CDA_M2 * v_ms ** 2
    f_roll = CRR * mass_kg * G
    return (f_wheel - f_drag - f_roll) / mass_kg, rpm, torque


def load_channel(con, log_id, channel):
    """Liefert Spalten ['t','value'] (t = t_elapsed_s) fuer einen Kanal
    eines Logs aus dem Datalake, sortiert, ohne NaN.

    ORDER BY nur nach t_elapsed_s reicht NICHT: manche Logs haben fuer denselben
    normalisierten Kanal zwei Rohspalten (z.B. "Motordrehzahl (RPM)" und
    "Engine Revolutions Per Minute (RPM)" fuer EngineRPM, siehe NAME_ALIASES in
    build_datalake.py), die auf denselben Zeitstempel fallen - teils mit
    identischen, teils (z.B. bei 2026-08-25 081538/EngineRPM: eine der beiden
    Spalten ist ueber die ganze Fahrt fast durchgehend 0) unterschiedlichen
    Werten. DuckDB garantiert bei gleichem t_elapsed_s KEINE stabile Reihenfolge
    (haengt von Thread-Scheduling der parallelen Ausfuehrung ab) - das machte
    downstream np.interp()/Schwellenwert-Vergleiche (Gang-Inferenz etc.)
    nicht-deterministisch. channel_original+value als Tiebreaker ergaenzt macht
    die Reihenfolge vollstaendig deterministisch."""
    df = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL "
        "ORDER BY t_elapsed_s, channel_original, value",
        [log_id, channel],
    ).fetchdf()
    return df


GEAR_INFER_MAX_RATIO_ERROR = 0.03  # 3% - deutlich enger als der ~6-9% Modell-Bias


def infer_gear_from_rpm_speed(v_ms, rpm):
    """Leitet den Gang direkt aus dem RPM/Speed-Verhaeltnis ab, statt aus
    TM_GEST zu lesen - fuer Logs mit defektem/fehlendem Gang-Kanal (siehe
    Log 2026-08-25 081538, wo ALLE DREI vorhandenen Gang-Quellen -
    TM_GEST aus zwei sich widersprechenden Rohspalten UND
    TransmissionActualGearRatio - eingefroren/unbrauchbar sind).

    Physikalisch robust bei einem manuellen, direkt gekoppelten Getriebe
    (kein Wandlerschlupf): implizites Uebersetzungsverhaeltnis =
    rpm * 2*pi*R_DYN_M / (60 * v * FINAL_DRIVE), dann der naechstliegende
    der 6 bekannten Gaenge. Punkte, die zu keinem Gang nahe genug passen
    (z.B. waehrend eines Schaltvorgangs, wenn die Drehzahl kurz von der
    Geschwindigkeit entkoppelt ist, oder bei Standgas), werden verworfen
    (NaN) statt eines falschen Gangs.

    WICHTIG: NICHT fuer den kinematischen Check (kinematic_check)
    verwenden - das waere zirkulaer, da genau die RPM-aus-Speed+Gang-
    Beziehung ueberprueft werden soll, die hier als Annahme eingeht.
    Fuer den Volllast-/Beschleunigungscheck (wot_segments) ist es
    unproblematisch, da dort eine unabhaengige Groesse (gemessene
    Beschleunigung) gegen das Modell geprueft wird - der Gang dient nur
    zur Auswahl der richtigen Uebersetzung."""
    v_safe = np.maximum(v_ms, 0.5)
    implied_ratio = rpm * 2 * np.pi * R_DYN_M / (60.0 * v_safe * FINAL_DRIVE)
    gears = np.array(list(GEAR_RATIOS.keys()))
    ratios = np.array(list(GEAR_RATIOS.values()))
    rel_err = np.abs(implied_ratio[:, None] - ratios[None, :]) / ratios[None, :]
    best_idx = np.argmin(rel_err, axis=1)
    best_err = rel_err[np.arange(len(implied_ratio)), best_idx]
    gear = gears[best_idx].astype(float)
    gear[best_err > GEAR_INFER_MAX_RATIO_ERROR] = np.nan
    gear[v_ms <= MIN_SPEED_MS] = np.nan
    return gear


def gear_channel_is_reliable(ge_values):
    """Grobe Plausibilitaetspruefung fuer TM_GEST: ein echtes, mehrere
    Minuten langes Log sollte mehrere verschiedene Gaenge durchlaufen.
    Weniger als 3 verschiedene Gaenge (1-6) unter den Nicht-Null-Werten
    ist ein starkes Indiz fuer einen eingefrorenen/defekten Kanal (siehe
    2026-08-25 081538: nur genau 2 Werte ueber die gesamte Fahrt)."""
    nonzero = ge_values[(ge_values >= 1) & (ge_values <= 6)]
    return len(np.unique(np.round(nonzero))) >= 3


def group_runs(mask):
    """Liefert (start_idx, end_idx) fuer zusammenhaengende True-Laeufe."""
    runs = []
    in_run = False
    start = None
    for i, m in enumerate(mask):
        if m and not in_run:
            in_run = True
            start = i
        elif not m and in_run:
            in_run = False
            runs.append((start, i - 1))
    if in_run:
        runs.append((start, len(mask) - 1))
    return runs


def kinematic_check(con, log_id):
    """Vergleicht gemessene EngineRPM mit der aus (Geschwindigkeit, Gang)
    vorhergesagten Drehzahl - reiner Geometrie-Check."""
    sp = load_channel(con, log_id, "VehicleSpeed")
    rp = load_channel(con, log_id, "EngineRPM")
    ge = load_channel(con, log_id, "TM_GEST")
    if len(sp) < 2 or len(rp) < 20 or len(ge) < 2:
        return None

    t_common = rp["t"].values
    v_i = np.interp(t_common, sp["t"].values, sp["value"].values) / 3.6
    g_i = np.round(np.interp(t_common, ge["t"].values, ge["value"].values))
    rpm_meas = rp["value"].values

    valid = (g_i >= 1) & (g_i <= 6) & (v_i > MIN_SPEED_MS) & (rpm_meas > 800)
    if valid.sum() < 20:
        return None

    rpm_pred = np.array([rpm_from_speed(v, int(g)) for v, g in zip(v_i[valid], g_i[valid])])
    err_pct = (rpm_meas[valid] - rpm_pred) / rpm_pred * 100.0
    return {
        "n": int(valid.sum()),
        "mean_error_pct": float(np.mean(err_pct)),
        "std_error_pct": float(np.std(err_pct)),
        "median_abs_error_pct": float(np.median(np.abs(err_pct))),
    }


def _grade_and_tailwind(con, log_id, t_start, t_end, elev_model, wind_cache,
                         lat_full, lon_full, heading_full):
    """Steigung (ueber segment_grade(), inkl. dessen Praezisionspfad aus
    Rohpunktwolken) + Rueckenwind-Komponente (Open-Meteo, wie in
    coastdown_analysis.py) fuer ein Zeitfenster. Liefert (grade_frac,
    tailwind_ms) - grade_frac ist 0.0 wenn keine DGM-Abdeckung, tailwind_ms
    ist 0.0 wenn GPS/Wind-Kanaele fehlen oder die Abfrage fehlschlaegt.
    Lazy-Imports (nicht auf Modulebene) vermeiden einen Zirkelimport:
    top_speed_validation importiert selbst von diesem Modul."""
    from top_speed_validation import segment_grade
    from coastdown_analysis import wind_at, tailwind_component_ms

    grade_frac, _dist_m, _n_gps, _sources = segment_grade(con, log_id, t_start, t_end, elev_model)
    grade_frac = grade_frac if grade_frac is not None else 0.0

    tailwind_ms = 0.0
    if len(lat_full) and len(lon_full) and len(heading_full):
        # alle drei Kanaele kommen aus demselben log_id - "t" ist ueberall
        # dieselbe t_elapsed_s-Basis wie t_start/t_end, keine Epoch-Umrechnung
        # noetig (anders als beim CAN<->OBD-Cross-Log-Abgleich anderswo).
        m = (lat_full["t"] >= t_start) & (lat_full["t"] <= t_end)
        if m.sum() >= 1:
            sub_lat = lat_full.loc[m, "value"].values
            sub_t = lat_full.loc[m, "t"].values
            sub_lon = np.interp(sub_t, lon_full["t"].values, lon_full["value"].values)
            heading_med = float(np.median(np.interp(sub_t, heading_full["t"].values, heading_full["value"].values)))
            lat_med, lon_med = float(np.median(sub_lat)), float(np.median(sub_lon))
            ts_mid = lat_full.loc[m, "timestamp_local"].iloc[len(sub_t) // 2]
            wind_speed_ms, wind_dir_deg = wind_at(ts_mid, lat_med, lon_med, wind_cache)
            if wind_speed_ms is not None:
                tailwind_ms = tailwind_component_ms(heading_med, wind_speed_ms, wind_dir_deg)
    return grade_frac, tailwind_ms


def _build_wot_segments(log_id, t_common, candidate, v_i, g_i, detection,
                         con=None, elev_model=None, wind_cache=None):
    """Gemeinsame Segment-Konstruktion (an Gangwechseln aufteilen, Mindest-
    dauer, ansteigende Geschwindigkeit) fuer beide Erkennungswege.

    MIT Steigungs-/Windkorrektur (2026-09-13, wie bereits in
    top_speed_validation.py fuer den Gang-6-/Vmax-Pfad) wenn con+elev_model
    uebergeben werden - sonst (Rueckwaertskompatibilitaet) nur das flache
    Modell wie bisher. `a_model_ms2` bleibt IMMER das flache Modell (fuer
    Vergleichbarkeit mit alten Laeufen); `a_model_corrected_ms2`/`grade_pct`/
    `tailwind_ms` sind neu und None, wenn keine Korrektur angefordert/moeglich
    war."""
    mass_kg = LOG_MASS_OVERRIDE_KG.get(log_id, MASS_KG)
    use_correction = con is not None and elev_model is not None
    if use_correction:
        from coastdown_analysis import load_channel_with_timestamp
        lat_full = load_channel_with_timestamp(con, log_id, "Breite")
        lon_full = load_channel(con, log_id, "Länge")
        heading_full = load_channel(con, log_id, "Lager")
        if wind_cache is None:
            from coastdown_analysis import _load_wind_cache
            wind_cache = _load_wind_cache()

    results = []
    for start, end in group_runs(candidate):
        sub_start = start
        for i in range(start + 1, end + 2):
            if i > end or g_i[i] != g_i[sub_start]:
                sub_end = i - 1
                if t_common[sub_end] - t_common[sub_start] >= MIN_SEGMENT_DURATION_S:
                    v0, v1 = v_i[sub_start], v_i[sub_end]
                    if v1 > v0:
                        dur = t_common[sub_end] - t_common[sub_start]
                        v_mean = (v0 + v1) / 2.0
                        a_meas = (v1 - v0) / dur
                        gear_here = int(g_i[sub_start])
                        a_mod, rpm_mod, torque_mod = model_accel(v_mean, gear_here, mass_kg)

                        a_mod_corrected, grade_pct, tailwind_ms = None, None, None
                        if use_correction:
                            grade_frac, tailwind_ms = _grade_and_tailwind(
                                con, log_id, float(t_common[sub_start]), float(t_common[sub_end]),
                                elev_model, wind_cache, lat_full, lon_full, heading_full)
                            v_air = v_mean - tailwind_ms
                            f_wheel = torque_mod * GEAR_RATIOS[gear_here] * FINAL_DRIVE * ETA / R_DYN_M
                            f_drag = 0.5 * RHO_KG_M3 * CDA_M2 * v_air ** 2
                            f_roll = CRR * mass_kg * G
                            f_grade = mass_kg * G * grade_frac
                            a_mod_corrected = float((f_wheel - f_drag - f_roll - f_grade) / mass_kg)
                            grade_pct = float(grade_frac * 100)

                        results.append({
                            "file": log_id, "detection": detection,
                            "t_start": float(t_common[sub_start]),
                            "t_end": float(t_common[sub_end]), "duration_s": float(dur),
                            "gear": gear_here, "v_start_kmh": float(v0 * 3.6),
                            "v_end_kmh": float(v1 * 3.6), "v_mean_ms": float(v_mean),
                            "a_measured_ms2": float(a_meas), "a_model_ms2": float(a_mod),
                            "a_model_corrected_ms2": a_mod_corrected, "grade_pct": grade_pct,
                            "tailwind_ms": tailwind_ms,
                            "rpm_model": float(rpm_mod), "torque_model_nm": float(torque_mod),
                            "mass_kg_used": float(mass_kg),
                        })
                sub_start = i
    return results


def wot_segments(con, log_id, elev_model=None, wind_cache=None):
    """Erkennt Volllast-Segmente und gibt gemessene vs. modellierte
    Beschleunigung zurueck.

    Primaer ueber APP>90% (Dokument: "haptische Pedalraste"). Fehlt der
    APP-Kanal (z.B. bei den kurzen CSVLog_*-Historienlogs), Fallback auf
    ETC_ACT>80% UND Lambda/AFR_MZ<0.9 (fette Gemischanreicherung) - ETC_ACT
    allein ist laut Nutzer NICHT verlaesslich, da es auch bei Teillast
    schon nahe an den Volllastwert herankommen kann; erst die tatsaechliche
    Anreicherung bestaetigt echte Volllast.

    Der Gang kommt normalerweise aus TM_GEST. Ist TM_GEST unplausibel
    (siehe `gear_channel_is_reliable`, z.B. 2026-08-25 081538 mit nur 2
    eingefrorenen Werten statt eines normalen 1-6-Verlaufs), wird er
    stattdessen aus RPM+Speed rueckgerechnet (`infer_gear_from_rpm_speed`)
    - fuer DIESEN Check unproblematisch, siehe Docstring dort."""
    sp = load_channel(con, log_id, "VehicleSpeed")
    ge = load_channel(con, log_id, "TM_GEST")
    if len(sp) < 2 or len(ge) < 2:
        return []

    ap = load_channel(con, log_id, "APP")
    if len(ap) >= 10:
        t_common = ap["t"].values
        v_i = np.interp(t_common, sp["t"].values, sp["value"].values) / 3.6
        app_i = ap["value"].values

        if gear_channel_is_reliable(ge["value"].values):
            g_i = np.round(np.interp(t_common, ge["t"].values, ge["value"].values))
        else:
            rp = load_channel(con, log_id, "EngineRPM")
            if len(rp) < 10:
                return []
            rpm_i = np.interp(t_common, rp["t"].values, rp["value"].values)
            g_i = infer_gear_from_rpm_speed(v_i, rpm_i)
            print(f"  Hinweis {log_id}: TM_GEST unplausibel, Gang aus RPM/Speed rueckgerechnet")

        candidate = (app_i > APP_WOT_MIN) & (g_i >= 1) & (g_i <= 6) & (v_i > MIN_SPEED_MS)
        candidate &= ~np.isnan(g_i)
        if candidate.any():
            return _build_wot_segments(log_id, t_common, candidate, v_i, g_i, "APP",
                                        con=con, elev_model=elev_model, wind_cache=wind_cache)
        # APP-Kanal vorhanden, aber nie ueber APP_WOT_MIN (z.B. der bekannte
        # kaputte/kaum gepollte APP-Duplikat-PID-Slot, siehe build_datalake.py
        # NAME_ALIASES-Kommentare und mx5_can_bus_logging - Log 2026-09-12 211851
        # hat nur 20 APP-Samples im Bereich 14.5-18.0%, obwohl real Vollgas
        # gefahren wurde) - auf den ETC_ACT+Lambda-Fallback ausweichen statt
        # stillschweigend 0 Segmente zu liefern.

    etc = load_channel(con, log_id, "ETC_ACT")
    lam = load_channel(con, log_id, "AFR_MZ")
    if len(etc) < 10 or len(lam) < 10:
        return []
    t_common = etc["t"].values
    v_i = np.interp(t_common, sp["t"].values, sp["value"].values) / 3.6
    g_i = np.round(np.interp(t_common, ge["t"].values, ge["value"].values))
    etc_i = etc["value"].values
    lam_i = np.interp(t_common, lam["t"].values, lam["value"].values)
    candidate = (
        (etc_i > ETC_WOT_MIN) & (lam_i < LAMBDA_WOT_MAX)
        & (g_i >= 1) & (g_i <= 6) & (v_i > MIN_SPEED_MS)
    )
    return _build_wot_segments(log_id, t_common, candidate, v_i, g_i, "ETC+Lambda",
                                con=con, elev_model=elev_model, wind_cache=wind_cache)


def plot_measured_vs_model(events, out_path):
    a_meas = np.array([e["a_measured_ms2"] for e in events])
    a_mod = np.array([e["a_model_ms2"] for e in events])
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(a_mod, a_meas, s=15, alpha=0.5, color="seagreen")
    lim = max(a_mod.max(), a_meas.max()) * 1.1
    ax.plot([0, lim], [0, lim], "--", color="gray", lw=1, label="perfekte Uebereinstimmung")
    ax.set_xlabel("Modell-Beschleunigung [m/s²]")
    ax.set_ylabel("Gemessene Beschleunigung (OBD) [m/s²]")
    ax.set_title("Volllast-Beschleunigung: Modell vs. Messung")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    log_ids = con.execute("SELECT log_id FROM logs ORDER BY log_id").fetchdf()["log_id"].tolist()

    print(f"=== 1. Kinematischer Check: Drehzahl aus Geschwindigkeit+Gang vs. gemessen ({len(log_ids)} Logs) ===")
    for log_id in log_ids:
        res = kinematic_check(con, log_id)
        if res is None:
            print(f"{log_id}: zu wenige Datenpunkte (RPM/Speed/Gang nicht ausreichend vorhanden)")
            continue
        print(f"{log_id}: n={res['n']:5d}  mittlerer Fehler={res['mean_error_pct']:+.2f}%  "
              f"Std={res['std_error_pct']:.2f}%  Median|Fehler|={res['median_abs_error_pct']:.2f}%")

    print("\n=== 2. Volllast-Beschleunigung: Modell vs. Messung ===")
    from elevation_model import ElevationModel
    from coastdown_analysis import _load_wind_cache
    elev_model = ElevationModel()
    wind_cache = _load_wind_cache()

    all_events = []
    for log_id in log_ids:
        events = wot_segments(con, log_id, elev_model=elev_model, wind_cache=wind_cache)
        all_events.extend(events)
        if events:
            method = events[0]["detection"]
            crit = (f"APP>{APP_WOT_MIN:.0f}%" if method == "APP"
                    else f"ETC_ACT>{ETC_WOT_MIN:.0f}% & Lambda<{LAMBDA_WOT_MAX}")
            print(f"{log_id}: {len(events)} Volllast-Segmente ({method}: {crit}, "
                  f">= {MIN_SEGMENT_DURATION_S}s, konstanter Gang)")
    con.close()

    if not all_events:
        print("Keine Volllast-Segmente gefunden.")
        return

    n_app = sum(1 for e in all_events if e["detection"] == "APP")
    n_etc = len(all_events) - n_app
    print(f"\n(davon {n_app} ueber APP, {n_etc} ueber ETC_ACT+Lambda-Fallback erkannt)")

    a_meas = np.array([e["a_measured_ms2"] for e in all_events])
    a_mod = np.array([e["a_model_ms2"] for e in all_events])
    ratio = a_meas / a_mod
    corr = np.corrcoef(a_meas, a_mod)[0, 1]

    print(f"\n=== Gesamtstatistik ueber {len(all_events)} Volllast-Segmente ===")
    print(f"Korrelation gemessen vs. Modell: {corr:.2f}")
    print(f"Verhaeltnis gemessen/Modell: Median={np.median(ratio):.2f}  "
          f"Mittel={np.mean(ratio):.2f}  Std={np.std(ratio):.2f}")
    print(f"{'Gang':>4s} {'n':>4s} {'a_mess Med':>10s} {'a_mod Med':>10s} {'Verh. Med':>9s}")
    for gear in sorted(set(e["gear"] for e in all_events)):
        sub = [e for e in all_events if e["gear"] == gear]
        am = np.array([e["a_measured_ms2"] for e in sub])
        ao = np.array([e["a_model_ms2"] for e in sub])
        print(f"{gear:4d} {len(sub):4d} {np.median(am):10.2f} {np.median(ao):10.2f} "
              f"{np.median(am/ao):9.2f}")

    # NEU (2026-09-13): mit Steigungs-/Windkorrektur (segment_grade() +
    # Open-Meteo-Wind, wie beim Gang-6-/Vmax-Pfad in top_speed_validation.py) -
    # nur ueber Segmente mit GPS/DGM-Abdeckung, a_model_ms2 (flach) bleibt fuer
    # alle Segmente unveraendert erhalten.
    corrected = [e for e in all_events if e.get("a_model_corrected_ms2") is not None]
    if corrected:
        am_c = np.array([e["a_measured_ms2"] for e in corrected])
        ao_flat_c = np.array([e["a_model_ms2"] for e in corrected])
        ao_corr_c = np.array([e["a_model_corrected_ms2"] for e in corrected])
        ratio_flat_c = am_c / ao_flat_c
        ratio_corr_c = am_c / ao_corr_c
        print(f"\n=== Mit Steigungs-/Windkorrektur ({len(corrected)}/{len(all_events)} Segmente mit "
              f"GPS/DGM-Abdeckung) ===")
        print(f"Verhaeltnis gemessen/Modell FLACH:      Median={np.median(ratio_flat_c):.2f}  "
              f"Std={np.std(ratio_flat_c):.2f}")
        print(f"Verhaeltnis gemessen/Modell KORRIGIERT: Median={np.median(ratio_corr_c):.2f}  "
              f"Std={np.std(ratio_corr_c):.2f}")
        print(f"{'Gang':>4s} {'n':>4s} {'Verh.flach Med':>14s} {'Verh.korr Med':>14s}")
        for gear in sorted(set(e["gear"] for e in corrected)):
            sub = [e for e in corrected if e["gear"] == gear]
            rf = np.array([e["a_measured_ms2"] / e["a_model_ms2"] for e in sub])
            rc = np.array([e["a_measured_ms2"] / e["a_model_corrected_ms2"] for e in sub])
            print(f"{gear:4d} {len(sub):4d} {np.median(rf):14.2f} {np.median(rc):14.2f}")

    plot_measured_vs_model(all_events, os.path.join(RESULTS_DIR, "drivetrain_model_vs_measured.png"))
    print("\nStreudiagramm: results/drivetrain_model_vs_measured.png")

    with open(os.path.join(RESULTS_DIR, "drivetrain_model_validation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(all_events, f, indent=2, ensure_ascii=False)
    print("Details: results/drivetrain_model_validation_summary.json")


if __name__ == "__main__":
    main()
