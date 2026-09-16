"""
CAN-Decode-Backend fuer das Renncockpit (dash_gui.py) - eigener Prozess statt
Hintergrund-Thread in der GUI.

Hintergrund: Messung 2026-09-16 (py-spy gegen vcan0-Replay des echten
Morgenlogs, mit Original-Zeitstempeln nachgespielt) zeigte ~55-60% der
Sample-Zeit im CAN-Decode-Thread (SocketCAN-Recv, cantools-Decode,
decode_choices-Enum-Overhead), der mit Tkinters Mainloop/_configure um den
GIL konkurrierte - resultierend in Refresh-Aussetzern bis 1,8s. Das deckte
sich mit einer vom Nutzer bei der echten Fahrt beobachteten ~1s-Verzoegerung
zwischen Pedalbetaetigung und Anzeige.

Dieser Daemon macht GENAU das, was vorher LiveCanValues._run() in
status_gui.py in einem Thread machte (SocketCAN mit Kernel-Filter auf
NEEDED_CAN_IDS, cantools-Decode, Sonderfaelle Bremsprozent/Oeltemp-UDS),
aber als eigener Prozess - kein GIL-Wettbewerb mit dem Rendering mehr
moeglich, weil Rendering (dash_gui.py, Kivy) in einem komplett anderen
Interpreter laeuft. Verbindung: ein kompakter JSON-Snapshot aller aktuellen
Werte, per UDP an 127.0.0.1 gesendet (Loopback, atomare Datagramme - kein
Torn-Read-Risiko, kein Lock/Shared-Memory noetig). Das Frontend liest jeweils
nur den neuesten Snapshot, unabhaengig von der tatsaechlichen CAN-Eingangsrate.

Neu ggue. status_gui.py: pollt zusaetzlich Lambda (Soll, PID 0x44) und
Batteriespannung (PID 0x42) ueber tpms_poller.poll_obd1 - selbes Muster wie
die bestehende Oeltemp-Abfrage, selber Header, selber konservativer Takt.
"""
import json
import os
import signal
import socket
import threading
import time

from tpms_poller import (
    decode_response as _uds_decode_response,
    PCM_PIDS as _PCM_PIDS,
    decode_response_mode1 as _obd1_decode_response,
    OBD1_PIDS as _OBD1_PIDS,
)

LOG_DIR = os.environ.get("MX5_LOG_DIR", "/home/pi/canlogs")
CAN_CHANNEL = os.environ.get("MX5_CAN_CHANNEL", "can0")
# Gegenstueck zu dash_gui.py SIM_TRIGGER_PATH - der "Vcan-Simulation starten"-
# Knopf auf dem WARTE-AUF-CAN-BUS-Screen schreibt diese Datei, wir lesen sie.
# Kein Shared-Modul fuer eine einzige Konstante - bewusst dieselbe Konvention
# wie MX5_SIMULATE_STATE (dateibasiert statt eigenem IPC-Kanal).
SIM_TRIGGER_PATH = "/tmp/mx5_sim_active"
DBC_CANDIDATES = [
    os.environ.get("MX5_DBC", ""),
    os.path.join(LOG_DIR, "MX5ND_6thGenMazda_HSCAN_extended.dbc"),
    os.path.join(LOG_DIR, "MX5ND_6thGenMazda_HSCAN.dbc"),
]
UDP_PORT = int(os.environ.get("MX5_DASH_UDP_PORT", "51234"))
PUBLISH_HZ = 50

