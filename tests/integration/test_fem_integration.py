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
