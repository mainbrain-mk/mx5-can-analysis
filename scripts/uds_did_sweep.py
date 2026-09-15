"""
UDS-DID-Sweep: systematisch durchprobieren, welche Diagnose-Datenpunkte ein Steuergeraet hat.

Bisher fragen wir nur DIDs ab, die wir schon kennen (tpms_poller.py: 8 TPMS-PIDs) oder die
wir dem Handy abgeschaut haben (obd_from_can.py). Dieses Skript dreht das um: es probiert
einen DID-Bereich durch und protokolliert, was antwortet. Das braucht keine externe Quelle
und keine Referenzgroesse - jede positive Antwort IST der Fund.

Externe Referenz fuer Machbarkeit und Ausbeute: `drewid74/2024-nd3-mazda-obdii`, Range-Scan
vom 2026-06-23 am ND3 - 11 Bloecke a 256 DIDs in 388 s, u.a. 60 Treffer im PCM-Block
`22 F4 xx`, 36 in `22 DA xx`, `22 13 10` = Oeltemperatur.

Bekannte Steuergeraete-Header in unserem Fahrzeug (aus eigenen Logs bestaetigt, siehe
obd_from_can.ECU_HEADERS):
    0x7E0/0x7E8  PCM   Motorsteuergeraet
    0x730/0x738  EPS   Lenkung (DID 0x3301 Lenkgeschwindigkeit, 0x3302 Lenkwinkel)
    0x760/0x768  DSC   Fahrdynamik/ABS  <- interessantester Header fuer den seit Wochen
                                           offenen ABS/DSC-Eingriffsindikator
    0x720/0x728  TPMS  Reifendruck

SICHERHEIT - bewusst eng gehalten:
  * Es werden AUSSCHLIESSLICH lesende Dienste gesendet: 0x22 (ReadDataByIdentifier).
    Schreibende/aendernde Dienste (0x2E WriteDataByIdentifier, 0x31 RoutineControl,
    0x11 ECUReset, 0x28 CommunicationControl, 0x85 ControlDTCSetting) sind nicht
    implementiert und duerfen hier auch nicht ergaenzt werden.
  * Default-Pause zwischen Anfragen begrenzt die Buslast; der Bus traegt im Normalbetrieb
    schon ~16-17 Requests/s vom Handy (siehe mx5_can_bus_status.md).
  * Fahrzeug im STAND, Motor an. Nicht waehrend der Fahrt laufen lassen.

Nutzt nur die Standardbibliothek (socket.AF_CAN) - python-can ist auf dem Pi im
Default-Interpreter nicht installiert.

Aufruf (auf dem Pi):
    python3 uds_did_sweep.py --self-test
    python3 uds_did_sweep.py --ecu DSC  --range 2B00-2BFF
    python3 uds_did_sweep.py --ecu DSC  --range 2000-20FF
    python3 uds_did_sweep.py --ecu PCM  --range 1300-13FF     # Oeltemperatur-Block (ND3)
    python3 uds_did_sweep.py --ecu PCM  --range F400-F4FF     # groesster Block am ND3
    python3 uds_did_sweep.py --ecu DSC  --range 0000-FFFF --out dsc_full.csv   # lang!
"""
import argparse
import csv
import socket
import struct
import sys
import time

ECUS = {
    "PCM": (0x7E0, 0x7E8),
    "EPS": (0x730, 0x738),
    "DSC": (0x760, 0x768),
    "TPMS": (0x720, 0x728),
}

# ISO 14229 Negative Response Codes, die beim Durchprobieren vorkommen
NRC = {
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",          # der Normalfall fuer eine nicht existierende DID
    0x33: "securityAccessDenied",       # existiert, aber gesperrt - trotzdem ein Fund!
    0x78: "responsePending",
}

CAN_FRAME_FMT = "=IB3x8s"


def build_request(did):
    """UDS Single Frame ReadDataByIdentifier: [PCI=3, 0x22, DIDhi, DIDlo, Padding]."""
    return bytes([0x03, 0x22, (did >> 8) & 0xFF, did & 0xFF, 0x00, 0x00, 0x00, 0x00])


# Mode-1-Support-Bitmasken (SAE J1979): eine Antwort auf PID 0x00 sagt, welche der PIDs
# 0x01-0x20 das Fahrzeug unterstuetzt, 0x20 deckt 0x21-0x40 ab und so weiter. Fuenf Anfragen
# liefern damit die VOLLSTAENDIGE Liste der Standard-PIDs - unendlich viel billiger als
# 256 einzeln durchzuprobieren.
SUPPORT_PIDS = (0x00, 0x20, 0x40, 0x60, 0x80)

