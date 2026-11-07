"""
MX-5 Fahrzeugmodell fuer die Streckensimulation, aus den eigenen Logs, 07.11.2026

Nutzerauftrag: Die Simulation auf der Strecke soll die aus den Logs berechneten Modelle nutzen. Dieses Skript
fasst zusammen, was Rundenzeit-Simulation, Ideallinien und Editor gemeinsam lesen
(results/mx5_vehicle_model.json), damit die Werte nicht doppelt gepflegt werden. Motor, Uebersetzungen, Traegheit,
Fahrwiderstaende und Traktionsgrenze kommen unveraendert aus drivetrain_model_validation.py/performance_simulation.py.

Nur Nankang NS-R2 (bis candump-2026-10-01_142300). Mit den Winterreifen seit 01.10. werden laut Nutzer keine
Rundenzeit-, Ideallinien- oder Grip-Rechnungen gemacht (docs/logs/projekt-stand.md, Reifenwechsel 01.10.2026).

- Quergrenze: hoechstes 0,5-s-Mittel des Sensors LateralAcc_CAN. Am Haftabriss bricht die Querbeschleunigung ein,
  ein "1 s gehalten" misst deshalb Dauerlast statt Grenze (so zuerst gebaut, ergab 0,82 g; Nutzerhinweis 07.11.2026).
  Einzelne 100-Hz-Werte (bis 1,15 g) sind Rauschen. Gierrate*v liegt in Rutschern hoeher als die echte
  Querbeschleunigung, das Handy-Gyro ist wegen des Halters unbrauchbar (A111 27.08.: Gyro 0,85-1,21 g, GPS-Kurs 0,54 g).
- Bremsgrenze: BRAKE_CAP_G aus spreewaldring_racing_line_optimal.py (Nutzerentscheid 02.10., haerteste
  ABS-Bremsung >= 1,5 s), in den wahren Rahmen umgerechnet.
- Beschleunigen: Reifengrenze aus TRACTION_MAX_FORCE_N. Die gemessene Beschleunigungs-Huelle ist bis auf Gang 1 die
  Motorkraft, nicht der Reifen.
- Pruefung der Ellipse: Anteil der Punkte ausserhalb und Lage der TCS-Eingriffe (e nahe 1 = Reifen nahe am Limit).

Zugkraftunterbrechung je Gangpaar: p10 der gemessenen Luecken bei Schaltungen unter Last (obere Haelfte der
Vorschalt-Beschleunigung, shift_traction_gap_analysis.py), ueber alle Logs: Schaltzeiten haengen vom Fahrer ab, nicht
vom Reifen. Das ist die eigene zuegige Schaltung, nicht die Literatur-ATTACK-Zeit (0,15-0,25 s).

Rahmen: alle Modelle rechnen in VehicleSpeed, der gegen GPS zu hoch liest (performance-model.md, Punkt 9). Die Strecke
ist in echten Metern, die Simulation rechnet deshalb v_wahr = speed_true_factor * VehicleSpeed. Die Parameter selbst
bleiben unveraendert, das ersetzt nicht den offenen Neufit.

Grenzen: Alltagsfahrten mit einzelnen Grenzfaellen. Die Literatur nennt fuer Semi-Slicks 1,0-1,3 g, gemessen ist die
hoechste erreichte Grenze, keine gezielte Grenzfahrt. Temperatur, Reifendruck und Abnutzung sind nicht getrennt.

Gas und Motorlast (08.11.2026): Tabellen fuer die Rueckrechnung aus dem simulierten Tempoverlauf. Drosselklappe x
Drehzahl -> Moment in % (0x167-Skala) aus dem Teillastkennfeld (partial_load_model.py, LOO 6,8 pp); Drosselklappe x
Drehzahl -> Gaspedal als Median aus allen CAN-Fahrten, Pedal >= 2 % und ruhig (ohne Pedal 0: dort regelt oft der
Tempomat). Ein Kennfeld direkt ueber dem Pedal war mit 12,2 pp deutlich schlechter.

Aufruf: .venv/bin/python scripts/mx5_vehicle_model.py
"""
import json
import os

import duckdb
import numpy as np
import pandas as pd

import partial_load_model as plm
from datalake_channels import load_channel, log_key
from drivetrain_model_validation import G, MASS_KG
from performance_simulation import TRACTION_MAX_FORCE_N

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
OUT_PATH = os.path.join(RESULTS_DIR, "mx5_vehicle_model.json")
SHIFT_GAP_SUMMARY = os.path.join(RESULTS_DIR, "shift_traction_gap_summary.json")

