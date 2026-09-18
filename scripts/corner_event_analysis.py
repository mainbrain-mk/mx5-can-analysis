"""
Kurvenereignis-Analyse: isolierte, klar bestaetigte Kurven (MX-5 Projekt)

Zweck: siehe docs/logs/projekt-stand.md / grip_estimation.py-Docstring - eine
kontinuierliche Querbeschleunigung ueber die ganze Fahrt ist mit der
aktuellen Halterung nicht zuverlaessig moeglich. Weder die X/Z-Rotation
(korreliert gar nicht mit echter Kurvenfahrt) noch das rohe Gyroskop
(RotationRateY) als kontinuierliches Signal taugen dafuer - Test bei
143 km/h Autobahn-GERADEAUSfahrt zeigte trotzdem Std=14 deg/s im Gyro,
d.h. bei normaler Fahrt dominiert Halterungs-Wackel-Rauschen das echte,
kleine Signal. NUR bei eindeutig starken, laenger anhaltenden Kurven ist
das echte Signal gross genug, um das Rauschen zu dominieren (das war
exakt die Bedingung, unter der die GPS-Validierung in
grip_estimation.py funktioniert hat: |heading_rate|>6-8 deg/s UND
Geschwindigkeit > ~12 m/s).

Deshalb hier der gleiche Ansatz wie bei brake_event_analysis.py, aber
fuer Kurven statt Bremsungen: nur isolierte Ereignisse auswerten, bei
denen ZWEI unabhaengige Signale (GPS-Kursaenderung und Gyroskop) im
Vorzeichen uebereinstimmen UND beide deutlich ueber ihrer jeweiligen
Rausch-/Unsicherheitsschwelle liegen - das ist die Kreuzvalidierung,
die eine echte Kurve von Rauschen unterscheidet (analog zur
OBD-Kreuzvalidierung fuer Ausreisser in vibration_analysis.py).

Methodik:
  1. GPS-Kurs ueber ein Zeitfenster von ±HEADING_WINDOW_S bestimmen
     (grosse Basisstrecke noetig wg. 1Hz/15-30m-GPS-Ungenauigkeit,
     Mindestbasisstrecke GPS_MIN_DISPLACEMENT_M).
  2. Gyroskop (per Log erkannter Gierraten-Kanal, siehe unten) mit
     LOWPASS_CUTOFF_HZ filtern, Vorzeichen wie in grip_estimation.py
     validiert:
       omega_deg_s = GYRO_SIGN_SCALE * RotationRate<Achse>_lowpass
     (positiv = Rechtskurve, ueber alle 7 Logs gegen GPS-Kursaenderung
     geprueft: Korrelation -0.54 bis -0.90, siehe docs/logs/projekt-stand.md).

  NACHTRAG (06.09.2026): "RotationRateY" oben beschreibt die Halterung bis
  inkl. 2026-09-05 (Y=vertikal, fest verbaut). Die Halterung ist jetzt
  variabel - der Gierraten-Kanal wird deshalb PRO LOG per
  `imu_orientation.detect_vertical_axis()` + `yaw_channel()` bestimmt
  (Rotation um die jeweils erkannte Vertikalachse), nicht mehr fest auf
  RotationRateY angenommen. Das Vorzeichen GYRO_SIGN_SCALE=-1.0 wurde fuer
  ein Log mit Z=vertikal ERNEUT gegen die GPS-Kursaenderung bestaetigt
  (Korrelation -0.76/-0.80, siehe imu_orientation.py Docstring).
  3. Ereignis-Kandidat (pro ~1Hz-GPS-Sample): |heading_rate| >
     HEADING_RATE_MIN_DEG_S UND Geschwindigkeit > SPEED_MIN_MS (GPS-
     Basisstrecke gross genug fuer verlaessliche Kursbestimmung) UND
     sign(heading_rate) == sign(omega) (Kreuzvalidierung).
  4. Zusammenhaengende Kandidaten-Samples zu Ereignissen gruppieren
     (gleiche Grundidee wie group_events() in brake_event_analysis.py).
  5. Pro Ereignis (Mindestdauer MIN_EVENT_DURATION_S): Dauer, mittlere
     Geschwindigkeit, Richtung (rechts/links aus Vorzeichen von
     heading_rate), a_lat = v*omega ueber die Ereignisdauer gemittelt
     (kinematische Naeherung: a_lat=v*omega ist die uebliche Formel fuer
     eine "coordinated turn", vernachlaessigt Schwimmwinkel/Drift).

WICHTIG: das ist KEINE vollstaendige Quergrip-Auswertung der Fahrt -
nur die staerksten, eindeutig bestaetigten Kurven werden erfasst (in
der Praxis: laengere Landstrassen-/Autobahnauffahrt-Kurven, keine
kurzen Stadtkurven oder sanftes Lenken). Sanfte/kurze Kurven und
normales "Fahrbahn-Wackeln" werden bewusst ausgeschlossen, weil sie vom
Halterungsrauschen nicht unterscheidbar sind.

Aufruf: python corner_event_analysis.py [log ...]
(ohne Argument: alle .dlg in data/raw/. Mit einem oder mehreren Log-Namen
(z.B. "2026-09-11 081200", mit oder ohne .dlg): nur diese Logs neu
berechnen, Ergebnisse werden in corner_event_summary.json mit den
bestehenden Eintraegen der UEBRIGEN Logs zusammengefuehrt, nicht
ueberschrieben.)
"""
import glob
import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from scipy import signal
from scipy.interpolate import interp1d

