"""
Nullpunkt-Korrektur fuer STEER_ANGL_EPS anhand echter Geradeausfahrt auf
OSM-Strassen (MX-5 Projekt)

Hintergrund (siehe docs/logs/projekt-stand.md, "STEER_ANGL_EPS-Nullpunkt-Offset in
beiden neuen Logs entdeckt", 02.09.2026): die Logs `2026-09-02 150720`
(Offset ca. +148°) und `2026-09-02 102031` (Offset ca. +20°) haben einen
Lenkwinkel-Kanal, dessen Nullpunkt NICHT bei 0° liegt (vermutlich weil
der EPS-Sensor nach einem Reset/einer Werkstattfahrt nicht neu gelernt
wurde). Der bisherige Kalibrieransatz in `steering_lateral_model.py`
geht implizit davon aus, dass der Rohwert bereits um 0° zentriert ist.

Idee (Nutzeranfrage): den Nullpunkt aus den Daten selbst schaetzen, indem
wir Zeitfenster suchen, in denen das Fahrzeug nachweislich GERADEAUS
faehrt - nicht anhand des (moeglicherweise selbst verschobenen)
Lenkwinkels, sondern anhand einer UNABHAENGIGEN Quelle: der tatsaechlichen
Strassengeometrie aus OpenStreetMap. Wenn die gefahrene GPS-Position auf
einen nachweislich geraden Strassenabschnitt faellt (Kruemmung der
OSM-Liniengeometrie nahe Null ueber ein Fenster von STRAIGHT_WINDOW_M)
UND das Fahrzeug dabei zuegig faehrt (kein Rangieren/Parken), muss der
Lenkradwinkel im Mittel nahe der wahren Mitte liegen. Der Median von
STEER_ANGL_EPS ueber alle solchen Fenster ist die Nullpunkt-Schaetzung.

Methodik:
  1. OSM-Strassengeometrie (Overpass, `way["highway"]`) fuer die Region
     aller Logs mit STEER_ANGL_EPS abrufen und cachen (gleiches Muster
     wie `elevation_model.get_or_build_trip_bridge_cache`).
  2. Jede Strassen-Linie in ein lokales, flaches ENU-Koordinatensystem
     projizieren, mit festem Schritt (RESAMPLE_STEP_M) entlang der
     Kantenfolge resampeln (robust gegen unterschiedliche OSM-
     Knotenabstaende), und pro resampeltem Punkt die Kursaenderung ueber
     ein Fenster von +/- STRAIGHT_WINDOW_M/2 bestimmen. Punkte mit
     Kursaenderung < STRAIGHT_ANGLE_MAX_DEG gelten als "gerade".
  3. Fuer jedes Log: gefahrene GPS-Punkte (nach Genauigkeitsfilter wie in
     corner_event_analysis.py) per Nearest-Neighbour (KD-Baum) gegen die
     "geraden" OSM-Punkte matchen. Match gilt nur bei Abstand <
     MATCH_TOLERANCE_M UND Geschwindigkeit > MIN_SPEED_MS (schliesst
     Rangieren/Parken/Ampelstopps aus).
  4. Zusammenhaengende Match-Zeitfenster >= MIN_RUN_S gruppieren (analog
     `group_events()` in corner_event_analysis.py), um zufaellige
     Einzeltreffer (z.B. kurze Ueberschneidung mit einer parallelen
     Nebenstrasse) auszuschliessen.
  5. Median von STEER_ANGL_EPS ueber alle qualifizierten Samples = Offset-
     Schaetzung pro Log. Nur vertrauenswuerdig ab MIN_SAMPLES_FOR_OFFSET
     Samples.

Ergebnis wird nach `results/steering_zero_offset.json` geschrieben und
NICHT automatisch in andere Skripte uebernommen - `steering_lateral_model.py`
liest diese Datei separat ein (siehe dortiger STEER_OFFSET_PATH).

Aufruf: python scripts/steering_zero_offset.py
"""
import os
import json
import urllib.request
import urllib.parse
from pathlib import Path

import numpy as np
import duckdb
from scipy.spatial import cKDTree

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
OUT_PATH = os.path.join(RESULTS_DIR, "steering_zero_offset.json")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
ROAD_CACHE_PATH = Path("data/track/osm_roads_steering_corridor.json")

# Deckt die Region aller bisherigen Logs mit STEER_ANGL_EPS grosszuegig ab
# (beobachtete Spanne: lat 52.53-52.75, lon 13.18-13.37, siehe docs/logs/projekt-stand.md) -
# bei kuenftigen Logs ausserhalb dieser Box muesste die Box erweitert und
# der Cache geloescht werden.
CORRIDOR_BBOX = (52.45, 13.10, 52.85, 13.45)  # south, west, north, east

HIGHWAY_TYPES = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
    "unclassified",
]

