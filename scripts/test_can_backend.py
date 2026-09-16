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


if __name__ == "__main__":
    test_kill_processes_terminates_matching_process()
    test_kill_processes_ignores_non_matching()
    print("ok")
