"""
shaft_critical_speed.py

First lateral critical speed of an agitator shaft.

Model: cantilever shaft (fixed at the drive end), impeller as a lumped mass at the free end,
optional extra lumped masses (additional impellers) along the shaft.

Methods:
    - Rayleigh-Ritz  : numerical, uses the static deflection curve of the shaft
    - Dunkerley      : closed form, 1/w^2 = sum of 1/w_i^2
    - Rayleigh closed form for a tip mass with the shaft mass included (33/140 equivalent mass)

Units (all SI):
    diameter, length   m
    mass               kg
    E                  Pa
    density            kg/m^3
    speed              rad/s (omega) or rpm
"""

import math

G_ACCEL = 9.80665
STEEL_E_PA = 200e9
STEEL_DENSITY = 7850.0
CANTILEVER_BETA1_L = 1.875104        # first root of the cantilever frequency equation
EQUIVALENT_MASS_FRACTION = 33.0 / 140.0   # distributed shaft mass seen at the tip
DEFAULT_SEGMENTS = 400
D_MIN_SEARCH_M = 0.005
D_MAX_SEARCH_M = 1.0


# ---------------------------------------------------------------------------
# Section properties
# ---------------------------------------------------------------------------
def area_m2(d_m: float) -> float:
    return math.pi * d_m ** 2 / 4.0


def second_moment_m4(d_m: float) -> float:
    return math.pi * d_m ** 4 / 64.0


def shaft_mass_kg(d_m: float, length_m: float, density: float = STEEL_DENSITY) -> float:
    return density * area_m2(d_m) * length_m


def rad_s_to_rpm(omega: float) -> float:
    return omega * 60.0 / (2.0 * math.pi)


def rpm_to_rad_s(rpm: float) -> float:
    return rpm * 2.0 * math.pi / 60.0


# ---------------------------------------------------------------------------
# Static deflection of a cantilever (Euler-Bernoulli)
# ---------------------------------------------------------------------------
def _point_load_deflection(x: float, a: float, p: float, ei: float) -> float:
    """Deflection at x for a point load p applied at distance a from the fixed end."""
    if x <= a:
        return p * x ** 2 * (3.0 * a - x) / (6.0 * ei)
    return p * a ** 2 * (3.0 * x - a) / (6.0 * ei)


# ---------------------------------------------------------------------------
# 1. Rayleigh-Ritz (numerical)
# ---------------------------------------------------------------------------
def rayleigh_critical_speed_rad_s(d_m: float, length_m: float, tip_mass_kg: float,
                                  e_pa: float = STEEL_E_PA,
                                  density: float = STEEL_DENSITY,
                                  extra_masses=None,
                                  segments: int = DEFAULT_SEGMENTS) -> float:
    """
    w^2 = g * sum(m_i * y_i) / sum(m_i * y_i^2)

    extra_masses : optional list of (mass_kg, position_m) measured from the fixed end
    The shaft is split into equal segments, each lumped at its midpoint.
    """
    ei = e_pa * second_moment_m4(d_m)
    seg_len = length_m / segments
    seg_mass = density * area_m2(d_m) * seg_len

    masses = [(seg_mass, (i + 0.5) * seg_len) for i in range(segments)]
    masses.append((tip_mass_kg, length_m))
    if extra_masses:
        masses.extend(extra_masses)

    # static deflection under the weight of every mass (superposition)
    def deflection_at(x: float) -> float:
        return sum(_point_load_deflection(x, pos, m * G_ACCEL, ei) for m, pos in masses)

    num = 0.0
    den = 0.0
    for m, pos in masses:
        y = deflection_at(pos)
        num += m * y
        den += m * y * y
    return math.sqrt(G_ACCEL * num / den)


# ---------------------------------------------------------------------------
# 2. Dunkerley (closed form)
# ---------------------------------------------------------------------------
def shaft_alone_frequency_rad_s(d_m: float, length_m: float,
                                e_pa: float = STEEL_E_PA,
                                density: float = STEEL_DENSITY) -> float:
    """First natural frequency of the bare cantilever shaft."""
    ei = e_pa * second_moment_m4(d_m)
    rho_a = density * area_m2(d_m)
    return CANTILEVER_BETA1_L ** 2 * math.sqrt(ei / (rho_a * length_m ** 4))


def tip_mass_frequency_rad_s(d_m: float, length_m: float, mass_kg: float,
                             e_pa: float = STEEL_E_PA) -> float:
    """Massless shaft with a lumped mass at the tip: w = sqrt(3 E I / (m L^3))."""
    ei = e_pa * second_moment_m4(d_m)
    return math.sqrt(3.0 * ei / (mass_kg * length_m ** 3))


