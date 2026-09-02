"""
generate_agitator_model.py

Headless FreeCAD macro that builds a parametric agitator assembly
(vessel + shaft + impeller + baffles) from a JSON parameter file, then
exports a STEP file (CAD-grade, for download/import into other CAD tools)
and an OBJ mesh (lightweight, for the Three.js browser preview).

NOTE: FreeCAD's headless Mesh module does not support writing .gltf/.glb
(glTF export only exists in the GUI-only ImportGui module) — OBJ is used
here instead since Mesh.write() supports it natively in headless mode.

Invoked by CadJobBackgroundService.cs as:
    python.exe generate_agitator_model.py --params params.json

This is a starting skeleton, not a finished parametric model — extend the
geometry building blocks below (vessel head type, multiple impellers,
baffle placement, shaft/impeller fillets, etc.) to match your real design
standards.
"""

import sys
import json
import argparse

import FreeCAD as App
import Part


def build_vessel(doc, diameter_m, liquid_height_m):
    radius_mm = (diameter_m * 1000) / 2
    height_mm = liquid_height_m * 1000
    vessel_shell = Part.makeCylinder(radius_mm, height_mm)
    obj = doc.addObject("Part::Feature", "Vessel")
    obj.Shape = vessel_shell
    return obj


def build_shaft(doc, length_mm, diameter_mm=50.0):
    shaft = Part.makeCylinder(diameter_mm / 2, length_mm)
    obj = doc.addObject("Part::Feature", "Shaft")
    obj.Shape = shaft
    return obj


def build_impeller_disc(doc, diameter_m, index, z_offset_mm):
    """
    Simplified placeholder impeller representation (a thin disc at the
    correct elevation). Replace with real blade geometry per impeller
    type (Rushton disc+blades, pitched blade, hydrofoil, anchor sweep,
    etc.) — this keeps the pipeline working end-to-end while you build
    out accurate geometry per type.
    """
    radius_mm = (diameter_m * 1000) / 2
    disc = Part.makeCylinder(radius_mm, 20.0)
    disc.translate(App.Vector(0, 0, z_offset_mm))
    obj = doc.addObject("Part::Feature", f"Impeller_{index}")
    obj.Shape = disc
    return obj


def build_baffles(doc, vessel_diameter_m, liquid_height_m, count, width_ratio):
    baffle_objs = []
    vessel_radius_mm = (vessel_diameter_m * 1000) / 2
    height_mm = liquid_height_m * 1000
    width_mm = vessel_diameter_m * 1000 * width_ratio
    thickness_mm = 6.0

    for i in range(count):
        angle_deg = (360.0 / count) * i
        baffle = Part.makeBox(thickness_mm, width_mm, height_mm)
        baffle.translate(App.Vector(vessel_radius_mm - thickness_mm, -width_mm / 2, 0))
        baffle.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), angle_deg)
        obj = doc.addObject("Part::Feature", f"Baffle_{i+1}")
        obj.Shape = baffle
        baffle_objs.append(obj)

    return baffle_objs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    # freecadcmd passes its own args first; only parse known args.
    args, _ = parser.parse_known_args(sys.argv[1:])

    with open(args.params, "r") as f:
        p = json.load(f)

    doc = App.newDocument("AgitatorAssembly")

    build_vessel(doc, p["vesselDiameterM"], p["liquidHeightM"])

    shaft_length_mm = p["liquidHeightM"] * 1000 * 1.15  # a bit above liquid surface
    build_shaft(doc, shaft_length_mm)

    clearance_mm = p["vesselDiameterM"] * 1000 * p["clearanceToDiameterRatio"]
    for i in range(p["numberOfImpellers"]):
        z_offset = clearance_mm + i * (p["vesselDiameterM"] * 1000 * 0.5)
        build_impeller_disc(doc, p["impellerDiameterM"], i + 1, z_offset)

    if p.get("hasBaffles"):
        build_baffles(
            doc,
            p["vesselDiameterM"],
            p["liquidHeightM"],
            p["numberOfBaffles"],
            0.0833,
        )

    doc.recompute()

    # Export STEP (CAD-grade, downstream CAD tools)
    all_shapes = [o.Shape for o in doc.Objects if hasattr(o, "Shape")]
    compound = Part.makeCompound(all_shapes)
    compound.exportStep(p["outputStepPath"])

    # Export a lightweight OBJ mesh for the browser preview. Requires the
    # FreeCAD Mesh workbench; adjust tessellation tolerance for
    # preview quality vs. file size. outputMeshPath must end in a format
    # Mesh.write() supports headlessly (.obj, .stl, .ply, .off, .amf, .3mf) —
    # NOT .gltf/.glb, which require the GUI-only ImportGui module.
    import Mesh
    mesh_doc_objs = []
    for o in doc.Objects:
        if hasattr(o, "Shape"):
            m = Mesh.Mesh()
            m.addFacets(o.Shape.tessellate(1.0))
            mesh_doc_objs.append(m)

    combined_mesh = Mesh.Mesh()
    for m in mesh_doc_objs:
        combined_mesh.addMesh(m)
    combined_mesh.write(p["outputMeshPath"])

    print(f"STEP exported to {p['outputStepPath']}")
    print(f"Mesh exported to {p['outputMeshPath']}")


if __name__ == "__main__":
    main()