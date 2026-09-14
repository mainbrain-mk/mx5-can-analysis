"""
Digitales Gelaendemodell (DGM) aus ALS-Punktwolken (LGB Brandenburg,
EPSG:25833) fuer die gefahrenen Strecken.

Rohdaten: `hoehendaten/als_<E_km><zone-prefix>-<N_km>.zip`, je Kachel ein
LAZ-Punktwolke (LAS 1.4, ~1000x1000m, Klassifizierung 2 = Boden).

Speicherstruktur (kachelbasiert, erweiterbar): jede Kachel wird EINZELN zu
einem 1m-Gitter gerastert (Mittelwert Bodenpunkte/Zelle) und als eigene
Datei in `data/elevation/tiles/<e_km>_<n_km>.npz` abgelegt. Neue Kacheln
in `hoehendaten/` hinzufuegen + `--rebuild` erneut laufen lassen baut NUR
die fehlenden Kachel-Dateien neu (kein Neuaufbau der bestehenden - skaliert
auf beliebig viele, auch geografisch weit verstreute Kacheln, ohne dass
ein einzelnes dichtes Gitter ueber die gesamte Bounding Box gehalten
werden muss). `ElevationModel` laedt Kacheln erst bei Bedarf und haelt nur
die zuletzt genutzten im RAM (LRU-Cache, `tile_cache_size`).

Andere Skripte nutzen `get_elevation()`/`get_elevation_along_track()` fuer
Hoehenabfragen (z.B. Steigungskorrektur von a_long, spaetere
Streckensimulation).

BERLIN-FALLBACK: die LGB-Brandenburg-Kacheln decken Berlin selbst NICHT ab
(eigenstaendige Quelle, siehe PROJEKT_STAND.md). Fuer Punkte ohne lokale
Kachel-Abdeckung fragt `get_elevation()` automatisch den Live-WMS-Dienst
der Berliner Geodateninfrastruktur ab (`BERLIN_WMS_URL`, Layer `c_dgm1`,
GetFeatureInfo, liefert "Hoehe_in_Meter_ueber_NHN") - gleiches Koordinaten-
/Hoehensystem (EPSG:25833, DHHN2016), daher direkt kompatibel. Jede Abfrage
wird auf Meter gerundet in `BERLIN_CACHE_PATH` (JSON) zwischengespeichert
und VOR jeder neuen Live-Abfrage zuerst dort nachgeschlagen - vermeidet
wiederholte Netzwerkabfragen fuer dieselbe Stelle. Kein Bulk-Download,
daher kein lokales Raster - nur einzeln abgefragte Punkte landen im Cache.
Netzwerkfehler/Timeouts werden abgefangen (liefern NaN wie eine echte
Datenluecke), kein harter Fehler.

WICHTIG - Bruecken: die "Boden"-Klassifizierung (Klasse 2) der ALS-Punkte
folgt bei Bruecken/Ueberfuehrungen dem UNTERLIEGENDEN Gelaende (Tal/Fluss/
Strasse darunter), NICHT der Fahrbahnoberkante der Bruecke - empirisch an
mehreren echten Bruecken verifiziert (29.08.2026, siehe PROJEKT_STAND.md).
`get_elevation()` liefert dort daher entweder eine Luecke (NaN) oder einen
falschen, zu tiefen Wert. Deshalb: fuer Hoehenprofile entlang einer
gefahrenen Strecke IMMER `get_elevation_along_track()` verwenden, nicht
`get_elevation()` direkt - das erkennt per OSM-Bruecken-Overlay
automatisch Bruecken-Ueberquerungen und interpoliert linear (nach
zurueckgelegter Strecke) zwischen den Werten an den Bruecken-Enden.
Bekannte Luecke: Bruecken ohne `bridge`-Tag auf der Hauptfahrbahn in OSM
werden NICHT erkannt (siehe PROJEKT_STAND.md, Anschlussstelle Stolpe).

DGM-XYZ-FALLBACK (NEU, 06.09.2026): fuer 3 Kacheln suedlich der bisherigen
Kernregion (E407/N5772, E407/N5776, E426/N5743 - neue Fahrtroute Richtung
Spreewald/Koenigs Wusterhausen) lieferte die ALS-Punktwolken-Quelle
(`als_*.zip`) HTTP 404, obwohl die Flaeche eindeutig in Brandenburg liegt.
Fund: die LGB bietet PARALLEL ein bereits fertig gerastertes 1m-DGM als
XYZ-Text an (`https://data.geobasis-bb.de/geobasis/daten/dgm/xyz/
dgm_<E_km><zone>-<N_km>.zip`, Inhalt "E N Z" pro Zeile, 1000x1000 Punkte,
gleiches Gitter/CRS wie die ALS-Rasterung) - dort waren alle 3 Kacheln
verfuegbar. Vermutlich publiziert die LGB das fertige DGM flaechendeckender
als die rohen Punktwolken. Deutlich kleinere Dateien (~4MB statt ~180MB)
UND kein Klassifizierungs-/Rasterungsschritt noetig (schon fertiges Gitter)
- macht diese Quelle als GENERELLEN Ergaenzungs-Fallback attraktiv, nicht
nur fuer diese 3 Kacheln. `build_all_tiles()` verarbeitet deshalb jetzt
zusaetzlich `hoehendaten/dgm_*.zip` (gleicher Ordner wie die ALS-Zips) fuer
alle Kacheln, die NICHT bereits per ALS abgedeckt sind (ALS hat Vorrang,
kein Ueberschreiben bestehender Kacheln) - erzeugt exakt dasselbe
npz-Schema, daher fuer den Rest von `elevation_model.py`/`ElevationModel`
voellig transparent.

Aufruf zum (Neu-)Aufbau (nur fehlende Kacheln + ggf. neue Bruecken-Bbox) +
Plausibilitaetscheck gegen die geloggte GPS-Hoehe (Kanal "Hoehe" im
Datalake):
    .venv/bin/python scripts/elevation_model.py [--rebuild]
"""

import io
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from collections import OrderedDict
from pathlib import Path

import duckdb
import laspy
import numpy as np
import pandas as pd
import pyproj
import rasterio

