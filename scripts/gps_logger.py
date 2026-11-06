#!/usr/bin/env python3
"""
Handy-GPS per Bluetooth mitloggen (Plan: docs/plans/gps-bluetooth-plan.md).

Die App auf dem Handy (GPS NMEA Tether) lauscht auf SPP/RFCOMM, dieser Dienst verbindet sich
selbst und schreibt jede empfangene Zeile unveraendert nach canlogs/gps-*.nmea, im Zeitformat
von candump: "(epoch) $GNRMC,...". Pruefsummen werden erst beim Einlesen ausgewertet, das Rohlog
bleibt vollstaendig.

Gemessen in Phase 0 (06.11.2026), daraus folgt die Schleife unten:
  - Nach jeder Trennung lauscht die App auf einem neuen Kanal, also vor jedem Verbinden per SDP
    fragen. "sdptool browse" zeigt den Dienst nicht, nur die Suche nach der UUID.
  - Direkt nach einer Trennung meldet SDP noch den alten Kanal (Reset/Refused), danach ist der
    Dienst etwa 24 s weg.
  - Ohne GPS-Fix steht die Verbindung, es kommen aber keine Daten. Das ist kein Abbruch.
  - Standort aus: Die App wiederholt alle 5 s den letzten Fix unveraendert (update_status()). Das
    Rohlog behaelt auch das, beim Einlesen muessen Saetze mit bekannter GPS-Zeit wegfallen.
  - Funkabriss (Handy ausser Reichweite): recv() meldet weder Fehler noch Ende, nur poll() sieht
    ihn (link_lost()).

Fuer die Fusszeile im Dash (gps_status() in dash_gui.py) ersetzt der Dienst nach jedem Datenpaket,
mindestens aber einmal pro Sekunde atomar eine Statusdatei in /tmp. Zeiten dort in time.monotonic(), damit Uhrspruenge nichts
ausmachen. Der Dienst haengt weder vom Dash noch von can_backend.py ab.
"""
import functools
import json
import os
import re
import select
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

LOG_DIR = "/home/pi/canlogs"
STATUS_PATH = "/tmp/mx5_gps_status.json"
NO_SERVICE_RETRY_S = 15.0  # Handy ausser Reichweite oder App aus; seltener funken, der Adapter gehoert auch bbq.service
CONNECT_RETRY_S = 5.0
RECV_TIMEOUT_S = 1.0
STATUS_INTERVAL_S = 1.0
MAX_LINE_BYTES = 4096  # NMEA-Saetze sind < 100 Byte; ohne Zeilenende waere der Puffer sonst unbegrenzt
KNOTS_TO_KMH = 1.852


def parse_channel(sdptool_output):
    """RFCOMM-Kanal aus der Ausgabe von "sdptool search", None ohne Treffer.
    ponytail: nimmt den ersten SPP-Dienst des Handys; bietet eine zweite App SPP an, hier nach
    dem Dienstnamen filtern."""
    m = re.search(r"Channel: (\d+)", sdptool_output)
    return int(m.group(1)) if m else None