TYRE = "NS-R2"
LAST_NSR2_LOG = "candump-2026-10-01_142300"
SPEED_TRUE_FACTOR = 0.971    # GPS/VehicleSpeed, 70 dlg-Logs 08-09/2026 (NS-R2)
BRAKE_CAP_VS_G = 0.762       # BRAKE_CAP_G, aus dem Tempoabfall in VehicleSpeed bestimmt

LAT_WINDOW_S = 0.5
MIN_V_MS = 3.0
ELLIPSE_DT_S = 0.1
CHANNELS = ["VehicleSpeed", "LateralAcc_CAN", "TCS_Active_CAN"]
LOAD_QUANTILE, GAP_QUANTILE = 0.5, 0.10

ETC_GRID = np.arange(0.0, 87.5, 2.5)      # Grad; 86,5 ist die hoechste gemessene Drosselklappe
RPM_GRID = np.arange(1000.0, 7750.0, 250.0)
APP_MIN, APP_STEADY = 2.0, 3.0            # Pedal getreten; Aenderung in 0,5 s darunter = ruhig


def load_log(con, log_id):
    """Querbeschleunigung als 0,5-s-Mittel der Rohwerte, dazu Tempo und Laengsbeschleunigung im 0,1-s-Raster."""
    df = con.execute(
        f"SELECT channel, t_elapsed_s t, value FROM measurements WHERE log_key=? AND log_id=? "
        f"AND channel IN ({','.join('?' * len(CHANNELS))}) AND value IS NOT NULL ORDER BY t",
        [log_key(log_id), log_id, *CHANNELS]).fetchdf()
    ch = {c: d for c, d in df.groupby("channel")}
    if "LateralAcc_CAN" not in ch or len(ch.get("VehicleSpeed", [])) < 100:
        return None
    lat = ch["LateralAcc_CAN"]
    rate = (len(lat) - 1) / max(lat.t.iloc[-1] - lat.t.iloc[0], 1e-6)
    lat_mean = lat.value.abs().rolling(max(1, round(LAT_WINDOW_S * rate)), center=True).mean().values
    t = np.arange(ch["VehicleSpeed"].t.min(), ch["VehicleSpeed"].t.max(), ELLIPSE_DT_S)
    v = np.interp(t, ch["VehicleSpeed"].t.values, ch["VehicleSpeed"].value.values) / 3.6 * SPEED_TRUE_FACTOR
    smooth_v = pd.Series(v).rolling(int(round(LAT_WINDOW_S / ELLIPSE_DT_S)), center=True, min_periods=1).mean().values
    tcs = ch.get("TCS_Active_CAN")
    return {
        "lat_t": lat.t.values, "lat": lat_mean, "lat_v": np.interp(lat.t.values, t, v),
        "t": t, "v": v, "ay": np.interp(t, lat.t.values, np.nan_to_num(lat_mean)),
        "ax": np.gradient(smooth_v, ELLIPSE_DT_S) / G,
        "tcs": np.zeros(len(t)) if tcs is None else np.interp(t, tcs.t.values, tcs.value.values),
    }


def measure_grip(con, log_ids):
    rec = {"lat_g": 0.0, "points": [], "tcs": [], "km": 0.0}
    for log_id in log_ids:
        d = load_log(con, log_id)
        if d is None:
            continue
        lat = np.where(d["lat_v"] > MIN_V_MS, d["lat"], np.nan)
        if np.isfinite(lat).any() and np.nanmax(lat) > rec["lat_g"]:
            i = int(np.nanargmax(lat))
            rec.update(lat_g=float(lat[i]), lat_event=[log_id, float(d["lat_t"][i]), float(d["lat_v"][i] * 3.6)])
        keep = d["v"] > 4.0
        rec["points"].append(np.c_[d["ay"][keep], d["ax"][keep]])
        rec["tcs"].append(np.c_[d["ay"][keep & (d["tcs"] > 0)], d["ax"][keep & (d["tcs"] > 0)]])
        rec["km"] += float(d["v"].sum() * ELLIPSE_DT_S / 1000)
    return rec


def ellipse_value(ay, ax, lat_g, accel_g, brake_g):
    return (ay / lat_g) ** 2 + (np.where(ax < 0, ax / brake_g, ax / accel_g)) ** 2


def build_tyre_model(rec):
    accel_g = TRACTION_MAX_FORCE_N / (MASS_KG * G)
    brake_g = BRAKE_CAP_VS_G * SPEED_TRUE_FACTOR
    pts, tcs = np.vstack(rec["points"]), np.vstack(rec["tcs"])
    e_all = ellipse_value(pts[:, 0], pts[:, 1], rec["lat_g"], accel_g, brake_g)
    e_tcs = ellipse_value(tcs[:, 0], tcs[:, 1], rec["lat_g"], accel_g, brake_g)
    return {
        "lat_g": rec["lat_g"], "accel_g": accel_g, "brake_g": brake_g,
        "brake_note": "ABS-Grenze (BRAKE_CAP_G, wahrer Rahmen)", "speed_true_factor": SPEED_TRUE_FACTOR,
        "lat_window_s": LAT_WINDOW_S, "lat_event": rec.get("lat_event"), "km": rec["km"], "tcs_samples": len(tcs),
        "ellipse_outside_pct": float((e_all > 1).mean() * 100),
        "ellipse_tcs_p50": float(np.median(e_tcs)), "ellipse_tcs_p90": float(np.percentile(e_tcs, 90)),
    }


