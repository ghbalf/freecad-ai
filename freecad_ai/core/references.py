"""Selection references for chat messages.

A reference captures the current FreeCAD selection — a whole object or a
single Face/Edge/Vertex sub-element — as a short text snapshot attached to
the user's message, so ``@Name`` tokens in the typed text resolve to
concrete geometry for the LLM.
"""

from .context import _get_key_properties


def references_from_selection() -> list[dict]:
    """Snapshot the current GUI selection into reference dicts.

    Returns one dict per selected whole object, or one per Face/Edge/Vertex
    sub-element when sub-elements are selected. Each dict has the keys
    ``name`` (internal object name), ``label`` (display label), ``sub``
    (sub-element name, or None) and ``text`` (short description).

    Returns an empty list outside FreeCAD or when nothing is selected.
    """
    try:
        import FreeCADGui as Gui
        sel = Gui.Selection.getSelectionEx()
    except Exception:
        return []

    refs = []
    for s in sel or []:
        obj_name = getattr(s, "ObjectName", "") or ""
        if not obj_name:
            continue
        obj = _get_selected_object(s, obj_name)
        if obj is None:
            continue
        label = getattr(obj, "Label", obj_name)
        subs = [sub for sub in (getattr(s, "SubElementNames", None) or [])
                if sub.startswith(("Face", "Edge", "Vertex"))]
        if subs:
            for sub in subs:
                refs.append({
                    "name": obj_name,
                    "label": label,
                    "sub": sub,
                    "text": describe_reference(obj, sub),
                })
        else:
            refs.append({
                "name": obj_name,
                "label": label,
                "sub": None,
                "text": describe_reference(obj),
            })
    return refs


def _get_selected_object(sel_obj, obj_name):
    """Return the FreeCAD object behind a GUI selection entry."""
    obj = getattr(sel_obj, "Object", None)
    if obj is not None:
        return obj
    try:
        from .active_document import resolve_active_document
        doc = resolve_active_document()
        if doc is not None:
            return doc.getObject(obj_name)
    except Exception:
        return None
    return None


def reference_token(ref: dict) -> str:
    """Display token for a reference, e.g. ``@Pad`` or ``@Pad.Face3``.

    Tokens use the object's immutable internal name — not the editable
    label — so they stay aligned with the chip dedupe key ``(name, sub)``
    and never go stale when an object is renamed. The label still reaches
    the LLM through the reference block's description text.
    """
    name = ref.get("name", "")
    sub = ref.get("sub") or ""
    return f"@{name}.{sub}" if sub else f"@{name}"


def format_reference_block(ref: dict) -> str:
    """Format one reference as a labeled text block for a user message."""
    return f"--- FreeCAD reference: {reference_token(ref)} ---\n{ref.get('text', '')}"


def describe_reference(obj, sub=None) -> str:
    """Return a short one-line description of an object or sub-element."""
    label = getattr(obj, "Label", None) or getattr(obj, "Name", "object")
    if sub:
        return _describe_sub_element(obj, label, sub)

    type_id = getattr(obj, "TypeId", "") or type(obj).__name__
    head = f"{label} ({type_id})"
    props = _get_key_properties(obj)
    segments = [head + (f" — {', '.join(props)}" if props else "")]
    try:
        base = obj.Placement.Base
        segments.append(f"pos ({base.x:.1f}, {base.y:.1f}, {base.z:.1f})")
    except Exception:
        pass
    try:
        bb = obj.Shape.BoundBox
        segments.append(f"bbox {bb.XLength:.1f}x{bb.YLength:.1f}x{bb.ZLength:.1f}")
    except Exception:
        pass
    return " | ".join(segments)


def _describe_sub_element(obj, label, sub) -> str:
    """Describe a Face/Edge/Vertex sub-element of an object."""
    shape = getattr(obj, "Shape", None)
    element = None
    if shape is not None:
        try:
            element = shape.getElement(sub)
        except Exception:
            element = None
    if element is None:
        return f"{sub} of {label}"
    if sub.startswith("Face"):
        return _describe_face(element, label, sub, shape)
    if sub.startswith("Edge"):
        return _describe_edge(element, label, sub, shape)
    if sub.startswith("Vertex"):
        return _describe_vertex(element, label, sub)
    return f"{sub} of {label}"


def _describe_face(face, label, sub, shape) -> str:
    """Describe a face: classification, area, center, normal (planar only)."""
    # Lazy import: freecad_tools pulls in the whole tool registry, and this
    # module is imported by conversation.py at startup.
    from ..tools.freecad_tools import _classify_face

    parts = []
    surface_name = ""
    try:
        surface_name = face.Surface.__class__.__name__
    except Exception:
        surface_name = ""
    classify = ""
    try:
        classify = _classify_face(face, shape.BoundBox)
    except Exception:
        classify = ""
    if surface_name == "Plane":
        parts.append(f"{classify} planar" if classify else "planar")
    elif classify:
        # e.g. "cylindrical (R=5.0)" — the classification carries the type
        parts.append(classify)
    elif surface_name:
        parts.append(f"{surface_name.lower()} face")
    try:
        parts.append(f"area {face.Area:.2f}")
    except Exception:
        pass
    try:
        c = face.CenterOfMass
        parts.append(f"center ({c.x:.1f}, {c.y:.1f}, {c.z:.1f})")
    except Exception:
        pass
    if surface_name == "Plane":
        try:
            n = face.normalAt(0, 0)
            parts.append(f"normal ({n.x:.2f}, {n.y:.2f}, {n.z:.2f})")
        except Exception:
            pass
    tail = ", ".join(parts)
    return f"{sub} of {label} — {tail}" if tail else f"{sub} of {label}"


def _describe_edge(edge, label, sub, shape) -> str:
    """Describe an edge: curve type, classification, length, endpoints."""
    from ..tools.freecad_tools import _classify_edge

    parts = []
    try:
        curve_name = edge.Curve.__class__.__name__
    except Exception:
        curve_name = ""
    if curve_name in ("Line", "LineSegment"):
        parts.append("Line")
    try:
        parts.append(_classify_edge(edge, shape.BoundBox))
    except Exception:
        pass
    try:
        parts.append(f"length {edge.Length:.2f}")
    except Exception:
        pass
    try:
        pts = [v.Point for v in edge.Vertexes]
        if len(pts) >= 2:
            p1, p2 = pts[0], pts[-1]
            parts.append(
                f"({p1.x:.1f}, {p1.y:.1f}, {p1.z:.1f})→"
                f"({p2.x:.1f}, {p2.y:.1f}, {p2.z:.1f})")
    except Exception:
        pass
    tail = ", ".join(parts)
    return f"{sub} of {label} — {tail}" if tail else f"{sub} of {label}"


def _describe_vertex(vertex, label, sub) -> str:
    """Describe a vertex by its point."""
    try:
        p = vertex.Point
        return f"{sub} of {label} — ({p.x:.1f}, {p.y:.1f}, {p.z:.1f})"
    except Exception:
        return f"{sub} of {label}"
