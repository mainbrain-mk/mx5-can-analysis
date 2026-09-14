"""
Interaktive Leaflet-Karte: GPS-Route eines Logs mit den bestaetigten Kurven
(corner_event_analysis.py) als nummerierte, nach Richtung/a_lat eingefaerbte
Marker. Selbststaendige lokale HTML-Datei (file://-kompatibel, siehe
leaflet_local_html_tiles-Memory: Esri-Tiles statt OSM/CARTO, die von
file:// aus blockiert werden).

Aufruf: python scripts/plot_track_corners.py <log_id>
(log_id wie in der DuckDB, z.B. "2026-09-12 211851")
"""
import sys
import json
import duckdb
import numpy as np

DB_PATH = "data/datalake.duckdb"
CORNER_SUMMARY_PATH = "results/corner_event_summary.json"


def build_data(log_id, can_corner_path=None, can_time_offset_s=0.0):
    """can_corner_path: optional Pfad zu einem can_corner_event_analysis.py-
    JSON (CAN-native Kurven, eigene Zeitbasis) statt des Standard-
    corner_event_summary.json (OBD/GPS-Kurven, Zeitbasis = log_id selbst).
    can_time_offset_s: t_can = t_obd + offset (siehe can_lateral_validation.py),
    zum Umrechnen der CAN-Zeitstempel auf die GPS-Zeitbasis des log_id."""
    con = duckdb.connect(DB_PATH, read_only=True)
    lat = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = 'Breite' ORDER BY t",
        [log_id]).fetchdf()
    lon = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = 'Länge' ORDER BY t",
        [log_id]).fetchdf()
    speed = con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = 'VehicleSpeed' ORDER BY t",
        [log_id]).fetchdf()
    if lat.empty or lon.empty:
        raise SystemExit(f"Kein GPS (Breite/Länge) fuer log_id={log_id!r} im Datalake")

    t = lat["t"].values
    lat_v = lat["value"].values
    lon_v = np.interp(t, lon["t"].values, lon["value"].values)
    speed_v = np.interp(t, speed["t"].values, speed["value"].values) if not speed.empty else np.zeros_like(t)
    route = [{"t": float(tt), "lat": float(la), "lon": float(lo), "v": float(v)}
             for tt, la, lo, v in zip(t, lat_v, lon_v, speed_v)]

    corners = []
    if can_corner_path:
        with open(can_corner_path, encoding="utf-8") as f:
            can_data = json.load(f)
        for ev in can_data["events"]:
            ts, te = ev["t_start"] - can_time_offset_s, ev["t_end"] - can_time_offset_s
            tm = (ts + te) / 2
            corners.append({
                "t_start": ts, "t_end": te,
                "lat": float(np.interp(tm, t, lat_v)), "lon": float(np.interp(tm, t, lon_v)),
                "direction": ev["direction"], "a_lat_g": ev["a_lat_peak_g"],
                "speed_kmh": ev["speed_mean_kmh"],
            })
    else:
        with open(CORNER_SUMMARY_PATH, encoding="utf-8") as f:
            corner_data = json.load(f)
        matches = [e for e in corner_data if log_id in e["file"]]
        if matches:
            for ev in matches[0]["events"]:
                tm = (ev["t_start"] + ev["t_end"]) / 2
                corners.append({
                    "t_start": ev["t_start"], "t_end": ev["t_end"],
                    "lat": float(np.interp(tm, t, lat_v)), "lon": float(np.interp(tm, t, lon_v)),
                    "direction": ev["direction"], "a_lat_g": ev["a_lat_mean_g"],
                    "speed_kmh": ev["speed_mean_kmh"],
                })
    corners.sort(key=lambda c: c["t_start"])
    return {"log_id": log_id, "route": route, "corners": corners}


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>Track + Kurven — {log_id}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body {{ margin: 0; height: 100%; font-family: sans-serif; }}
  #map {{ height: 100%; }}
  .legend {{ background: white; padding: 8px 10px; border-radius: 4px; box-shadow: 0 1px 4px rgba(0,0,0,.3); font-size: 13px; }}
  .legend div {{ margin: 2px 0; }}
  .swatch {{ display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:6px; vertical-align:middle; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
const data = {data_json};

const map = L.map('map');
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  attribution: 'Tiles &copy; Esri', maxZoom: 19
}}).addTo(map);

const routeLatLngs = data.route.map(p => [p.lat, p.lon]);
const routeLine = L.polyline(routeLatLngs, {{ color: '#3388ff', weight: 3, opacity: 0.7 }}).addTo(map);
map.fitBounds(routeLine.getBounds(), {{ padding: [20, 20] }});

data.corners.forEach((c, i) => {{
  const color = c.direction === 'rechts' ? '#e6550d' : '#3182bd';
  const radius = 6 + Math.min(Math.abs(c.a_lat_g), 1.0) * 10;
  const marker = L.circleMarker([c.lat, c.lon], {{
    radius: radius, color: color, fillColor: color, fillOpacity: 0.7, weight: 2,
  }}).addTo(map);
  marker.bindPopup(
    `<b>Kurve ${{i + 1}} (${{c.direction}})</b><br>` +
    `t=${{c.t_start.toFixed(1)}}-${{c.t_end.toFixed(1)}}s<br>` +
    `a_lat=${{c.a_lat_g.toFixed(2)}}g<br>` +
    `v=${{c.speed_kmh.toFixed(0)}} km/h`
  );
  marker.bindTooltip(String(i + 1), {{ permanent: true, direction: 'center', className: 'corner-label' }});
}});

const legend = L.control({{ position: 'topright' }});
legend.onAdd = function() {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = `<b>${{data.log_id}}</b><br>${{data.corners.length}} bestätigte Kurven<br>` +
    `<div><span class="swatch" style="background:#e6550d"></span>rechts</div>` +
    `<div><span class="swatch" style="background:#3182bd"></span>links</div>` +
    `<div>Kreisgröße ~ |a_lat|</div>`;
  return div;
}};
legend.addTo(map);
</script>
</body>
</html>
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Aufruf: python scripts/plot_track_corners.py <log_id> [can_corner_json] [can_time_offset_s]")
    log_id = sys.argv[1]
    can_corner_path = sys.argv[2] if len(sys.argv) > 2 else None
    can_offset = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    data = build_data(log_id, can_corner_path, can_offset)
    suffix = "_can" if can_corner_path else ""
    out_path = f"results/track_corners_{log_id.replace(' ', '_').replace(':', '')}{suffix}.html"
    html = HTML_TEMPLATE.format(log_id=log_id, data_json=json.dumps(data))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{len(data['route'])} Routenpunkte, {len(data['corners'])} Kurven")
    print(f"Karte: {out_path}")


if __name__ == "__main__":
    main()
