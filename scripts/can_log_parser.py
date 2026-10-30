"""
CAN-Log-Parser fuer candump-Dumps (MX-5 Projekt)

Zweck: rohe `candump -l`-Logs (data/can/*.log) mit dem Community-DBC
(berumiya/CAN_DBC_6thGenMazda, siehe data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc)
dekodieren und als Long-Format-CSV ablegen, passend zum bestehenden
Analyse-Workflow (vgl. vibration_analysis.py: Rohdaten -> abgeleitete Datei).

DBC-Dateien wurden lokal minimal gepatcht (Syntaxfehler im Original-Repo):
- Message-Name mit Bindestrich ("HS_PCM_w_i-ELOOP" -> "_")
- rein numerische Signalnamen ("SG_ 0" -> "SG_ byte0", HS_MRCC)
- eingebettetes "LICENSE"-Pseudo-Frame (0x990, >11bit, sprengt Standard-ID) entfernt
- echte Bit-Ueberlappungen behoben (0x91 LIGHT/FOG_SW, 0x4F2 HUD_Height/HUD_Bright,
  0x40A VIN-Felder mit ungueltiger Byte-Order "@4-"), siehe CM_-Kommentare in den
  DBC-Dateien und docs/logs/can-bus-status.md. Seitdem laedt die Datei auch mit
  strict=True fehlerfrei; strict=False bleibt trotzdem als Sicherheitsnetz gegen
  kuenftige, noch unentdeckte DBC-Fehler bestehen (Ladefehler dort waeren sonst
  fatal statt nur eine Warnung).
decode() protokolliert fehlgeschlagene Frames pro Botschaft (statt sie wie frueher
lautlos zu verschlucken) - genau dieses stille Verschlucken hatte die drei oben
genannten Bugs jahrelang unsichtbar gemacht.
"""

import sys
import os
import logging
import numpy as np
import pandas as pd
import cantools

CAN_DIR = "data/can"
DBC_HSCAN = os.path.join(CAN_DIR, "MX5ND_6thGenMazda_HSCAN_extended.dbc")
DBC_HSCAN_FALLBACK = os.path.join(CAN_DIR, "MX5ND_6thGenMazda_HSCAN.dbc")
DBC_MSCAN = os.path.join(CAN_DIR, "MX5ND_6thGenMazda_MSCAN.dbc")


def load_db(bus="hscan"):
    """HS-CAN und MS-CAN sind zwei getrennte physische Busse mit eigener
    ID-Vergabe - IDs ueberschneiden sich zwischen den DBC-Dateien, duerfen
    also NICHT in dieselbe Datenbank gemerged werden (sonst ueberschreiben
    sich z.B. MS-CAN-Platzhalter mit 0 Signalen und echte HS-CAN-Messages).
    Aktuell wird nur can0 = HS-CAN geloggt (siehe docs/logs/can-bus-status.md).

    HS-CAN laedt bevorzugt die erweiterte DBC (Basis + eigene Funde/Fixes,
    siehe docs/logs/can-bus-status.md) - genau wie status_gui.py auf dem Pi. Die
    reine Basis-Datei bleibt unangetastet als Fallback/Referenz auf den
    berumiya-Upstream-Stand stehen und wird nur geladen, wenn die erweiterte
    Datei mal fehlt."""
    if bus == "hscan":
        path = DBC_HSCAN if os.path.exists(DBC_HSCAN) else DBC_HSCAN_FALLBACK
    else:
        path = DBC_MSCAN
    logging.disable(logging.WARNING)  # cantools warnt bei jeder doppelten BO_-Definition (harmlos, siehe Docstring)
    db = cantools.database.load_file(path, strict=False)
    logging.disable(logging.NOTSET)
    return db


def parse_candump(path):
    """candump -l Zeile: '(1789142696.996778) can0 20A#35C8D45080000F80'

    Defekte Zeilen werden uebersprungen statt den ganzen Lauf abzubrechen.
    Grund: wird candump hart getoetet (Stromverlust an der Powerbank, Pi
    aus), endet die Datei mitten in einer Zeile - genau so passiert bei
    candump-2026-09-15_171047.log (urspr. falsch datiert 092732), das
    dadurch komplett aus der automatischen Pipeline fiel. Die Anzahl uebersprungener Zeilen wird
    gemeldet, damit echte Korruption nicht still bleibt."""
    rows, skipped = [], 0
    with open(path) as f:
        for line in f:
            try:
                ts_str, _, rest = line.split(" ", 2)
                can_id_str, data_str = rest.strip().split("#", 1)
                rows.append((float(ts_str.strip("()")), int(can_id_str, 16), bytes.fromhex(data_str)))
            except ValueError:
                skipped += 1
    if skipped:
        print(f"WARNUNG: {skipped} defekte Zeile(n) in {os.path.basename(path)} uebersprungen "
              f"(abgeschnittenes Log? harter Stromverlust?)", file=sys.stderr)
    return pd.DataFrame(rows, columns=["t", "can_id", "data"])


