"""
generate_agitator_model.py

Headless FreeCAD script for the Agitator Design Suite.

This replacement keeps the existing params.json contract and existing
tank/head/flange/shaft/baffle/STEP/OBJ/TechDraw pipeline. The impeller
builders are upgraded from box/flat-plate placeholders to parametric
3-D blade geometry.

IMPORTANT:
- This is CAD geometry, not fabrication certification.
- Exact vendor blade coordinates/correlations must be supplied separately
  when a specific commercial impeller is required.
"""

import sys
import os
import json
import math
import argparse
import traceback

import FreeCAD as App
import Part


# ----------------------------------------------------------------------
# 1. PARAMETRIC INPUT MODEL
# ----------------------------------------------------------------------
class AgitatorParams:
    """Thin wrapper around params.json. Lengths are mm; angles are degrees."""

    def __init__(self, p: dict):
        # Tank
        self.tank_od_mm = float(p["tankOuterDiameterMm"])
        self.shell_height_mm = float(p["shellHeightMm"])
        self.wall_thickness_mm = float(p["wallThicknessMm"])
        self.head_type = p.get("headType", "Torispherical")

        # Flange / nozzle
        self.flange_nozzle_od_mm = float(
            p.get("flangeNozzleOuterDiameterMm", 300)
        )
        self.flange_nozzle_height_mm = float(
            p.get("flangeNozzleHeightMm", 150)
        )
        self.flange_od_mm = float(p.get("flangeOuterDiameterMm", 450))
        self.flange_thickness_mm = float(p.get("flangeThicknessMm", 25))
        self.flange_bolt_circle_dia_mm = float(
            p.get("flangeBoltCircleDiameterMm", 400)
        )
        self.flange_bolt_hole_dia_mm = float(
            p.get("flangeBoltHoleDiameterMm", 18)
        )
        self.flange_bolt_qty = int(p.get("flangeBoltQuantity", 12))

        # Shaft
        self.shaft_diameter_mm = float(p["shaftDiameterMm"])
        self.shaft_length_mm = float(p["shaftTotalLengthMm"])

        # Impeller
        self.impeller_type = p.get("impellerType", "PitchedBladeTurbine")
        self.impeller_diameter_mm = float(p["impellerDiameterMm"])
        self.blade_thickness_mm = float(p.get("bladeThicknessMm", 6))
        self.blade_width_mm = float(
            p.get("bladeWidthMm", self.impeller_diameter_mm * 0.22)
        )
        self.blade_count = int(p.get("bladeCount", 4))
        self.blade_pitch_angle_deg = float(
            p.get("bladePitchAngleDeg", 45)
        )
        self.hub_diameter_mm = float(
            p.get("hubDiameterMm", self.shaft_diameter_mm * 2.2)
        )
        self.hub_height_mm = float(p.get("hubHeightMm", 60))
        self.number_of_impellers = int(p.get("numberOfImpellers", 1))
        self.impeller_clearance_ratio = float(
            p.get("clearanceToDiameterRatio", 0.33)
        )
        self.impeller_spacing_mm = float(
            p.get("impellerSpacingMm", self.tank_od_mm * 0.75)
        )

        # Baffles
        self.baffle_count = int(p.get("numberOfBaffles", 4))
        self.baffle_width_mm = float(
            p.get("baffleWidthMm", self.tank_od_mm * 0.0833)
        )
        self.baffle_thickness_mm = float(
            p.get("baffleThicknessMm", 8)
        )
        self.baffle_wall_clearance_mm = float(
            p.get("baffleWallClearanceMm", 15)
        )

        # Liquid level
        self.liquid_height_mm = float(
            p.get("liquidHeightMm", self.shell_height_mm * 0.9)
        )

        # Metadata
        self.project_name = p.get("projectName", "Untitled Project")
        self.part_name = p.get("partName", "Agitator Assembly")
        self.material = p.get("material", "SS316L")
        self.drawing_scale = p.get("drawingScale", "1:20")

        # Outputs
        self.output_step_path = p["outputStepPath"]
        self.output_mesh_path = p.get("outputMeshPath")
        self.output_pdf_path = p.get("outputPdfPath")
        self.output_dxf_path = p.get("outputDxfPath")
        self.template_path = p.get("techDrawTemplatePath")


