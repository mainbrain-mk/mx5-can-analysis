"""
Renncockpit-Frontend - reines Kivy/OpenGL-Rendering, liest CAN-Werte nicht
mehr selbst, sondern per UDP-Snapshot von can_backend.py (siehe dort fuer den
Hintergrund/die Architekturbegruendung: getrennte Prozesse statt Thread+Tk,
damit Rendering und CAN-Decode nicht mehr um den GIL konkurrieren).

Ersetzt status_gui.py produktiv (das bleibt unveraendert als sofortiger
Fallback liegen). Drei Screens wie zuvor:
  - "status"   - Wartezustand/Logging-Anzeige (frueher StatusGui.status_frame)
  - "testmode" - Checkliste mit Zeitstempel-Protokoll (frueher ChecklistGui)
  - "drive"    - Renncockpit, neu gestaltet nach Design-Vorlage 2026-09-16
Die Umschaltlogik entspricht StatusGui.update_state() in status_gui.py.
"""
import csv
import datetime
import glob
import gzip
import json
import os
import re
import socket
import subprocess
import threading
import time

os.environ.setdefault("KIVY_NO_ARGS", "1")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle, Ellipse
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.screenmanager import ScreenManager, Screen, NoTransition

LOG_DIR = os.environ.get("MX5_LOG_DIR", "/home/pi/canlogs")
# Neben dem Skript statt in LOG_DIR - ein Asset, kein Laufzeit-/Log-Artefakt.
CAR_TOP_VIEW_PNG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mx5_top_view.png")
UDP_PORT = int(os.environ.get("MX5_DASH_UDP_PORT", "51234"))
# Gegenstueck zu can_backend.py SIM_TRIGGER_PATH - siehe SimController unten.
SIM_TRIGGER_PATH = "/tmp/mx5_sim_active"
SIM_REPLAY_LOG = "/tmp/mx5_sim_replay.log"
POLL_INTERVAL_S = 1.0
DRIVE_REFRESH_HZ = 30
LIVE_STALE_S = 2.0
OIL_STALE_S = 25
TPMS_STALE_S = 200
LAMBDA_STALE_S = 25
BATTERY_STALE_S = 25

DRIVE_RPM_THRESHOLD = 300
DRIVE_RPM_MAX = 8000
DRIVE_RPM_YELLOW = 7000
DRIVE_RPM_ORANGE = 7400
DRIVE_RPM_RED = 7500
DRIVE_SHIFTLIGHT_MIN = 4000
DRIVE_SHIFTLIGHT_N = 16
DRIVE_STEER_VMAX = 60

OIL_RESPONSE_KEY = "2024"  # 0x7E8 als String, siehe can_backend.py-Snapshot-Keys "can_id:signal"
FUEL_TANK_CAN_ID = 158
COOLANT_TEMP_CAN_ID = 1056
ABS_CAN_ID = 529
IAT_CAN_ID = 1274
LOAD_CAN_ID = 359
TPMS_CAN_ID = 0x728

# Farben, konsistent mit dem bisherigen Renncockpit (status_gui.py DriveDashPanel)
# und der neuen Design-Vorlage.
BG = (0.03, 0.03, 0.04, 1)
CARD_BG = (0.08, 0.09, 0.11, 1)
BORDER = (0.15, 0.16, 0.2, 1)
CAR_LINE = (0.4, 0.43, 0.49, 1)  # heller als BORDER - die Silhouette soll ablesbar bleiben
TEXT = (0.91, 0.92, 0.96, 1)
TEXT_DIM = (0.4, 0.42, 0.47, 1)
RED = (1, 0.18, 0.23, 1)
YELLOW = (1, 0.83, 0, 1)
ORANGE = (1, 0.55, 0.1, 1)
GREEN = (0.18, 0.8, 0.32, 1)
BLUE = (0.22, 0.53, 0.9, 1)
GOOD = GREEN
WARN = YELLOW


def _rpm_zone_color(rpm):
    if rpm is None:
        return TEXT_DIM
    if rpm >= DRIVE_RPM_RED:
        return RED
    if rpm >= DRIVE_RPM_ORANGE:
        return ORANGE
    if rpm >= DRIVE_RPM_YELLOW:
        return YELLOW
    return TEXT


def _format_gear(raw):
    if raw is None:
        return "-"
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return str(raw)
    if n == 0:
        return "N"
    if n == 7:
        return "R"
    return str(n)


# --- IPC: liest die can_backend.py-UDP-Snapshots -----------------------------------------

class SnapshotClient:
    """Haelt nur den jeweils neuesten Snapshot vor (kein Verlauf noetig) -
    UDP-Datagramme sind atomar, also kein Lock fuer Torn-Reads noetig, nur
    fuer den gemeinsamen Zugriff auf die Referenz selbst."""

    def __init__(self, port=UDP_PORT):
        self.port = port
        self._lock = threading.Lock()
        self._snapshot = {"values": {}, "error": "warte auf can_backend.py", "dbc_ok": False,
                           "can_up": False, "logging": False, "session_logger_running": False,
                           "frames_per_sec": 0, "session_max_speed": 0, "logging_since": None}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", self.port))
        while True:
            try:
                data, _ = sock.recvfrom(65536)
                snap = json.loads(data)
            except (OSError, json.JSONDecodeError):
                continue
            with self._lock:
                self._snapshot = snap

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def get(self, can_id, signal, max_age=LIVE_STALE_S):
        snap = self.snapshot()
        entry = snap["values"].get(f"{can_id}:{signal}")
        if entry is None:
            return None
        value, ts = entry
        if time.time() - ts > max_age:
            return None
        return value


