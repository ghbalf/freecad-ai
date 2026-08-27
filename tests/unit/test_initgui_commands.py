"""Tests for the checkable workbench commands in InitGui.py (#62).

FreeCAD ``exec``s ``InitGui.py`` inline rather than importing it, so these
tests reproduce that: stub ``FreeCAD``/``FreeCADGui`` in ``sys.modules`` and
exec the real file, then exercise the command classes out of the resulting
namespace.

The bug being pinned: ``"Checkable": True`` reads as the action's *initial*
tick state in FreeCAD 1.1.x, not as "this action can be checked", and
``IsChecked()`` is never called. So a command declaring ``True`` shows a
checkmark on a fresh session regardless of the state it is supposed to
reflect, and the tick never changes afterwards.
"""

import pathlib
import sys
import types

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def initgui(monkeypatch):
    """Exec InitGui.py against stubbed FreeCAD modules; yield its namespace."""
    gui = types.ModuleType("FreeCADGui")

    class _Workbench:
        """Stand-in for Gui.Workbench, which FreeCADAIWorkbench subclasses."""

    gui.Workbench = _Workbench
    gui.addCommand = lambda *a, **k: None
    gui.addWorkbench = lambda *a, **k: None
    gui.addPreferencePage = lambda *a, **k: None
    gui.Command = types.SimpleNamespace(get=lambda name: None)
    gui.getMainWindow = lambda: None
    gui.runCommand = lambda *a, **k: None

    app = types.ModuleType("FreeCAD")
    # paths.py walks these looking for the installed workbench directory.
    app.getUserAppDataDir = lambda: str(PROJECT_ROOT)
    app.getResourceDir = lambda: str(PROJECT_ROOT)

    def _no_param_store(path):
        # config.py treats RuntimeError as "no FreeCAD parameter store", the
        # same path it takes outside FreeCAD. Keeps the stub honest and small.
        raise RuntimeError("no parameter store in tests")

    app.ParamGet = _no_param_store
    app.Console = types.SimpleNamespace(
        PrintError=lambda *a, **k: None,
        PrintMessage=lambda *a, **k: None,
        PrintWarning=lambda *a, **k: None,
    )

    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)

    source = (PROJECT_ROOT / "InitGui.py").read_text()
    namespace = {}
    exec(compile(source, "InitGui.py", "exec"), namespace)
    return namespace


@pytest.fixture
def ticks(monkeypatch):
    """Capture every set_command_checked call instead of touching Qt."""
    recorded = {}
    import freecad_ai.ui.command_state as command_state

    def _record(name, checked):
        recorded[name] = bool(checked)
        return True

    monkeypatch.setattr(command_state, "set_command_checked", _record)
    return recorded


# ---------------------------------------------------------------------------
# The commands must not be born ticked
# ---------------------------------------------------------------------------

def test_keep_dock_command_does_not_start_ticked(initgui):
    """#62: "Checkable": True made the menu entry show a checkmark always."""
    resources = initgui["ToggleKeepDockCommand"]().GetResources()

    # The key must still be present — that is what makes the action checkable.
    assert "Checkable" in resources
    assert resources["Checkable"] is False


def test_mcp_server_command_does_not_start_ticked(initgui):
    resources = initgui["ToggleMCPServerCommand"]().GetResources()

    assert "Checkable" in resources
    assert resources["Checkable"] is False


# ---------------------------------------------------------------------------
# The tick is pushed by hand, since FreeCAD never asks for it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("persisted", [True, False])
def test_workbench_activation_syncs_the_keep_dock_tick(
        initgui, ticks, tmp_config_dir, persisted):
    from freecad_ai.config import get_config
    get_config().keep_dock_on_workbench_switch = persisted

    initgui["FreeCADAIWorkbench"]._sync_command_ticks(None)

    assert ticks["FreeCADAI_ToggleKeepDock"] is persisted


@pytest.mark.parametrize("running", [True, False])
def test_workbench_activation_syncs_the_mcp_tick(
        initgui, ticks, monkeypatch, tmp_config_dir, running):
    """A server started from the command line must show as running."""
    import freecad_ai.mcp.gui_server as gui_server
    monkeypatch.setattr(
        gui_server, "get_server_controller",
        lambda: types.SimpleNamespace(is_running=lambda: running))

    initgui["FreeCADAIWorkbench"]._sync_command_ticks(None)

    assert ticks["FreeCADAI_ToggleMCPServer"] is running


def test_toggling_keep_dock_pushes_the_new_state(initgui, ticks, tmp_config_dir):
    """Flipping the flag has to update the tick in the same breath."""
    from freecad_ai.config import get_config
    cfg = get_config()
    cfg.keep_dock_on_workbench_switch = True

    # True -> False, which takes the create=False branch and so needs no
    # QApplication to hide a dock that was never built.
    initgui["ToggleKeepDockCommand"]().Activated()

    assert cfg.keep_dock_on_workbench_switch is False
    assert ticks["FreeCADAI_ToggleKeepDock"] is False
