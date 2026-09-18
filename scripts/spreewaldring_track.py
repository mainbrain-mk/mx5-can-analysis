"""
Streckenrekonstruktion "Spreewaldring Training Center" (STC Motodrom) aus
OpenStreetMap-Daten (MX-5 Projekt)

Nutzerfrage: "kannst du aus den OSM Daten das Spreewaldring Training Center
als Track rekonstruieren?" - erster Baustein in Richtung des urspruenglichen
Projektziels (Bestzeit-Berechnung), diesmal fuer eine ECHTE, praezise
kartierte Rennstrecke statt der verrauschten 1Hz-GPS-Spuren aus dem
oeffentlichen Strassenverkehr (die Streckengeometrie kommt hier direkt aus
OSM-Vermessungsdaten, nicht aus einer gefahrenen Spur - kein GPS-Rauschen-
Problem wie bei den bisherigen Kurven-Events).

RECHERCHE (30.08.2026): Das Gelaende ist in OSM als
`way 110163355 "Spreewaldring Training Center"` (leisure=sports_centre,
Adresse Waldhaus 2, 15910 Schoenwald/Waldow-Brand, website
stc-motodrom.de) verzeichnet - das ist nur die Gelaende-Flaeche, nicht die
Streckenfuehrung selbst. Die eigentliche Strecke liegt als separate
`highway=raceway`-Wege vor. Das Gelaende ist MODULAR (mehrere
Streckenkonfigurationen ueber Verbindungsstuecke, aehnlich Bilster Berg):

  - **Kartbahn** (sport=karting): ways 165986117/165986119/165986124/
    449049646/449055557 - raeumlich getrennt, oestlicher Teil des Gelaendes,
    NICHT Teil dieser Rekonstruktion (andere Fahrzeugklasse).
  - **Haupt-Rundstrecke fuer Motorsport** (sport=motor): way 172927073
    (95 Knoten, ~2650m) - DAS ist die eigentliche Rennstrecke.
  - **Boxengasse** (sport=motor): way 172927499 (26 Knoten) - NUTZER-
    KORREKTUR (30.08.2026, per Google-Maps-Satellitenbild + OSM-
    Standardkarte bestaetigt): dieser Weg wurde in der ERSTEN Version
    dieses Skripts faelschlich als Teil der Hauptschleife behandelt (er
    teilt sich einen exakten Knoten mit 172927073s Start, 0.0m Abstand,
    beide `oneway=yes` - das sieht aus wie eine Fortsetzung der Ideallinie,
    ist aber tatsaechlich der Boxengassen-Wiedereinstieg auf die
    Start-/Zielgerade). Wird jetzt korrekt AUSSERHALB der Rundenzeit-
    Rekonstruktion gefuehrt (nur zur Einordnung geplottet, siehe
    `PIT_LANE_WAY_ID`).
  - **Alternative kuerzere Streckenfuehrung** (sport=motor): way 243040316
    (6 Knoten) + way 243040319 (5 Knoten) bilden eine Sehne quer durch die
    Mitte der Hauptschleife (sichtbar im Kontroll-Plot als Abkuerzung) -
    eine ALTERNATIVE Streckenkonfiguration, NICHT Teil der Hauptschleife.
    Wird hier nur referenziert (`SHORTCUT_WAY_IDS`), nicht separat
    rekonstruiert.
  - **ZWEITE NUTZER-KORREKTUR (30.08.2026, Satellitenbild-Ausschnitt der
    Boxenausfahrt mit Gelb/Gruen-Markierung):** die erste Korrektur (oben)
    schloss die Runde noch mit einer 120m-Geraden-ANNAHME zwischen
    172927073s eigenem Start (Knoten-Index 0) und Ende (Index 94). Genauere
    Analyse der rohen Knotenkoordinaten zeigt: das war unnoetig UND falsch
    platziert. **Knoten-Index 3 und Index 94 (der allerletzte Knoten) von
    way 172927073 sind EXAKT derselbe OSM-Punkt** (51.9983402, 13.6886807,
    auf 7 Nachkommastellen identisch - kein Zufall). Die Rundstrecke
    schliesst sich also bereits VOLLSTAENDIG UND EXAKT in den OSM-Daten
    selbst, zwischen Index 3 und Index 94 - **keine Annahme/erfundene
    Gerade mehr noetig.**
    Die Knoten 0-1-2-3 (123,5m) sind stattdessen ein separater
    **Boxenausfahrt-Verbindungsweg**: die Boxengasse (172927499) muendet
    bei Index 0 ein, von dort fuehren 3 weitere Knoten zum Punkt, an dem
    diese Verbindung auf die geschlossene Rundstrecke trifft (Index 3).
    Ein Auto, das KEINEN Boxenstopp macht, durchfaehrt diesen Verbindungsweg
    nie - es kreist nur durch Index 3->4->...->93->94(=3)->4->... Das
    deckt sich mit dem vom Nutzer markierten Satellitenausschnitt: gelb
    (normaler Streckenverlauf) und gruen (Boxenausfahrt) laufen ein Stueck
    parallel und treffen sich VOR der grossen Kurve, nicht exakt an ihr.
    Nebenbefund: way 172927073 enthaelt zusaetzlich einen einzelnen sehr
    langen Knotenabstand ohne Zwischenpunkte (Index 91->92, 134,7m,
    schnurgerade) - vermutlich die tatsaechliche Start-/Zielgerade, von OSM
    einfach mit nur 2 Endknoten kartiert (ueblich fuer lange gerade
    Abschnitte ohne Kruemmung).
  - **Boxengasse (way 172927499) UND der Boxenausfahrt-Verbindungsweg
    (Knoten 0-3 von 172927073) werden im Plot weiterhin gezeigt (gepunktet),
    sind aber NICHT Teil der gewerteten Rundenlaenge/Kruemmung.**

METHODIK:
  1. Rundstrecke = way 172927073, Knoten-Index 3 bis 94 (=3, siehe oben -
     perfekt geschlossen, keine Annahme). Per Overpass abgerufen (gecacht in
     `data/track/spreewaldring_osm_raceways.json`), nach UTM33 (EPSG:25833,
     projektweiter Standard) transformiert. Boxengasse (172927499) +
     Boxenausfahrt-Verbindungsweg (Knoten 0-3) zusaetzlich geladen, nur fuer
     den Plot (nicht Teil der Rundenlaenge/Kruemmungsberechnung).
  2. Auf gleichmaessige Bogenlaenge resampled (`RESAMPLE_STEP_M`) - die
     Schleife schliesst sich dabei automatisch exakt (letzter Knoten =
     erster Knoten), keine zusaetzliche Verbindungsgerade noetig.
  3. Kruemmungsradius pro Punkt per 3-Punkt-Kreisradius (Menger-Kruemmung)
     mit einem Fenster von `CURVATURE_WINDOW_M` Bogenlaenge vor/hinter jedem
     Punkt - robuster gegen einzelne OSM-Knotenabstaende/-ungenauigkeiten
     als eine reine Nachbarpunkt-Kruemmung (bei 3-95 Knoten pro Originalweg
     ist der Knotenabstand sehr ungleichmaessig).
  4. Kurven = zusammenhaengende Abschnitte mit Radius < `CORNER_RADIUS_MAX_M`.
  5. GROBE Kurvengeschwindigkeits-Bracket pro Kurve: v = sqrt(mu*g*r) fuer
     mu=1.0 und mu=1.3 (Bandbreite fuer den Nankang NS-R2 Semi-Slick, siehe
     Memory "mx5-tires" - NICHT aus eigenen Messungen, reine
     Literatur-Einordnung fuer diese Reifenklasse, siehe dortige
     Einschraenkungen zu Reifenzustand/Fahrbahntemperatur). Dient nur als
     erste Groessenordnung, NICHT als validierte Vorhersage.

EINSCHRAENKUNGEN:
  - Streckenbreite/Curbs/Randbegrenzung nicht modelliert - die
    Kruemmungsberechnung nutzt die kartierte Mittellinie, keine
    ideale Rennlinie (die haette in echten Kurven einen groesseren Radius
    als die Streckenmittellinie).
  - Keine Hoehendaten: die vorhandene Brandenburg-DGM-Kachelabdeckung
    (siehe elevation_model.py) deckt nur die Berlin-Brandenburg-Stammschleife
    ab (E350-410km/N5810-5855km) - Spreewaldring liegt bei E~409.8km/
    N~5761.6km, deutlich suedlich davon, NICHT abgedeckt. Versuch, die
    naheliegende Kachel `als_33409-5761.zip` direkt zu laden, ergab 404 -
    offen, ob echte Datenluecke oder falsche Namenskonvention an dieser
    Stelle, nicht weiter verfolgt.
  - Kein Bezug zu den bisherigen 56 Fahrt-Logs - diese decken alle die
    Berlin-Brandenburg-Alltagsschleife (und die Suedtirol-Rueckfahrt) ab,
    NICHT den Spreewaldring. Diese Rekonstruktion ist eigenstaendig, dient
    als Vorbereitung fuer eine spaetere Rundenzeit-Simulation, falls der
    Nutzer dort tatsaechlich faehrt/Logs aufzeichnet.

ORTHOFOTO-VERFEINERUNG (NEU, 30.08.2026): Nutzerfrage: "Laut Betreiber hat
die Strecke eine Breite von 10m. Kannst du mit Hilfe Satellitenbild den
Streckenverlauf feiner aufgeloest interpolieren?" Die 91 OSM-Knoten der
Hauptschleife sind an manchen Stellen weit auseinander (einzelne Kanten bis
167m ohne Zwischenpunkt, siehe LOOP_START_NODE_IDX-Analyse oben) - fuer eine
praezise Kruemmungsberechnung zu grob. Statt Google-Maps-Screenshots zu
vermessen: **Brandenburg (LGB) stellt wie beim Hoehenmodell auch amtliche,
exakt georeferenzierte Orthofotos oeffentlich bereit** (20cm Bodenaufloesung,
`data.geobasis-bb.de/geobasis/daten/dop/rgb_jpg/`, gleiches Kachelschema wie
die ALS-Hoehendaten: `dop_<E_km>-<N_km>.zip`, enthaelt JPEG + .jgw World-File
fuer exakte Pixel<->UTM33-Transformation). Der komplette Spreewaldring passt
in eine einzige Kachel (`dop_33409-5761`, im Gegensatz zu den Hoehendaten,
fuer die an dieser Stelle keine Kachel verfuegbar war).

Methodik (`refine_centerline_with_orthophoto()`):
  1. Kachel herunterladen/cachen (`data/orthophoto/`), Pixel<->UTM33 ueber
     das World-File.
  2. Referenzfarbe der Fahrbahn NICHT hart codiert, sondern adaptiv aus dem
     Bild selbst gelernt: an allen 829 vorhandenen (aus OSM resampleten)
     Streckenpunkten wird die Bildfarbe abgetastet (Median+Streuung je
     RGB-Kanal) - die grobe OSM-Linie liegt zuverlaessig genug auf dem
     Asphalt, um eine robuste Referenzverteilung zu liefern.
  3. Fuer jeden Streckenpunkt: lokale Tangente aus den Nachbarpunkten,
     senkrecht dazu wird das Bild in 0,2m-Schritten (=1 Pixel) ueber
     `REFINE_SCAN_HALF_WIDTH_M` in beide Richtungen abgetastet und jeder
     Pixel per Farbabstand zur Referenz als "Fahrbahn"/"nicht Fahrbahn"
     klassifiziert. Der zusammenhaengende Fahrbahn-Abschnitt NAEHESTE am
     Nulldurchgang (=urspruenglicher OSM-Punkt) wird genommen, dessen
     Mittelpunkt ist der verfeinerte Streckenpunkt, seine Laenge die lokal
     gemessene Fahrbahnbreite (Plausibilitaetscheck gegen die
     Betreiberangabe 10m).
  4. Kruemmungsradius/Kurven werden auf der verfeinerten Linie neu
     berechnet (gleiche Methode wie auf der OSM-Linie).

GLAETTUNG (NEU, 30.08.2026, Nutzer-Feedback): Schritt 3 bestimmt jeden
Punkt UNABHAENGIG von seinen Nachbarn - kleinste Farbschwankungen (Schatten,
Reifenabrieb, einzelne helle/dunkle Pixel) lassen die Mittellinie auf
Geraden sichtbar wackeln. Quantifiziert an einem Testabschnitt (s=476-563m,
sollte gerade sein): bis zu 4,7m Abweichung von einer Ausgleichsgeraden,
Streuung 1,5m - VOR Glaettung. Fix: der Versatz (senkrecht zur groben
OSM-Linie) wird als 1D-Signal entlang der Bogenlaenge mit einem
Savitzky-Golay-Filter geglaettet (`SMOOTH_WINDOW_M`, zyklisch gepolstert
fuer die geschlossene Schleife) - Fensterbreite bewusst kleiner als die
Bogenlaenge der enges ten echten Kurve (11m Radius), damit echte Kurven
nicht mit weggeglaettet werden. `spreewaldring_track_summary.json` enthaelt
sowohl die rohe (`track_points_utm33_refined_raw`) als auch die geglaettete
Linie (`track_points_utm33_refined`) zum Vergleich.

EINSCHRAENKUNGEN:
  - Punkte, an denen im Scan-Fenster keine passende Fahrbahnfarbe gefunden
    wird (z.B. durch Schatten, Fahrbahnmarkierungen, Reifenabrieb, oder wo
    die grobe OSM-Linie mehr als `REFINE_SCAN_HALF_WIDTH_M` daneben liegt),
    bleiben unveraendert (OSM-Position) und werden als `refined=False`
    markiert statt eine falsche Verfeinerung zu erzwingen.
  - Nahe der Boxenausfahrt-Einmuendung (Knoten 0-3, siehe oben) liegen
    Boxengasse und Hauptstrecke nah beieinander - dort ist die
    "naehester Abschnitt zum Nulldurchgang"-Heuristik weniger zuverlaessig,
    da beide Fahrbahnen aehnliche Farbe haben.
  - Die gemessene Breite ist eine BILDBASIERTE Naeherung (Fahrbahn vs.
    Bankett/Curbs je nach Farbkontrast nicht immer eindeutig trennbar),
    kein amtliches Vermessungsergebnis.

Aufruf: .venv/bin/python scripts/spreewaldring_track.py
"""
import os
import json
import math
import zipfile
import urllib.request
import urllib.parse

