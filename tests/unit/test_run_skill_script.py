"""run_skill_script: allowlisted .py files through execute_code."""

from types import SimpleNamespace

import pytest

import freecad_ai.extensions.skills as skills_mod
from freecad_ai.tools import freecad_tools as ft


@pytest.fixture
def skill(tmp_path, monkeypatch):
    sd = tmp_path / "skills" / "maker"
    (sd / "scripts").mkdir(parents=True)
    (sd / "SKILL.md").write_text("# Maker\nMakes things.\n")
    (sd / "scripts" / "make.py").write_text("print('made')\n")
    (sd / "scripts" / "run.sh").write_text("echo no\n")
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
    return sd


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_execute_code(code, skip_safety=False):
        recorded.append({"code": code, "skip_safety": skip_safety})
        return SimpleNamespace(success=True, stdout="made\n", stderr="")

    monkeypatch.setattr(ft, "execute_code", fake_execute_code)
    return recorded


def _dangerous(monkeypatch, active):
    import freecad_ai.core.dangerous_mode as dm
    monkeypatch.setattr(dm, "get_dangerous_mode", lambda: SimpleNamespace(active=active))


def test_runs_the_wrapper_with_split_args(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    result = ft._handle_run_skill_script("maker", "scripts/make.py", "--size 10 'a b'")
    assert result.success and result.output == "made"
    code = calls[0]["code"]
    assert "runpy" in code and repr(str(skill / "scripts" / "make.py")) in code
    assert "'--size', '10', 'a b'" in code
    assert calls[0]["skip_safety"] is False


def test_non_python_script_points_at_use_skill(skill, calls):
    result = ft._handle_run_skill_script("maker", "scripts/run.sh")
    assert not result.success and "use_skill" in result.error and not calls


def test_unknown_script_lists_scripts(skill, calls):
    result = ft._handle_run_skill_script("maker", "scripts/nope.py")
    assert not result.success and "scripts/make.py" in result.error


def test_unknown_skill(skill, calls):
    assert "Unknown skill" in ft._handle_run_skill_script("nope", "x.py").error


def test_dangerous_script_is_rejected_before_running(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    (skill / "scripts" / "make.py").write_text("import subprocess\n")
    result = ft._handle_run_skill_script("maker", "scripts/make.py")
    assert not result.success and "subprocess" in result.error and not calls


def test_dangerous_sibling_module_is_rejected_too(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    (skill / "scripts" / "helper.py").write_text("import subprocess\n")
    result = ft._handle_run_skill_script("maker", "scripts/make.py")
    assert not result.success and "scripts/helper.py" in result.error and not calls


def test_dangerous_mode_skips_validation(skill, calls, monkeypatch):
    _dangerous(monkeypatch, True)
    (skill / "scripts" / "make.py").write_text("import subprocess\n")
    assert ft._handle_run_skill_script("maker", "scripts/make.py").success
    assert calls[0]["skip_safety"] is True


def test_unbalanced_quotes_in_args(skill, calls):
    result = ft._handle_run_skill_script("maker", "scripts/make.py", "'open")
    assert not result.success and "args" in result.error and not calls


def test_registered_as_general_tool():
    tool = next(t for t in ft.ALL_TOOLS if t.name == "run_skill_script")
    assert tool.category == "general"
    assert [p.name for p in tool.parameters] == ["skill", "script", "args"]


def test_sourceless_pyc_is_rejected(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    (skill / "scripts" / "helper.pyc").write_bytes(b"\0\0")
    result = ft._handle_run_skill_script("maker", "scripts/make.py")
    assert not result.success and "scripts/helper.pyc" in result.error
    assert "Dangerous mode" in result.error and not calls


def test_pyc_skill_runs_in_dangerous_mode(skill, calls, monkeypatch):
    _dangerous(monkeypatch, True)
    (skill / "scripts" / "helper.pyc").write_bytes(b"\0\0")
    assert ft._handle_run_skill_script("maker", "scripts/make.py").success
    assert calls[0]["skip_safety"] is True


def test_pycache_pyc_is_allowed(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    (skill / "scripts" / "__pycache__").mkdir()
    (skill / "scripts" / "__pycache__" / "make.cpython-311.pyc").write_bytes(b"\0")
    assert ft._handle_run_skill_script("maker", "scripts/make.py").success


def test_escaping_symlink_py_is_rejected(skill, calls, monkeypatch, tmp_path):
    _dangerous(monkeypatch, False)
    outside = tmp_path / "outside.py"
    outside.write_text("import subprocess\n")
    (skill / "scripts" / "helper.py").symlink_to(outside)
    result = ft._handle_run_skill_script("maker", "scripts/make.py")
    assert not result.success and "scripts/helper.py" in result.error and not calls


def test_unknown_script_list_is_capped(skill, calls):
    for i in range(25):
        (skill / "scripts" / f"s{i:02d}.py").write_text("pass\n")
    err = ft._handle_run_skill_script("maker", "scripts/nope.py").error
    assert "…and 6 more" in err


def test_none_args_are_tolerated(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    assert ft._handle_run_skill_script("maker", "scripts/make.py", None).success


def test_escaping_symlinked_dir_is_rejected(skill, calls, monkeypatch, tmp_path):
    _dangerous(monkeypatch, False)
    outside = tmp_path / "outside_lib"
    outside.mkdir()
    (outside / "x.py").write_text("import os\n")
    (skill / "scripts" / "lib").symlink_to(outside, target_is_directory=True)
    result = ft._handle_run_skill_script("maker", "scripts/make.py")
    assert not result.success and "scripts/lib" in result.error and not calls


def test_internal_symlinked_dir_is_allowed(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    (skill / "real").mkdir()
    (skill / "real" / "x.py").write_text("pass\n")
    (skill / "scripts" / "lib").symlink_to(skill / "real", target_is_directory=True)
    assert ft._handle_run_skill_script("maker", "scripts/make.py").success


def test_file_cap_reached_is_refused(skill, calls, monkeypatch):
    _dangerous(monkeypatch, False)
    monkeypatch.setattr(skills_mod, "MAX_SKILL_FILES", 2)
    for i in range(3):
        (skill / "scripts" / f"extra{i}.py").write_text("pass\n")
    result = ft._handle_run_skill_script("maker", "scripts/extra0.py")
    assert not result.success and "Dangerous mode" in result.error and not calls
