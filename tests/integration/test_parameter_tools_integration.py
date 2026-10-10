"""Integration tests for the VarSet / Spreadsheet tools (#121) in real FreeCAD.

Uses the run_freecad_script fixture (see tests/integration/conftest.py).
Object names avoid unit symbols (S, m, g...) — 'S.w' is a ParserError.
"""

import pytest

pytestmark = pytest.mark.integration

_VARSET = """
from freecad_ai.tools.freecad_tools import _handle_create_variable_set
from freecad_ai.tools.parameter_tools import (
    _handle_read_variable_set, _handle_edit_variable_set)
r = _handle_create_variable_set(variables={"ratio": 0.5, "count": 3}, label="Vars")
assert r.success, r.error
vs = doc.getObject(r.data["name"])
"""


class TestVariableSetTools:
    def test_read_lists_only_user_variables(self, run_freecad_script):
        result = run_freecad_script(_VARSET + """
r = _handle_read_variable_set(object_name="Vars")
results["data"] = {"success": r.success, "error": r.error,
                   "names": [v["name"] for v in r.data["variables"]] if r.success else None}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["names"] == ["count", "ratio"] or d["names"] == ["ratio", "count"]

    def test_string_values_convert_and_new_ones_get_units(self, run_freecad_script):
        result = run_freecad_script(_VARSET + """
r = _handle_edit_variable_set(object_name="Vars",
                              set={"ratio": "0.75", "count": "4", "depth": "10 mm"})
doc.recompute()
results["data"] = {"success": r.success, "error": r.error,
                   "ratio": vs.ratio, "count": vs.count,
                   "depth_type": vs.getTypeIdOfProperty("depth") if r.success else None,
                   "depth": vs.depth.Value if r.success else None}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["ratio"] == 0.75 and d["count"] == 4
        assert d["depth_type"] == "App::PropertyLength"
        assert d["depth"] == 10.0

    def test_one_bad_entry_changes_nothing(self, run_freecad_script):
        result = run_freecad_script(_VARSET + """
r = _handle_edit_variable_set(object_name="Vars",
                              set={"count": 9, "ratio": "abc", "extra": 1})
results["data"] = {"success": r.success, "error": r.error, "count": vs.count,
                   "has_extra": "extra" in vs.PropertiesList}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "ratio" in d["error"]
        assert d["count"] == 3 and not d["has_extra"]

    def test_expression_bound_variable_is_refused(self, run_freecad_script):
        result = run_freecad_script(_VARSET + """
vs.setExpression("ratio", "count / 10")
doc.recompute()
r = _handle_edit_variable_set(object_name="Vars", set={"ratio": 0.9})
results["data"] = {"success": r.success, "error": r.error}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"]
        assert "set_expression" in d["error"]

    def test_remove_refuses_while_used_then_removes(self, run_freecad_script):
        result = run_freecad_script(_VARSET + """
box = doc.addObject("Part::Box", "Box")
box.setExpression("Height", "Vars.count * 2")
doc.recompute()
used = _handle_edit_variable_set(object_name="Vars", remove=["count"])
unused = _handle_edit_variable_set(object_name="Vars", remove=["ratio"])
builtin = _handle_edit_variable_set(object_name="Vars", remove=["Label"])
results["data"] = {
    "used_ok": used.success, "used_err": used.error,
    "unused_ok": unused.success, "unused_err": unused.error,
    "builtin_ok": builtin.success,
    "props": vs.PropertiesList,
}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["used_ok"] and "Box" in d["used_err"]
        assert d["unused_ok"], d["unused_err"]
        assert not d["builtin_ok"]
        assert "count" in d["props"] and "ratio" not in d["props"]
        assert "Label" in d["props"]