# Standard-PIDs, die fuer dieses Projekt besonders interessant sind. Das Fahrzeug hat zwei
# Lambdasonden (vorn Breitband/Regelsonde, hinten Diagnosesonde hinter dem Kat) - die
# Mode-1-Sondenkanaele liefern das GEMESSENE Lambda je Sonde, waehrend das bisher genutzte
# PID 0x44 nur das vom Steuergeraet ANGEFORDERTE Lambda ist.
INTERESTING_MODE1 = {
    0x13: "welche O2-Sonden verbaut sind (Bitmaske)",
    0x1D: "dito, alternative Kodierung",
    0x24: "O2 Bank1 Sensor1 (vorn) - Lambda + Spannung",
    0x25: "O2 Bank1 Sensor2 (hinten) - Lambda + Spannung",
    0x34: "O2 Bank1 Sensor1 (vorn) - Lambda + Strom",
    0x35: "O2 Bank1 Sensor2 (hinten) - Lambda + Strom",
    0x14: "O2 Bank1 Sensor1 schmalbandig - Spannung + Trim",
    0x15: "O2 Bank1 Sensor2 schmalbandig - Spannung + Trim",
    0x5C: "Motoroeltemperatur (Standard-PID, falls unterstuetzt)",
    0x5E: "Kraftstoffverbrauchsrate",
}


def build_mode1_request(pid):
    """Mode-1-Anfrage: [PCI=2, 0x01, PID, Padding]."""
    return bytes([0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00])


def decode_support_mask(base, raw):
    """Antwort auf PID 0x00/0x20/... -> Liste der unterstuetzten PID-Nummern."""
    if len(raw) < 4:
        return []
    bits = int.from_bytes(raw[:4], "big")
    return [base + 1 + i for i in range(32) if bits & (1 << (31 - i))]


def parse_mode1_response(pid, data):
    """Wie parse_response, aber fuer Mode 1 (SID 0x41, 1-Byte-PID)."""
    if len(data) < 3:
        return None
    kind = data[0] >> 4
    if kind == 1:
        if len(data) >= 4 and data[2] == 0x41 and data[3] == pid:
            return ("multiframe", data[4:])
        return None
    if kind != 0:
        return None
    payload = data[1:1 + (data[0] & 0x0F)]
    if len(payload) >= 2 and payload[0] == 0x41 and payload[1] == pid:
        return ("hit", payload[2:])
    if len(payload) >= 3 and payload[0] == 0x7F and payload[1] == 0x01:
        return ("nrc", payload[2])
    return None


def probe_mode1(sock, req_id, resp_id, pid, timeout=0.15):
    send(sock, req_id, build_mode1_request(pid))
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = recv(sock, deadline - time.time())
        if got is None:
            break
        can_id, data = got
        if can_id != resp_id:
            continue
        parsed = parse_mode1_response(pid, data)
        if parsed is None:
            continue
        kind, detail = parsed
        if kind == "nrc" and detail == 0x78:
            deadline = time.time() + timeout
            continue
        return kind, detail
    return "silent", b""


def mode1_survey(sock, req_id, resp_id, timeout=0.15):
    """Erst die Support-Bitmasken lesen, dann jeden unterstuetzten PID einmal abfragen.
    Ergebnis: die vollstaendige Liste der Standard-OBD-Kanaele dieses Fahrzeugs."""
    supported = []
    for base in SUPPORT_PIDS:
        kind, raw = probe_mode1(sock, req_id, resp_id, base, timeout)
        if kind != "hit":
            break
        supported += decode_support_mask(base, bytes(raw))
        if base + 0x20 not in [p for p in supported]:
            break        # naechste Bitmaske nicht unterstuetzt -> hier ist Schluss
    print(f"  {len(supported)} Standard-PIDs unterstuetzt: "
          f"{' '.join(f'{p:02X}' for p in supported)}\n", flush=True)

    rows = []
    for pid in supported:
        kind, raw = probe_mode1(sock, req_id, resp_id, pid, timeout)
        if kind not in ("hit", "multiframe"):
            continue
        raw = bytes(raw)
        note = INTERESTING_MODE1.get(pid, "")
        rows.append(dict(did=f"01{pid:02X}", status=kind, n_bytes=len(raw),
                         raw_hex=raw.hex(), raw_int=int.from_bytes(raw, "big") if raw else ""))
        print(f"  PID {pid:02X}  {raw.hex():16s}{'  <- ' + note if note else ''}", flush=True)
    return rows


