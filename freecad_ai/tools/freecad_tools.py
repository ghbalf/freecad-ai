"""FreeCAD tool handlers.

Each tool wraps a FreeCAD operation in an undo transaction with error handling.
Tools are designed to be called by the LLM via structured tool calling.
"""

from .registry import ToolParam, ToolDefinition, ToolResult


def _coerce_str_list(value):
    """Coerce a stringified list into an actual list.

    LLMs sometimes send ``"['Face1', 'Face6']"`` (a string) instead of
    ``["Face1", "Face6"]`` (a JSON array).  This helper detects and parses
    that so tool handlers get a real list.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import ast
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return parsed
        except (ValueError, SyntaxError):
            pass
    return value


def _with_undo(label: str, func):
    """Run func inside a FreeCAD undo transaction. Returns ToolResult."""
    import FreeCAD as App
    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")
    doc.openTransaction(label)
    try:
        result = func(doc)
        doc.recompute()
        doc.commitTransaction()
        return result
    except Exception as e:
        try:
            doc.abortTransaction()
            doc.recompute()
        except Exception:
            pass
        return ToolResult(success=False, output="", error=str(e))


def _get_body_plane(body, plane_name: str):
    """Get a plane (XY/XZ/YZ) from a body's origin.

    Uses index-based access into OriginFeatures with error handling.
    Falls back to searching by Name if OriginFeatures fails (can happen
    in non-English locales where role-based lookup breaks).
    """
    plane_map = {"XY": 3, "XZ": 4, "YZ": 5}
    idx = plane_map.get(plane_name.upper())
    if idx is None:
        return None
    try:
        return body.Origin.OriginFeatures[idx]
    except Exception:
        pass
    # Fallback: search document objects by Name prefix
    try:
        prefix = plane_name.upper() + "_Plane"
        for obj in body.Document.Objects:
            if (obj.Name == prefix or obj.Name.startswith(prefix)) and \
               obj.TypeId == "App::Plane":
                return obj
    except Exception:
        pass
    return None


def _get_body_axis(body, axis_name: str):
    """Get an axis (X/Y/Z) from a body's origin.

    Same fallback strategy as _get_body_plane.
    """
    axis_map = {"X": 0, "Y": 1, "Z": 2}
    idx = axis_map.get(axis_name.upper())
    if idx is None:
        return None
    try:
        return body.Origin.OriginFeatures[idx]
    except Exception:
        pass
    # Fallback: search document objects by Name prefix
    try:
        prefix = axis_name.upper() + "_Axis"
        for obj in body.Document.Objects:
            if (obj.Name == prefix or obj.Name.startswith(prefix)) and \
               obj.TypeId == "App::Line":
                return obj
    except Exception:
        pass
    return None


# ── create_primitive ────────────────────────────────────────

def _handle_create_primitive(
    shape_type: str,
    label: str = "",
    body_name: str = "",
    operation: str = "additive",
    length: float = 10.0,
    width: float = 10.0,
    height: float = 10.0,
    radius: float = 5.0,
    radius2: float = 2.0,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> ToolResult:
    """Create a PartDesign primitive (Box, Cylinder, Sphere, Cone, Torus) inside a Body."""
    import FreeCAD as App

    additive_map = {
        "box": "PartDesign::AdditiveBox",
        "cylinder": "PartDesign::AdditiveCylinder",
        "sphere": "PartDesign::AdditiveSphere",
        "cone": "PartDesign::AdditiveCone",
        "torus": "PartDesign::AdditiveTorus",
    }
    subtractive_map = {
        "box": "PartDesign::SubtractiveBox",
        "cylinder": "PartDesign::SubtractiveCylinder",
        "sphere": "PartDesign::SubtractiveSphere",
        "cone": "PartDesign::SubtractiveCone",
        "torus": "PartDesign::SubtractiveTorus",
    }

    def do(doc):
        st = shape_type.lower()
        op = operation.lower()

        if op == "subtractive":
            type_map = subtractive_map
        else:
            type_map = additive_map

        pd_type = type_map.get(st)
        if not pd_type:
            return ToolResult(
                success=False, output="",
                error=f"Unknown shape type: {shape_type}. Use: {list(additive_map.keys())}"
            )

        # Get or create body
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(
                    success=False, output="",
                    error=f"Body '{body_name}' not found"
                )
        else:
            body_label = label or st.capitalize()
            body = doc.addObject("PartDesign::Body", body_label)
            body.Label = body_label

        name = label or st.capitalize()
        obj = body.newObject(pd_type, name)
        obj.Label = name

        if st == "box":
            obj.Length = length
            obj.Width = width
            obj.Height = height
        elif st == "cylinder":
            obj.Radius = radius
            obj.Height = height
        elif st == "sphere":
            obj.Radius = radius
        elif st == "cone":
            obj.Radius1 = radius
            obj.Radius2 = radius2
            obj.Height = height
        elif st == "torus":
            obj.Radius1 = radius
            obj.Radius2 = radius2

        if x != 0 or y != 0 or z != 0:
            obj.Placement.Base = App.Vector(x, y, z)

        return ToolResult(
            success=True,
            output=f"Created {op} {st} '{obj.Label}' ({obj.Name}) in body '{body.Label}' ({body.Name})",
            data={"name": obj.Name, "label": obj.Label, "type": pd_type,
                  "body_name": body.Name, "body_label": body.Label},
        )

    return _with_undo(f"Create {shape_type}", do)


CREATE_PRIMITIVE = ToolDefinition(
    name="create_primitive",
    description="Create a PartDesign primitive (Box, Cylinder, Sphere, Cone, Torus) inside a Body. Auto-creates a Body if body_name is not given. Use operation='subtractive' to cut material from an existing body.",
    category="modeling",
    parameters=[
        ToolParam("shape_type", "string", "Type of primitive to create",
                  enum=["box", "cylinder", "sphere", "cone", "torus"]),
        ToolParam("label", "string", "Display label for the object", required=False, default=""),
        ToolParam("body_name", "string", "Name of existing Body to add primitive to (auto-creates if empty)", required=False, default=""),
        ToolParam("operation", "string", "Additive (add material) or subtractive (cut material)",
                  required=False, default="additive", enum=["additive", "subtractive"]),
        ToolParam("length", "number", "Length (box)", required=False, default=10.0),
        ToolParam("width", "number", "Width (box)", required=False, default=10.0),
        ToolParam("height", "number", "Height (box/cylinder/cone)", required=False, default=10.0),
        ToolParam("radius", "number", "Radius (cylinder/sphere/cone r1/torus major)", required=False, default=5.0),
        ToolParam("radius2", "number", "Second radius (cone r2/torus minor)", required=False, default=2.0),
        ToolParam("x", "number", "X position", required=False, default=0.0),
        ToolParam("y", "number", "Y position", required=False, default=0.0),
        ToolParam("z", "number", "Z position", required=False, default=0.0),
    ],
    handler=_handle_create_primitive,
)


# ── create_body ─────────────────────────────────────────────

def _handle_create_body(
    label: str = "Body",
) -> ToolResult:
    """Create a PartDesign Body for parametric modeling."""
    import FreeCAD as App

    def do(doc):
        body = doc.addObject("PartDesign::Body", label)
        body.Label = label
        return ToolResult(
            success=True,
            output=f"Created PartDesign body '{body.Label}' ({body.Name})",
            data={"name": body.Name, "label": body.Label},
        )

    return _with_undo("Create Body", do)


CREATE_BODY = ToolDefinition(
    name="create_body",
    description="Create a PartDesign Body. Bodies are containers for parametric features (sketches, pads, pockets, fillets, etc). Create a body first, then add sketches to it using body_name parameter.",
    category="modeling",
    parameters=[
        ToolParam("label", "string", "Display label for the body", required=False, default="Body"),
    ],
    handler=_handle_create_body,
)


# ── create_sketch ───────────────────────────────────────────

def _handle_create_sketch(
    plane: str = "XY",
    body_name: str = "",
    geometries: list | None = None,
    constraints: list | None = None,
    label: str = "",
    offset: float = 0.0,
) -> ToolResult:
    """Create a sketch with geometry and constraints."""
    import FreeCAD as App
    import Part
    import Sketcher

    def do(doc):
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")

        if body:
            sketch = body.newObject("Sketcher::SketchObject", label or "Sketch")
        else:
            sketch = doc.addObject("Sketcher::SketchObject", label or "Sketch")

        # Attach to plane
        if body and plane.upper() in ("XY", "XZ", "YZ"):
            plane_feat = _get_body_plane(body, plane.upper())
            if plane_feat:
                sketch.AttachmentSupport = [(plane_feat, "")]
                sketch.MapMode = "FlatFace"

        # Offset the sketch along the plane normal
        if offset != 0:
            offset_map = {
                "XY": App.Vector(0, 0, offset),
                "XZ": App.Vector(0, offset, 0),
                "YZ": App.Vector(offset, 0, 0),
            }
            ovec = offset_map.get(plane.upper(), App.Vector(0, 0, offset))
            sketch.AttachmentOffset = App.Placement(ovec, App.Rotation())

        doc.recompute()

        geo_count = 0
        if geometries:
            for geo in geometries:
                # Some LLMs pass geometry items as JSON strings instead of dicts
                if isinstance(geo, str):
                    try:
                        import json as _json
                        geo = _json.loads(geo)
                    except (ValueError, TypeError):
                        continue
                geo_type = geo.get("type", "")
                if geo_type == "line":
                    p1 = App.Vector(geo.get("x1", 0), geo.get("y1", 0), 0)
                    p2 = App.Vector(geo.get("x2", 0), geo.get("y2", 0), 0)
                    sketch.addGeometry(Part.LineSegment(p1, p2))
                    geo_count += 1
                elif geo_type == "circle":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    sketch.addGeometry(Part.Circle(
                        App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r))
                    geo_count += 1
                elif geo_type == "arc":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    start_angle = geo.get("start_angle", 0)
                    end_angle = geo.get("end_angle", 3.14159)
                    sketch.addGeometry(Part.ArcOfCircle(
                        Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r),
                        start_angle, end_angle))
                    geo_count += 1
                elif geo_type == "rectangle":
                    # Accept both (x1,y1,x2,y2) and (x,y,width,height) formats
                    # Also accept "length" as alias for "height" (LLMs often confuse these)
                    rect_w = geo.get("width", None)
                    rect_h = geo.get("height", None) or geo.get("length", None)
                    if rect_w is not None and rect_h is not None:
                        x1 = geo.get("x", 0)
                        y1 = geo.get("y", 0)
                        x2 = x1 + rect_w
                        y2 = y1 + rect_h
                    else:
                        x1, y1 = geo.get("x1", 0), geo.get("y1", 0)
                        x2, y2 = geo.get("x2", 10), geo.get("y2", 10)
                    # 4 lines forming a rectangle
                    sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)))
                    sketch.addGeometry(Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)))
                    g = sketch.GeometryCount - 4
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g, 2, g+1, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+1, 2, g+2, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+2, 2, g+3, 1))
                    sketch.addConstraint(Sketcher.Constraint("Coincident", g+3, 2, g, 1))
                    sketch.addConstraint(Sketcher.Constraint("Horizontal", g))
                    sketch.addConstraint(Sketcher.Constraint("Horizontal", g+2))
                    sketch.addConstraint(Sketcher.Constraint("Vertical", g+1))
                    sketch.addConstraint(Sketcher.Constraint("Vertical", g+3))
                    geo_count += 4

        if constraints:
            for con in constraints:
                if isinstance(con, str):
                    try:
                        import json as _json
                        con = _json.loads(con)
                    except (ValueError, TypeError):
                        continue
                con_type = con.get("type", "")
                if not con_type:
                    continue

                # Validate: constraints need at least a geometry index ("first")
                # to be meaningful. Without it, FreeCAD's C++ layer may segfault.
                # Constraints with only type+value and no geometry refs are skipped.
                if "first" not in con and con_type not in ("Block",):
                    continue

                args = [con_type]
                for key in ("first", "first_pos", "second", "second_pos", "value"):
                    if key in con:
                        v = con[key]
                        # Ensure numeric args are ints (geometry/point indices) or float (value)
                        if key == "value":
                            args.append(float(v))
                        elif isinstance(v, float):
                            args.append(int(v))
                        else:
                            args.append(v)
                try:
                    sketch.addConstraint(Sketcher.Constraint(*args))
                except Exception:
                    pass  # Skip invalid constraints

        return ToolResult(
            success=True,
            output=f"Created sketch '{sketch.Label}' with {geo_count} geometries",
            data={"name": sketch.Name, "label": sketch.Label, "geometry_count": geo_count},
        )

    return _with_undo("Create Sketch", do)


CREATE_SKETCH = ToolDefinition(
    name="create_sketch",
    description="Create a 2D sketch with geometry (lines, circles, arcs, rectangles) and constraints. For PartDesign, specify body_name to add the sketch to a body.",
    category="modeling",
    parameters=[
        ToolParam("plane", "string", "Attachment plane: XY, XZ, or YZ", required=False, default="XY",
                  enum=["XY", "XZ", "YZ"]),
        ToolParam("body_name", "string", "Name of PartDesign body to add sketch to", required=False, default=""),
        ToolParam("geometries", "array",
                  "List of geometry objects. Each has a 'type' key plus type-specific params: "
                  "line: {x1,y1,x2,y2}, "
                  "rectangle: {x,y,width,height}, "
                  "circle: {cx,cy,radius}, "
                  "arc: {cx,cy,radius,start_angle,end_angle}.",
                  required=False, items={"type": "object"}),
        ToolParam("constraints", "array",
                  "List of Sketcher constraints. Each has 'type' plus constraint-specific params "
                  "(e.g. {type:'Distance',object1:'Edge1',value:50}).",
                  required=False, items={"type": "object"}),
        ToolParam("label", "string", "Display label for the sketch", required=False, default=""),
        ToolParam("offset", "number", "Offset the sketch along the plane normal (e.g. offset=40 on XY places sketch at z=40)", required=False, default=0.0),
    ],
    handler=_handle_create_sketch,
)


# ── pad_sketch ──────────────────────────────────────────────

def _handle_pad_sketch(
    sketch_name: str,
    length: float = 10.0,
    symmetric: bool = False,
    label: str = "",
    body_name: str = "",
) -> ToolResult:
    """Pad (extrude) a sketch."""

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found")

        # Find the body — prefer explicit body_name, fall back to auto-detect
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")
        else:
            body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        pad = body.newObject("PartDesign::Pad", label or "Pad")
        pad.Profile = sketch
        pad.Length = length
        if symmetric:
            pad.Midplane = True
        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Padded sketch '{sketch_name}' by {length}mm",
            data={"name": pad.Name, "label": pad.Label, "length": length},
        )

    return _with_undo("Pad Sketch", do)


PAD_SKETCH = ToolDefinition(
    name="pad_sketch",
    description="Pad (extrude) a sketch to create a solid. The sketch must be inside a PartDesign Body.",
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to pad"),
        ToolParam("length", "number", "Extrusion length in mm", required=False, default=10.0),
        ToolParam("symmetric", "boolean", "Pad symmetrically in both directions", required=False, default=False),
        ToolParam("label", "string", "Display label for the pad feature", required=False, default=""),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)", required=False, default=""),
    ],
    handler=_handle_pad_sketch,
)


# ── pocket_sketch ───────────────────────────────────────────

def _handle_pocket_sketch(
    sketch_name: str,
    length: float = 10.0,
    through_all: bool = False,
    label: str = "",
    body_name: str = "",
) -> ToolResult:
    """Create a pocket (cut) from a sketch."""

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")
        else:
            body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        pocket = body.newObject("PartDesign::Pocket", label or "Pocket")
        pocket.Profile = sketch
        if through_all:
            pocket.Type = 1  # Through All
        else:
            pocket.Length = length

        # Auto-direction: try both directions, keep the one that removes
        # the most material.  This handles sketches at any Z-offset
        # (e.g. offset=3 vs offset=H) and through_all pockets where the
        # default direction may only graze a thin slab.
        vol_before = body.Shape.Volume if body.Shape else 0

        # Try default direction (Reversed=False)
        pocket.Reversed = False
        doc.recompute()
        vol_default = body.Shape.Volume if body.Shape and body.Shape.isValid() else vol_before
        ok_default = pocket.Shape and pocket.Shape.isValid() and vol_default > 0.001

        # Try reversed direction
        pocket.Reversed = True
        doc.recompute()
        vol_reversed = body.Shape.Volume if body.Shape and body.Shape.isValid() else vol_before
        ok_reversed = pocket.Shape and pocket.Shape.isValid() and vol_reversed > 0.001

        # Pick direction that removes the most material
        removed_default = (vol_before - vol_default) if ok_default else 0
        removed_reversed = (vol_before - vol_reversed) if ok_reversed else 0

        if removed_default >= removed_reversed:
            pocket.Reversed = False
            doc.recompute()

        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Created pocket from sketch '{sketch_name}'",
            data={"name": pocket.Name, "label": pocket.Label},
        )

    return _with_undo("Pocket Sketch", do)


POCKET_SKETCH = ToolDefinition(
    name="pocket_sketch",
    description=(
        "Create a pocket (cut) from a sketch into the body's solid. "
        "Tip: for hollowing a box (e.g. enclosure), place the sketch at the top face "
        "using offset=H in create_sketch and set length=H-T (height minus wall thickness). "
        "The tool auto-detects the correct cut direction."
    ),
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to pocket"),
        ToolParam("length", "number", "Pocket depth in mm (prefer explicit depth over through_all)", required=False, default=10.0),
        ToolParam("through_all", "boolean", "Cut through the entire body (use only for holes, prefer explicit length for cavities)", required=False, default=False),
        ToolParam("label", "string", "Display label for the pocket feature", required=False, default=""),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)", required=False, default=""),
    ],
    handler=_handle_pocket_sketch,
)


# ── revolve_sketch ──────────────────────────────────────────

def _handle_revolve_sketch(
    sketch_name: str,
    axis: str = "Y",
    angle: float = 360.0,
    subtractive: bool = False,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Revolve a sketch around an axis (Revolution or Groove)."""

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")
        else:
            body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        # Resolve axis reference
        axis_upper = axis.upper()
        if axis_upper in ("X", "Y", "Z"):
            ref = (_get_body_axis(body, axis_upper), "")
        else:
            # Edge reference on the sketch: "Edge1", "Edge2", etc.
            ref = (sketch, [axis])

        type_name = "PartDesign::Groove" if subtractive else "PartDesign::Revolution"
        default_label = "Groove" if subtractive else "Revolution"
        feat = body.newObject(type_name, label or default_label)
        feat.Profile = sketch
        feat.ReferenceAxis = ref
        feat.Angle = angle
        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Revolved sketch '{sketch_name}' {angle}° around {axis}",
            data={"name": feat.Name, "label": feat.Label, "angle": angle, "axis": axis},
        )

    return _with_undo("Revolve Sketch", do)