HOEHENDATEN_DIR = Path("höhendaten")
TILES_DIR = Path("data/elevation/tiles")
BRIDGE_CACHE_PATH = Path("data/elevation/osm_bridges.json")
DATALAKE_PATH = Path("data/datalake.duckdb")
GRID_RES_M = 1.0
TILE_SIZE_M = 1000
GROUND_CLASS = 2
CRS = "EPSG:25833"
BRIDGE_BUFFER_M = 15.0
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_TILE_CACHE_SIZE = 64  # ~64 Kacheln * ~4MB (dicht, unkomprimiert im RAM) = ~256MB max

# Berlin-Fallback (siehe Docstring): Brandenburg-Kacheln decken Berlin nicht ab,
# daher Live-Abfrage gegen den Berliner DGM1-WMS statt lokaler Kacheln.
BERLIN_CACHE_PATH = Path("data/elevation/berlin_wms_cache.json")
BERLIN_WMS_URL = "https://gdi.berlin.de/services/wms/dgm1"
BERLIN_WMS_LAYER = "c_dgm1"
BERLIN_WMS_TIMEOUT_S = 10
# grober Sicherheitsrahmen (Berlin + naechste Umgebung), um sinnlose Live-
# Abfragen fuer offensichtlich falsche/weit entfernte Koordinaten zu vermeiden
BERLIN_WMS_BBOX_E = (350000, 420000)
BERLIN_WMS_BBOX_N = (5800000, 5860000)

# Ausserhalb Brandenburg/Berlin (EPSG:25833): weitere Bundeslaender fuer
# Fahrten ausserhalb der ueblichen Berlin-Brandenburg-Schleife (z.B.
# Suedtirol-Rueckfahrt ueber Bayern/Thueringen/Sachsen-Anhalt, 31.07.2026,
# siehe PROJEKT_STAND.md). Alle drei liegen in UTM-Zone 32 (EPSG:25832),
# ANDERS als Brandenburg/Berlin (EPSG:25833) - eigener Transformer noetig.
_LATLON_TO_UTM32 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)

# Bayern: LDBV OpenData, GeoTIFF-Einzelkacheln 1km x 1km, DGM1 (1m), frei ohne
# Anmeldung. Kachel-Namensschema per Metalink verifiziert (29./30.08.2026):
# https://geodaten.bayern.de/odd/a/dgm/dgm1/meta/metalink/09.meta4
BAYERN_TILES_DIR = Path("data/elevation/bayern_tiles")
BAYERN_TILE_URL = "https://download1.bayernwolke.de/a/dgm/dgm1/{e_km}_{n_km}.tif"

# Thueringen: Geoportal-th Download-Client, ZIP mit XYZ-Textdatei (2m-Raster,
# DGM2 - kein DGM1 oeffentlich per Direktlink gefunden), Jahrgang 2010-2013
# fuer unsere Region einzig verfuegbarer Jahrgang (geprueft 30.08.2026, kein
# 2014-2019 fuer die benoetigten Kacheln). Lizenz: Datenlizenz Deutschland -
# Namensnennung 2.0.
THUERINGEN_TILES_DIR = Path("data/elevation/thueringen_tiles")
THUERINGEN_TILE_URL = ("https://geoportal.geoportal-th.de/hoehendaten/DGM/dgm_2010-2013/"
                        "dgm2_{e_km}_{n_km}_1_th_2010-2013.zip")
THUERINGEN_GRID_RES_M = 2.0

# Sachsen-Anhalt: kein handhabbarer Direktdownload gefunden (DGM1 nur als 4
# Gesamt-Kacheln je 8-11GB fuer das ganze Land) - stattdessen Live-Abfrage
# ueber den offiziellen INSPIRE-WCS (2.0.1, GetCoverage auf ein kleines
# Fenster um den Punkt), analog zum Berlin-WMS-Fallback samt Cache.
SACHSEN_ANHALT_WCS_URL = "https://geodatenportal.sachsen-anhalt.de/ows_INSPIRE_LVermGeo_ATKIS_EL_DGM_WCS"
SACHSEN_ANHALT_CACHE_PATH = Path("data/elevation/sachsen_anhalt_wcs_cache.json")
SACHSEN_ANHALT_WCS_TIMEOUT_S = 15
SACHSEN_ANHALT_BBOX_E = (606000, 790000)
SACHSEN_ANHALT_BBOX_N = (5646000, 5882000)

TILE_NAME_RE = re.compile(r"als_(\d{2})(\d{3})-(\d{4})")
DGM_XYZ_TILE_NAME_RE = re.compile(r"dgm_(\d{2})(\d{3})-(\d{4})")
DGM_XYZ_URL = "https://data.geobasis-bb.de/geobasis/daten/dgm/xyz/dgm_{zone}{e_km:03d}-{n_km}.zip"

_LATLON_TO_UTM33 = pyproj.Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
_UTM33_TO_LATLON = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)


def _tile_key(zip_path: Path) -> tuple[int, int]:
    """(e_km, n_km) aus dem Dateinamen, OHNE die fuehrenden UTM-Zonen-Ziffern."""
    m = TILE_NAME_RE.match(zip_path.stem)
    if not m:
        raise ValueError(f"Unerwartetes Kachel-Namensschema: {zip_path.name}")
    _zone, e_km, n_km = m.groups()
    return int(e_km), int(n_km)


def _dgm_xyz_tile_key(zip_path: Path) -> tuple[int, int]:
    """Wie _tile_key(), fuer das DGM-XYZ-Namensschema (dgm_<zone><e_km>-<n_km>)."""
    m = DGM_XYZ_TILE_NAME_RE.match(zip_path.stem)
    if not m:
        raise ValueError(f"Unerwartetes DGM-XYZ-Namensschema: {zip_path.name}")
    _zone, e_km, n_km = m.groups()
    return int(e_km), int(n_km)


def _tile_path(e_km: int, n_km: int) -> Path:
    return TILES_DIR / f"{e_km}_{n_km}.npz"