def parse_response(did, data):
    """-> ("hit", raw_bytes) | ("nrc", code) | ("multiframe", first_bytes) | None.

    None heisst: gehoert nicht zu dieser Anfrage (andere DID, Fremdverkehr auf dem Bus)."""
    if len(data) < 3:
        return None
    pci_kind = data[0] >> 4
    if pci_kind == 1:                                   # First Frame einer langen Antwort
        if len(data) >= 5 and data[2] == 0x62 and ((data[3] << 8) | data[4]) == did:
            return ("multiframe", data[5:])
        return None
    if pci_kind != 0:
        return None
    length = data[0] & 0x0F
    payload = data[1:1 + length]
    if len(payload) < 2:
        return None
    if payload[0] == 0x62 and len(payload) >= 3 and ((payload[1] << 8) | payload[2]) == did:
        return ("hit", payload[3:])
    if payload[0] == 0x7F and len(payload) >= 3 and payload[1] == 0x22:
        return ("nrc", payload[2])
    return None


def open_bus(channel):
    s = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((channel,))
    return s


def send(sock, can_id, data):
    sock.send(struct.pack(CAN_FRAME_FMT, can_id, len(data), data))


def recv(sock, timeout):
    sock.settimeout(max(timeout, 0.001))
    try:
        frame = sock.recv(16)
    except (socket.timeout, TimeoutError):
        return None
    can_id, dlc, data = struct.unpack(CAN_FRAME_FMT, frame)
    return can_id & socket.CAN_EFF_MASK, data[:dlc]


def probe(sock, req_id, resp_id, did, timeout=0.15):
    """Eine DID anfragen. -> (status, detail). status: hit / nrc / multiframe / silent."""
    send(sock, req_id, build_request(did))
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = recv(sock, deadline - time.time())
        if got is None:
            break
        can_id, data = got
        if can_id != resp_id:
            continue                                     # Fremdverkehr (Handy-OBD etc.)
        parsed = parse_response(did, data)
        if parsed is None:
            continue
        kind, detail = parsed
        if kind == "nrc" and detail == 0x78:             # responsePending -> weiter warten
            deadline = time.time() + timeout
            continue
        return kind, detail
    return "silent", b""


def sweep(sock, req_id, resp_id, dids, gap, timeout, verbose=True):
    rows = []
    t0 = time.time()
    for n, did in enumerate(dids, 1):
        status, detail = probe(sock, req_id, resp_id, did, timeout)
        if status in ("hit", "multiframe"):
            raw = bytes(detail)
            rows.append(dict(did=f"{did:04X}", status=status, n_bytes=len(raw),
                             raw_hex=raw.hex(),
                             raw_int=int.from_bytes(raw, "big") if raw else ""))
            if verbose:
                print(f"  {did:04X}  {status:10s} {raw.hex()}"
                      f"{'  (' + str(int.from_bytes(raw, 'big')) + ')' if raw else ''}", flush=True)
        elif status == "nrc" and detail != 0x31:
            rows.append(dict(did=f"{did:04X}", status=f"nrc_{NRC.get(detail, hex(detail))}",
                             n_bytes=0, raw_hex="", raw_int=""))
            if verbose:
                print(f"  {did:04X}  NRC {NRC.get(detail, hex(detail))}", flush=True)
        if verbose and n % 256 == 0:
            print(f"  ... {n}/{len(dids)} ({time.time()-t0:.0f}s, {len(rows)} Treffer)", flush=True)
        if gap:
            time.sleep(gap)
    return rows


PROBE_STEPS = [
    ("PCM", "mode1", None),        # welche Standard-PIDs gibt es + einmal auslesen
    ("DSC", "did", "2B00-2BFF"),   # Fahrwerksblock (ND3: 11 Treffer, u.a. Bremspedal)
    ("DSC", "did", "2000-20FF"),   # Lenkungsblock (ND3: 4 Treffer)
    ("PCM", "did", "1300-13FF"),   # Thermoblock - hier liegt die Oeltemperatur (0x1310)
]


