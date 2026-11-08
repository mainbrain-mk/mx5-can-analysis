"""
Motorsport Arena Oschersleben - Streckenmodell aus OSM, Orthofoto und DGM, 08.11.2026

Selbes Verfahren wie spreewaldring_track_model.py (Viterbi auf der weissen Randlinie, build_segment() von dort).
Unterschied: Die Karten kommen direkt aus dem Orthofoto (dasselbe Rezept wie in PhotoCraft, siehe line_map()). Die
JSON-Datei hat das Schema von spreewaldring_track_model.json.

Boxengasse (Nutzerskizze 08.11.2026 aufs Orthofoto gelegt, Korrelation 0,94): Einfahrt und Ausfahrt wie die Strecke aus OSM
und Bild. Vor den Boxen liegt die Fahrbahn fast ganz im Schatten von Gebaeude und Turm, dort gibt es keine verfolgbare
Linie: Rand sind Mauerfuss (hell -> Asphalt) und Gebaeudefront (dunkel -> hell), je als Gerade ueber die ganze Laenge
gefittet (fit_edge()). Nutzerentscheid: Fastlane = Mauerfuss bis gestrichelte Markierung, Arbeitsbereich = Markierung bis
Gebaeudefront. Die zwei Anbindungen ans Fahrerlager sind nur Mittellinien, dort ist die Flaeche offen.

Quellen (dl-de/by-2-0, LVermGeo Sachsen-Anhalt; OSM ODbL), EPSG:25832, DHHN2016, Download einmalig:
  DOP20 per WMS, DGM1 per WCS. OSM-Hauptschleife = A-Kurs mit S-Kurve nach Start/Ziel (Betreiber 3,667 km). Die
  90-Grad-Variante (3,696 km) ist in OSM ein eigener Weg ("Variante", 89254333) und hier nicht modelliert.

Ausgaben: results/oschersleben_track_model.json, results/oschersleben_track_model_edges.png
Aufruf: .venv/bin/python scripts/oschersleben_track_model.py
"""
import io
import json
import os
import urllib.parse
import urllib.request

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from pyproj import Transformer
from scipy.ndimage import gaussian_filter, map_coordinates
from scipy.signal import argrelmin

import spreewaldring_track_model as M
from spreewaldring_track import RESULTS_DIR

Image.MAX_IMAGE_PIXELS = None

OSM_PATH = "data/track/oschersleben_osm_raceways.json"
DOP_PATH = "data/orthophoto/oschersleben_dop20.png"
DOP_GEO_PATH = "data/orthophoto/oschersleben_dop20.json"
DGM_PATH = "data/elevation/oschersleben_dgm1.tif"
MODEL_JSON = os.path.join(RESULTS_DIR, "oschersleben_track_model.json")
OVERLAY_PNG = os.path.join(RESULTS_DIR, "oschersleben_track_model_edges.png")
LAP_SIM_JSON = os.path.join(RESULTS_DIR, "oschersleben_lap_simulation.json")
START_FINISH_UTM32 = (656441.475, 5766506.612)  # Schachbrettstreifen im Orthofoto, s = 251 m
WMS_URL = "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DOP_WMS_OpenData/guest"
WCS_URL = "https://www.geodatenportal.sachsen-anhalt.de/ows_WCS_ST_DGM1"
USER_AGENT = "mx5-project-track-research/1.0 (personal, non-commercial track geometry lookup)"

# Fahrtrichtung, beginnend an der Zeppelin-Kurve-Ausfahrt (Start-Ziel-Gerade)
MAIN_LOOP_WAYS = (300009191, 299890015, 299890024, 299890030, 299890025, 299890017, 89251023, 300009193)
OSM_LENGTH_M, OSM_LENGTH_TOL_M = 3667.0, 40.0
SEARCH_M = (0.5, 14.0)    # Betreiber 11-13 m breit, OSM liegt nicht immer mittig
HIGHPASS_SIGMA_PX, LEVEL_BLACK, LEVEL_WHITE = 6.0, 128.0, 178.0   # wie die Linienkarte in PhotoCraft
ASPHALT_SAT_MAX = 0.13    # wie spreewaldring_track_surface.py: Asphalt und Linie entsaettigt, Gras und Kies nicht
ASPHALT_VALUE_MIN = 60    # Schatten und Leitplanken raus

