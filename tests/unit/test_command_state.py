"""Tests for pushing checkmark state onto FreeCAD command actions (#62).

FreeCAD 1.1.x never calls a Python command's ``IsChecked()``, so every
checkable command has to drive its own tick and every place that changes the
underlying state has to push it. ``set_command_checked`` is that push.
"""

import sys
import types

from freecad_ai.ui.command_state import set_command_checked


class _FakeAction:
    """A QAction as far as this module is concerned.

    The real one hands its state change to everything connected *before*
    ``setChecked`` returns, and FreeCAD has the command's ``Activated()`` on
    the other end. ``on_fire`` models that; ``blockSignals`` silences it, the
    way it silences a real QAction.
    """

    def __init__(self, on_fire=None):
        self.checked = None
        self.fired = 0
        self._blocked = False
        self._on_fire = on_fire

    def blockSignals(self, block):
        was = self._blocked
        self._blocked = bool(block)
        return was

    def signalsBlocked(self):
        return self._blocked

    def setChecked(self, value):
        self.checked = value
        if not self._blocked and self._on_fire is not None:
            self.fired += 1
            self._on_fire()


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


def test_pushing_a_tick_does_not_re_enter_the_command(monkeypatch):
    """#88: Activated -> _sync_action -> set_command_checked -> Activated ...

    Both checkable commands push their tick from inside their own
    ``Activated``, and the workbench pushes both again on every activation.
    Unblocked, each push synthesised another activation: the stack grew until
    it ran out, the setting flipped once per level on the way down, and every
    flip wrote config.json.
    """
    activations = []

    def activated():
        activations.append(True)
        # What ToggleKeepDockCommand._sync_action does at the end of Activated.
        set_command_checked("FreeCADAI_ToggleKeepDock", True)

    action = _FakeAction(on_fire=activated)
    _install_fake_gui(monkeypatch,
                      {"FreeCADAI_ToggleKeepDock": _FakeCommand([action])})

    activated()

    assert action.checked is True
    assert action.fired == 0, "pushing a tick synthesised a user activation"
    assert activations == [True]


def test_signals_are_unblocked_again(monkeypatch):
    """Blocking lasts for the push, not for the action's life."""
    action = _FakeAction(on_fire=lambda: None)
    _install_fake_gui(monkeypatch, {"cmd": _FakeCommand([action])})

    set_command_checked("cmd", True)

    assert action.signalsBlocked() is False


def test_a_failed_push_still_unblocks_the_action(monkeypatch):
    """Otherwise one bad push leaves the toolbar button permanently deaf."""
    action = _FakeAction()
    action.setChecked = lambda value: (_ for _ in ()).throw(RuntimeError("boom"))
    _install_fake_gui(monkeypatch, {"cmd": _FakeCommand([action])})

    assert set_command_checked("cmd", True) is False
    assert action.signalsBlocked() is False