def _load_tile_ground_points(zip_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with zipfile.ZipFile(zip_path) as zf:
        laz_name = next(n for n in zf.namelist() if n.endswith(".laz"))
        data = zf.read(laz_name)
    las = laspy.read(io.BytesIO(data))
    ground = las.classification == GROUND_CLASS
    return np.asarray(las.x[ground]), np.asarray(las.y[ground]), np.asarray(las.z[ground])


RAW_POINT_CACHE_SIZE = 8  # deutlich kleiner als der 1m-Gitter-Cache (DEFAULT_TILE_CACHE_SIZE=64,
                          # je ~4MB) - rohe Punktwolken sind ~300MB/Kachel selbst als float32
_raw_point_cache: "OrderedDict[tuple[int, int], dict | None]" = OrderedDict()


def get_raw_ground_points(e_km: int, n_km: int) -> dict | None:
    """Lazy-geladene, LRU-gecachte rohe ALS-Bodenpunkte (Klasse 2, volle native
    Dichte ~12-15 Pkt/m2) einer 1km-Kachel - im Gegensatz zu get_elevation()/
    get_tile(), die nur das daraus abgeleitete 1m-Raster liefern. Nur fuer die
    166/169 Brandenburg-Kacheln mit lokaler `als_*.zip` (siehe hoehendaten/) -
    liefert None fuer die 3 Kacheln, die nur ueber den DGM-XYZ-Fallback
    (bereits fertig gerastert, kein Rohpunktwolken-Vorteil) abgedeckt sind, und
    fuer alle Kacheln ausserhalb Brandenburgs. Rueckgabe als float32 (statt
    laspy's float64) - bei UTM33-Koordinaten (~370000-380000) noch << 1cm
    Praezisionsverlust, halbiert aber den Speicherbedarf im Cache.
    Genutzt von `segment_grade()`'s native-Praezisionspfad, siehe dort."""
    key = (e_km, n_km)
    if key in _raw_point_cache:
        _raw_point_cache.move_to_end(key)
        return _raw_point_cache[key]
    zip_path = HOEHENDATEN_DIR / f"als_33{e_km:03d}-{n_km}.zip"
    if not zip_path.exists():
        result = None
    else:
        x, y, z = _load_tile_ground_points(zip_path)
        result = {"e": x.astype(np.float32), "n": y.astype(np.float32), "z": z.astype(np.float32)}
    _raw_point_cache[key] = result
    if len(_raw_point_cache) > RAW_POINT_CACHE_SIZE:
        _raw_point_cache.popitem(last=False)
    return result


def build_tile(zip_path: Path) -> None:
    """Rastert eine einzelne Kachel zu einem 1000x1000-Gitter (@ GRID_RES_M)
    und speichert sie einzeln - unabhaengig von allen anderen Kacheln."""
    e_km, n_km = _tile_key(zip_path)
    e0, n0 = e_km * 1000, n_km * 1000
    x, y, z = _load_tile_ground_points(zip_path)

    ncells = int(round(TILE_SIZE_M / GRID_RES_M))
    col = np.floor((x - e0) / GRID_RES_M).astype(np.int64)
    row = np.floor((y - n0) / GRID_RES_M).astype(np.int64)
    valid = (col >= 0) & (col < ncells) & (row >= 0) & (row < ncells)
    col, row, z = col[valid], row[valid], z[valid]

    z_sum = np.zeros((ncells, ncells), dtype=np.float64)
    z_count = np.zeros((ncells, ncells), dtype=np.int32)
    np.add.at(z_sum, (row, col), z)
    np.add.at(z_count, (row, col), 1)

    with np.errstate(invalid="ignore"):
        grid = np.where(z_count > 0, z_sum / np.maximum(z_count, 1), np.nan).astype(np.float32)

    coverage = np.isfinite(grid).mean() * 100
    TILES_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        _tile_path(e_km, n_km),
        grid=grid,
        origin_e=e0,
        origin_n=n0,
        resolution=GRID_RES_M,
    )
    print(f"  {zip_path.name}: {valid.sum():,} Bodenpunkte, Abdeckung {coverage:.1f}% -> "
          f"{_tile_path(e_km, n_km)}")


def build_tile_from_xyz(zip_path: Path) -> None:
    """Wie build_tile(), aber fuer die fertig gerasterte DGM-XYZ-Quelle
    (siehe Docstring oben) statt einer ALS-Punktwolke. Schon ein 1x1m-Gitter
    (eine Zeile "E N Z" pro Zelle) - keine Mittelwertbildung/Klassifizierung
    noetig, aber ueber dieselbe sum/count-Logik wie build_tile() gefuehrt
    (robust gegen evtl. doppelte/fehlende Zeilen), Ergebnis-Schema identisch."""
    e_km, n_km = _dgm_xyz_tile_key(zip_path)
    e0, n0 = e_km * 1000, n_km * 1000
    with zipfile.ZipFile(zip_path) as zf:
        xyz_name = next(n for n in zf.namelist() if n.endswith(".xyz"))
        raw = zf.read(xyz_name)
    # Trennzeichen ist nicht einheitlich (manche Kacheln Leerzeichen, andere
    # Komma, je nach Erstellungs-/Exportjahr) - Kommas vor dem Parsen durch
    # Leerzeichen ersetzen macht np.loadtxt robust gegen beide Varianten.
    arr = np.loadtxt(io.BytesIO(raw.replace(b",", b" ")))
    x, y, z = arr[:, 0], arr[:, 1], arr[:, 2]

    ncells = int(round(TILE_SIZE_M / GRID_RES_M))
    col = np.floor((x - e0) / GRID_RES_M).astype(np.int64)
    row = np.floor((y - n0) / GRID_RES_M).astype(np.int64)
    valid = (col >= 0) & (col < ncells) & (row >= 0) & (row < ncells)
    col, row, z = col[valid], row[valid], z[valid]

    z_sum = np.zeros((ncells, ncells), dtype=np.float64)
    z_count = np.zeros((ncells, ncells), dtype=np.int32)
    np.add.at(z_sum, (row, col), z)
    np.add.at(z_count, (row, col), 1)

    with np.errstate(invalid="ignore"):
        grid = np.where(z_count > 0, z_sum / np.maximum(z_count, 1), np.nan).astype(np.float32)

    coverage = np.isfinite(grid).mean() * 100
    TILES_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        _tile_path(e_km, n_km),
        grid=grid,
        origin_e=e0,
        origin_n=n0,
        resolution=GRID_RES_M,
    )
    print(f"  {zip_path.name}: {valid.sum():,} Gitterpunkte (DGM-XYZ), Abdeckung {coverage:.1f}% -> "
          f"{_tile_path(e_km, n_km)}")


