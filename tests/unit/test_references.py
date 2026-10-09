"""Tests for the selection-reference feature."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from freecad_ai.core.conversation import Conversation
from freecad_ai.core.references import (
    describe_reference,
    format_reference_block,
    reference_token,
    references_from_selection,
)


def _make_ref(name="Pad", label="Pad", sub=None, text="desc"):
    return {"name": name, "label": label, "sub": sub, "text": text}


class TestConversationReferences:
    def test_text_only_unchanged(self):
        c = Conversation()
        c.add_user_message("hello")
        assert c.messages[-1]["content"] == "hello"

    def test_references_create_blocks(self):
        c = Conversation()
        ref = _make_ref(sub="Face3", text="Face3 of Pad — top planar")
        c.add_user_message("why?", references=[ref])
        content = c.messages[-1]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "why?"}
        assert content[1]["type"] == "text"
        assert content[1]["text"].startswith("--- FreeCAD reference: @Pad.Face3 ---")
        assert "top planar" in content[1]["text"]

    def test_block_ordering_text_reference_document_image(self):
        c = Conversation()
        images = [{"type": "image", "source": "base64",
                   "media_type": "image/png", "data": "abc"}]
        docs = [{"filename": "notes.md", "text": "# Notes"}]
        refs = [_make_ref(sub="Face3")]
        c.add_user_message("look", images=images, documents=docs, references=refs)
        content = c.messages[-1]["content"]
        # text first, then references, then documents, then images
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "look"
        assert "FreeCAD reference" in content[1]["text"]
        assert "notes.md" in content[2]["text"]
        assert content[3]["type"] == "image"

    def test_multiple_references(self):
        c = Conversation()
        refs = [_make_ref(sub="Face3"), _make_ref(sub="Face1")]
        c.add_user_message("compare", references=refs)
        content = c.messages[-1]["content"]
        assert len(content) == 3  # user text + 2 reference blocks
        assert "@Pad.Face3" in content[1]["text"]
        assert "@Pad.Face1" in content[2]["text"]

    def test_spaces_in_label_token_uses_name(self):
        c = Conversation()
        ref = _make_ref(name="Pad", label="Pad 001", sub=None)
        c.add_user_message("x", references=[ref])
        block = c.messages[-1]["content"][1]["text"]
        assert block.startswith("--- FreeCAD reference: @Pad ---")


class TestReferenceTokenAndBlock:
    def test_token_object(self):
        assert reference_token(_make_ref()) == "@Pad"

    def test_token_sub_element(self):
        assert reference_token(_make_ref(sub="Edge7")) == "@Pad.Edge7"

    def test_token_uses_immutable_name_not_label(self):
        # Tokens are keyed to the immutable internal name so renames
        # never desync chip, token, or reference block.
        assert reference_token(_make_ref(name="Pad", label="Base")) == "@Pad"
        assert reference_token(_make_ref(name="Pad001", label="Pad 001")) == "@Pad001"

    def test_format_block(self):
        ref = _make_ref(sub="Face3", text="Face3 of Pad — top planar")
        assert format_reference_block(ref) == (
            "--- FreeCAD reference: @Pad.Face3 ---\nFace3 of Pad — top planar")


class Plane:
    """Stub mirroring FreeCAD's Part.Plane surface class name."""

    pass


class Line:
    """Stub mirroring FreeCAD's Part.Line curve class name."""

    pass