import numpy as np
import pyproj
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from drivetrain_model_validation import G

RESULTS_DIR = "results"
TRACK_CACHE_PATH = "data/track/spreewaldring_osm_raceways.json"

BBOX = (51.990, 13.675, 52.005, 13.696)  # south, west, north, east (STC Motodrom-Gelaende + Puffer)
MAIN_LOOP_WAY_IDS = [172927073]              # die eigentliche Rundstrecke, siehe Docstring
LOOP_START_NODE_IDX = 3                      # Knoten 3..94(=3) = geschlossene Runde, siehe Docstring
PIT_LANE_WAY_ID = 172927499                  # Boxengasse - NUR fuer den Plot, nicht Teil der Runde
SHORTCUT_WAY_IDS = [243040316, 243040319]   # alternative Streckenfuehrung, nur referenziert

RESAMPLE_STEP_M = 3.0
CURVATURE_WINDOW_M = 15.0
CORNER_RADIUS_MAX_M = 100.0
CORNER_MERGE_GAP_M = 30.0  # naeher beieinanderliegende Kurven-Kandidaten
                           # gelten als EINE zusammenhaengende Kurve/Kurvenkombination
                           # (sonst zerlegt eine Schwellwert-Ueberschreitung mitten in
                           # einer S-Kurven-Sequenz diese kuenstlich in mehrere "Kurven")
