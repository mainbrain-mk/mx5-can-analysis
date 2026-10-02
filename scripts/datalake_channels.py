"""Kanalzugriff fuer die Modellskripte: CAN fuehrt, OBD/Handy nur noch als Rueckfall (02.10.2026).

Die Modellskripte (drivetrain_model_validation, top_speed_validation, coastdown_analysis,
steering_*, partial_load_model) fragen historisch die OBD-Namen der Handy-.dlg ab (TM_GEST,
CPP_PER_MZ, ...). Reine CAN-Fahrten haben diese Kanaele nicht und fielen dadurch still aus
jeder Auswertung (seit 27.09. kommt keine .dlg mehr). load_channel() loest den angefragten
Namen deshalb in eine Quellenliste auf: zuerst der CAN-Kanal (in die Einheit des OBD-Kanals
umgerechnet), dann der OBD-Kanal selbst. Pro Log gilt die erste Quelle mit Daten.

Umrechnungen gegen 26 Fahrten mit Handy+Pi gleichzeitig geprueft (1-Hz-Mediane, 02.10.):
  Gang      MT_Gear_Status == TM_GEST (gleiche Codierung, 86 % exakt gleich, Rest Schaltmomente)
  Kupplung  % = 0,5 * ClutchPosition_CAN_raw (exakte Skala laut can-bus.md 29.09.; Paarfit 0,48)
  Bremse    kPa = 100 * BrakePressure_CAN (bar); Paarfit 97 (Bremsdruck-Nachkalibrierung 29.09.)
  Lenkwinkel SteeringAngle_CAN 1:1 (Paarfit 0,96 durch den 1-s-Versatz, frueher r=0,999)
  VehicleSpeed/APP/EngineRPM tragen in beiden Quellen denselben Namen und dieselbe Skala.
  Drosselklappe ETC_ACT [Grad] = 1,0534 * (ThrottlePosition_CAN [%] - 9,80) + 0,18: Endpunkte geschlossen
            (TP-Minimum 9,80 % <-> ETC-Minimum 0,18 Grad) und offen (91,87 % <-> 86,63 Grad, je p99,9).
            Gegenprobe ohne gemeinsame Fahrt ueber das Gaspedal: Mediane je (APP, Drehzahl)-Bin aus
            Handy- (ETC) und CAN-Logs (TP), 125 Bins, ETC = 1,022*TP - 10,2, r=0,97 (02.10.).
  Motormoment ActualEnginePercentTorque <- Broadcast 0x167 (gegen PID 0x62 R2 0,96, DBC-Kommentar).
Bewusst NICHT abgebildet: AFR_MZ (gemessenes Lambda) ist nicht LambdaCommanded_CAN (Soll, r=0,74).

"Lager" (GPS-Kurs) liefert nur das Handy; fuer CAN-Fahrten wird er aus Breite/Laenge berechnet.

Doppelzaehlung: Liefen Handy und Pi gleichzeitig, steht dieselbe Fahrt zweimal im Datalake
(dlg-Log und candump-Log). load_channel() verwirft deshalb bei dlg-Logs alle Samples, deren
Zeit von einem CAN-Log abgedeckt ist, der denselben Kanal selbst liefert - dort gilt der
CAN-Log. Kanaele ohne CAN-Quelle (z.B. AFR_MZ) bleiben vollstaendig.
"""

from collections import OrderedDict

import numpy as np
import pandas as pd

# angefragter Name -> [(Datalake-Kanal, Faktor, Offset)]: Wert = Faktor * roh + Offset
_TP_TO_ETC = 1.0534
SOURCES = {
    "TM_GEST": [("MT_Gear_Status", 1.0, 0.0), ("TM_GEST", 1.0, 0.0)],
    "CPP_PER_MZ": [("ClutchPosition_CAN_raw", 0.5, 0.0), ("CPP_PER_MZ", 1.0, 0.0)],
    "BFP_PRE_MZ": [("BrakePressure_CAN", 100.0, 0.0), ("BFP_PRE_MZ", 1.0, 0.0)],
    "STEER_ANGL_EPS": [("SteeringAngle_CAN", 1.0, 0.0), ("STEER_ANGL_EPS", 1.0, 0.0)],
    "ETC_ACT": [("ThrottlePosition_CAN", _TP_TO_ETC, 0.18 - _TP_TO_ETC * 9.80), ("ETC_ACT", 1.0, 0.0)],
    "ActualEnginePercentTorque": [("ActualEnginePercentTorque_CAN", 1.0, 0.0), ("ActualEnginePercentTorque", 1.0, 0.0)],
}

