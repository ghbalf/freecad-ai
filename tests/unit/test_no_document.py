"""No tool could create the first document (#149).

``execute_code`` refused to run without an active document, even code whose
job is to create one, and no first-class tool created or opened a document.
With the MCP server autostarted into an empty FreeCAD, an external agent
(#139) could list tools but do nothing at all.

The fake below models only what matters: ``newDocument``/``openDocument``
make the document active, names stay unique the way FreeCAD keeps them,
and there is no ``FreeCADGui`` (a console session), so
``resolve_active_document`` falls back to ``App.ActiveDocument``.
"""

import sys
from unittest.mock import patch

import pytest


class _Doc:
    def __init__(self, name, filename=""):
        self.Name = name
        self.Label = name
        self.FileName = filename
        self.Objects = []
        self.transactions = []

    def openTransaction(self, name):
        self.transactions.append(("open", name))

    def commitTransaction(self):
        self.transactions.append(("commit",))

    def abortTransaction(self):
        self.transactions.append(("abort",))

    def recompute(self):
        pass


class _FakeApp:
    def __init__(self):
        self._docs = {}
        self.ActiveDocument = None
        self.opened = []

    def listDocuments(self):
        return dict(self._docs)

    def _unique(self, name):
        n, i = name, 0
        while n in self._docs:
            i += 1
            n = f"{name}{i:03d}"
        return n

    def newDocument(self, name="Unnamed", label=None):
        doc = _Doc(self._unique(name))
        if label:
            doc.Label = label
        self._docs[doc.Name] = doc
        self.ActiveDocument = doc
        return doc

    def openDocument(self, path):
        import os
        self.opened.append(path)
        doc = _Doc(self._unique(os.path.splitext(os.path.basename(path))[0]),
                   filename=path)
        self._docs[doc.Name] = doc
        self.ActiveDocument = doc
        return doc

    def setActiveDocument(self, name):
        self.ActiveDocument = self._docs[name]

    def getDocument(self, name):
        if name not in self._docs:
            raise NameError(name)
        return self._docs[name]


@pytest.fixture
def app():
    fake = _FakeApp()
    # FreeCADGui=None makes ``import FreeCADGui`` raise ImportError.
    with patch.dict(sys.modules, {"FreeCAD": fake, "FreeCADGui": None}):
        yield fake


# --- execute_code ------------------------------------------------------------

def test_execute_code_runs_without_a_document(app):
    from freecad_ai.core import executor
    res = executor.execute_code("App.newDocument('Fresh')", sandbox=False)
    assert res.success, res.stderr
    assert app.ActiveDocument is not None
    assert app.ActiveDocument.Name == "Fresh"


def test_execute_code_without_a_document_still_reports_errors(app):
    from freecad_ai.core import executor
    res = executor.execute_code("raise ValueError('boom')", sandbox=False)
    assert not res.success
    assert "ValueError: boom" in res.stderr


def test_execute_code_with_a_document_keeps_its_transaction(app):
    """The existing-document path is unchanged: one undo transaction."""
    from freecad_ai.core import executor
    doc = app.newDocument("Existing")
    res = executor.execute_code("x = 1", sandbox=False)
    assert res.success, res.stderr
    assert doc.transactions == [("open", "AI Code Execution"), ("commit",)]


def test_execute_code_tool_reports_the_created_document(app):
    from freecad_ai.tools import freecad_tools as ft
    with patch("freecad_ai.core.dangerous_mode.get_dangerous_mode") as dm:
        dm.return_value.active = True  # no sandbox subprocess in a unit test
        r = ft._handle_execute_code("App.newDocument('Made')")
    assert r.success, r.error
    assert r.data["document"] == "Made"


# --- create_document ---------------------------------------------------------

def test_create_document_makes_it_active(app):
    from freecad_ai.tools import freecad_tools as ft
    r = ft._handle_create_document(name="Bracket")
    assert r.success, r.error
    assert app.ActiveDocument.Name == "Bracket"
    assert r.data["name"] == "Bracket"


def test_create_document_reports_the_name_freecad_chose(app):
    """FreeCAD renames a taken name; the agent must hear the real one."""
    from freecad_ai.tools import freecad_tools as ft
    ft._handle_create_document(name="Part")
    r = ft._handle_create_document(name="Part")
    assert r.success, r.error
    assert r.data["name"] == "Part001"
    assert "Part001" in r.output


def test_create_document_sets_the_label(app):
    from freecad_ai.tools import freecad_tools as ft
    r = ft._handle_create_document(name="Bracket", label="Wall bracket v2")
    assert r.success, r.error
    assert app.ActiveDocument.Label == "Wall bracket v2"


# --- open_document -----------------------------------------------------------

def test_open_document_opens_and_activates(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    f = tmp_path / "gear.FCStd"
    f.write_bytes(b"PK")
    r = ft._handle_open_document(path=str(f))
    assert r.success, r.error
    assert app.opened == [str(f)]
    assert app.ActiveDocument.FileName == str(f)


def test_open_document_missing_file_fails_without_opening(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    missing = tmp_path / "nope.FCStd"
    r = ft._handle_open_document(path=str(missing))
    assert not r.success
    assert str(missing) in r.error
    assert app.opened == []


def test_open_document_already_open_switches_instead(app, tmp_path):
    """FreeCAD refuses to open a file twice; switch to the open copy."""
    from freecad_ai.tools import freecad_tools as ft
    f = tmp_path / "gear.FCStd"
    f.write_bytes(b"PK")
    ft._handle_open_document(path=str(f))
    app.newDocument("Other")
    r = ft._handle_open_document(path=str(f))
    assert r.success, r.error
    assert app.opened == [str(f)]  # not opened a second time
    assert app.ActiveDocument.FileName == str(f)


# --- registration and guidance ----------------------------------------------

def test_tools_are_registered():
    from freecad_ai.tools.setup import create_default_registry
    names = {t.name for t in create_default_registry(include_mcp=False).list_tools()}
    assert {"create_document", "open_document"} <= names


def test_guidance_names_create_document():
    from freecad_ai.core.tool_guidance import TOOL_GUIDANCE
    assert "`create_document`" in TOOL_GUIDANCE
    assert "`open_document`" in TOOL_GUIDANCE


def test_modelling_tools_point_an_agent_at_create_document(app):
    """Live check of #149: the agent's first call was create_primitive, and
    "open a document in FreeCAD" named nothing it could do. The shared
    no-document error must name the tool that fixes it."""
    from freecad_ai.tools import freecad_tools as ft
    r = ft._with_undo("x", lambda doc: None)
    assert not r.success
    assert "create_document" in r.error