TIRE_MU_RANGE = (1.0, 1.3)  # siehe Memory "mx5-tires" (Nankang NS-R2 Semi-Slick)

# --- Orthofoto-Verfeinerung ---
ORTHOPHOTO_DIR = "data/orthophoto"
ORTHOPHOTO_TILE = "33409-5761"  # deckt den kompletten Spreewaldring ab, siehe Docstring
ORTHOPHOTO_URL_TEMPLATE = "https://data.geobasis-bb.de/geobasis/daten/dop/rgb_jpg/dop_{tile}.zip"
TRACK_WIDTH_M = 10.0            # Betreiberangabe, Plausibilitaetscheck fuer die Verfeinerung
REFINE_SCAN_HALF_WIDTH_M = 8.0  # Suchradius senkrecht zur OSM-Linie (> halbe Breite + Toleranz)
REFINE_COLOR_THRESHOLD_STD = 4.0  # Pixel gilt als "Fahrbahn", wenn Farbabstand zur
                                  # gelernten Referenzfarbe < diesem Vielfachen der Streuung liegt
SMOOTH_WINDOW_M = 90.0            # Savitzky-Golay-Fenster fuer die Versatz-Glaettung.
                                  # Empirisch bestimmt (30.08.2026, siehe Docstring): an einem
                                  # NACHWEISLICH geraden 134m-Referenzabschnitt (einzelne
                                  # OSM-Kante ohne Zwischenknoten) sinkt die Streuung von
                                  # 0.86m (roh) auf 0.20m bei 90m Fenster, waehrend sich die
                                  # gemessenen Radien an 6 bekannten engen Kurven (11-25m)
                                  # dabei um <1m veraendern - Kurven bleiben also robust
                                  # erhalten, obwohl das Fenster viel groesser als jede
                                  # einzelne Kurve ist.