# ----------------------------------------------------------------------
# 2. TANK / HEAD GEOMETRY
# ----------------------------------------------------------------------
def build_torispherical_head_profile(od_mm, wall_mm):
    R = od_mm / 2.0
    L = od_mm
    r = 0.06 * od_mm

    def profile_points(radius_outer):
        L_eff = L - r
        knuckle_center_r = radius_outer - r
        theta = (
            math.asin(knuckle_center_r / L_eff)
            if L_eff > 0 else 0
        )

        pts = []
        steps = 20

        for i in range(steps + 1):
            a = (
                (math.pi / 2.0)
                * (i / steps)
                * (theta / (math.pi / 2.0))
                if theta > 0 else 0
            )
            ang = math.pi / 2.0 - a
            pts.append(
                App.Vector(
                    knuckle_center_r - r + r * math.cos(ang),
                    0,
                    r * math.sin(ang)
                )
            )

        z0 = pts[-1].z
        x0 = pts[-1].x
        crown_r = L_eff if L_eff > 0 else radius_outer
        cz = z0 - math.sqrt(
            max(crown_r * crown_r - x0 * x0, 0.0)
        )

        for i in range(1, steps + 1):
            frac = i / steps
            xx = x0 * (1 - frac)
            zz = cz + math.sqrt(
                max(crown_r * crown_r - xx * xx, 0.0)
            )
            pts.append(App.Vector(xx, 0, zz))

        return pts

    return profile_points(R), profile_points(R - wall_mm)


def build_head_solid(od_mm, wall_mm, upward=True):
    outer_pts, inner_pts = build_torispherical_head_profile(
        od_mm, wall_mm
    )

    def wire_from_points(pts):
        edges = [
            Part.LineSegment(pts[i], pts[i + 1]).toShape()
            for i in range(len(pts) - 1)
        ]
        return Part.Wire(edges)

    def close_to_axis_and_revolve(wire, pts):
        p_start = pts[0]
        p_end = pts[-1]
        axis_tol = 1e-6
        closing_edges = []

        if abs(p_end.x) > axis_tol:
            closing_edges.append(
                Part.LineSegment(
                    p_end, App.Vector(0, 0, p_end.z)
                ).toShape()
            )

        closing_edges.append(
            Part.LineSegment(
                App.Vector(0, 0, p_end.z),
                App.Vector(0, 0, p_start.z)
            ).toShape()
        )

        if abs(p_start.x) > axis_tol:
            closing_edges.append(
                Part.LineSegment(
                    App.Vector(0, 0, p_start.z), p_start
                ).toShape()
            )

        full_wire = Part.Wire(
            list(wire.Edges) + closing_edges
        )
        face = Part.Face(full_wire)

        return face.revolve(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            360
        )

    outer_solid = close_to_axis_and_revolve(
        wire_from_points(outer_pts), outer_pts
    )
    inner_solid = close_to_axis_and_revolve(
        wire_from_points(inner_pts), inner_pts
    )

    hollow_head = outer_solid.cut(inner_solid)

    if not upward:
        hollow_head.rotate(
            App.Vector(0, 0, 0),
            App.Vector(1, 0, 0),
            180
        )

    return hollow_head


def build_flat_head_solid(od_mm, wall_mm):
    return Part.makeCylinder(od_mm / 2.0, wall_mm)


def build_tank(doc, prm):
    R_out = prm.tank_od_mm / 2.0
    R_in = R_out - prm.wall_thickness_mm

    shell_outer = Part.makeCylinder(
        R_out, prm.shell_height_mm
    )
    shell_inner = Part.makeCylinder(
        R_in, prm.shell_height_mm
    )
    shell = shell_outer.cut(shell_inner)

    if prm.head_type.lower() == "flat":
        bottom_head = build_flat_head_solid(
            prm.tank_od_mm, prm.wall_thickness_mm
        )
        bottom_head.translate(
            App.Vector(0, 0, -prm.wall_thickness_mm)
        )

        top_head = build_flat_head_solid(
            prm.tank_od_mm, prm.wall_thickness_mm
        )
        top_head.translate(
            App.Vector(0, 0, prm.shell_height_mm)
        )
    else:
        bottom_head = build_head_solid(
            prm.tank_od_mm,
            prm.wall_thickness_mm,
            upward=False
        )
        top_head = build_head_solid(
            prm.tank_od_mm,
            prm.wall_thickness_mm,
            upward=True
        )
        top_head.translate(
            App.Vector(0, 0, prm.shell_height_mm)
        )

    tank_shape = shell.fuse(bottom_head).fuse(top_head)

    obj = doc.addObject("Part::Feature", "TankShell")
    obj.Shape = tank_shape
    return obj