class TestDescribeReference:
    def test_object_only(self):
        obj = SimpleNamespace(
            Name="Pad", Label="Pad", TypeId="PartDesign::Pad",
            Type="Dimension", Length=10.0,
            Placement=SimpleNamespace(Base=SimpleNamespace(x=0.0, y=0.0, z=0.0)),
            Shape=SimpleNamespace(BoundBox=SimpleNamespace(
                XLength=20.0, YLength=20.0, ZLength=10.0)),
        )
        desc = describe_reference(obj)
        assert desc.startswith("Pad (PartDesign::Pad)")
        assert "Type: Dimension" in desc
        assert "Length: 10.0" in desc
        assert "pos (0.0, 0.0, 0.0)" in desc
        assert "bbox 20.0x20.0x10.0" in desc

    def test_face(self):
        face = SimpleNamespace(
            Surface=Plane(),
            Area=400.0,
            CenterOfMass=SimpleNamespace(x=10.0, y=10.0, z=10.0),
            normalAt=lambda u, v: SimpleNamespace(x=0.0, y=0.0, z=1.0),
        )
        shape = SimpleNamespace(
            BoundBox=SimpleNamespace(XMin=0, XMax=20, YMin=0, YMax=20,
                                    ZMin=0, ZMax=10),
            getElement=lambda name: face,
        )
        obj = SimpleNamespace(Name="Pad", Label="Pad", Shape=shape)
        desc = describe_reference(obj, "Face3")
        assert desc.startswith("Face3 of Pad — ")
        assert "top planar" in desc
        assert "area 400.00" in desc
        assert "center (10.0, 10.0, 10.0)" in desc
        assert "normal (0.00, 0.00, 1.00)" in desc

    def test_edge(self):
        edge = SimpleNamespace(
            Curve=Line(),
            Length=20.0,
            Vertexes=[
                SimpleNamespace(Point=SimpleNamespace(x=0.0, y=0.0, z=0.0)),
                SimpleNamespace(Point=SimpleNamespace(x=20.0, y=0.0, z=0.0)),
            ],
            CenterOfMass=SimpleNamespace(x=10.0, y=0.0, z=0.0),
        )
        shape = SimpleNamespace(
            BoundBox=SimpleNamespace(XMin=0.0, XMax=20.0, YMin=0.0, YMax=20.0,
                                    ZMin=0.0, ZMax=10.0),
            getElement=lambda name: edge,
        )
        obj = SimpleNamespace(Name="Pad", Label="Pad", Shape=shape)
        desc = describe_reference(obj, "Edge2")
        assert desc.startswith("Edge2 of Pad — ")
        assert "Line" in desc
        assert "length 20.00" in desc
        assert "(0.0, 0.0, 0.0)→(20.0, 0.0, 0.0)" in desc

    def test_vertex(self):
        vertex = SimpleNamespace(Point=SimpleNamespace(x=5.0, y=6.0, z=7.0))
        shape = SimpleNamespace(getElement=lambda name: vertex)
        obj = SimpleNamespace(Name="Pad", Label="Pad", Shape=shape)
        assert describe_reference(obj, "Vertex1") == "Vertex1 of Pad — (5.0, 6.0, 7.0)"

    def test_missing_element_falls_back(self):
        obj = SimpleNamespace(
            Name="Pad", Label="Pad",
            Shape=SimpleNamespace(getElement=lambda name: None))
        assert describe_reference(obj, "Face99") == "Face99 of Pad"


def _stub_gui(selections):
    mod = types.ModuleType("FreeCADGui")
    mod.Selection = SimpleNamespace(getSelectionEx=lambda: selections)
    return mod


def _stub_selection(obj_name, subs=(), obj=None):
    return SimpleNamespace(ObjectName=obj_name, SubElementNames=list(subs),
                           Object=obj)


def _stub_object(name="Pad", label="Pad"):
    return SimpleNamespace(Name=name, Label=label, TypeId="PartDesign::Pad")


