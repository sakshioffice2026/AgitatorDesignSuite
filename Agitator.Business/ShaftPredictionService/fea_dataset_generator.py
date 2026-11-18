"""
fea_dataset_generator.py

Builds the independent ground-truth dataset for the shaft PINN using the same FEA stack
as CadScripts/fea_check_shaft.py (FreeCAD FEM + Gmsh + CalculiX).

Run with FreeCAD's bundled python.exe:

    python.exe fea_dataset_generator.py --output fea_dataset.jsonl --samples 200 --seed 1

Add --plan-only to list the sweep cases without running FEA (plain python is enough).

Sweep
-----
    Latin-hypercube sample of (rated torque, impeller diameter, liquid height) over the
    trusted ranges in shaft_physics.py. For every sample three shaft diameters are solved:
    the standard size below, the formula size, and the standard size above, so the data
    holds both passing and failing designs.

Load case (identical to fea_check_shaft.py)
    cantilever, fixed at z = 0, torque as a couple on the two half end-faces,
    radial force at z = L.

Output (one JSON object per line, appended; reruns skip finished case ids)
    inputs      physical values of the case (SI)
    stations    n cross-sections along the shaft
                  xi, deflection_m, slope, twist_rad (FEA)
                  y    = [w, theta, M, V, phi]  non-dimensional, null where FEA has no value
                  mask = [1, 1, 0, 0, 1]
    results     FEA max shear, FEA max von Mises, tip deflection, tip twist, mesh size

Non-dimensional scaling and sign convention match pinn_loss.py:
    w     = -u_y   * E I / (F L^3)       theta = -du_y/dz * E I / (F L^2)
    phi   =  twist * G J / (T L)
    M and V come from static equilibrium of the cantilever, not from FEA, so mask = 0.
"""

import argparse
import json
import math
import os
import random
import sys
import traceback

from shaft_physics import (
    SHAFT_LENGTH_FACTOR,
    STANDARD_SHAFT_SIZES_MM,
    TRUSTED_TORQUE_NM,
    TRUSTED_IMPELLER_DIAMETER_M,
    TRUSTED_LIQUID_HEIGHT_M,
    design_torque_nm,
    radial_force_n,
    raw_shaft_diameter_m,
    round_up_to_standard_size_m,
)

DEFAULT_E_PA = 210e9          # same as fea_check_shaft.py (210000 MPa)
DEFAULT_NU = 0.30
DEFAULT_STATIONS = 21
MIN_SLENDERNESS = 4.0         # L / d below this is not beam-like; case is skipped
END_EXCLUDE_FRACTION = 0.05

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CAD_SCRIPTS = os.path.normpath(
    os.path.join(HERE, "..", "..", "src", "AgitatorDesignSuite.Web", "CadScripts")
)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
def latin_hypercube(n: int, dims: int, rng: random.Random):
    """n points in [0, 1)^dims, one point per stratum in every dimension."""
    columns = []
    for _ in range(dims):
        strata = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(strata)
        columns.append(strata)
    return [[columns[d][i] for d in range(dims)] for i in range(n)]


def log_scale(u: float, lo: float, hi: float) -> float:
    return math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo)))


def neighbouring_standard_sizes_mm(raw_d_m: float):
    """Standard size below, formula size (rounded up), standard size above."""
    formula_mm = round(round_up_to_standard_size_m(raw_d_m) * 1000.0)
    sizes = [s for s in STANDARD_SHAFT_SIZES_MM]
    if formula_mm not in sizes:
        return [formula_mm]
    i = sizes.index(formula_mm)
    out = []
    if i > 0:
        out.append(sizes[i - 1])
    out.append(sizes[i])
    if i < len(sizes) - 1:
        out.append(sizes[i + 1])
    return out