def build_all_tiles(force: bool = False) -> list[tuple[int, int]]:
    """Baut alle Kacheln aus hoehendaten/, die noch nicht in tiles/ liegen
    (oder alle bei force=True). Rein additiv/inkrementell. Verarbeitet ALS-
    Punktwolken (als_*.zip) UND, fuer Kacheln ohne ALS-Abdeckung, die
    DGM-XYZ-Quelle (dgm_*.zip, siehe Docstring oben) - ALS hat Vorrang,
    bestehende Kacheln werden nie durch DGM-XYZ ueberschrieben."""
    zips = sorted(HOEHENDATEN_DIR.glob("als_*.zip"))
    xyz_zips = sorted(HOEHENDATEN_DIR.glob("dgm_*.zip"))
    if not zips and not xyz_zips:
        raise FileNotFoundError(f"Keine Kacheln in {HOEHENDATEN_DIR}/ gefunden")

    built, skipped = [], []
    for zip_path in zips:
        e_km, n_km = _tile_key(zip_path)
        if not force and _tile_path(e_km, n_km).exists():
            continue
        try:
            build_tile(zip_path)
            built.append((e_km, n_km))
        except zipfile.BadZipFile:
            # z.B. noch unvollstaendiger Download (laeuft parallel) - beim
            # naechsten Aufruf einfach erneut versuchen, kein harter Fehler
            print(f"  {zip_path.name}: uebersprungen (Datei unvollstaendig/beschaedigt, "
                  f"spaeter erneut versuchen)")
            skipped.append((e_km, n_km))

    built_xyz = []
    for zip_path in xyz_zips:
        e_km, n_km = _dgm_xyz_tile_key(zip_path)
        if not force and _tile_path(e_km, n_km).exists():
            continue
        try:
            build_tile_from_xyz(zip_path)
            built_xyz.append((e_km, n_km))
        except zipfile.BadZipFile:
            print(f"  {zip_path.name}: uebersprungen (Datei unvollstaendig/beschaedigt, "
                  f"spaeter erneut versuchen)")
            skipped.append((e_km, n_km))

    n_total = len(zips) + len(xyz_zips)
    n_built = len(built) + len(built_xyz)
    print(f"{n_built} neue Kachel(n) gebaut ({len(built)} ALS, {len(built_xyz)} DGM-XYZ), "
          f"{len(skipped)} uebersprungen, {n_total - n_built - len(skipped)} bereits vorhanden "
          f"({n_total} Kacheln gesamt in {HOEHENDATEN_DIR}/)")
    return built + built_xyz


def available_tiles() -> list[tuple[int, int]]:
    tiles = []
    for p in TILES_DIR.glob("*.npz"):
        e_str, n_str = p.stem.split("_")
        tiles.append((int(e_str), int(n_str)))
    return sorted(tiles)


def _tiles_bbox_latlon(tiles: list[tuple[int, int]]) -> tuple[float, float, float, float]:
    """Bounding Box (south, west, north, east) in WGS84 ueber alle Kacheln."""
    e0_min = min(e for e, _ in tiles) * 1000
    n0_min = min(n for _, n in tiles) * 1000
    e1_max = (max(e for e, _ in tiles) + 1) * 1000
    n1_max = (max(n for _, n in tiles) + 1) * 1000
    lons, lats = _UTM33_TO_LATLON.transform(
        [e0_min, e1_max, e0_min, e1_max], [n0_min, n0_min, n1_max, n1_max]
    )
    return min(lats), min(lons), max(lats), max(lons)


def fetch_osm_bridges(bbox_latlon: tuple[float, float, float, float]) -> list[dict]:
    """Laedt befahrbare Bruecken (highway+bridge=yes/viaduct/...) via Overpass API
    fuer die gegebene (south, west, north, east)-Bbox, Geometrie als UTM33-Punkte."""
    south, west, north, east = bbox_latlon
    query = (
        f'[out:json][timeout:60];'
        f'(way["highway"]["bridge"]({south},{west},{north},{east}););'
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
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.load(resp)

    bridges = []
    for el in payload.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue
        lons = [p["lon"] for p in geom]
        lats = [p["lat"] for p in geom]
        xs, ys = _LATLON_TO_UTM33.transform(lons, lats)
        bridges.append({
            "id": el["id"],
            "tags": el.get("tags", {}),
            "points": [[float(x), float(y)] for x, y in zip(xs, ys)],
        })
    return bridges


def build_bridge_cache() -> None:
    tiles = available_tiles()
    if not tiles:
        raise FileNotFoundError(f"Keine Kacheln in {TILES_DIR}/ - erst build_all_tiles() aufrufen")
    bbox = _tiles_bbox_latlon(tiles)
    print(f"Lade OSM-Bruecken (highway+bridge) fuer bbox lat {bbox[0]:.4f}-{bbox[2]:.4f}, "
          f"lon {bbox[1]:.4f}-{bbox[3]:.4f} ({len(tiles)} Kacheln) ...")
    bridges = fetch_osm_bridges(bbox)
    BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BRIDGE_CACHE_PATH, "w") as f:
        json.dump(bridges, f)
    print(f"{len(bridges)} befahrbare Bruecken-Wege gespeichert: {BRIDGE_CACHE_PATH}")


def _point_segment_distance(px: np.ndarray, py: np.ndarray, ax: float, ay: float, bx: float, by: float) -> np.ndarray:
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return np.hypot(px - ax, py - ay)
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / seg_len2, 0.0, 1.0)
    return np.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _load_berlin_cache() -> dict:
    if not BERLIN_CACHE_PATH.exists():
        return {}
    with open(BERLIN_CACHE_PATH) as f:
        return json.load(f)


def _save_berlin_cache(cache: dict) -> None:
    BERLIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BERLIN_CACHE_PATH, "w") as f:
        json.dump(cache, f)


