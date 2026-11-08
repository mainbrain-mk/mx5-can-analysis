"""Randverfolgung an einem kuenstlichen Ring mit bekannter Breite, auch ueber eine Luecke in der Linie."""
import numpy as np

import spreewaldring_track_model as M


def ring_maps(size=1200, radius_m=60.0, half_width_m=4.5, px=0.2):
    yy, xx = np.mgrid[:size, :size]
    # Geo so, dass Pixel (col,row) = (e/px, -n/px) relativ zum Bildmittelpunkt
    e = (xx - size / 2) * px
    n = -(yy - size / 2) * px
    r = np.hypot(e, n)
    asphalt = (np.abs(r - radius_m) < half_width_m).astype(np.float32)
    line = np.exp(-((np.abs(r - radius_m) - half_width_m) / 0.2) ** 2).astype(np.float32)
    line[(e > 40) & (np.abs(n) < 4)] = 0.0  # 8 m ohne Linie: Einmuendung
    asphalt[(e > 40) & (np.abs(n) < 4)] = 1.0
    geo = (-(M.CROP_COL + size / 2) * px, (M.CROP_ROW + size / 2) * px, px)
    return line, asphalt, geo


def test_trace_recovers_ring_and_bridges_gap():
    line, asphalt, geo = ring_maps()
    angle = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    # Fuehrungslinie 1 m neben der Mitte, Fahrtrichtung gegen den Uhrzeigersinn
    guide = 61.0 * np.c_[np.cos(angle), np.sin(angle)]
    tr = M.trace_edges(guide, line, asphalt, geo, closed=True)
    r_left, r_right = np.hypot(*tr["left"].T), np.hypot(*tr["right"].T)
    assert abs(np.median(r_left) - 55.5) < 0.1  # links = innen bei dieser Fahrtrichtung
    assert abs(np.median(r_right) - 64.5) < 0.1
    assert np.abs(r_left - 55.5).max() < 0.5  # auch in der Luecke
    assert np.abs(r_right - 64.5).max() < 0.5
    step = np.hypot(*np.diff(np.vstack([tr["left"], tr["left"][:1]]), axis=0).T)
    assert step.max() < 1.2  # keine Naht


if __name__ == "__main__":
    test_trace_recovers_ring_and_bridges_gap()
    print("ok")
