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
    registered = {}
    gui.addCommand = lambda name, obj, *a, **k: registered.__setitem__(name, obj)
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
    namespace["_REGISTERED_COMMANDS"] = registered
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

    initgui["ToggleKeepDockCommand"]().Activated()

    assert cfg.keep_dock_on_workbench_switch is False
    assert ticks["FreeCADAI_ToggleKeepDock"] is False


# ---------------------------------------------------------------------------
# A rejected allowed-hosts list must fail the click, not the session
# ---------------------------------------------------------------------------

def test_toggle_reports_a_rejected_allowed_hosts_list(initgui, ticks,
                                                      monkeypatch):
    """MCP_ALLOWED_HOSTS="*" is refused by resolve_allowed_hosts.

    That raises ValueError, not the OSError the bind path raises, so without
    handling it the toggle propagates out of Activated() into FreeCAD's
    command dispatcher — a console traceback and a button left mid-state.

    The rejection must also happen before any bind is attempted, so this
    never depends on a port being free.
    """
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")

    import freecad_ai.mcp.gui_server as gui_server

    def _must_not_bind(self, *args, **kwargs):
        raise AssertionError("start() ran despite a rejected allowlist")

    monkeypatch.setattr(gui_server.ServerController, "start", _must_not_bind)

    command = initgui["ToggleMCPServerCommand"]()
    reported = []
    monkeypatch.setattr(type(command), "_report_failure",
                        lambda self, host, port, exc: reported.append(exc))

    command.Activated()

    assert len(reported) == 1
    assert isinstance(reported[0], ValueError)
    assert "*" in str(reported[0])
    assert ticks["FreeCADAI_ToggleMCPServer"] is False


# ---------------------------------------------------------------------------
# Restore from Backup (#49) — menu only, by decision
# ---------------------------------------------------------------------------

def _shelves(initgui):
    """Run Initialize() with the shelf calls captured."""
    wb = initgui["FreeCADAIWorkbench"]()
    toolbar, menu = {}, {}
    wb.appendToolbar = lambda name, cmds: toolbar.update({name: cmds})
    wb.appendMenu = lambda name, cmds: menu.update({name: cmds})
    wb.Initialize()
    return toolbar["FreeCAD AI"], menu["FreeCAD AI"]


def test_restore_backup_is_reachable_from_the_menu(initgui):
    """#48 wrote snapshots nothing could read. An unreachable dialog would
    leave the feature exactly as useful as it was before."""
    _, menu = _shelves(initgui)

    assert "FreeCADAI_RestoreBackup" in menu


def test_restore_backup_stays_off_the_toolbar(initgui):
    """Deliberate: recovery is a rare, deliberate act, and a one-click button
    next to the everyday chat and settings icons invites the mis-click."""
    toolbar, _ = _shelves(initgui)

    assert "FreeCADAI_RestoreBackup" not in toolbar


def test_restore_backup_command_is_registered(initgui):
    """A name in the menu that was never handed to addCommand renders as a
    dead entry, with no error anywhere."""
    assert "FreeCADAI_RestoreBackup" in initgui["_REGISTERED_COMMANDS"]


def test_restore_backup_is_always_available(initgui):
    """It must work with no document open — recovering from a crash is
    precisely the case where nothing is loaded."""
    assert initgui["RestoreBackupCommand"]().IsActive() is True


# ---------------------------------------------------------------------------
# The setting governs leaving the workbench, and nothing else
# ---------------------------------------------------------------------------

class _FakeDock:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def raise_(self):
        self.calls.append("raise_")


@pytest.fixture
def dock(monkeypatch):
    """Stand in for the chat dock and record what is done to it."""
    import freecad_ai.ui.chat_widget as chat_widget
    fake = _FakeDock()
    monkeypatch.setattr(chat_widget, "get_chat_dock", lambda create=True: fake)
    return fake


@pytest.mark.parametrize("before", [True, False])
def test_toggling_keep_dock_leaves_the_panel_where_it_is(
        initgui, ticks, tmp_config_dir, dock, before):
    """Unticking it used to hide the panel on the spot.

    That happens inside the FreeCAD AI workbench -- the one workbench the
    panel belongs to -- so the panel vanished the moment the setting was
    turned off, which is not what "keep open when switching workbenches"
    means. The Settings dialog changes the same flag and never touched
    visibility; the menu entry now agrees with it. Showing and hiding the
    panel is the Open AI Chat command's job.
    """
    from freecad_ai.config import get_config
    get_config().keep_dock_on_workbench_switch = before

    initgui["ToggleKeepDockCommand"]().Activated()

    assert dock.calls == []


def test_leaving_the_workbench_hides_the_panel_when_the_flag_is_off(
        initgui, tmp_config_dir, dock):
    from freecad_ai.config import get_config
    get_config().keep_dock_on_workbench_switch = False

    initgui["FreeCADAIWorkbench"].Deactivated(None)

    assert dock.calls == ["hide"]


def test_leaving_the_workbench_keeps_the_panel_when_the_flag_is_on(
        initgui, tmp_config_dir, dock):
    """This is the whole point of the setting."""
    from freecad_ai.config import get_config
    get_config().keep_dock_on_workbench_switch = True

    initgui["FreeCADAIWorkbench"].Deactivated(None)

    assert dock.calls == []
