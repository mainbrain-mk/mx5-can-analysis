import numpy as np

from shift_time_analysis import detect_clutch_events


def _trace(t_on, t_off, t_end=6.0, dt=0.02, hole=None):
    t = np.arange(0.0, t_end, dt)
    if hole:
        t = t[(t < hole[0]) | (t > hole[1])]
    clutch = np.where((t >= t_on) & (t <= t_off), 100.0, 0.0)
    gear_t = np.array([0.0, 5.0])
    gear_v = np.array([5.0, 6.0])
    return t, clutch, gear_t, gear_v


def test_clean_upshift_measured():
    ev = detect_clutch_events(*_trace(2.0, 2.8))
    assert [e["kind"] for e in ev] == ["upshift"]
    assert 0.8 <= ev[0]["duration_s"] < 1.0  # Ende inkl. GAP_BRIDGE_S-Nachlauf


def test_upshift_cut_by_data_gap_is_flagged():
    # Luecke 2.3-3.5 s mitten in der Betaetigung: frueher "upshift" mit 0,3 s
    ev = detect_clutch_events(*_trace(2.0, 2.8, hole=(2.3, 3.5)))
    assert [e["kind"] for e in ev] == ["data_gap"]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
