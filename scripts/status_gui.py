"""
Statusanzeige fuer den MX-5-CAN-Logger auf dem Pi-Touchdisplay.

Zeigt auf einen Blick: warten auf CAN-Bus / Bus aktiv, wartet auf Zuendung /
Logging laeuft / Fehler. Leitet den Zustand direkt aus dem laufenden System
ab (can0-Interface vorhanden? session_logger.py-Prozess aktiv? candump-
Kindprozess aktiv?) statt einer eigenen Status-Datei/IPC - der Zustand ist
schon von aussen sichtbar, kein Grund das zu duplizieren.

Zusaetzlich: Testmodus (Knopf erscheint nur waehrend "LOGGING LAEUFT") fuehrt
per Touch durch die Checkliste gezielter Standtests (Lenkrad, Blinker, Tueren,
Kupplung, ...) und protokolliert Start-/Endzeit jeder Aktion als Unix-Timestamp
in eine Textdatei auf dem Stick. Abgleich mit dem CAN-Log passiert rein ueber
die gemeinsame absolute Zeitbasis (candump-Zeilen sind schon Unix-Epoch) - keine
Kopplung an session_logger.py/candump-Dateinamen noetig.

Zusaetzlich: waehrend "LOGGING LAEUFT" liest ein Hintergrund-Thread parallel zu
candump direkt vom can0-Socket mit (SocketCAN erlaubt beliebig viele Leser
gleichzeitig, kein Konflikt) und dekodiert eine kuratierte Auswahl bekannter
Signale live mit cantools - fuers direkte Sehen, ob eine Checklisten-Aktion
(Blinker, Tuer, Licht, ...) tatsaechlich ankommt, ohne erst das Log auswerten
zu muessen.
"""

import csv
import datetime
import math
import os
import threading
import time
import tkinter as tk

# Fuer die Motoroeltemperatur (siehe OIL_RESPONSE_ID unten) - weich importiert,
# damit ein fehlendes tpms_poller.py (z.B. beim Testen ausserhalb des Pi-
# Verzeichnisses) nur den Oeltemperatur-Kanal abschaltet statt die ganze GUI
# crashen zu lassen.
try:
    from tpms_poller import decode_response as _uds_decode_response, PCM_PIDS as _PCM_PIDS
except ImportError:
    _uds_decode_response, _PCM_PIDS = None, {}

POLL_INTERVAL_MS = 1000
LIVE_REFRESH_MS = 300
LIVE_STALE_S = 2.0  # Werte aelter als das gelten als "nicht mehr aktuell" (ausgegraut)

# Temporaere Performance-Diagnose (Nutzeranfrage 2026-09-16: gefuehlt niedrige FPS,
# Latenz Signal->Bildschirm). Nur aktiv mit MX5_PERF_DEBUG=1, sonst ohne jede
# Wirkung - kein fester Bestandteil des Dashboards, nach der Messung wieder
# entfernen. Loggt pro Renncockpit-Refresh: tatsaechliches Intervall zum
# vorigen Refresh (= reale interne "FPS") und das Alter des zuletzt
# empfangenen EngineRPM-Samples in diesem Moment (= Software-Latenz Bus->
# Refresh, der Zeitstempel steckt ohnehin schon in LiveCanValues). Das
# eingeblendete Debug-Label macht denselben Wert per Screenshot direkt
# ablesbar, ohne den CSV-Umweg.
PERF_DEBUG = os.environ.get("MX5_PERF_DEBUG") == "1"
PERF_LOG_PATH = os.environ.get("MX5_PERF_LOG", "/tmp/mx5_perf_log.csv")
LOG_DIR = os.environ.get("MX5_LOG_DIR", "/home/pi/canlogs")
CAN_CHANNEL = os.environ.get("MX5_CAN_CHANNEL", "can0")
DBC_CANDIDATES = [
    os.environ.get("MX5_DBC", ""),
    os.path.join(LOG_DIR, "MX5ND_6thGenMazda_HSCAN_extended.dbc"),
    os.path.join(LOG_DIR, "MX5ND_6thGenMazda_HSCAN.dbc"),
]

# (Anzeigename, CAN-ID, Signalname) - kuratierte Auswahl, an der Checkliste
# ausgerichtet (Lenkrad/Blinker/Licht/Wischer/Tueren/Kupplung/Gang/Bremse).
# Signale mit "(neu)" existieren nur in der erweiterten DBC (Kandidaten aus
# dem CAN-Reverse-Engineering vom 2026-09-11).
# Gas/Bremse/Kupplung/Lenkwinkel/Speed werden als grafische Balken angezeigt
# (siehe PEDAL_GAUGES) und sind hier bewusst nicht nochmal als Textzeile
# enthalten.
LIVE_SIGNALS = [
    ("Zündung", 80, "KeyState"),
    ("Anlasssperre", 80, "StarterInterLockSW"),
    ("Drehzahl", 514, "EngineRPM"),
    ("Gang (Actual)", 253, "MT_Gear_Actual"),
    ("Gang-Pos (roh)", 357, "MT_Gear_Position"),
    # Loest die N/1st-Mehrdeutigkeit von "Gang-Pos (roh)" auf: Neutral vs. InGear
    # (siehe mx5_can_bus_status.md, CM_ SG_ 357 MT_Gear_Select in der DBC).
    ("Gang-Wahl (N/InGear)", 357, "MT_Gear_Select"),
    ("Blinker", 154, "Turn"),
    ("Warnblinker", 145, "HAZ_SW"),
    ("Licht", 154, "Headlight"),
    ("Nebel hinten", 1086, "R_FOG_LAMP"),
    ("Wischer vorne", 145, "FrontWiper"),
    # Kein Wischer hinten am MX-5 (Roadster) - stattdessen Gesamtkilometerstand
    # (0x40A, gemultiplext ueber Central_Config_Index=49153, siehe mx5_can_bus_status.md).
    ("Kilometerstand", 1034, "C001_ODO"),
    ("Waschanlage", 145, "Washer"),
    # DoorLeft/DoorRight im DBC vertauscht (siehe mx5_can_bus_status.md, Bit37=DoorRight=
    # physisch links, Bit36=DoorLeft=physisch rechts) - hier gespiegelt, damit das Label
    # zur echten Fahrzeugseite passt.
    ("Tür links", 1086, "DoorRight"),
    ("Tür rechts", 1086, "DoorLeft"),
    ("Kofferraum", 1086, "Trunk"),
    ("Parkbremse", 159, "Parking_Brake"),
    ("Rückwärtsgang", 159, "Reverse_Flag_maybe"),
    ("Speed ABS (neu)", 535, "VehicleSpeed_ABS_raw"),
    ("Drehzahl 0x130 (neu)", 304, "EngineRPM_related_2"),
    ("Kupplung 0x166 (neu)", 358, "Clutch_Pedal_Position_related_2"),
    # Laut DBC-Kommentar vermutlich nur eine grob quantisierte Schaltempfehlungs-Zone,
    # NICHT die tatsaechliche Gangposition - "Gang (Actual)"/"Gang-Pos (roh)" sind
    # verlaesslicher.
    ("Gang-Anzeige (unsicher)", 1143, "GearDisplay_related_maybe"),
    ("Lenkwinkel 0x240 (neu)", 576, "SteeringAngle_related_2_maybe"),
]

# Grafische Balkenanzeigen fuer Gas/Bremse/Kupplung/Lenkwinkel/Speed. "signal"
# mit fuehrendem "_" ist kein echtes DBC-Signal, sondern wird in
# LiveCanValues._run per Hand aus den Rohbytes berechnet (siehe BRAKE_PCT_*) -
# die RaceChrono-Bremspedal-%-Formel ueberlappt bitweise mit BrakePressure und
# kann deshalb nicht als eigenes cantools-SG_ existieren (siehe CAN-Bericht
# 2026-09-11).
PEDAL_GAUGES = [
    {"label": "Gas", "unit": "%", "can_id": 514, "signal": "APP_Accelerator_Pedal_Position",
     "color": "#3987e5", "vmax": 100, "bidir": False, "transform": None},
    {"label": "Bremse", "unit": "%", "can_id": 120, "signal": "_BrakePedalPercent_derived",
     "color": "#d95926", "vmax": 100, "bidir": False, "transform": None},
    {"label": "Kupplung", "unit": "%", "can_id": 304, "signal": "Clutch_Pedal_Position_raw",
     "color": "#199e70", "vmax": 100, "bidir": False, "transform": lambda v: v / 1.99},
    {"label": "Lenkwinkel", "unit": "°", "can_id": 130, "signal": "Steering_Wheel_Absolute_Angle",
     "color": "#c98500", "vmax": 480, "bidir": True, "transform": None},
    {"label": "Speed", "unit": " km/h", "can_id": 514, "signal": "VehicleSpeed",
     "color": "#9085e9", "vmax": 220, "bidir": False, "transform": None},
]

