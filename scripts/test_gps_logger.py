"""Selbsttest fuer gps_logger.py, ohne Bluetooth: Socket und SDP-Abfrage sind Attrappen.
Koordinaten in den Testsaetzen sind erfunden."""
import functools
import json
import os
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gps_logger


def nmea(body):
    return f"${body}*{functools.reduce(lambda a, c: a ^ ord(c), body, 0):02X}"


GGA_FIX = nmea("GNGGA,112105.000,5000.0000,N,01000.0000,E,1,21,0.61,59.8,M,44.7,M,,")
GGA_NOFIX = nmea("GNGGA,112106.000,,,,,0,00,,,M,,M,,")
RMC = nmea("GNRMC,112105.000,A,5000.0000,N,01000.0000,E,0.000,0.00,061126,,,A")

SDPTOOL_SAMPLE = """Searching for 0x1101 on 00:11:22:33:44:55 ...
Service Name: GPS NMEA Tether
Service RecHandle: 0x10019
Protocol Descriptor List:
  "L2CAP" (0x0100)
  "RFCOMM" (0x0003)
    Channel: 9
"""


class FakeSocket:
    """Spielt eine Folge ab: Bytes = Daten, socket.timeout/OSError = wird geworfen, b"" = getrennt."""

    def __init__(self, script):
        self.script = list(script)
        self.closed = False

    def settimeout(self, _):
        pass

    def recv(self, _):
        item = self.script.pop(0)
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        return item

    def close(self):
        self.closed = True


def _session(script):
    tmp = tempfile.mkdtemp()
    status_path = os.path.join(tmp, "status.json")
    recorder = gps_logger.Recorder(tmp)
    sock = FakeSocket(script)
    gps_logger.read_session(sock, recorder, status_path, wall=lambda: 1794481265.5, mono=lambda: 100.0)
    return tmp, recorder, status_path, sock


def test_parse_channel():
    assert gps_logger.parse_channel(SDPTOOL_SAMPLE) == 9
    assert gps_logger.parse_channel("Searching for 0x1101 on 00:11:22:33:44:55 ...\n") is None


def test_checksum():
    assert gps_logger.checksum_ok(RMC)
    assert not gps_logger.checksum_ok(RMC[:-1] + ("0" if RMC[-1] != "0" else "1"))
    assert not gps_logger.checksum_ok("GNRMC,ohne-dollar*00")
    assert not gps_logger.checksum_ok("$GNRMC,abgeschnitten")
    assert not gps_logger.checksum_ok("$GNRMC,kaputt*ZZ")


def test_parse_gga():
    assert gps_logger.parse_gga(GGA_FIX) == (True, 21, 0.61)
    assert gps_logger.parse_gga(GGA_NOFIX) == (False, 0, None)
    assert gps_logger.parse_gga(RMC) is None
    assert gps_logger.parse_gga(GGA_FIX[:40]) is None  # abgeschnitten


def test_format_line_matches_candump_timestamp():
    assert gps_logger.format_line(1794481265.5, "$X*00") == "(1794481265.500000) $X*00\n"


def test_session_records_every_line_and_reports_status_after_disconnect():
    data = f"{RMC}\r\n{GGA_FIX}\r\nkaputte zeile\r\n".encode()
    tmp, rec, status_path, sock = _session([data[:30], socket.timeout, data[30:], b""])
    with open(rec.path) as fh:
        lines = fh.read().splitlines()
    assert lines == [f"(1794481265.500000) {RMC}", f"(1794481265.500000) {GGA_FIX}",
                     "(1794481265.500000) kaputte zeile"]  # Rohlog: auch Ungueltiges bleibt drin
    assert os.path.basename(rec.path).startswith("gps-") and rec.path.endswith(".nmea")
    assert sock.closed
    with open(status_path) as fh:
        assert json.load(fh)["connected"] is False


def test_no_data_writes_no_file_and_keeps_connection():
    tmp, rec, status_path, sock = _session([socket.timeout, socket.timeout, ConnectionResetError(104, "reset")])
    assert rec.path is None and os.listdir(tmp) == ["status.json"]
    assert sock.closed


def test_status_during_session():
    """Status mit Fix: einmal pro Sekunde geschrieben, nicht erst am Ende."""
    tmp = tempfile.mkdtemp()
    status_path = os.path.join(tmp, "status.json")
    seen = []
    clock = iter(range(100, 200))

    class Probe(FakeSocket):
        def recv(self, n):
            if not self.script:
                with open(status_path) as fh:
                    seen.append(json.load(fh))
                return b""
            return super().recv(n)

    gps_logger.read_session(Probe([f"{GGA_FIX}\r\n".encode(), socket.timeout]), gps_logger.Recorder(tmp), status_path,
                            wall=lambda: 1794481265.5, mono=lambda: next(clock))
    assert seen[0]["connected"] and seen[0]["fix"] and seen[0]["sats"] == 21 and seen[0]["hdop"] == 0.61
    assert seen[0]["last_sentence_mono"] is not None


def test_oversized_line_is_dropped():
    tmp, rec, _, _ = _session([b"x" * (gps_logger.MAX_LINE_BYTES + 1), f"\r\n{RMC}\r\n".encode(), b""])
    with open(rec.path) as fh:
        assert fh.read().splitlines() == [f"(1794481265.500000) {RMC}"]


def test_cycle_waits_according_to_outcome_and_reconnect_keeps_file():
    tmp = tempfile.mkdtemp()
    status_path = os.path.join(tmp, "status.json")
    rec = gps_logger.Recorder(tmp)
    assert gps_logger.cycle("M", rec, status_path, find=lambda m: None) == gps_logger.NO_SERVICE_RETRY_S

    def refuse(mac, channel):
        raise ConnectionRefusedError(111, "refused")
    assert gps_logger.cycle("M", rec, status_path, find=lambda m: 9, connect=refuse) == gps_logger.CONNECT_RETRY_S

    channels = []

    def accept(mac, channel):
        channels.append(channel)
        return FakeSocket([f"{RMC}\r\n".encode(), b""])
    for channel in (10, 11):  # neuer Kanal nach jeder Trennung
        assert gps_logger.cycle("M", rec, status_path, find=lambda m: channel, connect=accept) == gps_logger.CONNECT_RETRY_S
    assert channels == [10, 11]
    assert len([f for f in os.listdir(tmp) if f.endswith(".nmea")]) == 1
    with open(rec.path) as fh:
        assert len(fh.read().splitlines()) == 2


if __name__ == "__main__":
    test_parse_channel()
    test_checksum()
    test_parse_gga()
    test_format_line_matches_candump_timestamp()
    test_session_records_every_line_and_reports_status_after_disconnect()
    test_no_data_writes_no_file_and_keeps_connection()
    test_status_during_session()
    test_oversized_line_is_dropped()
    test_cycle_waits_according_to_outcome_and_reconnect_keeps_file()
    print("ok")
