"""Linienbearbeitung des Editors an einem kuenstlichen Kreis, ohne Dienst und ohne Streckendaten."""
import sys

sys.path.insert(0, "scripts")
import numpy as np

import spreewaldring_racing_line_editor as E
from spreewaldring_lap_simulation import literature_vehicle


def circle_track(n=209, radius=100.0):
    angle = np.linspace(0, 2 * np.pi, n, endpoint=False)
    center = radius * np.c_[np.cos(angle), np.sin(angle)]
    perp = np.c_[np.cos(angle), np.sin(angle)]
    return E.Track(center, center, center, perp, np.full(n, -3.0), np.full(n, 3.0))


def test_corners_by_direction_numbers_arcs_in_driving_order():
    # Stadion: Gerade, Halbkreis, Gerade, Halbkreis; Index 0 liegt auf der Geraden
    arc = np.linspace(-np.pi / 2, np.pi / 2, 31)[1:-1]
    center = np.vstack([np.c_[np.linspace(-90, 90, 61), np.full(61, -30.0)],
                        np.c_[90 + 30 * np.cos(arc), 30 * np.sin(arc)],
                        np.c_[np.linspace(90, -90, 61), np.full(61, 30.0)],
                        np.c_[-90 - 30 * np.cos(arc), -30 * np.sin(arc)]])
    radius = np.full(len(center), 1e9)
    radius[61:90], radius[151:180] = 30.0, 30.0
    corners = E.corners_by_direction(center, radius)
    assert [c["number"] for c in corners] == [1, 2]
    assert corners[0]["x"] > 90 and corners[1]["x"] < -90


def test_unknown_track_is_rejected():
    try:
        E.Editors()["gibtsnicht"]
    except ValueError:
        return
    raise AssertionError("unbekannte Strecke wurde nicht abgelehnt")


def test_brush_hits_target_and_ends_smoothly():
    track = circle_track()
    n_lat = E.apply_brush(track, np.zeros(209), 50, 2.0, 30.0)
    assert n_lat[50] == 2.0
    assert n_lat[50 + 10] == 0.0 and n_lat[50 - 10] == 0.0  # 30 m / 3 m = 10 Punkte Fenster
    assert 0.0 < n_lat[55] < 2.0


def test_brush_wraps_over_closure():
    n_lat = E.apply_brush(circle_track(), np.zeros(209), 0, 1.0, 30.0)
    assert n_lat[208] > 0.0 and n_lat[1] > 0.0


def test_clamp_keeps_margin_and_narrow_corridor_uses_middle():
    track = circle_track()
    assert E.clamp_inset(track, np.full(209, 9.0))[0] == 3.0 - E.EDGE_MARGIN_M
    narrow = track._replace(lo=np.full(209, 1.0), hi=np.full(209, 1.05))
    assert abs(E.clamp_inset(narrow, np.zeros(209))[0] - 1.025) < 1e-9


def test_hard_target_is_exact_after_optimizing():
    track = circle_track()
    edits = [{"index": 40, "value": 1.5, "hard": True, "radius": 30.0}]
    n_lat = E.recompute_from_edits(track, np.zeros(209), edits, literature_vehicle(1.0))
    assert abs(n_lat[40] - 1.5) < 1e-9
    assert np.all(n_lat >= -3.0) and np.all(n_lat <= 3.0)


if __name__ == "__main__":
    test_corners_by_direction_numbers_arcs_in_driving_order()
    test_unknown_track_is_rejected()
    test_brush_hits_target_and_ends_smoothly()
    test_brush_wraps_over_closure()
    test_clamp_keeps_margin_and_narrow_corridor_uses_middle()
    test_hard_target_is_exact_after_optimizing()
    print("OK")
