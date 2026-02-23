"""Integration tests for create_wedge tool (PartDesign loft-based wedge)."""

import pytest

pytestmark = pytest.mark.integration


class TestCreateWedge:
    def test_default_wedge(self, run_freecad_script):
        """Default wedge: 10x10x10, top tapers to ridge (top_width≈0)."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_wedge

r = _handle_create_wedge()
doc.recompute()
body = doc.getObject(r.data["body_name"])
feat = doc.getObject(r.data["name"])
results["data"] = {
    "success": r.success,
    "body_type": body.TypeId,
    "feat_type": feat.TypeId,
    "volume": body.Shape.Volume,
    "body_label": body.Label,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert d["body_type"] == "PartDesign::Body"
        assert d["feat_type"] == "PartDesign::AdditiveLoft"
        assert d["volume"] > 0
        assert d["body_label"] == "Wedge"

    def test_wedge_dimensions(self, run_freecad_script):
        """Wedge with specific dimensions has reasonable volume."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_wedge

r = _handle_create_wedge(length=50, width=30, height=20, top_length=50, top_width=30)
doc.recompute()
body = doc.getObject(r.data["body_name"])
results["data"] = {
    "success": r.success,
    "volume": body.Shape.Volume,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        # top == base → should be a box: 50*30*20 = 30000
        assert abs(d["volume"] - 30000.0) < 100.0

    def test_tapered_wedge_volume(self, run_freecad_script):
        """Tapered wedge has volume between 0 and full box."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_wedge

r = _handle_create_wedge(length=40, width=20, height=15, top_length=20, top_width=10)
doc.recompute()
body = doc.getObject(r.data["body_name"])
results["data"] = {
    "success": r.success,
    "volume": body.Shape.Volume,
    "full_box": 40 * 20 * 15,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert 0 < d["volume"] < d["full_box"]

    def test_wedge_with_label(self, run_freecad_script):
        """Custom label is applied to body (feature may get suffixed by FreeCAD)."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_wedge

r = _handle_create_wedge(length=30, width=20, height=10, label="Ramp")
doc.recompute()
body = doc.getObject(r.data["body_name"])
feat = doc.getObject(r.data["name"])
results["data"] = {
    "success": r.success,
    "body_label": body.Label,
    "feat_label": feat.Label,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert d["body_label"] == "Ramp"
        # FreeCAD may rename the feature since body already has the label
        assert d["feat_label"].startswith("Ramp")

    def test_wedge_with_position(self, run_freecad_script):
        """Position offsets are applied to body placement."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_wedge

r = _handle_create_wedge(x=10, y=20, z=30)
doc.recompute()
body = doc.getObject(r.data["body_name"])
results["data"] = {
    "success": r.success,
    "x": body.Placement.Base.x,
    "y": body.Placement.Base.y,
    "z": body.Placement.Base.z,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert d["x"] == 10.0
        assert d["y"] == 20.0
        assert d["z"] == 30.0

    def test_wedge_add_to_existing_body(self, run_freecad_script):
        """Wedge can be added to an existing body."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_create_wedge

r1 = _handle_create_primitive(shape_type="box", length=50, width=50, height=10)
doc.recompute()
body_name = r1.data["body_name"]
vol_before = doc.getObject(body_name).Shape.Volume

r2 = _handle_create_wedge(
    length=30, width=20, height=15, body_name=body_name,
)
doc.recompute()

body = doc.getObject(body_name)
vol_after = body.Shape.Volume
results["data"] = {
    "success": r2.success,
    "same_body": r2.data["body_name"] == body_name,
    "vol_before": vol_before,
    "vol_after": vol_after,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert d["same_body"]
        assert d["vol_after"] > d["vol_before"]

    def test_subtractive_wedge(self, run_freecad_script):
        """Subtractive wedge cuts material from an existing body."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_create_wedge

r1 = _handle_create_primitive(shape_type="box", length=50, width=50, height=50)
doc.recompute()
body_name = r1.data["body_name"]
vol_before = doc.getObject(body_name).Shape.Volume

r2 = _handle_create_wedge(
    length=30, width=20, height=40, body_name=body_name, operation="subtractive",
)
doc.recompute()

body = doc.getObject(body_name)
vol_after = body.Shape.Volume
feat = doc.getObject(r2.data["name"])
results["data"] = {
    "success": r2.success,
    "feat_type": feat.TypeId,
    "vol_before": vol_before,
    "vol_after": vol_after,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"]
        assert d["feat_type"] == "PartDesign::SubtractiveLoft"
        assert d["vol_after"] < d["vol_before"]

    def test_body_not_found(self, run_freecad_script):
        """Passing a nonexistent body_name returns an error."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_wedge

r = _handle_create_wedge(body_name="NoSuchBody")
results["data"] = {
    "success": r.success,
    "error": r.error,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "not found" in d["error"]

    def test_wedge_then_fillet(self, run_freecad_script):
        """PartDesign wedge is compatible with fillet_edges."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_wedge, _handle_fillet_edges

r1 = _handle_create_wedge(length=50, width=30, height=20)
doc.recompute()
body_name = r1.data["body_name"]
vol_before = doc.getObject(body_name).Shape.Volume

r2 = _handle_fillet_edges(
    object_name=r1.data["name"], edges=["Edge1"], radius=2,
)
doc.recompute()

body = doc.getObject(body_name)
vol_after = body.Shape.Volume
results["data"] = {
    "wedge_ok": r1.success,
    "fillet_ok": r2.success,
    "vol_before": vol_before,
    "vol_after": vol_after,
    "tip_type": body.Tip.TypeId,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["wedge_ok"]
        assert d["fillet_ok"]
        assert d["tip_type"] == "PartDesign::Fillet"
