"""
TPMS-Abfrage (Reifendruck + -temperatur) per aktivem UDS-Request auf can0.

Anders als der Rest des Loggers ist das hier AKTIV: es sendet selbst
Anfragen auf den Bus (Mode 0x22 ReadDataByIdentifier), statt nur passiv
mitzulesen wie candump/session_logger.py/can_log_parser.py. Grund: das
Kombiinstrument broadcastet Reifendruck/-temperatur nicht periodisch auf
HS-CAN (siehe mx5_can_bus_status.md, systematische Byte-Suche fand nichts) -
es muss einzeln per Diagnose-PID abgefragt werden.

Request-Header 0x720, PIDs vom Nutzer bereits per Handy-OBD-Adapter live
verifiziert (funktioniert also ueber denselben OBD-Port, den auch der
CANable-Adapter nutzt - keine zweite/MS-CAN-Verkabelung noetig):

  PID     Signal          Formel
  0x2A05  Tire1 Druck     (raw*1373/1000)/100  bar
  0x2A06  Tire2 Druck     "
  0x2A07  Tire3 Druck     "
  0x2A08  Tire4 Druck     "
  0x2A0A  Tire1 Temp      raw-50  C
  0x2A0B  Tire2 Temp      "
  0x2A0C  Tire3 Temp      "
  0x2A0D  Tire4 Temp      "

NICHT bekannt: welches Rad hinter Tire1-4 steckt (Community-Berichte
widersprechen sich) - muss per gezielter Druckaenderung an einem einzelnen
Rad zugeordnet werden, wie schon bei anderen CAN-Kalibrierungen in diesem
Projekt ueblich.

NICHT live bestaetigt: die Antwort-Header-ID. Ueblich (ISO-15765) waere
Request+8 (0x728) - das Skript prueft das nicht hart, sondern hoert nach
jeder Anfrage auf ALLE IDs in RESPONSE_ID_RANGE und meldet auch unerwartete
Frames, damit sich die echte ID beim ersten Livetest ablesen laesst.

ponytail: nur Single-Frame-ISO-TP (kein isotp-Paket/Flow-Control) - reicht,
weil Request (3 Byte) und Antwort (1 Datenbyte) beide in einen einzelnen
CAN-Frame passen. Falls ein Fahrzeug/PID mal eine Multi-Frame-Antwort
liefert, faellt decode_response() das einfach als "unerwarteter Frame" auf
(SID/DID stimmen dann nicht ueberein) - kein stiller Fehldecode.
"""
import argparse
import time

REQUEST_ID = 0x720
RESPONSE_ID_RANGE = range(0x720, 0x730)
CAN_CHANNEL = "can0"

PIDS = {
    0x2A05: ("Tire1_Pressure_bar", lambda raw: (raw * 1373 / 1000) / 100),
    0x2A06: ("Tire2_Pressure_bar", lambda raw: (raw * 1373 / 1000) / 100),
    0x2A07: ("Tire3_Pressure_bar", lambda raw: (raw * 1373 / 1000) / 100),
    0x2A08: ("Tire4_Pressure_bar", lambda raw: (raw * 1373 / 1000) / 100),
    0x2A0A: ("Tire1_Temp_C", lambda raw: raw - 50),
    0x2A0B: ("Tire2_Temp_C", lambda raw: raw - 50),
    0x2A0C: ("Tire3_Temp_C", lambda raw: raw - 50),
    0x2A0D: ("Tire4_Temp_C", lambda raw: raw - 50),
}


def build_request(did):
    """UDS-Single-Frame ReadDataByIdentifier: [PCI=3, SID=0x22, DIDhi, DIDlo, Padding...]."""
    return [0x03, 0x22, (did >> 8) & 0xFF, did & 0xFF, 0x00, 0x00, 0x00, 0x00]


def decode_response(did, data):
    """Erwartetes Single-Frame-Antwortformat: [PCI, SID=0x62, DIDhi, DIDlo, raw, ...].

    Gibt den rohen Datenwert zurueck, oder None wenn SID/DID nicht passen
    (z.B. Negative Response 0x7F, oder Antwort auf eine andere PID)."""
    if len(data) < 5 or data[1] != 0x62:
        return None
    if ((data[2] << 8) | data[3]) != did:
        return None
    return data[4]


def poll_once(bus, timeout=0.5):
    """Fragt alle PIDs nacheinander ab. Gibt (values, unerwartete_frames) zurueck."""
    import can

    values = {}
    unexpected = []
    for did, (name, formula) in PIDS.items():
        bus.send(can.Message(arbitration_id=REQUEST_ID, data=build_request(did), is_extended_id=False))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = bus.recv(timeout=deadline - time.time())
            if msg is None:
                break
            if msg.arbitration_id not in RESPONSE_ID_RANGE:
                continue
            raw = decode_response(did, msg.data)
            if raw is not None:
                values[name] = formula(raw)
                break
            unexpected.append((hex(msg.arbitration_id), msg.data.hex()))
    return values, unexpected


def main():
    import can

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", default=CAN_CHANNEL)
    parser.add_argument("--interval", type=float, default=120.0,
                         help="Sekunden zwischen Abfragerunden, 0=einmalig (Default 120s - "
                              "Reifendruck aendert sich langsam, Bus/OBD moeglichst wenig belasten)")
    args = parser.parse_args()

    bus = can.interface.Bus(channel=args.channel, interface="socketcan")
    try:
        while True:
            values, unexpected = poll_once(bus)
            print(values, flush=True)
            for arb_id, hex_data in unexpected:
                print(f"  unerwarteter Frame auf {arb_id}: {hex_data}", flush=True)
            if args.interval <= 0:
                break
            time.sleep(args.interval)
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
