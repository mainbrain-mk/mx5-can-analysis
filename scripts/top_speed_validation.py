"""
Validierung des Antriebsstrangmodells im Gang-6-/Vmax-Bereich, inkl.
Steigungskorrektur (MX-5 Projekt)

Nutzeranfrage: "suche in den Logs nach Situationen, bei denen wir im 6.
Gang bis Vmax fahren, validiere den Bereich im Modell, beachte ggf. das
Gefaelle." Anders als der bestehende Volllast-Check in
drivetrain_model_validation.py (kurze, ansteigende Segmente ueber alle
Gaenge, Mindestdauer 1.5s) sucht dieses Skript gezielt LANGE, SCHNELLE
Gang-6-WOT-Phasen (Mindestdauer 3s, muss mindestens einmal ueber
TOP_SPEED_MIN_KMH liegen) - inkl. Phasen, in denen die Geschwindigkeit
bereits (nahezu) stagniert, also effektiv am Vmax-Punkt fuer die
jeweilige Steigung/Gefaelle gefahren wird. Bisher n=3 fuer Gang 6 im
bestehenden Check ("weniger belastbar") - hier deutlich gezielter.

WOT-Erkennung wie wot_segments() in drivetrain_model_validation.py (APP>90%,
Fallback ETC_ACT>80% & Lambda/AFR_MZ<0.9), aber ohne die dortige Anforderung
"Geschwindigkeit steigt" (fuer Vmax-Naehe irrelevant/kontraproduktiv), mit
laengerer Mindestdauer, UND einem dritten Fallback "ETC_only" (ETC_ACT>=85 Grad
allein) fuer die kurzen CSVLog_*-Historienlogs, die WEDER APP NOCH AFR_MZ
enthalten (nur BFP_PRE_MZ/Breite/ETC_ACT/EngineRPM/Laenge/TM_GEST/VehicleSpeed).
ETC allein ist laut bestehender Nutzer-Vorgabe KEIN verlaesslicher WOT-Nachweis -
"ETC_only"-Segmente werden entsprechend deutlich gekennzeichnet (Diagnosewert,
nicht unabhaengig bestaetigt), mit strengerem Schwellwert (85 statt 80 Grad)
als Kompromiss.

Steigungskorrektur: verwendet das bestehende DGM (elevation_model.py,
`get_elevation_along_track`, bruecken-sicher). Fuer jedes Segment werden
die GPS-Fixe (Breite/Laenge) im Zeitfenster verwendet (gefiltert auf
Horz Genauigkeit <= GPS_MAX_HORZ_ACC_M, wie im externen IMU-Methodendokument
gefordert), daraus mittlere Steigung (Hoehenaenderung / zurueckgelegte
GPS-Strecke) berechnet und als zusaetzlicher Gravitationsterm
a_gravity = -g * grade_frac auf die Modellbeschleunigung addiert
(kleine Winkel, grade_frac ~ tan(theta) ~ sin(theta), fuer realistische
Strassenneigungen ausreichend genau). Segmente ausserhalb der
Kachelabdeckung (keine gueltige Hoehe an Start/Ende) werden nur ohne
Steigungskorrektur ausgewertet (geflaggt).

Gemessene Beschleunigung: lineare Regression von VehicleSpeed(t) ueber
das GANZE Segment (robuster als Endpunkt-Differenz, wichtig hier, weil
die Beschleunigung nahe Vmax klein ist und dann von 1-km/h-OBD-
Quantisierungsrauschen dominiert wuerde).

Aufruf: .venv/bin/python scripts/top_speed_validation.py
"""
import os
import json

import duckdb
import numpy as np
import pandas as pd
import pyproj
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import brentq

from drivetrain_model_validation import (
    DB_PATH, G, MASS_KG, LOG_MASS_OVERRIDE_KG, APP_WOT_MIN, ETC_WOT_MIN,
    LAMBDA_WOT_MAX, MIN_SPEED_MS,
    load_channel, group_runs, gear_channel_is_reliable, infer_gear_from_rpm_speed,
)
from performance_simulation import accel, EMPIRICAL_BIAS_FACTOR
from elevation_model import (
    ElevationModel, get_or_build_trip_bridge_cache, is_near_bridge_latlon, get_raw_ground_points,
)

# Bruecken-Erkennung ausserhalb Brandenburgs/Berlins (dort bereits ueber
# ElevationModel/get_elevation_along_track abgedeckt): fuer Bayern/
# Thueringen/Sachsen-Anhalt gibt es KEINE Kachel-eigene Bruecken-Logik -
# stattdessen ein eigener, corridor-weiter OSM-Bruecken-Cache (lat/lon-basiert,
# bundeslandunabhaengig). Ohne diese Filterung ergaben sich bei kurzen
# Hochgeschwindigkeitssegmenten in Bayern/Sachsen-Anhalt vereinzelt absurde
# Gefaelle-Werte (bis +13%), die sich als Bruecken-/Ueberfuehrungs-Artefakte
# herausstellten (DGM zeigt dort das Gelaende UNTER der Bruecke) - siehe
# docs/logs/projekt-stand.md.
TRIP_BRIDGE_BBOX = (47.5, 11.0, 52.9, 13.2)
_trip_bridges_cache = None