_LATLON_TO_UTM33 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)


def fetch_raceways(bbox=BBOX, cache_path=TRACK_CACHE_PATH, force=False):
    if os.path.exists(cache_path) and not force:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    south, west, north, east = bbox
    query = f"""
    [out:json][timeout:90];
    (
      way["highway"="raceway"]({south},{west},{north},{east});
    );
    out tags geom;
    """
    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "mx5-project-track-research/1.0 (personal, non-commercial track geometry lookup)"
    })
    with urllib.request.urlopen(req, timeout=100) as resp:
        result = json.load(resp)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return result


def build_main_loop_latlon(raceways):
    """Liefert die geschlossene Runde: Knoten LOOP_START_NODE_IDX bis zum
    letzten Knoten. Der letzte Knoten ist EXAKT identisch mit Knoten
    LOOP_START_NODE_IDX (siehe Docstring) - wird daher weggelassen,
    resample_closed_loop() schliesst die Schleife automatisch wieder an
    pts[0] an (mit der echten, kurzen letzten Kantenlaenge aus den
    OSM-Daten, keine Annahme)."""
    ways = {el["id"]: el for el in raceways["elements"]}
    pts = [(p["lat"], p["lon"]) for p in ways[MAIN_LOOP_WAY_IDS[0]]["geometry"]]
    assert pts[LOOP_START_NODE_IDX] == pts[-1], (
        "Erwartete exakte Knoten-Uebereinstimmung LOOP_START_NODE_IDX <-> letzter Knoten "
        "nicht gefunden - OSM-Daten haben sich vermutlich geaendert, Annahme neu pruefen."
    )
    return np.array(pts[LOOP_START_NODE_IDX:-1])


def build_pit_connector_latlon(raceways):
    """Boxenausfahrt-Verbindungsweg: Boxengasse (172927499) + die ersten
    LOOP_START_NODE_IDX+1 Knoten von 172927073 (Boxenausfahrt bis zum
    Einmuendungspunkt auf die Runde) - nur fuer den Plot, nicht Teil der
    gewerteten Rundenlaenge."""
    ways = {el["id"]: el for el in raceways["elements"]}
    pit = [(p["lat"], p["lon"]) for p in ways[PIT_LANE_WAY_ID]["geometry"]]
    stub = [(p["lat"], p["lon"]) for p in ways[MAIN_LOOP_WAY_IDS[0]]["geometry"][:LOOP_START_NODE_IDX + 1]]
    return np.array(pit + stub)


def to_utm33(latlon):
    e, n = _LATLON_TO_UTM33.transform(latlon[:, 1], latlon[:, 0])
    return np.column_stack([e, n])


def resample_closed_loop(points_utm, step_m):
    """Schliesst die Schleife (letzter Punkt -> erster Punkt, siehe
    Docstring - das ist eine ECHTE, kurze OSM-Kante, keine Annahme mehr,
    seit LOOP_START_NODE_IDX so gewaehlt ist, dass Start- und Endknoten
    identisch sind) und resampled auf gleichmaessige Bogenlaenge. Liefert
    (resampled_points, closing_segment_m, total_length_m, closing_start_s)."""
    closing_segment_m = float(np.hypot(*(points_utm[0] - points_utm[-1])))
    pts = np.vstack([points_utm, points_utm[0:1]])
    seg_len = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = cum[-1]
    n_samples = int(round(total / step_m))
    s_new = np.linspace(0, total, n_samples, endpoint=False)
    e_new = np.interp(s_new, cum, pts[:, 0])
    n_new = np.interp(s_new, cum, pts[:, 1])
    closing_start_s = cum[-2]  # Bogenlaenge, ab der die letzte (reale) Kante beginnt
    return np.column_stack([e_new, n_new]), closing_segment_m, total, closing_start_s


def compute_curvature_radius(points_closed, window_m, step_m):
    """3-Punkt-Kreisradius (Menger-Kruemmung) mit Fenster window_m
    Bogenlaenge vor/hinter jedem Punkt, zyklisch (geschlossene Schleife)."""
    n = len(points_closed)
    w = max(1, int(round(window_m / step_m)))
    radii = np.full(n, np.inf)
    for i in range(n):
        p0 = points_closed[(i - w) % n]
        p1 = points_closed[i]
        p2 = points_closed[(i + w) % n]
        a = np.hypot(*(p1 - p0))
        b = np.hypot(*(p2 - p1))
        c = np.hypot(*(p2 - p0))
        area2 = abs((p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1]))
        if area2 < 1e-6 or a * b * c < 1e-6:
            radii[i] = np.inf
        else:
            radii[i] = (a * b * c) / (2 * area2)
    return radii


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


def _merge_close_runs(runs, step_m, merge_gap_m):
    if not runs:
        return runs
    merged = [list(runs[0])]
    gap_steps = merge_gap_m / step_m
    for start, end in runs[1:]:
        if start - merged[-1][1] <= gap_steps:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [tuple(r) for r in merged]