from imu_orientation import detect_vertical_axis, yaw_channel, GYRO_SIGN_SCALE

RAW_DIR = "data/raw"
RESULTS_DIR = "results"

TICKS_OFFSET = 621355968000000000  # .NET-Ticks -> Unix-Referenz
FS_UNIFORM = 50.0
LOWPASS_CUTOFF_HZ = 6.0
HEADING_WINDOW_S = 4.0           # Basisfenster fuer GPS-Kursbestimmung (Obergrenze, siehe unten)
HEADING_WINDOW_MIN_S = 2.0       # NACHTRAG 06.09.2026: Untergrenze fuer schnelle, grossradige
                                 # Kurven ("Autobahn-Sweeper") - ein fixes 4s-Fenster verwaesserte
                                 # dort die Gierrate unter HEADING_RATE_MIN_DEG_S (Fund: 5 Kurven
                                 # bei ~93 km/h/Tempomat, eine verpasste Kurve hatte 7.99 statt
                                 # eigentlich ~13 deg/s Spitzenrate). Fenster jetzt adaptiv per
                                 # Sample: window_s = clip(GPS_MIN_DISPLACEMENT_M / v, MIN, MAX) -
                                 # bei niedriger Geschwindigkeit (Kreisverkehre etc.) bleibt es am
                                 # alten 4s-Maximum (unveraendertes Verhalten), bei hoher
                                 # Geschwindigkeit schrumpft es auf min. 2s (genug fuer >=2
                                 # 1Hz-GPS-Samples je Seite UND deutlich ueber der
                                 # Mindestbasisstrecke bei jeder relevanten Geschwindigkeit).
GPS_MIN_DISPLACEMENT_M = 15.0    # Mindestbasisstrecke (GPS-Genauigkeit ~15-30m)
HEADING_RATE_MIN_DEG_S = 8.0     # ab hier "eindeutige Kurve"
SPEED_MIN_MS = 3.0               # Mindestgeschwindigkeit fuer verlaessliche GPS-Basis.
                                 # NACHTRAG 01.09.2026: urspruenglich 10.0 (36 km/h) - schloss
                                 # damit JEDEN Kreisverkehr/jede langsame Ortskurve pauschal aus
                                 # (Nutzer-Nachfrage: "ich bin links um einen/zwei Kreisverkehre
                                 # gefahren" in 2026-09-01 162514/164308, dort 0 Kurven erkannt).
                                 # Auf 3.0 m/s gesenkt - der eigentliche Verlaesslichkeits-Schutz
                                 # ist ohnehin GPS_MIN_DISPLACEMENT_M (15m/4s-Fenster impliziert
                                 # rechnerisch schon eine Mindestgeschwindigkeit ~3.75 m/s), ein
                                 # zusaetzlicher harter 36 km/h-Cutoff war unbegruendet konservativ.
                                 # Verifiziert: mit 3.0 m/s tauchen in den beiden genannten Logs
                                 # neue, plausible Ereignisse auf (unter anderem die ersten
                                 # bestaetigten LINKSKURVEN in einem Lenkwinkel-Log ueberhaupt,
                                 # GPS-Genauigkeit durchweg sauber ~7.6m an diesen Stellen -
                                 # kein Wiederauftreten des 200m-Fehlalarm-Musters von oben).