REVOLVE_SKETCH = ToolDefinition(
    name="revolve_sketch",
    description="Revolve a sketch around an axis to create a solid of revolution (vase, bottle, wheel, etc). Uses PartDesign::Revolution (additive) or PartDesign::Groove (subtractive).",
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to revolve"),
        ToolParam("axis", "string", "Revolution axis: X, Y, Z (origin axes) or Edge1, Edge2... (sketch edge)",
                  required=False, default="Y"),
        ToolParam("angle", "number", "Revolution angle in degrees (360 = full revolution)",
                  required=False, default=360.0),
        ToolParam("subtractive", "boolean", "If true, use Groove (cut) instead of Revolution (add)",
                  required=False, default=False),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the feature", required=False, default=""),
    ],
    handler=_handle_revolve_sketch,
)


# ── loft_sketches ──────────────────────────────────────────

def _handle_loft_sketches(
    section_names: list,
    closed: bool = False,
    ruled: bool = False,
    subtractive: bool = False,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Loft between two or more sketches (AdditiveLoft or SubtractiveLoft)."""

    def do(doc):
        if len(section_names) < 2:
            return ToolResult(
                success=False, output="",
                error=f"Loft requires at least 2 sections, got {len(section_names)}"
            )

        sections = []
        for name in section_names:
            s = _get_object(doc, name)
            if not s:
                return ToolResult(success=False, output="", error=f"Section '{name}' not found")
            sections.append(s)

        # Find the body
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")
        else:
            body = _find_body_for(doc, sections[0])
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for section '{section_names[0]}'")

        # Verify all sections are in the same body
        for s in sections[1:]:
            sb = _find_body_for(doc, s)
            if sb is None or sb.Name != body.Name:
                return ToolResult(
                    success=False, output="",
                    error=f"Section '{s.Label}' is not in body '{body.Label}'. All sections must be in the same body."
                )

        type_name = "PartDesign::SubtractiveLoft" if subtractive else "PartDesign::AdditiveLoft"
        default_label = "SubtractiveLoft" if subtractive else "Loft"
        feat = body.newObject(type_name, label or default_label)
        feat.Profile = sections[0]
        feat.Sections = sections[1:]
        feat.Closed = closed
        feat.Ruled = ruled

        for s in sections:
            s.Visibility = False

        return ToolResult(
            success=True,
            output=f"Lofted {len(sections)} sections: {', '.join(section_names)}",
            data={"name": feat.Name, "label": feat.Label, "section_count": len(sections)},
        )

    return _with_undo("Loft Sketches", do)


LOFT_SKETCHES = ToolDefinition(
    name="loft_sketches",
    description="Loft between two or more sketches to create a smooth transitional solid (tapered shapes, bottles, organic forms). All sketches must be in the same PartDesign Body on different planes/offsets.",
    category="modeling",
    parameters=[
        ToolParam("section_names", "array", "Sketch names to loft between (minimum 2, ordered from start to end)",
                  items={"type": "string"}),
        ToolParam("closed", "boolean", "Close the loft loop (connect last section back to first)",
                  required=False, default=False),
        ToolParam("ruled", "boolean", "Use ruled (flat) surfaces instead of smooth",
                  required=False, default=False),
        ToolParam("subtractive", "boolean", "If true, cut instead of add",
                  required=False, default=False),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the loft feature", required=False, default=""),
    ],
    handler=_handle_loft_sketches,
)


# ── sweep_sketch ───────────────────────────────────────────

def _handle_sweep_sketch(
    profile_name: str,
    spine_name: str,
    subtractive: bool = False,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Sweep a profile sketch along a spine path (AdditivePipe or SubtractivePipe)."""

    def do(doc):
        profile = _get_object(doc, profile_name)
        if not profile:
            return ToolResult(success=False, output="", error=f"Profile sketch '{profile_name}' not found")

        spine = _get_object(doc, spine_name)
        if not spine:
            return ToolResult(success=False, output="", error=f"Spine sketch '{spine_name}' not found")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")
        else:
            body = _find_body_for(doc, profile)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for profile '{profile_name}'")

        # Verify spine is in the same body
        spine_body = _find_body_for(doc, spine)
        if spine_body is None or spine_body.Name != body.Name:
            return ToolResult(
                success=False, output="",
                error=f"Spine '{spine_name}' is not in body '{body.Label}'. Profile and spine must be in the same body."
            )

        type_name = "PartDesign::SubtractivePipe" if subtractive else "PartDesign::AdditivePipe"
        default_label = "SubtractiveSweep" if subtractive else "Sweep"
        feat = body.newObject(type_name, label or default_label)
        feat.Profile = profile
        feat.Spine = spine
        profile.Visibility = False
        spine.Visibility = False

        return ToolResult(
            success=True,
            output=f"Swept profile '{profile_name}' along spine '{spine_name}'",
            data={"name": feat.Name, "label": feat.Label},
        )

    return _with_undo("Sweep Sketch", do)


SWEEP_SKETCH = ToolDefinition(
    name="sweep_sketch",
    description="Sweep a profile sketch along a spine path to create a pipe, tube, or complex swept solid. Uses PartDesign::AdditivePipe (additive) or PartDesign::SubtractivePipe (subtractive). Both sketches must be in the same body.",
    category="modeling",
    parameters=[
        ToolParam("profile_name", "string", "Internal name of the cross-section sketch"),
        ToolParam("spine_name", "string", "Internal name of the path sketch (spine)"),
        ToolParam("subtractive", "boolean", "If true, cut instead of add",
                  required=False, default=False),
        ToolParam("body_name", "string", "Explicit body name (use when multiple bodies exist)",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the sweep feature", required=False, default=""),
    ],
    handler=_handle_sweep_sketch,
)


# ── boolean_operation ───────────────────────────────────────

def _handle_boolean_operation(
    operation: str,
    object1: str,
    object2: str,
    label: str = "",
) -> ToolResult:
    """Perform a boolean operation (fuse/cut/common) between two objects."""

    def do(doc):
        obj1 = _get_object(doc, object1)
        obj2 = _get_object(doc, object2)
        if not obj1:
            return ToolResult(success=False, output="", error=f"Object '{object1}' not found")
        if not obj2:
            return ToolResult(success=False, output="", error=f"Object '{object2}' not found")

        op_map = {
            "fuse": "Part::Fuse",
            "cut": "Part::Cut",
            "common": "Part::Common",
        }
        part_type = op_map.get(operation.lower())
        if not part_type:
            return ToolResult(
                success=False, output="",
                error=f"Unknown operation: {operation}. Use: fuse, cut, common"
            )

        name = label or operation.capitalize()
        result_obj = doc.addObject(part_type, name)
        result_obj.Base = obj1
        result_obj.Tool = obj2

        return ToolResult(
            success=True,
            output=f"Boolean {operation} of '{obj1.Label}' and '{obj2.Label}'",
            data={"name": result_obj.Name, "label": result_obj.Label},
        )

    return _with_undo(f"Boolean {operation}", do)


BOOLEAN_OPERATION = ToolDefinition(
    name="boolean_operation",
    description="Perform a boolean operation (fuse/cut/common) between two Part objects.",
    category="modeling",
    parameters=[
        ToolParam("operation", "string", "Boolean operation type", enum=["fuse", "cut", "common"]),
        ToolParam("object1", "string", "Internal name of the first object (base for cut)"),
        ToolParam("object2", "string", "Internal name of the second object (tool for cut)"),
        ToolParam("label", "string", "Display label for the result", required=False, default=""),
    ],
    handler=_handle_boolean_operation,
)


# ── transform_object ────────────────────────────────────────

def _handle_transform_object(
    object_name: str,
    translate_x: float = 0.0,
    translate_y: float = 0.0,
    translate_z: float = 0.0,
    rotate_axis_x: float = 0.0,
    rotate_axis_y: float = 0.0,
    rotate_axis_z: float = 1.0,
    rotate_angle: float = 0.0,
) -> ToolResult:
    """Move and/or rotate an object."""
    import FreeCAD as App

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        placement = App.Placement(
            App.Vector(translate_x, translate_y, translate_z),
            App.Rotation(App.Vector(rotate_axis_x, rotate_axis_y, rotate_axis_z), rotate_angle),
        )
        obj.Placement = placement

        parts = []
        if translate_x or translate_y or translate_z:
            parts.append(f"moved to ({translate_x}, {translate_y}, {translate_z})")
        if rotate_angle:
            parts.append(f"rotated {rotate_angle} degrees")
        desc = ", ".join(parts) if parts else "placement reset"

        return ToolResult(
            success=True,
            output=f"Transformed '{obj.Label}': {desc}",
            data={"name": obj.Name},
        )

    return _with_undo("Transform Object", do)


TRANSFORM_OBJECT = ToolDefinition(
    name="transform_object",
    description="Move and/or rotate an object by setting its Placement.",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to transform"),
        ToolParam("translate_x", "number", "X translation in mm", required=False, default=0.0),
        ToolParam("translate_y", "number", "Y translation in mm", required=False, default=0.0),
        ToolParam("translate_z", "number", "Z translation in mm", required=False, default=0.0),
        ToolParam("rotate_axis_x", "number", "Rotation axis X component", required=False, default=0.0),
        ToolParam("rotate_axis_y", "number", "Rotation axis Y component", required=False, default=0.0),
        ToolParam("rotate_axis_z", "number", "Rotation axis Z component", required=False, default=1.0),
        ToolParam("rotate_angle", "number", "Rotation angle in degrees", required=False, default=0.0),
    ],
    handler=_handle_transform_object,
)


# ── fillet_edges ────────────────────────────────────────────

def _handle_fillet_edges(
    object_name: str,
    edges: list | None = None,
    radius: float = 1.0,
    label: str = "",
) -> ToolResult:
    """Apply fillet to edges of an object."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        edge_refs = _coerce_str_list(edges) or ["Edge1"]

        # Check if this is a PartDesign body/feature
        body = _find_body_for(doc, obj)
        if body:
            fillet = body.newObject("PartDesign::Fillet", label or "Fillet")
            fillet.Base = (obj, edge_refs)
            fillet.Radius = radius
        else:
            fillet = doc.addObject("Part::Fillet", label or "Fillet")
            fillet.Base = obj
            fillet.Shape = obj.Shape.makeFillet(radius, [
                obj.Shape.Edges[int(e.replace("Edge", "")) - 1] for e in edge_refs
            ])

        return ToolResult(
            success=True,
            output=f"Applied fillet (r={radius}mm) to {len(edge_refs)} edge(s) of '{obj.Label}'",
            data={"name": fillet.Name, "label": fillet.Label, "radius": radius},
        )

    return _with_undo("Fillet Edges", do)


FILLET_EDGES = ToolDefinition(
    name="fillet_edges",
    description="Apply a fillet (rounded edge) to one or more edges of an object.",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("edges", "array", "Edge references, e.g. ['Edge1', 'Edge4']", required=False,
                  items={"type": "string"}),
        ToolParam("radius", "number", "Fillet radius in mm", required=False, default=1.0),
        ToolParam("label", "string", "Display label for the fillet", required=False, default=""),
    ],
    handler=_handle_fillet_edges,
)


# ── chamfer_edges ───────────────────────────────────────────

def _handle_chamfer_edges(
    object_name: str,
    edges: list | None = None,
    size: float = 1.0,
    label: str = "",
) -> ToolResult:
    """Apply chamfer to edges of an object."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        edge_refs = _coerce_str_list(edges) or ["Edge1"]

        body = _find_body_for(doc, obj)
        if body:
            chamfer = body.newObject("PartDesign::Chamfer", label or "Chamfer")
            chamfer.Base = (obj, edge_refs)
            chamfer.Size = size
        else:
            chamfer = doc.addObject("Part::Chamfer", label or "Chamfer")
            chamfer.Base = obj
            chamfer.Shape = obj.Shape.makeChamfer(size, [
                obj.Shape.Edges[int(e.replace("Edge", "")) - 1] for e in edge_refs
            ])

        return ToolResult(
            success=True,
            output=f"Applied chamfer (size={size}mm) to {len(edge_refs)} edge(s) of '{obj.Label}'",
            data={"name": chamfer.Name, "label": chamfer.Label, "size": size},
        )

    return _with_undo("Chamfer Edges", do)


CHAMFER_EDGES = ToolDefinition(
    name="chamfer_edges",
    description="Apply a chamfer (angled edge cut) to one or more edges of an object.",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("edges", "array", "Edge references, e.g. ['Edge1', 'Edge4']", required=False,
                  items={"type": "string"}),
        ToolParam("size", "number", "Chamfer size in mm", required=False, default=1.0),
        ToolParam("label", "string", "Display label for the chamfer", required=False, default=""),
    ],
    handler=_handle_chamfer_edges,
)


