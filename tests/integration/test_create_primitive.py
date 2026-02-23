"""Integration tests for create_primitive tool (PartDesign primitives)."""

import pytest

pytestmark = pytest.mark.integration


class TestCreatePrimitive:
    def test_create_box(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r = _handle_create_primitive(shape_type="box", length=20, width=15, height=10)
doc.recompute()
obj = doc.getObject(r.data["name"])
body = doc.getObject(r.data["body_name"])
results["data"] = {
    "success": r.success,
    "output": r.output,
    "volume": obj.Shape.Volume,
    "type_id": obj.TypeId,
    "body_type": body.TypeId,
    "length": obj.Length.Value,
    "width": obj.Width.Value,
    "height": obj.Height.Value,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["type_id"] == "PartDesign::AdditiveBox"
        assert d["body_type"] == "PartDesign::Body"
        assert abs(d["volume"] - 3000.0) < 1.0  # 20*15*10
        assert d["length"] == 20.0

    def test_create_cylinder(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive
import math

r = _handle_create_primitive(shape_type="cylinder", radius=5, height=20)
doc.recompute()
obj = doc.getObject(r.data["name"])
expected_vol = math.pi * 5**2 * 20
results["data"] = {
    "success": r.success,
    "volume": obj.Shape.Volume,
    "expected": expected_vol,
    "type_id": obj.TypeId,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["type_id"] == "PartDesign::AdditiveCylinder"
        assert abs(d["volume"] - d["expected"]) < 1.0

    def test_create_sphere(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive
import math

r = _handle_create_primitive(shape_type="sphere", radius=10)
doc.recompute()
obj = doc.getObject(r.data["name"])
expected_vol = (4/3) * math.pi * 10**3
results["data"] = {
    "success": r.success,
    "volume": obj.Shape.Volume,
    "expected": expected_vol,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert abs(d["volume"] - d["expected"]) < 10.0

    def test_create_cone(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r = _handle_create_primitive(shape_type="cone", radius=10, radius2=3, height=15)
doc.recompute()
obj = doc.getObject(r.data["name"])
results["data"] = {
    "success": r.success,
    "volume": obj.Shape.Volume,
    "type_id": obj.TypeId,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["type_id"] == "PartDesign::AdditiveCone"
        assert d["volume"] > 0

    def test_create_torus(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r = _handle_create_primitive(shape_type="torus", radius=10, radius2=3)
doc.recompute()
obj = doc.getObject(r.data["name"])
results["data"] = {
    "success": r.success,
    "volume": obj.Shape.Volume,
    "type_id": obj.TypeId,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["type_id"] == "PartDesign::AdditiveTorus"
        assert d["volume"] > 0

    def test_create_with_position(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r = _handle_create_primitive(shape_type="box", x=10, y=20, z=30)
doc.recompute()
obj = doc.getObject(r.data["name"])
results["data"] = {
    "x": obj.Placement.Base.x,
    "y": obj.Placement.Base.y,
    "z": obj.Placement.Base.z,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["x"] == 10.0
        assert d["y"] == 20.0
        assert d["z"] == 30.0

    def test_unknown_shape_type(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r = _handle_create_primitive(shape_type="hexagon")
results["data"] = {
    "success": r.success,
    "error": r.error,
}
""")
        assert result["ok"]
        d = result["data"]
        assert not d["success"]
        assert "Unknown shape type" in d["error"]

    def test_auto_creates_body(self, run_freecad_script):
        """create_primitive without body_name auto-creates a PartDesign::Body."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r = _handle_create_primitive(shape_type="box", length=50, width=30, height=20)
doc.recompute()
body = doc.getObject(r.data["body_name"])
results["data"] = {
    "success": r.success,
    "body_name": r.data["body_name"],
    "body_type": body.TypeId,
    "prim_name": r.data["name"],
    "prim_type": r.data["type"],
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["body_type"] == "PartDesign::Body"
        assert d["prim_type"] == "PartDesign::AdditiveBox"

    def test_add_to_existing_body(self, run_freecad_script):
        """create_primitive with body_name adds to an existing Body."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r1 = _handle_create_primitive(shape_type="box", length=50, width=30, height=20)
doc.recompute()
body_name = r1.data["body_name"]

r2 = _handle_create_primitive(shape_type="cylinder", body_name=body_name, radius=5, height=30)
doc.recompute()

body = doc.getObject(body_name)
results["data"] = {
    "success": r2.success,
    "same_body": r2.data["body_name"] == body_name,
    "cyl_type": r2.data["type"],
    "tip_name": body.Tip.Name,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["same_body"]
        assert d["cyl_type"] == "PartDesign::AdditiveCylinder"

    def test_subtractive_operation(self, run_freecad_script):
        """operation='subtractive' creates a SubtractiveCylinder that cuts material."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r1 = _handle_create_primitive(shape_type="box", length=50, width=50, height=50)
doc.recompute()
body_name = r1.data["body_name"]
vol_before = doc.getObject(r1.data["name"]).Shape.Volume

r2 = _handle_create_primitive(
    shape_type="cylinder", body_name=body_name, operation="subtractive",
    radius=10, height=60,
)
doc.recompute()

body = doc.getObject(body_name)
vol_after = body.Tip.Shape.Volume
results["data"] = {
    "success": r2.success,
    "cyl_type": r2.data["type"],
    "vol_before": vol_before,
    "vol_after": vol_after,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["success"]
        assert d["cyl_type"] == "PartDesign::SubtractiveCylinder"
        assert d["vol_after"] < d["vol_before"]

    def test_body_not_found(self, run_freecad_script):
        """Passing a nonexistent body_name returns an error."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive

r = _handle_create_primitive(shape_type="box", body_name="NoSuchBody")
results["data"] = {
    "success": r.success,
    "error": r.error,
}
""")
        assert result["ok"]
        d = result["data"]
        assert not d["success"]
        assert "not found" in d["error"]

    def test_primitive_then_shell(self, run_freecad_script):
        """PartDesign primitive is compatible with shell_object."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_shell_object

r1 = _handle_create_primitive(shape_type="box", length=150, width=80, height=40)
doc.recompute()
solid_vol = 150 * 80 * 40

r2 = _handle_shell_object(object_name=r1.data["name"], thickness=3, faces=["Face6"])
doc.recompute()

body = doc.getObject(r1.data["body_name"])
shell_vol = body.Tip.Shape.Volume
results["data"] = {
    "prim_ok": r1.success,
    "shell_ok": r2.success,
    "solid_vol": solid_vol,
    "shell_vol": shell_vol,
    "tip_type": body.Tip.TypeId,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["prim_ok"]
        assert d["shell_ok"]
        assert d["tip_type"] == "PartDesign::Thickness"
        assert d["shell_vol"] < d["solid_vol"]

    def test_box_shell_then_subtractive_cylinder(self, run_freecad_script):
        """Full workflow: box → shell → subtractive cylinder in same Body."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_shell_object

r1 = _handle_create_primitive(shape_type="box", length=150, width=80, height=40)
doc.recompute()
body_name = r1.data["body_name"]

r2 = _handle_shell_object(object_name=r1.data["name"], thickness=3, faces=["Face6"])
doc.recompute()
vol_after_shell = doc.getObject(body_name).Tip.Shape.Volume

r3 = _handle_create_primitive(
    shape_type="cylinder", body_name=body_name, operation="subtractive",
    radius=10, height=50, x=20, y=20,
)
doc.recompute()

body = doc.getObject(body_name)
vol_after_cut = body.Tip.Shape.Volume
results["data"] = {
    "box_ok": r1.success,
    "shell_ok": r2.success,
    "cut_ok": r3.success,
    "cut_type": r3.data["type"],
    "tip_type": body.Tip.TypeId,
    "vol_after_shell": vol_after_shell,
    "vol_after_cut": vol_after_cut,
}
""")
        assert result["ok"]
        d = result["data"]
        assert d["box_ok"]
        assert d["shell_ok"]
        assert d["cut_ok"]
        assert d["cut_type"] == "PartDesign::SubtractiveCylinder"
        assert d["tip_type"] == "PartDesign::SubtractiveCylinder"
        assert d["vol_after_cut"] <= d["vol_after_shell"] + 1.0  # tolerance for float rounding
