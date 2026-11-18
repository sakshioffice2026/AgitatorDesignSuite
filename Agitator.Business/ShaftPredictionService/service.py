"""
service.py

FastAPI service for the shaft PINN.

    POST /predict   shaft diameter from the PINN, the Km = 1.5 formula, and a physics block
                    (deflection, twist, stresses, critical speed, fatigue factors) evaluated
                    at the formula standard size, the same size the FEA check uses.
    GET  /health

Run:  uvicorn service:app --port 8001

Also exposes predict_case(inputs, xi) for accuracy_report.py:
    python accuracy_report.py --dataset fea_dataset.jsonl --predictor service:predict_case
"""

import math
import os
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from shaft_critical_speed import critical_speed_rpm
from shaft_fatigue import FatigueMaterial, fatigue_check
from shaft_model import (
    PARAM_NAMES,
    PARAM_RANGES,
    ShaftPINN,
    evaluate_design,
    make_params,
    pde_residual_value,
)
import pinn_loss as pl
from shaft_physics import (
    TRUSTED_TORQUE_NM,
    TRUSTED_IMPELLER_DIAMETER_M,
    TRUSTED_LIQUID_HEIGHT_M,
    analytic_tip_deflection_m,
    analytic_twist_rad,
    bending_moment_nm,
    combined_shear_stress_pa,
    design_torque_nm,
    formula_shaft_diameter_m,
    radial_force_n,
    raw_shaft_diameter_m,
    round_up_to_standard_size_m,
    shaft_length_m,
    tresca_shear_stress_pa,
    von_mises_stress_pa,
)

MODEL_PATH = os.environ.get("SHAFT_MODEL_PATH", "models/shaft_model.pth")

MODEL_VERSION = os.environ.get("SHAFT_MODEL_VERSION", "unversioned")

MAX_REPORTED_FACTOR = 999.0     # same cap as AgitatorCalculationService.cs
REQUIRED_FATIGUE_FACTOR = 1.5   # only used for the pass flag inside fatigue_check
FALLBACK_YIELD_MPA = 250.0      # same fallbacks as AgitatorCalculationService.cs
FALLBACK_ULTIMATE_MPA = 400.0
DEFAULT_E_GPA = 200.0
DEFAULT_NU = 0.30
DEFAULT_DENSITY = 7850.0