def build_top_flange(doc, prm):
    z_base = (
        prm.shell_height_mm
        + prm.tank_od_mm * 0.06 * 1.2
    )

    nozzle = Part.makeCylinder(
        prm.flange_nozzle_od_mm / 2.0,
        prm.flange_nozzle_height_mm
    )

    nozzle_bore = Part.makeCylinder(
        prm.shaft_diameter_mm / 2.0 + 10,
        prm.flange_nozzle_height_mm
        + prm.flange_thickness_mm
    )

    flange_disc = Part.makeCylinder(
        prm.flange_od_mm / 2.0,
        prm.flange_thickness_mm
    )
    flange_disc.translate(
        App.Vector(0, 0, prm.flange_nozzle_height_mm)
    )

    bolt_holes = []
    bcd_r = prm.flange_bolt_circle_dia_mm / 2.0

    for i in range(prm.flange_bolt_qty):
        ang = (
            2 * math.pi * i / prm.flange_bolt_qty
        )
        hole = Part.makeCylinder(
            prm.flange_bolt_hole_dia_mm / 2.0,
            prm.flange_thickness_mm + 2
        )
        hole.translate(
            App.Vector(
                bcd_r * math.cos(ang),
                bcd_r * math.sin(ang),
                prm.flange_nozzle_height_mm - 1
            )
        )
        bolt_holes.append(hole)

    flange_assembly = nozzle.fuse(flange_disc)
    flange_assembly = flange_assembly.cut(nozzle_bore)

    for hole in bolt_holes:
        flange_assembly = flange_assembly.cut(hole)

    flange_assembly.translate(
        App.Vector(0, 0, z_base)
    )

    obj = doc.addObject("Part::Feature", "MountingFlange")
    obj.Shape = flange_assembly
    return obj


# ----------------------------------------------------------------------
# 3. SHAFT
# ----------------------------------------------------------------------
def build_shaft(doc, prm):
    shaft = Part.makeCylinder(
        prm.shaft_diameter_mm / 2.0,
        prm.shaft_length_mm
    )
    shaft.translate(
        App.Vector(0, 0, -prm.shaft_length_mm)
    )
    shaft.translate(
        App.Vector(
            0,
            0,
            prm.shell_height_mm
            + prm.flange_nozzle_height_mm
        )
    )

    obj = doc.addObject("Part::Feature", "Shaft")
    obj.Shape = shaft
    return obj


# ----------------------------------------------------------------------
# 4. REAL PARAMETRIC IMPELLER GEOMETRY
# ----------------------------------------------------------------------
def _rotate_vector(vector, axis, angle_deg):
    return App.Rotation(axis, angle_deg).multVec(vector)


def _foil_section_wire(
    radius,
    chord,
    thickness,
    pitch_deg,
    skew_deg=0.0,
    camber=0.0,
    sweep=0.0,
    samples=16
):
    """
    Closed polygonal hydrofoil-like section.

    The section is generated in a local radial/tangential/axial frame,
    then pitched and skewed. Increasing sample count gives smoother
    FreeCAD lofts without changing the external params contract.
    """
    points = []

    for side in (1, -1):
        indices = (
            range(samples + 1)
            if side == 1
            else range(samples, -1, -1)
        )

        for i in indices:
            u = i / samples

            tangential = (u - 0.5) * chord

            camber_z = (
                camber
                * thickness
                * math.sin(math.pi * u)
            )

            thickness_factor = (
                0.55
                + 0.45 * math.sin(math.pi * u)
            )

            half_thickness = (
                0.5
                * thickness
                * thickness_factor
            )

            axial = (
                camber_z
                + side * half_thickness
            )

            p = App.Vector(
                radius,
                tangential,
                axial
            )

            local = p - App.Vector(
                radius, 0, 0
            )

            local = _rotate_vector(
                local,
                App.Vector(1, 0, 0),
                skew_deg
            )

            p = local + App.Vector(
                radius, 0, 0
            )

            p = _rotate_vector(
                p,
                App.Vector(0, 1, 0),
                pitch_deg
            )

            p.y += sweep
            points.append(p)

    points.append(points[0])
    return Part.makePolygon(points)


