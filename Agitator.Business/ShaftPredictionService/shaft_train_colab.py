"""
shaft_train_colab.py

Train the shaft PINN on Google Colab (or any machine with PyTorch).
All settings are plain variables in the CONFIG section. No command-line flags.

Colab cells:
    from google.colab import drive
    drive.mount('/content/drive')
    # copy shaft_physics.py, shaft_critical_speed.py, pinn_loss.py, shaft_model.py,
    # shaft_train_colab.py and fea_dataset.jsonl into /content
    %run shaft_train_colab.py

Training signals (the diameter formula in shaft_physics.py is NEVER used as a label):
    field network    : pinn_loss.total_loss = PDE residual + exact boundary conditions + FEA data
                       plus an FEA stress anchor (field-network stress vs FEA max shear)
    diameter network : pinn_loss.design_constraint_loss (stress, deflection, critical speed,
                       smallest passing diameter), using the trained field network as the physics
The two networks use separate optimisers, so the diameter loss cannot bend the field network.

Resuming: training continues from the saved epoch up to EPOCHS, so EPOCHS must be larger than
the epoch already reached (a finished 30000-epoch run saved epoch 29999).
"""

import json
import math
import os

import torch

import pinn_loss as pl
from shaft_model import (
    ShaftPINN,
    PARAM_NAMES,
    PARAM_RANGES,
    evaluate_design,
    constraint_batch,
    make_params,
    pde_residual_value,
    torch_raw_diameter_m,
    DEFLECTION_LIMIT_RATIO,
    SPEED_RATIO_LIMIT,
)

# =============================== CONFIG ======================================
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
SEED = 42

EPOCHS = 70000               # resume: trains from the saved epoch (30000) up to this value
LEARNING_RATE = 1e-3
LEARNING_RATE_END = 1e-5     # learning rate decays smoothly to this value
N_INTERIOR = 2048            # design points resampled every epoch (log-uniform)
N_XI = 2048                  # collocation positions per epoch
HIDDEN = 64
N_LAYERS = 4

W_PDE = 1.0
W_BC = 10.0
W_DATA = 1.0                 # FEA station data (w, theta, phi)
W_FEA_STRESS = 1.0           # FEA max shear anchor on the field network
W_CONSTRAINT = 20.0           # raised from 1.0: penalise designs over a limit harder
W_TIGHTNESS = 0.03            # kept low so the size is pulled down only gently
CONSTRAINT_WARMUP = 5000     # epochs over which the diameter loss ramps from 0 to full weight
N_DATA_BATCH = 4096          # FEA station rows per epoch

# FEA ground truth from fea_dataset_generator.py. None = physics only (PDE + constraints).
FEA_DATASET_JSONL = None     # e.g. "/content/drive/MyDrive/fea_dataset.jsonl"

# Training sub-ranges for the two inputs that are not bounded by the shaft geometry.
# Stored in the checkpoint; service.py skips the network outside these ranges.
TRAIN_SPEED_RPM = (10.0, 600.0)
TRAIN_MASS_KG = (1.0, 5000.0)

CHECKPOINT_EVERY = 2000
RESUME_FROM = "/content/drive/MyDrive/shaft_model_v2.pth"
CHECKPOINT_PATH = "/content/drive/MyDrive/shaft_model_v3.pth"
# =============================================================================

TRAIN_RANGES = list(PARAM_RANGES)
TRAIN_RANGES[PARAM_NAMES.index("operating_speed_rpm")] = TRAIN_SPEED_RPM
TRAIN_RANGES[PARAM_NAMES.index("impeller_mass_kg")] = TRAIN_MASS_KG

STRESS_WINDOW_END_FRACTION = 0.05   # same stress window as fea_dataset_generator.py


def log_uniform(n, lo, hi, device):
    u = torch.rand(n, 1, device=device)
    return torch.exp(math.log(lo) + u * (math.log(hi) - math.log(lo)))


def sample_params(n, device):
    cols = [log_uniform(n, lo, hi, device) for lo, hi in TRAIN_RANGES]
    return torch.cat(cols, dim=1)


