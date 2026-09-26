"""
OBD-Antworten direkt aus dem CAN-Log dekodieren (MX-5 Projekt).

Zweck: wann immer das Y-Splitter-Kabel genutzt wurde, sind die Handy-OBD-Requests/-Antworten
(0x7E0/0x7E8) selbst im candump-Log enthalten - siehe docs/logs/can-bus-status.md. Das liefert
Ground-Truth-Werte auf der GLEICHEN Pi-Uhr wie alle anderen CAN-Kanaele, ohne die bisherige
fehleranfaellige Zeit-Synchronisation zwischen CAN-Log und `.dlg`-Export ueber einen
geschaetzten Lag.

Format (ISO-TP Single Frame, gleiches Muster wie tpms_poller.py's decode_response(), nur
log-basiert statt live und ohne feste erwartete DID - wir lesen alles, was als gueltige
Mode-1/Mode-22-Antwort decodierbar ist):
  Request:  [PCI, SID(0x01 oder 0x22), PID/DIDhi, DIDlo?, Padding...]
  Response: [PCI, SID(0x41 oder 0x62), PID/DIDhi, DIDlo?, raw...]
PCI-High-Nibble 0 = Single Frame, Low-Nibble = Nutzlaenge.

2026-09-15: Multi-Frame-Antworten (ISO-TP, PCI 0x1X First Frame + 0x2X Consecutive Frames)
werden jetzt zusammengesetzt statt uebersprungen. Das war eine teure Luecke: OBD-Fusion
buendelt mehrere Mode-1-PIDs in EINE Anfrage (z.B. `06 01 0D 10 44 0E 62` = Speed, MAF,
CommandEquivalenceRatio, TimingAdvance, ActualEnginePercentTorque), und die Antwort darauf
passt nicht in einen Single Frame. Weil wir sie verworfen haben, galten genau diese Kanaele
im Projekt faelschlich als "von der App clientseitig berechnet, keine eigene Anfrage"
(docs/logs/can-bus-status.md, 2026-09-14) - tatsaechlich sind es echte, gemessene Werte.
"""
import re
import sys
import pandas as pd

from can_log_parser import parse_candump

REQUEST_ID = 0x7E0
RESPONSE_ID = 0x7E8

# Alle UDS-Header-Paare, die in unseren Logs tatsaechlich Verkehr zeigen (2026-09-15).
# Bis dahin wurde NUR das PCM-Paar dekodiert - die anderen drei liefen jahrelang mit und
# wurden verworfen. Modulzuordnung: PCM bestaetigt; EPS/DSC aus der Kombination von
# gepollter DID und externer ND3-Quelle (drewid74/2024-nd3-mazda-obdii) erschlossen.
ECU_HEADERS = {
    0x7E0: ("PCM", 0x7E8),    # Motorsteuergeraet - die bekannten 6 Mode-22-DIDs
    0x730: ("EPS", 0x738),    # Lenkung: DID 0x3301/0x3302, vom Handy mit voller Rate gepollt
    0x760: ("DSC", 0x768),    # Fahrdynamik/ABS: DID 0x2B0D = Bremspedalstellung (ND3-Quelle)
    0x720: ("TPMS", 0x728),   # unser eigener tpms_poller.py
}
_RESP_TO_ECU = {resp: name for name, resp in ECU_HEADERS.values()}
_REQ_TO_ECU = {req: name for req, (name, _) in ECU_HEADERS.items()}


def _decode_frame(data: bytes):
    """Ein einzelner Frame-Payload (8 Byte) -> (direction, sid, id_, raw_int, raw_bytes) oder None."""
    pci = data[0]
    if pci >> 4 != 0:
        return None  # Multi-Frame (First/Consecutive/Flow Control) - nicht unterstuetzt, sauber ueberspringen
    length = pci & 0x0F
    if length < 2 or length > 7:
        return None
    payload = data[1:1 + length]
    sid = payload[0]
    if sid == 0x22 and length >= 3:
        return ("request", "mode22", (payload[1] << 8) | payload[2], None, b"")
    if sid == 0x01 and length >= 2:
        return ("request", "mode1", payload[1], None, b"")
    if sid == 0x62 and length >= 4:
        id_ = (payload[1] << 8) | payload[2]
        raw = payload[3:length]
        return ("response", "mode22", id_, int.from_bytes(raw, "big"), raw)
    if sid == 0x41 and length >= 3:
        id_ = payload[1]
        raw = payload[2:length]
        return ("response", "mode1", id_, int.from_bytes(raw, "big"), raw)
    return None  # z.B. 0x7F Negative Response, oder Diagnose-Session-Frames - kein Interesse


