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
import os
import sys

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


if __name__ == "__main__":
    if dash_gui is None:
        print("kein Kivy installiert - dash_gui-Tests uebersprungen")
        sys.exit(0)
    test_logging_without_rpm_stays_on_drive()
    test_not_logging_falls_back_to_status()
    test_testmode_has_priority_over_logging()
    print("ok")
