"""Test: session_logger-Statemachine gegen echte KeyState-Frames aus dem Testlog."""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, "scripts")
from can_log_parser import load_db, parse_candump
from session_logger import SessionLogger


def test_start_stop_matches_known_session():
    db = load_db()
    keystate_msg = db.get_message_by_frame_id(0x50)

    popen_calls = []

    def fake_popen(cmd, *a, **kw):
        popen_calls.append(cmd[0])
        return MagicMock(pid=1234)

    run_calls = []
    session = SessionLogger(keystate_msg, popen=fake_popen, run=lambda *a, **kw: run_calls.append("gzip"))

    df = parse_candump("data/can/candump-2026-09-11_180456.log")
    df = df[df.can_id == 0x50].copy()
    df["t"] = df.t - df.t.min()

    events = []
    for _, row in df.iterrows():
        was_active = session.logging_active
        session.on_frame(row.can_id, row.data)
        if session.logging_active and not was_active:
            events.append(("start", round(row.t, 2)))
        elif was_active and not session.logging_active:
            events.append(("stop", round(row.t, 2)))

    print("Events:", events)
    assert events == [
        ("start", 0.0),
        ("stop", 16.34),
        ("start", 18.7),
        ("stop", 62.53),
    ], f"unerwartete Events: {events}"
    n_candump = sum(1 for c in popen_calls if c == "candump")
    n_tpms = len(popen_calls) - n_candump
    assert n_candump == 2, f"erwartet 2 candump-Starts, bekommen {n_candump}"
    assert n_tpms == 2, f"erwartet 2 TPMS-Poller-Starts (einer pro Fahrt), bekommen {n_tpms}"
    print("OK")


def test_probe_runs_once_and_only_with_flag():
    """Die Einmal-Erhebung darf ohne Flag-Datei gar nicht starten und mit Flag genau
    einmal - sie laeuft unbeaufsichtigt waehrend einer Fahrt, ein Dauerfeuer bei jeder
    Fahrt waere unnoetige Buslast."""
    import tempfile
    import session_logger as sl

    with tempfile.TemporaryDirectory() as tmp:
        started = []

        def fake_popen(cmd, **kwargs):
            started.append(os.path.basename(cmd[1]) if len(cmd) > 1 else cmd[0])
            class P:
                pid = 1
                def terminate(self): pass
                def wait(self, timeout=None): pass
            return P()

        orig_flag = sl.PROBE_FLAG_PATH
        try:
            sl.PROBE_FLAG_PATH = os.path.join(tmp, "RUN_PROBE")
            session = sl.SessionLogger(keystate_msg=None, log_dir=tmp,
                                       popen=fake_popen, run=lambda *a, **k: None)

            # ohne Flag: nichts
            session.maybe_start_probe()
            assert started == [], f"ohne Flag gestartet: {started}"

            # mit Flag: genau einmal, Flagge danach umbenannt
            open(sl.PROBE_FLAG_PATH, "w").close()
            session.maybe_start_probe()
            assert started == ["uds_did_sweep.py"], started
            assert not os.path.exists(sl.PROBE_FLAG_PATH), "Flagge nicht entfernt"
            assert any(f.startswith("RUN_PROBE.gestartet-") for f in os.listdir(tmp))

            # zweiter Aufruf (naechste Fahrt): nicht erneut
            session.maybe_start_probe()
            assert started == ["uds_did_sweep.py"], f"erneut gestartet: {started}"
        finally:
            sl.PROBE_FLAG_PATH = orig_flag
    print("Probe-Test OK")


def test_probe_failure_does_not_break_logging():
    """Ein Fehler in der Erhebung darf das Loggen nicht verhindern."""
    import tempfile
    import session_logger as sl

    with tempfile.TemporaryDirectory() as tmp:
        def exploding_popen(cmd, **kwargs):
            raise OSError("kaputt")
        orig_flag = sl.PROBE_FLAG_PATH
        try:
            sl.PROBE_FLAG_PATH = os.path.join(tmp, "RUN_PROBE")
            open(sl.PROBE_FLAG_PATH, "w").close()
            session = sl.SessionLogger(keystate_msg=None, log_dir=tmp,
                                       popen=exploding_popen, run=lambda *a, **k: None)
            session.maybe_start_probe()   # darf NICHT werfen
        finally:
            sl.PROBE_FLAG_PATH = orig_flag
    print("Probe-Fehlertoleranz OK")


def test_clock_restore_cases():
    """Die vier Faelle, die im Auto auftreten koennen. Die Uhr darf NUR gestellt werden,
    wenn sie nachweislich falsch geht - ein unnoetiger Zeitsprung waere schlimmer als eine
    leicht nachlaufende Uhr."""
    import tempfile
    from datetime import datetime, timedelta
    import session_logger as sl

    calls = []

    def run_stub(cmd, **kwargs):
        calls.append(cmd)
        class R: stdout = "no"
        if cmd[0] == "timedatectl":
            R.stdout = run_stub.ntp
        return R

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "last_known_time")
        now = datetime(2026, 9, 16, 8, 0, 0)

        # 1) NTP synchron -> nichts anfassen
        run_stub.ntp = "yes"; calls.clear()
        state, _ = sl.restore_clock(path, run_stub, now)
        assert state == "ntp", state
        assert not any(c[0] == "date" for c in calls), calls

        # 2) kein NTP, keine gespeicherte Zeit -> nichts stellen, aber als unsicher melden
        run_stub.ntp = "no"; calls.clear()
        state, _ = sl.restore_clock(path, run_stub, now)
        assert state == "kein_anker", state
        assert not any(c[0] == "date" for c in calls), calls

        # 3) kein NTP, gespeicherte Zeit AELTER -> Uhr laeuft plausibel weiter, nicht stellen
        with open(path, "w") as fh:
            fh.write((now - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"))
        calls.clear()
        state, _ = sl.restore_clock(path, run_stub, now)
        assert state == "plausibel", state
        assert not any(c[0] == "date" for c in calls), calls

        # 4) kein NTP, gespeicherte Zeit NEUER -> Uhr ist nachweislich falsch, stellen
        with open(path, "w") as fh:
            fh.write((now + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))
        calls.clear()
        state, _ = sl.restore_clock(path, run_stub, now)
        assert state == "korrigiert", state
        date_calls = [c for c in calls if c[0] == "date"]
        assert len(date_calls) == 1 and date_calls[0][2] == "2026-09-19 08:00:00", date_calls
    print("Uhr-Test OK")


def test_save_clock_roundtrip():
    import tempfile
    import session_logger as sl
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "last_known_time")
        sl.save_clock(path)
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp"), "Temporaerdatei nicht aufgeraeumt"
        from datetime import datetime
        datetime.strptime(open(path).read().strip(), "%Y-%m-%d %H:%M:%S")   # parsebar?
    print("save_clock OK")


if __name__ == "__main__":
    test_start_stop_matches_known_session()
    test_clock_restore_cases()
    test_save_clock_roundtrip()
    test_probe_runs_once_and_only_with_flag()
    test_probe_failure_does_not_break_logging()
