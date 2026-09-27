"""Selbsttest fuer can_backend.kill_processes() - der Helper, der den per
subprocess.Popen in dash_gui.py gestarteten canplayer-Prozess beendet, sobald
run_process_watch() erkennt, dass der echte can0-Adapter jetzt da ist (siehe
Docstring dort). kill_processes() ist reines /proc-Scanning + os.kill(), lässt
sich ohne Kivy/CAN-Hardware testen - der eigentliche Reconnect-Ablauf
(run_decode_loop()) braucht echtes SocketCAN und wurde stattdessen live auf
dem Pi gegen eine echte Vcan-Simulation verifiziert.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_backend
from can_backend import kill_processes


def test_kill_processes_terminates_matching_process():
    proc = subprocess.Popen(["sleep", "50"])
    try:
        for _ in range(20):
            with open(f"/proc/{proc.pid}/cmdline", "rb") as f:
                if b"sleep" in f.read():
                    break
            time.sleep(0.05)

        kill_processes("sleep 50")

        assert proc.wait(timeout=3) != 0  # per SIGTERM beendet, kein regulaerer Exit
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_processes_ignores_non_matching():
    proc = subprocess.Popen(["sleep", "50"])
    try:
        kill_processes("this-pattern-matches-nothing")
        time.sleep(0.2)
        assert proc.poll() is None  # laeuft unberuehrt weiter
    finally:
        proc.kill()
        proc.wait()


def _backend_without_dbc(candidates=()):
    saved = can_backend.DBC_CANDIDATES
    can_backend.DBC_CANDIDATES = list(candidates)
    try:
        return can_backend.CanBackend(channel="nonexistent0")
    finally:
        can_backend.DBC_CANDIDATES = saved


def test_new_logging_session_resets_vmax_and_starts_monotonic_timer():
    """Der Backend-Prozess laeuft seit dem Desktop-Login, zu Hause ueber mehrere
    Fahrten - VMAX muss mit jeder Logging-Session neu beginnen."""
    backend = _backend_without_dbc()
    backend.session_max_speed = 187.0
    backend._update_logging_edge(False)
    assert backend.snapshot()["logging_elapsed"] is None
    assert backend.session_max_speed == 187.0   # ohne neue Session bleibt der Wert

    backend._update_logging_edge(True)
    assert backend.session_max_speed == 0.0
    backend.session_max_speed = 55.0
    backend._update_logging_edge(True)          # laufende Session: kein erneuter Reset
    assert backend.session_max_speed == 55.0

    snap = backend.snapshot()
    assert snap["clock"] == "monotonic"
    assert 0 <= snap["logging_elapsed"] < 5
    assert snap["logging_since"] is not None     # Wanduhr fuer aeltere dash_gui.py

    backend._update_logging_edge(False)
    snap = backend.snapshot()
    assert snap["logging_elapsed"] is None and snap["logging_since"] is None


def test_snapshot_values_are_stamped_on_the_monotonic_clock():
    backend = _backend_without_dbc()
    backend._set(514, "EngineRPM", 3000, time.monotonic())
    snap = backend.snapshot()
    value, ts = snap["values"]["514:EngineRPM"]
    assert value == 3000
    assert abs(snap["t_mono"] - ts) < 1.0


def test_health_line_reports_value_ages_and_resets_obd_request_counts():
    backend = _backend_without_dbc()
    now = time.monotonic()
    backend._set(514, "VehicleSpeed", 42.0, now - 3.0)
    backend._obd_requests[0x7DF] = 7
    line = backend.health_line(now)
    assert "'speed': 3.0" in line and "'lambda': None" in line, line
    assert "7DF=7 7E0=0" in line, line
    assert "7DF=0" in backend.health_line(now)   # Zaehler je Zeile zurueckgesetzt


if __name__ == "__main__":
    test_kill_processes_terminates_matching_process()
    test_kill_processes_ignores_non_matching()
    test_new_logging_session_resets_vmax_and_starts_monotonic_timer()
    test_snapshot_values_are_stamped_on_the_monotonic_clock()
    test_health_line_reports_value_ages_and_resets_obd_request_counts()
    print("ok")
