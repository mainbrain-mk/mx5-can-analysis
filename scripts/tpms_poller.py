"""
TPMS-Abfrage (Reifendruck + -temperatur) per aktivem UDS-Request auf can0.

Anders als der Rest des Loggers ist das hier AKTIV: es sendet selbst
Anfragen auf den Bus (Mode 0x22 ReadDataByIdentifier), statt nur passiv
mitzulesen wie candump/session_logger.py/can_log_parser.py. Grund: das
Kombiinstrument broadcastet Reifendruck/-temperatur nicht periodisch auf
HS-CAN (siehe docs/logs/can-bus-status.md, systematische Byte-Suche fand nichts) -
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

# --- Klopfen/Zuendungskorrektur (2026-09-20) ---------------------------------------------
# DID 0x03EC identifiziert per Y-Splitter-Log (candump-2026-09-19_163755 + dlg
# 2026-09-19 163857, beide zeitgleich): Nutzer fragte "KnockRetard" per Handy-App ab (Car
# Scanners Kanalname "KNOCKR"), das aber KEINE SAE-J1979-Standard-Mode-1-PID ist (Websuche:
# herstellerspezifisch, bei GM z.B. 0x125D/0x125E, bei Toyota sogar eigener Diagnose-Mode) -
# stattdessen ein bisher unbekanntes Mode-0x22-UDS-DID auf dem PCM-Header. Gefunden per
# Korrelation aller im CAN-Log gesehenen Mode-1/22-Antworten gegen die App-Werte:
# signed_int16(raw)/512 trifft 73% der App-Werte bitgenau (restliche Abweichung ist
# Interpolations-Jitter beim Zeitstempel-Matching, keine Formelabweichung), R²=0,958.
# Vorzeichen unklar interpretierbar (negativ dominant in den Daten, fuer einen "Retard" an
# sich unerwartet) - wird hier unveraendert wie von der App gemeldet uebernommen.
# Laeuft in der SCHNELLEN Gruppe (siehe poll_knock_fast), aus demselben Grund wie
# Lambda/Drosselklappe: fuer eine Klopf-/Limiter-Analyse sind seltene 10s-Samples wertlos.
UDS_FAST_PIDS = {
    0x03EC: ("KnockRetard_deg", 2, lambda raw: (raw - 65536 if raw >= 32768 else raw) / 512),
}

# --- Lambda (Soll) + Batteriespannung (2026-09-16, Renncockpit-Neubau) -------------------
# Standard-Mode-1-OBD-PIDs (SAE J1979), nicht die herstellerspezifischen Mode-0x22-UDS-DIDs
# wie oben - anderes Anfrage/Antwort-Format (siehe build_request_mode1/decode_response_mode1).
# Laufen bewusst auf demselben Header 0x7E0/0x7E8 und im selben konservativen Poll-Takt wie
# die Oelabfrage (siehe poll_obd1) - aus demselben Grund: das Handy nutzt diesen Header
# parallel. PID 0x44 liefert nur das vom Steuergeraet ANGEFORDERTE Lambda, kein Sondenmesswert
# (siehe scripts/can_byte_search.py, scripts/uds_did_sweep.py) - im Dashboard entsprechend
# beschriften, nicht als gemessenen Wert ausgeben.
#
# 0x11 (Drosselklappenstellung, 2026-09-19) laeuft zusammen mit Lambda in der SCHNELLEN
# Gruppe (siehe OBD1_FAST_PIDS/poll_obd1_fast) - im Gegensatz zu Batteriespannung/Oel, die
# sich kaum aendern, will man Drosselklappe+Lambda so oft wie moeglich sehen. Die erreichbare
# Frequenz ist dabei nicht durch ein Intervall begrenzt, sondern einzig durch die
# Antwortzeit der ECU (poll_group wartet pro PID auf die Antwort, dann sofort die naechste).
OBD1_PIDS = {
    0x44: ("LambdaCommanded", 2, lambda raw: raw / 32768),
    0x42: ("BatteryVoltage", 2, lambda raw: raw / 1000),
    0x11: ("ThrottlePosition_pct", 1, lambda raw: raw * 100 / 255),
}
OBD1_FAST_PIDS = {0x44, 0x11}

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


def build_request_mode1(pid):
    """OBD-Mode-1-Single-Frame ("Show current data"): [PCI=2, SID=0x01, PID, Padding...].
    Anderes Format als UDS/Mode-0x22 oben: 1-Byte-PID statt 2-Byte-DID, SID 0x01 statt 0x22."""
    return [0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00]


def decode_response_mode1(pid, data, n_bytes=1):
    """Erwartetes Mode-1-Antwortformat: [PCI, SID=0x41, PID, raw...]."""
    if len(data) < 3 + n_bytes or data[1] != 0x41:
        return None
    if data[2] != pid:
        return None
    return int.from_bytes(bytes(data[3:3 + n_bytes]), "big")


def poll_group(bus, req_id, resp_range, pids, timeout=0.5, request_fn=build_request, decode_fn=decode_response):
    """Eine Gruppe PIDs auf einem Steuergeraet abfragen. request_fn/decode_fn austauschbar,
    damit dieselbe Poll-Schleife sowohl UDS-Mode-0x22-DIDs (Default) als auch
    OBD-Mode-1-PIDs (siehe poll_obd1) bedienen kann."""
    import can

    values = {}
    unexpected = []
    for did, (name, n_bytes, formula) in pids.items():
        bus.send(can.Message(arbitration_id=req_id, data=request_fn(did), is_extended_id=False))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = bus.recv(timeout=deadline - time.time())
            if msg is None:
                break
            if msg.arbitration_id not in resp_range:
                continue
            raw = decode_fn(did, msg.data, n_bytes)
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


def poll_knock_fast(bus, timeout=0.5):
    """KnockRetard (UDS-DID 0x03EC), ohne Wartezeit zwischen Aufrufen gedacht - wie
    poll_obd1_fast ist die erreichbare Frequenz allein durch die ECU-Antwortzeit begrenzt."""
    return poll_group(bus, PCM_REQUEST_ID, PCM_RESPONSE_ID_RANGE, UDS_FAST_PIDS, timeout)


def poll_obd1(bus, timeout=0.5):
    """Batteriespannung, Mode-1-PID, gleicher Header/Takt wie poll_oil - bewusst LANGSAM
    (siehe OBD1_PIDS), Lambda+Drosselklappe laufen separat ueber poll_obd1_fast."""
    slow_pids = {pid: v for pid, v in OBD1_PIDS.items() if pid not in OBD1_FAST_PIDS}
    return poll_group(bus, PCM_REQUEST_ID, PCM_RESPONSE_ID_RANGE, slow_pids, timeout,
                       request_fn=build_request_mode1, decode_fn=decode_response_mode1)


def poll_obd1_fast(bus, timeout=0.5):
    """Lambda (Soll) + Drosselklappenstellung, ohne Wartezeit zwischen Aufrufen gedacht -
    poll_group fragt beide PIDs nacheinander ab und wartet jeweils nur auf die Antwort,
    die erreichbare Frequenz ist also allein durch die ECU-Antwortzeit begrenzt."""
    fast_pids = {pid: v for pid, v in OBD1_PIDS.items() if pid in OBD1_FAST_PIDS}
    return poll_group(bus, PCM_REQUEST_ID, PCM_RESPONSE_ID_RANGE, fast_pids, timeout,
                       request_fn=build_request_mode1, decode_fn=decode_response_mode1)


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
            # Beide Gruppen sind gegeneinander isoliert: ein Fehler in der (neueren,
            # weniger erprobten) Oelgruppe darf die seit Wochen laufende TPMS-Abfrage
            # nicht mitreissen. Der Poller laeuft unbeaufsichtigt bei jeder Fahrt.
            if now >= next_tpms:
                try:
                    values, unexpected = poll_once(bus)
                    print(values, flush=True)
                    for arb_id, hex_data in unexpected:
                        print(f"  unerwarteter Frame auf {arb_id}: {hex_data}", flush=True)
                except Exception as exc:
                    print(f"  TPMS-Abfrage fehlgeschlagen: {exc!r}", flush=True)
                next_tpms = now + args.interval if args.interval > 0 else float("inf")
            if not args.no_oil and time.time() >= next_oil:
                try:
                    values, _ = poll_oil(bus)  # Fremdantworten auf 0x7E8 sind hier der
                    if values:                 # Normalfall (Handy pollt denselben Header)
                        print(values, flush=True)
                except Exception as exc:
                    print(f"  Oelabfrage fehlgeschlagen: {exc!r}", flush=True)
                try:
                    values, _ = poll_obd1(bus)  # Batteriespannung, gleicher Header/Takt wie
                    if values:                  # Oel, siehe poll_obd1 (bewusst langsam).
                        print(values, flush=True)
                except Exception as exc:
                    print(f"  Batterie-Abfrage fehlgeschlagen: {exc!r}", flush=True)
                next_oil = time.time() + args.oil_interval if args.oil_interval > 0 else float("inf")
            if not args.no_oil:
                # Lambda + Drosselklappe: kein next_*-Gate wie oben, jede Schleifenrunde
                # fragt sofort erneut ab - die Rate ergibt sich allein aus der Antwortzeit
                # der ECU (poll_group blockiert je PID auf die Antwort).
                try:
                    values, _ = poll_obd1_fast(bus)
                    if values:
                        print(values, flush=True)
                except Exception as exc:
                    print(f"  Lambda/Drosselklappe-Abfrage fehlgeschlagen: {exc!r}", flush=True)
                try:
                    values, _ = poll_knock_fast(bus)
                    if values:
                        print(values, flush=True)
                except Exception as exc:
                    print(f"  KnockRetard-Abfrage fehlgeschlagen: {exc!r}", flush=True)
            else:
                # Ohne die schnelle Gruppe blockiert hier nichts mehr auf den Bus - ohne
                # Pause wuerde die Schleife bis zum naechsten TPMS-Intervall leerlaufen.
                time.sleep(0.25)
            if args.interval <= 0 and (args.no_oil or args.oil_interval <= 0):
                break
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
