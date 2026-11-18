"""
pinn_loss.py

Loss functions for the physics-informed shaft network (PINN).

These losses come from beam / torsion mechanics, NOT from the diameter formula in
shaft_physics.py, so the trained network is an independent check of that formula.

Shaft model: cantilever (fixed at the drive end, impeller load at the free end),
the same load path the formula assumes (bending moment = radial force x shaft length).

Field network contract
----------------------
    field_net(xi, p) -> tensor [N, 5]
        xi : [N, 1] normalised position along the shaft, xi = x / L, in [0, 1]
        p  : [N, k] normalised design parameters (any width; passed through untouched)
        columns = (w, theta, M, V, phi)

All five fields are NON-DIMENSIONAL:
    w     = deflection        / (F * L^3 / (E * I))
    theta = slope             / (F * L^2 / (E * I))
    M     = bending moment    / (F * L)
    V     = shear force       / F
    phi   = angle of twist    / (T * L / (G * J))

Governing equations (Euler-Bernoulli beam + Saint-Venant torsion, no distributed load):
    E I w''''  = 0   written as first-order system
        w'   = theta
        theta' = M
        M'   = V
        V'   = 0
    phi' = 1

Boundary conditions (cantilever, tip load F, torque T):
    xi = 0 : w = 0, theta = 0, phi = 0
    xi = 1 : M = 0, V = 1

Closed-form check (used by accuracy_report.py):
    tip deflection = -1/3      (non-dimensional)  ->  F L^3 / (3 E I)
    root moment    = -1
    tip twist      =  1        ->  T L / (G J)
"""

from dataclasses import dataclass
import math

import torch

W, THETA, M, V, PHI = 0, 1, 2, 3, 4


@dataclass
class LossWeights:
    pde: float = 1.0
    bc: float = 10.0
    data: float = 1.0
    constraint: float = 1.0
    tightness: float = 0.1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ddx(y: torch.Tensor, xi: torch.Tensor) -> torch.Tensor:
    return torch.autograd.grad(
        y, xi, grad_outputs=torch.ones_like(y), create_graph=True
    )[0]


def sample_xi(n_points: int, device=None, dtype=torch.float32) -> torch.Tensor:
    """Random collocation points along the shaft, xi in (0, 1), gradient enabled."""
    xi = torch.rand(n_points, 1, device=device, dtype=dtype)
    return xi.requires_grad_(True)


def _boundary_xi(p: torch.Tensor, value: float) -> torch.Tensor:
    xi = torch.full((p.shape[0], 1), value, device=p.device, dtype=p.dtype)
    return xi.requires_grad_(True)


# ---------------------------------------------------------------------------
# 1. PDE residual loss
# ---------------------------------------------------------------------------
def pde_residual_loss(field_net, xi: torch.Tensor, p: torch.Tensor):
    """
    Residuals of the first-order beam + torsion system at collocation points.
    xi must come from sample_xi (requires_grad = True).
    """
    out = field_net(xi, p)
    w, th, m, v, phi = (out[:, i:i + 1] for i in range(5))

    r_w = _ddx(w, xi) - th          # w'     = theta
    r_th = _ddx(th, xi) - m         # theta' = M
    r_m = _ddx(m, xi) - v           # M'     = V
    r_v = _ddx(v, xi)               # V'     = 0
    r_phi = _ddx(phi, xi) - 1.0     # phi'   = 1

    parts = {
        "pde_slope": r_w.pow(2).mean(),
        "pde_curvature": r_th.pow(2).mean(),
        "pde_moment": r_m.pow(2).mean(),
        "pde_shear": r_v.pow(2).mean(),
        "pde_torsion": r_phi.pow(2).mean(),
    }
    return sum(parts.values()), parts


# ---------------------------------------------------------------------------
# 2. Boundary condition loss
# ---------------------------------------------------------------------------
def boundary_condition_loss(field_net, p: torch.Tensor):
    """Cantilever boundary conditions for every design parameter row in p."""
    out0 = field_net(_boundary_xi(p, 0.0), p)   # fixed end
    out1 = field_net(_boundary_xi(p, 1.0), p)   # free end

    parts = {
        "bc_w0": out0[:, W].pow(2).mean(),
        "bc_theta0": out0[:, THETA].pow(2).mean(),
        "bc_phi0": out0[:, PHI].pow(2).mean(),
        "bc_M1": out1[:, M].pow(2).mean(),
        "bc_V1": (out1[:, V] - 1.0).pow(2).mean(),
    }
    return sum(parts.values()), parts


# ---------------------------------------------------------------------------
# 3. Data loss (FEA ground truth, or published benchmark values)
# ---------------------------------------------------------------------------
def data_loss(field_net, xi_data: torch.Tensor, p_data: torch.Tensor,
              y_data: torch.Tensor, mask: torch.Tensor = None):
    """
    xi_data : [N, 1] positions where FEA values are known
    p_data  : [N, k] design parameters for each row
    y_data  : [N, 5] non-dimensional (w, theta, M, V, phi) from FEA
    mask    : [N, 5] optional 0/1 mask for columns FEA did not provide
    """
    pred = field_net(xi_data, p_data)
    err = (pred - y_data).pow(2)
    if mask is not None:
        err = err * mask
        return err.sum() / mask.sum().clamp(min=1.0)
    return err.mean()