# --- Wiederverwendbare Widgets -------------------------------------------------------------

class Panel(BoxLayout):
    """Rahmen-Panel, Nachfolger von make_panel() in status_gui.py - bewusst
    schlichte Rechtecke, keine angeschnittenen Ecken (siehe mx5_can_bus_status.md,
    Renncockpit-Zuverlaessigkeits-Odyssee - Chamfer-Panels waren auf dem alten
    Wayland/Tk-Stack unzuverlaessig; hier zwar ein anderer Rendering-Stack,
    aber kein Grund, dasselbe Risiko erneut einzugehen)."""

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        with self.canvas.before:
            Color(*CARD_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[8])
            Color(*BORDER)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, 8), width=1.2)
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *_args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, 8)


class MetricCard(Panel):
    """Kachel: Titel oben, grosser Wert+Einheit, optional ein Fuellbalken darunter -
    Nachfolger der Ribbon-Zellen (Oel/Kuehlwasser/Tank) UND der unteren
    Kennzahlen-Reihe (Gas/Bremse/Lambda/...) aus der Design-Vorlage."""

    def __init__(self, title, unit="", bar_color=None, bar_max=100, value_fmt="{:.0f}", **kwargs):
        super().__init__(padding=(14, 10), spacing=2, **kwargs)
        self.unit = unit
        self.bar_max = bar_max
        self.value_fmt = value_fmt
        self.title_label = Label(text=title, font_size="14sp", color=TEXT_DIM, bold=True,
                                  size_hint_y=None, height=22, halign="left", valign="middle")
        self.title_label.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.value_label = Label(text="–", font_size="34sp", color=TEXT, bold=True,
                                  halign="left", valign="middle")
        self.value_label.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(self.title_label)
        self.add_widget(self.value_label)
        self.bar = None
        if bar_color is not None:
            self.bar = FillBar(color=bar_color, size_hint_y=None, height=8)
            self.add_widget(self.bar)

    def set_value(self, value):
        if value is None:
            self.value_label.text = "–"
            self.value_label.color = TEXT_DIM
            if self.bar is not None:
                self.bar.frac = 0.0
            return
        self.value_label.text = self.value_fmt.format(value) + self.unit
        self.value_label.color = TEXT
        if self.bar is not None:
            self.bar.frac = max(0.0, min(1.0, value / self.bar_max))


class FillBar(Widget):
    def __init__(self, color=GREEN, **kwargs):
        super().__init__(**kwargs)
        self.color = color
        self._frac = 0.0
        with self.canvas:
            Color(1, 1, 1, 0.08)
            self._track = Rectangle(pos=self.pos, size=self.size)
            Color(*self.color)
            self._fill = Rectangle(pos=self.pos, size=(0, self.height))
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *_args):
        self._track.pos = self.pos
        self._track.size = self.size
        self._fill.pos = self.pos
        self._fill.size = (self.width * self._frac, self.height)

    # Plain Python-Property statt Kivy-NumericProperty: es gibt keine externen
    # Bindings auf frac, ein simples Redraw-on-set reicht.
    @property
    def frac(self):
        return self._frac

    @frac.setter
    def frac(self, value):
        self._frac = value
        self._redraw()


class ShiftLightRow(BoxLayout):
    """LED-Kette, faerbt sich von unten (Basis) ueber Gelb/Orange bis Rot je
    nach Drehzahl - Nachfolger von ShiftLightBar in status_gui.py, hier als
    horizontale Punktreihe wie in der Design-Vorlage."""

    DOT_SIZE = 34

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", spacing=4, **kwargs)
        anchor = AnchorLayout(anchor_x="center", anchor_y="center", size_hint_y=None, height=self.DOT_SIZE)
        dots_row = BoxLayout(orientation="horizontal", spacing=8, size_hint=(None, None),
                              size=(DRIVE_SHIFTLIGHT_N * (self.DOT_SIZE + 8), self.DOT_SIZE))
        self.dots = []
        for i in range(DRIVE_SHIFTLIGHT_N):
            dot = LedDot(size_hint=(None, None), size=(self.DOT_SIZE, self.DOT_SIZE))
            dots_row.add_widget(dot)
            self.dots.append(dot)
        anchor.add_widget(dots_row)
        label_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=16)
        label_row.add_widget(Label(text="SHIFT LIGHTS", font_size="11sp", color=TEXT_DIM))
        self.add_widget(anchor)
        self.add_widget(label_row)

    def update(self, rpm):
        span = DRIVE_RPM_MAX - DRIVE_SHIFTLIGHT_MIN
        frac = 0.0 if rpm is None else max(0.0, min(1.0, (rpm - DRIVE_SHIFTLIGHT_MIN) / span))
        lit = round(frac * DRIVE_SHIFTLIGHT_N)
        for i, dot in enumerate(self.dots):
            dot_rpm = DRIVE_SHIFTLIGHT_MIN + (i / DRIVE_SHIFTLIGHT_N) * span
            color = _rpm_zone_color(dot_rpm) if dot_rpm >= DRIVE_RPM_YELLOW else GREEN
            dot.set_lit(i < lit, color)


class LedDot(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.lit = False
        self.color = GREEN
        with self.canvas:
            Color(0.2, 0.2, 0.22, 1)
            self._circle = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *_args):
        self._circle.pos = self.pos
        self._circle.size = self.size

    def set_lit(self, lit, color):
        if lit == self.lit and color == self.color:
            return
        self.lit, self.color = lit, color
        self.canvas.clear()
        with self.canvas:
            if lit:
                Color(*color)
            else:
                Color(0.2, 0.2, 0.22, 1)
            self._circle = Ellipse(pos=self.pos, size=self.size)


