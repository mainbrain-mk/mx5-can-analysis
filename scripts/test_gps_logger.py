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
        self._local, self.peer = socket.socketpair()  # echter Deskriptor fuer poll() in link_lost()

    def fileno(self):
        return self._local.fileno()

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


def test_link_loss_ends_session_although_recv_only_times_out():
    """Funkabriss: recv() meldet nur Timeout, allein poll() sieht POLLHUP. Ohne Pruefung lief der Logger
    weiter und das Dash zeigte dauerhaft "kein Fix" (gemessen 06.11.2026). Ein weiteres recv() wuerde
    hier an der leeren Liste scheitern."""
    tmp = tempfile.mkdtemp()
    status_path = os.path.join(tmp, "status.json")
    sock = FakeSocket([f"{RMC}\r\n".encode(), socket.timeout])
    sock.peer.close()
    rec = gps_logger.Recorder(tmp)
    gps_logger.read_session(sock, rec, status_path, wall=lambda: 1794481265.5, mono=lambda: 100.0)
    assert sock.closed
    with open(status_path) as fh:
        assert json.load(fh)["connected"] is False
    with open(rec.path) as fh:
        assert len(fh.read().splitlines()) == 1  # was vor dem Abriss ankam, bleibt


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
    assert seen[0]["last_fix_mono"] is not None


def test_status_is_written_after_each_packet_not_only_on_the_timer():
    """1-Hz-Daten gegen 1-s-Schreibtakt: Pakete knapp vor Ablauf des Takts durften nicht ausfallen,
    sonst sah ein Fix im Dash gelegentlich 3 s alt aus."""
    tmp = tempfile.mkdtemp()
    status_path = os.path.join(tmp, "status.json")
    now = [100.0]
    ages = []
    second = nmea("GNGGA,112106.000,5000.0000,N,01000.0000,E,1,21,0.61,59.8,M,44.7,M,,")
    packets = [f"{GGA_FIX}\r\n".encode(), f"{second}\r\n".encode(), b""]

    class Probe(FakeSocket):
        def recv(self, n):
            now[0] += 0.98  # jedes Paket kurz vor Ablauf von STATUS_INTERVAL_S
            with open(status_path) as fh:
                s = json.load(fh)
            if s["last_fix_mono"] is not None:
                ages.append(now[0] - s["last_fix_mono"])
            return super().recv(n)

    gps_logger.read_session(Probe(packets), gps_logger.Recorder(tmp), status_path,
                            wall=lambda: 1794481265.5, mono=lambda: now[0])
    assert len(ages) == 2 and max(ages) < 1.0, ages  # schon der erste Fix steht vor dem zweiten Paket in der Datei


def test_repeated_fix_is_not_fresh():
    """Standort aus: Die App sendet alle 5 s denselben Fix erneut, Status "gueltig"."""
    status = gps_logger.new_status(connected=True)
    gps_logger.update_status(status, GGA_FIX, 100.0)
    assert status["last_fix_mono"] == 100.0 and status["fix"]
    gps_logger.update_status(status, RMC, 101.0)  # andere Saetze zaehlen nicht
    gps_logger.update_status(status, GGA_FIX, 105.0)  # gleiche GPS-Zeit
    assert status["last_fix_mono"] == 100.0
    next_second = nmea("GNGGA,112106.000,5000.0000,N,01000.0000,E,1,21,0.61,59.8,M,44.7,M,,")
    gps_logger.update_status(status, next_second, 106.0)
    assert status["last_fix_mono"] == 106.0
    gps_logger.update_status(status, GGA_NOFIX, 107.0)
    assert not status["fix"]


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
    test_link_loss_ends_session_although_recv_only_times_out()
    test_status_during_session()
    test_status_is_written_after_each_packet_not_only_on_the_timer()
    test_repeated_fix_is_not_fresh()
    test_oversized_line_is_dropped()
    test_cycle_waits_according_to_outcome_and_reconnect_keeps_file()
    print("ok")
