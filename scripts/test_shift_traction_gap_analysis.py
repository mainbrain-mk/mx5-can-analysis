"""Selbsttest fuer shift_traction_gap_analysis.py: synthetische Radgeschwindigkeits-
verlaeufe mit bekannter Zugkraftluecke, prueft dass find_longest_run() /
compute_drive_gap() sie korrekt wiederfindet. Kein Framework noetig:
`python scripts/test_shift_traction_gap_analysis.py`.
"""
import duckdb
import numpy as np

import shift_traction_gap_analysis as sga


def _make_db_with_wheel_speeds(t, v):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE measurements (log_id VARCHAR, channel VARCHAR, "
                "t_elapsed_s DOUBLE, value DOUBLE)")
    for ch in sga.WHEEL_CHANNELS_REAR:
        for ti, vi in zip(t, v):
            con.execute("INSERT INTO measurements VALUES ('log', ?, ?, ?)", [ch, ti, vi])
    return con


def test_clear_drive_gap_detected():
    # 0-1.0s: +5 km/h/s Beschleunigung, 1.0-1.5s: konstant (Luecke), 1.5-2.5s: wieder +5 km/h/s
    t = np.arange(0, 2.5, 0.02)
    v = np.piecewise(
        t, [t < 1.0, (t >= 1.0) & (t < 1.5), t >= 1.5],
        [lambda t: 30 + 5 * t,
         lambda t: 35.0,
         lambda t: 35.0 + 5 * (t - 1.5)],
    )
    con = _make_db_with_wheel_speeds(t, v)
    r = sga.compute_drive_gap(con, "log", t_start=0.9, t_end=1.6)
    assert not r["no_gap_detected"], r
    # Die 0.2s-Glaettung frisst an beiden Kanten der scharfen synthetischen Ecke
    # (reale Radgeschwindigkeit hat keine solchen Ecken) - erkannte Luecke daher
    # kleiner als die volle 0.5s-Plateaubreite, aber deutlich > 0.
    assert 0.15 < r["drive_gap_duration_s"] < 0.5, r
    assert 0.9 < r["drive_gap_start_s"] < 1.3, r
    assert 1.3 < r["drive_gap_end_s"] < 1.65, r


def test_no_gap_when_always_accelerating():
    t = np.arange(0, 2.5, 0.02)
    v = 30 + 5 * t  # durchgehend beschleunigend, keine Luecke
    con = _make_db_with_wheel_speeds(t, v)
    r = sga.compute_drive_gap(con, "log", t_start=0.9, t_end=1.6)
    assert r["no_gap_detected"], r
    assert r["drive_gap_duration_s"] == 0.0


def test_missing_wheel_channel_returns_none():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE measurements (log_id VARCHAR, channel VARCHAR, "
                "t_elapsed_s DOUBLE, value DOUBLE)")
    r = sga.compute_drive_gap(con, "log", t_start=0.9, t_end=1.6)
    assert r is None


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
