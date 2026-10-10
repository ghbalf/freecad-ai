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


class TestCreateVariableSetUnits:
    def test_quantities_get_unit_types(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_variable_set
r = _handle_create_variable_set(
    variables={"width": "50 mm", "angle": "30 deg", "count": 3,
               "ratio": 0.5, "material": "steel"},
    label="Dims")
vs = doc.getObject(r.data["name"]) if r.success else None
results["data"] = {"success": r.success, "error": r.error,
                   "types": {p: vs.getTypeIdOfProperty(p) for p in
                             ("width", "angle", "count", "ratio", "material")} if vs else None,
                   "width": vs.width.Value if vs else None}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["types"] == {
            "width": "App::PropertyLength", "angle": "App::PropertyAngle",
            "count": "App::PropertyInteger", "ratio": "App::PropertyFloat",
            "material": "App::PropertyString",
        }
        assert d["width"] == 50.0

    def test_bad_variable_creates_nothing(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.freecad_tools import _handle_create_variable_set
r = _handle_create_variable_set(variables={"ok": 1, "bad": [1, 2]}, label="Dims")
results["data"] = {"success": r.success, "error": r.error,
                   "varsets": [o.Name for o in doc.Objects if o.TypeId == "App::VarSet"]}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"] and "bad" in d["error"]
        assert d["varsets"] == []


_SHEET = """
from freecad_ai.tools.freecad_tools import _handle_create_spreadsheet
from freecad_ai.tools.parameter_tools import (
    _handle_read_spreadsheet, _handle_edit_spreadsheet)
r = _handle_create_spreadsheet(variables={"width": 50}, label="Params")
assert r.success, r.error
sheet = doc.getObject(r.data["name"])
"""


class TestSpreadsheetTools:
    def test_create_puts_name_before_value(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
results["data"] = {"A1": sheet.getContents("A1").lstrip("'"), "B1": sheet.getContents("B1"),
                   "alias_cell": sheet.getCellFromAlias("width")}
""")
        assert result["ok"], result.get("error")
        assert result["data"] == {"A1": "width", "B1": "50", "alias_cell": "B1"}

    def test_edits_survive_save_and_reopen(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
import os, tempfile
r = _handle_edit_spreadsheet(object_name="Params",
                             set={"width": 70, "height": "20 mm"})
path = os.path.join(tempfile.mkdtemp(), "sheet.FCStd")
doc.saveAs(path)
App.closeDocument(doc.Name)
doc = App.openDocument(path)
s = doc.getObject("Params") or doc.getObjectsByLabel("Params")[0]
results["data"] = {"success": r.success, "error": r.error,
                   "width": s.getContents(s.getCellFromAlias("width")),
                   "height_cell": s.getCellFromAlias("height"),
                   "height": s.getContents("B2"), "label": s.getContents("A2").lstrip("'")}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["width"] == "70"
        assert d["height_cell"] == "B2"
        assert d["height"] == "=20 mm" and d["label"] == "height"  # FreeCAD stores quantities as formulas

    def test_read_shows_formula_and_value(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
_handle_edit_spreadsheet(object_name="Params", set={"double": "=width*2"})
doc.recompute()
r = _handle_read_spreadsheet(object_name="Params", cells=["double", "A1"])
results["data"] = {"success": r.success, "error": r.error,
                   "rows": r.data["cells"] if r.success else None}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        double, a1 = d["rows"]
        assert double["alias"] == "double" and double["contents"].startswith("=")
        assert float(double["value"].split()[0]) == 100.0
        assert a1 == {"cell": "A1", "alias": None, "contents": "width", "value": "width"}

    def test_case_mismatch_is_an_error(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
r = _handle_edit_spreadsheet(object_name="Params", set={"Width": 60})
results["data"] = {"success": r.success, "error": r.error,
                   "cells": list(sheet.getNonEmptyCells())}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"] and "'width'" in d["error"]
        assert d["cells"] == ["A1", "B1"]

    def test_one_bad_entry_changes_nothing(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
r = _handle_edit_spreadsheet(object_name="Params",
                             set={"width": 99, "my height": 5})
results["data"] = {"success": r.success, "error": r.error,
                   "width": sheet.getContents("B1"),
                   "cells": list(sheet.getNonEmptyCells())}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["success"] and "my height" in d["error"]
        assert d["width"] == "50" and d["cells"] == ["A1", "B1"]

    def test_remove_refuses_while_used_then_clears(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
_handle_edit_spreadsheet(object_name="Params", set={"depth": 5})
box = doc.addObject("Part::Box", "Box")
box.setExpression("Length", "Params.width")
doc.recompute()
used = _handle_edit_spreadsheet(object_name="Params", remove=["width"])
unused = _handle_edit_spreadsheet(object_name="Params", remove=["depth"])
results["data"] = {"used_ok": used.success, "used_err": used.error,
                   "unused_ok": unused.success, "unused_err": unused.error,
                   "depth_alias": sheet.getCellFromAlias("depth"),
                   "cells": list(sheet.getNonEmptyCells())}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["used_ok"] and "Box" in d["used_err"]
        assert d["unused_ok"], d["unused_err"]
        assert d["depth_alias"] is None
        assert d["cells"] == ["A1", "B1"]  # value and label of depth both cleared

    def test_old_value_first_sheet_keeps_its_layout(self, run_freecad_script):
        result = run_freecad_script("""
from freecad_ai.tools.parameter_tools import _handle_edit_spreadsheet
sheet = doc.addObject("Spreadsheet::Sheet", "Old")
sheet.set("A1", "50"); sheet.set("B1", "width"); sheet.setAlias("A1", "width")
doc.recompute()
r = _handle_edit_spreadsheet(object_name="Old", set={"height": 20})
results["data"] = {"success": r.success, "error": r.error,
                   "A2": sheet.getContents("A2"), "B2": sheet.getContents("B2").lstrip("'"),
                   "alias_cell": sheet.getCellFromAlias("height")}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert (d["A2"], d["B2"], d["alias_cell"]) == ("20", "height", "A2")


class TestModifyPropertyGuard:
    def test_alias_write_refused_and_float_string_accepted(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
from freecad_ai.tools.freecad_tools import (
    _handle_modify_property, _handle_create_variable_set)
alias = _handle_modify_property(object_name="Params", property_name="width", value="7")
_handle_create_variable_set(variables={"ratio": 0.5}, label="Vars")
fl = _handle_modify_property(object_name="Vars", property_name="ratio", value="0.75")
results["data"] = {"alias_ok": alias.success, "alias_err": alias.error,
                   "contents": sheet.getContents("B1"),
                   "float_ok": fl.success, "float_err": fl.error,
                   "ratio": doc.getObjectsByLabel("Vars")[0].ratio}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["alias_ok"] and "edit_spreadsheet" in d["alias_err"]
        assert d["contents"] == "50"
        assert d["float_ok"], d["float_err"]
        assert d["ratio"] == 0.75