def _lofted_blade(
    radius_root,
    radius_tip,
    chord_root,
    chord_tip,
    thickness_root,
    thickness_tip,
    pitch_root,
    pitch_tip,
    skew_root,
    skew_tip,
    camber,
    stations=8
):
    """
    Creates one continuous 3-D blade using multiple spanwise
    hydrofoil sections rather than separate rectangular solids.
    """
    if stations < 3:
        stations = 3

    wires = []

    for i in range(stations):
        t = i / (stations - 1)

        radius = (
            radius_root
            + (radius_tip - radius_root) * t
        )

        chord = (
            chord_root
            + (chord_tip - chord_root) * t
        )

        thickness = (
            thickness_root
            + (thickness_tip - thickness_root) * t
        )

        pitch = (
            pitch_root
            + (pitch_tip - pitch_root) * t
        )

        skew = (
            skew_root
            + (skew_tip - skew_root) * t
        )

        # Smooth radial sweep/taper.
        sweep = (
            (chord_root - chord)
            * 0.10
        )

        wires.append(
            _foil_section_wire(
                radius,
                chord,
                thickness,
                pitch,
                skew,
                camber,
                sweep
            )
        )

    return Part.makeLoft(
        wires,
        True,
        False
    )


def _make_hub(prm):
    hub = Part.makeCylinder(
        prm.hub_diameter_mm / 2.0,
        prm.hub_height_mm
    )
    hub.translate(
        App.Vector(
            0,
            0,
            -prm.hub_height_mm / 2.0
        )
    )
    return hub


def build_pitched_blade_turbine(prm, z_offset_mm):
    """
    Parametric pitched-blade turbine.

    Uses a continuous tapered/cambered/lofted blade for each blade.
    User-supplied blade count, diameter, width, thickness and pitch
    remain the controlling parameters.
    """
    D = prm.impeller_diameter_mm

    radius_root = (
        prm.hub_diameter_mm / 2.0 * 0.98
    )
    radius_tip = D / 2.0

    chord_root = (
        prm.blade_width_mm * 1.35
    )
    chord_tip = (
        prm.blade_width_mm * 0.82
    )

    thickness_root = prm.blade_thickness_mm
    thickness_tip = max(
        prm.blade_thickness_mm * 0.65,
        1.5
    )

    pitch = prm.blade_pitch_angle_deg

    solid = _make_hub(prm)

    count = max(2, prm.blade_count)

    for i in range(count):
        blade = _lofted_blade(
            radius_root,
            radius_tip,
            chord_root,
            chord_tip,
            thickness_root,
            thickness_tip,
            pitch * 0.75,
            pitch,
            -5.0,
            8.0,
            0.18,
            stations=8
        )

        blade.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            i * 360.0 / count
        )

        solid = solid.fuse(blade)

    solid.translate(
        App.Vector(0, 0, z_offset_mm)
    )
    return solid


def build_marine_propeller(prm, z_offset_mm):
    """
    Parametric twisted propeller-style blade.

    This is a continuous lofted blade with spanwise pitch/chord/skew
    variation. It is substantially different from the old segmented-box
    approximation, while remaining controlled by the existing params.
    """
    D = prm.impeller_diameter_mm

    radius_root = (
        prm.hub_diameter_mm / 2.0 * 0.95
    )
    radius_tip = D / 2.0

    pitch_root = (
        prm.blade_pitch_angle_deg * 1.20
    )
    pitch_tip = (
        prm.blade_pitch_angle_deg * 0.55
    )

    chord_root = (
        prm.blade_width_mm * 1.35
    )
    chord_tip = (
        prm.blade_width_mm * 0.55
    )

    thickness_root = (
        prm.blade_thickness_mm * 1.25
    )
    thickness_tip = max(
        prm.blade_thickness_mm * 0.35,
        1.0
    )

    solid = _make_hub(prm)
    count = max(3, prm.blade_count)

    for i in range(count):
        blade = _lofted_blade(
            radius_root,
            radius_tip,
            chord_root,
            chord_tip,
            thickness_root,
            thickness_tip,
            pitch_root,
            pitch_tip,
            -12.0,
            18.0,
            0.30,
            stations=10
        )

        blade.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            i * 360.0 / count
        )

        solid = solid.fuse(blade)

    solid.translate(
        App.Vector(0, 0, z_offset_mm)
    )
    return solid