# Gleiche kuratierte Signalliste wie bisher status_gui.py LIVE_SIGNALS - Checkliste/
# Statusbildschirm braucht sie unveraendert, siehe dash_gui.py.
LIVE_SIGNALS = [
    ("Zündung", 80, "KeyState"),
    ("Anlasssperre", 80, "StarterInterLockSW"),
    ("Drehzahl", 514, "EngineRPM"),
    ("Gang (Actual)", 253, "MT_Gear_Actual"),
    ("Gang-Pos (roh)", 357, "MT_Gear_Position"),
    ("Gang-Wahl (N/InGear)", 357, "MT_Gear_Select"),
    ("Blinker", 154, "Turn"),
    ("Warnblinker", 145, "HAZ_SW"),
    ("Licht", 154, "Headlight"),
    ("Nebel hinten", 1086, "R_FOG_LAMP"),
    ("Wischer vorne", 145, "FrontWiper"),
    ("Kilometerstand", 1034, "C001_ODO"),
    ("Waschanlage", 145, "Washer"),
    ("Tür links", 1086, "DoorRight"),
    ("Tür rechts", 1086, "DoorLeft"),
    ("Kofferraum", 1086, "Trunk"),
    ("Parkbremse", 159, "Parking_Brake"),
    ("Rückwärtsgang", 159, "Reverse_Flag_maybe"),
    ("Speed ABS (neu)", 535, "VehicleSpeed_ABS_raw"),
    ("Drehzahl 0x130 (neu)", 304, "EngineRPM_related_2"),
    ("Kupplung 0x166 (neu)", 358, "Clutch_Pedal_Position_related_2"),
    ("Gang-Anzeige (unsicher)", 1143, "GearDisplay_related_maybe"),
    ("Lenkwinkel 0x240 (neu)", 576, "SteeringAngle_related_2_maybe"),
]

PEDAL_GAUGES = [
    {"label": "Gas", "can_id": 514, "signal": "APP_Accelerator_Pedal_Position"},
    {"label": "Bremse", "can_id": 120, "signal": "_BrakePedalPercent_derived"},
    {"label": "Kupplung", "can_id": 304, "signal": "Clutch_Pedal_Position_raw"},
    {"label": "Lenkwinkel", "can_id": 130, "signal": "Steering_Wheel_Absolute_Angle"},
    {"label": "Speed", "can_id": 514, "signal": "VehicleSpeed"},
]

TPMS_CAN_ID = 0x728
BRAKE_PCT_CAN_ID = 120
BRAKE_PCT_START_BIT = 28
BRAKE_PCT_LEN = 12
OIL_RESPONSE_ID = 0x7E8
OIL_DID = 0x1310
FUEL_TANK_CAN_ID = 158
COOLANT_TEMP_CAN_ID = 1056
RCM_LATERAL_CAN_ID = 117
RCM_LONGITUDINAL_CAN_ID = 118
ABS_CAN_ID = 529
IAT_CAN_ID = 1274  # BO_ 1274 HS_PCM, Signal IAT_Sensor_No1
LOAD_CAN_ID = 359   # BO_ 359 HS_PCM, Signal ActualEnginePercentTorque

NEEDED_CAN_IDS = sorted({
    *(can_id for _, can_id, _ in LIVE_SIGNALS),
    *(spec["can_id"] for spec in PEDAL_GAUGES),
    BRAKE_PCT_CAN_ID,
    TPMS_CAN_ID,
    OIL_RESPONSE_ID,
    FUEL_TANK_CAN_ID,
    COOLANT_TEMP_CAN_ID,
    RCM_LATERAL_CAN_ID,
    RCM_LONGITUDINAL_CAN_ID,
    ABS_CAN_ID,
    IAT_CAN_ID,
    LOAD_CAN_ID,
})


def extract_brake_pct(data):
    full = int.from_bytes(data, "big")
    total_bits = len(data) * 8
    shift = total_bits - BRAKE_PCT_START_BIT - BRAKE_PCT_LEN
    raw = (full >> shift) & ((1 << BRAKE_PCT_LEN) - 1)
    return max(0.0, min(100.0, (raw - 156) / 2.56))


def can0_up(channel):
    try:
        with open(f"/sys/class/net/{channel}/operstate") as f:
            return f.read().strip() == "up"
    except FileNotFoundError:
        return False


def process_running(pattern):
    try:
        pids = os.listdir("/proc")
    except FileNotFoundError:
        return False
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if pattern in cmdline:
            return True
    return False


def kill_processes(pattern):
    """Wie process_running(), aber schickt Treffern SIGTERM statt nur zu
    pruefen - fuer den canplayer der Vcan-Simulation (siehe run_process_watch()
    unten), der in dash_gui.py per subprocess.Popen gestartet wird, also kein
    Kindprozess von uns ist und nicht ueber .terminate() erreichbar."""
    try:
        pids = os.listdir("/proc")
    except FileNotFoundError:
        return
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if pattern in cmdline:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass


