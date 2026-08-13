"""FreeCAD tool handlers.

Each tool wraps a FreeCAD operation in an undo transaction with error handling.
Tools are designed to be called by the LLM via structured tool calling.
"""

import os

from .registry import ToolParam, ToolDefinition, ToolResult
from ..core.executor import execute_code
from ..branding import APP_NAME


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
    from ..core.active_document import get_synced_active_document
    doc = get_synced_active_document()
    if not doc:
        return ToolResult(
            success=False,
            output="",
            error=f"No active document — open a document in {APP_NAME} or select its tab.",
        )
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
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(
                    success=False, output="",
                    error=f"Body '{body_name}' not found.{hint}"
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
            output=(f"Created PartDesign body '{body.Name}' (label: '{body.Label}')."
                    f" Use body_name='{body.Name}' in subsequent tool calls."),
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


def _resolve_sketch_attachment(support, face, plane, body_present,
                               support_kind, face_exists, face_planar, in_body):
    """Decide where a sketch attaches. Pure — no FreeCAD calls.

    Inputs are already-inspected facts so this is unit-testable. When the
    GUI-selection fallback is used, the caller passes the selected object as
    ``support`` and its planar face as ``face`` — selection collapses into the
    same inputs as the explicit params.

    ``support_kind``: ``""`` (no support given and no usable selection),
    else ``"missing"`` | ``"plane"`` | ``"solid"`` | ``"other"``.
    ``face_exists`` / ``face_planar`` are meaningful only when ``face`` != "".
    ``in_body`` is the Body name owning the support, or ``None``.

    Returns one of:
      {"mode": "face", "support": str, "sub": str, "in_body": str|None}
      {"mode": "plane", "support": str, "in_body": str|None}
      {"mode": "origin", "plane": str}
      {"mode": "standalone"}
      {"mode": "error", "message": str}
    """
    support = support or ""
    face = face or ""

    if face and not support:
        return {"mode": "error", "message": "`face` requires `support`."}

    if support_kind == "missing":
        return {"mode": "error", "message": f"Object '{support}' not found."}

    if support_kind in ("plane", "solid", "other"):
        if face:
            if not face_exists:
                return {"mode": "error",
                        "message": f"Face '{face}' not found on '{support}'."}
            if not face_planar:
                return {"mode": "error",
                        "message": (f"Face '{face}' on '{support}' is not planar; "
                                    "sketches need a planar face.")}
            return {"mode": "face", "support": support, "sub": face,
                    "in_body": in_body}
        if support_kind == "plane":
            return {"mode": "plane", "support": support, "in_body": in_body}
        if support_kind == "solid":
            return {"mode": "error",
                    "message": (f"`support` '{support}' is a solid; specify a `face` "
                                "(e.g. 'Face6'), or pass a datum/origin plane as "
                                "`support`.")}
        # support_kind == "other" (e.g. a mesh-derived feature, a wire)
        return {"mode": "error",
                "message": (f"`support` '{support}' can't host a sketch on its own; "
                            "specify a planar `face`, or pass a datum/origin plane "
                            "as `support`.")}

    if support_kind:
        # Unknown classification — fail loudly rather than silently falling
        # through to standalone.
        return {"mode": "error",
                "message": f"Unsupported support kind '{support_kind}' for '{support}'."}

    # No support / no usable selection — original behavior.
    if body_present and plane.upper() in ("XY", "XZ", "YZ"):
        return {"mode": "origin", "plane": plane.upper()}
    return {"mode": "standalone"}


def _resolve_datum_plane_attachment(support, face, plane, body_present,
                                    support_kind, face_exists, face_planar, in_body):
    """Reference resolution for create_datum_plane. Pure — no FreeCAD calls.

    Delegates to ``_resolve_sketch_attachment`` and remaps the one datum-specific
    decision: a datum plane cannot be free-floating, so a ``standalone`` result
    (no usable reference) becomes an error. All other modes pass through.
    """
    spec = _resolve_sketch_attachment(
        support, face, plane, body_present,
        support_kind, face_exists, face_planar, in_body)
    if spec["mode"] == "standalone":
        return {"mode": "error",
                "message": ("create_datum_plane needs a reference: pass a plane "
                            "(XY/XZ/YZ) with body_name, or a support object "
                            "(optionally with a face).")}
    return spec


_DATUM_LINE_MODE_ERR = (
    "Specify exactly one of: two points (point1+point2), an edge "
    "(support+edge), or an origin axis (axis).")


def _resolve_datum_line_def(point1, point2, support, edge, axis,
                            body_present, support_kind, edge_exists,
                            edge_straight, in_body):
    """Decide how a datum line is defined. Pure — no FreeCAD calls.

    Exactly one mode's inputs must be supplied. ``support_kind`` is ``""`` |
    ``"missing"`` | ``"plane"`` | ``"solid"`` | ``"other"`` (only ``"missing"``
    changes the outcome — any found object can host an edge). ``edge_exists`` /
    ``edge_straight`` matter only when ``edge`` != "". ``in_body`` is the
    support's owning Body name or ``None``.

    Returns one of:
      {"mode": "points", "p1": [x,y,z], "p2": [x,y,z]}
      {"mode": "edge", "support": str, "sub": str, "in_body": str|None}
      {"mode": "origin", "axis": "X"|"Y"|"Z"}
      {"mode": "error", "message": str}
    """
    point1 = list(point1) if point1 else []
    point2 = list(point2) if point2 else []
    support = support or ""
    edge = edge or ""
    axis = (axis or "").upper()

    has_points = bool(point1) or bool(point2)
    has_edge = bool(support) or bool(edge)
    has_axis = bool(axis)

    if sum([has_points, has_edge, has_axis]) != 1:
        return {"mode": "error", "message": _DATUM_LINE_MODE_ERR}

    if has_points:
        if len(point1) != 3 or len(point2) != 3:
            return {"mode": "error",
                    "message": "Two-points mode needs point1 and point2 as "
                               "[x, y, z]."}
        d2 = sum((a - b) ** 2 for a, b in zip(point1, point2))
        if d2 < 1e-14:
            return {"mode": "error",
                    "message": "point1 and point2 are coincident; a line needs "
                               "two distinct points."}
        return {"mode": "points", "p1": point1, "p2": point2}

    if has_edge:
        if not support:
            return {"mode": "error", "message": "`edge` requires `support`."}
        if not edge:
            return {"mode": "error",
                    "message": "`support` requires an `edge` (e.g. 'Edge3')."}
        if support_kind == "missing":
            return {"mode": "error", "message": f"Object '{support}' not found."}
        if not edge_exists:
            return {"mode": "error",
                    "message": f"Edge '{edge}' not found on '{support}'."}
        if not edge_straight:
            return {"mode": "error",
                    "message": (f"Edge '{edge}' on '{support}' is not straight; "
                                "a datum line needs a straight edge.")}
        return {"mode": "edge", "support": support, "sub": edge,
                "in_body": in_body}

    # has_axis
    if axis not in ("X", "Y", "Z"):
        return {"mode": "error", "message": "axis must be X, Y, or Z."}
    if not body_present:
        return {"mode": "error",
                "message": "axis mode needs body_name (origin axes belong to a "
                           "Body)."}
    return {"mode": "origin", "axis": axis}


_PLANE_TYPE_IDS = ("App::Plane", "PartDesign::Plane", "Part::DatumPlane")


def _classify_support(obj):
    """Classify a support object for sketch attachment.

    Returns 'plane' for origin/datum planes, 'solid' for shapes with solids,
    'other' for everything else (sketches, wires, empty features).
    """
    if getattr(obj, "TypeId", "") in _PLANE_TYPE_IDS:
        return "plane"
    shape = getattr(obj, "Shape", None)
    try:
        if shape is not None and shape.Solids:
            return "solid"
    except Exception:
        pass
    return "other"


def _owning_body_name(obj, objects):
    """Return the Name of the PartDesign::Body that contains ``obj``, or None.

    ``objects`` is an iterable of document objects (e.g. ``doc.Objects``). A Body lists
    its children in ``.Group``.

    See also ``_find_body_for``, which returns the Body object itself.
    """
    for cand in objects:
        if getattr(cand, "TypeId", "") != "PartDesign::Body":
            continue
        try:
            if any(getattr(c, "Name", None) == obj.Name for c in cand.Group):
                return cand.Name
        except Exception:
            pass
    return None


def _inspect_face(obj, face_name):
    """Return (exists, planar) for ``face_name`` on ``obj``'s shape.

    Never raises — a missing face, unavailable Part module, or non-shape object
    yields (False, False).
    """
    try:
        import Part
    except Exception:
        return (False, False)
    try:
        face = obj.Shape.getElement(face_name)
    except Exception:
        return (False, False)
    if face is None or face.ShapeType != "Face":
        return (False, False)
    try:
        return (True, isinstance(face.Surface, Part.Plane))
    except Exception:
        return (True, False)


def _inspect_edge(obj, edge_name):
    """Return (exists, straight) for ``edge_name`` on ``obj``'s shape.

    Never raises — a missing edge, unavailable Part module, or non-shape object
    yields (False, False). "straight" means the edge's curve is a ``Part.Line``.
    """
    try:
        import Part
    except Exception:
        return (False, False)
    try:
        edge = obj.Shape.getElement(edge_name)
    except Exception:
        return (False, False)
    if edge is None or edge.ShapeType != "Edge":
        return (False, False)
    try:
        return (True, isinstance(edge.Curve, Part.Line))
    except Exception:
        return (True, False)


def _read_planar_selection():
    """Return (object_name, sub_element) for the first usable planar-face or
    plane selection in the GUI, or None.

    Used as a fallback when create_sketch is called with no support/face. Any
    error or non-usable selection (edge, vertex, nothing, non-planar face)
    returns None so the caller falls through to default behavior.
    """
    try:
        import FreeCADGui as Gui
        import Part
    except Exception:
        return None
    try:
        sel = Gui.Selection.getSelectionEx()
    except Exception:
        return None
    for s in sel or []:
        obj = getattr(s, "Object", None)
        if obj is None:
            continue
        subs = getattr(s, "SubElementNames", None) or []
        if subs:
            for sub in subs:
                try:
                    el = obj.Shape.getElement(sub)
                    if (el is not None and el.ShapeType == "Face"
                            and isinstance(el.Surface, Part.Plane)):
                        return (obj.Name, sub)
                except Exception:
                    continue
        elif getattr(obj, "TypeId", "") in _PLANE_TYPE_IDS:
            return (obj.Name, "")
    return None


# ── create_sketch ───────────────────────────────────────────

def _handle_create_sketch(
    plane: str = "XY",
    body_name: str = "",
    geometries: list | None = None,
    constraints: list | None = None,
    label: str = "",
    offset: float = 0.0,
    support: str = "",
    face: str = "",
) -> ToolResult:
    """Create a sketch with geometry and constraints."""
    import FreeCAD as App
    import Part
    import Sketcher

    def do(doc):
        warnings = []

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        # Gather attachment facts from explicit params, or fall back to the GUI
        # selection when neither support nor face was given.
        sup, fc = support, face
        # Only consult the viewport selection for a bare call. An explicit
        # plane, body_name, support, or face signals intent and must not be
        # silently overridden by whatever happens to be selected (preserves
        # backward compatibility for create_sketch(plane=..., body_name=...)).
        if (not support and not face and not body_name
                and plane.upper() == "XY"):
            picked = _read_planar_selection()
            if picked:
                sup, fc = picked

        sup_kind = ""
        face_exists = face_planar = False
        in_body = None
        sup_obj = None
        if sup:
            sup_obj = _get_object(doc, sup)
            if sup_obj is None:
                sup_kind = "missing"
            else:
                sup = sup_obj.Name
                sup_kind = _classify_support(sup_obj)
                in_body = _owning_body_name(sup_obj, doc.Objects)
                if fc:
                    face_exists, face_planar = _inspect_face(sup_obj, fc)

        spec = _resolve_sketch_attachment(
            sup, fc, plane, body is not None,
            sup_kind, face_exists, face_planar, in_body,
        )
        if spec["mode"] == "error":
            hint = _suggest_similar(doc, support) if sup_kind == "missing" else ""
            return ToolResult(success=False, output="", error=spec["message"] + hint)

        # Choose the container the sketch is created in.
        if spec["mode"] in ("face", "plane"):
            if body_name:
                warnings.append("body_name ignored — sketch placed relative to support.")
            if spec.get("in_body"):
                container = _get_object(doc, spec["in_body"])
            elif sup_obj is not None and getattr(sup_obj, "TypeId", "") == "PartDesign::Body":
                # Sketching on a Body's own face → keep the sketch inside it.
                container = sup_obj
            else:
                container = None
        elif spec["mode"] == "origin":
            container = body
        else:  # standalone
            container = body

        if container is not None:
            sketch = container.newObject("Sketcher::SketchObject", label or "Sketch")
        else:
            sketch = doc.addObject("Sketcher::SketchObject", label or "Sketch")

        # Apply the attachment. sup_obj is the resolved support object (non-None
        # whenever the resolver returned a face/plane mode).
        if spec["mode"] == "face":
            sketch.AttachmentSupport = [(sup_obj, spec["sub"])]
            sketch.MapMode = "FlatFace"
        elif spec["mode"] == "plane":
            sketch.AttachmentSupport = [(sup_obj, "")]
            sketch.MapMode = "FlatFace"
        elif spec["mode"] == "origin":
            plane_feat = _get_body_plane(container, spec["plane"])
            if plane_feat:
                sketch.AttachmentSupport = [(plane_feat, "")]
                sketch.MapMode = "FlatFace"

        # Offset along the attachment normal. For face/plane attachments the
        # sketch's local Z is the face/plane normal, so offset is (0,0,offset).
        # For origin planes keep the explicit per-plane vector.
        if offset != 0:
            if spec["mode"] in ("face", "plane"):
                sketch.AttachmentOffset = App.Placement(
                    App.Vector(0, 0, offset), App.Rotation())
            else:
                offset_map = {
                    "XY": App.Vector(0, 0, offset),
                    "XZ": App.Vector(0, offset, 0),
                    "YZ": App.Vector(offset, 0, 0),
                }
                ovec = offset_map.get(plane.upper(), App.Vector(0, 0, offset))
                sketch.AttachmentOffset = App.Placement(ovec, App.Rotation())

        doc.recompute()

        # If a face/plane attachment can't resolve, FreeCAD marks the sketch's
        # State as Invalid/Error after recompute. (The executor sandbox is the
        # higher-level net — it also captures the C++ PositionBySupport error.)
        if spec["mode"] in ("face", "plane"):
            state = list(getattr(sketch, "State", []) or [])
            if any(s in ("Invalid", "Error") for s in state):
                return ToolResult(
                    success=False, output="",
                    error=(f"Failed to attach sketch to '{spec['support']}'"
                           + (f":{spec['sub']}" if spec.get("sub") else "")
                           + " — attachment did not resolve."))

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
                    # Dimensions can be numbers or expression strings (e.g. "Variables.length")
                    rect_w = geo.get("width", None)
                    rect_h = geo.get("height", None) or geo.get("length", None)
                    w_expr = None
                    h_expr = None
                    if rect_w is not None and rect_h is not None:
                        # If dimensions are expression strings, use a placeholder
                        if isinstance(rect_w, str):
                            w_expr = rect_w
                            rect_w = 10  # placeholder, expression overrides
                        if isinstance(rect_h, str):
                            h_expr = rect_h
                            rect_h = 10
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
                    # Add dimension constraints (width = DistanceX, height = DistanceY)
                    # These make the rectangle fully constrained and bindable
                    w_ci = sketch.addConstraint(
                        Sketcher.Constraint("DistanceX", g, 1, g, 2, rect_w))
                    h_ci = sketch.addConstraint(
                        Sketcher.Constraint("DistanceY", g+1, 1, g+1, 2, rect_h))
                    # Bind to expressions if provided
                    if w_expr:
                        sketch.setExpression(f"Constraints[{w_ci}]", w_expr)
                    if h_expr:
                        sketch.setExpression(f"Constraints[{h_ci}]", h_expr)
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

                # Remove auto-generated constraint that matches this one
                # so the explicit constraint overwrites it.
                first = con.get("first", -2000)
                first_pos = con.get("first_pos", 0)
                second = con.get("second", -2000)
                second_pos = con.get("second_pos", 0)
                for ci in range(sketch.ConstraintCount - 1, -1, -1):
                    ec = sketch.Constraints[ci]
                    if (ec.Type == con_type
                            and ec.First == first
                            and ec.Second == second):
                        sketch.delConstraint(ci)
                        break

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

        # Report all constraints — dimension ones marked as bindable
        constraint_count = sketch.ConstraintCount
        constraint_status = ""
        _DIMENSION_TYPES = {
            "Distance", "DistanceX", "DistanceY", "Radius", "Angle",
        }
        constraint_details = []
        try:
            for ci in range(constraint_count):
                c = sketch.Constraints[ci]
                if c.Type in _DIMENSION_TYPES:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type} = {c.Value}  ← bindable"
                    )
                else:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type}"
                    )
        except Exception:
            pass

        try:
            if sketch.FullyConstrained:
                constraint_status = " Fully constrained."
            else:
                dof = sketch.solve()
                if dof > 0:
                    constraint_status = (
                        f" Under-constrained ({dof} DOF remaining)"
                        " — add constraints to fully define the sketch."
                    )
                elif dof < 0:
                    constraint_status = (
                        " Over-constrained — remove redundant constraints."
                    )
                else:
                    constraint_status = " Fully constrained."
        except Exception:
            pass

        constraint_info = ""
        if constraint_details:
            constraint_info = (
                "\nConstraints:\n"
                + "\n".join(f"  {d}" for d in constraint_details)
            )

        return ToolResult(
            success=True,
            output=(
                ("⚠ " + " ".join(warnings) + "\n" if warnings else "")
                + f"Created sketch '{sketch.Name}' with {geo_count} geometries"
                f" and {constraint_count} constraints.{constraint_status}"
                f"{constraint_info}"
                f"\nUse sketch_name='{sketch.Name}' in pad_sketch/pocket_sketch."),
            data={"name": sketch.Name, "label": sketch.Label,
                  "geometry_count": geo_count, "constraint_count": constraint_count,
                  "constraints": constraint_details,
                  "fully_constrained": getattr(sketch, "FullyConstrained", None)},
        )

    return _with_undo("Create Sketch", do)


