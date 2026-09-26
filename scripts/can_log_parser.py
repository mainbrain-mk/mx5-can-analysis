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
    result = pd.DataFrame(out, columns=["t", "can_id", "message", "signal", "value"])
    obd = decode_obd_channels(df)
    if not obd.empty:
        print(f"{len(obd)} OBD-Samples ergaenzt "
              f"({', '.join(sorted(obd['signal'].unique()))})")
        result = pd.concat([result, obd], ignore_index=True)
    return result, undecoded_ids


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