def build_cases(samples: int, seed: int, e_pa: float, nu: float,
                torque_range, imp_range, height_range):
    rng = random.Random(seed)
    points = latin_hypercube(samples, 3, rng)
    cases = []
    skipped = 0

    for idx, (u_t, u_i, u_h) in enumerate(points):
        torque = log_scale(u_t, *torque_range)
        imp_d = log_scale(u_i, *imp_range)
        height = log_scale(u_h, *height_range)
        length = height * SHAFT_LENGTH_FACTOR

        raw_d = raw_shaft_diameter_m(torque, imp_d, height)
        for d_mm in neighbouring_standard_sizes_mm(raw_d):
            d_m = d_mm / 1000.0
            if length / d_m < MIN_SLENDERNESS:
                skipped += 1
                continue
            cases.append({
                "case_id": "s%d-%04d-d%d" % (seed, idx, d_mm),
                "rated_torque_nm": torque,
                "impeller_diameter_m": imp_d,
                "liquid_height_m": height,
                "shaft_length_m": length,
                "shaft_diameter_m": d_m,
                "design_torque_nm": design_torque_nm(torque),
                "radial_force_n": radial_force_n(torque, imp_d),
                "e_pa": e_pa,
                "nu": nu,
                "g_pa": e_pa / (2.0 * (1.0 + nu)),
            })
    return cases, skipped


# ---------------------------------------------------------------------------
# Field extraction from the FEA result
# ---------------------------------------------------------------------------
def extract_stations(points, length_mm: float, n_stations: int):
    """
    points : list of (x, y, z, ux, uy, uz) in mm
    Returns stations with mean deflection u_y and twist, each from a z-window of nodes.
    """
    half = length_mm / (2.0 * (n_stations - 1))
    stations = []

    for i in range(n_stations):
        z_c = i * length_mm / (n_stations - 1)
        window = [p for p in points if z_c - half <= p[2] <= z_c + half]
        if len(window) < 4:
            continue

        n = float(len(window))
        xm = sum(p[0] for p in window) / n
        ym = sum(p[1] for p in window) / n
        zm = sum(p[2] for p in window) / n
        uxm = sum(p[3] for p in window) / n
        uym = sum(p[4] for p in window) / n

        num = 0.0
        den = 0.0
        for x, y, _z, ux, uy, _uz in window:
            dx, dy = x - xm, y - ym
            num += dx * (uy - uym) - dy * (ux - uxm)
            den += dx * dx + dy * dy
        twist = num / den if den > 0.0 else 0.0

        stations.append({"z_mm": zm, "uy_mm": uym, "twist_rad": twist})
    return stations


def finite_difference_slopes(z_m, w_m):
    """dw/dz with central differences inside, one-sided at the ends."""
    n = len(z_m)
    slopes = []
    for i in range(n):
        a = max(i - 1, 0)
        b = min(i + 1, n - 1)
        slopes.append((w_m[b] - w_m[a]) / (z_m[b] - z_m[a]))
    return slopes


def nondimensionalise(case: dict, stations: list):
    d = case["shaft_diameter_m"]
    length = case["shaft_length_m"]
    force = case["radial_force_n"]
    torque = case["design_torque_nm"]

    ei = case["e_pa"] * math.pi * d ** 4 / 64.0
    gj = case["g_pa"] * math.pi * d ** 4 / 32.0

    z_m = [s["z_mm"] * 1e-3 for s in stations]
    w_m = [s["uy_mm"] * 1e-3 for s in stations]
    slopes = finite_difference_slopes(z_m, w_m)

    out = []
    for s, z, w, slope in zip(stations, z_m, w_m, slopes):
        out.append({
            "xi": z / length,
            "deflection_m": w,
            "slope": slope,
            "twist_rad": s["twist_rad"],
            "y": [
                -w * ei / (force * length ** 3),
                -slope * ei / (force * length ** 2),
                None,
                None,
                s["twist_rad"] * gj / (torque * length),
            ],
            "mask": [1, 1, 0, 0, 1],
        })
    return out