class RpmBar(Panel):
    """Horizontale Segment-Leiste statt Bogen (RpmArcGauge-Nachfolger) - passend
    zur Design-Vorlage. Zonen: <7000 weiss, 7000-7400 gelb, 7400-7500 orange,
    7500-8000 rot (vom Nutzer 2026-09-16 bestaetigtes Schema)."""

    N_SEGMENTS = 60

    def __init__(self, **kwargs):
        super().__init__(padding=(20, 10), spacing=4, **kwargs)
        top = BoxLayout(orientation="horizontal", size_hint_y=None, height=60)
        left = BoxLayout(orientation="vertical", size_hint_x=None, width=200)
        self.rpm_value = Label(text="0", font_size="44sp", bold=True, color=TEXT, halign="left")
        self.rpm_value.bind(size=lambda w, s: setattr(w, "text_size", s))
        left.add_widget(Label(text="RPM", font_size="13sp", color=TEXT_DIM, size_hint_y=None,
                               height=16, halign="left"))
        left.add_widget(self.rpm_value)
        top.add_widget(left)
        self.bar_widget = _RpmBarCanvas(self.N_SEGMENTS)
        top.add_widget(self.bar_widget)
        self.add_widget(top)

    def update(self, rpm):
        self.rpm_value.text = "-" if rpm is None else f"{rpm:.0f}"
        self.rpm_value.color = _rpm_zone_color(rpm)
        self.bar_widget.update(rpm)


class _RpmBarCanvas(Widget):
    def __init__(self, n_segments, **kwargs):
        super().__init__(**kwargs)
        self.n_segments = n_segments
        self.rpm = 0
        self.bind(pos=self._redraw, size=self._redraw)

    def update(self, rpm):
        self.rpm = rpm or 0
        self._redraw()

    def _redraw(self, *_args):
        self.canvas.clear()
        if self.width <= 0:
            return
        gap = 4
        seg_w = (self.width - gap * (self.n_segments - 1)) / self.n_segments
        lit_frac = max(0.0, min(1.0, self.rpm / DRIVE_RPM_MAX))
        lit_n = round(lit_frac * self.n_segments)
        with self.canvas:
            for i in range(self.n_segments):
                seg_rpm = (i / self.n_segments) * DRIVE_RPM_MAX
                if i < lit_n:
                    r, g, b, a = _rpm_zone_color(seg_rpm)
                else:
                    r, g, b, a = (0.16, 0.17, 0.2, 1)
                Color(r, g, b, a)
                x = self.x + i * (seg_w + gap)
                Rectangle(pos=(x, self.y), size=(seg_w, self.height))


class TpmsCarView(Panel):
    """Reifendruck/-temperatur an einer Auto-Draufsicht - PNG-Asset
    (mx5_top_view.png, vom Nutzer als Gemini-Grafik geliefert: weisse
    Linienzeichnung auf transparentem Grund, Nase schon oben) statt
    selbstgezeichneter Vektor-Silhouette. Nachfolger von TpmsMiniGrid."""

    NOMINAL_LOW, NOMINAL_HIGH = 2.0, 2.3

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title_label = Label(text="TPMS", font_size="14sp", color=TEXT_DIM, bold=True,
                                  size_hint_y=None, height=22)
        self.add_widget(self.title_label)
        body = FloatLayout()
        self.add_widget(body)
        self._car = Image(source=CAR_TOP_VIEW_PNG, allow_stretch=True, keep_ratio=True,
                           color=CAR_LINE, size_hint=(0.4, 0.92),
                           pos_hint={"center_x": 0.5, "center_y": 0.44})
        body.add_widget(self._car)
        self.corner_labels = {}
        # (Eckenschluessel, links?, oben?) - Positionierung ueber pos_hint x/right + top,
        # halign ueber text_size=size erzwungen (sonst zentriert Kivy trotz halign).
        specs = [("VL", True, True), ("VR", False, True), ("HL", True, False), ("HR", False, False)]
        for key, left, top in specs:
            pos_hint = {"top": 0.98 if top else 0.42}
            pos_hint["x" if left else "right"] = 0.02 if left else 0.98
            halign = "left" if left else "right"
            col = BoxLayout(orientation="vertical", size_hint=(None, None), size=(130, 66),
                             pos_hint=pos_hint)
            head = Label(text=key, font_size="12sp", color=TEXT_DIM, halign=halign,
                         size_hint_y=None, height=16)
            val = Label(text="– bar", font_size="20sp", bold=True, color=TEXT, halign=halign,
                        size_hint_y=None, height=28)
            temp = Label(text="–°C", font_size="13sp", color=TEXT_DIM, halign=halign,
                         size_hint_y=None, height=20)
            for lbl in (head, val, temp):
                lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
            col.add_widget(head)
            col.add_widget(val)
            col.add_widget(temp)
            body.add_widget(col)
            self.corner_labels[key] = (val, temp)

    def set_corner(self, key, pressure, temp):
        val_label, temp_label = self.corner_labels[key]
        if pressure is None:
            val_label.text = "– bar"
            val_label.color = TEXT_DIM
        else:
            val_label.text = f"{pressure:.2f} bar"
            val_label.color = GOOD if self.NOMINAL_LOW <= pressure <= self.NOMINAL_HIGH else WARN
        temp_label.text = "–°C" if temp is None else f"{temp:.0f}°C"