def _trip_bridges():
    global _trip_bridges_cache
    if _trip_bridges_cache is None:
        _trip_bridges_cache = get_or_build_trip_bridge_cache(TRIP_BRIDGE_BBOX)
    return _trip_bridges_cache

RESULTS_DIR = "results"
TOP_SPEED_MIN_DURATION_S = 3.0
TOP_SPEED_MIN_KMH = 170.0       # Segment muss dieses Tempo mind. einmal erreichen
GPS_MAX_HORZ_ACC_M = 20.0       # aus docs/status/performance-model.md, Abschnitt 9
ETC_ONLY_MIN = 85.0             # strengerer WOT-Schwellwert fuer den ETC-only-Fallback, siehe unten
MAX_PLAUSIBLE_GRADE = 0.06       # 6% - Autobahn-Gefaelle darueber ist praktisch ausgeschlossen,
                                 # siehe segment_grade() Docstring (Bruecken-Artefakt-Schutz)
STEP_GRADE_MAX = 0.15            # 15% - Einzelschritt-Schwelle fuer den physikalischen
                                 # Plausibilitaetsfilter (Bruecken-Sprung-Erkennung), siehe unten
MIN_CLEAN_RUN_FRACTION = 0.6      # Segment wird verworfen, wenn nach Bruecken-/Sprung-
                                 # Ausschluss kein Abschnitt >=60% der Punkte uebrig bleibt

# Praezisions-Pfad (siehe _precise_grade_from_raw_points): native ALS-
# Bodenpunkte (~12-15 Pkt/m2, aus scripts/crest_profile_wot_2026_09_12.py
# generalisiert) statt 1Hz-GPS gegen das 1m-Cache-Raster. Nur innerhalb
# Brandenburgs mit lokaler als_*.zip (166/169 Kacheln) - siehe get_raw_
# ground_points(). Engerer Korridor als noetig waere riskant (Nachbarspur/
# Rampe faelschlich mit drin, siehe mx5_bidirectional_wot_validation.md),
# daher bewusst schmal.
PRECISE_CORRIDOR_HALF_WIDTH_M = 8.0
PRECISE_BIN_SIZE_M = 1.0
PRECISE_SMOOTH_WINDOW_M = 60.0
PRECISE_MIN_DIST_M = 50.0         # kuerzere Mindestlaenge als beim 1m-Raster-Pfad (200m) -
                                 # die native Punktdichte macht auch kuerzere Segmente
                                 # noch robust auswertbar (siehe Docstring unten)

_LATLON_TO_UTM33 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)


def find_vmax_graded(gear, bias, grade_frac, mass_kg=MASS_KG, v_lo=1.0, v_hi=130.0, n=260):
    """Wie find_vmax() in performance_simulation.py, nur mit zusaetzlichem
    konstanten Gravitationsterm (Steigung/Gefaelle) im Kraeftegleichgewicht."""
    vs = np.linspace(v_lo, v_hi, n)
    a_vals = np.array([accel(v, gear, bias, mass_kg)[0] - G * grade_frac for v in vs])
    sign_changes = np.where(np.diff(np.sign(a_vals)) < 0)[0]
    if len(sign_changes) == 0:
        return None
    i = sign_changes[0]
    return brentq(lambda v: accel(v, gear, bias, mass_kg)[0] - G * grade_frac, vs[i], vs[i + 1])


# Bekannte Abweichungen von der Projekt-Referenzmasse (MASS_KG) fuer
# einzelne Logs (siehe Memory "mx5-weight-check-on-new-logs" +
# docs/logs/projekt-stand.md). Suedtirol-Rueckfahrt 31.07.2026: Leergewicht 1073kg +
# Fahrer 86kg (nutzerbestaetigt) + Beifahrer 70kg + Gepaeck 50kg + Tank
# ~20kg (ANNAHME, halb voll im Mittel ueber die Fahrt, NICHT bestaetigt) =
# 1299kg. Alle 7 Logs dieser Fahrt (29.07.-31.07.2026) betroffen.
SUEDTIROL_TRIP_LOGS = {
    "CSVLog_20260729_171909", "CSVLog_20260731_061727", "CSVLog_20260731_070928",
    "CSVLog_20260731_090658", "CSVLog_20260731_141854", "CSVLog_20260731_151724",
    "CSVLog_20260731_175702",
}
SUEDTIROL_TRIP_MASS_KG = 1299.0

# Allgemeiner Massen-Override pro Log (ergaenzt die Suedtirol-Sonderfahrt
# oben) - SEIT 12.09.2026 aus drivetrain_model_validation importiert
# (dort aus data/log_mass_overrides.json geladen), keine eigene Kopie mehr.


