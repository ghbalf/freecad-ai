# User Extension Tools — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users register their own Python functions as first-class tools accessible by the LLM, skills, and MCP server.

**Architecture:** New `UserToolRegistry` in `freecad_ai/extensions/user_tools.py` discovers `.py`/`.FCMacro` files from `~/.config/FreeCAD/FreeCADAI/tools/`, validates them with `ast`, imports and introspects functions via `inspect`, and wraps each as a `ToolDefinition` registered into the main `ToolRegistry`. Settings GUI gets a new "User Tools" group matching the MCP Servers pattern.

**Tech Stack:** Python stdlib (`ast`, `inspect`, `importlib.util`), existing `ToolRegistry`/`ToolDefinition`/`ToolParam`/`ToolResult` from `freecad_ai/tools/registry.py`, PySide2 for GUI.

**Spec:** `docs/specs/2026-03-12-user-tools-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `freecad_ai/extensions/user_tools.py` | Discovery, ast validation, import, introspection, ToolDefinition creation |
| Create | `tests/unit/test_user_tools.py` | Unit tests for all user_tools functionality |
| Modify | `freecad_ai/config.py:11-14,60-73,88-91` | Add `USER_TOOLS_DIR`, new config fields, ensure dir creation |
| Modify | `freecad_ai/tools/setup.py:11-29` | Integrate user tools into `create_default_registry()` |
| Modify | `freecad_ai/ui/settings_dialog.py:176-197` | Add User Tools group to Settings dialog |

---

## Chunk 1: Core Module + Config

### Task 1: Add config fields and USER_TOOLS_DIR

**Files:**
- Modify: `freecad_ai/config.py:11-14` (add USER_TOOLS_DIR)
- Modify: `freecad_ai/config.py:60-73` (add fields to AppConfig)
- Modify: `freecad_ai/config.py:88-91` (add to _ensure_dirs)
- Test: `tests/unit/test_user_tools.py`

- [ ] **Step 1: Write failing test for config fields**

```python
# tests/unit/test_user_tools.py
"""Tests for user extension tools."""

import os
import pytest

import freecad_ai.config as config_mod


class TestUserToolsConfig:
    def test_user_tools_dir_exists(self):
        """USER_TOOLS_DIR is defined in config module."""
        assert hasattr(config_mod, "USER_TOOLS_DIR")
        assert "tools" in config_mod.USER_TOOLS_DIR

    def test_config_has_user_tools_fields(self):
        """AppConfig has user tool config fields."""
        cfg = config_mod.AppConfig()
        assert cfg.user_tools_disabled == []
        assert cfg.scan_freecad_macros is False

    def test_config_roundtrip(self, tmp_config_dir):
        """User tools config survives save/load."""
        cfg = config_mod.AppConfig()
        cfg.user_tools_disabled = ["bad_tool.py"]
        cfg.scan_freecad_macros = True
        config_mod.save_config(cfg)
        loaded = config_mod.load_config()
        assert loaded.user_tools_disabled == ["bad_tool.py"]
        assert loaded.scan_freecad_macros is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestUserToolsConfig -v`
Expected: FAIL — `USER_TOOLS_DIR` not defined, `user_tools_disabled` not a field

- [ ] **Step 3: Implement config changes**

In `freecad_ai/config.py`:

Add after line 14 (`SKILLS_DIR`):
```python
USER_TOOLS_DIR = os.path.join(CONFIG_DIR, "tools")
```

Add to `AppConfig` dataclass after `mcp_servers` (line 72):
```python
    user_tools_disabled: list = field(default_factory=list)
    scan_freecad_macros: bool = False
```

Add to `_ensure_dirs()` (line 90):
```python
    for d in (CONFIG_DIR, CONVERSATIONS_DIR, SKILLS_DIR, USER_TOOLS_DIR):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestUserToolsConfig -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py tests/unit/test_user_tools.py
git commit -m "feat: add user tools config fields and USER_TOOLS_DIR"
```

---

### Task 2: AST validation module

**Files:**
- Create: `freecad_ai/extensions/user_tools.py`
- Test: `tests/unit/test_user_tools.py`

- [ ] **Step 1: Write failing tests for validation**

```python
# Append to tests/unit/test_user_tools.py

