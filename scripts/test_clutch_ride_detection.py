"""Selbsttest fuer clutch_ride_detection.py: synthetische Kupplungsverlaeufe
mit bekannter Schleifzeit. Kein Framework noetig:
`python scripts/test_clutch_ride_detection.py`.
"""
import duckdb
import numpy as np

import clutch_ride_detection as crd


def _make_db(t, v):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE measurements (log_id VARCHAR, channel VARCHAR, "
                "t_elapsed_s DOUBLE, value DOUBLE)")
    for ti, vi in zip(t, v):
        con.execute("INSERT INTO measurements VALUES ('log', 'ClutchPosition_CAN_raw', ?, ?)",
                     [ti, vi])
    return con


def test_normal_quick_release_not_flagged():
    # 0-0.3s: 0->198 (Tritt), 0.3-0.6s: 198->0 (zuegiges Loslassen)
    t = np.arange(0, 0.6, 0.02)
    v = np.where(t < 0.3, (t / 0.3) * 198, 198 * (1 - (t - 0.3) / 0.3))
    v = np.clip(v, 0, 198)
    con = _make_db(t, v)
    rd = crd.compute_ride_duration(con, "log", t_start=0.0, t_end=0.6)
    assert rd is not None
    assert rd < crd.RIDE_DURATION_THRESHOLD_S, rd


def test_riding_clutch_flagged():
    # wie oben, aber haengt von 0.3-0.35s (Tritt) bei raw~20 fest bis 1.8s
    t = np.arange(0, 2.0, 0.02)
    v = np.piecewise(
        t,
        [t < 0.3, (t >= 0.3) & (t < 0.35), (t >= 0.35) & (t < 1.8), t >= 1.8],
        [lambda t: (t / 0.3) * 198,
         lambda t: 198 - (t - 0.3) / 0.05 * 178,  # schnell runter auf ~20
         lambda t: 20.0,
         lambda t: 20 - (t - 1.8) / 0.2 * 20],
    )
    con = _make_db(t, v)
    rd = crd.compute_ride_duration(con, "log", t_start=0.0, t_end=1.9)
    assert rd is not None
    assert rd > crd.RIDE_DURATION_THRESHOLD_S, rd


def test_no_clutch_data_returns_none():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE measurements (log_id VARCHAR, channel VARCHAR, "
                "t_elapsed_s DOUBLE, value DOUBLE)")
    assert crd.compute_ride_duration(con, "log", 0.0, 1.0) is None


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
