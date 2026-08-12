"""Geometry validation engine for skill output verification.

Parses VALIDATION.md files and runs geometric checks against FreeCAD documents.
"""
import ast
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safe arithmetic evaluator
# ---------------------------------------------------------------------------

_SAFE_FUNCTIONS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
}

_SAFE_CONSTANTS = {
    "pi": math.pi,
}


class _ArithmeticEvaluator(ast.NodeVisitor):
    """Walk an AST and evaluate arithmetic expressions safely."""

    def __init__(self, variables: dict[str, float]):
        self._vars = variables

    def visit_Expression(self, node: ast.Expression) -> float:  # noqa: N802
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> float:  # noqa: N802
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    def visit_Name(self, node: ast.Name) -> float:  # noqa: N802
        if node.id in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[node.id]
        if node.id in self._vars:
            return float(self._vars[node.id])
        raise ValueError(f"Unknown variable: {node.id}")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:  # noqa: N802
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

    def visit_BinOp(self, node: ast.BinOp) -> float:  # noqa: N802
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = type(node.op)
        if op is ast.Add:
            return left + right
        if op is ast.Sub:
            return left - right
        if op is ast.Mult:
            return left * right
        if op is ast.Div:
            if right == 0:
                raise ValueError("Division by zero")
            return left / right
        if op is ast.Pow:
            return left ** right
        if op is ast.Mod:
            if right == 0:
                raise ValueError("Modulo by zero")
            return left % right
        raise ValueError(f"Unsupported operator: {type(node.op).__name__}")

    def visit_Call(self, node: ast.Call) -> float:  # noqa: N802
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")
        name = node.func.id
        if name not in _SAFE_FUNCTIONS:
            raise ValueError(f"Unknown function: {name}")
        args = [self.visit(arg) for arg in node.args]
        if node.keywords:
            raise ValueError("Keyword arguments not supported")
        return float(_SAFE_FUNCTIONS[name](*args))

    def generic_visit(self, node: ast.AST) -> float:
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")


def safe_arithmetic(expression: str, variables: Optional[dict[str, float]] = None) -> float:
    """Evaluate a simple arithmetic expression safely.

    Supports +, -, *, /, **, %, parentheses, pi, sqrt/abs/min/max.
    Raises ValueError for anything unsafe.
    """
    if variables is None:
        variables = {}

    expr = expression.strip()
    if not expr:
        raise ValueError("Empty expression")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression: {e}") from e

    evaluator = _ArithmeticEvaluator(variables)
    return evaluator.visit(tree)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParamDef:
    """Parameter definition from VALIDATION.md."""

    name: str
    type: str  # "float", "int", "str", "bool"
    default: Optional[Any] = None


@dataclass
class ValidationRule:
    """A single validation check rule."""

    target: str  # object label or "_document"
    check: str  # check type name
    expected: str  # expression string (evaluated at check time)
    tolerance: float = 0.0
    tolerance_type: str = "absolute"  # "absolute" or "relative"
    condition: Optional[str] = None  # e.g. 'lid_type == "screw"'


@dataclass
class CheckResult:
    """Result of a single validation check."""

    target: str
    check: str
    passed: bool
    expected: Any
    actual: Any
    message: str


# ---------------------------------------------------------------------------
# VALIDATION.md parser
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(
    r"^(\w+)\s*:\s*(float|int|str|bool)(?:\s*=\s*(.+))?$"
)

_CHECK_RE = re.compile(
    r"^-\s+(\w+)\s*:\s*(.+)$"
)

_TOLERANCE_ABS_RE = re.compile(
    r"\(tolerance\s+([\d.]+)\)\s*$"
)

_TOLERANCE_PCT_RE = re.compile(
    r"\(tolerance\s+([\d.]+)%\)\s*$"
)

_CONDITION_RE = re.compile(
    r"^####\s+when\s+(.+)$"
)


def _coerce_default(value_str: str, type_str: str) -> Any:
    """Convert a default value string to the appropriate type."""
    value_str = value_str.strip()
    if type_str == "float":
        return float(value_str)
    if type_str == "int":
        return int(value_str)
    if type_str == "bool":
        return value_str.lower() in ("true", "1", "yes")
    # str
    return value_str