def passenger_occupied_seconds(can_log_path, settle_s=80.0, gap_s=60.0):
    """Zeitaufgeloeste Beifahrer-Belegung aus 0x340 Bit16 (PassengerSeatOccupied_maybe,
    Herleitung/offene Punkte siehe DBC-Kommentar bei BO_832 HS_RCM und
    docs/logs/can-bus-status.md 2026-09-20). Ein candump-File kann mehrere Zuendungszyklen
    enthalten (z.B. candump-2026-09-11_201950.log, zwei Fahrten mit 18,8h Standzeit dazwischen)
    - Sessions werden deshalb an Luecken > gap_s neu gestartet. Je Session werden die ersten
    settle_s Sekunden (Anlauf-/Kalibrierphase des Sensors, siehe DBC-Kommentar) ignoriert.
    Rueckgabe: (belegt_s, gesamt_s) - beide 0.0, falls die Botschaft im Log fehlt."""
    df = parse_candump(can_log_path)
    sub = df[df["can_id"] == 0x340].sort_values("t").reset_index(drop=True)
    if sub.empty:
        return 0.0, 0.0
    session_id = (sub["t"].diff().fillna(0) > gap_s).cumsum()
    occupied_s, total_s = 0.0, 0.0
    for _, part in sub.groupby(session_id):
        part = part.reset_index(drop=True)
        toff = part["t"] - part["t"].iloc[0]
        win = part[toff >= settle_s].reset_index(drop=True)
        if len(win) < 2:
            continue
        occupied = win["data"].apply(lambda d: d[2] & 1)
        dt = win["t"].diff().shift(-1)
        dt.iloc[-1] = dt.iloc[:-1].median()
        occupied_s += float(dt[occupied == 1].sum())
        total_s += float(dt.sum())
    return occupied_s, total_s


# OBD-Kanaele, die NICHT als CAN-Botschaft gebroadcastet werden, sondern nur als Antwort auf
# eine Diagnoseanfrage im Log stehen (Y-Splitter-Kabel bzw. unser eigener tpms_poller.py).
# Sie durchlaufen nicht die DBC, werden aber hier in dasselbe Long-Format gebracht, damit
# build_datalake.py sie wie jedes andere Signal uebernehmen kann.
#
# OilTemp_OBD (DID 0x1310) ist der Grund fuer diesen Block: die Motoroeltemperatur wird nicht
# gebroadcastet und vom Handy nur sporadisch abgefragt (in 12 Logs zusammen 20 Stichproben).
# Seit 2026-09-15 pollt tpms_poller.py sie selbst alle 10s, damit sie in jeder Fahrt vorliegt.
# Die uebrigen vier sind Standard-Mode-1-PIDs (SAE J1979), die OBD-Fusion gebuendelt abfragt
# und deren Multiframe-Antworten bis 2026-09-15 verworfen wurden.
OBD_CHANNELS = {
    ("mode22", 0x1310): ("OilTemp_OBD", lambda r: r / 100 - 40),
    ("mode1", 0x10): ("MassAirFlow_OBD", lambda r: r / 100),
    ("mode1", 0x44): ("LambdaCommanded_OBD", lambda r: r / 32768),
    ("mode1", 0x0E): ("TimingAdvance_OBD", lambda r: r / 2 - 64),
    ("mode1", 0x62): ("EnginePercentTorque_OBD", lambda r: r - 125),
    # PID 0x11 (2026-09-20): Drosselklappenstellung, laeuft seit demselben Tag zusammen mit
    # Lambda in tpms_poller.py's ungegateter Fast-Gruppe (siehe OBD1_FAST_PIDS) - bisher nur
    # live im Dash sichtbar, hier nachgezogen damit sie auch im Datalake landet.
    ("mode1", 0x11): ("ThrottlePosition_OBD", lambda r: r * 100 / 255),
    # DID 0x03EC (2026-09-20): "KnockRetard", herstellerspezifisches Mode-0x22-UDS-DID (keine
    # SAE-J1979-Standard-PID), identifiziert per Korrelation gegen den Handy-Kanal "KNOCKR"
    # im Y-Splitter-Log candump-2026-09-19_163755/dlg 2026-09-19 163857 - signed_int16(raw)/512
    # trifft 73% der App-Werte bitgenau, R²=0,958 (siehe scripts/tpms_poller.py UDS_FAST_PIDS).
    ("mode22", 0x03EC): ("KnockRetard_OBD", lambda r: (r - 65536 if r >= 32768 else r) / 512),
    # PID 0x42 (2026-09-26 nachgezogen): Steuergeraete-Versorgungsspannung, pollt tpms_poller.py
    # seit 2026-09-16 alle 10 s, landete bisher aber nur im Dash, nicht im Datalake.
    ("mode1", 0x42): ("BatteryVoltage_OBD", lambda r: r / 1000),
    # 2026-09-26 fuer den naechsten Fahrzeugtermin vorbereitet (tpms_poller.OBD1_PIDS, Test C9 in
    # docs/status/can-open-fields.md): Katalysatortemperatur, gemessenes Lambda, Tankfuellstand.
    ("mode1", 0x3C): ("CatalystTemp_OBD", lambda r: r / 10 - 40),
    ("mode1", 0x34): ("LambdaMeasured_OBD", lambda r: (int(r) >> 16) / 32768),
    ("mode1", 0x2F): ("FuelLevel_OBD", lambda r: r * 100 / 255),
}


