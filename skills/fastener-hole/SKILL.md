# Fastener Hole Patterns

Create standard fastener holes: clearance, counterbore, or countersink.

## Hole types

### Clearance holes (through-hole for bolt to pass through)
| Size | Close fit | Normal fit |
|------|-----------|------------|
| M2   | 2.2mm     | 2.4mm      |
| M2.5 | 2.7mm     | 2.9mm      |
| M3   | 3.2mm     | 3.4mm      |
| M4   | 4.3mm     | 4.5mm      |
| M5   | 5.3mm     | 5.5mm      |
| M6   | 6.4mm     | 6.6mm      |
| M8   | 8.4mm     | 9.0mm      |

### Counterbore (socket head cap screw)
| Size | CB diameter | CB depth |
|------|-------------|----------|
| M3   | 6.5mm       | 3.0mm    |
| M4   | 8.0mm       | 4.0mm    |
| M5   | 10.0mm      | 5.0mm    |
| M6   | 11.5mm      | 6.0mm    |
| M8   | 15.0mm      | 8.0mm    |

### Countersink (flat head screw, 90 degree)
| Size | CS diameter |
|------|-------------|
| M3   | 6.3mm       |
| M4   | 8.4mm       |
| M5   | 10.4mm      |
| M6   | 12.6mm      |

## Parameters to ask (if not provided)
- **Screw size**: M2 through M8
- **Hole type**: clearance / counterbore / countersink
- **Fit**: close / normal (for clearance holes)
- **Target object**: body or feature to cut into
- **Positions**: list of XY coordinates, or pattern (linear/circular)
- **Pattern**: if applicable — count, spacing, or bolt circle diameter + count

## Construction
1. Create a sketch on the specified face
2. For each hole position, add a circle (clearance diameter)
3. Pocket through-all for the clearance hole
4. For counterbore: add a second larger circle, pocket to CB depth
5. For countersink: use a cone (Part.makeCone) to create the 90-degree chamfer

## Cleanup
After construction, hide all sketches for a clean viewport:
```python
for obj in App.ActiveDocument.Objects:
    if obj.TypeId == "Sketcher::SketchObject":
        obj.Visibility = False
```
