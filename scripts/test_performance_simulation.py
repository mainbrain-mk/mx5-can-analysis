"""Selbstcheck Traegheit + Reifen-Traktionsgrenze in accel()."""
from performance_simulation import accel, TRACTION_MAX_FORCE_N
from drivetrain_model_validation import (
    MASS_KG, GEAR_RATIOS, FINAL_DRIVE, R_DYN_M, ENGINE_INERTIA_KGM2, effective_mass,
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


if __name__ == "__main__":
    test_inertia_grows_with_gear_ratio()
    test_traction_limit_binds_in_gear1_only()
    print("OK")
