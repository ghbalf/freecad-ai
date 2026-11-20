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

    def save(self):
        if not self.FileName:
            raise ValueError("Object attribute 'FileName' is not set")
        with open(self.FileName, "wb") as f:
            f.write(b"PK saved")

    def saveAs(self, path):
        import os
        # Real FreeCAD renames the Label to the file stem on Save As.
        self.FileName = path
        self.Label = os.path.splitext(os.path.basename(path))[0]
        self.save()


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

    def closeDocument(self, name):
        doc = self._docs.pop(name)
        if self.ActiveDocument is doc:
            self.ActiveDocument = next(iter(self._docs.values()), None)


class _GDoc:
    def __init__(self, doc):
        self.Document = doc
        self.Modified = False


class _FakeGui:
    """Just the GUI document layer: the Modified flag lives here."""

    def __init__(self, app):
        self._app = app
        self._gdocs = {}

    def getDocument(self, name):
        return self._gdocs.setdefault(name, _GDoc(self._app._docs[name]))

    @property
    def ActiveDocument(self):
        doc = self._app.ActiveDocument
        return self.getDocument(doc.Name) if doc is not None else None

    def updateGui(self):
        pass


@pytest.fixture
def app():
    fake = _FakeApp()
    # FreeCADGui=None makes ``import FreeCADGui`` raise ImportError.
    with patch.dict(sys.modules, {"FreeCAD": fake, "FreeCADGui": None}):
        yield fake


@pytest.fixture
def gui_app():
    fake = _FakeApp()
    gui = _FakeGui(fake)
    with patch.dict(sys.modules, {"FreeCAD": fake, "FreeCADGui": gui}):
        yield fake, gui


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
    assert {"create_document", "open_document", "save_document",
            "close_document"} <= names


def test_guidance_names_create_document():
    from freecad_ai.core.tool_guidance import TOOL_GUIDANCE
    assert "`create_document`" in TOOL_GUIDANCE
    assert "`open_document`" in TOOL_GUIDANCE
    assert "`save_document`" in TOOL_GUIDANCE
    assert "`close_document`" in TOOL_GUIDANCE


def test_modelling_tools_point_an_agent_at_create_document(app):
    """Live check of #149: the agent's first call was create_primitive, and
    "open a document in FreeCAD" named nothing it could do. The shared
    no-document error must name the tool that fixes it."""
    from freecad_ai.tools import freecad_tools as ft
    r = ft._with_undo("x", lambda doc: None)
    assert not r.success
    assert "create_document" in r.error


def test_open_document_only_opens_freecad_documents(app, tmp_path):
    """Security review of #150: the tool opens documents, nothing else."""
    from freecad_ai.tools import freecad_tools as ft
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    r = ft._handle_open_document(path=str(f))
    assert not r.success
    assert ".FCStd" in r.error
    assert app.opened == []
    ok = tmp_path / "Backup.FCBak"
    ok.write_bytes(b"PK")
    assert ft._handle_open_document(path=str(ok)).success



# --- save_document (#152) ----------------------------------------------------