def find_corners(radii, s, radius_max_m, step_m, merge_gap_m):
    mask = radii < radius_max_m
    runs = _merge_close_runs(group_runs(mask), step_m, merge_gap_m)
    corners = []
    for start, end in runs:
        seg = radii[start:end + 1]
        i_min = start + int(np.argmin(seg))
        r_min = float(radii[i_min])
        v_lo = math.sqrt(TIRE_MU_RANGE[0] * G * r_min)
        v_hi = math.sqrt(TIRE_MU_RANGE[1] * G * r_min)
        corners.append({
            "s_start_m": float(s[start]), "s_end_m": float(s[end]),
            "s_apex_m": float(s[i_min]), "radius_min_m": r_min,
            "v_corner_kmh_lo": v_lo * 3.6, "v_corner_kmh_hi": v_hi * 3.6,
        })
    return corners


# --- Orthofoto-Verfeinerung ---

def fetch_orthophoto(tile=ORTHOPHOTO_TILE, cache_dir=ORTHOPHOTO_DIR):
    """Laedt/cached die amtliche Brandenburg-Orthofoto-Kachel (20cm,
    georeferenziert per World-File) und liefert (image_rgb_uint8, geo), mit
    geo = (origin_e, origin_n, pixel_size_m) fuer Pixel<->UTM33 (siehe
    Docstring: E = origin_e + col*pixel_size, N = origin_n - row*pixel_size)."""
    os.makedirs(cache_dir, exist_ok=True)
    jpg_path = os.path.join(cache_dir, f"dop_{tile}.jpg")
    jgw_path = os.path.join(cache_dir, f"dop_{tile}.jgw")
    if not (os.path.exists(jpg_path) and os.path.exists(jgw_path)):
        zip_path = os.path.join(cache_dir, f"dop_{tile}.zip")
        url = ORTHOPHOTO_URL_TEMPLATE.format(tile=tile)
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(cache_dir)
        os.remove(zip_path)

    with open(jgw_path) as f:
        wf = [float(line.strip()) for line in f.readlines()]
    pixel_size_m, _, _, _, origin_e, origin_n = wf
    image = np.array(Image.open(jpg_path))
    return image, (origin_e, origin_n, pixel_size_m)


def utm_to_pixel(e, n, geo):
    origin_e, origin_n, px = geo
    col = (e - origin_e) / px
    row = (origin_n - n) / px
    return col, row


def sample_bilinear(image, col, row):
    """Bilineare Interpolation der Bildfarbe an (col,row) (sub-Pixel-genau).
    Liefert NaN-Array, falls ausserhalb des Bildes."""
    h, w = image.shape[:2]
    if col < 0 or row < 0 or col >= w - 1 or row >= h - 1:
        return np.full(3, np.nan)
    c0, r0 = int(col), int(row)
    fc, fr = col - c0, row - r0
    p00 = image[r0, c0].astype(float)
    p01 = image[r0, c0 + 1].astype(float)
    p10 = image[r0 + 1, c0].astype(float)
    p11 = image[r0 + 1, c0 + 1].astype(float)
    top = p00 * (1 - fc) + p01 * fc
    bot = p10 * (1 - fc) + p11 * fc
    return top * (1 - fr) + bot * fr


def learn_track_color(points_utm, image, geo):
    """Tastet die Bildfarbe an allen gegebenen (groben) Streckenpunkten ab
    und liefert (median_rgb, std_rgb) als adaptive Fahrbahn-Referenzfarbe -
    siehe Docstring, Schritt 2."""
    cols, rows = utm_to_pixel(points_utm[:, 0], points_utm[:, 1], geo)
    samples = np.array([sample_bilinear(image, c, r) for c, r in zip(cols, rows)])
    samples = samples[~np.isnan(samples).any(axis=1)]
    return np.median(samples, axis=0), np.std(samples, axis=0)


def refine_centerline_with_orthophoto(points_utm, image, geo, ref_color, ref_std,
                                       scan_half_width_m=REFINE_SCAN_HALF_WIDTH_M):
    """Verfeinert jeden Streckenpunkt per senkrechtem Scan gegen das
    Orthofoto (siehe Docstring, Schritt 3). Liefert dict mit je einem
    Array pro Streckenpunkt:
      offset_m       - Mittellinien-Versatz senkrecht zur urspruenglichen
                        OSM-Linie (Rohsignal, VOR Glaettung)
      offset_left_m, offset_right_m - Versatz der beiden Fahrbahnraender
                        (linker/rechter Rand aus Sicht der Scan-Richtung)
      width_m, ok    - wie bisher.
    `refined_points` (Mittellinie als Weltkoordinaten) wird weiterhin
    zusaetzlich geliefert, ist aber nur offset_m in Weltkoordinaten
    umgerechnet - fuer die eigentliche Weiterverarbeitung sind die
    offset_m-Arrays massgeblich (koennen spaeter geglaettet werden, siehe
    smooth_offsets())."""
    n = len(points_utm)
    pixel_size = geo[2]
    scan_offsets = np.arange(-scan_half_width_m, scan_half_width_m + pixel_size, pixel_size)
    zero_idx = len(scan_offsets) // 2

    offset_c = np.full(n, np.nan)
    offset_l = np.full(n, np.nan)
    offset_r = np.full(n, np.nan)
    widths = np.full(n, np.nan)
    ok = np.zeros(n, dtype=bool)
    tangents = np.zeros((n, 2))

    for i in range(n):
        p_prev = points_utm[(i - 1) % n]
        p_next = points_utm[(i + 1) % n]
        tangent = p_next - p_prev
        norm = np.hypot(*tangent)
        if norm < 1e-6:
            continue
        tangent /= norm
        tangents[i] = tangent
        perp = np.array([-tangent[1], tangent[0]])

        scan_pts = points_utm[i] + np.outer(scan_offsets, perp)
        cols, rows = utm_to_pixel(scan_pts[:, 0], scan_pts[:, 1], geo)
        colors = np.array([sample_bilinear(image, c, r) for c, r in zip(cols, rows)])
        valid = ~np.isnan(colors).any(axis=1)
        dist = np.full(len(scan_offsets), np.inf)
        dist[valid] = np.linalg.norm((colors[valid] - ref_color) / np.maximum(ref_std, 1.0), axis=1)
        is_track = dist < REFINE_COLOR_THRESHOLD_STD

        runs = group_runs(is_track)
        if not runs:
            continue
        # zusammenhaengenden Fahrbahn-Abschnitt naechst am Nulldurchgang (Index des
        # urspruenglichen OSM-Punkts, offset=0) waehlen - siehe Docstring, Schritt 3.
        best = min(runs, key=lambda r: min(abs(r[0] - zero_idx), abs(r[1] - zero_idx)))
        mid_idx = (best[0] + best[1]) / 2.0
        offset_axis = np.arange(len(scan_offsets))

        offset_c[i] = np.interp(mid_idx, offset_axis, scan_offsets)
        offset_l[i] = np.interp(best[0], offset_axis, scan_offsets)
        offset_r[i] = np.interp(best[1], offset_axis, scan_offsets)
        widths[i] = (best[1] - best[0]) * pixel_size
        ok[i] = True

    return {
        "offset_m": offset_c, "offset_left_m": offset_l, "offset_right_m": offset_r,
        "width_m": widths, "ok": ok, "tangents": tangents,
    }


