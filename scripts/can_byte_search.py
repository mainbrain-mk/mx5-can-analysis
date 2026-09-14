"""
Wiederverwendbares Werkzeug fuer die Byte-Suche in unbelegten CAN-Botschaften (MX-5 Projekt).

Generalisiert die bisher mehrfach ad hoc wiederholte Methodik (siehe
CAN_unbekannte_signale_bericht_2026-09-11.md, diverse Eintraege in
mx5_can_bus_logging.md) zu einem festen Skript statt Einmal-Analyse:

1. Unbelegte Byte-Bereiche pro Botschaft aus der aktuellen DBC ableiten.
2. Botschaften nach Sendeperiode vorsortieren (schnelle = eher Messwert, langsame = eher
   Status/Komfort).
3. Jedes offene Byte / Byte-Paar (BE/LE, signed/unsigned) gegen ein Set bekannter Anker
   korrelieren (Pearson + Spearman, mit/ohne Detrending) - plus abgeleitete Ereignis-Proxys
   (ABS-/DSC-Verdacht) fuer Faelle ohne sauberen analogen Anker.
4. Zusatzchecks: Konstant-Byte, Rollzaehler, exakter Rohwert-Treffer gegen extern dokumentierte
   Mode-22-DIDs (siehe map_obd_dids.py / obd_from_can.py).
5. Report: alles mit best(|r|) >= MIN_R in einer Markdown-Tabelle + CSV.

Mindest-Schwellwert MIN_R=0.6 ist empirisch aus der Projekthistorie abgeleitet: jeder bisher
bestaetigte Fund lag bei >=0.6 (meist 0.85-0.99), jeder verworfene Kandidat blieb bei 0.4-0.56.
"""
import argparse
import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from can_log_parser import parse_candump, load_db
from obd_from_can import decode_obd_traffic, extract_did_series

MIN_R = 0.6
GRID_HZ = 10.0
DETREND_WINDOW_S = 20.0

# Bekannte, bereits validierte Anker-Signale: (can_id, cantools_signal_name)
ANCHOR_SIGNALS = {
    "EngineRPM": 0x202,
    "VehicleSpeed": 0x202,
    "APP_Accelerator_Pedal_Position": 0x202,
    # ACHTUNG (2026-09-15 korrigiert): diese beiden waren vertauscht (Longi auf 0x75,
    # Lateral auf 0x76). extract_anchors() verwirft einen Anker stillschweigend, wenn der
    # Signalname in der Botschaft fehlt - beide Beschleunigungsanker fehlten dadurch in
    # JEDEM bisherigen Sweep, und der davon abhaengige PROXY_high_lat_g wurde nie gebaut.
    "Longitudinal_Acc_Raw": 0x76,
    "Lateral_Acc_Raw": 0x75,
    "YawRate_Raw": 0x75,
    "BrakePressure": 0x78,
    "Clutch_Pedal_Position_raw": 0x130,
    "WheelSpeed_1": 0x215,
    "WheelSpeed_2": 0x215,
    "WheelSpeed_3": 0x215,
    "WheelSpeed_4": 0x215,
    "Steering_Wheel_Absolute_Angle": 0x82,
    "MAP_Manifold_absolute_pressure_sensor": 0xFD,
    "CoolantTemp": 0x420,
}

# Mode-22-DIDs aus map_obd_dids.py (2026-09-14, ueber 4 Logs bestaetigt) - als zusaetzliche
# Anker, wo im Log OBD-Traffic vorhanden ist (siehe obd_from_can.py)
KNOWN_DIDS = {"AFR_MZ": 0xDA85, "BFP_PRE_MZ": 0x280A, "ETC_ACT": 0x093C,
              "CPP_PER_MZ": 0x0478, "FLI": 0xF42F,
              # 2026-09-15: zusaetzliche Module, vorher nie dekodiert (nur 0x7E0/0x7E8)
              "STEER_SPD_EPS": 0x3301, "STEER_ANGL_EPS": 0x3302}

# Standard-Mode-1-PIDs, die OBD-Fusion gebuendelt abfragt. Die Antworten kommen als ISO-TP-
# Multiframe und wurden bis 2026-09-15 verworfen - deshalb galten diese Kanaele faelschlich
# als "von der App berechnet".
# ACHTUNG bei 0x44: das ist die COMMANDED equivalence ratio, also das vom Steuergeraet
# ANGEFORDERTE Lambda, KEIN Sondenmesswert. Das Fahrzeug hat zwei Lambdasonden (vorn
# Breitband-Regelsonde, hinten Diagnosesonde hinter dem Kat); ihre gemessenen Werte liefern
# die Mode-1-Sonden-PIDs 0x24/0x25 bzw. 0x34/0x35 - noch nicht abgefragt, siehe
# uds_did_sweep.py --mode1-survey.
KNOWN_MODE1_PIDS = {"OBD1_VehicleSpeed": 0x0D, "OBD1_MAF": 0x10,
                    "OBD1_LambdaCommanded": 0x44, "OBD1_TimingAdvance": 0x0E,
                    "OBD1_EnginePercentTorque": 0x62}