def parse_validation_md(content: str) -> tuple[dict[str, ParamDef], list[ValidationRule]]:
    """Parse a VALIDATION.md file into parameter definitions and rules.

    Returns (param_defs, rules).
    """
    param_defs: dict[str, ParamDef] = {}
    rules: list[ValidationRule] = []

    if not content or not content.strip():
        return param_defs, rules

    section = ""  # "parameters" or "checks"
    current_target = "_document"
    current_condition: Optional[str] = None

    for line in content.splitlines():
        stripped = line.strip()

        # Section headers (h2)
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            if "parameter" in heading:
                section = "parameters"
            elif "check" in heading:
                section = "checks"
                current_target = "_document"
                current_condition = None
            continue

        if section == "parameters":
            m = _PARAM_RE.match(stripped)
            if m:
                name, typ, default_str = m.groups()
                default = _coerce_default(default_str, typ) if default_str else None
                param_defs[name] = ParamDef(name=name, type=typ, default=default)

        elif section == "checks":
            # h3 heading — target object (new h3 resets condition)
            if stripped.startswith("### "):
                heading_text = stripped[4:].strip()
                # If heading contains spaces, it's a document-level check
                if " " in heading_text:
                    current_target = "_document"
                else:
                    current_target = heading_text
                current_condition = None
                continue

            # h4 heading — conditional block
            cond_m = _CONDITION_RE.match(stripped)
            if cond_m:
                current_condition = cond_m.group(1).strip()
                continue

            # Check line
            check_m = _CHECK_RE.match(stripped)
            if check_m:
                check_name = check_m.group(1)
                value_part = check_m.group(2).strip()

                # Extract tolerance
                tolerance = 0.0
                tolerance_type = "absolute"

                tol_pct = _TOLERANCE_PCT_RE.search(value_part)
                if tol_pct:
                    tolerance = float(tol_pct.group(1))
                    tolerance_type = "relative"
                    value_part = value_part[: tol_pct.start()].strip()
                else:
                    tol_abs = _TOLERANCE_ABS_RE.search(value_part)
                    if tol_abs:
                        tolerance = float(tol_abs.group(1))
                        tolerance_type = "absolute"
                        value_part = value_part[: tol_abs.start()].strip()

                rules.append(
                    ValidationRule(
                        target=current_target,
                        check=check_name,
                        expected=value_part,
                        tolerance=tolerance,
                        tolerance_type=tolerance_type,
                        condition=current_condition,
                    )
                )

    return param_defs, rules


# ---------------------------------------------------------------------------
# Check execution
# ---------------------------------------------------------------------------


def _check_condition(condition: str, params: dict[str, Any]) -> bool:
    """Evaluate a simple condition like 'lid_type == "screw"' or 'lid_type != "none"'."""
    # Support != and ==
    for op in ("!=", "=="):
        if op in condition:
            parts = condition.split(op, 1)
            if len(parts) == 2:
                var_name = parts[0].strip()
                expected_val = parts[1].strip().strip('"').strip("'")
                actual_val = str(params.get(var_name, ""))
                if op == "==":
                    return actual_val == expected_val
                else:
                    return actual_val != expected_val
    # Unknown condition format — treat as not matching
    logger.warning("Could not parse condition: %s", condition)
    return False


def _get_object(doc: Any, label: str) -> Any:
    """Find object by label (or name) in a document."""
    results = doc.getObjectsByLabel(label)
    if results:
        return results[0]
    # Fallback: search by Name attribute
    for obj in doc.Objects:
        if getattr(obj, "Name", None) == label:
            return obj
    return None


def _eval_expected(expected: str, params: dict[str, float]) -> float:
    """Evaluate an expected value expression with params as variables."""
    return safe_arithmetic(expected, params)


def _numeric_params(params: dict[str, Any]) -> dict[str, float]:
    """Extract only numeric params for use in arithmetic evaluation."""
    result = {}
    for k, v in params.items():
        try:
            result[k] = float(v)
        except (TypeError, ValueError):
            pass
    return result


def run_checks(doc: Any, params: dict[str, Any], rules: list[ValidationRule]) -> list[CheckResult]:
    """Execute validation rules against a FreeCAD document."""
    results: list[CheckResult] = []
    num_params = _numeric_params(params)

    for rule in rules:
        # Evaluate condition
        if rule.condition is not None:
            if not _check_condition(rule.condition, params):
                results.append(
                    CheckResult(
                        target=rule.target,
                        check=rule.check,
                        passed=True,
                        expected=rule.expected,
                        actual="skipped (condition not met)",
                        message=f"Skipped: condition '{rule.condition}' not met",
                    )
                )
                continue

        try:
            result = _run_single_check(doc, params, num_params, rule)
            results.append(result)
        except Exception as e:
            results.append(
                CheckResult(
                    target=rule.target,
                    check=rule.check,
                    passed=False,
                    expected=rule.expected,
                    actual=None,
                    message=f"Error: {e}",
                )
            )

    return results


