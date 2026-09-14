"""Test: session_logger-Statemachine gegen echte KeyState-Frames aus dem Testlog."""
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


if __name__ == "__main__":
    test_start_stop_matches_known_session()
