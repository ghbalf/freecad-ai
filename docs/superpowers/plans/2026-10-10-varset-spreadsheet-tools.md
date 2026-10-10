# VarSet & Spreadsheet Parameter Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the model read, edit, add, and remove the parameters in VarSets and Spreadsheets. Add a general `delete_object`. Fix the three defects in issue #121.

**Architecture:** A new module, `freecad_ai/tools/parameter_tools.py`, holds two kinds of code:
- Pure helpers: address and alias resolution, type mapping, value coercion, and expression-reference matching. These are unit-tested without FreeCAD.
- Four tools: `read_variable_set`, `edit_variable_set`, `read_spreadsheet`, `edit_spreadsheet`. They are registered in `tools/setup.py` the way `run_fem_analysis` is.

`delete_object`, the `modify_property` guard and coercion, and unit-aware `create_variable_set` live in `freecad_tools.py`. They import the helpers lazily to avoid an import cycle. Both edit tools validate every entry before their first change, then apply all entries inside one `_with_undo` transaction. If any entry is invalid, nothing changes.

**Tech Stack:** Python 3.11, FreeCAD 1.0.2 and 1.1.1 APIs (`App::VarSet`, `Spreadsheet::Sheet`), pytest. Integration tests run in real FreeCAD through the `run_freecad_script` fixture.

**Spec:** GitHub issue #121 (body in the issue). Facts probed on 2026-10-10 in FreeCAD 1.1.1:
- `sheet.getNonEmptyCells()` returns cells row-major. `sheet.get(empty)` raises `ValueError`. A broken formula's `get` returns the string `"ERR: …"`.
- `sheet.clear(cell)` also clears the cell's alias.
- Valid addresses: `ZZ1` and `A16384`. `AAA1`, `A0`, `A16385` and `a1` are invalid.
- `setAlias` with an alias that already exists raises "Alias already defined".
- `App.Units.Quantity(s).Unit.Type` gives `Length`, `Angle`, `Mass`, `Area`, `Force`, `Pressure`, `Velocity`, `Moment`, … A bare `"50"` gives `'1'`. Text such as `"abc"` raises `ValueError`.
- `"App::Property" + Unit.Type` is in `vs.supportedProperties()` for every unit type above except `TimeSpan`.
- `PropertyQuantity` rejects `"5 kg"` ("Not matching Unit!").
- `PropertyFloat` accepts int, float and bool. `PropertyInteger` rejects `3.7`.
- `vs.removeProperty("Label")` raises no error. Removing a property that an expression uses also raises no error, and the dependent keeps a stale value.
- Same-object VarSet expressions are stored bare (`'len * 2'`). Cross-object expressions are stored qualified (`'Params.width'`).
- VarSet built-in properties: `ExpressionEngine`, `Label`, `Label2`, `Visibility`.

## Global Constraints

- Work on branch `feat/121-varset-spreadsheet-tools`, not on `master`.
- Run tests with `env PYTHONPATH= .venv/bin/pytest …`. Pass `--ignore=tests/unit/test_document_attach.py` (it segfaults Qt).
- Run integration tests with `env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py`. The harness picks `~/bin/FreeCAD_1.0.2-…AppImage` first, so the code must work on FreeCAD 1.0.2 as well as 1.1.1.
- No new external dependencies.
- Spreadsheet keys resolve alias-first: `getCellFromAlias`, then a strict address check (upper-case column `A`–`ZZ`, row 1–16384), then an error or a new alias. One parameter takes either form. Never ship separate alias and address tools.
- Each new tool returns the previous and new values in `data`. Each mutation runs inside `_with_undo`, so one Ctrl+Z reverts it.
- Read tools use `category="query"`. Edit and delete tools use `category="modeling"`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **Alias case mismatch:** `edit_spreadsheet(set={"Width": 60})` on a sheet that has an alias `width` must return an error that names `width`. It must not silently add a second `Width` row. Pinned in Tasks 1 and 4.
2. **Removing something still in use:** removing a VarSet variable or sheet cell that an expression or formula uses must be refused, and the refusal names the user. FreeCAD itself allows the removal and leaves a stale value. Pinned in Tasks 2 and 4.
3. **One bad entry in a multi-entry edit:** the whole edit fails and nothing changes. Earlier entries must not stay applied. Pinned in Tasks 2 and 4.
4. **Editing an expression-bound variable:** the edit returns an error that points to `set_expression`. Otherwise the next recompute would silently overwrite the value. Pinned in Task 2.
5. **Deleting a feature inside a Body:** the delete is refused because the Body lists the feature in its InList. The message names the Body and offers `force=true`. Pinned in Task 6.

---

### Task 1: Pure helpers

**Files:**
- Create: `freecad_ai/tools/parameter_tools.py` (helpers section only)
- Test: `tests/unit/test_parameter_tools.py`

**Interfaces:**
- Produces:
  - `MAX_ROW: int`
  - `is_cell_address(key: str) -> bool`
  - `split_address(address: str) -> tuple[str, int]`
  - `column_index(col: str) -> int`
  - `column_name(index: int) -> str`
  - `resolve_cell(sheet, key: str) -> str | None`
  - `case_insensitive_match(key: str, aliases: list[str]) -> str | None`
  - `is_valid_name(key: str) -> bool`
  - `is_valid_alias(key: str) -> bool`
  - `layout_columns(alias_cells: list[str]) -> tuple[str, str]` (returns `(value_col, label_col)`)
  - `last_row(cells: list[str]) -> int`
  - `as_list(value) -> list`
  - `new_property_type(value, unit_type_of) -> str`
  - `coerce_for_property(type_id: str, value)`
  - `reference_pattern(key: str, owners=()) -> re.Pattern`
  - `cell_text(value) -> str`
  - `format_cell_row(row: dict) -> str`
  - `describe_change(change: dict) -> str`

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/121-varset-spreadsheet-tools
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_parameter_tools.py`:

```python
"""Tests for the VarSet / Spreadsheet parameter helpers and tools (#121)."""

import types

import pytest

from freecad_ai.tools import parameter_tools as pt


class TestCellAddress:
    @pytest.mark.parametrize("key", ["A1", "B7", "AB12", "ZZ1", "A16384"])
    def test_valid(self, key):
        assert pt.is_cell_address(key)

    @pytest.mark.parametrize("key", ["a1", "b7", "XFD1", "ZZZ9", "AAA1",
                                     "A0", "A16385", "A01", "width", ""])
    def test_invalid(self, key):
        assert not pt.is_cell_address(key)

    def test_split(self):
        assert pt.split_address("AB12") == ("AB", 12)


class TestColumns:
    @pytest.mark.parametrize("name,index", [("A", 1), ("Z", 26), ("AA", 27),
                                            ("AZ", 52), ("ZZ", 702)])
    def test_round_trip(self, name, index):
        assert pt.column_index(name) == index
        assert pt.column_name(index) == name


def _sheet(aliases):
    """Fake sheet: `aliases` maps alias -> cell address."""
    return types.SimpleNamespace(getCellFromAlias=lambda key: aliases.get(key))


