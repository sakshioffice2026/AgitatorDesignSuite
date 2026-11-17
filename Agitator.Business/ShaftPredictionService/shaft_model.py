"""
shaft_model.py

Physics-guided neural network for agitator shaft diameter.

The network predicts the shaft diameter from the three inputs ALONE; the formula is
not called inside forward(). However, the training loss uses the same stress equation,
so the network is a fast learned surrogate of that formula, NOT an independent
validation of the design.

It is trained only on physics rules (see physics_loss):
    - the shear stress at the predicted diameter must equal the allowable stress
    - the diameter must grow with torque and liquid height, and fall as impeller grows
Real measured data can be added later through data_loss.

Inputs  (physical units): rated_torque_nm, impeller_diameter_m, liquid_height_m
Output  (physical units): raw shaft diameter in metres (before standard-size rounding)
"""

import math

import torch
import torch.nn as nn

from shaft_physics import (
    ALLOWABLE_SHEAR_STRESS_PA,
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

MAX_CORRECTION = 0.10  # agreement tolerance (+/- 10 %) used by sanity_check, not inside the model

# Output range of the network (metres). Covers the whole trusted input range.
D_MIN_M = 0.004
D_MAX_M = 3.0


# ---------------------------------------------------------------------------
# Torch version of the formula (differentiable)
# ---------------------------------------------------------------------------
def torch_raw_diameter_m(torque_nm, imp_d_m, liq_h_m):
    design_t = torque_nm * MOTOR_SERVICE_FACTOR
    eff_r = EFFECTIVE_RADIUS_FRACTION * imp_d_m / 2.0
    radial_f = RADIAL_FORCE_FRACTION * (design_t / eff_r)
    moment = radial_f * (liq_h_m * SHAFT_LENGTH_FACTOR)
    eq_t = torch.sqrt((BENDING_FACTOR_KM * moment) ** 2 + (TORSION_FACTOR_KT * design_t) ** 2)
    return torch.pow((16.0 * eq_t) / (math.pi * ALLOWABLE_SHEAR_STRESS_PA), 1.0 / 3.0)


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
# Input scaler (log space, fixed to trusted ranges so it is saved with the model)
# ---------------------------------------------------------------------------
class LogRangeScaler(nn.Module):
    def __init__(self):
        super().__init__()
        lo = torch.log(torch.tensor([TRUSTED_TORQUE_NM[0],
                                     TRUSTED_IMPELLER_DIAMETER_M[0],
                                     TRUSTED_LIQUID_HEIGHT_M[0]]))
        hi = torch.log(torch.tensor([TRUSTED_TORQUE_NM[1],
                                     TRUSTED_IMPELLER_DIAMETER_M[1],
                                     TRUSTED_LIQUID_HEIGHT_M[1]]))
        self.register_buffer("lo", lo)
        self.register_buffer("hi", hi)

    def forward(self, x):
        return 2.0 * (torch.log(x) - self.lo) / (self.hi - self.lo) - 1.0


# ---------------------------------------------------------------------------
# PINN
# ---------------------------------------------------------------------------
class ShaftPINN(nn.Module):
    def __init__(self, hidden: int = 64, n_layers: int = 4):
        super().__init__()
        self.scaler = LogRangeScaler()

        layers = [nn.Linear(3, hidden), nn.Tanh()]
        for _ in range(n_layers - 1):
            layers += [nn.Linear(hidden, hidden), nn.Tanh()]
        layers += [nn.Linear(hidden, 1)]
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        """
        x: tensor [N, 3] = (rated_torque_nm, impeller_diameter_m, liquid_height_m)
        returns: tensor [N, 1] raw shaft diameter in metres (no formula used here)
        """
        log_lo = math.log(D_MIN_M)
        log_hi = math.log(D_MAX_M)
        log_d = log_lo + (log_hi - log_lo) * torch.sigmoid(self.net(self.scaler(x)))
        return torch.exp(log_d)


# ---------------------------------------------------------------------------
# Physics loss terms
# ---------------------------------------------------------------------------
def physics_loss(model: ShaftPINN, x: torch.Tensor):
    """
    Penalties:
      1. stress     : shear stress at the predicted diameter must EQUAL the allowable
                      stress (log residual, so too thin and too thick are both penalised)
      2. torque     : d must not decrease when torque increases   (monotonic)
      3. impeller   : d must not decrease when impeller diameter decreases
                      (smaller impeller -> larger tangential force)
      4. height     : d must not decrease when liquid height increases
      5. positive   : d must be > 0
    Trends are measured in log space (d log d / d log x) so all inputs have the same scale.
    Returns (total, dict of parts).
    """
    x = x.clone()
    log_x = torch.log(x).requires_grad_(True)
    d = model(torch.exp(log_x))
    xe = torch.exp(log_x)

    # 1. stress balance (physics residual)
    stress = torch_shear_stress_pa(xe[:, 0:1], xe[:, 1:2], xe[:, 2:3], d)
    stress_pen = torch.log(stress / ALLOWABLE_SHEAR_STRESS_PA).pow(2).mean()

    # 2-4. monotonic trends through autograd (log-log slopes)
    grad = torch.autograd.grad(torch.log(d).sum(), log_x, create_graph=True)[0]
    d_dtorque, d_dimp, d_dheight = grad[:, 0], grad[:, 1], grad[:, 2]
    torque_pen = torch.relu(-d_dtorque).pow(2).mean()
    imp_pen = torch.relu(d_dimp).pow(2).mean()
    height_pen = torch.relu(-d_dheight).pow(2).mean()

    # 5. positivity
    pos_pen = torch.relu(-d).pow(2).mean()

    total = stress_pen + torque_pen + imp_pen + height_pen + pos_pen
    parts = {
        "stress": float(stress_pen),
        "torque_trend": float(torque_pen),
        "impeller_trend": float(imp_pen),
        "height_trend": float(height_pen),
        "positive": float(pos_pen),
    }
    return total, parts


def data_loss(model: ShaftPINN, x: torch.Tensor, d_target_m: torch.Tensor):
    """Relative error against targets (formula points and/or real measured data)."""
    d = model(x)
    return ((d - d_target_m) / d_target_m).pow(2).mean()