class StatusBar(BoxLayout):
    """Fusszeile: CAN-Framerate/DBC-Status/GPS-Platzhalter/Log-Status+Timer/Uhr."""

    def __init__(self, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=28,
                          padding=(20, 0), spacing=24, **kwargs)
        self.can_label = self._make_label()
        self.dbc_label = self._make_label()
        self.gps_label = self._make_label()
        self.log_label = self._make_label()
        self.clock_label = self._make_label()
        for w in (self.can_label, self.dbc_label, self.gps_label):
            self.add_widget(w)
        self.add_widget(Widget())  # Spacer
        self.add_widget(self.log_label)
        self.add_widget(self.clock_label)
        self.gps_label.text = "GPS  -- (kein Empfaenger)"
        self.gps_label.color = TEXT_DIM

    def _make_label(self):
        lbl = Label(text="", font_size="13sp", color=TEXT_DIM, size_hint_x=None, halign="left")
        lbl.bind(texture_size=lambda w, s: setattr(w, "width", s[0]))
        return lbl

    def update(self, snap):
        fps = snap.get("frames_per_sec", 0)
        self.can_label.text = f"CAN  {fps:.0f} Hz"
        self.can_label.color = GOOD if snap.get("can_up") else RED
        self.dbc_label.text = "DBC  OK" if snap.get("dbc_ok") else "DBC  FEHLT"
        self.dbc_label.color = GOOD if snap.get("dbc_ok") else RED
        since = snap.get("logging_since")
        if snap.get("logging") and since:
            elapsed = int(time.time() - since)
            self.log_label.text = f"LOG  REC  {elapsed // 60:02d}:{elapsed % 60:02d}"
            self.log_label.color = RED
        else:
            self.log_label.text = "LOG  --"
            self.log_label.color = TEXT_DIM
        self.clock_label.text = datetime.datetime.now().strftime("%H:%M:%S")
        self.clock_label.color = TEXT


# --- Drive-Screen (Renncockpit, Design-Vorlage 2026-09-16) --------------------------------

class DriveScreen(Screen):
    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.session_max_speed = 0.0

        root = BoxLayout(orientation="vertical", spacing=10, padding=14)
        with root.canvas.before:
            Color(*BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, p: setattr(self._bg_rect, "pos", p),
                  size=lambda w, s: setattr(self._bg_rect, "size", s))
        self.add_widget(root)

        self.shiftlights = ShiftLightRow(size_hint_y=0.13)
        root.add_widget(self.shiftlights)

        mid = BoxLayout(orientation="horizontal", spacing=10, size_hint_y=0.42)
        root.add_widget(mid)

        speed_card = Panel(size_hint_x=0.22, padding=18, spacing=4)
        speed_card.add_widget(Label(text="SPEED", font_size="15sp", color=TEXT_DIM, bold=True,
                                     size_hint_y=None, height=20, halign="left"))
        self.speed_value = Label(text="0", font_size="72sp", bold=True, color=TEXT, halign="left")
        self.speed_value.bind(size=lambda w, s: setattr(w, "text_size", s))
        speed_card.add_widget(self.speed_value)
        speed_card.add_widget(Label(text="km/h", font_size="14sp", color=TEXT_DIM,
                                     size_hint_y=None, height=18, halign="left"))
        vmax_row = BoxLayout(size_hint_y=None, height=24)
        vmax_row.add_widget(Label(text="VMAX", font_size="12sp", color=TEXT_DIM, halign="left"))
        self.vmax_value = Label(text="0 km/h", font_size="14sp", color=TEXT, bold=True, halign="right")
        vmax_row.add_widget(self.vmax_value)
        speed_card.add_widget(vmax_row)
        mid.add_widget(speed_card)

        gear_card = Panel(size_hint_x=0.18, padding=18, spacing=2)
        gear_card.add_widget(Label(text="GEAR", font_size="15sp", color=RED, bold=True,
                                    size_hint_y=None, height=20, halign="left"))
        self.gear_value = Label(text="-", font_size="80sp", bold=True, color=TEXT)
        gear_card.add_widget(self.gear_value)
        gear_card.add_widget(Label(text="MX-5\nTRACKDAY", font_size="13sp", color=RED, bold=True,
                                    halign="center", size_hint_y=None, height=36))
        mid.add_widget(gear_card)

        stat_col = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.2)
        self.coolant_card = MetricCard("WASSER", unit="°C")
        self.oil_card = MetricCard("ÖLTEMP.", unit="°C")
        self.battery_card = MetricCard("BATTERIE", unit="V", value_fmt="{:.1f}")
        stat_col.add_widget(self.coolant_card)
        stat_col.add_widget(self.oil_card)
        stat_col.add_widget(self.battery_card)
        mid.add_widget(stat_col)

        self.tpms = TpmsCarView(size_hint_x=0.4)
        mid.add_widget(self.tpms)

        self.rpm_bar = RpmBar(size_hint_y=0.22)
        root.add_widget(self.rpm_bar)

        bottom = BoxLayout(orientation="horizontal", spacing=10, size_hint_y=0.23)
        self.gas_card = MetricCard("GASPEDAL", unit="%", bar_color=GREEN)
        self.brake_card = MetricCard("BREMSE", unit="%", bar_color=BLUE)
        self.lambda_card = MetricCard("LAMBDA", value_fmt="{:.2f}")
        self.iat_card = MetricCard("ANSAUGLUFT", unit="°C")
        self.load_card = MetricCard("MOTORLAST", unit="%", bar_color=ORANGE)
        self.fuel_card = MetricCard("KRAFTSTOFF", unit="%", bar_color=YELLOW)
        for c in (self.gas_card, self.brake_card, self.lambda_card, self.iat_card,
                  self.load_card, self.fuel_card):
            bottom.add_widget(c)
        root.add_widget(bottom)

        self.status_bar = StatusBar()
        root.add_widget(self.status_bar)

    def get_rpm(self):
        return self.client.get(514, "EngineRPM")

    def refresh(self, *_args):
        c = self.client
        rpm = self.get_rpm()
        self.shiftlights.update(rpm)
        self.rpm_bar.update(rpm)

        speed = c.get(514, "VehicleSpeed")
        self.speed_value.text = "0" if speed is None else f"{speed:.0f}"
        snap = c.snapshot()
        self.session_max_speed = snap.get("session_max_speed", 0.0)
        self.vmax_value.text = f"{self.session_max_speed:.0f} km/h"

        self.gear_value.text = _format_gear(c.get(253, "MT_Gear_Actual"))

        self.coolant_card.set_value(c.get(COOLANT_TEMP_CAN_ID, "CoolantTemp"))
        self.oil_card.set_value(c.get(OIL_RESPONSE_KEY, "_OilTemp_derived", max_age=OIL_STALE_S))
        self.battery_card.set_value(
            c.get(OIL_RESPONSE_KEY, "_BatteryVoltage_derived", max_age=BATTERY_STALE_S))

        for key, spec in (("VL", "Tire1"), ("VR", "Tire2"), ("HL", "Tire3"), ("HR", "Tire4")):
            p = c.get(TPMS_CAN_ID, f"{spec}_Pressure", max_age=TPMS_STALE_S)
            t = c.get(TPMS_CAN_ID, f"{spec}_Temp_maybe", max_age=TPMS_STALE_S)
            self.tpms.set_corner(key, p, t)

        self.gas_card.set_value(c.get(514, "APP_Accelerator_Pedal_Position"))
        self.brake_card.set_value(c.get(120, "_BrakePedalPercent_derived"))
        self.lambda_card.set_value(
            c.get(OIL_RESPONSE_KEY, "_LambdaCommanded_derived", max_age=LAMBDA_STALE_S))
        self.iat_card.set_value(c.get(IAT_CAN_ID, "IAT_Sensor_No1"))
        self.load_card.set_value(c.get(LOAD_CAN_ID, "ActualEnginePercentTorque"))
        fuel_raw = c.get(FUEL_TANK_CAN_ID, "Fuel_Tank")
        fuel_pct = None if fuel_raw is None else max(0.0, min(100.0, 2.486 * fuel_raw - 0.02))
        self.fuel_card.set_value(fuel_pct)

        self.status_bar.update(snap)


