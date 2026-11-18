"""
fea_check_shaft.py

Per-project shaft check with FreeCAD FEM + CalculiX.
Run with FreeCAD's bundled python.exe, same as generate_agitator_model.py:

    python.exe fea_check_shaft.py --params fea_params.json --output fea_result.json

fea_params.json (required):
    {
      "shaftDiameterMm":   60.0,     selected standard shaft size
      "shaftLengthMm":     1500.0,   total shaft length (fixed end to impeller)
      "designTorqueNm":    376.05,   rated torque x service factor
      "radialForceN":      3342.7,   hydraulic radial force at the impeller
      "allowableShearMpa": 62.4      allowable shear stress for this material/temperature
    }

fea_params.json (optional, defaults keep the old behaviour):
    {
      "elasticModulusGpa": 210.0,
      "poissonRatio":      0.30,
      "densityKgM3":       7850.0
    }

fea_result.json:
    {
      "status": "ok" | "failed",
      "feaMaxShearMpa": 48.1,        max shear stress (Tresca) in the stress window
      "feaVonMisesMpa": 83.2,        max von Mises stress in the stress window
      "feaTipDeflectionMm": 1.23,    lateral deflection of the free end (along the load)
      "feaTwistDeg": 0.045,          rigid-body twist of the free end face about the shaft axis
      "allowableShearMpa": 62.4,
      "utilization": 0.771,
      "passed": true,
      "meshNodes": 12345,
      "error": null
    }

Model: cantilever shaft, fixed at z=0, torque (as a couple on two half end-faces)
plus radial force at z=L. Stress is read away from both end faces to avoid
load/support singularities. Deflection and twist are read on the free end face:
deflection is the mean displacement along the load direction (y), twist is the
least-squares rigid rotation about z of the end face after removing its translation.
No bending safety factor is applied, so the FEA stress is expected to be lower than
the formula stress for bending-heavy cases.
"""

import argparse
import json
import math
import os
import sys
import traceback

END_EXCLUDE_FRACTION = 0.05

DEFAULT_ELASTIC_MODULUS_GPA = 200.0
DEFAULT_POISSON_RATIO = 0.30
DEFAULT_DENSITY_KG_M3 = 7850.0


def bootstrap_freecad_paths():
    """Make FEM modules importable when running under FreeCAD's plain python.exe."""
    bin_dir = os.path.dirname(os.path.abspath(sys.executable))
    root = os.path.dirname(bin_dir)
    candidates = [
        bin_dir,
        os.path.join(root, "lib"),
        os.path.join(root, "Ext"),
        os.path.join(root, "Mod"),
        os.path.join(root, "Mod", "Fem"),
        os.path.join(root, "Mod", "Part"),
    ]
    for path in candidates:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)
    return bin_dir


def configure_solver_paths(App, bin_dir):
    """Point FreeCAD FEM at the bundled ccx and gmsh executables."""
    ccx = os.path.join(bin_dir, "ccx.exe")
    gmsh = os.path.join(bin_dir, "gmsh.exe")

    if os.path.exists(ccx):
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx")
        prefs.SetBool("UseStandardCcxLocation", False)
        prefs.SetString("ccxBinaryPath", ccx)

    if os.path.exists(gmsh):
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh")
        prefs.SetBool("UseStandardGmshLocation", False)
        prefs.SetString("gmshBinaryPath", gmsh)


def write_result(path, payload):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def faces_at_z(shape, z_target, tol=1e-4):
    found = []
    for i, face in enumerate(shape.Faces, start=1):
        if abs(face.CenterOfMass.z - z_target) < tol and abs(face.normalAt(0, 0).z) > 0.99:
            found.append(("Face%d" % i, face))
    if not found:
        raise RuntimeError("End faces not found at z=%s" % z_target)
    return found


def end_face_motion(nodes, result, length_mm):
    """
    Free-end deflection (mm, along y, the load direction) and twist (rad, about z).

    Deflection : mean y displacement of the free end face nodes.
    Twist      : least-squares rigid rotation about the face centroid after removing the
                 mean translation, phi = sum(x*duy - y*dux) / sum(x^2 + y^2).
    Returns (None, None) when the displacement result is not available.
    """
    vectors = getattr(result, "DisplacementVectors", None)
    if not vectors:
        return None, None

    tol = max(1e-4, 1e-6 * length_mm)
    points = []
    for node_id, disp in zip(result.NodeNumbers, vectors):
        node = nodes[node_id]
        if abs(node.z - length_mm) <= tol:
            points.append((node.x, node.y, disp.x, disp.y))

    if len(points) < 3:
        return None, None

    n = float(len(points))
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    ux0 = sum(p[2] for p in points) / n
    uy0 = sum(p[3] for p in points) / n

    numerator = 0.0
    denominator = 0.0
    for x, y, ux, uy in points:
        rx = x - cx
        ry = y - cy
        numerator += rx * (uy - uy0) - ry * (ux - ux0)
        denominator += rx * rx + ry * ry

    if denominator <= 0.0:
        return abs(uy0), None

    return abs(uy0), abs(numerator / denominator)


