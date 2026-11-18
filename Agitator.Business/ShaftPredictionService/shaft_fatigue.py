"""
shaft_fatigue.py

Fatigue check for a rotating agitator shaft: Goodman and Soderberg safety factors,
Marin-corrected endurance limit, and the minimum diameter for a target safety factor.

Load case (rotating shaft):
    bending  : fully reversed  -> alternating stress only   (sigma_a, sigma_m = 0)
    torsion  : steady          -> mean stress only          (tau_m,   tau_a   = 0)

Von Mises equivalent stresses:
    sigma_a' = Kf * sigma_a
    sigma_m' = sqrt(3) * Kfs * tau_m

Units (all SI):
    stress, strength   Pa
    diameter           m
    moment, torque     N*m
"""

from dataclasses import dataclass
import math

from shaft_physics import bending_moment_nm, design_torque_nm

# ---- Surface finish constants (Sut in MPa): ka = a * Sut^b ------------------
SURFACE_FACTORS = {
    "ground":     (1.58, -0.085),
    "machined":   (4.51, -0.265),
    "cold_drawn": (4.51, -0.265),
    "hot_rolled": (57.7, -0.718),
    "as_forged":  (272.0, -0.995),
}

# ---- Reliability factor ke ---------------------------------------------------
RELIABILITY_FACTORS = {
    50.0: 1.000,
    90.0: 0.897,
    95.0: 0.868,
    99.0: 0.814,
    99.9: 0.753,
    99.99: 0.702,
}

ENDURANCE_LIMIT_CAP_PA = 700e6   # Se' = 0.5 Sut, capped for Sut > 1400 MPa
LARGE_SIZE_FACTOR = 0.60         # conservative kb for d > 254 mm
D_MIN_SEARCH_M = 0.005
D_MAX_SEARCH_M = 1.0


@dataclass
class FatigueMaterial:
    ultimate_pa: float
    yield_pa: float
    endurance_limit_pa: float = None   # measured Se' (rotating beam); None -> 0.5 * Sut
    surface: str = "machined"
    temperature_factor: float = 1.0    # kd
    reliability_percent: float = 99.0  # ke lookup
    load_factor: float = 1.0           # kc, 1.0 for bending (combined load uses von Mises)


@dataclass
class FatigueResult:
    diameter_m: float
    sigma_a_pa: float
    sigma_m_pa: float
    endurance_limit_pa: float
    goodman_factor: float
    soderberg_factor: float
    yield_factor: float
    passed: bool


# ---------------------------------------------------------------------------
# Marin factors
# ---------------------------------------------------------------------------
def surface_factor(ultimate_pa: float, surface: str) -> float:
    a, b = SURFACE_FACTORS[surface]
    return a * (ultimate_pa / 1e6) ** b


def size_factor(diameter_m: float) -> float:
    d_mm = diameter_m * 1000.0
    if d_mm < 2.79:
        return 1.0
    if d_mm <= 51.0:
        return 1.24 * d_mm ** -0.107
    if d_mm <= 254.0:
        return 1.51 * d_mm ** -0.157
    return LARGE_SIZE_FACTOR


def reliability_factor(reliability_percent: float) -> float:
    if reliability_percent in RELIABILITY_FACTORS:
        return RELIABILITY_FACTORS[reliability_percent]
    keys = sorted(RELIABILITY_FACTORS)
    if reliability_percent <= keys[0]:
        return RELIABILITY_FACTORS[keys[0]]
    if reliability_percent >= keys[-1]:
        return RELIABILITY_FACTORS[keys[-1]]
    for lo, hi in zip(keys, keys[1:]):
        if lo <= reliability_percent <= hi:
            t = (reliability_percent - lo) / (hi - lo)
            return RELIABILITY_FACTORS[lo] + t * (RELIABILITY_FACTORS[hi] - RELIABILITY_FACTORS[lo])
    return RELIABILITY_FACTORS[99.0]


def corrected_endurance_limit_pa(material: FatigueMaterial, diameter_m: float) -> float:
    se_prime = material.endurance_limit_pa
    if se_prime is None:
        se_prime = min(0.5 * material.ultimate_pa, ENDURANCE_LIMIT_CAP_PA)
    return (
        surface_factor(material.ultimate_pa, material.surface)
        * size_factor(diameter_m)
        * material.load_factor
        * material.temperature_factor
        * reliability_factor(material.reliability_percent)
        * se_prime
    )