# ── measure ─────────────────────────────────────────────────

def _handle_measure(
    measure_type: str,
    target: str = "",
    target2: str = "",
) -> ToolResult:
    """Measure properties of objects (volume, area, bounding box, distance)."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    if measure_type == "distance" and target and target2:
        obj1 = _get_object(doc, target)
        obj2 = _get_object(doc, target2)
        if not obj1 or not obj2:
            return ToolResult(success=False, output="", error="One or both objects not found")
        bb1 = obj1.Shape.BoundBox
        bb2 = obj2.Shape.BoundBox
        c1 = App.Vector(bb1.Center)
        c2 = App.Vector(bb2.Center)
        dist = c1.distanceToPoint(c2)
        return ToolResult(
            success=True,
            output=f"Distance between centers of '{obj1.Label}' and '{obj2.Label}': {dist:.3f}mm",
            data={"distance": dist},
        )

    obj = _get_object(doc, target) if target else None
    if not obj:
        return ToolResult(success=False, output="", error=f"Object '{target}' not found")

    if measure_type == "volume":
        vol = obj.Shape.Volume
        return ToolResult(
            success=True,
            output=f"Volume of '{obj.Label}': {vol:.3f} mm^3",
            data={"volume": vol},
        )
    elif measure_type == "area":
        area = obj.Shape.Area
        return ToolResult(
            success=True,
            output=f"Surface area of '{obj.Label}': {area:.3f} mm^2",
            data={"area": area},
        )
    elif measure_type == "bbox":
        bb = obj.Shape.BoundBox
        return ToolResult(
            success=True,
            output=(f"Bounding box of '{obj.Label}': "
                    f"X[{bb.XMin:.1f}, {bb.XMax:.1f}] "
                    f"Y[{bb.YMin:.1f}, {bb.YMax:.1f}] "
                    f"Z[{bb.ZMin:.1f}, {bb.ZMax:.1f}] "
                    f"Size: {bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f}mm"),
            data={
                "xmin": bb.XMin, "xmax": bb.XMax,
                "ymin": bb.YMin, "ymax": bb.YMax,
                "zmin": bb.ZMin, "zmax": bb.ZMax,
                "size_x": bb.XLength, "size_y": bb.YLength, "size_z": bb.ZLength,
            },
        )
    elif measure_type == "edges":
        count = len(obj.Shape.Edges)
        edge_info = [f"Edge{i+1}" for i in range(count)]
        return ToolResult(
            success=True,
            output=f"'{obj.Label}' has {count} edges: {', '.join(edge_info)}",
            data={"edge_count": count, "edges": edge_info},
        )
    else:
        return ToolResult(
            success=False, output="",
            error=f"Unknown measure type: {measure_type}. Use: volume, area, bbox, distance, edges"
        )


MEASURE = ToolDefinition(
    name="measure",
    description="Measure properties of objects: volume, surface area, bounding box, distance between objects, or list edges.",
    category="query",
    parameters=[
        ToolParam("measure_type", "string", "What to measure",
                  enum=["volume", "area", "bbox", "distance", "edges"]),
        ToolParam("target", "string", "Internal name of the object to measure"),
        ToolParam("target2", "string", "Second object (for distance measurements)", required=False, default=""),
    ],
    handler=_handle_measure,
)


# ── get_document_state ──────────────────────────────────────

def _handle_get_document_state() -> ToolResult:
    """Get the current document state — all objects and their properties."""
    from ..core.context import get_document_context
    ctx = get_document_context()
    if not ctx:
        return ToolResult(
            success=True,
            output="No document is open, or the document is empty.",
            data={"objects": []},
        )
    return ToolResult(
        success=True,
        output=ctx,
        data={"context": ctx},
    )


GET_DOCUMENT_STATE = ToolDefinition(
    name="get_document_state",
    description="Get the current document state including all objects, their types, labels, and key properties.",
    category="query",
    parameters=[],
    handler=_handle_get_document_state,
)


# ── modify_property ─────────────────────────────────────────

def _handle_modify_property(
    object_name: str,
    property_name: str,
    value: str | int | float | bool | list = "",
) -> ToolResult:
    """Modify a property on an object."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        if not hasattr(obj, property_name):
            return ToolResult(
                success=False, output="",
                error=f"Object '{object_name}' has no property '{property_name}'"
            )

        setattr(obj, property_name, value)
        return ToolResult(
            success=True,
            output=f"Set {object_name}.{property_name} = {value}",
            data={"name": object_name, "property": property_name, "value": value},
        )

    return _with_undo("Modify Property", do)


