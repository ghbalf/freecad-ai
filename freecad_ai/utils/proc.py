"""Helpers for starting child processes without opening a console window.

On Windows a GUI process has no console of its own, so Windows allocates a
fresh one for every console child it starts. FreeCAD is a GUI process, which
means every headless child it launches opened an empty console window that sat
there for the life of the child. On Windows that is nearly every command: the
sandbox pre-check runs on every ``execute_code``.

None of the children here can use a console anyway — their output goes to pipes
(``capture_output``) or to files — so suppressing it costs nothing. On POSIX
neither flag exists and these helpers return the arguments the call sites
already passed.
"""

import os
import subprocess


def _no_window_flag() -> int:
    """``CREATE_NO_WINDOW`` where the platform has it, otherwise 0.

    Read through ``getattr`` so this stays importable on a platform or Python
    build that does not define the constant.
    """
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def hidden_process_kwargs() -> dict:
    """Keyword arguments that stop Windows allocating a console.

    Empty on POSIX, where a child never gets a window of its own and the flag
    does not exist, so call sites are unchanged there.
    """
    flags = _no_window_flag()
    return {"creationflags": flags} if flags else {}


def hidden_process_group_kwargs() -> dict:
    """Like :func:`hidden_process_kwargs`, for a child that also needs its own
    process group.

    POSIX keeps ``start_new_session``: an AppImage runs the real binary as a
    grandchild, which killing the direct child alone would leave running.
    Windows keeps ``CREATE_NEW_PROCESS_GROUP`` and additionally suppresses the
    console window, which is why the two flags are combined rather than
    swapped.
    """
    if os.name == "posix":
        return {"start_new_session": True}

    flags = _no_window_flag() | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags} if flags else {}