# TPMS-Ecken: Reifendruck/-temperatur an der Bildschirmposition der Radposition
# (oben=vorne, unten=hinten). Tire3=Hinten-Links/Tire4=Hinten-Rechts sind per
# Y-Splitter-Log BESTAETIGT (siehe mx5_can_bus_status.md), Tire1/Tire2=Vorderachse
# sind nur VERMUTET (uebliches VL-VR-HL-HR-Nummerierungsschema) - daher "*" im Label.
TPMS_CAN_ID = 0x728
TPMS_STALE_S = 200  # Poller fragt nur alle 120s ab (siehe session_logger.py) - grosszuegiger als LIVE_STALE_S
TPMS_CORNERS = [
    {"label": "Vorne Links *", "pressure_sig": "Tire1_Pressure", "temp_sig": "Tire1_Temp_maybe",
     "relx": 0.0, "rely": 0.0, "anchor": "nw", "x": 14, "y": 10},
    {"label": "Vorne Rechts *", "pressure_sig": "Tire2_Pressure", "temp_sig": "Tire2_Temp_maybe",
     "relx": 1.0, "rely": 0.0, "anchor": "ne", "x": -14, "y": 10},
    {"label": "Hinten Links", "pressure_sig": "Tire3_Pressure", "temp_sig": "Tire3_Temp_maybe",
     "relx": 0.0, "rely": 1.0, "anchor": "sw", "x": 14, "y": -10},
    {"label": "Hinten Rechts", "pressure_sig": "Tire4_Pressure", "temp_sig": "Tire4_Temp_maybe",
     "relx": 1.0, "rely": 1.0, "anchor": "se", "x": -14, "y": -10},
]


# Bremspedal-% aus 0x78 (BO_120): Bit 27|12 motorola (== MSB-first Bits[28:40)),
# Formel und Bitlage siehe data/can/CAN_unbekannte_signale_bericht_2026-09-11.md.
BRAKE_PCT_CAN_ID = 120
BRAKE_PCT_START_BIT = 28  # MSB-first, 0 = MSB von Byte0
BRAKE_PCT_LEN = 12

# Motoroeltemperatur (DID 0x1310, PCM) wird NICHT gebroadcastet, sondern von
# tpms_poller.py als zweitem Kindprozess aktiv per UDS abgefragt (alle 10s) -
# siehe dort. SocketCAN erlaubt beliebig viele Leser, die Antwortframes landen
# also auch hier auf can0 und lassen sich per Kernel-Filter mitlesen, ganz ohne
# selbst zu pollen. decode_response()/PCM_PIDS kommen von oben (Import), damit
# die Byte-Layout-Logik (SID/DID-Pruefung) nicht dupliziert wird.
OIL_RESPONSE_ID = 0x7E8  # bestaetigt, siehe tpms_poller.py Kommentar zu PCM_REQUEST_ID
OIL_DID = 0x1310
OIL_STALE_S = 25  # 10s-Poll-Intervall, grosszuegiger als LIVE_STALE_S wie beim TPMS-Vorbild

# Renncockpit-Ansicht (siehe mx5_can_bus_status.md, "ND Renncockpit"-Vorschlag):
# schaltet automatisch um, sobald der Motor wirklich laeuft - Leerlauf liegt bei
# ~700-800/min, 300 haelt sicheren Abstand zu Rauschen bei RPM~0.
DRIVE_RPM_THRESHOLD = 300
# Skala geht bis 8 wie bei einem echten Tacho (Nutzer-Feedback 2026-09-15) -
# die Nadel erreicht das Ende praktisch nie, die Schubabschaltung greift vor
# 8000/min. Bernstein/Rot bleiben bei den bestaetigten physischen Werten.
DRIVE_RPM_MAX = 8000
DRIVE_RPM_AMBER = 7000  # ab hier Bernstein-Zone
DRIVE_RPM_RED = 7400    # ab hier Redline (bestaetigt vom Nutzer, 2026-09-15)
DRIVE_SHIFTLIGHT_MIN = 4000  # Fenster, ueber das sich die LED-Kette fuellt
DRIVE_SHIFTLIGHT_N = 10
# Eigene, engere Lenkwinkel-Spanne fuer die Fahransicht (Lock-to-Lock ist
# +-490 Grad, siehe PEDAL_GAUGES - beim Fahren interessiert der Ausschlag um
# die Geradeausstellung, nicht der volle Bereich).
DRIVE_STEER_VMAX = 60

FUEL_TANK_CAN_ID = 158           # 0x9E, HS_IC
COOLANT_TEMP_CAN_ID = 1056       # 0x420, HS_PCM
RCM_LATERAL_CAN_ID = 117         # 0x75, HS_RCM (validiert, siehe mx5_can_bus_status.md)
RCM_LONGITUDINAL_CAN_ID = 118    # 0x76, HS_RCM
ABS_CAN_ID = 529                 # 0x211, HS_ABS - ABS_Active Bit 42, siehe DBC-Kommentar


# Kernel-seitiger SocketCAN-Filter (SO_CAN_RAW_FILTER) auf genau die IDs, die
# irgendein Panel anzeigt - vorher wurde JEDER Frame auf dem HS-CAN bis nach
# Python durchgereicht und mit cantools dekodiert, obwohl nur diese ~17 IDs
# je gebraucht werden. Spart Decode-CPU im Hintergrundthread, der sich sonst
# mit der Tk-Mainloop um die GIL streitet.
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
})


def extract_brake_pct(data):
    full = int.from_bytes(data, "big")
    total_bits = len(data) * 8
    shift = total_bits - BRAKE_PCT_START_BIT - BRAKE_PCT_LEN
    raw = (full >> shift) & ((1 << BRAKE_PCT_LEN) - 1)
    return max(0.0, min(100.0, (raw - 156) / 2.56))


def format_live_value(value):
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)

CHECKLIST = [
    "Lenkrad: Linksanschlag halten",
    "Lenkrad: Rechtsanschlag halten",
    "Lenkrad: Geradeaus (Mitte)",
    "Blinker links",
    "Blinker rechts",
    "Warnblinker",
    "Licht: Standlicht",
    "Licht: Abblendlicht",
    "Licht: Fernlicht",
    "Licht: Aus",
    "Nebelschlussleuchte",
    "Wischer: Intervall",
    "Wischer: An (normal)",
    "Wischer: Schnell",
    "Waschanlage",
    "Tuer links: oeffnen",
    "Tuer links: schliessen",
    "Tuer rechts: oeffnen",
    "Tuer rechts: schliessen",
    "Kofferraum: oeffnen",
    "Kofferraum: schliessen",
    "Handbremse: anziehen",
    "Handbremse: loesen",
    "Rueckwaertsgang: rein",
    "Rueckwaertsgang: raus (Leerlauf)",
    "Kupplung: 0% (oben, eingekuppelt)",
    "Kupplung: ca. 25%",
    "Kupplung: ca. 50%",
    "Kupplung: ca. 75%",
    "Kupplung: 100% (durchgetreten)",
]


def can0_up():
    try:
        with open("/sys/class/net/can0/operstate") as f:
            return f.read().strip() == "up"
    except FileNotFoundError:
        return False


def process_running(pattern):
    # Kein subprocess/pgrep-Fork mehr (blockierte frueher die Tk-Mainloop -
    # inkl. aller 300ms-Gauge-Refreshes - fuer die Dauer von fork()+exec(),
    # unter I/O-Last durch candump/session_logger/tpms_poller spuerbar).
    # /proc direkt lesen ist eine reine Python-Operation, kein neuer Prozess.
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


def get_state():
    # Nur fuer Demo/Vorschau ohne angeschlossenen CAN-Adapter, z.B.
    # MX5_SIMULATE_STATE=LOGGING. Nicht persistiert, wirkt nur fuer den
    # Prozess, dem die Variable beim Start mitgegeben wurde.
    forced = os.environ.get("MX5_SIMULATE_STATE")
    if forced == "LOGGING":
        return "LOGGING LÄUFT", "#1b7a3d", "#ffffff"
    if forced == "WAITING_IGNITION":
        return "BUS AKTIV\nWARTE AUF ZÜNDUNG", "#a86b00", "#ffffff"
    if forced == "WAITING_CAN":
        return "WARTE AUF CAN-BUS", "#2b3a55", "#e8ecf5"
    if forced == "ERROR":
        return "FEHLER\nSERVICE NICHT AKTIV", "#8a1c1c", "#ffffff"

    if not can0_up():
        return "WARTE AUF CAN-BUS", "#2b3a55", "#e8ecf5"
    if process_running("candump -l can0"):
        return "LOGGING LÄUFT", "#1b7a3d", "#ffffff"
    if process_running("session_logger.py"):
        return "BUS AKTIV\nWARTE AUF ZÜNDUNG", "#a86b00", "#ffffff"
    return "FEHLER\nSERVICE NICHT AKTIV", "#8a1c1c", "#ffffff"


def new_actions_log_path():
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return os.path.join(LOG_DIR, f"actions-{ts}.log")