class TestAstValidation:
    def test_valid_file(self, tmp_path):
        """Valid file with typed function passes validation."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "good.py"
        f.write_text(
            'def my_tool(diameter: float, count: int = 8) -> str:\n'
            '    """Create something."""\n'
            '    return "done"\n'
        )
        result = validate_file(str(f))
        assert result.valid
        assert len(result.functions) == 1
        assert result.functions[0].name == "my_tool"
        assert len(result.functions[0].params) == 2
        assert not result.warnings

    def test_syntax_error(self, tmp_path):
        """File with syntax error fails validation."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "bad.py"
        f.write_text("def oops(:\n")
        result = validate_file(str(f))
        assert not result.valid
        assert "syntax" in result.error.lower()

    def test_no_typed_functions(self, tmp_path):
        """File with no typed functions fails validation."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "untyped.py"
        f.write_text("def foo(x, y):\n    return x + y\n")
        result = validate_file(str(f))
        assert not result.valid
        assert "no valid tool functions" in result.error.lower()

    def test_private_functions_skipped(self, tmp_path):
        """Functions starting with _ are skipped."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "private.py"
        f.write_text(
            'def _helper(x: int) -> int:\n    return x\n'
            'def public_tool(x: int) -> str:\n'
            '    """Do stuff."""\n'
            '    return str(x)\n'
        )
        result = validate_file(str(f))
        assert result.valid
        assert len(result.functions) == 1
        assert result.functions[0].name == "public_tool"

    def test_unsupported_param_type(self, tmp_path):
        """Unsupported param types produce warnings."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "weird.py"
        f.write_text(
            'def my_tool(data: dict) -> str:\n'
            '    """Process data."""\n'
            '    return "ok"\n'
        )
        result = validate_file(str(f))
        assert not result.valid or len(result.warnings) > 0

    def test_missing_docstring_warns(self, tmp_path):
        """Missing docstring produces a warning."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "nodoc.py"
        f.write_text("def my_tool(x: float) -> str:\n    return 'ok'\n")
        result = validate_file(str(f))
        assert result.valid
        assert any("docstring" in w.lower() for w in result.warnings)

    def test_multiple_functions(self, tmp_path):
        """File with multiple valid functions extracts all."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "multi.py"
        f.write_text(
            'def tool_a(x: float) -> str:\n    """A."""\n    return "a"\n\n'
            'def tool_b(y: int, z: str = "hi") -> str:\n    """B."""\n    return "b"\n'
        )
        result = validate_file(str(f))
        assert result.valid
        assert len(result.functions) == 2

    def test_fcmacro_extension(self, tmp_path):
        """FCMacro files are accepted."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "macro.FCMacro"
        f.write_text(
            'def make_thing(size: float = 10.0) -> str:\n'
            '    """Make a thing."""\n'
            '    return "made"\n'
        )
        result = validate_file(str(f))
        assert result.valid

    def test_default_values_extracted(self, tmp_path):
        """Default values are correctly extracted."""
        from freecad_ai.extensions.user_tools import validate_file

        f = tmp_path / "defaults.py"
        f.write_text(
            'def my_tool(x: float, y: int = 5, name: str = "hi", flag: bool = True) -> str:\n'
            '    """Tool."""\n'
            '    return "ok"\n'
        )
        result = validate_file(str(f))
        func = result.functions[0]
        assert func.params[0].required is True   # x
        assert func.params[1].required is False   # y
        assert func.params[1].default == 5
        assert func.params[2].default == "hi"
        assert func.params[3].default is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestAstValidation -v`
Expected: FAIL — `user_tools` module doesn't exist

- [ ] **Step 3: Implement validate_file**

Create `freecad_ai/extensions/user_tools.py`:

```python
"""User extension tools — discover, validate, and register user-authored tool functions.

Scans .py and .FCMacro files from USER_TOOLS_DIR, validates with ast,
imports and introspects functions, and wraps each as a ToolDefinition.
"""

import ast
import importlib.util
import inspect
import os
from dataclasses import dataclass, field
from typing import Any

from ..config import USER_TOOLS_DIR
from ..tools.registry import ToolDefinition, ToolParam, ToolResult