def find_channel(mac):
    try:
        res = subprocess.run(["sdptool", "search", "--bdaddr", mac, "0x1101"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_channel(res.stdout)


def connect_rfcomm(mac, channel):
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(15)
    try:
        sock.connect((mac, channel))
    except OSError:
        sock.close()
        raise
    return sock


def checksum_ok(line):
    star = line.rfind("*")
    if not line.startswith("$") or star < 0:
        return False
    try:
        return functools.reduce(lambda a, c: a ^ ord(c), line[1:star], 0) == int(line[star + 1:star + 3], 16)
    except ValueError:
        return False


def parse_gga(line):
    """(Fix ja/nein, Satelliten, HDOP) aus einem gueltigen GGA-Satz, sonst None. Leere Felder
    bleiben None."""
    if not checksum_ok(line) or line[3:6] != "GGA":
        return None
    fields = line.split(",")
    try:
        return (fields[6] not in ("", "0"), int(fields[7]) if fields[7] else None,
                float(fields[8]) if fields[8] else None)
    except (IndexError, ValueError):
        return None


def format_line(wall_time, line):
    return f"({wall_time:.6f}) {line}\n"


def _degrees(value, hemisphere):
    """NMEA ddmm.mmmm -> Grad, Sueden und Westen negativ."""
    deg, minutes = divmod(float(value), 100)
    return (deg + minutes / 60) * (-1 if hemisphere in "SW" else 1)


def read_fixes(path):
    """Rohlog -> [(UTC-Epoch aus der GPS-Zeit, Breite, Laenge, Hoehe, km/h)], fuer den Datalake (auf
    dem Laptop, der Dienst selbst braucht das nicht). Position und Tempo aus RMC, das auch das Datum
    traegt. Jede GPS-Zeit nur einmal, siehe update_status(). Hoehe auf das Ellipsoid bezogen (GGA-Hoehe
    + Geoid-Abstand) wie in den GPX: dort liegt sie am selben Ort 49 m ueber der GGA-Hoehe, der
    Geoid-Abstand betraegt 44,7 m (gemessen 06.11.2026, 15.035 Punkte). Unbrauchbare Zeilen fallen weg.
    ponytail: Hoehe je Tageszeit nachgeschlagen, ein Rohlog ueber mehr als 24 h traefe den falschen Tag."""
    heights, rows, seen = {}, [], set()
    with open(path, encoding="ascii", errors="replace") as fh:
        for raw in fh:
            line = raw.partition(") ")[2].strip()
            if not checksum_ok(line):
                continue
            f = line[:line.rfind("*")].split(",")
            try:
                if line[3:6] == "GGA" and f[6] not in ("", "0"):
                    heights[f[1]] = float(f[9]) + float(f[11])
                elif line[3:6] == "RMC" and f[2] == "A" and (f[9], f[1]) not in seen:
                    t = datetime.strptime(f[9] + f[1], "%d%m%y%H%M%S.%f").replace(tzinfo=timezone.utc)
                    rows.append((f[1], t.timestamp(), _degrees(f[3], f[4]), _degrees(f[5], f[6]),
                                 float(f[7]) * KNOTS_TO_KMH if f[7] else None))
                    seen.add((f[9], f[1]))
            except (IndexError, ValueError):
                continue
    return [(t, lat, lon, heights.get(gps_time), speed) for gps_time, t, lat, lon, speed in rows]


class Recorder:
    """Legt die Datei erst mit der ersten Zeile an (keine leeren Dateien) und behaelt sie ueber
    Wiederverbindungen. Zeilenweise geflusht: Stromausfall kostet wie bei candump nur den Rest
    im Dateisystem-Cache, abgeschnittene Zeilen faengt das Einlesen ab."""

    def __init__(self, log_dir=LOG_DIR):
        self.log_dir = log_dir
        self.path = None
        self._fh = None

    def write(self, wall_time, line):
        if self._fh is None:
            name = time.strftime("gps-%Y-%m-%d_%H%M%S.nmea", time.localtime(wall_time))
            self.path = os.path.join(self.log_dir, name)
            self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
            say(f"schreibe {self.path}")
        self._fh.write(format_line(wall_time, line))


def write_status(status, mono_now, path=STATUS_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({**status, "written_mono": mono_now}, fh)
    os.replace(tmp, path)


def new_status(connected):
    return {"connected": connected, "fix": False, "sats": None, "hdop": None, "gps_time": None, "last_fix_mono": None}


def update_status(status, line, mono_now):
    """Frisch ist nur ein GGA mit neuer GPS-Zeit: Bei ausgeschaltetem Standort sendet die App alle
    5 s denselben alten Fix erneut, mit Status "gueltig" (gemessen 06.11.2026). Saetze allein
    sagen also nichts aus."""
    gga = parse_gga(line)
    if not gga:
        return
    status["fix"], status["sats"], status["hdop"] = gga
    gps_time = line.split(",")[1]
    if gps_time != status["gps_time"]:
        status["gps_time"] = gps_time
        status["last_fix_mono"] = mono_now


def link_lost(sock):
    """Reisst die Funkverbindung ab, setzt der Kernel POLLHUP, recv() liefert aber nur EAGAIN. Python
    versucht es dann bis zum Timeout immer wieder (87 % CPU), ein Ende meldet es nie (gemessen
    06.11.2026). POLLHUP und POLLERR meldet poll() auch mit leerer Ereignismaske."""
    poller = select.poll()
    poller.register(sock, 0)
    return bool(poller.poll(0))


def read_session(sock, recorder, status_path=STATUS_PATH, wall=time.time, mono=time.monotonic):
    """Liest bis zur Trennung. Ein Empfangs-Timeout ist nur "gerade keine Daten" (kein Fix).
    ponytail: alle Zeilen eines recv() bekommen dieselbe Empfangszeit; reicht, weil die GPS-Zeit
    die Referenz ist. Fuer eine genauere Latenzmessung die Zeit je Zeile schaetzen."""
    status = new_status(connected=True)
    write_status(status, mono(), status_path)
    next_status = mono() + STATUS_INTERVAL_S
    sock.settimeout(RECV_TIMEOUT_S)
    buf = b""
    try:
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                if link_lost(sock):
                    say("Funkverbindung verloren")
                    return
                data = None
            except OSError as e:
                say(f"Verbindung abgebrochen: {e}")
                return
            if data == b"":
                say("Gegenstelle hat getrennt")
                return
            if data:
                t = wall()
                *lines, buf = (buf + data).split(b"\n")
                if len(buf) > MAX_LINE_BYTES:
                    buf = b""
                for raw in lines:
                    line = raw.strip().decode("ascii", "replace")
                    if line:
                        recorder.write(t, line)
                        update_status(status, line, mono())
            # Auch nach jedem Paket: Nur im 1-s-Takt liefe er gegen die 1-Hz-Daten, ein Fix sah dann
            # gelegentlich 3 s alt aus und das Dash blitzte "kein Fix" (gemessen 06.11.2026).
            if data or mono() >= next_status:
                write_status(status, mono(), status_path)
                next_status = mono() + STATUS_INTERVAL_S
    finally:
        sock.close()
        write_status(new_status(connected=False), mono(), status_path)


def cycle(mac, recorder, status_path=STATUS_PATH, find=find_channel, connect=connect_rfcomm):
    """Ein Verbindungsversuch samt Aufzeichnung, -> Wartezeit bis zum naechsten."""
    channel = find(mac)
    if channel is None:
        say("kein SPP-Dienst am Handy")
        return NO_SERVICE_RETRY_S
    try:
        sock = connect(mac, channel)
    except OSError as e:
        say(f"Verbinden nicht moeglich: {e}")
        return CONNECT_RETRY_S
    say(f"verbunden auf Kanal {channel}")
    read_session(sock, recorder, status_path)
    return CONNECT_RETRY_S


_last_message = None


def say(message):
    """Nur Zustandswechsel ins Journal, sonst schreibt die Wartezeit es alle 15 s voll."""
    global _last_message
    if message != _last_message:
        print(message, flush=True)
        _last_message = message


def main():
    mac = os.environ.get("GPS_PHONE_MAC")
    if not mac:
        sys.exit("GPS_PHONE_MAC fehlt (Umgebungsdatei, siehe scripts/pi-config/SETUP.md)")
    recorder = Recorder()
    write_status(new_status(connected=False), time.monotonic())
    while True:
        time.sleep(cycle(mac, recorder))


if __name__ == "__main__":
    main()
