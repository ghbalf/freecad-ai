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
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_results_import_and_replace_on_a_second_run(tmp_path, freecad_bin):
    doc = _cantilever(tmp_path, freecad_bin)
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    code = ("import sys\nsys.path.insert(0, {!r})\n"
            "from freecad_ai.core import fem\n"
            "for _ in range(2):\n"
            "    names = fem.import_into(App.ActiveDocument.Name, 'Analysis', {!r})\n"
            "print(names)\n"
            "print(sorted(o.Name for o in App.ActiveDocument.Objects))\n").format(_REPO, s["frd"])
    names, objects = _go(tmp_path, freecad_bin, code, doc).stdout.splitlines()[-2:]
    assert names == "('CCX_Results', 'Pipeline_CCX_Results')"
    assert "'CCX_Results'" in objects and "'Pipeline_CCX_Results'" in objects
    assert "CCX_Results001" not in objects


def _cantilever_with(tmp_path, freecad_bin, extra):
    """The cantilever plus ``extra`` (code run before the save; sees doc, an, box)."""
    target = str(tmp_path / "cantilever.FCStd")
    _go(tmp_path, freecad_bin, "MATERIAL = True\nTARGET = {!r}\n".format(target)
        + _MAKE.replace("doc.saveAs(TARGET)", extra + "\ndoc.saveAs(TARGET)"))
    return target


_GMSH_MESH = '''
mesh = ObjectsFem.makeMeshGmsh(doc, "FEMMeshGmsh")
mesh.Shape = box
mesh.CharacteristicLengthMax = "5 mm"
an.addObject(mesh)
from femmesh.gmshtools import GmshTools
GmshTools(mesh).create_mesh()
'''


def test_the_gui_calculix_solver_type_is_used(tmp_path, freecad_bin):
    # Review: FreeCAD 1.1's GUI makes Fem::SolverCalculiX, not SolverCcxTools
    doc = _cantilever_with(tmp_path, freecad_bin,
                           "sol = ObjectsFem.makeSolverCalculiX(doc, 'SolverCalculiX')\n"
                           "sol.AnalysisType = 'frequency'\nan.addObject(sol)")
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    assert "error" not in s, s
    assert s["solver_created"] is False and s["analysis_type"] == "frequency"


def test_several_calculix_solvers_are_an_error(tmp_path, freecad_bin):
    doc = _cantilever_with(tmp_path, freecad_bin,
                           "an.addObject(ObjectsFem.makeSolverCalculiX(doc, 'SolverA'))\n"
                           "an.addObject(ObjectsFem.makeSolverCalculiXCcxTools(doc, 'SolverB'))")
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    assert "SolverA" in s.get("error", "") and "SolverB" in s["error"], s


def test_a_failed_gmsh_remesh_is_an_error_not_a_stale_solve(tmp_path, freecad_bin):
    # Review: a failed regeneration kept the old mesh and "solved" to zeros
    doc = _cantilever_with(tmp_path, freecad_bin,
                           _GMSH_MESH + "box.Length = 300\ndoc.recompute()")
    fail_gmsh = ("from femmesh import gmshtools as _g\n"
                 "_orig = _g.GmshTools.get_gmsh_command\n"
                 "def _bad(self):\n    _orig(self)\n    self.gmsh_bin = '/bin/false'\n"
                 "_g.GmshTools.get_gmsh_command = _bad\n")
    s = _summary(_go(tmp_path, freecad_bin,
                     fail_gmsh + fem.build_solve_code("Analysis", ""), doc))
    assert s.get("error", "").startswith("Meshing failed"), s


def test_an_existing_gmsh_mesh_is_regenerated(tmp_path, freecad_bin):
    doc = _cantilever_with(tmp_path, freecad_bin,
                           _GMSH_MESH + "box.Length = 300\ndoc.recompute()")
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    assert "error" not in s, s
    assert s["nodes"] > 400 and s["max_displacement"] > 10  # the 300 mm beam


def test_a_failing_calculix_exit_code_is_an_error(tmp_path, freecad_bin):
    fail_ccx = ("from femtools import ccxtools as _c\n"
                "_orig = _c.FemToolsCcx.start_ccx\n"
                "def _bad(self):\n    _orig(self)\n    return 1\n"
                "_c.FemToolsCcx.start_ccx = _bad\n")
    doc = _cantilever(tmp_path, freecad_bin)
    s = _summary(_go(tmp_path, freecad_bin,
                     fail_ccx + fem.build_solve_code("Analysis", ""), doc))
    assert s.get("error", "").startswith("CalculiX failed (exit code 1)"), s


def test_a_static_run_without_results_is_an_error(tmp_path, freecad_bin):
    no_results = ("from femtools import ccxtools as _c\n"
                  "_c.FemToolsCcx.load_results = lambda self: None\n")
    doc = _cantilever(tmp_path, freecad_bin)
    s = _summary(_go(tmp_path, freecad_bin,
                     no_results + fem.build_solve_code("Analysis", ""), doc))
    assert s.get("error", "").startswith("CalculiX produced no results"), s


def test_a_suppressed_mesh_is_ignored(tmp_path, freecad_bin):
    # Review: FreeCAD's own solver skips suppressed meshes
    doc = _cantilever_with(tmp_path, freecad_bin, _GMSH_MESH + (
        "old = ObjectsFem.makeMeshGmsh(doc, 'OldMesh')\nold.Shape = box\n"
        "old.Suppressed = True\nan.addObject(old)"))
    s = _summary(_go(tmp_path, freecad_bin, fem.build_solve_code("Analysis", ""), doc))
    assert "error" not in s, s
    assert s["nodes"] > 0