def decode_obd_channels(df):
    """OBD-Antworten aus den Rohframes in dasselbe Long-Format bringen wie decode().

    can_id wird auf die Antwort-ID gesetzt, message auf "OBD" - so bleibt im Datalake
    nachvollziehbar, dass diese Werte NICHT aus der DBC stammen."""
    from obd_from_can import decode_obd_traffic

    decoded = decode_obd_traffic(df)
    if decoded.empty:
        return pd.DataFrame(columns=["t", "can_id", "message", "signal", "value"])
    resp = decoded[decoded["direction"] == "response"]
    rows = []
    for (mode, id_), (name, formula) in OBD_CHANNELS.items():
        sel = resp[(resp["mode"] == mode) & (resp["id_"] == id_)]
        for t, raw in sel[["t", "raw_value"]].itertuples(index=False):
            rows.append((t, 0x7E8, "OBD", name, formula(raw)))
    return pd.DataFrame(rows, columns=["t", "can_id", "message", "signal", "value"])


def _signal_bits(sig):
    """(Wort, Shift, benoetigte Bytes): Rohwert = (Wort >> Shift) & Maske, Wort = die 8 Datenbytes
    als uint64 - little-endian fuer Intel-, big-endian fuer Motorola-Signale (cantools-Startbit
    = MSB in Saegezahn-Zaehlung)."""
    if sig.byte_order == "little_endian":
        return "le", sig.start, (sig.start + sig.length - 1) // 8 + 1
    lsb = (sig.start // 8) * 8 + 7 - sig.start % 8 + sig.length - 1
    return "be", 63 - lsb, lsb // 8 + 1


def _split_value(val, texts):
    """cantools-Wert -> (Zahl, Text-Code): Enum-Namen (NamedSignalValue) als Text, sonst Zahl."""
    if isinstance(val, (int, float)):
        return float(val), -1
    return np.nan, texts.setdefault(str(val), len(texts))


def _decode_vectorized(msg, idx, data, texts):
    """Alle Frames einer (nicht gemultiplexten) Botschaft auf einmal: Bits per numpy, Skalierung und
    Enum-Namen je eindeutigem Rohwert ueber cantools' eigene Umrechnung - Werte damit exakt wie
    msg.decode(allow_truncated=True), Frame fuer Frame war das ~60 % der Laufzeit."""
    n = len(data)
    dlc = np.fromiter(map(len, data), np.int64, n)
    flat = np.frombuffer(b"".join(data), np.uint8)
    rows = np.repeat(np.arange(n), dlc)  # Frames < 8 Byte rechts mit Nullen auffuellen
    buf = np.zeros((n, 8), np.uint8)
    buf[rows, np.arange(len(flat)) - np.repeat(np.cumsum(dlc) - dlc, dlc)] = flat
    words = {"le": buf.view("<u8").ravel(), "be": buf.view(">u8").ravel()}
    out = []
    for order, sig in enumerate(msg.signals):
        word, shift, need = _signal_bits(sig)
        raw = words[word] >> np.uint64(shift)
        if sig.length < 64:
            raw = raw & np.uint64((1 << sig.length) - 1)
        if sig.is_signed:
            raw = raw.astype(np.int64)
            if sig.length < 64:
                raw = np.where(raw >= 1 << (sig.length - 1), raw - (1 << sig.length), raw)
        ok = dlc >= need  # allow_truncated: Signale ausserhalb eines zu kurzen Frames fehlen
        uniq, inv = np.unique(raw[ok], return_inverse=True)
        lut = [_split_value(sig.conversion.raw_to_scaled(int(u), decode_choices=True), texts) for u in uniq]
        num, txt = (np.array(c).reshape(-1) for c in zip(*lut)) if lut else (np.empty(0), np.empty(0, np.int64))
        out.append((idx[ok], np.full(ok.sum(), order), sig.name, num[inv], txt.astype(np.int64)[inv]))
    return out


def _categorical(codes, names):
    return pd.Categorical.from_codes(codes, categories=list(names))


def decode(df, db):
    """Long-Format: t, can_id, message, signal, value, value_text - Zeilen nach Frame, je Frame in
    Signalreihenfolge (wie Frame fuer Frame mit msg.decode). value ist immer Zahl (NaN bei
    Enum-Signalen mit benanntem Wert), value_text der Enum-Name (KeyState "ON", ...) oder leer."""
    by_id = {m.frame_id: m for m in db.messages}
    undecoded_ids = set()
    decode_errors = {}  # can_id -> (msg_name, count, letzter Fehlertext)
    t_all, data_all = df["t"].to_numpy(), df["data"].to_numpy()
    parts = []  # (frame_idx, signal_order, can_id, msg_name, signal_name(s), Zahl, Text-Code)
    texts = {}
    for can_id, idx in df.groupby("can_id").indices.items():
        msg = by_id.get(can_id)
        if msg is None:
            undecoded_ids.add(can_id)
            continue
        data = data_all[idx]
        if not msg.is_multiplexed() and max(map(len, data)) <= 8:
            for f_idx, order, sig_name, num, txt in _decode_vectorized(msg, idx, data, texts):
                parts.append((f_idx, order, can_id, msg.name, sig_name, num, txt))
            continue
        rows = []  # Multiplex-Botschaften: Signalmenge haengt vom Frame ab -> einzeln
        for i, d in zip(idx, data):
            try:
                decoded = msg.decode(d, allow_truncated=True)
            except Exception as e:
                name, count, _ = decode_errors.get(can_id, (msg.name, 0, None))
                decode_errors[can_id] = (name, count + 1, str(e))
                continue
            rows += [(i, order, sig, *_split_value(val, texts)) for order, (sig, val) in enumerate(decoded.items())]
        if rows:
            f_idx, order, sigs, num, txt = zip(*rows)
            parts.append((np.array(f_idx), np.array(order), can_id, msg.name, np.array(sigs, dtype=object),
                          np.array(num, dtype=np.float64), np.array(txt, dtype=np.int64)))
    # message/signal als Categorical: 45 Mio. Python-Strings je Spalte kosteten ~20 s (CSV identisch)
    msg_names, sig_names = {}, {}
    msg_codes, sig_codes = [], []
    for f, _, _, msg_name, sig, _, _ in parts:
        msg_codes.append(np.full(len(f), msg_names.setdefault(msg_name, len(msg_names))))
        if isinstance(sig, str):
            sig_codes.append(np.full(len(f), sig_names.setdefault(sig, len(sig_names))))
        else:
            sig_codes.append(np.array([sig_names.setdefault(x, len(sig_names)) for x in sig], dtype=np.int64))
    f_idx = np.concatenate([p[0] for p in parts] + [np.empty(0, np.int64)])
    sort = np.lexsort((np.concatenate([p[1] for p in parts] + [np.empty(0, np.int64)]), f_idx))
    result = pd.DataFrame({
        "t": t_all[f_idx[sort]],
        "can_id": np.repeat([p[2] for p in parts], [len(p[0]) for p in parts]).astype(np.int64)[sort],
        "message": _categorical(np.concatenate(msg_codes + [np.empty(0, np.int64)])[sort], msg_names),
        "signal": _categorical(np.concatenate(sig_codes + [np.empty(0, np.int64)])[sort], sig_names),
        "value": np.concatenate([p[5] for p in parts] + [np.empty(0)])[sort],
        "value_text": _categorical(np.concatenate([p[6] for p in parts] + [np.empty(0, np.int64)])[sort], texts),
    })
    if decode_errors:
        print(f"WARNUNG: {len(decode_errors)} Botschaft(en) mit Decode-Fehlern (im DBC bekannt, aber "
              f"Frame(s) nicht dekodierbar - z.B. neuer Bit-Konflikt oder kaputte Signaldefinition):")
        for can_id, (name, count, last_err) in sorted(decode_errors.items()):
            print(f"  0x{can_id:03X} {name}: {count} Frame(s) fehlgeschlagen, zuletzt: {last_err}")
    obd = decode_obd_channels(df)
    if not obd.empty:
        print(f"{len(obd)} OBD-Samples ergaenzt "
              f"({', '.join(sorted(obd['signal'].unique()))})")
        obd["value"] = obd["value"].astype(float)
        obd["value_text"] = pd.Categorical([None] * len(obd), categories=result["value_text"].cat.categories)
        for col in ("message", "signal"):  # gleiche Kategorien, sonst faellt concat auf object zurueck
            cats = result[col].cat.categories.union(pd.Index(obd[col].unique()), sort=False)
            result[col] = result[col].cat.set_categories(cats)
            obd[col] = pd.Categorical(obd[col], categories=cats)
        result = pd.concat([result, obd], ignore_index=True)
    return result, undecoded_ids


def decoded_path(log_path):
    """Seit 31.10.2026 Parquet. Aeltere Logs haben noch <log>_decoded.csv (gleiche Spalten,
    value gemischt Zahl/Enum-Text statt value/value_text) - read_decoded() liest beides."""
    return os.path.splitext(log_path)[0] + "_decoded.parquet"


def write_decoded(decoded, path):
    """Via DuckDB (kein pyarrow noetig), Zeilenreihenfolge bleibt erhalten. Schreiben der CSV
    kostete bei grossen Logs 150-170 s, die Datei war ~10x groesser."""
    import duckdb
    tmp = path + ".tmp"
    con = duckdb.connect()
    try:
        con.register("decoded", decoded)
        con.execute(f"COPY (SELECT * FROM decoded) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
    finally:
        con.close()
    os.replace(tmp, path)


def read_decoded(log_path, signals=None):
    """DataFrame t/can_id/message/signal/value/value_text eines CAN-Logs aus *_decoded.parquet
    (Fallback: alte *_decoded.csv, dort steht ein Enum-Name direkt in value). signals: nur
    diese Signale lesen (Reihenfolge bleibt). Fuers Reverse Engineering: pd.read_parquet geht
    hier nicht (kein pyarrow) - diese Funktion oder duckdb.sql("FROM '<datei>'") nutzen."""
    import duckdb
    path = decoded_path(log_path)
    if not os.path.exists(path):
        return pd.read_csv(os.path.splitext(log_path)[0] + "_decoded.csv")
    con = duckdb.connect()
    try:
        # ORDER BY Pflicht: eine lange IN-Liste wird zum Join und liefert parallel ungeordnet
        # (31.10. gefunden) - Leser verlassen sich auf die Datei-Reihenfolge.
        where = f"WHERE signal IN ({','.join('?' * len(signals))}) " if signals else ""
        return con.execute(f"SELECT * EXCLUDE (file_row_number) FROM read_parquet(?, file_row_number = true) "
                           f"{where}ORDER BY file_row_number", [path, *(signals or [])]).df()
    finally:
        con.close()


if __name__ == "__main__":
    log_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(CAN_DIR, "candump-2026-09-11_180456.log")
    db = load_db()

    raw = parse_candump(log_path)
    t0 = raw["t"].min()
    print(f"{len(raw)} Frames, {raw['can_id'].nunique()} unique IDs, Dauer {raw['t'].max() - t0:.1f}s")

    decoded, undecoded_ids = decode(raw, db)
    decoded["t"] = decoded["t"] - t0
    covered_ids = raw["can_id"].nunique() - len(undecoded_ids)
    print(f"DBC deckt {covered_ids}/{raw['can_id'].nunique()} IDs ab, {len(decoded)} Signal-Samples dekodiert")
    print("Nicht im DBC:", sorted(hex(i) for i in undecoded_ids))

    out_path = decoded_path(log_path)
    write_decoded(decoded, out_path)
    print(f"-> {out_path}")
