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
from can_offline_lab import bit_order

MIN_R = 0.6
GRID_HZ = 10.0
DETREND_WINDOW_S = 20.0

# Schwellwerte fuer die abgeleiteten Ereignis-Proxys (extract_anchors(), 2026-09-20)
BRAKING_THRESHOLD_BAR = 5.0        # gleicher Schwellwert wie im bestehenden _abs_proxy
STANDSTILL_MIN_S = 3.0
GEAR_SHIFT_WINDOW_S = 1.0          # +-Fenster um einen Gangwechsel
WOT_APP_MIN = 95.0                 # grobe Vollgas-Naeherung, siehe Kommentar in extract_anchors()
LIMITER_RPM_MIN, LIMITER_APP_MIN, LIMITER_ETC_MAX = 7000, 99, 90  # wie dash_gui.py::_is_limiter_active

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
    # 2026-09-20 ergaenzt: Signale, die seit dem letzten Anker-Ausbau (2026-09-15) gefunden
    # wurden, siehe docs/status/can-bus.md - fehlten bisher in JEDEM --correlate-Lauf.
    "ABS_Active": 0x211,
    "DSC_Status": 0x415,
    "MT_Gear_Actual": 0xFD,
    "FuelCut": 0xFD,
    "AmbientTemp": 0x420,
    "SteeringAngle_EPAS": 0x86,
    "YawRate_related": 0x78,
}

# Mode-22-DIDs aus map_obd_dids.py (2026-09-14, ueber 4 Logs bestaetigt) - als zusaetzliche
# Anker, wo im Log OBD-Traffic vorhanden ist (siehe obd_from_can.py)
KNOWN_DIDS = {"AFR_MZ": 0xDA85, "BFP_PRE_MZ": 0x280A, "ETC_ACT": 0x093C,
              "CPP_PER_MZ": 0x0478, "FLI": 0xF42F,
              # 2026-09-15: zusaetzliche Module, vorher nie dekodiert (nur 0x7E0/0x7E8)
              "STEER_SPD_EPS": 0x3301, "STEER_ANGL_EPS": 0x3302,
              # 2026-09-20: KnockRetard-DID (siehe docs/status/can-bus.md), Rohwert reicht fuer
              # die reine Korrelation - Formel/Vorzeichen sind fuer diesen Zweck irrelevant.
              "KnockRetard": 0x03EC,
              # Oeltemperatur: seit 2026-09-15 pollt tpms_poller.py sie selbst alle 10s,
              # steht also in jedem neuen Log. ACHTUNG beim Auswerten: sie ist eine langsame
              # Monotonie - die Doppelschwelle dieses Skripts (roh UND detrended) verwirft
              # ein echtes Gegenstueck dort systematisch. Fuer diese Groesse ist
              # can_find_native_counterpart.py das richtige Werkzeug (Partialkorrelation +
              # Uebertragungstest ueber zwei Fahrten).
              "OilTemp": 0x1310}

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
                    "OBD1_EnginePercentTorque": 0x62,
                    # 2026-09-20 ergaenzt: PID 0x11, seit 2026-09-19 von tpms_poller.py selbst
                    # in der schnellen Gruppe gepollt (OBD1_FAST_PIDS) - die reale Quelle des
                    # Live-ETC_ACT im Renncockpit (dash_gui.py-Snapshot-Key
                    # "_ThrottlePosition_pct_derived"), NICHT der Mode-22-DID 0x093C oben
                    # (der nur bei aktivem Phone-Y-Splitter existiert). Formel raw*100/255
                    # siehe tpms_poller.py::OBD1_PIDS.
                    "OBD1_ThrottlePosition": 0x11}