CREATE_SKETCH = ToolDefinition(
    name="create_sketch",
    description=(
        "Create a 2D sketch with geometry (lines, circles, arcs, rectangles) and constraints. "
        "This is ALSO the correct tool to attach a sketch to a SELECTED or named planar "
        "face of an existing solid (pass support='Obj', face='Face6' — get the name from "
        "list_faces) or to a datum plane (support='PlaneName'); do NOT hand-write an "
        "AttachmentSupport/MapMode macro for this. Without support it attaches to the origin "
        "`plane` (XY/XZ/YZ). "
        "For PartDesign, specify body_name to add the sketch to a body. "
        "Rectangle dimensions can be expression strings for parametric models: "
        "width='Variables.length', height='Variables.width'. "
        "IMPORTANT: rectangles and circles auto-generate all necessary constraints "
        "(Coincident, Horizontal, Vertical, DistanceX, DistanceY, Radius). "
        "Do NOT add DistanceX/DistanceY/Radius in the constraints array for these — "
        "they are already constrained by the coordinates and dimensions you provide. "
        "COORDINATE SYSTEM: FreeCAD sketches use Y-up. When converting from SVG, images, "
        "or screen coordinates (which use Y-down), negate all Y values."
    ),
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
        ToolParam("support", "string",
                  "Object to attach the sketch to: a solid (with `face`) or a "
                  "datum/origin plane object (by name). Overrides `plane`. If "
                  "omitted and a planar face/plane is selected in the viewport, "
                  "that selection is used.",
                  required=False, default=""),
        ToolParam("face", "string",
                  "Planar sub-element of `support` to sketch on, e.g. 'Face6'. "
                  "Get face names from list_faces. Requires `support`. The face "
                  "must be planar.",
                  required=False, default=""),
    ],
    handler=_handle_create_sketch,
)


# ── create_datum_plane ──────────────────────────────────────