PIT_WAY = 89254337                 # OSM, Fahrtrichtung Einfahrt -> Ausfahrt
PIT_ENTRY_NODES, PIT_EXIT_NODES = (0, 34), (34, 52)
PIT_AXIS_EAST = np.array([656437.41, 5766493.02])   # rote Nutzerlinie, Fahrtrichtung nach Westen
PIT_AXIS_WEST = np.array([656180.95, 5766568.66])
PIT_WORK_S_M = (13.9, 263.5)       # gruene Nutzerlinie, gerades Stueck, Station ab PIT_AXIS_EAST
PIT_EXIT_VIA = np.array([656172.49, 5766570.95])    # Ende der gruenen Linie Richtung Ausfahrt
PADDOCK_EAST = np.array([[656446.49, 5766468.58], [656449.12, 5766476.69], [656437.06, 5766486.56]])  # gelb, Fahrerlager -> Boxen
PADDOCK_WEST = np.array([[656208.00, 5766548.59], [656203.18, 5766529.30]])                           # gelb, Boxen -> Fahrerlager
WALL_BAND_M, BUILDING_BAND_M = (0.4, 3.2), (-12.0, -9.0)   # quer zur Achse, Nord positiv, aus dem Querprofil
DASH_BAND_M = (-5.5, -3.2)         # relativ zum Mauerfuss, die Schattenkante liegt bei -1,6 m
PIT_SEARCH_M = (0.5, 9.0)
# Boxenstart in der Editor-Wiedergabe (Nutzerskizze 08.11.2026)
PIT_LIMIT_END = np.array([656180.11, 5766564.81])   # gelbe Linie = weisse Querlinie am Westende der Boxen, bis hier 80 km/h
PIT_JOIN_LINE = np.array([[655937.63, 5766577.72], [655915.77, 5766590.67],
                          [655902.28, 5766600.92], [655893.78, 5766608.75]])  # orange Linie ab der Kehre; erster Skizzenpunkt
                                                                              # lag innen in der Kehre (R 9 m statt Fahrbahnmitte)
PIT_PULLOUT_M = 30.0               # Weg aus dem Arbeitsbereich auf die Mitte der Fastlane
PIT_PATH_SMOOTH_M = 3.0
PIT_EXIT_BLEND_M = 40              # Ausfahrt-Mittellinie erst ab hier: am Uebergang liegt sie rund 3 m neben der Fastlane
PIT_ENTRY_START = np.array([656720.8, 5766528.4])   # weisse Querlinie vor Kurve 14, davor ist die Einfahrt noch Strecke


def fetch(url, params):
    with urllib.request.urlopen(urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                                       headers={"User-Agent": USER_AGENT}), timeout=170) as r:
        return r.read()


