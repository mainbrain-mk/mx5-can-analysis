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

import cantools

DBC_PATH = os.environ.get("MX5_DBC", "/home/pi/canlogs/MX5ND_6thGenMazda_HSCAN.dbc")
LOG_DIR = os.environ.get("MX5_LOG_DIR", "/home/pi/canlogs")
CAN_CHANNEL = os.environ.get("MX5_CAN_CHANNEL", "can0")
KEYSTATE_CAN_ID = 0x050
TPMS_POLLER_PATH = os.environ.get(
    "MX5_TPMS_POLLER", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tpms_poller.py")
)
TPMS_POLL_INTERVAL_S = 120  # Reifendruck aendert sich langsam, Bus/OBD moeglichst wenig belasten


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
        self.logging_active = False

    def gzip_finished_logs(self):
        self._run(f"gzip -f {self.log_dir}/*.log", shell=True, stderr=subprocess.DEVNULL)

    def start_logging(self):
        if self.candump_proc is not None:
            return
        self.candump_proc = self._popen(["candump", "-l", self.can_channel], cwd=self.log_dir)
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

    def stop_logging(self):
        if self.candump_proc is None:
            return
        if self.tpms_proc is not None:
            self.tpms_proc.terminate()
            self.tpms_proc.wait(timeout=5)
            self.tpms_proc = None
        self.candump_proc.terminate()
        self.candump_proc.wait(timeout=5)
        self.candump_proc = None
        print("[session_logger] Fahrt beendet, candump gestoppt", flush=True)
        self.gzip_finished_logs()

    def on_frame(self, arbitration_id, data):
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