class FullscreenToggler:
    """Doppel-Tap auf ein Hintergrund-Widget (Label/Frame, NICHT ein Button)
    wechselt zwischen Vollbild und Fenstermodus - fuers Touch-Display, auf dem
    keine Escape-Taste erreichbar ist. An Buttons wird bewusst nicht gebunden,
    damit schnelles zweimaliges Antippen von Start/Fertig o.ae. nicht versehentlich
    den Fenstermodus umschaltet."""

    DOUBLE_TAP_S = 0.4

    def __init__(self, root):
        self.root = root
        self._last_tap = 0.0
        # Zustand wird selbst getrackt statt bei Tk abgefragt: root.attributes("-fullscreen")
        # liefert je nach Fensterumgebung nicht zuverlaessig den aktuellen Wert zurueck.
        self.is_fullscreen = True

    def bind(self, *widgets):
        for w in widgets:
            w.bind("<Button-1>", self._on_tap, add="+")

    def set(self, fullscreen):
        self.is_fullscreen = fullscreen
        self.root.attributes("-fullscreen", fullscreen)

    def _on_tap(self, _event):
        now = time.time()
        if now - self._last_tap < self.DOUBLE_TAP_S:
            self.set(not self.is_fullscreen)
            self._last_tap = 0.0
        else:
            self._last_tap = now


class LiveCanValues:
    """Liest im Hintergrund direkt vom can0-Socket (parallel zu candump,
    SocketCAN erlaubt mehrere Leser) und dekodiert mit cantools. Haelt pro
    (can_id, signal) nur den jeweils letzten Wert + Zeitstempel vor - fuer
    eine Live-Anzeige reicht das, keine Historie noetig."""

    def __init__(self, channel=CAN_CHANNEL):
        self.channel = channel
        self._lock = threading.Lock()
        self._values = {}
        self.error = None
        self.db = self._load_db()
        if self.db is None:
            self.error = "keine DBC gefunden"
            return
        self._by_id = {m.frame_id: m for m in self.db.messages if m.signals}
        threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
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

    def _run(self):
        import can
        while True:
            try:
                bus = can.interface.Bus(
                    channel=self.channel, interface="socketcan",
                    can_filters=[{"can_id": cid, "can_mask": 0x7FF} for cid in NEEDED_CAN_IDS])
            except Exception as e:
                self.error = f"CAN-Bus nicht verfügbar ({e})"
                time.sleep(2)
                continue
            self.error = None
            try:
                while True:
                    msg = bus.recv(timeout=1.0)
                    if msg is None:
                        continue
                    now = time.time()
                    if msg.arbitration_id == BRAKE_PCT_CAN_ID and len(msg.data) == 8:
                        with self._lock:
                            self._values[(BRAKE_PCT_CAN_ID, "_BrakePedalPercent_derived")] = (
                                extract_brake_pct(msg.data), now)
                    elif msg.arbitration_id == OIL_RESPONSE_ID and _uds_decode_response is not None:
                        _, n_bytes, formula = _PCM_PIDS[OIL_DID]
                        raw = _uds_decode_response(OIL_DID, msg.data, n_bytes)
                        if raw is not None:
                            with self._lock:
                                self._values[(OIL_RESPONSE_ID, "_OilTemp_derived")] = (
                                    formula(raw), now)
                    m = self._by_id.get(msg.arbitration_id)
                    if m is None:
                        continue
                    try:
                        decoded = m.decode(msg.data, allow_truncated=True, decode_choices=True)
                    except Exception:
                        continue
                    with self._lock:
                        for sig, val in decoded.items():
                            self._values[(msg.arbitration_id, sig)] = (val, now)
            except Exception as e:
                self.error = f"Verbindung verloren ({e})"
            finally:
                try:
                    bus.shutdown()
                except Exception:
                    pass
            time.sleep(2)

    def get(self, can_id, signal):
        with self._lock:
            return self._values.get((can_id, signal))


class BarGauge:
    """Ein Balken (Tkinter-Canvas) fuer einen Pedal-/Lenkwinkelwert. Unidirektional
    fuellt von links; bidirektional (Lenkwinkel) fuellt von der Mitte nach links
    oder rechts. Neuzeichnen ist billig (2-3 Canvas-Primitive pro Tick), auch bei
    haeufigen Refreshes auf schwacher Hardware unkritisch."""

    WIDTH = 190
    HEIGHT = 22

    def __init__(self, parent, spec, bg, show_label=True):
        self.spec = spec
        self.frame = tk.Frame(parent, bg=bg)
        if show_label:
            tk.Label(self.frame, text=spec["label"], font=("DejaVu Sans", 12), bg=bg,
                     fg="#aabbcc", width=10, anchor="w").pack(side="left", padx=(4, 6))
        self.canvas = tk.Canvas(self.frame, width=self.WIDTH, height=self.HEIGHT,
                                 bg="#22222f", highlightthickness=0)
        self.canvas.pack(side="left")
        self.value_label = tk.Label(self.frame, font=("DejaVu Sans", 12, "bold"), bg=bg,
                                     fg="#ffffff", width=7, anchor="e")
        self.value_label.pack(side="left", padx=(8, 4))
        if spec["bidir"]:
            cx = self.WIDTH / 2
            self.canvas.create_line(cx, 0, cx, self.HEIGHT, fill="#555566")

    def update(self, value):
        self.canvas.delete("bar")
        if value is None:
            self.value_label.config(text="–", fg="#555566")
            return
        if self.spec["transform"]:
            value = self.spec["transform"](value)
        vmax = self.spec["vmax"]
        w, h = self.WIDTH, self.HEIGHT
        if self.spec["bidir"]:
            cx = w / 2
            frac = max(-1.0, min(1.0, value / vmax))
            x = cx + frac * cx
            self.canvas.create_rectangle(min(cx, x), 2, max(cx, x), h - 2,
                                          fill=self.spec["color"], outline="", tags="bar")
        else:
            frac = max(0.0, min(1.0, value / vmax))
            self.canvas.create_rectangle(2, 2, 2 + frac * (w - 4), h - 2,
                                          fill=self.spec["color"], outline="", tags="bar")
        self.value_label.config(
            text=f"{value:.0f}{self.spec['unit']}", fg="#ffffff")


class PedalGaugesPanel:
    """Vier Balkenanzeigen (Gas/Bremse/Kupplung/Lenkwinkel), siehe PEDAL_GAUGES."""

    def __init__(self, parent, live, bg="#0d0d1a"):
        self.live = live
        self.frame = tk.Frame(parent, bg=bg)
        self.gauges = []
        for spec in PEDAL_GAUGES:
            g = BarGauge(self.frame, spec, bg)
            g.frame.pack(anchor="w", pady=1)
            self.gauges.append(g)
        self._refresh_job = None
        self._refresh()

    def _refresh(self):
        # StatusGui haelt dieses Panel unabhaengig vom sichtbaren Screen am
        # Leben (siehe DriveDashPanel-Kommentar unten) - ohne diese Bremse
        # liefe die Arbeit hier auch dann unbedingt mit, wenn das Renncockpit
        # aktiv ist und diese Widgets gar nicht zu sehen sind. Bestaetigter
        # Beitrag zu den gemessenen Refresh-Stockern, siehe mx5_can_bus_status.md.
        if self.frame.winfo_ismapped():
            for g in self.gauges:
                spec = g.spec
                entry = self.live.get(spec["can_id"], spec["signal"])
                if entry is None:
                    g.update(None)
                    continue
                value, ts = entry
                g.update(value if time.time() - ts < LIVE_STALE_S else None)
        self._refresh_job = self.frame.after(LIVE_REFRESH_MS, self._refresh)

    def destroy(self):
        if self._refresh_job is not None:
            self.frame.after_cancel(self._refresh_job)
        self.frame.destroy()