# dlg-Logs: start_time_local ist in Wahrheit UTC (bekanntes Mislabeling, siehe
# build_datalake.ingest_dlg_gps); CAN-Logs: echte Ortszeit.
_LOCAL_TZ = "Europe/Berlin"
_covered_cache = {}
# Die Skripte arbeiten Log fuer Log; segment_grade() laedt GPS aber je Segment neu - ohne Cache
# kostete das 0,8 s je Abfrage (Vollscan ueber 250 Mio. Zeilen), drivetrain lief 26 min (02.10.).
_CHANNEL_CACHE_SIZE = 32
_channel_cache = OrderedDict()
_has_cache = {}


def _query(con, log_id, channel, with_timestamp):
    cols = "t_elapsed_s AS t, timestamp_local, value" if with_timestamp else "t_elapsed_s AS t, value"
    # channel_original+value als Tiebreaker: deterministische Reihenfolge bei gleichem t
    # (siehe drivetrain_model_validation.load_channel-Historie)
    return con.execute(
        f"SELECT {cols} FROM measurements WHERE log_id = ? AND channel = ? AND value IS NOT NULL "
        "ORDER BY t_elapsed_s, channel_original, value", [log_id, channel]).fetchdf()


def can_covered_intervals(con):
    """{dlg_log_id: [(t0, t1, can_log_id), ...]} in t_elapsed_s des dlg-Logs."""
    key = con
    if key not in _covered_cache:
        logs = con.execute("SELECT log_id, source_format, start_time_local, duration_s FROM logs").fetchdf()
        start = pd.to_datetime(logs["start_time_local"])
        is_dlg = logs["source_format"] == "dlg"
        is_can = logs["source_format"].str.startswith("can")
        epoch = pd.Series(np.nan, index=logs.index)
        epoch[is_dlg] = start[is_dlg].dt.tz_localize("UTC").map(pd.Timestamp.timestamp)
        epoch[is_can] = start[is_can].dt.tz_localize(_LOCAL_TZ, ambiguous="NaT",
                                                     nonexistent="NaT").map(pd.Timestamp.timestamp)
        can = [(e, e + d, lid) for lid, e, d in zip(logs["log_id"][is_can], epoch[is_can],
                                                    logs["duration_s"][is_can]) if np.isfinite(e)]
        covered = {}
        for lid, e, d in zip(logs["log_id"][is_dlg], epoch[is_dlg], logs["duration_s"][is_dlg]):
            iv = [(max(c0, e) - e, min(c1, e + d) - e, cl) for c0, c1, cl in can if c0 < e + d and c1 > e]
            if iv:
                covered[lid] = iv
        _covered_cache[key] = covered
    return _covered_cache[key]


def _can_has(con, can_log_id, channel):
    key = (con, can_log_id, channel)
    if key not in _has_cache:
        probe = "Breite" if channel == "Lager" else channel  # Kurs wird aus GPS berechnet
        sources = [src[0] for src in SOURCES.get(probe, [(probe,)])]
        _has_cache[key] = con.execute(
            f"SELECT 1 FROM measurements WHERE log_id = ? AND channel IN ({','.join('?' * len(sources))}) "
            "LIMIT 1", [can_log_id, *sources]).fetchone() is not None
    return _has_cache[key]


def _drop_can_covered(con, log_id, channel, df):
    intervals = can_covered_intervals(con).get(log_id)
    if not intervals or df.empty:
        return df
    keep = np.ones(len(df), dtype=bool)
    t = df["t"].to_numpy()
    for t0, t1, can_log_id in intervals:
        if _can_has(con, can_log_id, channel):
            keep &= ~((t >= t0) & (t <= t1))
    return df[keep].reset_index(drop=True)


def _heading_from_gps(con, log_id, with_timestamp):
    """GPS-Kurs in Grad (0 = Nord, im Uhrzeigersinn) aus aufeinanderfolgenden Positionen."""
    lat = _query(con, log_id, "Breite", with_timestamp)
    lon = _query(con, log_id, "Länge", False)
    if len(lat) < 2 or len(lon) < 2:
        return lat.iloc[0:0]
    t = lat["t"].to_numpy()
    phi = np.radians(lat["value"].to_numpy())
    lam = np.radians(np.interp(t, lon["t"].to_numpy(), lon["value"].to_numpy()))
    dlam = np.diff(lam)
    y = np.sin(dlam) * np.cos(phi[1:])
    x = np.cos(phi[:-1]) * np.sin(phi[1:]) - np.sin(phi[:-1]) * np.cos(phi[1:]) * np.cos(dlam)
    moved = (np.diff(phi) != 0) | (dlam != 0)  # Stand: kein Kurs
    out = lat.iloc[1:].copy()
    out["value"] = np.degrees(np.arctan2(y, x)) % 360
    return out[moved].reset_index(drop=True)