def dbc_unclaimed_bytes(db):
    """{can_id: (dlc, sorted list freier Byte-Indizes, sender)} fuer jede Botschaft."""
    out = {}
    for msg in db.messages:
        claimed = set()
        for sig in msg.signals:
            byte0 = sig.start // 8
            n_bytes = max(1, sig.length // 8)
            claimed.update(range(byte0, byte0 + n_bytes))
        free = sorted(set(range(msg.length)) - claimed)
        sender = msg.senders[0] if msg.senders else "?"
        out[msg.frame_id] = (msg.length, free, sender, msg.name)
    return out


def message_period_s(raw_df, can_id):
    t = raw_df.loc[raw_df["can_id"] == can_id, "t"]
    if len(t) < 5:
        return None
    return float(np.median(np.diff(np.sort(t.to_numpy()))))


def extract_anchors(raw_df, db, decoded_obd, log_has_obd):
    """Alle Anker-Zeitreihen (Name -> (t, val) np-Arrays) fuer dieses Log."""
    anchors = {}
    for sig_name, can_id in ANCHOR_SIGNALS.items():
        msg = db.get_message_by_frame_id(can_id)
        sub = raw_df[raw_df["can_id"] == can_id]
        if sub.empty:
            continue
        t_list, v_list = [], []
        for t, data in sub[["t", "data"]].itertuples(index=False):
            try:
                decoded = msg.decode(data, allow_truncated=True)
            except Exception:
                continue
            if sig_name in decoded:
                t_list.append(t)
                v_list.append(decoded[sig_name])
        if t_list:
            anchors[sig_name] = (np.array(t_list), np.array(v_list, dtype=float))

    if log_has_obd:
        for name, did in KNOWN_DIDS.items():
            series = extract_did_series(decoded_obd, did, mode="mode22")
            if len(series) > 20:
                anchors[f"OBD_{name}"] = (series["t"].to_numpy(), series["raw_value"].to_numpy(dtype=float))
        for name, pid in KNOWN_MODE1_PIDS.items():
            series = extract_did_series(decoded_obd, pid, mode="mode1")
            if len(series) > 20:
                anchors[f"OBD_{name}"] = (series["t"].to_numpy(), series["raw_value"].to_numpy(dtype=float))

    # Abgeleitete Ereignis-Proxys - fuer Flag-Kandidaten ohne sauberen analogen Anker
    if "WheelSpeed_1" in anchors and "BrakePressure" in anchors:
        anchors["PROXY_abs_activity"] = _abs_proxy(anchors)
    if "Lateral_Acc_Raw" in anchors:
        t, v = anchors["Lateral_Acc_Raw"]
        anchors["PROXY_high_lat_g"] = (t, np.abs(v))

    return {k: v for k, v in anchors.items() if v is not None and len(v[0]) > 50}


def _abs_proxy(anchors):
    """Grober ABS-Aktivitaets-Proxy: Streuung der 4 Radgeschwindigkeiten zueinander, nur
    relevant waehrend BrakePressure ueber einem Schwellwert - siehe Plan Phase 3."""
    try:
        ts = [anchors[f"WheelSpeed_{i}"][0] for i in range(1, 5)]
        vs = [anchors[f"WheelSpeed_{i}"][1] for i in range(1, 5)]
        t0, t1 = max(t.min() for t in ts), min(t.max() for t in ts)
        if t1 - t0 < 10:
            return None
        grid = np.arange(t0, t1, 1 / GRID_HZ)
        resampled = [np.interp(grid, t, v) for t, v in zip(ts, vs)]
        spread = np.std(resampled, axis=0)
        t_bp, v_bp = anchors["BrakePressure"]
        bp_on_grid = np.interp(grid, t_bp, v_bp, left=0, right=0)
        spread = np.where(bp_on_grid > 5, spread, 0.0)  # nur waehrend Bremsen relevant
        return grid, spread
    except Exception:
        return None


def _resample(t, v, t0, t1, hz):
    grid = np.arange(t0, t1, 1.0 / hz)
    if len(t) < 2:
        return grid, np.full_like(grid, np.nan)
    order = np.argsort(t)
    return grid, np.interp(grid, t[order], v[order], left=np.nan, right=np.nan)


def _detrend(v, hz, window_s):
    w = max(3, int(window_s * hz))
    med = pd.Series(v).rolling(w, center=True, min_periods=w // 2).median().to_numpy()
    return v - med


def prepare_anchors_for_window(anchors, t0, t1):
    """Anker einmal pro (can_id-)Zeitfenster resamplen+detrenden statt pro Kandidat neu -
    alle Kandidaten derselben Botschaft teilen sich denselben t_arr/dasselbe Fenster, das war
    vorher der dominante, unnoetig wiederholte Kostenfaktor. Rueckgabe: {name: (sa, sa_detrend)}."""
    prepared = {}
    for name, (t_a, v_a) in anchors.items():
        if t_a.min() > t1 or t_a.max() < t0:
            continue
        _, sa = _resample(t_a, v_a, t0, t1, GRID_HZ)
        sad = _detrend(sa, GRID_HZ, DETREND_WINDOW_S)
        prepared[name] = (sa, sad)
    return prepared


def correlate_candidate(t_cand, v_cand, t0, t1, prepared_anchors):
    """Bestes |r| ueber alle vorbereiteten Anker, Pearson+Spearman, roh UND detrended. Ein
    Anker zaehlt nur, wenn SOWOHL die rohe als auch die detrendete Korrelation die Schwelle
    erreichen (Schutz gegen Trend-Artefakte wie die bekannten Kuehlwassertemperatur-
    Scheinkorrelationen aus dem 09-11-Bericht - "nur was beides uebersteht zaehlt").
    Rueckgabe: (anchor_name, method, r_raw, r_detrend) fuer den besten qualifizierenden
    Treffer, oder None."""
    if len(t_cand) < 50 or np.nanstd(v_cand) < 1e-9:
        return None
    _, sc = _resample(t_cand, v_cand, t0, t1, GRID_HZ)
    scd = _detrend(sc, GRID_HZ, DETREND_WINDOW_S)

    best = None
    for name, (sa, sad) in prepared_anchors.items():
        mask = ~np.isnan(sc) & ~np.isnan(sa)
        if mask.sum() < 50:
            continue
        a, b = sc[mask], sa[mask]
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue

        raw_scores = {"pearson": np.corrcoef(a, b)[0, 1]}
        try:
            raw_scores["spearman"] = spearmanr(a, b).statistic
        except Exception:
            pass

        dm = ~np.isnan(scd) & ~np.isnan(sad)
        detrend_scores = {}
        if dm.sum() > 50 and scd[dm].std() > 1e-9 and sad[dm].std() > 1e-9:
            detrend_scores["pearson"] = np.corrcoef(scd[dm], sad[dm])[0, 1]
            try:
                detrend_scores["spearman"] = spearmanr(scd[dm], sad[dm]).statistic
            except Exception:
                pass

        for method in raw_scores:
            r_raw = raw_scores.get(method)
            r_dt = detrend_scores.get(method)
            if r_raw is None or np.isnan(r_raw) or r_dt is None or np.isnan(r_dt):
                continue  # kein detrended Gegenstueck -> nicht qualifiziert
            if abs(r_raw) < MIN_R or abs(r_dt) < MIN_R:
                continue  # beides muss die Schwelle uebersteigen
            score = min(abs(r_raw), abs(r_dt))
            if best is None or score > best[4]:
                best = (name, method, r_raw, r_dt, score)
    return best[:4] if best else None


def is_rolling_counter(values):
    diffs = np.diff(values.astype(int)) % 256
    return np.mean((diffs == 1) | (diffs == 0)) > 0.85


def search_log(can_path, min_period_s=0.2, max_bytes_to_scan=None, verbose=True):
    db = load_db()
    raw = parse_candump(can_path)
    unclaimed = dbc_unclaimed_bytes(db)

    obd_decoded = decode_obd_traffic(raw)
    log_has_obd = len(obd_decoded) > 0
    anchors = extract_anchors(raw, db, obd_decoded, log_has_obd)
    if verbose:
        print(f"{len(raw)} Frames, {len(anchors)} Anker-Zeitreihen "
              f"({'inkl. OBD' if log_has_obd else 'ohne OBD-Traffic'})", flush=True)

    # Botschaften nach Sendeperiode priorisieren (schnelle zuerst)
    periods = {cid: message_period_s(raw, cid) for cid in unclaimed}
    ordered = sorted(unclaimed.items(), key=lambda kv: periods.get(kv[0]) or 999)

    rows = []
    n_scanned = 0
    for can_id, (dlc, free_bytes, sender, msg_name) in ordered:
        period = periods.get(can_id)
        if period is None or period > min_period_s * 10:
            pass  # trotzdem scannen, nur nicht bevorzugt - kein hartes Ausschliessen
        sub = raw[raw["can_id"] == can_id]
        if len(sub) < 100 or not free_bytes:
            continue
        data_arr = np.stack(sub["data"].apply(lambda d: np.frombuffer(d, dtype=np.uint8)).to_numpy())
        t_arr = sub["t"].to_numpy()
        t0, t1 = t_arr.min(), t_arr.max()
        if t1 - t0 < 10:
            continue
        prepared_anchors = prepare_anchors_for_window(anchors, t0, t1)
        if not prepared_anchors:
            continue

        candidates = []
        for b in free_bytes:
            candidates.append((f"b{b}", data_arr[:, b].astype(float), data_arr[:, b]))
        for b in free_bytes:
            if b + 1 in free_bytes:
                raw16_be = (data_arr[:, b].astype(np.uint16) << 8) | data_arr[:, b + 1]
                raw16_le = (data_arr[:, b + 1].astype(np.uint16) << 8) | data_arr[:, b]
                candidates.append((f"b{b}-{b+1}_BE_u", raw16_be.astype(float), None))
                candidates.append((f"b{b}-{b+1}_LE_u", raw16_le.astype(float), None))
                candidates.append((f"b{b}-{b+1}_BE_s", raw16_be.astype(np.int16).astype(float), None))
                candidates.append((f"b{b}-{b+1}_LE_s", raw16_le.astype(np.int16).astype(float), None))

        for label, values, raw_bytes in candidates:
            n_scanned += 1
            if max_bytes_to_scan and n_scanned > max_bytes_to_scan:
                break
            if np.nanstd(values) < 1e-9:
                continue  # konstant
            if raw_bytes is not None and is_rolling_counter(raw_bytes):
                continue  # Rollzaehler, kein Messwert
            hit = correlate_candidate(t_arr, values, t0, t1, prepared_anchors)
            if hit:
                name, method, r_raw, r_dt = hit
                rows.append({
                    "can_id": f"0x{can_id:03X}", "message": msg_name, "sender": sender,
                    "byte": label, "period_s": round(period, 4) if period else None,
                    "anchor": name, "method": method,
                    "r_raw": round(r_raw, 4), "r_detrend": round(r_dt, 4),
                    "raw_min": float(np.nanmin(values)), "raw_max": float(np.nanmax(values)),
                })
        if verbose and n_scanned % 200 < 20:
            print(f"  ... {n_scanned} Kandidaten geprueft, {len(rows)} Treffer bisher", flush=True)

    if not rows:
        return pd.DataFrame(rows)
    df = pd.DataFrame(rows)
    return df.reindex(df["r_detrend"].abs().sort_values(ascending=False).index)


def self_test(can_path):
    """Selbsttest: die bekannten Byte-Belegungen aus dieser Session (siehe DBC-Coverage-Check
    beim Plan-Start) muessen exakt reproduziert werden - RPM/Speed/APP (Bytes 0-5) belegt,
    Bytes 6-7 bleiben frei (bekannt, bereits als 'kein Drehmoment/Last-Signal' widerlegt, siehe
    mx5_can_bus_logging.md) - und ein komplett unbekannter Signal-Kandidat darf nicht
    faelschlich als 'schon belegt' durchrutschen."""
    db = load_db()
    unclaimed = dbc_unclaimed_bytes(db)
    dlc, free, _, _ = unclaimed[0x202]
    assert free == [6, 7], f"0x202 sollte nur Bytes 6-7 frei haben, tatsaechlich: {free}"
    dlc, free_211, _, _ = unclaimed[0x211]
    assert free_211 == list(range(8)), f"0x211 (komplett leer in der DBC) sollte alle 8 Bytes frei zeigen: {free_211}"
    print("Selbsttest OK: DBC-Coverage-Diff reproduziert den bekannten Stand (0x202 nur "
          "Bytes 6-7 frei, 0x211 komplett frei).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", default="data/can/candump-2026-09-12_211833.log")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--max-bytes", type=int, default=None, help="Kandidaten begrenzen (Testlauf)")
    args = ap.parse_args()

    if args.demo:
        self_test(args.log)
        sys.exit(0)

    result = search_log(args.log, max_bytes_to_scan=args.max_bytes)
    print(f"\n{len(result)} Kandidaten mit |r|>={MIN_R}")
    if not result.empty:
        with pd.option_context("display.max_rows", 100, "display.width", 160):
            print(result.to_string(index=False))
    out_path = "results/can_byte_search_" + os.path.basename(args.log).replace(".log", ".csv")
    result.to_csv(out_path, index=False)
    print(f"-> {out_path}")