def wot_gear6_candidate_mask(con, log_id, t_common, v_i):
    """WOT-Kriterium identisch zu wot_segments() (drivetrain_model_validation.py),
    aber ohne Anforderung steigender Geschwindigkeit - liefert (candidate, g_i, detection)
    oder None, falls nicht genug Daten."""
    ge = load_channel(con, log_id, "TM_GEST")
    if len(ge) < 2:
        return None

    if gear_channel_is_reliable(ge["value"].values):
        g_i = np.round(np.interp(t_common, ge["t"].values, ge["value"].values))
    else:
        rp = load_channel(con, log_id, "EngineRPM")
        if len(rp) < 10:
            return None
        rpm_i = np.interp(t_common, rp["t"].values, rp["value"].values)
        g_i = infer_gear_from_rpm_speed(v_i, rpm_i)

    gear6 = (g_i == 6)
    if not gear6.any():
        return None

    ap = load_channel(con, log_id, "APP")
    if len(ap) >= 10:
        app_i = np.interp(t_common, ap["t"].values, ap["value"].values)
        candidate = (app_i > APP_WOT_MIN) & gear6 & (v_i > MIN_SPEED_MS)
        if candidate.any():
            return candidate, g_i, "APP"
        # siehe drivetrain_model_validation.wot_segments(): derselbe kaputte/
        # kaum gepollte APP-Duplikat-PID-Slot kann hier auch das Gang-6-Vmax-
        # Segment lautlos verschlucken - auf die Faellbacks ausweichen statt
        # 0 Segmente zurueckzugeben.

    etc = load_channel(con, log_id, "ETC_ACT")
    lam = load_channel(con, log_id, "AFR_MZ")
    if len(etc) >= 10 and len(lam) >= 10:
        etc_i = np.interp(t_common, etc["t"].values, etc["value"].values)
        lam_i = np.interp(t_common, lam["t"].values, lam["value"].values)
        candidate = (etc_i > ETC_WOT_MIN) & (lam_i < LAMBDA_WOT_MAX) & gear6 & (v_i > MIN_SPEED_MS)
        return candidate, g_i, "ETC+Lambda"

    # Letzter Fallback fuer die kurzen CSVLog_*-Historienlogs (nur 7 Kanaele:
    # BFP_PRE_MZ/Breite/ETC_ACT/EngineRPM/Laenge/TM_GEST/VehicleSpeed - weder
    # APP noch AFR_MZ vorhanden). ETC_ACT allein ist laut Nutzer-Vorgabe KEIN
    # verlaesslicher WOT-Nachweis (siehe drivetrain_model_validation.py) -
    # deshalb hier ein deutlich strengerer Schwellwert (nahe dem dokumentierten
    # ETC_open~86 Grad, statt der laxeren 80 Grad des ETC+Lambda-Fallbacks) UND
    # klare Kennzeichnung "ETC_only" in den Ergebnissen als NICHT unabhaengig
    # bestaetigtes WOT (nur Sichtung/Diagnose, siehe Docstring).
    if len(etc) >= 10:
        etc_i = np.interp(t_common, etc["t"].values, etc["value"].values)
        candidate = (etc_i >= ETC_ONLY_MIN) & gear6 & (v_i > MIN_SPEED_MS)
        return candidate, g_i, "ETC_only"

    return None