# ---------------------------------------------------------------------------
# Stresses
# ---------------------------------------------------------------------------
def alternating_mean_stress_pa(moment_nm: float, torque_nm: float, diameter_m: float,
                               kf: float = 1.0, kfs: float = 1.0):
    """Von Mises alternating and mean stress for reversed bending + steady torsion."""
    sigma_b = 32.0 * moment_nm / (math.pi * diameter_m ** 3)
    tau_t = 16.0 * torque_nm / (math.pi * diameter_m ** 3)
    sigma_a = kf * sigma_b
    sigma_m = math.sqrt(3.0) * kfs * tau_t
    return sigma_a, sigma_m


# ---------------------------------------------------------------------------
# Safety factors
# ---------------------------------------------------------------------------
def goodman_factor(sigma_a: float, sigma_m: float, se: float, sut: float) -> float:
    denom = sigma_a / se + sigma_m / sut
    return math.inf if denom <= 0.0 else 1.0 / denom


def soderberg_factor(sigma_a: float, sigma_m: float, se: float, sy: float) -> float:
    denom = sigma_a / se + sigma_m / sy
    return math.inf if denom <= 0.0 else 1.0 / denom


def first_cycle_yield_factor(sigma_a: float, sigma_m: float, sy: float) -> float:
    peak = sigma_a + sigma_m
    return math.inf if peak <= 0.0 else sy / peak


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fatigue_check(moment_nm: float, torque_nm: float, diameter_m: float,
                  material: FatigueMaterial, required_factor: float = 1.5,
                  kf: float = 1.0, kfs: float = 1.0) -> FatigueResult:
    se = corrected_endurance_limit_pa(material, diameter_m)
    sigma_a, sigma_m = alternating_mean_stress_pa(moment_nm, torque_nm, diameter_m, kf, kfs)

    n_goodman = goodman_factor(sigma_a, sigma_m, se, material.ultimate_pa)
    n_soderberg = soderberg_factor(sigma_a, sigma_m, se, material.yield_pa)
    n_yield = first_cycle_yield_factor(sigma_a, sigma_m, material.yield_pa)

    return FatigueResult(
        diameter_m=diameter_m,
        sigma_a_pa=sigma_a,
        sigma_m_pa=sigma_m,
        endurance_limit_pa=se,
        goodman_factor=n_goodman,
        soderberg_factor=n_soderberg,
        yield_factor=n_yield,
        passed=min(n_goodman, n_soderberg, n_yield) >= required_factor,
    )


def fatigue_check_from_inputs(rated_torque_nm: float, impeller_diameter_m: float,
                              liquid_height_m: float, diameter_m: float,
                              material: FatigueMaterial, required_factor: float = 1.5,
                              kf: float = 1.0, kfs: float = 1.0) -> FatigueResult:
    """Same load path as shaft_physics.py: design torque and bending moment from the 3 inputs."""
    moment = bending_moment_nm(rated_torque_nm, impeller_diameter_m, liquid_height_m)
    torque = design_torque_nm(rated_torque_nm)
    return fatigue_check(moment, torque, diameter_m, material, required_factor, kf, kfs)


def minimum_fatigue_diameter_m(moment_nm: float, torque_nm: float,
                               material: FatigueMaterial, required_factor: float = 1.5,
                               kf: float = 1.0, kfs: float = 1.0,
                               criterion: str = "goodman") -> float:
    """
    Smallest diameter whose governing safety factor reaches required_factor.
    criterion: "goodman" or "soderberg". The yield check is always included.
    Bisection is used because the size factor depends on the diameter.
    """
    def governing(d: float) -> float:
        r = fatigue_check(moment_nm, torque_nm, d, material, required_factor, kf, kfs)
        fatigue_n = r.goodman_factor if criterion == "goodman" else r.soderberg_factor
        return min(fatigue_n, r.yield_factor)

    lo, hi = D_MIN_SEARCH_M, D_MAX_SEARCH_M
    if governing(hi) < required_factor:
        return math.inf
    if governing(lo) >= required_factor:
        return lo

    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if governing(mid) >= required_factor:
            hi = mid
        else:
            lo = mid
    return hi
