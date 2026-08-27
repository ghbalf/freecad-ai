"""User extension tools — discover, validate, and register user-authored tool functions.

Scans .py and .FCMacro files from USER_TOOLS_DIR and any provider tool
directories, validates with ast, imports and introspects functions, and wraps
each as a ToolDefinition.
"""

import ast
import importlib.machinery
import importlib.util
import os
import textwrap
from dataclasses import dataclass, field
from typing import Any

from ..tools.registry import ToolDefinition, ToolParam, ToolResult


# Python type hint -> JSON Schema type
_TYPE_MAP = {
    "float": "number",
    "int": "integer",
    "str": "string",
    "bool": "boolean",
    "list[str]": "array",
    "list[float]": "array",
    "list[int]": "array",
}

# Array types additionally need "items" — strict providers (OpenAI's
# marketplace API) reject an "array" property without it, see
# tests/unit/test_registry.py::test_every_array_property_has_items.
_ITEMS_MAP = {
    "list[str]": {"type": "string"},
    "list[float]": {"type": "number"},
    "list[int]": {"type": "integer"},
}

SUPPORTED_TYPES = set(_TYPE_MAP.keys())

# Module-level string a tool file may define to choose its own tool-name
# prefix. Provider modules use it to namespace their tools by workbench
# instead of inheriting the "user_" prefix.
TOOL_PREFIX_ATTR = "__tool_prefix__"

DEFAULT_TOOL_PREFIX = "user_"


@dataclass
class FuncParam:
    """A parameter extracted from a function signature via AST."""
    name: str
    type_name: str
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass
class FuncInfo:
    """Metadata for a single tool function extracted via AST."""
    name: str
    description: str
    params: list[FuncParam] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Result of validating a user tool file."""
    valid: bool
    functions: list[FuncInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    prefix: str = DEFAULT_TOOL_PREFIX


def validate_file(path: str) -> ValidationResult:
    """Validate a Python file for user tool functions using AST (no execution).

    Checks:
    - Valid Python syntax
    - At least one public function with supported type hints
    - Warns on missing docstrings, unsupported param types
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return ValidationResult(valid=False, error=f"Cannot read file: {e}")

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return ValidationResult(valid=False, error=f"Syntax error: {e}")

    functions: list[FuncInfo] = []
    warnings: list[str] = []
    prefix = _extract_tool_prefix(tree)

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_"):
            continue

        # Parse the docstring first — the model reads the tool description and
        # per-parameter descriptions to decide how to call the tool, so both
        # come from prose the author already wrote.
        description, param_docs = _parse_docstring(node)

        # Extract type-hinted parameters
        params: list[FuncParam] = []
        has_unsupported = False
        args = node.args

        # Count defaults: they align to the end of args.args
        num_args = len(args.args)
        num_defaults = len(args.defaults)
        first_default_idx = num_args - num_defaults

        for i, arg in enumerate(args.args):
            if arg.arg == "self":
                continue
            type_name = _annotation_name(arg.annotation)
            if not type_name:
                continue  # skip untyped params

            if type_name not in SUPPORTED_TYPES:
                has_unsupported = True
                warnings.append(
                    f"{node.name}(): param '{arg.arg}' has unsupported type '{type_name}'"
                )
                continue

            # Check for default value
            default_idx = i - first_default_idx
            has_default = 0 <= default_idx < num_defaults
            default_val = None
            if has_default:
                default_node = args.defaults[default_idx]
                default_val = _extract_constant(default_node)

            params.append(FuncParam(
                name=arg.arg,
                type_name=type_name,
                required=not has_default,
                default=default_val,
                description=param_docs.get(arg.arg, ""),
            ))

        # A function that declares no parameters at all is a legitimate tool - "list
        # what is available" needs no arguments. Only skip functions whose parameters
        # exist but are all untyped, since there is no schema to build from those.
        if not params and node.args.args:
            continue

        if not description:
            warnings.append(f"{node.name}(): missing docstring")

        functions.append(FuncInfo(
            name=node.name,
            description=description or f"User tool: {node.name}",
            params=params,
        ))

    if not functions:
        return ValidationResult(
            valid=False,
            warnings=warnings,
            error="No valid tool functions found (need public functions with type hints)",
            prefix=prefix,
        )

    return ValidationResult(
        valid=True, functions=functions, warnings=warnings, prefix=prefix
    )


