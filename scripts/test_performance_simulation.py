"""Selbstcheck Traegheit + Reifen-Traktionsgrenze in accel(), Luftdichte im Moment."""
import pandas as pd

from performance_simulation import accel, TRACTION_MAX_FORCE_N
from drivetrain_model_validation import (
    MASS_KG, GEAR_RATIOS, FINAL_DRIVE, R_DYN_M, ENGINE_INERTIA_KGM2, RPM_TABLE, TORQUE_TABLE_NM,
    TORQUE_RHO_REF_KG_M3, effective_mass, model_accel, segment_air_density, torque_nm,
)


def test_inertia_grows_with_gear_ratio():
    extra = [effective_mass(MASS_KG, g) - MASS_KG for g in sorted(GEAR_RATIOS)]
    assert extra == sorted(extra, reverse=True)
    assert abs(extra[0] - ENGINE_INERTIA_KGM2 * (GEAR_RATIOS[1] * FINAL_DRIVE / R_DYN_M) ** 2) < 1e-9


def test_traction_limit_binds_in_gear1_only():
    v = 30 / 3.6
    assert accel(v, 1, f_max=TRACTION_MAX_FORCE_N)[0] < accel(v, 1)[0]
    for g, v in [(2, 70 / 3.6), (3, 110 / 3.6)]:
        assert accel(v, g, f_max=TRACTION_MAX_FORCE_N)[0] == accel(v, g)[0]


def test_torque_follows_air_density():
    assert torque_nm(RPM_TABLE[5]) == TORQUE_TABLE_NM[5]  # ohne Dichte: Kennfeld unveraendert
    assert abs(torque_nm(4000, 1.26) / torque_nm(4000) - 1.26 / TORQUE_RHO_REF_KG_M3) < 1e-9
    assert model_accel(25, 3, rho=1.26)[0] > model_accel(25, 3)[0]


def test_segment_air_density():
    iat = pd.DataFrame({"t": [0.0, 10.0, 200.0], "value": [20.0, 20.0, 0.0]})
    baro = pd.DataFrame({"t": [0.0], "value": [101.3]})
    empty = pd.DataFrame({"t": [], "value": []})
    assert abs(segment_air_density(iat, baro, 5, 15) - 1.204) < 0.001
    assert abs(segment_air_density(iat, baro, 30, 40) - 1.204) < 0.001  # Umfeld, Baro ebenso
    assert abs(segment_air_density(iat, empty, 5, 15) - 1.204) < 0.001  # Rueckfall Barometer
    assert segment_air_density(empty, baro, 5, 15) is None


if __name__ == "__main__":
    test_inertia_grows_with_gear_ratio()
    test_traction_limit_binds_in_gear1_only()
    test_torque_follows_air_density()
    test_segment_air_density()
    print("OK")
