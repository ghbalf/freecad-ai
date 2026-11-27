"""Unit tests for the pure create_sketch attachment resolver (no FreeCAD)."""

import types
import unittest
from freecad_ai.tools.freecad_tools import _resolve_sketch_attachment, _classify_support, _owning_body_name


def _resolve(**kw):
    base = dict(
        support="", face="", plane="XY", body_present=False,
        support_kind="", face_exists=False, face_planar=False, in_body=None,
    )
    base.update(kw)
    return _resolve_sketch_attachment(**base)


class TestResolveSketchAttachment:
    # --- explicit face ---
    def test_planar_face_on_solid_resolves_to_face_mode(self):
        spec = _resolve(support="Box", face="Face6", support_kind="solid",
                        face_exists=True, face_planar=True, in_body="Body")
        assert spec == {"mode": "face", "support": "Box", "sub": "Face6",
                        "in_body": "Body"}

    def test_face_on_standalone_solid_has_no_body(self):
        spec = _resolve(support="Imported", face="Face3", support_kind="solid",
                        face_exists=True, face_planar=True, in_body=None)
        assert spec["mode"] == "face"
        assert spec["in_body"] is None

    def test_missing_face_is_error(self):
        spec = _resolve(support="Box", face="Face99", support_kind="solid",
                        face_exists=False)
        assert spec["mode"] == "error"
        assert "Face99" in spec["message"]

    def test_non_planar_face_is_error(self):
        spec = _resolve(support="Cyl", face="Face1", support_kind="solid",
                        face_exists=True, face_planar=False)
        assert spec["mode"] == "error"
        assert "planar" in spec["message"].lower()

    # --- explicit plane ---
    def test_plane_support_without_face_resolves_to_plane_mode(self):
        spec = _resolve(support="DatumPlane", support_kind="plane", in_body="Body")
        assert spec == {"mode": "plane", "support": "DatumPlane", "in_body": "Body"}

    # --- validation ---
    def test_face_without_support_is_error(self):
        spec = _resolve(face="Face6")  # support empty
        assert spec["mode"] == "error"
        assert "support" in spec["message"].lower()

    def test_missing_support_is_error(self):
        spec = _resolve(support="Nope", support_kind="missing")
        assert spec["mode"] == "error"
        assert "not found" in spec["message"].lower()

    def test_solid_support_without_face_is_error(self):
        spec = _resolve(support="Box", support_kind="solid")
        assert spec["mode"] == "error"
        assert "face" in spec["message"].lower()

    # --- fall-through to current behavior ---
    def test_no_support_with_body_and_origin_plane(self):
        spec = _resolve(plane="XZ", body_present=True)
        assert spec == {"mode": "origin", "plane": "XZ"}

    def test_no_support_no_body_is_standalone(self):
        spec = _resolve(plane="XY", body_present=False)
        assert spec == {"mode": "standalone"}

    def test_origin_plane_is_case_insensitive(self):
        spec = _resolve(plane="xy", body_present=True)
        assert spec == {"mode": "origin", "plane": "XY"}

    def test_other_support_without_face_is_error_not_solid(self):
        spec = _resolve(support="Mesh", support_kind="other")
        assert spec["mode"] == "error"
        assert "is a solid" not in spec["message"]
        assert "face" in spec["message"].lower()

    def test_other_support_with_planar_face_resolves_to_face_mode(self):
        spec = _resolve(support="Mesh", face="Face2", support_kind="other",
                        face_exists=True, face_planar=True, in_body=None)
        assert spec == {"mode": "face", "support": "Mesh", "sub": "Face2",
                        "in_body": None}

    def test_standalone_regardless_of_plane_when_no_body(self):
        spec = _resolve(plane="XZ", body_present=False)
        assert spec == {"mode": "standalone"}

    def test_unknown_support_kind_is_error(self):
        spec = _resolve(support="X", support_kind="bogus")
        assert spec["mode"] == "error"
        assert "bogus" in spec["message"]

    def test_none_face_is_treated_as_absent(self):
        spec = _resolve(support="", face=None, plane="XY", body_present=True)
        assert spec == {"mode": "origin", "plane": "XY"}


class _FakeObj:
    def __init__(self, type_id, name="Obj", has_solids=False, group_of=None):
        self.TypeId = type_id
        self.Name = name
        self._has_solids = has_solids
        # FreeCAD groups expose .Group; a Body lists its children there.
        if group_of is not None:
            self.Group = group_of

    @property
    def Shape(self):
        class _S:
            Solids = [1] if self._has_solids else []
        return _S()


class TestClassifySupport:
    def test_datum_plane_is_plane(self):
        assert _classify_support(_FakeObj("PartDesign::Plane")) == "plane"

    def test_origin_app_plane_is_plane(self):
        assert _classify_support(_FakeObj("App::Plane")) == "plane"

    def test_part_datum_plane_is_plane(self):
        assert _classify_support(_FakeObj("Part::DatumPlane")) == "plane"

    def test_solid_feature_is_solid(self):
        assert _classify_support(
            _FakeObj("Part::Feature", has_solids=True)) == "solid"

    def test_feature_without_solids_is_other(self):
        assert _classify_support(
            _FakeObj("Part::Feature", has_solids=False)) == "other"

    def test_sketch_is_other(self):
        assert _classify_support(_FakeObj("Sketcher::SketchObject")) == "other"

    def test_solids_access_error_is_other(self):
        class _Boom:
            TypeId = "Part::Feature"
            Name = "Boom"

            @property
            def Shape(self):
                class _S:
                    @property
                    def Solids(self):
                        raise RuntimeError("boom")
                return _S()

        assert _classify_support(_Boom()) == "other"


class TestOwningBodyName:
    def test_object_in_body_returns_body_name(self):
        child = _FakeObj("PartDesign::Pad", name="Pad")
        body = _FakeObj("PartDesign::Body", name="Body", group_of=[child])
        # The fake document is just the list of objects to scan.
        assert _owning_body_name(child, [body, child]) == "Body"

    def test_standalone_object_returns_none(self):
        feat = _FakeObj("Part::Feature", name="Imported")
        assert _owning_body_name(feat, [feat]) is None


class TestBodySupportToTip(unittest.TestCase):
    """#168: attaching inside a Body to the Body itself is a dependency cycle."""

    def _obj(self, type_id, name, tip=None):
        o = types.SimpleNamespace(TypeId=type_id, Name=name)
        if tip is not None:
            o.Tip = tip
        return o

    def test_body_is_replaced_by_its_tip_with_a_warning(self):
        from freecad_ai.tools.freecad_tools import _body_support_to_tip
        pad = self._obj("PartDesign::Pad", "Pad")
        warnings = []
        self.assertIs(_body_support_to_tip(self._obj("PartDesign::Body", "Body", pad), warnings), pad)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Tip 'Pad'", warnings[0])

    def test_other_supports_and_empty_bodies_are_kept(self):
        from freecad_ai.tools.freecad_tools import _body_support_to_tip
        warnings = []
        pad = self._obj("PartDesign::Pad", "Pad")
        empty = self._obj("PartDesign::Body", "Body")  # no Tip yet
        empty.Tip = None
        self.assertIs(_body_support_to_tip(pad, warnings), pad)
        self.assertIs(_body_support_to_tip(empty, warnings), empty)
        self.assertEqual(warnings, [])
