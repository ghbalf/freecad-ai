"""Run a skill's Python script inside FreeCAD the way a CLI would run it.

The script is not pasted into execute_code: a preamble would break
``from __future__`` imports and shift traceback line numbers. Instead
execute_code runs a small runpy wrapper, so the script sees
``__name__ == "__main__"``, its real ``__file__`` (for assets/), CLI-style
``sys.argv`` and its own directory on ``sys.path``.
"""

import os

_TEMPLATE = '''\
import sys as _fcai_sys, runpy as _fcai_runpy
_fcai_saved = (_fcai_sys.argv[:], _fcai_sys.path[:])
_fcai_sys.argv = {argv}
_fcai_sys.path.insert(0, {script_dir})
try:
    _fcai_runpy.run_path({script}, run_name="__main__", init_globals={{
        _k: _v for _k, _v in globals().items() if not _k.startswith("_")}})
except SystemExit as _fcai_exit:
    if _fcai_exit.code not in (None, 0):
        raise RuntimeError("script exited with status %r" % (_fcai_exit.code,))
finally:
    _fcai_sys.argv, _fcai_sys.path[:] = _fcai_saved
    for _fcai_name, _fcai_mod in list(_fcai_sys.modules.items()):
        if (getattr(_fcai_mod, "__file__", None) or "").startswith({dir_prefix}):
            del _fcai_sys.modules[_fcai_name]
'''


def build_script_wrapper(path: str, argv: list) -> str:
    """Python source that runs ``path`` as ``__main__`` with ``argv``.

    A clean ``sys.exit()`` counts as success; any other exit status is
    raised, because inside FreeCAD an uncaught SystemExit must never reach
    the host process. Modules imported from the script's own directory are
    dropped afterwards, so two skills that both ship ``utils.py`` each get
    their own.
    """
    script_dir = os.path.dirname(os.path.abspath(path))
    return _TEMPLATE.format(
        script=repr(path),
        argv=repr([path, *argv]),
        script_dir=repr(script_dir),
        dir_prefix=repr(script_dir + os.sep),
    )
