"""
sanity_check.py

Physical checks for a trained shaft PINN checkpoint (.pth).
Run BEFORE exporting or deploying. Any FAIL = do not deploy.

Designs are sampled inside the checkpoint's trusted_ranges (the ranges the network was
trained on and the ranges service.py accepts), not the wider PARAM_RANGES.

Colab:
    !python sanity_check.py /content/drive/MyDrive/shaft_model.pth
"""

import sys

import torch

import pinn_loss as pl
from shaft_model import (
    D_MAX_M,
    D_MIN_M,
    DEFLECTION_LIMIT_RATIO,
    PARAM_NAMES,
    PARAM_RANGES,
    SPEED_RATIO_LIMIT,
    ShaftPINN,
    evaluate_design,
    make_params,
    torch_raw_diameter_m,
)
from shaft_physics import analytic_diameter_m, round_up_to_standard_size_m

N_POINTS = 5000
SEED = 123

BC_TOLERANCE = 1e-6             # boundary conditions are hard constraints, must be exact
FIELD_TOLERANCE = 0.05          # allowed error on the closed-form cantilever fields
MAX_PDE_RESIDUAL = 1e-3         # mean PDE residual of the field network
TREND_TOLERANCE = 0.02          # allowed noise in d(log diameter) / d(log input)
GOVERNING_RANGE = (0.8, 1.1)    # governing utilisation of the unrounded diameter
MAX_BAD_FRACTION = 0.01         # up to 1 % of points may violate a soft check
MAX_LOOSE_FRACTION = 0.05       # up to 5 % of points may sit outside GOVERNING_RANGE
MAX_TREND_BAD_FRACTION = 0.05   # up to 5 % of points may have a wrong trend slope (network wobble)

# (input name, +1 diameter rises / -1 diameter falls, label)
TRENDS = (
    ("rated_torque_nm", 1, "Diameter rises with torque"),
    ("impeller_diameter_m", -1, "Diameter falls as impeller diameter rises"),
    ("liquid_height_m", 1, "Diameter rises with liquid height"),
    ("allowable_shear_mpa", -1, "Diameter falls as allowable stress rises"),
    ("operating_speed_rpm", 1, "Diameter rises with operating speed"),
    ("impeller_mass_kg", 1, "Diameter rises with impeller mass"),
    ("elastic_modulus_gpa", -1, "Diameter falls as elastic modulus rises"),
)


def load_model(path):
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    if ckpt.get("trusted_ranges"):
        ranges = [(float(lo), float(hi)) for lo, hi in ckpt["trusted_ranges"]]
    else:
        ranges = [(float(lo), float(hi)) for lo, hi in PARAM_RANGES]
    return model, ranges


def log_uniform(n, lo, hi):
    u = torch.rand(n, 1)
    log_lo = torch.log(torch.tensor(lo))
    log_hi = torch.log(torch.tensor(hi))
    return torch.exp(log_lo + u * (log_hi - log_lo))


def sample(n, ranges):
    return torch.cat([log_uniform(n, lo, hi) for lo, hi in ranges], dim=1)


def report(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} {detail}")
    return ok


def info(name, detail=""):
    print(f"[INFO] {name} {detail}")


