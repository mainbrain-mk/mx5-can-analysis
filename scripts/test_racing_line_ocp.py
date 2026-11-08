"""Kreisring: analytische Loesung bekannt. Konstantes Tempo am Grip-Limit, t ~ sqrt(R), also ganz innen fahren."""
import sys

sys.path.insert(0, "scripts")
import numpy as np

import racing_line_ocp as R
import spreewaldring_racing_line_editor as E
from spreewaldring_lap_simulation import Vehicle
from spreewaldring_track import G


def test_circle_goes_inside_at_grip_limit():
    n, radius, half = 209, 100.0, 4.0
    angle = np.linspace(0, 2 * np.pi, n, endpoint=False)
    center = radius * np.c_[np.cos(angle), np.sin(angle)]
    perp = np.c_[np.cos(angle), np.sin(angle)]   # n > 0 ist aussen
    track = E.Track(center, center, center, perp, np.full(n, -half), np.full(n, half))
    veh = Vehicle("Test", 0.9, 0.5, 0.8, 1.0, {})
    engine = np.full(len(R.ENGINE_GRID_MS), 3.0)
    n_lat, v, status, _ = R.solve_nlp(track, veh, engine, np.zeros(n), np.full(n, 25.0))
    assert status == "Solve_Succeeded", status
    inner = radius - half + E.EDGE_MARGIN_M
    assert np.abs(n_lat - (inner - radius)).max() < 0.02
    assert abs(np.median(v) - np.sqrt(0.9 * G * inner)) < 0.05


if __name__ == "__main__":
    test_circle_goes_inside_at_grip_limit()
    print("ok")
