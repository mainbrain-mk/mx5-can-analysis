"""Positionsaufgeloeste Vmax-/WOT-Validierung fuer die bidirektionale
Vollgas-Fahrt vom 2026-09-12 (siehe mx5_bidirectional_wot_validation.md),
MIT der hochaufgeloesten Kuppen-Vermessung aus crest_profile_wot_2026_09_12.py
(native ALS-Punktdichte ~12-15 Pkt/m2, eigene Fahrspur-Mittellinie je
Richtung) statt der bisherigen Ein-Wert-Gefaellekorrektur pro Segment
(top_speed_validation.segment_grade()).

Fuer jedes Gang-Segment: echte lokale Steigung entlang der tatsaechlich
gefahrenen GPS-Position (nicht ein Segment-Mittelwert), echter historischer
Wind (Open-Meteo, wie coastdown_analysis.py), CdA/Crr/eta-Modell aus
drivetrain_model_validation.py.

WICHTIG - Vorzeichenkonvention der Kuppen-Datei: `<RICHTUNG>_grade` ist
d(Hoehe)/d(Ost-Koordinate e) - also die Steigung fuer eine Fahrt in
STEIGENDER e-Richtung (Osten). OST faehrt genau so (heading~89 Grad) und
kann den Wert direkt verwenden. WEST faehrt in FALLENDER e-Richtung
(heading~270 Grad) - dort muss das Vorzeichen umgedreht werden, siehe unten.

Aufruf: .venv/bin/python scripts/precise_vmax_validation_2026_09_12.py
"""
import datetime
import json
import os

import duckdb
import numpy as np
import pandas as pd

from drivetrain_model_validation import (
    load_channel, model_accel, RHO_KG_M3, G, CDA_M2, CRR, ETA, R_DYN_M,
    GEAR_RATIOS, FINAL_DRIVE,
)
from coastdown_analysis import _load_wind_cache, wind_at, tailwind_component_ms
from top_speed_validation import _LATLON_TO_UTM33

OBD_LOG_ID = "2026-09-12 211851"
CAN_LOG_ID = "candump-2026-09-12_211833"
CAN_DECODED_CSV = "data/can/candump-2026-09-12_211833_decoded.csv"
CAN_START_EPOCH = 1789240713.611747
MASS_KG = 1173.4
CREST_PROFILE_PATH = "data/elevation/crest_profile_2026-09-12_wot.npz"
RESULTS_DIR = "results"

# Gang-Fenster je Richtung (CAN-relative Zeit, aus der urspruenglichen
# bidirektionalen Analyse - siehe mx5_bidirectional_wot_validation.md)
GEAR_WINDOWS = {
    "WEST": [(508.6, 518.0, 4), (518.3, 540.5, 5), (541.1, 566.9, 6)],
    "OST": [(709.4, 713.8, 3), (714.2, 721.4, 4), (721.7, 735.8, 5), (736.5, 791.8, 6)],
}
GRADE_SIGN = {"WEST": -1.0, "OST": +1.0}  # siehe Docstring


def obd_channel_abs(con, channel):
    df = con.execute(
        "select timestamp_local, value from measurements where log_id=? and channel=?",
        [OBD_LOG_ID, channel],
    ).fetchdf()
    df["t_abs"] = df["timestamp_local"].astype("int64") / 1e9
    return df.sort_values("t_abs")


