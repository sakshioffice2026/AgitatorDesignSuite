"""
accuracy_report.py

Accuracy comparison of the four ways the shaft is calculated:

    manual    : the trusted formula in shaft_physics.py (includes the bending factor Km = 1.5)
    analytic  : plain beam / torsion theory (Km = 1), the textbook check on the FEA
    fea       : FreeCAD FEM + CalculiX ground truth from fea_dataset_generator.py
    pinn      : the physics-informed network

FEA is the reference. The PINN is accepted only if it matches the FEA, not the formula.

Quantities
    diameter    required (pre-rounding) shaft diameter
                  manual   = formula
                  analytic = sqrt(M^2 + T^2) torque equivalent, Km = 1
                  fea      = d * (tau_fea / allowable)^(1/3)   (stress scales with 1/d^3)
                  pinn     = predictor output
    deflection  lateral deflection at the last FEA station (xi close to 1)
    twist       angle of twist at the last FEA station

Predictor (PINN) is supplied in one of two ways:
    --predictor module:function
        function(inputs: dict, xi: float) -> dict with keys
            diameter_m     (required)  raw diameter for the case inputs
            deflection_m   (optional)  deflection at xi for inputs["shaft_diameter_m"]
            twist_rad      (optional)  twist at xi for inputs["shaft_diameter_m"]
            pde_residual   (optional)  mean PDE residual norm
    --predictions file.jsonl
        one JSON object per line: {"case_id": ..., "xi": ..., <same keys as above>}

Usage
    python accuracy_report.py --dataset fea_dataset.jsonl --predictor service:predict_case \
        --report accuracy_report.json
"""

import argparse
import importlib
import json
import math
import os
import sys

from shaft_physics import (
    ALLOWABLE_SHEAR_STRESS_PA,
    bending_moment_nm,
    design_torque_nm,
    raw_shaft_diameter_m,
)

