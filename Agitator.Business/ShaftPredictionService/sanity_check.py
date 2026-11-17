"""
sanity_check.py

Physical checks for a trained shaft PINN checkpoint (.pth).
Run BEFORE exporting or deploying. Any FAIL = do not deploy.

Colab:
    !python sanity_check.py /content/drive/MyDrive/shaft_model.pth
"""

import math
import sys

import torch

from shaft_physics import (
    ALLOWABLE_SHEAR_STRESS_PA,
    TRUSTED_TORQUE_NM,
    TRUSTED_IMPELLER_DIAMETER_M,
    TRUSTED_LIQUID_HEIGHT_M,
    round_up_to_standard_size_m,
)
from shaft_model import (
    ShaftPINN,
    MAX_CORRECTION,
    torch_raw_diameter_m,
    torch_shear_stress_pa,
)

N_POINTS = 5000
TREND_TOLERANCE = 1e-6          # allowed numerical noise in monotonic checks
MAX_BAD_FRACTION = 0.01         # up to 1 % of points may violate a soft check
SEED = 123


def load_model(path):
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model


def log_uniform(n, lo, hi):
    u = torch.rand(n, 1)
    return torch.exp(math.log(lo) + u * (math.log(hi) - math.log(lo)))


def sample(n):
    return torch.cat([
        log_uniform(n, *TRUSTED_TORQUE_NM),
        log_uniform(n, *TRUSTED_IMPELLER_DIAMETER_M),
        log_uniform(n, *TRUSTED_LIQUID_HEIGHT_M),
    ], dim=1)


def report(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} {detail}")
    return ok


def main(path):
    torch.manual_seed(SEED)
    model = load_model(path)
    x = sample(N_POINTS)
    results = []

    with torch.no_grad():
        d = model(x)
        d_formula = torch_raw_diameter_m(x[:, 0:1], x[:, 1:2], x[:, 2:3])

    # 1. NaN / inf
    results.append(report("No NaN/inf in output", bool(torch.isfinite(d).all())))

    # 2. Positive
    results.append(report("Diameter > 0", bool((d > 0).all())))

    # 3. Independent prediction must agree with the formula (formula is used only here, to compare)
    rel = ((d - d_formula) / d_formula).abs()
    results.append(report(
        "Agrees with formula within tolerance",
        bool(rel.max() <= MAX_CORRECTION + 1e-6),
        f"(mean gap {float(rel.mean()) * 100:.2f} %, max gap {float(rel.max()) * 100:.2f} %, "
        f"limit {MAX_CORRECTION * 100:.0f} %)",
    ))

    # 4. Stress after rounding up to a standard size
    d_round = torch.tensor(
        [[round_up_to_standard_size_m(float(v))] for v in d], dtype=torch.float32
    )
    stress = torch_shear_stress_pa(x[:, 0:1], x[:, 1:2], x[:, 2:3], d_round)
    bad = float((stress > ALLOWABLE_SHEAR_STRESS_PA).float().mean())
    results.append(report(
        "Stress <= allowable after standard-size rounding",
        bad <= MAX_BAD_FRACTION,
        f"(violations {bad * 100:.2f} %)",
    ))

    # 5-7. Monotonic trends via autograd
    xg = x.clone().requires_grad_(True)
    dg = model(xg)
    grad = torch.autograd.grad(dg.sum(), xg)[0]

    bad_t = float((grad[:, 0] < -TREND_TOLERANCE).float().mean())
    results.append(report("Diameter rises with torque", bad_t <= MAX_BAD_FRACTION,
                          f"(violations {bad_t * 100:.2f} %)"))

    bad_i = float((grad[:, 1] > TREND_TOLERANCE).float().mean())
    results.append(report("Diameter falls as impeller diameter rises", bad_i <= MAX_BAD_FRACTION,
                          f"(violations {bad_i * 100:.2f} %)"))

    bad_h = float((grad[:, 2] < -TREND_TOLERANCE).float().mean())
    results.append(report("Diameter rises with liquid height", bad_h <= MAX_BAD_FRACTION,
                          f"(violations {bad_h * 100:.2f} %)"))

    # 8. Different inputs -> different outputs
    probe = torch.tensor([[100.0, 0.3, 1.0], [5000.0, 1.0, 3.0]])
    with torch.no_grad():
        dp = model(probe)
    results.append(report("Different inputs give different outputs",
                          float((dp[0] - dp[1]).abs()) > 1e-6))

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