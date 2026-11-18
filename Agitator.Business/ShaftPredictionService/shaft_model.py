"""
shaft_model.py

Physics-informed neural network for the agitator shaft.

Two networks share one input vector p (physical units, 8 columns, see PARAM_NAMES):

    field network     (xi, p) -> [w, theta, M, V, phi]   non-dimensional beam and torsion fields
    diameter network  p       -> raw shaft diameter (m), before standard-size rounding

The field network satisfies the cantilever boundary conditions exactly (hard constraints):
    xi = 0 : w = theta = phi = 0
    xi = 1 : M = 0, V = 1
and is trained on the beam / torsion equations in pinn_loss.py plus FEA data.

The diameter network is trained so that, at the diameter it predicts, the fields coming from the
field network give stresses, deflection and critical speed that just pass every design limit
(see evaluate_design and pinn_loss.design_constraint_loss). The Km = 1.5 diameter formula in
shaft_physics.py is NOT used to train it, so it is an independent check of that formula.

Units: SI everywhere except allowable shear (MPa) and elastic modulus (GPa) inside p.
"""

import math

import torch
import torch.nn as nn

import pinn_loss as pl
from shaft_critical_speed import torch_critical_speed_rpm
from shaft_physics import (
    BENDING_FACTOR_KM,
    TORSION_FACTOR_KT,
    RADIAL_FORCE_FRACTION,
    MOTOR_SERVICE_FACTOR,
    EFFECTIVE_RADIUS_FRACTION,
    SHAFT_LENGTH_FACTOR,
    TRUSTED_TORQUE_NM,
    TRUSTED_IMPELLER_DIAMETER_M,
    TRUSTED_LIQUID_HEIGHT_M,
)

# Allowable shear stress range (MPa) used for training. Must match service.py.
TRUSTED_ALLOWABLE_SHEAR_MPA = (15.0, 250.0)

# Design limits. Must match appsettings.json section ShaftComparison.
DEFLECTION_LIMIT_RATIO = 0.001      # tip deflection <= ratio x shaft length
SPEED_RATIO_LIMIT = 0.7             # operating speed <= ratio x first critical speed

DEFAULT_NU = 0.30                   # same as fea_dataset_generator.py

# Output range of the diameter network (metres). Covers the whole trusted input range.
D_MIN_M = 0.001
D_MAX_M = 50.0

# ---------------------------------------------------------------------------
# Design parameter vector p
# ---------------------------------------------------------------------------
PARAM_NAMES = (
    "rated_torque_nm",
    "impeller_diameter_m",
    "liquid_height_m",
    "allowable_shear_mpa",
    "operating_speed_rpm",
    "impeller_mass_kg",
    "elastic_modulus_gpa",
    "density_kg_m3",
)
N_PARAMS = len(PARAM_NAMES)

PARAM_RANGES = (
    TRUSTED_TORQUE_NM,
    TRUSTED_IMPELLER_DIAMETER_M,
    TRUSTED_LIQUID_HEIGHT_M,
    TRUSTED_ALLOWABLE_SHEAR_MPA,
    (1.0, 3000.0),
    (1.0, 50000.0),
    (100.0, 250.0),
    (7000.0, 8200.0),
)

PARAM_DEFAULTS = {
    "allowable_shear_mpa": 40.0,
    "operating_speed_rpm": 60.0,
    "impeller_mass_kg": 20.0,
    "elastic_modulus_gpa": 210.0,
    "density_kg_m3": 7850.0,
}


def _column(value, dtype, device):
    return torch.as_tensor(value, dtype=dtype, device=device).reshape(-1)


def make_params(rated_torque_nm, impeller_diameter_m, liquid_height_m,
                allowable_shear_mpa=None, operating_speed_rpm=None,
                impeller_mass_kg=None, elastic_modulus_gpa=None,
                density_kg_m3=None, dtype=torch.float32, device=None) -> torch.Tensor:
    """Physical inputs (floats or tensors) -> raw parameter tensor [N, N_PARAMS]."""
    given = {
        "rated_torque_nm": rated_torque_nm,
        "impeller_diameter_m": impeller_diameter_m,
        "liquid_height_m": liquid_height_m,
        "allowable_shear_mpa": allowable_shear_mpa,
        "operating_speed_rpm": operating_speed_rpm,
        "impeller_mass_kg": impeller_mass_kg,
        "elastic_modulus_gpa": elastic_modulus_gpa,
        "density_kg_m3": density_kg_m3,
    }
    cols = []
    for name in PARAM_NAMES:
        value = given[name]
        if value is None:
            value = PARAM_DEFAULTS[name]
        cols.append(_column(value, dtype, device))
    cols = torch.broadcast_tensors(*cols)
    return torch.stack(cols, dim=1)