def _load_db():
    import logging
    import cantools
    for path in DBC_CANDIDATES:
        if path and os.path.exists(path):
            logging.disable(logging.WARNING)
            try:
                return cantools.database.load_file(path, strict=False)
            finally:
                logging.disable(logging.NOTSET)
    return None


class CanBackend:
    def __init__(self, channel=CAN_CHANNEL):
        self.channel = channel
        self._lock = threading.Lock()
        self._values = {}  # "can_id:signal" -> (value, timestamp)
        self.error = None
        self.db = _load_db()
        if self.db is None:
            self.error = "keine DBC gefunden"
        self._by_id = {m.frame_id: m for m in self.db.messages if m.signals} if self.db else {}
        self.session_max_speed = 0.0
        self._frame_count = 0
        self.frames_per_sec = 0.0
        self._logging_since = None
        # /proc-Scans sind zwar reines Python (kein fork(), siehe process_running()-
        # Docstring in status_gui.py), aber bei 50Hz Publish-Rate trotzdem unnoetige
        # Arbeit - werden deshalb nur 1x/s in run_process_watch() aktualisiert statt
        # bei jedem snapshot()-Aufruf neu erhoben.
        self._proc_state = {"can_up": False, "logging": False, "session_logger_running": False}

    def _set(self, can_id, signal, value, ts):
        with self._lock:
            self._values[f"{can_id}:{signal}"] = (value, ts)

    def snapshot(self):
        with self._lock:
            values = dict(self._values)
        return {
            "t": time.time(),
            "values": values,
            "error": self.error,
            "dbc_ok": self.db is not None,
            "frames_per_sec": round(self.frames_per_sec, 1),
            "session_max_speed": round(self.session_max_speed, 1),
            "logging_since": self._logging_since,
            **self._proc_state,
        }

    def run_process_watch(self):
        # Nur fuer Tests ohne echten can0-Adapter (z.B. vcan0-Replay - dessen
        # operstate ist "unknown", nie "up", selbst wenn Frames ankommen).
        # Wirkt nur fuer den Prozess, dem die Variable beim Start mitgegeben
        # wurde, nicht persistiert. Gleiches Muster wie zuvor in status_gui.py.
        forced = os.environ.get("MX5_SIMULATE_STATE")
        while True:
            if forced == "LOGGING":
                self._proc_state = {"can_up": True, "logging": True, "session_logger_running": True}
            elif forced == "WAITING_IGNITION":
                self._proc_state = {"can_up": True, "logging": False, "session_logger_running": True}
            elif forced == "WAITING_CAN":
                self._proc_state = {"can_up": False, "logging": False, "session_logger_running": False}
            elif forced == "ERROR":
                self._proc_state = {"can_up": True, "logging": False, "session_logger_running": False}
            elif os.path.exists(SIM_TRIGGER_PATH) and not can0_up(self.channel):
                # Vcan-Simulation vom "Warte auf CAN-Bus"-Knopf aus dash_gui.py -
                # so tun, als waere die Zuendung an und das Logging aktiv, damit
                # auch der Drive-Screen automatisch erscheint.
                self._proc_state = {"can_up": True, "logging": True, "session_logger_running": True}
            elif os.path.exists(SIM_TRIGGER_PATH):
                # can0_up() ist jetzt True - genau das Event, das sonst WARTE-
                # AUF-CAN-BUS auf BUS-AKTIV umschaltet: der echte Adapter ist
                # da. Simulation beenden statt sie im Hintergrund weiterlaufen
                # zu lassen, run_decode_loop() erkennt die geloeschte Trigger-
                # Datei binnen einer Sekunde und verbindet sich neu auf can0.
                kill_processes("canplayer")
                try:
                    os.remove(SIM_TRIGGER_PATH)
                except FileNotFoundError:
                    pass
                self._proc_state = {
                    "can_up": True,
                    "logging": process_running("candump -l"),
                    "session_logger_running": process_running("session_logger.py"),
                }
            else:
                self._proc_state = {
                    "can_up": can0_up(self.channel),
                    "logging": process_running("candump -l"),
                    "session_logger_running": process_running("session_logger.py"),
                }
            logging_now = self._proc_state["logging"]
            if logging_now:
                if self._logging_since is None:
                    self._logging_since = time.time()
            else:
                self._logging_since = None
            time.sleep(1.0)

    def run_decode_loop(self):
        """Blockierend - fuer den eigenen Prozess gedacht, siehe __main__."""
        import can
        while True:
            # Bei jedem (Re-)Connect neu pruefen - der Knopf kann jederzeit
            # dazwischenkommen, und genau dann (kein echtes can0 vorhanden)
            # dreht diese Schleife ohnehin schon alle 2s eine Runde.
            channel = "vcan0" if os.path.exists(SIM_TRIGGER_PATH) else self.channel
            try:
                bus = can.interface.Bus(
                    channel=channel, interface="socketcan",
                    can_filters=[{"can_id": cid, "can_mask": 0x7FF} for cid in NEEDED_CAN_IDS])
            except Exception as e:
                self.error = f"CAN-Bus nicht verfügbar ({e})"
                time.sleep(2)
                continue
            self.error = None
            try:
                while True:
                    # Laufend pruefen, nicht nur beim (Re-)Connect oben -
                    # solange auf vcan0 (oder gar nichts mehr) Frames
                    # ankommen, wirft bus.recv() nie eine Exception, die
                    # Schleife wuerde sonst auf vcan0 haengen bleiben, selbst
                    # nachdem run_process_watch() die Simulation schon laengst
                    # beendet hat (echter can0 jetzt verfuegbar).
                    if ("vcan0" if os.path.exists(SIM_TRIGGER_PATH) else self.channel) != channel:
                        break
                    msg = bus.recv(timeout=1.0)
                    if msg is None:
                        continue
                    now = time.time()
                    self._frame_count += 1

                    if msg.arbitration_id == BRAKE_PCT_CAN_ID and len(msg.data) == 8:
                        self._set(BRAKE_PCT_CAN_ID, "_BrakePedalPercent_derived",
                                   extract_brake_pct(msg.data), now)
                    elif msg.arbitration_id == OIL_RESPONSE_ID:
                        if _uds_decode_response is not None:
                            _, n_bytes, formula = _PCM_PIDS[OIL_DID]
                            raw = _uds_decode_response(OIL_DID, msg.data, n_bytes)
                            if raw is not None:
                                self._set(OIL_RESPONSE_ID, "_OilTemp_derived", formula(raw), now)
                        for pid, (name, n_bytes, formula) in _OBD1_PIDS.items():
                            raw = _obd1_decode_response(pid, msg.data, n_bytes)
                            if raw is not None:
                                self._set(OIL_RESPONSE_ID, f"_{name}_derived", formula(raw), now)

                    m = self._by_id.get(msg.arbitration_id)
                    if m is not None:
                        try:
                            decoded = m.decode(msg.data, allow_truncated=True, decode_choices=True)
                        except Exception:
                            decoded = None
                        if decoded is not None:
                            for sig, val in decoded.items():
                                self._set(msg.arbitration_id, sig, val, now)
                            if msg.arbitration_id == 514 and "VehicleSpeed" in decoded:
                                speed = decoded["VehicleSpeed"]
                                if isinstance(speed, (int, float)) and speed > self.session_max_speed:
                                    self.session_max_speed = speed
            except Exception as e:
                self.error = f"Verbindung verloren ({e})"
            finally:
                try:
                    bus.shutdown()
                except Exception:
                    pass
            time.sleep(2)

    def run_rate_counter(self):
        while True:
            time.sleep(1.0)
            self.frames_per_sec = self._frame_count
            self._frame_count = 0

    def run_publisher(self, port=UDP_PORT, hz=PUBLISH_HZ):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        interval = 1.0 / hz
        while True:
            # default=str faengt cantools' NamedSignalValue (decode_choices=True liefert
            # Enum-Objekte fuer z.B. Tuer/Blinker/Licht) ab - fuer die Checkliste soll
            # genau deren lesbarer Text ankommen, nicht ein Rohwert.
            payload = json.dumps(self.snapshot(), default=str).encode("utf-8")
            try:
                sock.sendto(payload, ("127.0.0.1", port))
            except OSError:
                pass
            time.sleep(interval)


if __name__ == "__main__":
    backend = CanBackend()
    threading.Thread(target=backend.run_rate_counter, daemon=True).start()
    threading.Thread(target=backend.run_process_watch, daemon=True).start()
    threading.Thread(target=backend.run_publisher, daemon=True).start()
    backend.run_decode_loop()