def dunkerley_critical_speed_rad_s(d_m: float, length_m: float, tip_mass_kg: float,
                                   e_pa: float = STEEL_E_PA,
                                   density: float = STEEL_DENSITY,
                                   extra_masses=None) -> float:
    """1/w^2 = 1/w_shaft^2 + 1/w_tip^2 (+ 1/w_extra^2 for every extra mass)."""
    inv = 1.0 / shaft_alone_frequency_rad_s(d_m, length_m, e_pa, density) ** 2
    inv += 1.0 / tip_mass_frequency_rad_s(d_m, length_m, tip_mass_kg, e_pa) ** 2

    if extra_masses:
        ei = e_pa * second_moment_m4(d_m)
        for mass, pos in extra_masses:
            # flexibility at the load point: a^3 / (3 E I), so w^2 = 3 E I / (m a^3)
            inv += mass * pos ** 3 / (3.0 * ei)
    return math.sqrt(1.0 / inv)


# ---------------------------------------------------------------------------
# 3. Closed form: tip mass + distributed shaft mass
# ---------------------------------------------------------------------------
def closed_form_critical_speed_rad_s(d_m: float, length_m: float, tip_mass_kg: float,
                                     e_pa: float = STEEL_E_PA,
                                     density: float = STEEL_DENSITY) -> float:
    ei = e_pa * second_moment_m4(d_m)
    m_eq = tip_mass_kg + EQUIVALENT_MASS_FRACTION * shaft_mass_kg(d_m, length_m, density)
    return math.sqrt(3.0 * ei / (m_eq * length_m ** 3))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def critical_speed_rpm(d_m: float, length_m: float, tip_mass_kg: float,
                       e_pa: float = STEEL_E_PA, density: float = STEEL_DENSITY,
                       method: str = "rayleigh", extra_masses=None,
                       added_mass_kg: float = 0.0) -> float:
    """
    method : "rayleigh", "dunkerley" or "closed_form"
        added_mass_kg : liquid mass carried by the impeller (hydrodynamic added mass). Default 0 matches
                    the .NET engine, which calculates critical speed in air; a real value lowers the result.
    """
    m_tip = tip_mass_kg + added_mass_kg
    if method == "rayleigh":
        w = rayleigh_critical_speed_rad_s(d_m, length_m, m_tip, e_pa, density, extra_masses)
    elif method == "dunkerley":
        w = dunkerley_critical_speed_rad_s(d_m, length_m, m_tip, e_pa, density, extra_masses)
    elif method == "closed_form":
        w = closed_form_critical_speed_rad_s(d_m, length_m, m_tip, e_pa, density)
    else:
        raise ValueError("method must be 'rayleigh', 'dunkerley' or 'closed_form'")
    return rad_s_to_rpm(w)


def speed_ratio(operating_rpm: float, critical_rpm: float) -> float:
    """Operating speed divided by the first critical speed."""
    return operating_rpm / critical_rpm


def is_speed_safe(operating_rpm: float, critical_rpm: float,
                  max_ratio: float = 0.7, min_ratio_supercritical: float = None) -> bool:
    """
    Subcritical : ratio <= max_ratio (same rule as AgitatorCalculationService.cs)
    Supercritical : only when min_ratio_supercritical is given (e.g. 1.3); the shaft must pass
    through resonance, so this is opt-in and never used by the .NET engine.
    """
    r = speed_ratio(operating_rpm, critical_rpm)
    if r <= max_ratio:
        return True
    return min_ratio_supercritical is not None and r >= min_ratio_supercritical


def minimum_diameter_for_critical_speed_m(operating_rpm: float, length_m: float,
                                          tip_mass_kg: float, max_ratio: float = 0.7,
                                          e_pa: float = STEEL_E_PA,
                                          density: float = STEEL_DENSITY,
                                          method: str = "rayleigh",
                                          extra_masses=None,
                                          added_mass_kg: float = 0.0) -> float:
    """Smallest diameter that keeps operating speed <= max_ratio * critical speed."""
    def ok(d: float) -> bool:
        crit = critical_speed_rpm(d, length_m, tip_mass_kg, e_pa, density,
                                  method, extra_masses, added_mass_kg)
        return operating_rpm <= max_ratio * crit

    lo, hi = D_MIN_SEARCH_M, D_MAX_SEARCH_M
    if not ok(hi):
        return math.inf
    if ok(lo):
        return lo
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


# ---------------------------------------------------------------------------
# Torch version (differentiable) for the PINN diameter head
# ---------------------------------------------------------------------------
def torch_critical_speed_rpm(d_m, length_m, tip_mass_kg,
                             e_pa: float = STEEL_E_PA,
                             density: float = STEEL_DENSITY):
    """Closed-form critical speed (rpm) on tensors; feeds design_constraint_loss."""
    import torch

    ei = e_pa * math.pi * d_m ** 4 / 64.0
    shaft_m = density * math.pi * d_m ** 2 / 4.0 * length_m
    m_eq = tip_mass_kg + EQUIVALENT_MASS_FRACTION * shaft_m
    omega = torch.sqrt(3.0 * ei / (m_eq * length_m ** 3))
    return omega * 60.0 / (2.0 * math.pi)