# ---------------------------------------------------------------------------
# Torch version of the Km = 1.5 formula (reference only, not used for training)
# ---------------------------------------------------------------------------
def torch_raw_diameter_m(torque_nm, imp_d_m, liq_h_m, allowable_shear_pa):
    eq_t = torch_equivalent_torque(torque_nm, imp_d_m, liq_h_m)
    return torch.pow((16.0 * eq_t) / (math.pi * allowable_shear_pa), 1.0 / 3.0)


def torch_equivalent_torque(torque_nm, imp_d_m, liq_h_m):
    design_t = torque_nm * MOTOR_SERVICE_FACTOR
    eff_r = EFFECTIVE_RADIUS_FRACTION * imp_d_m / 2.0
    radial_f = RADIAL_FORCE_FRACTION * (design_t / eff_r)
    moment = radial_f * (liq_h_m * SHAFT_LENGTH_FACTOR)
    return torch.sqrt((BENDING_FACTOR_KM * moment) ** 2 + (TORSION_FACTOR_KT * design_t) ** 2)


def torch_shear_stress_pa(torque_nm, imp_d_m, liq_h_m, shaft_d_m):
    eq_t = torch_equivalent_torque(torque_nm, imp_d_m, liq_h_m)
    return (16.0 * eq_t) / (math.pi * shaft_d_m ** 3)


# ---------------------------------------------------------------------------
# Input scaler (log space, fixed to the trusted ranges so it is saved with the model)
# ---------------------------------------------------------------------------
class ParamScaler(nn.Module):
    def __init__(self):
        super().__init__()
        lo = torch.tensor([r[0] for r in PARAM_RANGES], dtype=torch.float32)
        hi = torch.tensor([r[1] for r in PARAM_RANGES], dtype=torch.float32)
        self.register_buffer("lo", lo)
        self.register_buffer("hi", hi)
        self.register_buffer("log_lo", torch.log(lo))
        self.register_buffer("log_hi", torch.log(hi))

    def forward(self, p):
        x = torch.maximum(torch.minimum(p, self.hi), self.lo)
        return 2.0 * (torch.log(x) - self.log_lo) / (self.log_hi - self.log_lo) - 1.0


def _mlp(n_in: int, n_out: int, hidden: int, n_layers: int) -> nn.Sequential:
    layers = [nn.Linear(n_in, hidden), nn.Tanh()]
    for _ in range(n_layers - 1):
        layers += [nn.Linear(hidden, hidden), nn.Tanh()]
    layers += [nn.Linear(hidden, n_out)]
    return nn.Sequential(*layers)


# ---------------------------------------------------------------------------
# PINN
# ---------------------------------------------------------------------------
class ShaftPINN(nn.Module):
    def __init__(self, hidden: int = 64, n_layers: int = 4):
        super().__init__()
        self.scaler = ParamScaler()
        self.field_trunk = _mlp(1 + N_PARAMS, 5, hidden, n_layers)
        self.diameter_trunk = _mlp(N_PARAMS, 1, hidden, n_layers)

    def field_net(self, xi: torch.Tensor, p: torch.Tensor) -> torch.Tensor:
        """
        xi : [N, 1] position along the shaft, xi = x / L in [0, 1]
        p  : [N, N_PARAMS] raw design parameters (physical units)
        returns [N, 5] = (w, theta, M, V, phi), non-dimensional, boundary conditions exact
        """
        inp = torch.cat([2.0 * xi - 1.0, self.scaler(p)], dim=1)
        r = self.field_trunk(inp)
        w = xi ** 2 * r[:, 0:1]
        theta = xi * r[:, 1:2]
        m = (1.0 - xi) * r[:, 2:3]
        v = 1.0 + (1.0 - xi) * r[:, 3:4]
        phi = xi * r[:, 4:5]
        return torch.cat([w, theta, m, v, phi], dim=1)

    def diameter_m(self, p: torch.Tensor) -> torch.Tensor:
        """p : [N, N_PARAMS] raw parameters. Returns [N, 1] raw shaft diameter in metres."""
        log_lo = math.log(D_MIN_M)
        log_hi = math.log(D_MAX_M)
        log_d = log_lo + (log_hi - log_lo) * torch.sigmoid(self.diameter_trunk(self.scaler(p)))
        return torch.exp(log_d)

    def forward(self, xi: torch.Tensor, p: torch.Tensor) -> torch.Tensor:
        return self.field_net(xi, p)