def _compare_value(actual: float, expected: float, tolerance: float, tolerance_type: str) -> bool:
    """Compare actual vs expected with tolerance."""
    if tolerance_type == "relative":
        # Relative tolerance is a percentage
        if expected == 0:
            return actual == 0
        return abs(actual - expected) / abs(expected) * 100 <= tolerance
    else:
        return abs(actual - expected) <= tolerance


def _run_single_check(
    doc: Any,
    params: dict[str, Any],
    num_params: dict[str, float],
    rule: ValidationRule,
) -> CheckResult:
    """Run a single validation check."""
    check = rule.check
    target = rule.target

    # --- Document-level checks ---
    if check == "total_bodies":
        expected_val = _eval_expected(rule.expected, num_params)
        actual = sum(
            1
            for obj in doc.Objects
            if getattr(obj, "TypeId", None) == "PartDesign::Body"
        )
        passed = actual == int(expected_val)
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=int(expected_val),
            actual=actual,
            message=f"Expected {int(expected_val)} bodies, found {actual}",
        )

    if check == "exists":
        obj = _get_object(doc, target)
        passed = obj is not None
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=True,
            actual=passed,
            message=f"Object '{target}' {'found' if passed else 'not found'}",
        )

    # --- Object-level checks (need object) ---
    obj = _get_object(doc, target)
    if obj is None and check not in ("total_bodies", "exists"):
        return CheckResult(
            target=target,
            check=check,
            passed=False,
            expected=rule.expected,
            actual=None,
            message=f"Object '{target}' not found",
        )

    if check == "bbox":
        # Expected: "expr1, expr2, expr3"
        parts = [p.strip() for p in rule.expected.split(",")]
        if len(parts) != 3:
            return CheckResult(
                target=target,
                check=check,
                passed=False,
                expected=rule.expected,
                actual=None,
                message="bbox requires 3 comma-separated values",
            )
        expected_dims = [_eval_expected(p, num_params) for p in parts]
        bbox = obj.Shape.BoundBox
        actual_dims = [bbox.XLength, bbox.YLength, bbox.ZLength]
        passed = all(
            _compare_value(a, e, rule.tolerance, "absolute")
            for a, e in zip(actual_dims, expected_dims)
        )
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=expected_dims,
            actual=actual_dims,
            message=f"BBox expected {expected_dims}, got {actual_dims}",
        )

    if check == "bbox_position":
        # Expected: "zmin_expr, zmax_expr" — check absolute Z position
        parts = [p.strip() for p in rule.expected.split(",")]
        if len(parts) != 2:
            return CheckResult(
                target=target,
                check=check,
                passed=False,
                expected=rule.expected,
                actual=None,
                message="bbox_position requires 2 values: zmin, zmax",
            )
        expected_zmin = _eval_expected(parts[0], num_params)
        expected_zmax = _eval_expected(parts[1], num_params)
        bbox = obj.Shape.BoundBox
        tol = rule.tolerance or 0.5
        zmin_ok = _compare_value(bbox.ZMin, expected_zmin, tol, "absolute")
        zmax_ok = _compare_value(bbox.ZMax, expected_zmax, tol, "absolute")
        passed = zmin_ok and zmax_ok
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=f"Z[{expected_zmin:.1f}, {expected_zmax:.1f}]",
            actual=f"Z[{bbox.ZMin:.1f}, {bbox.ZMax:.1f}]",
            message=f"Position expected Z[{expected_zmin:.1f}, {expected_zmax:.1f}], "
                    f"got Z[{bbox.ZMin:.1f}, {bbox.ZMax:.1f}]",
        )

    if check == "volume":
        expected_val = _eval_expected(rule.expected, num_params)
        actual_val = obj.Shape.Volume
        passed = _compare_value(actual_val, expected_val, rule.tolerance, rule.tolerance_type)
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=expected_val,
            actual=actual_val,
            message=f"Volume expected {expected_val}, got {actual_val}",
        )

    if check == "section_area":
        # Expected: "axis, offset_expr, area_expr"
        # axis: X, Y, or Z (case-insensitive)
        # offset_expr: where to slice (expression using params)
        # area_expr: expected cross-section area (expression using params)
        parts = [p.strip() for p in rule.expected.split(",")]
        if len(parts) != 3:
            return CheckResult(
                target=target,
                check=check,
                passed=False,
                expected=rule.expected,
                actual=None,
                message="section_area requires 3 values: axis, offset, area",
            )
        axis_name = parts[0].upper()
        axis_map = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}
        if axis_name not in axis_map:
            return CheckResult(
                target=target,
                check=check,
                passed=False,
                expected=rule.expected,
                actual=None,
                message=f"section_area axis must be X, Y, or Z, got '{parts[0]}'",
            )
        offset = _eval_expected(parts[1], num_params)
        expected_area = _eval_expected(parts[2], num_params)
        try:
            try:
                import FreeCAD
                import Part
            except ImportError:
                from ..branding import APP_NAME
                return CheckResult(
                    target=target, check=check, passed=False,
                    expected=str(expected_area),
                    actual="{} not available".format(APP_NAME),
                    message="section_area: {} not available".format(APP_NAME),
                )
            direction = FreeCAD.Vector(*axis_map[axis_name])
            wires = obj.Shape.slice(direction, offset)
            if wires:
                face = Part.Face(wires)
                actual_area = face.Area
            else:
                actual_area = 0.0
        except Exception as e:
            # slice can fail if offset is outside shape or geometry is complex
            actual_area = 0.0
            logger.warning("section_area slice failed for %s at %s=%s: %s",
                           target, axis_name, offset, e)
        passed = _compare_value(
            actual_area, expected_area, rule.tolerance, rule.tolerance_type)
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=f"{expected_area:.1f}",
            actual=f"{actual_area:.1f}",
            message=f"Section area at {axis_name}={offset:.1f}: "
                    f"expected {expected_area:.1f}, got {actual_area:.1f}",
        )

    if check == "solid_count":
        expected_val = int(_eval_expected(rule.expected, num_params))
        actual_val = len(obj.Shape.Solids)
        passed = actual_val == expected_val
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=expected_val,
            actual=actual_val,
            message=f"Solid count expected {expected_val}, got {actual_val}",
        )

    if check == "valid_solid":
        is_valid = obj.Shape.isValid()
        has_solids = len(obj.Shape.Solids) >= 1
        passed = is_valid and has_solids
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=True,
            actual=passed,
            message=f"Valid solid: isValid={is_valid}, solids={len(obj.Shape.Solids)}",
        )

    if check == "has_holes":
        expected_val = int(_eval_expected(rule.expected, num_params))
        group = getattr(obj, "Group", [])
        actual_val = sum(
            1
            for feat in group
            if getattr(feat, "TypeId", None) == "PartDesign::Pocket"
            and getattr(feat, "Type", None) == "ThroughAll"
        )
        passed = actual_val == expected_val
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=expected_val,
            actual=actual_val,
            message=f"Through-all holes expected {expected_val}, got {actual_val}",
        )

    if check == "has_feature":
        group = getattr(obj, "Group", [])
        found = any(
            getattr(feat, "Label", None) == rule.expected
            for feat in group
        )
        return CheckResult(
            target=target,
            check=check,
            passed=found,
            expected=rule.expected,
            actual=found,
            message=f"Feature '{rule.expected}' {'found' if found else 'not found'} in {target}",
        )

    if check == "min_children":
        expected_val = int(_eval_expected(rule.expected, num_params))
        group = getattr(obj, "Group", [])
        actual_val = len(group)
        passed = actual_val >= expected_val
        return CheckResult(
            target=target,
            check=check,
            passed=passed,
            expected=expected_val,
            actual=actual_val,
            message=f"Min children expected {expected_val}, got {actual_val}",
        )

    # Unknown check type
    return CheckResult(
        target=target,
        check=check,
        passed=False,
        expected=rule.expected,
        actual=None,
        message=f"Unknown check type: {check}",
    )


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------


def _resolve_params(params: dict[str, Any], param_defs: dict[str, ParamDef]) -> dict[str, Any]:
    """Fill in defaults for missing params."""
    resolved = dict(params)
    for name, pdef in param_defs.items():
        if name not in resolved and pdef.default is not None:
            resolved[name] = pdef.default
    return resolved


def validate_skill(
    doc: Any,
    params: dict[str, Any],
    validation_content: str,
) -> list[CheckResult]:
    """Top-level API: parse VALIDATION.md, resolve defaults, run checks."""
    param_defs, rules = parse_validation_md(validation_content)
    resolved = _resolve_params(params, param_defs)
    return run_checks(doc, resolved, rules)


def compute_pass_rate(results: list[CheckResult]) -> float:
    """Compute the fraction of checks that passed (0.0 to 1.0)."""
    if not results:
        return 1.0
    return sum(1 for r in results if r.passed) / len(results)
