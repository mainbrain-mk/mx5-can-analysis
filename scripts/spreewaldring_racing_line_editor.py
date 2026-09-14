"""
Spreewaldring - interaktiver Ideallinien-Editor (NEU, 30.08.2026)

Nutzerauftrag: nach der alternierenden Linie/Geschwindigkeits-Optimierung
(`spreewaldring_racing_line_optimal.py`, 96.03s bei mu=1.0) und der lokalen
Kurve-1-Nachoptimierung (`spreewaldring_racing_line_corner.py`) wollte der
Nutzer die entstandene Linie an einem BELIEBIGEN Punkt selbst per Maus
verschieben koennen (Pinsel mit Falloff, verschobener Punkt wird zum harten
Fixpunkt/Scheitelpunkt), danach automatisch neu optimieren lassen (Abbruch
sobald eine Iteration die Rundenzeit verschlechtert, nur das Optimum wird
behalten) und das Ergebnis (Rundenzeit, Geschwindigkeit, Bremszonen) live
sehen - alles bei festem mu=1.0 (Nutzervorgabe, siehe Memory "mx5-tires"/
PROJEKT_STAND.md).

WICHTIGE ARCHITEKTURENTSCHEIDUNG (mit Nutzer abgestimmt): das braucht KEINEN
Python-Backend-Server. Die Physik (kombinierter Reifenkraftkreis-Loeser aus
`spreewaldring_racing_line_optimal.py`, Traktionsmodell aus
`performance_simulation.py`) ist billig genug (O(n) mit n~830 Punkten), um
1:1 nach JavaScript portiert zu werden und komplett im Browser zu laufen -
keine Serveranfrage pro Bearbeitungsschritt, keine Wartezeit. Dieses Skript
exportiert einmalig alle dafuer noetigen Daten (Streckenkorridor, Start-
Ideallinie, Physik-Konstanten) in eine einzige selbststaendige HTML-Datei.

WIEDERVERWENDUNG statt Neuberechnung: Kurvennummern (bereits um den
Schliesspunkt-Sonderfall bereinigt, 11 statt 12) und das zugeschnittene
Orthofoto (LGB Brandenburg, bereits als JPEG eingebettet) werden NICHT neu
berechnet/heruntergeladen, sondern direkt aus der bestehenden
`spreewaldring_racing_line_animation.html` (frueherer Schritt, siehe
PROJEKT_STAND.md) uebernommen - beide sind dort bereits verifiziert. Alle
uebrigen Daten (Korridor, Fixpunkt-Startlinie, Physik-Konstanten) kommen
frisch/autoritativ aus den eigentlichen Quellskripten/-JSONs, um Drift durch
doppelt gepflegte Werte zu vermeiden.

INTERAKTIONS-DESIGN (mit Nutzer abgestimmt, siehe Konversation):
  - Klick+Drag auf die Linie: Pinsel mit Cosinus-Falloff verschiebt eine
    Nachbarschaft live waehrend des Ziehens (schneller Einzel-Speedloesungs-
    Vorschau, keine volle Relaxation - bleibt fluessig bei 60fps).
  - Loslassen: der exakt getroffene Punkt wird zum harten Fixpunkt (bleibt
    JEDE Iteration exakt an dieser Stelle, wie ein Scheitelpunkt-Constraint -
    technisch: dieser eine Index wird bei jedem Relaxationsschritt von der
    Update-Regel ausgenommen). Alternierende Optimierung (Geschwindigkeit
    <-> Linie, wie im Ursprungsalgorithmus) laeuft danach automatisch weiter,
    bis eine Iteration die Rundenzeit verschlechtert - dann Abbruch, nur die
    letzte (bessere) Iteration wird behalten (Nutzervorgabe).
  - Kein explizites Fenster/Hann-Taper wie in `spreewaldring_racing_line_
    corner.py` noetig: da nur EIN Punkt hart gepinnt wird (nicht ein Fenster
    eingefroren), relaxiert der Rest der Runde ganz natuerlich um den Pin
    herum - funktioniert automatisch auch fuer Punkte am Schliesspunkt der
    Schleife (Modulo-Arithmetik), ohne die manuelle Sonderbehandlung, die
    Kurve 1 im Python-Skript brauchte.
  - Bremszonen werden NICHT per Schwellwert-Heuristik auf der Geschwindigkeit
    bestimmt, sondern direkt aus der Physik des Zweipass-Algorithmus
    abgeleitet: ein Punkt ist "Bremszone", wenn der RUECKWAERTS-Pass die
    Geschwindigkeit gegenueber dem VORWAERTS-Pass an dieser Stelle reduziert
    hat (v_final < v_vorwaerts) - das ist exakt die Definition von "hier
    wird gebremst, um eine spaetere Kurve zu schaffen".
  - Persistenz: bearbeitete Linie wird in localStorage gespeichert (bleibt
    bei Neuladen erhalten), Reset-Knopf setzt auf die Ausgangslinie zurueck.

Aufruf: .venv/bin/python scripts/spreewaldring_racing_line_editor.py

NACHTRAG (30.08.2026) - echte Schaltzeiten: der JS-Geschwindigkeitsloeser
fuehrt jetzt (wie das Python-Original in spreewaldring_racing_line_optimal.py,
siehe dortiger Docstring "Nachtrag Schaltzeiten") einen tatsaechlichen Gang
mit und fuegt bei jedem Hochschalten die echte ATTACK-Zugkraftunterbrechung
als Rollphase ein, statt der reinen Beschleunigungs-Huellkurve ueber alle
Gaenge. Ein "Teillastkennfeld statt Volllastkurve" wurde NICHT uebernommen -
empirisch an 47 echten Volllast-Segmenten geprueft, verschlechtert das die
Vorhersage (siehe Python-Docstring fuer die genauen Zahlen).
"""
import os
import re
import json

import numpy as np
from scipy.signal import savgol_filter

from spreewaldring_track import RESULTS_DIR, CURVATURE_WINDOW_M, RESAMPLE_STEP_M
from spreewaldring_track_surface import compute_tangents_closed
from spreewaldring_racing_line import load_corridor, corridor_bounds, CAR_HALF_WIDTH_M
from spreewaldring_lap_simulation import V_MIN_MS
from spreewaldring_racing_line_optimal import BRAKE_CAP_G
from drivetrain_model_validation import (
    MASS_KG, R_DYN_M, FINAL_DRIVE, GEAR_RATIOS, ETA, CDA_M2, RHO_KG_M3, CRR, G,
    RPM_TABLE, TORQUE_TABLE_NM,
)
from performance_simulation import TRACTION_MAX_FORCE_N, ATTACK_SHIFT_S, REDLINE_RPM

ANIMATION_HTML_PATH = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_animation.html")
CORNER1_SUMMARY_PATH = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_corners_summary.json")
OUT_PATH = os.path.join(RESULTS_DIR, "spreewaldring_racing_line_editor.html")

MU_FIXED = 1.0          # Nutzervorgabe: nur noch ein Reifen-Szenario im interaktiven Tool
BASE_ALPHA = 0.15        # wie in spreewaldring_racing_line_optimal.py
INNER_STEPS = 80         # wie in spreewaldring_racing_line_optimal.py
N_OUTER_MAX = 30          # Sicherheitsnetz - Abbruch bei Verschlechterung greift i.d.R. viel frueher
BRUSH_RADIUS_M_DEFAULT = 60.0

# NACHTRAG (30.08.2026): Nutzer fand einen Knick in der Ideallinie an Kurve 6
# (s~1304m, Index~435), der sich durch Ziehen NICHT wegbekommen liess - die
# Randerkennung (spreewaldring_track_surface.py) hat dort kleinraeumiges
# Rauschen (Pruefung ergab bis zu 2.3m Sprung der Korridorbreite von einem
# 3m-Schritt zum naechsten, deutlich mehr als an den meisten anderen Stellen),
# vermutlich eine Scan-Mehrdeutigkeit in dieser engen Kurve. Da eine feste
# Fixpunkt-Relaxation den Rand als HARTE Grenze behandelt, vererbt sich jedes
# Rauschen im Rand 1:1 in die Linie und kann durch Ziehen nicht geglaettet
# werden. Fix HIER (nur fuer den interaktiven Editor, NICHT das "offizielle"
# Streckenmodell in spreewaldring_track_surface_summary.json - falls die
# Randerkennung selbst an der Wurzel korrigiert werden soll, ist das ein
# separater, groesserer Schritt): linker/rechter Fahrbahnrand wird vor der
# Korridor-Berechnung mit einem Savitzky-Golay-Filter (zyklisch, passend zur
# bereits im Projekt etablierten Glaettungsmethodik) geglaettet. Getestet:
# reduziert die maximale Randbreiten-Sprunggroesse von 2.3m auf ~1.5-1.7m pro
# 3m-Schritt, keine invertierten (lo>hi) oder auf 0 zurueckfallenden Punkte,
# Mindestbreite bleibt bei ueber 6m (Auto braucht nur 1.74m).
CORRIDOR_SMOOTH_WINDOW_M = 27.0

# NACHTRAG (30.08.2026) - echte Start-Ziel-Linie statt Zufallsindex: Index 0
# der Streckenpunkt-Arrays (aus spreewaldring_track.py, LOOP_START_NODE_IDX)
# lag bisher an Kurve 1 - ein Zufallsprodukt davon, wo sich die urspruenglichen
# OSM-Rohdaten sauber schliessen liessen, NICHT die echte Start-Ziel-Linie.
# Nutzer hat die echte Linie (weisser Querstrich vor den aufgemalten
# Startboxen auf der Zielgeraden) im georeferenzierten Orthofoto markiert -
# rechnerisch trifft das Index 779 (nur 2.5m Abstand zur Mittellinie, klar
# eindeutig), 153m VOR dem bisherigen Index 0 auf derselben Geraden. Alle
# Streckenpunkt-Arrays werden deshalb um -779 rotiert, damit Index 0 (und
# damit s=0m im Editor - Drehzahl-/Geschwindigkeitsdiagramm, Schaltpunkt-
# Liste) jetzt an der ECHTEN Start-Ziel-Linie liegt.
START_FINISH_ROLL = 779


def smooth_closed_curve(points, window_m, step_m=RESAMPLE_STEP_M, polyorder=2):
    """Savitzky-Golay-Glaettung eines geschlossenen Punktzugs (zyklisch,
    mode='wrap' - die Kurve hat keinen Anfang/Ende). Glaettet x/y unabhaengig,
    siehe Docstring bei CORRIDOR_SMOOTH_WINDOW_M fuer den Anlass."""
    win = max(polyorder + 1, int(round(window_m / step_m)))
    if win % 2 == 0:
        win += 1
    x = savgol_filter(points[:, 0], win, polyorder, mode="wrap")
    y = savgol_filter(points[:, 1], win, polyorder, mode="wrap")
    return np.column_stack([x, y])


def load_reused_assets():
    """Kurvennummern + zugeschnittenes Orthofoto aus der bestehenden
    Animation uebernehmen (dort bereits verifiziert, siehe Docstring)."""
    with open(ANIMATION_HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r'<script type="application/json" id="viz-data">(.*?)</script>', html, re.S)
    old = json.loads(m.group(1))
    return {
        "origin_utm33": old["origin_utm33"],
        "corners_local": old["corners"],
        "background_image_jpeg_b64": old["background_image_jpeg_b64"],
        "background_extent_local": old["background_extent_local"],
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("Lade Fahrbahn-Korridor (Hauptschleife) ...")
    center, left, right = load_corridor()
    tangents = compute_tangents_closed(center)
    lo_raw, hi_raw, _ = corridor_bounds(center, left, right, tangents)
    print(f"Rand-Rauschen VOR Glaettung: max. Breitenaenderung pro 3m-Schritt "
          f"{np.max(np.abs(np.diff(np.append(hi_raw-lo_raw, (hi_raw-lo_raw)[0])))):.2f}m")

    # Rand glaetten, dann Korridor NEU daraus ableiten (siehe Docstring bei
    # CORRIDOR_SMOOTH_WINDOW_M) - damit bleibt links/rechts unzweideutig
    # (dieselbe corridor_bounds()-Logik wie im "offiziellen" Modell), nur mit
    # geglaetteten Eingangsdaten statt eigener lo/hi-Nachbearbeitung.
    left = smooth_closed_curve(left, CORRIDOR_SMOOTH_WINDOW_M)
    right = smooth_closed_curve(right, CORRIDOR_SMOOTH_WINDOW_M)
    lo, hi, perp = corridor_bounds(center, left, right, tangents)
    n_total = len(center)
    print(f"Rand-Rauschen NACH Glaettung ({CORRIDOR_SMOOTH_WINDOW_M:.0f}m-Fenster): "
          f"max. Breitenaenderung pro 3m-Schritt "
          f"{np.max(np.abs(np.diff(np.append(hi-lo, (hi-lo)[0])))):.2f}m, "
          f"Korridorbreite Median {np.median(hi - lo):.2f}m, Minimum {np.min(hi - lo):.2f}m")

    print("Lade Start-Ideallinie (alle 11 Kurven nacheinander lokal optimiert) ...")
    with open(CORNER1_SUMMARY_PATH, encoding="utf-8") as f:
        corner1 = json.load(f)
    # bestes Snapshot ueber ALLE Kurven/Iterationen (Lap-Time faellt pro Kurve
    # monoton, das globale Minimum liegt daher praktisch immer im letzten
    # bearbeiteten Fenster - kein separates "best_local_iteration"-Feld mehr,
    # da jetzt 11 Kurven statt nur einer nacheinander optimiert werden)
    start_snapshot = min(corner1["snapshots"], key=lambda s: s["lap_time_s"])
    print(f"Beste Iteration: Kurve {start_snapshot['corner']}, Rundenzeit {start_snapshot['lap_time_s']:.2f}s")
    start_points = np.array(start_snapshot["points_utm33"])
    assert len(start_points) == n_total, "Startlinie und Korridor muessen dieselbe Punktanzahl haben"
    n_lat_start = np.sum((start_points - center) * perp, axis=1)
    # die Startlinie wurde urspruenglich gegen den UNGEGLAETTETEN Korridor
    # optimiert - an einzelnen Punkten koennte sie dadurch knapp ausserhalb
    # des jetzt geglaetteten (i.d.R. etwas konservativeren) Korridors liegen.
    n_clipped = np.sum((n_lat_start < lo) | (n_lat_start > hi))
    if n_clipped:
        print(f"Hinweis: {n_clipped} Punkte der Startlinie lagen knapp ausserhalb "
              f"des geglaetteten Korridors, werden hineingeclippt.")
    n_lat_start = np.clip(n_lat_start, lo, hi)
    print(f"Start-Rundenzeit bei mu={MU_FIXED} wird im Browser neu berechnet "
          f"(Referenzwerte aus Vorlaeufer-Skripten nutzten mu=1.3 fuer die Optimierung selbst).")

    print(f"Rotiere Streckenpunkte um {START_FINISH_ROLL} - Index 0/s=0m liegt jetzt an der "
          f"echten (nutzerbestaetigten) Start-Ziel-Linie statt an Kurve 1.")
    center = np.roll(center, -START_FINISH_ROLL, axis=0)
    left = np.roll(left, -START_FINISH_ROLL, axis=0)
    right = np.roll(right, -START_FINISH_ROLL, axis=0)
    perp = np.roll(perp, -START_FINISH_ROLL, axis=0)
    lo = np.roll(lo, -START_FINISH_ROLL, axis=0)
    hi = np.roll(hi, -START_FINISH_ROLL, axis=0)
    n_lat_start = np.roll(n_lat_start, -START_FINISH_ROLL, axis=0)

    print("Uebernehme Kurvennummern + Orthofoto aus der bestehenden Animation ...")
    reused = load_reused_assets()
    origin = np.array(reused["origin_utm33"])

    def to_local(pts):
        return (pts - origin).tolist()

    data = {
        "origin_utm33": reused["origin_utm33"],
        "centerline": to_local(center),
        "left_edge": to_local(left),
        "right_edge": to_local(right),
        "perp": perp.tolist(),
        "corridor_lo_m": lo.tolist(),
        "corridor_hi_m": hi.tolist(),
        "n_lat_start": n_lat_start.tolist(),
        "corners": reused["corners_local"],
        "background_image_jpeg_b64": reused["background_image_jpeg_b64"],
        "background_extent_local": reused["background_extent_local"],
        "car_half_width_m": CAR_HALF_WIDTH_M,
        "physics": {
            "MASS_KG": MASS_KG, "R_DYN_M": R_DYN_M, "FINAL_DRIVE": FINAL_DRIVE,
            "GEAR_RATIOS": GEAR_RATIOS, "ETA": ETA, "CDA_M2": CDA_M2,
            "RHO_KG_M3": RHO_KG_M3, "CRR": CRR, "G": G,
            "RPM_TABLE": RPM_TABLE.tolist(), "TORQUE_TABLE_NM": TORQUE_TABLE_NM.tolist(),
            "TRACTION_MAX_FORCE_N": TRACTION_MAX_FORCE_N,
            "MU": MU_FIXED, "BRAKE_CAP_G": BRAKE_CAP_G, "CURVATURE_WINDOW_M": CURVATURE_WINDOW_M,
            "RESAMPLE_STEP_M": RESAMPLE_STEP_M, "V_MIN_MS": V_MIN_MS,
            "REDLINE_RPM": REDLINE_RPM,
            "ATTACK_SHIFT_S": {f"{g1}-{g2}": t for (g1, g2), t in ATTACK_SHIFT_S.items()},
        },
        "optimizer": {
            "base_alpha": BASE_ALPHA, "inner_steps": INNER_STEPS, "n_outer_max": N_OUTER_MAX,
            "brush_radius_m_default": BRUSH_RADIUS_M_DEFAULT,
        },
    }

    html = build_html(data)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"\nInteraktiver Editor geschrieben: {OUT_PATH} ({size_kb:.0f} KB)")