def fetch_rasters(bbox, px=0.2, tile=4000):
    """Orthofoto und DGM fuer bbox (min_e, min_n, max_e, max_n), Pixelraster am Kachelrand ausgerichtet."""
    min_e, min_n, max_e, max_n = bbox
    if not os.path.exists(DOP_PATH):
        w, h = int(round((max_e - min_e) / px)), int(round((max_n - min_n) / px))
        canvas = Image.new("RGB", (w, h))
        for row in range(0, h, tile):
            for col in range(0, w, tile):
                tw, th = min(tile, w - col), min(tile, h - row)
                box = (min_e + col * px, max_n - (row + th) * px, min_e + (col + tw) * px, max_n - row * px)
                data = fetch(WMS_URL, dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", STYLES="", CRS="EPSG:25832",
                                           LAYERS="lsa_lvermgeo_dop20_2", FORMAT="image/png", WIDTH=tw, HEIGHT=th,
                                           BBOX=",".join(f"{x:.1f}" for x in box)))
                canvas.paste(Image.open(io.BytesIO(data)).convert("RGB"), (col, row))
        canvas.save(DOP_PATH)
        with open(DOP_GEO_PATH, "w", encoding="utf-8") as f:
            json.dump({"crs": "EPSG:25832", "origin_e": min_e, "origin_n": max_n, "px": px,
                       "license": "dl-de/by-2-0, LVermGeo Sachsen-Anhalt"}, f)
    if not os.path.exists(DGM_PATH):
        data = fetch(WCS_URL, [("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"),
                               ("COVERAGEID", "Coverage1"), ("FORMAT", "image/tiff"),
                               ("SUBSET", f"x({min_e:.0f},{max_e:.0f})"), ("SUBSET", f"y({min_n:.0f},{max_n:.0f})")])
        start = min(i for i in (data.find(b"II*\x00"), data.find(b"MM\x00*")) if i >= 0)  # Antwort ist multipart
        end = data.rfind(b"\n--wcs")
        with open(DGM_PATH, "wb") as f:
            f.write(data[start:end if end > start else None])


def fetch_osm(bbox=(52.015, 11.26, 52.04, 11.30)):
    if os.path.exists(OSM_PATH):
        with open(OSM_PATH, encoding="utf-8") as f:
            return json.load(f)
    s, w, n, e = bbox
    query = f'[out:json][timeout:60];way["highway"="raceway"]({s},{w},{n},{e});out tags geom;'
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": query}).encode(),
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=100) as r:
        osm = json.load(r)
    with open(OSM_PATH, "w", encoding="utf-8") as f:
        json.dump(osm, f)
    return osm


def chain_ways(ways, order):
    """Punkte der geschlossenen Runde aus Wegen in Fahrtrichtung. Der Ein-/Ausstieg in einen Weg darf an einem inneren
    Knoten liegen; jeder Anschluss muss ein gemeinsamer Knoten sein, sonst gibt es einen Fehler statt einer Annahme."""
    out = []
    for prev, way, nxt in zip(np.roll(order, 1), order, np.roll(order, -1)):
        pts = ways[int(way)]
        def index_of(other):
            common = [i for i, p in enumerate(pts) if p in set(ways[int(other)])]
            if not common:
                raise ValueError(f"Weg {way} hat keinen gemeinsamen Knoten mit {other}")
            return common
        out += pts[index_of(prev)[0]:index_of(nxt)[-1]]  # Endknoten kommt als Anfang des naechsten Wegs
    return out


def line_map(rgb):
    """Rezept der PhotoCraft-Linienkarte: Graustufen, Hochpass, Tonwerte 128..178."""
    gray = rgb.mean(axis=2)
    return np.clip((gray - gaussian_filter(gray, HIGHPASS_SIGMA_PX) + 128.0 - LEVEL_BLACK) / (LEVEL_WHITE - LEVEL_BLACK),
                   0, 1).astype(np.float32)


def asphalt_map(rgb):
    value, low = rgb.max(axis=2), rgb.min(axis=2)
    return (((value - low) / np.maximum(value, 1.0) < ASPHALT_SAT_MAX) & (value > ASPHALT_VALUE_MIN)).astype(np.float32)


class Dgm:
    """Gleiche Abfrage wie ElevationModel, aber aus dem einen GeoTIFF (1 m, EPSG:25832)."""

    def __init__(self, path):
        with rasterio.open(path) as d:
            self.z = d.read(1).astype(np.float32)
            self.inv = ~d.transform

    def get_elevation(self, e, n):
        col, row = self.inv * (e, n)
        return map_coordinates(self.z, [row - 0.5, col - 0.5], order=1, mode="nearest")


def fit_line(s, y, tol=0.4):
    """Gerade y(s), Ausreisser (Leute, Fahrzeuge, Stuetzen) iterativ raus."""
    keep = np.ones(len(s), bool)
    for _ in range(4):
        coef = np.polyfit(s[keep], y[keep], 1)
        keep = np.abs(np.polyval(coef, s) - y) < tol
    return coef, float(keep.mean())


