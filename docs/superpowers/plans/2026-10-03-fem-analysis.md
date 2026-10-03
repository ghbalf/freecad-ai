# run_fem_analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a `run_fem_analysis` tool that solves an existing FEM analysis with CalculiX in a separate FreeCAD process, returns a stress/displacement summary, and imports the results into the open document.

**Architecture:** `core/fem.py` holds the child's solve code (run through #114's `run_headless`), the summary formatter, and the two GUI-thread steps (find analysis + save copy; purge + import results). `tools/fem_tools.py` holds the `main_thread=False` handler, which shares its start-up (timeout, binary, run folder) with `execute_code_headless` through a new `_start_headless` helper. (Deviation from the spec's "shared find-document-and-copy helper": the FEM copy step must also resolve the analysis in the same GUI-thread call, so `fem.prepare_copy` does both and only the start-up is shared.)

**Tech Stack:** Python 3.11, FreeCAD 1.1.1 FEM module (`ObjectsFem`, `femmesh.gmshtools`, `femtools.ccxtools`, `femresult.resulttools`, `feminout.importCcxFrdResults`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-fem-analysis-design.md`

## Global Constraints

- Branch `feat/113-fem-analysis`, based on `feat/114-headless-exec` (PR #117, unmerged).
- No external dependencies; CalculiX and Gmsh come with FreeCAD.
- Unit tests: `env PYTHONPATH= .venv/bin/pytest -q --ignore=tests/unit/test_document_attach.py`. Integration: add `-m integration` (needs the AppImage).
- Run the integration file with `env PYTHONPATH=$PWD` only if the child needs the repo; the unit suite always uses `env PYTHONPATH=`.
- Never `pkill FreeCAD`; kill a live-check GUI by its `setsid` process group.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Stress in MPa, displacement in mm, coordinates in mm.
- Format numbers in Python (`{:g}`), never with FreeCAD's `UserString` (locale: the probe got `"5,00 mm"`).
- macOS/Windows paths ship untested; say so in the docs.

## Review Focus

1. **Analysis names with quotes or non-ASCII labels:** embedded into the child code via `repr`, must not break the script. Test in Task 1.
2. **The user switches or closes the document during the solve:** the import targets the document captured before the solve (by name). If it is gone, the result is `success=True` with "Results not imported" and the `.frd` path. Test in Task 4.
3. **Locale-formatted mesh size:** the summary's `mesh_size` must be `"5 mm"`, not `"5,00 mm"`. The integration test in Task 1 checks it (the probe machine runs a German locale).
4. **Analysis selection by Label as well as Name, and several analyses with none named:** test in Task 2.
5. **The child ends "ok" without writing `summary.json`:** an error naming the run folder, not a crash in the handler. Test in Task 4.

---

### Task 1: Child solve code

**Files:**
- Create: `freecad_ai/core/fem.py`
- Test: `tests/unit/test_fem_core.py`, `tests/integration/test_fem_integration.py`

**Interfaces:**
- Consumes: `headless.run_headless(code, *, freecad_bin, run_dir, document_path, timeout, ...) -> HeadlessRun` and `headless.new_run_dir(base)` (#114). The headless harness defines `WORK_DIR` and `App` for the code.
- Produces: `fem.build_solve_code(analysis_name: str, mesh_size: str) -> str`. The child writes `WORK_DIR/summary.json`: either `{"error": str}` or the summary keys `analysis, solver_created, analysis_type, mesher, mesh_size (gmsh only), nodes, elements, ccx_warnings, frd, max_von_mises, max_von_mises_at, max_displacement (static only), elapsed_s`.

- [ ] **Step 1: Write the failing unit tests**

`tests/unit/test_fem_core.py`:

```python
"""core.fem: run_fem_analysis building blocks (#113)."""

import pytest

from freecad_ai.core import fem


def test_solve_code_compiles_and_embeds_the_values_safely():
    # Review focus 1
    name = "An'al\"ysis Ø"
    code = fem.build_solve_code(name, "3 mm")
    assert code.startswith("ANALYSIS = {!r}\nMESH_SIZE = {!r}\n".format(name, "3 mm"))
    compile(code, "solve.py", "exec")


def test_solve_code_writes_the_summary_into_the_run_folder():
    code = fem.build_solve_code("Analysis", "")
    assert 'os.path.join(WORK_DIR, "summary.json")' in code
    assert "UserString" not in code  # locale-formatted, see Global Constraints
    assert "Gmsh not found" in code and "Netgen" in code  # spec error table
```

- [ ] **Step 2: Run them to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_fem_core.py`
Expected: FAIL, `ImportError: cannot import name 'fem'`.

- [ ] **Step 3: Write `freecad_ai/core/fem.py`**

```python
"""run_fem_analysis building blocks (#113).

The solve runs in a headless FreeCAD child (core.headless, #114) on a copy of
the document; the GUI-thread steps find the analysis, save the copy and
import the results afterwards.
"""

# Runs in the child after "ANALYSIS = ...\nMESH_SIZE = ...\n". The headless
# harness provides App and WORK_DIR. Known problems end in {"error": msg};
# anything unexpected raises and reaches the tool as the child's traceback.
_SOLVE_CODE = r'''
import glob
import json
import os
import time

import ObjectsFem


def _fail(msg):
    return {"error": msg}


def _solve():
    t0 = time.time()
    doc = App.ActiveDocument
    analysis = doc.getObject(ANALYSIS) if doc else None
    if analysis is None:
        return _fail("Analysis {!r} not found in the document copy".format(ANALYSIS))
    summary = {"analysis": analysis.Label, "solver_created": False}

    solvers = [o for o in analysis.Group
               if getattr(getattr(o, "Proxy", None), "Type", "") == "Fem::SolverCcxTools"]
    if solvers:
        solver = solvers[0]
    else:
        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc)
        analysis.addObject(solver)
        summary["solver_created"] = True
    summary["analysis_type"] = getattr(solver, "AnalysisType", "static")

    meshes = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemMeshObject")
              and getattr(getattr(o, "Proxy", None), "Type", "") != "Fem::MeshResult"]
    if len(meshes) > 1:
        return _fail("The analysis has several meshes: {}. Keep one.".format(
            ", ".join(m.Label for m in meshes)))
    mesh = meshes[0] if meshes else None
    is_gmsh = mesh is None or getattr(getattr(mesh, "Proxy", None), "Type", "") == "Fem::FemMeshGmsh"
    if mesh is None:
        parts = []
        for o in analysis.Group:
            for ref in getattr(o, "References", None) or []:
                obj = ref[0]
                if obj not in parts and getattr(obj, "Shape", None) is not None \
                        and obj.Shape.Solids:
                    parts.append(obj)
        if len(parts) != 1:
            names = ", ".join(p.Label for p in parts) or "none"
            return _fail("Cannot pick the part to mesh: the constraints reference "
                         "{} solid object(s) ({}). Add a mesh to the analysis.".format(
                             len(parts), names))
        mesh = ObjectsFem.makeMeshGmsh(doc, "FEMMeshGmsh")
        mesh.Shape = parts[0]
        analysis.addObject(mesh)
        if not MESH_SIZE:
            bb = parts[0].Shape.BoundBox
            mesh.CharacteristicLengthMax = "{:.3g} mm".format(
                max(bb.XLength, bb.YLength, bb.ZLength) / 20.0)
    if is_gmsh:
        if MESH_SIZE:
            mesh.CharacteristicLengthMax = MESH_SIZE
        from femmesh.gmshtools import GmshTools
        try:
            err = GmshTools(mesh).create_mesh()
        except Exception as e:
            if type(e).__name__ == "GmshError":
                return _fail("Gmsh not found ({}). Install it, set its path in "
                             "Preferences → FEM → Gmsh, or add a Netgen mesh to "
                             "the analysis in FreeCAD.".format(str(e).strip()))
            return _fail("Meshing failed: {}".format(str(e).strip()))
        if err:
            return _fail("Meshing failed: {}".format(str(err).strip()))
        summary["mesher"] = "gmsh"
        summary["mesh_size"] = "{:g} mm".format(mesh.CharacteristicLengthMax.Value)
    else:
        summary["mesher"] = getattr(getattr(mesh, "Proxy", None), "Type", mesh.TypeId)
    fem_mesh = mesh.FemMesh
    summary["nodes"] = fem_mesh.NodeCount
    summary["elements"] = fem_mesh.VolumeCount or fem_mesh.FaceCount or fem_mesh.EdgeCount
    if not fem_mesh.NodeCount:
        return _fail("The mesh is empty after meshing")

    from femtools import ccxtools
    fea = ccxtools.FemToolsCcx(analysis, solver)
    try:
        fea.setup_ccx()
    except FileNotFoundError:
        return _fail("CalculiX (ccx) not found. Install it or set its path in "
                     "Preferences → FEM → CalculiX")
    fea.update_objects()
    work = os.path.join(WORK_DIR, "ccx")
    os.makedirs(work, exist_ok=True)
    fea.setup_working_dir(work)
    msg = fea.check_prerequisites()
    if msg:
        return _fail("The analysis is not ready: " + msg.strip())
    fea.purge_results()
    fea.write_inp_file()
    fea.ccx_run()
    stdout = getattr(fea, "ccx_stdout", "") or ""
    summary["ccx_warnings"] = [ln.strip() for ln in stdout.splitlines()
                               if "*WARNING" in ln.upper()][:20]
    frds = glob.glob(os.path.join(work, "*.frd"))
    if not frds:
        return _fail("CalculiX produced no results:\n"
                     + "\n".join(stdout.splitlines()[-20:]))
    summary["frd"] = frds[0]
    fea.load_results()
    results = [o for o in analysis.Group
               if o.isDerivedFrom("Fem::FemResultObjectPython")]
    if results and summary["analysis_type"] == "static":
        r = results[0]
        vm = list(r.vonMises)
        if vm:
            i = max(range(len(vm)), key=vm.__getitem__)
            summary["max_von_mises"] = vm[i]
            p = r.Mesh.FemMesh.Nodes[r.NodeNumbers[i]]
            summary["max_von_mises_at"] = [p.x, p.y, p.z]
        if len(r.DisplacementLengths):
            summary["max_displacement"] = max(r.DisplacementLengths)
    summary["elapsed_s"] = round(time.time() - t0, 2)
    return summary


with open(os.path.join(WORK_DIR, "summary.json"), "w", encoding="utf-8") as _f:
    json.dump(_solve(), _f)
'''


def build_solve_code(analysis_name, mesh_size):
    """The child code that solves ``analysis_name`` and writes summary.json."""
    return "ANALYSIS = {!r}\nMESH_SIZE = {!r}\n".format(
        analysis_name, mesh_size or "") + _SOLVE_CODE
```

- [ ] **Step 4: Run the unit tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_fem_core.py`
Expected: 2 passed.

- [ ] **Step 5: Write the integration test**

`tests/integration/test_fem_integration.py`:

```python
"""run_fem_analysis's solve code against a real FreeCAD + CalculiX (#113)."""

import json
import os

import pytest

from freecad_ai.core import fem, headless
from freecad_ai.core.executor import _find_freecad_cmd

pytestmark = pytest.mark.integration

_MAKE = '''
import ObjectsFem
doc = App.ActiveDocument
box = doc.addObject("Part::Box", "Beam")
box.Length, box.Width, box.Height = 100, 10, 10
doc.recompute()
an = ObjectsFem.makeAnalysis(doc, "Analysis")
if MATERIAL:
    mat = ObjectsFem.makeMaterialSolid(doc, "Steel")
    m = dict(mat.Material)
    m.update({"Name": "Steel", "YoungsModulus": "210000 MPa",
              "PoissonRatio": "0.3", "Density": "7900 kg/m^3"})
    mat.Material = m
    an.addObject(mat)
fix = ObjectsFem.makeConstraintFixed(doc, "Fixed")
fix.References = [(box, "Face1")]
an.addObject(fix)
force = ObjectsFem.makeConstraintForce(doc, "Force")
force.References = [(box, "Face2")]
force.Force = "1000 N"
force.Direction = (box, ["Edge5"])
an.addObject(force)
doc.recompute()
doc.saveAs(TARGET)
'''


@pytest.fixture
def freecad_bin():
    found = _find_freecad_cmd()
    if not found:
        pytest.skip("no FreeCAD console binary")
    return found


def _go(tmp_path, freecad_bin, code, document_path=None):
    run_dir = headless.new_run_dir(str(tmp_path / "headless"))
    run = headless.run_headless(code, freecad_bin=freecad_bin, run_dir=run_dir,
                                document_path=document_path, timeout=300)
    assert run.status == "ok", run.error
    return run


def _cantilever(tmp_path, freecad_bin, material=True):
    target = str(tmp_path / "cantilever.FCStd")
    _go(tmp_path, freecad_bin,
        "MATERIAL = {!r}\nTARGET = {!r}\n".format(material, target) + _MAKE)
    return target


def _summary(run):
    with open(os.path.join(run.run_dir, "summary.json"), encoding="utf-8") as f:
        return json.load(f)


def test_cantilever_solves_with_an_automatic_mesh(tmp_path, freecad_bin):
    doc = _cantilever(tmp_path, freecad_bin)
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    assert "error" not in s, s
    assert s["solver_created"] is True and s["mesher"] == "gmsh"
    assert s["mesh_size"] == "5 mm"  # Review focus 3: no locale comma
    assert s["nodes"] > 0 and s["elements"] > 0
    assert s["max_von_mises"] > 0 and s["max_displacement"] > 0
    assert len(s["max_von_mises_at"]) == 3
    assert os.path.getsize(s["frd"]) > 0


def test_mesh_size_is_applied(tmp_path, freecad_bin):
    doc = _cantilever(tmp_path, freecad_bin)
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", "3 mm"), doc))
    assert s["mesh_size"] == "3 mm"


def test_missing_material_is_reported(tmp_path, freecad_bin):
    doc = _cantilever(tmp_path, freecad_bin, material=False)
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    assert s == {"error": "The analysis is not ready: No material object defined "
                          "in the analysis."}
```

- [ ] **Step 6: Run the integration tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q -m integration tests/integration/test_fem_integration.py`
Expected: 3 passed (about 15 s).

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/core/fem.py tests/unit/test_fem_core.py tests/integration/test_fem_integration.py
git commit -m "feat(fem): child code that meshes and solves an analysis with CalculiX (#113)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: GUI-thread steps and the summary text

**Files:**
- Modify: `freecad_ai/core/fem.py`
- Test: `tests/unit/test_fem_core.py`, `tests/integration/test_fem_integration.py`

**Interfaces:**
- Consumes: `active_document.resolve_active_document()`; Task 1's summary keys.
- Produces:
  - `class FemError(Exception)`: a user-facing problem; its text is the tool error.
  - `prepare_copy(analysis: str, copy_path: str) -> tuple[str, str]`: on the GUI thread; returns `(doc.Name, analysis.Name)` after `doc.saveCopy(copy_path)`; raises `FemError`.
  - `import_results(analysis, frd_path: str) -> None`: purge, then import.
  - `import_into(doc_name: str, analysis_name: str, frd_path: str) -> None`: on the GUI thread; finds them by name, imports, recomputes. Raises if the document or analysis is gone.
  - `format_summary(summary: dict, import_note: str = "") -> str`.
  - `CONVERGENCE_NOTE: str`.

- [ ] **Step 1: Write the failing unit tests** (append to `tests/unit/test_fem_core.py`)

```python
import sys
from types import ModuleType, SimpleNamespace

from freecad_ai.core import active_document


def _obj(name, label=None, analysis=False):
    return SimpleNamespace(Name=name, Label=label or name,
                           isDerivedFrom=lambda t, a=analysis: a and t == "Fem::FemAnalysis")


@pytest.fixture
def doc(monkeypatch):
    d = SimpleNamespace(Name="Part1", Objects=[_obj("Box")], saved=[])
    d.saveCopy = d.saved.append
    monkeypatch.setattr(active_document, "resolve_active_document", lambda: d)
    return d


def test_prepare_copy_picks_the_only_analysis(doc):
    doc.Objects.append(_obj("Analysis", analysis=True))
    assert fem.prepare_copy("", "/r/input.FCStd") == ("Part1", "Analysis")
    assert doc.saved == ["/r/input.FCStd"]


@pytest.mark.parametrize("given", ["Analysis001", "Bracket study"])
def test_prepare_copy_matches_name_or_label(doc, given):
    # Review focus 4
    doc.Objects += [_obj("Analysis", analysis=True),
                    _obj("Analysis001", "Bracket study", analysis=True)]
    assert fem.prepare_copy(given, "/r/c")[1] == "Analysis001"


def test_several_analyses_and_none_named_is_refused(doc):
    doc.Objects += [_obj("Analysis", analysis=True),
                    _obj("Analysis001", "Bracket study", analysis=True)]
    with pytest.raises(fem.FemError, match="Analysis, Bracket study"):
        fem.prepare_copy("", "/r/c")
    assert not doc.saved


def test_unknown_name_lists_the_analyses(doc):
    doc.Objects.append(_obj("Analysis", analysis=True))
    with pytest.raises(fem.FemError, match="No analysis named 'Nope'.*Analysis"):
        fem.prepare_copy("Nope", "/r/c")


def test_no_analysis_is_refused(doc):
    with pytest.raises(fem.FemError, match="no FEM analysis"):
        fem.prepare_copy("", "/r/c")


def test_no_document_is_refused(monkeypatch):
    monkeypatch.setattr(active_document, "resolve_active_document", lambda: None)
    with pytest.raises(fem.FemError, match="No active document"):
        fem.prepare_copy("", "/r/c")


def test_import_results_purges_before_importing(monkeypatch):
    calls = []
    resulttools = ModuleType("femresult.resulttools")
    resulttools.purge_results = lambda an: calls.append(("purge", an))
    importer = ModuleType("feminout.importCcxFrdResults")
    importer.importFrd = lambda frd, an, prefix: calls.append(("import", frd, an, prefix))
    for name, mod in [("femresult", ModuleType("femresult")),
                      ("femresult.resulttools", resulttools),
                      ("feminout", ModuleType("feminout")),
                      ("feminout.importCcxFrdResults", importer)]:
        monkeypatch.setitem(sys.modules, name, mod)
    sys.modules["femresult"].resulttools = resulttools
    sys.modules["feminout"].importCcxFrdResults = importer
    fem.import_results("AN", "/r/ccx/Mesh.frd")
    assert calls == [("purge", "AN"), ("import", "/r/ccx/Mesh.frd", "AN", "CCX_")]


STATIC = {"analysis": "Analysis", "solver_created": False, "analysis_type": "static",
          "mesher": "gmsh", "mesh_size": "5 mm", "nodes": 198, "elements": 499,
          "ccx_warnings": [], "frd": "/r/ccx/Mesh.frd", "max_von_mises": 324.94,
          "max_von_mises_at": [100.0, 0.0, 10.0], "max_displacement": 1.1685,
          "elapsed_s": 1.4}


def test_static_summary():
    text = fem.format_summary(STATIC, "Results imported as CCX_Results.")
    assert text.splitlines()[:4] == [
        "FEM analysis 'Analysis' solved with CalculiX in 1.4 s.",
        "Mesh: gmsh, 5 mm, 198 nodes, 499 elements.",
        "Max von Mises stress: 324.9 MPa at (100, 0, 10) mm",
        "Max displacement: 1.168 mm"]
    assert text.index("Results imported") < text.index(fem.CONVERGENCE_NOTE)
    assert "solver" not in text.lower().replace("solved", "")


def test_created_solver_and_warnings_are_listed():
    s = dict(STATIC, solver_created=True,
             ccx_warnings=["*WARNING w{}".format(i) for i in range(25)])
    text = fem.format_summary(s)
    assert "a default CalculiX solver (SolverCcxTools) was used" in text
    assert "*WARNING w19" in text and "*WARNING w20" not in text


def test_non_static_summary_has_mesh_data_and_a_note():
    s = {k: v for k, v in STATIC.items()
         if k not in ("max_von_mises", "max_von_mises_at", "max_displacement")}
    s["analysis_type"] = "frequency"
    text = fem.format_summary(s)
    assert "198 nodes" in text and "MPa" not in text
    assert "only static analyses are summarised" in text
    assert fem.CONVERGENCE_NOTE not in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_fem_core.py`
Expected: the 2 Task-1 tests pass; the new ones FAIL with `AttributeError: module 'freecad_ai.core.fem' has no attribute ...`.

- [ ] **Step 3: Implement** (append to `freecad_ai/core/fem.py`)

```python
CONVERGENCE_NOTE = (
    "Note: a coarse mesh underestimates peak stress. Re-run with a smaller "
    "mesh_size and compare to check convergence.")


class FemError(Exception):
    """A problem the user can fix; the message is the tool's error text."""


def prepare_copy(analysis, copy_path):
    """Find the analysis and save a copy of its document. GUI thread only.

    Returns (document name, analysis object name).
    """
    from .active_document import resolve_active_document
    doc = resolve_active_document()
    if doc is None:
        raise FemError("No active document")
    analyses = [o for o in doc.Objects if o.isDerivedFrom("Fem::FemAnalysis")]
    if not analyses:
        raise FemError("The active document has no FEM analysis. Set one up "
                       "(analysis, material, constraints, loads) with execute_code first.")
    labels = ", ".join(a.Label for a in analyses)
    if analysis:
        found = [a for a in analyses if analysis in (a.Name, a.Label)]
        if not found:
            raise FemError("No analysis named {!r}. Analyses: {}".format(analysis, labels))
        chosen = found[0]
    elif len(analyses) > 1:
        raise FemError("Several analyses ({}): pass analysis to pick one.".format(labels))
    else:
        chosen = analyses[0]
    doc.saveCopy(copy_path)
    return doc.Name, chosen.Name


def import_results(analysis, frd_path):
    """Replace the analysis's results with those in ``frd_path``."""
    from femresult import resulttools
    from feminout import importCcxFrdResults
    resulttools.purge_results(analysis)
    importCcxFrdResults.importFrd(frd_path, analysis, "CCX_")


def import_into(doc_name, analysis_name, frd_path):
    """Import results into the document the solve started from. GUI thread only."""
    import FreeCAD as App
    doc = App.getDocument(doc_name)
    analysis = doc.getObject(analysis_name)
    if analysis is None:
        raise FemError("analysis {!r} no longer exists".format(analysis_name))
    import_results(analysis, frd_path)
    doc.recompute()


def format_summary(summary, import_note=""):
    """The text the LLM sees for a solved analysis."""
    lines = ["FEM analysis {!r} solved with CalculiX in {:g} s.".format(
        summary["analysis"], summary.get("elapsed_s", 0))]
    if summary.get("solver_created"):
        lines.append("The analysis had no CalculiX solver; a default CalculiX "
                     "solver (SolverCcxTools) was used for this run.")
    mesh = [summary["mesher"]]
    if summary.get("mesh_size"):
        mesh.append(summary["mesh_size"])
    mesh += ["{} nodes".format(summary["nodes"]), "{} elements".format(summary["elements"])]
    lines.append("Mesh: {}.".format(", ".join(mesh)))
    static = "max_von_mises" in summary
    if static:
        at = ", ".join("{:.4g}".format(c) for c in summary["max_von_mises_at"])
        lines.append("Max von Mises stress: {:.4g} MPa at ({}) mm".format(
            summary["max_von_mises"], at))
    if "max_displacement" in summary:
        lines.append("Max displacement: {:.4g} mm".format(summary["max_displacement"]))
    if not static:
        lines.append("Analysis type {!r}: only static analyses are summarised.".format(
            summary.get("analysis_type", "")))
    if summary.get("ccx_warnings"):
        lines.append("CalculiX warnings:")
        lines += summary["ccx_warnings"][:20]
    if import_note:
        lines.append(import_note)
    if static:
        lines.append(CONVERGENCE_NOTE)
    return "\n".join(lines)
```

- [ ] **Step 4: Run the unit tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_fem_core.py`
Expected: all pass.

- [ ] **Step 5: Add the import integration test** (append to `tests/integration/test_fem_integration.py`)

```python
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_results_import_and_replace_on_a_second_run(tmp_path, freecad_bin):
    doc = _cantilever(tmp_path, freecad_bin)
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    code = ("import sys\nsys.path.insert(0, {!r})\n"
            "from freecad_ai.core import fem\n"
            "an = App.ActiveDocument.getObject('Analysis')\n"
            "for _ in range(2):\n"
            "    fem.import_results(an, {!r})\n"
            "    App.ActiveDocument.recompute()\n"
            "print(sorted(o.Name for o in App.ActiveDocument.Objects))\n").format(_REPO, s["frd"])
    out = _go(tmp_path, freecad_bin, code, doc).stdout
    assert "'CCX_Results'" in out and "'Pipeline_CCX_Results'" in out
    assert "CCX_Results001" not in out
```

- [ ] **Step 6: Run the integration tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q -m integration tests/integration/test_fem_integration.py`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/core/fem.py tests/unit/test_fem_core.py tests/integration/test_fem_integration.py
git commit -m "feat(fem): find the analysis, import results, format the summary (#113)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Shared start of the headless tools

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (`_handle_execute_code_headless`)
- Test: `tests/unit/test_execute_code_headless.py`

**Interfaces:**
- Produces: `_start_headless(timeout) -> tuple[int, str, str] | ToolResult`, which returns `(timeout, freecad_bin, run_dir)` or a failed `ToolResult` (junk timeout, no binary). It creates the run folder only on success.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_execute_code_headless.py`)

```python
def test_start_headless_returns_timeout_binary_and_run_dir(env, tmp_path):
    assert ft._start_headless("60") == (60, "/opt/fc/bin/freecadcmd", str(tmp_path))


def test_start_headless_failures_are_tool_results(env, monkeypatch):
    assert "timeout" in ft._start_headless("soon").error
    monkeypatch.setattr(ex, "_find_freecad_cmd", lambda: "")
    assert "freecadcmd" in ft._start_headless(5).error
```

- [ ] **Step 2: Run to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_execute_code_headless.py`
Expected: the 2 new tests FAIL with `AttributeError: ... '_start_headless'`; the rest pass.

- [ ] **Step 3: Extract the helper.** In `freecad_tools.py`, add above `_handle_execute_code_headless`:

```python
def _start_headless(timeout):
    """Shared start of the headless tools (#114, #113).

    Returns (timeout, freecad_bin, run_dir), or a failed ToolResult.
    """
    from ..core import executor, headless
    try:
        timeout = max(1, int(float(timeout)))
    except (TypeError, ValueError):
        return ToolResult(success=False, output="",
                          error="timeout must be a number of seconds, got {!r}".format(timeout))
    freecad_bin = executor._find_freecad_cmd()
    if not freecad_bin:
        return ToolResult(success=False, output="", error=executor.FREECAD_CMD_SEARCHED)
    return timeout, freecad_bin, headless.new_run_dir()
```

and replace the body of `_handle_execute_code_headless` from the `try: timeout = ...` block down to `run_dir = headless.new_run_dir()` with:

```python
    if not get_dangerous_mode().active:
        warnings = executor._validate_code(code)
        if warnings:
            return ToolResult(success=False, output="",
                              error="Static validation failed:\n" + "\n".join(warnings))
    start = _start_headless(timeout)
    if isinstance(start, ToolResult):
        return start
    timeout, freecad_bin, run_dir = start
```

(Validation now runs before the timeout check, so refused code never creates a run folder.)

- [ ] **Step 4: Run the file**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_execute_code_headless.py`
Expected: all pass, the existing tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_execute_code_headless.py
git commit -m "refactor(tools): share the headless tools' start-up (#113)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The `run_fem_analysis` tool

**Files:**
- Create: `freecad_ai/tools/fem_tools.py`
- Modify: `freecad_ai/tools/setup.py` (register after `ALL_TOOLS`)
- Test: `tests/unit/test_run_fem_analysis.py`

**Interfaces:**
- Consumes: `_start_headless` (Task 3); `fem.prepare_copy`, `fem.build_solve_code`, `fem.import_into`, `fem.format_summary`, `fem.FemError` (Tasks 1–2); `headless.run_headless`, `executor_utils.run_on_main`, `executor_utils.current_thread_interrupted` (#114).
- Produces: `RUN_FEM_ANALYSIS: ToolDefinition` (`name="run_fem_analysis"`, `category="general"`, `main_thread=False`), registered by `create_default_registry`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_run_fem_analysis.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_run_fem_analysis.py`
Expected: FAIL, `ImportError: cannot import name 'fem_tools'`.

- [ ] **Step 3: Write `freecad_ai/tools/fem_tools.py`**

```python
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
```

In `freecad_ai/tools/setup.py`, after the `for tool in ALL_TOOLS:` loop:

```python
    from .fem_tools import RUN_FEM_ANALYSIS
    registry.register(RUN_FEM_ANALYSIS)
```

- [ ] **Step 4: Run the tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_run_fem_analysis.py`
Expected: all pass.

- [ ] **Step 5: Run the full unit suite**

Run: `env PYTHONPATH= .venv/bin/pytest -q --ignore=tests/unit/test_document_attach.py`
Expected: all pass (a test that pins the tool list or count, if any, is updated to include `run_fem_analysis`; ledger it).

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/fem_tools.py freecad_ai/tools/setup.py tests/unit/test_run_fem_analysis.py
git commit -m "feat(tools): run_fem_analysis solves an analysis with CalculiX (#113)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Docs and the live check

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Added`)
- Modify (wiki repo, local commit only): `/home/alf/Projects/programming/misc/freecad-ai-wiki/Tool-Reference.md`

- [ ] **Step 1: CHANGELOG entry** under `[Unreleased]` / `### Added`, after the `execute_code_headless` entry:

```markdown
- **`run_fem_analysis`** (#113): solves an existing FEM analysis with
  CalculiX in a separate FreeCAD process (FreeCAD stays responsive) and
  returns max von Mises stress, max displacement and mesh size. A Gmsh mesh
  is regenerated on every run (or created if the analysis has none, size
  via `mesh_size`); a missing CalculiX solver is added for the run. Results
  are imported into the open analysis, replacing earlier ones, so they can
  be colour-plotted. Setting up the analysis (material, constraints, loads)
  stays with `execute_code`. Tested on Linux only; the macOS/Windows paths
  to `ccx` and `gmsh` are untested.
```

- [ ] **Step 2: Wiki section** in `Tool-Reference.md`, next to `execute_code_headless`:

```markdown
### run_fem_analysis

Solves an existing FEM analysis with CalculiX in a separate FreeCAD
process and imports the results (`CCX_Results`, `Pipeline_CCX_Results`)
into the analysis. Parameters: `analysis` (name or label; default the
only one), `mesh_size` (e.g. `"3 mm"`), `timeout` (default 600 s).

Set up the analysis first (with `execute_code` or the FEM workbench):
material, at least one fixed constraint, a load. The tool adds a CalculiX
solver if there is none and (re)generates a Gmsh mesh every run.

The numbers depend on the mesh: a coarse mesh underestimates peak stress.
Re-run with a smaller `mesh_size` until the result stops changing.

`ccx` and `gmsh` ship with the FreeCAD AppImage. A custom path set in
Preferences → FEM is respected. Tested on Linux only.
```

Commit it in the wiki repo (`git -C /home/alf/Projects/programming/misc/freecad-ai-wiki commit -am "Tool reference: run_fem_analysis (#113)"`). Do not push.

- [ ] **Step 3: Live check (Xvfb GUI over MCP)**

Start a GUI FreeCAD the way #114's live check did (`setsid xvfb-run -a env QT_QPA_PLATFORM=xcb MCP_PORT=30000 ~/bin/freecad <scratchpad>/mcp_server_http.py &`), wait for `POST /mcp` to answer, then over MCP:
1. `execute_code`: build the cantilever from `_MAKE` in the integration test (without `saveAs`).
2. `run_fem_analysis` with `timeout=300`; while it runs, `get_document_state` must answer within about 1 s.
3. `get_document_state`: `CCX_Results` and `Pipeline_CCX_Results` exist.
4. `run_fem_analysis` again; `CCX_Results001` must not exist.
Kill the GUI by its process group. Record the timings and object lists in the ledger.

- [ ] **Step 4: Full suites**

Run: `env PYTHONPATH= .venv/bin/pytest -q --ignore=tests/unit/test_document_attach.py` and `env PYTHONPATH= .venv/bin/pytest -q -m integration tests/integration/`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): run_fem_analysis (#113)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