class TestReferencesFromSelection:
    def test_no_gui_returns_empty(self):
        with patch.dict(sys.modules, {"FreeCADGui": None}):
            assert references_from_selection() == []

    def test_empty_selection_returns_empty(self):
        with patch.dict(sys.modules, {"FreeCADGui": _stub_gui([])}):
            assert references_from_selection() == []

    def test_whole_object_selection(self):
        sel = _stub_selection("Pad", (), _stub_object())
        with patch.dict(sys.modules, {"FreeCADGui": _stub_gui([sel])}):
            refs = references_from_selection()
        assert len(refs) == 1
        assert refs[0]["name"] == "Pad"
        assert refs[0]["label"] == "Pad"
        assert refs[0]["sub"] is None
        assert "Pad (PartDesign::Pad)" in refs[0]["text"]

    def test_sub_elements_expand_one_ref_each(self):
        sel = _stub_selection("Pad", ["Face3", "Edge7"], _stub_object())
        with patch.dict(sys.modules, {"FreeCADGui": _stub_gui([sel])}):
            refs = references_from_selection()
        assert len(refs) == 2
        assert refs[0]["sub"] == "Face3"
        assert refs[0]["text"] == "Face3 of Pad"
        assert refs[1]["sub"] == "Edge7"

    def test_multiple_objects(self):
        s1 = _stub_selection("Pad", (), _stub_object("Pad"))
        s2 = _stub_selection("Box", ["Vertex2"], _stub_object("Box"))
        with patch.dict(sys.modules, {"FreeCADGui": _stub_gui([s1, s2])}):
            refs = references_from_selection()
        assert len(refs) == 2
        assert refs[0]["name"] == "Pad"
        assert refs[0]["sub"] is None
        assert refs[1]["name"] == "Box"
        assert refs[1]["sub"] == "Vertex2"

    def test_non_geometry_subs_fall_back_to_object(self):
        sel = _stub_selection("Pad", ["something"], _stub_object())
        with patch.dict(sys.modules, {"FreeCADGui": _stub_gui([sel])}):
            refs = references_from_selection()
        assert refs == [{
            "name": "Pad", "label": "Pad", "sub": None,
            "text": "Pad (PartDesign::Pad)",
        }]

    def test_unresolvable_object_skipped(self):
        sel = _stub_selection("Ghost", (), None)
        with patch.dict(sys.modules, {"FreeCADGui": _stub_gui([sel]),
                                       "FreeCAD": None}):
            assert references_from_selection() == []


class TestAttachmentStripReferences:
    """Reference chips on _AttachmentStrip."""

    @pytest.fixture
    def strip(self):
        pytest.importorskip("PySide6", reason="PySide6 required for widget tests")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        from freecad_ai.ui.chat_widget import _AttachmentStrip
        return _AttachmentStrip()

    def test_add_reference_shows_strip(self, strip):
        assert strip.add_reference(_make_ref(sub="Face3")) is True
        assert strip.isVisible()
        assert strip._items[0][1] == "reference"
        assert strip._items[0][2]["sub"] == "Face3"

    def test_chip_shows_token(self, strip):
        from PySide6.QtWidgets import QLabel
        strip.add_reference(_make_ref(sub="Face3"))
        chip = strip._items[0][0].findChild(QLabel)
        assert chip.text() == "@Pad.Face3"

    def test_chip_tooltip_has_description(self, strip):
        from PySide6.QtWidgets import QLabel
        strip.add_reference(_make_ref(sub="Face3", text="Face3 of Pad — top planar"))
        chip = strip._items[0][0].findChild(QLabel)
        assert chip.toolTip() == "Face3 of Pad — top planar"

    def test_get_references_returns_refs(self, strip):
        strip.add_reference(_make_ref(sub="Face3"))
        strip.add_reference(_make_ref(name="Box", label="Box", sub=None))
        refs = strip.get_references()
        assert len(refs) == 2
        assert refs[0]["name"] == "Pad"
        assert refs[0]["sub"] == "Face3"
        assert refs[1]["name"] == "Box"
        assert refs[1]["sub"] is None

    def test_duplicate_reference_ignored(self, strip):
        assert strip.add_reference(_make_ref(sub="Face3")) is True
        assert strip.add_reference(_make_ref(sub="Face3")) is False
        assert len(strip._items) == 1
        # same object, different sub-element still added
        assert strip.add_reference(_make_ref(sub="Face1")) is True
        assert len(strip._items) == 2

    def test_duplicate_refreshes_snapshot(self, strip):
        from PySide6.QtWidgets import QLabel
        assert strip.add_reference(_make_ref(sub="Face3", text="old")) is True
        # Re-click after geometry moved: same key → no second chip, but
        # the stored description and tooltip must be refreshed.
        assert strip.add_reference(_make_ref(sub="Face3", text="moved z=60")) is False
        assert len(strip._items) == 1
        assert strip.get_references()[0]["text"] == "moved z=60"
        chip = strip._items[0][0].findChild(QLabel)
        assert chip.toolTip() == "moved z=60"

    def test_rename_keeps_token_stable(self, strip):
        from PySide6.QtWidgets import QLabel
        assert strip.add_reference(_make_ref(sub="Face3")) is True
        # Object renamed (Label changed): same (name, sub) key → the
        # Name-keyed token stays stable; the snapshot still refreshes.
        renamed = _make_ref(name="Pad", label="Base", sub="Face3", text="top")
        assert strip.add_reference(renamed) is False
        assert len(strip._items) == 1
        chip = strip._items[0][0].findChild(QLabel)
        assert chip.text() == "@Pad.Face3"
        assert chip.toolTip() == "top"
        assert strip.get_references()[0]["label"] == "Base"

    def test_getters_isolated(self, strip):
        strip.add_reference(_make_ref())
        strip.add_document("a.txt", "hello")
        strip.add_image("image/png", "iVBORw0KGgo=")
        assert len(strip.get_references()) == 1
        assert len(strip.get_documents()) == 1
        assert len(strip.get_images()) == 1

    def test_clear_removes_references(self, strip):
        strip.add_reference(_make_ref())
        strip.clear()
        assert strip._items == []
        assert not strip.isVisible()

    def test_remove_reference(self, strip):
        strip.add_reference(_make_ref(sub="Face3"))
        strip.add_reference(_make_ref(sub="Edge1"))
        strip._remove(0)
        assert len(strip._items) == 1
        assert strip.get_references()[0]["sub"] == "Edge1"


