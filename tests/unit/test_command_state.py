"""Tests for pushing checkmark state onto FreeCAD command actions (#62).

FreeCAD 1.1.x never calls a Python command's ``IsChecked()``, so every
checkable command has to drive its own tick and every place that changes the
underlying state has to push it. ``set_command_checked`` is that push.
"""

import sys
import types

from freecad_ai.ui.command_state import set_command_checked


class _FakeAction:
    def __init__(self):
        self.checked = None

    def setChecked(self, value):
        self.checked = value


class _FakeCommand:
    def __init__(self, actions):
        self._actions = actions

    def getAction(self):
        return self._actions


def _install_fake_gui(monkeypatch, commands):
    gui = types.ModuleType("FreeCADGui")
    gui.Command = types.SimpleNamespace(get=lambda name: commands.get(name))
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    return gui


def test_ticks_every_action_of_the_command(monkeypatch):
    actions = [_FakeAction(), _FakeAction()]
    _install_fake_gui(monkeypatch, {"FreeCADAI_ToggleKeepDock": _FakeCommand(actions)})

    assert set_command_checked("FreeCADAI_ToggleKeepDock", True) is True
    assert [a.checked for a in actions] == [True, True]


def test_unticks_and_coerces_to_bool(monkeypatch):
    action = _FakeAction()
    _install_fake_gui(monkeypatch, {"cmd": _FakeCommand([action])})

    set_command_checked("cmd", 0)

    # Qt's setChecked wants a real bool, not a truthy value.
    assert action.checked is False


def test_unregistered_command_is_a_quiet_no_op(monkeypatch):
    _install_fake_gui(monkeypatch, {})

    assert set_command_checked("FreeCADAI_Nope", True) is False


def test_no_freecad_gui_is_a_quiet_no_op(monkeypatch):
    """Headless FreeCAD and the test suite have no FreeCADGui at all."""
    monkeypatch.setitem(sys.modules, "FreeCADGui", None)

    assert set_command_checked("anything", True) is False