# ---------------------------------------------------------------------------
# FEA data
# ---------------------------------------------------------------------------
def load_fea_dataset(path, device):
    if not path or not os.path.exists(path):
        return None

    xi, y, mask, rows = [], [], [], []
    cases = {"torque": [], "imp": [], "height": [], "e_gpa": [], "force": [], "length": [],
             "design_t": [], "d": [], "tau_mpa": []}

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") != "ok" or not rec.get("stations"):
                continue
            c = rec["inputs"]
            e_gpa = c["e_pa"] / 1e9

            cases["torque"].append(c["rated_torque_nm"])
            cases["imp"].append(c["impeller_diameter_m"])
            cases["height"].append(c["liquid_height_m"])
            cases["e_gpa"].append(e_gpa)
            cases["force"].append(c["radial_force_n"])
            cases["length"].append(c["shaft_length_m"])
            cases["design_t"].append(c["design_torque_nm"])
            cases["d"].append(c["shaft_diameter_m"])
            cases["tau_mpa"].append(rec["results"]["fea_max_shear_mpa"])

            for s in rec["stations"]:
                xi.append(s["xi"])
                y.append([0.0 if v is None else v for v in s["y"]])
                mask.append(s["mask"])
                rows.append((c["rated_torque_nm"], c["impeller_diameter_m"],
                             c["liquid_height_m"], e_gpa))

    if not xi:
        return None

    f32 = torch.float32
    t = torch.tensor(rows, dtype=f32, device=device)
    p_rows = make_params(t[:, 0], t[:, 1], t[:, 2], elastic_modulus_gpa=t[:, 3],
                         dtype=f32, device=device)

    ct = {k: torch.tensor(v, dtype=f32, device=device).reshape(-1, 1) for k, v in cases.items()}
    p_cases = make_params(ct["torque"], ct["imp"], ct["height"],
                          elastic_modulus_gpa=ct["e_gpa"], dtype=f32, device=device)

    return {
        "xi": torch.tensor(xi, dtype=f32, device=device).reshape(-1, 1),
        "p": p_rows,
        "y": torch.tensor(y, dtype=f32, device=device),
        "mask": torch.tensor(mask, dtype=f32, device=device),
        "case_p": p_cases,
        "case_force": ct["force"],
        "case_length": ct["length"],
        "case_design_t": ct["design_t"],
        "case_d": ct["d"],
        "case_tau_pa": ct["tau_mpa"] * 1e6,
    }


def fea_stress_loss(model, data):
    """Field-network max shear at the FEA stress window start vs FEA max shear (relative error)."""
    length = data["case_length"]
    d = data["case_d"]
    end_margin = torch.maximum(STRESS_WINDOW_END_FRACTION * length, 0.75 * d)
    xi_s = (end_margin / length).clamp(0.0, 1.0)

    fields = model.field_net(xi_s, data["case_p"])
    moment = fields[:, pl.M:pl.M + 1].abs() * data["case_force"] * length
    tau = pl.max_shear_stress_pa(moment, data["case_design_t"], d)
    return ((tau - data["case_tau_pa"]) / data["case_tau_pa"]).pow(2).mean()


