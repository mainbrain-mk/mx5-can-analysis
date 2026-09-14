"""
Einmalige Zuordnung: welche Mode-22-DID (aus dem CAN-Log selbst dekodiert, siehe
obd_from_can.py) entspricht welchem benannten OBD-Fusion-Kanal (aus der `.dlg`-Datei)?

Warum ueberhaupt eine `.dlg` noetig, wenn obd_from_can.py doch komplett `.dlg`-unabhaengig
ist? Nur fuer DIESEN einmaligen Namens-Abgleich: die CAN-Antworten tragen nur eine rohe DID
(z.B. 0xDA85), keinen Klartextnamen. Die `.dlg` kennt den Namen (z.B. "ActualEnginePercentTorque"),
aber nur als von OBD-Fusion selbst schon umgerechneten Wert. Sobald die Zuordnung einmal steht,
braucht keine zukuenftige Auswertung mehr eine `.dlg`-Datei.

Vorsicht Zeit-Sync: `.dlg`-Zeitstempel sind zwar epoch-basiert, aber NICHT framegenau zum
CAN-Log. Stichprobe zeigte zwei verschiedene Faelle: (a) kleine, nicht-konstante Drift
(-8..-27s, vermutlich Handy-seitige Pufferung) bei zwei Logs, (b) bei den anderen beiden Logs
(`_163711`, `_173057`) ein fast konstanter Offset von ~96.200-96.270s (~26,7h) - das ist die
schon dokumentierte "Pi-Uhr ohne RTC, kein NTP waehrend der Session"-Bug-Klasse
(mx5_can_bus_logging.md), hier zum ersten Mal an einem Log mit gepaarter `.dlg` UND CAN-Traffic
konkret nachgewiesen statt nur vermutet. Deshalb zweistufige Lag-Suche: zuerst ein grober
Offset per Kreuzkorrelation der `VehicleSpeed`-Spur (robust genug, um auch einen Stunden-
Versatz zu finden), danach die feine Suche (gleiche Technik wie beim GPS/OBD-Pairing in
mx5_can_bus_logging.md, Kurvenmodell-Eintrag) in einem Fenster um diesen groben Offset.
"""
import sys
import sqlite3
import numpy as np
import pandas as pd
from scipy.signal import correlate, correlation_lags

from can_log_parser import parse_candump
from obd_from_can import decode_obd_traffic, summarize

# Phone-Sensoren (GPS/IMU) - haben keinen CAN-Gegenpart, aus dem Kandidatenset ausschliessen
NON_OBD_CHANNELS = {
    "Breite", "Länge", "Höhe", "Horz Genauigkeit", "Lager", "GPS-Geschwindigkeit",
    "AccelerationX", "AccelerationY", "AccelerationZ",
    "AccelerationWithGravityX", "AccelerationWithGravityY", "AccelerationWithGravityZ",
    "MagnetometerX", "MagnetometerY", "MagnetometerZ",
    "RotationRateX", "RotationRateY", "RotationRateZ", "Pitch", "Roll",
}

FINE_WINDOW_S = 150  # Fenster um den groben Offset fuer die praezise Pro-DID-Suche - grosszuegig,
# der grobe VehicleSpeed-Offset traf den wahren Wert bei einem Log nur auf ~70s genau
FINE_STEP_S = 0.5
GRID_HZ = 2.0  # dlg wird nur mit ~2Hz gepollt (siehe mx5_can_bus_logging.md), CAN-Seite feiner - beide auf 2Hz resamplen


def coarse_offset_from_vehicle_speed(can_df, dlg_df):
    """Grober Zeitversatz (Sekunden, zur CAN-Zeit zu addieren um die .dlg-Zeit zu treffen) per
    FFT-Kreuzkorrelation der VehicleSpeed-Spuren - robust auch gegen Versaetze im Stundenbereich
    (Pi-Uhr ohne NTP), nicht nur die paar Sekunden Handy-Pufferung."""
    can_speed = can_df[can_df["can_id"] == 0x202]
    t_can = can_speed["t"].to_numpy()
    v_can = can_speed["data"].apply(lambda d: ((d[2] << 8) | d[3]) * 0.01).to_numpy()

    dlg_speed = dlg_df[dlg_df["channel"] == "VehicleSpeed"]
    t_dlg = dlg_speed["t_epoch"].to_numpy()
    v_dlg = pd.to_numeric(dlg_speed["value"], errors="coerce").to_numpy()
    valid = ~np.isnan(v_dlg)
    t_dlg, v_dlg = t_dlg[valid], v_dlg[valid]
    if len(t_can) < 50 or len(t_dlg) < 50:
        return 0.0, 0.0

    hz = 1.0
    grid_can = np.arange(t_can.min(), t_can.max(), 1 / hz)
    grid_dlg = np.arange(t_dlg.min(), t_dlg.max(), 1 / hz)
    s_can = np.interp(grid_can, t_can, v_can) - v_can.mean()
    s_dlg = np.interp(grid_dlg, t_dlg, v_dlg) - v_dlg.mean()
    if s_can.std() < 1e-6 or s_dlg.std() < 1e-6:
        return 0.0, 0.0

    corr = correlate(s_can, s_dlg, mode="full", method="fft")
    lags = correlation_lags(len(s_can), len(s_dlg), mode="full")
    best_idx = np.argmax(corr)
    r = corr[best_idx] / (np.linalg.norm(s_can) * np.linalg.norm(s_dlg))
    # s_can[n] entspricht s_dlg[n-lag] -> t_can.min()+n/hz == t_dlg.min()+(n-lag)/hz
    # -> Offset, der zur CAN-Zeit addiert die dlg-Zeit ergibt: dlg_t = can_t + offset
    offset = (t_dlg.min() - t_can.min()) + lags[best_idx] / hz
    return offset, r


