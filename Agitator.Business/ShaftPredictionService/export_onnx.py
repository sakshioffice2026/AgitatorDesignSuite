"""
export_onnx.py

Export a trained shaft PINN checkpoint (.pth) to ONNX.
Run only after sanity_check.py shows ALL PASS.

Colab:
    !pip install onnx --quiet
    !python export_onnx.py /content/drive/MyDrive/shaft_model.pth /content/drive/MyDrive/shaft_model.onnx

Keep the .pth. The .onnx alone cannot be retrained.
"""

import sys

import torch

from shaft_model import ShaftPINN

OPSET_VERSION = 17


def main(checkpoint_path, onnx_path):
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()

        dummy = torch.tensor([[500.0, 0.5, 2.0, 40.0]], dtype=torch.float32)

    torch.onnx.export(
    model,
        dummy,
        onnx_path,
                input_names=["inputs"],          # [N, 4] = torque_nm, impeller_diameter_m, liquid_height_m, allowable_shear_mpa
        output_names=["shaft_diameter_m"],  # [N, 1] raw diameter, metres
        dynamic_axes={"inputs": {0: "batch"}, "shaft_diameter_m": {0: "batch"}},
        opset_version=OPSET_VERSION,
    )
    print(f"Exported: {onnx_path}")
    print(f"Exported: {onnx_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python export_onnx.py <checkpoint.pth> <output.onnx>")
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
"""
export_onnx.py

Export a trained shaft PINN checkpoint (.pth) to ONNX.
Run only after sanity_check.py shows ALL PASS.

Colab:
    !pip install onnx --quiet
    !python export_onnx.py /content/drive/MyDrive/shaft_model.pth /content/drive/MyDrive/shaft_model.onnx

Keep the .pth. The .onnx alone cannot be retrained.
"""

import sys

import torch

from shaft_model import ShaftPINN

OPSET_VERSION = 17


def main(checkpoint_path, onnx_path):
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()

        dummy = torch.tensor([[500.0, 0.5, 2.0, 40.0]], dtype=torch.float32)

    torch.onnx.export(
    model,
        dummy,
        onnx_path,
                input_names=["inputs"],          # [N, 4] = torque_nm, impeller_diameter_m, liquid_height_m, allowable_shear_mpa
        output_names=["shaft_diameter_m"],  # [N, 1] raw diameter, metres
        dynamic_axes={"inputs": {0: "batch"}, "shaft_diameter_m": {0: "batch"}},
        opset_version=OPSET_VERSION,
    )
    print(f"Exported: {onnx_path}")
    print(f"Exported: {onnx_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python export_onnx.py <checkpoint.pth> <output.onnx>")
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
