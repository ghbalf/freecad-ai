"""Integration tests for multi_transform tool (PartDesign::MultiTransform).

Note: FreeCAD pattern features only produce a valid fused solid when copies
overlap or touch. Non-contiguous copies produce multiple solids and FreeCAD
keeps only the first. Tests use geometry that ensures copies touch/overlap.
"""

import pytest

pytestmark = pytest.mark.integration


class TestMultiTransform:
    def test_linear_then_mirror(self, run_freecad_script):
        """Linear pattern (overlapping) + mirror → ~5x volume."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_multi_transform

# Box 10x10x10 at origin
r1 = _handle_create_primitive(shape_type="box", length=10, width=10, height=10)
doc.recompute()
body_name = r1.data["body_name"]
feat_name = r1.data["name"]
vol_before = doc.getObject(body_name).Shape.Volume

# Linear 3x along X (length=15 → spacing=7.5 → overlapping), then mirror YZ
r2 = _handle_multi_transform(
    feature_names=[feat_name],
    transformations=[
        {"type": "linear_pattern", "direction": "X", "length": 15, "occurrences": 3},
        {"type": "mirror", "plane": "YZ"},
    ],
)
doc.recompute()

body = doc.getObject(body_name)
vol_after = body.Shape.Volume
results["data"] = {
    "success": r2.success,
    "error": r2.error,
    "steps": r2.data.get("steps", 0),
    "vol_before": vol_before,
    "vol_after": vol_after,
    "ratio": vol_after / vol_before if vol_before > 0 else 0,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d.get("error")
        assert d["steps"] == 2
        # 3 overlapping linear copies mirrored: linear gives X[0,25]=2500, mirror gives X[-25,25]=5000
        assert d["ratio"] > 4.0, f"Expected ~5x volume, got {d['ratio']:.1f}x"

    def test_polar_pattern_only(self, run_freecad_script):
        """Single polar step: box at origin, 4 copies around Z → ~4x volume."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_multi_transform

# Box 10x10x10 at origin — corners touch at origin when rotated 90°
r1 = _handle_create_primitive(shape_type="box", length=10, width=10, height=10)
doc.recompute()
body_name = r1.data["body_name"]
feat_name = r1.data["name"]
vol_before = doc.getObject(body_name).Shape.Volume

r2 = _handle_multi_transform(
    feature_names=[feat_name],
    transformations=[
        {"type": "polar_pattern", "axis": "Z", "angle": 360, "occurrences": 4},
    ],
)
doc.recompute()

body = doc.getObject(body_name)
vol_after = body.Shape.Volume
results["data"] = {
    "success": r2.success,
    "error": r2.error,
    "steps": r2.data.get("steps", 0),
    "vol_before": vol_before,
    "vol_after": vol_after,
    "ratio": vol_after / vol_before if vol_before > 0 else 0,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d.get("error")
        assert d["steps"] == 1
        # 4 touching boxes in + shape around Z: 4x volume
        assert d["ratio"] > 3.5, f"Expected ~4x volume, got {d['ratio']:.1f}x"

    def test_three_step_transform(self, run_freecad_script):
        """Linear + polar + mirror combined: verify feature created with 3 steps and volume increase."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_multi_transform

# Box 10x10x10 at origin
r1 = _handle_create_primitive(shape_type="box", length=10, width=10, height=10)
doc.recompute()
body_name = r1.data["body_name"]
feat_name = r1.data["name"]
vol_before = doc.getObject(body_name).Shape.Volume

r2 = _handle_multi_transform(
    feature_names=[feat_name],
    transformations=[
        {"type": "linear_pattern", "direction": "X", "length": 5, "occurrences": 2},
        {"type": "polar_pattern", "axis": "Z", "angle": 360, "occurrences": 4},
        {"type": "mirror", "plane": "XZ"},
    ],
)
doc.recompute()

body = doc.getObject(body_name)
vol_after = body.Shape.Volume
results["data"] = {
    "success": r2.success,
    "error": r2.error,
    "steps": r2.data.get("steps", 0),
    "vol_before": vol_before,
    "vol_after": vol_after,
    "ratio": vol_after / vol_before if vol_before > 0 else 0,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d.get("error")
        assert d["steps"] == 3
        # 2 overlapping linear * 4 polar * 2 mirror (significant overlap but still >5x)
        assert d["ratio"] > 3.0, f"Expected significant volume increase, got {d['ratio']:.1f}x"

    def test_empty_transformations_fails(self, run_freecad_script):
        """Empty transformations list should return an error."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_multi_transform

r1 = _handle_create_primitive(shape_type="box")
doc.recompute()

r2 = _handle_multi_transform(
    feature_names=[r1.data["name"]],
    transformations=[],
)
results["data"] = {
    "success": r2.success,
    "error": r2.error,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "empty" in d["error"].lower() or "must not" in d["error"].lower()

    def test_unknown_type_fails(self, run_freecad_script):
        """Unknown transformation type should return an error."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_primitive, _handle_multi_transform

r1 = _handle_create_primitive(shape_type="box")
doc.recompute()

r2 = _handle_multi_transform(
    feature_names=[r1.data["name"]],
    transformations=[{"type": "scale_pattern"}],
)
results["data"] = {
    "success": r2.success,
    "error": r2.error,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "unknown" in d["error"].lower()

    def test_feature_not_found(self, run_freecad_script):
        """Missing feature should return an error."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_multi_transform

r = _handle_multi_transform(
    feature_names=["NoSuchFeature"],
    transformations=[{"type": "mirror", "plane": "YZ"}],
)
results["data"] = {
    "success": r.success,
    "error": r.error,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "not found" in d["error"].lower()

    def test_multiple_features(self, run_freecad_script):
        """Two features (box pad + cylinder pad) mirrored together as a group."""
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import (
    _handle_create_body, _handle_create_sketch, _handle_pad_sketch,
    _handle_multi_transform,
)

# Create body + box pad
r_body = _handle_create_body(label="TestBody")
doc.recompute()
body_name = r_body.data["name"]

r_sk1 = _handle_create_sketch(
    body_name=body_name, plane="XY",
    geometries=[{"type": "rectangle", "x": 0, "y": 0, "width": 20, "height": 10}],
)
doc.recompute()
r_pad1 = _handle_pad_sketch(sketch_name=r_sk1.data["name"], length=10)
doc.recompute()
pad1_name = r_pad1.data["name"]

# Add cylinder pad on top
r_sk2 = _handle_create_sketch(
    body_name=body_name, plane="XY", offset=10,
    geometries=[{"type": "circle", "cx": 10, "cy": 5, "radius": 3}],
)
doc.recompute()
r_pad2 = _handle_pad_sketch(sketch_name=r_sk2.data["name"], length=5)
doc.recompute()
pad2_name = r_pad2.data["name"]

body = doc.getObject(body_name)
vol_before = body.Shape.Volume

# Mirror both features together across YZ
r_mt = _handle_multi_transform(
    feature_names=[pad1_name, pad2_name],
    transformations=[{"type": "mirror", "plane": "YZ"}],
)
doc.recompute()

vol_after = body.Shape.Volume
results["data"] = {
    "success": r_mt.success,
    "error": r_mt.error,
    "steps": r_mt.data.get("steps", 0) if r_mt.data else 0,
    "vol_before": vol_before,
    "vol_after": vol_after,
    "ratio": vol_after / vol_before if vol_before > 0 else 0,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d.get("error")
        assert d["steps"] == 1
        # Mirror doubles volume (approximately, some overlap possible)
        assert d["ratio"] > 1.8, f"Expected ~2x volume, got {d['ratio']:.1f}x"