def _berlin_cache_key(e: float, n: float) -> str:
    return f"{round(e)},{round(n)}"  # Meter-Rundung, siehe Docstring


def query_berlin_wms(e: float, n: float) -> float:
    """Live-GetFeatureInfo-Abfrage gegen den Berliner DGM1-WMS fuer einen
    einzelnen Punkt (UTM33). Liefert NaN bei Netzwerkfehler, Timeout oder
    wenn der Punkt ausserhalb der Berliner Abdeckung liegt - siehe
    Modul-Docstring "BERLIN-FALLBACK"."""
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo",
        "LAYERS": BERLIN_WMS_LAYER, "QUERY_LAYERS": BERLIN_WMS_LAYER, "STYLES": "",
        "CRS": CRS, "WIDTH": "3", "HEIGHT": "3", "I": "1", "J": "1",
        "INFO_FORMAT": "text/plain",
        # 3x3-Pixel-BBOX von 3x3m um den Punkt - liefert bei 1m-Rasterweite
        # genau die Zelle des Punkts als Mittelpixel
        "BBOX": f"{e - 1.5},{n - 1.5},{e + 1.5},{n + 1.5}",
    }
    url = f"{BERLIN_WMS_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=BERLIN_WMS_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return float("nan")

    m = re.search(r"H[oö]he_in_Meter[^=]*=\s*([-\d.]+)", text)
    if not m:
        return float("nan")
    value = float(m.group(1))
    # -9999 (und aehnliche Sentinel-Werte) sind der NODATA-Code des Dienstes,
    # keine echte Hoehe (Bug gefunden 30.08.2026: wurde zuvor ungefiltert als
    # gueltiger Wert gecacht - siehe PROJEKT_STAND.md). Grosszuegige untere
    # Schranke, da Berlin/Brandenburg keine Hoehen unter -20m hat.
    if value <= -1000:
        return float("nan")
    return value


def _download_if_missing(url: str, dest: Path, timeout: int = 120) -> bool:
    """Laedt eine Datei nach, falls sie noch nicht existiert. True bei Erfolg
    (oder falls bereits vorhanden), False bei Fehler (z.B. 404 - Kachel
    existiert nicht bei der Quelle, kein harter Fehler)."""
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.rename(dest)
        return True
    except Exception:
        return False


def load_bavaria_tile(e_km: int, n_km: int) -> dict | None:
    """Laedt (bei Bedarf herunter und) liest eine Bayern-DGM1-Kachel
    (GeoTIFF, EPSG:25832, 1m). None falls die Kachel bei der Quelle nicht
    existiert (z.B. ausserhalb Bayerns)."""
    path = BAYERN_TILES_DIR / f"{e_km}_{n_km}.tif"
    if not _download_if_missing(BAYERN_TILE_URL.format(e_km=e_km, n_km=n_km), path):
        return None
    with rasterio.open(path) as ds:
        grid = ds.read(1).astype(np.float64)
        b = ds.bounds
    return {"grid": grid, "origin_e": b.left, "origin_n": b.bottom, "resolution": 1.0}


def load_thueringen_tile(e_km: int, n_km: int) -> dict | None:
    """Laedt (bei Bedarf herunter und) liest eine Thueringen-DGM2-Kachel
    (ZIP mit XYZ-Textdatei, EPSG:25832, 2m). None falls die Kachel bei der
    Quelle nicht existiert (z.B. ausserhalb Thueringens oder kein
    2010-2013-Jahrgang fuer diese Stelle)."""
    zpath = THUERINGEN_TILES_DIR / f"{e_km}_{n_km}.zip"
    if not _download_if_missing(THUERINGEN_TILE_URL.format(e_km=e_km, n_km=n_km), zpath):
        return None
    with zipfile.ZipFile(zpath) as zf:
        xyz_name = next(n for n in zf.namelist() if n.endswith(".xyz"))
        raw = zf.read(xyz_name).decode("ascii")
    arr = np.fromstring(raw.replace("\n", " "), sep=" ").reshape(-1, 3)
    x, y, z = arr[:, 0], arr[:, 1], arr[:, 2]
    ncells = int(round(1000 / THUERINGEN_GRID_RES_M))
    col = np.round((x - e_km * 1000) / THUERINGEN_GRID_RES_M).astype(np.int64)
    row = np.round((y - n_km * 1000) / THUERINGEN_GRID_RES_M).astype(np.int64)
    grid = np.full((ncells, ncells), np.nan, dtype=np.float64)
    valid = (col >= 0) & (col < ncells) & (row >= 0) & (row < ncells)
    grid[row[valid], col[valid]] = z[valid]
    return {"grid": grid, "origin_e": float(e_km * 1000), "origin_n": float(n_km * 1000),
            "resolution": THUERINGEN_GRID_RES_M}


def _load_sachsen_anhalt_cache() -> dict:
    if not SACHSEN_ANHALT_CACHE_PATH.exists():
        return {}
    with open(SACHSEN_ANHALT_CACHE_PATH) as f:
        return json.load(f)


def _save_sachsen_anhalt_cache(cache: dict) -> None:
    SACHSEN_ANHALT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SACHSEN_ANHALT_CACHE_PATH, "w") as f:
        json.dump(cache, f)