MODIFY_PROPERTY = ToolDefinition(
    name="modify_property",
    description="Modify a property on a document object (e.g. Length, Width, Height, Radius, Label, Visibility).",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("property_name", "string", "Name of the property to modify"),
        ToolParam("value", "string", "New value for the property (numbers and booleans are auto-converted)"),
    ],
    handler=_handle_modify_property,
)


# ── export_model ────────────────────────────────────────────

def _handle_export_model(
    format: str,
    filename: str,
    objects: list | None = None,
) -> ToolResult:
    """Export the model to a file."""
    import FreeCAD as App
    import Part
    import Mesh

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    if objects:
        objs = [_get_object(doc, n) for n in objects if _get_object(doc, n)]
    else:
        objs = [o for o in doc.Objects if hasattr(o, "Shape")]

    if not objs:
        return ToolResult(success=False, output="", error="No objects to export")

    fmt = format.lower()
    try:
        if fmt == "stl":
            Mesh.export(objs, filename)
        elif fmt in ("step", "stp"):
            Part.export(objs, filename)
        elif fmt in ("iges", "igs"):
            Part.export(objs, filename)
        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown format: {format}. Use: stl, step, iges"
            )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Export failed: {e}")

    return ToolResult(
        success=True,
        output=f"Exported {len(objs)} object(s) to {filename} ({fmt.upper()})",
        data={"filename": filename, "format": fmt, "object_count": len(objs)},
    )


EXPORT_MODEL = ToolDefinition(
    name="export_model",
    description="Export objects to a file (STL, STEP, or IGES format).",
    category="file",
    parameters=[
        ToolParam("format", "string", "Export format", enum=["stl", "step", "iges"]),
        ToolParam("filename", "string", "Output file path"),
        ToolParam("objects", "array", "Object names to export (all if omitted)", required=False,
                  items={"type": "string"}),
    ],
    handler=_handle_export_model,
)


# ── execute_code ────────────────────────────────────────────

def _handle_execute_code(code: str) -> ToolResult:
    """Execute arbitrary Python code (fallback tool)."""
    from ..core.executor import execute_code
    result = execute_code(code)
    if result.success:
        output = result.stdout.strip() if result.stdout.strip() else "Code executed successfully"
        return ToolResult(success=True, output=output, data={"stdout": result.stdout})
    else:
        return ToolResult(success=False, output=result.stdout, error=result.stderr)


EXECUTE_CODE = ToolDefinition(
    name="execute_code",
    description="Execute arbitrary Python code in FreeCAD's interpreter. Use this as a fallback when structured tools don't cover the needed operation. The code has access to FreeCAD, Part, PartDesign, Sketcher, Draft modules.",
    category="general",
    parameters=[
        ToolParam("code", "string", "Python code to execute"),
    ],
    handler=_handle_execute_code,
)


# ── undo ────────────────────────────────────────────────────

