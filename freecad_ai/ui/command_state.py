"""Pushing checkmark state onto FreeCAD command actions.

FreeCAD 1.1.x never calls a Python command's ``IsChecked()``:
``Gui::PythonCommand::isChecked()`` returns ``GetResources()["Checkable"]``,
and that value is only the action's *initial* state — the key's mere presence
is what makes the action checkable. A checkable Python command must therefore
drive its own tick, and so must anything that changes the underlying state
from somewhere else (the Settings dialog, another command, a config reload).

Lives in a real module rather than ``InitGui.py`` because FreeCAD ``exec``s
that file inline: its module-level names are not importable from anywhere
else, and the Settings dialog needs this too.
"""


def set_command_checked(command_name, checked):
    """Tick or untick every action registered for ``command_name``.

    Returns True when the tick was applied. A missing FreeCAD GUI (headless,
    tests) or an unregistered command is a quiet no-op returning False, so
    callers need no guards of their own.
    """
    try:
        import FreeCADGui as Gui
        command = Gui.Command.get(command_name)
        if command is None:
            return False
        for action in command.getAction():
            action.setChecked(bool(checked))
        return True
    except Exception:
        return False
