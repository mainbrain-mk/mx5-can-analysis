"""
Ausrollversuch-Analyse (Coastdown): Rollphase mit Motor im Leerlauf,
Gang 0 (neutral), APP=0, Kupplung 0 (nicht getreten) (MX-5 Projekt)

Zweck: Nutzeranfrage 31.08.2026 - im Nachmittagslog 2026-08-31 152038
aufgefallen: eine echte Ausrollphase bei ~160-175 km/h mit ausgekuppeltem
Antriebsstrang (Gang 0, Motor im Leerlauf statt Schubabschaltung im
Gang). Das ist ein physikalisch besonders wertvolles Ereignis: OHNE
Motor-/Getriebeeingriff (kein Drehmoment, kein eta-Verlust) wirkt NUR
Luftwiderstand + Rollwiderstand auf die Verzoegerung - im Gegensatz zu
allen bisherigen Volllast-/Teillast-Checks entkoppelt das den in
docs/status/performance-model.md als "gekoppelt, nicht
unabhaengig identifiziert" markierten Parameter eta vollstaendig von
CdA/Crr. Ein echter Ausrollversuch ist genau die von diesem Dokument
selbst als noetig benannte Messung, um CdA/Crr unabhaengig zu pruefen.

Methodik:
  1. Ausroll-Ereignisse in einem Log automatisch finden: zusammenhaengende
     Phase mit Gang=0, APP<APP_MAX, Kupplung<CLUTCH_MAX, keine Bremsung
     (BFP_PRE_MZ<BRAKE_MAX), Mindestdauer MIN_DURATION_S.
  2. Physikalisches Modell (identische Formel wie drivetrain_model_
     validation.py, aber OHNE Antriebskraft, da Motor entkoppelt):
       m*dv/dt = -(0.5*rho*CdA*v^2 + Crr*m*g)
     CdA und Crr werden per Least-Squares an die gemessene v(t)-Kurve
     gefittet (numerische Integration des Modells ab v(t=0)=v_gemessen,
     Residuen gegen die gemessenen Geschwindigkeitspunkte minimiert -
     robuster als direkte Differentiation der quantisierten 1 km/h-
     VehicleSpeed-Werte).
  3. Vergleich der gefitteten CdA/Crr gegen die Projekt-Referenzwerte aus
     drivetrain_model_validation.py (CDA_M2=0.647 m^2, CRR=0.013, aus dem
     externen Dokument uebernommen, bisher nur indirekt ueber Volllast-
     Beschleunigung kalibriert).
  4. WIND (Nutzer-Nachfrage 31.08.2026, "nicht dass wir Rueckenwind
     hatten"): historische Stundenwerte (Windgeschwindigkeit + -richtung,
     10m Hoehe) von der Open-Meteo Archive-API (`archive-api.open-meteo.com`,
     kein API-Key noetig) fuer Ort+Zeit des Ereignisses abgefragt, in
     `data/weather/openmeteo_wind_cache.json` gecacht (ein API-Aufruf pro
     Tag+Ort deckt alle 24 Stunden ab, wiederholte Analysen brauchen kein
     erneutes Netz). Rueckenwind-Komponente entlang der Fahrtrichtung
     (Kanal `Lager` = GPS-Kurs) wird aus der Windgeschwindigkeit
     abzueglich der Fahrzeuggeschwindigkeit als effektive Anstroem-
     geschwindigkeit v_air fuer den Luftwiderstandsterm verwendet:
     F_drag = 0.5*rho*CdA*v_air*|v_air| (statt v_ground) - der
     Rollwiderstand bleibt windunabhaengig (reine Funktion der
     Fahrzeuggeschwindigkeit ueber Grund). Zwischen den beiden
     einschliessenden Stunden linear interpoliert, Windrichtung/-staerke
     ueber die kurze Ereignisdauer (Sekunden) als konstant angenommen.

EINSCHRAENKUNG: ein einzelnes ~24 km/h breites Ausrollfenster (typisch)
constrained CdA und Crr nur schwach gegeneinander (beide wirken im
gleichen Geschwindigkeitsbereich in aehnlicher Groessenordnung) - die
GESAMTE Verzoegerung/Widerstandskraft bei der gemessenen mittleren
Geschwindigkeit ist die verlaesslichere Kennzahl als die einzelnen
gefitteten CdA/Crr-Werte. Mehrere/breitere Ausrollereignisse (Vollgas
freikuppeln von hoher Geschwindigkeit bis zum Stillstand) wuerden die
Trennschaerfe deutlich verbessern.

Aufruf: python scripts/coastdown_analysis.py [log_id ...]
  (ein oder mehrere log_id optional, z.B. "2026-08-31 152038" - ohne
  Argument werden ALLE Logs im Datalake durchsucht. Bei angegebenen
  log_ids werden nur diese neu durchsucht, bestehende Ereignisse anderer
  Logs in coastdown_analysis_summary.json bleiben erhalten.)
"""
import os
import sys
import json
import urllib.request
import urllib.error
import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import least_squares