def run_fea(params, bin_dir):
    import FreeCAD as App
    import Part
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools

    configure_solver_paths(App, bin_dir)

    diameter_mm = float(params["shaftDiameterMm"])
    length_mm = float(params["shaftLengthMm"])
    design_torque_nmm = float(params["designTorqueNm"]) * 1000.0
    radial_force_n = float(params["radialForceN"])
    allowable_mpa = float(params["allowableShearMpa"])

    elastic_modulus_gpa = float(params.get("elasticModulusGpa") or DEFAULT_ELASTIC_MODULUS_GPA)
    poisson_ratio = float(params.get("poissonRatio") or DEFAULT_POISSON_RATIO)
    density_kg_m3 = float(params.get("densityKgM3") or DEFAULT_DENSITY_KG_M3)

    if diameter_mm <= 0 or length_mm <= 0 or allowable_mpa <= 0:
        raise ValueError("Diameter, length and allowable stress must be positive")
    if elastic_modulus_gpa <= 0 or density_kg_m3 <= 0 or not (-1.0 < poisson_ratio < 0.5):
        raise ValueError("Elastic modulus and density must be positive, Poisson ratio in (-1, 0.5)")

    radius_mm = diameter_mm / 2.0

    doc = App.newDocument("ShaftFEACheck")
    try:
        half_a = Part.makeCylinder(radius_mm, length_mm, App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180)
        half_b = Part.makeCylinder(radius_mm, length_mm, App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180)
        half_b.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180)
        shaft = doc.addObject("Part::Feature", "Shaft")
        shaft.Shape = half_a.fuse(half_b)
        doc.recompute()

        bottom_faces = faces_at_z(shaft.Shape, 0.0)
        top_faces = faces_at_z(shaft.Shape, length_mm)

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
        mat["YoungsModulus"] = "%.6f MPa" % (elastic_modulus_gpa * 1000.0)
        mat["PoissonRatio"] = "%.6f" % poisson_ratio
        mat["Density"] = "%.6f kg/m^3" % density_kg_m3
        material.Material = mat
        analysis.addObject(material)

        fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
        fixed.References = [(shaft, name) for name, _ in bottom_faces]
        analysis.addObject(fixed)

        for index, (face_name, face) in enumerate(top_faces):
            side = 1.0 if face.CenterOfMass.y > 0 else -1.0
            fx = -side * 3.0 * math.pi * design_torque_nmm / (8.0 * radius_mm)
            fy = radial_force_n / 2.0
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

        gmsh = GmshTools(mesh)
        error = gmsh.create_mesh()
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
        root_margin = 0.75 * diameter_mm
        tip_margin = max(END_EXCLUDE_FRACTION * length_mm, 0.75 * diameter_mm)
        z_lo = root_margin
        z_hi = length_mm - tip_margin
        if z_hi <= z_lo:
            raise RuntimeError("Shaft too short for stress window")

        von_mises_values = getattr(result, "vonMises", None)
        has_von_mises = bool(von_mises_values) and len(von_mises_values) == len(result.NodeNumbers)

        tau_max_mpa = 0.0
        von_mises_max_mpa = 0.0
        for index, node_id in enumerate(result.NodeNumbers):
            z = nodes[node_id].z
            if not (z_lo <= z <= z_hi):
                continue
            tau = result.MaxShear[index]
            if tau > tau_max_mpa:
                tau_max_mpa = tau
            if has_von_mises and von_mises_values[index] > von_mises_max_mpa:
                von_mises_max_mpa = von_mises_values[index]

        if tau_max_mpa <= 0.0:
            raise RuntimeError("Zero stress result")

        tip_deflection_mm, twist_rad = end_face_motion(nodes, result, length_mm)

        return {
            "status": "ok",
            "feaMaxShearMpa": round(tau_max_mpa, 3),
            "feaVonMisesMpa": round(von_mises_max_mpa, 3) if von_mises_max_mpa > 0.0 else None,
            "feaTipDeflectionMm": round(tip_deflection_mm, 4) if tip_deflection_mm is not None else None,
            "feaTwistDeg": round(math.degrees(twist_rad), 5) if twist_rad is not None else None,
            "allowableShearMpa": round(allowable_mpa, 3),
            "utilization": round(tau_max_mpa / allowable_mpa, 4),
            "passed": bool(tau_max_mpa <= allowable_mpa),
            "meshNodes": len(nodes),
            "error": None,
        }
    finally:
        App.closeDocument(doc.Name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[1:])

    bin_dir = bootstrap_freecad_paths()

    try:
        with open(args.params, "r", encoding="utf-8") as handle:
            params = json.load(handle)
        payload = run_fea(params, bin_dir)
        write_result(args.output, payload)
        return 0
    except Exception as exc:
        traceback.print_exc()
        write_result(args.output, {
            "status": "failed",
            "feaMaxShearMpa": None,
            "feaVonMisesMpa": None,
            "feaTipDeflectionMm": None,
            "feaTwistDeg": None,
            "allowableShearMpa": None,
            "utilization": None,
            "passed": None,
            "meshNodes": None,
            "error": str(exc),
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())