class TpmsCornersPanel:
    """Reifendruck/-temperatur in den vier Bildschirm-Ecken (siehe TPMS_CORNERS),
    an der Radposition ausgerichtet. Nutzt place() statt pack(), damit die Ecken
    unabhaengig vom mittigen Status-Layout auf dem ganzen Fenster sitzen -
    show()/hide() blenden alle vier zusammen ein/aus (gleiches Prinzip wie
    pack()/pack_forget() bei den anderen Live-Panels)."""

    def __init__(self, parent, live, bg="#0d0d1a"):
        self.live = live
        self.entries = []
        for spec in TPMS_CORNERS:
            frame = tk.Frame(parent, bg=bg)
            tk.Label(frame, text=spec["label"], font=("DejaVu Sans", 13), bg=bg,
                     fg="#8899aa").pack()
            pressure_lbl = tk.Label(frame, font=("DejaVu Sans", 26, "bold"), bg=bg, fg="#ffffff")
            pressure_lbl.pack()
            temp_lbl = tk.Label(frame, font=("DejaVu Sans", 14), bg=bg, fg="#aabbcc")
            temp_lbl.pack()
            self.entries.append((spec, frame, pressure_lbl, temp_lbl))
        self._refresh_job = None
        self._refresh()

    def show(self):
        for spec, frame, *_ in self.entries:
            frame.place(relx=spec["relx"], rely=spec["rely"], anchor=spec["anchor"],
                        x=spec["x"], y=spec["y"])

    def hide(self):
        for _, frame, *_ in self.entries:
            frame.place_forget()

    def _refresh(self):
        # Nur arbeiten, wenn gerade sichtbar (place()'d) - siehe Kommentar in
        # PedalGaugesPanel._refresh().
        if self.entries[0][1].winfo_ismapped():
            now = time.time()
            for spec, _, pressure_lbl, temp_lbl in self.entries:
                p_entry = self.live.get(TPMS_CAN_ID, spec["pressure_sig"])
                if p_entry is not None and now - p_entry[1] < TPMS_STALE_S:
                    pressure_lbl.config(text=f"{p_entry[0]:.2f} bar", fg="#ffffff")
                else:
                    pressure_lbl.config(text="– bar", fg="#555566")
                t_entry = self.live.get(TPMS_CAN_ID, spec["temp_sig"])
                if t_entry is not None and now - t_entry[1] < TPMS_STALE_S:
                    temp_lbl.config(text=f"{t_entry[0]:.0f} °C", fg="#aabbcc")
                else:
                    temp_lbl.config(text="– °C", fg="#444455")
        self._refresh_job = self.entries[0][1].after(LIVE_REFRESH_MS, self._refresh)

    def destroy(self):
        if self._refresh_job is not None:
            self.entries[0][1].after_cancel(self._refresh_job)
        for _, frame, *_ in self.entries:
            frame.destroy()


class LiveValuesPanel:
    """Kompakte Live-Werte-Tabelle (2 Spalten), fuer die Checkliste kuratiert
    (siehe LIVE_SIGNALS). Werte aelter als 2s werden ausgegraut - so faellt
    sofort auf, wenn ein Kanal seit einer Weile nicht mehr aktualisiert wurde."""

    def __init__(self, parent, live, bg="#0d0d1a"):
        self.live = live
        self.frame = tk.Frame(parent, bg=bg)
        self._value_labels = {}
        cols = 2
        for i, (label, can_id, sig) in enumerate(LIVE_SIGNALS):
            r, c = divmod(i, cols)
            tk.Label(self.frame, text=label + ":", font=("DejaVu Sans", 12), bg=bg,
                      fg="#8899aa", anchor="w").grid(row=r, column=c * 2, sticky="w", padx=(8, 4), pady=1)
            val_lbl = tk.Label(self.frame, text="–", font=("DejaVu Sans", 12, "bold"), bg=bg,
                                fg="#ffffff", anchor="w")
            val_lbl.grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 12), pady=1)
            self._value_labels[(can_id, sig)] = val_lbl
        err_row = (len(LIVE_SIGNALS) + cols - 1) // cols
        self.error_label = tk.Label(self.frame, text="", font=("DejaVu Sans", 12), bg=bg, fg="#ff8888")
        self.error_label.grid(row=err_row, column=0, columnspan=cols * 2, sticky="w", padx=8, pady=(4, 0))
        self._refresh_job = None
        self._refresh()

    def _refresh(self):
        # Nur arbeiten, wenn gerade sichtbar - siehe Kommentar in
        # PedalGaugesPanel._refresh().
        if self.frame.winfo_ismapped():
            self.error_label.config(text=self.live.error or "")
            now = time.time()
            for (can_id, sig), lbl in self._value_labels.items():
                entry = self.live.get(can_id, sig)
                if entry is None:
                    continue
                value, ts = entry
                lbl.config(text=format_live_value(value),
                           fg="#ffffff" if now - ts < LIVE_STALE_S else "#555566")
        self._refresh_job = self.frame.after(LIVE_REFRESH_MS, self._refresh)

    def destroy(self):
        if self._refresh_job is not None:
            self.frame.after_cancel(self._refresh_job)
        self.frame.destroy()


def _format_gear(gear):
    if gear is None:
        return "–"
    gear = int(round(gear))
    if gear == 0:
        return "N"
    if gear == 7:  # noch nicht zuverlaessig bestaetigt, siehe mx5_can_bus_status.md
        return "R"
    return str(gear)


def _polar(cx, cy, r, deg):
    """0 Grad = oben, im Uhrzeigersinn positiv - dieselbe Konvention wie im
    Web-Mockup ("ND Renncockpit"-Artefakt), damit Tick-/Nadel-/Bandmathe an
    einer Stelle steht statt bei jedem Element neu hergeleitet zu werden."""
    t = math.radians(deg - 90)
    return cx + r * math.cos(t), cy + r * math.sin(t)


def _arc_points(cx, cy, r, deg0, deg1, steps=None):
    if steps is None:
        steps = max(2, int(abs(deg1 - deg0) / 6))
    pts = []
    for i in range(steps + 1):
        d = deg0 + (deg1 - deg0) * i / steps
        pts.extend(_polar(cx, cy, r, d))
    return pts


def make_panel(parent, width, height, bg, border="#262a33"):
    """Rechteckiges Panel mit duenner Umrandung fuer die Renncockpit-Module
    (Lampen, Öl/Kühlwasser/Tank, TPMS-Zellen, Gang-/Lenkwinkel-/Drehzahl-Box).

    ponytail-Nachtrag (2026-09-16): urspruenglich die angeschnittene Ecke aus
    dem "ND Renncockpit"-Mockup, per Canvas-Polygon nachgebaut - erst mit
    einem in den Canvas eingebetteten `create_window()`-Kind-Fenster, dann
    (nach demselben Symptom) mit zwei per `place()` uebereinandergelegten
    Geschwister-Widgets. Auf dem Pi (Wayland/labwc, Tk laeuft darunter per
    XWayland) blieben dabei wiederholt und unvorhersehbar Panels leer (TPMS-
    Raster, Gang-Anzeige, Lenkwinkel-Box) - lokal auf X11 nie reproduzierbar,
    und selbst zwischen zwei Screenshots auf demselben Lauf unterschiedlich.
    Beide Varianten ueberlappten ein Canvas mit anderen Widgets; genau das
    war offenbar der fragile Teil. Einfache, nicht ueberlappende Frames
    (wie im Rest dieser Datei seit Monaten) zeigten dagegen nie dieses
    Problem - fuer ein Live-Instrument beim Fahren zaehlt Zuverlaessigkeit
    mehr als eine angeschnittene Ecke, siehe mx5_can_bus_status.md."""
    f = tk.Frame(parent, width=width, height=height, bg=bg,
                 highlightthickness=1, highlightbackground=border)
    f.pack_propagate(False)
    return f