def _handle_undo(steps: int = 1) -> ToolResult:
    """Undo the last N operations."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    available = doc.UndoCount
    if available == 0:
        return ToolResult(
            success=False, output="",
            error="Nothing to undo (undo stack is empty)"
        )

    actual = min(steps, available)
    for i in range(actual):
        doc.undo()
    doc.recompute()

    return ToolResult(
        success=True,
        output=f"Undid {actual} operation(s)",
        data={"steps": actual},
    )


UNDO = ToolDefinition(
    name="undo",
    description="Undo the last N operations in the active document.",
    category="general",
    parameters=[
        ToolParam("steps", "integer", "Number of operations to undo", required=False, default=1),
    ],
    handler=_handle_undo,
)


# ── create_inner_ridge ─────────────────────────────────────

def _handle_create_inner_ridge(
    body_name: str,
    length: float,
    width: float,
    wall_thickness: float = 2.0,
    ridge_width: float = 0.8,
    ridge_height: float = 0.5,
    z_position: float = 0.0,
    label: str = "Ridge",
) -> ToolResult:
    """Add a thin ridge/ledge around the inside perimeter of a rectangular body."""
    import FreeCAD as App
    import Part
    import Sketcher

    def do(doc):
        body = _get_object(doc, body_name)
        if not body:
            return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")

        T = wall_thickness
        rw = ridge_width

        # Outer rectangle = inner wall of enclosure
        ox, oy = T, T
        ow, oh = length - 2 * T, width - 2 * T

        # Inner rectangle = inset by ridge_width
        ix, iy = T + rw, T + rw
        iw, ih = length - 2 * T - 2 * rw, width - 2 * T - 2 * rw

        sketch = body.newObject("Sketcher::SketchObject", label + "Sketch")
        xy_plane = _get_body_plane(body, "XY")
        if xy_plane:
            sketch.AttachmentSupport = [(xy_plane, "")]
        sketch.MapMode = "FlatFace"
        sketch.AttachmentOffset = App.Placement(
            App.Vector(0, 0, z_position), App.Rotation())

        # Outer rectangle
        sketch.addGeometry(Part.LineSegment(App.Vector(ox, oy, 0), App.Vector(ox + ow, oy, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(ox + ow, oy, 0), App.Vector(ox + ow, oy + oh, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(ox + ow, oy + oh, 0), App.Vector(ox, oy + oh, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(ox, oy + oh, 0), App.Vector(ox, oy, 0)))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))

        # Inner rectangle (creates the ring shape)
        sketch.addGeometry(Part.LineSegment(App.Vector(ix, iy, 0), App.Vector(ix + iw, iy, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(ix + iw, iy, 0), App.Vector(ix + iw, iy + ih, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(ix + iw, iy + ih, 0), App.Vector(ix, iy + ih, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(ix, iy + ih, 0), App.Vector(ix, iy, 0)))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 4, 2, 5, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 5, 2, 6, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 6, 2, 7, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", 7, 2, 4, 1))

        doc.recompute()

        pad = body.newObject("PartDesign::Pad", label)
        pad.Profile = sketch
        pad.Length = ridge_height
        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Created inner ridge '{label}' at z={z_position}mm ({ridge_width}mm wide, {ridge_height}mm tall)",
            data={"name": pad.Name, "label": label},
        )

    return _with_undo("Create Inner Ridge", do)


CREATE_INNER_RIDGE = ToolDefinition(
    name="create_inner_ridge",
    description=(
        "Add a thin ridge/ledge running around the inside perimeter of a rectangular "
        "hollow body. Useful as a catch for snap-fit lids. The defaults (0.8mm wide, "
        "0.5mm tall) are tuned for 3D printing — do NOT override ridge_width/ridge_height "
        "unless the user explicitly requests different dimensions."
    ),
    category="modeling",
    parameters=[
        ToolParam("body_name", "string", "Name of the PartDesign body to add the ridge to"),
        ToolParam("length", "number", "Outer length of the enclosure (L)"),
        ToolParam("width", "number", "Outer width of the enclosure (W)"),
        ToolParam("wall_thickness", "number", "Wall thickness (T) — MUST match the enclosure wall thickness"),
        ToolParam("ridge_width", "number", "Inward protrusion from wall (mm). Default 0.8 — do not increase", required=False, default=0.8),
        ToolParam("ridge_height", "number", "Height along Z (mm). Default 0.5 — do not increase", required=False, default=0.5),
        ToolParam("z_position", "number", "Z height where the ridge starts (typically H-2)"),
        ToolParam("label", "string", "Display label", required=False, default="Ridge"),
    ],
    handler=_handle_create_inner_ridge,
)


# ── create_snap_tabs ──────────────────────────────────────

def _handle_create_snap_tabs(
    body_name: str,
    length: float,
    width: float,
    wall_thickness: float = 2.0,
    clearance: float = 0.2,
    lip_height: float = 3.0,
    tab_width: float = 3.0,
    tab_height: float = 1.0,
    protrusion: float = 0.5,
    label: str = "SnapTab",
) -> ToolResult:
    """Add snap tabs on the outside of a rectangular lip that catch on an inner ridge."""
    import FreeCAD as App
    import Part

    def do(doc):
        body = _get_object(doc, body_name)
        if not body:
            return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")

        T = wall_thickness
        cl = clearance

        # Clamp protrusion so tabs stay within the clearance gap
        # (otherwise they penetrate the base wall)
        actual_protrusion = min(protrusion, cl - 0.05)
        if actual_protrusion < 0.1:
            return ToolResult(
                success=False, output="",
                error=f"Clearance ({cl}mm) too small for snap tabs. "
                      f"Need at least 0.5mm; got {cl}mm. "
                      f"Use a wider lip clearance for snap-fit lids.")

        # body.Shape for PartDesign bodies already includes Placement,
        # so tab boxes must be created in global coordinates to match.
        ox = body.Placement.Base.x
        oy = body.Placement.Base.y
        oz = body.Placement.Base.z

        # Lip outer dimensions (shifted to global coords)
        lip_x1 = T + cl + ox
        lip_x2 = length - T - cl + ox
        lip_y1 = T + cl + oy
        lip_y2 = width - T - cl + oy
        lip_cx = (lip_x1 + lip_x2) / 2
        lip_cy = (lip_y1 + lip_y2) / 2

        # Tab Z: at the bottom of the lip, with a gap below the ridge.
        # Shorten tab by 0.3mm so it doesn't touch the ridge above.
        snap_gap = 0.3
        th = tab_height - snap_gap  # effective tab height
        tab_z = th / 2 + oz

        # Place 2 tabs on each long side, 1 on each short side
        tabs = []

        # Long sides (front y=lip_y1, back y=lip_y2) — 2 tabs each
        p = actual_protrusion
        third = (lip_x2 - lip_x1) / 3
        for x_off in [lip_x1 + third, lip_x1 + 2 * third]:
            # Front wall tab: protrudes in -Y direction
            tabs.append({
                "x": x_off, "y": lip_y1, "z": tab_z,
                "sx": tab_width, "sy": p, "sz": th,
                "dy": -p,
            })
            # Back wall tab: protrudes in +Y direction
            tabs.append({
                "x": x_off, "y": lip_y2, "z": tab_z,
                "sx": tab_width, "sy": p, "sz": th,
                "dy": 0,
            })

        # Short sides (left x=lip_x1, right x=lip_x2) — 1 tab each
        # Left wall tab: protrudes in -X direction
        tabs.append({
            "x": lip_x1, "y": lip_cy, "z": tab_z,
            "sx": p, "sy": tab_width, "sz": th,
            "dx": -p,
        })
        # Right wall tab: protrudes in +X direction
        tabs.append({
            "x": lip_x2, "y": lip_cy, "z": tab_z,
            "sx": p, "sy": tab_width, "sz": th,
            "dx": 0,
        })

        # Create tab shapes and fuse with body
        body_shape = body.Shape.copy()
        tab_count = 0
        for t in tabs:
            bx = t.get("dx", 0) + t["x"] - t["sx"] / 2
            by = t.get("dy", 0) + t["y"] - t["sy"] / 2
            bz = t["z"] - t["sz"] / 2
            # Adjust: for wall-adjacent tabs, anchor to wall edge
            if "dx" in t:
                bx = t["x"] + t["dx"]
            if "dy" in t:
                by = t["y"] + t["dy"]

            box = Part.makeBox(t["sx"], t["sy"], t["sz"],
                               App.Vector(bx, by, bz))
            body_shape = body_shape.fuse(box)
            tab_count += 1

        body_shape = body_shape.removeSplitter()

        # Create a Part::Feature (shape is already in global coords)
        tab_obj = doc.addObject("Part::Feature", label)
        tab_obj.Label = label
        tab_obj.Shape = body_shape

        # Hide the original body — the tab object replaces its visual
        body.Visibility = False

        return ToolResult(
            success=True,
            output=f"Added {tab_count} snap tabs to '{body_name}' (protrusion={actual_protrusion:.1f}mm). Result in '{label}'.",
            data={"name": tab_obj.Name, "label": label, "tab_count": tab_count},
        )

    return _with_undo("Create Snap Tabs", do)


CREATE_SNAP_TABS = ToolDefinition(
    name="create_snap_tabs",
    description=(
        "Add snap tabs on the outside of a rectangular lip. The tabs catch on an inner "
        "ridge to hold the lid in place. Places 2 tabs on each long side and 1 on each "
        "short side. IMPORTANT: the lid must be built lip-FIRST (lip at body origin, slab "
        "on top) and positioned BEFORE calling this tool. Use defaults for tab dimensions."
    ),
    category="modeling",
    parameters=[
        ToolParam("body_name", "string", "Name of the lid body with the lip"),
        ToolParam("length", "number", "Outer length of the enclosure (L)"),
        ToolParam("width", "number", "Outer width of the enclosure (W)"),
        ToolParam("wall_thickness", "number", "Wall thickness (T) — MUST match the enclosure wall thickness"),
        ToolParam("clearance", "number", "Gap between lip and wall (mm). Use 1.0 for snap-fit", required=False, default=0.2),
        ToolParam("lip_height", "number", "Height of the lip (mm)", required=False, default=3.0),
        ToolParam("tab_width", "number", "Width of each tab along the wall (mm)", required=False, default=3.0),
        ToolParam("tab_height", "number", "Height of each tab along Z (mm)", required=False, default=1.0),
        ToolParam("protrusion", "number", "How far each tab protrudes outward (mm)", required=False, default=0.5),
        ToolParam("label", "string", "Display label for the result", required=False, default="SnapTab"),
    ],
    handler=_handle_create_snap_tabs,
)


# ── create_enclosure_lid ─────────────────────────────────

def _handle_create_enclosure_lid(
    length: float,
    width: float,
    wall_thickness: float,
    clearance: float = 1.0,
    lip_height: float = 3.0,
    label: str = "EnclosureLid",
) -> ToolResult:
    """Create a snap-fit enclosure lid with correct lip+slab geometry."""
    import FreeCAD as App
    import Part

    def do(doc):
        T = wall_thickness
        CL = clearance
        LH = lip_height

        body = doc.addObject("PartDesign::Body", label)
        body.Label = label
        doc.recompute()

        # ── Step 1: Lip (built first so it points downward when positioned) ──
        lip_x = T + CL
        lip_y = T + CL
        lip_w = length - 2 * T - 2 * CL
        lip_h = width - 2 * T - 2 * CL

        if lip_w <= 0 or lip_h <= 0:
            return ToolResult(
                success=False, output="",
                error=f"Lip dimensions too small ({lip_w:.1f}x{lip_h:.1f}mm). "
                      f"Reduce wall_thickness or clearance.")

        lip_sketch = body.newObject("Sketcher::SketchObject", "LipSketch")
        xy_plane = _get_body_plane(body, "XY")
        if xy_plane:
            lip_sketch.AttachmentSupport = [(xy_plane, "")]
        lip_sketch.MapMode = "FlatFace"

        # Rectangle for lip
        x1, y1 = lip_x, lip_y
        x2, y2 = lip_x + lip_w, lip_y + lip_h
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)))
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)))
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)))
        lip_sketch.addGeometry(Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)))
        import Sketcher
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))
        lip_sketch.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))
        doc.recompute()

        lip_pad = body.newObject("PartDesign::Pad", "LipPad")
        lip_pad.Profile = lip_sketch
        lip_pad.Length = LH
        lip_sketch.Visibility = False

        # ── Step 2: Slab on top of lip (full enclosure size) ──
        slab_sketch = body.newObject("Sketcher::SketchObject", "SlabSketch")
        if xy_plane:
            slab_sketch.AttachmentSupport = [(xy_plane, "")]
        slab_sketch.MapMode = "FlatFace"
        slab_sketch.AttachmentOffset = App.Placement(
            App.Vector(0, 0, LH), App.Rotation())

        slab_sketch.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(length, 0, 0)))
        slab_sketch.addGeometry(Part.LineSegment(App.Vector(length, 0, 0), App.Vector(length, width, 0)))
        slab_sketch.addGeometry(Part.LineSegment(App.Vector(length, width, 0), App.Vector(0, width, 0)))
        slab_sketch.addGeometry(Part.LineSegment(App.Vector(0, width, 0), App.Vector(0, 0, 0)))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))
        slab_sketch.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))
        doc.recompute()

        slab_pad = body.newObject("PartDesign::Pad", "SlabPad")
        slab_pad.Profile = slab_sketch
        slab_pad.Length = T
        slab_sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Created enclosure lid '{label}' (lip: {lip_w:.0f}x{lip_h:.0f}x{LH:.0f}mm, slab: {length:.0f}x{width:.0f}x{T:.0f}mm). "
                   f"Use transform_object to position at z=H-{LH:.0f}.",
            data={"name": body.Name, "label": label,
                  "lip_width": lip_w, "lip_height_dim": lip_h, "lip_depth": LH,
                  "slab_thickness": T},
        )

    return _with_undo("Create Enclosure Lid", do)


CREATE_ENCLOSURE_LID = ToolDefinition(
    name="create_enclosure_lid",
    description=(
        "Create a snap-fit enclosure lid body with correct lip+slab geometry. "
        "The lip is automatically inset by wall_thickness+clearance so it fits inside "
        "the base cavity with room for snap tabs. After calling this, position the lid "
        "with transform_object at z=H-lip_height, then add snap tabs."
    ),
    category="modeling",
    parameters=[
        ToolParam("length", "number", "Outer length of the enclosure (L)"),
        ToolParam("width", "number", "Outer width of the enclosure (W)"),
        ToolParam("wall_thickness", "number", "Wall thickness (T) — must match the base"),
        ToolParam("clearance", "number", "Gap between lip and cavity wall (mm). Use 1.0 for snap-fit", required=False, default=1.0),
        ToolParam("lip_height", "number", "How far the lip extends down into the base (mm)", required=False, default=3.0),
        ToolParam("label", "string", "Display label for the lid body", required=False, default="EnclosureLid"),
    ],
    handler=_handle_create_enclosure_lid,
)


# ── create_wedge ───────────────────────────────────────────

def _handle_create_wedge(
    length: float = 10.0,
    width: float = 10.0,
    height: float = 10.0,
    top_length: float | None = None,
    top_width: float | None = None,
    label: str = "",
    body_name: str = "",
    operation: str = "additive",
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> ToolResult:
    """Create a wedge (tapered box) as a PartDesign loft between two rectangular sketches."""
    import FreeCAD as App
    import Part
    import Sketcher

    def _add_rect(sketch, x1, y1, x2, y2):
        """Add a closed rectangle to a sketch with coincident + H/V constraints."""
        sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)))
        g = sketch.GeometryCount - 4
        sketch.addConstraint(Sketcher.Constraint("Coincident", g, 2, g + 1, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 1, 2, g + 2, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 2, 2, g + 3, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 3, 2, g, 1))
        sketch.addConstraint(Sketcher.Constraint("Horizontal", g))
        sketch.addConstraint(Sketcher.Constraint("Horizontal", g + 2))
        sketch.addConstraint(Sketcher.Constraint("Vertical", g + 1))
        sketch.addConstraint(Sketcher.Constraint("Vertical", g + 3))

    def do(doc):
        tl = top_length if top_length is not None else length
        tw = top_width if top_width is not None else 0.0
        # Clamp degenerate dimensions — lofting a rect to a line/point is unreliable
        tl = max(tl, 0.01)
        tw = max(tw, 0.01)

        op = operation.lower()

        # Get or create body
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                return ToolResult(
                    success=False, output="",
                    error=f"Body '{body_name}' not found",
                )
        else:
            body_label = label or "Wedge"
            body = doc.addObject("PartDesign::Body", body_label)
            body.Label = body_label

        # Attach sketches to XY plane
        xy_plane = _get_body_plane(body, "XY")
        if not xy_plane:
            return ToolResult(
                success=False, output="",
                error="Cannot find XY plane in body's origin",
            )

        # Bottom sketch: rectangle (0,0) to (length, width) on XY plane
        bot = body.newObject("Sketcher::SketchObject", "WedgeBase")
        bot.AttachmentSupport = [(xy_plane, "")]
        bot.MapMode = "FlatFace"
        doc.recompute()
        _add_rect(bot, 0, 0, length, width)

        # Top sketch: centered top rectangle at z=height
        top = body.newObject("Sketcher::SketchObject", "WedgeTop")
        top.AttachmentSupport = [(xy_plane, "")]
        top.MapMode = "FlatFace"
        top.AttachmentOffset = App.Placement(
            App.Vector(0, 0, height), App.Rotation()
        )
        doc.recompute()
        tx1 = (length - tl) / 2
        ty1 = (width - tw) / 2
        _add_rect(top, tx1, ty1, tx1 + tl, ty1 + tw)

        doc.recompute()

        # Loft between the two sketches
        if op == "subtractive":
            type_name = "PartDesign::SubtractiveLoft"
        else:
            type_name = "PartDesign::AdditiveLoft"
        feat_label = label or "Wedge"
        feat = body.newObject(type_name, feat_label)
        feat.Profile = bot
        feat.Sections = [top]
        feat.Ruled = True

        bot.Visibility = False
        top.Visibility = False

        # Position the body if needed
        if x != 0 or y != 0 or z != 0:
            body.Placement.Base = App.Vector(x, y, z)

        doc.recompute()

        return ToolResult(
            success=True,
            output=(
                f"Created {op} wedge '{feat.Label}' ({feat.Name}) in body "
                f"'{body.Label}' ({body.Name}) — "
                f"{length}x{width}x{height}mm, top: {tl}x{tw}mm"
            ),
            data={
                "name": feat.Name,
                "label": feat.Label,
                "body_name": body.Name,
                "body_label": body.Label,
            },
        )

    return _with_undo("Create Wedge", do)


CREATE_WEDGE = ToolDefinition(
    name="create_wedge",
    description="Create a PartDesign wedge (tapered box) inside a Body via loft. Base is length x width, top face is top_length x top_width (centered). Default top_width=0 creates a classic ramp/wedge shape. Compatible with fillet, chamfer, shell, pattern, mirror.",
    category="modeling",
    parameters=[
        ToolParam("length", "number", "Base length (X dimension)", required=False, default=10.0),
        ToolParam("width", "number", "Base width (Y dimension)", required=False, default=10.0),
        ToolParam("height", "number", "Height (Z dimension)", required=False, default=10.0),
        ToolParam("top_length", "number", "Top face length (defaults to base length = no taper in X)", required=False),
        ToolParam("top_width", "number", "Top face width (defaults to 0 = tapers to ridge)", required=False),
        ToolParam("label", "string", "Display label", required=False, default=""),
        ToolParam("body_name", "string", "Name of existing Body to add wedge to (auto-creates if empty)", required=False, default=""),
        ToolParam("operation", "string", "Additive (add material) or subtractive (cut material)",
                  required=False, default="additive", enum=["additive", "subtractive"]),
        ToolParam("x", "number", "X position", required=False, default=0.0),
        ToolParam("y", "number", "Y position", required=False, default=0.0),
        ToolParam("z", "number", "Z position", required=False, default=0.0),
    ],
    handler=_handle_create_wedge,
)


# ── scale_object ──────────────────────────────────────────

def _handle_scale_object(
    object_name: str,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    scale_z: float = 1.0,
    uniform: float = 0.0,
    copy: bool = False,
    label: str = "",
) -> ToolResult:
    """Scale an object non-uniformly via shape.transformGeometry()."""
    import FreeCAD as App

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            return ToolResult(
                success=False, output="",
                error="Cannot scale a PartDesign::Body directly. Scale individual Part objects instead."
            )

        if not hasattr(obj, "Shape"):
            return ToolResult(success=False, output="", error=f"Object '{object_name}' has no Shape")

        sx = uniform if uniform != 0 else scale_x
        sy = uniform if uniform != 0 else scale_y
        sz = uniform if uniform != 0 else scale_z

        mat = App.Matrix()
        mat.scale(sx, sy, sz)
        new_shape = obj.Shape.transformGeometry(mat)

        if copy:
            new_name = label or f"{obj.Label}_Scaled"
            new_obj = doc.addObject("Part::Feature", new_name)
            new_obj.Label = new_name
            new_obj.Shape = new_shape
            return ToolResult(
                success=True,
                output=f"Created scaled copy '{new_obj.Label}' (scale: {sx}, {sy}, {sz})",
                data={"name": new_obj.Name, "label": new_obj.Label},
            )
        else:
            obj.Shape = new_shape
            return ToolResult(
                success=True,
                output=f"Scaled '{obj.Label}' by ({sx}, {sy}, {sz})",
                data={"name": obj.Name, "label": obj.Label},
            )

    return _with_undo("Scale Object", do)


SCALE_OBJECT = ToolDefinition(
    name="scale_object",
    description="Scale an object uniformly or non-uniformly. Works on Part objects (not PartDesign bodies). Set uniform>0 to scale all axes equally.",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to scale"),
        ToolParam("scale_x", "number", "X scale factor", required=False, default=1.0),
        ToolParam("scale_y", "number", "Y scale factor", required=False, default=1.0),
        ToolParam("scale_z", "number", "Z scale factor", required=False, default=1.0),
        ToolParam("uniform", "number", "Uniform scale (overrides x/y/z if non-zero)", required=False, default=0.0),
        ToolParam("copy", "boolean", "Create a scaled copy instead of modifying in-place", required=False, default=False),
        ToolParam("label", "string", "Label for the copy (only used with copy=True)", required=False, default=""),
    ],
    handler=_handle_scale_object,
)


# ── section_object ────────────────────────────────────────

def _handle_section_object(
    object_name: str,
    tool_object: str = "",
    plane: str = "XY",
    offset: float = 0.0,
    label: str = "",
) -> ToolResult:
    """Create a cross-section of an object."""
    import FreeCAD as App
    import Part

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        if tool_object:
            # Shape-vs-shape section
            tool = _get_object(doc, tool_object)
            if not tool:
                return ToolResult(success=False, output="", error=f"Tool object '{tool_object}' not found")
            name = label or "Section"
            sec = doc.addObject("Part::Section", name)
            sec.Base = obj
            sec.Tool = tool
            return ToolResult(
                success=True,
                output=f"Created section of '{obj.Label}' with '{tool.Label}'",
                data={"name": sec.Name, "label": sec.Label},
            )

        # Shape-vs-plane section
        bb = obj.Shape.BoundBox
        size = max(bb.XLength, bb.YLength, bb.ZLength) * 2 + 10

        plane_upper = plane.upper()
        if plane_upper == "XY":
            origin = App.Vector(bb.XMin - 5, bb.YMin - 5, offset)
            normal = App.Vector(0, 0, 1)
        elif plane_upper == "XZ":
            origin = App.Vector(bb.XMin - 5, offset, bb.ZMin - 5)
            normal = App.Vector(0, 1, 0)
        elif plane_upper == "YZ":
            origin = App.Vector(offset, bb.YMin - 5, bb.ZMin - 5)
            normal = App.Vector(1, 0, 0)
        else:
            return ToolResult(success=False, output="", error=f"Unknown plane: {plane}. Use XY, XZ, or YZ")

        cut_plane = Part.makePlane(size, size, origin, normal)
        section_shape = obj.Shape.section(cut_plane)

        name = label or "Section"
        sec_obj = doc.addObject("Part::Feature", name)
        sec_obj.Label = name
        sec_obj.Shape = section_shape

        edge_count = len(section_shape.Edges)
        return ToolResult(
            success=True,
            output=f"Created {plane_upper} section of '{obj.Label}' at offset={offset}mm ({edge_count} edges)",
            data={"name": sec_obj.Name, "label": sec_obj.Label, "edge_count": edge_count,
                  "bbox": {"xmin": section_shape.BoundBox.XMin, "xmax": section_shape.BoundBox.XMax,
                           "ymin": section_shape.BoundBox.YMin, "ymax": section_shape.BoundBox.YMax,
                           "zmin": section_shape.BoundBox.ZMin, "zmax": section_shape.BoundBox.ZMax}},
        )

    return _with_undo("Section Object", do)


SECTION_OBJECT = ToolDefinition(
    name="section_object",
    description="Create a cross-section: either cut an object with a plane (XY/XZ/YZ at a given offset) or intersect two shapes.",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to section"),
        ToolParam("tool_object", "string", "Second object for shape-vs-shape section (omit for plane section)", required=False, default=""),
        ToolParam("plane", "string", "Section plane (used when tool_object is omitted)", required=False, default="XY",
                  enum=["XY", "XZ", "YZ"]),
        ToolParam("offset", "number", "Offset along the plane normal (e.g. z-height for XY plane)", required=False, default=0.0),
        ToolParam("label", "string", "Display label for the section", required=False, default=""),
    ],
    handler=_handle_section_object,
)


# ── linear_pattern ────────────────────────────────────────

def _handle_linear_pattern(
    feature_name: str,
    direction: str = "X",
    length: float = 10.0,
    occurrences: int = 2,
    label: str = "",
) -> ToolResult:
    """Create a PartDesign::LinearPattern repeating a feature along an axis."""

    def do(doc):
        feature = _get_object(doc, feature_name)
        if not feature:
            return ToolResult(success=False, output="", error=f"Feature '{feature_name}' not found")

        body = _find_body_for(doc, feature)
        if not body:
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is not inside a PartDesign body. Linear pattern requires a body."
            )

        name = label or "LinearPattern"
        pattern = body.newObject("PartDesign::LinearPattern", name)
        pattern.Originals = [feature]

        # Resolve direction
        dir_upper = direction.upper()
        if dir_upper in ("X", "Y", "Z"):
            axis = _get_body_axis(body, dir_upper)
            if not axis:
                return ToolResult(success=False, output="", error=f"Could not find {dir_upper} axis on body")
            pattern.Direction = (axis, [""])
        else:
            # Sketch edge reference like "Sketch.Edge1"
            parts = direction.split(".")
            if len(parts) == 2:
                ref_obj = _get_object(doc, parts[0])
                if ref_obj:
                    pattern.Direction = (ref_obj, [parts[1]])
                else:
                    return ToolResult(success=False, output="", error=f"Reference object '{parts[0]}' not found")
            else:
                return ToolResult(success=False, output="", error=f"Invalid direction: {direction}. Use X/Y/Z or Sketch.Edge1")

        pattern.Length = length
        pattern.Occurrences = occurrences

        return ToolResult(
            success=True,
            output=f"Created linear pattern of '{feature_name}' ({occurrences} occurrences, {length}mm span, direction={direction})",
            data={"name": pattern.Name, "label": pattern.Label, "occurrences": occurrences},
        )

    return _with_undo("Linear Pattern", do)


LINEAR_PATTERN = ToolDefinition(
    name="linear_pattern",
    description="Repeat a PartDesign feature in a linear pattern along an axis. The feature must be inside a PartDesign Body.",
    category="modeling",
    parameters=[
        ToolParam("feature_name", "string", "Internal name of the feature to repeat"),
        ToolParam("direction", "string", "Pattern direction: X, Y, Z (origin axes) or Sketch.Edge1 (sketch edge)", required=False, default="X"),
        ToolParam("length", "number", "Total span of the pattern in mm"),
        ToolParam("occurrences", "integer", "Number of occurrences (including the original)"),
        ToolParam("label", "string", "Display label for the pattern", required=False, default=""),
    ],
    handler=_handle_linear_pattern,
)


# ── polar_pattern ─────────────────────────────────────────

def _handle_polar_pattern(
    feature_name: str,
    axis: str = "Z",
    angle: float = 360.0,
    occurrences: int = 2,
    label: str = "",
) -> ToolResult:
    """Create a PartDesign::PolarPattern repeating a feature around an axis."""

    def do(doc):
        feature = _get_object(doc, feature_name)
        if not feature:
            return ToolResult(success=False, output="", error=f"Feature '{feature_name}' not found")

        body = _find_body_for(doc, feature)
        if not body:
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is not inside a PartDesign body. Polar pattern requires a body."
            )

        name = label or "PolarPattern"
        pattern = body.newObject("PartDesign::PolarPattern", name)
        pattern.Originals = [feature]

        # Resolve axis
        axis_upper = axis.upper()
        if axis_upper in ("X", "Y", "Z"):
            axis_obj = _get_body_axis(body, axis_upper)
            if not axis_obj:
                return ToolResult(success=False, output="", error=f"Could not find {axis_upper} axis on body")
            pattern.Axis = (axis_obj, [""])
        else:
            parts = axis.split(".")
            if len(parts) == 2:
                ref_obj = _get_object(doc, parts[0])
                if ref_obj:
                    pattern.Axis = (ref_obj, [parts[1]])
                else:
                    return ToolResult(success=False, output="", error=f"Reference object '{parts[0]}' not found")
            else:
                return ToolResult(success=False, output="", error=f"Invalid axis: {axis}. Use X/Y/Z or Sketch.Edge1")

        pattern.Angle = angle
        pattern.Occurrences = occurrences

        return ToolResult(
            success=True,
            output=f"Created polar pattern of '{feature_name}' ({occurrences} occurrences, {angle}° span, axis={axis})",
            data={"name": pattern.Name, "label": pattern.Label, "occurrences": occurrences},
        )

    return _with_undo("Polar Pattern", do)


POLAR_PATTERN = ToolDefinition(
    name="polar_pattern",
    description="Repeat a PartDesign feature in a circular pattern around an axis. The feature must be inside a PartDesign Body.",
    category="modeling",
    parameters=[
        ToolParam("feature_name", "string", "Internal name of the feature to repeat"),
        ToolParam("axis", "string", "Rotation axis: X, Y, Z (origin axes) or Sketch.Edge1 (sketch edge)", required=False, default="Z"),
        ToolParam("angle", "number", "Total angular span in degrees (360 = full circle)", required=False, default=360.0),
        ToolParam("occurrences", "integer", "Number of occurrences (including the original)"),
        ToolParam("label", "string", "Display label for the pattern", required=False, default=""),
    ],
    handler=_handle_polar_pattern,
)


# ── shell_object ───────────────────────────────────────────

def _handle_shell_object(
    object_name: str,
    faces: list | None = None,
    thickness: float = 1.0,
    join: str = "Arc",
    reversed: bool = True,
    label: str = "",
) -> ToolResult:
    """Hollow out a solid by removing faces and applying wall thickness."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        face_refs = _coerce_str_list(faces) or ["Face1"]
        join_map = {"Arc": 0, "Intersection": 1}

        body = _find_body_for(doc, obj)
        if body:
            shell = body.newObject("PartDesign::Thickness", label or "Shell")
            shell.Base = (obj, face_refs)
            shell.Value = thickness
            shell.Join = join_map.get(join, 0)
            shell.Reversed = reversed
        else:
            return ToolResult(
                success=False, output="",
                error=f"Object '{object_name}' is not inside a PartDesign Body. "
                      "shell_object requires a PartDesign Body. Use create_body + create_sketch + pad_sketch "
                      "to create the solid, then apply shell_object.",
            )

        return ToolResult(
            success=True,
            output=f"Applied shell (thickness={thickness}mm) to '{obj.Label}' removing {len(face_refs)} face(s)",
            data={"name": shell.Name, "label": shell.Label, "thickness": thickness},
        )

    return _with_undo("Shell Object", do)