# --- Status-/Testmodus-Screens (funktional gleichwertiger Port, kein Redesign) -------------

LIVE_SIGNALS = [
    ("Zündung", 80, "KeyState"), ("Anlasssperre", 80, "StarterInterLockSW"),
    ("Drehzahl", 514, "EngineRPM"), ("Gang (Actual)", 253, "MT_Gear_Actual"),
    ("Gang-Pos (roh)", 357, "MT_Gear_Position"), ("Gang-Wahl (N/InGear)", 357, "MT_Gear_Select"),
    ("Blinker", 154, "Turn"), ("Warnblinker", 145, "HAZ_SW"), ("Licht", 154, "Headlight"),
    ("Nebel hinten", 1086, "R_FOG_LAMP"), ("Wischer vorne", 145, "FrontWiper"),
    ("Kilometerstand", 1034, "C001_ODO"), ("Waschanlage", 145, "Washer"),
    ("Tür links", 1086, "DoorRight"), ("Tür rechts", 1086, "DoorLeft"),
    ("Kofferraum", 1086, "Trunk"), ("Parkbremse", 159, "Parking_Brake"),
    ("Rückwärtsgang", 159, "Reverse_Flag_maybe"), ("Speed ABS (neu)", 535, "VehicleSpeed_ABS_raw"),
    ("Drehzahl 0x130 (neu)", 304, "EngineRPM_related_2"),
    ("Kupplung 0x166 (neu)", 358, "Clutch_Pedal_Position_related_2"),
    ("Gang-Anzeige (unsicher)", 1143, "GearDisplay_related_maybe"),
    ("Lenkwinkel 0x240 (neu)", 576, "SteeringAngle_related_2_maybe"),
]

CHECKLIST = [
    "Lenkrad: Linksanschlag halten", "Lenkrad: Rechtsanschlag halten", "Lenkrad: Geradeaus (Mitte)",
    "Blinker links", "Blinker rechts", "Warnblinker",
    "Licht: Standlicht", "Licht: Abblendlicht", "Licht: Fernlicht", "Licht: Aus",
    "Nebelschlussleuchte", "Wischer: Intervall", "Wischer: An (normal)", "Wischer: Schnell",
    "Waschanlage", "Tuer links: oeffnen", "Tuer links: schliessen",
    "Tuer rechts: oeffnen", "Tuer rechts: schliessen",
    "Kofferraum: oeffnen", "Kofferraum: schliessen",
    "Handbremse: anziehen", "Handbremse: loesen",
    "Rueckwaertsgang: rein", "Rueckwaertsgang: raus (Leerlauf)",
    "Kupplung: 0% (oben, eingekuppelt)", "Kupplung: ca. 25%", "Kupplung: ca. 50%",
    "Kupplung: ca. 75%", "Kupplung: 100% (durchgetreten)",
]


def _format_live_value(value):
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def new_actions_log_path():
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return os.path.join(LOG_DIR, f"actions-{ts}.log")


