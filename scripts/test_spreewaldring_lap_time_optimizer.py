"""Prueft transients(): Querruck und Gierbeschleunigung einer geschlossenen Runde."""
import sys

import numpy as np

from spreewaldring_lap_time_optimizer import transients


def test_transients_zero_on_circle_and_large_on_sudden_turn():
    n, r, v = 400, 50.0, 15.0
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pts = np.c_[r * np.cos(ang), r * np.sin(ang)]
    ds = np.full(n, 2 * np.pi * r / n)
    sim = {"ds": ds, "v": np.full(n, v), "radius": np.full(n, r), "lap_time_s": ds.sum() / v,
           "t": np.r_[0.0, np.cumsum(ds / v)][:n]}
    jerk, yaw_acc = transients(pts, sim)
    assert jerk < 1e-6 and yaw_acc < 1e-6
    sim["radius"] = np.where(np.arange(n) < n // 2, 1e6, r)  # Gerade, dann schlagartig Kreis
    jerk, yaw_acc = transients(pts, sim)
    expected = v ** 2 / r / 9.81 / 0.5                       # Sprung, ueber 0,5 s verschmiert
    assert 0.8 * expected < jerk < 1.2 * expected and yaw_acc > 0


if __name__ == "__main__":
    test_transients_zero_on_circle_and_large_on_sudden_turn()
    print("ok")
    sys.exit(0)