def build_rushton_turbine(prm, z_offset_mm):
    """
    Rushton disc turbine.

    Keeps the existing project proportions:
      disc OD = 0.67 D
      blade axial height = 0.20 D
      six blades

    The blades are now profiled/tapered rather than rectangular blocks.
    """
    D = prm.impeller_diameter_mm

    disc_od = 0.67 * D
    disc_t = max(
        prm.blade_thickness_mm * 2.5,
        D * 0.04
    )

    blade_axial = 0.20 * D
    blade_t = prm.blade_thickness_mm

    hub = _make_hub(prm)

    disc = Part.makeCylinder(
        disc_od / 2.0,
        disc_t
    )
    disc.translate(
        App.Vector(
            0,
            0,
            -disc_t / 2.0
        )
    )

    solid = hub.fuse(disc)

    # Six radial vertical blades. Each blade has a slight root/tip
    # taper while retaining the Rushton vertical-blade concept.
    for i in range(6):
        root = disc_od / 2.0
        tip = D / 2.0

        chord_root = blade_axial * 0.92
        chord_tip = blade_axial * 0.70

        blade = _lofted_blade(
            root,
            tip,
            chord_root,
            chord_tip,
            blade_t,
            max(blade_t * 0.85, 1.0),
            90.0,
            90.0,
            0.0,
            0.0,
            0.0,
            stations=5
        )

        blade.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            i * 60.0
        )

        solid = solid.fuse(blade)

    solid.translate(
        App.Vector(0, 0, z_offset_mm)
    )
    return solid


def build_hydrofoil_a310(prm, z_offset_mm):
    """
    Three-blade hydrofoil-style impeller.

    Uses a cambered tapered loft rather than a flat box. The exact
    proprietary/vendor A310 profile is NOT assumed; this remains a
    parametric hydrofoil-style geometry.
    """
    D = prm.impeller_diameter_mm

    radius_root = prm.hub_diameter_mm / 2.0
    radius_tip = D / 2.0

    chord_root = D * 0.18
    chord_tip = D * 0.11

    thickness_root = max(
        prm.blade_thickness_mm * 1.15,
        D * 0.012
    )
    thickness_tip = max(
        prm.blade_thickness_mm * 0.55,
        D * 0.006
    )

    solid = _make_hub(prm)

    for i in range(3):
        blade = _lofted_blade(
            radius_root,
            radius_tip,
            chord_root,
            chord_tip,
            thickness_root,
            thickness_tip,
            27.0,
            18.0,
            -4.0,
            10.0,
            0.42,
            stations=10
        )

        blade.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            i * 120.0
        )

        solid = solid.fuse(blade)

    solid.translate(
        App.Vector(0, 0, z_offset_mm)
    )
    return solid


def build_anchor_foil(prm, z_offset_mm):
    """
    Existing close-clearance anchor geometry retained.
    """
    R = prm.impeller_diameter_mm / 2.0
    arm_h = prm.liquid_height_mm * 0.85
    arm_w = prm.blade_width_mm
    arm_t = prm.blade_thickness_mm * 3.0
    bar_t = prm.blade_thickness_mm * 2.0

    hub = _make_hub(prm)

    top_bar = Part.makeBox(
        2.0 * R, arm_t, bar_t
    )
    top_bar.translate(
        App.Vector(
            -R,
            -arm_t / 2.0,
            arm_h / 2.0 - bar_t
        )
    )

    bottom_bar = Part.makeBox(
        2.0 * R, arm_t, bar_t
    )
    bottom_bar.translate(
        App.Vector(
            -R,
            -arm_t / 2.0,
            -arm_h / 2.0
        )
    )

    def make_arm(angle_deg):
        arm = Part.makeBox(
            arm_w,
            arm_t,
            arm_h
        )
        arm.translate(
            App.Vector(
                R - arm_w,
                -arm_t / 2.0,
                -arm_h / 2.0
            )
        )
        arm.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            angle_deg
        )
        return arm

    solid = (
        hub
        .fuse(top_bar)
        .fuse(bottom_bar)
        .fuse(make_arm(0.0))
        .fuse(make_arm(180.0))
    )

    solid.translate(
        App.Vector(0, 0, z_offset_mm)
    )
    return solid