def load_channel(con, log_id, channel, with_timestamp=False):
    """Spalten ['t', ('timestamp_local'), 'value'] fuer den angefragten (OBD-)Kanalnamen, aus der
    ersten Quelle mit Daten, in der Einheit des OBD-Kanals. Leer, wenn keine Quelle Daten hat.
    Liefert eine Kopie - Aufrufer duerfen das Ergebnis veraendern."""
    key = (con, log_id, channel, with_timestamp)  # con selbst, nicht id(): ids werden wiederverwendet
    if key in _channel_cache:
        _channel_cache.move_to_end(key)
    else:
        _channel_cache[key] = _load_uncached(con, log_id, channel, with_timestamp)
        if len(_channel_cache) > _CHANNEL_CACHE_SIZE:
            _channel_cache.popitem(last=False)
    return _channel_cache[key].copy()


def _load_uncached(con, log_id, channel, with_timestamp):
    df = None
    for source, scale, offset in SOURCES.get(channel, [(channel, 1.0, 0.0)]):
        df = _query(con, log_id, source, with_timestamp)
        if len(df):
            if (scale, offset) != (1.0, 0.0):
                df["value"] = df["value"] * scale + offset
            break
    if channel == "Lager" and not len(df):
        df = _heading_from_gps(con, log_id, with_timestamp)
    return _drop_can_covered(con, log_id, channel, df)


def logs_with(con, channel):
    """Sortierte log_ids, die den Kanal aus irgendeiner Quelle haben."""
    sources = [src[0] for src in SOURCES.get(channel, [(channel,)])]
    rows = con.execute(
        f"SELECT DISTINCT log_id FROM measurements WHERE channel IN ({','.join('?' * len(sources))})",
        sources).fetchall()
    return sorted(r[0] for r in rows)


if __name__ == "__main__":
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE logs (log_id VARCHAR, source_format VARCHAR, start_time_local TIMESTAMP, "
                "duration_s DOUBLE)")
    con.execute("CREATE TABLE measurements (log_id VARCHAR, channel VARCHAR, channel_original VARCHAR, "
                "t_elapsed_s DOUBLE, timestamp_local TIMESTAMP, value DOUBLE)")
    # dlg 10:00-11:00 UTC; CAN 12:30-13:00 Ortszeit (CEST = 10:30-11:00 UTC) -> dlg t 1800-3600 gedeckt
    con.execute("INSERT INTO logs VALUES ('2026-09-26 120000', 'dlg', '2026-09-26 10:00:00', 3600), "
                "('candump-2026-09-26_123000', 'can+gps', '2026-09-26 12:30:00', 1800)")
    rows = [("2026-09-26 120000", ch, t, 40.0) for t in (100.0, 2000.0) for ch in ("CPP_PER_MZ", "ETC_ACT")]
    rows += [("candump-2026-09-26_123000", "ClutchPosition_CAN_raw", 5.0, 80.0)]
    rows += [("candump-2026-09-26_123000", "Breite", t, lat) for t, lat in ((0, 52.0), (1, 52.001))]
    rows += [("candump-2026-09-26_123000", "Länge", t, 13.0) for t in (0, 1)]
    con.executemany("INSERT INTO measurements VALUES (?, ?, ?, ?, NULL, ?)",
                    [(l, c, c, t, v) for l, c, t, v in rows])
    dlg = load_channel(con, "2026-09-26 120000", "CPP_PER_MZ")
    assert dlg["t"].tolist() == [100.0], dlg  # t=2000 liegt im CAN-Log -> verworfen
    etc = load_channel(con, "2026-09-26 120000", "ETC_ACT")
    assert etc["t"].tolist() == [100.0, 2000.0], etc  # CAN-Log hat keine Drosselklappe -> bleibt
    con.execute("INSERT INTO measurements VALUES ('candump-2026-09-26_123000', 'ThrottlePosition_CAN', "
                "'ThrottlePosition_CAN', 1.0, NULL, 91.87)")
    assert abs(load_channel(con, "candump-2026-09-26_123000", "ETC_ACT")["value"].iloc[0] - 86.63) < 0.01
    can = load_channel(con, "candump-2026-09-26_123000", "CPP_PER_MZ")
    assert can["value"].tolist() == [40.0], can  # 0,5 * roh
    heading = load_channel(con, "candump-2026-09-26_123000", "Lager")
    assert abs(heading["value"].iloc[0]) < 1e-6, heading  # nach Norden
    assert logs_with(con, "CPP_PER_MZ") == ["2026-09-26 120000", "candump-2026-09-26_123000"]
    assert load_channel(con, "candump-2026-09-26_123000", "TM_GEST").empty
    print("datalake_channels: Selbsttest OK")