def build_html(data):
    data_json = json.dumps(data, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


HTML_TEMPLATE = r"""<meta charset="utf-8">
<title>Ideallinien-Editor Spreewaldring</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;700;800&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root{
    --bg:#12171b;
    --surface:#1a2126;
    --surface-2:#212a30;
    --border:#313c43;
    --text:#eef2f3;
    --text-muted:#8fa2ab;
    --accent-amber:#e8a33d;
    --accent-teal:#45d6c4;
    --accent-rose:#d97a72;
    --accent-brake:#e0483c;
  }
  *{box-sizing:border-box;}
  html,body{height:100%;}
  body{
    margin:0;
    background:var(--bg);
    color:var(--text);
    font-family:"IBM Plex Sans", system-ui, sans-serif;
    display:flex;
    flex-direction:column;
    min-height:100vh;
  }
  .eyebrow{
    font-family:"IBM Plex Mono", ui-monospace, monospace;
    font-size:0.72rem;
    letter-spacing:0.12em;
    text-transform:uppercase;
    color:var(--text-muted);
  }
  header{
    padding:1.1rem 1.5rem 0.9rem;
    border-bottom:1px solid var(--border);
    display:flex;
    align-items:baseline;
    gap:0.9rem;
    flex-wrap:wrap;
  }
  h1{
    font-family:"Big Shoulders Display", sans-serif;
    font-weight:800;
    font-size:1.7rem;
    letter-spacing:0.01em;
    margin:0;
    text-transform:uppercase;
  }
  header .sub{
    font-size:0.85rem;
    color:var(--text-muted);
  }
  main{
    flex:1;
    min-height:0; /* flex-item-Default ist ebenfalls min-height:auto (content-
      basiert) - ohne das hier ignoriert main die 100vh-Deckelung von body
      genauso wie .rail/.stage weiter unten dieselbe Grid-Variante des Bugs */
    display:grid;
    grid-template-columns:minmax(0,1fr) 22rem;
    grid-template-rows:minmax(0,1fr); /* sonst waechst die (einzige) Auto-Zeile
      mit dem Inhalt der Seitenleiste mit, statt sich auf die verfuegbare
      Hoehe zu begrenzen - siehe .rail/.stage min-height:0 fuer die andere
      Haelfte desselben Grid-Blowout-Bugs */
    gap:1px;
    background:var(--border);
  }
  @media (max-width:900px){
    main{grid-template-columns:1fr;}
  }
  .stage{
    background:var(--bg);
    display:flex;
    flex-direction:column;
    min-width:0;
    min-height:0;
  }
  .canvas-wrap{
    position:relative;
    flex:1;
    min-height:420px;
  }
  .canvas-wrap canvas#trackCanvas{
    position:absolute;
    inset:0;
    width:100%;
    height:100%;
    display:block;
    touch-action:none;
    cursor:grab;
  }
  #hoverTooltip{
    position:absolute;
    pointer-events:none;
    background:rgba(10,14,17,0.92);
    border:1px solid rgba(238,242,243,0.25);
    border-radius:6px;
    padding:0.4rem 0.6rem;
    font-size:0.75rem;
    line-height:1.4;
    color:#eef2f3;
    white-space:nowrap;
    z-index:5;
    display:none;
  }
  #hoverTooltip .nearest{ color:#9aa4a9; }
  #hoverTooltip .chainable{ color:#45d6c4; }
  .legend{
    display:flex;
    align-items:center;
    gap:1.4rem;
    padding:0.7rem 1.2rem;
    border-top:1px solid var(--border);
    font-family:"IBM Plex Mono", monospace;
    font-size:0.72rem;
    color:var(--text-muted);
    flex-wrap:wrap;
  }
  .legend .speedscale{
    display:flex;
    align-items:center;
    gap:0.6rem;
  }
  .legend .bar{
    width:11rem;
    height:8px;
    border-radius:2px;
    background:linear-gradient(90deg,#3a6dd1 0%,#45d6c4 33%,#e8a33d 66%,#e0483c 100%);
  }
  .legend .swatch{
    display:inline-block;
    width:0.9rem;
    height:0.9rem;
    border-radius:2px;
    vertical-align:middle;
    margin-right:0.35rem;
  }
  .pedals-row{
    display:flex;
    align-items:flex-end;
    gap:0.7rem;
  }
  .pedal-bar{
    width:1.7rem;
    height:4.2rem;
    border:2px solid;
    border-radius:3px;
    display:flex;
    align-items:flex-end;
    overflow:hidden;
    flex-shrink:0;
  }
  .pedal-bar.brake{border-color:var(--accent-brake);}
  .pedal-bar.throttle{border-color:#4ade80;}
  .pedal-fill{width:100%; height:0%;}
  .pedal-bar.brake .pedal-fill{background:var(--accent-brake);}
  .pedal-bar.throttle .pedal-fill{background:#4ade80;}
  .wheel{
    width:4.2rem;
    height:4.2rem;
    border:2px solid var(--text-muted);
    border-radius:50%;
    position:relative;
    flex-shrink:0;
    margin-left:0.2rem;
  }
  .wheel-rotor{position:absolute; inset:0;}
  .wheel-dot{
    position:absolute;
    top:0.15rem;
    left:50%;
    width:0.55rem;
    height:0.55rem;
    transform:translateX(-50%);
    background:var(--accent-brake);
    border-radius:50%;
  }
  .trace-wrap{
    height:180px;
    border-top:1px solid var(--border);
    flex-shrink:0;
  }
  .trace-wrap canvas{
    width:100%;
    height:100%;
    display:block;
  }
  .rail{
    background:var(--surface);
    display:flex;
    flex-direction:column;
    gap:1.1rem;
    padding:1.2rem 1.3rem 1.4rem;
    overflow-y:auto;
    min-height:0; /* Grid-Item-Default ist min-height:auto (=content-basiert) -
                     ohne das hier sprengt eine lange Editier-/Schaltliste die
                     ganze Zeile statt selbst zu scrollen, das verschiebt dann
                     die Karte + Canvas-Aufloesung auseinander (Klick-Offset) */
  }
  .panel{
    background:var(--surface-2);
    border:1px solid var(--border);
    border-radius:6px;
    padding:0.9rem 1rem;
  }
  .panel h2{
    font-family:"IBM Plex Mono", monospace;
    font-size:0.7rem;
    letter-spacing:0.1em;
    text-transform:uppercase;
    color:var(--text-muted);
    margin:0 0 0.6rem;
    font-weight:500;
  }
  .readout-grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:0.7rem 0.9rem;
  }
  .readout{
    display:flex;
    flex-direction:column;
    gap:0.15rem;
  }
  .readout .label{
    font-size:0.68rem;
    color:var(--text-muted);
    letter-spacing:0.04em;
  }
  .readout .value{
    font-family:"IBM Plex Mono", monospace;
    font-variant-numeric:tabular-nums;
    font-size:1.5rem;
    font-weight:600;
    line-height:1.1;
  }
  .readout .value.accent{color:var(--accent-teal);}
  .readout .value.small{font-size:1.05rem;}
  .badge{
    display:inline-block;
    margin-top:0.3rem;
    font-family:"IBM Plex Mono", monospace;
    font-size:0.66rem;
    letter-spacing:0.06em;
    color:var(--accent-amber);
    border:1px solid var(--accent-amber);
    border-radius:3px;
    padding:0.15rem 0.4rem;
  }
  .row{
    display:flex;
    gap:0.5rem;
    flex-wrap:wrap;
  }
  button.action{
    background:var(--surface);
    border:1px solid var(--border);
    color:var(--text);
    border-radius:5px;
    padding:0.5rem 0.8rem;
    font-family:"IBM Plex Sans", sans-serif;
    font-size:0.8rem;
    cursor:pointer;
    transition:background 0.15s, border-color 0.15s;
  }
  button.action:hover{border-color:var(--accent-teal);}
  button.action:disabled{opacity:0.4; cursor:default;}
  button.action:focus-visible{outline:2px solid var(--accent-teal); outline-offset:1px;}
  #shiftsList{
    max-height:190px;
    overflow-y:auto;
  }
  .edit-row{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:0.5rem;
    padding:0.4rem 0.1rem;
    border-bottom:1px solid var(--border);
    font-family:"IBM Plex Mono", monospace;
    font-size:0.76rem;
  }
  .edit-row:last-child{border-bottom:none;}
  .edit-row .marker-dot{
    display:inline-block;
    width:0.6rem;
    height:0.6rem;
    border-radius:2px;
    background:var(--accent-teal);
    transform:rotate(45deg);
    margin-right:0.5rem;
  }
  .note-inline{
    color:var(--text-muted);
    font-size:0.7rem;
  }
  .hard-toggle{
    display:flex;
    align-items:center;
    gap:0.25rem;
    font-size:0.7rem;
    color:var(--text-muted);
    white-space:nowrap;
  }
  .hard-toggle input{ margin:0; }
  button.action.del{
    padding:0.15rem 0.5rem;
    border-radius:4px;
    font-size:0.8rem;
    line-height:1.2;
    color:var(--accent-rose);
  }
  button.action.del:hover{border-color:var(--accent-rose);}
  .slider-row{
    display:flex;
    flex-direction:column;
    gap:0.35rem;
  }
  .slider-row .slabel{
    display:flex;
    justify-content:space-between;
    font-size:0.74rem;
    color:var(--text-muted);
    font-family:"IBM Plex Mono", monospace;
  }
  input[type="range"]{
    width:100%;
    accent-color:var(--accent-teal);
  }
  .note{
    font-size:0.74rem;
    color:var(--text-muted);
    line-height:1.5;
  }
  #statusLine{
    font-family:"IBM Plex Mono", monospace;
    font-size:0.72rem;
    color:var(--accent-teal);
    min-height:1.1em;
  }
  footer{
    padding:0.7rem 1.5rem;
    border-top:1px solid var(--border);
    font-size:0.7rem;
    color:var(--text-muted);
  }
</style>

<header>
  <h1>Ideallinien-Editor Spreewaldring</h1>
  <div class="sub">Mazda MX-5 ND · Linie per Drag bearbeiten, &mu;=1.0 (konservativ)</div>
</header>

<main>
  <div class="stage">
    <div class="canvas-wrap">
      <canvas id="trackCanvas"></canvas>
      <div id="hoverTooltip"></div>
    </div>
    <div class="legend">
      <div class="speedscale">
        <span>langsam</span>
        <span class="bar"></span>
        <span>schnell (km/h)</span>
      </div>
      <div><span class="swatch" style="background:rgba(224,72,60,0.55);"></span>Bremszone</div>
      <div><span class="swatch" style="background:rgba(69,214,196,0.92); transform:rotate(45deg);"></span>bearbeiteter Punkt</div>
      <div><span class="swatch" style="background:rgba(197,150,255,0.95);"></span>Schaltpunkt</div>
    </div>
    <div class="trace-wrap">
      <canvas id="traceCanvas"></canvas>
    </div>
  </div>

  <div class="rail">
    <div class="panel">
      <h2>Ergebnis</h2>
      <div class="readout-grid">
        <div class="readout">
          <span class="label">Rundenzeit</span>
          <span class="value accent" id="roLapTime">&ndash;</span>
        </div>
        <div class="readout">
          <span class="label">Top-Speed</span>
          <span class="value small" id="roTopSpeed">&ndash;</span>
        </div>
        <div class="readout">
          <span class="label">&Delta; zur Ausgangslinie</span>
          <span class="value small" id="roDelta">&ndash;</span>
        </div>
        <div class="readout">
          <span class="label">Streckenlaenge</span>
          <span class="value small" id="roLength">&ndash;</span>
        </div>
      </div>
      <div id="baselineBadge" class="badge" style="display:none;">Ausgangslinie (unbearbeitet)</div>
    </div>

    <div class="panel">
      <h2>Fahrsimulation</h2>
      <div class="row">
        <button class="action" id="simPrevBtn" title="einen Punkt zurueck">&#8592; Punkt</button>
        <button class="action" id="simPlayBtn">&#9654; Start</button>
        <button class="action" id="simNextBtn" title="einen Punkt vor">Punkt &#8594;</button>
      </div>
      <div class="pedals-row">
        <div class="pedal-bar brake"><div class="pedal-fill" id="brakeFill"></div></div>
        <div class="pedal-bar throttle"><div class="pedal-fill" id="throttleFill"></div></div>
        <div class="wheel"><div class="wheel-rotor" id="wheelRotor"><div class="wheel-dot"></div></div></div>
      </div>
      <div class="slider-row" style="margin-top:0.7rem;">
        <div class="slabel"><span>Geschwindigkeit</span><span id="simSpeedLabel">1.0&times; (Echtzeit)</span></div>
        <input type="range" id="simSpeed" min="0.25" max="8" step="0.25" value="1">
      </div>
      <div class="readout-grid" style="margin-top:0.7rem;">
        <div class="readout">
          <span class="label">Zeit</span>
          <span class="value small" id="simTime">0.0 s</span>
        </div>
        <div class="readout">
          <span class="label">Tempo</span>
          <span class="value small" id="simSpeedKmh">-</span>
        </div>
        <div class="readout">
          <span class="label">Gang</span>
          <span class="value small" id="simGear">-</span>
        </div>
        <div class="readout">
          <span class="label">Drehzahl</span>
          <span class="value small" id="simRpm">-</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <h2>Schaltvorgaenge</h2>
      <div id="shiftsList"></div>
    </div>

    <div class="panel">
      <h2>Ansicht</h2>
      <div class="row">
        <button class="action" id="zoomInBtn" title="Reinzoomen">+</button>
        <button class="action" id="zoomOutBtn" title="Rauszoomen">&minus;</button>
        <button class="action" id="zoomResetBtn">Ansicht zuruecksetzen</button>
      </div>
      <p class="note" style="margin-top:0.5rem;">
        Mausrad zum Zoomen, Doppelklick zoomt an dieser Stelle rein, im leeren
        Bereich (ausserhalb der Linie) ziehen zum Verschieben.
      </p>
    </div>

    <div class="panel">
      <h2>Physik (experimentell)</h2>
      <label class="hard-toggle" style="display:flex; align-items:center; gap:0.5rem;" title="Ersetzt Vollgas-sobald-moeglich durch eine lenkraten-basierte Gasvorgabe, kalibriert an EINER echten Kurve (R^2=0.76). WICHTIG: nicht verlaesslich fuer Streckenkurven validiert - Plausibilitaetspruefung und eine breitere Log-Aggregation zeigten schlechte Generalisierung (siehe PROJEKT_STAND.md). Nur zum Anschauen/Vergleichen gedacht.">
        <input type="checkbox" id="naturalThrottleCb">
        <span>Natuerliches Gasmodell (unkalibriert fuer Streckenkurven!)</span>
      </label>
    </div>

    <div class="panel">
      <h2>Bearbeiten</h2>
      <div class="slider-row">
        <div class="slabel"><span>Pinsel-Radius</span><span id="brushLabel">60 m</span></div>
        <input type="range" id="brushRadius" min="15" max="150" step="5" value="60">
      </div>
      <div class="row" style="margin-top:0.9rem;">
        <button class="action" id="undoBtn" disabled>&#8630; Rueckgaengig</button>
        <button class="action" id="resetBtn">Zuruecksetzen</button>
      </div>
      <div id="statusLine" style="margin-top:0.6rem;"></div>
    </div>

    <div class="panel">
      <h2>Bearbeitete Punkte</h2>
      <div id="editsList"></div>
      <div class="row" style="margin-top:0.7rem;">
        <button class="action" id="exportBtn">Bearbeitungen kopieren (fuer Support)</button>
        <button class="action" id="importBtn">Bearbeitungen importieren</button>
      </div>
      <textarea id="exportBox" readonly style="display:none; width:100%; margin-top:0.5rem; height:5rem; font-family:monospace; font-size:0.75rem;"></textarea>
      <textarea id="importBox" style="display:none; width:100%; margin-top:0.5rem; height:5rem; font-family:monospace; font-size:0.75rem;" placeholder="JSON-Liste hier einfuegen, z.B. von einer anderen Sitzung oder von Claude, dann auf 'Uebernehmen' klicken"></textarea>
      <div class="row" id="importActions" style="display:none; margin-top:0.4rem;">
        <button class="action" id="importApplyBtn">Uebernehmen</button>
        <button class="action" id="importCancelBtn">Abbrechen</button>
      </div>
    </div>

    <p class="note">
      Klicke auf die Linie und ziehe sie an eine neue Position (Pinsel mit
      Falloff bewegt die Nachbarschaft mit). Beim Loslassen wird genau dieser
      Punkt zum festen Fixpunkt (bleibt an dieser Stelle, wie ein
      Scheitelpunkt), die Strecke drumherum relaxiert automatisch
      (Geschwindigkeit&nbsp;&harr;&nbsp;Linie, kombinierter Reifenkraftkreis,
      Traktionsgrenze aus dem Antriebsstrangmodell) und bricht ab, sobald eine
      Iteration die Rundenzeit verschlechtert - nur die beste wird behalten.
      Alles laeuft direkt im Browser (kein Server), Aenderungen bleiben lokal
      in diesem Browser gespeichert.
    </p>
  </div>
</main>

<footer>Spreewaldring Training Center · Fahrbahn-Flaechenmodell aus Orthofoto (LGB Brandenburg, 20cm) · MX-5-Breite 1.74m angenommen · Traktionsmodell aus performance_simulation.py (F_max=4450N)</footer>

<script type="application/json" id="viz-data">__DATA_JSON__</script>
<script>
(function(){
  const data = JSON.parse(document.getElementById('viz-data').textContent);
  const P = data.physics;
  const OPT = data.optimizer;
  const N = data.centerline.length;
  const CENTER = data.centerline;
  const PERP = data.perp;
  const LO = data.corridor_lo_m;
  const HI = data.corridor_hi_m;
  const STORAGE_KEY = 'spreewaldring_racing_line_editor_edits_v2';
  // Lenkgeschwindigkeit: Ackermann-Naeherung (siehe drawCar/computeSteerRateDegS)
  // fuer Anzeige UND jetzt auch als echte physikalische Randbedingung beim
  // Optimieren (siehe pinnedOptimize) - Nutzer-Vorgabe 30.08.2026: 240°/s am
  // Lenkrad NIE ueberschreiten (harte Grenze), reale kleine Korrekturen
  // liegen eher um 70°/s oder langsamer (Sanity-Check, keine eigene Grenze).
  const WHEELBASE_M = 2.31, STEERING_RATIO = 15;
  const STEER_RATE_MAX_DEG_S = 240;
  const NEARBY_HINT_M = 80.0;   // nur fuer den Hover-Tooltip: "diese Fixpunkte beeinflussen sich spuerbar"
  // Sicherheitsabstand zur Korridorgrenze bei der Relaxation (siehe Diskussion
  // 2026-09-07): die querkraftbasierte Optimierung darf die Innenlinie nicht
  // bis auf 0cm an den Rand pressen - mehrere Punkte hart am Anschlag ergeben
  // eine unnatuerliche flache "Beule" statt einer runden Kurve. Ein paar cm
  // Puffer erlauben eine saubere, durchgehend gekruemmte Linie.
  const EDGE_MARGIN_M = 0.10;
  // Staerke, mit der ein WEICHER Fixpunkt pro AEUSSERER Iteration Richtung
  // Zielwert gezogen wird (siehe pinnedOptimize) - bewusst moderat: die
  // bestehende "brich ab, wenn Rundenzeit schlechter wird"-Regel entscheidet
  // dann selbst, wie weit der Zug in Richtung Zielwert tatsaechlich Zeit
  // kostet und wann er sich nicht mehr lohnt (siehe Diskussion 2026-09-07).
  const SOFT_PIN_PULL = 0.3;
  // Kurze Vollgas-Inseln zwischen zwei Bremszonen sind fuer ein Punktmassen-
  // Modell ohne Umschaltkosten zeit-optimal (bang-bang), aber in der
  // Realitaet destabilisierend - siehe smoothWastedAccelBrake() und
  // PROJEKT_STAND.md (Nutzerbeobachtung 08.09.2026). Port von
  // MIN_ACCEL_HOLD_M/smooth_wasted_accel_brake() aus
  // spreewaldring_racing_line_optimal.py.
  const MIN_ACCEL_HOLD_M = 40.0;

  // EXPERIMENTELL (Nutzeranfrage 08.09.2026): "natuerliches" Gasmodell statt
  // Vollgas-sobald-Physik-es-zulaesst. Herleitung: eine reale, saubere
  // Kurve (Strasse, R=44.5m/75km/h, siehe partial_throttle_calibration.py)
  // zeigte einen starken Zusammenhang zwischen LENKRATE (Oeffnen der
  // Lenkung) und Gaspedalstellung, R^2=0.76. WICHTIG - diese Formel ist
  // NICHT verlaesslich validiert fuer Streckenkurven: eine Plausibilitaets-
  // pruefung (spreewaldring_natural_throttle_check.py) zeigte, dass 13 von
  // 17 Spreewaldring-Kurvenabschnitten Lenkraten weit ausserhalb des
  // kalibrierten Bereichs brauchen (bis 392°/s vs. kalibrierte -14 bis
  // +24°/s), UND eine breitere Aggregation ueber alle 17 verfuegbaren Logs
  // (throttle_steering_rate_aggregate.py) fand fuer echte harte Kurvenfahrt
  // (|a_lat|>0.5g, n=98) nur noch R^2=0.04 - die Ein-Kurven-Formel
  // generalisiert NICHT zuverlaessig. Dieser Modus ist bewusst als
  // Vergleichs-/Anschauungswerkzeug gebaut (Toggle "Natuerliches Gasmodell"),
  // NICHT als Ersatz fuer das produktive, zeitoptimale Modell - siehe
  // PROJEKT_STAND.md.
  const K1_STEER = 0.023382, K2_STEER = -0.00003895;  // results/steering_lateral_model_summary.json
  const APP_STEER_A = 38.4, APP_STEER_B = 2.4;         // Kurve-1-Regression, partial_throttle_calibration.py
  // Domain-Gatter (Nutzerbeobachtung 08.09.2026, Punkte 1-35): die Formel
  // wurde AUSSCHLIESSLICH innerhalb einer echten Kurve kalibriert (Kurve 1s
  // eigene Punkte lagen bei 0.68-1.05g genutzter Querkraft) und kennt daher
  // "praktisch keine Kurve" nicht. Bei grossem Radius/kleiner Querkraft ist
  // ausserdem die Kruemmungsschaetzung selbst rauschanfaellig (kleinste
  // Punktabweichungen kippen den geschaetzten Radius stark), was ohne
  // Gatter zu einem voellig unplausiblen Fladdern des Gaspedals zwischen
  // 0% und 100% von Punkt zu Punkt fuehrte. Unterhalb dieser Schwelle
  // (angelehnt an den harten-Kurvenfahrt-Filter aus
  // throttle_steering_rate_aggregate.py) wird komplett auf das bestehende
  // Vollgas-Modell (accelEnvelope) zurueckgefallen statt die Formel
  // ausserhalb ihres kalibrierten Bereichs zu extrapolieren.
  // Schwelle auf 0.8 angehoben (Nutzervorschlag 08.09.2026, "Optimierung
  // der Beschleunigung in Teillast"): unterhalb bleibt es bei Vollgas,
  // AUSSER ein Blick auf die naechsten NATURAL_THROTTLE_LOOKAHEAD_N Punkte
  // zeigt, dass die aktuelle Geschwindigkeit dort schon ueber
  // NATURAL_THROTTLE_LOOKAHEAD_MAX_G fuehren wuerde - dann greift die
  // Lenkrate-Formel schon vorausschauend frueher, statt erst im letzten
  // Moment abzubrechen. Reine Vorwaerts-Heuristik mit der AKTUELLEN
  // Geschwindigkeit (keine echte Vorab-Simulation der naechsten Punkte) -
  // bewusst einfach gehalten, siehe PROJEKT_STAND.md.
  const NATURAL_THROTTLE_MIN_G = 0.9;
  const NATURAL_THROTTLE_LOOKAHEAD_N = 10;
  const NATURAL_THROTTLE_LOOKAHEAD_MAX_G = 1.0;
  let naturalThrottleMode = false;
  let debugForceAppFrac = null; // Array von {index, value} - siehe __editorDebug.setDebugForceAppFrac
  let lastPreSmoothV = null; // Debug: v VOR smoothWastedAccelBrake/smoothShortBrakeSpikes, siehe getPreSmoothV()
  let lastMidSmoothV = null; // Debug: v NACH smoothWastedAccelBrake, VOR smoothShortBrakeSpikes

  // Motorbremskraft bei APP=0/Kupplung geschlossen/Gang eingelegt
  // (Nutzerhinweis 08.09.2026): coastAccel() (unten) modelliert nur Luft-/
  // Rollwiderstand - das ist fuer die Rollphase WAEHREND eines Schaltvorgangs
  // richtig (Kraftschluss kurz unterbrochen), fehlt aber fuer STEADY-STATE
  // Rollen mit geschlossener Kupplung. Empirische v-Interpolationstabelle
  // aus 397 realen "Schub im Gang"-Ereignissen (39 Logs), siehe
  // engine_braking_analysis.py/results/engine_braking_model.json - ein
  // RPM-/Gang-basiertes Modell wurde dort explizit versucht und verworfen
  // (R^2~0), die geschwindigkeitsbasierte Kurve war dagegen sauber.
  const ENGINE_BRAKE_V_MS = [6.94, 9.72, 12.5, 15.28, 18.06, 20.83, 25.0, 30.56, 37.5, 48.61];
  const ENGINE_BRAKE_A_MS2 = [-0.421, -0.315, -0.314, -0.201, -0.205, -0.218, -0.143, -0.145, -0.040, -0.056];
  function coastAccelInGear(v){
    return coastAccel(v) + interp(v, ENGINE_BRAKE_V_MS, ENGINE_BRAKE_A_MS2);
  }

  // Loest (180/(pi*R)) = (k1+k2*|steer|)*|steer| nach |steer| [deg] auf -
  // Umkehrung des Lenkwinkel->Giergeschwindigkeit-Modells, v-unabhaengig
  // (siehe steer_from_radius() in spreewaldring_natural_throttle_check.py).
  function steerFromRadius(radiusM){
    const target = (180/Math.PI) / Math.max(radiusM, 1e-6);
    const disc = Math.max(K1_STEER*K1_STEER + 4*K2_STEER*target, 0);
    return Math.abs((-K1_STEER + Math.sqrt(disc)) / (2*K2_STEER));
  }

  // ---------- Physik (Port aus performance_simulation.py / spreewaldring_racing_line_optimal.py) ----------
  function interp(x, xs, ys){
    if (x <= xs[0]) return ys[0];
    if (x >= xs[xs.length-1]) return ys[ys.length-1];
    for (let i=0;i<xs.length-1;i++){
      if (x>=xs[i] && x<=xs[i+1]){
        const t=(x-xs[i])/(xs[i+1]-xs[i]);
        return ys[i] + t*(ys[i+1]-ys[i]);
      }
    }
    return ys[ys.length-1];
  }
  function rpmFromSpeed(v, gear){
    const wheelRps = v / (2*Math.PI*P.R_DYN_M);
    return wheelRps * P.GEAR_RATIOS[gear] * P.FINAL_DRIVE * 60.0;
  }
  function accel(v, gear){
    const rpm = rpmFromSpeed(v, gear);
    const torque = interp(rpm, P.RPM_TABLE, P.TORQUE_TABLE_NM);
    let fWheel = torque * P.GEAR_RATIOS[gear] * P.FINAL_DRIVE * P.ETA / P.R_DYN_M;
    fWheel = Math.min(fWheel, P.TRACTION_MAX_FORCE_N);
    const fDrag = 0.5 * P.RHO_KG_M3 * P.CDA_M2 * v * v;
    const fRoll = P.CRR * P.MASS_KG * P.G;
    return (fWheel - fDrag - fRoll) / P.MASS_KG;
  }
  function sqrtNonneg(x){ return x>0 ? Math.sqrt(x) : 0; }

  // ---------- Schaltzeiten (Port aus spreewaldring_racing_line_optimal.py,
  // Nachtrag "echte Schaltzeiten statt perfekter Gangwahl") ----------
  function coastAccel(v){
    const fDrag = 0.5*P.RHO_KG_M3*P.CDA_M2*v*v;
    const fRoll = P.CRR*P.MASS_KG*P.G;
    return -(fDrag+fRoll)/P.MASS_KG;
  }
  function bestGearForSpeed(v){
    // Gaenge, die bei dieser Geschwindigkeit schon ueber Redline liegen,
    // werden ausgeschlossen (siehe Python-Docstring: torque_nm() extrapoliert
    // sonst flach weiter und taeuscht bei stark ueberhoehter Drehzahl eine
    // physikalisch unmoegliche hohe Beschleunigung vor).
    let best = null, bestA = -Infinity;
    for (let gear=1; gear<=6; gear++){
      if (rpmFromSpeed(v, gear) > P.REDLINE_RPM) continue;
      const a = accel(v, gear);
      if (a > bestA){ bestA = a; best = gear; }
    }
    return best != null ? best : 6;
  }
  function forwardStepShifted(v, gear, shiftRemaining, shiftTarget, ds, radius, mu){
    if (shiftRemaining > 0){
      const aCoast = coastAccel(v);
      const vFull = sqrtNonneg(v*v + 2*aCoast*ds);
      const tFull = (v+vFull) > 1e-9 ? 2*ds/(v+vFull) : 0;
      if (tFull <= shiftRemaining){
        return {v: vFull, gear, shiftRemaining: shiftRemaining - tFull, shiftTarget};
      }
      const t = shiftRemaining;
      const vEnd = Math.max(v + aCoast*t, 0);
      const dsUsed = v*t + 0.5*aCoast*t*t;
      return forwardStepShifted(vEnd, shiftTarget, 0, shiftTarget, Math.max(ds-dsUsed, 0), radius, mu);
    }
    const aLat = Math.min(v*v/Math.max(radius,1e-6), mu*P.G);
    const aLongAvail = sqrtNonneg((mu*P.G)**2 - aLat**2);
    const rpm = rpmFromSpeed(v, gear);
    if (rpm >= P.REDLINE_RPM && gear < 6){
      // Harter Hochschalt-Zwang bei Redline, keine Hysterese (physikalische
      // Grenze, siehe deriveGearTrace()).
      const dur = P.ATTACK_SHIFT_S[`${gear}-${gear+1}`];
      return forwardStepShifted(v, gear, dur, gear+1, ds, radius, mu);
    }
    // Bidirektionale Gangwahl (Hoch- ODER Runter), dieselbe Hysterese wie
    // deriveGearTrace() (die ANZEIGE) - fehlte hier bisher komplett, siehe
    // Nutzerfund PROJEKT_STAND.md (Anzeige zeigte plausible Rueckschaltungen,
    // die reale, hier berechnete Beschleunigung blieb aber im zu hohen Gang
    // haengen). WICHTIG: der Vergleich nutzt die ROHE Motorbeschleunigung
    // (nicht durch aLongAvail gedeckelt!) - sonst sehen mitten in einer Kurve
    // (aLongAvail klein) ALLE Gaenge gleich schlecht aus und es wuerde nie
    // zurueckgeschaltet, ausgerechnet dort wo es fuer den Kurvenausgang am
    // meisten zaehlt (erster Versuch hatte genau diesen Bug: gedeckelter
    // Vergleich, 0 Rueckschaltungen in 830 Punkten trotz Fix). Gangwahl ist
    // eine Drehzahl-/Motorfrage, kein Grip-Kompromiss - die Grip-Kappung
    // greift erst danach bei der tatsaechlich genutzten Beschleunigung.
    // Nur Nachbargaenge (nicht alle 6) - ein Schaltvorgang ueberspringt keinen
    // Gang, ATTACK_SHIFT_S kennt nur Nachbarpaar-Zeiten.
    let bestA = accel(v, gear), bestGear = gear, bestDur = 0;
    if (gear < 6){
      const aUp = accel(v, gear+1);
      if (aUp > bestA*(1+GEAR_HYSTERESIS)){ bestA = aUp; bestGear = gear+1; bestDur = P.ATTACK_SHIFT_S[`${gear}-${gear+1}`]; }
    }
    if (gear > 1 && rpmFromSpeed(v, gear-1) <= P.REDLINE_RPM*REDLINE_DOWNSHIFT_MARGIN){
      const aDown = accel(v, gear-1);
      if (aDown > bestA*(1+GEAR_HYSTERESIS)){ bestA = aDown; bestGear = gear-1; bestDur = P.ATTACK_SHIFT_S[`${gear-1}-${gear}`]; }
    }
    if (bestGear !== gear){
      return forwardStepShifted(v, gear, bestDur, bestGear, ds, radius, mu);
    }
    const aUse = Math.min(accel(v, gear), aLongAvail);
    const vNew = sqrtNonneg(v*v + 2*aUse*ds);
    return {v: vNew, gear, shiftRemaining: 0, shiftTarget: gear};
  }

  function curvatureRadius(points, windowM, stepM){
    const n = points.length;
    const w = Math.max(1, Math.round(windowM/stepM));
    const radii = new Float64Array(n);
    for (let i=0;i<n;i++){
      const p0 = points[(i-w+n)%n], p1=points[i], p2=points[(i+w)%n];
      const a = Math.hypot(p1[0]-p0[0], p1[1]-p0[1]);
      const b = Math.hypot(p2[0]-p1[0], p2[1]-p1[1]);
      const c = Math.hypot(p2[0]-p0[0], p2[1]-p0[1]);
      const area2 = Math.abs((p1[0]-p0[0])*(p2[1]-p0[1]) - (p2[0]-p0[0])*(p1[1]-p0[1]));
      radii[i] = (area2 < 1e-6 || a*b*c < 1e-6) ? Infinity : (a*b*c)/(2*area2);
    }
    return radii;
  }

  function median(arr){
    const s = arr.slice().sort((a,b)=>a-b);
    const mid = Math.floor(s.length/2);
    return s.length%2 ? s[mid] : (s[mid-1]+s[mid])/2;
  }

  // Zweipass kombinierter Reifenkraftkreis (Port von simulate_lap_combined_friction()).
  // Liefert zusaetzlich vFwd, um Bremszonen physikalisch (nicht per Schwellwert) zu erkennen:
  // ein Punkt ist Bremszone, wenn der Rueckwaerts-Pass die Geschwindigkeit ggue. dem
  // Vorwaerts-Pass reduziert hat (siehe Docstring des Python-Exportskripts).
  function accelEnvelope(v){
    return accel(v, bestGearForSpeed(v));
  }

  // Port von smooth_wasted_accel_brake() (spreewaldring_racing_line_optimal.py) -
  // ersetzt kurze, von Bremszonen eingerahmte Vollgas-Inseln (Laenge <
  // MIN_ACCEL_HOLD_M) durch eine konstante Teillast-Beschleunigung, die
  // exakt auf die vom Rueckwaertspass bereits geforderte
  // Einfahrtsgeschwindigkeit der naechsten Bremszone einschwenkt. Die
  // erforderliche Beschleunigung darf auch leicht negativ sein (bis
  // coastAccel(), volle Schubabschaltung) - auch ein sanftes Verzoegern
  // bleibt so am Gaspedal statt an der Bremse. throttleFrac ist die
  // tatsaechliche Pedalstellung als Anteil der RADKRAFT (nicht der
  // Netto-Beschleunigung!): 0% Gas bedeutet coastAccel (Rollen/Schleppmoment),
  // nicht "0 Nettobeschleunigung" - sonst wuerde ein exaktes Halten der
  // Geschwindigkeit faelschlich als 0% Gas angezeigt (Nutzerbeobachtung
  // 08.09.2026), obwohl real Kraft am Rad noetig ist, um Luft-/Rollwiderstand
  // auszugleichen.
  function smoothWastedAccelBrake(vBwd, vFwd, vCornerO, dsO, mu){
    const n = vBwd.length;
    const braking = new Uint8Array(n);
    let anyBraking = false, allBraking = true;
    for (let i=0;i<n;i++){
      braking[i] = vBwd[i] < vFwd[i]-1e-3 ? 1 : 0;
      if (braking[i]) anyBraking = true; else allBraking = false;
    }
    const vOut = vBwd.slice();
    const coasting = new Uint8Array(n);       // Punkte, die diese Funktion veraendert hat
    const throttleFrac = new Float64Array(n).fill(1);  // 1 = Vollgas/unveraendert
    if (!anyBraking || allBraking) return {v: vOut, coasting, throttleFrac};
    let start0 = 0;
    for (let i=0;i<n;i++) if (braking[i]){ start0 = i; break; }
    const order2 = new Array(n);
    for (let k=0;k<n;k++) order2[k] = (start0+k)%n;
    let k = 0;
    while (k < n){
      const idx = order2[k];
      if (braking[idx]){ k++; continue; }
      let j = k, arcLen = 0;
      while (j < n && !braking[order2[j]]){ arcLen += dsO[order2[j]]; j++; }
      const prevIdx = k > 0 ? order2[k-1] : order2[n-1];
      const nextIdx = j < n ? order2[j] : order2[0];
      if (arcLen < MIN_ACCEL_HOLD_M){
        const vStart = vBwd[prevIdx], vEnd = vBwd[nextIdx];
        const aReq = (vEnd*vEnd - vStart*vStart) / Math.max(2*arcLen, 1e-6);
        const aMax = accelEnvelope(vStart);
        const aMin = coastAccel(vStart);
        if (aReq >= aMin && aReq <= aMax){
          const frac = aMax > aMin ? (aReq-aMin)/(aMax-aMin) : 0;
          let sLocal = 0;
          for (let m=k; m<j; m++){
            sLocal += dsO[order2[m]];
            const vSmooth = sqrtNonneg(vStart*vStart + 2*aReq*sLocal);
            const i2 = order2[m];
            vOut[i2] = Math.min(vSmooth, vCornerO[i2]);
            coasting[i2] = 1;
            throttleFrac[i2] = Math.min(1, Math.max(0, frac));
          }
        }
      }
      k = j;
    }
    return {v: vOut, coasting, throttleFrac};
  }

  // Spiegelfall zu smoothWastedAccelBrake(): eine kurze, isolierte Bremsinsel
  // (oft nur ein einzelner Punkt), die von Nicht-Bremsen eingerahmt ist, wird
  // durch frueheres, sanfteres Anbremsen ersetzt statt einem abrupten
  // Bremsstoss - Bremsbeginn rueckwaerts vorverlegt bis min. MIN_ACCEL_HOLD_M,
  // konstante Verzoegerung, Endgeschwindigkeit der Insel bleibt exakt erhalten
  // (Nutzerbeobachtung 08.09.2026, Punkt 492).
  function smoothShortBrakeSpikes(vBwd, vFwd, vCornerO, dsO, mu, brakeCapG){
    const n = vBwd.length;
    const braking = new Uint8Array(n);
    let anyBraking = false, allBraking = true;
    for (let i=0;i<n;i++){
      braking[i] = vBwd[i] < vFwd[i]-1e-3 ? 1 : 0;
      if (braking[i]) anyBraking = true; else allBraking = false;
    }
    const vOut = vBwd.slice();
    const brakeFrac = new Float64Array(n).fill(1);  // 1 = unveraendert
    if (!anyBraking || allBraking) return {v: vOut, brakeFrac};
    let start0 = 0;
    for (let i=0;i<n;i++) if (!braking[i]){ start0 = i; break; }
    const order2 = new Array(n);
    for (let k=0;k<n;k++) order2[k] = (start0+k)%n;
    let k = 0;
    while (k < n){
      const idx = order2[k];
      if (!braking[idx]){ k++; continue; }
      let j = k, arcLen = 0;
      while (j < n && braking[order2[j]]){ arcLen += dsO[order2[j]]; j++; }
      const nextIdx = j < n ? order2[j] : order2[0];
      if (arcLen < MIN_ACCEL_HOLD_M){
        const vEnd = vBwd[nextIdx];
        // Rueckwaerts nur soweit ausdehnen, wie die Geschwindigkeit dort
        // NICHT niedriger ist als am bisher besten Ankerpunkt - sonst
        // laeuft die Verlaengerung in eine vorangehende BESCHLEUNIGUNGS-
        // rampe zurueck (Geschwindigkeit faellt beim Rueckwaertsgehen) und
        // verletzt vStart>vEnd unten, was die Glaettung komplett abbricht,
        // obwohl schon die Bremsinsel selbst (ohne jede Verlaengerung)
        // laengst machbar waere (Bugfund 08.09.2026, Nutzerbeobachtung
        // "einzelner 100%-Bremspunkt" nach einer Kurvenausfahrt-Rampe).
        let farK = k-1, extLen = arcLen, bestFarK = k-1;
        while (extLen < MIN_ACCEL_HOLD_M && farK >= 0 && !braking[order2[farK]]){
          if (vBwd[order2[farK]] < vBwd[order2[bestFarK]]) break;
          extLen += dsO[order2[farK]];
          bestFarK = farK;
          farK--;
        }
        const farIdx = order2[bestFarK];
        const vStart = vBwd[farIdx];
        if (vStart > vEnd && extLen > 1e-6){
          const aReq = (vStart*vStart - vEnd*vEnd) / (2*extLen);
          if (aReq <= brakeCapG*P.G){
            let sLocal = 0, m = farK+1;
            while (order2[m] !== nextIdx){
              sLocal += dsO[order2[m]];
              const vSmooth = sqrtNonneg(vStart*vStart - 2*aReq*sLocal);
              const i2 = order2[m];
              vOut[i2] = Math.min(vSmooth, vCornerO[i2]);
              brakeFrac[i2] = aReq > 1e-6 ? Math.min(1, aReq/(brakeCapG*P.G)) : 0;
              m++;
            }
          }
        }
      }
      k = j;
    }
    return {v: vOut, brakeFrac};
  }

  function simulateLap(points, mu){
    const n = points.length;
    const ds = new Float64Array(n);
    for (let i=0;i<n;i++){
      const j=(i+1)%n;
      ds[i] = Math.hypot(points[j][0]-points[i][0], points[j][1]-points[i][1]);
    }
    const stepM = median(Array.from(ds));
    const radius = curvatureRadius(points, P.CURVATURE_WINDOW_M, stepM);
    const vCorner = new Float64Array(n);
    for (let i=0;i<n;i++) vCorner[i] = Math.sqrt(Math.max(mu*P.G*radius[i], P.V_MIN_MS**2));

    let i0=0, vmin=Infinity;
    for (let i=0;i<n;i++) if (vCorner[i]<vmin){ vmin=vCorner[i]; i0=i; }
    const order = new Array(n);
    for (let k=0;k<n;k++) order[k]=(i0+k)%n;
    const vCornerO = order.map(i=>vCorner[i]);
    const dsO = order.map(i=>ds[i]);
    const radiusO = order.map(i=>radius[i]);

    // EXPERIMENTELL: fuer das "natuerliche Gasmodell" (siehe naturalThrottleMode
    // oben) - Lenkwinkel-Aequivalent pro Punkt ist rein geometrisch (v-unabhaengig,
    // siehe steerFromRadius()), daher einmal vorab berechnet statt im Vorwaerts-
    // pass neu.
    const steerAbsO = radiusO.map(r => steerFromRadius(r));
    // Oeffnungsrate ueber ein BREITES Fenster (wie computeSteerRateDegS/
    // CURVATURE_WINDOW_M, nicht ueber die unmittelbaren Nachbarpunkte) -
    // Nutzerbeobachtung 08.09.2026 (Punkte 98-101): eine Differenzierung
    // ueber nur 1-2 Punkte reagiert auf jedes kleine S in der eigenen
    // Kruemmungsaenderung der Linie, obwohl Lenkwinkel und a_lat im
    // uebergeordneten Trend laengst in eine Richtung laufen - ein realer
    // Fahrer reagiert vorausschauend auf den Trend der naechsten ~15-20m,
    // nicht auf jede Punkt-zu-Punkt-Fluktuation. Sicherheitsnetz bleibt
    // unveraendert: aLongAvail (im Reifenkraftkreis unten) verhindert
    // weiterhin, dass die gedaempfte Gasfreigabe eine Kombination aus
    // Quer-/Laengskraft ueber mu*g hinaus verlangt, das breitere Fenster
    // aendert daran nichts.
    const stepMNatural = median(Array.from(dsO));
    const wNatural = Math.max(1, Math.round(P.CURVATURE_WINDOW_M/stepMNatural));
    const dSteerDsO = new Float64Array(n);
    for (let i=0;i<n;i++){
      const ip = (i-wNatural+n)%n, in_ = (i+wNatural)%n;
      let span = 0;
      for (let k=0;k<2*wNatural;k++) span += dsO[(ip+k)%n];
      dSteerDsO[i] = (steerAbsO[in_]-steerAbsO[ip]) / Math.max(span, 1e-6);
    }
    const appFracNaturalO = new Float64Array(n).fill(1);

    // Spiegelt forwardStepShifted() (Gangwahl + echte Schaltzeit als
    // Rollphase), ersetzt darin nur die Vollgas-Beschleunigung durch die
    // Lenkrate-Teillast - fuer einen fairen Rundenzeit-Vergleich muss auch
    // der natuerliche Gas-Pfad Schaltzeiten kosten (Nutzerhinweis
    // 08.09.2026: die zunaechst schaltzeitfreie Variante war faelschlich
    // SCHNELLER als das zeitoptimale Modell - physikalisch unmoeglich fuer
    // ein konservativeres Gasmodell).
    function naturalStep(v, gear, shiftRemaining, shiftTarget, ds, iTarget){
      if (shiftRemaining > 0){
        const aCoast = coastAccel(v);
        const vFull = sqrtNonneg(v*v + 2*aCoast*ds);
        const tFull = (v+vFull) > 1e-9 ? 2*ds/(v+vFull) : 0;
        if (tFull <= shiftRemaining){
          return {v: vFull, gear, shiftRemaining: shiftRemaining - tFull, shiftTarget};
        }
        const t = shiftRemaining;
        const vEnd = Math.max(v + aCoast*t, 0);
        const dsUsed = v*t + 0.5*aCoast*t*t;
        return naturalStep(vEnd, shiftTarget, 0, shiftTarget, Math.max(ds-dsUsed, 0), iTarget);
      }
      const radius = radiusO[iTarget];
      const aLat = Math.min(v*v/Math.max(radius,1e-6), mu*P.G);
      const aLongAvail = sqrtNonneg((mu*P.G)**2 - aLat**2);
      const rpm = rpmFromSpeed(v, gear);
      if (rpm >= P.REDLINE_RPM && gear < 6){
        // appFracNaturalO[iTarget] explizit setzen (Kupplung offen waehrend
        // des Schaltvorgangs, keine Kraftuebertragung) - sonst behaelt das
        // Array an GENAU diesem Punkt seinen .fill(1)-Anfangswert, weil die
        // spaetere Zuweisung unten hier uebersprungen wird (Bugfund
        // 09.09.2026: erzeugte einen Phantom-100%-Ausreisser mitten in einer
        // sonst korrekt auf 0% stehenden Bremszone, siehe PROJEKT_STAND.md).
        appFracNaturalO[iTarget] = 0;
        const dur = P.ATTACK_SHIFT_S[`${gear}-${gear+1}`];
        return naturalStep(v, gear, dur, gear+1, ds, iTarget);
      }
      // Bidirektionale Gangwahl - siehe forwardStepShifted() fuer die
      // Begruendung (Nutzerfund: Anzeige zeigte Rueckschaltungen, die
      // tatsaechlich berechnete Beschleunigung nutzte weiterhin den zu hohen,
      // nie zurueckgeschalteten Gang). Gangwahl bleibt unabhaengig von der
      // Teillast-Frage - ein Fahrer waehlt den Gang nach Drehzahl/
      // Leistungsband, nicht neu je Pedalstellung. WICHTIG: Vergleich mit der
      // ROHEN Motorbeschleunigung, nicht grip-gedeckelt - siehe
      // forwardStepShifted() fuer den Bugfund (gedeckelter Vergleich sah
      // mitten in der Kurve alle Gaenge gleich schlecht, nie zurueckgeschaltet).
      {
        let bestA = accel(v, gear), bestGear = gear, bestDur = 0;
        if (gear < 6){
          const aUp = accel(v, gear+1);
          if (aUp > bestA*(1+GEAR_HYSTERESIS)){ bestA = aUp; bestGear = gear+1; bestDur = P.ATTACK_SHIFT_S[`${gear}-${gear+1}`]; }
        }
        if (gear > 1 && rpmFromSpeed(v, gear-1) <= P.REDLINE_RPM*REDLINE_DOWNSHIFT_MARGIN){
          const aDown = accel(v, gear-1);
          if (aDown > bestA*(1+GEAR_HYSTERESIS)){ bestA = aDown; bestGear = gear-1; bestDur = P.ATTACK_SHIFT_S[`${gear-1}-${gear}`]; }
        }
        if (bestGear !== gear){
          appFracNaturalO[iTarget] = 0; // siehe Kommentar beim Redline-Zwangsschalt oben
          return naturalStep(v, gear, bestDur, bestGear, ds, iTarget);
        }
      }
      let cornering = (aLat / (mu*P.G)) > NATURAL_THROTTLE_MIN_G;
      if (!cornering){
        // Vorausschauend (Nutzervorschlag 08.09.2026): auch unterhalb der
        // Schwelle schon moderieren, wenn die naechsten Punkte zu eng
        // wuerden. URSPRUENGLICH mit GLEICHBLEIBENDER Geschwindigkeit
        // geprueft - Nutzer-Analyse 09.09.2026 (Punkt 172-179) zeigte: das
        // ist zu pessimistisch. Selbst bei Vollgas an einem Punkt kurz vor
        // einer Verengung rollt der Wagen bis zum Scheitel durch Luft-/
        // Rollwiderstand UND Motorbremse (siehe coastAccelInGear, dieselbe
        // Funktion wie aMin unten) schon spuerbar ab, auch OHNE dass
        // irgendjemand bremst oder vom Gas geht - die "konstante
        // Geschwindigkeit"-Annahme unterstellte faelschlich, dass gar keine
        // Verzoegerung stattfindet, und liess das Gas dadurch frueher als
        // noetig auf 0 fallen. Jetzt wird das Ausrollen (appFrac=0, das
        // pessimistischste/sicherste Szenario - genau das, was passiert,
        // WENN man ab hier vom Gas geht) bis zu jedem Vorausschau-Punkt
        // vorsimuliert, statt v konstant zu halten.
        let vCoast = v;
        for (let k=1; k<=NATURAL_THROTTLE_LOOKAHEAD_N; k++){
          const j = (iTarget+k) % n;
          const jPrev = (iTarget+k-1) % n;
          vCoast = sqrtNonneg(vCoast*vCoast + 2*coastAccelInGear(vCoast)*dsO[jPrev]);
          const aLatAheadG = (vCoast*vCoast/Math.max(radiusO[j],1e-6)) / (mu*P.G);
          if (aLatAheadG > NATURAL_THROTTLE_LOOKAHEAD_MAX_G){ cornering = true; break; }
        }
      }
      let appFrac = 1;
      if (cornering){
        // Ausserhalb des kalibrierten Bereichs faellt appFrac=1 (Vollgas)
        // zurueck - siehe NATURAL_THROTTLE_MIN_G.
        const openingRate = -dSteerDsO[iTarget] * v;  // >0 = Kurve oeffnet sich
        appFrac = Math.min(1, Math.max(0, (APP_STEER_A + APP_STEER_B*openingRate)/100));
        // Sicherheitsnetz gegen die Formel selbst (Nutzererkenntnis 09.09.2026,
        // Kurve 3, ~30 reale Runden: "zwischen 194 und 300 fahre ich alles
        // voll") - die obige Formel ist ein Fahrgefuehl-Proxy aus NUR EINER
        // kalibrierten Kurve (R^2=0.76) und kann hier zu vorsichtig sein,
        // ohne dass die 1g-Haftgrenze das je verlangt haette. Deshalb: derselbe
        // Vorausschau-Mechanismus wie oben (X% Gas HIER, danach realistisches
        // Ausrollen statt konstanter Geschwindigkeit) sucht per Bisektion die
        // MAXIMALE appFrac, bei der die Haftgrenze im Vorausschaufenster
        // nirgends gerissen wird - appFrac gewinnt gegen die Formel, wenn das
        // mehr erlaubt. Erste Version hier war ein harter 0/1-Schalter (nur
        // "Vollgas sicher?" statt "wieviel ist sicher?") - das erzeugte an der
        // Kippschwelle dasselbe Bang-Bang-Chattern wie der urspruengliche
        // Lookahead-Bug (Fund bei Punkt 757-759: 0/100/18/100/46). Die
        // Bisektion liefert stattdessen einen STETIGEN Wert, der sich mit der
        // Position mitbewegt statt zu kippen. Kann appFrac dadurch NUR
        // erhoehen, nie absenken (Math.max via cornering-Zweig oben) - macht
        // also strukturell keine Stelle im Lap langsamer als vorher.
        const aMaxHere = Math.min(accel(v, gear), aLongAvail);
        const aMinHere = coastAccelInGear(v);
        function safeAtFrac(frac){
          const aUseTest = aMinHere + frac*(aMaxHere-aMinHere);
          let vTest = sqrtNonneg(v*v + 2*aUseTest*ds);
          for (let k=1; k<=NATURAL_THROTTLE_LOOKAHEAD_N; k++){
            const j = (iTarget+k) % n;
            const jPrev = (iTarget+k-1) % n;
            vTest = sqrtNonneg(vTest*vTest + 2*coastAccelInGear(vTest)*dsO[jPrev]);
            const aLatAheadG = (vTest*vTest/Math.max(radiusO[j],1e-6)) / (mu*P.G);
            if (aLatAheadG > NATURAL_THROTTLE_LOOKAHEAD_MAX_G) return false;
          }
          return true;
        }
        if (safeAtFrac(1)){
          appFrac = 1;
        } else {
          let lo = 0, hi = 1;
          for (let iter=0; iter<12; iter++){
            const mid = (lo+hi)/2;
            if (safeAtFrac(mid)) lo = mid; else hi = mid;
          }
          appFrac = Math.max(appFrac, lo);
        }
      }
      // Debug-Override fuer die Rundenzeit-Gegenprobe (siehe __editorDebug.
      // setDebugForceAppFrac) - erzwingt appFrac an EINEM natuerlichen
      // Streckenindex, unabhaengig vom cornering-Gate.
      if (debugForceAppFrac){
        const override = debugForceAppFrac.find(o => order[iTarget] === o.index);
        if (override) appFrac = override.value;
      }
      // aMin = APP 0% BEI GESCHLOSSENER KUPPLUNG/eingelegtem Gang (nicht
      // die Schaltpause) - braucht die Motorbremskraft-Korrektur, siehe
      // coastAccelInGear().
      const aMax = Math.min(accel(v, gear), aLongAvail), aMin = coastAccelInGear(v);
      const aUse = aMin + appFrac*(aMax-aMin);
      appFracNaturalO[iTarget] = appFrac;
      const vNew = sqrtNonneg(v*v + 2*aUse*ds);
      return {v: vNew, gear, shiftRemaining: 0, shiftTarget: gear};
    }

    let v = vCornerO.slice();
    let vFwd = v.slice();
    let gearO = new Int32Array(n).fill(1);
    for (let pass=0; pass<2; pass++){
      vFwd = v.slice();
      let gear = bestGearForSpeed(vFwd[0]);
      gearO[0] = gear;
      let shiftRemaining = 0;
      let shiftTarget = gear;
      for (let i=1;i<n;i++){
        const step = naturalThrottleMode
          ? naturalStep(vFwd[i-1], gear, shiftRemaining, shiftTarget, dsO[i-1], i)
          : forwardStepShifted(vFwd[i-1], gear, shiftRemaining, shiftTarget, dsO[i-1], radiusO[i], mu);
        gear = step.gear; shiftRemaining = step.shiftRemaining; shiftTarget = step.shiftTarget;
        vFwd[i] = Math.min(vCornerO[i], step.v);
        gearO[i] = gear;
      }
      {
        const step0 = naturalThrottleMode
          ? naturalStep(vFwd[n-1], gear, shiftRemaining, shiftTarget, dsO[n-1], 0)
          : forwardStepShifted(vFwd[n-1], gear, shiftRemaining, shiftTarget, dsO[n-1], radiusO[0], mu);
        gear = step0.gear; shiftRemaining = step0.shiftRemaining; shiftTarget = step0.shiftTarget;
        vFwd[0] = Math.min(vCornerO[0], step0.v, vFwd[0]);
        gearO[0] = gear;
      }
      // Bremsverzoegerung zusaetzlich auf BRAKE_CAP_G begrenzt (reale,
      // ABS-limitierte Bremsung aus BFP_PRE_MZ-Daten statt der rein
      // theoretischen Reifenkraftkreis-Grenze - Nutzerentscheidung
      // 08.09.2026, siehe braking_model.py/PROJEKT_STAND.md). Gilt nur fuer
      // die Laengsverzoegerung beim Bremsen, nicht fuer die Kurven-
      // Querbeschleunigung.
      const vBwd = vFwd.slice();
      for (let i=n-2;i>=0;i--){
        const aLat = Math.min(vBwd[i+1]**2/Math.max(radiusO[i],1e-6), mu*P.G);
        const aBrake = Math.min(sqrtNonneg((mu*P.G)**2 - aLat**2), P.BRAKE_CAP_G*P.G);
        vBwd[i] = Math.min(vBwd[i], Math.sqrt(vBwd[i+1]**2 + 2*aBrake*dsO[i]));
      }
      {
        const aLatLast = Math.min(vBwd[0]**2/Math.max(radiusO[n-1],1e-6), mu*P.G);
        const aBrakeLast = Math.min(sqrtNonneg((mu*P.G)**2 - aLatLast**2), P.BRAKE_CAP_G*P.G);
        vBwd[n-1] = Math.min(vBwd[n-1], Math.sqrt(vBwd[0]**2+2*aBrakeLast*dsO[n-1]));
      }
      v = vBwd;
    }

    // Einmalig NACH der Konvergenz ueber beide Passes anwenden (nicht
    // innerhalb der Schleife) - sonst wuerde der naechste Vorwaerts-Pass
    // wieder mit Vollgas von den geglaetteten Werten starten und dieselbe
    // Spitze neu erzeugen. Siehe MIN_ACCEL_HOLD_M/smoothWastedAccelBrake().
    // Debug-Snapshot VOR jeder Glaettung, in natuerliche Reihenfolge
    // zurueckgemappt - fuer den Python-Port-Abgleich (09.09.2026), siehe
    // __editorDebug.getPreSmoothV().
    lastPreSmoothV = new Float64Array(n);
    for (let k=0;k<n;k++) lastPreSmoothV[order[k]] = v[k];
    const smoothed = smoothWastedAccelBrake(v, vFwd, vCornerO, dsO, mu);
    v = smoothed.v;
    lastMidSmoothV = new Float64Array(n);
    for (let k=0;k<n;k++) lastMidSmoothV[order[k]] = v[k];
    const spikeFixed = smoothShortBrakeSpikes(v, vFwd, vCornerO, dsO, mu, P.BRAKE_CAP_G);
    v = spikeFixed.v;

    const vFinal = new Float64Array(n), vFwdOut = new Float64Array(n);
    const coasting = new Uint8Array(n), throttleFrac = new Float64Array(n);
    const brakeFrac = new Float64Array(n), throttleFracNatural = new Float64Array(n);
    const gearInternalOut = new Int32Array(n); // Debug: tatsaechlich intern genutzter Gang, siehe __editorDebug
    for (let k=0;k<n;k++){
      vFinal[order[k]]=v[k]; vFwdOut[order[k]]=vFwd[k];
      coasting[order[k]]=smoothed.coasting[k]; throttleFrac[order[k]]=smoothed.throttleFrac[k];
      brakeFrac[order[k]]=spikeFixed.brakeFrac[k];
      throttleFracNatural[order[k]]=appFracNaturalO[k];
      gearInternalOut[order[k]]=gearO[k];
    }
    let lapTime=0;
    for (let i=0;i<n;i++) lapTime += ds[i]/vFinal[i];

    // Gang-Verlauf fuer die ANZEIGE (RPM-/Gang-Diagramm, Schaltpunkt-Marker)
    // wird BEWUSST separat aus dem fertigen vFinal(s) in NATUERLICHER
    // Streckenreihenfolge (Index 0 = Start/Ziel) abgeleitet, NICHT aus gearO
    // oben (das laeuft intern rotiert ab dem langsamsten Punkt, den der
    // Zweipass-Loeser als numerischen Anker braucht). Grund: gearO haette an
    // GENAU dieser Rotationsnaht einen kuenstlichen Ruecksprung (z.B. 4->1),
    // der beim Ablesen in echter Streckenreihenfolge wie ein bizarrer
    // Schaltvorgang aussieht (Bug gefunden 30.08.2026, User-Report "Schalt-
    // vorgaenge nicht plausibel"). Die Rundenzeit selbst (oben) bleibt davon
    // unberuehrt - sie kommt weiterhin aus dem korrekt rotierten Loeser.
    const gear = deriveGearTrace(vFinal, radius, mu);
    const rpm = new Float64Array(n);
    for (let i=0;i<n;i++) rpm[i] = rpmFromSpeed(vFinal[i], gear[i]);
    const sArr = new Float64Array(n);
    { let s=0; for (let i=0;i<n;i++){ sArr[i]=s; s+=ds[i]; } }
    const tArr = new Float64Array(n); // kumulierte Zeit je Punkt, fuer die Fahrsimulation unten
    { let t=0; for (let i=0;i<n;i++){ tArr[i]=t; t+=ds[i]/vFinal[i]; } }

    return {vFinal, vFwd: vFwdOut, gear, rpm, s: sArr, t: tArr, radius, ds, lapTime, coasting, throttleFrac, brakeFrac,
             throttleFracNatural, naturalMode: naturalThrottleMode, gearInternal: gearInternalOut};
  }

  // Anteil, um den ein ANDERER Gang bei gleicher Geschwindigkeit mehr
  // Beschleunigung liefern muss, damit deriveGearTrace() tatsaechlich
  // wechselt (siehe Nutzer-Feedback 30.08.2026: ohne diese Hysterese
  // schaltete die Anzeige an Stellen, wo die Geschwindigkeit knapp um eine
  // Gangwechsel-Schwelle pendelt, mehrfach auf wenigen Metern hin und her -
  // unrealistisch, ein echter Schaltvorgang lohnt sich erst bei einem
  // spuerbaren Vorteil, nicht bei jedem noch so kleinen rechnerischen Gewinn).
  const GEAR_HYSTERESIS = 0.15;
  // Sicherheitsabstand unter der echten Redline, ab dem ein Gang ueberhaupt
  // erst wieder als RUECKSCHALT-Kandidat gilt (siehe Bugreport 30.08.2026:
  // ohne diesen Puffer flatterte der Gang bei Geschwindigkeiten, die genau
  // an der Redline-Schaltschwelle liegen, zwischen zwei Gaengen - Hochschalten
  // bei Redline hat KEINE Hysterese (harte physikalische Grenze), aber das
  // Zurueckschalten via Hysterese sah den niedrigeren Gang sofort wieder als
  // "deutlich besser" an, sobald die Drehzahl durch kleinstes Kurvenrauschen
  // (z.B. aus dicht beieinanderliegenden manuellen Punkt-Edits) einen Tick
  // unter Redline fiel - ein echter Fahrer schaltet in so einer Situation
  // nicht sekuendlich hin und her, sondern bleibt im hoeheren Gang, bis
  // wieder klar Luft nach unten ist).
  const REDLINE_DOWNSHIFT_MARGIN = 0.97;

  function deriveGearTrace(vFinal, radius, mu){
    // Reine ANZEIGE-Ableitung (siehe Kommentar in simulateLap): der Gang wird
    // jetzt STATEFUL fortgeschrieben (nicht mehr pro Punkt unabhaengig neu
    // bestimmt) - er bleibt, bis entweder (a) Redline erzwingt einen
    // sequenziellen Hochschalt (keine Hysterese moeglich, das ist eine harte
    // physikalische Grenze), oder (b) ein ANDERER Gang um mehr als
    // GEAR_HYSTERESIS bessere Beschleunigung liefert. Das ist weiterhin
    // RICHTUNGSLOS (deckt Hoch- wie Runterschalten mit derselben Regel ab,
    // siehe vorheriger Nachtrag), aber ohne das Chattern nahe einer
    // Wechselschwelle.
    const n = vFinal.length;
    const gear = new Int32Array(n);
    let g = bestGearForSpeed(vFinal[0]);
    gear[0] = g;
    for (let i=1;i<n;i++){
      const v = vFinal[i];
      if (rpmFromSpeed(v, g) > P.REDLINE_RPM){
        g = Math.min(6, g+1);
      } else {
        let bestA = accel(v, g), bestG = g;
        for (let cand=1; cand<=6; cand++){
          if (cand === g || rpmFromSpeed(v, cand) > P.REDLINE_RPM*REDLINE_DOWNSHIFT_MARGIN) continue;
          const a = accel(v, cand);
          if (a > bestA*(1+GEAR_HYSTERESIS)){ bestA = a; bestG = cand; }
        }
        g = bestG;
      }
      gear[i] = g;
    }
    return gear;
  }

  function pointsFromNLat(nLat){
    const pts = new Array(N);
    for (let i=0;i<N;i++){
      pts[i] = [CENTER[i][0]+nLat[i]*PERP[i][0], CENTER[i][1]+nLat[i]*PERP[i][1]];
    }
    return pts;
  }

  // Klemmt mit EDGE_MARGIN_M Sicherheitsabstand zur Korridorgrenze statt hart
  // auf [LO,HI] - faellt auf die Streckenmitte zurueck, falls der Korridor an
  // dieser Stelle schmaler als 2x Puffer ist (siehe EDGE_MARGIN_M).
  function clampInset(val, i){
    const mid = (LO[i]+HI[i])/2;
    const loEff = Math.min(LO[i]+EDGE_MARGIN_M, mid);
    const hiEff = Math.max(HI[i]-EDGE_MARGIN_M, mid);
    return Math.min(Math.max(val, loEff), hiEff);
  }

  // hardTargets: Array von {index, value} - werden JEDEN Schritt hart erzwungen
  // (fuer den "muss getroffen werden"-Modus). Weiche Fixpunkte laufen NICHT
  // hier hinein, sondern werden in pinnedOptimize() einmal pro AEUSSERER
  // Iteration Richtung Ziel gezogen (siehe dort) - das gibt der "brich bei
  // schlechterer Rundenzeit ab"-Regel eine Chance, einen zu teuren Zug wieder
  // zu verwerfen.
  function relaxOnce(nLat, alphaArr, hardTargets){
    const pts = pointsFromNLat(nLat);
    const nNew = new Float64Array(N);
    for (let i=0;i<N;i++){
      const prev = pts[(i-1+N)%N], next = pts[(i+1)%N];
      const avgX=(prev[0]+next[0])/2, avgY=(prev[1]+next[1])/2;
      const pNewX = pts[i][0] + alphaArr[i]*(avgX-pts[i][0]);
      const pNewY = pts[i][1] + alphaArr[i]*(avgY-pts[i][1]);
      let nVal = (pNewX-CENTER[i][0])*PERP[i][0] + (pNewY-CENTER[i][1])*PERP[i][1];
      nNew[i] = clampInset(nVal, i);
    }
    if (hardTargets) for (const t of hardTargets) nNew[t.index] = t.value;
    return nNew;
  }

  // Alternierende Optimierung mit einem harten Fixpunkt (Port der Kernidee aus
  // optimize_alternating() in spreewaldring_racing_line_optimal.py), Abbruch
  // sobald eine Iteration schlechter wird (Nutzervorgabe), nur letztes (bestes)
  // Ergebnis wird behalten.
  // Vorzeichenbehaftete Lenkrad-Winkelgeschwindigkeit [deg/s] je Punkt (siehe
  // Ackermann-Naeherung in drawCar) - jetzt auch als echte Optimierungs-
  // Randbedingung genutzt (siehe pinnedOptimize), nicht nur zur Anzeige.
  // Fensterbreite = CURVATURE_WINDOW_M, dieselbe Konvention wie curvatureRadius():
  // eine Ableitung ueber Nachbarpunkte (~6m) ist ein reines Diskretisierungsartefakt,
  // keine reale Lenkbewegung (siehe PROJEKT_STAND.md, geprueft 2026-09-07).
  function computeSteerRateDegS(pts, radius, vFinal, dsArr){
    const n = pts.length;
    const stepM = median(Array.from(dsArr));
    const w = Math.max(1, Math.round(P.CURVATURE_WINDOW_M/stepM));
    const theta = new Float64Array(n);
    for (let i=0;i<n;i++){
      const prevI=(i-w+n)%n, nextI=(i+w)%n;
      const p0=pts[prevI], p1=pts[i], p2=pts[nextI];
      const cross = (p1[0]-p0[0])*(p2[1]-p0[1]) - (p2[0]-p0[0])*(p1[1]-p0[1]);
      const turnSign = cross>0 ? -1 : (cross<0 ? 1 : 0);
      theta[i] = Math.atan(WHEELBASE_M/Math.max(radius[i],1)) * 180/Math.PI * STEERING_RATIO * turnSign;
    }
    const rate = new Float64Array(n);
    for (let i=0;i<n;i++){
      const prevI=(i-w+n)%n, nextI=(i+w)%n;
      let span = 0;
      for (let k=0;k<2*w;k++) span += dsArr[(prevI+k)%n];
      const dThetaDs = (theta[nextI]-theta[prevI]) / Math.max(span, 1e-6);
      rate[i] = dThetaDs * vFinal[i];
    }
    return rate;
  }

  // NACHBEARBEITUNG (Nutzerbeobachtung 08.09.2026, Punkte 236-238: Radius
  // 107.8 -> 110.8 -> 106.5 - eine einzelne Kuppe mitten in einer sonst
  // durchgehenden Verengung): findet EINZELPUNKT-Ausreisser im Radius-
  // Verlauf und ersetzt NUR die Punktposition (nLat) dieses einen Punktes
  // durch den Mittelwert seiner Nachbarn - KEIN erneutes Relaxieren, keine
  // Aenderung an pinnedOptimize()/relaxOnce() selbst (Nutzervorgabe: "an
  // der Art und Weise wie du die Linie berechnest nichts aendern").
  //
  // Erkennung: ein Punkt i gilt als Ausreisser, wenn die Trendrichtung des
  // Radius (waechst/faellt) fuer GENAU einen Schritt umgekehrt wird und
  // direkt danach wieder in die urspruengliche Richtung zurueckkehrt -
  // s_before==s_out UND s_into==-s_before. Das unterscheidet einen
  // einzelnen Wackler von einem echten, glatten Kurvenscheitel (dort kehrt
  // sich die Richtung nur EINMAL dauerhaft um, s_into wuerde dort mit
  // s_before uebereinstimmen statt es umzukehren).
  function findRadiusNotches(nLat){
    const pts = pointsFromNLat(nLat);
    const ds = new Float64Array(N);
    for (let i=0;i<N;i++){
      const j=(i+1)%N;
      ds[i] = Math.hypot(pts[j][0]-pts[i][0], pts[j][1]-pts[i][1]);
    }
    const stepM = median(Array.from(ds));
    const radius = curvatureRadius(pts, P.CURVATURE_WINDOW_M, stepM);
    const sign = (x) => x>0 ? 1 : (x<0 ? -1 : 0);
    // Auf Geraden (Radius im drei- bis vierstelligen Meterbereich) kippt
    // die Radius-SCHAETZUNG schon bei winzigstem Rauschen relativ stark,
    // obwohl der tatsaechliche Lenkwinkel dort verschwindend klein bleibt
    // (< 0.1deg) - reines Messrauschen, kein spuerbarer "Sprung". Erst ab
    // MIN_NOTCH_STEER_DEG tatsaechlicher Winkelabweichung zaehlt es als
    // Ausreisser (dieselbe Ueberlegung wie NATURAL_THROTTLE_MIN_G: nicht
    // auf etwas reagieren, das ein Fahrer gar nicht spueren wuerde).
    const MIN_NOTCH_STEER_DEG = 0.3;
    // MIN_NOTCH_STEER_DEG allein reicht auf Geraden nicht: dort ist die
    // Radius-SCHAETZUNG selbst bei winzigem Positionsrauschen extrem
    // empfindlich (1/R-Umkehrung verstaerkt kleine Aenderungen bei grossem R
    // stark), sodass der Winkel-Sprung trotzdem gelegentlich ueber die
    // Schwelle rutscht. Zusaetzliche, direktere Bedingung: eine echte Kuppe
    // kann nur INNERHALB einer Kurve liegen, nie auf einer Geraden - also
    // nur Punkte pruefen, deren Radius ueberhaupt in Kurvenreichweite liegt
    // (Nutzervorgabe 08.09.2026: Radius < ~300-500m; 400m gewaehlt, mit
    // Sicherheitsabstand ueber der groessten bekannten echten Kurve von
    // 322m, siehe PROJEKT_STAND.md Radius-Aufblaehung-Fund).
    const MAX_NOTCH_RADIUS_M = 400;
    const steerAbs = radius.map(r => steerFromRadius(r));
    const flagged = [];
    for (let i=0;i<N;i++){
      if (radius[i] > MAX_NOTCH_RADIUS_M) continue;
      const im2=(i-2+N)%N, im1=(i-1+N)%N, ip1=(i+1)%N;
      const sBefore = sign(radius[im1]-radius[im2]);
      const sInto = sign(radius[i]-radius[im1]);
      const sOut = sign(radius[ip1]-radius[i]);
      if (sBefore === 0 || sInto !== -sBefore || sOut !== sBefore) continue;
      const jumpDeg = Math.abs(steerAbs[i] - (steerAbs[im1]+steerAbs[ip1])/2);
      if (jumpDeg >= MIN_NOTCH_STEER_DEG) flagged.push(i);
    }
    return flagged;
  }

  function smoothSinglePointNotches(nLat){
    // curvatureRadius() nutzt ein ~CURVATURE_WINDOW_M breites Fenster (der
    // Punkt selbst plus je ein Nachbar ~w~5 Punkte weiter aussen), nicht
    // nur die unmittelbaren Nachbarn - radius[237] haengt z.B. von Punkt
    // 232/242 ab, radius[236]/radius[238] aber von 231/241 bzw. 233/243:
    // DREI Berechnungen ohne gemeinsamen Punkt. Eine Ziehung nur des einen
    // Ausreisser-Punktes kann radius[237] daher aendern, aber nicht
    // garantiert zwischen radius[236]/radius[238] bringen (Bugfund
    // 08.09.2026). Fix: die Korrektur auf eine ganze Nachbarschaft von
    // +/-w Punkten ausweiten (Smootherstep-Kernel, identisch zu
    // applyBrush() - dasselbe bewaehrte Werkzeug, nur automatisch getriggert
    // statt per Hand gezogen), damit das gesamte ueberlappende
    // Fenster konsistent glatt wird, nicht nur ein einzelner Punkt darin.
    // Bis zu MAX_NOTCH_PASSES mal wiederholen, bis keine Ausreisser mehr
    // gefunden werden.
    const MAX_NOTCH_PASSES = 8;
    let cur = nLat;
    for (let pass=0; pass<MAX_NOTCH_PASSES; pass++){
      const flagged = findRadiusNotches(cur);
      if (flagged.length === 0) break;
      const ptsCur = pointsFromNLat(cur);
      const dsCur = new Float64Array(N);
      for (let i=0;i<N;i++){
        const j=(i+1)%N;
        dsCur[i] = Math.hypot(ptsCur[j][0]-ptsCur[i][0], ptsCur[j][1]-ptsCur[i][1]);
      }
      const w = Math.max(1, Math.round(P.CURVATURE_WINDOW_M/median(Array.from(dsCur))));
      const alphaArr = new Float64Array(N);
      for (const i of flagged){
        for (let k=-w;k<=w;k++){
          const j = ((i+k)%N+N)%N;
          const t = Math.abs(k)/w;
          const u = 1-t;
          const wgt = u*u*u*(u*(u*6-15)+10);  // Smootherstep, wie applyBrush()
          alphaArr[j] = Math.max(alphaArr[j], wgt);
        }
      }
      cur = relaxOnce(cur, alphaArr, null);
    }
    return cur;
  }

  // Gemeinsame, querkraftbasierte Relaxation ALLER aktuellen Fixpunkte
  // gleichzeitig statt nacheinander mit unabhaengigen harten Pins (siehe
  // Diskussion 2026-09-07: der Fahrer plant nicht Punkt-fuer-Punkt-isoliert,
  // sondern waehlt die Ausfahrt an jedem Punkt im Hinblick auf den naechsten -
  // genau das leistet diese bereits vorhandene, querkraftgewichtete
  // Relaxation von selbst, wenn alle Fixpunkte gemeinsam statt sequenziell
  // hart gepinnt einfliessen).
  //
  // targets: Array von {index, value, hard}. hard=true verhaelt sich wie
  // frueher (jeden Schritt exakt erzwungen, fuer spaeteren "muss getroffen
  // werden"-Modus zur Fahrfehler-Analyse). hard=false (Standard) ist ein
  // WEICHER Zielwert: einmal pro AEUSSERER Iteration ein Stueck (SOFT_PIN_PULL)
  // dorthin gezogen - die bestehende "brich ab, wenn Rundenzeit schlechter
  // wird"-Regel entscheidet dadurch selbst, wie weit sich der Zug lohnt (ein
  // Punkt darf geringfuegig verfehlt werden, wenn das schneller ist).
  // targets=[] (oder leer) laeuft als reine STEER_RATE_MAX_DEG_S-Durchsetzung
  // ohne Zielwerte (siehe Baseline-Bereinigung beim Laden).
  let lastPreNotchNLat = null; // Debug-Snapshot, siehe __editorDebug.testNotches
  function pinnedOptimize(nLatStart, targets, mu){
    let nLat = nLatStart.slice();
    // WICHTIG: den margen-sicheren Abstand schon HIER durchsetzen, nicht erst
    // nach der ersten Relaxation - der "brich ab, wenn schlechter"-Mechanismus
    // unten kann sonst auf DIESEN Rohzustand zurueckfallen (naemlich dann,
    // wenn schon der erste Relaxationsversuch schlechter wird) und wuerde
    // damit die Randbedingung umgehen (applyBrush()/direkter Zielwert kennt
    // EDGE_MARGIN_M nicht, siehe Nutzer-Report 2026-09-07: "Innenlinie darf
    // nicht eingebeult werden").
    for (let i=0;i<N;i++) nLat[i] = clampInset(nLat[i], i);
    const hardTargets = targets.filter(t => t.hard);
    const softTargets = targets.filter(t => !t.hard);
    for (const t of hardTargets) nLat[t.index] = t.value;
    let prevLapTime = Infinity;
    let prevNLat = nLat.slice();
    let compliant = false; // wird true, sobald 240deg/s nirgends mehr ueberschritten wird
    for (let outer=0; outer<OPT.n_outer_max; outer++){
      const pts = pointsFromNLat(nLat);
      const sim = simulateLap(pts, mu);
      const steerRate = computeSteerRateDegS(pts, sim.radius, sim.vFinal, sim.ds);
      let maxAbsRate = 0;
      for (let i=0;i<N;i++) maxAbsRate = Math.max(maxAbsRate, Math.abs(steerRate[i]));
      const nowCompliant = maxAbsRate <= STEER_RATE_MAX_DEG_S;
      // Solange die Lenkgeschwindigkeits-Grenze noch verletzt ist, wird NICHT
      // wegen einer schlechteren Rundenzeit abgebrochen - das ist eine harte
      // Randbedingung (physikalisch unmoeglich), keine Optimierungspraeferenz,
      // die man gegen Rundenzeit abwaegt. Erst wenn compliant, gilt wieder die
      // normale "brich bei erster Verschlechterung ab"-Regel von vorher.
      if (compliant && nowCompliant && sim.lapTime > prevLapTime) { nLat = prevNLat; break; }
      prevLapTime = sim.lapTime;
      prevNLat = nLat.slice();
      compliant = nowCompliant;
      const alphaArr = new Float64Array(N);
      for (let i=0;i<N;i++){
        const aLatReq = sim.vFinal[i]**2 / Math.max(sim.radius[i],1e-6);
        const gripExcess = Math.min(Math.max(aLatReq/(mu*P.G),0),1);
        const steerExcess = Math.max(0, Math.abs(steerRate[i])/STEER_RATE_MAX_DEG_S - 1);
        alphaArr[i] = Math.min(0.9, OPT.base_alpha*(0.1+0.9*gripExcess) + 0.6*steerExcess);
      }
      for (let s=0;s<OPT.inner_steps;s++) nLat = relaxOnce(nLat, alphaArr, hardTargets);
      for (const t of softTargets){
        const pulled = nLat[t.index] + SOFT_PIN_PULL*(t.value-nLat[t.index]);
        nLat[t.index] = clampInset(pulled, t.index);
      }
    }
    // Nachbearbeitung, siehe smoothSinglePointNotches() - reine Punkt-
    // Korrektur, kein erneutes Relaxieren, aendert die obige Optimierung
    // selbst nicht.
    lastPreNotchNLat = nLat.slice(); // Debug-Snapshot fuer __editorDebug, siehe unten
    return smoothSinglePointNotches(nLat);
  }

  function applyBrush(nLatBase, index, targetVal, radiusM){
    const nLat = nLatBase.slice();
    const stepM = P.RESAMPLE_STEP_M;
    const win = Math.max(1, Math.round(radiusM/stepM));
    const delta = targetVal - nLatBase[index];
    for (let k=-win;k<=win;k++){
      const i = ((index+k)%N+N)%N;
      const t = Math.abs(k)/win;
      // Smootherstep (Perlin) statt Raised-Cosine: Kruemmung (2. Ableitung) am
      // Fensterrand ebenfalls auf 0, nicht nur die Steigung - sonst entsteht am
      // Rand des Pinsels ein realer Kruemmungssprung (spuerbarer "Kink" in der
      // Physik, siehe PROJEKT_STAND.md, gefunden 2026-09-07).
      const u = 1-t;
      const w = u*u*u*(u*(u*6-15)+10);
      let val = nLatBase[i] + delta*w;
      val = Math.min(Math.max(val, LO[i]), HI[i]);
      nLat[i] = val;
    }
    nLat[index] = targetVal;
    return nLat;
  }

  // ---------- Zustand ----------
  // Statt der reinen Punktposition wird die BEARBEITUNGSHISTORIE als geordnete
  // Liste von Fixpunkten {index, value} gespeichert (chronologisch, ein erneutes
  // Ziehen desselben Punktes aktualisiert seinen Wert und ruecken ihn ans Ende
  // der Liste, wie eine "zuletzt beruehrt"-Reihenfolge). Die tatsaechliche Linie
  // wird daraus IMMER durch Neuabspielen ab der Ausgangslinie erzeugt
  // (recomputeFromEdits) - dadurch kann jede einzelne Bearbeitung, nicht nur die
  // letzte, geloescht werden, ohne den Zustand inkonsistent werden zu lassen.
  // Die Ausgangslinie kommt aus einem externen Kurven-Optimierer und kann an
  // sehr engen Scheitelpunkten selbst schon die Lenkgeschwindigkeits-Grenze
  // verletzen (gefunden 30.08.2026: 671°/s an der enttesten Kurve, in der
  // UNBEARBEITETEN Linie - kein Bearbeitungs-Artefakt). Einmalig beim Laden
  // per pinnedOptimize() ohne Fixpunkte (leere targets-Liste) bereinigt, damit die
  // 240°/s-Grenze ueberall gilt, nicht nur nach einer manuellen Bearbeitung.
  const nLatBaselineRaw = Float64Array.from(data.n_lat_start);
  const nLatBaseline = pinnedOptimize(nLatBaselineRaw, [], P.MU);
  let baselineLapTime = simulateLap(pointsFromNLat(nLatBaseline), P.MU).lapTime;
  let edits = loadEditsFromStorage();
  let nLat = recomputeFromEdits(edits);
  let dragging = false, dragIndex = null;

  function validateEditsArray(arr){
    if (!Array.isArray(arr)) return null;
    const valid = arr.filter(e => e && Number.isInteger(e.index) && e.index>=0 && e.index<N
                            && typeof e.value === 'number' && isFinite(e.value));
    return valid.map(e => ({
      index: e.index, value: e.value,
      radius: (typeof e.radius === 'number' && isFinite(e.radius)) ? e.radius : OPT.brush_radius_m_default,
      hard: !!e.hard,
    }));
  }

  function loadEditsFromStorage(){
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return validateEditsArray(arr) || [];
    } catch(e) { return []; }
  }
  function saveEditsToStorage(){
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(edits)); } catch(e) {}
  }

  function recomputeFromEdits(editList){
    let n = nLatBaseline.slice();
    for (const e of editList){
      // Warm-Start: Pinsel-Falloff auf den aktuellen Zustand anwenden, damit
      // die anschliessende gemeinsame Relaxation nicht von einer Linie
      // startet, die an genau einem Index hart auf den Zielwert springt.
      const radius = (e.radius != null) ? e.radius : OPT.brush_radius_m_default;
      n = applyBrush(n, e.index, e.value, radius);
    }
    // Alle aktuellen Fixpunkte GEMEINSAM (nicht nacheinander mit unabhaengigen
    // harten Pins) querkraftbasiert relaxieren - siehe pinnedOptimize().
    return pinnedOptimize(n, editList, P.MU);
  }

  // ---------- Rendering ----------
  const trackCanvas = document.getElementById('trackCanvas');
  const ctx = trackCanvas.getContext('2d');

  function computeShiftEvents(sim){
    // Ein Schaltpunkt ist ein Index, an dem sich der (in natuerlicher
    // Streckenreihenfolge abgeleitete, siehe deriveGearTrace) Gang gegenueber
    // dem VORHERIGEN Index aendert - liefert (index, fromGear, toGear, s_m,
    // up) je Ereignis, BEIDE Richtungen (Hoch- UND Runterschalten, siehe
    // Nutzer-Feedback/Nachtrag in deriveGearTrace). Bewusst NICHT zyklisch
    // verglichen (Index 0 vs. n-1): an der Start/Ziel-Naht wuerde ein
    // legitimer Neustart der Gangwahl sonst als Schaltvorgang erscheinen.
    const n = sim.gear.length;
    const events = [];
    for (let i=1;i<n;i++){
      if (sim.gear[i] !== sim.gear[i-1]){
        events.push({index: i, fromGear: sim.gear[i-1], toGear: sim.gear[i], s_m: sim.s[i],
                     up: sim.gear[i] > sim.gear[i-1]});
      }
    }
    return events;
  }

  function speedColor(v){
    const stops = [[40,[58,109,209]],[90,[69,214,196]],[140,[232,163,61]],[190,[224,72,60]]];
    if (v <= stops[0][0]) return stops[0][1];
    if (v >= stops[stops.length-1][0]) return stops[stops.length-1][1];
    for (let i=0;i<stops.length-1;i++){
      const [s0,c0]=stops[i], [s1,c1]=stops[i+1];
      if (v>=s0 && v<=s1){
        const t=(v-s0)/(s1-s0);
        return [0,1,2].map(k=>Math.round(c0[k]+t*(c1[k]-c0[k])));
      }
    }
    return stops[stops.length-1][1];
  }

  function fitBounds(){
    const allX = data.left_edge.map(p=>p[0]).concat(data.right_edge.map(p=>p[0]));
    const allY = data.left_edge.map(p=>p[1]).concat(data.right_edge.map(p=>p[1]));
    return { minX:Math.min(...allX), maxX:Math.max(...allX), minY:Math.min(...allY), maxY:Math.max(...allY) };
  }
  const bounds = fitBounds();
  const PAD = 28;

  // Zoom/Pan-Zustand: zoom=1/pan=0 entspricht der urspruenglichen
  // "auf den Korridor eingepassten" Ansicht. project()/unproject() wenden
  // diese Transformation zusaetzlich zur festen Basis-Einpassung an, damit
  // Treffertest, Zeichnen und Maus-/Touch-Koordinaten immer konsistent
  // bleiben, egal wie weit reingezoomt/verschoben wurde.
  const view = {zoom: 1, panX: 0, panY: 0};
  const ZOOM_MIN = 1, ZOOM_MAX = 25;

  function baseScaleFor(w, h){
    const spanX=bounds.maxX-bounds.minX, spanY=bounds.maxY-bounds.minY;
    return Math.min((w-2*PAD)/spanX, (h-2*PAD)/spanY);
  }
  function project(p, w, h){
    const scale = baseScaleFor(w,h);
    const bx = PAD+(p[0]-bounds.minX)*scale;
    const by = h-PAD-(p[1]-bounds.minY)*scale;
    return [bx*view.zoom+view.panX, by*view.zoom+view.panY];
  }
  function unproject(x, y, w, h){
    const scale = baseScaleFor(w,h);
    const bx = (x-view.panX)/view.zoom;
    const by = (y-view.panY)/view.zoom;
    return [bounds.minX+(bx-PAD)/scale, bounds.minY+(h-PAD-by)/scale];
  }
  function zoomAt(px, py, factor){
    const newZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.zoom*factor));
    if (newZoom <= ZOOM_MIN){
      // ganz rausgezoomt: IMMER auf die komplette, zentrierte Einpassung
      // zurueck (unabhaengig vom bisherigen Pan) - sonst koennte man beim
      // Rauszoomen "haengenbleiben", ohne wieder die volle Strecke zu sehen.
      view.zoom = ZOOM_MIN; view.panX = 0; view.panY = 0;
    } else {
      const actual = newZoom/view.zoom;
      view.panX = px - (px-view.panX)*actual;
      view.panY = py - (py-view.panY)*actual;
      view.zoom = newZoom;
    }
    draw();
  }

  let bgImage=null, bgReady=false;
  if (data.background_image_jpeg_b64){
    bgImage = new Image();
    bgImage.onload = ()=>{ bgReady=true; render(); };
    bgImage.src = "data:image/jpeg;base64," + data.background_image_jpeg_b64;
  }

  function resizeCanvas(){
    const wrap = trackCanvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    trackCanvas.width = wrap.clientWidth*dpr;
    trackCanvas.height = wrap.clientHeight*dpr;
    ctx.setTransform(dpr,0,0,dpr,0,0);
    resizeTraceCanvas();
    render();
  }

  // ---------- Geschwindigkeit/Drehzahl ueber die Runde (Trace-Diagramm) ----------
  const traceCanvas = document.getElementById('traceCanvas');
  const tctx = traceCanvas.getContext('2d');

  function resizeTraceCanvas(){
    const wrap = traceCanvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    traceCanvas.width = wrap.clientWidth*dpr;
    traceCanvas.height = wrap.clientHeight*dpr;
    tctx.setTransform(dpr,0,0,dpr,0,0);
  }

  function drawTrace(){
    if (!lastSim) return;
    const sim = lastSim;
    const n = sim.s.length;
    const w = traceCanvas.clientWidth, h = traceCanvas.clientHeight;
    const padL=40, padR=40, padT=10, padB=20;
    tctx.clearRect(0,0,w,h);
    tctx.fillStyle = '#12171b';
    tctx.fillRect(0,0,w,h);

    const totalLen = sim.s[n-1] + sim.ds[n-1];
    const vMax = Math.max(...sim.vFinal)*3.6*1.08;
    const rpmMax = P.REDLINE_RPM*1.05;
    const xOf = s => padL + (s/totalLen)*(w-padL-padR);
    const yOfV = v => h-padB - (v/vMax)*(h-padT-padB);
    const yOfR = r => h-padB - (r/rpmMax)*(h-padT-padB);

    // Bremszonen-Hintergrund (dieselbe Definition wie auf der Karte). Punkte,
    // die smoothWastedAccelBrake() auf eine kontrollierte Teillast-
    // Beschleunigung umgestellt hat, zaehlen NICHT als Bremsen (siehe dort).
    for (let i=0;i<n;i++){
      if (!sim.coasting[i] && sim.vFinal[i] < sim.vFwd[i]-1e-3){
        const x0=xOf(sim.s[i]), x1=xOf(sim.s[i]+sim.ds[i]);
        tctx.fillStyle = 'rgba(224,72,60,0.16)';
        tctx.fillRect(x0, padT, Math.max(x1-x0,1.2), h-padT-padB);
      }
    }

    // Redline-Referenzlinie
    tctx.save();
    tctx.setLineDash([3,3]);
    tctx.strokeStyle = 'rgba(238,242,243,0.25)';
    tctx.lineWidth = 1;
    tctx.beginPath();
    tctx.moveTo(padL, yOfR(P.REDLINE_RPM));
    tctx.lineTo(w-padR, yOfR(P.REDLINE_RPM));
    tctx.stroke();
    tctx.restore();
    tctx.fillStyle = 'rgba(238,242,243,0.4)';
    tctx.font = '500 9px "IBM Plex Mono", monospace';
    tctx.textAlign = 'left'; tctx.textBaseline = 'bottom';
    tctx.fillText('Redline', padL+2, yOfR(P.REDLINE_RPM)-2);

    // Schaltpunkte als vertikale Linien
    computeShiftEvents(sim).forEach(ev=>{
      const x = xOf(ev.s_m);
      tctx.strokeStyle = 'rgba(197,150,255,0.55)';
      tctx.lineWidth = 1.2;
      tctx.beginPath();
      tctx.moveTo(x, padT); tctx.lineTo(x, h-padB);
      tctx.stroke();
    });

    // Geschwindigkeitskurve (teal, linke Achse)
    tctx.strokeStyle = '#45d6c4';
    tctx.lineWidth = 1.6;
    tctx.beginPath();
    for (let i=0;i<=n;i++){
      const k = i%n;
      const x = xOf(sim.s[k] + (i===n ? totalLen-sim.s[k] : 0));
      const y = yOfV(sim.vFinal[k]*3.6);
      if (i===0) tctx.moveTo(x,y); else tctx.lineTo(x,y);
    }
    tctx.stroke();

    // Drehzahlkurve (amber, rechte Achse)
    tctx.strokeStyle = '#e8a33d';
    tctx.lineWidth = 1.6;
    tctx.beginPath();
    for (let i=0;i<=n;i++){
      const k = i%n;
      const x = xOf(sim.s[k] + (i===n ? totalLen-sim.s[k] : 0));
      const y = yOfR(sim.rpm[k]);
      if (i===0) tctx.moveTo(x,y); else tctx.lineTo(x,y);
    }
    tctx.stroke();

    // Achsen/Beschriftung
    tctx.strokeStyle = 'rgba(238,242,243,0.3)';
    tctx.lineWidth = 1;
    tctx.beginPath();
    tctx.moveTo(padL, padT); tctx.lineTo(padL, h-padB); tctx.lineTo(w-padR, h-padB);
    tctx.stroke();
    tctx.font = '500 9px "IBM Plex Mono", monospace';
    tctx.textBaseline = 'middle';
    tctx.fillStyle = '#45d6c4'; tctx.textAlign = 'right';
    [0, vMax/2, vMax].forEach(v=>tctx.fillText(Math.round(v)+'', padL-4, yOfV(v)));
    tctx.fillStyle = '#e8a33d'; tctx.textAlign = 'left';
    [0, rpmMax/2, rpmMax].forEach(r=>tctx.fillText(Math.round(r)+'', w-padR+4, yOfR(r)));
    tctx.fillStyle = 'rgba(238,242,243,0.5)'; tctx.textAlign = 'center'; tctx.textBaseline = 'top';
    [0, totalLen/2, totalLen].forEach(s=>tctx.fillText(Math.round(s)+'m', xOf(s), h-padB+3));
    tctx.save();
    tctx.textAlign = 'left'; tctx.textBaseline = 'top';
    tctx.fillStyle = '#45d6c4'; tctx.fillText('km/h', 2, 2);
    tctx.fillStyle = '#e8a33d'; tctx.textAlign = 'right'; tctx.fillText('U/min', w-2, 2);
    tctx.restore();
  }

  function drawPolyline(points, w, h, close){
    ctx.beginPath();
    points.forEach((p,i)=>{
      const [x,y]=project(p,w,h);
      if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    });
    if (close) ctx.closePath();
  }

  let lastSim = null, lastPts = null;

  function draw(){
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    ctx.clearRect(0,0,w,h);
    ctx.fillStyle='#12171b';
    ctx.fillRect(0,0,w,h);

    if (bgReady && data.background_extent_local){
      const [ex0,ey0,ex1,ey1] = data.background_extent_local;
      const [px0,py0] = project([ex0,ey1], w, h);
      const [px1,py1] = project([ex1,ey0], w, h);
      ctx.drawImage(bgImage, px0, py0, px1-px0, py1-py0);
      ctx.fillStyle='rgba(18,23,27,0.32)';
      ctx.fillRect(0,0,w,h);
    }

    const ribbon = data.left_edge.concat([...data.right_edge].reverse());
    drawPolyline(ribbon, w, h, true);
    ctx.strokeStyle='rgba(238,242,243,0.55)';
    ctx.lineWidth=1;
    ctx.stroke();
    if (!bgReady){ ctx.fillStyle='#3a4148'; ctx.fill(); }

    ctx.save();
    ctx.setLineDash([5,6]);
    ctx.strokeStyle='rgba(238,242,243,0.35)';
    ctx.lineWidth=1;
    drawPolyline(data.centerline, w, h, true);
    ctx.stroke();
    ctx.restore();

    const pts = lastPts, sim = lastSim;

    // Bremszonen als farbiges Band UNTER der Ideallinie (siehe Docstring:
    // physikalisch aus vFinal < vFwd abgeleitet, kein Schwellwert-Rateversuch).
    // Teillast-geglaettete Punkte (siehe smoothWastedAccelBrake) zaehlen nicht
    // als Bremsen.
    for (let i=0;i<N;i++){
      const j=(i+1)%N;
      const braking = !sim.coasting[i] && sim.vFinal[i] < sim.vFwd[i]-1e-3;
      if (!braking) continue;
      const p0=project(pts[i],w,h), p1=project(pts[j],w,h);
      ctx.strokeStyle='rgba(224,72,60,0.5)';
      ctx.lineWidth=9;
      ctx.beginPath(); ctx.moveTo(p0[0],p0[1]); ctx.lineTo(p1[0],p1[1]); ctx.stroke();
    }

    // Ideallinie, nach Geschwindigkeit eingefaerbt
    for (let i=0;i<N;i++){
      const j=(i+1)%N;
      const p0=project(pts[i],w,h), p1=project(pts[j],w,h);
      const [r,g,b] = speedColor(sim.vFinal[i]*3.6);
      ctx.strokeStyle=`rgb(${r},${g},${b})`;
      ctx.lineWidth=3.2;
      ctx.beginPath(); ctx.moveTo(p0[0],p0[1]); ctx.lineTo(p1[0],p1[1]); ctx.stroke();
    }

    // aktiver Pinsel-Radius waehrend des Ziehens
    if (dragging && dragIndex != null){
      const win = Math.max(1, Math.round(brushRadiusM/P.RESAMPLE_STEP_M));
      const idxs=[]; for (let k=-win;k<=win;k++) idxs.push(((dragIndex+k)%N+N)%N);
      ctx.save();
      ctx.strokeStyle='rgba(232,163,61,0.7)';
      ctx.lineWidth=6;
      ctx.beginPath();
      idxs.forEach((idx,k)=>{ const [x,y]=project(pts[idx],w,h); if(k===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
      ctx.stroke();
      ctx.restore();
    }

    // Schaltpunkte: kurzer Querstrich genau an der Stelle, an der ein
    // Gangwechsel beginnt, plus "3→4"-Beschriftung leicht versetzt daneben.
    computeShiftEvents(sim).forEach(ev=>{
      const [x,y] = project(pts[ev.index], w, h);
      const j = (ev.index+1)%N;
      const [x2,y2] = project(pts[j], w, h);
      const dx = x2-x, dy = y2-y;
      const len = Math.hypot(dx,dy) || 1;
      const nx = -dy/len, ny = dx/len; // senkrecht zur Fahrtrichtung
      ctx.strokeStyle = 'rgba(197,150,255,0.95)';
      ctx.lineWidth = 2.4;
      ctx.beginPath();
      ctx.moveTo(x-nx*7, y-ny*7);
      ctx.lineTo(x+nx*7, y+ny*7);
      ctx.stroke();
      ctx.fillStyle = 'rgba(197,150,255,0.95)';
      ctx.font = '600 10px "IBM Plex Mono", monospace';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(`${ev.fromGear}→${ev.toGear}`, x+nx*11+4, y+ny*11);
    });

    // Marker fuer manuell bearbeitete Punkte (Raute, Teal - unterscheidbar von
    // den amberfarbenen Kurvennummern-Kreisen)
    edits.forEach((e,i)=>{
      const [x,y] = project(pts[e.index], w, h);
      ctx.save();
      ctx.translate(x,y);
      ctx.rotate(Math.PI/4);
      ctx.fillStyle = 'rgba(69,214,196,0.92)';
      ctx.strokeStyle = '#0d1a18';
      ctx.lineWidth = 1.3;
      ctx.fillRect(-5,-5,10,10);
      ctx.strokeRect(-5,-5,10,10);
      ctx.restore();
      ctx.fillStyle = '#0d1a18';
      ctx.font = '700 9px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(i+1), x, y+0.5);
    });

    if (data.corners){
      data.corners.forEach(c=>{
        const [x,y]=project([c.x,c.y],w,h);
        ctx.beginPath(); ctx.arc(x,y,9,0,Math.PI*2);
        ctx.fillStyle='rgba(18,23,27,0.85)'; ctx.fill();
        ctx.strokeStyle='#e8a33d'; ctx.lineWidth=1.4; ctx.stroke();
        ctx.fillStyle='#e8a33d';
        ctx.font='600 11px "IBM Plex Mono", monospace';
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(String(c.number), x, y+0.5);
      });
    }

    drawCar(pts, w, h);
  }

  // ---------- Fahrsimulation (Echtzeit-Wiedergabe entlang der Runde) ----------
  const sim_ = {playing:false, t:0, speed:1, lastTs:null};

  // Reine Anzeige-Zugabe (Nutzerwunsch 09.09.2026): die allererste Wiedergabe
  // nach einem Seiten-Reload zeigt einen stehenden Start (v=0 an der
  // Start/Ziel-Linie, Vollgas-Sprint) statt direkt mit Rundentempo
  // loszufahren. WICHTIG: greift NICHT in lastSim/nLat/edits/lapTime/den
  // Export ein - reiner lokaler Anzeige-Zustand (nur s/v/gear/shiftT unten),
  // die eigentliche Rundensimulation ("Grundlage aller Berechnungen", im
  // Nutzerbild bereits "Runde 2") bleibt komplett unveraendert. Sobald das
  // Launch-Tempo das reale Rundentempo an DERSELBEN Streckenposition
  // erreicht, uebernimmt ab da nahtlos die normale, unveraenderte Wiedergabe
  // (Uebergang per sim_.t = sim.t[i], danach exakt der bisherige Codepfad).
  // Initial (vor dem ersten Play) schon gesetzt, nicht erst bei Play erzeugt
  // - dadurch steht der Wagen auch im Standbild vor dem ersten Play still.
  let standingStart = {s: 0, v: 0, gear: 1, shiftT: 0};

  function stepStandingStart(dt){
    const sim = lastSim;
    let {s, v, gear, shiftT} = standingStart;
    if (shiftT > 0){
      v = Math.max(0, v + coastAccel(v)*dt);
      shiftT = Math.max(0, shiftT - dt);
    } else if (gear < 6){
      const aCur = accel(v, gear), aNext = accel(v, gear+1);
      if (rpmFromSpeed(v, gear) >= P.REDLINE_RPM || aNext > aCur){
        gear += 1;
        shiftT = P.ATTACK_SHIFT_S[`${gear-1}-${gear}`] ?? 0.15;
      } else {
        v = v + Math.max(aCur, 0)*dt;
      }
    } else {
      v = v + Math.max(accel(v, gear), 0)*dt;
    }
    s += v*dt;
    const i = findSimIndex(sim.s, Math.min(s, sim.s[N-1]));
    // Fertig, sobald das Launch-Tempo das reale Rundentempo an dieser Position
    // erreicht (oder als Sicherheitsnetz nach einer vollen Rundenlaenge).
    if (v >= sim.vFinal[i] || s >= sim.s[N-1]){
      standingStart = null;
      sim_.t = sim.t[i];
      return;
    }
    standingStart = {s, v, gear, shiftT};
  }

  function positionFromArcLength(pts, sim, sNow){
    const i = findSimIndex(sim.s, Math.min(sNow, sim.s[N-1]));
    const j = (i+1)%N;
    const segLen = sim.ds[i];
    const frac = segLen>1e-9 ? Math.max(0, Math.min(1, (sNow-sim.s[i])/segLen)) : 0;
    const x = pts[i][0]+(pts[j][0]-pts[i][0])*frac;
    const y = pts[i][1]+(pts[j][1]-pts[i][1])*frac;
    const heading = Math.atan2(pts[j][1]-pts[i][1], pts[j][0]-pts[i][0]);
    return {x, y, i, heading};
  }

  function findSimIndex(tArr, tNow){
    const n = tArr.length;
    for (let i=1;i<n;i++){ if (tArr[i] > tNow) return i-1; }
    return n-1;
  }

  function carPositionAt(pts, sim, tNow){
    const n = pts.length;
    const i = findSimIndex(sim.t, tNow);
    const j = (i+1)%n;
    const segT = (j===0 ? sim.lapTime : sim.t[j]) - sim.t[i];
    const frac = segT>1e-9 ? (tNow-sim.t[i])/segT : 0;
    const x = pts[i][0]+(pts[j][0]-pts[i][0])*frac;
    const y = pts[i][1]+(pts[j][1]-pts[i][1])*frac;
    const heading = Math.atan2(pts[j][1]-pts[i][1], pts[j][0]-pts[i][0]);
    return {x, y, i, heading};
  }

  function drawCar(pts, w, h){
    if (!lastSim) return;
    const {x, y, i, heading} = standingStart
      ? positionFromArcLength(pts, lastSim, standingStart.s)
      : carPositionAt(pts, lastSim, sim_.t);
    const [sx, sy] = project([x,y], w, h);
    // Bildschirm-Blickwinkel (Nord=oben, Weltwinkel invertiert) fuer die Rotation beruecksichtigen
    const screenHeading = -heading;
    ctx.save();
    ctx.translate(sx, sy);
    ctx.rotate(screenHeading);
    ctx.fillStyle = '#eef2f3';
    ctx.strokeStyle = '#12171b';
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(9,0); ctx.lineTo(-6,5); ctx.lineTo(-3,0); ctx.lineTo(-6,-5);
    ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.restore();

    document.getElementById('simTime').textContent = standingStart ? 'Start' : (sim_.t.toFixed(1)+' s');
    document.getElementById('simSpeedKmh').textContent = Math.round((standingStart ? standingStart.v : lastSim.vFinal[i])*3.6)+' km/h';
    document.getElementById('simGear').textContent = standingStart ? standingStart.gear : lastSim.gear[i];
    document.getElementById('simRpm').textContent = Math.round(standingStart ? rpmFromSpeed(standingStart.v, standingStart.gear) : lastSim.rpm[i])+' U/min';

    // Bremse/Gas: ueberwiegend voll am Limit bremsend (Bremszone, gleiche
    // Definition wie das rote Kartenband), voll beschleunigend, oder am
    // Kurvenlimit rollend (beides 0%) - AUSSER an den von
    // smoothWastedAccelBrake()/smoothShortBrakeSpikes() geglaetteten Stellen
    // (kurze Vollgas-Inseln zwischen zwei Bremszonen bzw. kurze isolierte
    // Bremsspitzen, siehe dort/PROJEKT_STAND.md), dort zeigt das jeweilige
    // Pedal die tatsaechlich berechnete Teillast-/Teilbrems-Stellung.
    const prevI = (i-1+N)%N;
    const isCoasting = !standingStart && !!lastSim.coasting[i];
    const braking = !standingStart && !isCoasting && lastSim.vFinal[i] < lastSim.vFwd[i]-1e-3;
    const accelerating = !standingStart && !braking && !isCoasting && lastSim.vFinal[i] > lastSim.vFinal[prevI]+1e-6;
    const brakePct = braking ? Math.round((lastSim.brakeFrac[i] ?? 1)*100) : 0;
    document.getElementById('brakeFill').style.height = brakePct + '%';
    // Im experimentellen "natuerlichen Gasmodell" kommt die Gaspedal-
    // Stellung IMMER aus der Lenkrate-Formel (nicht nur an geglaetteten
    // Stellen) - siehe naturalThrottleMode. ABER: die Formel wird im
    // Vorwaertspass berechnet, BEVOR der Rueckwaertspass die Geschwindigkeit
    // fuer eine spaetere Bremszone kappt - an einem Bremspunkt darf die
    // Anzeige den (dann ueberholten) Vorwaerts-Wert nicht mehr zeigen: man
    // kann nicht gleichzeitig bremsen und Gas geben (Nutzerhinweis
    // 08.09.2026).
    const throttlePct = standingStart ? 100 : (lastSim.naturalMode
      ? (braking ? 0 : Math.round((lastSim.throttleFracNatural[i] ?? 0)*100))
      : (accelerating ? 100 : (isCoasting ? Math.round(lastSim.throttleFrac[i]*100) : 0)));
    document.getElementById('throttleFill').style.height = throttlePct + '%';

    // Lenkwinkel: im Projekt gibt es kein Lenkmodell (nur Kruemmungsradius) -
    // grobe Ackermann-Naeherung (Radstand oeffentliche Herstellerangabe MX-5
    // ND, KEIN gemessener/validierter Wert wie der Rest des Fahrzeugmodells)
    // nur fuer diese Anzeige, keine physikalische Grundlage fuer irgendeine
    // andere Berechnung im Tool. Vorzeichen (Links-/Rechtskurve) ueber
    // dieselbe Fensterbreite wie curvatureRadius()/computeSteerRateDegS
    // bestimmt (CURVATURE_WINDOW_M) statt ueber unmittelbare Nachbarpunkte -
    // Nutzer-Report 2026-09-08: bei Nachbarpunkten (~3m Basis) durchlief die
    // Lenkradanzeige mitten in einer durchgehenden Kurve kurz die Nullachse
    // (z.B. Punkt 513 einer Linkskurve bis 517), obwohl die tatsaechliche
    // (fensterbasierte) Lenkrate dort glatt und einseitig blieb - derselbe
    // Diskretisierungsartefakt wie bei der urspruenglichen 934°/s-Messung,
    // nur in dieser separaten Rohformel nie mitkorrigiert.
    const wSign = Math.max(1, Math.round(P.CURVATURE_WINDOW_M/P.RESAMPLE_STEP_M));
    const p0 = pts[(i-wSign+N)%N], p1 = pts[i], p2 = pts[(i+wSign)%N];
    const cross = (p1[0]-p0[0])*(p2[1]-p0[1]) - (p2[0]-p0[0])*(p1[1]-p0[1]);
    const turnSign = cross > 0 ? -1 : (cross < 0 ? 1 : 0);
    const roadWheelDeg = Math.atan(WHEELBASE_M/Math.max(lastSim.radius[i],1)) * 180/Math.PI * turnSign;
    const wheelDeg = Math.max(-150, Math.min(150, roadWheelDeg*STEERING_RATIO));
    document.getElementById('wheelRotor').style.transform = `rotate(${wheelDeg}deg)`;
  }

  function simFrame(ts){
    if (!sim_.playing) return;
    if (sim_.lastTs != null){
      const dt = (ts-sim_.lastTs)/1000 * sim_.speed;
      if (standingStart){
        stepStandingStart(dt); // setzt bei Abschluss selbst sim_.t (siehe dort)
      } else {
        sim_.t += dt;
        if (lastSim && sim_.t >= lastSim.lapTime) sim_.t %= lastSim.lapTime;
      }
    }
    sim_.lastTs = ts;
    draw();
    requestAnimationFrame(simFrame);
  }

  document.getElementById('simPlayBtn').addEventListener('click', ()=>{
    sim_.playing = !sim_.playing;
    const btn = document.getElementById('simPlayBtn');
    if (sim_.playing){
      btn.innerHTML = '&#10074;&#10074; Pause';
      sim_.lastTs = null;
      requestAnimationFrame(simFrame);
    } else {
      btn.innerHTML = '&#9654; Start';
    }
  });
  function pauseSim(){
    if (!sim_.playing) return;
    sim_.playing = false;
    document.getElementById('simPlayBtn').innerHTML = '&#9654; Start';
  }
  // Punktweises Vor-/Zurueckspringen fuer die Feininspektion im Pausenmodus -
  // springt direkt auf die Zeit des Nachbarpunkts in lastSim.t, statt eine
  // kontinuierliche Zeitspanne zu addieren (siehe findSimIndex/carPositionAt).
  function stepSimPoint(delta){
    if (!lastSim) return;
    pauseSim();
    standingStart = null; // manuelles Springen beendet den Start-Sprint, siehe oben
    const i = findSimIndex(lastSim.t, sim_.t);
    const j = ((i+delta)%N+N)%N;
    sim_.t = lastSim.t[j];
    draw();
  }
  document.getElementById('simPrevBtn').addEventListener('click', ()=>stepSimPoint(-1));
  document.getElementById('simNextBtn').addEventListener('click', ()=>stepSimPoint(1));
  document.getElementById('simSpeed').addEventListener('input', (e)=>{
    sim_.speed = parseFloat(e.target.value);
    document.getElementById('simSpeedLabel').textContent =
      sim_.speed.toFixed(2).replace(/\.?0+$/,'') + 'x' + (sim_.speed===1 ? ' (Echtzeit)' : '');
  });

  function updateReadouts(){
    const sim = lastSim;
    document.getElementById('roLapTime').textContent = sim.lapTime.toFixed(2) + ' s';
    document.getElementById('roTopSpeed').textContent = Math.round(Math.max(...sim.vFinal)*3.6) + ' km/h';
    const delta = sim.lapTime - baselineLapTime;
    document.getElementById('roDelta').textContent = (delta>=0?'+':'') + delta.toFixed(2) + ' s';
    const totalLen = sim.ds.reduce((a,b)=>a+b,0);
    document.getElementById('roLength').textContent = totalLen.toFixed(0) + ' m';
    document.getElementById('baselineBadge').style.display = edits.length===0 ? 'inline-block' : 'none';
    document.getElementById('undoBtn').disabled = edits.length===0;
  }

  function renderEditsList(){
    const container = document.getElementById('editsList');
    container.innerHTML = '';
    if (edits.length===0){
      const p = document.createElement('div');
      p.className = 'note';
      p.textContent = 'Noch keine Bearbeitungen - klicke auf die Linie und ziehe sie.';
      container.appendChild(p);
      return;
    }
    edits.forEach((e,i)=>{
      const row = document.createElement('div');
      row.className = 'edit-row';
      // Bei weichen Fixpunkten kann der tatsaechlich erreichte Wert vom
      // gezogenen Zielwert abweichen (siehe pinnedOptimize: Zeitvorteil vs.
      // Zielnaehe) - das direkt anzuzeigen macht sichtbar, wo die Physik eine
      // andere Linie fuer schneller haelt, und ist die Datengrundlage fuer
      // die spaeter geplante Fahrfehler-Analyse im "hart"-Modus.
      const achieved = nLat[e.index];
      const delta = achieved - e.value;
      const deltaTxt = (!e.hard && Math.abs(delta) > 0.02)
        ? ` <span class="note-inline">(erreicht ${achieved.toFixed(2)}m, ${delta>0?'+':''}${delta.toFixed(2)}m)</span>`
        : '';
      const lbl = document.createElement('span');
      lbl.innerHTML = `<span class="marker-dot"></span>#${i+1} &middot; Punkt ${e.index}${deltaTxt}`;
      const hardLbl = document.createElement('label');
      hardLbl.className = 'hard-toggle';
      hardLbl.title = 'muss exakt getroffen werden (fuer spaetere Fahrfehler-Analyse) statt weicher, zeitoptimaler Zielwert';
      const hardCb = document.createElement('input');
      hardCb.type = 'checkbox';
      hardCb.checked = !!e.hard;
      hardCb.addEventListener('change', ()=>{
        e.hard = hardCb.checked;
        setStatus('neu berechnen ...');
        setTimeout(()=>{
          nLat = recomputeFromEdits(edits);
          render();
          setStatus('');
        }, 10);
      });
      hardLbl.appendChild(hardCb);
      hardLbl.appendChild(document.createTextNode('hart'));
      const del = document.createElement('button');
      del.className = 'action del';
      del.textContent = '×';
      del.title = 'diese Bearbeitung entfernen';
      del.addEventListener('click', ()=>deleteEdit(i));
      row.appendChild(lbl);
      row.appendChild(hardLbl);
      row.appendChild(del);
      container.appendChild(row);
    });
  }

  function deleteEdit(i){
    edits.splice(i, 1);
    setStatus('neu berechnen ...');
    setTimeout(()=>{
      nLat = recomputeFromEdits(edits);
      render();
      setStatus('');
    }, 10);
  }

  function renderShiftsList(){
    const container = document.getElementById('shiftsList');
    container.innerHTML = '';
    const events = computeShiftEvents(lastSim);
    if (events.length === 0){
      const p = document.createElement('div');
      p.className = 'note';
      p.textContent = 'Keine Schaltvorgaenge auf dieser Linie.';
      container.appendChild(p);
      return;
    }
    events.forEach(ev=>{
      const row = document.createElement('div');
      row.className = 'edit-row';
      const lbl = document.createElement('span');
      lbl.innerHTML = `<span class="marker-dot" style="background:rgba(197,150,255,0.95); transform:none; border-radius:1px; width:0.7rem; height:0.35rem;"></span>`
        + `Gang ${ev.fromGear}→${ev.toGear} bei s=${Math.round(ev.s_m)}m`;
      row.appendChild(lbl);
      container.appendChild(row);
    });
  }

  function render(){
    lastPts = pointsFromNLat(nLat);
    lastSim = simulateLap(lastPts, P.MU);
    draw();
    drawTrace();
    updateReadouts();
    renderEditsList();
    renderShiftsList();
    saveEditsToStorage();
  }

  document.getElementById('naturalThrottleCb').addEventListener('change', (e)=>{
    naturalThrottleMode = e.target.checked;
    setStatus(naturalThrottleMode ? 'natuerliches Gasmodell aktiv ...' : 'zeitoptimales Gasmodell ...');
    setTimeout(()=>{ render(); setStatus(''); }, 10);
  });

  // ---------- Interaktion ----------
  let brushRadiusM = OPT.brush_radius_m_default;
  const brushSlider = document.getElementById('brushRadius');
  brushSlider.value = brushRadiusM;
  document.getElementById('brushLabel').textContent = brushRadiusM.toFixed(0) + ' m';
  brushSlider.addEventListener('input', (e)=>{
    brushRadiusM = parseFloat(e.target.value);
    document.getElementById('brushLabel').textContent = brushRadiusM.toFixed(0) + ' m';
  });

  function nearestIndex(worldX, worldY){
    let best=-1, bestD=Infinity;
    for (let i=0;i<N;i++){
      const dx=lastPts[i][0]-worldX, dy=lastPts[i][1]-worldY;
      const d=dx*dx+dy*dy;
      if (d<bestD){ bestD=d; best=i; }
    }
    return {index:best, dist:Math.sqrt(bestD)};
  }

  function setStatus(msg){ document.getElementById('statusLine').textContent = msg; }

  function targetLatOffset(index, worldX, worldY){
    const dx=worldX-CENTER[index][0], dy=worldY-CENTER[index][1];
    let val = dx*PERP[index][0] + dy*PERP[index][1];
    return Math.min(Math.max(val, LO[index]), HI[index]);
  }

  function pixelToHitRadiusM(){
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    return 16/(baseScaleFor(w,h)*view.zoom); // 16px Trefferradius, in Meter umgerechnet
  }

  let nLatAtDragStart = null;
  let panning = false;
  let panStart = null;

  function pointerPos(e){
    const rect = trackCanvas.getBoundingClientRect();
    return [e.clientX-rect.left, e.clientY-rect.top];
  }

  trackCanvas.addEventListener('pointerdown', (e)=>{
    const [px,py] = pointerPos(e);
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    const [wx,wy] = unproject(px,py,w,h);
    const {index,dist} = nearestIndex(wx,wy);
    const hitR = pixelToHitRadiusM();
    trackCanvas.setPointerCapture(e.pointerId);
    hideHoverTooltip();
    if (dist <= hitR){
      dragging = true; dragIndex = index;
      nLatAtDragStart = nLat.slice();
      setStatus('ziehen ...');
    } else {
      // Klick auf leeren Bereich (nicht nah genug an der Linie) -> Verschieben
      // der Ansicht statt Punkt-Bearbeitung.
      panning = true;
      panStart = {px, py, panX: view.panX, panY: view.panY};
      trackCanvas.style.cursor = 'grabbing';
    }
  });

  trackCanvas.addEventListener('pointermove', (e)=>{
    const [px,py] = pointerPos(e);
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    if (dragging){
      const [wx,wy] = unproject(px,py,w,h);
      const target = targetLatOffset(dragIndex, wx, wy);
      nLat = applyBrush(nLatAtDragStart, dragIndex, target, brushRadiusM);
      lastPts = pointsFromNLat(nLat);
      lastSim = simulateLap(lastPts, P.MU);
      draw();
      drawTrace();
      updateReadouts();
      return;
    }
    if (panning){
      view.panX = panStart.panX + (px-panStart.px);
      view.panY = panStart.panY + (py-panStart.py);
      draw();
      return;
    }
    // reine Hover-Rueckmeldung (Cursor + Tooltip mit naechsten Fixpunkten),
    // keine Interaktion aktiv
    const [wx,wy] = unproject(px,py,w,h);
    const {index, dist} = nearestIndex(wx,wy);
    if (dist <= pixelToHitRadiusM()){
      trackCanvas.style.cursor = 'pointer';
      showHoverTooltip(px, py, index);
    } else {
      trackCanvas.style.cursor = 'grab';
      hideHoverTooltip();
    }
  });
  trackCanvas.addEventListener('pointerleave', hideHoverTooltip);

  // Distanz zweier Punktindizes entlang der geschlossenen Strecke in Metern.
  function circularGapM(i, j){
    const d = Math.abs(i-j);
    return Math.min(d, N-d) * P.RESAMPLE_STEP_M;
  }

  function showHoverTooltip(px, py, index){
    const tip = document.getElementById('hoverTooltip');
    const sVal = lastSim.s[index];
    const nearby = edits
      .filter(e => e.index !== index)
      .map(e => ({ index: e.index, gapM: circularGapM(index, e.index) }))
      .sort((a,b) => a.gapM - b.gapM)
      .slice(0, 3);
    let html = `Punkt ${index} &middot; s=${sVal.toFixed(0)}m`;
    if (nearby.length){
      html += '<br>' + nearby.map(nb => {
        const cls = nb.gapM <= NEARBY_HINT_M ? 'chainable' : 'nearest';
        const note = nb.gapM <= NEARBY_HINT_M ? ' (beeinflusst sich)' : '';
        return `<span class="${cls}">Fixpunkt #${nb.index} &middot; ${nb.gapM.toFixed(0)}m${note}</span>`;
      }).join('<br>');
    }
    tip.innerHTML = html;
    tip.style.display = 'block';
    const wrap = trackCanvas.parentElement;
    let left = px + 14, top = py + 14;
    if (left + tip.offsetWidth > wrap.clientWidth) left = px - tip.offsetWidth - 14;
    if (top + tip.offsetHeight > wrap.clientHeight) top = py - tip.offsetHeight - 14;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }

  function hideHoverTooltip(){
    document.getElementById('hoverTooltip').style.display = 'none';
  }

  trackCanvas.addEventListener('wheel', (e)=>{
    e.preventDefault();
    const [px,py] = pointerPos(e);
    const factor = Math.exp(-e.deltaY*0.0015);
    zoomAt(px, py, factor);
  }, {passive:false});

  trackCanvas.addEventListener('dblclick', (e)=>{
    const [px,py] = pointerPos(e);
    zoomAt(px, py, 1.8);
  });

  function endDrag(e){
    if (panning){
      panning = false;
      trackCanvas.style.cursor = 'grab';
      return;
    }
    if (!dragging) return;
    dragging = false;
    const finalTarget = nLat[dragIndex];
    const idx = dragIndex;
    // erneutes Ziehen eines bereits bearbeiteten Punktes: Wert aktualisieren
    // und ans Ende der Historie ruecken, statt einen zweiten Eintrag fuer
    // denselben Punkt anzulegen. Nah beieinanderliegende, aber verschiedene
    // Fixpunkte werden NICHT mehr automatisch geloescht/zusammengelegt - die
    // gemeinsame querkraftbasierte Relaxation (siehe pinnedOptimize) kommt
    // mit beliebig dicht stehenden weichen Zielpunkten klar, das war nur eine
    // Notloesung fuer die inzwischen abgeloeste rein geometrische Kettenglaettung.
    const existing = edits.findIndex(e2 => e2.index === idx);
    const hardFlag = existing >= 0 ? !!edits[existing].hard : false;
    if (existing >= 0) edits.splice(existing, 1);
    edits.push({index: idx, value: finalTarget, radius: brushRadiusM, hard: hardFlag});
    dragIndex = null;
    setStatus('optimiere ...');
    // kurze Verzoegerung, damit "optimiere ..." sichtbar aufblitzt (Relaxation
    // selbst dauert nur ~50-150ms, siehe Docstring)
    setTimeout(()=>{
      nLat = recomputeFromEdits(edits);
      render();
      setStatus('');
    }, 10);
  }
  trackCanvas.addEventListener('pointerup', endDrag);
  trackCanvas.addEventListener('pointercancel', endDrag);

  document.getElementById('undoBtn').addEventListener('click', ()=>{
    if (edits.length===0) return;
    edits.pop();
    setStatus('neu berechnen ...');
    setTimeout(()=>{
      nLat = recomputeFromEdits(edits);
      render();
      setStatus('');
    }, 10);
  });
  document.getElementById('resetBtn').addEventListener('click', ()=>{
    edits = [];
    nLat = Float64Array.from(nLatBaseline);
    render();
  });

  document.getElementById('exportBtn').addEventListener('click', async ()=>{
    const json = JSON.stringify(edits);
    const box = document.getElementById('exportBox');
    try {
      await navigator.clipboard.writeText(json);
      setStatus('Bearbeitungen in Zwischenablage kopiert');
    } catch (e) {
      box.style.display = 'block';
      box.value = json;
      box.select();
    }
  });

  document.getElementById('importBtn').addEventListener('click', ()=>{
    document.getElementById('exportBox').style.display = 'none';
    const box = document.getElementById('importBox');
    box.style.display = 'block';
    box.value = '';
    document.getElementById('importActions').style.display = 'flex';
    setStatus('');
    box.focus();
  });
  document.getElementById('importCancelBtn').addEventListener('click', ()=>{
    document.getElementById('importBox').style.display = 'none';
    document.getElementById('importActions').style.display = 'none';
    setStatus('');
  });
  document.getElementById('importApplyBtn').addEventListener('click', ()=>{
    const raw = document.getElementById('importBox').value.trim();
    if (!raw){ setStatus('Fehler: Feld ist leer'); return; }
    let parsed;
    try { parsed = JSON.parse(raw); }
    catch(e){ setStatus('Fehler: kein gueltiges JSON'); return; }
    const valid = validateEditsArray(parsed);
    if (!valid){ setStatus('Fehler: erwarte ein JSON-Array von Punkten'); return; }
    if (valid.length === 0){ setStatus('Fehler: keine gueltigen Punkte gefunden (index/value pruefen)'); return; }
    edits = valid;
    document.getElementById('importBox').style.display = 'none';
    document.getElementById('importActions').style.display = 'none';
    setStatus('optimiere ...');
    setTimeout(()=>{
      nLat = recomputeFromEdits(edits);
      render();
      setStatus(`${edits.length} Punkte importiert`);
    }, 10);
  });

  document.getElementById('zoomInBtn').addEventListener('click', ()=>{
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    zoomAt(w/2, h/2, 1.4);
  });
  document.getElementById('zoomOutBtn').addEventListener('click', ()=>{
    const w=trackCanvas.clientWidth, h=trackCanvas.clientHeight;
    zoomAt(w/2, h/2, 1/1.4);
  });
  document.getElementById('zoomResetBtn').addEventListener('click', ()=>{
    view.zoom = 1; view.panX = 0; view.panY = 0;
    draw();
  });

  window.addEventListener('resize', resizeCanvas);
  resizeCanvas();
  render();
  if (document.fonts && document.fonts.ready){
    document.fonts.ready.then(()=>requestAnimationFrame(render));
  }

  // Read-only Debug-Zugriff (keine Seiteneffekte) - nuetzlich, um den
  // internen Zustand ohne Umwege ueber die Browser-Konsole zu pruefen.
  window.__editorDebug = {
    getPoints: () => lastPts,
    getEdits: () => edits,
    getNLat: () => Array.from(nLat),
    getSim: () => lastSim,
    getSteerRate: () => Array.from(computeSteerRateDegS(lastPts, lastSim.radius, lastSim.vFinal, lastSim.ds)),
    getCenterline: () => data.centerline,
    getPerp: () => data.perp,
    getLoHi: () => ({lo: LO, hi: HI}),
    project: (p) => project(p, trackCanvas.clientWidth, trackCanvas.clientHeight),
    clientRect: () => trackCanvas.getBoundingClientRect(),
    // Zeigt den Zustand VOR der Einzelpunkt-Glaettung (siehe
    // smoothSinglePointNotches) - zum Abstimmen von MIN_NOTCH_STEER_DEG/
    // MAX_NOTCH_RADIUS_M in findRadiusNotches() gegen die echten Streckendaten.
    testNotches: () => {
      if (!lastPreNotchNLat) return null;
      const pts = pointsFromNLat(lastPreNotchNLat);
      const ds = new Float64Array(N);
      for (let i=0;i<N;i++){ const j=(i+1)%N; ds[i]=Math.hypot(pts[j][0]-pts[i][0], pts[j][1]-pts[i][1]); }
      const radius = curvatureRadius(pts, P.CURVATURE_WINDOW_M, median(Array.from(ds)));
      return { flagged: findRadiusNotches(lastPreNotchNLat), radius: Array.from(radius) };
    },
    // Pro-Punkt-Tabelle fuer den Excel-Export (spreewaldring_simulierte_runde.xlsx)
    // - dieselbe Gas/Bremse/Lenkwinkel-Logik wie updateReadouts() (siehe dort),
    // nur fuer ALLE Punkte statt nur den aktuellen Zeitpunkt.
    getTelemetryTable: () => {
      const sim = lastSim, pts = lastPts;
      const wSign = Math.max(1, Math.round(P.CURVATURE_WINDOW_M/P.RESAMPLE_STEP_M));
      const rows = [];
      for (let i=0;i<N;i++){
        const prevI = (i-1+N)%N;
        const isCoasting = !!sim.coasting[i];
        const braking = !isCoasting && sim.vFinal[i] < sim.vFwd[i]-1e-3;
        const accelerating = !braking && !isCoasting && sim.vFinal[i] > sim.vFinal[prevI]+1e-6;
        const brakePct = braking ? Math.round((sim.brakeFrac[i] ?? 1)*100) : 0;
        const throttlePct = sim.naturalMode
          ? (braking ? 0 : Math.round((sim.throttleFracNatural[i] ?? 0)*100))
          : (accelerating ? 100 : (isCoasting ? Math.round(sim.throttleFrac[i]*100) : 0));
        const p0 = pts[(i-wSign+N)%N], p1 = pts[i], p2 = pts[(i+wSign)%N];
        const cross = (p1[0]-p0[0])*(p2[1]-p0[1]) - (p2[0]-p0[0])*(p1[1]-p0[1]);
        const turnSign = cross > 0 ? -1 : (cross < 0 ? 1 : 0);
        const lenkwinkelDeg = Math.atan(WHEELBASE_M/Math.max(sim.radius[i],1)) * 180/Math.PI * turnSign;
        const aLatG = (sim.vFinal[i]**2 / Math.max(sim.radius[i],1e-6)) / P.G;
        rows.push({i, s: sim.s[i], vKmh: sim.vFinal[i]*3.6, gasPct: throttlePct, bremsePct: brakePct, lenkwinkelDeg, gang: sim.gear[i], aLatG});
      }
      return { lapTime: sim.lapTime, naturalMode: sim.naturalMode, rows };
    },
    // Rundenzeit-Gegenprobe: erzwingt appFrac an einem oder mehreren
    // Streckenindizes im natuerlichen Gasmodell (siehe naturalStep/
    // debugForceAppFrac), dann neu simulieren. overrides=null oder []
    // hebt den Override wieder auf. overrides: [{index, value}, ...].
    setDebugForceAppFrac: (overrides) => {
      debugForceAppFrac = (!overrides || overrides.length===0) ? null : overrides;
      render();
      return lastSim.lapTime;
    },
    // Manuelles Voranschreiten des Start-Sprints ohne echte requestAnimationFrame-
    // Zeitbasis (fuer Tests in Umgebungen ohne sichtbaren Tab) - treibt exakt
    // denselben Code wie simFrame(), nur zeitgesteuert statt frame-getrieben.
    getPreSmoothV: () => lastPreSmoothV ? Array.from(lastPreSmoothV) : null,
    getMidSmoothV: () => lastMidSmoothV ? Array.from(lastMidSmoothV) : null,
    tickStandingStart: (dtSeconds, steps) => {
      for (let k=0; k<(steps||1) && standingStart; k++) stepStandingStart(dtSeconds);
      return standingStart ? {...standingStart, active:true} : {active:false, t: sim_.t};
    },
  };
})();
</script>
"""


if __name__ == "__main__":
    main()
