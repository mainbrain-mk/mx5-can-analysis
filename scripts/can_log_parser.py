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
  DBC-Dateien und mx5_can_bus_status.md. Seitdem laedt die Datei auch mit
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
    Aktuell wird nur can0 = HS-CAN geloggt (siehe mx5_can_bus_status.md).

    HS-CAN laedt bevorzugt die erweiterte DBC (Basis + eigene Funde/Fixes,
    siehe mx5_can_bus_status.md) - genau wie status_gui.py auf dem Pi. Die
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
    """candump -l Zeile: '(1789142696.996778) can0 20A#35C8D45080000F80'"""
    rows = []
    with open(path) as f:
        for line in f:
            ts_str, _, rest = line.split(" ", 2)
            can_id_str, data_str = rest.strip().split("#", 1)
            rows.append((float(ts_str.strip("()")), int(can_id_str, 16), bytes.fromhex(data_str)))
    return pd.DataFrame(rows, columns=["t", "can_id", "data"])


def decode(df, db):
    """Long-Format: t, can_id, message, signal, value"""
    by_id = {m.frame_id: m for m in db.messages}
    out = []
    undecoded_ids = set()
    decode_errors = {}  # can_id -> (msg_name, count, letzter Fehlertext)
    for t, can_id, data in df[["t", "can_id", "data"]].itertuples(index=False):
        msg = by_id.get(can_id)
        if msg is None:
            undecoded_ids.add(can_id)
            continue
        try:
            decoded = msg.decode(data, allow_truncated=True)
        except Exception as e:
            name, count, _ = decode_errors.get(can_id, (msg.name, 0, None))
            decode_errors[can_id] = (name, count + 1, str(e))
            continue
        for sig, val in decoded.items():
            out.append((t, can_id, msg.name, sig, val))
    if decode_errors:
        print(f"WARNUNG: {len(decode_errors)} Botschaft(en) mit Decode-Fehlern (im DBC bekannt, aber "
              f"Frame(s) nicht dekodierbar - z.B. neuer Bit-Konflikt oder kaputte Signaldefinition):")
        for can_id, (name, count, last_err) in sorted(decode_errors.items()):
            print(f"  0x{can_id:03X} {name}: {count} Frame(s) fehlgeschlagen, zuletzt: {last_err}")
    return pd.DataFrame(out, columns=["t", "can_id", "message", "signal", "value"]), undecoded_ids


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

    out_path = os.path.splitext(log_path)[0] + "_decoded.csv"
    decoded.to_csv(out_path, index=False)
    print(f"-> {out_path}")