RESAMPLE_STEP_M = 5.0
STRAIGHT_WINDOW_M = 60.0        # Basis fuer die Kursaenderungsmessung (+/- 30m)
STRAIGHT_ANGLE_MAX_DEG = 4.0    # Kursaenderung ueber das ganze Fenster
MATCH_TOLERANCE_M = 12.0        # Fahrzeug-GPS <-> "gerader" OSM-Punkt
MIN_SPEED_MS = 8.0              # ~29 km/h - schliesst Rangieren/Ampelstopps aus
MIN_RUN_S = 3.0
GPS_MAX_HORZ_ACC_M = 20.0       # wie top_speed_validation.py / corner_event_analysis.py
MIN_SAMPLES_FOR_OFFSET = 50


def fetch_osm_roads(bbox):
    south, west, north, east = bbox
    types = "|".join(HIGHWAY_TYPES)
    query = (
        f'[out:json][timeout:180];'
        f'(way["highway"~"^({types})$"]({south},{west},{north},{east}););'
        f'out geom;'
    )
    req = urllib.request.Request(
        OVERPASS_URL,
        data=f"data={query}".encode(),
        method="POST",
        headers={
            "User-Agent": "mx5-fahrdynamik-projekt/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        payload = json.load(resp)

    ways = []
    for el in payload.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue
        ways.append({
            "id": el["id"],
            "highway": el.get("tags", {}).get("highway", ""),
            "points": [[float(p["lat"]), float(p["lon"])] for p in geom],
        })
    return ways


def get_or_build_road_cache(bbox):
    if ROAD_CACHE_PATH.exists():
        with open(ROAD_CACHE_PATH) as f:
            return json.load(f)
    ways = fetch_osm_roads(bbox)
    ROAD_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ROAD_CACHE_PATH, "w") as f:
        json.dump(ways, f)
    return ways


def make_projector(lat0):
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * np.cos(np.radians(lat0))

    def project(lat, lon):
        x = (np.asarray(lon, dtype=np.float64) - 0.0) * m_per_deg_lon
        y = (np.asarray(lat, dtype=np.float64) - 0.0) * m_per_deg_lat
        return x, y

    return project


def resample_polyline(x, y, step_m):
    """Resample eine Polylinie (x,y in Metern) auf gleichmaessigen Schrittabstand."""
    seg_len = np.hypot(np.diff(x), np.diff(y))
    s = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = s[-1]
    if total < step_m:
        return None, None, None
    s_new = np.arange(0.0, total, step_m)
    x_new = np.interp(s_new, s, x)
    y_new = np.interp(s_new, s, y)
    return x_new, y_new, s_new


def straight_mask_for_way(x_new, y_new, window_m, angle_max_deg):
    """Fuer jeden resampelten Punkt: Kursaenderung ueber ein Fenster von
    +/- window_m/2 um den Punkt. True = 'gerade' (Kursaenderung klein)."""
    n = len(x_new)
    half_n = max(1, int(round((window_m / 2) / RESAMPLE_STEP_M)))
    mask = np.zeros(n, dtype=bool)
    for i in range(n):
        i0 = max(0, i - half_n)
        i1 = min(n - 1, i + half_n)
        if i1 - i0 < 2:
            continue
        dx0, dy0 = x_new[i0 + 1] - x_new[i0], y_new[i0 + 1] - y_new[i0]
        dx1, dy1 = x_new[i1] - x_new[i1 - 1], y_new[i1] - y_new[i1 - 1]
        if (dx0 == 0 and dy0 == 0) or (dx1 == 0 and dy1 == 0):
            continue
        b0 = np.degrees(np.arctan2(dx0, dy0))
        b1 = np.degrees(np.arctan2(dx1, dy1))
        diff = (b1 - b0 + 180) % 360 - 180
        mask[i] = abs(diff) < angle_max_deg
    return mask


def build_straight_point_cloud(ways, project):
    xs, ys = [], []
    for way in ways:
        pts = way["points"]
        lat = np.array([p[0] for p in pts])
        lon = np.array([p[1] for p in pts])
        x, y = project(lat, lon)
        x_new, y_new, _ = resample_polyline(x, y, RESAMPLE_STEP_M)
        if x_new is None:
            continue
        mask = straight_mask_for_way(x_new, y_new, STRAIGHT_WINDOW_M, STRAIGHT_ANGLE_MAX_DEG)
        if mask.any():
            xs.append(x_new[mask])
            ys.append(y_new[mask])
    if not xs:
        return np.empty((0, 2))
    return np.column_stack([np.concatenate(xs), np.concatenate(ys)])


def load_channel(con, log_id, channel):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements "
        "WHERE log_id = ? AND channel = ? AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()


def group_runs(t, mask, min_duration_s):
    events = []
    in_run, start = False, None
    n = len(mask)
    for i in range(n):
        if mask[i] and not in_run:
            in_run, start = True, i
        elif not mask[i] and in_run:
            in_run = False
            if t[i - 1] - t[start] >= min_duration_s:
                events.append((start, i - 1))
    if in_run and t[n - 1] - t[start] >= min_duration_s:
        events.append((start, n - 1))
    return events


def logs_with_steering(con):
    return con.execute(
        "SELECT DISTINCT log_id FROM measurements WHERE channel = 'STEER_ANGL_EPS' ORDER BY log_id"
    ).fetchdf()["log_id"].tolist()


def estimate_offset_for_log(con, log_id, straight_tree, project):
    gps_lat = load_channel(con, log_id, "Breite")
    gps_lon = load_channel(con, log_id, "Länge")
    speed = load_channel(con, log_id, "VehicleSpeed")
    acc = load_channel(con, log_id, "Horz Genauigkeit")
    steer = load_channel(con, log_id, "STEER_ANGL_EPS")
    if len(gps_lat) < 10 or len(steer) < 10:
        return {"log_id": log_id, "error": "zu wenige GPS- oder Lenkwinkel-Punkte"}

    t = gps_lat["t"].values
    lat = gps_lat["value"].values
    lon = np.interp(t, gps_lon["t"].values, gps_lon["value"].values)

    if len(acc) >= 2:
        acc_i = np.interp(t, acc["t"].values, acc["value"].values)
        good = acc_i <= GPS_MAX_HORZ_ACC_M
        t, lat, lon = t[good], lat[good], lon[good]
    if len(t) < 10:
        return {"log_id": log_id, "error": "zu wenige GPS-Punkte nach Genauigkeitsfilter"}

    v_ms = np.interp(t, speed["t"].values, speed["value"].values) / 3.6
    x, y = project(lat, lon)
    dist, _ = straight_tree.query(np.column_stack([x, y]))

    candidate = (dist < MATCH_TOLERANCE_M) & (v_ms > MIN_SPEED_MS)
    runs = group_runs(t, candidate, MIN_RUN_S)
    if not runs:
        return {"log_id": log_id, "error": "keine bestaetigten Geradeausfahrt-Fenster gefunden",
                "n_candidate_samples": int(candidate.sum())}

    steer_t = steer["t"].values
    steer_v = steer["value"].values
    samples = []
    windows = []
    for i0, i1 in runs:
        t0, t1 = t[i0], t[i1]
        m = (steer_t >= t0) & (steer_t <= t1)
        if m.sum() == 0:
            continue
        samples.append(steer_v[m])
        windows.append({"t_start": float(t0), "t_end": float(t1), "duration_s": float(t1 - t0),
                         "n_steer_samples": int(m.sum())})

    if not samples:
        return {"log_id": log_id, "error": "keine Lenkwinkel-Samples in den Geradeaus-Fenstern"}

    all_samples = np.concatenate(samples)
    n = len(all_samples)
    result = {
        "log_id": log_id,
        "n_samples": int(n),
        "n_runs": len(windows),
        "total_straight_duration_s": float(sum(w["duration_s"] for w in windows)),
        "offset_deg_median": float(np.median(all_samples)),
        "offset_deg_mean": float(np.mean(all_samples)),
        "offset_deg_std": float(np.std(all_samples)),
        "trusted": n >= MIN_SAMPLES_FOR_OFFSET,
        "windows": windows,
    }
    return result


def main():
    print(f"Lade OSM-Strassengeometrie (Overpass, gecacht unter {ROAD_CACHE_PATH}) ...")
    ways = get_or_build_road_cache(CORRIDOR_BBOX)
    print(f"{len(ways)} OSM-Strassen-Ways geladen (Typen: {', '.join(HIGHWAY_TYPES)})")

    lat0 = (CORRIDOR_BBOX[0] + CORRIDOR_BBOX[2]) / 2
    project = make_projector(lat0)

    print("Extrahiere gerade Strassenabschnitte "
          f"(Kursaenderung < {STRAIGHT_ANGLE_MAX_DEG}° ueber {STRAIGHT_WINDOW_M:.0f}m-Fenster) ...")
    straight_points = build_straight_point_cloud(ways, project)
    print(f"{len(straight_points)} 'gerade' Strassenpunkte (Resample-Schritt {RESAMPLE_STEP_M:.0f}m)")
    if len(straight_points) == 0:
        print("Keine geraden Strassenpunkte gefunden - Abbruch.")
        return
    straight_tree = cKDTree(straight_points)

    con = duckdb.connect(DB_PATH, read_only=True)
    steering_logs = logs_with_steering(con)
    print(f"\n{len(steering_logs)} Logs mit STEER_ANGL_EPS-Kanal werden geprueft.\n")

    results = {}
    for log_id in steering_logs:
        res = estimate_offset_for_log(con, log_id, straight_tree, project)
        results[log_id] = res
        if "error" in res:
            extra = f" ({res['n_candidate_samples']} Kandidaten-Samples vor Gruppierung)" \
                if "n_candidate_samples" in res else ""
            print(f"{log_id}: {res['error']}{extra}")
            continue
        trust = "" if res["trusted"] else "  [NICHT VERTRAUENSWUERDIG - zu wenige Samples]"
        print(f"{log_id}: Offset(Median)={res['offset_deg_median']:+.1f}°  "
              f"(Mittel={res['offset_deg_mean']:+.1f}°  Std={res['offset_deg_std']:.1f}°)  "
              f"n={res['n_samples']} aus {res['n_runs']} Fenstern "
              f"({res['total_straight_duration_s']:.0f}s gesamt){trust}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {OUT_PATH}")


if __name__ == "__main__":
    main()
