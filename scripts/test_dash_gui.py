"""Selbsttest fuer den Screen-Umschalter in dash_gui.py (MX5DashApp.update_state):
seit 2026-09-16 abends haengt "drive" vs "status" nur noch an snap["logging"]
(== Zuendung an, siehe session_logger.py KeyState-Check), nicht mehr an der
Motordrehzahl - sonst wirft Mazdas Start/Stop-System (Motor aus, Zuendung an)
den Fahrer waehrend der Fahrt zurueck auf den Status-Screen.

Braucht kein echtes Kivy-Fenster: ruft update_state() ungebunden auf einem
minimalen Fake-"self" auf, der nur die dafuer gelesenen/geschriebenen
Attribute bereitstellt. Kivy selbst muss trotzdem importierbar sein (laeuft
regulaer nur auf dem Pi) - ohne Kivy wird der Test uebersprungen.
"""
import gzip
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import dash_gui
except ImportError:
    dash_gui = None


class FakeScreenManager:
    def __init__(self):
        self.current = "status"


class FakeScreen:
    def __init__(self):
        self.refreshed = False

    def refresh(self, *_args):
        self.refreshed = True


class FakeApp:
    """Traegt nur, was MX5DashApp.update_state liest/schreibt."""

    def __init__(self, snap):
        self.client = self
        self._snap = snap
        self.sm = FakeScreenManager()
        self.status_screen = FakeScreen()
        self.testmode_screen = FakeScreen()
        self._in_testmode = False

    def snapshot(self):
        return self._snap


def test_logging_without_rpm_stays_on_drive():
    app = FakeApp({"logging": True})
    dash_gui.MX5DashApp.update_state(app)
    assert app.sm.current == "drive"


def test_not_logging_falls_back_to_status():
    app = FakeApp({"logging": False})
    dash_gui.MX5DashApp.update_state(app)
    assert app.sm.current == "status"
    assert app.status_screen.refreshed


def test_testmode_has_priority_over_logging():
    app = FakeApp({"logging": True})
    app._in_testmode = True
    dash_gui.MX5DashApp.update_state(app)
    assert app.sm.current == "testmode"
    assert app.testmode_screen.refreshed


def test_prepare_replay_log_streams_without_buffering_all_lines():
    """Regression fuer den OOM-Absturz beim Klick auf "Vcan-Simulation starten"
    (2026-09-17): _prepare_replay_log() sammelte frueher jede Zeile in einer
    Liste, bevor sie geschrieben wurde. Bei den ~50MB-gzip-Logs, die hier
    taeglich anfallen, entpackt das auf mehrere hundert MB Text und hat den
    Pi (1.8GB RAM) per OOM-Killer abgeschossen. Der Test prueft nur noch das
    fachliche Verhalten (abgeschnittene letzte Zeile wird verworfen), nicht
    den Speicherverbrauch."""
    with tempfile.TemporaryDirectory() as tmp:
        gz_path = os.path.join(tmp, "in.log.gz")
        out_path = os.path.join(tmp, "out.log")
        good = "(1700000000.123456) can0 123#DEADBEEF\n"
        truncated = "(1700000000.223456) can0 12"
        with gzip.open(gz_path, "wt") as f:
            f.write(good * 3 + truncated)

        dash_gui.SimController._prepare_replay_log(gz_path, out_path)

        with open(out_path) as f:
            result = f.read()
        assert result == good * 3


class FakeSnapshotClient:
    """Minimaler Ersatz fuer SnapshotClient: gleiche .get()/.snapshot()-
    Signatur, Werte von Hand statt vom UDP-Socket."""

    def __init__(self):
        self._values = {}

    def set(self, can_id, signal, value):
        self._values[(can_id, signal)] = value

    def get(self, can_id, signal, max_age=None):
        return self._values.get((can_id, signal))

    def snapshot(self):
        return {}


def test_gear_display_turns_baby_blue_when_clutch_not_closed():
    """Kupplung nicht geschlossen (Pedal getreten) -> GEAR-Anzeige babyblau,
    sonst normale Textfarbe - siehe CLUTCH_ACTIVE_RAW (gleiche Schwelle wie
    can_traction_circle.py/shift_time_analysis.py)."""
    client = FakeSnapshotClient()
    client.set(253, "MT_Gear_Actual", 3)
    screen = dash_gui.DriveScreen(client)

    client.set(304, "Clutch_Pedal_Position_raw", 0.0)
    screen.refresh()
    assert screen.gear_value.color == list(dash_gui.TEXT)

    client.set(304, "Clutch_Pedal_Position_raw", 20.0)
    screen.refresh()
    assert screen.gear_value.color == list(dash_gui.BABY_BLUE)

    client.set(304, "Clutch_Pedal_Position_raw", None)
    screen.refresh()
    assert screen.gear_value.color == list(dash_gui.TEXT)