# ---------------------------------------------------------------------------
# Design evaluation: fields -> dimensional quantities at a given diameter
# ---------------------------------------------------------------------------
def evaluate_design(model: ShaftPINN, p: torch.Tensor, d_m=None, nu: float = DEFAULT_NU) -> dict:
    """
    Dimensional results for every row of p. d_m is the shaft diameter to check; when it is
    None the diameter network is used. All tensors are [N, 1] and stay differentiable.
    """
    torque = p[:, 0:1]
    imp_d = p[:, 1:2]
    height = p[:, 2:3]
    allowable_mpa = p[:, 3:4]
    speed_rpm = p[:, 4:5]
    mass_kg = p[:, 5:6]
    e_pa = p[:, 6:7] * 1e9
    density = p[:, 7:8]

    if d_m is None:
        d = model.diameter_m(p)
    else:
        d = torch.as_tensor(d_m, dtype=p.dtype, device=p.device).reshape(-1, 1)
        d = d.expand(p.shape[0], 1)

    design_t = torque * MOTOR_SERVICE_FACTOR
    eff_r = EFFECTIVE_RADIUS_FRACTION * imp_d / 2.0
    force = RADIAL_FORCE_FRACTION * design_t / eff_r
    length = height * SHAFT_LENGTH_FACTOR
    g_pa = e_pa / (2.0 * (1.0 + nu))

    root = model.field_net(torch.zeros_like(torque), p)
    tip = model.field_net(torch.ones_like(torque), p)

    moment = root[:, pl.M:pl.M + 1].abs() * force * length
    tip_deflection = tip[:, pl.W:pl.W + 1].abs() * force * length ** 3 / (e_pa * pl.second_moment_m4(d))
    tip_twist = tip[:, pl.PHI:pl.PHI + 1].abs() * design_t * length / (g_pa * pl.polar_moment_m4(d))

    return {
        "d_m": d,
        "length_m": length,
        "force_n": force,
        "design_torque_nm": design_t,
        "allowable_shear_pa": allowable_mpa * 1e6,
        "root_moment_nm": moment,
        "tip_deflection_m": tip_deflection,
        "tip_twist_rad": tip_twist,
        "tau_max_pa": pl.max_shear_stress_pa(moment, design_t, d),
        "von_mises_pa": pl.von_mises_stress_pa(moment, design_t, d),
        "critical_speed_rpm": torch_critical_speed_rpm(d, length, mass_kg, e_pa, density),
        "speed_rpm": speed_rpm,
    }


def constraint_batch(ev: dict) -> dict:
    """Keyword arguments for pinn_loss.design_constraint_loss from evaluate_design output."""
    return {
        "tau_max_pa": ev["tau_max_pa"],
        "allowable_shear_pa": ev["allowable_shear_pa"],
        "tip_deflection_m": ev["tip_deflection_m"],
        "deflection_limit_m": DEFLECTION_LIMIT_RATIO * ev["length_m"],
        "speed_rpm": ev["speed_rpm"],
        "critical_speed_rpm": ev["critical_speed_rpm"],
        "speed_ratio_limit": SPEED_RATIO_LIMIT,
    }


def pde_residual_value(model: ShaftPINN, p: torch.Tensor, n_points: int = 33) -> float:
    """Mean PDE residual of the field network for one design p [1, N_PARAMS]."""
    with torch.enable_grad():
        xi = torch.linspace(0.0, 1.0, n_points, dtype=p.dtype, device=p.device).reshape(-1, 1)
        xi = xi.requires_grad_(True)
        rows = p[0:1].expand(n_points, -1)
        loss, _ = pl.pde_residual_loss(model.field_net, xi, rows)
    return float(loss.detach())