def engine_rpm(sock, timeout=0.3):
    """Aktuelle Drehzahl per Standard-PID 0x0C. None, wenn keine Antwort kommt."""
    req_id, resp_id = ECUS["PCM"]
    kind, raw = probe_mode1(sock, req_id, resp_id, 0x0C, timeout)
    if kind != "hit" or len(raw) < 2:
        return None
    return (raw[0] * 256 + raw[1]) / 4


def wait_for_engine(channel, max_wait_s, min_rpm=400, poll_s=5.0):
    """Warten, bis der Motor laeuft. -> True, wenn er laeuft; False bei Zeitablauf."""
    sock = open_bus(channel)
    deadline = time.time() + max_wait_s
    try:
        while time.time() < deadline:
            rpm = engine_rpm(sock)
            if rpm is not None and rpm > min_rpm:
                print(f"Motor laeuft ({rpm:.0f} 1/min), starte Erhebung", flush=True)
                return True
            time.sleep(poll_s)
    except Exception as exc:
        print(f"Drehzahlpruefung fehlgeschlagen: {exc!r}", flush=True)
        return False
    finally:
        sock.close()
    return False


def run_probe(channel, timeout, gap, out_path=None):
    """Einmal-Erhebung. Bewusst fehlertolerant: ein fehlschlagender Schritt darf die
    uebrigen nicht verhindern - das Ding laeuft unbeaufsichtigt waehrend einer Fahrt."""
    sock = open_bus(channel)
    rows = []
    try:
        for ecu, kind, rng in PROBE_STEPS:
            req_id, resp_id = ECUS[ecu]
            label = f"{ecu} {kind} {rng or ''}".strip()
            print(f"\n--- {label} ---", flush=True)
            try:
                if kind == "mode1":
                    got = mode1_survey(sock, req_id, resp_id, timeout)
                else:
                    got = sweep(sock, req_id, resp_id, parse_range(rng), gap, timeout)
                for r in got:
                    r["step"] = label
                rows += got
            except Exception as exc:
                print(f"  Schritt fehlgeschlagen: {exc!r}", flush=True)
    finally:
        sock.close()

    print(f"\nEinmal-Erhebung fertig: {len(rows)} Treffer", flush=True)
    if out_path and rows:
        with open(out_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"-> {out_path}", flush=True)
    return rows


def parse_range(text):
    if "-" in text:
        lo, hi = text.split("-", 1)
        return range(int(lo, 16), int(hi, 16) + 1)
    return [int(text, 16)]