SHELL_OBJECT = ToolDefinition(
    name="shell_object",
    description="Hollow out a solid by removing selected faces and applying a wall thickness (PartDesign::Thickness).",
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the solid object to shell"),
        ToolParam("faces", "array", "Face references to remove, e.g. ['Face1', 'Face6']", required=False,
                  items={"type": "string"}),
        ToolParam("thickness", "number", "Wall thickness in mm", required=False, default=1.0),
        ToolParam("join", "string", "Join type for corners", required=False, default="Arc",
                  enum=["Arc", "Intersection"]),
        ToolParam("reversed", "boolean", "Shell direction: True (default) = inward (preserves outer dimensions), False = outward", required=False, default=True),
        ToolParam("label", "string", "Display label for the shell feature", required=False, default=""),
    ],
    handler=_handle_shell_object,
)


# ── mirror_feature ─────────────────────────────────────────

def _handle_mirror_feature(
    feature_name: str,
    plane: str = "YZ",
    label: str = "",
) -> ToolResult:
    """Mirror a PartDesign feature across a plane."""

    def do(doc):
        feature = _get_object(doc, feature_name)
        if not feature:
            return ToolResult(success=False, output="", error=f"Feature '{feature_name}' not found")

        body = _find_body_for(doc, feature)
        if not body:
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is not inside a PartDesign Body",
            )

        # Only additive/subtractive features can be transformed
        if hasattr(feature, "isDerivedFrom") and feature.isDerivedFrom("PartDesign::Transformed"):
            originals = getattr(feature, "Originals", [])
            hint = ""
            if originals:
                hint = f" Try mirroring '{originals[0].Name}' instead."
            return ToolResult(
                success=False, output="",
                error=f"Feature '{feature_name}' is a transformation (Mirrored/Pattern) and cannot be mirrored. "
                      f"Only additive features (Pad, Loft, etc.) and subtractive features (Pocket, Groove, etc.) "
                      f"can be mirrored.{hint}",
            )

        name = label or "Mirrored"
        mirror = body.newObject("PartDesign::Mirrored", name)
        mirror.Originals = [feature]

        # Resolve mirror plane
        plane_upper = plane.upper()
        if plane_upper in ("XY", "XZ", "YZ"):
            plane_obj = _get_body_plane(body, plane_upper)
            if not plane_obj:
                return ToolResult(success=False, output="", error=f"Could not find {plane_upper} plane on body")
            mirror.MirrorPlane = (plane_obj, [""])
        else:
            # "Sketch.N_Axis" or "Sketch.V_Axis" format
            parts = plane.split(".")
            if len(parts) == 2:
                ref_obj = _get_object(doc, parts[0])
                if ref_obj:
                    mirror.MirrorPlane = (ref_obj, [parts[1]])
                else:
                    return ToolResult(success=False, output="", error=f"Reference object '{parts[0]}' not found")
            else:
                return ToolResult(success=False, output="", error=f"Invalid plane: {plane}. Use XY/XZ/YZ or Sketch.N_Axis")

        return ToolResult(
            success=True,
            output=f"Mirrored '{feature_name}' across {plane}",
            data={"name": mirror.Name, "label": mirror.Label, "plane": plane},
        )

    return _with_undo("Mirror Feature", do)