def offsets_to_points(points_utm, tangents, offset_m):
    """Rechnet einen (geglaetteten) senkrechten Versatz in Weltkoordinaten um."""
    perp = np.column_stack([-tangents[:, 1], tangents[:, 0]])
    return points_utm + offset_m[:, None] * perp


def smooth_offset_circular(offset_m, ok, window_m, step_m):
    """Glaettet ein Versatz-Signal entlang einer geschlossenen Schleife
    (Savitzky-Golay, zyklisch gepolstert) - siehe Docstring "Ortho-
    Verfeinerung: Glaettung". Punkte mit ok=False werden vorher linear
    ueber die naechsten gueltigen Nachbarn interpoliert (zyklisch), damit
    der Filter keine Luecken sieht."""
    from scipy.signal import savgol_filter

    n = len(offset_m)
    idx = np.arange(n)
    if ok.all():
        filled = offset_m.copy()
    else:
        valid_idx = idx[ok]
        # zyklische Interpolation: Werte dreifach wiederholen, mittleren Teil zurueckgeben
        rep_idx = np.concatenate([valid_idx - n, valid_idx, valid_idx + n])
        rep_val = np.tile(offset_m[ok], 3)
        filled = np.interp(idx, rep_idx, rep_val)

    w = max(5, int(round(window_m / step_m)))
    if w % 2 == 0:
        w += 1
    pad = w
    padded = np.concatenate([filled[-pad:], filled, filled[:pad]])
    smoothed = savgol_filter(padded, window_length=w, polyorder=2)
    return smoothed[pad:-pad]


def arc_length_cumulative(points_closed):
    """Kumulative Bogenlaenge einer geschlossenen Punktfolge. Liefert
    (s, total_length_m) mit s[0]=0, s[i] = Bogenlaenge bis Punkt i."""
    pts = np.vstack([points_closed, points_closed[0:1]])
    seg_len = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    return cum[:-1], float(cum[-1])


def crop_orthophoto(image, geo, bbox_utm, margin_m=30.0):
    """Schneidet das Orthofoto auf die Bounding Box (e_min,n_min,e_max,n_max)
    + Rand zu, liefert (crop, extent) fuer matplotlib imshow(extent=...)."""
    e_min, n_min, e_max, n_max = bbox_utm
    c0, r1 = utm_to_pixel(e_min - margin_m, n_min - margin_m, geo)
    c1, r0 = utm_to_pixel(e_max + margin_m, n_max + margin_m, geo)
    c0, c1 = int(max(c0, 0)), int(min(c1, image.shape[1]))
    r0, r1 = int(max(r0, 0)), int(min(r1, image.shape[0]))
    crop = image[r0:r1, c0:c1]
    origin_e, origin_n, px = geo
    extent = (origin_e + c0 * px, origin_e + c1 * px, origin_n - r1 * px, origin_n - r0 * px)
    return crop, extent


