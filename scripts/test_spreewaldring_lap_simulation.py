"""Test: Kraftkreis-Kopplung in der Spreewaldring-Rundenzeit-Simulation."""
import sys

sys.path.insert(0, "scripts")
import numpy as np

from performance_simulation import accel, TRACTION_MAX_FORCE_N
from spreewaldring_lap_simulation import _lon_limit, accel_true, find_corner_min_speeds, literature_vehicle, G
from spreewaldring_racing_line_optimal import lon_avail, plan_gears, simulate_lap_full


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


def test_corner_merge_gap_follows_actual_step():
    # zwei Kurven 20 m auseinander: bei jeder Schrittweite eine Kurve (merge_gap 30 m)
    for step in (1.0, 3.0):
        s = np.arange(0, 300, step)
        radius = np.full(len(s), 500.0)
        radius[(s >= 50) & (s < 80)] = 40.0
        radius[(s >= 100) & (s < 130)] = 30.0
        corners = find_corner_min_speeds(s, radius, np.sqrt(radius))
        assert len(corners) == 1, step
        assert corners[0]["radius_m"] == 30.0


def test_accel_true_converts_frame_without_changing_model():
    a_vs, rpm_vs = accel(30.0, 3, f_max=TRACTION_MAX_FORCE_N)
    assert accel_true(30.0, 3, 1.0) == (a_vs, rpm_vs)
    a, rpm = accel_true(30.0 * 0.971, 3, 0.971)  # gleiche Motordrehzahl, nur anderer Tempo-Rahmen
    assert rpm == rpm_vs
    assert abs(a - a_vs * 0.971) < 1e-12


def test_limiter_takes_torque_away_above_onset():
    from drivetrain_model_validation import rpm_from_speed
    from spreewaldring_lap_simulation import LIMITER_ONSET_RPM, LIMITER_WIDTH_RPM, limiter_factor
    assert limiter_factor(7000, 3) == 1.0
    assert abs(limiter_factor(LIMITER_ONSET_RPM[3] + LIMITER_WIDTH_RPM[3] / 2, 3) - 0.5) < 1e-9
    assert limiter_factor(7500, 2) == 0.0
    v = 1.0
    while rpm_from_speed(v, 3) < LIMITER_ONSET_RPM[3] + LIMITER_WIDTH_RPM[3]:
        v += 0.05
    assert accel_true(v, 3, 1.0)[0] < 0  # kein Moment mehr, nur Fahrwiderstand


def test_throttle_trace_full_limiter_and_braking():
    from spreewaldring_lap_simulation import engine_controls, throttle_trace
    load, tp, pedal = engine_controls([1.0, 0.3, 0.5, -0.2], [6000, 7300, 4000, 3000], [True, True, False, False])
    assert pedal[0] == 100 and tp[0] > 90 and 75 < load[0] < 100   # Vollgas
    assert pedal[1] == 100 and tp[1] < 90 and load[1] < 0.5 * load[0]  # Limiter: Klappe zu, Pedal bleibt
    assert 0 < pedal[2] < 100 and 0 < load[2] < 75                   # Teilgas
    assert pedal[3] == 0 and load[3] == 0                            # Schub
    veh = literature_vehicle(1.0)
    v = np.full(50, 25.0)                                            # konstantes Tempo im 4.: Teilgas gegen Fahrwiderstand
    load, _, pedal = throttle_trace(v, np.full(50, 4), np.full(50, 3.0), veh)
    assert (pedal > 0).all() and (pedal < 60).all() and (load > 0).all()


def test_lon_avail_is_ellipse():
    veh = literature_vehicle(1.0)._replace(lat_g=0.8, accel_g=0.5)
    assert abs(lon_avail(0.0, veh, veh.accel_g) - 0.5 * G) < 1e-9
    assert abs(lon_avail(0.8 * G, veh, veh.accel_g)) < 1e-9
    assert abs(lon_avail(0.48 * G, veh, veh.accel_g) - 0.4 * G) < 1e-9  # (0,6)^2 + (0,8)^2 = 1


def straight(n=120, limit=60.0):
    return [limit] * n, [1e6] * n, [3.0] * n


def test_plan_gears_shifts_up_on_a_straight():
    veh = literature_vehicle(1.0)
    limit, radius, ds = straight(n=300)
    limit[0] = 15.0  # Start im langsamen Punkt, danach frei
    start, shifts = plan_gears(limit, radius, ds, veh)
    assert start <= 3
    assert shifts and all(gap > 0 for _, gap in shifts.values())


def test_plan_gears_skips_gear_that_only_lasts_a_moment():
    # Tempo kaum ueber der Redline von Gang 1 begrenzt: Nur ein Gang lohnt, keine Schaltung
    veh = literature_vehicle(1.0)
    limit = [13.0] * 80
    _, shifts = plan_gears(limit, [1e6] * 80, [3.0] * 80, veh)
    assert not shifts


def test_plan_gears_shifts_free_while_braking():
    # Vollgas, Bremszone, danach wieder Vollgas aus niedrigem Tempo: Runterschalten beim Bremsen kostet keine Luecke
    veh = literature_vehicle(1.0)
    limit = [10.0] + [60.0] * 149 + list(np.linspace(60.0, 10.0, 100)) + [60.0] * 150
    _, shifts = plan_gears(limit, [1e6] * 400, [3.0] * 400, veh)
    assert any(gap == 0.0 for _, gap in shifts.values())


def test_gear_trace_is_closed_and_steps_by_one():
    # geschlossene Runde: kurze Gerade, enge Kurve. Nie mehr als ein Gang je Punkt, Start und Ende passen
    angle = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    r = 150 + 60 * np.cos(2 * angle)
    points = np.c_[r * np.cos(angle), r * np.sin(angle)]
    sim = simulate_lap_full(points, literature_vehicle(1.0))
    assert np.abs(np.diff(sim["gear"])).max() <= 1
    assert abs(int(sim["gear"][0]) - int(sim["gear"][-1])) <= 1


if __name__ == "__main__":
    test_accel_true_converts_frame_without_changing_model()
    test_lon_avail_is_ellipse()
    test_limiter_takes_torque_away_above_onset()
    test_throttle_trace_full_limiter_and_braking()
    test_plan_gears_shifts_up_on_a_straight()
    test_plan_gears_skips_gear_that_only_lasts_a_moment()
    test_plan_gears_shifts_free_while_braking()
    test_gear_trace_is_closed_and_steps_by_one()
    test_corner_merge_gap_follows_actual_step()
    test_lon_limit_unchanged_without_friction_circle()
    test_lon_limit_full_at_zero_lateral()
    test_lon_limit_zero_at_full_lateral_grip()
    test_lon_limit_is_monotonically_smaller_with_friction_circle()
    test_lon_limit_supports_asymmetric_ellipse()
    print("OK")