def measure_shift_gaps():
    """Zugkraftluecke je Gangpaar bei Schaltungen unter Last, Raster der Erkennung 0,05 s."""
    with open(SHIFT_GAP_SUMMARY, encoding="utf-8") as f:
        r = pd.DataFrame(json.load(f)["all_results"])
    r = r[~r.no_gap_detected]
    out = {}
    for pair, d in r.groupby("gear_pair"):
        loaded = d[d.baseline_accel_kmh_s >= d.baseline_accel_kmh_s.quantile(LOAD_QUANTILE)]
        out[pair] = {"gap_s": float(loaded.drive_gap_duration_s.quantile(GAP_QUANTILE)),
                     "median_s": float(loaded.drive_gap_duration_s.median()), "n": int(len(loaded))}
    return out


def pedal_rows(con, log_ids):
    """(Log, Drosselklappe, Drehzahl, Pedal) im 0,5-s-Raster, Pedal getreten und ruhig."""
    rows = []
    for log_id in log_ids:
        app, etc, rpm = (load_channel(con, log_id, c) for c in ("APP", "ETC_ACT", "EngineRPM"))
        if min(len(app), len(etc), len(rpm)) < 100:
            continue
        t = np.arange(app.t.min(), app.t.max(), 0.5)
        a = np.interp(t, app.t, app.value)
        e, r = np.interp(t, etc.t, etc.value), np.interp(t, rpm.t, rpm.value)
        ok = (a >= APP_MIN) & (np.abs(a - np.interp(t - 0.5, app.t, app.value)) < APP_STEADY) & (r > 1000)
        rows += [(log_id, *x) for x in zip(e[ok], r[ok], a[ok])]
    return rows


def throttle_maps(con, can_log_ids):
    """Tabellen ETC_GRID x RPM_GRID: Moment in % (0x167) und Gaspedal in %."""
    torque, _ = plm.build_kennfeld_predictor(DB_PATH)
    pedal = plm.fit_surface(plm.robust_bins(pedal_rows(con, can_log_ids)))
    e, r = np.meshgrid(ETC_GRID, RPM_GRID, indexing="ij")
    return {"etc_deg": ETC_GRID.tolist(), "rpm": RPM_GRID.tolist(),
            "torque_pct": np.round(torque(e.ravel(), r.ravel()).reshape(e.shape), 2).tolist(),
            "app_pct": np.round(pedal(e.ravel(), r.ravel()).reshape(e.shape), 2).tolist()}


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    log_ids = con.execute("SELECT log_id FROM logs WHERE source_format LIKE 'can%' AND duration_s >= 60 AND log_id <= ? "
                          "ORDER BY start_time_local", [LAST_NSR2_LOG]).fetchdf().log_id.tolist()
    rec = measure_grip(con, log_ids)
    all_can = con.execute("SELECT log_id FROM logs WHERE source_format LIKE 'can%' AND duration_s > 600").fetchdf()
    throttle = throttle_maps(con, all_can.log_id.tolist())  # Motorverhalten: alle Reifensaetze
    con.close()
    model = {"default_tyre": TYRE, "tyres": {TYRE: build_tyre_model(rec)}, "shift_gaps": measure_shift_gaps(),
             "throttle": throttle}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, ensure_ascii=False)
    m = model["tyres"][TYRE]
    print(f"{TYRE} ({len(log_ids)} CAN-Logs, {m['km']:.0f} km): quer {m['lat_g']:.3f} g bei {m['lat_event']}, bremsen "
          f"{m['brake_g']:.3f} g, beschl. {m['accel_g']:.3f} g; Ellipse: {m['ellipse_outside_pct']:.3f} % ausserhalb, "
          f"TCS e p50 {m['ellipse_tcs_p50']:.2f}, p90 {m['ellipse_tcs_p90']:.2f} (n={m['tcs_samples']})")
    for pair, s in model["shift_gaps"].items():
        print(f"Schaltung {pair}: {s['gap_s']:.2f} s (Median unter Last {s['median_s']:.2f} s, n={s['n']})")
    print(f"Geschrieben: {OUT_PATH}")


if __name__ == "__main__":
    main()
