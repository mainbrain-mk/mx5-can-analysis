"""Test: Kraftkreis-Kopplung in der Spreewaldring-Rundenzeit-Simulation."""
import sys

sys.path.insert(0, "scripts")
from spreewaldring_lap_simulation import _lon_limit, G


def test_lon_limit_unchanged_without_friction_circle():
    assert _lon_limit(30.0, 20.0, 1.0 * G, 1.0 * G, accel_cap=5.0, friction_circle=False) == 5.0


def test_lon_limit_full_at_zero_lateral():
    # gerade Strecke (a_lat~0, riesiger Radius): Kraftkreis-Grenze ~a_lon_max
    a = _lon_limit(30.0, 1e9, G, G, accel_cap=1e9, friction_circle=True)
    assert abs(a - G) < 1e-3
    # accel_cap enger als a_lon_max bleibt der bindende Deckel
    a2 = _lon_limit(30.0, 1e9, G, G, accel_cap=1.0, friction_circle=True)
    assert a2 == 1.0


def test_lon_limit_zero_at_full_lateral_grip():
    # v^2/R = a_lat_max -> gesamte Reifenhaftung schon fuer die Kurve verbraucht
    a_lat_max = G
    radius = 50.0
    v = (a_lat_max * radius) ** 0.5
    a = _lon_limit(v, radius, a_lat_max, G, accel_cap=1e9, friction_circle=True)
    assert a < 1e-6


def test_lon_limit_is_monotonically_smaller_with_friction_circle():
    v, radius = 25.0, 40.0
    without = _lon_limit(v, radius, G, G, accel_cap=1e9, friction_circle=False)
    with_fc = _lon_limit(v, radius, G, G, accel_cap=1e9, friction_circle=True)
    assert with_fc <= without


def test_lon_limit_supports_asymmetric_ellipse():
    # gemessener Kraftkreis: a_lat_max != a_lon_max (z.B. Bremsen != Beschleunigen)
    a_lat_max, a_lon_max = 1.09 * G, 0.81 * G
    a_zero_lat = _lon_limit(0.001, 1e9, a_lat_max, a_lon_max, accel_cap=1e9, friction_circle=True)
    assert abs(a_zero_lat - a_lon_max) < 1e-3


if __name__ == "__main__":
    test_lon_limit_unchanged_without_friction_circle()
    test_lon_limit_full_at_zero_lateral()
    test_lon_limit_zero_at_full_lateral_grip()
    test_lon_limit_is_monotonically_smaller_with_friction_circle()
    test_lon_limit_supports_asymmetric_ellipse()
    print("OK")