class TestResolveCell:
    def test_alias(self):
        assert pt.resolve_cell(_sheet({"width": "B1"}), "width") == "B1"

    def test_address(self):
        assert pt.resolve_cell(_sheet({}), "C3") == "C3"

    def test_lookalike_aliases_resolve_as_aliases(self):
        # FreeCAD accepts 'b7' and 'XFD1' as aliases; alias lookup comes first.
        sheet = _sheet({"b7": "B2", "XFD1": "B3"})
        assert pt.resolve_cell(sheet, "b7") == "B2"
        assert pt.resolve_cell(sheet, "XFD1") == "B3"

    def test_unknown(self):
        assert pt.resolve_cell(_sheet({}), "XFD1") is None
        assert pt.resolve_cell(_sheet({}), "height") is None


class TestNames:
    def test_case_mismatch_found(self):
        assert pt.case_insensitive_match("Width", ["width", "depth"]) == "width"

    def test_exact_match_is_not_a_mismatch(self):
        assert pt.case_insensitive_match("width", ["width"]) is None

    @pytest.mark.parametrize("key,ok", [("width", True), ("_w2", True),
                                        ("b7", True), ("B7", False),
                                        ("2w", False), ("my width", False),
                                        ("", False)])
    def test_valid_alias(self, key, ok):
        assert pt.is_valid_alias(key) is ok

    def test_valid_name_allows_address_lookalikes(self):
        # VarSet property names are not cell addresses, so B7 is fine there.
        assert pt.is_valid_name("B7")
        assert not pt.is_valid_name("my width")


class TestLayout:
    def test_default_is_name_then_value(self):
        assert pt.layout_columns([]) == ("B", "A")

    def test_follows_old_value_first_layout(self):
        # Sheets made by create_spreadsheet before #121: value in A, name in B.
        assert pt.layout_columns(["A1", "A2"]) == ("A", "B")

    def test_follows_existing_alias_column(self):
        assert pt.layout_columns(["D4"]) == ("D", "C")

    def test_last_row(self):
        assert pt.last_row([]) == 0
        assert pt.last_row(["A1", "B12", "C3"]) == 12


class TestAsList:
    @pytest.mark.parametrize("value,expected", [
        (None, []), ("", []), ("width", ["width"]),
        (["a", "b"], ["a", "b"]), ("['a', 'b']", ["a", "b"]),
    ])
    def test_values(self, value, expected):
        assert pt.as_list(value) == expected


def _units(table):
    return lambda text: table.get(text)


class TestNewPropertyType:
    def test_plain_python_types(self):
        u = _units({})
        assert pt.new_property_type(True, u) == "App::PropertyBool"
        assert pt.new_property_type(3, u) == "App::PropertyInteger"
        assert pt.new_property_type(2.5, u) == "App::PropertyFloat"

    def test_quantity_gets_its_unit_type(self):
        u = _units({"50 mm": "Length", "30 deg": "Angle"})
        assert pt.new_property_type("50 mm", u) == "App::PropertyLength"
        assert pt.new_property_type("30 deg", u) == "App::PropertyAngle"

    def test_dimensionless_string_is_float(self):
        assert pt.new_property_type("60", _units({"60": ""})) == "App::PropertyFloat"

    def test_text_is_string(self):
        assert pt.new_property_type("steel", _units({})) == "App::PropertyString"

    def test_container_rejected(self):
        with pytest.raises(ValueError):
            pt.new_property_type([1, 2], _units({}))


class TestCoerceForProperty:
    def test_float_from_string(self):
        assert pt.coerce_for_property("App::PropertyFloat", "60") == 60.0

    def test_integer_from_string_and_whole_float(self):
        assert pt.coerce_for_property("App::PropertyInteger", "3") == 3
        assert pt.coerce_for_property("App::PropertyInteger", 4.0) == 4

    def test_integer_rejects_fraction(self):
        with pytest.raises(ValueError, match="whole number"):
            pt.coerce_for_property("App::PropertyInteger", 3.7)

    def test_bool_from_string(self):
        assert pt.coerce_for_property("App::PropertyBool", "false") is False
        assert pt.coerce_for_property("App::PropertyBool", "True") is True

    def test_bool_rejects_other_text(self):
        with pytest.raises(ValueError):
            pt.coerce_for_property("App::PropertyBool", "maybe")

    def test_float_rejects_text(self):
        with pytest.raises(ValueError):
            pt.coerce_for_property("App::PropertyFloat", "abc")

    def test_unit_property_passes_through(self):
        assert pt.coerce_for_property("App::PropertyLength", "60 mm") == "60 mm"


class TestReferencePattern:
    def test_qualified_by_name_or_label(self):
        p = pt.reference_pattern("width", ("Spreadsheet", "Params"))
        assert p.search("Params.width * 2")
        assert p.search("Spreadsheet.width")
        assert p.search("<<Params>>.width + 1")

    def test_no_false_hits(self):
        p = pt.reference_pattern("width", ("Params",))
        assert not p.search("Params.width2")
        assert not p.search("OtherParams.width")
        assert not p.search("Other.width")

    def test_bare_use(self):
        p = pt.reference_pattern("width")
        assert p.search("=width * 2")
        assert p.search("width * 2")
        assert not p.search("=Params.width * 2")
        assert not p.search("=widths")


class TestCellText:
    @pytest.mark.parametrize("value,text", [(50, "50"), (2.5, "2.5"),
                                            (True, "1"), (False, "0"),
                                            ("50 mm", "50 mm"),
                                            ("=width*2", "=width*2")])
    def test_values(self, value, text):
        assert pt.cell_text(value) == text

    def test_rejects_containers(self):
        with pytest.raises(ValueError):
            pt.cell_text({"a": 1})


class TestFormatting:
    def test_formula_shows_value(self):
        row = {"cell": "B2", "alias": "dbl", "contents": "=width * 2",
               "value": "100.0 mm"}
        assert pt.format_cell_row(row) == "B2 (dbl): =width * 2 → 100.0 mm"

    def test_text_shown_once(self):
        row = {"cell": "A1", "alias": None, "contents": "width", "value": "width"}
        assert pt.format_cell_row(row) == "A1: width"

    def test_empty(self):
        row = {"cell": "C9", "alias": None, "contents": "", "value": None}
        assert pt.format_cell_row(row) == "C9: (empty)"

    def test_describe_change(self):
        assert pt.describe_change({"name": "w", "action": "changed",
                                   "previous": "50 mm", "value": "60 mm"}) \
            == "w: 50 mm → 60 mm"
        assert pt.describe_change({"name": "d", "action": "added",
                                   "type": "Length", "previous": None,
                                   "value": "10 mm"}) \
            == "added d = 10 mm (Length)"
        assert pt.describe_change({"name": "n", "action": "removed",
                                   "previous": "steel", "value": None}) \
            == "removed n (was steel)"

    def test_describe_change_names_the_cell(self):
        assert pt.describe_change({"name": "height", "cell": "B2",
                                   "action": "added", "previous": None,
                                   "value": "20 mm"}) \
            == "added height (B2) = 20 mm"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q`
Expected: ERROR, `ModuleNotFoundError: No module named 'freecad_ai.tools.parameter_tools'`

- [ ] **Step 4: Write the helpers**

`freecad_ai/tools/parameter_tools.py`:

```python
"""Read and edit the parameters in VarSets and Spreadsheets (#121).

One read tool and one edit tool per object type. Spreadsheet keys are
resolved alias-first, then as a cell address, so one parameter takes
either form — unambiguous because FreeCAD rejects aliases that are valid
addresses (B7, AB12) while accepting look-alikes (b7, XFD1).
"""