def test_fuel_gauge_smooths_out_tank_slosh():
    """Tankschwappen zeigt sich als schnelles Rauschen um den echten
    Fuellstand - der EMA-Filter (FUEL_SMOOTH_ALPHA, tau=8s) soll die
    Schwankung deutlich daempfen, aber einem echten Stufenwechsel (Tanken)
    binnen weniger Sekunden folgen."""
    import math

    client = FakeSnapshotClient()
    screen = dash_gui.DriveScreen(client)

    # Fuel_Tank ist der Rohwert vor der 2.486x-0.02-Umrechnung - Mittelwert
    # hier so gewaehlt, dass fuel_pct um 50% herum schwankt.
    base_raw = (50.0 + 0.02) / 2.486
    readings = []
    for i in range(240):  # 8s bei 30Hz
        noisy_raw = base_raw + 4.0 * math.sin(i * 1.3)  # schnelles Schwappen
        client.set(158, "Fuel_Tank", noisy_raw)
        screen.refresh()
        readings.append(screen._fuel_smooth)

    raw_values = [max(0.0, min(100.0, 2.486 * (base_raw + 4.0 * math.sin(i * 1.3)) - 0.02))
                  for i in range(240)]
    tail_raw = raw_values[-60:]
    tail_smoothed = readings[-60:]
    raw_spread = max(tail_raw) - min(tail_raw)
    smoothed_spread = max(tail_smoothed) - min(tail_smoothed)
    assert smoothed_spread < raw_spread * 0.5, (smoothed_spread, raw_spread)

    # Ein echter Sprung (z.B. Tanken) muss trotzdem durchkommen, nicht auf
    # ewig weggeglaettet werden.
    client.set(158, "Fuel_Tank", (90.0 + 0.02) / 2.486)
    for _ in range(720):  # weitere 24s (~3 Tau, ~95% Annaeherung)
        screen.refresh()
    assert screen._fuel_smooth > 80.0, screen._fuel_smooth


def test_shiftlight_colors_cover_the_narrow_orange_zone():
    """Regression: bei 16 LEDs ueber 4000-8000rpm (250rpm/LED) faellt die nur
    100rpm breite Orange-Zone (7400-7500) durchs Punktraster, wenn man jede
    LED nur an ihrem exakten rpm-Wert einfaerbt - keine LED trifft je
    hinein. _rpm_zone_color_range() faerbt stattdessen ueber den von der LED
    abgedeckten Bereich, damit Orange sichtbar bleibt."""
    span = dash_gui.DRIVE_RPM_MAX - dash_gui.DRIVE_SHIFTLIGHT_MIN
    step = span / dash_gui.DRIVE_SHIFTLIGHT_N
    colors = []
    for i in range(dash_gui.DRIVE_SHIFTLIGHT_N):
        lo = dash_gui.DRIVE_SHIFTLIGHT_MIN + i * step
        colors.append(dash_gui._rpm_zone_color_range(lo, lo + step))
    assert dash_gui.ORANGE in colors
    assert colors.count(dash_gui.RED) >= 1
    assert colors.count(dash_gui.YELLOW) >= 1
    assert colors.count(dash_gui.GREEN) >= 1
    # Reihenfolge bleibt aufsteigend: gruen -> gelb -> orange -> rot, keine LED
    # springt zurueck auf eine "kaeltere" Farbe.
    severity = {dash_gui.GREEN: 0, dash_gui.YELLOW: 1, dash_gui.ORANGE: 2, dash_gui.RED: 3}
    levels = [severity[c] for c in colors]
    assert levels == sorted(levels)


if __name__ == "__main__":
    if dash_gui is None:
        print("kein Kivy installiert - dash_gui-Tests uebersprungen")
        sys.exit(0)
    test_logging_without_rpm_stays_on_drive()
    test_not_logging_falls_back_to_status()
    test_testmode_has_priority_over_logging()
    test_prepare_replay_log_streams_without_buffering_all_lines()
    test_gear_display_turns_baby_blue_when_clutch_not_closed()
    test_fuel_gauge_smooths_out_tank_slosh()
    test_shiftlight_colors_cover_the_narrow_orange_zone()
    print("ok")