class LiveGrid(BoxLayout):
    """2-spaltige Signal-Tabelle - Nachfolger von LiveValuesPanel."""

    def __init__(self, client, **kwargs):
        super().__init__(orientation="horizontal", spacing=16, **kwargs)
        self.client = client
        self.rows = []
        half = (len(LIVE_SIGNALS) + 1) // 2
        for col_signals in (LIVE_SIGNALS[:half], LIVE_SIGNALS[half:]):
            col = BoxLayout(orientation="vertical", spacing=2)
            for label, can_id, signal in col_signals:
                row = BoxLayout(orientation="horizontal", size_hint_y=None, height=20)
                name_lbl = Label(text=label + ":", font_size="13sp", color=TEXT_DIM, halign="left")
                val_lbl = Label(text="–", font_size="13sp", color=TEXT, halign="right", bold=True)
                for lbl in (name_lbl, val_lbl):
                    lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
                row.add_widget(name_lbl)
                row.add_widget(val_lbl)
                col.add_widget(row)
                self.rows.append((can_id, signal, val_lbl))
            self.add_widget(col)

    def refresh(self):
        for can_id, signal, val_lbl in self.rows:
            v = self.client.get(can_id, signal)
            val_lbl.text = "–" if v is None else _format_live_value(v)


PEDAL_GAUGES_STATUS = [
    {"label": "Gas", "can_id": 514, "signal": "APP_Accelerator_Pedal_Position", "unit": "%"},
    {"label": "Bremse", "can_id": 120, "signal": "_BrakePedalPercent_derived", "unit": "%"},
    {"label": "Kupplung", "can_id": 304, "signal": "Clutch_Pedal_Position_raw", "unit": "%",
     "transform": lambda v: v / 1.99},
    {"label": "Lenkwinkel", "can_id": 130, "signal": "Steering_Wheel_Absolute_Angle", "unit": "°"},
    {"label": "Speed", "can_id": 514, "signal": "VehicleSpeed", "unit": " km/h"},
]


class PedalRow(BoxLayout):
    def __init__(self, client, **kwargs):
        super().__init__(orientation="horizontal", spacing=10, size_hint_y=None, height=90, **kwargs)
        self.client = client
        self.cards = []
        for spec in PEDAL_GAUGES_STATUS:
            card = MetricCard(spec["label"], unit=spec["unit"])
            self.add_widget(card)
            self.cards.append((spec, card))

    def refresh(self):
        for spec, card in self.cards:
            v = self.client.get(spec["can_id"], spec["signal"])
            if v is not None and "transform" in spec:
                v = spec["transform"](v)
            card.set_value(v)