import json
import re

from .registry import ToolParam, ToolDefinition, ToolResult
from .freecad_tools import (
    _coerce_str_list, _get_object, _suggest_similar, _with_undo,
)

# ── Pure helpers (unit-tested without FreeCAD) ─────────────

_ADDRESS_RE = re.compile(r"^([A-Z]{1,2})([1-9][0-9]*)$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ROW = 16384


def is_cell_address(key):
    """True for a FreeCAD cell address: upper-case column A..ZZ, row 1..16384."""
    m = _ADDRESS_RE.match(key or "")
    return bool(m) and int(m.group(2)) <= MAX_ROW


def split_address(address):
    m = _ADDRESS_RE.match(address)
    return m.group(1), int(m.group(2))


def column_index(col):
    """'A' -> 1, 'Z' -> 26, 'AA' -> 27, 'ZZ' -> 702."""
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n


def column_name(index):
    """Inverse of column_index."""
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def resolve_cell(sheet, key):
    """The cell `key` names — alias first, then address — or None."""
    cell = sheet.getCellFromAlias(key)
    if cell:
        return cell
    return key if is_cell_address(key) else None


def case_insensitive_match(key, aliases):
    """An alias equal to `key` except for case ('Width' vs 'width'), or None."""
    low = key.lower()
    for alias in aliases:
        if alias != key and alias.lower() == low:
            return alias
    return None


def is_valid_name(key):
    """A property name: letters, digits and _, not starting with a digit."""
    return bool(_NAME_RE.match(key or ""))


def is_valid_alias(key):
    """A new alias must be a valid name and must not be a cell address."""
    return is_valid_name(key) and not is_cell_address(key)


def layout_columns(alias_cells):
    """(value_col, label_col) for new rows, following the existing aliases.

    create_spreadsheet wrote values in A and names in B before #121 and
    names in A, values in B since; the sheet's first alias decides.
    """
    if not alias_cells:
        return "B", "A"
    col, _ = split_address(alias_cells[0])
    idx = column_index(col)
    return col, column_name(idx - 1 if idx > 1 else idx + 1)


def last_row(cells):
    return max((split_address(c)[1] for c in cells), default=0)


def as_list(value):
    """A list from a list, a stringified list, or a single string."""
    value = _coerce_str_list(value)
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


_PLAIN_TYPES = {
    bool: "App::PropertyBool",
    int: "App::PropertyInteger",
    float: "App::PropertyFloat",
}


def new_property_type(value, unit_type_of):
    """Property type for a new VarSet variable holding `value`.

    unit_type_of(text) returns FreeCAD's unit type name ('Length'), '' for
    a dimensionless number, or None when the text is not a quantity.
    """
    if type(value) in _PLAIN_TYPES:
        return _PLAIN_TYPES[type(value)]
    if not isinstance(value, str):
        raise ValueError(
            f"unsupported value {value!r}: use a number, text or true/false")
    unit = unit_type_of(value)
    if unit is None:
        return "App::PropertyString"
    if unit == "":
        return "App::PropertyFloat"
    return f"App::Property{unit}"


def coerce_for_property(type_id, value):
    """Convert `value` to what a property of `type_id` accepts.

    Float, Integer and Bool properties reject strings (#121 defect 3);
    unit properties (Length, Angle, ...) take numbers and '60 mm' as is.
    """
    if type_id == "App::PropertyBool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "false"):
            return text == "true"
        raise ValueError(f"{value!r} is not true or false")
    if type_id == "App::PropertyInteger":
        number = float(value)
        if not number.is_integer():
            raise ValueError(f"{value!r} is not a whole number")
        return int(number)
    if type_id == "App::PropertyFloat":
        return float(value)
    if type_id == "App::PropertyString":
        return str(value)
    return value


def reference_pattern(key, owners=()):
    """Regex matching a use of `key` in an expression or formula.

    With owners (an object's Name and Label) it matches qualified uses —
    'Params.width', '<<My Params>>.width'. Without, it matches bare uses
    inside the owning object ('=width * 2' in a cell, 'len * 2' in a
    VarSet, where FreeCAD stores same-object references unqualified).
    """
    if owners:
        names = [re.escape(o) for o in owners]
        names += ["<<%s>>" % re.escape(o) for o in owners]
        prefix = r"(?<![\w.])(?:%s)\." % "|".join(names)
    else:
        prefix = r"(?<![\w.>])"
    return re.compile(prefix + re.escape(key) + r"(?!\w)")


def cell_text(value):
    """The text sheet.set() needs: numbers as written, strings as given."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, str)):
        return str(value)
    raise ValueError(
        f"unsupported value {value!r}: use a number, text or a formula")


def format_cell_row(row):
    name = f"{row['cell']} ({row['alias']})" if row["alias"] else row["cell"]
    if not row["contents"]:
        return f"{name}: (empty)"
    if row["value"] is None or row["value"] == row["contents"]:
        return f"{name}: {row['contents']}"
    return f"{name}: {row['contents']} → {row['value']}"


def describe_change(change):
    name = change["name"]
    if change.get("cell") and change["cell"] != name:
        name = f"{name} ({change['cell']})"
    if change["action"] == "added":
        kind = f" ({change['type']})" if change.get("type") else ""
        return f"added {name} = {change['value']}{kind}"
    if change["action"] == "removed":
        return f"removed {name} (was {change['previous']})"
    return f"{name}: {change['previous']} → {change['value']}"
```

(`json`, `ToolParam`, `ToolDefinition`, `ToolResult`, `_get_object`, `_suggest_similar` and `_with_undo` are first used in Task 2. Leave the imports in place now so later tasks only append code.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/parameter_tools.py tests/unit/test_parameter_tools.py
git commit -m "feat(tools): parameter helpers for VarSet/Spreadsheet tools (#121)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `read_variable_set` and `edit_variable_set`

**Files:**
- Modify: `freecad_ai/tools/parameter_tools.py` (append FreeCAD-side helpers and two tools)
- Modify: `freecad_ai/tools/setup.py` (register `PARAMETER_TOOLS`)
- Test: `tests/unit/test_parameter_tools.py` (definitions and registration)
- Test: `tests/integration/test_parameter_tools_integration.py` (create)

**Interfaces:**
- Consumes: everything from Task 1.
- Produces:
  - `_active_doc()`
  - `_lookup(doc, object_name, type_id) -> (obj, None) | (None, ToolResult)`
  - `_fail(msg) -> ToolResult`
  - `_as_dict(value, what) -> dict`
  - `_referrers(obj, keys: list[str], own_expressions: list[str]) -> list[str]`
  - `new_variable(vs, value) -> tuple[str, object]`
  - `_handle_read_variable_set(object_name, names=None)`
  - `_handle_edit_variable_set(object_name, set=None, remove=None)`
  - `READ_VARIABLE_SET`, `EDIT_VARIABLE_SET`, `PARAMETER_TOOLS: list[ToolDefinition]`

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/unit/test_parameter_tools.py`:

```python
class TestDefinitions:
    @pytest.mark.parametrize("attr,name,category", [
        ("READ_VARIABLE_SET", "read_variable_set", "query"),
        ("EDIT_VARIABLE_SET", "edit_variable_set", "modeling"),
    ])
    def test_definition(self, attr, name, category):
        tool = getattr(pt, attr)
        assert tool.name == name
        assert tool.category == category
        assert tool.parameters[0].name == "object_name"
        assert tool in pt.PARAMETER_TOOLS

    def test_registered_in_default_registry(self):
        from freecad_ai.tools.setup import create_default_registry
        registry = create_default_registry(include_mcp=False)
        for tool in pt.PARAMETER_TOOLS:
            assert registry.get(tool.name) is tool
```

- [ ] **Step 2: Write the failing integration tests**

`tests/integration/test_parameter_tools_integration.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q -k Definitions`
Expected: FAIL, `AttributeError: module ... has no attribute 'READ_VARIABLE_SET'`

Run: `env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q`
Expected: FAIL. The script raises `ImportError` for `_handle_read_variable_set`, so `result["ok"]` is False.

- [ ] **Step 4: Implement the tools**

Append to `freecad_ai/tools/parameter_tools.py`:

```python
# ── FreeCAD-side helpers ───────────────────────────────────

_VARSET_BUILTIN = frozenset({"ExpressionEngine", "Label", "Label2", "Visibility"})
_TOOLS_FOR_TYPE = {
    "App::VarSet": "read_variable_set / edit_variable_set",
    "Spreadsheet::Sheet": "read_spreadsheet / edit_spreadsheet",
}


def _fail(message):
    return ToolResult(success=False, output="", error=message)


def _active_doc():
    from ..core.active_document import get_synced_active_document
    return get_synced_active_document()


def _lookup(doc, object_name, type_id):
    """(obj, None), or (None, error ToolResult) when missing or the wrong type."""
    obj = _get_object(doc, object_name)
    if obj is None:
        hint = _suggest_similar(doc, object_name)
        return None, _fail(f"Object '{object_name}' not found.{hint}")
    if obj.TypeId != type_id:
        tools = _TOOLS_FOR_TYPE.get(obj.TypeId)
        tip = f" Use {tools} for it." if tools else ""
        return None, _fail(f"'{obj.Label}' is a {obj.TypeId}, not a {type_id}.{tip}")
    return obj, None


def _as_dict(value, what):
    """A dict from a dict or its JSON string (LLMs sometimes stringify)."""
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict):
        raise ValueError(f"'{what}' must be an object like {{\"width\": 60}}")
    return value


def _referrers(obj, keys, own_expressions):
    """Labels of what still uses obj.<key> for any of `keys`.

    Other objects reference it qualified (in their ExpressionEngine);
    the object itself references it bare (own_expressions).
    """
    qualified = [reference_pattern(k, (obj.Name, obj.Label)) for k in keys]
    bare = [reference_pattern(k) for k in keys]
    users = []
    for other in obj.InList:
        exprs = [e for _, e in getattr(other, "ExpressionEngine", [])]
        if any(p.search(e) for p in qualified for e in exprs):
            users.append(other.Label)
    if any(p.search(e) for p in bare for e in own_expressions):
        users.append(f"{obj.Label} (its own expressions)")
    return users


def _unit_type_of(text):
    """FreeCAD unit type of `text`, '' if dimensionless, None if not a quantity."""
    import FreeCAD as App
    try:
        kind = App.Units.Quantity(text).Unit.Type
    except Exception:
        return None
    return "" if kind in ("", "1") else kind


def new_variable(vs, value):
    """(property type, converted value) for a new variable on VarSet `vs`."""
    prop_type = new_property_type(value, _unit_type_of)
    if prop_type not in vs.supportedProperties():
        raise ValueError(f"{value!r} has a unit VarSets can't store "
                         f"({prop_type.replace('App::Property', '')})")
    if prop_type == "App::PropertyFloat" and isinstance(value, str):
        import FreeCAD as App
        return prop_type, App.Units.Quantity(value).Value
    return prop_type, value


def _variables(vs):
    return [p for p in vs.PropertiesList if p not in _VARSET_BUILTIN]


def _short_type(type_id):
    return type_id.replace("App::Property", "")


# ── read_variable_set / edit_variable_set ──────────────────

def _handle_read_variable_set(object_name="", names=None) -> ToolResult:
    doc = _active_doc()
    if not doc:
        return _fail("No active document.")
    vs, err = _lookup(doc, object_name, "App::VarSet")
    if err:
        return err
    available = _variables(vs)
    wanted = as_list(names) or available
    unknown = [n for n in wanted if n not in available]
    if unknown:
        return _fail(f"'{vs.Label}' has no variable {', '.join(unknown)}. "
                     f"Variables: {', '.join(available) or 'none'}")
    exprs = dict(vs.ExpressionEngine)
    rows = [{"name": n, "type": _short_type(vs.getTypeIdOfProperty(n)),
             "value": str(getattr(vs, n)), "expression": exprs.get(n)}
            for n in wanted]
    if not rows:
        output = f"VarSet '{vs.Label}' ({vs.Name}) has no variables."
    else:
        lines = [f"{r['name']} ({r['type']}) = {r['value']}"
                 + (f"  [= {r['expression']}]" if r["expression"] else "")
                 for r in rows]
        output = (f"VarSet '{vs.Label}' ({vs.Name}), {len(rows)} variables:\n"
                  + "\n".join(lines))
    return ToolResult(success=True, output=output,
                      data={"name": vs.Name, "label": vs.Label, "variables": rows})


def _handle_edit_variable_set(object_name="", set=None, remove=None) -> ToolResult:
    # `set` shadows the builtin here; it is the tool's public parameter name.
    try:
        updates = _as_dict(set, "set")
    except ValueError as e:
        return _fail(str(e))
    removals = as_list(remove)
    if not updates and not removals:
        return _fail("Nothing to do: pass set={name: value} and/or remove=[name].")
    both = [k for k in updates if k in removals]
    if both:
        return _fail(f"{', '.join(both)} is in both set and remove.")

    def do(doc):
        vs, err = _lookup(doc, object_name, "App::VarSet")
        if err:
            return err
        existing = _variables(vs)
        exprs = dict(vs.ExpressionEngine)

        # Validate every entry before the first change: one bad entry
        # must leave the VarSet untouched (ValueError aborts the transaction).
        plan = []
        for name, value in updates.items():
            try:
                if name in existing:
                    if name in exprs:
                        raise ValueError(
                            f"bound to the expression '{exprs[name]}' — "
                            "change it with set_expression instead")
                    type_id = vs.getTypeIdOfProperty(name)
                    plan.append((name, "changed", type_id,
                                 coerce_for_property(type_id, value)))
                elif name in _VARSET_BUILTIN or not is_valid_name(name):
                    raise ValueError("not a valid variable name (letters, "
                                     "digits and _, not a built-in property)")
                else:
                    plan.append((name, "added", *new_variable(vs, value)))
            except (TypeError, ValueError) as e:
                raise ValueError(f"{name}: {e}")
        for name in removals:
            if name not in existing:
                raise ValueError(f"'{vs.Label}' has no variable '{name}'. "
                                 f"Variables: {', '.join(existing) or 'none'}")
            own = [e for p, e in vs.ExpressionEngine if p != name]
            users = _referrers(vs, [name], own)
            if users:
                raise ValueError(f"'{name}' is still used by {', '.join(users)}"
                                 " — remove those uses first")

        changes = []
        for name, action, type_id, value in plan:
            previous = None
            if action == "added":
                vs.addProperty(type_id, name, "Parameters", "")
            else:
                previous = str(getattr(vs, name))
            setattr(vs, name, value)
            changes.append({"name": name, "action": action,
                            "type": _short_type(type_id),
                            "previous": previous, "value": str(getattr(vs, name))})
        for name in removals:
            previous = str(getattr(vs, name))
            vs.removeProperty(name)
            changes.append({"name": name, "action": "removed",
                            "previous": previous, "value": None})
        return ToolResult(
            success=True,
            output=(f"Updated VarSet '{vs.Label}': "
                    + "; ".join(describe_change(c) for c in changes)),
            data={"name": vs.Name, "label": vs.Label, "changes": changes},
        )

    return _with_undo("Edit Variable Set", do)


READ_VARIABLE_SET = ToolDefinition(
    name="read_variable_set",
    description=(
        "Read the variables of a VarSet: name, type, value with unit, and the "
        "expression if one is bound. Omit names to read every variable."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Name or label of the VarSet"),
        ToolParam("names", "array", "Variables to read; omit for all",
                  required=False, items={"type": "string"}),
    ],
    handler=_handle_read_variable_set,
)

EDIT_VARIABLE_SET = ToolDefinition(
    name="edit_variable_set",
    description=(
        "Change, add or remove variables of an existing VarSet in one undoable "
        "step. set={name: value} changes a variable, or adds it when missing "
        "('50 mm' becomes a Length, '30 deg' an Angle, 3 an Integer, 2.5 a "
        "Float, other text a String). remove=[name] deletes variables that no "
        "expression uses. If any entry is invalid, nothing changes."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Name or label of the VarSet"),
        ToolParam("set", "object",
                  "Variables to change or add, e.g. {\"width\": \"60 mm\", \"count\": 4}",
                  required=False),
        ToolParam("remove", "array", "Variable names to delete",
                  required=False, items={"type": "string"}),
    ],
    handler=_handle_edit_variable_set,
)

PARAMETER_TOOLS = [
    READ_VARIABLE_SET,
    EDIT_VARIABLE_SET,
]
```

In `freecad_ai/tools/setup.py`, add the import below `from .freecad_tools import ALL_TOOLS`:

```python
from .parameter_tools import PARAMETER_TOOLS
```

Then add this after `registry.register(RUN_FEM_ANALYSIS)`:

```python
    for tool in PARAMETER_TOOLS:
        registry.register(tool)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q`
Expected: all pass.

Run: `env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q`
Expected: all 5 pass.

`test_read_lists_only_user_variables` checks the built-in filter on FreeCAD 1.0.2. If it lists an extra built-in, add that name to `_VARSET_BUILTIN` and rerun. If FreeCAD 1.0.2 doesn't roll back `addProperty` in `abortTransaction`, `test_one_bad_entry_changes_nothing` still passes, because validation runs before the first change.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/parameter_tools.py freecad_ai/tools/setup.py \
        tests/unit/test_parameter_tools.py tests/integration/test_parameter_tools_integration.py
git commit -m "feat(tools): read_variable_set and edit_variable_set (#121)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Unit-aware `create_variable_set`

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (`_handle_create_variable_set` and the `CREATE_VARIABLE_SET` description)
- Test: `tests/integration/test_parameter_tools_integration.py`

**Interfaces:**
- Consumes: `new_variable(vs, value)` from Task 2.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/integration/test_parameter_tools_integration.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q -k CreateVariableSetUnits`
Expected: FAIL. `width` is `App::PropertyString` today, and the half-built VarSet is left in the document.

- [ ] **Step 3: Implement**

In `_handle_create_variable_set`, replace the `_PROP_TYPES` dict and the loop with the code below. Raising instead of returning makes `_with_undo` abort, so a failed create leaves no half-built VarSet.

```python
        from .parameter_tools import new_variable

        vs = doc.addObject("App::VarSet", label)
        var_names = []
        for name, value in variables.items():
            try:
                prop_type, converted = new_variable(vs, value)
                vs.addProperty(prop_type, name, "Parameters", "")
                setattr(vs, name, converted)
            except Exception as e:
                raise ValueError(f"Invalid variable '{name}': {e}")
            var_names.append(name)
```

In `CREATE_VARIABLE_SET.description`, replace the sentence `"Create a VarSet (App::VarSet) with named, typed variables for parametric modeling. "` with:

```python
        "Create a VarSet (App::VarSet) with named, typed variables for parametric modeling. "
        "Values with units become unit properties ('50 mm' → Length, '30 deg' → Angle); "
        "plain numbers become Integer/Float. "
```

Next, check that the existing `tests/unit/test_variable_set.py` still passes, because the `variables` param description is unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q && env PYTHONPATH= .venv/bin/pytest tests/unit/test_variable_set.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/integration/test_parameter_tools_integration.py
git commit -m "fix(tools): create_variable_set gives quantities unit types (#121)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `read_spreadsheet`, `edit_spreadsheet`, and the `create_spreadsheet` column order

**Files:**
- Modify: `freecad_ai/tools/parameter_tools.py` (append two tools and extend `PARAMETER_TOOLS`)
- Modify: `freecad_ai/tools/freecad_tools.py` (`_handle_create_spreadsheet`: name in A, value in B)
- Test: `tests/unit/test_parameter_tools.py`, `tests/integration/test_parameter_tools_integration.py`

**Interfaces:**
- Consumes: Task 1 helpers, plus `_lookup`, `_fail`, `_as_dict`, `_referrers` and `_active_doc` from Task 2.
- Produces: `_handle_read_spreadsheet(object_name, cells=None)`, `_handle_edit_spreadsheet(object_name, set=None, remove=None)`, `READ_SPREADSHEET`, `EDIT_SPREADSHEET`.

- [ ] **Step 1: Write the failing unit tests**

In `tests/unit/test_parameter_tools.py`, add these two rows to the `TestDefinitions` parametrize list:

```python
        ("READ_SPREADSHEET", "read_spreadsheet", "query"),
        ("EDIT_SPREADSHEET", "edit_spreadsheet", "modeling"),
```

- [ ] **Step 2: Write the failing integration tests**

Append to `tests/integration/test_parameter_tools_integration.py`:

```python
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
results["data"] = {"A1": sheet.getContents("A1"), "B1": sheet.getContents("B1"),
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
                   "height": s.getContents("B2"), "label": s.getContents("A2")}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert d["width"] == "70"
        assert d["height_cell"] == "B2"
        assert d["height"] == "20 mm" and d["label"] == "height"

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
                   "A2": sheet.getContents("A2"), "B2": sheet.getContents("B2"),
                   "alias_cell": sheet.getCellFromAlias("height")}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert d["success"], d["error"]
        assert (d["A2"], d["B2"], d["alias_cell"]) == ("20", "height", "A2")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q -k Definitions && env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q -k Spreadsheet`
Expected: FAIL. The unit test raises `AttributeError: READ_SPREADSHEET`. The integration scripts fail to import.

- [ ] **Step 4: Implement**

Append to `freecad_ai/tools/parameter_tools.py`, above the `PARAMETER_TOOLS` list:

```python
# ── read_spreadsheet / edit_spreadsheet ────────────────────

_READ_CAP = 200


def _cell_value(sheet, cell):
    try:
        return str(sheet.get(cell))
    except ValueError:  # empty cell
        return None


def _alias_cells(sheet):
    return [c for c in sheet.getNonEmptyCells() if sheet.getAlias(c)]


def _unknown_key(sheet, key):
    aliases = [sheet.getAlias(c) for c in _alias_cells(sheet)]
    near = case_insensitive_match(key, aliases)
    if near:
        return (f"No alias '{key}' in '{sheet.Label}' — did you mean '{near}'? "
                "Aliases are case-sensitive.")
    return (f"'{key}' is neither an alias nor a cell address in '{sheet.Label}'. "
            f"Aliases: {', '.join(aliases) or 'none'}")


def _handle_read_spreadsheet(object_name="", cells=None) -> ToolResult:
    doc = _active_doc()
    if not doc:
        return _fail("No active document.")
    sheet, err = _lookup(doc, object_name, "Spreadsheet::Sheet")
    if err:
        return err
    keys = as_list(cells)
    if keys:
        addresses = []
        for key in keys:
            cell = resolve_cell(sheet, key)
            if not cell:
                return _fail(_unknown_key(sheet, key))
            addresses.append(cell)
    else:
        addresses = list(sheet.getNonEmptyCells())
    truncated = len(addresses) > _READ_CAP
    addresses = addresses[:_READ_CAP]
    rows = [{"cell": c, "alias": sheet.getAlias(c),
             "contents": sheet.getContents(c), "value": _cell_value(sheet, c)}
            for c in addresses]
    if not rows:
        output = f"Spreadsheet '{sheet.Label}' ({sheet.Name}) is empty."
    else:
        more = (f" (first {_READ_CAP}; pass cells=[...] for others)"
                if truncated else "")
        output = (f"Spreadsheet '{sheet.Label}' ({sheet.Name}), {len(rows)} cells{more}:\n"
                  + "\n".join(format_cell_row(r) for r in rows))
    return ToolResult(success=True, output=output,
                      data={"name": sheet.Name, "label": sheet.Label,
                            "cells": rows, "truncated": truncated})


def _handle_edit_spreadsheet(object_name="", set=None, remove=None) -> ToolResult:
    # `set` shadows the builtin here; it is the tool's public parameter name.
    try:
        updates = _as_dict(set, "set")
    except ValueError as e:
        return _fail(str(e))
    removals = as_list(remove)
    if not updates and not removals:
        return _fail("Nothing to do: pass set={alias_or_cell: value} "
                     "and/or remove=[alias_or_cell].")
    both = [k for k in updates if k in removals]
    if both:
        return _fail(f"{', '.join(both)} is in both set and remove.")

    def do(doc):
        sheet, err = _lookup(doc, object_name, "Spreadsheet::Sheet")
        if err:
            return err
        used = list(sheet.getNonEmptyCells())
        aliases = [sheet.getAlias(c) for c in used if sheet.getAlias(c)]
        value_col, label_col = layout_columns(
            [c for c in used if sheet.getAlias(c)])
        next_row = last_row(used) + 1

        # Validate every entry before the first change (see edit_variable_set).
        plan = []  # (key, cell, text, label_cell or None)
        for key, value in updates.items():
            try:
                text = cell_text(value)
            except ValueError as e:
                raise ValueError(f"{key}: {e}")
            cell = resolve_cell(sheet, key)
            if cell:
                plan.append((key, cell, text, None))
                continue
            if case_insensitive_match(key, aliases):
                raise ValueError(_unknown_key(sheet, key))
            if not is_valid_alias(key):
                raise ValueError(
                    f"'{key}' can't be an alias: use letters, digits and _, "
                    "start with a letter, and avoid cell addresses like B7")
            if next_row > MAX_ROW:
                raise ValueError(f"'{sheet.Label}' has no free row left")
            plan.append((key, f"{value_col}{next_row}", text,
                         f"{label_col}{next_row}"))
            next_row += 1
        doomed = []
        for key in removals:
            cell = resolve_cell(sheet, key)
            if not cell:
                raise ValueError(_unknown_key(sheet, key))
            alias = sheet.getAlias(cell)
            own = [sheet.getContents(c) for c in used if c != cell]
            own = [f for f in own if f.startswith("=")]
            users = _referrers(sheet, [cell] + ([alias] if alias else []), own)
            if users:
                raise ValueError(f"'{key}' is still used by {', '.join(users)}"
                                 " — remove those uses first")
            doomed.append((key, cell, alias))

        changes = []
        for key, cell, text, label_cell in plan:
            if label_cell:
                sheet.set(label_cell, key)
                sheet.set(cell, text)
                sheet.setAlias(cell, key)
                changes.append({"name": key, "cell": cell, "action": "added",
                                "previous": None})
            else:
                changes.append({"name": key, "cell": cell, "action": "changed",
                                "previous": sheet.getContents(cell) or "(empty)"})
                sheet.set(cell, text)
        for key, cell, alias in doomed:
            changes.append({"name": key, "cell": cell, "action": "removed",
                            "previous": sheet.getContents(cell), "value": None})
            sheet.clear(cell)  # also drops the alias
            col, row = split_address(cell)
            label_cell = f"{label_col}{row}"
            if alias and col == value_col and sheet.getContents(label_cell) == alias:
                sheet.clear(label_cell)
        doc.recompute()
        for change in changes:
            if change["action"] != "removed":
                change["value"] = _cell_value(sheet, change["cell"])
        return ToolResult(
            success=True,
            output=(f"Updated spreadsheet '{sheet.Label}': "
                    + "; ".join(describe_change(c) for c in changes)),
            data={"name": sheet.Name, "label": sheet.Label, "changes": changes},
        )

    return _with_undo("Edit Spreadsheet", do)


READ_SPREADSHEET = ToolDefinition(
    name="read_spreadsheet",
    description=(
        "Read spreadsheet cells: address, alias, contents (formulas included) "
        "and computed value. cells takes aliases or addresses, e.g. "
        "['width', 'B3']; omit it to read every non-empty cell."
    ),
    category="query",
    parameters=[
        ToolParam("object_name", "string", "Name or label of the spreadsheet"),
        ToolParam("cells", "array", "Aliases or cell addresses; omit for all",
                  required=False, items={"type": "string"}),
    ],
    handler=_handle_read_spreadsheet,
)

EDIT_SPREADSHEET = ToolDefinition(
    name="edit_spreadsheet",
    description=(
        "Change, add or remove spreadsheet cells in one undoable step; changes "
        "persist when the document is saved. set={alias_or_cell: value}: an "
        "existing alias or address ('width', 'B3') is overwritten, a new name "
        "becomes a new aliased row (name, value). Values: numbers, '50 mm', or "
        "formulas like '=width*2'. remove=[alias_or_cell] clears cells that no "
        "formula or expression uses. Use this, not modify_property, for "
        "spreadsheet values. If any entry is invalid, nothing changes."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Name or label of the spreadsheet"),
        ToolParam("set", "object",
                  "Cells to change or add, e.g. {\"width\": 60, \"B3\": \"=width*2\"}",
                  required=False),
        ToolParam("remove", "array", "Aliases or cell addresses to clear",
                  required=False, items={"type": "string"}),
    ],
    handler=_handle_edit_spreadsheet,
)
```

Extend the list:

```python
PARAMETER_TOOLS = [
    READ_VARIABLE_SET,
    EDIT_VARIABLE_SET,
    READ_SPREADSHEET,
    EDIT_SPREADSHEET,
]
```

In `_handle_create_spreadsheet` (`freecad_tools.py`), swap the columns so the name comes first:

```python
            row = i + 1
            cell = f"B{row}"
            sheet.set(f"A{row}", str(name))
            sheet.set(cell, str(value))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q && env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/parameter_tools.py freecad_ai/tools/freecad_tools.py \
        tests/unit/test_parameter_tools.py tests/integration/test_parameter_tools_integration.py
git commit -m "feat(tools): read_spreadsheet and edit_spreadsheet (#121)

Edits go through sheet.set(), so they persist on save. create_spreadsheet
now writes name before value; edit_spreadsheet follows either layout.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `modify_property` guard and value coercion

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (`_handle_modify_property`)
- Test: `tests/unit/test_parameter_tools.py`, `tests/integration/test_parameter_tools_integration.py`

**Interfaces:**
- Consumes: `resolve_cell` and `coerce_for_property` from Task 1.

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/unit/test_parameter_tools.py`:

```python
class _Obj(types.SimpleNamespace):
    def getTypeIdOfProperty(self, name):
        return self._types[name]


def _doc_with(obj):
    return types.SimpleNamespace(
        Objects=[obj], getObject=lambda n: obj if n == obj.Name else None)


def _run_modify(monkeypatch, obj, prop, value):
    from freecad_ai.tools import freecad_tools as ft
    doc = _doc_with(obj)
    monkeypatch.setattr(ft, "_with_undo", lambda label, do: do(doc))
    return ft._handle_modify_property(object_name=obj.Name,
                                      property_name=prop, value=value)


class TestModifyProperty:
    def test_refuses_spreadsheet_alias(self, monkeypatch):
        sheet = _Obj(Name="Params", Label="Params", TypeId="Spreadsheet::Sheet",
                     width=5, _types={},
                     getCellFromAlias=lambda k: "B1" if k == "width" else None)
        r = _run_modify(monkeypatch, sheet, "width", "7")
        assert not r.success
        assert "edit_spreadsheet" in r.error
        assert sheet.width == 5

    def test_float_property_accepts_string(self, monkeypatch):
        vs = _Obj(Name="Vars", Label="Vars", TypeId="App::VarSet", ratio=0.5,
                  _types={"ratio": "App::PropertyFloat"})
        r = _run_modify(monkeypatch, vs, "ratio", "0.75")
        assert r.success, r.error
        assert vs.ratio == 0.75

    def test_relative_change_on_integer_stays_integer(self, monkeypatch):
        vs = _Obj(Name="Vars", Label="Vars", TypeId="App::VarSet", count=4,
                  _types={"count": "App::PropertyInteger"})
        r = _run_modify(monkeypatch, vs, "count", "*2")
        assert r.success, r.error
        assert vs.count == 8 and isinstance(vs.count, int)
```

- [ ] **Step 2: Write the failing integration test**

Append to `tests/integration/test_parameter_tools_integration.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q -k ModifyProperty`
Expected: FAIL. The alias write succeeds, and `ratio` stays a string or setattr raises on the fake.

- [ ] **Step 4: Implement**

In `_handle_modify_property`, add this guard right after the `if not obj:` block:

```python
        if getattr(obj, "TypeId", "") == "Spreadsheet::Sheet":
            from .parameter_tools import resolve_cell
            if resolve_cell(obj, property_name):
                return ToolResult(
                    success=False, output="",
                    error=(f"'{property_name}' is a cell of spreadsheet "
                           f"'{obj.Label}'. modify_property would change only "
                           "its computed value, and the change would be lost "
                           f"on reload. Use edit_spreadsheet(object_name="
                           f"'{obj.Name}', set={{'{property_name}': <value>}})."),
                )
```

Next, find these lines:

```python
        current = getattr(obj, property_name)
        resolved = _resolve_relative_value(current, value)

        # Report old→new for relative changes
        if resolved != value:
```

Replace them with:

```python
        current = getattr(obj, property_name)
        resolved = _resolve_relative_value(current, value)
        relative = resolved != value

        # Float/Integer/Bool properties reject strings like "60" (#121)
        try:
            type_id = obj.getTypeIdOfProperty(property_name)
        except Exception:
            type_id = ""
        if type_id in ("App::PropertyFloat", "App::PropertyInteger",
                       "App::PropertyBool"):
            from .parameter_tools import coerce_for_property
            resolved = coerce_for_property(type_id, resolved)

        # Report old→new for relative changes
        if relative:
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q && env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q -k ModifyPropertyGuard && env PYTHONPATH= .venv/bin/pytest tests/unit -q -k modify --ignore=tests/unit/test_document_attach.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_parameter_tools.py \
        tests/integration/test_parameter_tools_integration.py
git commit -m "fix(tools): modify_property refuses sheet aliases, coerces numeric strings (#121)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `delete_object`

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py`: add the handler and definition after `DUPLICATE_OBJECT`, and add `DELETE_OBJECT` to `ALL_TOOLS` after `DUPLICATE_OBJECT`
- Test: `tests/unit/test_parameter_tools.py`, `tests/integration/test_parameter_tools_integration.py`

**Interfaces:**
- Produces: `_handle_delete_object(object_name, force=False)`, `DELETE_OBJECT`.

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/unit/test_parameter_tools.py`:

```python
def _run_delete(monkeypatch, objects, name, force=False):
    from freecad_ai.tools import freecad_tools as ft
    removed = []
    doc = types.SimpleNamespace(
        Objects=objects,
        getObject=lambda n: next((o for o in objects if o.Name == n), None),
        removeObject=removed.append)
    monkeypatch.setattr(ft, "_with_undo", lambda label, do: do(doc))
    return ft._handle_delete_object(object_name=name, force=force), removed


class TestDeleteObject:
    def _feature_in_body(self):
        body = types.SimpleNamespace(Name="Body", Label="Body",
                                     TypeId="PartDesign::Body", InList=[])
        pad = types.SimpleNamespace(Name="Pad", Label="Pad",
                                    TypeId="PartDesign::Pad", InList=[body])
        return [body, pad]

    def test_unused_object_is_deleted(self, monkeypatch):
        box = types.SimpleNamespace(Name="Box", Label="Box",
                                    TypeId="Part::Box", InList=[])
        r, removed = _run_delete(monkeypatch, [box], "Box")
        assert r.success, r.error
        assert removed == ["Box"]

    def test_refuses_feature_listed_by_its_body(self, monkeypatch):
        r, removed = _run_delete(monkeypatch, self._feature_in_body(), "Pad")
        assert not r.success
        assert "Body" in r.error and "force" in r.error
        assert removed == []

    def test_force_deletes_and_reports_dependents(self, monkeypatch):
        r, removed = _run_delete(monkeypatch, self._feature_in_body(), "Pad",
                                 force="true")
        assert r.success, r.error
        assert removed == ["Pad"]
        assert "Body" in r.output

    def test_missing_object(self, monkeypatch):
        r, removed = _run_delete(monkeypatch, [], "Nope")
        assert not r.success and "not found" in r.error

    def test_in_all_tools(self):
        from freecad_ai.tools.freecad_tools import ALL_TOOLS, DELETE_OBJECT
        assert DELETE_OBJECT in ALL_TOOLS
        assert DELETE_OBJECT.category == "modeling"
```

- [ ] **Step 2: Write the failing integration test**

Append to `tests/integration/test_parameter_tools_integration.py`:

```python
class TestDeleteObject:
    def test_refuses_used_object_then_force_deletes(self, run_freecad_script):
        result = run_freecad_script(_SHEET + """
from freecad_ai.tools.freecad_tools import _handle_delete_object
box = doc.addObject("Part::Box", "Box")
box.setExpression("Length", "Params.width")
doc.recompute()
refused = _handle_delete_object(object_name="Params")
forced = _handle_delete_object(object_name="Params", force=True)
gone = _handle_delete_object(object_name="Box")
results["data"] = {"refused_ok": refused.success, "refused_err": refused.error,
                   "forced_ok": forced.success, "gone_ok": gone.success,
                   "names": [o.Name for o in doc.Objects]}
""")
        assert result["ok"], result.get("error")
        d = result["data"]
        assert not d["refused_ok"] and "Box" in d["refused_err"]
        assert d["forced_ok"] and d["gone_ok"]
        assert d["names"] == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q -k DeleteObject`
Expected: FAIL, `AttributeError: ... '_handle_delete_object'`

- [ ] **Step 4: Implement**

In `freecad_tools.py`, after the `DUPLICATE_OBJECT` definition:

```python
# ── delete_object ───────────────────────────────────────────

def _handle_delete_object(object_name: str, force=False) -> ToolResult:
    """Delete a document object; refuse while other objects still use it."""
    force = force is True or str(force).strip().lower() == "true"

    def do(doc):
        obj = _get_object(doc, object_name)
        if not obj:
            hint = _suggest_similar(doc, object_name)
            return ToolResult(success=False, output="",
                              error=f"Object '{object_name}' not found.{hint}")
        users = [o.Label for o in obj.InList]
        if users and not force:
            return ToolResult(
                success=False, output="",
                error=(f"'{obj.Label}' is used by {', '.join(users)} (a "
                       "containing Body or Part counts too). Deleting it "
                       "would break them: delete or rewire those first, or "
                       "pass force=true to delete anyway."),
            )
        name, label, type_id = obj.Name, obj.Label, obj.TypeId
        doc.removeObject(name)
        msg = f"Deleted '{label}' ({name}, {type_id})"
        if users:
            msg += f". Still referring to it, check them: {', '.join(users)}"
        return ToolResult(success=True, output=msg,
                          data={"name": name, "label": label,
                                "type": type_id, "users": users})

    return _with_undo("Delete Object", do)


DELETE_OBJECT = ToolDefinition(
    name="delete_object",
    description=(
        "Delete a document object (feature, sketch, VarSet, spreadsheet, ...). "
        "Refuses while other objects use it, naming them; pass force=true to "
        "delete anyway. Deleting a Body or Part does not delete its contents. "
        "Undo reverts it."
    ),
    category="modeling",
    parameters=[
        ToolParam("object_name", "string", "Name or label of the object"),
        ToolParam("force", "boolean",
                  "Delete even though other objects still use it",
                  required=False, default=False),
    ],
    handler=_handle_delete_object,
)
```

In `ALL_TOOLS`, add `DELETE_OBJECT,` on the line after `DUPLICATE_OBJECT,`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_parameter_tools.py -q && env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_parameter_tools.py \
        tests/integration/test_parameter_tools_integration.py
git commit -m "feat(tools): delete_object refuses while dependents exist (#121)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Docs, prompt, full suite, PR

**Files:**
- Modify: `README.md` (tool table, rows after `modify_property`; any tool count)
- Modify: `freecad_ai/core/system_prompt.py` (tool guidance, after the `modify_property` line)
- Modify: `CHANGELOG.md` (`## [Unreleased]`)

- [ ] **Step 1: README tool table**

After the `| \`modify_property\` | … |` row, add:

```markdown
| `read_variable_set` | Read a VarSet's variables with types, units and expressions |
| `edit_variable_set` | Change, add or remove VarSet variables (unit-aware) |
| `read_spreadsheet` | Read spreadsheet cells: alias, contents, computed value |
| `edit_spreadsheet` | Change, add or remove spreadsheet cells by alias or address |
| `delete_object` | Delete an object; refuses while other objects still use it |
```

Then run `grep -n "[0-9][0-9] tools" README.md`. If it prints a built-in tool count, raise that count by 5.

- [ ] **Step 2: System prompt**

In `freecad_ai/core/system_prompt.py`, after the line ``- For changing object properties (length, width, label, visibility, etc.): use `modify_property` ``, add:

```
- For reading or changing parameters in a VarSet or Spreadsheet: use `read_variable_set`/`edit_variable_set` or `read_spreadsheet`/`edit_spreadsheet` — never `modify_property` on a spreadsheet cell (the change is lost on reload)
- For deleting an object: use `delete_object`
```

- [ ] **Step 3: CHANGELOG**

Under `## [Unreleased]` → `### Added`, after the Reference-button entry:

```markdown
- **Maintain VarSets and Spreadsheets, delete objects** (#121).
  `read_variable_set` / `edit_variable_set` and `read_spreadsheet` /
  `edit_spreadsheet` read and change parameters after creation: set
  changes or adds, remove deletes, and one bad entry changes nothing.
  Spreadsheet keys take an alias or a cell address. Removing anything an
  expression still uses is refused and names the user. `delete_object`
  refuses while dependents exist unless `force=true`.
```

Then add a `### Fixed` section below `### Added`, if one doesn't exist:

```markdown
### Fixed

- **`modify_property` on a spreadsheet alias reported success, then lost
  the change on reload** (#121) — it now refuses and points to
  `edit_spreadsheet`, which writes the cell contents. It also converts
  `"60"` for Float/Integer/Bool properties instead of failing.
- **`create_variable_set` ignored units** (#121) — `"50 mm"` now becomes
  a Length, `"30 deg"` an Angle. `create_spreadsheet` writes the name
  before the value.
```

- [ ] **Step 4: Full suite**

Run: `env PYTHONPATH= .venv/bin/pytest -q --ignore=tests/unit/test_document_attach.py`
Expected: every test passes. The count is the post-#119 baseline of 2561 plus the new unit tests. Record the exact number from the output and don't predict it.

Run: `env PYTHONPATH= .venv/bin/pytest -m integration tests/integration/test_parameter_tools_integration.py -q`
Expected: all pass.

- [ ] **Step 5: Commit and open the PR**

```bash
git add README.md CHANGELOG.md freecad_ai/core/system_prompt.py
git commit -m "docs: VarSet/Spreadsheet tools and delete_object (#121)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push -u origin feat/121-varset-spreadsheet-tools
```

Open the PR with `gh pr create`. Use the title `feat(tools): read/edit VarSet & Spreadsheet, delete_object (#121)`. In the body:
- Summarize the tools and the three defect fixes.
- List the probe facts.
- Report the exact unit and integration test counts.
- Include a GUI test recipe. Ask the model "make a spreadsheet with width 50", then "change width to 70". Save, reopen, and check that the cell shows 70.
- End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Don't merge until the user has run the GUI test.
