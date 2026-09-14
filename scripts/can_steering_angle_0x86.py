"""
Nichtlineare Umrechnung fuer SteeringAngle_related (CAN 0x86, HS_EPAS Byte0-1) - MX-5 Projekt.

Hintergrund (siehe mx5_can_bus_logging.md, Phase-5-Eintrag 2026-09-14): das seit Wochen als
"schwach korreliert, Ursache ungeklaert" gefuehrte Signal ist tatsaechlich fast perfekt mit dem
echten Lenkwinkel (Steering_Wheel_Absolute_Angle@0x82) verknuepft (Spearman r=0.987) - nur
NICHTLINEAR (sehr flach/grobaufloesend nahe 0 Grad, steiler an den Aussenseiten). Eine simple
DBC-Skala/Offset kann das nicht abbilden, deshalb hier eine empirische, monotone Lookup-Tabelle
(isotonische Regression) statt einer linearen Formel.

ponytail: Lookup-Tabelle statt parametrischem Kurvenfit (Sigmoid o.ae.) - deckt jede beliebige
monotone Form ab, ohne eine Funktionsform raten zu muessen, und ist mit `scipy.optimize.
isotonic_regression` + `np.interp` in wenigen Zeilen gebaut. Kein neues Package (kein sklearn).
"""
import os
import sys
import numpy as np
import pandas as pd
from scipy.optimize import isotonic_regression

from can_log_parser import parse_candump, load_db

LUT_PATH = "data/can/steering_angle_0x86_lut.csv"
# Der Schwenk-Log (siehe mx5_can_bus_status.md, "Bonus find" 2026-09-12) deckt zwar den vollen
# Lenkwinkelbereich ab, aber sehr UNGLEICHMAESSIG - viele Samples nahe der Endanschlaege
# (Wagen stand, Lenkrad wurde dort laenger gehalten), fast keine im mittleren Uebergangsbereich
# (schnell durchgedreht). Ein Isotonic-Fit NUR auf diesem Log haelt in der grossen Datenluecke
# faelschlich flach (getestet, R2<0 auf einem Fahr-Log). Deshalb mehrere echte Fahrten dazu, die
# genau diesen mittleren Bereich dicht abdecken - der Schwenk-Log bleibt fuer die sonst nirgends
# erreichten Extremwerte nahe +-490 Grad noetig.
CALIBRATION_LOGS = [
    "data/can/candump-2026-09-12_120952.log",  # deckt die Extreme ab (bis +-490 Grad)
    "data/can/candump-2026-09-12_155117.log",  # dichte Alltagsfahrt, mittlerer Bereich
    "data/can/candump-2026-09-12_211833.log",  # dito, andere Fahrt/Bedingungen
]
MAX_PLAUSIBLE_ANGLE = 500  # ° - filtert die 2 bekannten Power-Up-Glitch-Samples (siehe Session)


def _decode_signal(raw_df, db, can_id, sig_name):
    msg = db.get_message_by_frame_id(can_id)
    sub = raw_df[raw_df["can_id"] == can_id]
    t_list, v_list = [], []
    for t, data in sub[["t", "data"]].itertuples(index=False):
        try:
            dec = msg.decode(data, allow_truncated=True)
        except Exception:
            continue
        if sig_name in dec:
            t_list.append(t)
            v_list.append(dec[sig_name])
    return np.array(t_list), np.array(v_list, dtype=float)