def _precise_grade_from_raw_points(sub_lat, sub_lon):
    """Native-Praezisions-Steigung aus rohen ALS-Bodenpunkten, generalisiert
    aus scripts/crest_profile_wot_2026_09_12.py (dort: bidirektionale Vollgas-
    Fahrt 2026-09-12, siehe mx5_bidirectional_wot_validation.md). Nutzt die
    eigene GPS-Spur DIESES Segments als Mittellinie (stueckweise linear durch
    die Fixe) + einen engen 8m-Korridor, statt eine 1Hz-GPS-Positionsabfrage
    gegen das 1m-Cache-Raster - vermeidet damit sowohl die groebere
    Rasterabtastung als auch (bei einer gemeinsamen Mittellinie mehrerer
    Fahrten) eine versehentliche Vermischung mit der Gegenfahrbahn.

    Positionsachse: die Achse (Ost- oder Nordkoordinate) mit der GROESSEREN
    Spannweite ueber das Segment - fuer die in diesem Projekt bisher
    gefahrenen, weitgehend geraden Autobahnabschnitte eine gute Naeherung an
    die tatsaechliche Bogenlaenge, ohne eine vollstaendige Punkt-auf-Polygon-
    Projektion zu brauchen. Das Vorzeichen der Steigung wird danach explizit
    auf die tatsaechliche Fahrtrichtung (erster vs. letzter Punkt entlang
    dieser Achse) zurueckgedreht.

    Liefert (grade_frac, dist_m, n_gps_points, sources) wie segment_grade(),
    oder None falls nicht anwendbar (fehlende Rohpunktwolken-Abdeckung fuer
    eine der beruehrten Kacheln, zu kurzes Segment, zu wenige Punkte im
    Korridor) - der Aufrufer faellt dann auf den bestehenden 1m-Raster-Pfad
    zurueck."""
    if len(sub_lat) < 3:
        return None
    e, n = _LATLON_TO_UTM33.transform(sub_lon, sub_lat)
    e, n = np.asarray(e), np.asarray(n)

    span_e, span_n = e.max() - e.min(), n.max() - n.min()
    use_e_axis = span_e >= span_n
    pos, lat_coord = (e, n) if use_e_axis else (n, e)
    travel_sign = 1.0 if pos[-1] >= pos[0] else -1.0

    order = np.argsort(pos)
    pos_sorted, lat_sorted = pos[order], lat_coord[order]
    if pos_sorted[-1] - pos_sorted[0] < PRECISE_MIN_DIST_M:
        return None

    e_km_lo, e_km_hi = int(e.min() // 1000), int(e.max() // 1000)
    n_km_lo, n_km_hi = int(n.min() // 1000), int(n.max() // 1000)
    all_pos, all_lat_coord, all_z = [], [], []
    for e_km in range(e_km_lo, e_km_hi + 1):
        for n_km in range(n_km_lo, n_km_hi + 1):
            pts = get_raw_ground_points(e_km, n_km)
            if pts is None:
                return None  # eine benoetigte Kachel hat keine rohe Punktwolke - kompletter Fallback
            p_e, p_n, p_z = pts["e"], pts["n"], pts["z"]
            p_pos, p_lat = (p_e, p_n) if use_e_axis else (p_n, p_e)
            all_pos.append(p_pos)
            all_lat_coord.append(p_lat)
            all_z.append(p_z)
    p_pos = np.concatenate(all_pos)
    p_lat = np.concatenate(all_lat_coord)
    p_z = np.concatenate(all_z)

    margin = 50.0
    in_range = (p_pos >= pos_sorted[0] - margin) & (p_pos <= pos_sorted[-1] + margin)
    p_pos, p_lat, p_z = p_pos[in_range], p_lat[in_range], p_z[in_range]
    centerline_lat = np.interp(p_pos, pos_sorted, lat_sorted)
    corridor = np.abs(p_lat - centerline_lat) <= PRECISE_CORRIDOR_HALF_WIDTH_M
    p_pos, p_z = p_pos[corridor], p_z[corridor]
    if len(p_pos) < 50:
        return None

    s = p_pos - p_pos.min()  # NICHT p_pos-pos_sorted[0]: der margin-Puffer laesst
                              # p_pos.min() unter pos_sorted[0] fallen, sonst waeren
                              # die ersten Bins negativ und np.digitize wirft sie alle
                              # in Bin 0 - bins[-1] (Python-Indexing!) statt bins[0]
    bins = np.arange(0, s.max() + PRECISE_BIN_SIZE_M, PRECISE_BIN_SIZE_M)
    bin_idx = np.digitize(s, bins)
    df = pd.DataFrame({"bin": bin_idx, "z": p_z})
    grouped = df.groupby("bin")["z"].median()
    s_centers = bins[grouped.index.to_numpy() - 1] + PRECISE_BIN_SIZE_M / 2
    elev_raw = grouped.to_numpy()

    win = max(3, int(PRECISE_SMOOTH_WINDOW_M / PRECISE_BIN_SIZE_M) | 1)
    elev_s = pd.Series(elev_raw).rolling(win, center=True, min_periods=max(3, win // 4)).mean().to_numpy()
    valid = ~np.isnan(elev_s)
    s_centers, elev_s = s_centers[valid], elev_s[valid]
    if len(s_centers) < 5 or s_centers[-1] - s_centers[0] < PRECISE_MIN_DIST_M:
        return None

    grade_vs_pos = np.gradient(elev_s, s_centers)
    if np.any(np.abs(grade_vs_pos) > MAX_PLAUSIBLE_GRADE):
        implausible = np.abs(grade_vs_pos) > MAX_PLAUSIBLE_GRADE
        idx = np.arange(len(s_centers))
        elev_clean = elev_s.copy()
        elev_clean[implausible] = np.nan
        if (~implausible).sum() < 5:
            return None
        elev_clean = np.interp(idx, idx[~implausible], elev_clean[~implausible])
        elev_s = pd.Series(elev_clean).rolling(win, center=True, min_periods=max(3, win // 4)).mean().to_numpy()
        valid2 = ~np.isnan(elev_s)
        s_centers, elev_s = s_centers[valid2], elev_s[valid2]
        if len(s_centers) < 5:
            return None

    grade_frac = float(np.polyfit(s_centers, elev_s, 1)[0]) * travel_sign
    dist_m = float(s_centers[-1] - s_centers[0])
    return grade_frac, dist_m, len(sub_lat), {"Brandenburg-ALS-praezise"}


def segment_grade(con, log_id, t_start, t_end, elev_model):
    """Mittlere Steigung (grade_frac ~ sin(theta)) ueber die per GPS
    zurueckgelegte Strecke im Zeitfenster [t_start, t_end]. Liefert
    (grade_frac, distance_m, n_gps_points, sources) oder (None, None, n, set())
    falls nicht auswertbar (zu wenige gueltige GPS-Fixe oder keine
    Kachelabdeckung).

    Primaer Brandenburg/Berlin (bruecken-sicheres get_elevation_along_track).
    Fuer Punkte, die dort NaN bleiben (z.B. Fahrten ausserhalb der ueblichen
    Berlin-Brandenburg-Schleife, siehe Suedtirol-Rueckfahrt 31.07.2026),
    Fallback auf get_elevation_germany_wide() (Bayern/Thueringen/Sachsen-
    Anhalt) - OHNE Bruecken-Behandlung dort (nicht implementiert fuer diese
    Bundeslaender, siehe docs/logs/projekt-stand.md - Einzelfehler an Bruecken/
    Ueberfuehrungen dort moeglich, nicht ausgeschlossen)."""
    lat = load_channel(con, log_id, "Breite")
    lon = load_channel(con, log_id, "Länge")
    acc = load_channel(con, log_id, "Horz Genauigkeit")
    if len(lat) < 2 or len(lon) < 2:
        return None, None, 0, set()

    m = (lat["t"] >= t_start) & (lat["t"] <= t_end)
    sub_t = lat.loc[m, "t"].values
    sub_lat = lat.loc[m, "value"].values
    sub_lon = np.interp(sub_t, lon["t"].values, lon["value"].values)
    if len(acc) >= 2:
        sub_acc = np.interp(sub_t, acc["t"].values, acc["value"].values)
        ok = sub_acc <= GPS_MAX_HORZ_ACC_M
        sub_lat, sub_lon = sub_lat[ok], sub_lon[ok]

    if len(sub_lat) < 2 or (sub_lat == 0).all():
        return None, None, len(sub_lat), set()

    valid = sub_lat != 0
    sub_lat, sub_lon = sub_lat[valid], sub_lon[valid]
    if len(sub_lat) < 2:
        return None, None, len(sub_lat), set()

    precise = _precise_grade_from_raw_points(sub_lat, sub_lon)
    if precise is not None:
        return precise

    e, n = _LATLON_TO_UTM33.transform(sub_lon, sub_lat)
    elev, _ = elev_model.get_elevation_along_track(np.asarray(e), np.asarray(n))
    elev = np.array(elev, dtype=np.float64, copy=True)
    dist = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(e), np.diff(n)))))

    sources = set()
    non_bb_mask = np.zeros(len(sub_lat), dtype=bool)
    if np.isnan(elev).any():
        for i in np.where(np.isnan(elev))[0]:
            v, src = elev_model.get_elevation_germany_wide(float(sub_lat[i]), float(sub_lon[i]))
            if src is not None:
                elev[i] = v
                sources.add(src)
                non_bb_mask[i] = True
    if (~np.isnan(elev)).any() and not sources:
        sources.add("Brandenburg-ALS/Berlin-WMS")

    # Bruecken-Ausschluss NUR fuer die neuen Bundeslaender (Brandenburg/Berlin
    # sind bereits ueber get_elevation_along_track bruecken-sicher). Deckt nur
    # Bruecken ab, die in OSM tatsaechlich mit bridge=yes getaggt sind -
    # bekanntlich unvollstaendig (siehe docs/logs/projekt-stand.md, Anschlussstelle
    # Stolpe) - deshalb zusaetzlich der physikalische Plausibilitaetsfilter
    # unten, der davon UNABHAENGIG ist.
    if non_bb_mask.any():
        bridge_hit = is_near_bridge_latlon(sub_lat[non_bb_mask], sub_lon[non_bb_mask], _trip_bridges())
        idx = np.where(non_bb_mask)[0]
        elev[idx[bridge_hit]] = np.nan

    # Physikalischer Plausibilitaetsfilter (unabhaengig von OSM-Tagging):
    # ein abrupter Hoehensprung zwischen zwei aufeinanderfolgenden GPS-Punkten
    # (~1Hz), der eine Steigung > STEP_GRADE_MAX ergaebe, ist bei einer realen
    # Autobahnfahrt physikalisch ausgeschlossen - typischerweise eine
    # Bruecke/Ueberfuehrung, bei der das DGM das Gelaende UNTER der Bruecke
    # zeigt statt der Fahrbahn (empirisch bestaetigt an mehreren Bayern-
    # Segmenten, z.B. Sprung 669.75m->612.48m ueber nur ~40m Fahrstrecke -
    # eindeutig eine Talbruecke, keine reale Steigung). Trennt an solchen
    # Spruengen und behaelt nur das LAENGSTE zusammenhaengende Teilstueck.
    valid_elev = np.where(~np.isnan(elev))[0]
    if len(valid_elev) >= 3:
        d_valid = dist[valid_elev]
        e_valid = elev[valid_elev]
        step_grade = np.abs(np.diff(e_valid)) / np.maximum(np.diff(d_valid), 1e-6)
        breaks = np.where(step_grade > STEP_GRADE_MAX)[0]
        if len(breaks) > 0:
            runs = np.split(np.arange(len(valid_elev)), breaks + 1)
            best_run = max(runs, key=lambda r: d_valid[r[-1]] - d_valid[r[0]])
            if len(best_run) / len(valid_elev) < MIN_CLEAN_RUN_FRACTION:
                # zu zerstueckelt (mehrere Sprung-Cluster, kein eindeutig
                # dominanter sauberer Abschnitt) - Segment als nicht
                # verlaesslich gefaellekorrigierbar verwerfen statt eine
                # moeglicherweise noch kontaminierte Teilstrecke zu nutzen
                return None, float(dist[-1]), len(sub_lat), sources | {"VERWORFEN_ZU_ZERSTUECKELT"}
            keep = valid_elev[best_run]
            drop = np.setdiff1d(valid_elev, keep)
            elev[drop] = np.nan

    valid_elev = np.where(~np.isnan(elev))[0]
    if len(valid_elev) < 3:
        return None, float(dist[-1]), len(sub_lat), sources

    d_span = dist[valid_elev[-1]] - dist[valid_elev[0]]
    if d_span < 200.0:
        # zu kurz UND zu wenige GPS-Punkte (~1Hz) fuer eine belastbare
        # Steigungsschaetzung per Zweipunkt-Differenz - insbesondere in
        # bergigem Gelaende (Bayern/Thueringen) reicht ein einzelner
        # verrauschter Endpunkt sonst fuer unplausible Kunstwerte (siehe
        # docs/logs/projekt-stand.md)
        return None, float(dist[-1]), len(sub_lat), sources
    # lineare Regression Hoehe vs. zurueckgelegte Strecke ueber ALLE
    # gueltigen Punkte (robuster als reine Zweipunkt-Differenz, siehe oben)
    grade_frac = float(np.polyfit(dist[valid_elev], elev[valid_elev], 1)[0])

    if abs(grade_frac) > MAX_PLAUSIBLE_GRADE and not sources.issubset({"Brandenburg-ALS/Berlin-WMS"}):
        # In Bayern/Sachsen-Anhalt/Thueringen wird KEINE Bruecken-/Ueber-
        # fuehrungserkennung durchgefuehrt (nur fuer Brandenburg/Berlin
        # implementiert, siehe elevation_model.py) - eine Autobahnbruecke
        # ueber ein Tal wuerde sonst als scheinbar extremes Gefaelle
        # erscheinen (das DGM zeigt dort das Gelaende UNTER der Bruecke,
        # nicht die Fahrbahn). >6% ist fuer Autobahnen praktisch
        # ausgeschlossen - wird daher als wahrscheinliches Artefakt verworfen
        # statt als (falscher) Wert verwendet zu werden.
        return None, float(dist[-1]), len(sub_lat), {"UNPLAUSIBEL_VERWORFEN(%.1f%%, %s)" % (
            grade_frac * 100, ",".join(sorted(sources)))}
    return grade_frac, float(dist[-1]), len(sub_lat), sources


def find_top_speed_segments(con, log_id):
    sp = load_channel(con, log_id, "VehicleSpeed")
    if len(sp) < 5:
        return []
    t_common = sp["t"].values
    v_i = sp["value"].values / 3.6

    res = wot_gear6_candidate_mask(con, log_id, t_common, v_i)
    if res is None:
        return []
    candidate, g_i, detection = res

    segments = []
    for start, end in group_runs(candidate):
        dur = t_common[end] - t_common[start]
        if dur < TOP_SPEED_MIN_DURATION_S:
            continue
        v_seg = v_i[start:end + 1]
        if v_seg.max() * 3.6 < TOP_SPEED_MIN_KMH:
            continue
        t_seg = t_common[start:end + 1]
        a_meas = float(np.polyfit(t_seg, v_seg, 1)[0])
        segments.append({
            "log_id": log_id, "detection": detection,
            "t_start": float(t_seg[0]), "t_end": float(t_seg[-1]), "duration_s": float(dur),
            "v_min_kmh": float(v_seg.min() * 3.6), "v_max_kmh": float(v_seg.max() * 3.6),
            "v_mean_ms": float(v_seg.mean()), "a_measured_ms2": a_meas,
        })
    return segments


def evaluate_segment(seg, elev_model, con):
    v_mean = seg["v_mean_ms"]
    grade_frac, dist_m, n_gps, sources = segment_grade(
        con, seg["log_id"], seg["t_start"], seg["t_end"], elev_model)

    out = dict(seg)
    out["grade_pct"] = grade_frac * 100 if grade_frac is not None else None
    out["gps_distance_m"] = dist_m
    out["n_gps_points"] = n_gps
    out["elevation_sources"] = sorted(sources)

    if seg["log_id"] in SUEDTIROL_TRIP_LOGS:
        mass_kg = SUEDTIROL_TRIP_MASS_KG
    else:
        mass_kg = LOG_MASS_OVERRIDE_KG.get(seg["log_id"], MASS_KG)
    out["mass_kg_used"] = mass_kg
    for bias, tag in [(1.0, "raw"), (EMPIRICAL_BIAS_FACTOR, "bias")]:
        a_flat, rpm = accel(v_mean, 6, bias, mass_kg)
        out[f"a_model_flat_{tag}_ms2"] = float(a_flat)
        out[f"rpm_model_{tag}"] = float(rpm)
        if grade_frac is not None:
            a_slope = a_flat - G * grade_frac
            out[f"a_model_slope_adjusted_{tag}_ms2"] = float(a_slope)
            vmax_g = find_vmax_graded(6, bias, grade_frac, mass_kg)
            out[f"vmax_graded_{tag}_kmh"] = vmax_g * 3.6 if vmax_g is not None else None
        else:
            out[f"a_model_slope_adjusted_{tag}_ms2"] = None
            out[f"vmax_graded_{tag}_kmh"] = None
    return out


def print_segment(seg):
    grade_str = f"{seg['grade_pct']:+.2f}%" if seg["grade_pct"] is not None else "unbekannt (keine DGM-Abdeckung)"
    print(f"\n{seg['log_id']}  t={seg['t_start']:.1f}-{seg['t_end']:.1f}s "
          f"({seg['duration_s']:.1f}s)  v={seg['v_min_kmh']:.0f}-{seg['v_max_kmh']:.0f} km/h  "
          f"Erkennung={seg['detection']}  Gefaelle/Steigung={grade_str}")
    if seg.get("elevation_sources"):
        print(f"  Hoehenquelle(n): {', '.join(seg['elevation_sources'])}")
    if seg["detection"] == "ETC_only":
        print("  HINWEIS: WOT nur ueber ETC_ACT, KEINE Lambda-Bestaetigung verfuegbar in diesem Log "
              "(kurzes CSVLog_*-Format) - Diagnosewert, kein unabhaengig bestaetigtes WOT.")
    print(f"  a_gemessen (Regression):        {seg['a_measured_ms2']:+.3f} m/s²")
    print(f"  a_Modell flach (bias-korr.):     {seg['a_model_flat_bias_ms2']:+.3f} m/s²  "
          f"(raw: {seg['a_model_flat_raw_ms2']:+.3f})")
    if seg["a_model_slope_adjusted_bias_ms2"] is not None:
        print(f"  a_Modell +Gefaelle (bias-korr.): {seg['a_model_slope_adjusted_bias_ms2']:+.3f} m/s²  "
              f"(raw: {seg['a_model_slope_adjusted_raw_ms2']:+.3f})")
        vb = seg["vmax_graded_bias_kmh"]
        vr = seg["vmax_graded_raw_kmh"]
        vb_s = f"{vb:.1f}" if vb is not None else "kein Gleichgewicht im Suchbereich"
        vr_s = f"{vr:.1f}" if vr is not None else "kein Gleichgewicht im Suchbereich"
        print(f"  Vmax bei dieser Steigung (bias-korr.): {vb_s} km/h  (raw: {vr_s})")


def plot_comparison(segments, out_path):
    # Bayern-DGM1 ausgeschlossen: Gefaellekorrektur dort trotz Bruecken-
    # Filterung nachweislich unzuverlaessig (verschlechtert die Passung),
    # siehe docs/logs/projekt-stand.md
    with_grade = [s for s in segments if s["a_model_slope_adjusted_bias_ms2"] is not None
                  and "Bayern-DGM1" not in s.get("elevation_sources", [])]
    if not with_grade:
        return
    a_meas = np.array([s["a_measured_ms2"] for s in with_grade])
    a_flat = np.array([s["a_model_flat_bias_ms2"] for s in with_grade])
    a_slope = np.array([s["a_model_slope_adjusted_bias_ms2"] for s in with_grade])

    fig, ax = plt.subplots(figsize=(7, 7))
    lim_lo = min(a_meas.min(), a_flat.min(), a_slope.min()) - 0.1
    lim_hi = max(a_meas.max(), a_flat.max(), a_slope.max()) + 0.1
    ax.plot([lim_lo, lim_hi], [lim_lo, lim_hi], "--", color="gray", lw=1, label="perfekte Uebereinstimmung")
    ax.scatter(a_flat, a_meas, s=30, alpha=0.6, color="indianred", label="Modell ohne Gefaelle")
    ax.scatter(a_slope, a_meas, s=30, alpha=0.6, color="seagreen", label="Modell mit Gefaellekorrektur")
    for af, as_, am in zip(a_flat, a_slope, a_meas):
        ax.plot([af, as_], [am, am], color="gray", lw=0.7, alpha=0.5)
    ax.set_xlabel("Modell-Beschleunigung [m/s²] (Gang 6, bias-korrigiert)")
    ax.set_ylabel("Gemessene Beschleunigung (OBD, Regression) [m/s²]")
    ax.set_title("Gang-6-/Vmax-Segmente: Modell vs. Messung, mit/ohne Gefaellekorrektur\n"
                  "(Bayern-DGM1 ausgeschlossen - unzuverlaessige Gefaelledaten, siehe docs/logs/projekt-stand.md)")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    log_ids = con.execute("SELECT log_id FROM logs ORDER BY log_id").fetchdf()["log_id"].tolist()

    print(f"=== Suche Gang-6-Vmax-Kandidaten (>= {TOP_SPEED_MIN_DURATION_S}s, "
          f">= {TOP_SPEED_MIN_KMH:.0f} km/h) ueber {len(log_ids)} Logs ===")
    raw_segments = []
    for log_id in log_ids:
        segs = find_top_speed_segments(con, log_id)
        raw_segments.extend(segs)
    print(f"{len(raw_segments)} Segmente gefunden in {len(set(s['log_id'] for s in raw_segments))} Logs")

    if not raw_segments:
        print("Keine geeigneten Segmente gefunden.")
        con.close()
        return

    elev_model = ElevationModel()
    segments = [evaluate_segment(s, elev_model, con) for s in raw_segments]
    con.close()

    for seg in segments:
        print_segment(seg)

    with_grade = [s for s in segments if s["grade_pct"] is not None]
    print(f"\n=== Zusammenfassung ueber {len(segments)} Segmente "
          f"({len(with_grade)} mit Gefaelle-/Steigungskorrektur, "
          f"{len(segments) - len(with_grade)} ohne DGM-Abdeckung) ===")

    a_meas = np.array([s["a_measured_ms2"] for s in segments])
    a_flat = np.array([s["a_model_flat_bias_ms2"] for s in segments])
    rmse_flat = float(np.sqrt(np.mean((a_meas - a_flat) ** 2)))
    print(f"RMSE gemessen vs. Modell (bias-korr., OHNE Gefaellekorrektur), alle {len(segments)} Segmente: "
          f"{rmse_flat:.3f} m/s²")

    if with_grade:
        a_meas_g = np.array([s["a_measured_ms2"] for s in with_grade])
        a_flat_g = np.array([s["a_model_flat_bias_ms2"] for s in with_grade])
        a_slope_g = np.array([s["a_model_slope_adjusted_bias_ms2"] for s in with_grade])
        rmse_flat_g = float(np.sqrt(np.mean((a_meas_g - a_flat_g) ** 2)))
        rmse_slope_g = float(np.sqrt(np.mean((a_meas_g - a_slope_g) ** 2)))
        print(f"  Nur die {len(with_grade)} Segmente mit DGM-Abdeckung:")
        print(f"    RMSE ohne Gefaellekorrektur: {rmse_flat_g:.3f} m/s²")
        print(f"    RMSE MIT Gefaellekorrektur:  {rmse_slope_g:.3f} m/s²")

        trusted = [s for s in with_grade if "Bayern-DGM1" not in s.get("elevation_sources", [])]
        if len(trusted) != len(with_grade):
            n_bayern = len(with_grade) - len(trusted)
            am_t = np.array([s["a_measured_ms2"] for s in trusted])
            af_t = np.array([s["a_model_flat_bias_ms2"] for s in trusted])
            as_t = np.array([s["a_model_slope_adjusted_bias_ms2"] for s in trusted])
            rmse_flat_t = float(np.sqrt(np.mean((am_t - af_t) ** 2)))
            rmse_slope_t = float(np.sqrt(np.mean((am_t - as_t) ** 2)))
            print(f"  OHNE die {n_bayern} Bayern-DGM1-Segmente (Gefaellekorrektur dort nachweislich "
                  f"unzuverlaessig, siehe docs/logs/projekt-stand.md) - {len(trusted)} verbleibende Segmente:")
            print(f"    RMSE ohne Gefaellekorrektur: {rmse_flat_t:.3f} m/s²")
            print(f"    RMSE MIT Gefaellekorrektur:  {rmse_slope_t:.3f} m/s²")
            improvement_t = (1 - rmse_slope_t / rmse_flat_t) * 100 if rmse_flat_t > 0 else 0.0
            print(f"    -> Gefaellekorrektur {'verbessert' if improvement_t > 0 else 'verschlechtert'} "
                  f"die Uebereinstimmung um {abs(improvement_t):.0f}%")
        improvement = (1 - rmse_slope_g / rmse_flat_g) * 100 if rmse_flat_g > 0 else 0.0
        print(f"  Alle {len(with_grade)} Segmente INKL. Bayern-DGM1: Gefaellekorrektur "
              f"{'verbessert' if improvement > 0 else 'verschlechtert'} "
              f"die Uebereinstimmung um {abs(improvement):.0f}%")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    plot_comparison(segments, os.path.join(RESULTS_DIR, "top_speed_validation.png"))
    with open(os.path.join(RESULTS_DIR, "top_speed_validation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(segments, f, indent=2, ensure_ascii=False)
    print(f"\nDiagramm: {RESULTS_DIR}/top_speed_validation.png")
    print(f"Details: {RESULTS_DIR}/top_speed_validation_summary.json")


if __name__ == "__main__":
    main()