def _handle_create_datum_plane(
    plane: str = "XY",
    support: str = "",
    face: str = "",
    offset: float = 0.0,
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Create a parametric datum plane offset from a reference."""
    import FreeCAD as App

    def do(doc):
        warnings = []

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        # Bare-call selection fallback — same gate as create_sketch: only when
        # no explicit reference and the default plane were given.
        sup, fc = support, face
        if (not support and not face and not body_name
                and plane.upper() == "XY"):
            picked = _read_planar_selection()
            if picked:
                sup, fc = picked

        sup_kind = ""
        face_exists = face_planar = False
        in_body = None
        sup_obj = None
        if sup:
            sup_obj = _get_object(doc, sup)
            if sup_obj is None:
                sup_kind = "missing"
            else:
                sup = sup_obj.Name
                sup_kind = _classify_support(sup_obj)
                in_body = _owning_body_name(sup_obj, doc.Objects)
                if fc:
                    face_exists, face_planar = _inspect_face(sup_obj, fc)

        spec = _resolve_datum_plane_attachment(
            sup, fc, plane, body is not None,
            sup_kind, face_exists, face_planar, in_body,
        )
        if spec["mode"] == "error":
            hint = _suggest_similar(doc, support) if sup_kind == "missing" else ""
            return ToolResult(success=False, output="", error=spec["message"] + hint)

        # Choose the container the datum plane is created in (same logic as
        # create_sketch).
        if spec["mode"] in ("face", "plane"):
            if body_name:
                warnings.append("body_name ignored — datum plane placed relative to support.")
            if spec.get("in_body"):
                container = _get_object(doc, spec["in_body"])
            elif sup_obj is not None and getattr(sup_obj, "TypeId", "") == "PartDesign::Body":
                container = sup_obj
            else:
                container = None
        else:  # origin
            container = body

        # PartDesign::Plane works both inside a Body (newObject) and standalone
        # (addObject) — FreeCAD 1.1 has no separate standalone datum-plane type.
        if container is not None:
            datum = container.newObject("PartDesign::Plane", label or "DatumPlane")
        else:
            datum = doc.addObject("PartDesign::Plane", label or "DatumPlane")

        # Attach to the reference. All modes use FlatFace, so the datum's local
        # Z is the reference normal and the offset is always (0, 0, offset).
        if spec["mode"] == "face":
            datum.AttachmentSupport = [(sup_obj, spec["sub"])]
            datum.MapMode = "FlatFace"
        elif spec["mode"] == "plane":
            datum.AttachmentSupport = [(sup_obj, "")]
            datum.MapMode = "FlatFace"
        elif spec["mode"] == "origin":
            plane_feat = _get_body_plane(container, spec["plane"])
            if plane_feat:
                datum.AttachmentSupport = [(plane_feat, "")]
                datum.MapMode = "FlatFace"
            else:
                warnings.append(
                    f"could not resolve the {spec['plane']} origin plane — "
                    "datum left at the document origin.")

        # AttachmentOffset is in the datum's local frame, whose Z is the
        # reference normal (FlatFace) for every mode — so (0, 0, offset) is
        # always along the normal. (create_sketch maps origin-plane offsets to
        # global axes for legacy reasons; the result is equivalent.)
        if offset != 0:
            datum.AttachmentOffset = App.Placement(
                App.Vector(0, 0, offset), App.Rotation())

        doc.recompute()

        # If the attachment can't resolve, FreeCAD marks the feature's State as
        # Invalid/Error. (The executor sandbox is the higher-level net.)
        state = list(getattr(datum, "State", []) or [])
        if any(s in ("Invalid", "Error") for s in state):
            if spec.get("support"):
                ref = spec["support"]
            elif spec.get("plane"):
                ref = "origin plane " + spec["plane"]
            else:
                ref = "reference"
            return ToolResult(
                success=False, output="",
                error=(f"Failed to attach datum plane to '{ref}'"
                       + (f":{spec['sub']}" if spec.get("sub") else "")
                       + " — attachment did not resolve."))

        return ToolResult(
            success=True,
            output=(("⚠ " + " ".join(warnings) + "\n" if warnings else "")
                    + f"Created datum plane '{datum.Name}' ({datum.TypeId})."
                    + f" Use support='{datum.Name}' in create_sketch."),
            data={"name": datum.Name, "label": datum.Label,
                  "type_id": datum.TypeId},
        )

    return _with_undo("Create Datum Plane", do)


CREATE_DATUM_PLANE = ToolDefinition(
    name="create_datum_plane",
    description=(
        "Create a parametric datum plane offset (parallel) from a reference — "
        "useful as a mid-plane, an offset sketch plane, or a mirror reference. "
        "Reference options: an origin plane (plane=XY/XZ/YZ, needs body_name); a "
        "planar face of an object (support='Obj', face='Face6' — names from "
        "list_faces); or an existing plane by name (support='PlaneName'). With no "
        "reference, the current planar-face/plane selection is used. The result "
        "is a PartDesign::Plane (inside a Body, or standalone when the reference "
        "is not in a Body), "
        "and can be passed to create_sketch as support='<name>'."
    ),
    category="modeling",
    parameters=[
        ToolParam("plane", "string", "Origin-plane reference: XY, XZ, or YZ "
                  "(used when no support; needs body_name).",
                  required=False, default="XY", enum=["XY", "XZ", "YZ"]),
        ToolParam("support", "string", "Object to offset from: a solid (with "
                  "`face`) or an existing plane object by name.",
                  required=False, default=""),
        ToolParam("face", "string", "Planar sub-element of `support`, e.g. "
                  "'Face6' (from list_faces). Requires `support`.",
                  required=False, default=""),
        ToolParam("offset", "number", "Parallel offset along the reference "
                  "normal in mm (may be negative).",
                  required=False, default=0.0),
        ToolParam("body_name", "string", "Body to create the PartDesign::Plane "
                  "in (required for an origin-plane reference).",
                  required=False, default=""),
        ToolParam("label", "string", "Display label for the datum plane.",
                  required=False, default=""),
    ],
    handler=_handle_create_datum_plane,
)


def _handle_create_datum_line(
    point1: list | None = None,
    point2: list | None = None,
    support: str = "",
    edge: str = "",
    axis: str = "",
    body_name: str = "",
    label: str = "",
) -> ToolResult:
    """Create a datum line (axis) by two points, a straight edge, or an origin
    axis. No offset — use transform_object to place a parallel/duplicate line."""
    import FreeCAD as App

    def do(doc):
        warnings = []

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="",
                                  error=f"Body '{body_name}' not found.{hint}")

        # Inspect the support/edge so the pure resolver gets plain facts.
        sup = support
        sup_kind = ""
        edge_exists = edge_straight = False
        in_body = None
        sup_obj = None
        if support:
            sup_obj = _get_object(doc, support)
            if sup_obj is None:
                sup_kind = "missing"
            else:
                sup = sup_obj.Name
                sup_kind = _classify_support(sup_obj)
                in_body = _owning_body_name(sup_obj, doc.Objects)
                if edge:
                    edge_exists, edge_straight = _inspect_edge(sup_obj, edge)

        spec = _resolve_datum_line_def(
            point1, point2, sup, edge, axis, body is not None,
            sup_kind, edge_exists, edge_straight, in_body)
        if spec["mode"] == "error":
            hint = _suggest_similar(doc, support) if sup_kind == "missing" else ""
            return ToolResult(success=False, output="", error=spec["message"] + hint)

        # Choose the container the datum line is created in.
        if spec["mode"] == "edge":
            if body_name:
                warnings.append("body_name ignored — datum line placed relative "
                                "to support.")
            if spec.get("in_body"):
                container = _get_object(doc, spec["in_body"])
            elif sup_obj is not None and getattr(sup_obj, "TypeId", "") == "PartDesign::Body":
                container = sup_obj
            else:
                container = None
        else:  # origin or points — honor body_name (origin requires it)
            container = body

        # PartDesign::Line works in-Body (newObject) and standalone (addObject).
        if container is not None:
            line = container.newObject("PartDesign::Line", label or "DatumLine")
        else:
            line = doc.addObject("PartDesign::Line", label or "DatumLine")

        if spec["mode"] == "edge":
            line.AttachmentSupport = [(sup_obj, spec["sub"])]
            line.MapMode = "Tangent"
        elif spec["mode"] == "origin":
            axis_feat = _get_body_axis(container, spec["axis"])
            if axis_feat:
                line.AttachmentSupport = [(axis_feat, "")]
                line.MapMode = "Tangent"
            else:
                warnings.append(
                    f"could not resolve the {spec['axis']} origin axis — datum "
                    "line left at the document origin.")
        else:  # points — place through p1 directed toward p2
            p1 = App.Vector(*spec["p1"])
            p2 = App.Vector(*spec["p2"])
            direction = p2.sub(p1)
            # PartDesign::Line lies along its local Z; orient local Z onto the
            # p1→p2 direction (no attachment in two-points mode).
            line.Placement = App.Placement(
                p1, App.Rotation(App.Vector(0, 0, 1), direction))
            if hasattr(line, "Length"):
                line.Length = direction.Length

        doc.recompute()

        # Attachment modes: FreeCAD marks State Invalid/Error if it can't resolve.
        if spec["mode"] in ("edge", "origin"):
            state = list(getattr(line, "State", []) or [])
            if any(s in ("Invalid", "Error") for s in state):
                ref = spec.get("support") or ("origin axis " + spec.get("axis", ""))
                return ToolResult(
                    success=False, output="",
                    error=(f"Failed to attach datum line to '{ref}'"
                           + (f":{spec['sub']}" if spec.get("sub") else "")
                           + " — attachment did not resolve."))

        return ToolResult(
            success=True,
            output=(("⚠ " + " ".join(warnings) + "\n" if warnings else "")
                    + f"Created datum line '{line.Name}' ({line.TypeId})."
                    + " Use it as a revolve_sketch axis or a mirror reference."),
            data={"name": line.Name, "label": line.Label, "type_id": line.TypeId},
        )

    return _with_undo("Create Datum Line", do)


CREATE_DATUM_LINE = ToolDefinition(
    name="create_datum_line",
    description=(
        "Create a datum line (axis) — useful as a rotation/revolve axis or a "
        "mirror reference. Three mutually exclusive modes: two points "
        "(point1=[x,y,z], point2=[x,y,z]) for an axis where no geometry exists; "
        "a straight edge of an object (support='Obj', edge='Edge3' — names from "
        "list_edges); or an origin axis (axis=X/Y/Z, needs body_name). The result "
        "is a PartDesign::Line (inside a Body, or standalone when the edge is not "
        "in a Body). No offset — use transform_object to place a parallel or "
        "duplicate line."
    ),
    category="modeling",
    parameters=[
        ToolParam("point1", "array", "First 3-D point [x, y, z] (two-points "
                  "mode).", required=False, default=None,
                  items={"type": "number"}),
        ToolParam("point2", "array", "Second 3-D point [x, y, z] (two-points "
                  "mode).", required=False, default=None,
                  items={"type": "number"}),
        ToolParam("support", "string", "Object whose edge to attach to (edge "
                  "mode).", required=False, default=""),
        ToolParam("edge", "string", "Straight sub-element of `support`, e.g. "
                  "'Edge3' (from list_edges). Requires `support`.",
                  required=False, default=""),
        ToolParam("axis", "string", "Origin axis to reference: X, Y, or Z "
                  "(needs body_name).", required=False, default="",
                  enum=["X", "Y", "Z"]),
        ToolParam("body_name", "string", "Body to create the line in (required "
                  "for axis mode; optional for two-points; ignored for edge "
                  "mode).", required=False, default=""),
        ToolParam("label", "string", "Display label for the datum line.",
                  required=False, default=""),
    ],
    handler=_handle_create_datum_line,
)


# ── edit_sketch ────────────────────────────────────────────

def _handle_edit_sketch(
    sketch_name: str,
    add_geometries: list | None = None,
    remove_geometries: list | None = None,
    add_constraints: list | None = None,
    remove_constraints: list | None = None,
    clear_all: bool = False,
    label: str = "",
) -> ToolResult:
    """Add/remove geometry and constraints on an existing sketch."""
    import FreeCAD as App
    import Part
    import Sketcher

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="",
                              error=f"Sketch '{sketch_name}' not found.{hint}")

        if not hasattr(sketch, "addGeometry"):
            return ToolResult(success=False, output="",
                              error=f"'{sketch_name}' is not a sketch.")

        geo_added = 0
        geo_removed = 0
        con_added = 0
        con_removed = 0
        warnings = []

        # ── Clear all geometry and constraints (for full replacement)
        if clear_all:
            while sketch.ConstraintCount > 0:
                sketch.delConstraint(sketch.ConstraintCount - 1)
                con_removed += 1
            while sketch.GeometryCount > 0:
                sketch.delGeometry(sketch.GeometryCount - 1)
                geo_removed += 1

        # ── Remove constraints first (before geometry removal shifts indices)
        if remove_constraints:
            indices = sorted((int(i) for i in remove_constraints), reverse=True)
            for idx in indices:
                if 0 <= idx < sketch.ConstraintCount:
                    sketch.delConstraint(idx)
                    con_removed += 1

        # ── Remove geometries (highest-first)
        if remove_geometries:
            indices = sorted((int(i) for i in remove_geometries), reverse=True)
            for idx in indices:
                if 0 <= idx < sketch.GeometryCount:
                    sketch.delGeometry(idx)
                    geo_removed += 1

        # ── Add geometries
        if add_geometries:
            for geo in add_geometries:
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
                    geo_added += 1
                elif geo_type == "circle":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    sketch.addGeometry(Part.Circle(
                        App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r))
                    geo_added += 1
                elif geo_type == "arc":
                    cx = geo.get("cx", geo.get("x", 0))
                    cy = geo.get("cy", geo.get("y", 0))
                    r = geo.get("radius", 10)
                    start_angle = geo.get("start_angle", 0)
                    end_angle = geo.get("end_angle", 3.14159)
                    sketch.addGeometry(Part.ArcOfCircle(
                        Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r),
                        start_angle, end_angle))
                    geo_added += 1
                elif geo_type == "rectangle":
                    rect_w = geo.get("width", None)
                    rect_h = geo.get("height", None) or geo.get("length", None)
                    if rect_w is not None and rect_h is not None:
                        x1 = geo.get("x", 0)
                        y1 = geo.get("y", 0)
                        x2 = x1 + float(rect_w)
                        y2 = y1 + float(rect_h)
                    else:
                        x1, y1 = geo.get("x1", 0), geo.get("y1", 0)
                        x2, y2 = geo.get("x2", 10), geo.get("y2", 10)
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
                    sketch.addConstraint(
                        Sketcher.Constraint("DistanceX", g, 1, g, 2, float(x2 - x1)))
                    sketch.addConstraint(
                        Sketcher.Constraint("DistanceY", g+1, 1, g+1, 2, float(y2 - y1)))
                    geo_added += 4
                elif geo_type == "polygon":
                    points = geo.get("points", [])
                    if len(points) >= 2:
                        for i in range(len(points)):
                            p1 = App.Vector(points[i][0], points[i][1], 0)
                            p2 = App.Vector(points[(i + 1) % len(points)][0],
                                            points[(i + 1) % len(points)][1], 0)
                            sketch.addGeometry(Part.LineSegment(p1, p2))
                            geo_added += 1
                        n = len(points)
                        base = sketch.GeometryCount - n
                        for i in range(n):
                            sketch.addConstraint(Sketcher.Constraint(
                                "Coincident", base + i, 2, base + (i + 1) % n, 1))

        # ── Add constraints
        if add_constraints:
            for con in add_constraints:
                if isinstance(con, str):
                    try:
                        import json as _json
                        con = _json.loads(con)
                    except (ValueError, TypeError):
                        continue
                con_type = con.get("type", "")
                if not con_type:
                    continue

                if "first" not in con and con_type not in ("Block",):
                    warnings.append(f"Skipped {con_type}: missing 'first' index")
                    continue

                # Remove existing constraint that matches this one
                # so the explicit constraint overwrites it.
                first = con.get("first", -2000)
                second = con.get("second", -2000)
                for ci in range(sketch.ConstraintCount - 1, -1, -1):
                    ec = sketch.Constraints[ci]
                    if (ec.Type == con_type
                            and ec.First == first
                            and ec.Second == second):
                        sketch.delConstraint(ci)
                        break

                args = [con_type]
                for key in ("first", "first_pos", "second", "second_pos", "value"):
                    if key in con:
                        v = con[key]
                        if key == "value":
                            args.append(float(v))
                        elif isinstance(v, float):
                            args.append(int(v))
                        else:
                            args.append(v)

                expr = con.get("expression")

                try:
                    ci = sketch.addConstraint(Sketcher.Constraint(*args))
                    con_added += 1
                    if expr:
                        sketch.setExpression(f"Constraints[{ci}]", expr)
                except Exception as e:
                    warnings.append(f"Failed {con_type}: {e}")

        if label:
            sketch.Label = label

        doc.recompute()

        # ── Report constraint status
        _DIMENSION_TYPES = {
            "Distance", "DistanceX", "DistanceY", "Radius", "Angle",
        }
        constraint_details = []
        try:
            for ci in range(sketch.ConstraintCount):
                c = sketch.Constraints[ci]
                if c.Type in _DIMENSION_TYPES:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type} = {c.Value}  <- bindable"
                    )
                else:
                    constraint_details.append(
                        f"Constraints[{ci}]: {c.Type}"
                    )
        except Exception:
            pass

        status = ""
        try:
            if sketch.FullyConstrained:
                status = " Fully constrained."
            else:
                dof = sketch.solve()
                if dof > 0:
                    status = f" Under-constrained ({dof} DOF remaining)."
                elif dof < 0:
                    status = " Over-constrained — remove redundant constraints."
                else:
                    status = " Fully constrained."
        except Exception:
            pass

        warn_info = ""
        if warnings:
            warn_info = "\nWarnings: " + "; ".join(warnings)

        constraint_info = ""
        if constraint_details:
            constraint_info = (
                "\nConstraints:\n"
                + "\n".join(f"  {d}" for d in constraint_details)
            )

        parts = []
        if geo_added or geo_removed:
            parts.append(f"geometry +{geo_added}/-{geo_removed}")
        if con_added or con_removed:
            parts.append(f"constraints +{con_added}/-{con_removed}")
        changes = ", ".join(parts) if parts else "no changes"

        return ToolResult(
            success=True,
            output=(f"Edited sketch '{sketch.Name}': {changes}. "
                    f"Total: {sketch.GeometryCount} geometries, "
                    f"{sketch.ConstraintCount} constraints.{status}"
                    f"{constraint_info}{warn_info}"),
            data={"name": sketch.Name,
                  "geo_added": geo_added, "geo_removed": geo_removed,
                  "con_added": con_added, "con_removed": con_removed,
                  "geometry_count": sketch.GeometryCount,
                  "constraint_count": sketch.ConstraintCount,
                  "constraints": constraint_details,
                  "fully_constrained": getattr(sketch, "FullyConstrained", None)},
        )

    return _with_undo("Edit Sketch", do)


EDIT_SKETCH = ToolDefinition(
    name="edit_sketch",
    description=(
        "Modify an existing sketch: add/remove geometry and constraints in one call. "
        "Geometry types: line, circle, arc, rectangle, polygon (same format as create_sketch). "
        "IMPORTANT: rectangles and circles auto-generate all necessary constraints "
        "(Coincident, Horizontal, Vertical, DistanceX, DistanceY, Radius). "
        "Do NOT add DistanceX/DistanceY/Radius constraints for rectangles or circles — "
        "they are already constrained by the geometry coordinates and dimensions you provide. "
        "Only add constraints for relationships BETWEEN geometries (e.g. Coincident to pin "
        "a circle center to a rectangle edge). "
        "COORDINATE SYSTEM: FreeCAD sketches use Y-up. When converting from SVG, images, "
        "or screen coordinates (which use Y-down), negate all Y values. "
        "BEST PRACTICE: To resize, move, or replace geometry, use clear_all=true and "
        "provide the complete new geometry in add_geometries. This avoids over-constraint "
        "issues. Incremental remove+add is error-prone because a rectangle is 4 lines "
        "(indices 0-3) with 10 constraints, not a single object."
    ),
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Name of the sketch to edit", required=True),
        ToolParam("clear_all", "boolean",
                  "Clear ALL existing geometry and constraints before adding new ones. "
                  "Use this for resizing, repositioning, or replacing the sketch content. "
                  "The sketch object (and its attachment plane/body) is preserved.",
                  required=False, default=False),
        ToolParam("add_geometries", "array",
                  "Geometries to add. Same format as create_sketch: "
                  "line: {type:'line',x1,y1,x2,y2}, "
                  "rectangle: {type:'rectangle',x,y,width,height}, "
                  "circle: {type:'circle',cx,cy,radius}, "
                  "arc: {type:'arc',cx,cy,radius,start_angle,end_angle}, "
                  "polygon: {type:'polygon',points:[[x,y],...]}.",
                  required=False, items={"type": "object"}),
        ToolParam("remove_geometries", "array",
                  "Geometry indices to remove (0-based, highest-first).",
                  required=False, items={"type": "integer"}),
        ToolParam("add_constraints", "array",
                  "Constraints to add. Each has 'type' plus: "
                  "first (geometry index, required), first_pos (vertex: 1=start 2=end), "
                  "second (2nd geometry index), second_pos, value (for dimensional). "
                  "Optional 'expression' to bind dimensional value. "
                  "Examples: "
                  "{type:'Horizontal', first:0}, "
                  "{type:'Coincident', first:0, first_pos:2, second:1, second_pos:1}, "
                  "{type:'Distance', first:0, value:25.0}, "
                  "{type:'DistanceX', first:0, first_pos:1, second:0, second_pos:2, value:40, expression:'Vars.width'}.",
                  required=False, items={"type": "object"}),
        ToolParam("remove_constraints", "array",
                  "Constraint indices to remove (0-based, highest-first).",
                  required=False, items={"type": "integer"}),
        ToolParam("label", "string", "New label for the sketch", required=False, default=""),
    ],
    handler=_handle_edit_sketch,
)


# ── pad_sketch ──────────────────────────────────────────────

def _handle_pad_sketch(
    sketch_name: str,
    length: float | str = 10.0,
    symmetric: bool = False,
    label: str = "",
    body_name: str = "",
) -> ToolResult:
    """Pad (extrude) a sketch."""

    def do(doc):
        sketch = _get_object(doc, sketch_name)
        if not sketch:
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found.{hint}")

        # Find the body — prefer explicit body_name, fall back to auto-detect
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
        else:
            body = _find_body_for(doc, sketch)
        if not body:
            return ToolResult(success=False, output="", error=f"No PartDesign body found for sketch '{sketch_name}'")

        pad = body.newObject("PartDesign::Pad", label or "Pad")
        pad.Profile = sketch
        # Length can be a number or an expression string (e.g. "Variables.height")
        length_expr = None
        if isinstance(length, str):
            length_expr = length
            pad.Length = 10  # placeholder
        else:
            pad.Length = length
        if length_expr:
            pad.setExpression("Length", length_expr)
        if symmetric:
            pad.Midplane = True
        sketch.Visibility = False

        return ToolResult(
            success=True,
            output=f"Padded sketch '{sketch.Name}' by {length}mm (pad name: '{pad.Name}')",
            data={"name": pad.Name, "label": pad.Label, "length": length},
        )

    return _with_undo("Pad Sketch", do)


PAD_SKETCH = ToolDefinition(
    name="pad_sketch",
    description=(
        "Pad (extrude) a sketch to create a solid. The sketch must be inside a PartDesign Body. "
        "Length can be an expression string for parametric models: length='Variables.height'."
    ),
    category="modeling",
    parameters=[
        ToolParam("sketch_name", "string", "Internal name of the sketch to pad"),
        ToolParam("length", "string", "Extrusion length in mm, or expression string (e.g. 'Variables.height')",
                  required=False, default=10.0),
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
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found.{hint}")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
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
            output=f"Created pocket from sketch '{sketch.Name}' (pocket name: '{pocket.Name}')",
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
            hint = _suggest_similar(doc, sketch_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Sketch '{sketch_name}' not found.{hint}")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
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
                hint = _suggest_similar(doc, name, "Sketcher")
                return ToolResult(success=False, output="", error=f"Section '{name}' not found.{hint}")
            sections.append(s)

        # Find the body
        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
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
            hint = _suggest_similar(doc, profile_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Profile sketch '{profile_name}' not found.{hint}")

        spine = _get_object(doc, spine_name)
        if not spine:
            hint = _suggest_similar(doc, spine_name, "Sketcher")
            return ToolResult(success=False, output="", error=f"Spine sketch '{spine_name}' not found.{hint}")

        body = None
        if body_name:
            body = _get_object(doc, body_name)
            if not body:
                hint = _suggest_similar(doc, body_name, "Body")
                return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")
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
            hint = _suggest_similar(doc, object1)
            return ToolResult(success=False, output="", error=f"Object '{object1}' not found.{hint}")
        if not obj2:
            hint = _suggest_similar(doc, object2)
            return ToolResult(success=False, output="", error=f"Object '{object2}' not found.{hint}")

        op = operation.lower()
        if op not in ("fuse", "cut", "common"):
            return ToolResult(
                success=False, output="",
                error=f"Unknown operation: {operation}. Use: fuse, cut, common",
            )

        # When BOTH operands are PartDesign Bodies, use a parametric
        # PartDesign::Boolean inside the base Body instead of a Part:: boolean.
        # A Part::Cut/Fuse/Common claims its operands as tree children,
        # reparenting the base Body under the new node — its sketches and
        # features become buried and uneditable (issue #17). PartDesign::Boolean
        # appends the operation to the base Body's feature history, so the Body
        # stays top-level and fully editable.
        if obj1.TypeId == "PartDesign::Body" and obj2.TypeId == "PartDesign::Body":
            pd_type_map = {"fuse": "Fuse", "cut": "Cut", "common": "Common"}
            boolean = obj1.newObject("PartDesign::Boolean", label or operation.capitalize())
            boolean.Type = pd_type_map[op]
            boolean.addObjects([obj2])
            return ToolResult(
                success=True,
                output=(f"Boolean {operation} of '{obj1.Label}' and '{obj2.Label}' "
                        f"(parametric PartDesign feature inside '{obj1.Label}' — "
                        f"history preserved)"),
                data={"name": boolean.Name, "label": boolean.Label,
                      "base_body": obj1.Name},
            )

        # Otherwise (one or both operands are plain Part shapes) fall back to a
        # generic Part:: boolean, which works on any shape.
        op_map = {
            "fuse": "Part::Fuse",
            "cut": "Part::Cut",
            "common": "Part::Common",
        }
        part_type = op_map[op]
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
    description=(
        "Boolean operation (fuse/cut/common) between two SEPARATE objects. "
        "If both operands are PartDesign Bodies, this automatically uses a "
        "parametric PartDesign::Boolean inside the first Body, preserving its "
        "editable feature history. For plain Part shapes it uses a Part:: "
        "boolean, which consumes both operands into the result. "
        "Do NOT use this to drill a hole / cut a feature into a SINGLE existing "
        "solid — for that, add a feature inside that Body instead: "
        "create_primitive(operation='subtractive', body_name=...) or "
        "pocket_sketch."
    ),
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

def _apply_relative_placement(old, tx, ty, tz, ax, ay, az, angle):
    """Compose a relative move/rotate onto an existing placement.

    Translate is global (added to the base); rotation is applied in place about
    the object's own origin (pre-multiplied onto the current rotation, base kept).
    Uses FreeCAD's placement math — intentionally NOT a pure/standalone helper
    (reimplementing quaternion composition would be more error-prone). Pinned by
    integration tests.
    """
    import FreeCAD as App
    new_base = old.Base + App.Vector(tx, ty, tz)
    delta_rot = App.Rotation(App.Vector(ax, ay, az), angle)
    new_rot = delta_rot.multiply(old.Rotation)
    return App.Placement(new_base, new_rot)


def _handle_transform_object(
    object_name: str,
    translate_x: float = 0.0,
    translate_y: float = 0.0,
    translate_z: float = 0.0,
    rotate_axis_x: float = 0.0,
    rotate_axis_y: float = 0.0,
    rotate_axis_z: float = 1.0,
    rotate_angle: float = 0.0,
    relative: bool = True,
) -> ToolResult:
    """Move and/or rotate an object, relative to its current placement by default."""
    import FreeCAD as App

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        if relative:
            obj.Placement = _apply_relative_placement(
                obj.Placement, translate_x, translate_y, translate_z,
                rotate_axis_x, rotate_axis_y, rotate_axis_z, rotate_angle)
        else:
            obj.Placement = App.Placement(
                App.Vector(translate_x, translate_y, translate_z),
                App.Rotation(App.Vector(rotate_axis_x, rotate_axis_y, rotate_axis_z), rotate_angle))

        parts = []
        if translate_x or translate_y or translate_z:
            parts.append(f"translated ({translate_x}, {translate_y}, {translate_z})")
        if rotate_angle:
            parts.append(f"rotated {rotate_angle}°")
        mode = "relative" if relative else "absolute"
        if parts:
            desc = ", ".join(parts) + f" ({mode})"
        elif relative:
            desc = "unchanged (relative, no delta given)"
        else:
            desc = "placement reset to origin (absolute)"

        return ToolResult(
            success=True,
            output=f"Transformed '{obj.Label}': {desc}",
            data={"name": obj.Name},
        )

    return _with_undo("Transform Object", do)


TRANSFORM_OBJECT = ToolDefinition(
    name="transform_object",
    description=(
        "Move and/or rotate an object. By default (relative=True) the change is "
        "applied RELATIVE to the object's current placement — translation adds to "
        "its position, rotation spins it in place, and 0 means no change. Set "
        "relative=False for an ABSOLUTE placement (overwrites position and "
        "orientation; omitted values reset to 0). Does not copy — use "
        "duplicate_object to make a copy."
    ),
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
        ToolParam("relative", "boolean", "Apply relative to the current placement "
                  "(default). False = absolute overwrite.", required=False, default=True),
    ],
    handler=_handle_transform_object,
)


def _duplicate_label(base_label, requested):
    """Label for a duplicated object: the requested label, or '<base>_Copy'."""
    return requested or f"{base_label}_Copy"


def _handle_duplicate_object(
    object_name: str,
    translate_x: float = 0.0,
    translate_y: float = 0.0,
    translate_z: float = 0.0,
    rotate_axis_x: float = 0.0,
    rotate_axis_y: float = 0.0,
    rotate_axis_z: float = 1.0,
    rotate_angle: float = 0.0,
    label: str = "",
) -> ToolResult:
    """Duplicate an object (independent parametric copy), optionally offsetting it."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        result = doc.copyObject(obj, True)
        # copyObject(single_obj, True) returns a plain DocumentObject on FreeCAD
        # 1.1.x; the list/tuple branch is a forward-compat guard. [-1] is chosen
        # because topological order places the most-derived object (e.g. the Body)
        # last. Pinned by integration.
        copy = result[-1] if isinstance(result, (list, tuple)) else result
        if copy is None:
            return ToolResult(success=False, output="", error=f"Failed to duplicate '{obj.Label}'.")

        copy.Label = _duplicate_label(obj.Label, label)

        if translate_x or translate_y or translate_z or rotate_angle:
            copy.Placement = _apply_relative_placement(
                copy.Placement, translate_x, translate_y, translate_z,
                rotate_axis_x, rotate_axis_y, rotate_axis_z, rotate_angle)

        doc.recompute()

        # If the copy can't recompute cleanly, State contains Invalid/Error. (For a
        # Body only the Body-level state is checked here; the executor sandbox
        # catches child-level failures as the higher-level net.)
        state = list(getattr(copy, "State", []) or [])
        if any(s in ("Invalid", "Error") for s in state):
            return ToolResult(success=False, output="",
                              error=f"Duplicate '{copy.Label}' did not recompute cleanly.")

        return ToolResult(
            success=True,
            output=(f"Duplicated '{obj.Label}' → '{copy.Label}' ({copy.TypeId}); "
                    "original unchanged."),
            data={"name": copy.Name, "label": copy.Label},
        )

    return _with_undo("Duplicate Object", do)


DUPLICATE_OBJECT = ToolDefinition(
    name="duplicate_object",
    description=(
        "Duplicate an object as an independent, editable copy that preserves its "
        "parametric history (the whole feature tree — e.g. a Body with its sketches "
        "and pads). The original is left unchanged. Optional translate/rotate offset "
        "the copy relative to the original (0 = on top of it). To duplicate a solid, "
        "pass its Body. Note: if the object's placement is driven by an attachment "
        "(e.g. a datum attached to an edge), the offset won't stick — duplicate a "
        "fixed-placement object (such as a two-point datum line) for a parallel copy."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object to duplicate"),
        ToolParam("translate_x", "number", "X offset of the copy in mm", required=False, default=0.0),
        ToolParam("translate_y", "number", "Y offset of the copy in mm", required=False, default=0.0),
        ToolParam("translate_z", "number", "Z offset of the copy in mm", required=False, default=0.0),
        ToolParam("rotate_axis_x", "number", "Rotation axis X component", required=False, default=0.0),
        ToolParam("rotate_axis_y", "number", "Rotation axis Y component", required=False, default=0.0),
        ToolParam("rotate_axis_z", "number", "Rotation axis Z component", required=False, default=1.0),
        ToolParam("rotate_angle", "number", "Rotation angle for the copy in degrees", required=False, default=0.0),
        ToolParam("label", "string", "Label for the copy (default '<original>_Copy')", required=False, default=""),
    ],
    handler=_handle_duplicate_object,
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
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        raw_refs = _coerce_str_list(edges) or ["Edge1"]

        # Check if this is a PartDesign body/feature
        # If obj IS a Body, use its Tip (last feature) as the fillet base
        body = None
        base_feature = obj
        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            body = obj
            base_feature = obj.Tip
            if not base_feature:
                return ToolResult(success=False, output="",
                                  error=f"Body '{obj.Label}' has no features to fillet.")
        else:
            body = _find_body_for(doc, obj)

        # Resolve filter keywords (all, vertical, top, etc.) into edge names
        edge_refs = _resolve_edge_refs(obj.Shape, raw_refs)
        if not edge_refs:
            return ToolResult(success=False, output="",
                              error=f"No edges match filter {raw_refs} on '{obj.Label}'.")

        if body:
            fillet = body.newObject("PartDesign::Fillet", label or "Fillet")
            fillet.Base = (base_feature, edge_refs)
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
            data={"name": fillet.Name, "label": fillet.Label, "radius": radius,
                  "edges": edge_refs},
        )

    return _with_undo("Fillet Edges", do)


FILLET_EDGES = ToolDefinition(
    name="fillet_edges",
    description=(
        "Apply a fillet (rounded edge) to one or more edges of an object. "
        "Edges can be explicit names (Edge1, Edge4) or filter keywords: "
        "'all', 'vertical', 'horizontal', 'top', 'bottom', 'front', 'back', "
        "'left', 'right', 'circular'. Filters can be combined: ['top', 'vertical']."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("edges", "array",
                  "Edge references or filter keywords, e.g. ['all'], ['vertical'], ['Edge1', 'Edge4']",
                  required=False, items={"type": "string"}),
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
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

        raw_refs = _coerce_str_list(edges) or ["Edge1"]

        # If obj IS a Body, use its Tip (last feature) as the chamfer base
        body = None
        base_feature = obj
        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            body = obj
            base_feature = obj.Tip
            if not base_feature:
                return ToolResult(success=False, output="",
                                  error=f"Body '{obj.Label}' has no features to chamfer.")
        else:
            body = _find_body_for(doc, obj)

        # Resolve filter keywords
        edge_refs = _resolve_edge_refs(obj.Shape, raw_refs)
        if not edge_refs:
            return ToolResult(success=False, output="",
                              error=f"No edges match filter {raw_refs} on '{obj.Label}'.")

        if body:
            chamfer = body.newObject("PartDesign::Chamfer", label or "Chamfer")
            chamfer.Base = (base_feature, edge_refs)
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
            data={"name": chamfer.Name, "label": chamfer.Label, "size": size,
                  "edges": edge_refs},
        )

    return _with_undo("Chamfer Edges", do)


CHAMFER_EDGES = ToolDefinition(
    name="chamfer_edges",
    description=(
        "Apply a chamfer (angled edge cut) to one or more edges of an object. "
        "Edges can be explicit names (Edge1, Edge4) or filter keywords: "
        "'all', 'vertical', 'horizontal', 'top', 'bottom', 'front', 'back', "
        "'left', 'right', 'circular'. Filters can be combined: ['top', 'vertical']."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("edges", "array",
                  "Edge references or filter keywords, e.g. ['all'], ['vertical'], ['Edge1', 'Edge4']",
                  required=False, items={"type": "string"}),
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


# ── describe_model ──────────────────────────────────────────

def _handle_describe_model(object_name: str) -> ToolResult:
    """Return a comprehensive geometry summary of an object."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        hint = _suggest_similar(doc, object_name)
        return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

    shape = getattr(obj, "Shape", None)
    if not shape:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no Shape")

    lines = [f"## Geometry of '{obj.Label}' ({obj.TypeId})"]

    # Bounding box
    bb = shape.BoundBox
    lines.append(f"**Bounding box:** {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm")
    lines.append(f"  X: [{bb.XMin:.2f}, {bb.XMax:.2f}]  Y: [{bb.YMin:.2f}, {bb.YMax:.2f}]  Z: [{bb.ZMin:.2f}, {bb.ZMax:.2f}]")

    # Volume and area
    if shape.Volume > 0:
        lines.append(f"**Volume:** {shape.Volume:.1f} mm\u00b3")
    if shape.Area > 0:
        lines.append(f"**Surface area:** {shape.Area:.1f} mm\u00b2")

    # Solid check
    lines.append(f"**Valid:** {shape.isValid()}")
    lines.append(f"**Solids:** {len(shape.Solids)}  **Shells:** {len(shape.Shells)}  **Faces:** {len(shape.Faces)}  **Edges:** {len(shape.Edges)}")

    # Hollow detection via comparing volume to bounding box volume
    bb_vol = bb.XLength * bb.YLength * bb.ZLength
    if bb_vol > 0 and shape.Volume > 0:
        fill_ratio = shape.Volume / bb_vol
        if fill_ratio < 0.5:
            lines.append(f"**Likely hollow** (fill ratio: {fill_ratio:.1%})")
        else:
            lines.append(f"**Likely solid** (fill ratio: {fill_ratio:.1%})")

    # Wall thickness estimation via ray casting from center
    try:
        center = App.Vector(bb.Center)
        thicknesses = []
        for direction in [App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1),
                          App.Vector(-1, 0, 0), App.Vector(0, -1, 0), App.Vector(0, 0, -1)]:
            # Cast ray from center outward, find first two face intersections
            # to estimate wall thickness
            try:
                hits = shape.distToShape(
                    shape.__class__.makeBox(0.01, 0.01, 0.01,
                                           center + direction * 500)
                )
            except Exception:
                continue
        # Alternative: use section cuts to estimate wall thickness
        for axis, offset in [("XY", bb.ZMax), ("XZ", bb.YMax), ("YZ", bb.XMax)]:
            try:
                if axis == "XY":
                    plane_base = App.Vector(0, 0, offset - 0.1)
                    plane_norm = App.Vector(0, 0, 1)
                elif axis == "XZ":
                    plane_base = App.Vector(0, offset - 0.1, 0)
                    plane_norm = App.Vector(0, 1, 0)
                else:
                    plane_base = App.Vector(offset - 0.1, 0, 0)
                    plane_norm = App.Vector(1, 0, 0)
                wires = shape.slice(plane_norm, offset - 0.1)
                if len(wires) >= 2:
                    # Two concentric wires = hollow with walls
                    bbs = sorted([w.BoundBox for w in wires],
                                 key=lambda b: b.XLength * b.YLength, reverse=True)
                    if len(bbs) >= 2:
                        outer = bbs[0]
                        inner = bbs[1]
                        wall_x = (outer.XLength - inner.XLength) / 2
                        wall_y = (outer.YLength - inner.YLength) / 2
                        if wall_x > 0.1 and wall_y > 0.1:
                            thicknesses.append(min(wall_x, wall_y))
            except Exception:
                continue
        if thicknesses:
            avg_wall = sum(thicknesses) / len(thicknesses)
            lines.append(f"**Estimated wall thickness:** ~{avg_wall:.1f} mm")
    except Exception:
        pass

    # PartDesign body features
    if obj.TypeId == "PartDesign::Body" and hasattr(obj, "Group"):
        features = [m for m in obj.Group if not m.TypeId.startswith("App::")]
        if features:
            lines.append(f"**Features ({len(features)}):**")
            for feat in features:
                feat_info = f"  - {feat.Name} ({feat.TypeId})"
                if hasattr(feat, "Length"):
                    feat_info += f" — Length: {float(feat.Length):.1f} mm"
                if hasattr(feat, "Radius"):
                    feat_info += f" — Radius: {float(feat.Radius):.1f} mm"
                lines.append(feat_info)

    output = "\n".join(lines)
    return ToolResult(success=True, output=output, data={"label": obj.Label})


DESCRIBE_MODEL = ToolDefinition(
    name="describe_model",
    description=(
        "Get a comprehensive geometry summary of an object: dimensions, volume, "
        "face/edge counts, hollow/solid detection, estimated wall thickness, "
        "and PartDesign feature list. Use this to inspect or verify a model."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Internal name or label of the object to describe"),
    ],
    handler=_handle_describe_model,
)


# ── list_faces ──────────────────────────────────────────────

def _classify_face(face, bbox) -> str:
    """Classify a face by its geometry and position relative to the object bounding box.

    Returns a human-readable label like 'top', 'bottom', 'front', etc.
    """
    surface = face.Surface
    surface_type = surface.__class__.__name__

    # Planar faces — classify by normal direction and position
    if surface_type == "Plane":
        normal = face.normalAt(0, 0)
        center = face.CenterOfMass
        tol = 0.1  # tolerance for axis-aligned detection

        # Determine which axis the normal is closest to
        abs_x, abs_y, abs_z = abs(normal.x), abs(normal.y), abs(normal.z)

        if abs_z > abs_x and abs_z > abs_y:
            # Z-axis face
            if normal.z > 0:
                return "top" if abs(center.z - bbox.ZMax) < tol else "horizontal"
            else:
                return "bottom" if abs(center.z - bbox.ZMin) < tol else "horizontal"
        elif abs_y > abs_x and abs_y > abs_z:
            # Y-axis face
            if normal.y > 0:
                return "back" if abs(center.y - bbox.YMax) < tol else "side"
            else:
                return "front" if abs(center.y - bbox.YMin) < tol else "side"
        elif abs_x > abs_y and abs_x > abs_z:
            # X-axis face
            if normal.x > 0:
                return "right" if abs(center.x - bbox.XMax) < tol else "side"
            else:
                return "left" if abs(center.x - bbox.XMin) < tol else "side"
        return "angled"

    elif surface_type == "Cylinder":
        radius = surface.Radius
        return f"cylindrical (R={radius:.1f})"
    elif surface_type == "Cone":
        return "conical"
    elif surface_type == "Sphere":
        radius = surface.Radius
        return f"spherical (R={radius:.1f})"
    elif surface_type == "Toroid":
        return "toroidal"
    else:
        return surface_type.lower()


def _handle_list_faces(object_name: str, filter: str = "") -> ToolResult:
    """List faces of an object, optionally filtered by keyword."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        hint = _suggest_similar(doc, object_name)
        return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

    shape = getattr(obj, "Shape", None)
    if not shape:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no Shape")

    if not shape.Faces:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no faces")

    bbox = shape.BoundBox
    filter_lower = filter.strip().lower() if filter else ""

    lines = []
    face_data = []

    for i, face in enumerate(shape.Faces):
        name = f"Face{i + 1}"
        center = face.CenterOfMass
        area = face.Area
        label = _classify_face(face, bbox)

        # Apply filter if specified
        if filter_lower and filter_lower not in label.lower():
            continue

        surface_type = face.Surface.__class__.__name__

        # Get normal for planar faces
        normal_str = ""
        if surface_type == "Plane":
            n = face.normalAt(0, 0)
            normal_str = f"  normal=({n.x:.2f}, {n.y:.2f}, {n.z:.2f})"

        lines.append(
            f"- **{name}** \"{label}\" — center=({center.x:.1f}, {center.y:.1f}, {center.z:.1f}), "
            f"area={area:.1f}mm²{normal_str}"
        )

        face_data.append({
            "name": name,
            "label": label,
            "type": surface_type.lower(),
            "center": [round(center.x, 2), round(center.y, 2), round(center.z, 2)],
            "area": round(area, 2),
        })

    total = len(shape.Faces)
    shown = len(face_data)
    if filter_lower:
        header = f"## Faces of '{obj.Label}' matching '{filter}' ({shown}/{total} faces)"
    else:
        header = f"## Faces of '{obj.Label}' ({total} faces)"

    output = "\n".join([header] + lines)
    return ToolResult(success=True, output=output, data={"faces": face_data})


LIST_FACES = ToolDefinition(
    name="list_faces",
    description=(
        "List faces of an object with reference names (Face1, Face2, ...), "
        "human-readable labels (top, bottom, front, back, left, right, cylindrical), "
        "center positions, normals, and areas. Use this to identify which face to "
        "reference in shell_object, assembly constraints, or other face-based operations. "
        "Optional filter to show only matching faces (e.g. 'top', 'cylindrical')."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Internal name or label of the object"),
        ToolParam("filter", "string",
                  "Filter keyword to show only matching faces (e.g. 'top', 'cylindrical', 'side')",
                  required=False, default=""),
    ],
    handler=_handle_list_faces,
)


# ── list_edges ──────────────────────────────────────────────

def _classify_edge(edge, bbox) -> str:
    """Classify an edge by its geometry, direction, and position relative to the bounding box.

    Returns a human-readable label like 'top-front horizontal', 'front-left vertical', etc.
    """
    curve = edge.Curve
    curve_type = curve.__class__.__name__

    if curve_type not in ("Line", "LineSegment"):
        # Curved edges — report type and radius if available
        if curve_type in ("Circle", "ArcOfCircle"):
            return f"circular (R={curve.Radius:.1f})"
        elif curve_type == "BSplineCurve":
            return "spline"
        elif curve_type in ("Ellipse", "ArcOfEllipse"):
            return "elliptical"
        return curve_type.lower()

    # Straight edge — classify by direction and position
    mid = edge.CenterOfMass
    tol = 0.1
    length = edge.Length

    # Determine direction from start/end vertices
    p1 = edge.Vertexes[0].Point
    p2 = edge.Vertexes[1].Point
    dx = abs(p2.x - p1.x)
    dy = abs(p2.y - p1.y)
    dz = abs(p2.z - p1.z)
    max_d = max(dx, dy, dz)

    if max_d < tol:
        return "point"

    if dz / max_d > 0.9:
        direction = "vertical"
    elif dx / max_d > 0.9 and dy / max_d < 0.1:
        direction = "horizontal-X"
    elif dy / max_d > 0.9 and dx / max_d < 0.1:
        direction = "horizontal-Y"
    elif dz / max_d < 0.1:
        direction = "horizontal"
    else:
        direction = "diagonal"

    # Determine position labels from midpoint proximity to bounding box faces
    parts = []

    # Z position
    if abs(mid.z - bbox.ZMax) < tol:
        parts.append("top")
    elif abs(mid.z - bbox.ZMin) < tol:
        parts.append("bottom")

    # Y position
    if abs(mid.y - bbox.YMin) < tol:
        parts.append("front")
    elif abs(mid.y - bbox.YMax) < tol:
        parts.append("back")

    # X position
    if abs(mid.x - bbox.XMin) < tol:
        parts.append("left")
    elif abs(mid.x - bbox.XMax) < tol:
        parts.append("right")

    position = "-".join(parts) if parts else "interior"
    return f"{position} {direction}"


# ── Edge / face filter keywords ────────────────────────────
#
# Filter keywords that can be used instead of (or mixed with) explicit
# Edge/Face references in fillet_edges, chamfer_edges, shell_object, etc.
#
# Edge keywords: "all", "vertical", "horizontal", "top", "bottom",
#                "front", "back", "left", "right", "circular"
# Face keywords: "all", "top", "bottom", "front", "back", "left",
#                "right", "cylindrical", "spherical"

_EDGE_FILTER_KEYWORDS = {
    "all", "vertical", "horizontal", "top", "bottom",
    "front", "back", "left", "right", "circular",
}

_FACE_FILTER_KEYWORDS = {
    "all", "top", "bottom", "front", "back", "left", "right",
    "cylindrical", "spherical", "side",
}


def _resolve_edge_refs(shape, edge_input: list[str]) -> list[str]:
    """Resolve a mix of explicit edge names and filter keywords into edge names.

    Args:
        shape: FreeCAD Shape with .Edges and .BoundBox.
        edge_input: List of strings — Edge references ("Edge1") and/or
            filter keywords ("all", "vertical", "top", etc.).

    Returns:
        Sorted, deduplicated list of edge reference strings.
    """
    bbox = shape.BoundBox
    result = set()

    # Check if any element is a filter keyword
    has_filters = any(e.lower() in _EDGE_FILTER_KEYWORDS for e in edge_input)

    if not has_filters:
        # All explicit — return as-is
        return list(edge_input)

    for token in edge_input:
        token_lower = token.lower()
        if token_lower not in _EDGE_FILTER_KEYWORDS:
            # Explicit edge name — keep it
            result.add(token)
            continue

        if token_lower == "all":
            return [f"Edge{i + 1}" for i in range(len(shape.Edges))]

        # Filter by classification label
        for i, edge in enumerate(shape.Edges):
            label = _classify_edge(edge, bbox).lower()
            if token_lower == "circular":
                if "circular" in label:
                    result.add(f"Edge{i + 1}")
            elif token_lower in label:
                result.add(f"Edge{i + 1}")

    # Sort numerically: Edge1, Edge2, ..., Edge12
    return sorted(result, key=lambda e: int(e.replace("Edge", "")))


def _resolve_face_refs(shape, face_input: list[str]) -> list[str]:
    """Resolve a mix of explicit face names and filter keywords into face names.

    Args:
        shape: FreeCAD Shape with .Faces and .BoundBox.
        face_input: List of strings — Face references ("Face1") and/or
            filter keywords ("all", "top", "bottom", etc.).

    Returns:
        Sorted, deduplicated list of face reference strings.
    """
    bbox = shape.BoundBox
    result = set()

    has_filters = any(f.lower() in _FACE_FILTER_KEYWORDS for f in face_input)

    if not has_filters:
        return list(face_input)

    for token in face_input:
        token_lower = token.lower()
        if token_lower not in _FACE_FILTER_KEYWORDS:
            result.add(token)
            continue

        if token_lower == "all":
            return [f"Face{i + 1}" for i in range(len(shape.Faces))]

        for i, face in enumerate(shape.Faces):
            label = _classify_face(face, bbox).lower()
            if token_lower in label:
                result.add(f"Face{i + 1}")

    return sorted(result, key=lambda f: int(f.replace("Face", "")))


def _handle_list_edges(object_name: str, filter: str = "") -> ToolResult:
    """List edges of an object, optionally filtered by keyword."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    obj = _get_object(doc, object_name)
    if not obj:
        hint = _suggest_similar(doc, object_name)
        return ToolResult(success=False, output="", error=f"Object '{object_name}' not found.{hint}")

    shape = getattr(obj, "Shape", None)
    if not shape:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no Shape")

    if not shape.Edges:
        return ToolResult(success=False, output="",
                          error=f"Object '{object_name}' has no edges")

    bbox = shape.BoundBox
    filter_lower = filter.strip().lower() if filter else ""

    lines = []
    edge_data = []

    for i, edge in enumerate(shape.Edges):
        name = f"Edge{i + 1}"
        mid = edge.CenterOfMass
        length = edge.Length
        label = _classify_edge(edge, bbox)

        # Apply filter if specified
        if filter_lower and filter_lower not in label.lower():
            continue

        lines.append(
            f"- **{name}** \"{label}\" — midpoint=({mid.x:.1f}, {mid.y:.1f}, {mid.z:.1f}), "
            f"length={length:.1f}mm"
        )

        edge_data.append({
            "name": name,
            "label": label,
            "midpoint": [round(mid.x, 2), round(mid.y, 2), round(mid.z, 2)],
            "length": round(length, 2),
        })

    total = len(shape.Edges)
    shown = len(edge_data)
    if filter_lower:
        header = f"## Edges of '{obj.Label}' matching '{filter}' ({shown}/{total} edges)"
    else:
        header = f"## Edges of '{obj.Label}' ({total} edges)"

    output = "\n".join([header] + lines)
    return ToolResult(success=True, output=output, data={"edges": edge_data})


LIST_EDGES = ToolDefinition(
    name="list_edges",
    description=(
        "List edges of an object with reference names (Edge1, Edge2, ...), "
        "human-readable labels (top-front horizontal, front-left vertical, circular, etc.), "
        "midpoint positions, and lengths. Use this to identify which edges to "
        "reference in fillet_edges, chamfer_edges, or other edge-based operations. "
        "Optional filter to show only matching edges (e.g. 'vertical', 'top', 'circular')."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Internal name or label of the object"),
        ToolParam("filter", "string",
                  "Filter keyword to show only matching edges (e.g. 'vertical', 'top', 'circular')",
                  required=False, default=""),
    ],
    handler=_handle_list_edges,
)


# ── list_documents ─────────────────────────────────────────

def _handle_list_documents() -> ToolResult:
    """List all open FreeCAD documents."""
    import FreeCAD as App
    from ..core.active_document import resolve_active_document

    docs = list(App.listDocuments().values())
    if not docs:
        return ToolResult(success=True, output="No documents open.",
                          data={"documents": []})

    active = resolve_active_document()
    active_name = active.Name if active else ""

    lines = [f"## Open Documents ({len(docs)})"]
    doc_data = []
    for doc in docs:
        obj_count = len(doc.Objects)
        marker = " (active)" if doc.Name == active_name else ""
        modified = " *" if doc.Modified else ""
        path = doc.FileName or "(unsaved)"
        lines.append(
            f"- **{doc.Name}**{marker}{modified} — "
            f"{obj_count} objects — {path}"
        )
        doc_data.append({
            "name": doc.Name,
            "label": doc.Label,
            "active": doc.Name == active_name,
            "object_count": obj_count,
            "modified": doc.Modified,
            "path": doc.FileName,
        })

    return ToolResult(success=True, output="\n".join(lines),
                      data={"documents": doc_data})


LIST_DOCUMENTS = ToolDefinition(
    name="list_documents",
    description=f"List all open {APP_NAME} documents with object counts and active indicator.",
    category="query",
    parameters=[],
    handler=_handle_list_documents,
)


# ── switch_document ───────────────────────────────────────

def _handle_switch_document(document_name: str) -> ToolResult:
    """Switch the active document."""
    import FreeCAD as App
    from ..core.active_document import sync_app_active_document, refresh_gui_for_document

    docs = App.listDocuments()
    doc = docs.get(document_name)
    if not doc:
        # Try matching by label
        for d in docs.values():
            if d.Label == document_name:
                doc = d
                break
    if not doc:
        available = ", ".join(docs.keys())
        return ToolResult(success=False, output="",
                          error=f"Document '{document_name}' not found. Available: {available}")

    sync_app_active_document(doc)
    refresh_gui_for_document(doc)

    return ToolResult(
        success=True,
        output=f"Switched to document '{doc.Name}' ({len(doc.Objects)} objects).",
        data={"name": doc.Name, "label": doc.Label},
    )


SWITCH_DOCUMENT = ToolDefinition(
    name="switch_document",
    description=f"Switch the active {APP_NAME} document by name or label.",
    category="query",
    parameters=[
        ToolParam("document_name", "string", "Name or label of the document to activate"),
    ],
    handler=_handle_switch_document,
)


# ── get_document_state ──────────────────────────────────────

def _handle_get_document_state() -> ToolResult:
    """Get the current document state — all objects and their properties."""
    import FreeCAD as App
    from ..core.context import get_document_context

    if not App.ActiveDocument:
        return ToolResult(
            success=False,
            output="",
            error="No active document",
            data={},
        )
    ctx = get_document_context()
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


# ── create_variable_set ────────────────────────────────────

def _handle_create_variable_set(
    variables: dict | None = None,
    label: str = "Variables",
) -> ToolResult:
    """Create an App::VarSet with typed properties for parametric modeling."""

    def do(doc):
        if not variables:
            return ToolResult(success=False, output="",
                              error="No variables provided. Pass a dict like "
                                    "{\"length\": 50, \"width\": 30}.")

        _PROP_TYPES = {
            int: "App::PropertyInteger",
            float: "App::PropertyFloat",
            str: "App::PropertyString",
            bool: "App::PropertyBool",
        }

        vs = doc.addObject("App::VarSet", label)
        var_names = []
        for name, value in variables.items():
            prop_type = _PROP_TYPES.get(type(value), "App::PropertyFloat")
            try:
                vs.addProperty(prop_type, name, "Parameters", "")
                setattr(vs, name, value)
            except Exception as e:
                return ToolResult(
                    success=False, output="",
                    error=f"Invalid variable name '{name}': {e}")
            var_names.append(name)

        doc.recompute()

        names_str = ", ".join(f"{n}={variables[n]}" for n in var_names)
        usage = ", ".join(f'"{vs.Label}.{n}"' for n in var_names[:3])
        if len(var_names) > 3:
            usage += ", ..."

        return ToolResult(
            success=True,
            output=(f"Created VarSet '{vs.Name}' with {len(var_names)} variables: "
                    f"{names_str}. "
                    f"Reference in expressions as {usage}. "
                    f"Pass these as width/height/length values in create_sketch and pad_sketch."),
            data={"name": vs.Name, "label": vs.Label,
                  "variables": dict(variables)},
        )

    return _with_undo("Create Variable Set", do)


CREATE_VARIABLE_SET = ToolDefinition(
    name="create_variable_set",
    description=(
        "Create a VarSet (App::VarSet) with named, typed variables for parametric modeling. "
        "Variables appear as editable properties in the Data panel. "
        "After creation, pass variable references as dimension values in create_sketch "
        "(e.g. width='Variables.length') and pad_sketch (e.g. length='Variables.height'). "
        "Example: create_variable_set(variables={\"length\": 50, \"width\": 30, \"height\": 20}). "
        "Alternative: use create_spreadsheet for a spreadsheet-based approach."
    ),
    category="modeling",
    parameters=[
        ToolParam("variables", "object",
                  "Dict of variable names and values, e.g. {\"length\": 50, \"wall\": 2}"),
        ToolParam("label", "string", "Display label for the variable set",
                  required=False, default="Variables"),
    ],
    handler=_handle_create_variable_set,
)


# ── create_spreadsheet ────────────────────────────────────

def _handle_create_spreadsheet(
    variables: dict | None = None,
    label: str = "Variables",
) -> ToolResult:
    """Create a Spreadsheet with named cells for parametric modeling."""

    def do(doc):
        if not variables:
            return ToolResult(success=False, output="",
                              error="No variables provided. Pass a dict like "
                                    "{\"length\": 50, \"width\": 30}.")

        sheet = doc.addObject("Spreadsheet::Sheet", label)
        var_names = []
        for i, (name, value) in enumerate(variables.items()):
            row = i + 1
            cell = f"A{row}"
            sheet.set(f"B{row}", str(name))
            sheet.set(cell, str(value))
            try:
                sheet.setAlias(cell, name)
            except Exception as e:
                return ToolResult(
                    success=False, output="",
                    error=f"Invalid variable name '{name}': {e}. "
                          "Avoid names that look like cell addresses (e.g. A1, B2).")
            var_names.append(name)

        doc.recompute()

        names_str = ", ".join(f"{n}={variables[n]}" for n in var_names)
        usage = ", ".join(f'"{sheet.Label}.{n}"' for n in var_names[:3])
        if len(var_names) > 3:
            usage += ", ..."

        return ToolResult(
            success=True,
            output=(f"Created Spreadsheet '{sheet.Name}' with {len(var_names)} variables: "
                    f"{names_str}. "
                    f"Reference in expressions as {usage}. "
                    f"Pass these as width/height/length values in create_sketch and pad_sketch."),
            data={"name": sheet.Name, "label": sheet.Label,
                  "variables": dict(variables)},
        )

    return _with_undo("Create Spreadsheet", do)


CREATE_SPREADSHEET = ToolDefinition(
    name="create_spreadsheet",
    description=(
        "Create a Spreadsheet with named variables (cell aliases) for parametric modeling. "
        "Variables are stored as named cells that can be edited in the Spreadsheet workbench. "
        "After creation, pass variable references as dimension values in create_sketch "
        "(e.g. width='Variables.length') and pad_sketch (e.g. length='Variables.height'). "
        "Supports formulas in cells (e.g. '=A1*2'). "
        "Alternative: use create_variable_set for a cleaner property-based approach."
    ),
    category="modeling",
    parameters=[
        ToolParam("variables", "object",
                  "Dict of variable names and values, e.g. {\"length\": 50, \"wall\": 2}"),
        ToolParam("label", "string", "Display label for the spreadsheet",
                  required=False, default="Variables"),
    ],
    handler=_handle_create_spreadsheet,
)


# ── set_expression ─────────────────────────────────────────

def _handle_set_expression(
    object_name: str,
    property_name: str,
    expression: str,
) -> ToolResult:
    """Bind an object property to an expression."""

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="",
                              error=f"Object '{object_name}' not found.{hint}")

        # Validate property exists — skip check for indexed/nested properties
        # like "Constraints[8]" or "Placement.Base.x" since hasattr doesn't
        # handle those.
        base_prop = property_name.split("[")[0].split(".")[0]
        if not hasattr(obj, base_prop):
            return ToolResult(
                success=False, output="",
                error=f"Object '{object_name}' has no property '{base_prop}'")

        # Clearing an expression
        if not expression or expression.strip() == "":
            obj.setExpression(property_name, None)
            doc.recompute()
            return ToolResult(
                success=True,
                output=f"Cleared expression on {object_name}.{property_name}",
                data={"name": object_name, "property": property_name},
            )

        # Validate expression before applying
        try:
            err = obj.setExpression(property_name, expression)
        except Exception as e:
            return ToolResult(
                success=False, output="",
                error=f"Invalid expression '{expression}' for "
                      f"{object_name}.{property_name}: {e}")

        doc.recompute()

        # Read back the computed value
        try:
            computed = getattr(obj, property_name)
            value_str = f" = {computed}"
        except Exception:
            value_str = ""

        return ToolResult(
            success=True,
            output=(f"Bound {object_name}.{property_name} to expression "
                    f"'{expression}'{value_str}"),
            data={"name": object_name, "property": property_name,
                  "expression": expression},
        )

    return _with_undo("Set Expression", do)


SET_EXPRESSION = ToolDefinition(
    name="set_expression",
    description=(
        "Bind an object property to an expression for parametric relationships. "
        "Use with create_variable_set to make models parametric. "
        "Examples: set_expression('Pad', 'Length', 'Variables.height') for pad length, "
        "set_expression('Sketch', 'Constraints[0]', 'Variables.width') for sketch constraints. "
        "Also supports formulas: 'Variables.length * 2'. "
        "Pass empty expression to clear the binding."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("property_name", "string",
                  "Property to bind (e.g. Length, Width, Height, Radius)"),
        ToolParam("expression", "string",
                  "Expression string (e.g. 'Variables.length', 'Variables.wall * 2'). "
                  "Empty string clears the expression."),
    ],
    handler=_handle_set_expression,
)


# ── modify_property ─────────────────────────────────────────

def _resolve_relative_value(current, expr: str):
    """Resolve a relative expression against a current numeric value.

    Supports: "+10%", "-20%", "*1.5", "+5", "-3", or absolute values.
    Returns the resolved value, or the expression unchanged if not numeric.
    """
    if not isinstance(expr, str):
        return expr
    expr = expr.strip()
    if not expr:
        return expr

    try:
        current_float = float(current)
    except (TypeError, ValueError):
        return expr  # Current value isn't numeric — can't do relative

    # Percentage: "+10%", "-20%"
    if expr.endswith("%"):
        try:
            pct = float(expr[:-1])
            return current_float * (1 + pct / 100)
        except ValueError:
            pass

    # Multiply: "*1.5", "*2"
    if expr.startswith("*"):
        try:
            factor = float(expr[1:])
            return current_float * factor
        except ValueError:
            pass

    # Add/subtract: "+5", "-3"
    if expr.startswith("+") or (expr.startswith("-") and len(expr) > 1):
        try:
            delta = float(expr)
            return current_float + delta
        except ValueError:
            pass

    return expr


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

        current = getattr(obj, property_name)
        resolved = _resolve_relative_value(current, value)

        # Report old→new for relative changes
        if resolved != value:
            msg = f"Set {object_name}.{property_name} = {resolved} (was {current}, applied {value})"
        else:
            msg = f"Set {object_name}.{property_name} = {resolved}"

        setattr(obj, property_name, resolved)
        return ToolResult(
            success=True,
            output=msg,
            data={"name": object_name, "property": property_name,
                  "value": resolved, "previous": current},
        )

    return _with_undo("Modify Property", do)


MODIFY_PROPERTY = ToolDefinition(
    name="modify_property",
    description=(
        "Modify a property on a document object (e.g. Length, Width, Height, Radius). "
        "Values can be absolute (50) or relative expressions: "
        "'+10%' (increase by 10%), '-20%' (decrease by 20%), '*1.5' (multiply by 1.5), "
        "'+5' (add 5mm), '-3' (subtract 3mm)."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the object"),
        ToolParam("property_name", "string", "Name of the property to modify"),
        ToolParam("value", "string",
                  "New value or relative expression (e.g. 50, '+10%', '*1.5', '+5')"),
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
    from ..core.active_document import resolve_active_document
    from ..core.dangerous_mode import get_dangerous_mode

    result = execute_code(code, skip_safety=get_dangerous_mode().active)
    if result.success:
        output = result.stdout.strip() if result.stdout.strip() else "Code executed successfully"
        doc = resolve_active_document()
        data = {"stdout": result.stdout}
        if doc:
            data["document"] = doc.Name
        return ToolResult(success=True, output=output, data=data)
    else:
        return ToolResult(success=False, output=result.stdout, error=result.stderr)


EXECUTE_CODE = ToolDefinition(
    name="execute_code",
    description="Execute arbitrary Python code in FreeCAD's interpreter. LAST-RESORT fallback for operations no structured tool covers — check the tool list first. Do NOT use it to re-implement a covered operation (e.g. attaching a sketch to a face is `create_sketch`, not a hand-written AttachmentSupport macro). The code has access to FreeCAD, Part, PartDesign, Sketcher, Draft modules. Each call runs in a fresh namespace — variables, imports, and objects do NOT persist between calls, so make every call fully self-contained (re-fetch objects with getObject/getObjectsByLabel each time; a NameError means you referenced a name from an earlier call, not that your query was wrong).",
    category="general",
    parameters=[
        ToolParam("code", "string", "Python code to execute"),
    ],
    handler=_handle_execute_code,
)


# ── run_macro ────────────────────────────────────────────────

def _macro_allowed_dirs() -> list:
    """Enumerable macro dirs for safe-mode resolution."""
    dirs = []
    try:
        import FreeCAD as App
        d = App.getUserMacroDir(True)  # True = create if missing
        if d:
            dirs.append(d)
    except Exception:
        pass
    try:
        from ..config import USER_TOOLS_DIR
        dirs.append(USER_TOOLS_DIR)
    except Exception:
        pass
    return dirs


def _active_doc_dir() -> "str | None":
    try:
        import FreeCAD as App
        doc = App.ActiveDocument
        fn = getattr(doc, "FileName", "") if doc else ""
        return os.path.dirname(fn) if fn else None
    except Exception:
        return None


def _handle_run_macro(macro: str) -> ToolResult:
    """Run an existing FreeCAD macro file and return its console output."""
    from ..core.dangerous_mode import get_dangerous_mode
    from .macro_runner import resolve_macro_path

    dangerous = get_dangerous_mode().active
    path, err = resolve_macro_path(
        macro, _macro_allowed_dirs(), dangerous=dangerous,
        active_doc_dir=_active_doc_dir(),
    )
    if err:
        return ToolResult(success=False, output="", error=err)
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return ToolResult(success=False, output="", error=f"Could not read macro: {e}")

    result = execute_code(code, skip_safety=dangerous)
    if result.success:
        out = result.stdout.strip() or f"Macro '{macro}' ran successfully (no output)."
        return ToolResult(success=True, output=out,
                          data={"macro": path, "stdout": result.stdout})
    return ToolResult(success=False, output=result.stdout, error=result.stderr)


RUN_MACRO = ToolDefinition(
    name="run_macro",
    description=(
        f"Run an EXISTING {APP_NAME} macro file and return its console output "
        "(stdout/stderr). Use this to execute a macro the user already has on "
        "disk, e.g. a test harness. In normal mode, pass a bare macro NAME "
        f"(without extension) that lives in {APP_NAME}'s macro directory; file "
        "paths are refused unless the user has enabled Dangerous mode. Use "
        "execute_code instead when you want to run code you are writing inline."
    ),
    category="general",
    parameters=[
        ToolParam("macro", "string",
                  "Macro name (normal mode) or file path (Dangerous mode only)."),
    ],
    handler=_handle_run_macro,
)


# ── undo ────────────────────────────────────────────────────

def _handle_undo(steps: int = 1, until: str = "") -> ToolResult:
    """Undo operations. Either N steps or until a named transaction is reached."""
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

    # Get undo stack names for context
    undo_names = doc.UndoNames if hasattr(doc, "UndoNames") else []

    if until:
        # Undo until we find the named transaction
        query = until.lower()
        found = False
        for i, name in enumerate(undo_names):
            if query in name.lower():
                steps = i + 1
                found = True
                break
        if not found:
            stack_str = ", ".join(undo_names[:10])
            return ToolResult(
                success=False, output="",
                error=f"Transaction '{until}' not found in undo stack. "
                      f"Recent: {stack_str}")

    actual = min(steps, available)
    undone_names = list(undo_names[:actual])
    for i in range(actual):
        doc.undo()
    doc.recompute()

    # Show what was undone and what's left
    remaining = doc.UndoCount
    redo_count = doc.RedoCount if hasattr(doc, "RedoCount") else 0
    output = f"Undid {actual} operation(s): {', '.join(undone_names)}"
    if remaining > 0:
        output += f"\n{remaining} more undo(s) available"
    if redo_count > 0:
        output += f" | {redo_count} redo(s) available"

    return ToolResult(
        success=True,
        output=output,
        data={"steps": actual, "undone": undone_names},
    )


UNDO = ToolDefinition(
    name="undo",
    description=(
        "Undo operations. Use steps=N to undo N operations, or "
        "until='name' to undo back to a named transaction (e.g. 'Pad Sketch'). "
        "Returns what was undone and how many undo/redo steps remain."
    ),
    category="general",
    parameters=[
        ToolParam("steps", "integer", "Number of operations to undo", required=False, default=1),
        ToolParam("until", "string",
                  "Undo until this transaction name is reached (substring match). "
                  "Overrides steps.", required=False, default=""),
    ],
    handler=_handle_undo,
)


def _handle_redo(steps: int = 1) -> ToolResult:
    """Redo previously undone operations."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    available = doc.RedoCount if hasattr(doc, "RedoCount") else 0
    if available == 0:
        return ToolResult(
            success=False, output="",
            error="Nothing to redo (redo stack is empty)"
        )

    redo_names = doc.RedoNames if hasattr(doc, "RedoNames") else []
    actual = min(steps, available)
    redone_names = list(redo_names[:actual])
    for i in range(actual):
        doc.redo()
    doc.recompute()

    return ToolResult(
        success=True,
        output=f"Redid {actual} operation(s): {', '.join(redone_names)}",
        data={"steps": actual, "redone": redone_names},
    )


REDO = ToolDefinition(
    name="redo",
    description="Redo previously undone operations.",
    category="general",
    parameters=[
        ToolParam("steps", "integer", "Number of operations to redo", required=False, default=1),
    ],
    handler=_handle_redo,
)


def _handle_undo_history() -> ToolResult:
    """Show the undo/redo stack."""
    import FreeCAD as App

    doc = App.ActiveDocument
    if not doc:
        return ToolResult(success=False, output="", error="No active document")

    undo_names = list(doc.UndoNames) if hasattr(doc, "UndoNames") else []
    redo_names = list(doc.RedoNames) if hasattr(doc, "RedoNames") else []

    lines = []
    if undo_names:
        lines.append(f"**Undo stack ({len(undo_names)}):** (most recent first)")
        for i, name in enumerate(undo_names):
            lines.append(f"  {i + 1}. {name}")
    else:
        lines.append("Undo stack is empty.")

    if redo_names:
        lines.append(f"**Redo stack ({len(redo_names)}):**")
        for i, name in enumerate(redo_names):
            lines.append(f"  {i + 1}. {name}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        data={"undo": undo_names, "redo": redo_names},
    )


UNDO_HISTORY = ToolDefinition(
    name="undo_history",
    description=(
        "Show the undo/redo stack with named transactions. Use this to "
        "see what can be undone or redone before calling undo/redo."
    ),
    category="query",
    parameters=[],
    handler=_handle_undo_history,
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

    def _add_rect(sketch, x, y, w, h):
        """Add a closed rectangle to the sketch at (x, y) with size (w, h)."""
        g = sketch.GeometryCount
        sketch.addGeometry(Part.LineSegment(App.Vector(x, y, 0), App.Vector(x + w, y, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x + w, y, 0), App.Vector(x + w, y + h, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x + w, y + h, 0), App.Vector(x, y + h, 0)))
        sketch.addGeometry(Part.LineSegment(App.Vector(x, y + h, 0), App.Vector(x, y, 0)))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g, 2, g + 1, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 1, 2, g + 2, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 2, 2, g + 3, 1))
        sketch.addConstraint(Sketcher.Constraint("Coincident", g + 3, 2, g, 1))

    def do(doc):
        body = _get_object(doc, body_name)
        if not body:
            hint = _suggest_similar(doc, body_name, "Body")
            return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

        T = wall_thickness
        rw = ridge_width

        sketch = body.newObject("Sketcher::SketchObject", label + "Sketch")
        xy_plane = _get_body_plane(body, "XY")
        if xy_plane:
            sketch.AttachmentSupport = [(xy_plane, "")]
        sketch.MapMode = "FlatFace"
        sketch.AttachmentOffset = App.Placement(
            App.Vector(0, 0, z_position), App.Rotation())

        # Outer rectangle = inner wall of enclosure
        _add_rect(sketch, T, T, length - 2 * T, width - 2 * T)
        # Inner rectangle = inset by ridge_width (creates ring shape)
        _add_rect(sketch, T + rw, T + rw, length - 2 * T - 2 * rw, width - 2 * T - 2 * rw)

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
    clearance: float = 1.0,
    lip_height: float = 3.0,
    tab_width: float = 3.0,
    tab_height: float = 1.0,
    protrusion: float = 0.5,
    label: str = "SnapTab",
) -> ToolResult:
    """Add snap tabs on the outside of a rectangular lip that catch on an inner ridge.

    Creates PartDesign::AdditiveBox features inside the lid body so tabs
    remain editable and compatible with PartDesign tools (fillet, chamfer,
    pattern, etc.).
    """
    import FreeCAD as App

    def do(doc):
        body = _get_object(doc, body_name)
        if not body:
            hint = _suggest_similar(doc, body_name, "Body")
            return ToolResult(success=False, output="", error=f"Body '{body_name}' not found.{hint}")

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

        # All positions are in body-local coordinates (AdditiveBox
        # placement is relative to the body, not global).
        # Lip outer edges
        lip_x1 = T + cl
        lip_x2 = length - T - cl
        lip_y1 = T + cl
        lip_y2 = width - T - cl
        lip_cx = (lip_x1 + lip_x2) / 2
        lip_cy = (lip_y1 + lip_y2) / 2

        # Tab Z: at the bottom of the lip, with a gap below the ridge.
        # Shorten tab by 0.3mm so it doesn't touch the ridge above.
        snap_gap = 0.3
        th = tab_height - snap_gap  # effective tab height
        p = actual_protrusion

        # Define tab positions: (x, y, z, sx, sy, sz, side_label)
        # x/y/z = corner of the box (not center)
        tabs = []
        third = (lip_x2 - lip_x1) / 3

        # Long sides (front y=lip_y1, back y=lip_y2) — 2 tabs each
        for i, x_center in enumerate([lip_x1 + third, lip_x1 + 2 * third]):
            # Front wall tab: protrudes in -Y direction
            tabs.append((
                x_center - tab_width / 2, lip_y1 - p, 0,
                tab_width, p, th, f"Front{i + 1}"))
            # Back wall tab: protrudes in +Y direction
            tabs.append((
                x_center - tab_width / 2, lip_y2, 0,
                tab_width, p, th, f"Back{i + 1}"))

        # Short sides (left x=lip_x1, right x=lip_x2) — 1 tab each
        tabs.append((
            lip_x1 - p, lip_cy - tab_width / 2, 0,
            p, tab_width, th, "Left"))
        tabs.append((
            lip_x2, lip_cy - tab_width / 2, 0,
            p, tab_width, th, "Right"))

        # Create each tab as an AdditiveBox inside the body
        tab_names = []
        for (bx, by, bz, sx, sy, sz, side) in tabs:
            tab_label = f"{label}_{side}"
            box = body.newObject("PartDesign::AdditiveBox", tab_label)
            box.Length = sx
            box.Width = sy
            box.Height = sz
            box.Placement.Base = App.Vector(bx, by, bz)
            tab_names.append(box.Name)

        return ToolResult(
            success=True,
            output=f"Added {len(tabs)} snap tabs to '{body_name}' (protrusion={actual_protrusion:.1f}mm) as PartDesign features.",
            data={"name": tab_names[-1], "label": label, "tab_count": len(tabs),
                  "tab_names": tab_names},
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
        ToolParam("clearance", "number", "Gap between lip and wall (mm)", required=False, default=1.0),
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

        raw_refs = _coerce_str_list(faces) or ["Face1"]
        join_map = {"Arc": 0, "Intersection": 1}

        # If obj IS a Body, use its Tip (last feature) as the shell base
        body = None
        base_feature = obj
        if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
            body = obj
            base_feature = obj.Tip
            if not base_feature:
                return ToolResult(success=False, output="",
                                  error=f"Body '{obj.Label}' has no features to shell.")
        else:
            body = _find_body_for(doc, obj)

        # Resolve filter keywords
        face_refs = _resolve_face_refs(obj.Shape, raw_refs)
        if not face_refs:
            return ToolResult(success=False, output="",
                              error=f"No faces match filter {raw_refs} on '{obj.Label}'.")

        if body:
            shell = body.newObject("PartDesign::Thickness", label or "Shell")
            shell.Base = (base_feature, face_refs)
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
    description=(
        "Hollow out a solid by removing selected faces and applying a wall thickness "
        "(PartDesign::Thickness). Faces can be explicit names (Face1, Face6) or filter "
        "keywords: 'top', 'bottom', 'front', 'back', 'left', 'right', 'cylindrical'."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Internal name of the solid object to shell"),
        ToolParam("faces", "array",
                  "Face references or filter keywords, e.g. ['top'], ['Face1', 'Face6']",
                  required=False, items={"type": "string"}),
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

    Also handles common LLM naming mistakes:
      - "Sketch0" → "Sketch" (first object has no numeric suffix)
      - "Sketch1" → "Sketch001" (FreeCAD uses zero-padded 3-digit suffixes)
      - "Body0" → "Body", "Body1" → "Body001", etc.
    """
    obj = doc.getObject(name_or_label)
    if obj:
        return obj
    # Fallback: search by Label
    for o in doc.Objects:
        if o.Label == name_or_label:
            return o

    # Try common LLM naming variants (e.g. "Sketch0" → "Sketch",
    # "Sketch1" → "Sketch001", "Pad2" → "Pad002")
    import re
    m = re.match(r'^(.+?)(\d+)$', name_or_label)
    if m:
        base, num_str = m.group(1), m.group(2)
        num = int(num_str)
        variants = []
        if num == 0:
            # "Sketch0" → try "Sketch" (first object has no suffix)
            variants.append(base)
        else:
            # "Sketch1" → try "Sketch001"; "Sketch12" → try "Sketch012"
            variants.append(f"{base}{num:03d}")
        # Also try without leading zeros: "Sketch001" when given "Sketch1"
        if len(num_str) == 1 and num > 0:
            variants.append(f"{base}0{num_str}")  # e.g. "Sketch01"
        for variant in variants:
            obj = doc.getObject(variant)
            if obj:
                return obj
            for o in doc.Objects:
                if o.Label == variant:
                    return o

    return None


def _suggest_similar(doc, name_or_label, type_filter=None):
    """Return a hint string listing objects with similar names.

    Args:
        doc: FreeCAD document
        name_or_label: The name that was not found
        type_filter: Optional TypeId substring to filter (e.g. "Sketcher" or "Body")
    """
    import re
    # Extract the base name (letters) for matching
    base = re.match(r'^[A-Za-z_]+', name_or_label)
    base_str = base.group(0).lower() if base else ""

    candidates = []
    for o in doc.Objects:
        if type_filter and type_filter not in o.TypeId:
            continue
        # Match by base name similarity
        o_base = re.match(r'^[A-Za-z_]+', o.Name)
        o_base_str = o_base.group(0).lower() if o_base else ""
        if base_str and o_base_str == base_str:
            candidates.append(o.Name)
        elif base_str and base_str in o.Label.lower():
            candidates.append(o.Name)

    if not candidates:
        # No base-name match — list all objects of that type
        for o in doc.Objects:
            if type_filter and type_filter not in o.TypeId:
                continue
            # Skip internal objects like Origin, axes, planes
            if o.TypeId.startswith("App::"):
                continue
            candidates.append(o.Name)

    if candidates:
        return f" Available: {', '.join(candidates[:8])}"
    return ""


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


# ── report_skill_params ────────────────────────────────────

_reported_skill_params = None


def get_reported_skill_params():
    """Get the last reported skill params, or None."""
    return _reported_skill_params


def clear_reported_skill_params():
    """Clear stored skill params."""
    global _reported_skill_params
    _reported_skill_params = None


def _handle_report_skill_params(params: dict) -> ToolResult:
    """Store skill parameters for validation."""
    global _reported_skill_params
    _reported_skill_params = dict(params)
    return ToolResult(
        success=True,
        output=f"Skill parameters recorded: {', '.join(f'{k}={v}' for k, v in params.items())}",
    )


REPORT_SKILL_PARAMS = ToolDefinition(
    name="report_skill_params",
    description="Report the parameters used for the current skill execution. Call this after completing a skill so the system can validate the result.",
    parameters=[
        ToolParam("params", "object", "Dict of parameter names and values used (e.g., {\"L\": 100, \"W\": 80})", required=True),
    ],
    handler=_handle_report_skill_params,
    category="query",
)


# ── use_skill ──────────────────────────────────────────────

def _handle_use_skill(name: str, args: str = "", resource: str = "") -> ToolResult:
    """Load a skill's instructions (or one of its reference files) for the model.

    With `resource` set, returns the contents of that reference file (tier-3
    progressive disclosure — the key indexes a pre-scanned allowlist, never a
    filesystem path). Otherwise returns SKILL.md plus a manifest of any
    references the skill bundles. If the exact name isn't found, a fuzzy search
    on skill names and descriptions is attempted.
    """
    from ..extensions.skills import SkillsRegistry
    registry = SkillsRegistry()

    if resource:
        res = registry.get_skill_resource(name, resource)
        if "error" in res:
            return ToolResult(success=False, output="", error=res["error"])
        return ToolResult(success=True, output=res["output"])

    result = registry.execute_skill(name, args)

    if "error" in result:
        # Fuzzy match: search skill names and descriptions
        query = name.lower()
        matches = []
        for skill in registry.get_available():
            if query in skill.name.lower() or query in skill.description.lower():
                matches.append(skill)
        if len(matches) == 1:
            # Exactly one match — use it
            result = registry.execute_skill(matches[0].name, args)
        elif matches:
            names = [s.name for s in matches]
            return ToolResult(
                success=False, output="",
                error=f"Skill '{name}' not found. Did you mean: {', '.join(names)}?")
        else:
            available = [s.name for s in registry.get_available()]
            return ToolResult(
                success=False, output="",
                error=f"Skill '{name}' not found. Available: {', '.join(available)}")

    if "inject_prompt" in result:
        content = result["inject_prompt"]
        if args:
            content += f"\n\nUser request: {args}"
        return ToolResult(success=True, output=content)

    if "output" in result:
        return ToolResult(success=True, output=result["output"])

    return ToolResult(success=False, output="", error="Skill returned no content")


USE_SKILL = ToolDefinition(
    name="use_skill",
    description=(
        "Load a skill's detailed instructions for a complex task. "
        "Skills provide step-by-step construction guides (e.g. enclosure, gear). "
        "Call this when the user's request matches a skill, then follow the "
        "returned instructions using your tools. If the skill lists 'Available "
        "references', pull one into context on demand by calling use_skill again "
        "with the same name and the reference's `resource` key."
    ),
    parameters=[
        ToolParam("name", "string",
                  "Skill name (e.g. 'enclosure', 'gear', 'fastener-hole')"),
        ToolParam("args", "string",
                  "User's parameters for the skill (e.g. '120x80x60mm, screw lid')",
                  required=False, default=""),
        ToolParam("resource", "string",
                  "Optional reference key from the skill's 'Available references' "
                  "list, to load that reference file instead of the skill itself",
                  required=False, default=""),
    ],
    handler=_handle_use_skill,
    category="query",
)


# ── Assembly tools ──────────────────────────────────────────


def _setup_assembly_imports():
    """Import Assembly workbench modules. Call inside FreeCAD context only."""
    import FreeCAD as App
    import sys
    sys.path.insert(0, App.getHomePath() + "Mod/Assembly")
    import JointObject
    import UtilsAssembly
    return JointObject, UtilsAssembly


def _get_joint_group(asm):
    """Get or create the JointGroup inside an assembly."""
    for child in asm.Group:
        if child.TypeId == "Assembly::JointGroup":
            return child
    return asm.newObject("Assembly::JointGroup", "Joints")


def _handle_create_assembly(
    label: str = "Assembly",
    part_names: list | None = None,
    ground_first: bool = True,
) -> ToolResult:
    """Create an Assembly and optionally add existing bodies/parts to it."""
    import FreeCAD as App

    part_names = _coerce_str_list(part_names) or []

    def do(doc):
        JointObject, _ = _setup_assembly_imports()

        asm = doc.addObject("Assembly::AssemblyObject", label)
        asm.Label = label
        jg = asm.newObject("Assembly::JointGroup", "Joints")

        added = []
        errors = []
        for pname in part_names:
            obj = _get_object(doc, pname)
            if obj:
                asm.addObject(obj)
                added.append(obj)
            else:
                errors.append(f"'{pname}' not found")

        # Ground the first part so the solver has a fixed reference frame
        if ground_first and added:
            ground = jg.newObject("App::FeaturePython", "GroundedJoint")
            JointObject.GroundedJoint(ground, added[0])
            if ground.ViewObject and hasattr(JointObject, "ViewProviderGroundedJoint"):
                JointObject.ViewProviderGroundedJoint(ground.ViewObject)

        labels = [o.Label for o in added]
        parts_str = [f"  - {l}" for l in labels]
        msg = f"Created assembly '{asm.Name}' (label: '{asm.Label}')."
        if parts_str:
            msg += f"\nAdded {len(added)} part(s):\n" + "\n".join(parts_str)
        if ground_first and added:
            msg += f"\nGrounded '{added[0].Label}' (fixed reference frame)."
        if errors:
            msg += f"\nWarnings: {', '.join(errors)}"
        msg += f"\nUse assembly_name='{asm.Name}' for add_assembly_joint."

        return ToolResult(
            success=True, output=msg,
            data={"name": asm.Name, "label": asm.Label, "parts": labels},
        )

    return _with_undo("Create Assembly", do)


CREATE_ASSEMBLY = ToolDefinition(
    name="create_assembly",
    description=(
        "Create an Assembly container and optionally add existing bodies/parts to it. "
        "An assembly groups parts and allows positioning them relative to each other "
        "using joints (via add_assembly_joint). The first part is grounded (fixed in place) "
        "by default. Create the assembly first, then add joints."
    ),
    category="modeling",
    parameters=[
        ToolParam("label", "string", "Display label for the assembly",
                  required=False, default="Assembly"),
        ToolParam("part_names", "array",
                  "List of body/part names to add to the assembly",
                  required=False, items={"type": "string"}),
        ToolParam("ground_first", "boolean",
                  "Ground (fix in place) the first part as reference frame (default: true)",
                  required=False, default=True),
    ],
    handler=_handle_create_assembly,
)


def _find_sub_name(part, face_str):
    """Build the sub-element path for a face on a body (e.g. 'Box1.Face6').

    For PartDesign bodies, the face belongs to the tip feature.
    """
    if hasattr(part, "Tip") and part.Tip:
        return f"{part.Tip.Name}.{face_str}"
    return face_str



def _handle_add_assembly_joint(
    assembly_name: str,
    part1_name: str,
    face1: str,
    part2_name: str,
    face2: str,
    joint_type: str = "Fixed",
    label: str = "",
) -> ToolResult:
    """Add a joint between two faces and use the native solver to position parts."""
    import FreeCAD as App

    def do(doc):
        JointObject, UtilsAssembly = _setup_assembly_imports()

        asm = _get_object(doc, assembly_name)
        if not asm or asm.TypeId != "Assembly::AssemblyObject":
            return ToolResult(
                success=False, output="",
                error=f"Assembly '{assembly_name}' not found. Create one with create_assembly first.",
            )

        part1 = _get_object(doc, part1_name)
        part2 = _get_object(doc, part2_name)
        if not part1:
            return ToolResult(success=False, output="", error=f"Part '{part1_name}' not found.")
        if not part2:
            return ToolResult(success=False, output="", error=f"Part '{part2_name}' not found.")

        # Ensure parts are in the assembly
        asm_children = set(o.Name for o in asm.Group)
        for part in (part1, part2):
            if part.Name not in asm_children:
                asm.addObject(part)

        jg = _get_joint_group(asm)

        # Create the joint
        type_map = {
            "Fixed": 0, "Revolute": 1, "Cylindrical": 2, "Slider": 3,
            "Ball": 4, "Distance": 5, "Parallel": 6, "Perpendicular": 7,
            "Angle": 8,
        }
        type_idx = type_map.get(joint_type, 0)

        joint_label = label or f"{joint_type}_{part1.Label}_{part2.Label}"
        joint = jg.newObject("App::FeaturePython", joint_label)
        JointObject.Joint(joint, type_idx)
        if joint.ViewObject and hasattr(JointObject, "ViewProviderJoint"):
            JointObject.ViewProviderJoint(joint.ViewObject)

        # Set references: (body, ["Tip.FaceN", "Tip.FaceN"])
        # Duplicating the face sub-name means "use face center" (not a specific vertex)
        sub1 = _find_sub_name(part1, face1)
        sub2 = _find_sub_name(part2, face2)
        joint.Reference1 = (part1, [sub1, sub1])
        joint.Reference2 = (part2, [sub2, sub2])

        # Compute placements using the Assembly workbench's own function
        # This handles all geometry types (planar, cylindrical, conical, etc.)
        joint.Placement1 = UtilsAssembly.findPlacement(joint.Reference1)
        joint.Placement2 = UtilsAssembly.findPlacement(joint.Reference2)

        # Pre-position the moving part before solving (replicates GUI's preSolve).
        # preSolve checks JCS orientation and flips if needed for face-to-face contact.
        joint.Proxy.preSolve(joint)

        # Let the native C++ solver position the parts
        doc.recompute()
        solve_result = asm.solve()

        pos = part2.Placement.Base
        msg = (
            f"Created {joint_type} joint '{joint.Label}' between "
            f"'{part1.Label}.{face1}' and '{part2.Label}.{face2}'.\n"
            f"Solver result: {solve_result} (0=OK).\n"
            f"'{part2.Label}' positioned at ({pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f})."
        )

        return ToolResult(
            success=True, output=msg,
            data={"joint_name": joint.Name, "part2_position": [pos.x, pos.y, pos.z]},
        )

    return _with_undo("Add Assembly Joint", do)


ADD_ASSEMBLY_JOINT = ToolDefinition(
    name="add_assembly_joint",
    description=(
        "Add a joint between two parts in an assembly by specifying which faces to mate. "
        "The second part is repositioned so its face meets the first part's face. "
        "Use list_faces first to check face normals and positions.\n"
        "FACE SELECTION GUIDE:\n"
        "- Fixed (stacking): use top face of base + bottom face of part (e.g. Face6+Face5 for boxes)\n"
        "- Fixed (side-by-side): use right face of part1 + left face of part2\n"
        "- Revolute (hinge): use a SIDE face of the mount + an END face of the arm, "
        "so the arm extends outward and rotates around the face normal\n"
        "- Cylindrical: use the curved face (Face1) of a cylinder + a hole face\n"
        "- Ball: REQUIRES spherical geometry — one part needs an additive sphere (ball), "
        "the other a subtractive sphere (socket). Reference the spherical faces.\n"
        "IMPORTANT: The rotation axis of Revolute/Cylindrical joints is the face normal. "
        "For a horizontal hinge, connect vertical side faces (normal along X or Y). "
        "For a vertical turntable, connect horizontal faces (normal along Z)."
    ),
    category="modeling",
    parameters=[
        ToolParam("assembly_name", "string", "Name of the assembly (from create_assembly)"),
        ToolParam("part1_name", "string", "Name of the first (reference) part/body"),
        ToolParam("face1", "string", "Face name on part1 (e.g. 'Face6')"),
        ToolParam("part2_name", "string", "Name of the second part/body to position"),
        ToolParam("face2", "string", "Face name on part2 to mate with face1 (e.g. 'Face1')"),
        ToolParam("joint_type", "string",
                  "Joint type: Fixed (default), Revolute, Cylindrical, Slider, Ball",
                  required=False, default="Fixed"),
        ToolParam("label", "string", "Optional label for the joint",
                  required=False, default=""),
    ],
    handler=_handle_add_assembly_joint,
)


def _handle_add_part_to_assembly(
    assembly_name: str,
    part_name: str,
    position: list | None = None,
) -> ToolResult:
    """Add a part/body to an existing assembly, optionally at a given position."""
    import FreeCAD as App
    from FreeCAD import Placement, Vector, Rotation

    position = _coerce_str_list(position)

    def do(doc):
        asm = _get_object(doc, assembly_name)
        if not asm or asm.TypeId != "Assembly::AssemblyObject":
            return ToolResult(
                success=False, output="",
                error=f"Assembly '{assembly_name}' not found.",
            )

        obj = _get_object(doc, part_name)
        if not obj:
            return ToolResult(success=False, output="", error=f"Part '{part_name}' not found.")

        asm.addObject(obj)

        if position and len(position) >= 3:
            obj.Placement = Placement(
                Vector(float(position[0]), float(position[1]), float(position[2])),
                Rotation(),
            )

        msg = f"Added '{obj.Label}' to assembly '{asm.Label}'."
        if position:
            msg += f" Positioned at ({position[0]}, {position[1]}, {position[2]})."

        return ToolResult(
            success=True, output=msg,
            data={"name": obj.Name, "label": obj.Label},
        )

    return _with_undo("Add Part to Assembly", do)


ADD_PART_TO_ASSEMBLY = ToolDefinition(
    name="add_part_to_assembly",
    description=(
        "Add an existing body/part to an assembly, optionally setting its position. "
        "Use this to add parts that weren't included in create_assembly, "
        "or to reposition parts before adding joints."
    ),
    category="modeling",
    parameters=[
        ToolParam("assembly_name", "string", "Name of the assembly"),
        ToolParam("part_name", "string", "Name of the body/part to add"),
        ToolParam("position", "array",
                  "Optional [x, y, z] position for the part",
                  required=False, items={"type": "number"}),
    ],
    handler=_handle_add_part_to_assembly,
)


# ── All tools ───────────────────────────────────────────────

ALL_TOOLS = [
    CREATE_PRIMITIVE,
    CREATE_BODY,
    CREATE_SKETCH,
    CREATE_DATUM_PLANE,
    CREATE_DATUM_LINE,
    EDIT_SKETCH,
    PAD_SKETCH,
    POCKET_SKETCH,
    REVOLVE_SKETCH,
    LOFT_SKETCHES,
    SWEEP_SKETCH,
    BOOLEAN_OPERATION,
    TRANSFORM_OBJECT,
    DUPLICATE_OBJECT,
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
    DESCRIBE_MODEL,
    LIST_FACES,
    LIST_EDGES,
    LIST_DOCUMENTS,
    SWITCH_DOCUMENT,
    GET_DOCUMENT_STATE,
    CREATE_VARIABLE_SET,
    CREATE_SPREADSHEET,
    SET_EXPRESSION,
    MODIFY_PROPERTY,
    EXPORT_MODEL,
    EXECUTE_CODE,
    RUN_MACRO,
    UNDO,
    REDO,
    UNDO_HISTORY,
    CAPTURE_VIEWPORT,
    SET_VIEW,
    ZOOM_OBJECT,
    REPORT_SKILL_PARAMS,
    USE_SKILL,
    CREATE_ASSEMBLY,
    ADD_ASSEMBLY_JOINT,
    ADD_PART_TO_ASSEMBLY,
    SELECT_GEOMETRY,
]
