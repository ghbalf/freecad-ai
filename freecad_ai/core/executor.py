"""Code execution engine for FreeCAD AI.

Extracts Python code from LLM responses and executes them in FreeCAD's
interpreter with the appropriate modules in scope.
"""

import io
import re
import sys
import traceback
from dataclasses import dataclass


@dataclass
class ExecutionResult:
    success: bool
    stdout: str
    stderr: str
    code: str


# Regex to extract ```python ... ``` code blocks
CODE_BLOCK_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


def extract_code_blocks(text: str) -> list[str]:
    """Extract all Python code blocks from markdown-formatted text."""
    return CODE_BLOCK_RE.findall(text)


def execute_code(code: str) -> ExecutionResult:
    """Execute Python code in FreeCAD's context.

    The code runs with FreeCAD modules available in its namespace.
    stdout/stderr are captured and returned along with success status.
    """
    # Build execution namespace with FreeCAD modules
    namespace = _build_namespace()

    # Capture stdout/stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_err

    success = True
    try:
        exec(code, namespace)
        # Recompute document after successful execution
        _recompute(namespace)
    except Exception:
        success = False
        traceback.print_exc(file=captured_err)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return ExecutionResult(
        success=success,
        stdout=captured_out.getvalue(),
        stderr=captured_err.getvalue(),
        code=code,
    )


def _build_namespace() -> dict:
    """Build a namespace dict with FreeCAD modules for code execution."""
    ns = {"__builtins__": __builtins__}

    # Try to import each FreeCAD module
    modules = [
        ("FreeCAD", "App"),
        ("FreeCADGui", "Gui"),
        ("Part", None),
        ("PartDesign", None),
        ("Sketcher", None),
        ("Draft", None),
        ("Mesh", None),
        ("BOPTools", None),
    ]
    for mod_name, alias in modules:
        try:
            mod = __import__(mod_name)
            ns[mod_name] = mod
            if alias:
                ns[alias] = mod
        except ImportError:
            pass

    # Convenience: math module is often useful
    import math
    ns["math"] = math

    return ns


def _recompute(namespace: dict):
    """Recompute the active document if available."""
    app = namespace.get("App") or namespace.get("FreeCAD")
    if app and hasattr(app, "ActiveDocument") and app.ActiveDocument:
        app.ActiveDocument.recompute()