class RpmArcGauge:
    """Drehzahl-Rundinstrument, 240-Grad-Skala (siehe DRIVE_RPM_*-Konstanten).
    Baender/Ticks werden einmal gezeichnet, pro Refresh bewegt sich nur die
    Nadel (canvas.coords auf ein bestehendes Item, kein delete+recreate) -
    wichtig auf der schwachen Pi-CPU, siehe mx5_pi_status_gui_lag_fix.md."""

    # Auf einem echten 1920x1080-Panel getestet (2026-09-15) - deutlich groesser
    # als der erste Wurf, der von einem ~1280x800-Bildschirm ausging und den
    # Dash auf ein Drittel des echten Screens schrumpfte.
    WIDTH, HEIGHT = 560, 440
    CX, CY, R = 280, 310, 190
    SWEEP0, SWEEP1 = -120, 120  # Grad, siehe _polar-Konvention; Luecke unten

    def __init__(self, parent, bg):
        cx, cy, r = self.CX, self.CY, self.R
        self.canvas = tk.Canvas(parent, width=self.WIDTH, height=self.HEIGHT,
                                 bg=bg, highlightthickness=0)

        # Schwacher warmer Glow hinter dem Zifferblatt (Tk-Canvas kennt keine
        # echten Farbverlaeufe/Transparenz - zwei kaum vom Hintergrund
        # abgesetzte Ovale approximieren den radial-gradient aus dem Mockup).
        for rr, col in ((r * 1.35, "#120f0c"), (r * 1.0, "#171310")):
            self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                                     fill=col, outline="")

        self.canvas.create_line(*_arc_points(cx, cy, r, self.SWEEP0, self.SWEEP1),
                                 fill="#20232c", width=5)
        amber0 = self._rpm_to_deg(DRIVE_RPM_AMBER)
        red0 = self._rpm_to_deg(DRIVE_RPM_RED)
        self.canvas.create_line(*_arc_points(cx, cy, r + 14, amber0, red0),
                                 fill="#ffb020", width=11)
        self.canvas.create_line(*_arc_points(cx, cy, r + 14, red0, self.SWEEP1),
                                 fill="#ff2f3a", width=11)

        majors = list(range(0, DRIVE_RPM_MAX + 1, 1000))
        if majors[-1] != DRIVE_RPM_MAX:
            majors.append(DRIVE_RPM_MAX)
        for rpm in majors:
            d = self._rpm_to_deg(rpm)
            x1, y1 = _polar(cx, cy, r - 5, d)
            x2, y2 = _polar(cx, cy, r - 28, d)
            color = "#ff2f3a" if rpm >= DRIVE_RPM_RED else "#7a7e89"
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=3)
            lx, ly = _polar(cx, cy, r - 54, d)
            self.canvas.create_text(lx, ly, text=str(rpm // 1000),
                                     fill="#b0b3bc", font=("DejaVu Sans Mono", 19, "bold"))
        for rpm in range(200, DRIVE_RPM_MAX, 200):
            if rpm % 1000 == 0:
                continue
            d = self._rpm_to_deg(rpm)
            x1, y1 = _polar(cx, cy, r - 5, d)
            x2, y2 = _polar(cx, cy, r - 17, d)
            self.canvas.create_line(x1, y1, x2, y2, fill="#454a56", width=2)

        nx, ny = _polar(cx, cy, r - 38, self.SWEEP0)
        self.needle = self.canvas.create_line(cx, cy, nx, ny, fill="#ff6a1a",
                                               width=8, capstyle="round")
        self.canvas.create_oval(cx - 15, cy - 15, cx + 15, cy + 15,
                                 fill="#1a1c22", outline="#ff6a1a", width=3)

    def _rpm_to_deg(self, rpm):
        rpm = max(0, min(DRIVE_RPM_MAX, rpm))
        return self.SWEEP0 + (rpm / DRIVE_RPM_MAX) * (self.SWEEP1 - self.SWEEP0)

    def update(self, rpm):
        d = self._rpm_to_deg(rpm if rpm is not None else 0)
        nx, ny = _polar(self.CX, self.CY, self.R - 38, d)
        self.canvas.coords(self.needle, self.CX, self.CY, nx, ny)


class GMeterGauge:
    """G-Kreis aus Lateral_Acc_Raw/Longitudinal_Acc_Raw (HS_RCM, validiert
    gegen das OBD-Lenkwinkelmodell, siehe mx5_can_bus_status.md). GMAX=1.2g
    laesst etwas Luft ueber dem bisher hoechsten CAN-bestaetigten Wert
    (1.09g, Log candump-2026-09-15_171047)."""

    SIZE = 300
    GMAX = 1.2

    def __init__(self, parent, bg):
        s = self.SIZE
        self.cx = self.cy = s / 2
        self.r = s * 0.4
        self.canvas = tk.Canvas(parent, width=s, height=s, bg=bg, highlightthickness=0)
        cx, cy, r = self.cx, self.cy, self.r
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#2a2e38", width=2)
        for g in (0.5, 1.0):
            rr = r * g / self.GMAX
            self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline="#20232c")
        self.canvas.create_line(cx - r, cy, cx + r, cy, fill="#1c1f26")
        self.canvas.create_line(cx, cy - r, cx, cy + r, fill="#1c1f26")
        self.dot = self.canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9,
                                            fill="#5ad9ff", outline="")
        self.label = self.canvas.create_text(cx, s - 16, text="– g",
                                              fill="#b0b3bc", font=("DejaVu Sans Mono", 15, "bold"))

    def update(self, a_lat, a_long):
        if a_lat is None or a_long is None:
            self.canvas.itemconfig(self.label, text="– g")
            return
        gx = self.cx + max(-1.0, min(1.0, a_lat / self.GMAX)) * self.r
        gy = self.cy - max(-1.0, min(1.0, a_long / self.GMAX)) * self.r
        self.canvas.coords(self.dot, gx - 9, gy - 9, gx + 9, gy + 9)
        self.canvas.itemconfig(self.label, text=f"{math.hypot(a_lat, a_long):.2f} g")


class ShiftLightBar:
    """LED-Kette, fuellt sich mit der Drehzahl zwischen DRIVE_SHIFTLIGHT_MIN
    und DRIVE_RPM_MAX. Kein Selbsttest-Sweep wie im Web-Mockup - das war dort
    reine Einschalt-Deko, hier reicht der direkte Zustand."""

    OFF = "#1a1d24"
    COLORS = ["#3fd17e"] * 4 + ["#ffb020"] * 3 + ["#ff2f3a"] * 3

    def __init__(self, parent, bg):
        self.frame = tk.Frame(parent, bg=bg)
        self.leds = []
        self._lit = 0  # letzter Stand, um Refreshes ohne Aenderung zu ueberspringen
        for i in range(DRIVE_SHIFTLIGHT_N):
            led = tk.Frame(self.frame, width=52, height=24, bg=self.OFF,
                            highlightthickness=1, highlightbackground="#262a33")
            led.pack(side="left", padx=3)
            led.pack_propagate(False)
            self.leds.append(led)

    def update(self, rpm):
        span = DRIVE_RPM_MAX - DRIVE_SHIFTLIGHT_MIN
        frac = 0.0 if rpm is None else max(0.0, min(1.0, (rpm - DRIVE_SHIFTLIGHT_MIN) / span))
        lit = round(frac * DRIVE_SHIFTLIGHT_N)
        if lit == self._lit:
            return  # spart 10 Widget-Reconfigures pro Refresh, wenn sich nichts aendert
        # Nur die LEDs anfassen, die sich wirklich aendern (meist 0-1 Stueck),
        # statt bei jedem Refresh alle 10 neu zu konfigurieren - auf dem Pi
        # unter CAN-Vollast messbar (siehe mx5_can_bus_status.md).
        lo, hi = sorted((lit, self._lit))
        for i in range(lo, hi):
            self.leds[i].config(bg=self.COLORS[i] if i < lit else self.OFF)
        self._lit = lit


class VerticalBarGauge:
    """Wie BarGauge, aber senkrecht (fuellt von unten) - fuers Renncockpit, wo
    Gas/Bremse/Kupplung als hohe Saeulen deutlich praesenter sind als in der
    schmalen horizontalen Form des Testmodus-Screens. Eigene Klasse statt
    BarGauge um einen Orientierungs-Schalter erweitert, damit der Testmodus-
    Screen unveraendert bleibt (der nutzt BarGauge weiter direkt)."""

    WIDTH, HEIGHT = 92, 360

    def __init__(self, parent, spec, bg, label_font, value_font):
        self.spec = spec
        self.frame = tk.Frame(parent, bg=bg)
        tk.Label(self.frame, text=spec["label"].upper(), font=label_font, bg=bg,
                 fg="#9497a1").pack()
        self.canvas = tk.Canvas(self.frame, width=self.WIDTH, height=self.HEIGHT,
                                 bg="#0a0b0e", highlightthickness=1,
                                 highlightbackground="#262a33")
        self.canvas.pack(pady=(4, 4))
        if spec["bidir"]:
            cy = self.HEIGHT / 2
            self.canvas.create_line(4, cy, self.WIDTH - 4, cy, fill="#333844")
        self.value_label = tk.Label(self.frame, font=value_font, bg=bg, fg="#eae7e0")
        self.value_label.pack()

    def update(self, value):
        self.canvas.delete("bar")
        if value is None:
            self.value_label.config(text="–", fg="#555566")
            return
        if self.spec["transform"]:
            value = self.spec["transform"](value)
        vmax = self.spec["vmax"]
        w, h = self.WIDTH, self.HEIGHT
        if self.spec["bidir"]:
            cy = h / 2
            frac = max(-1.0, min(1.0, value / vmax))
            y = cy - frac * cy
            self.canvas.create_rectangle(3, min(cy, y), w - 3, max(cy, y),
                                          fill=self.spec["color"], outline="", tags="bar")
        else:
            frac = max(0.0, min(1.0, value / vmax))
            self.canvas.create_rectangle(3, h - frac * (h - 6) - 3, w - 3, h - 3,
                                          fill=self.spec["color"], outline="", tags="bar")
        self.value_label.config(text=f"{value:.0f}{self.spec['unit']}", fg="#eae7e0")