def query_sachsen_anhalt_wcs(e: float, n: float) -> float:
    """Live-GetCoverage-Abfrage (WCS 2.0.1, INSPIRE-Hoehe-Dienst) fuer einen
    einzelnen Punkt (EPSG:25832). Liefert NaN bei Netzwerkfehler/Timeout
    oder ausserhalb der Sachsen-Anhalt-Abdeckung. Die Antwort ist ein
    Multipart-MIME-Dokument (GML-Metadaten + eingebettetes GeoTIFF) - wird
    hier manuell aus den rohen Bytes extrahiert, da rasterio kein Multipart
    liest."""
    params = {
        "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage",
        "COVERAGEID": "Coverage1", "FORMAT": "image/tiff",
        "SUBSET": [f"x({e - 1.5},{e + 1.5})", f"y({n - 1.5},{n + 1.5})"],
    }
    url = f"{SACHSEN_ANHALT_WCS_URL}?{urllib.parse.urlencode(params, doseq=True)}"
    try:
        with urllib.request.urlopen(url, timeout=SACHSEN_ANHALT_WCS_TIMEOUT_S) as resp:
            data = resp.read()
        idx = data.find(b"Content-Type: image/tiff")
        sep = data.find(b"\n\n", idx)
        end = data.find(b"--wcs--", sep)
        tiff_bytes = data[sep + 2:end]
        with rasterio.open(io.BytesIO(tiff_bytes)) as ds:
            band = ds.read(1)
        center = band[band.shape[0] // 2, band.shape[1] // 2]
        return float(center)
    except Exception:
        return float("nan")


TRIP_BRIDGE_CACHE_PATH = Path("data/elevation/osm_bridges_trip_corridor.json")


def fetch_osm_bridges_latlon(bbox_latlon: tuple[float, float, float, float]) -> list[dict]:
    """Wie fetch_osm_bridges(), liefert die Geometrie aber als WGS84 lat/lon
    (statt UTM33) - fuer Bruecken-Pruefung ausserhalb Brandenburgs/Berlins,
    wo je nach Bundesland unterschiedliche UTM-Zonen (25832 vs 25833) im
    Spiel sind und eine gemeinsame lat/lon-Basis einfacher ist als staendig
    umzurechnen."""
    south, west, north, east = bbox_latlon
    query = (
        f'[out:json][timeout:90];'
        f'(way["highway"]["bridge"]({south},{west},{north},{east}););'
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.load(resp)

    bridges = []
    for el in payload.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue
        bridges.append({
            "id": el["id"], "tags": el.get("tags", {}),
            "points": [[float(p["lat"]), float(p["lon"])] for p in geom],
        })
    return bridges


def get_or_build_trip_bridge_cache(bbox_latlon: tuple[float, float, float, float]) -> list[dict]:
    if TRIP_BRIDGE_CACHE_PATH.exists():
        with open(TRIP_BRIDGE_CACHE_PATH) as f:
            return json.load(f)
    bridges = fetch_osm_bridges_latlon(bbox_latlon)
    TRIP_BRIDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRIP_BRIDGE_CACHE_PATH, "w") as f:
        json.dump(bridges, f)
    return bridges


def is_near_bridge_latlon(lat: np.ndarray, lon: np.ndarray, bridges: list[dict],
                           buffer_m: float = BRIDGE_BUFFER_M) -> np.ndarray:
    """Wie ElevationModel._bridge_mask, aber fuer beliebige Bundeslaender:
    einfache lokale Plattkarten-Projektion (Fehler <1% bei diesen
    Massstaeben - fuer einen 15m-Bruecken-Puffer voellig ausreichend) statt
    einer festen UTM-Zone, da Bayern/Thueringen/Sachsen-Anhalt (UTM32) und
    Brandenburg/Berlin (UTM33) sonst unterschiedliche Projektionen bräuchten."""
    lat = np.atleast_1d(np.asarray(lat, dtype=np.float64))
    lon = np.atleast_1d(np.asarray(lon, dtype=np.float64))
    lat0 = float(np.mean(lat))
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * np.cos(np.radians(lat0))
    px = (lon - lon.mean()) * m_per_deg_lon
    py = (lat - lat.mean()) * m_per_deg_lat

    mask = np.zeros(lat.shape, dtype=bool)
    for bridge in bridges:
        pts = bridge["points"]
        for (alat, alon), (blat, blon) in zip(pts[:-1], pts[1:]):
            ax = (alon - lon.mean()) * m_per_deg_lon
            ay = (alat - lat.mean()) * m_per_deg_lat
            bx = (blon - lon.mean()) * m_per_deg_lon
            by = (blat - lat.mean()) * m_per_deg_lat
            mask |= _point_segment_distance(px, py, ax, ay, bx, by) < buffer_m
    return mask


class ElevationModel:
    """Kachelbasiertes DGM mit bilinearer Hoehenabfrage und Lazy-Loading.

    Kacheln werden erst bei der ersten Abfrage geladen und danach in einem
    LRU-Cache (`tile_cache_size` Eintraege) gehalten - RAM-Verbrauch skaliert
    mit der Zahl tatsaechlich ABGEFRAGTER Kacheln, nicht mit der Gesamtzahl
    vorhandener Kacheln (aktuell knapp 100, koennte beliebig weiter wachsen).
    """

    def __init__(
        self,
        tiles_dir: Path = TILES_DIR,
        bridge_cache_path: Path = BRIDGE_CACHE_PATH,
        tile_cache_size: int = DEFAULT_TILE_CACHE_SIZE,
        use_berlin_fallback: bool = True,
    ):
        self.tiles_dir = tiles_dir
        self.tile_cache_size = tile_cache_size
        self._tile_cache: "OrderedDict[tuple[int, int], dict]" = OrderedDict()
        self.use_berlin_fallback = use_berlin_fallback
        self._berlin_cache = _load_berlin_cache() if use_berlin_fallback else {}
        self._sachsen_anhalt_cache = _load_sachsen_anhalt_cache()
        self._bavaria_tile_cache: "OrderedDict[tuple[int, int], dict | None]" = OrderedDict()
        self._thueringen_tile_cache: "OrderedDict[tuple[int, int], dict | None]" = OrderedDict()

        self.known_tiles = set()
        for p in self.tiles_dir.glob("*.npz"):
            e_str, n_str = p.stem.split("_")
            self.known_tiles.add((int(e_str), int(n_str)))
        if not self.known_tiles:
            raise FileNotFoundError(
                f"Keine Kacheln in {tiles_dir} - erst `python scripts/elevation_model.py --rebuild` laufen lassen"
            )

        self.bridges: list[dict] = []
        if bridge_cache_path.exists():
            with open(bridge_cache_path) as f:
                self.bridges = json.load(f)
        else:
            print(f"Hinweis: {bridge_cache_path} fehlt - Bruecken werden NICHT automatisch "
                  f"erkannt/ueberbrueckt. Mit --rebuild neu aufbauen.")

    def _get_tile(self, e_km: int, n_km: int) -> dict | None:
        key = (e_km, n_km)
        if key in self._tile_cache:
            self._tile_cache.move_to_end(key)
            return self._tile_cache[key]
        if key not in self.known_tiles:
            return None

        data = np.load(_tile_path(e_km, n_km))
        tile = {
            "grid": data["grid"],
            "origin_e": float(data["origin_e"]),
            "origin_n": float(data["origin_n"]),
            "resolution": float(data["resolution"]),
        }
        self._tile_cache[key] = tile
        if len(self._tile_cache) > self.tile_cache_size:
            self._tile_cache.popitem(last=False)
        return tile

    def get_elevation(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        """Bilineare Interpolation der Hoehe an UTM33-Koordinaten (e, n).
        NaN fuer Punkte ausserhalb der Kachelabdeckung oder in Datenluecken."""
        e = np.atleast_1d(np.asarray(e, dtype=np.float64))
        n = np.atleast_1d(np.asarray(n, dtype=np.float64))
        out = np.full(e.shape, np.nan, dtype=np.float64)

        e_km = np.floor(e / TILE_SIZE_M).astype(np.int64)
        n_km = np.floor(n / TILE_SIZE_M).astype(np.int64)

        for key in set(zip(e_km.tolist(), n_km.tolist())):
            tile = self._get_tile(*key)
            if tile is None:
                continue
            sel = (e_km == key[0]) & (n_km == key[1])
            out[sel] = self._bilinear(tile, e[sel], n[sel])

        if self.use_berlin_fallback:
            for i in np.where(np.isnan(out))[0]:
                out[i] = self._get_elevation_berlin(float(e[i]), float(n[i]))

        return out if out.size > 1 else out[0]

    def _get_elevation_berlin(self, e: float, n: float) -> float:
        """Berlin-Fallback fuer einen einzelnen Punkt ohne lokale Kachel-
        Abdeckung: erst Cache, sonst Live-WMS-Abfrage (siehe Modul-Docstring
        "BERLIN-FALLBACK"). Punkte weit ausserhalb der bekannten Region
        werden gar nicht erst abgefragt (vermeidet sinnlose Netzwerkaufrufe)."""
        if not (BERLIN_WMS_BBOX_E[0] <= e <= BERLIN_WMS_BBOX_E[1]
                and BERLIN_WMS_BBOX_N[0] <= n <= BERLIN_WMS_BBOX_N[1]):
            return float("nan")

        key = _berlin_cache_key(e, n)
        if key in self._berlin_cache:
            return self._berlin_cache[key]

        value = query_berlin_wms(e, n)
        self._berlin_cache[key] = value
        _save_berlin_cache(self._berlin_cache)
        return value

    @staticmethod
    def _bilinear(tile: dict, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        grid = tile["grid"]
        nrows, ncols = grid.shape
        res = tile["resolution"]
        col_f = (e - tile["origin_e"]) / res
        row_f = (n - tile["origin_n"]) / res

        col0 = np.floor(col_f).astype(np.int64)
        row0 = np.floor(row_f).astype(np.int64)
        # An der letzten Zeile/Spalte einer Kachel (Rand zur Nachbarkachel)
        # wird die Nachbarkachel NICHT mitgeladen - hier wird auf die
        # naechstgelegene Zelle innerhalb dieser Kachel geklemmt statt echter
        # Kachel-uebergreifender Interpolation (Fehler <1m, vernachlaessigbar
        # fuer Steigungsauswertungen).
        col0 = np.clip(col0, 0, ncols - 2)
        row0 = np.clip(row0, 0, nrows - 2)
        col1, row1 = col0 + 1, row0 + 1

        in_bounds = (col_f >= 0) & (col_f <= ncols - 1) & (row_f >= 0) & (row_f <= nrows - 1)
        out = np.full(e.shape, np.nan, dtype=np.float64)
        if not in_bounds.any():
            return out

        fx = np.clip(col_f - col0, 0.0, 1.0)
        fy = np.clip(row_f - row0, 0.0, 1.0)

        z00 = grid[row0, col0]
        z01 = grid[row0, col1]
        z10 = grid[row1, col0]
        z11 = grid[row1, col1]
        z = (
            z00 * (1 - fx) * (1 - fy)
            + z01 * fx * (1 - fy)
            + z10 * (1 - fx) * fy
            + z11 * fx * fy
        )
        out[in_bounds] = z[in_bounds]
        return out

    def get_elevation_latlon(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Wie get_elevation, nimmt aber WGS84 lat/lon entgegen."""
        lat = np.atleast_1d(lat)
        lon = np.atleast_1d(lon)
        e, n = _LATLON_TO_UTM33.transform(lon, lat)
        return self.get_elevation(np.asarray(e), np.asarray(n))

    def _get_bavaria_tile(self, e_km: int, n_km: int) -> dict | None:
        key = (e_km, n_km)
        if key in self._bavaria_tile_cache:
            self._bavaria_tile_cache.move_to_end(key)
            return self._bavaria_tile_cache[key]
        tile = load_bavaria_tile(e_km, n_km)
        self._bavaria_tile_cache[key] = tile
        if len(self._bavaria_tile_cache) > self.tile_cache_size:
            self._bavaria_tile_cache.popitem(last=False)
        return tile

    def _get_thueringen_tile(self, e_km: int, n_km: int) -> dict | None:
        key = (e_km, n_km)
        if key in self._thueringen_tile_cache:
            self._thueringen_tile_cache.move_to_end(key)
            return self._thueringen_tile_cache[key]
        tile = load_thueringen_tile(e_km, n_km)
        self._thueringen_tile_cache[key] = tile
        if len(self._thueringen_tile_cache) > self.tile_cache_size:
            self._thueringen_tile_cache.popitem(last=False)
        return tile

    def _get_sachsen_anhalt(self, e: float, n: float) -> float:
        if not (SACHSEN_ANHALT_BBOX_E[0] <= e <= SACHSEN_ANHALT_BBOX_E[1]
                and SACHSEN_ANHALT_BBOX_N[0] <= n <= SACHSEN_ANHALT_BBOX_N[1]):
            return float("nan")
        key = _berlin_cache_key(e, n)  # gleiches Rundungsschema, generischer Name waere sauberer
        if key in self._sachsen_anhalt_cache:
            return self._sachsen_anhalt_cache[key]
        value = query_sachsen_anhalt_wcs(e, n)
        self._sachsen_anhalt_cache[key] = value
        _save_sachsen_anhalt_cache(self._sachsen_anhalt_cache)
        return value

    def get_elevation_germany_wide(self, lat: float, lon: float) -> tuple[float, str | None]:
        """Hoehenabfrage fuer einen einzelnen Punkt UEBER Brandenburg/Berlin
        hinaus - probiert der Reihe nach Brandenburg-Kacheln, Berlin-WMS,
        Bayern-DGM1-Kacheln, Thueringen-DGM2-Kacheln, Sachsen-Anhalt-WCS.
        Fuer Fahrten ausserhalb der ueblichen Berlin-Brandenburg-Schleife
        (z.B. Suedtirol-Rueckfahrt 31.07.2026, siehe PROJEKT_STAND.md).
        Gibt (Hoehe, Quelle) zurueck - Quelle ist None, wenn keine der
        Quellen einen Wert liefern konnte (NICHT raten/interpolieren ueber
        Bundeslandgrenzen hinweg - lieber ehrlich NaN)."""
        e33, n33 = _LATLON_TO_UTM33.transform(lon, lat)
        v = self.get_elevation_latlon(lat, lon)
        v = float(v if np.isscalar(v) else v[0])
        if not np.isnan(v):
            known_tile = (int(e33 // TILE_SIZE_M), int(n33 // TILE_SIZE_M)) in self.known_tiles
            return v, ("Brandenburg-ALS" if known_tile else "Berlin-WMS")

        e32, n32 = _LATLON_TO_UTM32.transform(lon, lat)
        e_km, n_km = int(e32 // 1000), int(n32 // 1000)

        tile = self._get_bavaria_tile(e_km, n_km)
        if tile is not None:
            v = float(self._bilinear(tile, np.array([e32]), np.array([n32]))[0])
            if not np.isnan(v):
                return v, "Bayern-DGM1"

        tile = self._get_thueringen_tile(e_km, n_km)
        if tile is not None:
            v = float(self._bilinear(tile, np.array([e32]), np.array([n32]))[0])
            if not np.isnan(v):
                return v, "Thueringen-DGM2"

        v = self._get_sachsen_anhalt(e32, n32)
        if not np.isnan(v):
            return v, "Sachsen-Anhalt-WCS"

        return float("nan"), None

    def _bridge_mask(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        mask = np.zeros(e.shape, dtype=bool)
        for bridge in self.bridges:
            pts = bridge["points"]
            for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
                mask |= _point_segment_distance(e, n, ax, ay, bx, by) < BRIDGE_BUFFER_M
        return mask

    def get_elevation_along_track(self, e: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Hoehe entlang einer geordneten Trajektorie (UTM33), Bruecken-sicher.

        Punkte im OSM-Bruecken-Puffer (`BRIDGE_BUFFER_M`) sowie normale
        Datenluecken werden linear nach zurueckgelegter Strecke zwischen den
        jeweils naechsten gueltigen Nachbarpunkten interpoliert (kein
        Extrapolieren an den Rändern der Trajektorie). Notwendig, weil
        `get_elevation()` bei Bruecken das UNTERLIEGENDE Gelaende statt der
        Fahrbahn liefert oder Luecken hat, siehe Modul-Docstring.

        Returns (hoehe, is_interpoliert) - beide gleicher Laenge wie e/n.
        """
        e = np.atleast_1d(np.asarray(e, dtype=np.float64))
        n = np.atleast_1d(np.asarray(n, dtype=np.float64))
        raw = np.atleast_1d(self.get_elevation(e, n)).astype(np.float64)

        bridge_mask = self._bridge_mask(e, n) if self.bridges else np.zeros(e.shape, dtype=bool)
        dist = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(e), np.diff(n)))))

        s = pd.Series(raw, index=dist)
        s[bridge_mask] = np.nan
        corrected = s.interpolate(method="index", limit_area="inside").to_numpy()

        is_interp = bridge_mask | (np.isnan(raw) & ~np.isnan(corrected))
        return corrected, is_interp


