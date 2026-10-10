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
