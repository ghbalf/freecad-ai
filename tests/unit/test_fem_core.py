"""core.fem: run_fem_analysis building blocks (#113)."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from freecad_ai.core import active_document, fem


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
        "Max displacement: 1.169 mm"]
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


def _typed(name, type_id, label=None):
    return SimpleNamespace(Name=name, Label=label or name,
                           isDerivedFrom=lambda t, k=type_id: t == k)


def test_import_into_returns_the_labels_it_created(monkeypatch):
    # Live check: in the GUI a re-imported CCX_Results keeps its Name but is
    # labelled CCX_Results001, and get_document_state shows labels
    an = SimpleNamespace(Group=[_typed("Steel", "App::MaterialObjectPython")])
    monkeypatch.setattr(fem, "import_results", lambda a, frd: a.Group.extend([
        _typed("CCX_Results", "Fem::FemResultObjectPython", "CCX_Results001"),
        _typed("Pipeline_CCX_Results", "Fem::FemPostPipeline")]))
    doc = SimpleNamespace(getObject={"Analysis": an}.get, recompute=lambda: None)
    freecad = ModuleType("FreeCAD")
    freecad.getDocument = {"Part1": doc}.__getitem__
    monkeypatch.setitem(sys.modules, "FreeCAD", freecad)
    assert fem.import_into("Part1", "Analysis", "/r/x.frd") == (
        "CCX_Results001", "Pipeline_CCX_Results")