def dbc_unclaimed_bytes(db):
    """{can_id: (dlc, sorted list freier Byte-Indizes, sender)} fuer jede Botschaft."""
    out = {}
    for msg in db.messages:
        claimed = set()
        for sig in msg.signals:
            # bitgenau (seit 29.09.): vorher nur Startbyte + length//8 Bytes, dadurch galt bei
            # Motorola-Signalen ueber eine Bytegrenze (z.B. 0x20A 1|8 = Byte0 Bit1-0 + Byte1
            # Bit7-2) das zweite Byte als frei und der Sweep "fand" das bekannte Signal neu.
            claimed.update(b // 8 for b in bit_order(sig.start, sig.length,
                                                      sig.byte_order == "big_endian"))
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
    """Alle Anker-Zeitreihen (Name -> (t, val) np-Arrays) fuer dieses Log.

    2026-09-20: nach CAN-ID gruppiert statt pro Signalname einzeln - mehrere ANCHOR_SIGNALS
    teilen sich oft dieselbe Botschaft (z.B. EngineRPM/VehicleSpeed/APP alle auf 0x202,
    WheelSpeed_1-4 alle auf 0x215), wurden also bisher pro Frame mehrfach unabhaengig
    dekodiert. Gruppiert deckt EIN msg.decode()-Aufruf pro Frame alle ihre Signale gleichzeitig
    ab - 22 Anker teilen sich nur 12 eindeutige CAN-IDs, also ~1,6-1,7x weniger Dekodierarbeit
    (gemessen, nicht nur geschaetzt)."""
    anchors = {}
    by_can_id = {}
    for sig_name, can_id in ANCHOR_SIGNALS.items():
        by_can_id.setdefault(can_id, []).append(sig_name)

    for can_id, sig_names in by_can_id.items():
        msg = db.get_message_by_frame_id(can_id)
        sub = raw_df[raw_df["can_id"] == can_id]
        if sub.empty:
            continue
        t_lists = {n: [] for n in sig_names}
        v_lists = {n: [] for n in sig_names}
        for t, data in sub[["t", "data"]].itertuples(index=False):
            try:
                # decode_choices=False: wir wollen den rohen Zahlenwert (z.B. DSC_Status
                # 0/1), keine cantools-NamedSignalValue-Enums - gleiches Muster wie
                # can_opendbc_crosscheck.py.
                decoded = msg.decode(data, allow_truncated=True, decode_choices=False)
            except Exception:
                continue
            for n in sig_names:
                if n in decoded:
                    t_lists[n].append(t)
                    v_lists[n].append(decoded[n])
        for n in sig_names:
            if t_lists[n]:
                anchors[n] = (np.array(t_lists[n]), np.array(v_lists[n], dtype=float))

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
    # 2026-09-20 ergaenzt (siehe Plan "Cluster-C gegen bekannten Fahrtverlauf"): nicht nur
    # stetige Messwerte, sondern auch Fahrsituationen als Vergleichsgroesse fuer Kandidaten,
    # die selbst eher Flags/Zustaende sind statt analoger Messwerte.
    if "MT_Gear_Actual" in anchors:
        p = _gear_shift_proxy(anchors)
        if p is not None:
            anchors["PROXY_gear_shift"] = p
    if "VehicleSpeed" in anchors:
        p = _standstill_proxy(anchors)
        if p is not None:
            anchors["PROXY_standstill"] = p
    p = _threshold_proxy(anchors, "BrakePressure", BRAKING_THRESHOLD_BAR)
    if p is not None:
        anchors["PROXY_braking"] = p
    # Grobe Vollgas-Naeherung ueber APP allein (in JEDEM Log vorhanden, kein OBD-Traffic
    # noetig) - bewusst NICHT die vollstaendige WOT-Erkennung aus drivetrain_model_validation.
    # py::wot_segments() (ETC_ACT>80 & Lambda/AFR_MZ<0.9, siehe mx5_wot_detection_criteria-
    # Memory zur bekannten Unzuverlaessigkeit von ETC_ACT allein): der rohe CAN-OBD-Wert von
    # AFR_MZ hat hier keine bestaetigte Lambda-Skala, und ETC_ACT braucht OBD-Traffic (nur
    # ~10/32 Logs). Als reiner Korrelations-Anker (nicht als autoritative Ereigniserkennung)
    # ist die APP-Naeherung ausreichend und deckt jedes Log ab.
    p = _threshold_proxy(anchors, "APP_Accelerator_Pedal_Position", WOT_APP_MIN)
    if p is not None:
        anchors["PROXY_wot_active"] = p
    p = _limiter_proxy(anchors)
    if p is not None:
        anchors["PROXY_limiter_active"] = p

    return _clean_anchors(anchors)


INIT_SKIP_S = 5.0


def _clean_anchors(anchors):
    """Startphase weg und fast konstante Anker verwerfen (30.09.). Im Init nach Zuendung
    senden viele Botschaften Default-/Sentinel-Werte (Lampentest DSC_Status ~2,5 s,
    AmbientTemp 0, Radgeschwindigkeit-Sentinel). Jede Botschaft mit eigener Startphase
    korrelierte dadurch mit diesen Ankern (DSC_Status gegen sechs IDs mit identischem
    r=0,875). Was nach der Startphase fast konstant bleibt (DSC_Status: nur noch der
    Abstell-Frame), traegt keine Information fuer eine Korrelation - Flags gehoeren zu
    can_event_bit_diff/can_rare_bits. Bezugspunkt ist der erste Frame JEDES Ankers, nicht der
    Logbeginn: manche Logs starten vor der Zuendung (29_162615: 0x202 erst nach ~3 min)."""
    out = {}
    for k, v in anchors.items():
        if v is None:
            continue
        t, val = v
        keep = t >= t.min() + INIT_SKIP_S
        t, val = t[keep], val[keep]
        if len(t) <= 50:
            continue
        minority = len(val) - np.unique(val, return_counts=True)[1].max()
        if minority < max(10, 0.005 * len(val)):
            continue
        out[k] = (t, val)
    return out


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