def fit_edge(gray, geo, points, north, band, sign):
    """Staerkster Helligkeitssprung im Band quer zur Achse je Station, als Gerade. sign +1: nach Norden heller."""
    offsets = np.arange(band[0], band[1], 0.05)
    profile = gaussian_filter(M.sample_across(gray, points, north, offsets, geo), (1.5, 2.0))
    edge = offsets[np.argmax(sign * np.gradient(profile, axis=1), axis=1)]
    return fit_line(np.arange(len(points), dtype=float), edge)


def strip_segment(name, left, right, dgm):
    """Fahrstreifen aus zwei Raendern in Fahrtrichtung, Schema wie build_segment()."""
    center = (left + right) / 2
    z_l, z_c, z_r = (M.gaussian_filter1d(dgm.get_elevation(p[:, 0], p[:, 1]), M.Z_SMOOTH_M, mode="nearest")
                     for p in (left, center, right))
    ds = np.hypot(*np.diff(center, axis=0).T)
    width = np.hypot(*(left - right).T)
    return dict(name=name, closed=False, s_m=np.r_[0, np.cumsum(ds)], center=center, left=left, right=right,
                z_center=z_c, z_left=z_l, z_right=z_r, width_m=width, grade=np.gradient(z_c, M.STEP_M),
                bank=(z_l - z_r) / width, length_2d_m=float(ds.sum()),
                length_3d_m=float(np.hypot(ds, np.diff(z_c)).sum()))


def center_segment(name, points, dgm):
    """Nur Mittellinie: Anbindung ueber offene Flaeche, ohne erkennbare Raender."""
    center = M.resample(points, M.STEP_M, False)
    ds = np.hypot(*np.diff(center, axis=0).T)
    return dict(name=name, closed=False, s_m=np.r_[0, np.cumsum(ds)], center=center, edges_known=False,
                z_center=dgm.get_elevation(center[:, 0], center[:, 1]), length_2d_m=float(ds.sum()))


def build_pit(guide_entry, guide_exit, line_img, asphalt_img, gray, geo, dgm):
    """Einfahrt und Ausfahrt aus dem Bild, Fastlane und Arbeitsbereich aus den gefitteten Kanten."""
    axis = PIT_AXIS_WEST - PIT_AXIS_EAST
    length = float(np.hypot(*axis))
    u = axis / length
    n_stations = int(length) + 1
    points = PIT_AXIS_EAST + np.arange(n_stations)[:, None] * u
    north = np.tile([u[1], -u[0]], (n_stations, 1))
    wall, seen_wall = fit_edge(gray, geo, points, north, WALL_BAND_M, -1)
    building, seen_building = fit_edge(gray, geo, points, north, BUILDING_BAND_M, -1)
    st = np.arange(n_stations, dtype=float)
    foot = points + north * np.polyval(wall, st)[:, None]
    rel = np.arange(DASH_BAND_M[0], DASH_BAND_M[1], 0.05)
    profile = np.percentile(M.sample_across(gray, foot, north, rel, geo), 92, axis=0)
    dash = float(rel[np.argmax(gaussian_filter(profile, 4) - gaussian_filter(profile, 30))])
    dashes = foot + north * dash
    front = points + north * np.polyval(building, st)[:, None]
    work = (st >= PIT_WORK_S_M[0]) & (st <= PIT_WORK_S_M[1])
    fastlane = strip_segment("pit_fastlane", dashes, foot, dgm)
    work_area = strip_segment("pit_work_area", front[work], dashes[work], dgm)
    print(f"Boxen: Mauerfuss/Gebaeudefront passen zu {seen_wall * 100:.0f} / {seen_building * 100:.0f} % der Stationen, "
          f"Markierung {-dash:.2f} m neben dem Mauerfuss")

    first = int(np.argmin(np.linalg.norm(guide_entry - PIT_ENTRY_START, axis=1)))
    entry_guide = np.vstack([PIT_ENTRY_START, guide_entry[first + 1:], fastlane["center"][:1]])
    exit_guide = np.vstack([fastlane["center"][-1:], PIT_EXIT_VIA, guide_exit])
    entry, pit_exit = (M.build_segment(name, g, line_img, asphalt_img, geo, dgm, False, PIT_SEARCH_M, True, gray)
                       for name, g in (("pit_entry", entry_guide), ("pit_exit", exit_guide)))
    return [entry, fastlane, work_area, pit_exit, center_segment("paddock_east", PADDOCK_EAST, dgm),
            center_segment("paddock_west", PADDOCK_WEST, dgm)]