_state = {"model": None, "ranges": [tuple(r) for r in PARAM_RANGES]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    ckpt = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
    model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    _state["model"] = model
    if ckpt.get("trusted_ranges"):
        _state["ranges"] = [tuple(r) for r in ckpt["trusted_ranges"]]
    yield
    _state["model"] = None


app = FastAPI(title="Shaft Prediction Service", lifespan=lifespan)


class ShaftPredictRequest(BaseModel):
    ratedTorqueNm: float = Field(..., gt=0)
    impellerDiameterM: float = Field(..., gt=0)
    liquidHeightM: float = Field(..., gt=0)
    allowableShearStressMpa: float = Field(40.0, gt=0)
    operatingTemperatureC: float = 30.0
    shaftMaterialName: str = ""
    operatingSpeedRpm: float = Field(60.0, gt=0)
    impellerMassKg: float = Field(0.0, ge=0)
    elasticModulusGpa: float = Field(DEFAULT_E_GPA, gt=0)
    poissonRatio: float = Field(DEFAULT_NU, gt=-1.0, lt=0.5)
    densityKgM3: float = Field(DEFAULT_DENSITY, gt=0)
    yieldStrengthMpa: float = Field(0.0, ge=0)
    ultimateStrengthMpa: float = Field(0.0, ge=0)
    enduranceLimitMpa: float = Field(0.0, ge=0)
    surfaceFinish: str = "machined"
    shaftDiameterMm: float = Field(0.0, ge=0)


class ShaftPhysicsBlock(BaseModel):
    tipDeflectionMm: float
    twistDeg: float
    rootBendingMomentNm: float
    maxShearStressMpa: float
    vonMisesStressMpa: float
    criticalSpeedRpm: float
    goodmanFactor: float
    soderbergFactor: float
    yieldFactor: float
    pdeResidual: float


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
    shaftLengthM: float
    physics: ShaftPhysicsBlock


@app.get("/health")
def health():
    return {"status": "ok", "modelLoaded": _state["model"] is not None,
            "modelVersion": MODEL_VERSION}


def _cap(value: float) -> float:
    return MAX_REPORTED_FACTOR if (not math.isfinite(value) or value > MAX_REPORTED_FACTOR) else value


def _in_trusted_ranges(values) -> bool:
    return all(lo <= v <= hi for v, (lo, hi) in zip(values, _state["ranges"]))


def _fatigue_factors(req: ShaftPredictRequest, moment_nm: float, torque_nm: float, d_m: float):
    yield_mpa = req.yieldStrengthMpa if req.yieldStrengthMpa > 0 else FALLBACK_YIELD_MPA
    ultimate_mpa = req.ultimateStrengthMpa if req.ultimateStrengthMpa > 0 else FALLBACK_ULTIMATE_MPA
    material = FatigueMaterial(
        ultimate_pa=ultimate_mpa * 1e6,
        yield_pa=yield_mpa * 1e6,
        endurance_limit_pa=req.enduranceLimitMpa * 1e6 if req.enduranceLimitMpa > 0 else None,
        surface=req.surfaceFinish if req.surfaceFinish else "machined",
    )
    r = fatigue_check(moment_nm, torque_nm, d_m, material, REQUIRED_FATIGUE_FACTOR)
    return _cap(r.goodman_factor), _cap(r.soderberg_factor), _cap(r.yield_factor)


@app.post("/predict", response_model=ShaftPredictResponse)
def predict(req: ShaftPredictRequest):
    model = _state["model"]
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    t, d, h = req.ratedTorqueNm, req.impellerDiameterM, req.liquidHeightM
    tau_mpa = req.allowableShearStressMpa
    tau_pa = tau_mpa * 1e6

    numbers = (t, d, h, tau_mpa, req.operatingSpeedRpm, req.impellerMassKg,
               req.elasticModulusGpa, req.poissonRatio, req.densityKgM3,
               req.yieldStrengthMpa, req.ultimateStrengthMpa, req.enduranceLimitMpa)
    if not all(math.isfinite(v) for v in numbers):
        raise HTTPException(status_code=422, detail="Inputs must be finite numbers")

    mass_lo = _state["ranges"][PARAM_NAMES.index("impeller_mass_kg")][0]
    p_values = [t, d, h, tau_mpa, req.operatingSpeedRpm, max(req.impellerMassKg, mass_lo),
                req.elasticModulusGpa, req.densityKgM3]
    in_range = _in_trusted_ranges(p_values)

    raw_formula_unrounded_m = raw_shaft_diameter_m(t, d, h, tau_pa)
    formula_std_m = formula_shaft_diameter_m(t, d, h, tau_pa)
    length_m = shaft_length_m(h)
    check_std_m = req.shaftDiameterMm / 1000.0 if req.shaftDiameterMm > 0 else formula_std_m

    p = make_params(t, d, h, tau_mpa, req.operatingSpeedRpm, req.impellerMassKg,
                    req.elasticModulusGpa, req.densityKgM3)

    notes = []
    if in_range:
        with torch.no_grad():
            raw_pred_m = float(model.diameter_m(p).item())
    else:
        # Never extrapolate the network outside its trusted range: fall back to the formula.
        raw_pred_m = raw_formula_unrounded_m
        notes.append(
            "Input outside trusted range, PINN skipped and formula value returned "
            f"(torque {TRUSTED_TORQUE_NM[0]}-{TRUSTED_TORQUE_NM[1]} N*m, "
            f"impeller {TRUSTED_IMPELLER_DIAMETER_M[0]}-{TRUSTED_IMPELLER_DIAMETER_M[1]} m, "
            f"liquid height {TRUSTED_LIQUID_HEIGHT_M[0]}-{TRUSTED_LIQUID_HEIGHT_M[1]} m, "
            "allowable stress, speed, impeller mass, modulus and density within the trained ranges)."
        )

    pred_std_m = round_up_to_standard_size_m(raw_pred_m)

    # gap on raw (unrounded) values so it is not hidden by standard sizes
    gap_pct = (raw_pred_m - raw_formula_unrounded_m) / raw_formula_unrounded_m * 100.0

    stress_pa = combined_shear_stress_pa(t, d, h, pred_std_m)
    if stress_pa > tau_pa:
        notes.append("Shear stress exceeds allowable at predicted size.")
    if pred_std_m != check_std_m:
        notes.append("Predicted standard size differs from recommended standard size.")

    # ---- Physics block at the recommended standard size (the size the checks use) ----
    design_t = design_torque_nm(t)
    if in_range:
        with torch.no_grad():
            ev = evaluate_design(model, p, d_m=check_std_m, nu=req.poissonRatio)
        moment_nm = float(ev["root_moment_nm"].item())
        tip_defl_m = float(ev["tip_deflection_m"].item())
        twist_rad = float(ev["tip_twist_rad"].item())
        tau_max_pa = float(ev["tau_max_pa"].item())
        von_mises_pa = float(ev["von_mises_pa"].item())
        crit_rpm = float(ev["critical_speed_rpm"].item())
        pde = pde_residual_value(model, p)
    else:
        e_pa = req.elasticModulusGpa * 1e9
        g_pa = e_pa / (2.0 * (1.0 + req.poissonRatio))
        force_n = radial_force_n(t, d)
        moment_nm = bending_moment_nm(t, d, h)
        tip_defl_m = analytic_tip_deflection_m(force_n, length_m, e_pa, check_std_m)
        twist_rad = analytic_twist_rad(design_t, length_m, g_pa, check_std_m)
        tau_max_pa = tresca_shear_stress_pa(moment_nm, design_t, check_std_m)
        von_mises_pa = von_mises_stress_pa(moment_nm, design_t, check_std_m)
        crit_rpm = critical_speed_rpm(check_std_m, length_m, req.impellerMassKg, e_pa,
                                      req.densityKgM3, method="closed_form")
        pde = 0.0

    goodman, soderberg, yield_f = _fatigue_factors(req, moment_nm, design_t, check_std_m)

    physics = ShaftPhysicsBlock(
        tipDeflectionMm=tip_defl_m * 1000.0,
        twistDeg=math.degrees(twist_rad),
        rootBendingMomentNm=moment_nm,
        maxShearStressMpa=tau_max_pa / 1e6,
        vonMisesStressMpa=von_mises_pa / 1e6,
        criticalSpeedRpm=crit_rpm,
        goodmanFactor=goodman,
        soderbergFactor=soderberg,
        yieldFactor=yield_f,
        pdeResidual=pde,
    )

    return ShaftPredictResponse(
        predictedShaftDiameterMm=round(pred_std_m * 1000.0, 1),
        rawPredictedDiameterMm=round(raw_pred_m * 1000.0, 2),
        formulaShaftDiameterMm=round(formula_std_m * 1000.0, 1),  # standard (rounded-up) size
        rawFormulaDiameterMm=round(raw_formula_unrounded_m * 1000.0, 2),
        gapPercent=round(gap_pct, 3),
        shearStressMpa=round(stress_pa / 1e6, 3),
        allowableShearStressMpa=round(tau_mpa, 3),
        withinTrustedRange=in_range,
        modelVersion=MODEL_VERSION,
        notes=" ".join(notes),
        shaftLengthM=round(length_m, 4),
        physics=physics,
    )


# ---------------------------------------------------------------------------
# Predictor for accuracy_report.py (FEA dataset case -> PINN values at position xi)
# ---------------------------------------------------------------------------
def _load_model_from_disk():
    if _state["model"] is None:
        ckpt = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
        model = ShaftPINN(hidden=ckpt["hidden"], n_layers=ckpt["n_layers"])
        model.load_state_dict(ckpt["model_state"])
        model.eval()
        _state["model"] = model
        if ckpt.get("trusted_ranges"):
            _state["ranges"] = [tuple(r) for r in ckpt["trusted_ranges"]]
    return _state["model"]


def predict_case(inputs: dict, xi: float) -> dict:
    """
    inputs : one "inputs" record from fea_dataset_generator.py (SI units)
    xi     : position along the shaft, x / L
    Returns diameter_m (PINN raw diameter), deflection_m and twist_rad at xi for the case's
    shaft_diameter_m, and the mean PDE residual.
    """
    model = _load_model_from_disk()

    p = make_params(inputs["rated_torque_nm"], inputs["impeller_diameter_m"],
                    inputs["liquid_height_m"], elastic_modulus_gpa=inputs["e_pa"] / 1e9)

    d_case = inputs["shaft_diameter_m"]
    xi_t = torch.tensor([[float(xi)]], dtype=p.dtype)

    with torch.no_grad():
        diameter_m = float(model.diameter_m(p).item())
        fields = model.field_net(xi_t, p)[0]

    e_pa = inputs["e_pa"]
    g_pa = inputs["g_pa"]
    length = inputs["shaft_length_m"]
    force = inputs["radial_force_n"]
    ei = e_pa * math.pi * d_case ** 4 / 64.0
    gj = g_pa * math.pi * d_case ** 4 / 32.0

    return {
        "diameter_m": diameter_m,
        "deflection_m": float(fields[pl.W]) * force * length ** 3 / ei,
        "twist_rad": float(fields[pl.PHI]) * inputs["design_torque_nm"] * length / gj,
        "pde_residual": pde_residual_value(model, p),
    }