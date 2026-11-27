"""The sandbox dry run must see linked documents as the live session does (#165).

Runs inside real FreeCAD: partial loading of linked files only happens there.
"""

import pytest

pytestmark = pytest.mark.integration

_SETUP = """
import os, tempfile
from freecad_ai.core.executor import validate_code
base = tempfile.mkdtemp()
os.makedirs(base + "/proj/sub"); os.makedirs(base + "/proj/data")
alkis = App.newDocument("ALKIS")
alkis.addObject("Part::Box", "Box"); alkis.addObject("Part::Box", "Extra")
alkis.recompute(); alkis.saveAs(base + "/proj/data/ALKIS.FCStd")
doc.saveAs(base + "/proj/sub/main.FCStd")
link = doc.addObject("App::Link", "L"); link.LinkedObject = alkis.Box
doc.recompute(); doc.save()
doc.addObject("Part::Box", "Fresh"); alkis.addObject("Part::Box", "FreshB")
doc.recompute(); alkis.recompute()
App.setActiveDocument(doc.Name)
"""


def _dry_run(run_freecad_script, code):
    out = run_freecad_script(
        _SETUP + "r = validate_code(" + repr(code) + ", timeout=120)\n"
        "results['data'] = {'ok': r.success, 'err': r.stderr}\n", timeout=300)
    assert out["ok"], out["error"]
    return out["data"]


@pytest.mark.parametrize("code", [
    "assert App.getDocument('ALKIS') is not None",
    "assert doc.getObject('Fresh') is not None",
    # not referenced by the link: missing when ALKIS is loaded partially
    "assert App.getDocument('ALKIS').getObject('Extra') is not None",
    "assert App.getDocument('ALKIS').getObject('FreshB') is not None",
])
def test_dry_run_sees_linked_and_unsaved_objects(run_freecad_script, code):
    res = _dry_run(run_freecad_script, code)
    assert res["ok"], res["err"]


def test_dry_run_error_is_short_and_names_the_failing_line(run_freecad_script):
    code = "".join("x%d = %d  # padding\n" % (i, i) for i in range(150))
    res = _dry_run(run_freecad_script, code + "raise ValueError('at the end')\n")
    assert not res["ok"]
    assert "raise ValueError('at the end')" in res["err"]
    assert "padding" not in res["err"]