def pit_out_path(model):
    """Weg ab der mittleren Box bis zum Anschluss an die Ideallinie, 1-m-Raster, UTM. Liefert (Punkte, Station des Limit-Endes).
    model: "segments" des Streckenmodells. Aus dem Arbeitsbereich laeuft die Linie weich auf die Mitte der Fastlane."""
    fast, work, out = (np.array(model[k]["center"]) for k in ("pit_fastlane", "pit_work_area", "pit_exit"))
    first_work = int(np.ceil(PIT_WORK_S_M[0]))         # Station der ersten Zeile des Arbeitsbereichs
    box = first_work + len(work) // 2
    fade = np.clip((np.arange(len(fast)) - box) / PIT_PULLOUT_M, 0, 1)
    offset = np.zeros_like(fast)
    in_work = np.arange(first_work, min(len(fast), first_work + len(work)))
    offset[in_work] = work[in_work - first_work] - fast[in_work]
    lane = (fast + offset * (0.5 + 0.5 * np.cos(np.pi * fade))[:, None])[box:]
    last = int(np.argmin(np.linalg.norm(out - PIT_JOIN_LINE[0], axis=1)))
    path = M.resample(np.vstack([lane, out[PIT_EXIT_BLEND_M:last], PIT_JOIN_LINE]), M.STEP_M, False)
    path = M.gaussian_filter1d(path, PIT_PATH_SMOOTH_M, axis=0, mode="nearest")
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(path, axis=0).T))]
    return path, float(s[int(np.argmin(np.linalg.norm(path - PIT_LIMIT_END, axis=1)))])


def write_model(segments, geo, photo):
    out = {}
    for seg in segments:
        d = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in seg.items()}
        if seg["name"] == "main_loop":
            r = np.minimum(seg["radius_m"], 500.0)
            d["apexes"] = [{"s_m": float(seg["s_m"][i]), "radius_m": float(r[i])}
                           for i in argrelmin(r, order=25, mode="wrap")[0] if r[i] < M.CORNER_RADIUS_MAX_M]
            d["radius_m"] = [None if not np.isfinite(x) else x for x in seg["radius_m"].tolist()]
        elif d.get("radius_m") is None:
            d.pop("radius_m", None)
        out[seg["name"]] = d
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(MODEL_JSON, "w", encoding="utf-8") as f:
        json.dump({"source": "OSM + DOP20 + DGM1 (LVermGeo Sachsen-Anhalt, dl-de/by-2-0)", "crs": "EPSG:25832",
                   "height_system": "DHHN2016 m", "step_m": M.STEP_M, "segments": out}, f, ensure_ascii=False)
    draw = ImageDraw.Draw(photo)
    to_px = lambda pts: [((e - geo[0]) / geo[2], (geo[1] - n) / geo[2]) for e, n in pts]
    for seg in segments:
        if seg["name"] == "main_loop":
            for key, color in (("left", (255, 40, 40)), ("right", (40, 170, 255))):
                draw.line(to_px(seg[key]) + to_px(seg[key])[:1], fill=color, width=1)
        elif "left" in seg:
            for key, color in (("left", (255, 200, 0)), ("right", (0, 255, 120))):
                draw.line(to_px(seg[key]), fill=color, width=1)
        else:
            draw.line(to_px(seg["center"]), fill=(255, 0, 255), width=2)
    photo.save(OVERLAY_PNG)


