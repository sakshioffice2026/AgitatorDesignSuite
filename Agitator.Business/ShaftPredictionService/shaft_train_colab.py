"""
shaft_train_colab.py

Train the shaft PINN on Google Colab (or any machine with PyTorch).
All settings are plain variables in the CONFIG section. No command-line flags.

Colab cells:
    from google.colab import drive
    drive.mount('/content/drive')
    # get shaft_physics.py, shaft_model.py, shaft_train_colab.py into /content
    %run shaft_train_colab.py
"""

import csv
import math
import os

import torch

from shaft_physics import (
    TRUSTED_TORQUE_NM,
    TRUSTED_IMPELLER_DIAMETER_M,
    TRUSTED_LIQUID_HEIGHT_M,
    formula_shaft_diameter_m,
)
from shaft_model import ShaftPINN, physics_loss, data_loss, torch_raw_diameter_m

# =============================== CONFIG ======================================
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
SEED = 42

EPOCHS = 30000
LEARNING_RATE = 1e-3
LEARNING_RATE_END = 1e-5     # learning rate decays smoothly to this value
N_INTERIOR = 2048            # points resampled every epoch (log-uniform in trusted range)
HIDDEN = 64
N_LAYERS = 4

W_DATA = 1.0                 # weight: data term (real measurements)
W_PHYSICS = 1.0              # weight: physics penalties (the formula is NOT used for training)

# Optional real measured data. CSV header:
# torque_nm,impeller_diameter_m,liquid_height_m,measured_diameter_m
REAL_DATA_CSV = None         # e.g. "/content/drive/MyDrive/shaft_real_data.csv"

CHECKPOINT_EVERY = 2000
RESUME_FROM = None           # e.g. "/content/drive/MyDrive/shaft_model.pth"
CHECKPOINT_PATH = "/content/drive/MyDrive/shaft_model.pth"
# =============================================================================


def log_uniform(n, lo, hi, device):
    u = torch.rand(n, 1, device=device)
    return torch.exp(math.log(lo) + u * (math.log(hi) - math.log(lo)))


def sample_inputs(n, device):
    t = log_uniform(n, *TRUSTED_TORQUE_NM, device)
    d = log_uniform(n, *TRUSTED_IMPELLER_DIAMETER_M, device)
    h = log_uniform(n, *TRUSTED_LIQUID_HEIGHT_M, device)
    return torch.cat([t, d, h], dim=1)


def load_real_data(path, device):
    if not path or not os.path.exists(path):
        return None, None
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append([
                float(r["torque_nm"]),
                float(r["impeller_diameter_m"]),
                float(r["liquid_height_m"]),
                float(r["measured_diameter_m"]),
            ])
    if not rows:
        return None, None
    t = torch.tensor(rows, dtype=torch.float32, device=device)
    return t[:, 0:3], t[:, 3:4]


def save_checkpoint(model, optimizer, epoch, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.save({
        "epoch": epoch,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "hidden": HIDDEN,
        "n_layers": N_LAYERS,
    }, path)


def main():
    torch.manual_seed(SEED)
    print(f"Device: {DEVICE}")

    model = ShaftPINN(hidden=HIDDEN, n_layers=N_LAYERS).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    start_epoch = 0

    if RESUME_FROM and os.path.exists(RESUME_FROM):
        ckpt = torch.load(RESUME_FROM, map_location=DEVICE, weights_only=True)
        model.load_state_dict(ckpt["model_state"])
        optimizer.load_state_dict(ckpt["optimizer_state"])
        start_epoch = ckpt["epoch"] + 1
        print(f"Resumed from {RESUME_FROM} at epoch {start_epoch}")

    real_x, real_d = load_real_data(REAL_DATA_CSV, DEVICE)
    if real_x is not None:
        print(f"Real data points: {real_x.shape[0]}")
    else:
        print("No real data. Training on physics rules only (formula is not used as a label).")

    for epoch in range(start_epoch, EPOCHS):
        lr = LEARNING_RATE * (LEARNING_RATE_END / LEARNING_RATE) ** (epoch / EPOCHS)
        for group in optimizer.param_groups:
            group["lr"] = lr

        x = sample_inputs(N_INTERIOR, DEVICE)

        l_phys, parts = physics_loss(model, x)

        loss = W_PHYSICS * l_phys
        if real_x is not None:
            loss = loss + W_DATA * data_loss(model, real_x, real_d)

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()

        if not math.isfinite(float(loss)):
            print("Loss is nan/inf. Stop. Lower LEARNING_RATE or change SEED and retrain.")
            return

        if epoch % 500 == 0:
            print(f"[{epoch:>6}/{EPOCHS}] loss={float(loss):.3e} "
                  f"physics={float(l_phys):.3e} lr={lr:.2e}")

        if CHECKPOINT_EVERY and epoch > 0 and epoch % CHECKPOINT_EVERY == 0:
            save_checkpoint(model, optimizer, epoch, CHECKPOINT_PATH)

    save_checkpoint(model, optimizer, EPOCHS - 1, CHECKPOINT_PATH)
    print(f"Saved: {CHECKPOINT_PATH}")

    # quick reference check against the rounded formula
    model.eval()
    with torch.no_grad():
        probe = torch.tensor([[500.0, 0.5, 2.0]], device=DEVICE)
        d_pred_mm = float(model(probe)) * 1000.0
    d_formula_mm = formula_shaft_diameter_m(500.0, 0.5, 2.0) * 1000.0
    print(f"Probe (500 N*m, 0.5 m, 2.0 m): PINN raw={d_pred_mm:.2f} mm, "
          f"formula rounded={d_formula_mm:.1f} mm")

    # independent agreement check (evaluation only, never used for training)
    with torch.no_grad():
        xe = sample_inputs(5000, DEVICE)
        d_pred = model(xe)
        d_ref = torch_raw_diameter_m(xe[:, 0:1], xe[:, 1:2], xe[:, 2:3])
        gap = ((d_pred - d_ref) / d_ref).abs()
    print(f"Agreement with formula on 5000 random points: "
          f"mean gap={float(gap.mean()) * 100:.3f} %, max gap={float(gap.max()) * 100:.3f} %")


if __name__ == "__main__":
    main()