class TestAddSelectionReference:
    """Branch logic of _add_selection_reference (fake-self pattern,
    see test_input_history_wiring.py — no real dock needed)."""

    @pytest.fixture
    def dock(self):
        pytest.importorskip("PySide6", reason="PySide6 required for widget tests")
        from freecad_ai.ui import chat_widget as cw

        cursor = MagicMock()
        cursor.position.return_value = 0
        cursor.insertText = MagicMock()

        input_edit = MagicMock()
        input_edit.toPlainText.return_value = ""
        input_edit.textCursor.return_value = cursor
        input_edit.setTextCursor = MagicMock()
        input_edit.setFocus = MagicMock()

        fake = SimpleNamespace(
            input_edit=input_edit,
            _attachment_strip=MagicMock(),
            _append_html=MagicMock(),
        )
        fake._add_selection_reference = types.MethodType(
            cw.ChatDockWidget._add_selection_reference, fake)
        return fake, cw

    def _click(self, fake, cw, monkeypatch, refs):
        monkeypatch.setattr(cw, "references_from_selection", lambda: refs)
        fake._add_selection_reference()

    def test_new_reference_inserts_token(self, dock, monkeypatch):
        fake, cw = dock
        fake._attachment_strip.add_reference.return_value = True
        self._click(fake, cw, monkeypatch, [_make_ref(sub="Face3")])
        fake._attachment_strip.add_reference.assert_called_once()
        inserted = fake.input_edit.textCursor().insertText.call_args[0][0]
        assert inserted == "@Pad.Face3 "
        fake.input_edit.setFocus.assert_called_once()

    def test_multiple_new_tokens_joined(self, dock, monkeypatch):
        fake, cw = dock
        fake._attachment_strip.add_reference.return_value = True
        self._click(fake, cw, monkeypatch, [
            _make_ref(sub="Face3"), _make_ref(name="Box", label="Box")])
        inserted = fake.input_edit.textCursor().insertText.call_args[0][0]
        assert inserted == "@Pad.Face3 @Box "

    def test_empty_selection_posts_hint(self, dock, monkeypatch):
        fake, cw = dock
        self._click(fake, cw, monkeypatch, [])
        fake._append_html.assert_called_once()
        fake._attachment_strip.add_reference.assert_not_called()
        fake.input_edit.textCursor().insertText.assert_not_called()

    def test_deleted_token_reinserted(self, dock, monkeypatch):
        fake, cw = dock
        fake._attachment_strip.add_reference.return_value = False
        self._click(fake, cw, monkeypatch, [_make_ref(sub="Face3")])
        inserted = fake.input_edit.textCursor().insertText.call_args[0][0]
        assert inserted == "@Pad.Face3 "

    def test_present_token_not_duplicated(self, dock, monkeypatch):
        fake, cw = dock
        fake._attachment_strip.add_reference.return_value = False
        fake.input_edit.toPlainText.return_value = "why @Pad.Face3 ?"
        self._click(fake, cw, monkeypatch, [_make_ref(sub="Face3")])
        fake.input_edit.textCursor().insertText.assert_not_called()
        fake.input_edit.setTextCursor.assert_not_called()

    def test_trailing_period_counts_as_present(self, dock, monkeypatch):
        # "what about @Pad.Face3." — the token ends a sentence; it must
        # count as present (the old (?![\w.]) failed on the trailing dot).
        fake, cw = dock
        fake._attachment_strip.add_reference.return_value = False
        fake.input_edit.toPlainText.return_value = "what about @Pad.Face3."
        self._click(fake, cw, monkeypatch, [_make_ref(sub="Face3")])
        fake.input_edit.textCursor().insertText.assert_not_called()

    def test_rename_reclick_no_stale_token(self, dock, monkeypatch):
        # Token already typed, object renamed, Reference re-clicked:
        # the Name-keyed token is still present, so no second (stale,
        # label-based) token may be appended.
        fake, cw = dock
        fake._attachment_strip.add_reference.return_value = False
        fake.input_edit.toPlainText.return_value = "why @Pad.Face3 ?"
        self._click(fake, cw, monkeypatch, [_make_ref(sub="Face3", label="Base")])
        fake.input_edit.textCursor().insertText.assert_not_called()

    def test_prefix_names_do_not_confuse_presence_check(self, dock, monkeypatch):
        # "@Pad" is a substring of "@Pad001" and "@Pad.Face3" of
        # "@Pad.Face30", yet both chips' tokens are genuinely missing.
        fake, cw = dock
        fake._attachment_strip.add_reference.return_value = False
        fake.input_edit.toPlainText.return_value = "@Pad001 @Pad.Face30"
        self._click(fake, cw, monkeypatch, [
            _make_ref(sub=None), _make_ref(sub="Face3")])
        inserted = fake.input_edit.textCursor().insertText.call_args[0][0]
        assert inserted == "@Pad @Pad.Face3 "

    def test_reclick_refreshes_real_chip_snapshot(self):
        pytest.importorskip("PySide6", reason="PySide6 required for widget tests")
        from PySide6.QtWidgets import QApplication, QLabel
        app = QApplication.instance()
        if app is None:
            QApplication([])
        from freecad_ai.ui import chat_widget as cw
        from freecad_ai.ui.chat_widget import _AttachmentStrip

        cursor = MagicMock()
        cursor.position.return_value = 0
        cursor.insertText = MagicMock()
        input_edit = MagicMock()
        input_edit.toPlainText.return_value = ""
        input_edit.textCursor.return_value = cursor
        input_edit.setTextCursor = MagicMock()
        input_edit.setFocus = MagicMock()
        fake = SimpleNamespace(
            input_edit=input_edit,
            _attachment_strip=_AttachmentStrip(),
            _append_html=MagicMock(),
        )
        fake._add_selection_reference = types.MethodType(
            cw.ChatDockWidget._add_selection_reference, fake)

        first = _make_ref(sub="Face3", text="center z=10")
        with patch.object(cw, "references_from_selection", return_value=[first]):
            fake._add_selection_reference()
        assert fake._attachment_strip.get_references()[0]["text"] == "center z=10"
        cursor.insertText.assert_called_once()

        # Geometry moved; user re-clicks Reference with the token still in
        # the input: chip snapshot refreshes, token is not duplicated.
        cursor.insertText.reset_mock()
        fake.input_edit.toPlainText.return_value = "@Pad.Face3 "
        moved = _make_ref(sub="Face3", text="center z=60")
        with patch.object(cw, "references_from_selection", return_value=[moved]):
            fake._add_selection_reference()

        refs = fake._attachment_strip.get_references()
        assert len(refs) == 1                    # no second chip
        assert refs[0]["text"] == "center z=60"  # snapshot refreshed
        chip = fake._attachment_strip._items[0][0].findChild(QLabel)
        assert chip.toolTip() == "center z=60"
        cursor.insertText.assert_not_called()    # token not duplicated
