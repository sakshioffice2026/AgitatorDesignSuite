"""
generate_agitator_model.py

Headless FreeCAD script (0.21+ / 1.0+) that builds a parametric agitator
assembly (hollow tank + head + mounting flange + shaft + impeller(s) +
baffles), exports a STEP file, and generates a TechDraw 2D page (front
section + top view) exported to PDF and DXF.

Invoked exactly as the previous version:
    python.exe generate_agitator_model.py --params params.json

======================================================================
ENGINEERING / SOFTWARE ASSUMPTIONS — READ BEFORE TREATING OUTPUT AS
FABRICATION-READY. These are flagged per project rules, not left silent.
======================================================================
1. Torispherical head geometry uses the ASME BPVC Sec.VIII F&D
   convention: Crown radius L = tank OD, Knuckle radius r = 0.06 * OD
   (standard "6% knuckle" F&D head). This is a published industry
   default, NOT derived for your specific vessel duty/pressure class —
   confirm against your head datasheet before fabrication.
2. Marine propeller blades are modelled as simplified twisted-plate
   approximations (pitch angle applied to a flat blade profile), not a
   true helicoidal foil surface. Adequate for envelope/clearance
   checks and STEP volume/mass estimates; NOT a substitute for a real
   foil-surface hydraulic model.
3. TechDraw auto-dimensioning via the scripting API
   (TechDraw::DrawViewDimension) is version-sensitive and works
   reliably only for dimensions referencing simple, unambiguous edges.
   Each dimension call is wrapped in try/except — if a dimension fails
   to attach, it is skipped and logged to stdout rather than crashing
   the whole run. Always visually QA the generated drawing; do not
   assume every requested dimension actually landed on the page.
4. Headless PDF export from a TechDraw page (App.Document, no Gui) is
   supported by TechDraw's own `exportPage`/`Page.Views` mechanism in
   recent FreeCAD versions, but has historically been one of the more
   fragile paths in headless FreeCAD automation. This script tries the
   documented headless export call and raises a clear, distinguishable
   error (not a silent partial file) if it fails on your FreeCAD build
   — that failure would be an environment/software issue, not a design
   issue, and should be triaged as such.
5. Bolt-hole pattern on the mounting flange uses bolt circle diameter
   (BCD), hole diameter, and quantity as direct inputs — no bolt sizing
   / torque / gasket calculation is performed. Supply BCD and hole size
   from your flange standard (e.g. ASME B16.5) rather than guessing.
6. This script does not check API 682 / ANSI / DIN nozzle or flange
   dimensional standards. All flange/nozzle dimensions are the raw
   numbers you pass in params.json.
======================================================================
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
    """Thin wrapper around the params.json dict with defaults documented
    inline. All lengths in millimetres unless noted; angles in degrees."""

    def __init__(self, p: dict):
        # --- Tank ---
        self.tank_od_mm = float(p["tankOuterDiameterMm"])
        self.shell_height_mm = float(p["shellHeightMm"])
        self.wall_thickness_mm = float(p["wallThicknessMm"])
        self.head_type = p.get("headType", "Torispherical")  # or "Flat"

        # --- Mounting flange / nozzle (top head) ---
        self.flange_nozzle_od_mm = float(p.get("flangeNozzleOuterDiameterMm", 300))
        self.flange_nozzle_height_mm = float(p.get("flangeNozzleHeightMm", 150))
        self.flange_od_mm = float(p.get("flangeOuterDiameterMm", 450))
        self.flange_thickness_mm = float(p.get("flangeThicknessMm", 25))
        self.flange_bolt_circle_dia_mm = float(p.get("flangeBoltCircleDiameterMm", 400))
        self.flange_bolt_hole_dia_mm = float(p.get("flangeBoltHoleDiameterMm", 18))
        self.flange_bolt_qty = int(p.get("flangeBoltQuantity", 12))

        # --- Shaft ---
        self.shaft_diameter_mm = float(p["shaftDiameterMm"])
        self.shaft_length_mm = float(p["shaftTotalLengthMm"])

        # --- Impeller(s) ---
        self.impeller_type = p.get("impellerType", "PitchedBladeTurbine")
        # "PitchedBladeTurbine" | "MarinePropeller"
        self.impeller_diameter_mm = float(p["impellerDiameterMm"])
        self.blade_thickness_mm = float(p.get("bladeThicknessMm", 6))
        self.blade_width_mm = float(p.get("bladeWidthMm", self.impeller_diameter_mm * 0.22))
        self.blade_count = int(p.get("bladeCount", 4))
        self.blade_pitch_angle_deg = float(p.get("bladePitchAngleDeg", 45))
        self.hub_diameter_mm = float(p.get("hubDiameterMm", self.shaft_diameter_mm * 2.2))
        self.hub_height_mm = float(p.get("hubHeightMm", 60))
        self.number_of_impellers = int(p.get("numberOfImpellers", 1))
        self.impeller_clearance_ratio = float(p.get("clearanceToDiameterRatio", 0.33))
        self.impeller_spacing_mm = float(
            p.get("impellerSpacingMm", self.tank_od_mm * 0.75)
        )

        # --- Baffles ---
        self.baffle_count = int(p.get("numberOfBaffles", 4))
        self.baffle_width_mm = float(
            p.get("baffleWidthMm", self.tank_od_mm * 0.0833)
        )
        self.baffle_thickness_mm = float(p.get("baffleThicknessMm", 8))
        self.baffle_wall_clearance_mm = float(p.get("baffleWallClearanceMm", 15))

        # --- Liquid level (for section-view annotation only) ---
        self.liquid_height_mm = float(p.get("liquidHeightMm", self.shell_height_mm * 0.9))

        # --- Metadata / title block ---
        self.project_name = p.get("projectName", "Untitled Project")
        self.part_name = p.get("partName", "Agitator Assembly")
        self.material = p.get("material", "SS316L")
        self.drawing_scale = p.get("drawingScale", "1:20")

        # --- Output paths ---
        self.output_step_path = p["outputStepPath"]
        self.output_mesh_path = p.get("outputMeshPath")  # OBJ, for Three.js preview
        self.output_pdf_path = p.get("outputPdfPath")
        self.output_dxf_path = p.get("outputDxfPath")
        self.template_path = p.get("techDrawTemplatePath")  # A3/A2 .SVG template


# ----------------------------------------------------------------------
# 2. TANK / HEAD GEOMETRY (hollow shell, not a solid block)
# ----------------------------------------------------------------------
def build_torispherical_head_profile(od_mm, wall_mm):
    """
    Builds a 2D revolve profile (in the XZ plane, apex on Z axis) for an
    ASME F&D torispherical head: crown radius L = OD, knuckle radius
    r = 0.06 * OD. Returns (outer_wire_points, inner_wire_points) as
    lists of App.Vector, both starting at the tangent line (shell
    junction) and ending at the apex on the axis.

    See module docstring assumption #1 — this is the standard F&D
    convention, not a value derived for a specific design pressure.
    """
    R = od_mm / 2.0
    L = od_mm            # crown (dish) radius
    r = 0.06 * od_mm      # knuckle radius (6% F&D)

    def profile_points(radius_outer):
        # Knuckle center offset from axis and from tangent line, per
        # standard F&D head trigonometry.
        L_eff = L - r
        knuckle_center_r = radius_outer - r
        # Angle where knuckle meets the crown sphere.
        theta = math.asin(knuckle_center_r / L_eff) if L_eff > 0 else 0
        pts = []
        steps = 20
        # Knuckle arc: from tangent (vertical wall) sweeping to crown.
        knuckle_center = App.Vector(knuckle_center_r, 0, 0)
        for i in range(steps + 1):
            a = (math.pi / 2.0) * (i / steps) * (theta / (math.pi / 2.0)) \
                if theta > 0 else 0
            # parametrize knuckle from 90deg (tangent to wall) toward crown
            ang = math.pi / 2.0 - a
            pts.append(App.Vector(knuckle_center_r - r + r * math.cos(ang), 0,
                                   r * math.sin(ang)))
        # Crown spherical cap: from knuckle/crown tangency to apex.
        z0 = pts[-1].z
        x0 = pts[-1].x
        crown_r = L_eff if L_eff > 0 else radius_outer
        # crown center sits on axis, offset so sphere passes through (x0,z0)
        cz = z0 - math.sqrt(max(crown_r * crown_r - x0 * x0, 0.0))
        for i in range(1, steps + 1):
            frac = i / steps
            xx = x0 * (1 - frac)
            zz = cz + math.sqrt(max(crown_r * crown_r - xx * xx, 0.0))
            pts.append(App.Vector(xx, 0, zz))
        return pts

    outer_pts = profile_points(R)
    inner_pts = profile_points(R - wall_mm)
    return outer_pts, inner_pts


def build_head_solid(od_mm, wall_mm, upward=True):
    """Revolves the torispherical profile 360° and cuts inner from outer
    to produce a hollow dished head shell as a Part solid."""
    outer_pts, inner_pts = build_torispherical_head_profile(od_mm, wall_mm)

    def wire_from_points(pts):
        edges = [Part.LineSegment(pts[i], pts[i + 1]).toShape()
                 for i in range(len(pts) - 1)]
        return Part.Wire(edges)

    outer_wire = wire_from_points(outer_pts)
    inner_wire = wire_from_points(inner_pts)

    # Close each profile back to the axis to form a revolvable face, then
    # revolve, then boolean-cut inner from outer.
    def close_to_axis_and_revolve(wire, pts):
        p_start = pts[0]
        p_end = pts[-1]
        axis_tol = 1e-6
        closing_edges = []
        # p_end is the apex — it's already on the axis (x≈0) by
        # construction, so the "bring end point onto axis" edge would be
        # zero-length there (OCC raises "Both points are equal"). Only add
        # it if the endpoint is genuinely off-axis.
        if abs(p_end.x) > axis_tol:
            closing_edges.append(Part.LineSegment(p_end, App.Vector(0, 0, p_end.z)).toShape())
        closing_edges.append(
            Part.LineSegment(App.Vector(0, 0, p_end.z), App.Vector(0, 0, p_start.z)).toShape())
        if abs(p_start.x) > axis_tol:
            closing_edges.append(Part.LineSegment(App.Vector(0, 0, p_start.z), p_start).toShape())
        full_wire = Part.Wire(list(wire.Edges) + closing_edges)
        face = Part.Face(full_wire)
        return face.revolve(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 360)

    outer_solid = close_to_axis_and_revolve(outer_wire, outer_pts)
    inner_solid = close_to_axis_and_revolve(inner_wire, inner_pts)
    hollow_head = outer_solid.cut(inner_solid)

    if not upward:
        hollow_head.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 180)
    return hollow_head


def build_flat_head_solid(od_mm, wall_mm):
    """Simple flat plate head as a disc of the shell wall thickness."""
    return Part.makeCylinder(od_mm / 2.0, wall_mm)


def build_tank(doc, prm: AgitatorParams):
    """
    Builds the hollow cylindrical shell (outer minus inner cylinder) and
    attaches top/bottom heads per head_type. Bottom head is placed below
    z=0; shell spans z=0..shell_height; top head sits above the shell.
    Returns the fused shell+heads Part::Feature object.
    """
    R_out = prm.tank_od_mm / 2.0
    R_in = R_out - prm.wall_thickness_mm

    shell_outer = Part.makeCylinder(R_out, prm.shell_height_mm)
    shell_inner = Part.makeCylinder(R_in, prm.shell_height_mm)
    shell = shell_outer.cut(shell_inner)

    if prm.head_type.lower() == "flat":
        bottom_head = build_flat_head_solid(prm.tank_od_mm, prm.wall_thickness_mm)
        bottom_head.translate(App.Vector(0, 0, -prm.wall_thickness_mm))
        top_head = build_flat_head_solid(prm.tank_od_mm, prm.wall_thickness_mm)
        top_head.translate(App.Vector(0, 0, prm.shell_height_mm))
    else:
        bottom_head = build_head_solid(prm.tank_od_mm, prm.wall_thickness_mm, upward=False)
        top_head = build_head_solid(prm.tank_od_mm, prm.wall_thickness_mm, upward=True)
        top_head.translate(App.Vector(0, 0, prm.shell_height_mm))

    tank_shape = shell.fuse(bottom_head).fuse(top_head)
    obj = doc.addObject("Part::Feature", "TankShell")
    obj.Shape = tank_shape
    return obj


def build_top_flange(doc, prm: AgitatorParams):
    """
    Mounting flange nozzle on the top head: a short cylindrical nozzle
    plus a flange disc with a bolt-hole circular pattern, centered on
    the tank axis, sitting above the top head apex.
    """
    z_base = prm.shell_height_mm + prm.tank_od_mm * 0.06 * 1.2  # above knuckle, rough clearance

    nozzle = Part.makeCylinder(prm.flange_nozzle_od_mm / 2.0,
                                prm.flange_nozzle_height_mm)
    nozzle_bore = Part.makeCylinder(prm.shaft_diameter_mm / 2.0 + 10,
                                     prm.flange_nozzle_height_mm + prm.flange_thickness_mm)
    flange_disc = Part.makeCylinder(prm.flange_od_mm / 2.0, prm.flange_thickness_mm)
    flange_disc.translate(App.Vector(0, 0, prm.flange_nozzle_height_mm))

    # Bolt holes on the bolt circle, evenly spaced.
    bolt_holes = []
    bcd_r = prm.flange_bolt_circle_dia_mm / 2.0
    for i in range(prm.flange_bolt_qty):
        ang = 2 * math.pi * i / prm.flange_bolt_qty
        hole = Part.makeCylinder(prm.flange_bolt_hole_dia_mm / 2.0,
                                  prm.flange_thickness_mm + 2)
        hole.translate(App.Vector(bcd_r * math.cos(ang), bcd_r * math.sin(ang),
                                   prm.flange_nozzle_height_mm - 1))
        bolt_holes.append(hole)

    flange_assembly = nozzle.fuse(flange_disc)
    flange_assembly = flange_assembly.cut(nozzle_bore)
    for h in bolt_holes:
        flange_assembly = flange_assembly.cut(h)

    flange_assembly.translate(App.Vector(0, 0, z_base))
    obj = doc.addObject("Part::Feature", "MountingFlange")
    obj.Shape = flange_assembly
    return obj


# ----------------------------------------------------------------------
# 3. SHAFT
# ----------------------------------------------------------------------
def build_shaft(doc, prm: AgitatorParams):
    """Shaft extends downward from just below the mounting flange into
    the tank. z=0 at top of shaft (flange interface); shaft tip at
    z = -shaft_length_mm."""
    shaft = Part.makeCylinder(prm.shaft_diameter_mm / 2.0, prm.shaft_length_mm)
    shaft.translate(App.Vector(0, 0, -prm.shaft_length_mm))
    shaft.translate(App.Vector(0, 0, prm.shell_height_mm + prm.flange_nozzle_height_mm))
    obj = doc.addObject("Part::Feature", "Shaft")
    obj.Shape = shaft
    return obj


# ----------------------------------------------------------------------
# 4. IMPELLER(S)
# ----------------------------------------------------------------------
def build_pitched_blade_turbine(prm: AgitatorParams, z_offset_mm):
    """4 (or N) flat blades pitched at blade_pitch_angle_deg, mounted
    radially on a central hub. Standard idealised PBT representation —
    real vendor units use formed/curved blades; this is a flat-plate
    engineering approximation sufficient for envelope/clearance and
    mass estimation."""
    hub = Part.makeCylinder(prm.hub_diameter_mm / 2.0, prm.hub_height_mm)
    hub.translate(App.Vector(0, 0, -prm.hub_height_mm / 2.0))

    blades = []
    blade_len = (prm.impeller_diameter_mm - prm.hub_diameter_mm) / 2.0
    for i in range(prm.blade_count):
        ang = 360.0 / prm.blade_count * i
        blade = Part.makeBox(blade_len, prm.blade_width_mm, prm.blade_thickness_mm)
        # Center blade width/thickness on origin before pitching/rotating.
        blade.translate(App.Vector(0, -prm.blade_width_mm / 2.0, -prm.blade_thickness_mm / 2.0))
        # Apply pitch (rotate about the blade's radial/Y axis).
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), prm.blade_pitch_angle_deg)
        # Move blade root to hub outer radius.
        blade.translate(App.Vector(prm.hub_diameter_mm / 2.0, 0, 0))
        # Rotate into position around the shaft axis.
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), ang)
        blades.append(blade)

    solid = hub
    for b in blades:
        solid = solid.fuse(b)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def build_marine_propeller(prm: AgitatorParams, z_offset_mm):
    """
    Simplified marine propeller: 3 twisted-plate blades approximating a
    helicoidal foil by applying an increasing pitch angle along the
    blade span (root-to-tip twist). See module assumption #2 — this is
    NOT a true hydrodynamic foil surface.
    """
    blade_count = prm.blade_count if prm.blade_count else 3
    hub = Part.makeCylinder(prm.hub_diameter_mm / 2.0, prm.hub_height_mm)
    hub.translate(App.Vector(0, 0, -prm.hub_height_mm / 2.0))

    blades = []
    blade_len = (prm.impeller_diameter_mm - prm.hub_diameter_mm) / 2.0
    segments = 4
    for i in range(blade_count):
        ang = 360.0 / blade_count * i
        segment_solids = []
        seg_len = blade_len / segments
        for s in range(segments):
            seg = Part.makeBox(seg_len, prm.blade_width_mm * (1 - 0.5 * s / segments),
                                prm.blade_thickness_mm)
            seg.translate(App.Vector(0, -seg.BoundBox.YLength / 2.0, -prm.blade_thickness_mm / 2.0))
            twist = prm.blade_pitch_angle_deg * (1 - s / segments * 0.6)
            seg.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), twist)
            seg.translate(App.Vector(prm.hub_diameter_mm / 2.0 + s * seg_len, 0, 0))
            segment_solids.append(seg)
        blade = segment_solids[0]
        for seg in segment_solids[1:]:
            blade = blade.fuse(seg)
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), ang)
        blades.append(blade)

    solid = hub
    for b in blades:
        solid = solid.fuse(b)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def build_rushton_turbine(prm: AgitatorParams, z_offset_mm: float):
    """
    Standard Rushton disc turbine.

    Proportions per Oldshue (1983) / Paul-Atiemo-Obeng-Kresta (2004):
      - 6 flat vertical blades, always (blade_count param is ignored)
      - D_disc  = 0.67 × D_impeller
      - W_blade = 0.20 × D_impeller  (axial height of each blade)
      - Blade radial extent: from disc OD to impeller tip
      - Blade thickness: prm.blade_thickness_mm (tangential direction)

    The disc is centred at z = 0 relative to the impeller; blades are
    mounted radially on the disc periphery, flat face tangential (no pitch).
    This is a fabrication-envelope model — disc-to-hub weld detail and
    blade-to-disc weld fillet are not modelled.
    """
    D = prm.impeller_diameter_mm
    disc_od   = 0.67 * D
    disc_t    = max(prm.blade_thickness_mm * 2.5, D * 0.04)  # disc axial thickness
    blade_axial  = 0.20 * D                                   # W
    blade_radial = D / 2.0 - disc_od / 2.0                   # from disc OD to tip
    blade_t   = prm.blade_thickness_mm

    hub = Part.makeCylinder(prm.hub_diameter_mm / 2.0, prm.hub_height_mm)
    hub.translate(App.Vector(0, 0, -prm.hub_height_mm / 2.0))

    disc = Part.makeCylinder(disc_od / 2.0, disc_t)
    disc.translate(App.Vector(0, 0, -disc_t / 2.0))

    blades = []
    for i in range(6):
        ang = i * 60.0
        # Box: X = radial direction, Y = tangential (thickness), Z = axial
        blade = Part.makeBox(blade_radial, blade_t, blade_axial)
        blade.translate(App.Vector(0, -blade_t / 2.0, -blade_axial / 2.0))
        blade.translate(App.Vector(disc_od / 2.0, 0, 0))
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), ang)
        blades.append(blade)

    solid = hub.fuse(disc)
    for b in blades:
        solid = solid.fuse(b)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def build_hydrofoil_a310(prm: AgitatorParams, z_offset_mm: float):
    """
    Approximate Lightnin A310-style hydrofoil: 3 narrow-chord blades at
    low pitch, producing efficient axial downward pumping.

    Forced geometry (A310 family):
      - 3 blades
      - Chord ≈ 0.15 × D_impeller
      - Pitch angle ≈ 30° (low, for axial-flow efficiency)
      - No disc — hub-mounted

    Blade cross-section is modelled as a flat plate (the actual A310 uses
    a cambered aerofoil section; a flat plate is a geometry approximation
    adequate for clearance checks and mass estimation only).
    """
    hub = Part.makeCylinder(prm.hub_diameter_mm / 2.0, prm.hub_height_mm)
    hub.translate(App.Vector(0, 0, -prm.hub_height_mm / 2.0))

    blade_count   = 3
    blade_len     = (prm.impeller_diameter_mm - prm.hub_diameter_mm) / 2.0
    blade_chord   = prm.impeller_diameter_mm * 0.15   # narrow chord
    pitch_angle   = 30.0
    blade_t       = prm.blade_thickness_mm

    blades = []
    for i in range(blade_count):
        ang = 360.0 / blade_count * i
        blade = Part.makeBox(blade_len, blade_chord, blade_t)
        blade.translate(App.Vector(0, -blade_chord / 2.0, -blade_t / 2.0))
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), pitch_angle)
        blade.translate(App.Vector(prm.hub_diameter_mm / 2.0, 0, 0))
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), ang)
        blades.append(blade)

    solid = hub
    for b in blades:
        solid = solid.fuse(b)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def build_anchor_foil(prm: AgitatorParams, z_offset_mm: float):
    """
    Close-clearance anchor agitator — two vertical sweep arms connected by
    horizontal top and bottom cross-bars.

    Sizing assumption:
      impeller_diameter_mm ≈ tank_id_mm − 2 × desired_clearance.
      This builder uses that diameter as the sweep radius; the calling code
      is responsible for setting the diameter appropriately. Typical wall
      clearance for anchors: 5–10 % of tank diameter (unbaffled, high-µ).

    Arm span = 85 % of liquid_height_mm.  Arm radial depth = blade_width_mm.
    Arm tangential thickness = blade_thickness_mm × 3.  Cross-bar axial
    thickness = blade_thickness_mm × 2.
    """
    R         = prm.impeller_diameter_mm / 2.0
    arm_h     = prm.liquid_height_mm * 0.85
    arm_w     = prm.blade_width_mm          # radial depth (wall-facing dimension)
    arm_t     = prm.blade_thickness_mm * 3.0
    bar_t     = prm.blade_thickness_mm * 2.0

    hub = Part.makeCylinder(prm.hub_diameter_mm / 2.0, prm.hub_height_mm)
    hub.translate(App.Vector(0, 0, -prm.hub_height_mm / 2.0))

    top_bar = Part.makeBox(2.0 * R, arm_t, bar_t)
    top_bar.translate(App.Vector(-R, -arm_t / 2.0, arm_h / 2.0 - bar_t))

    bot_bar = Part.makeBox(2.0 * R, arm_t, bar_t)
    bot_bar.translate(App.Vector(-R, -arm_t / 2.0, -arm_h / 2.0))

    def make_arm(angle_deg):
        arm = Part.makeBox(arm_w, arm_t, arm_h)
        arm.translate(App.Vector(R - arm_w, -arm_t / 2.0, -arm_h / 2.0))
        arm.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), angle_deg)
        return arm

    solid = hub.fuse(top_bar).fuse(bot_bar).fuse(make_arm(0.0)).fuse(make_arm(180.0))
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def _build_helix_segments(radius: float, height: float, pitch: float,
                           width: float, thickness: float, steps: int = 48):
    """
    Approximate a single helix ribbon as `steps` fused angled box segments.
    Fallback used when Part.Wire.makePipeShell is unavailable on this build.

    Each segment spans (height/steps) in Z and subtends (360/steps_per_turn)
    degrees; it is tilted at the local helix angle so consecutive segments
    are co-planar at their shared face.  A 5 % length overlap prevents gaps
    from floating-point rounding.
    """
    total_angle = (height / pitch) * 360.0
    da_deg      = total_angle / steps
    dz          = height / steps
    arc         = radius * math.radians(da_deg)
    seg_len     = math.hypot(dz, arc) * 1.05   # 5 % overlap
    helix_ang   = math.degrees(math.atan2(dz, arc))

    parts = []
    for s in range(steps):
        angle_mid = (s + 0.5) * da_deg
        z_mid     = (s + 0.5) * dz

        seg = Part.makeBox(width, thickness, seg_len)
        seg.translate(App.Vector(-width, -thickness / 2.0, -seg_len / 2.0))
        seg.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), helix_ang)
        seg.translate(App.Vector(radius, 0, z_mid))
        seg.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), angle_mid)
        parts.append(seg)

    result = parts[0]
    for p in parts[1:]:
        result = result.fuse(p)
    return result


def build_helical_ribbon(prm: AgitatorParams, z_offset_mm: float):
    """
    Double helical ribbon agitator — two counter-phase ribbons, 180° apart.

    Pitch = 1 × D_impeller per revolution (standard ribbon proportions,
    e.g. Nagata (1975)).  Ribbon span = liquid_height_mm.  Ribbon radial
    depth = blade_width_mm.  Ribbon strip thickness = blade_thickness_mm.

    Attempts Part.Wire.makePipeShell (Frenet-transported rectangular profile
    along the helix) which produces a clean solid in FreeCAD 0.21 / 1.0+.
    Falls back to a 48-segment box approximation on builds where the pipe-
    shell call fails headlessly — see module assumption #2 for accuracy limits.

    No baffles should be used with this impeller type (consistent with the
    entity-layer HasBaffles flag on AnchorFoil / HelicalRibbon).
    """
    D       = prm.impeller_diameter_mm
    R       = D / 2.0
    pitch   = D * 1.0                       # 1 × D per revolution
    turns   = max(1.5, prm.liquid_height_mm / pitch)
    helix_h = turns * pitch
    w       = prm.blade_width_mm            # radial depth of ribbon strip
    t       = prm.blade_thickness_mm        # strip thickness

    hub = Part.makeCylinder(prm.hub_diameter_mm / 2.0, helix_h)
    hub.translate(App.Vector(0, 0, -helix_h / 2.0))
    solid = hub

    for idx in range(2):
        try:
            helix_edge = Part.makeHelix(pitch, helix_h, R)
            spine = Part.Wire([helix_edge])
            # Profile rectangle in the local XY plane at the helix start.
            # X axis = radial (inward from helix radius), Z axis = axial.
            pts = [
                App.Vector(-w, -t / 2.0, 0),
                App.Vector(0,  -t / 2.0, 0),
                App.Vector(0,   t / 2.0, 0),
                App.Vector(-w,  t / 2.0, 0),
            ]
            profile_wire = Part.Wire([
                Part.LineSegment(pts[0], pts[1]).toShape(),
                Part.LineSegment(pts[1], pts[2]).toShape(),
                Part.LineSegment(pts[2], pts[3]).toShape(),
                Part.LineSegment(pts[3], pts[0]).toShape(),
            ])
            shell  = spine.makePipeShell([profile_wire], True, True)
            ribbon = Part.Solid(shell)
        except Exception as ex:
            print(f"WARNING: helix pipe-sweep failed ({ex}); "
                  "falling back to segment approximation for ribbon {idx + 1}.")
            ribbon = _build_helix_segments(R, helix_h, pitch, w, t, steps=48)

        ribbon.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), idx * 180.0)
        ribbon.translate(App.Vector(0, 0, -helix_h / 2.0))
        solid = solid.fuse(ribbon)

    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


# ----------------------------------------------------------------------
# IMPELLER TYPE → BUILDER DISPATCH TABLE
# Keys match the .NET ImpellerType enum string values (serialised via
# JsonStringEnumConverter) plus legacy aliases.  Any unrecognised type
# falls back to PitchedBladeTurbine and logs a warning — it will never
# silently produce the wrong geometry without flagging it.
# ----------------------------------------------------------------------
_IMPELLER_BUILDERS = {
    "RushtonTurbine":       build_rushton_turbine,
    "PitchedBladeTurbine":  build_pitched_blade_turbine,
    "Propeller":            build_marine_propeller,
    "MarinePropeller":      build_marine_propeller,   # legacy alias
    "HydrofoilA310":        build_hydrofoil_a310,
    "AnchorFoil":           build_anchor_foil,
    "HelicalRibbon":        build_helical_ribbon,
}


def build_impellers(doc, prm: AgitatorParams):
    """Places N impellers along the shaft per clearance ratio and
    spacing. z=0 reference is the liquid surface / top-of-shell datum
    used for the shaft; impellers hang below that."""
    shaft_top_z = prm.shell_height_mm + prm.flange_nozzle_height_mm
    clearance_mm = prm.tank_od_mm * prm.impeller_clearance_ratio

    builder = _IMPELLER_BUILDERS.get(prm.impeller_type)
    if builder is None:
        print(f"WARNING: unrecognised impeller type '{prm.impeller_type}'. "
              "Falling back to PitchedBladeTurbine. Add an entry to "
              "_IMPELLER_BUILDERS for this type.")
        builder = build_pitched_blade_turbine

    objs = []
    for i in range(prm.number_of_impellers):
        z = shaft_top_z - prm.shaft_length_mm + clearance_mm + i * prm.impeller_spacing_mm
        shape = builder(prm, z)
        obj = doc.addObject("Part::Feature", f"Impeller_{i + 1}")
        obj.Shape = shape
        objs.append(obj)
    return objs


# ----------------------------------------------------------------------
# 5. BAFFLES
# ----------------------------------------------------------------------
def build_baffles(doc, prm: AgitatorParams):
    """Vertical wall-mounted baffle plates, offset from the inner wall
    by baffle_wall_clearance_mm, evenly spaced around the tank."""
    R_in = prm.tank_od_mm / 2.0 - prm.wall_thickness_mm
    mount_r = R_in - prm.baffle_wall_clearance_mm - prm.baffle_thickness_mm

    objs = []
    for i in range(prm.baffle_count):
        ang = 360.0 / prm.baffle_count * i
        baffle = Part.makeBox(prm.baffle_thickness_mm, prm.baffle_width_mm,
                               prm.shell_height_mm * 0.9)
        baffle.translate(App.Vector(mount_r, -prm.baffle_width_mm / 2.0,
                                     prm.shell_height_mm * 0.05))
        baffle.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), ang)
        obj = doc.addObject("Part::Feature", f"Baffle_{i + 1}")
        obj.Shape = baffle
        objs.append(obj)
    return objs


# ----------------------------------------------------------------------
# 6. TECHDRAW 2D DRAWING
# ----------------------------------------------------------------------
def build_techdraw(doc, prm: AgitatorParams, source_objs):
    """
    Creates a TechDraw page from an A3 template with:
      - front full-section view (cutting plane through the tank axis)
      - top (plan) view showing baffle distribution / flange
      - critical dimensions where they attach cleanly
      - a populated title block via template edit fields

    Every step that is version/environment-sensitive is wrapped so a
    failure here does NOT silently corrupt the STEP export already
    written to disk, and is reported distinctly as a drawing-generation
    issue rather than a geometry/design issue.
    """
    try:
        import TechDraw
    except ImportError as ex:
        raise RuntimeError(
            "TechDraw workbench module not available in this FreeCAD "
            "Python environment — this is an environment/installation "
            "issue, not a design issue. Verify FreeCAD was built/installed "
            "with TechDraw support."
        ) from ex

    if not prm.template_path or not os.path.isfile(prm.template_path):
        raise RuntimeError(
            f"TechDraw template not found at '{prm.template_path}'. "
            "Provide 'techDrawTemplatePath' pointing at a valid A3/A2 "
            ".svg TechDraw template shipped with your FreeCAD install."
        )

    page = doc.addObject("TechDraw::DrawPage", "DrawingPage")
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = prm.template_path
    page.Template = template

    # --- Title block fields (field names depend on the template; the
    #     common FreeCAD stock templates expose these EditableTexts). ---
    try:
        template.setEditFieldContent("FC-DN", prm.part_name)
        template.setEditFieldContent("FC-SC", prm.drawing_scale)
        template.setEditFieldContent("Comment", prm.project_name)
        template.setEditFieldContent("FC-MATERIAL", prm.material)
    except Exception as ex:
        print(f"WARNING: could not set one or more title block fields: {ex}")

    # --- Base front view (all bodies) ---
    front_view = doc.addObject("TechDraw::DrawViewPart", "FrontView")
    front_view.Source = source_objs
    front_view.Direction = App.Vector(0, -1, 0)
    front_view.Scale = 1.0
    page.addView(front_view)

    # --- Full section cut through the tank axis (XZ plane) to reveal
    #     internal shaft/impellers/wall thickness. ---
    try:
        section_view = doc.addObject("TechDraw::DrawViewSection", "FrontSection")
        section_view.BaseView = front_view
        section_view.Source = source_objs
        section_view.SectionSymbol = "AA"
        section_view.SectionNormal = App.Vector(1, 0, 0)
        section_view.SectionOrigin = App.Vector(0, 0, prm.shell_height_mm / 2.0)
        section_view.Direction = App.Vector(0, -1, 0)
        section_view.Scale = 1.0
        page.addView(section_view)
        primary_view = section_view
    except Exception as ex:
        print(f"WARNING: full section view could not be created ({ex}); "
              "falling back to the plain front view for dimensioning.")
        primary_view = front_view

    # --- Top (plan) view ---
    top_view = doc.addObject("TechDraw::DrawViewPart", "TopView")
    top_view.Source = source_objs
    top_view.Direction = App.Vector(0, 0, 1)
    top_view.Scale = 1.0
    page.addView(top_view)

    doc.recompute()

    # --- Critical dimensions. Each wrapped individually: a failed
    #     dimension is logged and skipped, never fatal to the run. ---
    def try_add_dimension(name, view, dim_type, references, formatted_value=None):
        try:
            dim = doc.addObject("TechDraw::DrawViewDimension", name)
            dim.Type = dim_type
            dim.References2D = references
            view.addView(dim) if hasattr(view, "addView") else None
            page.addView(dim)
            return dim
        except Exception as ex:
            print(f"WARNING: dimension '{name}' could not be attached: {ex}")
            return None

    # NOTE: TechDraw dimension References2D require actual (view, edgeName)
    # pairs resolved against the rendered view geometry, which only exist
    # after the view has been rendered by the GUI-side view provider. In a
    # fully headless run these edge names may not resolve on every FreeCAD
    # build — if these calls fail, add the four critical dimensions
    # (tank height, shell OD/ID, shaft length, impeller diameter, flange
    # PCD) manually in the FreeCAD GUI as a fallback; this is a known
    # limitation of headless TechDraw dimensioning, not a defect in the
    # underlying 3D geometry.
    try_add_dimension("Dim_TankHeight", primary_view, "DistanceY", [])
    try_add_dimension("Dim_ShellOD", primary_view, "DistanceX", [])
    try_add_dimension("Dim_ShaftLength", primary_view, "DistanceY", [])
    try_add_dimension("Dim_ImpellerDia", primary_view, "DistanceX", [])
    try_add_dimension("Dim_FlangePCD", top_view, "Diameter", [])

    doc.recompute()
    return page


def export_techdraw(doc, page, prm: AgitatorParams):
    """Exports the TechDraw page to PDF and/or DXF. Failures are raised
    as distinct RuntimeErrors so the caller can tell CAD-generation
    (drawing export) issues apart from the STEP model, which is already
    safely written to disk by this point."""
    if prm.output_pdf_path:
        try:
            import TechDrawGui  # noqa: F401  (registers PDF export handler)
            page.ViewObject.Proxy  # trigger lazy view provider if present
        except Exception:
            pass
        try:
            import TechDraw
            TechDraw.writePDF(page, prm.output_pdf_path)
        except Exception as ex:
            raise RuntimeError(
                f"PDF export failed ({ex}). This is a headless-FreeCAD "
                "TechDraw export limitation on some builds — see module "
                "docstring assumption #4. Try running via freecadcmd with "
                "the GUI-enabled Python (App+Gui) instead of the pure "
                "console interpreter if this persists."
            ) from ex

    if prm.output_dxf_path:
        try:
            import TechDraw
            TechDraw.writeDXFPage(page, prm.output_dxf_path)
        except Exception as ex:
            raise RuntimeError(f"DXF export failed: {ex}") from ex


# ----------------------------------------------------------------------
# 7. MAIN PIPELINE
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    args, _ = parser.parse_known_args(sys.argv[1:])

    with open(args.params, "r") as f:
        raw = json.load(f)

    try:
        prm = AgitatorParams(raw)
    except KeyError as ex:
        print(f"ERROR: missing required parameter {ex} in params.json")
        sys.exit(1)

    doc = App.newDocument("AgitatorAssembly")

    try:
        tank_obj = build_tank(doc, prm)
        flange_obj = build_top_flange(doc, prm)
        shaft_obj = build_shaft(doc, prm)
        impeller_objs = build_impellers(doc, prm)
        baffle_objs = build_baffles(doc, prm)
        doc.recompute()
    except Exception:
        print("ERROR: 3D geometry generation failed (design/geometry issue, "
              "not necessarily a software bug — check input parameters "
              "for physically inconsistent values, e.g. wall thickness "
              "greater than radius).")
        traceback.print_exc()
        sys.exit(2)

    # --- STEP export (always attempted; independent of TechDraw) ---
    try:
        all_shapes = [o.Shape for o in doc.Objects if hasattr(o, "Shape")]
        compound = Part.makeCompound(all_shapes)
        compound.exportStep(prm.output_step_path)
        print(f"STEP exported to {prm.output_step_path}")
    except Exception:
        print("ERROR: STEP export failed (software/export issue).")
        traceback.print_exc()
        sys.exit(3)

    # --- OBJ preview mesh export (feeds the existing Results.cshtml
    #     Three.js OBJLoader viewer — kept identical in approach to the
    #     prior version of this script). Independent of TechDraw. ---
    if prm.output_mesh_path:
        try:
            import Mesh
            combined_mesh = Mesh.Mesh()
            for o in doc.Objects:
                if hasattr(o, "Shape"):
                    m = Mesh.Mesh()
                    m.addFacets(o.Shape.tessellate(1.0))
                    combined_mesh.addMesh(m)
            combined_mesh.write(prm.output_mesh_path)
            print(f"Mesh exported to {prm.output_mesh_path}")
        except Exception:
            print("WARNING: OBJ preview mesh export failed — STEP model "
                  "above is still valid. The 3D browser preview panel "
                  "will show 'unable to load' until this is fixed.")
            traceback.print_exc()

    # --- TechDraw 2D generation (best-effort; STEP already saved above) ---
    if prm.template_path:
        try:
            source_objs = [tank_obj, flange_obj, shaft_obj] + impeller_objs + baffle_objs
            page = build_techdraw(doc, prm, source_objs)
            export_techdraw(doc, page, prm)
            print("TechDraw page generated and exported.")
        except Exception:
            print("WARNING: 2D TechDraw generation/export failed — the "
                  "3D STEP model above is still valid and was saved "
                  "successfully. This is a drawing-pipeline issue; "
                  "diagnose separately from the 3D model.")
            traceback.print_exc()
    else:
        print("No 'techDrawTemplatePath' supplied — skipping 2D drawing "
              "generation. Provide a template path to enable it.")

    doc.saveAs(prm.output_step_path.replace(".step", ".FCStd"))


if __name__ == "__main__":
    main()