# Lattice / Infill Pattern

Generate a 3D lattice or infill pattern inside a bounding region. Useful for lightweight structural parts.

## Pattern types
- **Grid**: rectangular grid of holes or beams
- **Honeycomb**: hexagonal pattern (strongest weight-to-strength ratio)
- **Diagonal**: 45-degree crosshatch
- **Gyroid** (advanced): triply periodic minimal surface

## Parameters to ask (if not provided)
- **Pattern type**: grid / honeycomb / diagonal
- **Region**: existing object to fill, or bounding box dimensions
- **Cell size**: distance between pattern centers (default 10mm)
- **Wall thickness**: thickness of lattice walls/beams (default 1.5mm)
- **Height/depth**: extrusion height of the pattern

## Construction approach

### Grid pattern
1. Create a sketch with a rectangular array of circles or squares
2. Use spacing = cell_size, hole diameter = cell_size - wall_thickness
3. Pad to height, then boolean-intersect with the bounding shape

### Honeycomb
1. Create hexagonal cells: 6 line segments per cell
2. Hex radius = cell_size / 2
3. Offset rows by cell_size * 0.75 horizontally and cell_size * sqrt(3)/2 vertically
4. Pad and intersect with bounding shape

### Diagonal crosshatch
1. Create parallel lines at 45 degrees, spaced by cell_size
2. Create second set at -45 degrees
3. Lines have width = wall_thickness
4. Pad and intersect

## Important
- Always create the lattice larger than the target region, then use boolean intersection to trim
- Label clearly: "Lattice Grid 10mm" etc.
- For 3D printing, ensure wall_thickness >= 2x nozzle diameter (typically >= 0.8mm)
- After construction, hide all sketches for a clean viewport:
```python
for obj in App.ActiveDocument.Objects:
    if obj.TypeId == "Sketcher::SketchObject":
        obj.Visibility = False
```
