"""Regression tests for the list_documents tool (#57).

``App.Document`` has no ``Modified`` attribute — the dirty flag lives on the
*Gui* document instead. Reading ``doc.Modified`` raised AttributeError, which
broke the tool for every user, not just the Flatpak reporter. Verified against
FreeCAD 1.1.1 (AppImage)::

    App.Document has Modified attr: False
    reading .Modified raised: AttributeError:
        'App.Document' object has no attribute 'Modified'

The fakes below are deliberately plain classes rather than MagicMocks: a
MagicMock auto-creates ``Modified`` on access and would hide the very bug
these tests exist to catch.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch


class _AppDocument:
    """Stand-in for App.Document — has no ``Modified``, like the real thing."""

    def __init__(self, name, obj_count=0, filename=""):
        self.Name = name
        self.Label = name
        self.Objects = [MagicMock() for _ in range(obj_count)]
        self.FileName = filename


def _gui_module(modified_by_name=None, raises=False):
    """Build a fake FreeCADGui whose documents carry the dirty flag."""
    gui = MagicMock()
    if raises:
        gui.getDocument.side_effect = RuntimeError("no such document")
        return gui

    modified_by_name = modified_by_name or {}

    def get_document(name):
        if name not in modified_by_name:
            return None
        gdoc = MagicMock()
        gdoc.Modified = modified_by_name[name]
        return gdoc

    gui.getDocument.side_effect = get_document
    return gui


def _run(docs, gui=None, active=None):
    """Invoke the handler with FreeCAD/FreeCADGui faked out.

    ``gui=None`` simulates a headless session: binding FreeCADGui to None in
    sys.modules makes ``import FreeCADGui`` raise ImportError, which is exactly
    what happens under the STDIO MCP server entry point.
    """
    app = MagicMock()
    app.listDocuments.return_value = {d.Name: d for d in docs}

    with patch.dict(sys.modules, {"FreeCAD": app, "FreeCADGui": gui}):
        with patch(
            "freecad_ai.core.active_document.resolve_active_document",
            return_value=active,
        ):
            from freecad_ai.tools.freecad_tools import _handle_list_documents

            return _handle_list_documents()


class TestListDocumentsModifiedFlag(unittest.TestCase):
    def test_succeeds_when_app_document_has_no_modified_attribute(self):
        """The #57 regression: the handler must not touch App.Document.Modified."""
        doc = _AppDocument("Unnamed", obj_count=2)

        result = _run([doc], gui=_gui_module({"Unnamed": False}), active=doc)

        self.assertTrue(result.success)
        self.assertIn("Unnamed", result.output)

    def test_reports_modified_from_the_gui_document(self):
        doc = _AppDocument("Dirty", obj_count=1)

        result = _run([doc], gui=_gui_module({"Dirty": True}), active=doc)

        self.assertTrue(result.data["documents"][0]["modified"])
        # The dirty marker is " *" before the separator. Asserting on a bare
        # "*" would match the markdown bold around the document name and pass
        # no matter what the flag says.
        self.assertIn(" * —", result.output)

    def test_clean_gui_document_is_not_marked_modified(self):
        doc = _AppDocument("Clean", obj_count=1)

        result = _run([doc], gui=_gui_module({"Clean": False}), active=doc)

        self.assertFalse(result.data["documents"][0]["modified"])
        self.assertNotIn(" * —", result.output)

    def test_headless_session_reports_not_modified(self):
        """No FreeCADGui at all — the MCP STDIO entry point runs this way."""
        doc = _AppDocument("Headless", obj_count=1)

        result = _run([doc], gui=None, active=doc)

        self.assertTrue(result.success)
        self.assertFalse(result.data["documents"][0]["modified"])

    def test_document_unknown_to_the_gui_reports_not_modified(self):
        doc = _AppDocument("Ghost", obj_count=1)

        result = _run([doc], gui=_gui_module({}), active=doc)

        self.assertTrue(result.success)
        self.assertFalse(result.data["documents"][0]["modified"])

    def test_gui_lookup_failure_is_swallowed(self):
        doc = _AppDocument("Boom", obj_count=1)

        result = _run([doc], gui=_gui_module(raises=True), active=doc)

        self.assertTrue(result.success)
        self.assertFalse(result.data["documents"][0]["modified"])

    def test_active_marker_still_applied(self):
        """Guard the surrounding behaviour the fix reaches into."""
        a = _AppDocument("A", obj_count=1)
        b = _AppDocument("B", obj_count=1)

        result = _run([a, b], gui=_gui_module({"A": False, "B": False}), active=b)

        by_name = {d["name"]: d for d in result.data["documents"]}
        self.assertFalse(by_name["A"]["active"])
        self.assertTrue(by_name["B"]["active"])


if __name__ == "__main__":
    unittest.main()
