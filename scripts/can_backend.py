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

Neu ggue. status_gui.py: dekodiert zusaetzlich die Mode-1-Antworten von
tpms_poller.py (Lambda-Soll, Drosselklappe ...) passiv mit. Batteriespannung kommt
seit 26.09. aus dem Broadcast 0x08A (DCDC_Voltage, 100 Hz) statt aus PID 0x42.

Zeitbasis (27.09.): Wert-Zeitstempel und REC-Timer laufen auf CLOCK_MONOTONIC statt
auf der Wanduhr. Der Pi hat keine RTC, und seit der Bootbeschleunigung kommt der
NTP-Sync fast immer erst waehrend der Fahrt - die Wanduhr springt dann um Stunden
bis Tage (Logbuch "Bootzeit-Optimierung 27.09."). Mit Wanduhr-Stempeln wurden danach
alle langsamen Werte (TPMS, Oel) bis zum naechsten Poll als veraltet verworfen bzw.
bei einem Rueckwaertssprung nie mehr, und der REC-Timer sprang. CLOCK_MONOTONIC ist
systemweit, dash_gui.py vergleicht also mit derselben Uhr (Snapshot-Feld "clock").
"""
import glob
import json
import math
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
    ("Rückwärtsgang", 159, "ReverseGear_IC"),
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
# 0x420 traegt auch Byte1 Wegzaehler (0,1992 m/Schritt, ODO-kalibriert) und Byte2
# Kraftstoffzaehler (4985 Schritte/l, Voll-bis-Voll-Kette 16.09.-26.10.), beide 8 Bit
# umlaufend, 10 Hz - daraus der Momentanverbrauch, unabhaengig vom zu hohen VehicleSpeed.
TRAVEL_M_PER_STEP = 0.1992
FUEL_STEPS_PER_L = 4985.0
CONSUMPTION_WINDOW_S = 1.0   # Nutzerwunsch 31.10.: schneller statt ruhiger (2 s zappelte halb so viel)
CONSUMPTION_MIN_KMH = 4.0    # darunter zeigt das Dash l/h statt l/100km
RCM_LATERAL_CAN_ID = 117
RCM_LONGITUDINAL_CAN_ID = 118
ABS_CAN_ID = 529
IAT_CAN_ID = 1274  # BO_ 1274 HS_PCM, Signal IAT_Sensor_No1
LOAD_CAN_ID = 359   # BO_ 359 HS_PCM, Signal ActualEnginePercentTorque
DCDC_CAN_ID = 138   # BO_ 138 HS_DCDC, Signal DCDC_Voltage (Bordnetzspannung, ersetzt PID 0x42)
# OBD-Anfragen nur zaehlen, fuer die Zustandszeile: 0x7DF = Handy-Init, 0x7E0 = Pi-Poller
# (per SocketCAN-Loopback sichtbar) + Handy. Anlass: 27.09., Dash zeigte bei laufendem OBD
# Fusion ueberall "–" ausser TPMS, das Log dazu lag in /tmp und war nach dem Reboot weg.
OBD_REQUEST_IDS = (0x7DF, 0x7E0)
HEALTH_LOG_S = 10
HEALTH_KEYS = {
    "speed": "514:VehicleSpeed",
    "lambda": f"{OIL_RESPONSE_ID}:_LambdaCommanded_derived",
    "oel": f"{OIL_RESPONSE_ID}:_OilTemp_derived",
    "tpms": f"{TPMS_CAN_ID}:Tire1_Pressure",
}

# --- Tankinhalt aus dem Kraftstoffzaehler (Nutzerwunsch 04.11.2026) ---------------------------
# Der Tankgeber 0x09E saettigt bei roh 36,2 (= 42,8 l): nach dem Volltanken zeigte das Dash
# ~90 %, bis die ersten ~2,3 l verbraucht waren. Jetzt: nach dem Volltanken 45 l, danach wird
# der Kraftstoffzaehler 0x420 Byte2 abgezogen (4985 Schritte je Liter, Voll-bis-Voll-Kette
# 16.09.->26.10., ~1 %). Der Geber dient nur noch zum Erkennen von Tankstopps und Fahrten ohne
# Pi: weicht er laenger als FUEL_RESYNC_HOLD_* um mehr als FUEL_RESYNC_L ab, gilt sein Wert
# (bzw. 45 l, wenn er gesaettigt ist). Der Stand liegt auf dem Stick (FUEL_STATE_PATH) und
# ueberlebt Neustart und Zuendung aus. Logbuch "Tankanzeige aus dem Kraftstoffzaehler".
FUEL_COUNTER_CAN_ID = COOLANT_TEMP_CAN_ID  # 0x420 traegt Kuehlwasser und Kraftstoffzaehler
FUEL_COUNTER_SIGNAL = "FuelConsumption_Counter"
FUEL_TANK_SIGNAL = "Fuel_Tank"
FUEL_FULL_L = 45.0                # Mazda-Angabe, bei jeder Voll-Tankung bestaetigt
FUEL_COUNTS_PER_L = FUEL_STEPS_PER_L  # 0x420 Byte2 (4985/l), umlaufend 8 Bit
FUEL_SENSOR_L0 = 3.0              # Geber-Kennlinie: Liter = 3,0 + 1,10 * Fuel_Tank (DBC-Wert)
FUEL_SENSOR_L_PER_RAW = 1.10
FUEL_SENSOR_SATURATED_RAW = 36.1  # Saettigung liest 36,2 (Aufloesung 0,2)
FUEL_SENSOR_TAU_S = 20.0          # Glaettung gegen Schwappen, nur fuer den Abgleich
FUEL_RESYNC_L = 4.0               # Abweichung Geber <-> Zaehlerstand, ab der der Geber gilt
FUEL_RESYNC_HOLD_STAND_S = 3.0    # so lange muss sie anliegen: im Stand (Tankstopp) kurz,
FUEL_RESYNC_HOLD_MOVING_S = 60.0  # in Fahrt lang, damit Schwappen in Kurven nichts ausloest
FUEL_STAND_KMH = 2.0
FUEL_SAMPLE_GAP_S = 5.0           # laengere Luecke (Zuendung aus, Neustart) = Neubeginn
FUEL_MAX_COUNTS_PER_S = 60.0      # ~43 l/h; groessere Spruenge sind Lesefehler
FUEL_COUNTER_FRESH_S = 5.0        # ohne Zaehler (alte DBC) kein Literwert -> Dash nimmt den Geber
FUEL_SAVE_INTERVAL_S = 30.0
FUEL_STATE_PATH = os.path.join(LOG_DIR, "fuel_state.json")
# Tankbuch (04.11.2026): je candump-Log Literstand bei Beginn/Ende und Tankstopps, eine JSON-Zeile je
# Ereignis, nur angehaengt. Die Vcan-Simulation startet mit dem Stand des abgespielten Logs statt mit
# dem Geber (der ist bis 42,8 l gesaettigt). Alte Logs: scripts/fuel_ledger_backfill.py ("nachberechnet").
FUEL_LEDGER_PATH = os.path.join(LOG_DIR, "fuel_ledger.jsonl")


def fuel_sensor_liters(raw):
    return FUEL_SENSOR_L0 + FUEL_SENSOR_L_PER_RAW * raw


def log_key(path):
    """/home/pi/canlogs/candump-2026-11-04_152230.log.gz -> candump-2026-11-04_152230"""
    return os.path.basename(path).split(".")[0]


def fuel_ledger_append(entry, path=FUEL_LEDGER_PATH, log=print):
    try:
        with open(path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
            os.fsync(fh.fileno())  # keine USV: Strom weg heisst sofort weg
    except OSError as e:
        log(f"{time.strftime('%F %T')} Tankbuch nicht geschrieben: {e}")


def fuel_ledger_start_liters(log_name, path=FUEL_LEDGER_PATH):
    """Literstand bei Beginn des Logs laut Tankbuch, None ohne Eintrag. Der erste Live-Eintrag gilt
    (ein Backend-Neustart in der Fahrt schreibt einen zweiten, mittendrin); ohne ihn der letzte
    nachberechnete."""
    key, found, found_live = log_key(log_name), None, False
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue  # halbe Zeile nach Stromausfall
                if e.get("log") != key or e.get("event") != "start" or e.get("liters") is None:
                    continue
                live = e.get("source") == "live"
                if not found_live:
                    found, found_live = float(e["liters"]), live
    except OSError:
        return None
    return found


def current_candump_log(log_dir=LOG_DIR):
    """Das gerade geschriebene Log: ungepackt, juengster Name (seit der RTC stimmen die Namen)."""
    logs = sorted(glob.glob(os.path.join(log_dir, "candump-*.log")))
    return log_key(logs[-1]) if logs else None


class FuelEstimator:
    """Tankinhalt in Litern: Zaehlerintegration ab einem Ankerpunkt (Volltanken = 45 l), Abgleich
    gegen den Tankgeber. Thread-sicher: Decode-Thread fuettert, Publisher/Prozess-Watch lesen und
    speichern. state_path=None = nichts lesen/schreiben (Vcan-Simulation)."""

    def __init__(self, state_path=None, log=print, on_resync=None):
        self.state_path = state_path
        self._log = log
        self._on_resync = on_resync  # (Volltanken?, Liter vorher, Liter nachher) -> Tankbuch
        self._lock = threading.Lock()
        self.liters = None
        self._counter = None          # (Rohwert, t)
        self._counter_seen = None     # t des letzten gueltigen Zaehlerwerts
        self._sensor = None           # (geglaetteter Rohwert, t)
        self._deviation_since = None
        self._saved_liters = None
        self._saved_t = None
        self._load()

    # -- Persistenz --
    def _load(self):
        if not self.state_path:
            return
        try:
            with open(self.state_path) as fh:
                liters = float(json.load(fh)["liters"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        if 0.0 <= liters <= FUEL_FULL_L:
            self.liters = self._saved_liters = liters

    def save(self, now=None, force=False):
        if not self.state_path:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            liters = self.liters
            if liters is None or liters == self._saved_liters:
                return
            if not force and self._saved_t is not None and now - self._saved_t < FUEL_SAVE_INTERVAL_S:
                return
            self._saved_liters, self._saved_t = liters, now
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump({"liters": round(liters, 3), "saved": time.strftime("%F %T")}, fh)
            os.replace(tmp, self.state_path)
        except OSError as e:
            self._log(f"{time.strftime('%F %T')} Tankstand nicht gespeichert: {e}")

    # -- Eingaenge aus dem Decode-Thread --
    def on_counter(self, value, now):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        with self._lock:
            last, self._counter = self._counter, (value, now)
            if last is None or now - last[1] > FUEL_SAMPLE_GAP_S:
                return
            delta = (value - last[0]) % 256
            if delta > FUEL_MAX_COUNTS_PER_S * (now - last[1]) + 3:
                return  # Lesefehler: nur neu aufsetzen
            self._counter_seen = now
            if self.liters is not None and delta:
                self.liters = max(0.0, self.liters - delta / FUEL_COUNTS_PER_L)
        self.save(now)

    def on_sensor(self, raw, now, speed=None):
        # 0,0 = Init-Artefakt direkt nach Zuendung an (30 ms bis 5,5 s); real waeren das 3,0 l.
        # Die Glaettung startete sonst bei 0 und loeste einen Zwischen-Abgleich aus (Tankstopp 04.11.).
        if not isinstance(raw, (int, float)) or raw <= 0:
            return
        event = resync = None
        with self._lock:
            if self._sensor is None or now - self._sensor[1] > FUEL_SAMPLE_GAP_S:
                smooth = raw
                self._deviation_since = None
            else:
                prev, t_prev = self._sensor
                smooth = prev + (1.0 - math.exp(-(now - t_prev) / FUEL_SENSOR_TAU_S)) * (raw - prev)
            self._sensor = (smooth, now)
            saturated = smooth >= FUEL_SENSOR_SATURATED_RAW
            sensor_l = min(FUEL_FULL_L, fuel_sensor_liters(smooth))
            if self.liters is None:
                # Gesaettigt = frisch getankt (Nutzer tankt immer voll), nicht 42,8 l = 94 % (Sim 04.11.)
                self.liters = FUEL_FULL_L if saturated else sensor_l
                event = f"Start ohne gespeicherten Stand: {self.liters:.1f} l laut Geber"
            else:
                # Gesaettigt heisst "mindestens 42,8 l" - widerspricht nur einem Stand darunter.
                off = (sensor_l - self.liters) if (not saturated or self.liters < sensor_l) else 0.0
                if abs(off) <= FUEL_RESYNC_L:
                    self._deviation_since = None
                else:
                    if self._deviation_since is None:
                        self._deviation_since = now
                    standing = speed is None or speed < FUEL_STAND_KMH  # ohne Tempo: Start im Stand
                    hold = FUEL_RESYNC_HOLD_STAND_S if standing else FUEL_RESYNC_HOLD_MOVING_S
                    if now - self._deviation_since >= hold:
                        new = FUEL_FULL_L if saturated else sensor_l
                        event = (f"{'Volltanken' if saturated else 'Abgleich mit dem Geber'}: "
                                 f"{self.liters:.1f} -> {new:.1f} l (Geber roh {smooth:.1f})")
                        resync = (saturated, self.liters, new)
                        self.liters = new
                        self._deviation_since = None
        if event:
            self._log(f"{time.strftime('%F %T')} Tank: {event}")
            self.save(now, force=True)
        if resync and self._on_resync:
            self._on_resync(*resync)

    def published_liters(self, now=None):
        """Literwert fuers Dash, None solange kein Zaehler laeuft (dann zeigt es den Geber)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.liters is None or self._counter_seen is None:
                return None
            if now - self._counter_seen > FUEL_COUNTER_FRESH_S:
                return None
            return round(self.liters, 2)


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
    DCDC_CAN_ID,
    *OBD_REQUEST_IDS,
})