# Acceptance: PINN vs FEA, mean absolute percentage error (edit after design review)
ACCEPTANCE_MAPE_PCT = {
    "diameter": 5.0,
    "deflection": 5.0,
    "twist": 5.0,
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics(pred, ref):
    """Error of pred against ref (ref is the truth)."""
    pairs = [(p, r) for p, r in zip(pred, ref)
             if p is not None and r is not None and r != 0.0
             and math.isfinite(p) and math.isfinite(r)]
    n = len(pairs)
    if n == 0:
        return {"count": 0}

    errs = [p - r for p, r in pairs]
    apes = [abs(e / r) * 100.0 for e, (_, r) in zip(errs, pairs)]
    mean_ref = sum(r for _, r in pairs) / n
    ss_res = sum(e * e for e in errs)
    ss_tot = sum((r - mean_ref) ** 2 for _, r in pairs)

    return {
        "count": n,
        "mae": sum(abs(e) for e in errs) / n,
        "rmse": math.sqrt(ss_res / n),
        "mape_pct": sum(apes) / n,
        "max_ape_pct": max(apes),
        "bias_pct": sum(e / r * 100.0 for e, (_, r) in zip(errs, pairs)) / n,
        "r2": (1.0 - ss_res / ss_tot) if ss_tot > 0.0 else None,
    }


# ---------------------------------------------------------------------------
# Reference values
# ---------------------------------------------------------------------------
def analytic_diameter_m(torque_nm, imp_d_m, liq_h_m, allowable_pa):
    """Textbook max-shear diameter: no bending factor, Te = sqrt(M^2 + T^2)."""
    m = bending_moment_nm(torque_nm, imp_d_m, liq_h_m)
    t = design_torque_nm(torque_nm)
    te = math.sqrt(m * m + t * t)
    return ((16.0 * te) / (math.pi * allowable_pa)) ** (1.0 / 3.0)


def analytic_deflection_m(inputs, xi):
    d = inputs["shaft_diameter_m"]
    ei = inputs["e_pa"] * math.pi * d ** 4 / 64.0
    f, length = inputs["radial_force_n"], inputs["shaft_length_m"]
    return f * length ** 3 / ei * (3.0 * xi ** 2 - xi ** 3) / 6.0


def analytic_twist_rad(inputs, xi):
    d = inputs["shaft_diameter_m"]
    gj = inputs["g_pa"] * math.pi * d ** 4 / 32.0
    return inputs["design_torque_nm"] * inputs["shaft_length_m"] / gj * xi


def fea_required_diameter_m(inputs, fea_tau_mpa, allowable_pa):
    return inputs["shaft_diameter_m"] * (fea_tau_mpa * 1e6 / allowable_pa) ** (1.0 / 3.0)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def load_dataset(path):
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") == "ok" and rec.get("stations"):
                records.append(rec)
    return records


def load_predictions(path):
    table = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                row = json.loads(line)
                table[row["case_id"]] = row
    return table


def load_predictor(spec):
    module_name, func_name = spec.split(":")
    return getattr(importlib.import_module(module_name), func_name)


def sample_key(case_id):
    return case_id.rsplit("-d", 1)[0]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def build_report(records, predictor, predictions, allowable_pa):
    rows = []
    for rec in records:
        inputs = rec["inputs"]
        last = rec["stations"][-1]
        xi = last["xi"]

        row = {
            "case_id": inputs["case_id"],
            "sample": sample_key(inputs["case_id"]),
            "xi": xi,
            "manual_d": raw_shaft_diameter_m(inputs["rated_torque_nm"],
                                             inputs["impeller_diameter_m"],
                                             inputs["liquid_height_m"], allowable_pa),
            "analytic_d": analytic_diameter_m(inputs["rated_torque_nm"],
                                              inputs["impeller_diameter_m"],
                                              inputs["liquid_height_m"], allowable_pa),
            "fea_d": fea_required_diameter_m(inputs, rec["results"]["fea_max_shear_mpa"],
                                             allowable_pa),
            "analytic_defl": analytic_deflection_m(inputs, xi),
            "fea_defl": abs(last["deflection_m"]),
            "analytic_twist": analytic_twist_rad(inputs, xi),
            "fea_twist": abs(last["twist_rad"]),
            "pinn_d": None, "pinn_defl": None, "pinn_twist": None, "pde_residual": None,
        }

        pred = None
        if predictor is not None:
            pred = predictor(inputs, xi)
        elif predictions is not None:
            pred = predictions.get(inputs["case_id"])
        if pred:
            row["pinn_d"] = pred.get("diameter_m")
            row["pinn_defl"] = abs(pred["deflection_m"]) if pred.get("deflection_m") is not None else None
            row["pinn_twist"] = abs(pred["twist_rad"]) if pred.get("twist_rad") is not None else None
            row["pde_residual"] = pred.get("pde_residual")
        rows.append(row)

    # Diameter does not depend on the shaft size tried, so use one value per sample
    by_sample = {}
    for r in rows:
        by_sample.setdefault(r["sample"], []).append(r)
    diam_rows = []
    for group in by_sample.values():
        fea_vals = [g["fea_d"] for g in group]
        diam_rows.append({
            "manual": group[0]["manual_d"],
            "analytic": group[0]["analytic_d"],
            "fea": sum(fea_vals) / len(fea_vals),
            "pinn": group[0]["pinn_d"],
        })

    def col(items, key):
        return [i[key] for i in items]

    comparisons = {
        "diameter": {
            "pinn_vs_fea": compute_metrics(col(diam_rows, "pinn"), col(diam_rows, "fea")),
            "pinn_vs_manual": compute_metrics(col(diam_rows, "pinn"), col(diam_rows, "manual")),
            "manual_vs_fea": compute_metrics(col(diam_rows, "manual"), col(diam_rows, "fea")),
            "analytic_vs_fea": compute_metrics(col(diam_rows, "analytic"), col(diam_rows, "fea")),
        },
        "deflection": {
            "pinn_vs_fea": compute_metrics(col(rows, "pinn_defl"), col(rows, "fea_defl")),
            "analytic_vs_fea": compute_metrics(col(rows, "analytic_defl"), col(rows, "fea_defl")),
        },
        "twist": {
            "pinn_vs_fea": compute_metrics(col(rows, "pinn_twist"), col(rows, "fea_twist")),
            "analytic_vs_fea": compute_metrics(col(rows, "analytic_twist"), col(rows, "fea_twist")),
        },
    }

    residuals = [r["pde_residual"] for r in rows if r["pde_residual"] is not None]
    verdict = {}
    for quantity, limit in ACCEPTANCE_MAPE_PCT.items():
        m = comparisons[quantity]["pinn_vs_fea"]
        if m.get("count", 0) == 0:
            verdict[quantity] = {"limit_mape_pct": limit, "mape_pct": None, "passed": None}
        else:
            verdict[quantity] = {"limit_mape_pct": limit, "mape_pct": m["mape_pct"],
                                 "passed": m["mape_pct"] <= limit}

    return {
        "cases": len(rows),
        "samples": len(diam_rows),
        "allowable_shear_mpa": allowable_pa / 1e6,
        "comparisons": comparisons,
        "pde_residual_mean": (sum(residuals) / len(residuals)) if residuals else None,
        "acceptance": verdict,
        "all_passed": all(v["passed"] for v in verdict.values() if v["passed"] is not None)
                      and any(v["passed"] is not None for v in verdict.values()),
    }


def print_report(report):
    print("cases: %d   samples: %d   allowable shear: %.1f MPa" % (
        report["cases"], report["samples"], report["allowable_shear_mpa"]))
    header = "%-12s %-16s %6s %10s %10s %9s %8s" % (
        "quantity", "comparison", "n", "MAPE %", "max APE %", "bias %", "R2")
    print(header)
    print("-" * len(header))
    for quantity, comps in report["comparisons"].items():
        for name, m in comps.items():
            if m.get("count", 0) == 0:
                print("%-12s %-16s %6d %10s" % (quantity, name, 0, "n/a"))
                continue
            r2 = "%8.4f" % m["r2"] if m["r2"] is not None else "     n/a"
            print("%-12s %-16s %6d %10.3f %10.3f %9.3f %s" % (
                quantity, name, m["count"], m["mape_pct"], m["max_ape_pct"], m["bias_pct"], r2))
    if report["pde_residual_mean"] is not None:
        print("mean PDE residual: %.3e" % report["pde_residual_mean"])
    print()
    for quantity, v in report["acceptance"].items():
        status = "n/a" if v["passed"] is None else ("PASS" if v["passed"] else "FAIL")
        mape = "n/a" if v["mape_pct"] is None else "%.3f" % v["mape_pct"]
        print("%-12s PINN vs FEA  MAPE %s %%  (limit %.1f %%)  %s" % (
            quantity, mape, v["limit_mape_pct"], status))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, help="JSONL from fea_dataset_generator.py")
    parser.add_argument("--predictor", default="", help="module:function (PINN predictor)")
    parser.add_argument("--predictions", default="", help="JSONL of PINN predictions")
    parser.add_argument("--allowable-mpa", type=float, default=ALLOWABLE_SHEAR_STRESS_PA / 1e6)
    parser.add_argument("--report", default="", help="write the report as JSON")
    args = parser.parse_args(sys.argv[1:])

    records = load_dataset(args.dataset)
    if not records:
        print("no usable FEA records in %s" % args.dataset)
        return 2

    predictor = load_predictor(args.predictor) if args.predictor else None
    predictions = load_predictions(args.predictions) if args.predictions else None

    report = build_report(records, predictor, predictions, args.allowable_mpa * 1e6)
    print_report(report)

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