class TpmsMiniGrid:
    """TPMS als 2x2-Raster INNERHALB der Ribbon (neben Öl/Kühlwasser/Tank) -
    anders als TpmsCornersPanel (fuer den normalen Status-/Testmodus-Screen),
    das an den echten Bildschirmecken schwebt. Das war fuer den alten
    Testmodus-Screen gedacht, nicht fuers Renncockpit (Nutzer-Feedback
    2026-09-16). Einfache Rahmen-Panels (`make_panel`, kein Chamfer) - dieselbe
    Technik wie bei Öl/Kühlwasser/Tank, dort seither stoerungsfrei."""

    CELL_W, CELL_H = 298, 64

    def __init__(self, parent, live, bg, panel_bg, label_font, value_font):
        self.live = live
        self.frame = tk.Frame(parent, bg=bg)
        self.cells = []
        for i, spec in enumerate(TPMS_CORNERS):
            r, c = divmod(i, 2)
            f = make_panel(self.frame, self.CELL_W, self.CELL_H, panel_bg)
            f.grid(row=r, column=c, padx=4, pady=3)
            tk.Label(f, text=spec["label"], font=label_font, bg=panel_bg,
                     fg="#666a75", anchor="w").pack(fill="x", padx=10, pady=(6, 0))
            row = tk.Frame(f, bg=panel_bg)
            row.pack(fill="x", padx=10)
            p_lbl = tk.Label(row, font=value_font, bg=panel_bg, fg="#eae7e0")
            p_lbl.pack(side="left")
            t_lbl = tk.Label(row, font=("DejaVu Sans", 11), bg=panel_bg, fg="#9497a1")
            t_lbl.pack(side="right")
            self.cells.append((spec, p_lbl, t_lbl))

    def refresh(self):
        now = time.time()
        for spec, p_lbl, t_lbl in self.cells:
            p_entry = self.live.get(TPMS_CAN_ID, spec["pressure_sig"])
            if p_entry is not None and now - p_entry[1] < TPMS_STALE_S:
                p_lbl.config(text=f"{p_entry[0]:.2f}", fg="#eae7e0")
            else:
                p_lbl.config(text="–", fg="#555566")
            t_entry = self.live.get(TPMS_CAN_ID, spec["temp_sig"])
            if t_entry is not None and now - t_entry[1] < TPMS_STALE_S:
                t_lbl.config(text=f"{t_entry[0]:.0f}°", fg="#9497a1")
            else:
                t_lbl.config(text="–°", fg="#444455")


