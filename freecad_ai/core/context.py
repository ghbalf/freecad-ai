"""Document context inspector.

Captures the current FreeCAD document state (objects, properties,
selection, etc.) and formats it as text for inclusion in the LLM
system prompt.
"""


def get_document_context() -> str:
    """Return a text summary of the active FreeCAD document.

    Returns an empty string if no document is open.
    """
    try:
        import FreeCAD as App
    except ImportError:
        return "(FreeCAD not available — running outside FreeCAD)"

    doc = App.ActiveDocument
    if doc is None:
        return "No document is currently open."

    lines = []

    # Document info
    name = doc.Name
    path = doc.FileName or "(unsaved)"
    lines.append(f'Document: "{name}"')
    lines.append(f"File: {path}")

    # Active body / sketch
    try:
        import FreeCADGui as Gui
        active_view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
        active_body = _get_active_body()
        if active_body:
            lines.append(f"Active Body: {active_body.Label}")
    except Exception:
        active_body = None

    # Objects
    objects = doc.Objects
    if not objects:
        lines.append("Objects: (none)")
    else:
        lines.append(f"Objects ({len(objects)}):")
        # Build parent-child map
        children_of = {}
        has_parent = set()
        for obj in objects:
            kids = _get_children(obj)
            if kids:
                children_of[obj.Name] = kids
                for k in kids:
                    has_parent.add(k)

        # Print top-level objects with indented children
        for obj in objects:
            if obj.Name not in has_parent:
                _format_object(obj, lines, indent=1, children_of=children_of,
                               active_body=active_body, doc=doc)

    # Selection
    try:
        import FreeCADGui as Gui
        sel = Gui.Selection.getSelectionEx()
        if sel:
            sel_parts = []
            for s in sel:
                if s.SubElementNames:
                    for sub in s.SubElementNames:
                        sel_parts.append(f"{s.ObjectName}.{sub}")
                else:
                    sel_parts.append(s.ObjectName)
            lines.append(f"Selection: {', '.join(sel_parts)}")
    except Exception:
        pass

    return "\n".join(lines)


def _get_active_body():
    """Get the active PartDesign Body, if any."""
    try:
        import FreeCADGui as Gui
        if Gui.ActiveDocument:
            active_view = Gui.ActiveDocument.ActiveView
            if hasattr(active_view, "getActiveObject"):
                return active_view.getActiveObject("pdbody")
    except Exception:
        pass
    return None


def _get_children(obj) -> list[str]:
    """Get the names of child objects (e.g., features in a Body)."""
    children = []
    # PartDesign Body groups
    if hasattr(obj, "Group"):
        children = [o.Name for o in obj.Group]
    return children


def _format_object(obj, lines: list, indent: int, children_of: dict,
                   active_body, doc):
    """Format a single object and its children."""
    prefix = "  " * indent + "- "
    type_id = obj.TypeId if hasattr(obj, "TypeId") else type(obj).__name__
    label = obj.Label

    # Annotation for active body
    active_tag = ""
    if active_body and obj.Name == active_body.Name:
        active_tag = " [active]"

    # Key properties
    props = _get_key_properties(obj)
    props_str = ""
    if props:
        props_str = " — " + ", ".join(props)

    lines.append(f"{prefix}{label} ({type_id}){active_tag}{props_str}")

    # Children
    if obj.Name in children_of:
        for child_name in children_of[obj.Name]:
            child_obj = doc.getObject(child_name)
            if child_obj:
                _format_object(child_obj, lines, indent + 1, children_of,
                               active_body, doc)


def _get_key_properties(obj) -> list[str]:
    """Extract a few key properties from an object for display."""
    props = []
    type_id = getattr(obj, "TypeId", "")

    # Sketch info
    if "Sketcher" in type_id:
        try:
            geo_count = obj.GeometryCount
            constraint_count = len(obj.Constraints)
            support = ""
            if hasattr(obj, "Support") and obj.Support:
                support_ref = obj.Support[0]
                support = f"{support_ref[0].Label}"
            parts = [f"{geo_count} geometries", f"{constraint_count} constraints"]
            if support:
                parts.insert(0, support)
            props.extend(parts)
        except Exception:
            pass

    # Pad/Pocket length
    if "Pad" in type_id or "Pocket" in type_id:
        try:
            props.append(f"Length: {obj.Length}")
        except Exception:
            pass

    # Fillet/Chamfer size
    if "Fillet" in type_id:
        try:
            props.append(f"Radius: {obj.Radius}")
        except Exception:
            pass
    if "Chamfer" in type_id:
        try:
            props.append(f"Size: {obj.Size}")
        except Exception:
            pass

    # Revolution angle
    if "Revolution" in type_id:
        try:
            props.append(f"Angle: {obj.Angle}")
        except Exception:
            pass

    # Part primitives
    if "Part::Box" in type_id:
        try:
            props.append(f"{obj.Length}x{obj.Width}x{obj.Height}")
        except Exception:
            pass
    if "Part::Cylinder" in type_id:
        try:
            props.append(f"R={obj.Radius}, H={obj.Height}")
        except Exception:
            pass
    if "Part::Sphere" in type_id:
        try:
            props.append(f"R={obj.Radius}")
        except Exception:
            pass

    return props