def extract_brake_pct(data):
    full = int.from_bytes(data, "big")
    total_bits = len(data) * 8
    shift = total_bits - BRAKE_PCT_START_BIT - BRAKE_PCT_LEN
    raw = (full >> shift) & ((1 << BRAKE_PCT_LEN) - 1)
    return max(0.0, min(100.0, (raw - 156) / 2.56))


class ConsumptionMeter:
    """Momentanverbrauch aus den 0x420-Zaehlern ueber CONSUMPTION_WINDOW_S: (l/100km, l/h),
    l/100km ist None unter CONSUMPTION_MIN_KMH (Stand) - dann zeigt das Dash l/h."""

    def __init__(self):
        self._last = None   # (t, weg_roh, kraftstoff_roh)
        self._hist = []     # (t, weg_summe, kraftstoff_summe)

    def update(self, data, t):
        dist_raw, fuel_raw = data[1], data[2]
        if self._last is None or t - self._last[0] > 1.0:
            self._hist = [(t, 0, 0)]
        else:
            d_dist = (dist_raw - self._last[1]) % 256
            d_fuel = (fuel_raw - self._last[2]) % 256
            # 10 Hz: max. ~35 Weg-/~9 Kraftstoffschritte je Frame - mehr ist ein Glitch
            if d_dist > 60 or d_fuel > 20:
                d_dist = d_fuel = 0
            _, dist, fuel = self._hist[-1]
            self._hist.append((t, dist + d_dist, fuel + d_fuel))
        self._last = (t, dist_raw, fuel_raw)
        while len(self._hist) > 2 and t - self._hist[1][0] >= CONSUMPTION_WINDOW_S:
            self._hist.pop(0)
        t0, dist0, fuel0 = self._hist[0]
        t1, dist1, fuel1 = self._hist[-1]
        dist_m = (dist1 - dist0) * TRAVEL_M_PER_STEP
        if t1 - t0 < 0.5 * CONSUMPTION_WINDOW_S:
            return None, None
        litres = (fuel1 - fuel0) / FUEL_STEPS_PER_L
        l100 = None if dist_m < CONSUMPTION_MIN_KMH / 3.6 * (t1 - t0) else litres / (dist_m / 1e5)
        return l100, litres / ((t1 - t0) / 3600)


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
    def __init__(self, channel=CAN_CHANNEL, fuel_state_path=FUEL_STATE_PATH, fuel_ledger_path=FUEL_LEDGER_PATH):
        self.channel = channel
        # Echter Tankstand (auf dem Stick gespeichert) und, waehrend der Vcan-Simulation, ein
        # fluechtiger - das abgespielte Log soll den gespeicherten Stand nicht verstellen.
        self._fuel_real = FuelEstimator(fuel_state_path, on_resync=self._ledger_resync)
        self.fuel = self._fuel_real
        self.fuel_ledger_path = fuel_ledger_path
        self._ledger_log = None            # Logname der laufenden echten Fahrt
        self._ledger_start_pending = False
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
        self._obd_requests = dict.fromkeys(OBD_REQUEST_IDS, 0)  # seit der letzten Zustandszeile
        self._consumption = ConsumptionMeter()
        self._logging_since = None       # Wanduhr, nur noch fuer aeltere dash_gui.py-Staende
        self._logging_since_mono = None
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
        now_mono = time.monotonic()
        since_mono = self._logging_since_mono
        return {
            "t": time.time(),
            "t_mono": now_mono,
            "clock": "monotonic",  # Zeitbasis der Wert-Stempel in "values", siehe Modul-Docstring
            "values": values,
            "error": self.error,
            "dbc_ok": self.db is not None,
            "frames_per_sec": round(self.frames_per_sec, 1),
            "session_max_speed": round(self.session_max_speed, 1),
            "logging_since": self._logging_since,
            "logging_elapsed": None if since_mono is None else round(now_mono - since_mono, 1),
            "fuel_liters": self.fuel.published_liters(now_mono),
            **self._proc_state,
        }

    def _update_logging_edge(self, logging_now):
        """Beginn/Ende einer Logging-Session (== Zuendung an, siehe session_logger.py).
        Beim Beginn startet der REC-Timer und VMAX wird zurueckgesetzt - der Backend-Prozess
        laeuft seit dem Desktop-Login, zu Hause auch ueber mehrere Fahrten hinweg, und
        VMAX zeigte sonst den Hoechstwert irgendeiner frueheren Fahrt."""
        if logging_now:
            if self._logging_since_mono is None:
                self._logging_since_mono = time.monotonic()
                self._logging_since = time.time()
                self.session_max_speed = 0.0
                if not os.path.exists(SIM_TRIGGER_PATH):  # die Sim hat ihr eigenes Log und Tankbuch
                    self._ledger_log = current_candump_log(os.path.dirname(self.fuel_ledger_path))
                    self._ledger_start_pending = self._ledger_log is not None
            # Startstand, sobald bekannt: sofort aus fuel_state.json, ohne sie nach dem ersten Geberwert
            if self._ledger_start_pending and self._fuel_real.liters is not None:
                self._ledger_start_pending = False
                self._ledger_write("start", self._fuel_real.liters)
        else:
            if self._logging_since_mono is not None:
                self._fuel_real.save(force=True)  # Zuendung aus: Stand sofort sichern
                if self._ledger_log is not None and self._fuel_real.liters is not None:
                    self._ledger_write("end", self._fuel_real.liters)
            self._logging_since_mono = None
            self._logging_since = None
            self._ledger_log = None
            self._ledger_start_pending = False

    def _ledger_write(self, event, liters, **extra):
        fuel_ledger_append({"log": self._ledger_log, "event": event, "liters": round(liters, 2), **extra,
                            "time": time.strftime("%F %T"), "source": "live"}, self.fuel_ledger_path)

    def _ledger_resync(self, full, before, after):
        if self._ledger_log is not None:
            self._ledger_write("refuel" if full else "resync", after, before=round(before, 2))

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
            self._update_logging_edge(self._proc_state["logging"])
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
            if channel.startswith("vcan"):
                # Sim: Startstand des abgespielten Logs (dash_gui.py schreibt seinen Pfad in die Trigger-Datei)
                self.fuel = FuelEstimator(None)
                try:
                    with open(SIM_TRIGGER_PATH) as fh:
                        sim_log = fh.read().strip()
                except OSError:
                    sim_log = ""
                self.fuel.liters = fuel_ledger_start_liters(sim_log, self.fuel_ledger_path) if sim_log else None
                print(f"{time.strftime('%F %T')} Sim {log_key(sim_log) or '?'}: Tank laut Tankbuch "
                      f"{self.fuel.liters if self.fuel.liters is not None else 'unbekannt (Geber)'}", flush=True)
            else:
                self.fuel = self._fuel_real
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
                    now = time.monotonic()
                    self._frame_count += 1
                    if msg.arbitration_id in self._obd_requests:
                        self._obd_requests[msg.arbitration_id] += 1
                        continue

                    if msg.arbitration_id == BRAKE_PCT_CAN_ID and len(msg.data) == 8:
                        self._set(BRAKE_PCT_CAN_ID, "_BrakePedalPercent_derived",
                                   extract_brake_pct(msg.data), now)
                    elif msg.arbitration_id == COOLANT_TEMP_CAN_ID and len(msg.data) >= 3:
                        l100, lh = self._consumption.update(msg.data, now)
                        self._set(COOLANT_TEMP_CAN_ID, "_FuelConsumption_l100km_derived", l100, now)
                        self._set(COOLANT_TEMP_CAN_ID, "_FuelRate_lh_derived", lh, now)
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
                            if msg.arbitration_id == FUEL_COUNTER_CAN_ID and FUEL_COUNTER_SIGNAL in decoded:
                                self.fuel.on_counter(decoded[FUEL_COUNTER_SIGNAL], now)
                            elif msg.arbitration_id == FUEL_TANK_CAN_ID and FUEL_TANK_SIGNAL in decoded:
                                speed = self._values.get("514:VehicleSpeed", (None,))[0]
                                self.fuel.on_sensor(decoded[FUEL_TANK_SIGNAL], now,
                                                    speed if isinstance(speed, (int, float)) else None)
                            if msg.arbitration_id == 514 and "VehicleSpeed" in decoded:
                                speed = decoded["VehicleSpeed"]
                                if isinstance(speed, (int, float)) and speed > self.session_max_speed:
                                    self.session_max_speed = speed
            except Exception as e:
                self.error = f"Verbindung verloren ({e})"
                print(f"{time.strftime('%F %T')} {self.error}", flush=True)
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

    def health_line(self, now=None):
        """Eine Zeile Zustand fuer das Log (respawn.sh -> applogs/can_backend.log): Framerate,
        Fehler, Alter der Leitwerte in s (None = nie gesehen), OBD-Anfragen je ID."""
        now = time.monotonic() if now is None else now
        with self._lock:
            ages = {k: (round(now - self._values[key][1], 1) if key in self._values else None)
                    for k, key in HEALTH_KEYS.items()}
            n_values = len(self._values)
        requests, self._obd_requests = self._obd_requests, dict.fromkeys(OBD_REQUEST_IDS, 0)
        obd = " ".join(f"{cid:X}={n}" for cid, n in requests.items())
        state = " ".join(k for k, v in self._proc_state.items() if v) or "-"
        fuel = self.fuel.liters
        tank = "-" if fuel is None else f"{fuel:.2f}l"
        return (f"{time.strftime('%F %T')} fps={self.frames_per_sec:.0f} werte={n_values} "
                f"alter={ages} obd/{HEALTH_LOG_S}s: {obd} tank={tank} status={state} "
                f"fehler={self.error}")

    def run_health_log(self, interval=HEALTH_LOG_S):
        while True:
            time.sleep(interval)
            print(self.health_line(), flush=True)

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
    threading.Thread(target=backend.run_health_log, daemon=True).start()
    print(f"{time.strftime('%F %T')} can_backend.py gestartet (Kanal {backend.channel})", flush=True)
    backend.run_decode_loop()