class DriveDashPanel:
    """Renncockpit-Ansicht - ersetzt den normalen Status-Screen automatisch,
    sobald der Motor laeuft (siehe StatusGui.update_state, DRIVE_RPM_THRESHOLD).
    Entwurf/Vorschlag als HTML-Mockup siehe "ND Renncockpit"-Artefakt; hier die
    reale Tkinter-Umsetzung mit denselben Kanaelen. Kein CSS-Chamfer-Motiv,
    keine Web-Fonts (Tk hat nur lokale Systemfonts) - "Piboto Condensed" ist
    aber auf dem Pi bereits installiert und kommt dem Mockup-Look fuer
    Grossbuchstaben-Labels naeher als das Standard-DejaVu Sans.

    Zweiter Anlauf (2026-09-15): der erste Wurf war fuer einen ~1280x800-
    Bildschirm bemessen, der echte Pi-Bildschirm ist aber 1920x1080 (per
    `wlr-randr` bestaetigt) - dadurch wirkte alles winzig und links oben
    zusammengedraengt, siehe Live-Screenshot-Vergleich mit dem Mockup. Jetzt:
    eine feste, deutlich groessere "Geraete"-Karte (CARD_W x CARD_H), auf dem
    Bildschirm zentriert statt bildschirmfuellend gestreckt (bildschirmfuellend
    waere krude verzerrt, weil alle Gauges feste Pixelmasse haben) - GENAU wie
    im Mockup, wo die `.dash`-Karte ebenfalls kleiner als die Seite ist. TPMS
    bleibt bei den an root gehaengten, schwebenden Bildschirmecken
    (`TpmsCornersPanel`, dieselbe Klasse wie im normalen Status-Screen) - ein
    eigenes 2x2-Raster in der Ribbon erwies sich im Live-Test 2026-09-16 als
    unzuverlaessig (siehe `make_panel`)."""

    BG = "#0d0d13"          # Karte
    OUTER_BG = "#07080a"    # Bildschirmhintergrund rund um die Karte
    PANEL_BG = "#151821"
    CARD_W, CARD_H = 1680, 900

    LABEL_FONT = ("Piboto Condensed", 13, "bold")
    LABEL_FONT_SM = ("Piboto Condensed", 11, "bold")
    VALUE_FONT = ("DejaVu Sans Mono", 22, "bold")

    def __init__(self, parent, live):
        self.live = live
        bg = self.BG
        self.frame = tk.Frame(parent, bg=self.OUTER_BG)
        self.card = tk.Frame(self.frame, width=self.CARD_W, height=self.CARD_H,
                              bg=bg, highlightthickness=2, highlightbackground="#262a33")
        self.card.pack_propagate(False)
        self.card.place(relx=0.5, rely=0.5, anchor="center")

        self.drive_pedal_specs = PEDAL_GAUGES[:3]
        self.steer_spec = {
            "label": "Lenkwinkel", "unit": "°", "can_id": 130,
            "signal": "Steering_Wheel_Absolute_Angle", "color": "#5ad9ff",
            "vmax": DRIVE_STEER_VMAX, "bidir": True, "transform": None,
        }

        header = tk.Frame(self.card, bg=bg)
        header.pack(fill="x", padx=28, pady=(16, 0))
        # Kein echtes Mazda-Logo (Markenrechte) - neutrale Raute als Platzhalter.
        tk.Label(header, text="◆", font=("DejaVu Sans", 15), bg=bg,
                 fg="#ff6a1a").pack(side="left", padx=(0, 8))
        tk.Label(header, text="MX-5 ND RF G184", font=("Piboto Condensed", 20, "bold"),
                 bg=bg, fg="#c9cdd6").pack(side="left")

        # Als ein kompaktes Element zentriert statt ueber die volle Kartenbreite
        # auseinandergezogen - bei nur zwei Gruppen (Shift-Lights/Lampen) liesse
        # fill="x" sonst eine grosse, unmotivierte Luecke in der Mitte stehen
        # (siehe Nutzer-Skizze).
        top = tk.Frame(self.card, bg=bg)
        top.pack(pady=(10, 4))
        self.shiftlights = ShiftLightBar(top, bg)
        self.shiftlights.frame.pack(side="left")
        right_top = tk.Frame(top, bg=bg)
        right_top.pack(side="left", padx=(60, 0))
        self.abs_lamp = self._make_lamp(right_top, "ABS")
        self.fuelcut_lamp = self._make_lamp(right_top, "SCHUB")
        self.clock = tk.Label(right_top, font=("DejaVu Sans Mono", 16), bg=bg, fg="#9497a1")
        self.clock.pack(side="left", padx=(12, 0))

        mid = tk.Frame(self.card, bg=bg)
        mid.pack(expand=True, fill="both", padx=28)

        left = tk.Frame(mid, bg=bg)
        left.pack(side="left", fill="y", pady=10)
        self.pedal_gauges = []
        for spec in self.drive_pedal_specs:
            g = VerticalBarGauge(left, spec, bg, self.LABEL_FONT_SM, self.VALUE_FONT)
            g.frame.pack(side="left", padx=8)
            self.pedal_gauges.append(g)

        # Einziges Modul, das im zweiten Wurf noch frei auf der Karte schwebte
        # (siehe Nutzer-Skizze) - jetzt wie alle anderen in einer Box.
        center = tk.Frame(mid, bg=bg)
        center.pack(side="left", expand=True, fill="both")
        gauge_box = make_panel(center, 620, 610, self.PANEL_BG)
        gauge_box.pack()
        self.rpm_gauge = RpmArcGauge(gauge_box, self.PANEL_BG)
        self.rpm_gauge.canvas.pack(pady=(8, 0))
        readout = tk.Frame(gauge_box, bg=self.PANEL_BG)
        readout.pack(pady=(4, 10))
        self.speed_label = tk.Label(readout, text="–", font=("DejaVu Sans Mono", 72, "bold"),
                                     bg=self.PANEL_BG, fg="#eae7e0")
        self.speed_label.pack(side="left")
        tk.Label(readout, text=" km/h", font=self.LABEL_FONT, bg=self.PANEL_BG,
                 fg="#666a75").pack(side="left", anchor="s", pady=(0, 14))
        # Direkt als Label mit Rahmen statt in einem eigenen make_panel()-
        # Wrapper - der zusaetzliche Frame erwies sich hier im Live-Test
        # 2026-09-16 als unzuverlaessig (siehe make_panel-Docstring), diese
        # einfachere Form lief davor stoerungsfrei.
        self.gear_label = tk.Label(readout, text="–", font=("DejaVu Sans Mono", 52, "bold"),
                                    bg=bg, fg="#ff6a1a", width=2,
                                    highlightthickness=2, highlightbackground="#3a3f4b")
        self.gear_label.pack(side="left", padx=(32, 0))

        right = tk.Frame(mid, bg=bg)
        right.pack(side="left", fill="y", pady=10)
        steer_box = make_panel(right, 320, 96, self.PANEL_BG)
        steer_box.pack(anchor="e", pady=(6, 14))
        shead = tk.Frame(steer_box, bg=self.PANEL_BG)
        shead.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(shead, text="LENKWINKEL", font=self.LABEL_FONT, bg=self.PANEL_BG,
                 fg="#666a75").pack(side="left")
        self.steer_value = tk.Label(shead, text="–°", font=self.VALUE_FONT,
                                     bg=self.PANEL_BG, fg="#eae7e0")
        self.steer_value.pack(side="right")
        self.steer_gauge = BarGauge(steer_box, self.steer_spec, self.PANEL_BG, show_label=False)
        self.steer_gauge.value_label.pack_forget()  # eigener grosser Wert oben statt der kleinen Zahl rechts
        self.steer_gauge.frame.pack(padx=10, pady=(6, 10))

        gm_box = make_panel(right, 320, 400, self.PANEL_BG)
        gm_box.pack(anchor="e")
        tk.Label(gm_box, text="G-KREIS", font=self.LABEL_FONT, bg=self.PANEL_BG,
                 fg="#666a75", anchor="w").pack(fill="x", padx=14, pady=(10, 0))
        self.gmeter = GMeterGauge(gm_box, self.PANEL_BG)
        self.gmeter.canvas.pack(pady=(4, 0))

        # Ribbon ueber die volle Kartenbreite (Nutzer-Feedback 2026-09-16) -
        # TPMS zieht von den schwebenden Bildschirmecken (das war fuer den
        # alten Testmodus-Screen gedacht) hier mit rein, gleiche einfache
        # Rahmen-Panels wie Öl/Kühlwasser/Tank (kein Chamfer, unwichtig).
        ribbon = tk.Frame(self.card, bg=bg)
        ribbon.pack(fill="x", padx=28, pady=(4, 22))
        self.oil_cell = self._make_readout(ribbon, "ÖL", "°C")
        self.coolant_cell = self._make_readout(ribbon, "KÜHLWASSER", "°C")
        self.fuel_cell = self._make_readout(ribbon, "TANK", "%")
        self.tpms_grid = TpmsMiniGrid(ribbon, live, bg, self.PANEL_BG,
                                       self.LABEL_FONT_SM, self.VALUE_FONT)
        self.tpms_grid.frame.pack(side="left", padx=(4, 0))

        self._last_refresh_wall = None
        self._perf_log = None
        self.debug_label = None
        if PERF_DEBUG:
            self.debug_label = tk.Label(self.card, font=("DejaVu Sans Mono", 13),
                                         bg=bg, fg="#ff6a1a")
            self.debug_label.place(relx=1.0, rely=0.0, anchor="ne", x=-14, y=44)
            self._perf_log = open(PERF_LOG_PATH, "a")
            self._perf_log.write("wall_time,refresh_interval_ms,rpm_signal_age_ms\n")

        self._refresh_job = None
        self._refresh()

    def _make_lamp(self, parent, text):
        f = make_panel(parent, 116, 44, self.PANEL_BG)
        f.pack(side="left", padx=6)
        dot = tk.Label(f, text="●", font=("DejaVu Sans", 13), bg=self.PANEL_BG, fg="#333844")
        dot.pack(side="left", padx=(10, 4), pady=5)
        tk.Label(f, text=text, font=self.LABEL_FONT, bg=self.PANEL_BG,
                 fg="#666a75").pack(side="left", padx=(0, 10))
        return dot

    def _make_readout(self, parent, label, unit):
        f = make_panel(parent, 315, 140, self.PANEL_BG)
        f.pack(side="left", padx=(0, 12))
        tk.Label(f, text=label, font=self.LABEL_FONT, bg=self.PANEL_BG,
                 fg="#666a75", anchor="w").pack(fill="x", padx=16, pady=(14, 0))
        val = tk.Label(f, text="–", font=("DejaVu Sans Mono", 32, "bold"),
                        bg=self.PANEL_BG, fg="#eae7e0", anchor="w")
        val.pack(fill="x", padx=16, pady=(2, 10))
        val.unit = unit
        return val

    def show(self):
        self.frame.pack(expand=True, fill="both")

    def hide(self):
        self.frame.pack_forget()

    def _get(self, can_id, signal, max_age=LIVE_STALE_S):
        entry = self.live.get(can_id, signal)
        if entry is None or time.time() - entry[1] > max_age:
            return None
        return entry[0]

    def _set_readout(self, label, value):
        if value is None:
            label.config(text="–", fg="#555566")
        else:
            label.config(text=f"{value:.0f}{label.unit}", fg="#eae7e0")

    def get_rpm(self):
        """Fuer StatusGui.update_state() - dieselbe Quelle, mit der auch die
        Nadel gezeichnet wird, statt den Live-Zugriff dort zu duplizieren."""
        return self._get(514, "EngineRPM")

    def _perf_tick(self):
        """Nur bei MX5_PERF_DEBUG=1 aktiv, siehe PERF_DEBUG-Konstante."""
        now = time.time()
        interval_ms = None if self._last_refresh_wall is None else (now - self._last_refresh_wall) * 1000
        self._last_refresh_wall = now
        rpm_entry = self.live.get(514, "EngineRPM")  # bewusst ungefiltert von LIVE_STALE_S
        rpm_age_ms = (now - rpm_entry[1]) * 1000 if rpm_entry is not None else None
        self._perf_log.write(
            f"{now:.6f},{'' if interval_ms is None else f'{interval_ms:.1f}'},"
            f"{'' if rpm_age_ms is None else f'{rpm_age_ms:.1f}'}\n")
        self._perf_log.flush()
        parts = [f"t={now:.3f}"]
        if interval_ms is not None:
            parts.append(f"Δrefresh={interval_ms:.0f}ms")
        if rpm_age_ms is not None:
            parts.append(f"RPM-Alter={rpm_age_ms:.0f}ms")
        self.debug_label.config(text="  ".join(parts))

    def _refresh(self):
        if PERF_DEBUG:
            self._perf_tick()

        rpm = self.get_rpm()
        self.rpm_gauge.update(rpm)
        self.shiftlights.update(rpm)

        speed = self._get(514, "VehicleSpeed")
        self.speed_label.config(text="–" if speed is None else f"{speed:.0f}")
        self.gear_label.config(text=_format_gear(self._get(253, "MT_Gear_Actual")))

        for g in self.pedal_gauges:
            spec = g.spec
            g.update(self._get(spec["can_id"], spec["signal"]))
        steer_val = self._get(self.steer_spec["can_id"], self.steer_spec["signal"])
        self.steer_gauge.update(steer_val)
        self.steer_value.config(text="–°" if steer_val is None else f"{steer_val:.0f}°")

        a_lat = self._get(RCM_LATERAL_CAN_ID, "Lateral_Acc_Raw")
        a_long = self._get(RCM_LONGITUDINAL_CAN_ID, "Longitudinal_Acc_Raw")
        self.gmeter.update(a_lat, a_long)

        abs_active = self._get(ABS_CAN_ID, "ABS_Active")
        self.abs_lamp.config(fg="#ff2f3a" if abs_active else "#333844")
        fuelcut = self._get(253, "FuelCut")
        self.fuelcut_lamp.config(fg="#5ad9ff" if fuelcut else "#333844")

        self._set_readout(self.oil_cell, self._get(OIL_RESPONSE_ID, "_OilTemp_derived",
                                                     max_age=OIL_STALE_S))
        self._set_readout(self.coolant_cell, self._get(COOLANT_TEMP_CAN_ID, "CoolantTemp"))
        fuel_raw = self._get(FUEL_TANK_CAN_ID, "Fuel_Tank")
        # Kalibrierung FLI% ~= 2.486*raw - 0.02, siehe mx5_can_bus_status.md
        # (raw = bereits DBC-dekodierter Fuel_Tank-Wert, noch kein Prozentwert).
        fuel_pct = None if fuel_raw is None else max(0.0, min(100.0, 2.486 * fuel_raw - 0.02))
        self._set_readout(self.fuel_cell, fuel_pct)
        self.tpms_grid.refresh()

        self.clock.config(text=datetime.datetime.now().strftime("%H:%M:%S"))

        self._refresh_job = self.frame.after(LIVE_REFRESH_MS, self._refresh)

    def destroy(self):
        if self._refresh_job is not None:
            self.frame.after_cancel(self._refresh_job)
        if self._perf_log is not None:
            self._perf_log.close()
        self.frame.destroy()