def main(path):
    torch.manual_seed(SEED)
    model, ranges = load_model(path)
    x = sample(N_POINTS, ranges)
    results = []

    for name, (lo, hi) in zip(PARAM_NAMES, ranges):
        info(f"range {name}", f"({lo:g} to {hi:g})")

    zeros = torch.zeros(N_POINTS, 1)
    ones = torch.ones(N_POINTS, 1)

    with torch.no_grad():
        d = model.diameter_m(x)
        f0 = model.field_net(zeros, x)
        f1 = model.field_net(ones, x)
        ev = evaluate_design(model, x)
        d_round = torch.tensor(
            [[round_up_to_standard_size_m(v)] for v in d.flatten().tolist()], dtype=x.dtype
        )
        ev_round = evaluate_design(model, x, d_m=d_round)

    # 1. NaN / inf
    finite = all(bool(torch.isfinite(t).all()) for t in (
        d, f0, f1, ev["tau_max_pa"], ev["tip_deflection_m"], ev["tip_twist_rad"],
        ev["critical_speed_rpm"]))
    results.append(report("No NaN/inf in outputs", finite))

    # 2. Diameter inside the network output range
    results.append(report(
        "Diameter > 0 and inside output range",
        bool(((d >= D_MIN_M) & (d <= D_MAX_M)).all()),
        f"({D_MIN_M * 1000:.0f} mm to {D_MAX_M * 1000:.0f} mm)",
    ))

    # 3. Cantilever boundary conditions (w = theta = phi = 0 at xi = 0, M = 0, V = 1 at xi = 1)
    bc_err = max(
        float(f0[:, pl.W].abs().max()),
        float(f0[:, pl.THETA].abs().max()),
        float(f0[:, pl.PHI].abs().max()),
        float(f1[:, pl.M].abs().max()),
        float((f1[:, pl.V] - 1.0).abs().max()),
    )
    results.append(report("Boundary conditions exact", bc_err <= BC_TOLERANCE,
                          f"(max error {bc_err:.2e})"))

    # 4. Fields agree with the closed-form cantilever solution: M(0) = -1, w(1) = -1/3, phi(1) = 1
    err_m = (f0[:, pl.M] + 1.0).abs()
    err_w = (f1[:, pl.W] + 1.0 / 3.0).abs() / (1.0 / 3.0)
    err_phi = (f1[:, pl.PHI] - 1.0).abs()
    worst = torch.maximum(torch.maximum(err_m, err_w), err_phi)
    bad_f = float((worst > FIELD_TOLERANCE).float().mean())
    results.append(report(
        "Fields match closed-form cantilever (M, w, phi)",
        bad_f <= MAX_BAD_FRACTION,
        f"(violations {bad_f * 100:.2f} %, max error {float(worst.max()) * 100:.2f} %, "
        f"limit {FIELD_TOLERANCE * 100:.0f} %)",
    ))

    # 5. PDE residual
    xi = pl.sample_xi(N_POINTS)
    pde, _ = pl.pde_residual_loss(model.field_net, xi, x)
    pde_value = float(pde.detach())
    results.append(report("PDE residual small", pde_value <= MAX_PDE_RESIDUAL,
                          f"(mean {pde_value:.2e}, limit {MAX_PDE_RESIDUAL:.0e})"))

    # 6. Every design check after rounding up to a standard size
    u_stress = ev_round["tau_max_pa"] / ev_round["allowable_shear_pa"]
    u_defl = ev_round["tip_deflection_m"] / (DEFLECTION_LIMIT_RATIO * ev_round["length_m"])
    u_speed = ev_round["speed_rpm"] / (SPEED_RATIO_LIMIT * ev_round["critical_speed_rpm"])

    for label, u in (
        ("Max shear stress <= allowable after standard-size rounding", u_stress),
        ("Tip deflection <= limit after standard-size rounding", u_defl),
        ("Operating speed <= critical speed limit after standard-size rounding", u_speed),
    ):
        bad = float((u > 1.0 + 1e-6).float().mean())
        results.append(report(label, bad <= MAX_BAD_FRACTION,
                              f"(violations {bad * 100:.2f} %, worst utilisation {float(u.max()):.3f})"))

    # 7. Unrounded diameter is tight: the governing check sits close to its limit
    g_stress = ev["tau_max_pa"] / ev["allowable_shear_pa"]
    g_defl = ev["tip_deflection_m"] / (DEFLECTION_LIMIT_RATIO * ev["length_m"])
    g_speed = ev["speed_rpm"] / (SPEED_RATIO_LIMIT * ev["critical_speed_rpm"])
    governing = torch.maximum(torch.maximum(g_stress, g_defl), g_speed)
    loose = float(((governing < GOVERNING_RANGE[0]) | (governing > GOVERNING_RANGE[1])).float().mean())
    results.append(report(
        "Unrounded diameter is the smallest passing size",
        loose <= MAX_LOOSE_FRACTION,
        f"(outside {GOVERNING_RANGE[0]}-{GOVERNING_RANGE[1]} utilisation: {loose * 100:.2f} %, "
        f"mean {float(governing.mean()):.3f})",
    ))

    # 8. Monotonic trends via autograd, in log-log form: d(log d) / d(log input)
    xg = x.clone().requires_grad_(True)
    dg = model.diameter_m(xg)
    grad = torch.autograd.grad(dg.sum(), xg)[0]
    elasticity = grad * xg.detach() / dg.detach()

    for name, sign, label in TRENDS:
        e = elasticity[:, PARAM_NAMES.index(name)]
        bad_mask = (e < -TREND_TOLERANCE) if sign > 0 else (e > TREND_TOLERANCE)
        bad = float(bad_mask.float().mean())
        results.append(report(label, bad <= MAX_TREND_BAD_FRACTION,
                              f"(violations {bad * 100:.2f} %, limit {MAX_TREND_BAD_FRACTION * 100:.0f} %)"))

    # 9. Different inputs -> different outputs
    probe = make_params([100.0, 5000.0], [0.3, 1.0], [1.0, 3.0], [40.0, 80.0])
    with torch.no_grad():
        dp = model.diameter_m(probe)
    results.append(report("Different inputs give different outputs",
                          float((dp[0] - dp[1]).abs()) > 1e-6))

    # Information only: independent of the Km = 1.5 formula, so gaps are expected
    tau_pa = x[:, 3:4] * 1e6
    d_km15 = torch_raw_diameter_m(x[:, 0:1], x[:, 1:2], x[:, 2:3], tau_pa)
    d_km1 = torch.tensor(
        [[analytic_diameter_m(t, di, h, a)] for t, di, h, a in zip(
            x[:, 0].tolist(), x[:, 1].tolist(), x[:, 2].tolist(), tau_pa[:, 0].tolist())],
        dtype=x.dtype,
    )
    gap15 = ((d - d_km15) / d_km15).abs()
    gap1 = ((d - d_km1) / d_km1).abs()
    info("PINN vs Km = 1.5 formula",
         f"(mean gap {float(gap15.mean()) * 100:.2f} %, max gap {float(gap15.max()) * 100:.2f} %)")
    info("PINN vs Km = 1 analytic (stress only)",
         f"(mean gap {float(gap1.mean()) * 100:.2f} %, max gap {float(gap1.max()) * 100:.2f} %)")

    print()
    if all(results):
        print("RESULT: ALL PASS. Model can go to ONNX export.")
        return 0
    print("RESULT: FAIL. Do not deploy. Retrain (more epochs / weights) and re-run.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sanity_check.py <checkpoint.pth>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))