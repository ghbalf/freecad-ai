"""build_script_wrapper, exec'd in plain Python (no FreeCAD needed)."""

import sys

import pytest

from freecad_ai.extensions.skill_scripts import build_script_wrapper


def _run(path, argv=(), ns=None):
    ns = {"__builtins__": __builtins__} if ns is None else ns
    exec(build_script_wrapper(str(path), list(argv)), ns)


def _script(tmp_path, body, name="main.py", sub="scripts"):
    d = tmp_path / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body)
    return p


def test_runs_as_main_with_file_and_argv(tmp_path, capsys):
    p = _script(tmp_path, "import sys\nif __name__ == '__main__':\n"
                          "    print(__file__, sys.argv[1:])\n")
    _run(p, ["--size", "a b"])
    assert capsys.readouterr().out.strip() == f"{p} ['--size', 'a b']"


def test_future_import_works(tmp_path, capsys):
    _run(_script(tmp_path, "from __future__ import annotations\nprint('ok')\n"))
    assert capsys.readouterr().out == "ok\n"


def test_freecad_globals_are_passed_in(tmp_path, capsys):
    _run(_script(tmp_path, "print(App)\n"), ns={"__builtins__": __builtins__, "App": "APP"})
    assert capsys.readouterr().out == "APP\n"


@pytest.mark.parametrize("call", ["sys.exit()", "sys.exit(0)"])
def test_clean_exit_is_success(tmp_path, call):
    _run(_script(tmp_path, f"import sys\n{call}\n"))


def test_nonzero_exit_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="status 2"):
        _run(_script(tmp_path, "import sys\nsys.exit(2)\n"))


def test_argv_and_path_restored_after_exception(tmp_path):
    argv, path = sys.argv[:], sys.path[:]
    with pytest.raises(ValueError):
        _run(_script(tmp_path, "raise ValueError('x')\n"), ["a"])
    assert sys.argv == argv and sys.path == path


def test_sibling_import(tmp_path, capsys):
    _script(tmp_path, "VALUE = 'sibling'\n", name="helper.py")
    _run(_script(tmp_path, "import helper\nprint(helper.VALUE)\n"))
    assert capsys.readouterr().out == "sibling\n"


def test_same_named_sibling_modules_do_not_leak_between_skills(tmp_path, capsys):
    for skill in ("one", "two"):
        _script(tmp_path, f"VALUE = '{skill}'\n", name="fcai_utils.py", sub=f"{skill}/scripts")
        _script(tmp_path, "import fcai_utils\nprint(fcai_utils.VALUE)\n", sub=f"{skill}/scripts")
    _run(tmp_path / "one" / "scripts" / "main.py")
    _run(tmp_path / "two" / "scripts" / "main.py")
    assert capsys.readouterr().out == "one\ntwo\n"
    assert "fcai_utils" not in sys.modules