def _build_helix_segments(
    radius,
    height,
    pitch,
    width,
    thickness,
    steps=48
):
    total_angle = (
        height / pitch
    ) * 360.0

    da_deg = total_angle / steps
    dz = height / steps
    arc = radius * math.radians(da_deg)

    seg_len = (
        math.hypot(dz, arc) * 1.05
    )
    helix_angle = math.degrees(
        math.atan2(dz, arc)
    )

    parts = []

    for s in range(steps):
        angle_mid = (
            s + 0.5
        ) * da_deg
        z_mid = (
            s + 0.5
        ) * dz

        segment = Part.makeBox(
            width,
            thickness,
            seg_len
        )

        segment.translate(
            App.Vector(
                -width,
                -thickness / 2.0,
                -seg_len / 2.0
            )
        )

        segment.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 1, 0),
            helix_angle
        )

        segment.translate(
            App.Vector(
                radius,
                0,
                z_mid
            )
        )

        segment.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            angle_mid
        )

        parts.append(segment)

    result = parts[0]

    for part in parts[1:]:
        result = result.fuse(part)

    return result


def build_helical_ribbon(prm, z_offset_mm):
    """
    Existing double-helical ribbon implementation retained.
    """
    D = prm.impeller_diameter_mm
    R = D / 2.0
    pitch = D
    turns = max(
        1.5,
        prm.liquid_height_mm / pitch
    )
    helix_h = turns * pitch

    width = prm.blade_width_mm
    thickness = prm.blade_thickness_mm

    hub = Part.makeCylinder(
        prm.hub_diameter_mm / 2.0,
        helix_h
    )
    hub.translate(
        App.Vector(
            0,
            0,
            -helix_h / 2.0
        )
    )

    solid = hub

    for idx in range(2):
        try:
            helix_edge = Part.makeHelix(
                pitch,
                helix_h,
                R
            )

            spine = Part.Wire([helix_edge])

            points = [
                App.Vector(-width, -thickness / 2.0, 0),
                App.Vector(0, -thickness / 2.0, 0),
                App.Vector(0, thickness / 2.0, 0),
                App.Vector(-width, thickness / 2.0, 0),
            ]

            profile_wire = Part.Wire([
                Part.LineSegment(
                    points[0], points[1]
                ).toShape(),
                Part.LineSegment(
                    points[1], points[2]
                ).toShape(),
                Part.LineSegment(
                    points[2], points[3]
                ).toShape(),
                Part.LineSegment(
                    points[3], points[0]
                ).toShape(),
            ])

            shell = spine.makePipeShell(
                [profile_wire],
                True,
                True
            )

            ribbon = Part.Solid(shell)

        except Exception as ex:
            print(
                f"WARNING: helix pipe-sweep failed ({ex}); "
                f"falling back to segment approximation for "
                f"ribbon {idx + 1}."
            )

            ribbon = _build_helix_segments(
                R,
                helix_h,
                pitch,
                width,
                thickness,
                steps=48
            )

        ribbon.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            idx * 180.0
        )

        ribbon.translate(
            App.Vector(
                0,
                0,
                -helix_h / 2.0
            )
        )

        solid = solid.fuse(ribbon)

    solid.translate(
        App.Vector(0, 0, z_offset_mm)
    )
    return solid


# ----------------------------------------------------------------------
# IMPELLER DISPATCH
# ----------------------------------------------------------------------
_IMPELLER_BUILDERS = {
    "RushtonTurbine": build_rushton_turbine,
    "PitchedBladeTurbine": build_pitched_blade_turbine,
    "Propeller": build_marine_propeller,
    "MarinePropeller": build_marine_propeller,
    "HydrofoilA310": build_hydrofoil_a310,
    "AnchorFoil": build_anchor_foil,
    "HelicalRibbon": build_helical_ribbon,
}


def build_impellers(doc, prm):
    shaft_top_z = (
        prm.shell_height_mm
        + prm.flange_nozzle_height_mm
    )

    clearance_mm = (
        prm.tank_od_mm
        * prm.impeller_clearance_ratio
    )

    builder = _IMPELLER_BUILDERS.get(
        prm.impeller_type
    )

    if builder is None:
        print(
            f"WARNING: unrecognised impeller type "
            f"'{prm.impeller_type}'. Falling back to "
            f"PitchedBladeTurbine."
        )
        builder = build_pitched_blade_turbine

    objects = []

    for i in range(prm.number_of_impellers):
        z = (
            shaft_top_z
            - prm.shaft_length_mm
            + clearance_mm
            + i * prm.impeller_spacing_mm
        )

        shape = builder(prm, z)

        obj = doc.addObject(
            "Part::Feature",
            f"Impeller_{i + 1}"
        )
        obj.Shape = shape
        objects.append(obj)

    return objects