# ---------------------------------------------------------------------------
# FEA (FreeCAD FEM + CalculiX), same model as fea_check_shaft.py
# ---------------------------------------------------------------------------
def solve_case(case: dict, bin_dir: str, n_stations: int, helpers) -> dict:
    import FreeCAD as App
    import Part
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools

    helpers.configure_solver_paths(App, bin_dir)

    diameter_mm = case["shaft_diameter_m"] * 1000.0
    length_mm = case["shaft_length_m"] * 1000.0
    torque_nmm = case["design_torque_nm"] * 1000.0
    force_n = case["radial_force_n"]
    radius_mm = diameter_mm / 2.0

    doc = App.newDocument("ShaftFEADataset")
    try:
        half_a = Part.makeCylinder(radius_mm, length_mm, App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180)
        half_b = Part.makeCylinder(radius_mm, length_mm, App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180)
        half_b.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180)
        shaft = doc.addObject("Part::Feature", "Shaft")
        shaft.Shape = half_a.fuse(half_b)
        doc.recompute()

        bottom_faces = helpers.faces_at_z(shaft.Shape, 0.0)
        top_faces = helpers.faces_at_z(shaft.Shape, length_mm)

        analysis = ObjectsFem.makeAnalysis(doc, "Analysis")

        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "CalculiX")
        solver.AnalysisType = "static"
        solver.GeometricalNonlinearity = "linear"
        solver.ThermoMechSteadyState = False
        solver.MatrixSolverType = "default"
        solver.IterationsControlParameterTimeUse = False
        analysis.addObject(solver)

        material = ObjectsFem.makeMaterialSolid(doc, "Steel")
        mat = material.Material
        mat["Name"] = "Steel"
        mat["YoungsModulus"] = "%.3f MPa" % (case["e_pa"] / 1e6)
        mat["PoissonRatio"] = "%.4f" % case["nu"]
        mat["Density"] = "7850 kg/m^3"
        material.Material = mat
        analysis.addObject(material)

        fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
        fixed.References = [(shaft, name) for name, _ in bottom_faces]
        analysis.addObject(fixed)

        for index, (face_name, face) in enumerate(top_faces):
            side = 1.0 if face.CenterOfMass.y > 0 else -1.0
            fx = -side * 3.0 * math.pi * torque_nmm / (8.0 * radius_mm)
            fy = force_n / 2.0
            magnitude = math.hypot(fx, fy)
            line = doc.addObject("Part::Feature", "Direction%d" % index)
            line.Shape = Part.makeLine(
                App.Vector(0, 0, 0),
                App.Vector(fx / magnitude, fy / magnitude, 0),
            )
            doc.recompute()
            load = ObjectsFem.makeConstraintForce(doc, "Load%d" % index)
            load.References = [(shaft, face_name)]
            load.Force = "%.6f N" % magnitude
            load.Direction = (line, ["Edge1"])
            load.Reversed = False
            analysis.addObject(load)

        mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
        mesh.Shape = shaft
        mesh.ElementOrder = "2nd"
        mesh.CharacteristicLengthMax = "%.6f mm" % max(radius_mm / 2.0, length_mm / 200.0)
        mesh.CharacteristicLengthMin = "%.6f mm" % (radius_mm / 8.0)
        analysis.addObject(mesh)
        doc.recompute()

        error = GmshTools(mesh).create_mesh()
        if error:
            raise RuntimeError("Gmsh error: %s" % error)
        doc.recompute()

        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        fea.setup_working_dir()
        fea.setup_ccx()
        problem = fea.check_prerequisites()
        if problem:
            raise RuntimeError("CalculiX prerequisites: %s" % problem)
        fea.purge_results()
        fea.write_inp_file()
        fea.ccx_run()
        fea.load_results()

        result = None
        for obj in analysis.Group:
            if obj.isDerivedFrom("Fem::FemResultObject"):
                result = obj
        if result is None or not result.MaxShear:
            raise RuntimeError("No FEA result")

        nodes = mesh.FemMesh.Nodes
        points = []
        for node_id, disp in zip(result.NodeNumbers, result.DisplacementVectors):
            n = nodes[node_id]
            points.append((n.x, n.y, n.z, disp.x, disp.y, disp.z))

        end_margin = max(END_EXCLUDE_FRACTION * length_mm, 0.75 * diameter_mm)
        z_lo, z_hi = end_margin, length_mm - end_margin
        if z_hi <= z_lo:
            raise RuntimeError("Shaft too short for stress window")

        tau_max = 0.0
        vm_max = 0.0
        von_mises = list(result.vonMises) if getattr(result, "vonMises", None) else []
        for k, (node_id, tau) in enumerate(zip(result.NodeNumbers, result.MaxShear)):
            z = nodes[node_id].z
            if z_lo <= z <= z_hi:
                tau_max = max(tau_max, tau)
                if k < len(von_mises):
                    vm_max = max(vm_max, von_mises[k])

        stations = extract_stations(points, length_mm, n_stations)
        if len(stations) < 3:
            raise RuntimeError("Too few stations extracted")

        fields = nondimensionalise(case, stations)
        return {
            "stations": fields,
            "results": {
                "fea_max_shear_mpa": tau_max,
                "fea_max_von_mises_mpa": vm_max,
                "tip_deflection_m": fields[-1]["deflection_m"],
                "tip_twist_rad": fields[-1]["twist_rad"],
                "mesh_nodes": len(nodes),
            },
        }
    finally:
        App.closeDocument(doc.Name)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def load_finished_ids(path: str) -> set:
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["inputs"]["case_id"])
            except (ValueError, KeyError):
                continue
    return done