# Python type hint -> JSON Schema type
_TYPE_MAP = {
    "float": "number",
    "int": "integer",
    "str": "string",
    "bool": "boolean",
}

SUPPORTED_TYPES = set(_TYPE_MAP.keys())


@dataclass
class FuncParam:
    """A parameter extracted from a function signature via AST."""
    name: str
    type_name: str
    required: bool = True
    default: Any = None


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


def validate_file(path: str) -> ValidationResult:
    """Validate a Python file for user tool functions using AST (no execution).

    Checks:
    - Valid Python syntax
    - At least one public function with supported type hints
    - Warns on missing docstrings, unsupported param types
    """
    try:
        with open(path, "r") as f:
            source = f.read()
    except OSError as e:
        return ValidationResult(valid=False, error=f"Cannot read file: {e}")

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return ValidationResult(valid=False, error=f"Syntax error: {e}")

    functions: list[FuncInfo] = []
    warnings: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_"):
            continue

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
            annotation = arg.annotation
            if annotation is None:
                continue  # skip untyped params

            type_name = ""
            if isinstance(annotation, ast.Name):
                type_name = annotation.id
            elif isinstance(annotation, ast.Attribute):
                type_name = annotation.attr

            if type_name not in SUPPORTED_TYPES:
                has_unsupported = True
                warnings.append(
                    f"{node.name}(): param '{arg.arg}' has unsupported type '{type_name}'"
                )
                continue

            # Check for default value
            default_idx = i - first_default_idx
            has_default = default_idx >= 0 and default_idx < num_defaults
            default_val = None
            if has_default:
                default_node = args.defaults[default_idx]
                default_val = _extract_constant(default_node)

            params.append(FuncParam(
                name=arg.arg,
                type_name=type_name,
                required=not has_default,
                default=default_val,
            ))

        if not params:
            continue  # no typed params — skip this function

        if has_unsupported and not params:
            continue  # only unsupported types

        # Extract docstring
        description = ""
        if (node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, (ast.Constant, ast.Str))):
            raw = (node.body[0].value.value
                   if isinstance(node.body[0].value, ast.Constant)
                   else node.body[0].value.s)
            description = raw.strip().split("\n")[0]

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
        )

    return ValidationResult(valid=True, functions=functions, warnings=warnings)


