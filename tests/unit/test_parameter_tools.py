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
