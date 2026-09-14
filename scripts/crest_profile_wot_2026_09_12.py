"""Hochaufgeloestes Hoehenprofil der Kuppe aus der bidirektionalen Vollgas-
Fahrt (2026-09-12, siehe mx5_can_bus_status.md / mx5_bidirectional_wot_
validation.md), fuer eine praezisere Vmax-Validierung als die bisherige
Ein-Wert-Gefaellekorrektur pro Segment (top_speed_validation.segment_grade()).

Nutzt die volle native Punktdichte der ALS-Rohdaten (~12-14 Bodenpunkte/m2,
mittlerer Punktabstand ~0.27-0.29m - deutlich feiner als das global genutzte
1m-Cache-Raster von elevation_model.py) statt des vorhandenen 1m-Kachel-Caches.
Baut daraus ein feines (1m-Bins), robustes (Median je Bin) Hoehenprofil ENTLANG
der tatsaechlich gefahrenen Strecke (Fahrbahnkorridor, kein flaechendeckendes
Raster) - viel praeziser als eine 1Hz-GPS-Interpolation gegen ein 1m-Raster.

Ergebnis: data/elevation/crest_profile_2026-09-12_wot.npz mit
  s_m: Bogenlaenge entlang der gefahrenen Strecke [m]
  elev_m: robustes Hoehenprofil (Median je 1m-Bin, geglaettet)
  grade: lokale Steigung (dElev/ds, geglaettet)
sowie eine Fit-Funktion (Centerline) um beliebige (lat,lon) auf s zu projizieren.
"""
import io
import zipfile
import sys

sys.path.insert(0, "scripts")
import duckdb
import laspy
import numpy as np
import pandas as pd
import pyproj

from elevation_model import BRIDGE_BUFFER_M
from top_speed_validation import _trip_bridges, is_near_bridge_latlon, _LATLON_TO_UTM33
from drivetrain_model_validation import load_channel

HOEHENDATEN_DIR = "höhendaten"
OUT_PATH = "data/elevation/crest_profile_2026-09-12_wot.npz"
OBD_LOG_ID = "2026-09-12 211851"
CORRIDOR_HALF_WIDTH_M = 8.0    # seitlicher Streifen um die Fahrspur (siehe unten:
                                # 20m fing an mehreren Stellen eine zweite Flaeche
                                # ein - vermutlich Gegenfahrbahn/Rampe bei leichter
                                # Kruemmung, die der quadratische Centerline-Fit
                                # nicht exakt trifft - 8m bleibt sicher auf der
                                # eigenen Fahrspur, bei ~12 Pkt/m2 immer noch
                                # tausende Punkte pro Bin)
BIN_SIZE_M = 1.0               # Bin-Breite entlang der Strecke (native Punktdichte traegt das locker)
SMOOTH_WINDOW_M = 60.0         # Glaettung fuer die Steigungsableitung (kurzwellige ALS-/Bin-Rauschanteile raus,
                                # Kuppenform (~1km Halbwellenlaenge) bleibt erhalten)

_UTM33_TO_LATLON = pyproj.Transformer.from_crs("EPSG:25833", "EPSG:4326", always_xy=True)

TILE_KMS_E = [372, 373, 374, 375, 376, 377]
TILE_KM_N = 5841


def load_ground_points(e_km, n_km):
    zpath = f"{HOEHENDATEN_DIR}/als_33{e_km}-{n_km}.zip"
    with zipfile.ZipFile(zpath) as z:
        laz_name = [n for n in z.namelist() if n.lower().endswith(".laz")][0]
        with z.open(laz_name) as f:
            data = io.BytesIO(f.read())
    las = laspy.read(data)
    ground = np.asarray(las.classification) == 2
    return np.asarray(las.x)[ground], np.asarray(las.y)[ground], np.asarray(las.z)[ground]