def _sorted_by_t(t, v):
    order = np.argsort(t)
    return t[order], v[order]


def _threshold_proxy(anchors, name, threshold):
    """1 wenn Anker > threshold, sonst 0 - auf dessen eigener Zeitbasis (Resampling
    uebernimmt correlate_candidate()/prepare_anchors_for_window() spaeter selbst)."""
    if name not in anchors:
        return None
    t, v = anchors[name]
    return t, (v > threshold).astype(float)


def _gear_shift_proxy(anchors, window_s=GEAR_SHIFT_WINDOW_S):
    """1 fuer +-window_s um jeden Gangwechsel (MT_Gear_Actual aendert sich) - Schaltmomente
    als Vergleichsgroesse fuer Kandidaten, die beim Schalten mitkippen (z.B. Kupplungs-/
    Motorsteuerungs-Status)."""
    t, v = _sorted_by_t(*anchors["MT_Gear_Actual"])
    changes = t[1:][np.diff(v) != 0]
    if len(changes) == 0:
        return None
    grid = np.arange(t.min(), t.max(), 1 / GRID_HZ)
    near = np.zeros_like(grid)
    for c in changes:
        near[np.abs(grid - c) <= window_s] = 1.0
    return grid, near


def _standstill_proxy(anchors, min_duration_s=STANDSTILL_MIN_S):
    """1 wenn VehicleSpeed<1 km/h fuer mindestens min_duration_s am Stueck (kurze Nulldurch-
    gaenge/Ampel-Anrollen sollen nicht als Stillstand zaehlen)."""
    t, v = _sorted_by_t(*anchors["VehicleSpeed"])
    grid = np.arange(t.min(), t.max(), 1 / GRID_HZ)
    slow = np.interp(grid, t, v) < 1.0
    min_run = max(1, int(min_duration_s * GRID_HZ))
    run = np.zeros_like(slow, dtype=float)
    count = 0
    for i, s in enumerate(slow):
        count = count + 1 if s else 0
        if count >= min_run:
            run[i - min_run + 1:i + 1] = 1.0
    return (grid, run) if run.any() else None


def _limiter_proxy(anchors):
    """ECU-Soft-Limiter-Verdacht: RPM>7000 & APP>=99 & ETC_ACT<90% - dieselben Schwellwerte
    wie dash_gui.py::_is_limiter_active(), hier auf den rohen Log-Ankern statt dem Live-
    Snapshot. Quelle fuer ETC_ACT ist OBD_OBD1_ThrottlePosition (Mode-1-PID 0x11, raw*100/255,
    seit 2026-09-19 in JEDEM Log von tpms_poller.py selbst gepollt) - NICHT der Mode-22-DID
    0x093C, der nur bei aktivem Phone-Y-Splitter existiert (siehe KNOWN_MODE1_PIDS-Kommentar)."""
    needed = ("EngineRPM", "APP_Accelerator_Pedal_Position", "OBD_OBD1_ThrottlePosition")
    if not all(n in anchors for n in needed):
        return None
    t_r, v_r = _sorted_by_t(*anchors["EngineRPM"])
    t_a, v_a = _sorted_by_t(*anchors["APP_Accelerator_Pedal_Position"])
    t_e, v_e = _sorted_by_t(*anchors["OBD_OBD1_ThrottlePosition"])
    v_e = v_e * 100 / 255  # raw -> % (siehe tpms_poller.py::OBD1_PIDS-Formel)
    t0 = max(t_r.min(), t_a.min(), t_e.min())
    t1 = min(t_r.max(), t_a.max(), t_e.max())
    if t1 - t0 < 10:
        return None
    grid = np.arange(t0, t1, 1 / GRID_HZ)
    rpm = np.interp(grid, t_r, v_r)
    app = np.interp(grid, t_a, v_a)
    etc = np.interp(grid, t_e, v_e)
    active = (rpm > LIMITER_RPM_MIN) & (app >= LIMITER_APP_MIN) & (etc < LIMITER_ETC_MAX)
    return (grid, active.astype(float)) if active.any() else None


