"""
GPS-Kreuzcheck fuer CAN-Gierrate + Lenkwinkel-Nullpunkt-Korrektur fuer das
CAN-Log vom 2026-09-11 (candump-2026-09-11_202803.log + zugehoeriges GPX).

Zwei Teile:
1. Grober Plausibilitaetscheck: stimmen Kurven im GPS-Track zeitlich mit
   Gierraten-Peaks im CAN-Log (0x75 YawRate_Raw) ueberein?
2. Lenkwinkel-Nullpunkt (0x82 Steering_Wheel_Absolute_Angle) nach demselben
   Verfahren wie steering_zero_offset.py fuer STEER_ANGL_EPS: Fenster auf
   nachweislich geraden OSM-Strassenabschnitten suchen (unabhaengig vom
   moeglicherweise verschobenen Lenkwinkel selbst) und den Median des
   Lenkwinkels in diesen Fenstern als Nullpunkt-Schaetzung nehmen. Nutzt
   denselben OSM-Cache (GPS-BBox dieser Fahrt liegt innerhalb der bereits
   gecachten CORRIDOR_BBOX).
"""
import sys
import datetime
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

sys.path.insert(0, "scripts")
from can_log_parser import load_db, parse_candump
from steering_zero_offset import (
    get_or_build_road_cache, make_projector, build_straight_point_cloud,
    group_runs, CORRIDOR_BBOX, MATCH_TOLERANCE_M, MIN_SPEED_MS, MIN_RUN_S,
    MIN_SAMPLES_FOR_OFFSET,
)

CAN_LOG = "data/can/candump-2026-09-11_202803.log"
GPX_PATH = "data/can/20260911-202812.gpx"
GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/0"}


def load_gpx(path):
    tree = ET.parse(path)
    root = tree.getroot()
    rows = []
    for trkpt in root.iter("{http://www.topografix.com/GPX/1/0}trkpt"):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        time_el = trkpt.find("gpx:time", GPX_NS)
        t = datetime.datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
        rows.append((t.timestamp(), lat, lon))
    df = pd.DataFrame(rows, columns=["t_epoch", "lat", "lon"])
    return df


def gps_yaw_rate(df):
    """Kursaenderung zwischen aufeinanderfolgenden Punkten / dt -> Gierrate-Schaetzung (deg/s)."""
    lat = np.radians(df.lat.values)
    lon = np.radians(df.lon.values)
    dlat = np.diff(lat)
    dlon = np.diff(lon)
    lat0 = lat[:-1]
    bearing = np.degrees(np.arctan2(
        np.sin(dlon) * np.cos(lat[1:]),
        np.cos(lat0) * np.sin(lat[1:]) - np.sin(lat0) * np.cos(lat[1:]) * np.cos(dlon),
    ))
    dt = np.diff(df.t_epoch.values)
    dbearing = (np.diff(bearing, prepend=bearing[0]) + 180) % 360 - 180
    # dbearing hat einen Punkt weniger Ausrichtung als bearing selbst - grob genug fuer den Plausibilitaetscheck
    yaw = np.zeros(len(df))
    yaw[1:-1] = dbearing[1:] / dt[1:]
    return yaw


def part1_yawrate_crosscheck(gpx, can_db_path_msg, raw):
    print("=== Teil 1: GPS-Kurven vs. CAN-Gierrate ===")
    gpx = gpx.copy()
    gpx["yaw_gps"] = gps_yaw_rate(gpx)

    yaw_can = raw[raw.can_id == 0x75].copy()
    msg = can_db_path_msg
    yaw_can["yaw_can"] = yaw_can.data.apply(lambda b: msg.decode(b, allow_truncated=True).get("YawRate_Raw"))
    yaw_can = yaw_can.dropna(subset=["yaw_can"])
    yaw_can["t_epoch"] = yaw_can["t"]  # bereits absolute epoch-Zeit aus parse_candump

    # groebstes gemeinsames Zeitraster: 1s-Fenster, jeweils Extremwert (groesster Ausschlag)
    gpx["t_bin"] = gpx.t_epoch.round(0)
    yaw_can["t_bin"] = yaw_can.t_epoch.round(0)
    gps_1s = gpx.groupby("t_bin").yaw_gps.apply(lambda s: s.loc[s.abs().idxmax()])
    can_1s = yaw_can.groupby("t_bin").yaw_can.apply(lambda s: s.loc[s.abs().idxmax()])
    merged = pd.merge(gps_1s.rename("gps"), can_1s.rename("can"), left_index=True, right_index=True, how="inner")

    corr = merged.gps.corr(merged.can)
    print(f"Ueberlappende 1s-Fenster: {len(merged)}")
    print(f"Korrelation GPS-Gierrate (grob) vs. CAN YawRate_Raw: {corr:.3f}")

    # groesste 5 CAN-Gierraten-Peaks und ob GPS zur gleichen Zeit auch was zeigt
    top = merged.reindex(merged.can.abs().sort_values(ascending=False).index[:5])
    print("\nGroesste 5 CAN-Gierraten-Peaks (Zeitpunkt, CAN deg/s, GPS grob deg/s):")
    for t_bin, row in top.iterrows():
        print(f"  {datetime.datetime.utcfromtimestamp(t_bin).strftime('%H:%M:%S')} UTC  "
              f"CAN={row['can']:+7.1f}  GPS(grob)={row['gps']:+7.1f}")
    return merged


