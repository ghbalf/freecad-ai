"""FreeCAD tool handlers.

Each tool wraps a FreeCAD operation in an undo transaction with error handling.
Tools are designed to be called by the LLM via structured tool calling.
"""

from .registry import ToolParam, ToolDefinition, ToolResult


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


# ── create_primitive ────────────────────────────────────────

def _handle_create_primitive(
    shape_type: str,
    label: str = "",
    length: float = 10.0,
    width: float = 10.0,
    height: float = 10.0,
    radius: float = 5.0,
    radius2: float = 2.0,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> ToolResult:
    """Create a Part primitive (Box, Cylinder, Sphere, Cone, Torus)."""
    import FreeCAD as App

    def do(doc):
        type_map = {
            "box": "Part::Box",
            "cylinder": "Part::Cylinder",
            "sphere": "Part::Sphere",
            "cone": "Part::Cone",
            "torus": "Part::Torus",
        }
        part_type = type_map.get(shape_type.lower())
        if not part_type:
            return ToolResult(
                success=False, output="",
                error=f"Unknown shape type: {shape_type}. Use: {list(type_map.keys())}"
            )

        name = label or shape_type.capitalize()
        obj = doc.addObject(part_type, name)
        obj.Label = name

        st = shape_type.lower()
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
            output=f"Created {shape_type} '{obj.Label}' ({obj.Name})",
            data={"name": obj.Name, "label": obj.Label, "type": part_type},
        )

    return _with_undo(f"Create {shape_type}", do)


CREATE_PRIMITIVE = ToolDefinition(
    name="create_primitive",
    description="Create a 3D primitive shape (Box, Cylinder, Sphere, Cone, Torus) in the active document.",
    category="modeling",
    parameters=[
        ToolParam("shape_type", "string", "Type of primitive to create",
                  enum=["box", "cylinder", "sphere", "cone", "torus"]),
        ToolParam("label", "string", "Display label for the object", required=False, default=""),
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
) -> ToolResult:
    """Create a sketch with geometry and constraints."""
    import FreeCAD as App
    import Part
    import Sketcher

    def do(doc):
        body = None
        if body_name:
            body = doc.getObject(body_name)
            if not body:
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found")

        if body:
            sketch = body.newObject("Sketcher::SketchObject", label or "Sketch")
        else:
            sketch = doc.addObject("Sketcher::SketchObject", label or "Sketch")

        # Attach to plane
        plane_map = {
            "XY": 3, "XZ": 4, "YZ": 5,
        }
        if body and plane.upper() in plane_map:
            idx = plane_map[plane.upper()]
            sketch.AttachmentSupport = [(body.Origin.OriginFeatures[idx], "")]
            sketch.MapMode = "FlatFace"

        doc.recompute()

        geo_count = 0
        if geometries:
            for geo in geometries:
                geo_type = geo.get("type", "")
                if geo_type == "line":
                    p1 = App.Vector(geo.get("x1", 0), geo.get("y1", 0), 0)
                    p2 = App.Vector(geo.get("x2", 0), geo.get("y2", 0), 0)
                    sketch.addGeometry(Part.LineSegment(p1, p2))
                    geo_count += 1
                elif geo_type == "circle":
                    cx, cy = geo.get("cx", 0), geo.get("cy", 0)
                    r = geo.get("radius", 10)
                    sketch.addGeometry(Part.Circle(
                        App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r))
                    geo_count += 1
                elif geo_type == "arc":
                    cx, cy = geo.get("cx", 0), geo.get("cy", 0)
                    r = geo.get("radius", 10)
                    start_angle = geo.get("start_angle", 0)
                    end_angle = geo.get("end_angle", 3.14159)
                    sketch.addGeometry(Part.ArcOfCircle(
                        Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r),
                        start_angle, end_angle))
                    geo_count += 1
                elif geo_type == "rectangle":
                    # Accept both (x1,y1,x2,y2) and (x,y,width,height) formats
                    if "width" in geo and "height" in geo:
                        x1 = geo.get("x", 0)
                        y1 = geo.get("y", 0)
                        x2 = x1 + geo["width"]
                        y2 = y1 + geo["height"]
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
                args = [con["type"]]
                for key in ("first", "first_pos", "second", "second_pos", "value"):
                    if key in con:
                        args.append(con[key])
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
        ToolParam("geometries", "array", "List of geometry objects. Each has 'type' (line/circle/arc/rectangle) plus type-specific coords.",
                  required=False, items={"type": "object"}),
        ToolParam("constraints", "array", "List of Sketcher constraints. Each has 'type' plus constraint-specific params.",
                  required=False, items={"type": "object"}),
        ToolParam("label", "string", "Display label for the sketch", required=False, default=""),
    ],
    handler=_handle_create_sketch,
)