def main():
    con = duckdb.connect("data/datalake.duckdb", read_only=True)
    can = pd.read_csv(CAN_DECODED_CSV)
    wind_cache = _load_wind_cache()
    crest = np.load(CREST_PROFILE_PATH)

    lat_full = obd_channel_abs(con, "Breite")
    lon_full = obd_channel_abs(con, "Länge")
    heading_full = obd_channel_abs(con, "Lager")

    sig = can[can["signal"] == "VehicleSpeed"].copy()
    sig["value"] = pd.to_numeric(sig["value"], errors="coerce")
    sig = sig.dropna(subset=["value"])
    sig["t_abs"] = CAN_START_EPOCH + sig["t"]
    sig = sig.sort_values("t_abs")

    results = []
    for name in ["WEST", "OST"]:
        prof_e = crest[f"{name}_e_utm33"]
        prof_grade = crest[f"{name}_grade"] * GRADE_SIGN[name]

        t0w, t1w = GEAR_WINDOWS[name][0][0], GEAR_WINDOWS[name][-1][1]
        t0a, t1a = CAN_START_EPOCH + t0w - 5, CAN_START_EPOCH + t1w + 5
        m = (lat_full["t_abs"] >= t0a) & (lat_full["t_abs"] <= t1a)
        sub_lat = lat_full.loc[m, "value"].values
        sub_t_abs = lat_full.loc[m, "t_abs"].values
        sub_lon = np.interp(sub_t_abs, lon_full["t_abs"].values, lon_full["value"].values)
        e_gps, n_gps = _LATLON_TO_UTM33.transform(sub_lon, sub_lat)

        heading_med = float(np.median(np.interp(
            sub_t_abs, heading_full["t_abs"].values, heading_full["value"].values)))
        ts_mid = datetime.datetime.fromtimestamp(float(np.median(sub_t_abs)), tz=datetime.timezone.utc)
        wind_speed_ms, wind_dir_deg = wind_at(ts_mid, float(np.median(sub_lat)), float(np.median(sub_lon)), wind_cache)
        tailwind = tailwind_component_ms(heading_med, wind_speed_ms, wind_dir_deg) if wind_speed_ms is not None else 0.0

        print(f"\n=== {name} (heading={heading_med:.0f}°, Rueckenwind={tailwind:+.2f} m/s) ===")
        for t0, t1, gear in GEAR_WINDOWS[name]:
            t0a2, t1a2 = CAN_START_EPOCH + t0, CAN_START_EPOCH + t1
            vseg = sig[(sig.t_abs >= t0a2) & (sig.t_abs <= t1a2)]
            t_v, v_ms = vseg.t_abs.values, vseg.value.values / 3.6
            e_v = np.interp(t_v, sub_t_abs, e_gps)
            grade_local = np.interp(e_v, prof_e, prof_grade).mean()

            a_meas = float(np.polyfit(t_v - t_v[0], v_ms, 1)[0])
            v_mean = float(v_ms.mean())
            a_flat, rpm, torque = model_accel(v_mean, gear, MASS_KG)
            v_air = v_mean - tailwind
            f_wheel = torque * GEAR_RATIOS[gear] * FINAL_DRIVE * ETA / R_DYN_M
            f_drag = 0.5 * RHO_KG_M3 * CDA_M2 * v_air ** 2
            f_roll = CRR * MASS_KG * G
            f_grade = MASS_KG * G * grade_local
            a_corr = (f_wheel - f_drag - f_roll - f_grade) / MASS_KG
            ratio_corr = a_meas / a_corr if abs(a_corr) > 1e-3 else None

            ratio_str = f"{ratio_corr:.3f}" if ratio_corr is not None else "n/a (Vmax-nah)"
            print(f"  Gang {gear}: v_mean={v_mean*3.6:5.0f}km/h  lokale Steigung={grade_local*100:+.3f}%  "
                  f"a_meas={a_meas:+.3f}  a_flach={float(a_flat):+.3f}  a_korr={a_corr:+.3f}  "
                  f"Verh.flach={a_meas/a_flat:.3f}  Verh.korr={ratio_str}")
            results.append(dict(
                direction=name, gear=gear, t_start=t0, t_end=t1,
                v_mean_kmh=v_mean * 3.6, local_grade_pct=grade_local * 100,
                tailwind_ms=tailwind, a_measured_ms2=a_meas,
                a_model_flat_ms2=float(a_flat), a_model_corrected_ms2=a_corr,
                ratio_flat=a_meas / a_flat, ratio_corrected=ratio_corr,
            ))

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "precise_vmax_validation_2026-09-12.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {out_path}")


if __name__ == "__main__":
    main()