# ----------------------------------------------------------------------
# 5. BAFFLES
# ----------------------------------------------------------------------
def build_baffles(doc, prm):
    R_in = (
        prm.tank_od_mm / 2.0
        - prm.wall_thickness_mm
    )

    mount_r = (
        R_in
        - prm.baffle_wall_clearance_mm
        - prm.baffle_thickness_mm
    )

    objects = []

    for i in range(prm.baffle_count):
        angle = (
            360.0 / prm.baffle_count * i
        )

        baffle = Part.makeBox(
            prm.baffle_thickness_mm,
            prm.baffle_width_mm,
            prm.shell_height_mm * 0.9
        )

        baffle.translate(
            App.Vector(
                mount_r,
                -prm.baffle_width_mm / 2.0,
                prm.shell_height_mm * 0.05
            )
        )

        baffle.rotate(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 1),
            angle
        )

        obj = doc.addObject(
            "Part::Feature",
            f"Baffle_{i + 1}"
        )
        obj.Shape = baffle
        objects.append(obj)

    return objects


# ----------------------------------------------------------------------
# 6. TECHDRAW
# ----------------------------------------------------------------------
def build_techdraw(doc, prm, source_objs):
    try:
        import TechDraw
    except ImportError as ex:
        raise RuntimeError(
            "TechDraw workbench module is not available."
        ) from ex

    if (
        not prm.template_path
        or not os.path.isfile(prm.template_path)
    ):
        raise RuntimeError(
            f"TechDraw template not found at "
            f"'{prm.template_path}'."
        )

    page = doc.addObject(
        "TechDraw::DrawPage",
        "DrawingPage"
    )

    template = doc.addObject(
        "TechDraw::DrawSVGTemplate",
        "Template"
    )

    template.Template = prm.template_path
    page.Template = template

    try:
        template.setEditFieldContent(
            "FC-DN", prm.part_name
        )
        template.setEditFieldContent(
            "FC-SC", prm.drawing_scale
        )
        template.setEditFieldContent(
            "Comment", prm.project_name
        )
        template.setEditFieldContent(
            "FC-MATERIAL", prm.material
        )
    except Exception as ex:
        print(
            f"WARNING: could not set one or more "
            f"title block fields: {ex}"
        )

    front_view = doc.addObject(
        "TechDraw::DrawViewPart",
        "FrontView"
    )
    front_view.Source = source_objs
    front_view.Direction = App.Vector(0, -1, 0)
    front_view.Scale = 1.0
    page.addView(front_view)

    try:
        section_view = doc.addObject(
            "TechDraw::DrawViewSection",
            "FrontSection"
        )
        section_view.BaseView = front_view
        section_view.Source = source_objs
        section_view.SectionSymbol = "AA"
        section_view.SectionNormal = App.Vector(1, 0, 0)
        section_view.SectionOrigin = App.Vector(
            0,
            0,
            prm.shell_height_mm / 2.0
        )
        section_view.Direction = App.Vector(0, -1, 0)
        section_view.Scale = 1.0
        page.addView(section_view)
        primary_view = section_view
    except Exception as ex:
        print(
            f"WARNING: full section view could not be "
            f"created ({ex}); using front view."
        )
        primary_view = front_view

    top_view = doc.addObject(
        "TechDraw::DrawViewPart",
        "TopView"
    )
    top_view.Source = source_objs
    top_view.Direction = App.Vector(0, 0, 1)
    top_view.Scale = 1.0
    page.addView(top_view)

    doc.recompute()

    def try_add_dimension(
        name,
        view,
        dim_type,
        references
    ):
        try:
            dim = doc.addObject(
                "TechDraw::DrawViewDimension",
                name
            )
            dim.Type = dim_type
            dim.References2D = references
            if hasattr(view, "addView"):
                view.addView(dim)
            page.addView(dim)
            return dim
        except Exception as ex:
            print(
                f"WARNING: dimension '{name}' could "
                f"not be attached: {ex}"
            )
            return None

    # Kept consistent with the existing headless TechDraw behavior.
    try_add_dimension(
        "Dim_TankHeight",
        primary_view,
        "DistanceY",
        []
    )
    try_add_dimension(
        "Dim_ShellOD",
        primary_view,
        "DistanceX",
        []
    )
    try_add_dimension(
        "Dim_ShaftLength",
        primary_view,
        "DistanceY",
        []
    )
    try_add_dimension(
        "Dim_ImpellerDia",
        primary_view,
        "DistanceX",
        []
    )
    try_add_dimension(
        "Dim_FlangePCD",
        top_view,
        "Diameter",
        []
    )

    doc.recompute()
    return page