def load_user_tools(
    tools_dir: str,
    disabled: list[str] | None = None,
    extra_dirs: list[str] | None = None,
) -> list[ToolDefinition]:
    """Discover, validate, import, and wrap user tool functions.

    Args:
        tools_dir: Primary directory to scan for .py/.FCMacro files.
        disabled: List of filenames to skip.
        extra_dirs: Additional directories to scan (e.g. FreeCAD macro dir).
            Primary dir takes precedence on name conflicts.

    Returns:
        List of ToolDefinition ready for registration.
    """
    disabled_set = set(disabled or [])
    tool_defs: list[ToolDefinition] = []

    dirs_to_scan = []
    if os.path.isdir(tools_dir):
        dirs_to_scan.append(tools_dir)
    for d in (extra_dirs or []):
        if os.path.isdir(d):
            dirs_to_scan.append(d)

    registered_names: set[str] = set()

    for scan_dir in dirs_to_scan:
        for fname in sorted(os.listdir(scan_dir)):
            if fname in disabled_set:
                continue
            if not (fname.endswith(".py") or fname.endswith(".FCMacro")):
                continue

            fpath = os.path.join(scan_dir, fname)
            if not os.path.isfile(fpath):
                continue

            vr = validate_file(fpath)
            if not vr.valid:
                continue

            try:
                mod = _import_module(fpath)
            except Exception:
                continue

            for func_info in vr.functions:
                tool_name = f"{vr.prefix}{func_info.name}"
                if tool_name in registered_names:
                    continue  # primary dir takes precedence

                func = getattr(mod, func_info.name, None)
                if func is None or not callable(func):
                    continue

                params = [
                    ToolParam(
                        name=fp.name,
                        type=_TYPE_MAP[fp.type_name],
                        description=fp.description or f"Parameter: {fp.name}",
                        required=fp.required,
                        default=fp.default,
                        items=_ITEMS_MAP.get(fp.type_name),
                    )
                    for fp in func_info.params
                ]

                tool_defs.append(ToolDefinition(
                    name=tool_name,
                    description=func_info.description,
                    parameters=params,
                    handler=_make_handler(func),
                    category="user",
                ))
                registered_names.add(tool_name)

    return tool_defs


def _import_module(path: str):
    """Import a Python file as a module.

    Uses SourceFileLoader directly to handle non-standard extensions like .FCMacro.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    loader = importlib.util.spec_from_file_location(
        f"user_tool_{name}",
        path,
        submodule_search_locations=[],
        loader=importlib.machinery.SourceFileLoader(f"user_tool_{name}", path),
    )
    mod = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(mod)
    return mod


def _make_handler(func):
    """Wrap a user function into a ToolResult-returning handler."""
    def handler(**kwargs) -> ToolResult:
        try:
            result = func(**kwargs)
            if isinstance(result, dict):
                return ToolResult(
                    success=True,
                    output=str(result.get("output", "")),
                    data=result.get("data", {}),
                )
            return ToolResult(success=True, output=str(result))
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
    return handler


def _annotation_name(annotation) -> str:
    """Return a canonical type name for a parameter annotation.

    Handles the plain scalar forms (``float``, ``module.Thing``) plus the
    single-argument generic ``list[str]``, which is written back out in the
    same ``list[x]`` spelling used as the _TYPE_MAP key.
    """
    if annotation is None:
        return ""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        container = _annotation_name(annotation.value)
        item = _annotation_name(annotation.slice)
        if container and item:
            return f"{container}[{item}]"
        return container
    return ""


def _parse_docstring(node: ast.FunctionDef) -> tuple[str, dict]:
    """Split a function docstring into a description and per-parameter docs.

    Recognises the Google-style ``Args:`` block used throughout this codebase.
    The description keeps every line before ``Args:`` (not just the summary),
    because a tool's description is the model's only account of what the tool
    does and when to reach for it. ``Returns:``/``Raises:`` sections are
    dropped — they describe the handler's return value, which the model sees
    directly in the tool result.
    """
    raw = ast.get_docstring(node)
    if not raw:
        return "", {}

    lines = textwrap.dedent(raw).strip().splitlines()
    section = "description"
    description_lines: list[str] = []
    param_docs: dict[str, str] = {}
    current_param = ""

    for line in lines:
        stripped = line.strip()
        heading = stripped.rstrip(":").lower()
        if stripped.endswith(":") and heading in ("args", "arguments", "parameters"):
            section = "args"
            current_param = ""
            continue
        if stripped.endswith(":") and heading in (
            "returns", "return", "raises", "yields", "examples", "example", "note", "notes"
        ):
            section = "other"
            current_param = ""
            continue

        if section == "description":
            description_lines.append(stripped)
        elif section == "args":
            name, sep, text = stripped.partition(":")
            # "name (type): text" is also valid Google style.
            name = name.split("(")[0].strip()
            if sep and name.isidentifier():
                current_param = name
                param_docs[current_param] = text.strip()
            elif current_param and stripped:
                # Continuation of the previous parameter's description.
                param_docs[current_param] = (
                    f"{param_docs[current_param]} {stripped}".strip()
                )

    description = "\n".join(description_lines).strip()
    return description, param_docs


def _extract_tool_prefix(tree: ast.Module) -> str:
    """Return the module's __tool_prefix__ value, or the default."""
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == TOOL_PREFIX_ATTR:
                value = _extract_constant(node.value)
                if isinstance(value, str):
                    return value
    return DEFAULT_TOOL_PREFIX


def _extract_constant(node: ast.expr) -> Any:
    """Extract a constant value from an AST node."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _extract_constant(node.operand)
        if val is not None:
            return -val
    return None
