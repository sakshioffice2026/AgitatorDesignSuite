"""
shaft_physics.py

Python port of the trusted shaft formula in
Agitator.Business/AgitatorCalculationService.cs -> CalculateShaftDiameterM.

Units (all SI):
    torque_nm            N*m
    impeller_diameter_m  m
    liquid_height_m      m
    shaft diameter       m (raw) / mm (rounded)
    stress               Pa
"""

import math

# ---- Constants (must match AgitatorCalculationService.cs) -------------------
ALLOWABLE_SHEAR_STRESS_PA = 40_000_000.0
BENDING_FACTOR_KM = 1.5
TORSION_FACTOR_KT = 1.0
RADIAL_FORCE_FRACTION = 0.5
MOTOR_SERVICE_FACTOR = 1.25
EFFECTIVE_RADIUS_FRACTION = 0.75
SHAFT_LENGTH_FACTOR = 1.15

STANDARD_SHAFT_SIZES_MM = [
    20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 90, 100,
    110, 120, 130, 140, 150, 160, 180, 200, 220, 240, 260, 280, 300,
]

# ---- Trusted input range (edit after Phase A design review) -----------------
TRUSTED_TORQUE_NM = (1.0, 200_000.0)
TRUSTED_IMPELLER_DIAMETER_M = (0.05, 4.0)
TRUSTED_LIQUID_HEIGHT_M = (0.1, 10.0)


def design_torque_nm(rated_torque_nm: float) -> float:
    return rated_torque_nm * MOTOR_SERVICE_FACTOR


def radial_force_n(rated_torque_nm: float, impeller_diameter_m: float) -> float:
    effective_radius = EFFECTIVE_RADIUS_FRACTION * impeller_diameter_m / 2.0
    tangential_force = design_torque_nm(rated_torque_nm) / effective_radius
    return RADIAL_FORCE_FRACTION * tangential_force


def bending_moment_nm(rated_torque_nm: float, impeller_diameter_m: float,
                      liquid_height_m: float) -> float:
    shaft_length = liquid_height_m * SHAFT_LENGTH_FACTOR
    return radial_force_n(rated_torque_nm, impeller_diameter_m) * shaft_length


def equivalent_torque_nm(rated_torque_nm: float, impeller_diameter_m: float,
                         liquid_height_m: float) -> float:
    m = bending_moment_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    t = design_torque_nm(rated_torque_nm)
    return math.sqrt((BENDING_FACTOR_KM * m) ** 2 + (TORSION_FACTOR_KT * t) ** 2)


def raw_shaft_diameter_m(rated_torque_nm: float, impeller_diameter_m: float,
                         liquid_height_m: float) -> float:
    """Shaft diameter before rounding to a standard size (m)."""
    te = equivalent_torque_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    return ((16.0 * te) / (math.pi * ALLOWABLE_SHEAR_STRESS_PA)) ** (1.0 / 3.0)


def round_up_to_standard_size_m(diameter_m: float) -> float:
    mm = diameter_m * 1000.0
    for size in STANDARD_SHAFT_SIZES_MM:
        if mm <= size:
            return size / 1000.0
    return math.ceil(mm / 10.0) * 10.0 / 1000.0


def formula_shaft_diameter_m(rated_torque_nm: float, impeller_diameter_m: float,
                             liquid_height_m: float) -> float:
    """Same result as CalculateShaftDiameterM in C#."""
    return round_up_to_standard_size_m(
        raw_shaft_diameter_m(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    )


def combined_shear_stress_pa(rated_torque_nm: float, impeller_diameter_m: float,
                             liquid_height_m: float, shaft_diameter_m: float) -> float:
    """Combined torsion + bending shear stress for a given shaft diameter."""
    te = equivalent_torque_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    return (16.0 * te) / (math.pi * shaft_diameter_m ** 3)


def is_within_trusted_range(rated_torque_nm: float, impeller_diameter_m: float,
                            liquid_height_m: float) -> bool:
    return (
        TRUSTED_TORQUE_NM[0] <= rated_torque_nm <= TRUSTED_TORQUE_NM[1]
        and TRUSTED_IMPELLER_DIAMETER_M[0] <= impeller_diameter_m <= TRUSTED_IMPELLER_DIAMETER_M[1]
        and TRUSTED_LIQUID_HEIGHT_M[0] <= liquid_height_m <= TRUSTED_LIQUID_HEIGHT_M[1]
    )