def load_dlg_channels(path):
    conn = sqlite3.connect(path)
    df = pd.read_sql_query("""
        SELECT pde.Time AS raw_time, pme.PidName AS channel, pde.Value AS value
        FROM PidDataEntry pde LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
        ORDER BY pde.Time ASC
    """, conn)
    conn.close()
    TICKS_OFFSET = 621355968000000000
    df["t_epoch"] = (df["raw_time"] - TICKS_OFFSET) / 1e7
    return df


def resample(t, v, t0, t1, hz):
    grid = np.arange(t0, t1, 1.0 / hz)
    if len(t) < 2:
        return grid, np.full_like(grid, np.nan)
    order = np.argsort(t)
    t, v = np.asarray(t)[order], np.asarray(v)[order]
    return grid, np.interp(grid, t, v, left=np.nan, right=np.nan)


def best_lag_correlation(t_did, raw_did, t_ch, val_ch, center_s, window_s=FINE_WINDOW_S, step_s=FINE_STEP_S):
    """Grid beider Reihen auf einen gemeinsamen Zeitraum, dann Lag mit maximaler |Korrelation|
    in [center_s-window_s, center_s+window_s] suchen. Rueckgabe: (best_lag_s, best_r) oder
    (None, 0.0) bei zu wenig ueberlappenden Daten."""
    if len(t_did) < 20 or len(t_ch) < 20:
        return None, 0.0
    t0 = max(t_did.min(), t_ch.min() + center_s) - 5
    t1 = min(t_did.max(), t_ch.max() + center_s) + 5
    if t1 - t0 < 30:
        return None, 0.0
    grid_did, series_did = resample(t_did, raw_did, t0, t1, GRID_HZ)
    best_lag, best_r = None, 0.0
    for lag in np.arange(center_s - window_s, center_s + window_s, step_s):
        grid_ch, series_ch = resample(t_ch + lag, val_ch, t0, t1, GRID_HZ)
        mask = ~np.isnan(series_did) & ~np.isnan(series_ch)
        if mask.sum() < 30:
            continue
        a, b = series_did[mask], series_ch[mask]
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        r = np.corrcoef(a, b)[0, 1]
        if abs(r) > abs(best_r):
            best_r, best_lag = r, lag
    return best_lag, best_r


def map_one_log(can_path, dlg_path, min_n=50):
    raw = parse_candump(can_path)
    decoded = decode_obd_traffic(raw)
    summary = summarize(decoded)
    dids = summary[(summary["mode"] == "mode22") & (summary["n"] >= min_n)]

    dlg = load_dlg_channels(dlg_path)
    channels = sorted(set(dlg["channel"].dropna()) - NON_OBD_CHANNELS)

    coarse_offset, coarse_r = coarse_offset_from_vehicle_speed(raw, dlg)
    center_s = -coarse_offset  # t_ch (dlg) + lag soll can-Zeit treffen: lag = -( dlg_t - can_t )
    print(f"  grober Offset (VehicleSpeed-Kreuzkorrelation): {coarse_offset:+.1f}s (r={coarse_r:.3f}) "
          f"-> Suchzentrum {center_s:+.1f}s", flush=True)

    rows = []
    for _, did_row in dids.iterrows():
        did_hex = did_row["id_hex"]
        did_int = int(did_hex, 16)
        sel = decoded[(decoded["direction"] == "response") & (decoded["mode"] == "mode22") & (decoded["id_"] == did_int)]
        t_did, raw_did = sel["t"].to_numpy(), sel["raw_value"].to_numpy()

        best = (None, 0.0, None)
        for ch in channels:
            g = dlg[dlg["channel"] == ch]
            v = pd.to_numeric(g["value"], errors="coerce")
            mask = v.notna()
            if mask.sum() < 20:
                continue
            lag, r = best_lag_correlation(t_did, raw_did, g.loc[mask, "t_epoch"].to_numpy(), v[mask].to_numpy(), center_s)
            if lag is not None and abs(r) > abs(best[1]):
                best = (ch, r, lag)
        rows.append({"did_hex": did_hex, "best_channel": best[0], "r": round(best[1], 4), "lag_s": best[2]})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pairs = [
        ("data/can/candump-2026-09-12_211833.log", "data/raw/2026-09-12 211851.dlg"),
        ("data/can/candump-2026-09-14_081105.log", "data/raw/2026-09-14 081132.dlg"),
        ("data/can/candump-2026-09-14_163711.log", "data/raw/2026-09-14 163745.dlg"),
        ("data/can/candump-2026-09-14_173057.log", "data/raw/2026-09-14 173044.dlg"),
    ]
    if len(sys.argv) > 1:
        pairs = pairs[:int(sys.argv[1])]

    all_results = []
    for can_path, dlg_path in pairs:
        print(f"\n=== {can_path} ===", flush=True)
        res = map_one_log(can_path, dlg_path)
        res["log"] = can_path
        print(res.to_string(index=False))
        all_results.append(res)

    combined = pd.concat(all_results, ignore_index=True)
    combined.to_csv("results/obd_did_mapping.csv", index=False)
    print("\n-> results/obd_did_mapping.csv")