def build_lookup_table(log_paths=CALIBRATION_LOGS, hz=10):
    """Baut die monotone raw->Grad-Umrechnung aus mehreren Logs (Schwenk-Log fuer die Extreme,
    echte Fahrten fuer eine dichte Abdeckung des mittleren Bereichs, siehe Modul-Docstring)."""
    db = load_db()
    raw_all, angle_all = [], []
    for log_path in log_paths:
        raw_df = parse_candump(log_path)
        t_raw, v_raw = _decode_signal(raw_df, db, 0x86, "SteeringAngle_related")
        t_angle, v_angle = _decode_signal(raw_df, db, 0x82, "Steering_Wheel_Absolute_Angle")
        t0, t1 = max(t_raw.min(), t_angle.min()), min(t_raw.max(), t_angle.max())
        grid = np.arange(t0, t1, 1 / hz)
        raw_all.append(np.interp(grid, t_raw, v_raw))
        angle_all.append(np.interp(grid, t_angle, v_angle))
    raw_g, angle_g = np.concatenate(raw_all), np.concatenate(angle_all)
    keep = np.abs(angle_g) <= MAX_PLAUSIBLE_ANGLE
    raw_g, angle_g = raw_g[keep], angle_g[keep]

    order = np.argsort(raw_g)
    raw_sorted, angle_sorted = raw_g[order], angle_g[order]
    angle_fit = isotonic_regression(angle_sorted).x

    # Duplikate im Rohwert (mehrfach exakt derselbe Raw-Count) auf einen Eintrag verdichten,
    # sonst hat np.interp spaeter mehrdeutige/redundante Stuetzstellen.
    lut = (pd.DataFrame({"raw": raw_sorted, "angle": angle_fit})
           .groupby("raw", as_index=False).mean())
    lut.to_csv(LUT_PATH, index=False)
    print(f"Lookup-Tabelle gebaut aus {len(log_paths)} Logs: {len(lut)} Stuetzstellen, "
          f"raw=[{lut['raw'].min():.0f},{lut['raw'].max():.0f}], "
          f"angle=[{lut['angle'].min():.1f},{lut['angle'].max():.1f}]deg -> {LUT_PATH}")
    return lut


def raw_to_angle(raw_values):
    """Rohwert(e) von SteeringAngle_related@0x86 -> Grad, per Lookup-Tabelle (siehe oben).
    Baut die Tabelle bei Bedarf einmalig. Ausserhalb des kalibrierten Rohwert-Bereichs wird auf
    den Rand geklemmt (keine Extrapolation ueber eine isotonische Tabelle hinaus)."""
    if not os.path.exists(LUT_PATH):
        build_lookup_table()
    lut = pd.read_csv(LUT_PATH)
    return np.interp(raw_values, lut["raw"], lut["angle"])


def _demo():
    """Selbsttest: Tabelle aus den CALIBRATION_LOGS trainieren, dann an einem Log, das NICHT in
    dieser Liste steht, gegen dessen eigenen echten Lenkwinkel pruefen - echte
    Out-of-Sample-Validierung, kein Zirkelschluss."""
    build_lookup_table()

    db = load_db()
    test_log = "data/can/candump-2026-09-14_081105.log"
    assert test_log not in CALIBRATION_LOGS
    raw_df = parse_candump(test_log)
    t_raw, v_raw = _decode_signal(raw_df, db, 0x86, "SteeringAngle_related")
    t_angle, v_angle = _decode_signal(raw_df, db, 0x82, "Steering_Wheel_Absolute_Angle")

    t0, t1 = max(t_raw.min(), t_angle.min()), min(t_raw.max(), t_angle.max())
    grid = np.arange(t0, t1, 0.1)
    raw_g = np.interp(grid, t_raw, v_raw)
    angle_true = np.interp(grid, t_angle, v_angle)
    angle_pred = raw_to_angle(raw_g)

    rmse = np.sqrt(np.mean((angle_pred - angle_true) ** 2))
    r2 = 1 - np.sum((angle_true - angle_pred) ** 2) / np.sum((angle_true - angle_true.mean()) ** 2)
    print(f"Out-of-Sample-Test auf {test_log}: R²={r2:.4f}, RMSE={rmse:.2f}deg (n={len(grid)})")
    assert r2 > 0.85, f"R2 zu niedrig fuer eine brauchbare Umrechnung: {r2:.4f}"
    print("Selbsttest OK.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        _demo()
    else:
        build_lookup_table()
