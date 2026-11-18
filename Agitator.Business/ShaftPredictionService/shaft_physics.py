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
# Fallback only (mild carbon steel, room temperature). The service passes the material- and
# temperature-based allowable from the .NET request; fea_dataset_generator.py still uses this default.
ALLOWABLE_SHEAR_STRESS_PA = 40_000_000.0
BENDING_FACTOR_KM = 1.5
TORSION_FACTOR_KT = 1.0
RADIAL_FORCE_FRACTION = 0.5
MOTOR_SERVICE_FACTOR = 1.25
EFFECTIVE_RADIUS_FRACTION = 0.75
SHAFT_LENGTH_FACTOR = 1.15   # estimate only; must equal AgitatorCalculationService.ComputeShaftLengthM

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
                         liquid_height_m: float,
                         allowable_shear_pa: float = ALLOWABLE_SHEAR_STRESS_PA) -> float:
    """Shaft diameter before rounding to a standard size (m)."""
    te = equivalent_torque_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    return ((16.0 * te) / (math.pi * allowable_shear_pa)) ** (1.0 / 3.0)

def round_up_to_standard_size_m(diameter_m: float) -> float:
    mm = diameter_m * 1000.0
    for size in STANDARD_SHAFT_SIZES_MM:
        if mm <= size:
            return size / 1000.0
    return math.ceil(mm / 10.0) * 10.0 / 1000.0


def formula_shaft_diameter_m(rated_torque_nm: float, impeller_diameter_m: float,
                             liquid_height_m: float,
                             allowable_shear_pa: float = ALLOWABLE_SHEAR_STRESS_PA) -> float:
    """Same result as CalculateShaftDiameterM in C#."""
    return round_up_to_standard_size_m(
        raw_shaft_diameter_m(rated_torque_nm, impeller_diameter_m, liquid_height_m,
                             allowable_shear_pa)
    )

def combined_shear_stress_pa(rated_torque_nm: float, impeller_diameter_m: float,
                             liquid_height_m: float, shaft_diameter_m: float) -> float:
    """Combined torsion + bending shear stress for a given shaft diameter."""
    te = equivalent_torque_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    return (16.0 * te) / (math.pi * shaft_diameter_m ** 3)

def shaft_length_m(liquid_height_m: float) -> float:
    """Shaft length used for bending moment, deflection and twist (m)."""
    return liquid_height_m * SHAFT_LENGTH_FACTOR


def analytic_equivalent_torque_nm(rated_torque_nm: float, impeller_diameter_m: float,
                                  liquid_height_m: float) -> float:
    """Textbook equivalent torque, Km = Kt = 1: sqrt(M^2 + T^2)."""
    m = bending_moment_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    t = design_torque_nm(rated_torque_nm)
    return math.sqrt(m * m + t * t)


def analytic_diameter_m(rated_torque_nm: float, impeller_diameter_m: float,
                        liquid_height_m: float,
                        allowable_shear_pa: float = ALLOWABLE_SHEAR_STRESS_PA) -> float:
    """Shaft diameter from plain max-shear theory (Km = 1), before rounding (m)."""
    te = analytic_equivalent_torque_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    return ((16.0 * te) / (math.pi * allowable_shear_pa)) ** (1.0 / 3.0)


def bending_stress_pa(moment_nm: float, shaft_diameter_m: float) -> float:
    """Outer-fibre bending stress sigma = 32 M / (pi d^3)."""
    return 32.0 * abs(moment_nm) / (math.pi * shaft_diameter_m ** 3)


def torsional_shear_stress_pa(torque_nm: float, shaft_diameter_m: float) -> float:
    """Outer-fibre torsional shear stress tau = 16 T / (pi d^3)."""
    return 16.0 * torque_nm / (math.pi * shaft_diameter_m ** 3)


def tresca_shear_stress_pa(moment_nm: float, torque_nm: float,
                           shaft_diameter_m: float) -> float:
    """Maximum shear stress (Tresca): sqrt((sigma/2)^2 + tau^2)."""
    sigma = bending_stress_pa(moment_nm, shaft_diameter_m)
    tau = torsional_shear_stress_pa(torque_nm, shaft_diameter_m)
    return math.sqrt((sigma / 2.0) ** 2 + tau ** 2)


def von_mises_stress_pa(moment_nm: float, torque_nm: float,
                        shaft_diameter_m: float) -> float:
    """von Mises stress: sqrt(sigma^2 + 3 tau^2)."""
    sigma = bending_stress_pa(moment_nm, shaft_diameter_m)
    tau = torsional_shear_stress_pa(torque_nm, shaft_diameter_m)
    return math.sqrt(sigma ** 2 + 3.0 * tau ** 2)


def analytic_tip_deflection_m(radial_force_n_value: float, shaft_length_m_value: float,
                              elastic_modulus_pa: float, shaft_diameter_m: float) -> float:
    """Cantilever tip deflection F L^3 / (3 E I)."""
    ei = elastic_modulus_pa * math.pi * shaft_diameter_m ** 4 / 64.0
    return radial_force_n_value * shaft_length_m_value ** 3 / (3.0 * ei)


def analytic_twist_rad(design_torque_nm_value: float, shaft_length_m_value: float,
                       shear_modulus_pa: float, shaft_diameter_m: float) -> float:
    """Angle of twist T L / (G J)."""
    gj = shear_modulus_pa * math.pi * shaft_diameter_m ** 4 / 32.0
    return design_torque_nm_value * shaft_length_m_value / gj

def is_within_trusted_range(rated_torque_nm: float, impeller_diameter_m: float,
                            liquid_height_m: float) -> bool:
    return (
        TRUSTED_TORQUE_NM[0] <= rated_torque_nm <= TRUSTED_TORQUE_NM[1]
        and TRUSTED_IMPELLER_DIAMETER_M[0] <= impeller_diameter_m <= TRUSTED_IMPELLER_DIAMETER_M[1]
        and TRUSTED_LIQUID_HEIGHT_M[0] <= liquid_height_m <= TRUSTED_LIQUID_HEIGHT_M[1]
    )