def _extract_constant(node: ast.expr) -> Any:
    """Extract a constant value from an AST node."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _extract_constant(node.operand)
        if val is not None:
            return -val
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestAstValidation -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/extensions/user_tools.py tests/unit/test_user_tools.py
git commit -m "feat: add user tools AST validation"
```

---

### Task 3: Loading and registration

**Files:**
- Modify: `freecad_ai/extensions/user_tools.py`
- Test: `tests/unit/test_user_tools.py`

- [ ] **Step 1: Write failing tests for loading and registration**

```python
# Append to tests/unit/test_user_tools.py

from freecad_ai.tools.registry import ToolRegistry


class TestLoadAndRegister:
    def _write_tool(self, tmp_path, name="my_tool.py", content=None):
        if content is None:
            content = (
                'def greet(name: str, times: int = 1) -> str:\n'
                '    """Greet someone."""\n'
                '    return f"Hello {name}! " * times\n'
            )
        f = tmp_path / name
        f.write_text(content)
        return f

    def test_load_module(self, tmp_path):
        """load_user_tools loads functions from a file."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tools = load_user_tools(str(tmp_path))
        assert len(tools) == 1
        assert tools[0].name == "user_greet"

    def test_tool_execution(self, tmp_path):
        """Loaded tool can be executed and returns ToolResult."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tools = load_user_tools(str(tmp_path))
        result = tools[0].handler(name="World", times=2)
        assert result.success
        assert "Hello World" in result.output

    def test_tool_exception_handling(self, tmp_path):
        """Tool that raises returns error ToolResult."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, content=(
            'def boom(x: int) -> str:\n'
            '    """Explode."""\n'
            '    raise ValueError("kaboom")\n'
        ))
        tools = load_user_tools(str(tmp_path))
        result = tools[0].handler(x=1)
        assert not result.success
        assert "kaboom" in result.error

    def test_dict_return(self, tmp_path):
        """Tool returning dict maps to ToolResult fields."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, content=(
            'def info(x: int) -> dict:\n'
            '    """Get info."""\n'
            '    return {"output": "done", "data": {"val": x}}\n'
        ))
        tools = load_user_tools(str(tmp_path))
        result = tools[0].handler(x=42)
        assert result.success
        assert result.output == "done"
        assert result.data == {"val": 42}

    def test_register_into_registry(self, tmp_path):
        """User tools register into ToolRegistry."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tools = load_user_tools(str(tmp_path))
        registry = ToolRegistry()
        for t in tools:
            registry.register(t)
        assert registry.get("user_greet") is not None
        result = registry.execute("user_greet", {"name": "Test"})
        assert result.success

    def test_disabled_files_skipped(self, tmp_path):
        """Disabled files are not loaded."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, "skip_me.py")
        tools = load_user_tools(str(tmp_path), disabled=["skip_me.py"])
        assert len(tools) == 0

    def test_params_schema(self, tmp_path):
        """Loaded tool has correct ToolParam schema."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path)
        tool = load_user_tools(str(tmp_path))[0]
        params = {p.name: p for p in tool.parameters}
        assert params["name"].type == "string"
        assert params["name"].required is True
        assert params["times"].type == "integer"
        assert params["times"].required is False
        assert params["times"].default == 1

    def test_multiple_files(self, tmp_path):
        """Multiple files in directory all get loaded."""
        from freecad_ai.extensions.user_tools import load_user_tools

        self._write_tool(tmp_path, "tool_a.py",
            'def alpha(x: float) -> str:\n    """A."""\n    return "a"\n')
        self._write_tool(tmp_path, "tool_b.py",
            'def beta(y: int) -> str:\n    """B."""\n    return "b"\n')
        tools = load_user_tools(str(tmp_path))
        names = {t.name for t in tools}
        assert names == {"user_alpha", "user_beta"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestLoadAndRegister -v`
Expected: FAIL — `load_user_tools` not defined

- [ ] **Step 3: Implement load_user_tools**

Append to `freecad_ai/extensions/user_tools.py`:

```python
def load_user_tools(
    tools_dir: str,
    disabled: list[str] | None = None,
) -> list[ToolDefinition]:
    """Discover, validate, import, and wrap user tool functions.

    Args:
        tools_dir: Directory to scan for .py/.FCMacro files.
        disabled: List of filenames to skip.

    Returns:
        List of ToolDefinition ready for registration.
    """
    disabled = set(disabled or [])
    tool_defs: list[ToolDefinition] = []

    if not os.path.isdir(tools_dir):
        return tool_defs

    for fname in sorted(os.listdir(tools_dir)):
        if fname in disabled:
            continue
        if not (fname.endswith(".py") or fname.endswith(".FCMacro")):
            continue

        fpath = os.path.join(tools_dir, fname)
        if not os.path.isfile(fpath):
            continue

        # Validate with AST first
        vr = validate_file(fpath)
        if not vr.valid:
            continue

        # Import the module
        try:
            mod = _import_module(fpath)
        except Exception:
            continue

        # Wrap each validated function
        for func_info in vr.functions:
            func = getattr(mod, func_info.name, None)
            if func is None or not callable(func):
                continue

            params = [
                ToolParam(
                    name=fp.name,
                    type=_TYPE_MAP[fp.type_name],
                    description=f"Parameter: {fp.name}",
                    required=fp.required,
                    default=fp.default,
                )
                for fp in func_info.params
            ]

            handler = _make_handler(func)

            tool_defs.append(ToolDefinition(
                name=f"user_{func_info.name}",
                description=func_info.description,
                parameters=params,
                handler=handler,
                category="user",
            ))

    return tool_defs


def _import_module(path: str):
    """Import a Python file as a module."""
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(f"user_tool_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestLoadAndRegister -v`
Expected: PASS

- [ ] **Step 5: Run ALL tests to check nothing is broken**

Run: `.venv/bin/pytest tests/unit/ -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/extensions/user_tools.py tests/unit/test_user_tools.py
git commit -m "feat: add user tools loading and ToolDefinition wrapping"
```

---

## Chunk 2: Integration + GUI

### Task 4: Integrate into create_default_registry

**Files:**
- Modify: `freecad_ai/tools/setup.py:11-29`
- Test: `tests/unit/test_user_tools.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/unit/test_user_tools.py

class TestSetupIntegration:
    def test_create_default_registry_includes_user_tools(self, tmp_path, monkeypatch):
        """create_default_registry loads user tools."""
        import freecad_ai.config as config_mod
        monkeypatch.setattr(config_mod, "USER_TOOLS_DIR", str(tmp_path))

        (tmp_path / "my_tool.py").write_text(
            'def hello(name: str) -> str:\n'
            '    """Say hello."""\n'
            '    return f"Hi {name}"\n'
        )

        from freecad_ai.tools.setup import create_default_registry
        registry = create_default_registry(include_mcp=False)
        assert registry.get("user_hello") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestSetupIntegration -v`
Expected: FAIL — user_hello not registered

- [ ] **Step 3: Implement integration in setup.py**

Modify `freecad_ai/tools/setup.py` — add user tools loading after built-in tools, before MCP:

```python
"""Default tool registry factory.

Creates a ToolRegistry pre-loaded with all built-in FreeCAD tools,
user extension tools, and optionally MCP tools from connected servers.
"""

from .registry import ToolRegistry
from .freecad_tools import ALL_TOOLS


def create_default_registry(include_mcp: bool = True) -> ToolRegistry:
    """Create a ToolRegistry with all built-in FreeCAD tools registered.

    Also loads user extension tools from USER_TOOLS_DIR, and optionally
    integrates MCP tools from connected servers.
    """
    registry = ToolRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    # Load user extension tools
    try:
        from ..config import USER_TOOLS_DIR, get_config
        from ..extensions.user_tools import load_user_tools
        cfg = get_config()
        user_tools = load_user_tools(
            USER_TOOLS_DIR,
            disabled=cfg.user_tools_disabled,
        )
        for tool in user_tools:
            registry.register(tool)
    except Exception:
        pass  # User tools not available

    if include_mcp:
        try:
            from ..mcp.manager import get_mcp_manager
            manager = get_mcp_manager()
            manager.register_tools_into(registry)
        except Exception:
            pass  # MCP not available or no servers connected

    return registry
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestSetupIntegration -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest tests/unit/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/setup.py tests/unit/test_user_tools.py
git commit -m "feat: integrate user tools into default registry"
```

---

### Task 5: Settings GUI — User Tools group

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py`

- [ ] **Step 1: Read settings_dialog.py for exact insertion point**

The MCP Servers group is at lines 176-197. The User Tools group goes after it, before the "Test Connection" button. Follow the same QGroupBox + QListWidget + buttons pattern.

- [ ] **Step 2: Add User Tools group to _build_ui()**

In `freecad_ai/ui/settings_dialog.py`, add after the MCP Servers group and before "Test Connection":

```python
        # --- User Tools group ---
        user_tools_group = QGroupBox(translate("SettingsDialog", "User Tools"))
        user_tools_layout = QVBoxLayout()

        self.user_tools_list = QListWidget()
        self.user_tools_list.setMaximumHeight(100)
        user_tools_layout.addWidget(self.user_tools_list)

        ut_btn_layout = QHBoxLayout()
        ut_add_btn = QPushButton(translate("SettingsDialog", "Add..."))
        ut_add_btn.clicked.connect(self._add_user_tool)
        ut_btn_layout.addWidget(ut_add_btn)

        ut_remove_btn = QPushButton(translate("SettingsDialog", "Remove"))
        ut_remove_btn.clicked.connect(self._remove_user_tool)
        ut_btn_layout.addWidget(ut_remove_btn)

        ut_reload_btn = QPushButton(translate("SettingsDialog", "Reload"))
        ut_reload_btn.clicked.connect(self._reload_user_tools)
        ut_btn_layout.addWidget(ut_reload_btn)

        ut_btn_layout.addStretch()
        user_tools_layout.addLayout(ut_btn_layout)

        self.scan_macros_cb = QCheckBox(
            translate("SettingsDialog", "Also scan FreeCAD macro directory")
        )
        user_tools_layout.addWidget(self.scan_macros_cb)

        user_tools_group.setLayout(user_tools_layout)
        layout.addWidget(user_tools_group)
```

- [ ] **Step 3: Add _load_user_tools_list() helper**

```python
    def _load_user_tools_list(self):
        """Scan user tools directory and populate the list widget."""
        from ..config import USER_TOOLS_DIR
        from ..extensions.user_tools import validate_file

        self.user_tools_list.clear()
        self._user_tool_files = []

        if not os.path.isdir(USER_TOOLS_DIR):
            return

        disabled = set(self._cfg.user_tools_disabled)

        for fname in sorted(os.listdir(USER_TOOLS_DIR)):
            if not (fname.endswith(".py") or fname.endswith(".FCMacro")):
                continue
            fpath = os.path.join(USER_TOOLS_DIR, fname)
            if not os.path.isfile(fpath):
                continue

            vr = validate_file(fpath)
            self._user_tool_files.append(fname)

            if not vr.valid:
                label = f"✗ {fname} — {vr.error}"
            elif vr.warnings:
                func_names = ", ".join(f.name for f in vr.functions)
                label = f"⚠ {fname} ({func_names}) — {'; '.join(vr.warnings)}"
            else:
                func_names = ", ".join(f.name for f in vr.functions)
                label = f"✓ {fname} ({func_names})"

            if fname in disabled:
                label = f"(disabled) {label}"

            item = QListWidgetItem(label)
            self.user_tools_list.addItem(item)
```

- [ ] **Step 4: Add _add_user_tool(), _remove_user_tool(), _reload_user_tools()**

```python
    def _add_user_tool(self):
        """Open file picker and copy selected file to user tools dir."""
        from ..config import USER_TOOLS_DIR

        path, _ = QFileDialog.getOpenFileName(
            self,
            translate("SettingsDialog", "Select Tool File"),
            "",
            translate("SettingsDialog", "Python Files (*.py *.FCMacro)"),
        )
        if not path:
            return

        import shutil
        os.makedirs(USER_TOOLS_DIR, exist_ok=True)
        dest = os.path.join(USER_TOOLS_DIR, os.path.basename(path))
        if os.path.exists(dest):
            QMessageBox.warning(
                self,
                translate("SettingsDialog", "File Exists"),
                translate("SettingsDialog", f"'{os.path.basename(path)}' already exists in tools directory."),
            )
            return
        shutil.copy2(path, dest)
        self._reload_user_tools()

    def _remove_user_tool(self):
        """Remove selected tool file from user tools dir."""
        from ..config import USER_TOOLS_DIR

        row = self.user_tools_list.currentRow()
        if row < 0 or row >= len(self._user_tool_files):
            return

        fname = self._user_tool_files[row]
        fpath = os.path.join(USER_TOOLS_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)
        self._reload_user_tools()

    def _reload_user_tools(self):
        """Re-scan and refresh the user tools list."""
        self._load_user_tools_list()
```

- [ ] **Step 5: Wire into _load_from_config() and _save()**

In `_load_from_config()`, add:
```python
        self.scan_macros_cb.setChecked(cfg.scan_freecad_macros)
        self._cfg = cfg
        self._load_user_tools_list()
```

In `_save()`, add:
```python
        cfg.scan_freecad_macros = self.scan_macros_cb.isChecked()
```

- [ ] **Step 6: Test manually in FreeCAD**

Run: `QT_QPA_PLATFORM=xcb ~/bin/freecad`
Open Settings → verify User Tools group appears with Add/Remove/Reload buttons and scan checkbox.

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py
git commit -m "feat: add User Tools group to settings dialog"
```

---

### Task 6: Scan FreeCAD macros option

**Files:**
- Modify: `freecad_ai/extensions/user_tools.py`
- Modify: `freecad_ai/tools/setup.py`
- Test: `tests/unit/test_user_tools.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/unit/test_user_tools.py

class TestScanMacros:
    def test_extra_dirs_scanned(self, tmp_path):
        """load_user_tools accepts extra_dirs to scan."""
        from freecad_ai.extensions.user_tools import load_user_tools

        macros_dir = tmp_path / "macros"
        macros_dir.mkdir()
        (macros_dir / "macro_tool.FCMacro").write_text(
            'def macro_fn(r: float) -> str:\n'
            '    """A macro."""\n'
            '    return "ok"\n'
        )

        tools = load_user_tools(str(tmp_path / "empty"), extra_dirs=[str(macros_dir)])
        assert len(tools) == 1
        assert tools[0].name == "user_macro_fn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py::TestScanMacros -v`
Expected: FAIL — `extra_dirs` param not supported

- [ ] **Step 3: Add extra_dirs parameter to load_user_tools**

In `freecad_ai/extensions/user_tools.py`, update `load_user_tools` signature:

```python
def load_user_tools(
    tools_dir: str,
    disabled: list[str] | None = None,
    extra_dirs: list[str] | None = None,
) -> list[ToolDefinition]:
```

After scanning `tools_dir`, add:
```python
    for extra_dir in (extra_dirs or []):
        if os.path.isdir(extra_dir):
            for fname in sorted(os.listdir(extra_dir)):
                if fname in disabled:
                    continue
                if not (fname.endswith(".py") or fname.endswith(".FCMacro")):
                    continue
                fpath = os.path.join(extra_dir, fname)
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
                    func = getattr(mod, func_info.name, None)
                    if func is None or not callable(func):
                        continue
                    # Skip if name already registered (primary dir takes precedence)
                    if any(t.name == f"user_{func_info.name}" for t in tool_defs):
                        continue
                    params = [
                        ToolParam(
                            name=fp.name,
                            type=_TYPE_MAP[fp.type_name],
                            description=f"Parameter: {fp.name}",
                            required=fp.required,
                            default=fp.default,
                        )
                        for fp in func_info.params
                    ]
                    tool_defs.append(ToolDefinition(
                        name=f"user_{func_info.name}",
                        description=func_info.description,
                        parameters=params,
                        handler=_make_handler(func),
                        category="user",
                    ))
```

Update `setup.py` to pass FreeCAD macro dir when `scan_freecad_macros` is enabled:
```python
        extra_dirs = []
        if cfg.scan_freecad_macros:
            fc_macro_dir = os.path.join(os.path.expanduser("~"), ".config", "FreeCAD", "Macro")
            if os.path.isdir(fc_macro_dir):
                extra_dirs.append(fc_macro_dir)
        user_tools = load_user_tools(
            USER_TOOLS_DIR,
            disabled=cfg.user_tools_disabled,
            extra_dirs=extra_dirs,
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_user_tools.py -v`
Expected: All pass

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/extensions/user_tools.py freecad_ai/tools/setup.py tests/unit/test_user_tools.py
git commit -m "feat: support scanning FreeCAD macro directory for user tools"
```

---

### Task 7: End-to-end manual test

- [ ] **Step 1: Create a sample user tool file**

```bash
mkdir -p ~/.config/FreeCAD/FreeCADAI/tools
cat > ~/.config/FreeCAD/FreeCADAI/tools/bolt_circle.py << 'EOF'
import math


def bolt_circle(diameter: float, count: int = 8, bolt_size: float = 6.5) -> str:
    """Create a bolt hole circle pattern on the XY plane."""
    import Part
    import FreeCAD as App

    doc = App.ActiveDocument
    if doc is None:
        return "Error: no active document"

    radius = diameter / 2
    bolt_r = bolt_size / 2
    shapes = []
    for i in range(count):
        angle = math.radians(i * 360.0 / count)
        cx = radius * math.cos(angle)
        cy = radius * math.sin(angle)
        hole = Part.makeCylinder(bolt_r, 100, App.Vector(cx, cy, -50))
        shapes.append(hole)

    compound = shapes[0]
    for s in shapes[1:]:
        compound = compound.fuse(s)

    obj = doc.addObject("Part::Feature", "BoltCircle")
    obj.Shape = compound
    doc.recompute()
    return f"Created {count} x {bolt_size}mm bolt holes on {diameter}mm PCD"
EOF
```

- [ ] **Step 2: Launch FreeCAD and verify**

Run: `QT_QPA_PLATFORM=xcb ~/bin/freecad`
1. Open Settings → verify bolt_circle.py appears in User Tools list with green status
2. Open a new document, switch to Act mode
3. Ask: "create a bolt circle with diameter 150mm and 6 holes"
4. Verify the LLM calls `user_bolt_circle` tool

- [ ] **Step 3: Final commit if any fixes needed**