def parse_range(text: str, default):
    if not text:
        return default
    lo, hi = (float(v) for v in text.split(","))
    return (lo, hi)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="JSONL dataset path (appended)")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--stations", type=int, default=DEFAULT_STATIONS)
    parser.add_argument("--e-pa", type=float, default=DEFAULT_E_PA)
    parser.add_argument("--nu", type=float, default=DEFAULT_NU)
    parser.add_argument("--torque-range", default="", help="lo,hi in N*m")
    parser.add_argument("--impeller-range", default="", help="lo,hi in m")
    parser.add_argument("--height-range", default="", help="lo,hi in m")
    parser.add_argument("--cad-scripts", default=DEFAULT_CAD_SCRIPTS)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(sys.argv[1:])

    cases, skipped = build_cases(
        args.samples, args.seed, args.e_pa, args.nu,
        parse_range(args.torque_range, TRUSTED_TORQUE_NM),
        parse_range(args.impeller_range, TRUSTED_IMPELLER_DIAMETER_M),
        parse_range(args.height_range, TRUSTED_LIQUID_HEIGHT_M),
    )
    print("planned cases: %d  (skipped as too stubby: %d)" % (len(cases), skipped))

    if args.plan_only:
        for c in cases:
            print("%s  T=%.1f N*m  Dimp=%.3f m  H=%.3f m  d=%.0f mm" % (
                c["case_id"], c["rated_torque_nm"], c["impeller_diameter_m"],
                c["liquid_height_m"], c["shaft_diameter_m"] * 1000.0))
        return 0

    if args.cad_scripts not in sys.path:
        sys.path.insert(0, args.cad_scripts)
    import fea_check_shaft as helpers

    bin_dir = helpers.bootstrap_freecad_paths()
    done = load_finished_ids(args.output)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    ok = failed = 0
    with open(args.output, "a", encoding="utf-8") as out:
        for case in cases:
            if case["case_id"] in done:
                continue
            record = {"inputs": case, "status": "ok", "error": None}
            try:
                record.update(solve_case(case, bin_dir, args.stations, helpers))
                ok += 1
            except Exception as exc:
                traceback.print_exc()
                record.update({"status": "failed", "error": str(exc),
                               "stations": [], "results": {}})
                failed += 1
            out.write(json.dumps(record) + "\n")
            out.flush()
            print("%s  %s" % (case["case_id"], record["status"]))

    print("done: %d ok, %d failed" % (ok, failed))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