def _sanity_check_against_gps() -> None:
    if not DATALAKE_PATH.exists():
        print(f"Ueberspringe Plausibilitaetscheck ({DATALAKE_PATH} nicht gefunden)")
        return

    model = ElevationModel()
    con = duckdb.connect(str(DATALAKE_PATH), read_only=True)
    df = con.execute(
        """
        select b.log_id, b.t_elapsed_s, b.value as lat, l.value as lon, h.value as gps_alt
        from (select * from measurements where channel = 'Breite') b
        join (select * from measurements where channel = 'Länge') l
          on b.log_id = l.log_id and b.t_elapsed_s = l.t_elapsed_s
        join (select * from measurements where channel = 'Höhe') h
          on b.log_id = h.log_id and b.t_elapsed_s = h.t_elapsed_s
        where b.value != 0 and l.value != 0
        """
    ).df()

    if df.empty:
        print("Keine gemeinsamen Breite/Laenge/Hoehe-Punkte im Datalake gefunden")
        return

    dgm_alt = model.get_elevation_latlon(df["lat"].to_numpy(), df["lon"].to_numpy())
    df["dgm_alt"] = dgm_alt
    matched = df.dropna(subset=["dgm_alt"])

    print(
        f"\nPlausibilitaetscheck DGM vs. geloggte GPS-Hoehe: "
        f"{len(matched)} von {len(df)} GPS-Punkten liegen in der Kachelabdeckung"
    )
    if matched.empty:
        return

    diff = matched["gps_alt"] - matched["dgm_alt"]
    print(f"  Differenz GPS-Hoehe minus DGM-Hoehe: "
          f"Median {diff.median():.1f}m, Mean {diff.mean():.1f}m, "
          f"StdAbw {diff.std():.1f}m, |Median| {diff.abs().median():.1f}m")
    print(f"  betroffene Logs: {sorted(matched['log_id'].unique())}")


if __name__ == "__main__":
    rebuild = "--rebuild" in sys.argv
    build_all_tiles(force=False)
    if rebuild or not BRIDGE_CACHE_PATH.exists():
        build_bridge_cache()
    _sanity_check_against_gps()