def export_techdraw(doc, page, prm):
    if prm.output_pdf_path:
        try:
            import TechDrawGui  # noqa: F401
            page.ViewObject.Proxy
        except Exception:
            pass

        try:
            import TechDraw
            TechDraw.writePDF(
                page,
                prm.output_pdf_path
            )
        except Exception as ex:
            raise RuntimeError(
                f"PDF export failed: {ex}"
            ) from ex

    if prm.output_dxf_path:
        try:
            import TechDraw
            TechDraw.writeDXFPage(
                page,
                prm.output_dxf_path
            )
        except Exception as ex:
            raise RuntimeError(
                f"DXF export failed: {ex}"
            ) from ex


# ----------------------------------------------------------------------
# 7. MAIN PIPELINE
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--params",
        required=True
    )

    args, _ = parser.parse_known_args(
        sys.argv[1:]
    )

    with open(args.params, "r") as f:
        raw = json.load(f)

    try:
        prm = AgitatorParams(raw)
    except KeyError as ex:
        print(
            f"ERROR: missing required parameter "
            f"{ex} in params.json"
        )
        sys.exit(1)

    doc = App.newDocument(
        "AgitatorAssembly"
    )

    try:
        tank_obj = build_tank(
            doc,
            prm
        )
        flange_obj = build_top_flange(
            doc,
            prm
        )
        shaft_obj = build_shaft(
            doc,
            prm
        )
        impeller_objs = build_impellers(
            doc,
            prm
        )
        baffle_objs = build_baffles(
            doc,
            prm
        )

        doc.recompute()

    except Exception:
        print(
            "ERROR: 3D geometry generation failed."
        )
        traceback.print_exc()
        sys.exit(2)

    # STEP
    try:
        all_shapes = [
            obj.Shape
            for obj in doc.Objects
            if hasattr(obj, "Shape")
        ]

        compound = Part.makeCompound(
            all_shapes
        )

        compound.exportStep(
            prm.output_step_path
        )

        print(
            f"STEP exported to "
            f"{prm.output_step_path}"
        )

    except Exception:
        print(
            "ERROR: STEP export failed."
        )
        traceback.print_exc()
        sys.exit(3)

    # OBJ preview
    if prm.output_mesh_path:
        try:
            import Mesh

            combined_mesh = Mesh.Mesh()

            for obj in doc.Objects:
                if hasattr(obj, "Shape"):
                    mesh = Mesh.Mesh()
                    mesh.addFacets(
                        obj.Shape.tessellate(1.0)
                    )
                    combined_mesh.addMesh(mesh)

            combined_mesh.write(
                prm.output_mesh_path
            )

            print(
                f"Mesh exported to "
                f"{prm.output_mesh_path}"
            )

        except Exception:
            print(
                "WARNING: OBJ preview mesh export failed. "
                "STEP model remains valid."
            )
            traceback.print_exc()

    # TechDraw
    if prm.template_path:
        try:
            source_objs = (
                [tank_obj, flange_obj, shaft_obj]
                + impeller_objs
                + baffle_objs
            )

            page = build_techdraw(
                doc,
                prm,
                source_objs
            )

            export_techdraw(
                doc,
                page,
                prm
            )

            print(
                "TechDraw page generated and exported."
            )

        except Exception:
            print(
                "WARNING: 2D TechDraw generation/export "
                "failed. 3D STEP remains valid."
            )
            traceback.print_exc()

    else:
        print(
            "No 'techDrawTemplatePath' supplied — "
            "skipping 2D drawing generation."
        )

    # Preserve the existing FCStd save behavior.
    fcstd_path = prm.output_step_path

    if fcstd_path.lower().endswith(".step"):
        fcstd_path = fcstd_path[:-5] + ".FCStd"
    elif fcstd_path.lower().endswith(".stp"):
        fcstd_path = fcstd_path[:-4] + ".FCStd"
    else:
        fcstd_path += ".FCStd"

    doc.saveAs(fcstd_path)


if __name__ == "__main__":
    main()
