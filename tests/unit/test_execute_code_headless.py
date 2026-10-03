"""execute_code_headless: the tool handler (#114)."""

from types import SimpleNamespace

import pytest

import freecad_ai.core.executor as ex
from freecad_ai.core import headless
from freecad_ai.tools import executor_utils
from freecad_ai.tools import freecad_tools as ft


def _run(status="ok", stdout="", stderr="", error="", rc=0, run_dir="/r"):
    return headless.HeadlessRun(status, stdout, stderr, error, run_dir, rc, 1.2)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Binary found, safe mode, no document, the child faked."""
    import freecad_ai.core.dangerous_mode as dm
    state = {"calls": [], "dangerous": False, "run": _run(stdout="1000\n")}
    monkeypatch.setattr(dm, "get_dangerous_mode",
                        lambda: SimpleNamespace(active=state["dangerous"]))
    monkeypatch.setattr(ex, "_find_freecad_cmd", lambda: "/opt/fc/bin/freecadcmd")
    monkeypatch.setattr(headless, "new_run_dir", lambda: str(tmp_path))
    monkeypatch.setattr(headless, "save_active_copy", lambda path: None)

    def fake_run(code, **kw):
        state["calls"].append({"code": code, **kw})
        return state["run"]

    monkeypatch.setattr(headless, "run_headless", fake_run)
    return state


def test_success_returns_output_and_run_data(env, tmp_path):
    result = ft._handle_execute_code_headless("print(1000)")
    assert result.success and result.output == "1000"
    assert result.data == {"run_dir": "/r", "exit_code": 0, "duration_s": 1.2}
    call = env["calls"][0]
    assert call["timeout"] == 600 and call["document_path"] is None
    assert call["freecad_bin"] == "/opt/fc/bin/freecadcmd"
    assert call["run_dir"] == str(tmp_path)
    assert call["is_cancelled"] is executor_utils.current_thread_interrupted


def test_no_output_says_so(env):
    env["run"] = _run(stdout="")
    assert ft._handle_execute_code_headless("x = 1").output == "Code ran without output"


def test_stderr_is_appended(env):
    env["run"] = _run(stdout="a\n", stderr="careful\n")
    assert ft._handle_execute_code_headless("x").output == "a\n--- stderr ---\ncareful"


def test_failure_keeps_output_and_error(env):
    env["run"] = _run("error", stdout="before\n", error="Traceback…ZeroDivisionError")
    result = ft._handle_execute_code_headless("1/0")
    assert not result.success and result.output == "before"
    assert "ZeroDivisionError" in result.error and result.data["run_dir"] == "/r"


@pytest.mark.parametrize("status", ["timeout", "cancelled", "crashed", "no_result",
                                    "launch_failed"])
def test_every_failure_status_is_unsuccessful(env, status):
    env["run"] = _run(status, error="msg for " + status)
    result = ft._handle_execute_code_headless("x")
    assert not result.success and result.error == "msg for " + status


def test_output_is_capped(env):
    env["run"] = _run(stdout="x" * 25_000)
    out = ft._handle_execute_code_headless("x").output
    assert len(out) < 20_200 and "output.log" in out


def test_active_document_copy_is_passed_on(env, monkeypatch):
    seen = {}

    def save(path):
        seen["path"] = path
        return path

    monkeypatch.setattr(headless, "save_active_copy", save)
    ft._handle_execute_code_headless("x")
    assert seen["path"].endswith("input.FCStd")
    assert env["calls"][0]["document_path"] == seen["path"]


def test_copy_failure_is_reported(env, monkeypatch):
    def save(path):
        raise OSError("disk full")

    monkeypatch.setattr(headless, "save_active_copy", save)
    result = ft._handle_execute_code_headless("x")
    assert not result.success and result.error == "Could not copy the document: disk full"
    assert not env["calls"]


def test_copy_runs_through_run_on_main(env, monkeypatch):
    used = []
    monkeypatch.setattr(executor_utils, "run_on_main", lambda fn: used.append(fn) or fn())
    ft._handle_execute_code_headless("x")
    assert used


def test_missing_binary_names_the_places_searched(env, monkeypatch):
    monkeypatch.setattr(ex, "_find_freecad_cmd", lambda: "")
    result = ft._handle_execute_code_headless("x")
    assert not result.success and "freecadcmd" in result.error
    assert "AppImage" in result.error and not env["calls"]


def test_validation_refuses_in_safe_mode(env):
    result = ft._handle_execute_code_headless("import subprocess")
    assert not result.success and "subprocess" in result.error and not env["calls"]


def test_dangerous_mode_skips_validation(env):
    env["dangerous"] = True
    assert ft._handle_execute_code_headless("import subprocess").success


@pytest.mark.parametrize("given, used", [("60", 60), (0, 1), (-5, 1), (12.7, 12)])
def test_timeout_is_coerced(env, given, used):
    # Review focus 4
    ft._handle_execute_code_headless("x", timeout=given)
    assert env["calls"][0]["timeout"] == used


def test_junk_timeout_is_refused(env):
    result = ft._handle_execute_code_headless("x", timeout="soon")
    assert not result.success and "timeout" in result.error and not env["calls"]


def test_registered_off_the_main_thread():
    tool = next(t for t in ft.ALL_TOOLS if t.name == "execute_code_headless")
    assert tool.main_thread is False and tool.category == "general"
    assert [(p.name, p.required) for p in tool.parameters] == [("code", True),
                                                               ("timeout", False)]
    for phrase in ("WORK_DIR", "execute_code", "not changed", "60 s"):
        assert phrase in tool.description