# Nutzdatenlaenge der Mode-1-PIDs, die in unseren Logs vorkommen (SAE J1979). Wird nur zum
# Zerlegen einer Mehr-PID-Antwort gebraucht; unbekannte PID -> Rest der Antwort verwerfen,
# lieber nichts als falsch zugeordnete Bytes.
MODE1_PID_LEN = {
    0x04: 1, 0x05: 1, 0x0B: 1, 0x0C: 2, 0x0D: 1, 0x0E: 1, 0x0F: 1, 0x10: 2, 0x11: 1,
    0x1F: 2, 0x21: 2, 0x2F: 1, 0x31: 2, 0x33: 1, 0x34: 4, 0x3C: 2, 0x42: 2, 0x43: 2, 0x44: 2, 0x45: 1,
    0x46: 1, 0x47: 1, 0x49: 1, 0x4A: 1, 0x4C: 1, 0x5C: 1, 0x5E: 2, 0x62: 1,
}


def _split_mode1_multi(payload: bytes):
    """`41 <pid> <data..> <pid> <data..> ...` -> [(pid, raw_bytes), ...].

    Abbruch bei unbekannter PID oder wenn die Laenge nicht aufgeht - eine halb geratene
    Zerlegung waere schlimmer als gar keine (vgl. den TM_GEST-Dual-Column-Bug)."""
    out, i = [], 1
    while i < len(payload):
        pid = payload[i]
        n = MODE1_PID_LEN.get(pid)
        if n is None or i + 1 + n > len(payload):
            break
        out.append((pid, payload[i + 1:i + 1 + n]))
        i += 1 + n
    return out


def _reassemble(sub: pd.DataFrame):
    """ISO-TP: Single Frames unveraendert, First/Consecutive Frames je Antwort-ID zusammen-
    setzen. Liefert (t, can_id, payload) - payload OHNE PCI, in Sendereihenfolge."""
    pending = {}
    for t, can_id, data in sub[["t", "can_id", "data"]].itertuples(index=False):
        if not data:
            continue
        kind = data[0] >> 4
        if kind == 0:                                     # Single Frame
            length = data[0] & 0x0F
            if 2 <= length <= 7:
                yield t, can_id, bytes(data[1:1 + length])
        elif kind == 1:                                   # First Frame
            total = ((data[0] & 0x0F) << 8) | data[1]
            pending[can_id] = [t, total, bytearray(data[2:8])]
        elif kind == 2 and can_id in pending:             # Consecutive Frame
            t0, total, buf = pending[can_id]
            buf += data[1:8]
            if len(buf) >= total:
                yield t0, can_id, bytes(buf[:total])
                del pending[can_id]
        # kind == 3 (Flow Control) ignorieren - trägt keine Nutzdaten


