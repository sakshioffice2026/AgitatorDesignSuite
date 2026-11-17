"""
fea_check_shaft.py  (NEW FILE)

Per-project shaft stress check with FreeCAD FEM + CalculiX.
Run with FreeCAD's bundled python.exe, same as generate_agitator_model.py:

    python.exe fea_check_shaft.py --params fea_params.json --output fea_result.json

fea_params.json (all required):
    {
      "shaftDiameterMm":   60.0,     selected standard shaft size
      "shaftLengthMm":     1500.0,   total shaft length (fixed end to impeller)
      "designTorqueNm":    376.05,   rated torque x service factor
      "radialForceN":      3342.7,   hydraulic radial force at the impeller
      "allowableShearMpa": 62.4      allowable shear stress for this material/temperature
    }

fea_result.json:
    {
      "status": "ok" | "failed",
      "feaMaxShearMpa": 48.1,
      "allowableShearMpa": 62.4,
      "utilization": 0.771,
      "passed": true,
      "meshNodes": 12345,
      "error": null
    }

Model: cantilever shaft, fixed at z=0, torque (as a couple on two half end-faces)
plus radial force at z=L. Stress is read away from both end faces to avoid
load/support singularities. No bending safety factor is applied, so the FEA
stress is expected to be lower than the formula stress for bending-heavy cases.
"""

import argparse
import json
import math
import os
import sys
import traceback

END_EXCLUDE_FRACTION = 0.05


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

    if diameter_mm <= 0 or length_mm <= 0 or allowable_mpa <= 0:
        raise ValueError("Diameter, length and allowable stress must be positive")

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
        mat["YoungsModulus"] = "210000 MPa"
        mat["PoissonRatio"] = "0.30"
        mat["Density"] = "7850 kg/m^3"
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
        end_margin = max(END_EXCLUDE_FRACTION * length_mm, 0.75 * diameter_mm)
        z_lo = end_margin
        z_hi = length_mm - end_margin
        if z_hi <= z_lo:
            raise RuntimeError("Shaft too short for stress window")

        tau_max_mpa = 0.0
        for node_id, tau in zip(result.NodeNumbers, result.MaxShear):
            z = nodes[node_id].z
            if z_lo <= z <= z_hi and tau > tau_max_mpa:
                tau_max_mpa = tau

        if tau_max_mpa <= 0.0:
            raise RuntimeError("Zero stress result")

        return {
            "status": "ok",
            "feaMaxShearMpa": round(tau_max_mpa, 3),
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
            "allowableShearMpa": None,
            "utilization": None,
            "passed": None,
            "meshNodes": None,
            "error": str(exc),
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())