def test_save_in_place(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    f = tmp_path / "gear.FCStd"
    f.write_bytes(b"PK")
    ft._handle_open_document(path=str(f))
    r = ft._handle_save_document()
    assert r.success, r.error
    assert f.read_bytes() == b"PK saved"
    assert r.data["file"] == str(f)


def test_save_never_saved_document_needs_a_path(app):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Fresh")
    r = ft._handle_save_document()
    assert not r.success
    assert "path" in r.error


def test_save_as_reports_the_label_freecad_gives_it(app, tmp_path):
    """FreeCAD renames the Label to the file stem; the agent must hear it."""
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Fresh")
    target = tmp_path / "bracket.FCStd"
    r = ft._handle_save_document(path=str(target))
    assert r.success, r.error
    assert target.exists()
    assert app.ActiveDocument.FileName == str(target)
    assert r.data["label"] == "bracket"
    assert "bracket" in r.output


def test_save_as_appends_the_extension(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Fresh")
    r = ft._handle_save_document(path=str(tmp_path / "plate"))
    assert r.success, r.error
    assert (tmp_path / "plate.FCStd").exists()


def test_save_as_refuses_a_foreign_extension(app, tmp_path):
    """A .step path means an export, not a save."""
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Fresh")
    r = ft._handle_save_document(path=str(tmp_path / "plate.step"))
    assert not r.success
    assert "export_model" in r.error
    assert not (tmp_path / "plate.step").exists()


def test_save_as_will_not_overwrite_another_file_unless_told(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Fresh")
    other = tmp_path / "other.FCStd"
    other.write_bytes(b"PK precious")
    r = ft._handle_save_document(path=str(other))
    assert not r.success
    assert "overwrite" in r.error
    assert other.read_bytes() == b"PK precious"
    r = ft._handle_save_document(path=str(other), overwrite=True)
    assert r.success, r.error
    assert other.read_bytes() == b"PK saved"


def test_save_as_own_file_is_not_an_overwrite(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    f = tmp_path / "gear.FCStd"
    f.write_bytes(b"PK")
    ft._handle_open_document(path=str(f))
    r = ft._handle_save_document(path=str(f))
    assert r.success, r.error


def test_save_as_missing_folder_fails(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Fresh")
    r = ft._handle_save_document(path=str(tmp_path / "no" / "such" / "x.FCStd"))
    assert not r.success
    assert "folder" in r.error


def test_save_named_document_leaves_the_active_one(app, tmp_path):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Back")
    front = app.newDocument("Front")
    r = ft._handle_save_document(document="Back", path=str(tmp_path / "back.FCStd"))
    assert r.success, r.error
    assert app.getDocument("Back").FileName == str(tmp_path / "back.FCStd")
    assert app.ActiveDocument is front


def test_save_unknown_document_lists_the_open_ones(app):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Real")
    r = ft._handle_save_document(document="Ghost", path="/tmp/x.FCStd")
    assert not r.success
    assert "Ghost" in r.error and "Real" in r.error


def test_save_clears_the_gui_modified_flag(gui_app, tmp_path):
    """App-level save leaves Gui Modified set (probed on 1.1.1); a stale
    flag keeps the tab's * and makes close_document refuse a saved doc."""
    from freecad_ai.tools import freecad_tools as ft
    app, gui = gui_app
    app.newDocument("Fresh")
    gui.getDocument("Fresh").Modified = True
    r = ft._handle_save_document(path=str(tmp_path / "f.FCStd"))
    assert r.success, r.error
    assert gui.getDocument("Fresh").Modified is False


# --- close_document (#152) ---------------------------------------------------

def test_close_clean_document(gui_app):
    from freecad_ai.tools import freecad_tools as ft
    app, gui = gui_app
    app.newDocument("Keep")
    app.newDocument("Done")
    r = ft._handle_close_document(document="Done")
    assert r.success, r.error
    assert "Done" not in app.listDocuments()
    assert "Keep" in r.output  # says what is active now


def test_close_refuses_unsaved_changes(gui_app):
    """App.closeDocument discards changes without asking; the guard is ours."""
    from freecad_ai.tools import freecad_tools as ft
    app, gui = gui_app
    app.newDocument("Dirty")
    gui.getDocument("Dirty").Modified = True
    r = ft._handle_close_document(document="Dirty")
    assert not r.success
    assert "save_document" in r.error and "discard_changes" in r.error
    assert "Dirty" in app.listDocuments()
    r = ft._handle_close_document(document="Dirty", discard_changes=True)
    assert r.success, r.error
    assert "Dirty" not in app.listDocuments()


def test_close_without_gui_cannot_tell_so_it_refuses(app):
    """Headless FreeCAD has no unsaved-changes signal (probed on 1.1.1)."""
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Maybe")
    r = ft._handle_close_document(document="Maybe")
    assert not r.success
    assert "discard_changes" in r.error
    assert "Maybe" in app.listDocuments()
    assert ft._handle_close_document(document="Maybe", discard_changes=True).success


def test_close_last_document_says_none_is_open(gui_app):
    from freecad_ai.tools import freecad_tools as ft
    app, gui = gui_app
    app.newDocument("Only")
    r = ft._handle_close_document(document="Only")
    assert r.success, r.error
    assert "create_document" in r.output


# --- get_document_state(document=) (#152) -----------------------------------

def test_state_of_a_named_document_does_not_switch(app):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Other")
    front = app.newDocument("Front")
    r = ft._handle_get_document_state(document="Other")
    assert r.success, r.error
    assert 'Document: "Other"' in r.output
    assert app.ActiveDocument is front


def test_state_by_label(app):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Internal", label="Pretty")
    app.newDocument("Front")
    r = ft._handle_get_document_state(document="Pretty")
    assert r.success, r.error
    assert 'Document: "Internal"' in r.output


def test_state_of_unknown_document_fails(app):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Real")
    r = ft._handle_get_document_state(document="Ghost")
    assert not r.success
    assert "Real" in r.error


def test_state_without_argument_is_the_active_document(app):
    from freecad_ai.tools import freecad_tools as ft
    app.newDocument("Other")
    app.newDocument("Front")
    r = ft._handle_get_document_state()
    assert r.success, r.error
    assert 'Document: "Front"' in r.output


def test_save_as_never_overwrites_a_file_another_document_has_open(app, tmp_path):
    """overwrite=true replaces a file on disk, not a document someone has open."""
    from freecad_ai.tools import freecad_tools as ft
    f = tmp_path / "gear.FCStd"
    f.write_bytes(b"PK gear")
    ft._handle_open_document(path=str(f))
    app.newDocument("Fresh")
    r = ft._handle_save_document(path=str(f), overwrite=True)
    assert not r.success
    assert "open" in r.error and "close_document" in r.error
    assert f.read_bytes() == b"PK gear"
