"""Selbsttest fuer die Renncockpit-Ansicht (DriveDashPanel) in status_gui.py:
Winkel-/Nadelmathe, Live-Wert-Formatierung inkl. fehlender Kanaele, und der
automatische Umschalter Status-Screen<->Renncockpit ueber die Motordrehzahl
(siehe StatusGui.update_state, DRIVE_RPM_THRESHOLD).

Laeuft ohne echten CAN-Bus/Hardware - status_gui.py importiert cantools/can
nur lazy innerhalb von LiveCanValues, ein FakeLive-Stub reicht hier aus.
Braucht ein echtes Tk-Display (DISPLAY gesetzt); ohne Display Test ueberspringen.
"""
import os
import sys
import tempfile
import time
import tkinter as tk

# Vor dem Import setzen: status_gui.LOG_DIR faellt sonst auf /home/pi/canlogs
# zurueck (nicht beschreibbar ausserhalb des Pi) - der Testmodus-Screen
# schreibt beim Start eine Log-Zeile dorthin.
os.environ.setdefault("MX5_LOG_DIR", tempfile.mkdtemp(prefix="mx5_test_"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status_gui


class FakeLive:
    """Minimaler Ersatz fuer LiveCanValues: gleiche .get()-Signatur, Werte
    werden von Hand mit Alter gesetzt statt vom can0-Socket gelesen."""

    def __init__(self):
        self.error = None
        self._values = {}

    def set(self, can_id, signal, value, age=0.0):
        self._values[(can_id, signal)] = (value, time.time() - age)

    def clear(self, can_id, signal):
        self._values.pop((can_id, signal), None)

    def get(self, can_id, signal):
        return self._values.get((can_id, signal))


def test_format_gear():
    assert status_gui._format_gear(None) == "–"
    assert status_gui._format_gear(0) == "N"
    assert status_gui._format_gear(7) == "R"
    assert status_gui._format_gear(3) == "3"
    assert status_gui._format_gear(3.0) == "3"


def test_polar_orientation():
    # 0 Grad = oben (negatives y), im Uhrzeigersinn positiv (90 Grad = rechts) -
    # siehe _polar-Docstring, dieselbe Konvention wie im Web-Mockup.
    x, y = status_gui._polar(0, 0, 10, 0)
    assert abs(x) < 1e-9 and abs(y - (-10)) < 1e-9, (x, y)
    x, y = status_gui._polar(0, 0, 10, 90)
    assert abs(x - 10) < 1e-9 and abs(y) < 1e-9, (x, y)


def _fill_live(live):
    live.set(514, "EngineRPM", 5111.0)
    live.set(514, "VehicleSpeed", 66.0)
    live.set(514, "APP_Accelerator_Pedal_Position", 100.0)
    live.set(253, "MT_Gear_Actual", 2)
    live.set(253, "FuelCut", 0)
    live.set(120, "_BrakePedalPercent_derived", 0.0)
    live.set(304, "Clutch_Pedal_Position_raw", 0.0)
    live.set(130, "Steering_Wheel_Absolute_Angle", -11.0)
    live.set(117, "Lateral_Acc_Raw", -0.32)
    live.set(118, "Longitudinal_Acc_Raw", 0.37)
    live.set(529, "ABS_Active", 0)
    live.set(1056, "CoolantTemp", 89.0)
    live.set(158, "Fuel_Tank", 24.3)
    live.set(status_gui.OIL_RESPONSE_ID, "_OilTemp_derived", 98.0)


def test_rpm_arc_and_gmeter_widgets():
    root = tk.Tk()
    root.withdraw()
    try:
        arc = status_gui.RpmArcGauge(root, "#0d0d13")
        for rpm in (0, 1, 5111, status_gui.DRIVE_RPM_MAX, status_gui.DRIVE_RPM_MAX + 500, None):
            arc.update(rpm)  # darf bei keinem Wert (inkl. Ueberlauf/None) crashen

        g = status_gui.GMeterGauge(root, "#0d0d13")
        g.update(None, None)
        assert g.canvas.itemcget(g.label, "text") == "– g"
        g.update(-0.32, 0.37)
        expected = f"{(0.32 ** 2 + 0.37 ** 2) ** 0.5:.2f} g"
        assert g.canvas.itemcget(g.label, "text") == expected, g.canvas.itemcget(g.label, "text")
    finally:
        root.destroy()


def test_drive_dash_panel_live_values():
    root = tk.Tk()
    root.withdraw()
    live = FakeLive()
    _fill_live(live)
    panel = status_gui.DriveDashPanel(root, live)
    try:
        panel._refresh()
        root.update_idletasks()

        assert panel.speed_label.cget("text") == "66"
        assert panel.gear_label.cget("text") == "2"
        assert panel.oil_cell.cget("text") == "98°C"
        assert panel.coolant_cell.cget("text") == "89°C"
        fuel_text = panel.fuel_cell.cget("text")
        assert fuel_text.endswith("%") and fuel_text != "–", fuel_text
        assert panel.abs_lamp.cget("fg") == "#333844"  # inaktiv

        assert panel.steer_gauge.spec["vmax"] == status_gui.DRIVE_STEER_VMAX
        assert panel.steer_gauge.value_label.cget("text") == "-11°"
        assert len(panel.pedal_gauges) == 3  # nur noch Gas/Bremse/Kupplung, Lenkwinkel separat

        # Shift-Lights: (5111-4000)/(8000-4000) = 0.278 * 10 LEDs -> 3 an
        lit = sum(1 for led in panel.shiftlights.leds
                  if led.cget("bg") != status_gui.ShiftLightBar.OFF)
        assert lit == 3, lit

        # ABS-Eingriff -> Lampe schaltet um
        live.set(529, "ABS_Active", 1)
        panel._refresh()
        assert panel.abs_lamp.cget("fg") == "#ff2f3a"

        # Fehlender Kanal (z.B. Signal seit >LIVE_STALE_S nicht aktualisiert)
        # darf nicht crashen, sondern faellt auf "-" zurueck.
        live.clear(514, "VehicleSpeed")
        panel._refresh()
        assert panel.speed_label.cget("text") == "–"
    finally:
        panel.destroy()
        root.destroy()


def test_engine_running_switches_screen():
    # Kein root.withdraw() hier - winfo_ismapped() braucht ein tatsaechlich
    # angezeigtes Fenster, ein zurueckgezogener Root macht jeden Nachfahren
    # "unmapped", egal was pack()/pack_forget() dazu sagen.
    root = tk.Tk()
    live = FakeLive()
    orig_get_state = status_gui.get_state
    status_gui.get_state = lambda: ("LOGGING LÄUFT", "#1b7a3d", "#ffffff")
    try:
        gui = status_gui.StatusGui(root, live)

        gui.update_state()
        root.update_idletasks()
        assert gui.status_frame.winfo_ismapped()
        assert not gui.drive_dash.frame.winfo_ismapped()

        live.set(514, "EngineRPM", 5000.0)
        gui.update_state()
        root.update_idletasks()
        assert not gui.status_frame.winfo_ismapped()
        assert gui.drive_dash.frame.winfo_ismapped()

        live.set(514, "EngineRPM", 0.0)
        gui.update_state()
        root.update_idletasks()
        assert gui.status_frame.winfo_ismapped()
        assert not gui.drive_dash.frame.winfo_ismapped()

        # Testmodus hat immer Vorrang, auch bei laufendem Motor.
        live.set(514, "EngineRPM", 5000.0)
        gui.start_test_mode()
        gui.update_state()
        root.update_idletasks()
        assert not gui.drive_dash.frame.winfo_ismapped()
    finally:
        status_gui.get_state = orig_get_state
        root.destroy()


if __name__ == "__main__":
    if not os.environ.get("DISPLAY"):
        print("kein DISPLAY gesetzt - Tk-Tests uebersprungen")
        sys.exit(0)
    test_format_gear()
    test_polar_orientation()
    test_rpm_arc_and_gmeter_widgets()
    test_drive_dash_panel_live_values()
    test_engine_running_switches_screen()
    print("OK")