MIN_EVENT_DURATION_S = 0.9       # NACHTRAG 06.09.2026: war 1.5s, gesenkt auf 0.9s. Bei ~1Hz-GPS
                                 # spannen bereits 2 aufeinanderfolgende, cross-validierte Samples
                                 # nur ~1.0s auf - 1.5s verlangte de facto 3 Samples und schloss
                                 # kurze, schnell durchfahrene Autobahn-Sweeper aus (Fund: Kurve 4
                                 # aus log 145116 t=163-165s, nur 2 Samples ueber der Schwelle).
                                 # 0.9s laesst genau diesen 2-Sample-Fall zu, verwirft aber weiter
                                 # echte Einzelsample-Ausreisser (duration=0).
MAX_GAP_S = 2.5                  # NACHTRAG 06.09.2026: kurze Luecken innerhalb eines Ereignisses
                                 # ueberbruecken. Grund: das adaptive Fenster (siehe oben) kann bei
                                 # sehr scharfen Kurven fuer ein einzelnes ~1Hz-GPS-Sample die
                                 # Mindestbasisstrecke knapp verfehlen (Sehnen- statt Bogenlaenge
                                 # bei schnellem Kursaenderung), was sonst ein durchgehendes
                                 # Ereignis in zwei zu kurze, unterhalb MIN_EVENT_DURATION_S
                                 # liegende Fragmente zerreisst und komplett verschwinden laesst
                                 # (Fund: die ESP-Kurve aus log 145116 t=1037-1041s). 2.5s
                                 # ueberbrueckt so eine Einzel-Luecke sicher, ist aber weit kuerzer
                                 # als der Abstand zwischen echten, verschiedenen Kurven.
GPS_MAX_HORZ_ACC_M = 20.0        # siehe top_speed_validation.py - Fixe mit schlechterer
                                 # Genauigkeit werden VOR der Kursberechnung verworfen (nicht
                                 # nur ueber die Mindestbasisstrecke abgefangen). NACHTRAG
                                 # 01.09.2026: ohne diesen Filter erzeugt ein einzelner sehr
                                 # ungenauer Fix (200m, vermutlich frisch wiedererlangter GPS-
                                 # Empfang) einen falschen "bestaetigten" Kurven-Treffer (Log
                                 # 2026-09-01 152031, t=1602-1604s) - aufgedeckt durch
                                 # Gegenpruefung mit dem neuen Lenkwinkel-Kanal (Lenkrad dort
                                 # nahezu geradeaus, siehe steering_lateral_model.py).
G = 9.81


def load_channel(conn, pid_names):
    placeholders = ",".join("?" for _ in pid_names)
    q = f"""
        SELECT pde.Time AS raw_time, pme.PidName AS sensor_name, pde.Value AS value
        FROM PidDataEntry pde
        LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
        WHERE pme.PidName IN ({placeholders})
        ORDER BY pde.Time ASC
    """
    df = pd.read_sql_query(q, conn, params=pid_names)
    df["datetime"] = pd.to_datetime((df["raw_time"] - TICKS_OFFSET) / 10, unit="us")
    return df


def compute_heading_rate(t_common, lat_i, lon_i, v_ms, max_window_s=HEADING_WINDOW_S,
                          min_window_s=HEADING_WINDOW_MIN_S, min_disp_m=GPS_MIN_DISPLACEMENT_M):
    lat_mean = np.radians(np.mean(lat_i))
    E = lon_i * np.cos(lat_mean) * 111320.0
    N = lat_i * 111320.0
    n = len(t_common)
    heading = np.full(n, np.nan)
    window_per_sample = np.clip(min_disp_m / np.maximum(v_ms, 0.5), min_window_s, max_window_s)
    for i in range(n):
        window_s = window_per_sample[i]
        mprev = (t_common >= t_common[i] - window_s) & (t_common <= t_common[i])
        mnext = (t_common >= t_common[i]) & (t_common <= t_common[i] + window_s)
        if mprev.sum() < 2 or mnext.sum() < 2:
            continue
        e0, n0 = E[mprev][0], N[mprev][0]
        e1, n1 = E[mnext][-1], N[mnext][-1]
        dE, dN = e1 - e0, n1 - n0
        if np.hypot(dE, dN) < min_disp_m:
            continue
        heading[i] = np.degrees(np.arctan2(dE, dN))
    valid = ~np.isnan(heading)
    heading_rate = np.full(n, np.nan)
    if valid.sum() >= 2:
        hu = np.degrees(np.unwrap(np.radians(heading[valid])))
        heading_rate[valid] = np.gradient(hu, t_common[valid])
    return heading_rate, valid


