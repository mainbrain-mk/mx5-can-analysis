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
import os
import threading
import time
import tkinter as tk

POLL_INTERVAL_MS = 1000
LIVE_REFRESH_MS = 300
LIVE_STALE_S = 2.0  # Werte aelter als das gelten als "nicht mehr aktuell" (ausgegraut)
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

    def __init__(self, parent, spec, bg):
        self.spec = spec
        self.frame = tk.Frame(parent, bg=bg)
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

        self.status_frame.pack(expand=True, fill="both")
        self.checklist_gui = None
        self.update_state()

    def update_state(self):
        text, bg, fg = get_state()
        self.label.config(text=text, bg=bg, fg=fg)
        self.clock.config(bg=bg, fg=fg)
        self.status_frame.config(bg=bg)
        self.clock.config(text=datetime.datetime.now().strftime("%H:%M:%S"))
        if text == "LOGGING LÄUFT":
            self.test_button.pack(pady=(10, 0))
            self.gauges_panel.frame.pack(pady=(10, 4))
            self.live_panel.frame.pack(pady=(4, 10))
            self.tpms_panel.show()
        else:
            self.test_button.pack_forget()
            self.gauges_panel.frame.pack_forget()
            self.live_panel.frame.pack_forget()
            self.tpms_panel.hide()
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