class StatusGui:
    def __init__(self, root, live):
        self.root = root
        self.live = live
        root.attributes("-fullscreen", True)
        self.toggler = FullscreenToggler(root)
        root.bind("<Escape>", lambda e: self.toggler.set(False))
        root.bind("q", lambda e: root.destroy())

        self.status_frame = tk.Frame(root)
        self.label = tk.Label(self.status_frame, text="", font=("DejaVu Sans", 56, "bold"), justify="center")
        self.label.pack(expand=True, fill="both")
        self.test_button = tk.Button(
            self.status_frame, text="Testmodus starten", font=("DejaVu Sans", 24),
            command=self.start_test_mode,
        )
        self.gauges_panel = PedalGaugesPanel(self.status_frame, live)
        self.live_panel = LiveValuesPanel(self.status_frame, live)
        self.tpms_panel = TpmsCornersPanel(self.status_frame, live)
        self.clock = tk.Label(self.status_frame, font=("DejaVu Sans", 20))
        self.clock.pack(side="bottom", pady=10)
        self.toggler.bind(self.status_frame, self.label, self.clock)

        # Renncockpit-Ansicht (siehe DriveDashPanel) - eigener Screen, der den
        # Status-Screen automatisch ersetzt statt in ihm zu leben, an root
        # gehaengt statt an status_frame, damit beide unabhaengig ein-/
        # ausgeblendet werden koennen (genau wie ChecklistGui es schon tut).
        self.drive_dash = DriveDashPanel(root, live)
        self.toggler.bind(self.drive_dash.frame)

        self.status_frame.pack(expand=True, fill="both")
        self.checklist_gui = None
        # Welcher Screen zuletzt sichtbar gemacht wurde ("drive"/"status_logging"/
        # "status_idle"/"testmode") - update_state() fasst pack()/pack_forget()
        # sonst JEDE Sekunde unbedingt an, auch wenn sich nichts geaendert hat.
        # Das ist fuer den tief verschachtelten Renncockpit-Baum ein messbarer
        # Tk-Geometrie-Overhead (siehe mx5_can_bus_status.md, Performance-Messung
        # 2026-09-16: Refresh-Intervalle bis 1,8s statt der gewollten 300ms).
        self._visible_screen = None
        self.update_state()

    def update_state(self):
        text, bg, fg = get_state()
        self.label.config(text=text, bg=bg, fg=fg)
        self.clock.config(bg=bg, fg=fg)
        self.status_frame.config(bg=bg)
        self.clock.config(text=datetime.datetime.now().strftime("%H:%M:%S"))

        logging_now = text == "LOGGING LÄUFT"
        rpm = self.drive_dash.get_rpm() if logging_now else None
        engine_running = logging_now and rpm is not None and rpm > DRIVE_RPM_THRESHOLD

        if self.checklist_gui is not None:
            target = "testmode"
        elif engine_running:
            target = "drive"
        elif logging_now:
            target = "status_logging"
        else:
            target = "status_idle"

        if target != self._visible_screen:
            # Erst ALLES explizit ausblenden, dann nur das Ziel einblenden -
            # keine Annahme mehr, dass ein unsichtbarer Vorfahre (status_frame)
            # auch place()-Kinder (TPMS-Ecken) zuverlaessig mit-verdeckt. Genau
            # das hat winfo_ismapped() in den Panel-eigenen Refresh-Loops
            # getaeuscht: py-spy zeigte TpmsCornersPanel/LiveValuesPanel bei
            # der Performance-Messung 2026-09-16 weiter aktiv, obwohl laengst
            # "drive" sichtbar war - ein guter Teil der gemessenen Refresh-
            # Stocker kam von dieser unnoetigen Arbeit im Hintergrund.
            self.drive_dash.hide()
            self.status_frame.pack_forget()
            self.test_button.pack_forget()
            self.gauges_panel.frame.pack_forget()
            self.live_panel.frame.pack_forget()
            self.tpms_panel.hide()

            if target == "drive":
                self.drive_dash.show()
            elif target in ("status_logging", "status_idle"):
                self.status_frame.pack(expand=True, fill="both")
                if target == "status_logging":
                    self.test_button.pack(pady=(10, 0))
                    self.gauges_panel.frame.pack(pady=(10, 4))
                    self.live_panel.frame.pack(pady=(4, 10))
                    self.tpms_panel.show()
            # "testmode": alles bleibt aus, ChecklistGui hat die Flaeche.

            self._visible_screen = target

        self.root.after(POLL_INTERVAL_MS, self.update_state)

    def start_test_mode(self):
        self.status_frame.pack_forget()
        self.checklist_gui = ChecklistGui(self.root, on_finish=self.end_test_mode,
                                           toggler=self.toggler, live=self.live)

    def end_test_mode(self):
        if self.checklist_gui is not None:
            self.checklist_gui.destroy()
            self.checklist_gui = None
        self.status_frame.pack(expand=True, fill="both")


class ChecklistGui:
    """Fuehrt per Touch durch CHECKLIST, protokolliert Start/Ende jeder Aktion
    als Unix-Timestamp in eine neue Datei unter LOG_DIR."""

    def __init__(self, root, on_finish, toggler, live):
        self.root = root
        self.on_finish = on_finish
        self.index = 0
        self.step_start = None
        self.log_path = new_actions_log_path()
        self.history = []

        self.frame = tk.Frame(root, bg="#1a1a2e")
        self.frame.pack(expand=True, fill="both")

        self.progress_label = tk.Label(
            self.frame, font=("DejaVu Sans", 18), bg="#1a1a2e", fg="#aaaaaa")
        self.progress_label.pack(pady=(20, 0))

        self.step_label = tk.Label(
            self.frame, font=("DejaVu Sans", 40, "bold"), bg="#1a1a2e", fg="#ffffff",
            wraplength=900, justify="center")
        self.step_label.pack(expand=True, fill="both", padx=20)
        toggler.bind(self.frame, self.progress_label, self.step_label)

        self.action_button = tk.Button(
            self.frame, text="Start", font=("DejaVu Sans", 32, "bold"),
            bg="#1b7a3d", fg="#ffffff", height=2, command=self.on_action_button)
        self.action_button.pack(fill="x", padx=60, pady=10)

        nav = tk.Frame(self.frame, bg="#1a1a2e")
        nav.pack(fill="x", padx=20, pady=(0, 10))
        tk.Button(nav, text="< Zurück", font=("DejaVu Sans", 18), command=self.go_back).pack(
            side="left", expand=True, fill="x", padx=5)
        tk.Button(nav, text="Überspringen", font=("DejaVu Sans", 18), command=self.skip_step).pack(
            side="left", expand=True, fill="x", padx=5)
        tk.Button(nav, text="Test beenden", font=("DejaVu Sans", 18), bg="#8a1c1c", fg="white",
                  command=self.finish).pack(side="left", expand=True, fill="x", padx=5)

        self.history_label = tk.Label(
            self.frame, font=("DejaVu Sans", 14), bg="#1a1a2e", fg="#7fdd8f",
            justify="left", anchor="w")
        self.history_label.pack(fill="x", padx=20, pady=(0, 5))
        toggler.bind(self.history_label)

        self.gauges_panel = PedalGaugesPanel(self.frame, live, bg="#1a1a2e")
        self.gauges_panel.frame.pack(pady=(0, 4))
        toggler.bind(self.gauges_panel.frame)

        self.live_panel = LiveValuesPanel(self.frame, live, bg="#1a1a2e")
        self.live_panel.frame.pack(pady=(0, 10))
        toggler.bind(self.live_panel.frame)

        self._write_row("_session_start", "", time.time(), time.time())
        self.show_step()

    def _write_row(self, step_name, phase, t_start, t_end):
        os.makedirs(LOG_DIR, exist_ok=True)
        is_new = not os.path.exists(self.log_path)
        with open(self.log_path, "a", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["step", "phase", "t_start_epoch", "t_end_epoch",
                            "t_start_local", "t_end_local"])
            w.writerow([
                step_name, phase, f"{t_start:.3f}", f"{t_end:.3f}",
                datetime.datetime.fromtimestamp(t_start).strftime("%H:%M:%S"),
                datetime.datetime.fromtimestamp(t_end).strftime("%H:%M:%S"),
            ])

    def show_step(self):
        if self.index >= len(CHECKLIST):
            self.step_label.config(text=f"Fertig!\n{len(CHECKLIST)} Schritte protokolliert.")
            self.action_button.config(text="Beenden", bg="#1b7a3d", command=self.finish)
            self.progress_label.config(text="")
            return
        self.progress_label.config(text=f"Schritt {self.index + 1}/{len(CHECKLIST)}")
        self.step_label.config(text=CHECKLIST[self.index])
        self.action_button.config(text="Start", bg="#1b7a3d")
        self.step_start = None

    def on_action_button(self):
        if self.step_start is None:
            self.step_start = time.time()
            self.action_button.config(text="Fertig", bg="#a86b00")
        else:
            t_end = time.time()
            step_name = CHECKLIST[self.index]
            self._write_row(step_name, "done", self.step_start, t_end)
            self._push_history(f"✓ {step_name}  ({t_end - self.step_start:.1f}s)")
            self.index += 1
            self.show_step()

    def skip_step(self):
        if self.index >= len(CHECKLIST):
            return
        now = time.time()
        step_name = CHECKLIST[self.index]
        self._write_row(step_name, "skipped", now, now)
        self._push_history(f"– {step_name} übersprungen")
        self.index += 1
        self.show_step()

    def go_back(self):
        if self.index > 0:
            self.index -= 1
            self.show_step()

    def _push_history(self, line):
        self.history.append(line)
        self.history_label.config(text="\n".join(self.history[-3:]))

    def finish(self):
        self.on_finish()

    def destroy(self):
        self.gauges_panel.destroy()
        self.live_panel.destroy()
        self.frame.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("MX-5 CAN Logger Status")
    live = LiveCanValues()
    gui = StatusGui(root, live)
    if os.environ.get("MX5_START_IN_TESTMODE") == "1":
        gui.start_test_mode()
    root.mainloop()