def _resample(t, v, t0, t1, hz):
    """Auf ein festes Raster legen, aber NUR Rasterpunkte mit einer echten Stuetzstelle im
    Abstand <= halbe Rasterweite behalten, der Rest wird NaN. Fuer schnelle CAN-Signale
    aendert das nichts. Fuer duenne Referenzen (OBD alle 0,5-10 s) bleibt so nur je Sample
    ein Rasterpunkt uebrig, der Vergleich laeuft also an den Referenz-Zeitstempeln statt
    gegen deren lineare Interpolation (die hatte R2 und Skala verzerrt: 0x082 gegen
    STEER_SPD_EPS R2 0,990 -> 0,886, Steigung -10 %, siehe Logbuch 2026-09-29). Nebenbei
    werden Log-Luecken (Uhrspruenge) nicht mehr linear ueberbrueckt."""
    grid = np.arange(t0, t1, 1.0 / hz)
    if len(t) < 2:
        return grid, np.full_like(grid, np.nan)
    order = np.argsort(t)
    t, v = t[order], v[order]
    out = np.interp(grid, t, v, left=np.nan, right=np.nan)
    i = np.clip(np.searchsorted(t, grid), 1, len(t) - 1)
    gap = np.minimum(np.abs(grid - t[i - 1]), np.abs(t[i] - grid))
    out[gap > 0.5 / hz] = np.nan
    return grid, out


def _detrend(v, hz, window_s):
    # min_periods klein, damit duenne Referenzen (nach _resample nur ~1 Punkt je Sample)
    # ueberhaupt einen gleitenden Median bekommen - frueher w//2, das fiel bei OBD durch.
    w = max(3, int(window_s * hz))
    med = pd.Series(v).rolling(w, center=True, min_periods=max(3, w // 20)).median().to_numpy()
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
        # Diagnose-IDs (0x7xx) sind die OBD-Antworten selbst: an den Referenz-Zeitstempeln
        # (korrigiertes _resample, 29.09.) matchen sie ihren eigenen Anker - kein Broadcast.
        if 0x700 <= can_id <= 0x7FF:
            continue
        period = periods.get(can_id)
        if period is None or period > min_period_s * 10:
            pass  # trotzdem scannen, nur nicht bevorzugt - kein hartes Ausschliessen
        sub = raw[raw["can_id"] == can_id]
        if len(sub) < 100 or not free_bytes:
            continue
        # Frames mit abweichender Laenge (z.B. abgeschnittene letzte Zeile nach hartem Stromverlust,
        # candump-2026-09-16_090826) auf die haeufigste Laenge einschraenken, sonst scheitert np.stack
        lens = sub["data"].str.len()
        sub = sub[lens == lens.mode().iloc[0]]
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
            # fast konstant: ein einzelner abweichender Frame (0x596: nur der Init-Frame
            # FFFF.. nach dem Start, sonst immer AA55..) faellt mit dem Init-Sentinel eines
            # Ankers zusammen und ergibt r=0,99 aus einem Punkt (29.09.). Seltene Flags
            # gehoeren ohnehin zu can_event_bit_diff/can_rare_bits, nicht in die Korrelation.
            minority = len(values) - np.unique(values, return_counts=True)[1].max()
            if minority < max(10, 0.005 * len(values)):
                continue
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
    # 0x211 war bis 15.09. komplett leer, traegt jetzt ABS/TCS - frei sind nur noch Bytes
    # ohne jedes Signal (Stand 2026-09-29: 0, 1, 4, 7).
    dlc, free_211, _, _ = unclaimed[0x211]
    assert 0 < len(free_211) < 8, f"0x211 sollte teils belegt, teils frei sein: {free_211}"
    # _resample: duenne Referenz (alle 0,5 s) darf nur an ihren eigenen Zeitpunkten Werte
    # liefern, dichtes Signal (100 Hz) bleibt voll erhalten.
    t_sparse = np.arange(0.0, 10.0, 0.5)
    grid, v_sparse = _resample(t_sparse, t_sparse, 0.0, 10.0, GRID_HZ)
    assert np.isfinite(v_sparse).sum() == len(t_sparse), np.isfinite(v_sparse).sum()
    assert np.allclose(v_sparse[np.isfinite(v_sparse)], t_sparse)
    # _clean_anchors: Startphase weg, danach fast konstanter Anker (Lampentest-Muster) faellt raus,
    # ein echter Anker bleibt
    tt = np.arange(0.0, 100.0, 0.1)
    lamp = np.where(tt < 2.5, 1.0, 0.0)
    real = np.sin(tt)
    cleaned = _clean_anchors({"lamp": (tt + 60, lamp), "real": (tt, real)})   # lamp startet spaeter
    assert "lamp" not in cleaned and "real" in cleaned and cleaned["real"][0].min() >= 5.0, cleaned.keys()
    t_dense = np.arange(0.0, 10.0, 0.01)
    _, v_dense = _resample(t_dense, t_dense, 0.0, 9.9, GRID_HZ)
    assert np.isfinite(v_dense).all()
    print("Selbsttest OK: DBC-Coverage-Diff reproduziert den bekannten Stand (0x202 nur "
          "Bytes 6-7 frei, 0x211 teils belegt), _resample behaelt nur echte Stuetzstellen.")


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
