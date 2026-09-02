"""Parametric real impeller blade geometry for FreeCAD.

Replaces rectangular placeholder blades with lofted 3-D geometry while keeping
the existing AgitatorParams contract unchanged.
"""
import math
import FreeCAD as App
import Part


def _rot(v, axis, angle_deg):
    return App.Rotation(axis, angle_deg).multVec(v)


def _section_wire(radius, chord, thickness, pitch_deg, skew_deg=0.0,
                  camber=0.0, sweep=0.0, n=8):
    pts = []
    for side in (1, -1):
        rng = range(n + 1) if side == 1 else range(n, -1, -1)
        for i in rng:
            u = i / n
            y = (u - 0.5) * chord
            zc = camber * thickness * math.sin(math.pi * u)
            half_t = 0.5 * thickness * (0.55 + 0.45 * math.sin(math.pi * u))
            z = zc + side * half_t
            p = App.Vector(radius, y, z)
            local = p - App.Vector(radius, 0, 0)
            local = _rot(local, App.Vector(1, 0, 0), skew_deg)
            p = local + App.Vector(radius, 0, 0)
            p = _rot(p, App.Vector(0, 1, 0), pitch_deg)
            p.y += sweep
            pts.append(p)
    pts.append(pts[0])
    return Part.makePolygon(pts)


def _foil_blade(radius_root, radius_tip, chord_root, chord_tip,
                thickness_root, thickness_tip, pitch_root, pitch_tip,
                skew_root, skew_tip, camber, stations=6):
    wires = []
    for i in range(stations):
        t = i / (stations - 1)
        r = radius_root + (radius_tip - radius_root) * t
        chord = chord_root + (chord_tip - chord_root) * t
        thick = thickness_root + (thickness_tip - thickness_root) * t
        pitch = pitch_root + (pitch_tip - pitch_root) * t
        skew = skew_root + (skew_tip - skew_root) * t
        sweep = (chord_root - chord) * 0.10
        wires.append(_section_wire(r, chord, thick, pitch, skew, camber, sweep))
    return Part.makeLoft(wires, True, False)


def _hub(prm, height=None):
    h = prm.hub_height_mm if height is None else height
    hub = Part.makeCylinder(prm.hub_diameter_mm / 2.0, h)
    hub.translate(App.Vector(0, 0, -h / 2.0))
    return hub


def build_pitched_blade_turbine(prm, z_offset_mm):
    D = prm.impeller_diameter_mm
    r0 = prm.hub_diameter_mm / 2.0 * 0.98
    r1 = D / 2.0
    chord_root = prm.blade_width_mm * 1.35
    chord_tip = prm.blade_width_mm * 0.82
    t0 = prm.blade_thickness_mm
    t1 = max(t0 * 0.65, 1.5)
    pitch = prm.blade_pitch_angle_deg
    solid = _hub(prm)
    for i in range(prm.blade_count):
        blade = _foil_blade(r0, r1, chord_root, chord_tip, t0, t1,
                            pitch * 0.75, pitch, -5.0, 8.0, 0.18)
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1),
                     i * 360.0 / prm.blade_count)
        solid = solid.fuse(blade)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def build_marine_propeller(prm, z_offset_mm):
    D = prm.impeller_diameter_mm
    r0 = prm.hub_diameter_mm / 2.0 * 0.95
    r1 = D / 2.0
    root_pitch = prm.blade_pitch_angle_deg * 1.20
    tip_pitch = prm.blade_pitch_angle_deg * 0.55
    chord_root = prm.blade_width_mm * 1.35
    chord_tip = prm.blade_width_mm * 0.55
    t0 = prm.blade_thickness_mm * 1.25
    t1 = max(prm.blade_thickness_mm * 0.35, 1.0)
    solid = _hub(prm)
    for i in range(prm.blade_count):
        blade = _foil_blade(r0, r1, chord_root, chord_tip, t0, t1,
                            root_pitch, tip_pitch, -12.0, 18.0, 0.30,
                            stations=8)
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1),
                     i * 360.0 / prm.blade_count)
        solid = solid.fuse(blade)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def build_hydrofoil_a310(prm, z_offset_mm):
    D = prm.impeller_diameter_mm
    r0 = prm.hub_diameter_mm / 2.0
    r1 = D / 2.0
    chord_root = D * 0.18
    chord_tip = D * 0.11
    t0 = max(prm.blade_thickness_mm * 1.15, D * 0.012)
    t1 = max(prm.blade_thickness_mm * 0.55, D * 0.006)
    solid = _hub(prm)
    for i in range(3):
        blade = _foil_blade(r0, r1, chord_root, chord_tip, t0, t1,
                            27.0, 18.0, -4.0, 10.0, 0.42, stations=8)
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), i * 120.0)
        solid = solid.fuse(blade)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid


def build_rushton_turbine(prm, z_offset_mm):
    D = prm.impeller_diameter_mm
    disc_od = 0.67 * D
    disc_t = max(prm.blade_thickness_mm * 2.5, D * 0.04)
    blade_h = 0.20 * D
    t = prm.blade_thickness_mm
    hub = _hub(prm)
    disc = Part.makeCylinder(disc_od / 2.0, disc_t)
    disc.translate(App.Vector(0, 0, -disc_t / 2.0))
    solid = hub.fuse(disc)
    for i in range(6):
        x0 = disc_od / 2.0
        x1 = D / 2.0
        y = t / 2.0
        pts = [App.Vector(x0, -y, -blade_h/2),
               App.Vector(x1-y, -y, -blade_h/2),
               App.Vector(x1, 0, -blade_h/2),
               App.Vector(x1-y, y, -blade_h/2),
               App.Vector(x0, y, -blade_h/2),
               App.Vector(x0, -y, -blade_h/2)]
        blade = Part.Face(Part.makePolygon(pts)).extrude(App.Vector(0, 0, blade_h))
        blade.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), i * 60.0)
        solid = solid.fuse(blade)
    solid.translate(App.Vector(0, 0, z_offset_mm))
    return solid
