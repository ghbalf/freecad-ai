"""run_fem_analysis: solve a FEM analysis with CalculiX in a headless child (#113)."""

import json
import os

from .registry import ToolParam, ToolDefinition, ToolResult


def _handle_run_fem_analysis(analysis="", mesh_size="", timeout=600) -> ToolResult:
    """Registered with main_thread=False: only the copy and the import run on
    the GUI thread; the solve runs in a separate FreeCAD process."""
    from ..core import fem, headless
    from . import executor_utils
    from .freecad_tools import _start_headless

    start = _start_headless(timeout)
    if isinstance(start, ToolResult):
        return start
    timeout, freecad_bin, run_dir = start
    copy_path = os.path.join(run_dir, "input.FCStd")
    try:
        doc_name, analysis_name = executor_utils.run_on_main(
            lambda: fem.prepare_copy(analysis, copy_path))
    except fem.FemError as e:
        return ToolResult(success=False, output="", error=str(e))
    except Exception as e:
        return ToolResult(success=False, output="",
                          error="Could not copy the document: {}".format(e))

    run = headless.run_headless(
        fem.build_solve_code(analysis_name, mesh_size), freecad_bin=freecad_bin,
        run_dir=run_dir, document_path=copy_path, timeout=timeout,
        is_cancelled=executor_utils.current_thread_interrupted)
    data = {"run_dir": run_dir}
    if run.status != "ok":
        return ToolResult(success=False, output="", data=data, error=run.error)
    try:
        with open(os.path.join(run_dir, "summary.json"), encoding="utf-8") as f:
            summary = json.load(f)
    except (OSError, ValueError):
        return ToolResult(success=False, output="", data=data,
                          error="The solver run ended without a summary; see " + run_dir)
    data["summary"] = summary
    if "error" in summary:
        return ToolResult(success=False, output="", data=data, error=summary["error"])

    try:
        executor_utils.run_on_main(
            lambda: fem.import_into(doc_name, analysis_name, summary["frd"]))
        note = "Results imported as CCX_Results (colour plot via Pipeline_CCX_Results)."
    except Exception as e:
        note = "Results not imported: {}. Results file: {}".format(e, summary["frd"])
    return ToolResult(success=True, output=fem.format_summary(summary, note), data=data)


RUN_FEM_ANALYSIS = ToolDefinition(
    name="run_fem_analysis",
    description=(
        "Solve an existing FEM analysis (Fem::FemAnalysis) with CalculiX in a "
        "separate FreeCAD process and return max von Mises stress (MPa), max "
        "displacement (mm) and mesh size. Set up the analysis first with "
        "execute_code: material, fixed constraint, loads. A missing CalculiX "
        "solver is added for the run; a Gmsh mesh is (re)generated every time, "
        "or created if the analysis has none. The model is not changed: only "
        "result objects (CCX_Results, Pipeline_CCX_Results) are added, "
        "replacing earlier ones. Results depend on the mesh: re-run with a "
        "smaller mesh_size to check convergence. timeout defaults to 600 s; "
        "pass a smaller one for quick jobs, because some MCP clients give up "
        "after about 60 s."),
    category="general",
    parameters=[
        ToolParam("analysis", "string",
                  "Analysis name or label (default: the only analysis)",
                  required=False, default=""),
        ToolParam("mesh_size", "string",
                  "Max element size for the Gmsh mesh, e.g. '3 mm' "
                  "(default: keep the mesh's setting, or 1/20 of the part size)",
                  required=False, default=""),
        ToolParam("timeout", "integer",
                  "Seconds before the solve is killed (default 600)",
                  required=False, default=600),
    ],
    handler=_handle_run_fem_analysis,
    main_thread=False,
)