def plot_track_refined(osm_points, refined_points, radii, corners, s, ok_mask,
                        pit_connector_utm, ortho_crop, ortho_extent, out_path):
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(ortho_crop, extent=ortho_extent, origin="upper")

    ax.plot(osm_points[:, 0], osm_points[:, 1], "-", color="cyan", lw=1.0, alpha=0.7,
            label="OSM-Linie (grob, 91 Knoten)")
    r_capped = np.clip(radii, 0, 300)
    sc = ax.scatter(refined_points[:, 0], refined_points[:, 1], c=r_capped, cmap="RdYlGn",
                     s=5, vmin=0, vmax=300, label="Orthofoto-verfeinerte Mittellinie")
    if (~ok_mask).any():
        ax.scatter(refined_points[~ok_mask, 0], refined_points[~ok_mask, 1],
                    color="magenta", s=10, marker="x", label="nicht verfeinert (OSM-Position uebernommen)")
    plt.colorbar(sc, ax=ax, label="Kruemmungsradius [m] (auf 300m gedeckelt)")

    ax.plot(pit_connector_utm[:, 0], pit_connector_utm[:, 1], ":", color="dimgray", lw=1.2,
            label="Boxengasse + Ausfahrt-Verbindung")

    for c in corners:
        i_apex = int(np.argmin(np.abs(s - c["s_apex_m"])))
        ax.annotate(f"{c['radius_min_m']:.0f}m", (refined_points[i_apex, 0], refined_points[i_apex, 1]),
                    fontsize=7, color="white", fontweight="bold")

    ax.set_xlabel("UTM33 Ost [m]")
    ax.set_ylabel("UTM33 Nord [m]")
    ax.set_aspect("equal")
    ax.set_title("Spreewaldring - Orthofoto-verfeinerte Streckenrekonstruktion")
    ax.legend(fontsize=7, loc="upper right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_track(points, radii, corners, s, pit_connector_utm, out_path):
    fig, ax = plt.subplots(figsize=(9, 9))
    r_capped = np.clip(radii, 0, 300)
    sc = ax.scatter(points[:, 0], points[:, 1], c=r_capped, cmap="RdYlGn", s=6, vmin=0, vmax=300)
    plt.colorbar(sc, ax=ax, label="Kruemmungsradius [m] (auf 300m gedeckelt)")

    ax.plot(pit_connector_utm[:, 0], pit_connector_utm[:, 1], ":", color="dimgray", lw=1.5,
            label="Boxengasse + Ausfahrt-Verbindung (nicht Teil der Runde)")

    for c in corners:
        i_apex = int(np.argmin(np.abs(s - c["s_apex_m"])))
        ax.annotate(f"{c['radius_min_m']:.0f}m", (points[i_apex, 0], points[i_apex, 1]),
                    fontsize=7, color="black")

    ax.set_xlabel("UTM33 Ost [m]")
    ax.set_ylabel("UTM33 Nord [m]")
    ax.set_aspect("equal")
    ax.set_title("Spreewaldring Training Center - Streckenrekonstruktion (OSM)")
    ax.legend(fontsize=8, loc="upper right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close(fig)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("Lade Raceway-Geometrie (OSM Overpass, gecacht) ...")
    raceways = fetch_raceways()
    latlon = build_main_loop_latlon(raceways)
    print(f"Rundstrecke: {len(latlon)} OSM-Knoten aus way {MAIN_LOOP_WAY_IDS[0]} "
          f"(Index {LOOP_START_NODE_IDX}..94, exakt geschlossen - siehe Docstring)")
    pit_connector_utm = to_utm33(build_pit_connector_latlon(raceways))

    utm = to_utm33(latlon)
    points, closing_segment_m, total_len_m, closing_start_s = resample_closed_loop(utm, RESAMPLE_STEP_M)
    print(f"Streckenlaenge (vollstaendig aus OSM, keine Annahme): {total_len_m:.1f} m")
    print(f"  letzte Kante (schliesst die Runde, real, {closing_segment_m:.1f} m)")
    print(f"Resampled auf {len(points)} Punkte, Schrittweite {RESAMPLE_STEP_M} m")

    s = np.arange(len(points)) * RESAMPLE_STEP_M
    radii = compute_curvature_radius(points, CURVATURE_WINDOW_M, RESAMPLE_STEP_M)
    corners = find_corners(radii, s, CORNER_RADIUS_MAX_M, RESAMPLE_STEP_M, CORNER_MERGE_GAP_M)

    print(f"\n{len(corners)} Kurven gefunden (Radius < {CORNER_RADIUS_MAX_M:.0f}m, "
          f"Fenster {CURVATURE_WINDOW_M:.0f}m):")
    print(f"{'#':>3s} {'s_apex':>8s} {'R_min':>7s} {'v_corner (mu=1.0-1.3)':>24s}")
    for i, c in enumerate(sorted(corners, key=lambda c: c["s_apex_m"])):
        print(f"{i+1:3d} {c['s_apex_m']:7.0f}m {c['radius_min_m']:6.1f}m "
              f"{c['v_corner_kmh_lo']:6.1f}-{c['v_corner_kmh_hi']:5.1f} km/h")

    plot_track(points, radii, corners, s, pit_connector_utm,
               os.path.join(RESULTS_DIR, "spreewaldring_track.png"))
    print(f"\nStrecken-Plot: {RESULTS_DIR}/spreewaldring_track.png")

    summary = {
        "source": "OpenStreetMap (Overpass API), way " + str(MAIN_LOOP_WAY_IDS[0])
                  + f" (node index {LOOP_START_NODE_IDX}..94, exact closure)",
        "pit_lane_way_id_excluded_from_lap": PIT_LANE_WAY_ID,
        "shortcut_way_ids_not_reconstructed": SHORTCUT_WAY_IDS,
        "total_length_m": total_len_m,
        "closing_segment_m_real_osm_edge": closing_segment_m,
        "resample_step_m": RESAMPLE_STEP_M,
        "curvature_window_m": CURVATURE_WINDOW_M,
        "corner_radius_max_m": CORNER_RADIUS_MAX_M,
        "tire_mu_range_used": TIRE_MU_RANGE,
        "corners": corners,
        "track_points_utm33": points.tolist(),
        "radius_m": [None if not math.isfinite(r) else r for r in radii.tolist()],
        "s_m": s.tolist(),
    }

    # --- Orthofoto-Verfeinerung ---
    print(f"\n=== Orthofoto-Verfeinerung (Kachel {ORTHOPHOTO_TILE}, LGB Brandenburg, 20cm) ===")
    image, geo = fetch_orthophoto()
    ref_color, ref_std = learn_track_color(points, image, geo)
    print(f"Gelernte Fahrbahnfarbe (RGB Median): {ref_color.round(1).tolist()}  "
          f"Streuung: {ref_std.round(1).tolist()}")

    det = refine_centerline_with_orthophoto(points, image, geo, ref_color, ref_std)
    ok, widths = det["ok"], det["width_m"]
    print(f"Verfeinert: {ok.sum()}/{len(ok)} Punkte "
          f"({100 * ok.sum() / len(ok):.0f}%), Rest = OSM-Position uebernommen")
    print(f"Gemessene Fahrbahnbreite: Median={np.nanmedian(widths):.2f}m  "
          f"Mittel={np.nanmean(widths):.2f}m  (Betreiberangabe: {TRACK_WIDTH_M}m, "
          f"siehe Docstring - wird hier nicht als Zielgroesse verwendet)")

    # Rohe (ungeglaettete) Mittellinie - zum Vergleich
    refined_raw = offsets_to_points(points, det["tangents"], np.nan_to_num(det["offset_m"]))

    # Geglaettete Mittellinie: Savitzky-Golay auf dem Versatz-Signal (siehe
    # Docstring "Ortho-Verfeinerung: Glaettung") - behebt das vom Nutzer
    # bemerkte Wackeln auf den Geraden (siehe docs/logs/projekt-stand.md fuer die
    # Diagnose: bis zu 4,7m Abweichung von einer Geraden auf einem
    # 87m-Abschnitt VOR der Glaettung).
    offset_smooth = smooth_offset_circular(det["offset_m"], ok, SMOOTH_WINDOW_M, RESAMPLE_STEP_M)
    refined = offsets_to_points(points, det["tangents"], offset_smooth)

    s_refined, total_len_refined = arc_length_cumulative(refined)
    radii_refined = compute_curvature_radius(refined, CURVATURE_WINDOW_M, RESAMPLE_STEP_M)
    corners_refined = find_corners(radii_refined, s_refined, CORNER_RADIUS_MAX_M,
                                    RESAMPLE_STEP_M, CORNER_MERGE_GAP_M)
    print(f"Streckenlaenge (verfeinert, geglaettet): {total_len_refined:.1f} m "
          f"(OSM-Naeherung: {total_len_m:.1f} m)")

    # Jitter-Diagnose: max. Abweichung von einer Geraden auf einem Abschnitt,
    # der laut den ROHEN OSM-Knoten GARANTIERT gerade ist (einzelne Kante
    # ohne Zwischenknoten, Original-Knoten-Index 91->92 von way 172927073,
    # 134,7m - siehe Docstring). Ein erster Test auf s=476-563m ergab
    # zunaechst 4,7m Abweichung, stellte sich aber als reale sanfte Kurve
    # heraus (R~210m ueber 87m Bogenlaenge erklaert genau diese Abweichung
    # rechnerisch) - false positive, NICHT Rauschen. Testabschnitt daher
    # auf den tatsaechlich verifizierten Geraden-Abschnitt korrigiert.
    test_mask = (s >= 2255) & (s <= 2389)
    if test_mask.sum() >= 2:
        for label, pts_cmp in [("roh", refined_raw), ("geglaettet", refined)]:
            seg = pts_cmp[test_mask]
            p0, p1 = seg[0], seg[-1]
            d_unit = (p1 - p0) / np.hypot(*(p1 - p0))
            perp_t = np.array([-d_unit[1], d_unit[0]])
            devs = (seg - p0) @ perp_t
            print(f"  Jitter-Test s=2255-2389m, bekannte Gerade ({label}): "
                  f"max|Abw.|={np.max(np.abs(devs)):.2f}m  std={np.std(devs):.2f}m")

    print(f"\n{len(corners_refined)} Kurven (verfeinert, Radius < {CORNER_RADIUS_MAX_M:.0f}m):")
    print(f"{'#':>3s} {'s_apex':>8s} {'R_min':>7s} {'v_corner (mu=1.0-1.3)':>24s}")
    for i, c in enumerate(sorted(corners_refined, key=lambda c: c["s_apex_m"])):
        print(f"{i+1:3d} {c['s_apex_m']:7.0f}m {c['radius_min_m']:6.1f}m "
              f"{c['v_corner_kmh_lo']:6.1f}-{c['v_corner_kmh_hi']:5.1f} km/h")

    e_min, n_min = refined.min(axis=0)
    e_max, n_max = refined.max(axis=0)
    ortho_crop, ortho_extent = crop_orthophoto(image, geo, (e_min, n_min, e_max, n_max))
    plot_track_refined(points, refined, radii_refined, corners_refined, s_refined, ok,
                        pit_connector_utm, ortho_crop, ortho_extent,
                        os.path.join(RESULTS_DIR, "spreewaldring_track_refined.png"))
    print(f"\nVerfeinerter Strecken-Plot: {RESULTS_DIR}/spreewaldring_track_refined.png")

    summary["orthophoto_refinement"] = {
        "tile": ORTHOPHOTO_TILE,
        "pixel_size_m": geo[2],
        "reference_color_rgb_median": ref_color.tolist(),
        "reference_color_rgb_std": ref_std.tolist(),
        "n_refined": int(ok.sum()),
        "n_total": len(ok),
        "measured_width_m_median": float(np.nanmedian(widths)),
        "measured_width_m_mean": float(np.nanmean(widths)),
        "operator_stated_width_m": TRACK_WIDTH_M,
        "smooth_window_m": SMOOTH_WINDOW_M,
        "total_length_m_refined": total_len_refined,
        "corners_refined": corners_refined,
        "track_points_utm33_refined": refined.tolist(),
        "track_points_utm33_refined_raw": refined_raw.tolist(),
        "radius_m_refined": [None if not math.isfinite(r) else r for r in radii_refined.tolist()],
        "s_m_refined": s_refined.tolist(),
        "point_refined_mask": ok.tolist(),
        "point_width_m": [None if math.isnan(w) else w for w in widths.tolist()],
        "offset_left_m": [None if math.isnan(x) else x for x in det["offset_left_m"].tolist()],
        "offset_right_m": [None if math.isnan(x) else x for x in det["offset_right_m"].tolist()],
    }

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_track_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