# ---------------------------------------------------------------------------
# 4. Dimensional conversion and stresses
# ---------------------------------------------------------------------------
def second_moment_m4(d_m: torch.Tensor) -> torch.Tensor:
    return math.pi * d_m ** 4 / 64.0


def polar_moment_m4(d_m: torch.Tensor) -> torch.Tensor:
    return math.pi * d_m ** 4 / 32.0


def to_dimensional(fields: torch.Tensor, force_n, torque_nm, length_m,
                   e_pa, g_pa, d_m) -> dict:
    """Scale the non-dimensional fields [N, 5] back to SI units."""
    ei = e_pa * second_moment_m4(d_m)
    gj = g_pa * polar_moment_m4(d_m)
    return {
        "deflection_m": fields[:, W:W + 1] * force_n * length_m ** 3 / ei,
        "slope_rad": fields[:, THETA:THETA + 1] * force_n * length_m ** 2 / ei,
        "moment_nm": fields[:, M:M + 1] * force_n * length_m,
        "shear_n": fields[:, V:V + 1] * force_n,
        "twist_rad": fields[:, PHI:PHI + 1] * torque_nm * length_m / gj,
    }


def max_shear_stress_pa(moment_nm, torque_nm, d_m) -> torch.Tensor:
    """Maximum shear stress (Tresca) from combined bending and torsion."""
    sigma_b = 32.0 * moment_nm.abs() / (math.pi * d_m ** 3)
    tau_t = 16.0 * torque_nm / (math.pi * d_m ** 3)
    return torch.sqrt((sigma_b / 2.0) ** 2 + tau_t ** 2)


def von_mises_stress_pa(moment_nm, torque_nm, d_m) -> torch.Tensor:
    sigma_b = 32.0 * moment_nm.abs() / (math.pi * d_m ** 3)
    tau_t = 16.0 * torque_nm / (math.pi * d_m ** 3)
    return torch.sqrt(sigma_b ** 2 + 3.0 * tau_t ** 2)


# ---------------------------------------------------------------------------
# 5. Design constraint loss (diameter head)
# ---------------------------------------------------------------------------
def design_constraint_loss(tau_max_pa, allowable_shear_pa,
                           tip_deflection_m, deflection_limit_m,
                           speed_rpm, critical_speed_rpm,
                           speed_ratio_limit: float = 0.7,
                           tightness_weight: float = 0.1):
    """
    Feasibility (hinge, squared):
        stress     : tau_max          <= allowable shear stress
        deflection : |tip deflection| <= deflection limit
        speed      : operating rpm    <= speed_ratio_limit * critical rpm

    Tightness: the governing utilisation (largest of the three) should be 1.0,
    so the pre-rounding diameter is the smallest one that passes every check.
    """
    u_stress = tau_max_pa / allowable_shear_pa
    u_defl = tip_deflection_m.abs() / deflection_limit_m
    u_speed = speed_rpm / (speed_ratio_limit * critical_speed_rpm)

    feasibility = (
        torch.relu(u_stress - 1.0).pow(2).mean()
        + torch.relu(u_defl - 1.0).pow(2).mean()
        + torch.relu(u_speed - 1.0).pow(2).mean()
    )

    governing = torch.maximum(torch.maximum(u_stress, u_defl), u_speed)
    tightness = (torch.log(governing)).pow(2).mean()

    parts = {
        "constraint_feasibility": feasibility,
        "constraint_tightness": tightness,
    }
    return feasibility + tightness_weight * tightness, parts


# ---------------------------------------------------------------------------
# 6. Total loss
# ---------------------------------------------------------------------------
def total_loss(field_net, xi: torch.Tensor, p: torch.Tensor,
               weights: LossWeights = None,
               data_batch: dict = None,
               constraint_batch: dict = None):
    """
    xi, p            : collocation points and design parameters
    data_batch       : optional {"xi", "p", "y", "mask"} FEA / benchmark data
    constraint_batch : optional kwargs for design_constraint_loss
    Returns (total, dict of float parts).
    """
    weights = weights or LossWeights()

    pde, pde_parts = pde_residual_loss(field_net, xi, p)
    bc, bc_parts = boundary_condition_loss(field_net, p)
    total = weights.pde * pde + weights.bc * bc

    parts = {"pde": pde, "bc": bc}
    parts.update(pde_parts)
    parts.update(bc_parts)

    if data_batch is not None:
        dl = data_loss(field_net, data_batch["xi"], data_batch["p"],
                       data_batch["y"], data_batch.get("mask"))
        total = total + weights.data * dl
        parts["data"] = dl

    if constraint_batch is not None:
        cl, cparts = design_constraint_loss(
            tightness_weight=weights.tightness, **constraint_batch
        )
        total = total + weights.constraint * cl
        parts["constraint"] = cl
        parts.update(cparts)

    return total, {k: float(v) for k, v in parts.items()}