# ── pad_sketch ──────────────────────────────────────────────

def _handle_pad_sketch(
    sketch_name: str,
    length: float = 10.0,
    symmetric: bool = False,
    label: str = "",
) -> ToolResult:
    """Pad (extrude) a sketch."""

    def do(doc):
        sketch = doc.getObject(sketch_name)
        if not sketch:
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found")

        # Find the body containing this sketch
        body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        pad = body.newObject("PartDesign::Pad", label or "Pad")
        pad.Profile = sketch
        pad.Length = length
        if symmetric:
            pad.Symmetric = True

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
    ],
    handler=_handle_pad_sketch,
)


# ── pocket_sketch ───────────────────────────────────────────

def _handle_pocket_sketch(
    sketch_name: str,
    length: float = 10.0,
    through_all: bool = False,
    label: str = "",
) -> ToolResult:
    """Create a pocket (cut) from a sketch."""

    def do(doc):
        sketch = doc.getObject(sketch_name)
        if not sketch:
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found")

        body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        pocket = body.newObject("PartDesign::Pocket", label or "Pocket")
        pocket.Profile = sketch
        if through_all:
            pocket.Type = 1  # Through All
        else:
            pocket.Length = length

        return ToolResult(
            success=True,
            output=f"Created pocket from sketch '{sketch_name}'",
            data={"name": pocket.Name, "label": pocket.Label},
        )

    return _with_undo("Pocket Sketch", do)


POCKET_SKETCH = ToolDefinition(
    name="pocket_sketch",
    description="Create a pocket (cut) from a sketch into the body's solid.",
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to pocket"),
        ToolParam("length", "number", "Pocket depth in mm", required=False, default=10.0),
        ToolParam("through_all", "boolean", "Cut through the entire body", required=False, default=False),
        ToolParam("label", "string", "Display label for the pocket feature", required=False, default=""),
    ],
    handler=_handle_pocket_sketch,
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
        obj1 = doc.getObject(object1)
        obj2 = doc.getObject(object2)
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
        obj = doc.getObject(object_name)
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
        obj = doc.getObject(object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        edge_refs = edges or ["Edge1"]

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
        obj = doc.getObject(object_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found")

        edge_refs = edges or ["Edge1"]

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
        obj1 = doc.getObject(target)
        obj2 = doc.getObject(target2)
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

    obj = doc.getObject(target) if target else None
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
        obj = doc.getObject(object_name)
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
        objs = [doc.getObject(n) for n in objects if doc.getObject(n)]
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


# ── Helpers ─────────────────────────────────────────────────

def _find_body_for(doc, obj):
    """Find the PartDesign body containing an object, if any."""
    for o in doc.Objects:
        if hasattr(o, "TypeId") and o.TypeId == "PartDesign::Body":
            if hasattr(o, "Group") and obj in o.Group:
                return o
    return None


# ── All tools ───────────────────────────────────────────────

ALL_TOOLS = [
    CREATE_PRIMITIVE,
    CREATE_BODY,
    CREATE_SKETCH,
    PAD_SKETCH,
    POCKET_SKETCH,
    BOOLEAN_OPERATION,
    TRANSFORM_OBJECT,
    FILLET_EDGES,
    CHAMFER_EDGES,
    MEASURE,
    GET_DOCUMENT_STATE,
    MODIFY_PROPERTY,
    EXPORT_MODEL,
    EXECUTE_CODE,
    UNDO,
]
