"""Code execution engine for FreeCAD AI.

Extracts Python code from LLM responses and executes them in FreeCAD's
interpreter with the appropriate modules in scope.

Uses FreeCAD undo transactions so failed operations can be rolled back.
"""

import io
import re
import signal
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


def execute_code(code: str, timeout: int = 30) -> ExecutionResult:
    """Execute Python code in FreeCAD's context.

    The code runs with FreeCAD modules available in its namespace.
    stdout/stderr are captured and returned along with success status.
    Execution is wrapped in an undo transaction so failures can be rolled back.
    """
    # Pre-execution validation
    warnings = _validate_code(code)
    if warnings:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr="Pre-execution validation failed:\n" + "\n".join(warnings),
            code=code,
        )

    # Build execution namespace with FreeCAD modules
    namespace = _build_namespace()

    # Capture stdout/stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_err

    doc = _get_active_doc(namespace)
    success = True
    try:
        # Open undo transaction so we can roll back on failure
        if doc:
            doc.openTransaction("AI Code Execution")

        # Set an alarm timeout to catch infinite loops / hangs
        _old_handler = None
        try:
            def _timeout_handler(signum, frame):
                raise TimeoutError("Code execution timed out after {} seconds".format(timeout))
            _old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout)
        except (OSError, AttributeError):
            # SIGALRM not available on all platforms
            pass

        try:
            exec(code, namespace)
        finally:
            # Cancel the alarm
            try:
                signal.alarm(0)
                if _old_handler is not None:
                    signal.signal(signal.SIGALRM, _old_handler)
            except (OSError, AttributeError):
                pass

        # Recompute and commit
        _recompute(namespace)
        if doc:
            doc.commitTransaction()
    except Exception:
        success = False
        traceback.print_exc(file=captured_err)
        # Roll back the failed transaction
        if doc:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception:
                pass
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return ExecutionResult(
        success=success,
        stdout=captured_out.getvalue(),
        stderr=captured_err.getvalue(),
        code=code,
    )


def _validate_code(code: str) -> list[str]:
    """Check code for patterns known to crash FreeCAD.

    Returns a list of warning strings. Empty list means no issues found.
    """
    warnings = []

    # Dangerous imports / operations that could crash or damage
    dangerous_patterns = [
        (r"\bos\.system\s*\(", "os.system() calls are not allowed"),
        (r"\bsubprocess\b", "subprocess module is not allowed"),
        (r"\bshutil\.rmtree\b", "shutil.rmtree() is not allowed"),
        (r"\b__import__\s*\(\s*['\"]os['\"]\s*\)", "Dynamic import of os is not allowed"),
    ]
    for pattern, msg in dangerous_patterns:
        if re.search(pattern, code):
            warnings.append(msg)

    return warnings


def _get_active_doc(namespace: dict):
    """Get the active FreeCAD document, if any."""
    app = namespace.get("App") or namespace.get("FreeCAD")
    if app and hasattr(app, "ActiveDocument"):
        return app.ActiveDocument
    return None


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
