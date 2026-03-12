# User Extension Tools — Design Spec

**Date**: 2026-03-12
**Status**: Approved

## Overview

A module that discovers Python files (`.py`, `.FCMacro`) from a configurable directory, introspects functions using type hints and docstrings, and registers them as first-class tools — available to the LLM, skills, and MCP server.

## Function Convention

```python
def bolt_circle(diameter: float, count: int = 8, bolt_size: float = 6.5) -> str:
    """Create a bolt circle pattern on the selected face.

    Distributes bolt holes evenly around a circle of the given diameter.
    """
    import Part
    import FreeCAD as App
    # ... geometry code ...
    return f"Created {count} bolt holes on {diameter}mm circle"
```

- **Name**: function name (e.g. `bolt_circle`)
- **Description**: first line of docstring
- **Parameters**: from type hints. Supported types: `float`, `int`, `str`, `bool`. Defaults make params optional.
- **Return**: `str` → `ToolResult(success=True, output=str)`. `dict` with `output`/`data` keys → mapped to ToolResult. Exception → `ToolResult(success=False, error=str(e))`.
- Functions with no type hints or starting with `_` are skipped.

## Discovery & Validation

### Directories
- **Primary**: `~/.config/FreeCAD/FreeCADAI/tools/`
- **Optional**: FreeCAD macro directory (toggled via `scan_freecad_macros` setting)

### Validation (moderate, via `ast` parsing — no execution)
- Valid Python syntax
- At least one public function with type hints
- All parameter types in supported set (`float`, `int`, `str`, `bool`)
- Warnings (non-blocking): missing docstring, no return type hint, bare `except`

### Loading
- `importlib.util.spec_from_file_location` + `loader.exec_module`
- Introspect with `inspect.signature` to confirm params match ast analysis
- Each valid function → `ToolDefinition` with handler wrapper

## Handler Wrapper

The wrapper around each user function:
1. Wraps execution in FreeCAD undo transaction
2. Converts return value to `ToolResult`:
   - `str` → `ToolResult(success=True, output=str)`
   - `dict` → `ToolResult(success=True, output=dict.get("output",""), data=dict.get("data"))`
   - Exception → `ToolResult(success=False, error=str(e))`
3. Prefixes tool name with `user_` namespace to avoid collisions with built-in tools

## Registration Flow

```
.py/.FCMacro file
  → ast parse + validate (no execution)
  → importlib load module
  → inspect functions (type hints, docstrings)
  → create ToolDefinition per function (handler = wrapper)
  → register into ToolRegistry
  → available to: LLM tool calls, skills, MCP server
```

## Config

```python
# In AppConfig
user_tools_enabled: list[str] = []      # enabled tool file basenames
user_tools_disabled: list[str] = []     # explicitly disabled
scan_freecad_macros: bool = False        # scan FreeCAD macro dir too
```

## Settings GUI

New "User Tools" group in the existing Settings dialog (matching MCP Servers pattern):
- List showing: tool name, source file, status icon (green=loaded, yellow=warning, red=error)
- Tooltip on warning/error items shows details
- **Add...** button → file picker for `.py`/`.FCMacro`, copies file to tools directory
- **Remove** button → deletes from tools directory
- **Enable/disable** checkbox per file
- **Scan FreeCAD macros** checkbox
- **Reload** button → re-scan and re-validate

## File Structure

```
freecad_ai/extensions/user_tools.py    # Discovery, validation, loading, registration
```

Single new module. Integration points:
- Startup: called after built-in tools registered, before MCP connect
- Settings dialog: new group added to existing dialog
- Config: new fields in AppConfig
