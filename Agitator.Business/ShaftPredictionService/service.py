"""
service.py

FastAPI service that hosts the trained shaft PINN.
One endpoint: POST /predict. Model is loaded once at startup.

Run (internal network only):
    set SHAFT_MODEL_PATH=D:\\AgitatorSuite\\Models\\shaft_model.pth
    set SHAFT_MODEL_VERSION=2026-11-v1
    uvicorn service:app --host 127.0.0.1 --port 8001

JSON field names are camelCase and must match the .NET DTO exactly.
Units: N*m, metres. Diameters in the response are millimetres.
"""

import os
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from shaft_physics import (
    ALLOWABLE_SHEAR_STRESS_PA,
    TRUSTED_TORQUE_NM,
    TRUSTED_IMPELLER_DIAMETER_M,
    TRUSTED_LIQUID_HEIGHT_M,
    formula_shaft_diameter_m,
    round_up_to_standard_size_m,
    combined_shear_stress_pa,
    is_within_trusted_range,
)
from shaft_model import ShaftPINN

MODEL_PATH = os.environ.get("SHAFT_MODEL_PATH", "models/shaft_model.pth")
MODEL_VERSION = os.environ.get("SHAFT_MODEL_VERSION", "unversioned")

_state = {"model": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    ckpt = torch.load(MODEL_PATH, map_location="cpu")
    model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    _state["model"] = model
    yield
    _state["model"] = None


app = FastAPI(title="Shaft Prediction Service", lifespan=lifespan)


class ShaftPredictRequest(BaseModel):
    ratedTorqueNm: float = Field(..., gt=0)
    impellerDiameterM: float = Field(..., gt=0)
    liquidHeightM: float = Field(..., gt=0)


class ShaftPredictResponse(BaseModel):
    predictedShaftDiameterMm: float      # PINN, rounded up to standard size
    rawPredictedDiameterMm: float        # PINN, before rounding
    formulaShaftDiameterMm: float        # trusted formula, rounded up to standard size
    rawFormulaDiameterMm: float          # trusted formula, before rounding
    gapPercent: float                    # (predicted - formula) / formula * 100, on raw values
    shearStressMpa: float                # at predicted standard size
    allowableShearStressMpa: float
    withinTrustedRange: bool
    modelVersion: str
    notes: str


@app.get("/health")
def health():
    return {"status": "ok", "modelLoaded": _state["model"] is not None,
            "modelVersion": MODEL_VERSION}


@app.post("/predict", response_model=ShaftPredictResponse)
def predict(req: ShaftPredictRequest):
    model = _state["model"]
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    t, d, h = req.ratedTorqueNm, req.impellerDiameterM, req.liquidHeightM

    x = torch.tensor([[t, d, h]], dtype=torch.float32)
    with torch.no_grad():
        raw_pred_m = float(model(x).item())

    pred_std_m = round_up_to_standard_size_m(raw_pred_m)
    formula_std_m = formula_shaft_diameter_m(t, d, h)
    raw_formula_m = formula_std_m  # rounded reference for display

    # gap on raw (unrounded) values so it is not hidden by standard sizes
    from shaft_physics import raw_shaft_diameter_m
    raw_formula_unrounded_m = raw_shaft_diameter_m(t, d, h)
    gap_pct = (raw_pred_m - raw_formula_unrounded_m) / raw_formula_unrounded_m * 100.0

    stress_pa = combined_shear_stress_pa(t, d, h, pred_std_m)
    in_range = is_within_trusted_range(t, d, h)

    notes = []
    if not in_range:
        notes.append(
            "Input outside trusted range "
            f"(torque {TRUSTED_TORQUE_NM[0]}-{TRUSTED_TORQUE_NM[1]} N*m, "
            f"impeller {TRUSTED_IMPELLER_DIAMETER_M[0]}-{TRUSTED_IMPELLER_DIAMETER_M[1]} m, "
            f"liquid height {TRUSTED_LIQUID_HEIGHT_M[0]}-{TRUSTED_LIQUID_HEIGHT_M[1]} m)."
        )
    if stress_pa > ALLOWABLE_SHEAR_STRESS_PA:
        notes.append("Shear stress exceeds allowable at predicted size.")
    if pred_std_m != formula_std_m:
        notes.append("Predicted standard size differs from formula standard size.")

    return ShaftPredictResponse(
        predictedShaftDiameterMm=round(pred_std_m * 1000.0, 1),
        rawPredictedDiameterMm=round(raw_pred_m * 1000.0, 2),
        formulaShaftDiameterMm=round(raw_formula_m * 1000.0, 1),
        rawFormulaDiameterMm=round(raw_formula_unrounded_m * 1000.0, 2),
        gapPercent=round(gap_pct, 3),
        shearStressMpa=round(stress_pa / 1e6, 3),
        allowableShearStressMpa=round(ALLOWABLE_SHEAR_STRESS_PA / 1e6, 3),
        withinTrustedRange=in_range,
        modelVersion=MODEL_VERSION,
        notes=" ".join(notes),
    )