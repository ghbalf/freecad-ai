---
name: sketch-from-image
description: Extract 2D geometry from an attached image and create a FreeCAD sketch from it.
---

# Sketch from Image

Convert an image (drawing, sketch, reference photo, technical drawing) into a
FreeCAD sketch by identifying its geometric shapes and creating a sketch with
equivalent geometry.

## When to use
- User has attached an image and says "create a sketch from this", "trace this
  shape", "make a sketch of this", or similar.
- User wants a starting point for CAD work from a visual reference.

## Required user inputs (ask if missing)

1. **Bounding size in mm** — the real-world size the sketch should occupy.
   Ask as: "What size (in mm) should this sketch be? Provide either the overall
   width or height." Example: "width 40mm" or "height 25mm".
2. **Plane** — default `XY` if not specified. Accept `XY`, `XZ`, `YZ`.
3. **Body name** — if the user wants the sketch on an existing body. Default:
   no body (standalone sketch).

Do NOT guess the size. Without a real dimension, the sketch is useless for CAD.

## Extraction procedure

Look at the attached image (or the textual description if no image is present)
and identify the 2D shapes. For each shape, record:

- **Shape type**: `rect`, `circle`, `polygon`, or `line`
- **Position and size**, scaled so that the overall bounding box matches the
  user-provided dimension (width OR height; preserve aspect ratio)

### Scaling rule

1. Measure the image's apparent bounding box in pixels (or relative units).
2. Compute `scale = user_dimension_mm / measured_dimension_pixels`.
3. Multiply every coordinate and radius by `scale`.
4. **Flip Y-axis if the source uses Y-down** (SVG, screen pixels, most image
   formats). FreeCAD sketches use Y-up. Negate all Y coordinates after scaling.
5. Translate so the sketch is centered around origin (or anchored at 0,0 —
   state which).

### Shape JSON schema (internal — use this shape before calling the tool)

```json
{
  "shapes": [
    {"type": "rect",    "x": 0, "y": 0, "width": 40, "height": 25},
    {"type": "circle",  "cx": 20, "cy": 12.5, "r": 3},
    {"type": "polygon", "points": [[0,0], [10,0], [5,8]]},
    {"type": "line",    "x1": 0, "y1": 0, "x2": 10, "y2": 10}
  ],
  "dimensions": [],
  "notes": "brief description of what was recognized"
}
```

The `dimensions` array is reserved for future use (auto-constraint from
measured values in technical drawings). Leave it `[]` for now.

## Output

After deriving the JSON, call `create_sketch` with the shapes. Do NOT add
constraints — rectangles and circles are auto-constrained. Example:

```
create_sketch(
  plane="XY",
  geometries=[
    {"type": "rectangle", "x": 0, "y": 0, "width": 40, "height": 25},
    {"type": "circle", "cx": 20, "cy": 12.5, "radius": 3}
  ]
)
```

For `line` shapes, emit them as polygons with two points, since `create_sketch`
groups connected line segments into a polygon.

## After creating the sketch

Briefly report:
- Shapes created (count per type)
- Final bounding size
- Any shapes you couldn't confidently identify — list them so the user can
  clarify or re-attach a clearer image.

Do NOT pad, pocket, or otherwise extrude the sketch unless the user explicitly
asks — this skill only produces the 2D profile.

## Iterating on the sketch

If the user wants to adjust the sketch after initial creation, use `edit_sketch`.

**For resizing, moving, or replacing geometry** (most common), use `clear_all=true`
and provide the complete updated geometry:

```
edit_sketch(sketch_name="MySketch", clear_all=true, add_geometries=[
    {"type": "rectangle", "x": 0, "y": 0, "width": 50, "height": 30},
    {"type": "circle", "cx": 6, "cy": 6, "radius": 3}
])
```

This clears all old geometry and constraints, then adds fresh geometry — no
over-constraint issues. The sketch object, plane attachment, and body membership
are preserved.

**For adding new geometry to existing** (e.g. "add a second hole"):
`edit_sketch(sketch_name, add_geometries=[...])`

**For changing dimensions** (e.g. "make it 50mm wide"): use `clear_all=true` with
updated geometry coordinates — dimensions are auto-constrained from the geometry.
Do NOT manually add DistanceX/DistanceY/Radius constraints.

## Limitations (mention if relevant)

- Curves and splines are approximated as polygons; for complex curves the user
  should trace manually or use a dedicated tracing tool.
- Dimension lines and annotations in the image are ignored in v1 (will be
  supported in a future version via the `dimensions` array).
- Hidden lines, section lines, and construction lines are treated as regular
  geometry. If the image uses drafting conventions, warn the user.
