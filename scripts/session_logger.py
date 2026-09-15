"""
KeyState-gesteuerter CAN-Logger fuer den MX-5-Pi (ersetzt den direkten
candump-Aufruf in can-logger.service).

Warum: der Pi laeuft waehrend der Fahrt durchgehend an einer Powerbank,
unabhaengig von der Zuendung. Ohne diesen Trigger wuerde ein ganzer Tag
mit mehreren Fahrten in einer einzigen candump-Datei landen. Stattdessen
lauscht dieses Skript selbst auf KeyState (CAN-ID 0x050, HS_SSU, siehe
data/can/MX5ND_6thGenMazda_HSCAN.dbc) und startet/stoppt candump als
Kindprozess bei jedem Wechsel OFF <-> ACC/ON/START -> ein Log pro Fahrt.

candump selbst macht weiterhin das eigentliche Loggen (bewaehrt, schneller
als eine eigene Python-Schreibschleife) - dieses Skript ist nur der
Start/Stop-Trigger.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

import cantools

DBC_PATH = os.environ.get("MX5_DBC", "/home/pi/canlogs/MX5ND_6thGenMazda_HSCAN.dbc")
LOG_DIR = os.environ.get("MX5_LOG_DIR", "/home/pi/canlogs")
CAN_CHANNEL = os.environ.get("MX5_CAN_CHANNEL", "can0")
KEYSTATE_CAN_ID = 0x050
TPMS_POLLER_PATH = os.environ.get(
    "MX5_TPMS_POLLER", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tpms_poller.py")
)
TPMS_POLL_INTERVAL_S = 120  # Reifendruck aendert sich langsam, Bus/OBD moeglichst wenig belasten

# Einmal-Erhebung (2026-09-15): liegt die Flag-Datei, wird bei der naechsten Fahrt einmal
# uds_did_sweep.py --probe gestartet und die Flagge danach umbenannt. Ohne die Datei
# verhaelt sich dieses Skript exakt wie zuvor - das ist Absicht, der Logger soll nicht von
# einer neuen, selten genutzten Funktion abhaengen.
# Zweck: die beiden Fragen, die sich nur am laufenden Fahrzeug klaeren lassen - welche
# Standard-OBD-PIDs das Fahrzeug unterstuetzt (u.a. die GEMESSENEN Lambdawerte beider
# Sonden und ggf. PID 0x5C Oeltemperatur) und was die DSC-DID-Bloecke hergeben (Suche nach
# dem ABS/DSC-Eingriffsindikator). Rein lesende Dienste, siehe uds_did_sweep.py.
PROBE_FLAG_PATH = os.path.join(LOG_DIR, "RUN_PROBE")
PROBE_SCRIPT_PATH = os.environ.get(
    "MX5_PROBE_SCRIPT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "uds_did_sweep.py")
)
PROBE_DELAY_S = 90   # Motor soll laufen - bei blosser Zuendung ACC sind die Werte wertlos

# Uhr-Absicherung (2026-09-15). Der Pi hat keine RTC. fake-hwclock IST installiert und
# aktiviert, kann aber nichts ausrichten: seine Datei /etc/fake-hwclock.data liegt auf dem
# overlayroot=tmpfs-Overlay und ist nach jedem Reboot weg. Im Auto gibt es kein Netz, also
# auch kein NTP -> nach einem Reboot ohne Netz laeuft die Uhr auf dem Datum des Images
# weiter, OHNE dass im Log ein Sprung sichtbar waere (genau der Fehler, der 2026-09-13 und
# 2026-09-14 je ein Log falsch datiert hat, siehe mx5_can_bus_status.md).
#
# Gegenmassnahme ohne Eingriff ins schreibgeschuetzte Root-Dateisystem: Zeitstempel auf dem
# USB-Stick mitschreiben. Beim Start wird die Uhr NUR dann gestellt, wenn sie nachweislich
# falsch ist (kein NTP-Sync UND gespeicherte Zeit neuer als die Systemzeit) - sonst wird
# nichts angefasst. Zusaetzlich landet neben jedem Log ein Marker mit dem Uhr-Zustand,
# damit eine zweifelhafte Datierung spaeter erkennbar ist statt still zu bleiben.
CLOCK_FILE_PATH = os.path.join(LOG_DIR, "last_known_time")
CLOCK_WRITE_INTERVAL_S = 60


def ntp_synchronized(run=subprocess.run):
    try:
        out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                  capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "yes"
    except Exception:
        return False


def restore_clock(clock_path=CLOCK_FILE_PATH, run=subprocess.run, now=None):
    """-> (zustand, meldung). Stellt die Uhr nur bei nachgewiesenem Fehlstand."""
    now = now or datetime.now()
    if ntp_synchronized(run):
        return "ntp", f"Uhr per NTP synchronisiert ({now:%Y-%m-%d %H:%M:%S})"
    try:
        with open(clock_path) as fh:
            saved = datetime.strptime(fh.read().strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return "kein_anker", f"kein NTP und keine gespeicherte Zeit - Datum unsicher ({now:%Y-%m-%d %H:%M:%S})"
    if saved <= now:
        return "plausibel", f"kein NTP, Systemzeit >= gespeicherte Zeit - vermutlich ok ({now:%Y-%m-%d %H:%M:%S})"
    try:
        run(["date", "-s", saved.strftime("%Y-%m-%d %H:%M:%S")], check=True,
            capture_output=True, timeout=5)
    except Exception as exc:
        return "stellen_fehlgeschlagen", f"Uhr war {now:%Y-%m-%d %H:%M:%S}, Korrektur auf {saved:%Y-%m-%d %H:%M:%S} fehlgeschlagen: {exc!r}"
    return "korrigiert", f"Uhr von {now:%Y-%m-%d %H:%M:%S} auf gespeicherte {saved:%Y-%m-%d %H:%M:%S} vorgestellt (kein NTP)"


def save_clock(clock_path=CLOCK_FILE_PATH):
    try:
        tmp = clock_path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        os.replace(tmp, clock_path)   # atomar - ein Stromausfall darf keine halbe Datei hinterlassen
    except Exception:
        pass


class SessionLogger:
    """Kapselt den Start/Stop-Trigger, damit er ohne echten CAN-Bus/candump testbar ist."""

    def __init__(self, keystate_msg, log_dir=LOG_DIR, can_channel=CAN_CHANNEL,
                 popen=subprocess.Popen, run=subprocess.run):
        self.keystate_msg = keystate_msg
        self.log_dir = log_dir
        self.can_channel = can_channel
        self._popen = popen
        self._run = run
        self.candump_proc = None
        self.tpms_proc = None
        self.probe_proc = None
        self.logging_active = False
        self.clock_state = "unbekannt"
        self.clock_note = ""
        self._last_clock_write = 0.0

    def gzip_finished_logs(self):
        self._run(f"gzip -f {self.log_dir}/*.log", shell=True, stderr=subprocess.DEVNULL)

    def start_logging(self):
        if self.candump_proc is not None:
            return
        self.candump_proc = self._popen(["candump", "-l", self.can_channel], cwd=self.log_dir)
        self.write_clock_marker()
        print(f"[session_logger] Fahrt erkannt, candump gestartet (pid {self.candump_proc.pid})", flush=True)
        # TPMS wird nicht periodisch gebroadcastet (siehe mx5_can_bus_status.md) - aktiver
        # UDS-Poller sendet eigene Requests, deren Antworten wie jeder andere Frame vom
        # gerade gestarteten candump mitgeschrieben werden. Kein eigenes Log noetig.
        # Reifendruck aendert sich sehr langsam - Intervall bewusst gross (siehe
        # TPMS_POLL_INTERVAL_S in tpms_poller.py), um Bus/OBD-Schnittstelle so wenig
        # wie moeglich mit eigenen Requests zu belasten.
        self.tpms_proc = self._popen(
            [sys.executable, TPMS_POLLER_PATH, "--channel", self.can_channel,
             "--interval", str(TPMS_POLL_INTERVAL_S)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.maybe_start_probe()

    def write_clock_marker(self):
        """Uhr-Zustand neben die Logs schreiben, damit eine zweifelhafte Datierung spaeter
        auffaellt. Fehler hier duerfen das Loggen nie stoeren."""
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            with open(os.path.join(self.log_dir, f"clockstate-{stamp}.txt"), "w") as fh:
                fh.write(f"{self.clock_state}\n{self.clock_note}\n")
        except Exception as exc:
            print(f"[session_logger] Uhr-Marker nicht geschrieben: {exc!r}", flush=True)

    def maybe_start_probe(self):
        """Einmal-Erhebung starten, falls die Flag-Datei liegt. Darf unter keinen Umstaenden
        das Loggen verhindern - deshalb alles in try/except und die Flagge wird VOR dem Start
        umbenannt, damit ein Absturz nicht bei jeder Fahrt erneut ausgeloest wird."""
        try:
            if not os.path.exists(PROBE_FLAG_PATH):
                return
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out_path = os.path.join(self.log_dir, f"probe-{stamp}.csv")
            os.rename(PROBE_FLAG_PATH, f"{PROBE_FLAG_PATH}.gestartet-{stamp}")
            self.probe_proc = self._popen(
                [sys.executable, PROBE_SCRIPT_PATH, "--channel", self.can_channel,
                 "--probe", "--delay", str(PROBE_DELAY_S), "--out", out_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"[session_logger] Einmal-Erhebung gestartet -> {out_path}", flush=True)
        except Exception as exc:
            print(f"[session_logger] Einmal-Erhebung nicht gestartet: {exc!r}", flush=True)

    def stop_logging(self):
        if self.candump_proc is None:
            return
        if self.tpms_proc is not None:
            self.tpms_proc.terminate()
            self.tpms_proc.wait(timeout=5)
            self.tpms_proc = None
        if self.probe_proc is not None:
            self.probe_proc.terminate()
            self.probe_proc.wait(timeout=5)
            self.probe_proc = None
        self.candump_proc.terminate()
        self.candump_proc.wait(timeout=5)
        self.candump_proc = None
        print("[session_logger] Fahrt beendet, candump gestoppt", flush=True)
        self.gzip_finished_logs()

    def on_frame(self, arbitration_id, data):
        now = time.time()
        if now - self._last_clock_write >= CLOCK_WRITE_INTERVAL_S:
            self._last_clock_write = now
            save_clock()
        if arbitration_id != KEYSTATE_CAN_ID:
            return
        decoded = self.keystate_msg.decode(data, allow_truncated=True)
        key_state = decoded.get("KeyState")
        # ponytail: kein Watchdog fuer einen abgestuerzten candump-Kindprozess
        # waehrend should_log weiterhin True bleibt - Restart=on-failure am
        # systemd-Service faengt nur einen Absturz dieses Skripts selbst ab.
        # Aufwand bisher nicht beobachtet, bei Bedarf candump_proc.poll() pruefen.
        should_log = key_state is not None and key_state != "OFF"
        if should_log and not self.logging_active:
            self.start_logging()
            self.logging_active = True
        elif not should_log and self.logging_active:
            self.stop_logging()
            self.logging_active = False


def main():
    import can

    logging.disable(logging.WARNING)  # cantools warnt bei jeder doppelten BO_-Definition im DBC (harmlos)
    db = cantools.database.load_file(DBC_PATH, strict=False)
    logging.disable(logging.NOTSET)
    keystate_msg = db.get_message_by_frame_id(KEYSTATE_CAN_ID)
    session = SessionLogger(keystate_msg)
    session.clock_state, session.clock_note = restore_clock()
    print(f"[session_logger] {session.clock_note}", flush=True)
    save_clock()

    bus = can.interface.Bus(channel=CAN_CHANNEL, interface="socketcan")

    def handle_shutdown(signum, frame):
        session.stop_logging()
        bus.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    session.gzip_finished_logs()  # von vorherigen Fahrten liegengebliebene Logs nachtraeglich komprimieren

    print(f"[session_logger] warte auf KeyState auf {CAN_CHANNEL}...", flush=True)
    try:
        for frame in bus:
            session.on_frame(frame.arbitration_id, frame.data)
    except can.CanOperationError:
        # Adapter wurde getrennt/verlor Strom (z.B. Pi ausgesteckt nach Fahrtende) -
        # kein Fehler, session.stop_logging() lief zu diesem Zeitpunkt schon.
        print("[session_logger] can0 verschwunden, beende", flush=True)
        session.stop_logging()


if __name__ == "__main__":
    main()
