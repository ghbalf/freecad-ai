# Spur Gear Generator

Create an involute spur gear using FreeCAD's Part module.

## Parameters to extract from user request
- **Module** (m): tooth size parameter, default 2.0mm
- **Number of teeth** (z): default 20
- **Pressure angle**: default 20 degrees
- **Face width** (thickness): default 10mm
- **Bore diameter**: center hole, default 5mm (0 = no bore)

## Derived dimensions
- Pitch diameter: d = m * z
- Tip diameter: da = m * (z + 2)
- Root diameter: df = m * (z - 2.5)

## Construction

Use `execute_code` with the complete gear generation code below.
Replace the parameter values at the top with the user's requested values.

```python
import math
import Part
import FreeCAD as App

# === PARAMETERS (replace with user values) ===
module = 2.0
teeth = 20
pressure_angle_deg = 20
face_width = 10.0
bore_dia = 5.0

# === DERIVED ===
pitch_r = module * teeth / 2.0
base_r = pitch_r * math.cos(math.radians(pressure_angle_deg))
tip_r = pitch_r + module
root_r = pitch_r - 1.25 * module

def involute_pts(base_r, tip_r, n=20):
    max_angle = math.sqrt((tip_r / base_r) ** 2 - 1)
    pts = []
    for i in range(n):
        t = i * max_angle / (n - 1)
        x = base_r * (math.cos(t) + t * math.sin(t))
        y = base_r * (math.sin(t) - t * math.cos(t))
        pts.append(App.Vector(x, y, 0))
    return pts

def rotate_pt(pt, angle_rad):
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return App.Vector(c * pt.x - s * pt.y, s * pt.x + c * pt.y, 0)

def pt_angle(pt):
    return math.atan2(pt.y, pt.x)

# Involute half-tooth thickness angle
inv_alpha = math.tan(math.radians(pressure_angle_deg)) - math.radians(pressure_angle_deg)
half_thick_angle = math.pi / (2 * teeth) + inv_alpha

# Generate involute profile for one tooth, centered on +Y axis
right_involute = involute_pts(base_r, tip_r, 20)
right_side = [rotate_pt(p, -half_thick_angle + math.pi / 2) for p in right_involute]
left_side = [App.Vector(-p.x, p.y, 0) for p in right_side]

# Key points
left_base, left_tip = left_side[0], left_side[-1]
right_base, right_tip = right_side[0], right_side[-1]
lb_a, rb_a = pt_angle(left_base), pt_angle(right_base)

# Points on root circle at flank angles
lb_on_root = App.Vector(root_r * math.cos(lb_a), root_r * math.sin(lb_a), 0)
rb_on_root = App.Vector(root_r * math.cos(rb_a), root_r * math.sin(rb_a), 0)

# Build closed tooth profile: root → left involute → tip arc → right involute → root arc
tooth_edges = []

# Radial: left root → left base
if lb_on_root.distanceToPoint(left_base) > 0.01:
    tooth_edges.append(Part.LineSegment(lb_on_root, left_base).toShape())

# Left involute BSpline (base → tip)
bs_left = Part.BSplineCurve()
bs_left.interpolate(left_side)
tooth_edges.append(bs_left.toShape())

# Tip arc (left tip → right tip)
tip_mid_a = (pt_angle(left_tip) + pt_angle(right_tip)) / 2
tip_mid = App.Vector(tip_r * math.cos(tip_mid_a), tip_r * math.sin(tip_mid_a), 0)
tooth_edges.append(Part.Arc(left_tip, tip_mid, right_tip).toShape())

# Right involute BSpline (tip → base)
bs_right = Part.BSplineCurve()
bs_right.interpolate(list(reversed(right_side)))
tooth_edges.append(bs_right.toShape())

# Radial: right base → right root
if right_base.distanceToPoint(rb_on_root) > 0.01:
    tooth_edges.append(Part.LineSegment(right_base, rb_on_root).toShape())

# Root arc (right root → left root, closing the profile)
root_mid_a = (lb_a + rb_a) / 2
root_mid = App.Vector(root_r * math.cos(root_mid_a), root_r * math.sin(root_mid_a), 0)
tooth_edges.append(Part.Arc(rb_on_root, root_mid, lb_on_root).toShape())

# Create one tooth solid
tooth_wire = Part.Wire(tooth_edges)
tooth_face = Part.Face(tooth_wire)
tooth_solid = tooth_face.extrude(App.Vector(0, 0, face_width))

# Build gear: base cylinder + all teeth fused together
gear = Part.makeCylinder(root_r, face_width)
for t in range(teeth):
    rotated = tooth_solid.copy()
    if t > 0:
        rotated.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), t * 360.0 / teeth)
    gear = gear.fuse(rotated)
gear = gear.removeSplitter()

# Cut bore hole
if bore_dia > 0:
    bore = Part.makeCylinder(bore_dia / 2, face_width + 2, App.Vector(0, 0, -1))
    gear = gear.cut(bore)

# Add to document
gear_obj = App.ActiveDocument.addObject("Part::Feature", "Gear_M{}_Z{}".format(module, teeth))
gear_obj.Label = "Gear M{} Z{}".format(module, teeth)
gear_obj.Shape = gear
App.ActiveDocument.recompute()

print("Gear created: pitch dia={:.1f}mm, tip dia={:.1f}mm, root dia={:.1f}mm".format(
    pitch_r * 2, tip_r * 2, root_r * 2))
```

## Important
- Copy the COMPLETE code block above into `execute_code`, only changing the parameter values at the top
- Do NOT try to use PartDesign Body/Sketch for the gear — use Part::Feature directly
- Do NOT use `App.Gui` — it may not be available in the sandbox
- Label the gear clearly with its parameters: "Gear M2 Z24"
- The gear is centered at the origin