class SimController:
    """"Vcan-Simulation starten"-Knopf auf dem WARTE-AUF-CAN-BUS-Screen: baut
    vcan0 auf, spielt das zuletzt geschriebene candump-Log darauf ab (gleiche
    Methodik wie beim manuellen Testen diese Session: canplayer -l i -s 5,
    vorher die evtl. abgeschnittene letzte Zeile verwerfen - sonst bricht
    canplayer komplett ab, siehe can_log_parser.py-Waisenzeilen-Guard fuer den
    selben Fall) und setzt SIM_TRIGGER_PATH, das can_backend.py's
    run_decode_loop()/run_process_watch() dazu bringt, auf vcan0 umzuschalten
    und can_up/logging als aktiv zu melden."""

    LINE_RE = re.compile(r"^\(\d+\.\d+\) \S+ [0-9A-Fa-f]{3,8}#[0-9A-Fa-f]{0,16}\s*$")

    def __init__(self):
        self.proc = None
        self.log_name = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running:
            return True, self.log_name
        latest = self._find_latest_log()
        if latest is None:
            return False, "kein Log gefunden"
        try:
            for cmd in (["sudo", "modprobe", "vcan"],
                        ["sudo", "ip", "link", "add", "dev", "vcan0", "type", "vcan"],
                        ["sudo", "ip", "link", "set", "up", "vcan0"]):
                subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._prepare_replay_log(latest, SIM_REPLAY_LOG)
            self.proc = subprocess.Popen(
                ["canplayer", "-I", SIM_REPLAY_LOG, "-l", "i", "-s", "5", "vcan0=can0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(SIM_TRIGGER_PATH, "w") as f:
                f.write("vcan0")
            self.log_name = os.path.basename(latest)
            return True, self.log_name
        except Exception as exc:
            return False, str(exc)

    def stop(self):
        try:
            os.remove(SIM_TRIGGER_PATH)
        except FileNotFoundError:
            pass
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        subprocess.run(["sudo", "ip", "link", "delete", "vcan0"], check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            os.remove(SIM_REPLAY_LOG)
        except FileNotFoundError:
            pass

    @staticmethod
    def _find_latest_log():
        candidates = glob.glob(os.path.join(LOG_DIR, "candump-*.log.gz"))
        if not candidates:
            return None
        return max(candidates, key=os.path.getmtime)

    @classmethod
    def _prepare_replay_log(cls, gz_path, out_path):
        # Zeilenweise statt readlines(): das "letzte geschriebene Log" ist per
        # Definition das mit der hoechsten Chance, mitten im gzip-Puffer
        # abgebrochen zu sein (Zuendung aus waehrend candump/gzip noch
        # puffern) - beobachtet an candump-2026-09-16_090826.log.gz (8MB
        # glatt, "ended before end-of-stream marker"). readlines() wirft
        # dabei alles weg; zeilenweises Lesen behaelt, was bis zum Abbruch
        # erfolgreich entpackt wurde (hier 902479/~x Zeilen) - gleiche Idee
        # wie der candump-Waisenzeilen-Guard in can_log_parser.py, nur auf
        # Container- statt Zeilenebene.
        lines = []
        try:
            with gzip.open(gz_path, "rt", errors="replace") as f_in:
                for line in f_in:
                    lines.append(line)
        except (OSError, EOFError):
            pass
        if lines and not cls.LINE_RE.match(lines[-1]):
            lines = lines[:-1]
        with open(out_path, "w") as f_out:
            f_out.writelines(lines)


class StatusScreen(Screen):
    def __init__(self, client, on_start_test, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        root = FloatLayout()
        with root.canvas.before:
            self._bg_color = Color(*BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, p: setattr(self._bg_rect, "pos", p),
                  size=lambda w, s: setattr(self._bg_rect, "size", s))
        self.add_widget(root)

        col = BoxLayout(orientation="vertical", spacing=14, padding=24,
                         size_hint=(1, 1))
        root.add_widget(col)

        self.state_label = Label(text="", font_size="46sp", bold=True, color=TEXT,
                                  halign="center", size_hint_y=0.32)
        col.add_widget(self.state_label)

        self.test_button = Button(text="Testmodus starten", font_size="22sp",
                                   size_hint_y=None, height=56)
        self.test_button.bind(on_release=lambda *_: on_start_test())
        self._test_button_visible = False

        self.pedal_row = PedalRow(client)
        self.live_grid = LiveGrid(client, size_hint_y=0.34)
        self.tpms = TpmsCarView(size_hint_y=0.34)

        self._extras = BoxLayout(orientation="vertical", spacing=10, size_hint_y=0.6)
        col.add_widget(self._extras)

        # Nur sichtbar bei WARTE AUF CAN-BUS - Vcan-Simulation mit dem
        # zuletzt geschriebenen Log, siehe SimController.
        self.sim = SimController()
        self.sim_button = Button(text="Vcan-Simulation starten", font_size="20sp",
                                  size_hint_y=None, height=52)
        self.sim_button.bind(on_release=lambda *_: self._on_sim_button())
        self._sim_area = BoxLayout(orientation="vertical", size_hint_y=None, height=52)
        self._sim_button_visible = False
        col.add_widget(self._sim_area)

        self.clock_label = Label(text="", font_size="18sp", color=TEXT_DIM,
                                  size_hint_y=None, height=30)
        col.add_widget(self.clock_label)

    def set_logging_extras_visible(self, visible):
        self._extras.clear_widgets()
        if visible:
            self._extras.add_widget(self.test_button)
            self._extras.add_widget(self.pedal_row)
            self._extras.add_widget(self.live_grid)
            self._extras.add_widget(self.tpms)

    def _on_sim_button(self):
        self.sim_button.disabled = True
        if self.sim.running:
            self.sim_button.text = "Simulation stoppt..."
            threading.Thread(target=self._stop_sim_thread, daemon=True).start()
        else:
            self.sim_button.text = "Simulation startet..."
            threading.Thread(target=self._start_sim_thread, daemon=True).start()

    def _start_sim_thread(self):
        ok, info = self.sim.start()
        Clock.schedule_once(lambda dt: self._on_sim_result(ok, info))

    def _stop_sim_thread(self):
        self.sim.stop()
        Clock.schedule_once(lambda dt: self._on_sim_result(True, None))

    def _on_sim_result(self, ok, info):
        self.sim_button.disabled = False
        if self.sim.running:
            self.sim_button.text = f"Simulation stoppen ({info})"
        elif ok:
            self.sim_button.text = "Vcan-Simulation starten"
        else:
            self.sim_button.text = f"Fehler: {info}"

    def refresh(self, snap):
        can_up = snap.get("can_up")
        logging = snap.get("logging")
        session_logger = snap.get("session_logger_running")
        if not can_up:
            text, color = "WARTE AUF CAN-BUS", (0.17, 0.23, 0.33, 1)
        elif logging:
            text, color = "LOGGING LÄUFT", (0.11, 0.48, 0.24, 1)
        elif session_logger:
            text, color = "BUS AKTIV\nWARTE AUF ZÜNDUNG", (0.66, 0.42, 0, 1)
        else:
            text, color = "FEHLER\nSERVICE NICHT AKTIV", (0.54, 0.11, 0.11, 1)
        self.state_label.text = text
        self._bg_color.rgba = color

        want_extras = logging
        if want_extras != self._test_button_visible:
            self._test_button_visible = want_extras
            self.set_logging_extras_visible(want_extras)
        if want_extras:
            self.pedal_row.refresh()
            self.live_grid.refresh()
            for key, spec in (("VL", "Tire1"), ("VR", "Tire2"), ("HL", "Tire3"), ("HR", "Tire4")):
                p = self.client.get(TPMS_CAN_ID, f"{spec}_Pressure", max_age=TPMS_STALE_S)
                t = self.client.get(TPMS_CAN_ID, f"{spec}_Temp_maybe", max_age=TPMS_STALE_S)
                self.tpms.set_corner(key, p, t)

        # Auch sichtbar, waehrend eine laufende Simulation den Bus als aktiv
        # meldet (can_up dann per Trigger erzwungen True) - sonst gibt es
        # keinen Weg mehr, sie ueber das GUI zu stoppen, sobald sie greift.
        want_sim = not can_up or self.sim.running
        if want_sim != self._sim_button_visible:
            self._sim_button_visible = want_sim
            self._sim_area.clear_widgets()
            if want_sim:
                self._sim_area.add_widget(self.sim_button)
        self.clock_label.text = datetime.datetime.now().strftime("%H:%M:%S")


class TestModeScreen(Screen):
    """Fuehrt per Touch durch CHECKLIST, protokolliert Start/Ende jeder Aktion
    als Unix-Timestamp - Nachfolger von ChecklistGui."""

    def __init__(self, client, on_finish, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.on_finish = on_finish
        self.index = 0
        self.step_start = None
        self.log_path = None
        self.history = []

        root = BoxLayout(orientation="vertical", spacing=10, padding=20)
        with root.canvas.before:
            Color(0.1, 0.1, 0.18, 1)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, p: setattr(self._bg_rect, "pos", p),
                  size=lambda w, s: setattr(self._bg_rect, "size", s))
        self.add_widget(root)

        self.progress_label = Label(text="", font_size="18sp", color=TEXT_DIM,
                                     size_hint_y=None, height=30)
        self.step_label = Label(text="", font_size="34sp", bold=True, color=TEXT, halign="center")
        self.step_label.bind(size=lambda w, s: setattr(w, "text_size", s))
        root.add_widget(self.progress_label)
        root.add_widget(self.step_label)

        self.action_button = Button(text="Start", font_size="26sp", size_hint_y=None, height=90,
                                     background_color=GREEN)
        self.action_button.bind(on_release=lambda *_: self.on_action_button())
        root.add_widget(self.action_button)

        nav = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=60)
        back_btn = Button(text="< Zurück", font_size="16sp")
        back_btn.bind(on_release=lambda *_: self.go_back())
        skip_btn = Button(text="Überspringen", font_size="16sp")
        skip_btn.bind(on_release=lambda *_: self.skip_step())
        finish_btn = Button(text="Test beenden", font_size="16sp", background_color=RED)
        finish_btn.bind(on_release=lambda *_: self.finish())
        for b in (back_btn, skip_btn, finish_btn):
            nav.add_widget(b)
        root.add_widget(nav)

        self.history_label = Label(text="", font_size="14sp", color=GREEN, halign="left",
                                    size_hint_y=None, height=60)
        self.history_label.bind(size=lambda w, s: setattr(w, "text_size", s))
        root.add_widget(self.history_label)

        self.pedal_row = PedalRow(client)
        root.add_widget(self.pedal_row)
        self.live_grid = LiveGrid(client, size_hint_y=0.4)
        root.add_widget(self.live_grid)

    def start(self):
        self.log_path = new_actions_log_path()
        self.index = 0
        self.history = []
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
            w.writerow([step_name, phase, f"{t_start:.3f}", f"{t_end:.3f}",
                        datetime.datetime.fromtimestamp(t_start).strftime("%H:%M:%S"),
                        datetime.datetime.fromtimestamp(t_end).strftime("%H:%M:%S")])

    def show_step(self):
        if self.index >= len(CHECKLIST):
            self.step_label.text = f"Fertig!\n{len(CHECKLIST)} Schritte protokolliert."
            self.action_button.text = "Beenden"
            self.action_button.background_color = GREEN
            self.progress_label.text = ""
            return
        self.progress_label.text = f"Schritt {self.index + 1}/{len(CHECKLIST)}"
        self.step_label.text = CHECKLIST[self.index]
        self.action_button.text = "Start"
        self.action_button.background_color = GREEN
        self.step_start = None

    def on_action_button(self):
        if self.index >= len(CHECKLIST):
            self.finish()
            return
        if self.step_start is None:
            self.step_start = time.time()
            self.action_button.text = "Fertig"
            self.action_button.background_color = ORANGE
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
        self.history_label.text = "\n".join(self.history[-3:])

    def finish(self):
        self.on_finish()

    def refresh(self):
        self.pedal_row.refresh()
        self.live_grid.refresh()


# --- App -------------------------------------------------------------------------------

class MX5DashApp(App):
    def build(self):
        Window.clearcolor = BG
        self.client = SnapshotClient()
        self.sm = ScreenManager(transition=NoTransition())

        self.drive_screen = DriveScreen(self.client, name="drive")
        self.status_screen = StatusScreen(self.client, on_start_test=self.start_test_mode, name="status")
        self.testmode_screen = TestModeScreen(self.client, on_finish=self.end_test_mode, name="testmode")
        for s in (self.status_screen, self.drive_screen, self.testmode_screen):
            self.sm.add_widget(s)
        self.sm.current = "status"

        self._in_testmode = False
        Clock.schedule_interval(self.update_state, POLL_INTERVAL_S)
        Clock.schedule_interval(self.drive_screen.refresh, 1.0 / DRIVE_REFRESH_HZ)
        return self.sm

    def start_test_mode(self):
        self._in_testmode = True
        self.sm.current = "testmode"
        self.testmode_screen.start()

    def end_test_mode(self):
        self._in_testmode = False
        self.sm.current = "status"

    def update_state(self, *_args):
        snap = self.client.snapshot()
        logging_now = snap.get("logging", False)
        rpm = self.drive_screen.get_rpm() if logging_now else None
        engine_running = logging_now and rpm is not None and rpm > DRIVE_RPM_THRESHOLD

        if self._in_testmode:
            target = "testmode"
        elif engine_running:
            target = "drive"
        else:
            target = "status"

        if self.sm.current != target:
            self.sm.current = target
        if target == "status":
            self.status_screen.refresh(snap)
        elif target == "testmode":
            self.testmode_screen.refresh()


if __name__ == "__main__":
    if os.environ.get("MX5_FULLSCREEN", "1") == "1":
        Window.fullscreen = "auto"
    MX5DashApp().run()