from drivetrain_model_validation import (
    MASS_KG, LOG_MASS_OVERRIDE_KG, RHO_KG_M3, CDA_M2, CRR, G,
)
from elevation_model import ElevationModel
from top_speed_validation import segment_grade

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
WIND_CACHE_PATH = "data/weather/openmeteo_wind_cache.json"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

APP_MAX = 1.0          # % - "APP 0"
CLUTCH_MAX = 1.0        # % - "Kupplung 0" (Pedal nicht getreten -> ausgekuppelt bei Gang 0 irrelevant, aber sauberes Kriterium)
BRAKE_MAX = 20.0         # kPa - keine Bremsung (Sensor-Grundrauschen beachten)
MIN_DURATION_S = 3.0
MIN_SPEED_KMH = 30.0     # sehr langsame Ausrollphasen (Ausrollen bis Stillstand) ausschliessen


def load_channel(con, log_id, channel):
    df = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()
    return df


def load_channel_with_timestamp(con, log_id, channel):
    df = con.execute(
        "SELECT t_elapsed_s AS t, timestamp_local, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()
    return df


def _load_wind_cache():
    if os.path.exists(WIND_CACHE_PATH):
        with open(WIND_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_wind_cache(cache):
    os.makedirs(os.path.dirname(WIND_CACHE_PATH), exist_ok=True)
    with open(WIND_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def fetch_wind_day(date_str, lat, lon, cache):
    """Liefert stuendliche (wind_speed_kmh, wind_direction_deg)-Listen fuer
    einen Kalendertag an einem Ort (Open-Meteo Archive-API, 10m Hoehe,
    lokale Zeitzone Europe/Berlin - identisch zur Log-Zeitbasis). Rundet
    lat/lon auf 0.1 Grad (~11km) fuer den Cache-Key, da Windfelder auf
    dieser Skala ohnehin nicht punktgenau sind und das die Cache-Trefferquote
    deutlich erhoeht."""
    lat_r, lon_r = round(lat, 1), round(lon, 1)
    key = f"{date_str}_{lat_r}_{lon_r}"
    if key in cache:
        return cache[key]
    url = (f"{OPEN_METEO_ARCHIVE_URL}?latitude={lat_r}&longitude={lon_r}"
           f"&start_date={date_str}&end_date={date_str}"
           f"&hourly=wind_speed_10m,wind_direction_10m"
           f"&timezone=Europe%2FBerlin")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = {
            "time": data["hourly"]["time"],
            "wind_speed_kmh": data["hourly"]["wind_speed_10m"],
            "wind_direction_deg": data["hourly"]["wind_direction_10m"],
        }
    except (urllib.error.URLError, KeyError, TimeoutError) as e:
        print(f"  WARNUNG: Windabfrage fehlgeschlagen ({e}) - Wind wird als 0 angenommen")
        result = None
    cache[key] = result
    _save_wind_cache(cache)
    return result


def wind_at(timestamp_local, lat, lon, cache):
    """Interpoliert Windgeschwindigkeit [m/s] und -richtung (meteorologisch,
    Grad woher der Wind weht) linear zwischen den beiden umschliessenden
    Stunden. Liefert (None, None), falls keine Winddaten verfuegbar."""
    date_str = timestamp_local.strftime("%Y-%m-%d")
    day = fetch_wind_day(date_str, lat, lon, cache)
    if day is None:
        return None, None
    times = np.array([np.datetime64(t) for t in day["time"]])
    t_np = np.datetime64(timestamp_local.replace(microsecond=0))
    idx = np.searchsorted(times, t_np)
    if idx == 0 or idx >= len(times):
        idx = min(max(idx, 1), len(times) - 1)
    t0, t1 = times[idx - 1], times[idx]
    frac = ((t_np - t0) / (t1 - t0)) if t1 != t0 else 0.0
    frac = float(np.clip(frac, 0.0, 1.0))
    speed = (day["wind_speed_kmh"][idx - 1] * (1 - frac) + day["wind_speed_kmh"][idx] * frac) / 3.6
    # Windrichtung zirkulaer interpolieren (kurze Winkeldifferenz nehmen)
    d0, d1 = day["wind_direction_deg"][idx - 1], day["wind_direction_deg"][idx]
    delta = ((d1 - d0 + 180) % 360) - 180
    direction = (d0 + frac * delta) % 360
    return float(speed), float(direction)


def tailwind_component_ms(heading_deg, wind_speed_ms, wind_dir_from_deg):
    """Positive = Rueckenwind (verringert effektive Anstroemgeschwindigkeit),
    negativ = Gegenwind. wind_dir_from_deg ist meteorologisch (Richtung,
    AUS der der Wind weht); die Windrichtung, WOHIN er weht, ist +180."""
    wind_to_deg = (wind_dir_from_deg + 180) % 360
    angle = np.radians(heading_deg - wind_to_deg)
    return wind_speed_ms * np.cos(angle)


def group_runs(mask):
    runs = []
    in_run = False
    start = None
    for i, m in enumerate(mask):
        if m and not in_run:
            in_run, start = True, i
        elif not m and in_run:
            in_run = False
            runs.append((start, i - 1))
    if in_run:
        runs.append((start, len(mask) - 1))
    return runs


def find_coastdown_events(con, log_id):
    sp = load_channel(con, log_id, "VehicleSpeed")
    ge = load_channel(con, log_id, "TM_GEST")
    if len(sp) < 5 or len(ge) < 5:
        return []
    ap = load_channel(con, log_id, "APP")
    cp = load_channel(con, log_id, "CPP_PER_MZ")
    bf = load_channel(con, log_id, "BFP_PRE_MZ")
    rp = load_channel(con, log_id, "EngineRPM")
    if len(ap) < 5 or len(cp) < 5:
        return []

    t = sp["t"].values
    v_kmh = sp["value"].values
    g_i = np.round(np.interp(t, ge["t"].values, ge["value"].values))
    app_i = np.interp(t, ap["t"].values, ap["value"].values)
    cpp_i = np.interp(t, cp["t"].values, cp["value"].values)
    brake_i = (np.interp(t, bf["t"].values, bf["value"].values)
               if len(bf) >= 2 else np.zeros_like(t))
    rpm_i = (np.interp(t, rp["t"].values, rp["value"].values)
             if len(rp) >= 2 else np.full_like(t, np.nan))

    mask = ((g_i == 0) & (app_i < APP_MAX) & (cpp_i < CLUTCH_MAX)
            & (brake_i < BRAKE_MAX) & (v_kmh >= MIN_SPEED_KMH))

    events = []
    for start, end in group_runs(mask):
        if t[end] - t[start] < MIN_DURATION_S:
            continue
        events.append({
            "log_id": log_id,
            "t_start": float(t[start]), "t_end": float(t[end]),
            "duration_s": float(t[end] - t[start]),
            "v_start_kmh": float(v_kmh[start]), "v_end_kmh": float(v_kmh[end]),
            "t": t[start:end + 1].tolist(),
            "v_kmh": v_kmh[start:end + 1].tolist(),
            "rpm": rpm_i[start:end + 1].tolist(),
        })
    return events


def simulate_v(t_rel, v0_ms, cda, crr, mass_kg, grade_frac=0.0, tailwind_ms=0.0, dt=0.02):
    """Integriert m*dv/dt = -(0.5*rho*CdA*v_air*|v_air| + Crr*m*g) -
    m*g*grade_frac per RK4 auf ein feines Zeitraster und interpoliert auf
    die gewuenschten t_rel-Punkte. v_air = v - tailwind_ms ist die
    effektive Anstroemgeschwindigkeit (Rueckenwind verringert sie, siehe
    tailwind_component_ms()) - NUR fuer den Luftwiderstandsterm relevant,
    der Rollwiderstand haengt weiterhin von v (Geschwindigkeit ueber
    Grund) ab. grade_frac wie in top_speed_validation.py: positiv =
    Steigung (bremst zusaetzlich), negativ = Gefaelle (rollt zusaetzlich
    an) - siehe dortiger Docstring "Steigungskorrektur"."""
    t_end = t_rel[-1]
    n = max(2, int(np.ceil(t_end / dt)) + 1)
    t_grid = np.linspace(0.0, t_end, n)
    v_grid = np.empty(n)
    v_grid[0] = v0_ms

    def dvdt(v):
        v_air = v - tailwind_ms
        return -(0.5 * RHO_KG_M3 * cda * v_air * abs(v_air) + crr * mass_kg * G) / mass_kg - G * grade_frac

    for i in range(1, n):
        h = t_grid[i] - t_grid[i - 1]
        v = v_grid[i - 1]
        k1 = dvdt(v)
        k2 = dvdt(v + 0.5 * h * k1)
        k3 = dvdt(v + 0.5 * h * k2)
        k4 = dvdt(v + h * k3)
        v_grid[i] = v + (h / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
    return np.interp(t_rel, t_grid, v_grid)


def fit_cda_crr(event, mass_kg, grade_frac=0.0, tailwind_ms=0.0):
    """grade_frac: mittlere Streckensteigung waehrend des Ereignisses (aus
    segment_grade()/DGM, 0.0 falls nicht verfuegbar/keine Kachelabdeckung -
    dann per grade_source="keine DGM-Abdeckung" im Aufrufer kenntlich
    gemacht). Ein Gefaelle taeuscht sonst zu WENIG Widerstand vor (und
    umgekehrt eine Steigung zu VIEL) - ungeprueft waere jeder CdA/Crr-Fit
    aus einem Ausrollversuch wertlos, siehe Docstring oben. Gleiches gilt
    fuer tailwind_ms (Rueckenwind-Komponente aus Open-Meteo, 0.0 falls
    Windabfrage fehlgeschlagen) - Rueckenwind senkt die effektive
    Anstroemgeschwindigkeit und taeuscht sonst zu WENIG Luftwiderstand vor."""
    t_rel = np.array(event["t"]) - event["t"][0]
    v_ms = np.array(event["v_kmh"]) / 3.6

    def residuals(params):
        cda, crr = params
        v_model = simulate_v(t_rel, v_ms[0], cda, crr, mass_kg, grade_frac, tailwind_ms)
        return v_model - v_ms

    res = least_squares(residuals, x0=[CDA_M2, CRR],
                         bounds=([0.1, 0.0], [1.5, 0.05]))
    cda_fit, crr_fit = res.x
    v_model_fit = simulate_v(t_rel, v_ms[0], cda_fit, crr_fit, mass_kg, grade_frac, tailwind_ms)
    rmse_kmh = float(np.sqrt(np.mean((v_model_fit - v_ms) ** 2)) * 3.6)

    v_ref_ms = np.mean(v_ms)
    v_air_ref_ms = v_ref_ms - tailwind_ms
    f_drag_fit = 0.5 * RHO_KG_M3 * cda_fit * v_air_ref_ms ** 2
    f_roll_fit = crr_fit * mass_kg * G
    f_grade = mass_kg * G * grade_frac
    f_drag_ref = 0.5 * RHO_KG_M3 * CDA_M2 * v_air_ref_ms ** 2
    f_roll_ref = CRR * mass_kg * G
    v_model_ref = simulate_v(t_rel, v_ms[0], CDA_M2, CRR, mass_kg, grade_frac, tailwind_ms)
    rmse_ref_kmh = float(np.sqrt(np.mean((v_model_ref - v_ms) ** 2)) * 3.6)
    # Referenzmodell OHNE Windkorrektur (fuer den Vorher/Nachher-Vergleich)
    v_model_ref_nowind = simulate_v(t_rel, v_ms[0], CDA_M2, CRR, mass_kg, grade_frac, 0.0)
    rmse_ref_nowind_kmh = float(np.sqrt(np.mean((v_model_ref_nowind - v_ms) ** 2)) * 3.6)
    f_drag_ref_nowind = 0.5 * RHO_KG_M3 * CDA_M2 * v_ref_ms ** 2

    return {
        "grade_frac": float(grade_frac), "grade_pct": float(grade_frac * 100),
        "f_grade_N": float(f_grade),
        "tailwind_ms": float(tailwind_ms), "tailwind_kmh": float(tailwind_ms * 3.6),
        "v_air_ref_ms": float(v_air_ref_ms),
        "cda_fit_m2": float(cda_fit), "crr_fit": float(crr_fit),
        "rmse_fit_kmh": rmse_kmh,
        "rmse_reference_model_kmh": rmse_ref_kmh,
        "rmse_reference_model_no_wind_kmh": rmse_ref_nowind_kmh,
        "v_ref_kmh": float(v_ref_ms * 3.6),
        "f_drag_fit_N": float(f_drag_fit), "f_roll_fit_N": float(f_roll_fit),
        "f_total_fit_N": float(f_drag_fit + f_roll_fit),
        "f_drag_reference_N": float(f_drag_ref), "f_roll_reference_N": float(f_roll_ref),
        "f_total_reference_N": float(f_drag_ref + f_roll_ref),
        "f_drag_reference_no_wind_N": float(f_drag_ref_nowind),
        "f_total_reference_no_wind_N": float(f_drag_ref_nowind + f_roll_ref),
        "v_model_fit_kmh": (v_model_fit * 3.6).tolist(),
        "v_model_reference_kmh": (v_model_ref * 3.6).tolist(),
        "v_model_reference_no_wind_kmh": (v_model_ref_nowind * 3.6).tolist(),
        "mass_kg_used": float(mass_kg),
        "cda_reference_m2": CDA_M2, "crr_reference": CRR,
    }


def event_wind(con, log_id, t_start, t_end, wind_cache):
    """Rueckenwind-Komponente [m/s] fuer ein Ereignis: Median aus GPS-
    Position (Breite/Laenge), Fahrtrichtung (Kanal 'Lager', GPS-Kurs in
    Grad) und Zeitstempel im Ereignisfenster, damit eine Windabfrage bei
    Open-Meteo. Liefert ein dict mit allen Zwischenwerten (oder
    tailwind_ms=0.0 + wind_available=False, falls Kanaele fehlen oder die
    Windabfrage fehlschlaegt)."""
    lat = load_channel_with_timestamp(con, log_id, "Breite")
    lon = load_channel(con, log_id, "Länge")
    heading = load_channel(con, log_id, "Lager")
    m = (lat["t"] >= t_start) & (lat["t"] <= t_end)
    if m.sum() < 1 or len(lon) < 1 or len(heading) < 1:
        return {"tailwind_ms": 0.0, "wind_available": False}

    sub_t = lat.loc[m, "t"].values
    lat_med = float(np.median(lat.loc[m, "value"].values))
    lon_med = float(np.median(np.interp(sub_t, lon["t"].values, lon["value"].values)))
    heading_med = float(np.median(np.interp(sub_t, heading["t"].values, heading["value"].values)))
    ts_mid = lat.loc[m, "timestamp_local"].iloc[len(sub_t) // 2]

    wind_speed_ms, wind_dir_deg = wind_at(ts_mid, lat_med, lon_med, wind_cache)
    if wind_speed_ms is None:
        return {"tailwind_ms": 0.0, "wind_available": False,
                "lat": lat_med, "lon": lon_med, "heading_deg": heading_med,
                "timestamp_local": str(ts_mid)}

    tw = tailwind_component_ms(heading_med, wind_speed_ms, wind_dir_deg)
    return {
        "tailwind_ms": float(tw), "wind_available": True,
        "lat": lat_med, "lon": lon_med, "heading_deg": heading_med,
        "timestamp_local": str(ts_mid),
        "wind_speed_ms": wind_speed_ms, "wind_speed_kmh": wind_speed_ms * 3.6,
        "wind_direction_from_deg": wind_dir_deg,
    }


def plot_event(event, fit, out_path):
    t_rel = np.array(event["t"]) - event["t"][0]
    v_kmh = np.array(event["v_kmh"])
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 8), sharex=True)

    ax1.plot(t_rel, v_kmh, "o-", color="black", ms=4, label="gemessen (VehicleSpeed)")
    ax1.plot(t_rel, fit["v_model_fit_kmh"], "--", color="seagreen",
              label=f"Modell gefittet (CdA={fit['cda_fit_m2']:.3f} m², Crr={fit['crr_fit']:.4f})")
    ax1.plot(t_rel, fit["v_model_reference_kmh"], ":", color="tomato",
              label=f"Modell Referenzwerte (CdA={fit['cda_reference_m2']:.3f} m², Crr={fit['crr_reference']:.4f}, mit Wind+Gefaelle)")
    if fit.get("wind", {}).get("wind_available"):
        ax1.plot(t_rel, fit["v_model_reference_no_wind_kmh"], "-.", color="darkorange", alpha=0.7,
                  label="Modell Referenzwerte (ohne Windkorrektur)")
    ax1.set_ylabel("Geschwindigkeit [km/h]")
    grade_note = (f"Gefaelle={fit['grade_pct']:+.2f}%" if fit.get("grade_source") else "Gefaelle unbekannt")
    wind_note = (f"Rueckenwind {fit['tailwind_kmh']:+.1f} km/h"
                 if fit.get("wind", {}).get("wind_available") else "Wind unbekannt")
    ax1.set_title(f"Ausrollversuch {event['log_id']} — Gang 0, APP=0, Kupplung=0\n"
                   f"t={event['t_start']:.1f}-{event['t_end']:.1f}s, "
                   f"{event['v_start_kmh']:.0f}->{event['v_end_kmh']:.0f} km/h — {grade_note}, {wind_note}")
    ax1.legend()
    ax1.grid(alpha=0.3)

    rpm = np.array(event["rpm"])
    ax2.plot(t_rel, rpm, color="steelblue")
    ax2.set_xlabel("Zeit seit Ausroll-Beginn [s]")
    ax2.set_ylabel("Motordrehzahl [U/min]")
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    target_logs = sys.argv[1:]
    con = duckdb.connect(DB_PATH, read_only=True)
    if target_logs:
        log_ids = target_logs
    else:
        log_ids = con.execute("SELECT log_id FROM logs ORDER BY log_id").fetchdf()["log_id"].tolist()

    elev_model = ElevationModel()
    wind_cache = _load_wind_cache()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    new_results = []
    for log_id in log_ids:
        events = find_coastdown_events(con, log_id)
        for ev in events:
            mass_kg = LOG_MASS_OVERRIDE_KG.get(log_id, MASS_KG)
            grade_frac, dist_m, n_gps, sources = segment_grade(
                con, log_id, ev["t_start"], ev["t_end"], elev_model)
            grade_used = grade_frac if grade_frac is not None else 0.0
            wind = event_wind(con, log_id, ev["t_start"], ev["t_end"], wind_cache)
            fit = fit_cda_crr(ev, mass_kg, grade_used, wind["tailwind_ms"])
            fit["grade_source"] = ", ".join(sorted(sources)) if grade_frac is not None else None
            fit["grade_gps_distance_m"] = dist_m
            fit["grade_n_gps_points"] = n_gps
            fit["wind"] = wind

            print(f"\n{log_id}  t={ev['t_start']:.1f}-{ev['t_end']:.1f}s "
                  f"({ev['duration_s']:.1f}s)  v={ev['v_start_kmh']:.0f}->{ev['v_end_kmh']:.0f} km/h  "
                  f"Masse={mass_kg:.1f}kg")
            if grade_frac is not None:
                print(f"  Gefaelle/Steigung: {fit['grade_pct']:+.2f}% ueber {dist_m:.0f}m "
                      f"({n_gps} GPS-Punkte, Quelle: {fit['grade_source']})")
            else:
                print(f"  Gefaelle/Steigung: KEINE DGM-Abdeckung ({n_gps} GPS-Punkte) - als 0% angenommen, Ergebnis entsprechend unsicherer")
            if wind.get("wind_available"):
                print(f"  Wind: {wind['wind_speed_kmh']:.1f} km/h aus {wind['wind_direction_from_deg']:.0f}° "
                      f"(Fahrtrichtung {wind['heading_deg']:.0f}°, {wind['timestamp_local']}) "
                      f"-> Rueckenwind-Komponente {fit['tailwind_kmh']:+.1f} km/h "
                      f"(v_air statt v_ground: {fit['v_air_ref_ms']*3.6:.0f} statt {fit['v_ref_kmh']:.0f} km/h)")
            else:
                print("  Wind: NICHT verfuegbar (Abfrage fehlgeschlagen oder Kanaele fehlen) - als 0 angenommen, Ergebnis entsprechend unsicherer")
            print(f"  gefittet:  CdA={fit['cda_fit_m2']:.3f} m²  Crr={fit['crr_fit']:.4f}  "
                  f"RMSE={fit['rmse_fit_kmh']:.2f} km/h")
            print(f"  Referenz (mit Wind+Gefaelle): CdA={fit['cda_reference_m2']:.3f} m²  Crr={fit['crr_reference']:.4f}  "
                  f"RMSE={fit['rmse_reference_model_kmh']:.2f} km/h  "
                  f"(RMSE OHNE Windkorrektur: {fit['rmse_reference_model_no_wind_kmh']:.2f} km/h)")
            print(f"  Widerstandskraft bei v_mean={fit['v_ref_kmh']:.0f} km/h (Gefaelleanteil {fit['f_grade_N']:.0f} N bereits herausgerechnet): "
                  f"gefittet={fit['f_total_fit_N']:.0f} N (Luft {fit['f_drag_fit_N']:.0f} + Roll {fit['f_roll_fit_N']:.0f}), "
                  f"Referenz MIT Wind={fit['f_total_reference_N']:.0f} N (Luft {fit['f_drag_reference_N']:.0f} + Roll {fit['f_roll_reference_N']:.0f}), "
                  f"Referenz OHNE Wind={fit['f_total_reference_no_wind_N']:.0f} N (Luft {fit['f_drag_reference_no_wind_N']:.0f} + Roll {fit['f_roll_reference_N']:.0f})")

            plot_path = os.path.join(
                RESULTS_DIR, f"coastdown_{log_id.replace(' ', '_').replace(':', '')}_{ev['t_start']:.0f}s.png")
            plot_event(ev, fit, plot_path)
            print(f"  Plot: {plot_path}")

            entry = {**{k: v for k, v in ev.items() if k not in ("t", "v_kmh", "rpm")}, **fit}
            entry["t"], entry["v_kmh"], entry["rpm"] = ev["t"], ev["v_kmh"], ev["rpm"]
            new_results.append(entry)

    if not new_results:
        print(f"Keine Ausrollereignisse (Gang=0, APP=0, Kupplung=0, keine Bremsung) "
              f"in den durchsuchten {len(log_ids)} Log(s) gefunden.")
        return

    summary_path = os.path.join(RESULTS_DIR, "coastdown_analysis_summary.json")
    existing = []
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            existing = json.load(f)
    kept = [e for e in existing if e["log_id"] not in log_ids]
    all_results = kept + new_results

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\n=== {len(new_results)} neue(s) Ausrollereignis(se) in {len(log_ids)} durchsuchten Log(s) "
          f"({len(all_results)} insgesamt in {summary_path}) ===")
    print("Details: results/coastdown_analysis_summary.json")


if __name__ == "__main__":
    main()
