"""
Phase 4a: bestehende _maybe/_related-Signale gegen die NEUE OBD-Ground-Truth (direkt aus dem
CAN-Log dekodiert, siehe obd_from_can.py) und den vollen Anker-Satz aus can_byte_search.py
gegenpruefen - viele davon wurden vor dieser Session nur gegen einen kleineren Anker-Satz oder
nur an einem einzigen Log geprueft (MX-5 Projekt).

Ausgeschlossen: reine Rollzaehler/Strukturbytes (MsgCounter_maybe etc.), die ASCII-Teilenummer
(0x438), die TPMS-Antworten (0x728, anderer Mechanismus, schon gut verstanden), und die 3 in
dieser Session neu hinzugefuegten Signale (bereits frisch getestet).
"""
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from can_log_parser import parse_candump, load_db
from obd_from_can import decode_obd_traffic
from can_byte_search import extract_anchors, prepare_anchors_for_window, correlate_candidate, MIN_R

CANDIDATES = [
    (0x075, "HardBrakingEvent_flag_maybe"),
    (0x076, "HardBrakingEvent_flag_maybe"),
    (0x078, "Longi_Acc_Corr"),
    (0x079, "Lateral_Acc_Corr_maybe"),
    (0x079, "YawRate_Corr"),
    (0x09F, "Reverse_Flag_maybe"),
    (0x130, "EngineRPM_related_2"),
    (0x166, "Clutch_Pedal_Position_related_2"),
    (0x167, "EngineLoad_or_Torque_pct_maybe"),
    (0x200, "EngineLoad_related_maybe"),
    (0x240, "SteeringAngle_related_2_maybe"),
    (0x240, "SteeringTorque_related"),
    (0x415, "BrakeRelated_weak_maybe"),
    (0x420, "Travel_distance_related"),
    (0x477, "GearDisplay_related_maybe"),
]

LOGS = [
    "data/can/candump-2026-09-12_211833.log",
    "data/can/candump-2026-09-14_081105.log",
    "data/can/candump-2026-09-14_163711.log",
    "data/can/candump-2026-09-14_173057.log",
]


def decode_signal(raw_df, db, can_id, sig_name):
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


def main():
    db = load_db()
    rows = []
    for log_path in LOGS:
        print(f"=== {log_path} ===", flush=True)
        raw = parse_candump(log_path)
        obd_decoded = decode_obd_traffic(raw)
        anchors = extract_anchors(raw, db, obd_decoded, len(obd_decoded) > 0)

        for can_id, sig_name in CANDIDATES:
            t_sig, v_sig = decode_signal(raw, db, can_id, sig_name)
            if len(t_sig) < 100 or np.nanstd(v_sig) < 1e-9:
                continue
            t0, t1 = t_sig.min(), t_sig.max()
            prepared = prepare_anchors_for_window(anchors, t0, t1)
            if not prepared:
                continue
            hit = correlate_candidate(t_sig, v_sig, t0, t1, prepared)
            if hit:
                name, method, r_raw, r_dt = hit
                rows.append({
                    "can_id": f"0x{can_id:03X}", "signal": sig_name, "log": log_path.split("candump-")[1][:-4],
                    "anchor": name, "method": method, "r_raw": round(r_raw, 4), "r_detrend": round(r_dt, 4),
                })
                print(f"  0x{can_id:03X} {sig_name} <-> {name}: r_raw={r_raw:.3f} r_detrend={r_dt:.3f}", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv("results/can_retest_maybe_signals.csv", index=False)
    print(f"\n{len(df)} Treffer -> results/can_retest_maybe_signals.csv")
    if not df.empty:
        summary = (df.groupby(["can_id", "signal", "anchor"])
                   .agg(n_logs=("log", "nunique"), r_detrend_min=("r_detrend", lambda s: s.abs().min()),
                        r_detrend_max=("r_detrend", lambda s: s.abs().max()))
                   .reset_index().sort_values(["n_logs", "r_detrend_min"], ascending=[False, False]))
        with pd.option_context("display.max_rows", 60, "display.width", 160):
            print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