def group_events(t, mask, min_duration_s=MIN_EVENT_DURATION_S, max_gap_s=MAX_GAP_S):
    runs = []
    in_run = False
    start_idx = None
    n = len(mask)
    for i in range(n):
        if mask[i] and not in_run:
            in_run = True
            start_idx = i
        elif not mask[i] and in_run:
            in_run = False
            runs.append((start_idx, i - 1))
    if in_run:
        runs.append((start_idx, n - 1))

    merged = []
    for run in runs:
        if merged and (t[run[0]] - t[merged[-1][1]]) <= max_gap_s:
            merged[-1] = (merged[-1][0], run[1])
        else:
            merged.append(list(run))

    return [(s, e) for s, e in merged if t[e] - t[s] >= min_duration_s]


def analyze_log(db_path):
    db_file = os.path.basename(db_path)
    orient = detect_vertical_axis(db_path)
    v_axis = orient["axis"]
    conn = sqlite3.connect(db_path)
    accel = load_channel(conn, ["AccelerationX", "AccelerationY", "AccelerationZ"])
    rot = load_channel(conn, [yaw_channel(v_axis)])
    gps = load_channel(conn, ["Länge", "Breite"])
    speed = load_channel(conn, ["VehicleSpeed"])
    acc = load_channel(conn, ["Horz Genauigkeit"])
    conn.close()

    if len(gps) == 0:
        return {"file": db_file, "error": "kein GPS-Kanal in diesem Log"}
    if len(rot) == 0:
        return {"file": db_file, "error": f"kein {yaw_channel(v_axis)}-Kanal in diesem Log"}

    t0 = accel["datetime"].min()
    rot["t"] = (rot["datetime"] - t0).dt.total_seconds()
    gps["t"] = (gps["datetime"] - t0).dt.total_seconds()
    speed["t"] = (speed["datetime"] - t0).dt.total_seconds()
    acc["t"] = (acc["datetime"] - t0).dt.total_seconds()

    lon = gps[gps.sensor_name == "Länge"][["t", "value"]].dropna().sort_values("t")
    lat = gps[gps.sensor_name == "Breite"][["t", "value"]].dropna().sort_values("t")
    if len(lat) < 50:
        return {"file": db_file, "error": "zu wenige GPS-Punkte"}

    t_common = lat["t"].values
    lon_i = np.interp(t_common, lon["t"].values, lon["value"].values)
    lat_i = lat["value"].values

    acc_sub = acc[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    if len(acc_sub) >= 2:
        acc_i = np.interp(t_common, acc_sub["t"].values, acc_sub["value"].values)
        good_acc = acc_i <= GPS_MAX_HORZ_ACC_M
        t_common, lon_i, lat_i = t_common[good_acc], lon_i[good_acc], lat_i[good_acc]
        if len(t_common) < 50:
            return {"file": db_file, "error": "zu wenige GPS-Punkte nach Genauigkeitsfilter"}

    speed_sub = speed[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    v_ms = np.interp(t_common, speed_sub["t"].values, speed_sub["value"].values) / 3.6

    heading_rate, hvalid = compute_heading_rate(t_common, lat_i, lon_i, v_ms)

    rot_sub = rot[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    f_rot = interp1d(rot_sub["t"].values, rot_sub["value"].values, kind="linear",
                      bounds_error=False, fill_value=(rot_sub["value"].values[0],
                                                       rot_sub["value"].values[-1]))
    b, a = signal.butter(4, LOWPASS_CUTOFF_HZ / (FS_UNIFORM / 2), btype="low")
    t_uniform = np.arange(t_common[0], t_common[-1], 1 / FS_UNIFORM)
    gyro_u = f_rot(t_uniform)
    gyro_lp_uniform = signal.filtfilt(b, a, gyro_u)
    gyro_lp = np.interp(t_common, t_uniform, gyro_lp_uniform)
    omega_deg_s = GYRO_SIGN_SCALE * gyro_lp

    candidate = (hvalid & (np.abs(heading_rate) > HEADING_RATE_MIN_DEG_S)
                 & (v_ms > SPEED_MIN_MS)
                 & (np.sign(heading_rate) == np.sign(omega_deg_s)))

    events = group_events(t_common, candidate)
    event_list = []
    for i0, i1 in events:
        seg = slice(i0, i1 + 1)
        v_seg = v_ms[seg]
        omega_seg = np.radians(omega_deg_s[seg])
        a_lat_seg = v_seg * omega_seg
        direction = "rechts" if np.nanmean(heading_rate[seg]) > 0 else "links"
        event_list.append({
            "t_start": float(t_common[i0]),
            "t_end": float(t_common[i1]),
            "duration_s": float(t_common[i1] - t_common[i0]),
            "direction": direction,
            "speed_mean_kmh": float(np.mean(v_seg) * 3.6),
            "heading_rate_mean_deg_s": float(np.nanmean(heading_rate[seg])),
            "a_lat_mean_g": float(np.mean(a_lat_seg) / G),
            "a_lat_peak_g": float(a_lat_seg[np.argmax(np.abs(a_lat_seg))] / G),
        })
    event_list.sort(key=lambda e: abs(e["a_lat_peak_g"]), reverse=True)

    return {"file": db_file, "vertical_axis": v_axis, "vertical_axis_defaulted": orient["defaulted"],
            "yaw_channel": yaw_channel(v_axis), "n_events": len(event_list), "events": event_list}


def merge_results(path, new_results, key="file"):
    """Bestehende Eintraege behalten, nur die neu verarbeiteten Logs ersetzen -
    damit ein Aufruf mit einzelnen Log-Namen (taegliche Routine) nicht die
    Historie der uebrigen, nicht neu verarbeiteten Logs ueberschreibt."""
    existing = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = {r[key]: r for r in json.load(f)}
    for r in new_results:
        existing[r[key]] = r
    merged = [existing[k] for k in sorted(existing)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    return merged


def main():
    target_logs = [os.path.basename(a).removesuffix(".dlg") for a in sys.argv[1:]]
    if target_logs:
        db_paths = sorted(f"{RAW_DIR}/{t}.dlg" for t in target_logs)
    else:
        db_paths = sorted(glob.glob(f"{RAW_DIR}/*.dlg"))

    new_results = []
    for db_path in db_paths:
        res = analyze_log(db_path)
        db_file = res["file"]
        new_results.append(res)
        if "error" in res:
            print(f"{db_file}: {res['error']}")
            continue
        defaulted_str = " [Vertikalachse per Fallback=Y angenommen]" if res["vertical_axis_defaulted"] else ""
        print(f"\n=== {db_file}: {res['n_events']} bestaetigte Kurve(n) "
              f"(vertikal={res['vertical_axis']}, Gierrate={res['yaw_channel']}){defaulted_str} ===")
        n_right = sum(1 for e in res["events"] if e["direction"] == "rechts")
        n_left = res["n_events"] - n_right
        print(f"  ({n_right} rechts, {n_left} links)")
        for ev in res["events"]:
            print(f"  t={ev['t_start']:7.1f}-{ev['t_end']:7.1f}s ({ev['duration_s']:.1f}s)  "
                  f"{ev['direction']:6s}  v={ev['speed_mean_kmh']:5.1f} km/h  "
                  f"Gierrate={ev['heading_rate_mean_deg_s']:+6.1f} deg/s  "
                  f"a_lat: mean={ev['a_lat_mean_g']:+.2f}g  peak={ev['a_lat_peak_g']:+.2f}g")

    summary_path = os.path.join(RESULTS_DIR, "corner_event_summary.json")
    all_results = merge_results(summary_path, new_results)

    all_peak_g = [abs(ev["a_lat_peak_g"]) for r in all_results if "error" not in r for ev in r["events"]]
    if all_peak_g:
        arr = np.array(all_peak_g)
        print(f"\n=== Zusammenfassung ueber {len(arr)} bestaetigte Kurven (alle Logs) ===")
        print(f"|a_lat_peak|: max={arr.max():.2f}g  p90={np.percentile(arr,90):.2f}g  "
              f"median={np.median(arr):.2f}g")

    print(f"\nDetails: {summary_path} ({len(all_results)} Log(s) insgesamt, {len(new_results)} davon in diesem Lauf neu berechnet)")


if __name__ == "__main__":
    main()
