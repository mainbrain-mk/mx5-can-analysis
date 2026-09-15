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

# --- Motoroeltemperatur (2026-09-15) -----------------------------------------------------
# Zweite Pollgruppe, anderes Steuergeraet (PCM statt Kombiinstrument). DID 0x1310 wurde am
# 2026-09-15 als OELTEMPERATUR identifiziert - im Projekt war sie bis dahin faelschlich als
# "VehicleOdometerReading" gefuehrt (Scheinkorrelation zweier monoton steigender Groessen;
# der echte Kilometerstand steht bei 169.765 km, 0x1310 bei 7053). Belegt ueber den Vergleich
# mit dem Kuehlwasser: 30,5 Grad Oel bei 49-51 Grad Kuehlwasser im Warmlauf, Anstieg
# ~1,8 Grad/min. Formel aus drewid74/2024-nd3-mazda-obdii, an unseren Daten plausibilisiert.
#
# Warum ueberhaupt pollen: die Oeltemperatur wird NICHT gebroadcastet, und das Handy fragt
# sie nur sporadisch ab - in 12 Logs zusammen ganze 20 Stichproben, alle in einem einzigen
# Log. Ohne eigenes Polling gibt es den Kanal praktisch nicht.
#
# ACHTUNG, Unterschied zur TPMS-Gruppe: 0x7E0 ist derselbe Header, den auch der
# Handy-OBD-Adapter mit ~16-17 Requests/s benutzt. Unsere Antworten erscheinen dort als
# zusaetzliche 0x7E8-Frames. Das Intervall ist deshalb bewusst konservativ und die Gruppe
# per --no-oil abschaltbar, falls sich die App daran stoert.
PCM_REQUEST_ID = 0x7E0
PCM_RESPONSE_ID_RANGE = range(0x7E8, 0x7F0)
OIL_POLL_INTERVAL_S = 10.0

PCM_PIDS = {
    0x1310: ("OilTemp_C", 2, lambda raw: raw / 100 - 40),
}

PIDS = {
    0x2A05: ("Tire1_Pressure_bar", 1, lambda raw: (raw * 1373 / 1000) / 100),
    0x2A06: ("Tire2_Pressure_bar", 1, lambda raw: (raw * 1373 / 1000) / 100),
    0x2A07: ("Tire3_Pressure_bar", 1, lambda raw: (raw * 1373 / 1000) / 100),
    0x2A08: ("Tire4_Pressure_bar", 1, lambda raw: (raw * 1373 / 1000) / 100),
    0x2A0A: ("Tire1_Temp_C", 1, lambda raw: raw - 50),
    0x2A0B: ("Tire2_Temp_C", 1, lambda raw: raw - 50),
    0x2A0C: ("Tire3_Temp_C", 1, lambda raw: raw - 50),
    0x2A0D: ("Tire4_Temp_C", 1, lambda raw: raw - 50),
}


def build_request(did):
    """UDS-Single-Frame ReadDataByIdentifier: [PCI=3, SID=0x22, DIDhi, DIDlo, Padding...]."""
    return [0x03, 0x22, (did >> 8) & 0xFF, did & 0xFF, 0x00, 0x00, 0x00, 0x00]


def decode_response(did, data, n_bytes=1):
    """Erwartetes Single-Frame-Antwortformat: [PCI, SID=0x62, DIDhi, DIDlo, raw...].

    Gibt den rohen Datenwert (big endian ueber n_bytes) zurueck, oder None wenn SID/DID
    nicht passen (z.B. Negative Response 0x7F, oder Antwort auf eine andere PID). Der
    DID-Vergleich ist die einzige Zuordnung - wichtig auf 0x7E0, wo parallel auch das
    Handy Anfragen stellt und fremde Antworten dazwischenliegen."""
    if len(data) < 4 + n_bytes or data[1] != 0x62:
        return None
    if ((data[2] << 8) | data[3]) != did:
        return None
    return int.from_bytes(bytes(data[4:4 + n_bytes]), "big")


def poll_group(bus, req_id, resp_range, pids, timeout=0.5):
    """Eine Gruppe PIDs auf einem Steuergeraet abfragen."""
    import can

    values = {}
    unexpected = []
    for did, (name, n_bytes, formula) in pids.items():
        bus.send(can.Message(arbitration_id=req_id, data=build_request(did), is_extended_id=False))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = bus.recv(timeout=deadline - time.time())
            if msg is None:
                break
            if msg.arbitration_id not in resp_range:
                continue
            raw = decode_response(did, msg.data, n_bytes)
            if raw is not None:
                values[name] = formula(raw)
                break
            unexpected.append((hex(msg.arbitration_id), msg.data.hex()))
    return values, unexpected


def poll_once(bus, timeout=0.5):
    """Nur die TPMS-Gruppe - Signatur unveraendert, damit bestehende Aufrufer weiterlaufen."""
    return poll_group(bus, REQUEST_ID, RESPONSE_ID_RANGE, PIDS, timeout)


def poll_oil(bus, timeout=0.5):
    """Nur die PCM-Gruppe (Oeltemperatur)."""
    return poll_group(bus, PCM_REQUEST_ID, PCM_RESPONSE_ID_RANGE, PCM_PIDS, timeout)


def main():
    import can

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", default=CAN_CHANNEL)
    parser.add_argument("--interval", type=float, default=120.0,
                         help="Sekunden zwischen TPMS-Abfragerunden, 0=einmalig (Default 120s - "
                              "Reifendruck aendert sich langsam, Bus/OBD moeglichst wenig belasten)")
    parser.add_argument("--oil-interval", type=float, default=OIL_POLL_INTERVAL_S,
                         help=f"Sekunden zwischen Oeltemperatur-Abfragen (Default {OIL_POLL_INTERVAL_S:.0f}s)")
    parser.add_argument("--no-oil", action="store_true",
                         help="Oeltemperatur NICHT pollen (sie laeuft ueber denselben Header "
                              "0x7E0 wie der Handy-OBD-Adapter)")
    args = parser.parse_args()

    bus = can.interface.Bus(channel=args.channel, interface="socketcan")
    next_tpms = 0.0
    next_oil = 0.0
    try:
        while True:
            now = time.time()
            if now >= next_tpms:
                values, unexpected = poll_once(bus)
                print(values, flush=True)
                for arb_id, hex_data in unexpected:
                    print(f"  unerwarteter Frame auf {arb_id}: {hex_data}", flush=True)
                next_tpms = now + args.interval if args.interval > 0 else float("inf")
            if not args.no_oil and time.time() >= next_oil:
                values, _ = poll_oil(bus)   # Fremdantworten auf 0x7E8 sind hier der Normalfall
                if values:                  # (das Handy pollt denselben Header) - nicht melden
                    print(values, flush=True)
                next_oil = time.time() + args.oil_interval if args.oil_interval > 0 else float("inf")
            if args.interval <= 0 and (args.no_oil or args.oil_interval <= 0):
                break
            time.sleep(0.25)
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