def decode_obd_traffic(df: pd.DataFrame) -> pd.DataFrame:
    """df: Ausgabe von can_log_parser.parse_candump() (Spalten t, can_id, data).

    Gibt Long-Format zurueck: t, ecu, direction, mode, id_, raw_value, n_bytes - fuer ALLE
    Header-Paare aus ECU_HEADERS. Jede Antwort ist selbstbeschreibend (traegt ihre eigene
    PID/DID), muss also nicht zeitlich mit einem Request gepaart werden."""
    ids = set(ECU_HEADERS) | set(name_resp[1] for name_resp in ECU_HEADERS.values())
    sub = df[df["can_id"].isin(ids)]
    out = []
    for t, can_id, payload in _reassemble(sub):
        ecu = _REQ_TO_ECU.get(can_id) or _RESP_TO_ECU.get(can_id, "?")
        sid = payload[0]
        if sid == 0x41:                       # Mode-1-Antwort, evtl. mehrere PIDs
            for pid, raw in _split_mode1_multi(payload):
                out.append((t, ecu, "response", "mode1", pid,
                            int.from_bytes(raw, "big"), len(raw)))
        elif sid == 0x62 and len(payload) >= 4:
            id_ = (payload[1] << 8) | payload[2]
            raw = payload[3:]
            out.append((t, ecu, "response", "mode22", id_, int.from_bytes(raw, "big"), len(raw)))
        elif sid == 0x22 and len(payload) >= 3:
            out.append((t, ecu, "request", "mode22", (payload[1] << 8) | payload[2], None, 0))
        elif sid == 0x01 and len(payload) >= 2:
            for pid in payload[1:]:
                out.append((t, ecu, "request", "mode1", pid, None, 0))
    return pd.DataFrame(out, columns=["t", "ecu", "direction", "mode", "id_", "raw_value", "n_bytes"])


def extract_did_series(decoded: pd.DataFrame, id_: int, mode: str = "mode22",
                       ecu: str | None = None) -> pd.DataFrame:
    """Zeitreihe (t, raw_value) fuer eine einzelne PID/DID, nur Antworten.

    `ecu` nur noetig, falls zwei Module dieselbe DID-Nummer benutzen - bisher disjunkt."""
    sel = decoded[(decoded["direction"] == "response") & (decoded["mode"] == mode) & (decoded["id_"] == id_)]
    if ecu is not None:
        sel = sel[sel["ecu"] == ecu]
    return sel[["t", "raw_value"]].reset_index(drop=True)


def summarize(decoded: pd.DataFrame) -> pd.DataFrame:
    """Ein Ueberblick pro (mode, id_): Anzahl, Byte-Breite(n), Beispielwerte - fuer die
    manuelle Sichtung, welche DIDs/PIDs ueberhaupt aktiv abgefragt werden."""
    resp = decoded[decoded["direction"] == "response"]
    rows = []
    for (ecu, mode, id_), g in resp.groupby(["ecu", "mode", "id_"]):
        rows.append({
            "ecu": ecu,
            "mode": mode,
            "id_hex": f"{id_:04X}" if mode == "mode22" else f"{id_:02X}",
            "n": len(g),
            "n_bytes": sorted(g["n_bytes"].unique().tolist()),
            "raw_min": g["raw_value"].min(),
            "raw_max": g["raw_value"].max(),
        })
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)


def _demo():
    """Selbsttest: die 6 bekannten hochfrequenten Mode-22-DIDs muessen im Y-Splitter-Log
    mit plausibler, fast identischer Haeufigkeit auftauchen (siehe docs/logs/can-bus-status.md)."""
    log_path = "data/can/candump-2026-09-12_211833.log"
    raw = parse_candump(log_path)
    decoded = decode_obd_traffic(raw)
    summary = summarize(decoded)
    known_dids = {"DA85", "280A", "F4A4", "F42F", "093C", "0478"}
    found = set(summary[summary["mode"] == "mode22"]["id_hex"])
    missing = known_dids - found
    assert not missing, f"bekannte DIDs fehlen: {missing}"
    counts = summary.set_index("id_hex").loc[list(known_dids), "n"]
    assert counts.min() > 2000, f"unplausibel wenige Treffer: {counts.to_dict()}"
    assert (counts.max() - counts.min()) < 50, f"Haeufigkeiten sollten fast gleich sein: {counts.to_dict()}"
    print("Selbsttest OK:", counts.to_dict())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        _demo()
        sys.exit(0)

    log_path = sys.argv[1] if len(sys.argv) > 1 else "data/can/candump-2026-09-12_211833.log"
    raw = parse_candump(log_path)
    decoded = decode_obd_traffic(raw)
    print(f"{len(decoded)} OBD-Frames dekodiert (von {len(raw)} Gesamt-Frames)")
    summary = summarize(decoded)
    with pd.option_context("display.max_rows", 50, "display.width", 120):
        print(summary)
