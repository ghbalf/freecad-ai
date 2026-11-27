"""list_documents in real FreeCAD: linked_from, and the partial flag (PR #167).

Opening a document that links into another file makes FreeCAD load that file
partially (only the linked objects) -- no GUI needed to reach that state.
"""

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = '''
import os, tempfile
from freecad_ai.tools.freecad_tools import _handle_list_documents
d = tempfile.mkdtemp()
lib = App.newDocument("Lib")
lib.addObject("Part::Box", "Box")
lib.addObject("Part::Cylinder", "Cyl")
lib.recompute()
lib.saveAs(os.path.join(d, "Lib.FCStd"))
main = App.newDocument("Main")
main.saveAs(os.path.join(d, "Main.FCStd"))
link = main.addObject("App::Link", "L")
link.LinkedObject = lib.Box
main.recompute()
main.save()
results["data"]["full"] = _handle_list_documents().data["documents"]
c = App.newDocument("C")
c.addObject("Part::Box", "Box")
c.recompute()
c.saveAs(os.path.join(d, "C.FCStd"))
lc = lib.addObject("App::Link", "LC")
lc.LinkedObject = c.Box
lib.recompute()
lib.save()
results["data"]["chain"] = _handle_list_documents().data["documents"]
for name in ("Main", "Lib", "C"):
    App.closeDocument(name)
App.openDocument(os.path.join(d, "Main.FCStd"))
results["data"]["reopened"] = _handle_list_documents().data["documents"]
'''


def test_linked_from_and_partial_flag(run_freecad_script):
    r = run_freecad_script(_SCRIPT)
    assert r["ok"], r["error"]
    full = {x["name"]: x for x in r["data"]["full"]}
    assert full["Lib"]["linked_from"] == ["Main"]
    assert full["Main"]["linked_from"] == []
    assert not full["Lib"]["partial"]

    # Main -> Lib -> C: C is linked from Lib only, not from Main
    chain = {x["name"]: x["linked_from"] for x in r["data"]["chain"]
             if x["name"] != "TestDoc"}  # the fixture's own document
    assert chain == {"Main": [], "Lib": ["Main"], "C": ["Lib"]}

    reopened = {x["name"]: x for x in r["data"]["reopened"]}
    assert reopened["Lib"]["partial"] is True
    assert reopened["Lib"]["linked_from"] == ["Main"]
    assert reopened["Main"]["partial"] is False
