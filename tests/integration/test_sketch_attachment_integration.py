"""Integration tests for create_sketch face/plane/selection attachment.

Runs inside FreeCAD via the run_freecad_script fixture (see conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


class TestCreateSketchAttachment:
    def test_sketch_on_box_face_attaches(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_sketch

feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

r = _handle_create_sketch(support="Box", face="Face6")
doc.recompute()
sk = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "map_mode": sk.MapMode if sk else None,
    "has_support": bool(sk.AttachmentSupport) if sk else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["map_mode"] == "FlatFace"
        assert d["has_support"]

    def test_sketch_on_standalone_solid_face_is_standalone(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_sketch

feat = doc.addObject("Part::Feature", "Imported")
feat.Shape = Part.makeBox(8, 8, 8)
doc.recompute()

r = _handle_create_sketch(support="Imported", face="Face1")
doc.recompute()
sk = doc.getObject(r.data["name"]) if r.success else None
grp = sk.getParentGeoFeatureGroup() if sk else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "map_mode": sk.MapMode if sk else None,
    "in_body": grp.Name if grp else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["map_mode"] == "FlatFace"
        # A standalone Part::Feature is not a Body, so the sketch is standalone.
        assert d["in_body"] is None

    def test_sketch_on_datum_plane_by_name(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_sketch

body = doc.addObject("PartDesign::Body", "Body")
body.newObject("PartDesign::Plane", "DatumPlane")
doc.recompute()

r = _handle_create_sketch(support="DatumPlane")
doc.recompute()
sk = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "map_mode": sk.MapMode if sk else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["map_mode"] == "FlatFace"

    def test_non_planar_face_errors_cleanly(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_sketch

feat = doc.addObject("Part::Feature", "Cyl")
feat.Shape = Part.makeCylinder(5, 10)
doc.recompute()

# Find a non-planar (curved) face to target.
nonplanar = None
for i, f in enumerate(feat.Shape.Faces, start=1):
    if not isinstance(f.Surface, Part.Plane):
        nonplanar = "Face%d" % i
        break

r = _handle_create_sketch(support="Cyl", face=nonplanar)
results["data"] = {
    "success": r.success,
    "error": r.error,
    "targeted_face": nonplanar,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["targeted_face"] is not None  # cylinder has a curved face
        assert not d["success"]
        assert "planar" in d["error"].lower()

    def test_missing_support_errors(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_sketch

r = _handle_create_sketch(support="DoesNotExist", face="Face1")
results["data"] = {"success": r.success, "error": r.error}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "not found" in d["error"].lower()

    def test_origin_plane_still_works(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_body, _handle_create_sketch

_handle_create_body(label="Body")
doc.recompute()
r = _handle_create_sketch(plane="XZ", body_name="Body")
doc.recompute()
sk = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "map_mode": sk.MapMode if sk else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["map_mode"] == "FlatFace"

    def test_standalone_sketch_still_works(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_sketch

r = _handle_create_sketch(plane="XY")
results["data"] = {"success": r.success, "error": r.error}
""")
        assert result["ok"], result.get("error")
        assert result["data"]["success"], result["data"]["error"]

    def test_offset_on_face_sets_attachment_offset(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_sketch

feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

r = _handle_create_sketch(support="Box", face="Face6", offset=5.0)
doc.recompute()
sk = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {
    "success": r.success,
    "error": r.error,
    "offset_z": sk.AttachmentOffset.Base.z if sk else None,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert abs(d["offset_z"] - 5.0) < 1e-6

    def test_body_name_with_support_warns(self, run_freecad_script):
        result = run_freecad_script("""
import Part
from freecad_ai.tools.freecad_tools import _handle_create_body, _handle_create_sketch

_handle_create_body(label="Body")
feat = doc.addObject("Part::Feature", "Box")
feat.Shape = Part.makeBox(10, 10, 10)
doc.recompute()

r = _handle_create_sketch(support="Box", face="Face6", body_name="Body")
doc.recompute()
results["data"] = {"success": r.success, "error": r.error, "output": r.output}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert "ignored" in d["output"].lower()


class TestSketchOnBodyFace:
    """#168: support=<the Body itself> attached the sketch to its own container.

    The Body then depended on a sketch that depended on the Body ("The graph
    must be a DAG.") and the pocket built on it failed. A Body's shape is its
    Tip's shape, so the face names are the Tip's: attach there instead.
    """

    def test_body_support_attaches_to_the_tip_and_the_pocket_cuts(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import (
    _handle_create_body, _handle_create_sketch, _handle_pad_sketch, _handle_pocket_sketch)

_handle_create_body(label="Body")
r = _handle_create_sketch(body_name="Body", geometries=[
    {"type": "rectangle", "x": -15, "y": -10, "width": 30, "height": 20}])
_handle_pad_sketch(sketch_name=r.data["name"], length=8)
r = _handle_create_sketch(support="Body", face="Face6", label="Hole_Sketch",
                          geometries=[{"type": "circle", "cx": 0, "cy": 0, "radius": 3}])
sk = doc.getObject(r.data["name"]) if r.success else None
p = _handle_pocket_sketch(sketch_name=sk.Name, through_all=True) if sk else None
doc.recompute()
body = doc.getObject("Body")
results["data"] = {
    "success": r.success, "error": r.error, "output": r.output,
    "attached_to": [s[0].Name for s in sk.AttachmentSupport] if sk else None,
    "pocket_ok": bool(p and p.success),
    "volume": body.Shape.Volume,
    "body_valid": body.isValid(),
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["attached_to"] == ["Pad"]
        assert "Tip" in d["output"]  # the redirect is reported, not silent
        assert d["pocket_ok"]
        assert d["body_valid"]
        assert abs(d["volume"] - (30 * 20 * 8 - 3.141592653589793 * 9 * 8)) < 0.01

    @pytest.mark.parametrize("tool, kwargs", [
        ("_handle_create_datum_plane", {"support": "Body", "face": "Face6"}),
        ("_handle_create_datum_line", {"support": "Body", "edge": "Edge1"}),
    ])
    def test_datum_on_body_attaches_to_the_tip(self, run_freecad_script, tool, kwargs):
        result = run_freecad_script(f"""
from freecad_ai.tools import freecad_tools as T

T._handle_create_body(label="Body")
r = T._handle_create_sketch(body_name="Body", geometries=[
    {{"type": "rectangle", "x": 0, "y": 0, "width": 30, "height": 20}}])
T._handle_pad_sketch(sketch_name=r.data["name"], length=8)
r = T.{tool}(**{kwargs!r})
doc.recompute()
o = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {{
    "success": r.success, "error": r.error, "output": r.output,
    "attached_to": [s[0].Name for s in o.AttachmentSupport] if o else None,
    "body_valid": doc.getObject("Body").isValid(),
}}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["attached_to"] == ["Pad"]
        assert "Tip" in d["output"]
        assert d["body_valid"]
