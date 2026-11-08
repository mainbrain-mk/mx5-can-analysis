"""
Motorsport Arena Oschersleben - Streckenmodell aus OSM, Orthofoto und DGM, 08.11.2026

Selbes Verfahren wie spreewaldring_track_model.py (Viterbi auf der weissen Randlinie, build_segment() von dort).
Unterschiede: Die Karten kommen direkt aus dem Orthofoto (dasselbe Rezept wie in PhotoCraft, siehe line_map()), und nur
die Hauptschleife wird gebaut, keine Boxengasse. Die JSON-Datei hat das Schema von spreewaldring_track_model.json.

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


def write_model(seg, geo, photo):
    d = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in seg.items()}
    r = np.minimum(seg["radius_m"], 500.0)
    d["apexes"] = [{"s_m": float(seg["s_m"][i]), "radius_m": float(r[i])}
                   for i in argrelmin(r, order=25, mode="wrap")[0] if r[i] < M.CORNER_RADIUS_MAX_M]
    d["radius_m"] = [None if not np.isfinite(x) else x for x in seg["radius_m"].tolist()]
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(MODEL_JSON, "w", encoding="utf-8") as f:
        json.dump({"source": "OSM + DOP20 + DGM1 (LVermGeo Sachsen-Anhalt, dl-de/by-2-0)", "crs": "EPSG:25832",
                   "height_system": "DHHN2016 m", "step_m": M.STEP_M, "segments": {"main_loop": d}}, f, ensure_ascii=False)
    draw = ImageDraw.Draw(photo)
    for key, color in (("left", (255, 40, 40)), ("right", (40, 170, 255))):
        px = [((e - geo[0]) / geo[2], (geo[1] - n) / geo[2]) for e, n in seg[key]]
        draw.line(px + px[:1], fill=color, width=1)
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
    seg = M.build_segment("main_loop", guide, line_map(rgb), asphalt_map(rgb), geo, Dgm(DGM_PATH), True, search=SEARCH_M)
    write_model(seg, (g["origin_e"], g["origin_n"], g["px"]), photo)

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