def part2_steering_offset(gpx, msg82, raw):
    print("\n=== Teil 2: Lenkwinkel-Nullpunkt (0x82) via geraden OSM-Strassen ===")
    ways = get_or_build_road_cache(CORRIDOR_BBOX)
    lat0 = (CORRIDOR_BBOX[0] + CORRIDOR_BBOX[2]) / 2
    project = make_projector(lat0)
    straight_points = build_straight_point_cloud(ways, project)
    print(f"{len(straight_points)} 'gerade' OSM-Strassenpunkte im Korridor-Cache")
    straight_tree = cKDTree(straight_points)

    # GPS-Geschwindigkeit grob aus Positionsaenderung (kein eigener Speed-Kanal im GPX)
    gpx = gpx.sort_values("t_epoch").reset_index(drop=True)
    lat, lon, t = gpx.lat.values, gpx.lon.values, gpx.t_epoch.values
    x, y = project(lat, lon)
    dist_m = np.hypot(np.diff(x), np.diff(y))
    dt = np.diff(t)
    v_ms = np.zeros(len(gpx))
    v_ms[1:] = dist_m / np.where(dt == 0, np.nan, dt)

    dist_to_straight, _ = straight_tree.query(np.column_stack([x, y]))
    candidate = (dist_to_straight < MATCH_TOLERANCE_M) & (v_ms > MIN_SPEED_MS)
    runs = group_runs(t, candidate, MIN_RUN_S)
    print(f"{len(runs)} zusammenhaengende Geradeausfahrt-Fenster gefunden "
          f"({candidate.sum()} Kandidaten-Punkte vor Gruppierung)")
    if not runs:
        print("Keine bestaetigten Geradeausfahrt-Fenster - keine Nullpunkt-Schaetzung moeglich.")
        return

    steer = raw[raw.can_id == 0x82].copy()
    steer["angle"] = steer.data.apply(lambda b: msg82.decode(b, allow_truncated=True).get("Steering_Wheel_Absolute_Angle"))
    steer = steer.dropna(subset=["angle"])
    steer_t = steer.t.values
    steer_v = steer.angle.values

    samples = []
    windows = []
    for i0, i1 in runs:
        t0, t1 = t[i0], t[i1]
        m = (steer_t >= t0) & (steer_t <= t1)
        if m.sum() == 0:
            continue
        samples.append(steer_v[m])
        windows.append((t1 - t0, int(m.sum())))

    if not samples:
        print("Keine Lenkwinkel-Samples in den Geradeaus-Fenstern gefunden.")
        return

    all_samples = np.concatenate(samples)
    n = len(all_samples)
    print(f"\n{n} Lenkwinkel-Samples aus {len(windows)} Fenstern "
          f"({sum(w[0] for w in windows):.0f}s gesamt)")
    print(f"Nullpunkt-Schaetzung: Median={np.median(all_samples):+.1f}  "
          f"Mittel={np.mean(all_samples):+.1f}  Std={np.std(all_samples):.1f}")
    print(f"Vertrauenswuerdig (>= {MIN_SAMPLES_FOR_OFFSET} Samples): {n >= MIN_SAMPLES_FOR_OFFSET}")
    return np.median(all_samples)


if __name__ == "__main__":
    db = load_db()
    msg75 = db.get_message_by_frame_id(0x75)
    msg82 = db.get_message_by_frame_id(0x82)

    raw = parse_candump(CAN_LOG)  # raw.t ist bereits absolute epoch-Zeit
    gpx = load_gpx(GPX_PATH)

    part1_yawrate_crosscheck(gpx, msg75, raw)
    part2_steering_offset(gpx, msg82, raw)