def main():
    osm = fetch_osm()
    to_utm = Transformer.from_crs(4326, 25832, always_xy=True)
    ways = {e["id"]: [(p["lon"], p["lat"]) for p in e["geometry"]] for e in osm["elements"]
            if e["type"] == "way" and e["tags"].get("highway") == "raceway"}
    lonlat = np.array(chain_ways(ways, np.array(MAIN_LOOP_WAYS)))
    guide = np.column_stack(to_utm.transform(lonlat[:, 0], lonlat[:, 1]))
    osm_len = float(np.hypot(*(np.roll(guide, -1, axis=0) - guide).T).sum())
    assert abs(osm_len - OSM_LENGTH_M) < OSM_LENGTH_TOL_M, f"OSM-Runde {osm_len:.0f} m passt nicht zum A-Kurs"
    print(f"OSM-Runde: {len(guide)} Knoten, {osm_len:.1f} m (Betreiber {OSM_LENGTH_M:.0f} m)")

    all_pts = np.array([to_utm.transform(*p) for pts in ways.values() for p in pts])
    fetch_rasters((*(np.floor((all_pts.min(axis=0) - 40) / 10) * 10), *(np.ceil((all_pts.max(axis=0) + 40) / 10) * 10)))
    with open(DOP_GEO_PATH, encoding="utf-8") as f:
        g = json.load(f)
    photo = Image.open(DOP_PATH).convert("RGB")
    rgb = np.asarray(photo, dtype=np.float32)
    # sample_across() zieht CROP_COL/CROP_ROW des Spreewaldring-Ausschnitts ab, hier ist der Ausschnitt das ganze Bild
    geo = (g["origin_e"] - M.CROP_COL * g["px"], g["origin_n"] + M.CROP_ROW * g["px"], g["px"])
    lines, asphalt, dgm = line_map(rgb), asphalt_map(rgb), Dgm(DGM_PATH)
    seg = M.build_segment("main_loop", guide, lines, asphalt, geo, dgm, True, search=SEARCH_M)
    pit_lonlat = np.array(ways[PIT_WAY])
    pit_utm = np.column_stack(to_utm.transform(pit_lonlat[:, 0], pit_lonlat[:, 1]))
    pit = build_pit(pit_utm[slice(*PIT_ENTRY_NODES)], pit_utm[slice(*PIT_EXIT_NODES)], lines, asphalt,
                    rgb.mean(axis=2) / 255.0, geo, dgm)
    write_model([seg, *pit], (g["origin_e"], g["origin_n"], g["px"]), photo)
    for p in pit:
        extra = f", Breite Median {np.median(p['width_m']):.2f} m" if "width_m" in p else ""
        print(f"{p['name']}: {p['length_2d_m']:.1f} m{extra}")

    w = seg["width_m"]
    print(f"Laenge 2D {seg['length_2d_m']:.1f} m, 3D {seg['length_3d_m']:.1f} m, Breite Median {np.median(w):.2f} m "
          f"(p5 {np.percentile(w, 5):.2f}, p95 {np.percentile(w, 95):.2f}), Linie gesehen links "
          f"{seg['left_line_seen'].mean() * 100:.0f} %, rechts {seg['right_line_seen'].mean() * 100:.0f} %")
    print(f"Hoehe {seg['z_center'].min():.2f}-{seg['z_center'].max():.2f} m, Steigung |p50| "
          f"{np.median(np.abs(seg['grade'])) * 100:.2f} %, |p95| {np.percentile(np.abs(seg['grade']), 95) * 100:.2f} %, "
          f"Querneigung |p50| {np.median(np.abs(seg['bank'])) * 100:.2f} %")
    print(f"Abstand OSM-Knoten zur Mittellinie: p95 "
          f"{np.percentile(np.linalg.norm(seg['center'][:, None] - guide[None], axis=2).min(axis=0), 95):.1f} m")


if __name__ == "__main__":
    main()
