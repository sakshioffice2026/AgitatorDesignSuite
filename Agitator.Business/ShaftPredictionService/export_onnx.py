"""
export_onnx.py

Export a trained shaft PINN checkpoint (.pth) to ONNX.
Run only after sanity_check.py shows ALL PASS.

Colab:
    !pip install onnx --quiet
    !python export_onnx.py /content/drive/MyDrive/shaft_model.pth /content/drive/MyDrive/shaft_model.onnx

Graph
    inputs   xi      [N, 1]  position along the shaft, xi = x / L in [0, 1]
             params  [N, 8]  physical design parameters, column order = PARAM_NAMES in shaft_model.py
                             (rated_torque_nm, impeller_diameter_m, liquid_height_m,
                              allowable_shear_mpa, operating_speed_rpm, impeller_mass_kg,
                              elastic_modulus_gpa, density_kg_m3)
    outputs  fields      [N, 5]  non-dimensional (w, theta, M, V, phi), see pinn_loss.py
             diameter_m  [N, 1]  raw shaft diameter in metres, before standard-size rounding

The parameter names and trusted ranges are stored in the ONNX metadata
(keys: param_names, trusted_ranges, deflection_limit_ratio, speed_ratio_limit).

Keep the .pth. The .onnx alone cannot be retrained.
"""

import json
import sys

import torch
import torch.nn as nn

from shaft_model import (
    ShaftPINN,
    PARAM_NAMES,
    PARAM_RANGES,
    N_PARAMS,
    DEFLECTION_LIMIT_RATIO,
    SPEED_RATIO_LIMIT,
)

OPSET_VERSION = 17


class ShaftOnnxWrapper(nn.Module):
    """Single graph with both heads: the field network and the diameter network."""

    def __init__(self, model: ShaftPINN):
        super().__init__()
        self.model = model

    def forward(self, xi, params):
        return self.model.field_net(xi, params), self.model.diameter_m(params)


def add_metadata(onnx_path, ckpt):
    import onnx

    ranges = ckpt.get("trusted_ranges") or [[float(lo), float(hi)] for lo, hi in PARAM_RANGES]
    meta = {
        "param_names": json.dumps(list(PARAM_NAMES)),
        "trusted_ranges": json.dumps(ranges),
        "deflection_limit_ratio": str(ckpt.get("deflection_limit_ratio", DEFLECTION_LIMIT_RATIO)),
        "speed_ratio_limit": str(ckpt.get("speed_ratio_limit", SPEED_RATIO_LIMIT)),
    }

    proto = onnx.load(onnx_path)
    del proto.metadata_props[:]
    for key, value in meta.items():
        entry = proto.metadata_props.add()
        entry.key = key
        entry.value = value
    onnx.checker.check_model(proto)
    onnx.save(proto, onnx_path)


def main(checkpoint_path, onnx_path):
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    wrapper = ShaftOnnxWrapper(model).eval()

    dummy_xi = torch.tensor([[0.0], [0.5], [1.0]], dtype=torch.float32)
    dummy_params = torch.tensor(
        [[500.0, 0.5, 2.0, 40.0, 60.0, 20.0, 210.0, 7850.0]] * 3, dtype=torch.float32
    )
    assert dummy_params.shape[1] == N_PARAMS

    torch.onnx.export(
        wrapper,
        (dummy_xi, dummy_params),
        onnx_path,
        input_names=["xi", "params"],
        output_names=["fields", "diameter_m"],
        dynamic_axes={
            "xi": {0: "batch"},
            "params": {0: "batch"},
            "fields": {0: "batch"},
            "diameter_m": {0: "batch"},
        },
        opset_version=OPSET_VERSION,
    )

    add_metadata(onnx_path, ckpt)
    print(f"Exported: {onnx_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python export_onnx.py <checkpoint.pth> <output.onnx>")
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])