def self_test():
    assert build_request(0x2A05) == bytes([0x03, 0x22, 0x2A, 0x05, 0, 0, 0, 0])
    # echte TPMS-Antwort aus unseren Logs: 04 62 2A05 8F
    assert parse_response(0x2A05, bytes([0x04, 0x62, 0x2A, 0x05, 0x8F, 0, 0, 0])) == ("hit", b"\x8f")
    # Antwort auf eine ANDERE DID darf nicht zugeordnet werden
    assert parse_response(0x2A06, bytes([0x04, 0x62, 0x2A, 0x05, 0x8F, 0, 0, 0])) is None
    # Negative Response requestOutOfRange
    assert parse_response(0x1234, bytes([0x03, 0x7F, 0x22, 0x31, 0, 0, 0, 0])) == ("nrc", 0x31)
    # Multiframe-Antwort (First Frame)
    kind, first = parse_response(0x3302, bytes([0x10, 0x0A, 0x62, 0x33, 0x02, 0x11, 0x22, 0x33]))
    assert kind == "multiframe" and first == b"\x11\x22\x33", (kind, first)
    # Drehzahl-Auswertung (PID 0x0C, (A*256+B)/4) - die Bedingung, an der die erste
    # Erhebung am 2026-09-15 gescheitert ist (lief bei Drehzahl 0)
    assert parse_mode1_response(0x0C, bytes([0x04, 0x41, 0x0C, 0x0F, 0xA0, 0, 0, 0])) == ("hit", b"\x0f\xa0")
    _kind, _raw = parse_mode1_response(0x0C, bytes([0x04, 0x41, 0x0C, 0x0F, 0xA0, 0, 0, 0]))
    assert (_raw[0] * 256 + _raw[1]) / 4 == 1000.0          # 0x0FA0 = 4000 -> 1000 1/min
    _kind, _raw = parse_mode1_response(0x0C, bytes([0x04, 0x41, 0x0C, 0x00, 0x00, 0, 0, 0]))
    assert (_raw[0] * 256 + _raw[1]) / 4 == 0.0             # Motor aus -> Erhebung muss warten

    # Fremdverkehr
    assert parse_response(0x2A05, bytes([0x00, 0x00, 0x00, 0, 0, 0, 0, 0])) is None

    # Mode 1
    assert build_mode1_request(0x0D) == bytes([0x02, 0x01, 0x0D, 0, 0, 0, 0, 0])
    assert parse_mode1_response(0x0D, bytes([0x03, 0x41, 0x0D, 0x2A, 0, 0, 0, 0])) == ("hit", b"\x2a")
    assert parse_mode1_response(0x0E, bytes([0x03, 0x41, 0x0D, 0x2A, 0, 0, 0, 0])) is None
    # Support-Bitmaske: Bit31 gesetzt -> PID 0x01 unterstuetzt, Bit0 -> PID 0x20
    assert decode_support_mask(0x00, bytes([0x80, 0x00, 0x00, 0x01])) == [0x01, 0x20]
    assert decode_support_mask(0x20, bytes([0x00, 0x00, 0x00, 0x00])) == []
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ecu", choices=sorted(ECUS), default="DSC")
    ap.add_argument("--range", dest="rng", default="2B00-2BFF",
                    help="DID-Bereich hex, z.B. 2B00-2BFF oder einzeln 1310")
    ap.add_argument("--channel", default="can0")
    ap.add_argument("--gap", type=float, default=0.01, help="Pause zwischen Anfragen (s)")
    ap.add_argument("--timeout", type=float, default=0.15, help="Antwort-Timeout (s)")
    ap.add_argument("--out", help="CSV-Ausgabe")
    ap.add_argument("--mode1-survey", action="store_true",
                    help="statt DID-Sweep: alle unterstuetzten Standard-Mode-1-PIDs "
                         "ermitteln und einmal auslesen (5 Anfragen + je eine pro PID). "
                         "Liefert u.a. die GEMESSENEN Lambdawerte beider Sonden.")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="vor dem Start so viele Sekunden warten")
    ap.add_argument("--wait-rpm", type=float, default=0.0,
                    help="vor dem Start warten, bis der Motor laeuft (Drehzahl > 400), "
                         "hoechstens so viele Sekunden. Eine feste Wartezeit reicht NICHT: "
                         "KeyState loest schon bei Zuendung ACC aus, und bei stehendem Motor "
                         "liefern Lambda, Last und Zuendwinkel nur Ruhewerte (2026-09-15 "
                         "genau so passiert - die erste Erhebung lief mit Drehzahl 0).")
    ap.add_argument("--probe", action="store_true",
                    help="vordefinierte Einmal-Erhebung: Mode-1-Bestandsaufnahme am PCM "
                         "plus die beiden DSC-Bloecke, die laut ND3-Quelle Fahrwerks- und "
                         "Lenkungsdaten tragen. Alles rein lesend.")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    if args.delay > 0:
        print(f"warte {args.delay:.0f}s...", flush=True)
        time.sleep(args.delay)

    if args.wait_rpm > 0 and not wait_for_engine(args.channel, args.wait_rpm):
        print("Motor lief nicht rechtzeitig - Erhebung wird trotzdem gestartet "
              "(statische PIDs sind auch so gueltig)", flush=True)

    if args.probe:
        run_probe(args.channel, args.timeout, args.gap, args.out)
        return

    req_id, resp_id = ECUS[args.ecu]
    if args.mode1_survey:
        print(f"Mode-1-Bestandsaufnahme an {args.ecu} (0x{req_id:03X}->0x{resp_id:03X})")
        sock = open_bus(args.channel)
        try:
            rows = mode1_survey(sock, req_id, resp_id, args.timeout)
        finally:
            sock.close()
        if args.out and rows:
            with open(args.out, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader(); w.writerows(rows)
            print(f"-> {args.out}")
        return
    dids = parse_range(args.rng)
    print(f"Sweep {args.ecu} (0x{req_id:03X}->0x{resp_id:03X}), "
          f"{len(dids)} DIDs, geschaetzt {len(dids)*(args.gap+0.02):.0f}-{len(dids)*(args.gap+args.timeout):.0f}s")
    sock = open_bus(args.channel)
    try:
        rows = sweep(sock, req_id, resp_id, dids, args.gap, args.timeout)
    finally:
        sock.close()

    print(f"\n{len(rows)} Treffer/auffaellige Antworten")
    if args.out and rows:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"-> {args.out}")


if __name__ == "__main__":
    main()