def sample_data_batch(data, n, device):
    total = data["xi"].shape[0]
    idx = torch.randint(0, total, (min(n, total),), device=device)
    xi = data["xi"][idx].clone().requires_grad_(True)
    return {"xi": xi, "p": data["p"][idx], "y": data["y"][idx], "mask": data["mask"][idx]}


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------
def save_checkpoint(model, opt_field, opt_diameter, epoch, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    torch.save({
        "epoch": epoch,
        "model_state": model.state_dict(),
        "optimizer_state": {"field": opt_field.state_dict(), "diameter": opt_diameter.state_dict()},
        "hidden": HIDDEN,
        "n_layers": N_LAYERS,
        "param_names": list(PARAM_NAMES),
        "trusted_ranges": [[float(lo), float(hi)] for lo, hi in TRAIN_RANGES],
        "deflection_limit_ratio": DEFLECTION_LIMIT_RATIO,
        "speed_ratio_limit": SPEED_RATIO_LIMIT,
    }, path)


def set_lr(optimizers, lr):
    for opt in optimizers:
        for group in opt.param_groups:
            group["lr"] = lr


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def main():
    torch.manual_seed(SEED)
    print(f"Device: {DEVICE}")

    model = ShaftPINN(hidden=HIDDEN, n_layers=N_LAYERS).to(DEVICE)
    field_params = list(model.field_trunk.parameters())
    diameter_params = list(model.diameter_trunk.parameters())
    opt_field = torch.optim.Adam(field_params, lr=LEARNING_RATE)
    opt_diameter = torch.optim.Adam(diameter_params, lr=LEARNING_RATE)
    start_epoch = 0

    if RESUME_FROM and os.path.exists(RESUME_FROM):
        ckpt = torch.load(RESUME_FROM, map_location=DEVICE, weights_only=True)
        model.load_state_dict(ckpt["model_state"])
        opt_field.load_state_dict(ckpt["optimizer_state"]["field"])
        opt_diameter.load_state_dict(ckpt["optimizer_state"]["diameter"])
        start_epoch = ckpt["epoch"] + 1
        print(f"Resumed from {RESUME_FROM} at epoch {start_epoch}")

    if start_epoch >= EPOCHS:
        print(f"Nothing to train: saved epoch {start_epoch} >= EPOCHS {EPOCHS}. Increase EPOCHS.")
        return

    data = load_fea_dataset(FEA_DATASET_JSONL, DEVICE)
    if data is not None:
        print(f"FEA data: {data['xi'].shape[0]} station rows, {data['case_p'].shape[0]} cases")
    else:
        print("No FEA data. Field network trains on PDE + boundary conditions only.")

    weights = pl.LossWeights(pde=W_PDE, bc=W_BC, data=W_DATA, constraint=W_CONSTRAINT,
                             tightness=W_TIGHTNESS)

    for epoch in range(start_epoch, EPOCHS):
        lr = LEARNING_RATE * (LEARNING_RATE_END / LEARNING_RATE) ** (epoch / EPOCHS)
        set_lr((opt_field, opt_diameter), lr)

        p = sample_params(N_INTERIOR, DEVICE)
        xi = pl.sample_xi(N_XI, device=DEVICE)
        p_xi = sample_params(N_XI, DEVICE)

        # ---- field network: PDE + BC + FEA data + FEA stress anchor ----
        data_batch = sample_data_batch(data, N_DATA_BATCH, DEVICE) if data is not None else None
        l_field, parts = pl.total_loss(model.field_net, xi, p_xi, weights, data_batch=data_batch)
        if data is not None:
            l_stress = fea_stress_loss(model, data)
            l_field = l_field + W_FEA_STRESS * l_stress
            parts["fea_stress"] = float(l_stress)

        opt_field.zero_grad()
        l_field.backward(inputs=field_params)
        torch.nn.utils.clip_grad_norm_(field_params, 1.0)
        opt_field.step()

        # ---- diameter network: design constraints through the field network ----
        ramp = min(1.0, epoch / CONSTRAINT_WARMUP) if CONSTRAINT_WARMUP > 0 else 1.0
        ev = evaluate_design(model, p)
        l_con, cparts = pl.design_constraint_loss(tightness_weight=W_TIGHTNESS,
                                                  **constraint_batch(ev))
        l_diameter = ramp * W_CONSTRAINT * l_con

        opt_diameter.zero_grad()
        l_diameter.backward(inputs=diameter_params)
        torch.nn.utils.clip_grad_norm_(diameter_params, 1.0)
        opt_diameter.step()

        if not (math.isfinite(float(l_field)) and math.isfinite(float(l_diameter))):
            print("Loss is nan/inf. Stop. Lower LEARNING_RATE or change SEED and retrain.")
            return

        if epoch % 500 == 0:
            print(f"[{epoch:>6}/{EPOCHS}] field={float(l_field):.3e} "
                  f"pde={parts['pde']:.3e} bc={parts['bc']:.3e} "
                  f"constraint={float(l_con):.3e} lr={lr:.2e}")

        if CHECKPOINT_EVERY and epoch > 0 and epoch % CHECKPOINT_EVERY == 0:
            save_checkpoint(model, opt_field, opt_diameter, epoch, CHECKPOINT_PATH)

    save_checkpoint(model, opt_field, opt_diameter, EPOCHS - 1, CHECKPOINT_PATH)
    print(f"Saved: {CHECKPOINT_PATH}")

    evaluate(model)


# ---------------------------------------------------------------------------
# Evaluation (never used for training)
# ---------------------------------------------------------------------------
def evaluate(model):
    model.eval()
    p = sample_params(2000, DEVICE)

    with torch.no_grad():
        ev = evaluate_design(model, p)
        d = ev["d_m"]
        force, length = ev["force_n"], ev["length_m"]
        e_pa = p[:, 6:7] * 1e9
        g_pa = e_pa / (2.0 * (1.0 + 0.30))

        defl_ref = force * length ** 3 / (3.0 * e_pa * pl.second_moment_m4(d))
        twist_ref = ev["design_torque_nm"] * length / (g_pa * pl.polar_moment_m4(d))
        moment_ref = force * length

        err_defl = ((ev["tip_deflection_m"] - defl_ref) / defl_ref).abs()
        err_twist = ((ev["tip_twist_rad"] - twist_ref) / twist_ref).abs()
        err_moment = ((ev["root_moment_nm"] - moment_ref) / moment_ref).abs()

        u_stress = ev["tau_max_pa"] / ev["allowable_shear_pa"]
        u_defl = ev["tip_deflection_m"] / (DEFLECTION_LIMIT_RATIO * length)
        u_speed = ev["speed_rpm"] / (SPEED_RATIO_LIMIT * ev["critical_speed_rpm"])
        governing = torch.maximum(torch.maximum(u_stress, u_defl), u_speed)

        d_formula = torch_raw_diameter_m(p[:, 0:1], p[:, 1:2], p[:, 2:3], p[:, 3:4] * 1e6)
        gap = ((d - d_formula) / d_formula)

    pde = pde_residual_value(model, p[0:1])

    print("Field network vs closed-form beam theory (2000 random designs):")
    print(f"  tip deflection  mean err={float(err_defl.mean()) * 100:.3f} %  max={float(err_defl.max()) * 100:.3f} %")
    print(f"  tip twist       mean err={float(err_twist.mean()) * 100:.3f} %  max={float(err_twist.max()) * 100:.3f} %")
    print(f"  root moment     mean err={float(err_moment.mean()) * 100:.3f} %  max={float(err_moment.max()) * 100:.3f} %")
    print(f"  PDE residual    {pde:.3e}")
    print("Diameter network, governing utilisation (1.00 = exactly at the limit):")
    print(f"  mean={float(governing.mean()):.3f}  min={float(governing.min()):.3f}  max={float(governing.max()):.3f}")
    print("Reference only: gap to the Km = 1.5 formula (expected to differ, the formula adds a bending factor):")
    print(f"  mean={float(gap.mean()) * 100:.2f} %  min={float(gap.min()) * 100:.2f} %  max={float(gap.max()) * 100:.2f} %")


if __name__ == "__main__":
    main()