MIRROR_FEATURE = ToolDefinition(
    name="mirror_feature",
    description="Mirror a PartDesign feature across a plane. The feature must be an additive (Pad, Loft, etc.) or subtractive (Pocket, Groove, etc.) feature inside a Body. Cannot mirror other transformations (Mirrored, LinearPattern, PolarPattern) — mirror the original feature instead.",
    category="modeling",
    parameters=[
        ToolParam("feature_name", "string", "Internal name of the feature to mirror"),
        ToolParam("plane", "string", "Mirror plane: XY, XZ, YZ (origin planes) or Sketch.N_Axis (sketch axis)",
                  required=False, default="YZ"),
        ToolParam("label", "string", "Display label for the mirror", required=False, default=""),
    ],
    handler=_handle_mirror_feature,
)


# ── multi_transform ────────────────────────────────────────

def _handle_multi_transform(
    feature_names: list = None,
    transformations: list = None,
    label: str = "",
    # Backward compat: old callers may pass feature_name as str
    feature_name: str = None,
) -> ToolResult:
    """Chain multiple transformation steps (linear, polar, mirror) into one MultiTransform feature."""

    if not transformations:
        return ToolResult(success=False, output="", error="transformations list must not be empty")

    # Normalize: accept old feature_name kwarg for backward compat
    raw = feature_names if feature_names is not None else feature_name
    if raw is None:
        return ToolResult(success=False, output="", error="feature_names is required")
    names = _coerce_str_list(raw)
    # Bare string → wrap in list
    if isinstance(names, str):
        names = [names]
    if not names:
        return ToolResult(success=False, output="", error="feature_names must not be empty")

    def do(doc):
        features = []
        body = None
        for fname in names:
            feat = _get_object(doc, fname)
            if not feat:
                return ToolResult(success=False, output="", error=f"Feature '{fname}' not found")

            feat_body = _find_body_for(doc, feat)
            if not feat_body:
                return ToolResult(
                    success=False, output="",
                    error=f"Feature '{fname}' is not inside a PartDesign Body",
                )

            if body is None:
                body = feat_body
            elif feat_body.Name != body.Name:
                return ToolResult(
                    success=False, output="",
                    error=f"All features must be in the same Body. "
                          f"'{fname}' is in '{feat_body.Label}', expected '{body.Label}'.",
                )

            # Reject transformation features (same guard as mirror_feature)
            if hasattr(feat, "isDerivedFrom") and feat.isDerivedFrom("PartDesign::Transformed"):
                originals = getattr(feat, "Originals", [])
                hint = ""
                if originals:
                    hint = f" Try transforming '{originals[0].Name}' instead."
                return ToolResult(
                    success=False, output="",
                    error=f"Feature '{fname}' is a transformation and cannot be multi-transformed. "
                          f"Only additive/subtractive features can be used.{hint}",
                )
            features.append(feat)

        name = label or "MultiTransform"
        multi = body.newObject("PartDesign::MultiTransform", name)
        multi.Originals = features

        sub_features = []
        descriptions = []

        for i, step in enumerate(transformations):
            step_type = step.get("type", "")

            if step_type == "linear_pattern":
                sub = body.newObject("PartDesign::LinearPattern", f"LP{i}")
                direction = step.get("direction", "X")
                dir_upper = direction.upper()
                if dir_upper in ("X", "Y", "Z"):
                    axis = _get_body_axis(body, dir_upper)
                    if not axis:
                        return ToolResult(success=False, output="", error=f"Step {i}: could not find {dir_upper} axis")
                    sub.Direction = (axis, [""])
                else:
                    parts = direction.split(".")
                    if len(parts) == 2:
                        ref_obj = _get_object(doc, parts[0])
                        if ref_obj:
                            sub.Direction = (ref_obj, [parts[1]])
                        else:
                            return ToolResult(success=False, output="", error=f"Step {i}: reference '{parts[0]}' not found")
                    else:
                        return ToolResult(success=False, output="", error=f"Step {i}: invalid direction '{direction}'")
                sub.Length = step.get("length", 10.0)
                sub.Occurrences = step.get("occurrences", 2)
                sub_features.append(sub)
                descriptions.append(f"linear({dir_upper}, {sub.Length}mm, {sub.Occurrences}x)")

            elif step_type == "polar_pattern":
                sub = body.newObject("PartDesign::PolarPattern", f"PP{i}")
                axis = step.get("axis", "Z")
                axis_upper = axis.upper()
                if axis_upper in ("X", "Y", "Z"):
                    axis_obj = _get_body_axis(body, axis_upper)
                    if not axis_obj:
                        return ToolResult(success=False, output="", error=f"Step {i}: could not find {axis_upper} axis")
                    sub.Axis = (axis_obj, [""])
                else:
                    parts = axis.split(".")
                    if len(parts) == 2:
                        ref_obj = _get_object(doc, parts[0])
                        if ref_obj:
                            sub.Axis = (ref_obj, [parts[1]])
                        else:
                            return ToolResult(success=False, output="", error=f"Step {i}: reference '{parts[0]}' not found")
                    else:
                        return ToolResult(success=False, output="", error=f"Step {i}: invalid axis '{axis}'")
                sub.Angle = step.get("angle", 360.0)
                sub.Occurrences = step.get("occurrences", 2)
                sub_features.append(sub)
                descriptions.append(f"polar({axis_upper}, {sub.Angle}°, {sub.Occurrences}x)")

            elif step_type == "mirror":
                sub = body.newObject("PartDesign::Mirrored", f"MR{i}")
                plane = step.get("plane", "YZ")
                plane_upper = plane.upper()
                if plane_upper in ("XY", "XZ", "YZ"):
                    plane_obj = _get_body_plane(body, plane_upper)
                    if not plane_obj:
                        return ToolResult(success=False, output="", error=f"Step {i}: could not find {plane_upper} plane")
                    sub.MirrorPlane = (plane_obj, [""])
                else:
                    parts = plane.split(".")
                    if len(parts) == 2:
                        ref_obj = _get_object(doc, parts[0])
                        if ref_obj:
                            sub.MirrorPlane = (ref_obj, [parts[1]])
                        else:
                            return ToolResult(success=False, output="", error=f"Step {i}: reference '{parts[0]}' not found")
                    else:
                        return ToolResult(success=False, output="", error=f"Step {i}: invalid plane '{plane}'")
                sub_features.append(sub)
                descriptions.append(f"mirror({plane_upper})")

            else:
                return ToolResult(
                    success=False, output="",
                    error=f"Step {i}: unknown type '{step_type}'. Use linear_pattern, polar_pattern, or mirror",
                )

        multi.Transformations = sub_features
        body.Tip = multi

        # Ensure visibility: sub-features should be hidden, multi should be visible
        for sub in sub_features:
            sub.Visibility = False
        multi.Visibility = True

        feat_list = ", ".join(f"'{n}'" for n in names)
        return ToolResult(
            success=True,
            output=f"Created MultiTransform on {feat_list} with {len(sub_features)} step(s): {', '.join(descriptions)}",
            data={"name": multi.Name, "label": multi.Label, "steps": len(sub_features)},
        )

    return _with_undo("Multi Transform", do)


