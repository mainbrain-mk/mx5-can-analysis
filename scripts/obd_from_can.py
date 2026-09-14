"""
OBD-Antworten direkt aus dem CAN-Log dekodieren (MX-5 Projekt).

Zweck: wann immer das Y-Splitter-Kabel genutzt wurde, sind die Handy-OBD-Requests/-Antworten
(0x7E0/0x7E8) selbst im candump-Log enthalten - siehe mx5_can_bus_status.md. Das liefert
Ground-Truth-Werte auf der GLEICHEN Pi-Uhr wie alle anderen CAN-Kanaele, ohne die bisherige
fehleranfaellige Zeit-Synchronisation zwischen CAN-Log und `.dlg`-Export ueber einen
geschaetzten Lag.

Format (ISO-TP Single Frame, gleiches Muster wie tpms_poller.py's decode_response(), nur
log-basiert statt live und ohne feste erwartete DID - wir lesen alles, was als gueltige
Mode-1/Mode-22-Antwort decodierbar ist):
  Request:  [PCI, SID(0x01 oder 0x22), PID/DIDhi, DIDlo?, Padding...]
  Response: [PCI, SID(0x41 oder 0x62), PID/DIDhi, DIDlo?, raw...]
PCI-High-Nibble 0 = Single Frame, Low-Nibble = Nutzlaenge. Multi-Frame-Antworten (PCI 0x1X/
0x2X/0x3X, z.B. die einmalige VIN-Abfrage) werden bewusst uebersprungen statt falsch dekodiert.
"""
import re
import sys
import pandas as pd

from can_log_parser import parse_candump

REQUEST_ID = 0x7E0
RESPONSE_ID = 0x7E8


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


def decode_obd_traffic(df: pd.DataFrame) -> pd.DataFrame:
    """df: Ausgabe von can_log_parser.parse_candump() (Spalten t, can_id, data).

    Gibt Long-Format zurueck: t, direction, mode, id_hex, raw_value, n_bytes - nur Zeilen fuer
    0x7E0/0x7E8. Jede Antwort ist selbstbeschreibend (traegt ihre eigene PID/DID), muss also
    nicht zeitlich mit einem vorherigen Request gepaart werden."""
    sub = df[df["can_id"].isin((REQUEST_ID, RESPONSE_ID))]
    out = []
    for t, can_id, data in sub[["t", "can_id", "data"]].itertuples(index=False):
        decoded = _decode_frame(data)
        if decoded is None:
            continue
        direction, mode, id_, raw_value, raw_bytes = decoded
        out.append((t, direction, mode, id_, raw_value, len(raw_bytes)))
    return pd.DataFrame(out, columns=["t", "direction", "mode", "id_", "raw_value", "n_bytes"])


def extract_did_series(decoded: pd.DataFrame, id_: int, mode: str = "mode22") -> pd.DataFrame:
    """Zeitreihe (t, raw_value) fuer eine einzelne PID/DID, nur Antworten."""
    sel = decoded[(decoded["direction"] == "response") & (decoded["mode"] == mode) & (decoded["id_"] == id_)]
    return sel[["t", "raw_value"]].reset_index(drop=True)


def summarize(decoded: pd.DataFrame) -> pd.DataFrame:
    """Ein Ueberblick pro (mode, id_): Anzahl, Byte-Breite(n), Beispielwerte - fuer die
    manuelle Sichtung, welche DIDs/PIDs ueberhaupt aktiv abgefragt werden."""
    resp = decoded[decoded["direction"] == "response"]
    rows = []
    for (mode, id_), g in resp.groupby(["mode", "id_"]):
        rows.append({
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
    mit plausibler, fast identischer Haeufigkeit auftauchen (siehe mx5_can_bus_status.md)."""
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
