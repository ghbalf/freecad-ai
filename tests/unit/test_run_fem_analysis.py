"""run_fem_analysis: the tool handler (#113)."""

import json
import os

import pytest

import freecad_ai.core.executor as ex
from freecad_ai.core import fem, headless
from freecad_ai.tools import executor_utils
from freecad_ai.tools import fem_tools as ftool

SUMMARY = {"analysis": "Analysis", "solver_created": False, "analysis_type": "static",
           "mesher": "gmsh", "mesh_size": "5 mm", "nodes": 198, "elements": 499,
           "ccx_warnings": [], "frd": "/r/ccx/Mesh.frd", "max_von_mises": 324.94,
           "max_von_mises_at": [100.0, 0.0, 10.0], "max_displacement": 1.1685,
           "elapsed_s": 1.4}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Binary found, one analysis, the child faked to write ``state['summary']``."""
    state = {"calls": [], "imports": [], "summary": SUMMARY, "status": "ok", "error": ""}
    monkeypatch.setattr(ex, "_find_freecad_cmd", lambda: "/opt/fc/bin/freecadcmd")
    monkeypatch.setattr(headless, "new_run_dir", lambda: str(tmp_path))
    monkeypatch.setattr(fem, "prepare_copy", lambda name, path: ("Part1", "Analysis"))
    monkeypatch.setattr(fem, "import_into",
                        lambda doc, an, frd: state["imports"].append((doc, an, frd)))

    def fake_run(code, **kw):
        state["calls"].append({"code": code, **kw})
        if state["summary"] is not None:
            with open(os.path.join(kw["run_dir"], "summary.json"), "w") as f:
                json.dump(state["summary"], f)
        return headless.HeadlessRun(state["status"], "", "", state["error"],
                                    kw["run_dir"], 0, 1.5)

    monkeypatch.setattr(headless, "run_headless", fake_run)
    return state


def test_success_summarises_and_imports(env, tmp_path):
    result = ftool._handle_run_fem_analysis()
    assert result.success
    assert "Max von Mises stress: 324.9 MPa" in result.output
    assert "Results imported as CCX_Results" in result.output
    assert env["imports"] == [("Part1", "Analysis", "/r/ccx/Mesh.frd")]
    assert result.data == {"run_dir": str(tmp_path), "summary": SUMMARY}
    call = env["calls"][0]
    assert call["code"] == fem.build_solve_code("Analysis", "")
    assert call["document_path"] == os.path.join(str(tmp_path), "input.FCStd")
    assert call["timeout"] == 600
    assert call["is_cancelled"] is executor_utils.current_thread_interrupted


def test_mesh_size_and_timeout_are_passed_on(env):
    ftool._handle_run_fem_analysis(mesh_size="3 mm", timeout="90")
    assert env["calls"][0]["code"] == fem.build_solve_code("Analysis", "3 mm")
    assert env["calls"][0]["timeout"] == 90


def test_analysis_problems_are_reported_without_a_run(env, monkeypatch):
    def refuse(name, path):
        raise fem.FemError("Several analyses (A, B): pass analysis to pick one.")
    monkeypatch.setattr(fem, "prepare_copy", refuse)
    result = ftool._handle_run_fem_analysis()
    assert not result.success and result.error.startswith("Several analyses")
    assert not env["calls"]


def test_copy_failure_is_reported(env, monkeypatch):
    def broken(name, path):
        raise OSError("disk full")
    monkeypatch.setattr(fem, "prepare_copy", broken)
    result = ftool._handle_run_fem_analysis()
    assert result.error == "Could not copy the document: disk full"


def test_solver_errors_from_the_child_are_the_tool_error(env, tmp_path):
    env["summary"] = {"error": "The analysis is not ready: No material object defined"}
    result = ftool._handle_run_fem_analysis()
    assert not result.success and "No material" in result.error
    assert result.data["run_dir"] == str(tmp_path) and not env["imports"]


@pytest.mark.parametrize("status", ["timeout", "cancelled", "crashed", "error"])
def test_failed_runs_report_the_runner_error(env, status):
    env["status"], env["error"], env["summary"] = status, "msg " + status, None
    result = ftool._handle_run_fem_analysis()
    assert not result.success and result.error == "msg " + status


def test_ok_run_without_a_summary_names_the_run_folder(env, tmp_path):
    # Review focus 5
    env["summary"] = None
    result = ftool._handle_run_fem_analysis()
    assert not result.success and str(tmp_path) in result.error


def test_failed_import_still_returns_the_summary(env, monkeypatch):
    # Review focus 2: document closed during the solve
    def gone(doc, an, frd):
        raise NameError("Unknown document 'Part1'")
    monkeypatch.setattr(fem, "import_into", gone)
    result = ftool._handle_run_fem_analysis()
    assert result.success and "324.9 MPa" in result.output
    assert "Results not imported: Unknown document 'Part1'" in result.output
    assert "/r/ccx/Mesh.frd" in result.output


def test_gui_steps_run_through_run_on_main(env, monkeypatch):
    used = []
    monkeypatch.setattr(executor_utils, "run_on_main", lambda fn: used.append(fn) or fn())
    ftool._handle_run_fem_analysis()
    assert len(used) == 2  # copy before, import after


def test_junk_timeout_is_refused(env):
    result = ftool._handle_run_fem_analysis(timeout="soon")
    assert not result.success and "timeout" in result.error and not env["calls"]


def test_registered_off_the_main_thread_in_the_default_registry():
    from freecad_ai.tools.setup import create_default_registry
    tool = create_default_registry(include_mcp=False).get("run_fem_analysis")
    assert tool is not None and tool.main_thread is False and tool.category == "general"
    assert [(p.name, p.required) for p in tool.parameters] == [
        ("analysis", False), ("mesh_size", False), ("timeout", False)]
