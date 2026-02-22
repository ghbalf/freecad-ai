# Spur Gear Generator

Create an involute spur gear using the parametric approach.

## Parameters to ask (if not provided)
- **Module** (m): tooth size parameter, default 1.0mm
- **Number of teeth** (z): default 20
- **Pressure angle**: default 20 degrees
- **Face width** (thickness): default 10mm
- **Bore diameter**: center hole, default 5mm (0 = no bore)

## Derived dimensions
- Pitch diameter: d = m * z
- Addendum: ha = m
- Dedendum: hf = 1.25 * m
- Tip diameter: da = d + 2*ha
- Root diameter: df = d - 2*hf

## Construction approach
Use `execute_code` with the involute gear profile:

```python
import math, Part, FreeCAD as App

def involute_point(base_r, angle):
    x = base_r * (math.cos(angle) + angle * math.sin(angle))
    y = base_r * (math.sin(angle) - angle * math.cos(angle))
    return App.Vector(x, y, 0)

def make_gear(module, teeth, pressure_angle=20, width=10, bore=5):
    pitch_r = module * teeth / 2
    base_r = pitch_r * math.cos(math.radians(pressure_angle))
    tip_r = pitch_r + module
    root_r = pitch_r - 1.25 * module
    # Build involute tooth profile, mirror, pattern around circle
    # ... (generate BSpline curves for each tooth)
```

Since involute gear math is complex, prefer using the `Part.makeHelix` and BSpline approach, or use the FCGear workbench if available. If neither works, create an approximation with polygonal tooth profiles.

## Important
- Label the gear clearly with its parameters: "Gear M1 Z20"
- Add the center bore as a pocket if bore > 0
- Position gear centered at origin