def build_profile(name, t0, t1, con, lat, lon, all_e, all_n, all_z):
    """Eigene Mittellinie + eigener Korridor PRO Fahrtrichtung (statt einer
    gemeinsamen Mittellinie fuer beide) - eine gemeinsame Linie zieht den
    Korridor bei leichter seitlicher Kruemmung zwischen die beiden echten
    Fahrspuren und faengt dann teils die GEGENFAHRBAHN/Rampen mit ein (siehe
    Docstring-Update: 20m-Korridor+gemeinsame Linie zeigte an mehreren Stellen
    eine deutlich bimodale Hoehenverteilung - zwei Flaechen im selben Fenster,
    kein Kuppen-Rauschen). Die eigene GPS-Spur jeder Richtung bleibt dagegen
    auf der jeweils tatsaechlich befahrenen Spur."""
    m = (lat["t"] >= t0) & (lat["t"] <= t1)
    sub_t = lat.loc[m, "t"].values
    sub_lat = lat.loc[m, "value"].values
    sub_lon = np.interp(sub_t, lon["t"].values, lon["value"].values)
    ref_e, ref_n = _LATLON_TO_UTM33.transform(sub_lon, sub_lat)
    order = np.argsort(ref_e)
    ref_e, ref_n = ref_e[order], ref_n[order]
    print(f"\n=== {name}: {len(ref_e)} GPS-Referenzpunkte, e={ref_e.min():.0f}-{ref_e.max():.0f} ===")

    # Mittellinie: stueckweise LINEAR durch die eigenen GPS-Punkte dieser
    # Richtung (statt ein globales Polynom - ein Polynom durch nur ~65-90
    # Punkte ueber 3-5km hat sich als zu unflexibel erwiesen, um die
    # tatsaechliche Spurfuehrung zu treffen: erste Version (Polynom 2. Grades)
    # gab fuer WEST einen Kuppenscheitel an einer anderen Stelle+Hoehe als der
    # unabhaengig ermittelte OST-Wert - stueckweise linear zwischen echten
    # GPS-Fixen (~1Hz, ~50-60m Punktabstand bei diesem Tempo) folgt der
    # tatsaechlichen Spur enger, ohne zu ueberfitten).
    def centerline_n(e_query):
        return np.interp(e_query, ref_e, ref_n)

    e_lo, e_hi = ref_e.min() - 50, ref_e.max() + 50
    in_range = (all_e >= e_lo) & (all_e <= e_hi)
    e_c, n_c, z_c = all_e[in_range], all_n[in_range], all_z[in_range]
    lateral = n_c - centerline_n(e_c)
    corridor = np.abs(lateral) <= CORRIDOR_HALF_WIDTH_M
    e_c, n_c, z_c = e_c[corridor], n_c[corridor], z_c[corridor]
    print(f"{len(e_c):,} Bodenpunkte im {CORRIDOR_HALF_WIDTH_M:.0f}m-Fahrbahnkorridor "
          f"(Dichte: {len(e_c)/((e_hi-e_lo)*2*CORRIDOR_HALF_WIDTH_M):.2f} Pkt/m2)")

    lat_c, lon_c = _UTM33_TO_LATLON.transform(e_c, n_c)
    bridge_hit = is_near_bridge_latlon(np.asarray(lat_c), np.asarray(lon_c), _trip_bridges())
    if bridge_hit.any():
        print(f"{bridge_hit.sum()} Punkte als Bruecken-/Ueberfuehrungsartefakt ausgeschlossen")
        e_c, n_c, z_c = e_c[~bridge_hit], n_c[~bridge_hit], z_c[~bridge_hit]

    s_c = e_c - e_lo
    s_max = s_c.max()
    bins = np.arange(0, s_max + BIN_SIZE_M, BIN_SIZE_M)
    bin_idx = np.digitize(s_c, bins)
    df = pd.DataFrame({"bin": bin_idx, "z": z_c})
    grouped = df.groupby("bin")["z"].agg(["median", "count", "std"])
    s_centers = bins[grouped.index - 1] + BIN_SIZE_M / 2
    elev_raw = grouped["median"].values.copy()
    counts = grouped["count"].values
    stds = grouped["std"].values
    print(f"{len(s_centers)} Bins, Median-Punktzahl/Bin={np.median(counts):.0f} "
          f"(min={counts.min()}, max={counts.max()}), Median-Std/Bin={np.nanmedian(stds):.2f}m")

    # (Eine Streuungs-basierte Bimodalitaets-Wache wurde verworfen: bei einem
    # 16m-Korridor erzeugt schon normale Fahrbahn-Querneigung/Kamber
    # [typisch 2-3%] ueber die Breite ~0.3-0.5m Streuung pro Bin - ein
    # Schwellwert darauf haette ueberwiegend normale Bins statt echter
    # Kontamination getroffen. Stattdessen unten der bereits etablierte
    # physikalische Plausibilitaetsfilter auf die abgeleitete Steigung.)

    win = max(3, int(SMOOTH_WINDOW_M / BIN_SIZE_M) | 1)
    elev_s = pd.Series(elev_raw).rolling(win, center=True, min_periods=max(3, win // 4)).median()
    elev_s = elev_s.rolling(win, center=True, min_periods=max(3, win // 4)).mean().to_numpy()
    valid = ~np.isnan(elev_s)
    s_centers, elev_s, counts = s_centers[valid], elev_s[valid], counts[valid]

    grade = np.gradient(elev_s, s_centers)

    # physikalischer Plausibilitaetsfilter (wie top_speed_validation.
    # segment_grade(), MAX_PLAUSIBLE_GRADE=6%): einzelne Bins/kurze Abschnitte
    # ueber 6% sind fuer eine Autobahn praktisch ausgeschlossen - typischer
    # Kontaminations-/ALS-Artefakt (Rampe, Brueckenrand). Ueber die Luecke
    # linear interpolieren statt eines falschen Werts, dann neu glaetten.
    implausible = np.abs(grade) > 0.06
    if implausible.any():
        print(f"{implausible.sum()} Bins mit unplausibler Steigung (>6%) - interpoliere darueber")
        idx = np.arange(len(s_centers))
        elev_clean = elev_s.copy()
        elev_clean[implausible] = np.nan
        elev_clean = np.interp(idx, idx[~implausible], elev_clean[~implausible])
        elev_s = pd.Series(elev_clean).rolling(win, center=True, min_periods=max(3, win // 4)).mean().to_numpy()
        valid2 = ~np.isnan(elev_s)
        s_centers, elev_s, counts = s_centers[valid2], elev_s[valid2], counts[valid2]
        grade = np.gradient(elev_s, s_centers)

    print(f"Hoehenprofil: {elev_s.min():.2f} - {elev_s.max():.2f} m ueber {s_centers[-1]-s_centers[0]:.0f}m")
    peak_i = np.argmax(elev_s)
    print(f"Scheitelpunkt bei s={s_centers[peak_i]:.0f}m (e={s_centers[peak_i]+e_lo:.0f}), "
          f"Hoehe={elev_s[peak_i]:.2f}m")
    print(f"Steigung Anfahrt: {np.median(grade[:peak_i])*100:+.3f}%, "
          f"Abfahrt: {np.median(grade[peak_i:])*100:+.3f}%, "
          f"Max |grade|: {np.max(np.abs(grade))*100:.3f}%")

    lat_centers, lon_centers = _UTM33_TO_LATLON.transform(s_centers + e_lo, centerline_n(s_centers + e_lo))
    return dict(s_m=s_centers, elev_m=elev_s, grade=grade, n_points_per_bin=counts,
                e_utm33=s_centers + e_lo, n_utm33=centerline_n(s_centers + e_lo),
                lat=np.asarray(lat_centers), lon=np.asarray(lon_centers),
                centerline_ref_e=ref_e, centerline_ref_n=ref_n, e_offset=e_lo,
                corridor_half_width_m=CORRIDOR_HALF_WIDTH_M, bin_size_m=BIN_SIZE_M)


def main():
    print("Lade GPS-Referenzstrecke...")
    con = duckdb.connect("data/datalake.duckdb", read_only=True)
    lat = load_channel(con, OBD_LOG_ID, "Breite")
    lon = load_channel(con, OBD_LOG_ID, "Länge")

    print("Lade rohe ALS-Bodenpunkte (volle native Dichte) fuer 6 Kacheln...")
    all_e, all_n, all_z = [], [], []
    for e_km in TILE_KMS_E:
        x, y, z = load_ground_points(e_km, TILE_KM_N)
        all_e.append(x)
        all_n.append(y)
        all_z.append(z)
        print(f"  {e_km}_{TILE_KM_N}: {len(x):,} Bodenpunkte")
    all_e = np.concatenate(all_e)
    all_n = np.concatenate(all_n)
    all_z = np.concatenate(all_z)
    print(f"Gesamt: {len(all_e):,} rohe Bodenpunkte ueber alle 6 Kacheln")

    profiles = {}
    for name, t0, t1 in [("WEST", 505.0, 570.0), ("OST", 705.0, 795.0)]:
        profiles[name] = build_profile(name, t0, t1, con, lat, lon, all_e, all_n, all_z)

    np.savez(OUT_PATH,
             **{f"{name}_{k}": v for name, prof in profiles.items() for k, v in prof.items()})
    print(f"\nGespeichert: {OUT_PATH} (Profile: {list(profiles.keys())})")


if __name__ == "__main__":
    main()