MULTI_TRANSFORM = ToolDefinition(
    name="multi_transform",
    description=(
        "Chain multiple transformation steps (linear pattern, polar pattern, mirror) into a single "
        "PartDesign::MultiTransform feature. Accepts one or more features — pass related features "
        "(e.g. a post and its screw hole) together so they are transformed as a group. "
        "Cleaner than stacking separate pattern/mirror features "
        "and avoids 'transformation of a transformation' errors. All features must be additive or "
        "subtractive features inside the same Body."
    ),
    category="modeling",
    parameters=[
        ToolParam("feature_names", "array",
                  "Feature(s) to transform. Order matters: the last feature should be the most "
                  "recent in the model tree (tip). Pass multiple related features to transform them "
                  "as a group (e.g. a boss and its pocket).",
                  items={"type": "string"}),
        ToolParam("transformations", "array",
                  "List of transformation steps. Each is an object with 'type' (linear_pattern, polar_pattern, mirror) "
                  "plus type-specific params. linear_pattern: direction (X/Y/Z), length, occurrences. "
                  "polar_pattern: axis (X/Y/Z), angle, occurrences. mirror: plane (XY/XZ/YZ).",
                  items={
                      "type": "object",
                      "properties": {
                          "type": {"type": "string", "enum": ["linear_pattern", "polar_pattern", "mirror"]},
                          "direction": {"type": "string"},
                          "length": {"type": "number"},
                          "occurrences": {"type": "integer"},
                          "axis": {"type": "string"},
                          "angle": {"type": "number"},
                          "plane": {"type": "string"},
                      },
                      "required": ["type"],
                  }),
        ToolParam("label", "string", "Display label for the MultiTransform", required=False, default=""),
    ],
    handler=_handle_multi_transform,
)


# ── Interactive selection ─────────────────────────────────────

def _handle_select_geometry(prompt="Select geometry", select_type="any", max_count=0):
    """Open an interactive selection panel and wait for user picks."""
    from freecad_ai.ui.selection_panel import SelectionPanel

    panel = SelectionPanel(prompt=prompt, select_type=select_type, max_count=max_count)
    selections = panel.exec()

    if not selections:
        return ToolResult(
            success=True,
            output="User cancelled selection or selected nothing.",
            data={"selections": []},
        )

    lines = [
        f"- {s['object']}.{s['sub_element']} at "
        f"({s['point'][0]:.2f}, {s['point'][1]:.2f}, {s['point'][2]:.2f})"
        for s in selections
    ]
    return ToolResult(
        success=True,
        output="Selected:\n" + "\n".join(lines),
        data={"selections": selections},
    )


SELECT_GEOMETRY = ToolDefinition(
    name="select_geometry",
    description=(
        "Ask the user to select geometry (edges, faces, vertices) in the 3D viewport. "
        "Opens an interactive selection panel and waits for the user to click on "
        "geometry and press Done."
    ),
    category="interactive",
    parameters=[
        ToolParam("prompt", "string",
                  "Instruction shown to the user, e.g. 'Select edges to fillet'",
                  required=False, default="Select geometry"),
        ToolParam("select_type", "string",
                  "Type of geometry to accept",
                  required=False, default="any",
                  enum=["any", "edge", "face", "vertex"]),
        ToolParam("max_count", "integer",
                  "Max selections (0=unlimited)",
                  required=False, default=0),
    ],
    handler=_handle_select_geometry,
)


# ── capture_viewport ───────────────────────────────────────

def _handle_capture_viewport(
    filepath: str,
    width: int = 800,
    height: int = 600,
    background: str = "Current",
) -> ToolResult:
    """Save a screenshot of the 3D viewport to a file."""
    from ..utils.viewport import capture_viewport_image

    img_bytes = capture_viewport_image(width, height, background)
    if img_bytes is None:
        return ToolResult(success=False, output="", error="No active document or view")

    try:
        with open(filepath, "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Failed to write file: {e}")

    return ToolResult(
        success=True,
        output=f"Screenshot saved to {filepath} ({width}x{height}, background={background})",
        data={"filepath": filepath, "width": width, "height": height},
    )


CAPTURE_VIEWPORT = ToolDefinition(
    name="capture_viewport",
    description="Save a screenshot of the 3D viewport to a file.",
    category="view",
    parameters=[
        ToolParam("filepath", "string", "Output file path (e.g. /tmp/screenshot.png)"),
        ToolParam("width", "integer", "Image width in pixels",
                  required=False, default=800),
        ToolParam("height", "integer", "Image height in pixels",
                  required=False, default=600),
        ToolParam("background", "string",
                  "Background color for the screenshot",
                  required=False, default="Current",
                  enum=["Current", "White", "Black", "Transparent"]),
    ],
    handler=_handle_capture_viewport,
)


# ── set_view ───────────────────────────────────────────────

def _handle_set_view(
    orientation: str,
    fit_all: bool = True,
    projection: str = "",
) -> ToolResult:
    """Set the camera to a standard view orientation."""
    import FreeCADGui as Gui

    if not Gui.ActiveDocument:
        return ToolResult(success=False, output="", error="No active document")

    view = Gui.ActiveDocument.ActiveView

    view_methods = {
        "isometric": "viewIsometric",
        "front": "viewFront",
        "back": "viewRear",
        "top": "viewTop",
        "bottom": "viewBottom",
        "left": "viewLeft",
        "right": "viewRight",
    }
    method_name = view_methods.get(orientation.lower())
    if not method_name:
        return ToolResult(
            success=False, output="",
            error=f"Unknown orientation: {orientation}. "
                  f"Use: {', '.join(view_methods.keys())}"
        )

    try:
        getattr(view, method_name)()

        if fit_all:
            Gui.SendMsgToActiveView("ViewFit")

        if projection:
            view.setCameraType(projection)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Set view failed: {e}")

    parts = [f"Set view to {orientation}"]
    if fit_all:
        parts.append("fit all")
    if projection:
        parts.append(f"projection={projection}")
    return ToolResult(
        success=True,
        output=", ".join(parts),
        data={"orientation": orientation, "fit_all": fit_all, "projection": projection},
    )


SET_VIEW = ToolDefinition(
    name="set_view",
    description=(
        "Set the camera to a standard view orientation (front, top, isometric, etc.) "
        "and optionally adjust zoom and projection mode."
    ),
    category="view",
    parameters=[
        ToolParam("orientation", "string", "Camera orientation",
                  enum=["isometric", "front", "back", "top", "bottom", "left", "right"]),
        ToolParam("fit_all", "boolean", "Zoom to fit all objects in view",
                  required=False, default=True),
        ToolParam("projection", "string", "Projection mode",
                  required=False, default="",
                  enum=["Orthographic", "Perspective"]),
    ],
    handler=_handle_set_view,
)


# ── zoom_object ────────────────────────────────────────────

def _handle_zoom_object(object_name: str) -> ToolResult:
    """Zoom the viewport to focus on a specific object."""
    import FreeCAD as App
    import FreeCADGui as Gui

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        return ToolResult(
            success=False, output="",
            error=f"Object '{object_name}' not found"
        )

    try:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)
        Gui.SendMsgToActiveView("ViewSelection")
        Gui.Selection.clearSelection()
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Zoom failed: {e}")

    return ToolResult(
        success=True,
        output=f"Zoomed to object '{obj.Label}'",
        data={"object_name": obj.Name, "label": obj.Label},
    )


ZOOM_OBJECT = ToolDefinition(
    name="zoom_object",
    description="Zoom the viewport to focus on a specific object.",
    category="view",
    parameters=[
        ToolParam("object_name", "string", "Name or label of the object to zoom to"),
    ],
    handler=_handle_zoom_object,
)


# ── Helpers ─────────────────────────────────────────────────

def _get_object(doc, name_or_label):
    """Find a document object by internal Name first, then by Label.

    FreeCAD may assign different internal Names than requested (e.g., "Body"
    instead of "EnclosureBase"), so we fall back to Label matching.
    """
    obj = doc.getObject(name_or_label)
    if obj:
        return obj
    # Fallback: search by Label
    for o in doc.Objects:
        if o.Label == name_or_label:
            return o
    return None


def _find_body_for(doc, obj):
    """Find the PartDesign body containing an object, if any."""
    target_name = obj.Name
    for o in doc.Objects:
        if hasattr(o, "TypeId") and o.TypeId == "PartDesign::Body":
            if hasattr(o, "Group"):
                for member in o.Group:
                    if member.Name == target_name:
                        return o
    return None


# ── All tools ───────────────────────────────────────────────

ALL_TOOLS = [
    CREATE_PRIMITIVE,
    CREATE_BODY,
    CREATE_SKETCH,
    PAD_SKETCH,
    POCKET_SKETCH,
    REVOLVE_SKETCH,
    LOFT_SKETCHES,
    SWEEP_SKETCH,
    BOOLEAN_OPERATION,
    TRANSFORM_OBJECT,
    FILLET_EDGES,
    CHAMFER_EDGES,
    CREATE_INNER_RIDGE,
    CREATE_SNAP_TABS,
    CREATE_ENCLOSURE_LID,
    CREATE_WEDGE,
    SCALE_OBJECT,
    SECTION_OBJECT,
    LINEAR_PATTERN,
    POLAR_PATTERN,
    SHELL_OBJECT,
    MIRROR_FEATURE,
    MULTI_TRANSFORM,
    MEASURE,
    GET_DOCUMENT_STATE,
    MODIFY_PROPERTY,
    EXPORT_MODEL,
    EXECUTE_CODE,
    UNDO,
    CAPTURE_VIEWPORT,
    SET_VIEW,
    ZOOM_OBJECT,
    SELECT_GEOMETRY,
]
