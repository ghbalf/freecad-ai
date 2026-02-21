"""Code execution engine for FreeCAD AI.

Extracts Python code from LLM responses and executes them in FreeCAD's
interpreter with the appropriate modules in scope.

Safety layers:
  1. Static validation — block dangerous patterns
  2. Subprocess sandbox — test code in a headless FreeCAD process first
  3. Undo transactions — roll back failed operations
  4. Auto-save — save document before execution so crashes don't lose work
"""

import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
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


def _find_freecad_cmd() -> str:
    """Find the FreeCAD executable for console-mode subprocess runs."""
    # Check common locations
    candidates = [
        os.path.expanduser("~/bin/freecad"),
        "/usr/bin/freecad",
        "/usr/bin/freecadcmd",
        "/usr/local/bin/freecad",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    # Fallback: try PATH
    import shutil
    for name in ("freecadcmd", "freecad"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _sandbox_test(code: str, timeout: int = 15) -> tuple:
    """Test code in a headless FreeCAD subprocess.

    Returns (safe: bool, error_message: str).
    If FreeCAD console is not available, returns (True, "") to skip sandboxing.
    """
    freecad_bin = _find_freecad_cmd()
    if not freecad_bin:
        return True, ""  # Can't sandbox, let it through

    result_file = tempfile.mktemp(suffix=".json")
    script_file = tempfile.mktemp(suffix=".py")

    # Write a test harness that runs the code in a fresh document
    harness = '''import sys, json, traceback
result = {{"ok": False, "error": ""}}
try:
    import FreeCAD as App
    doc = App.newDocument("SandboxTest")
    # --- user code ---
{indented_code}
    # --- end user code ---
    doc.recompute()
    result["ok"] = True
except Exception as e:
    result["error"] = traceback.format_exc()
finally:
    with open({result_path!r}, "w") as f:
        json.dump(result, f)
'''.format(
        indented_code="\n".join("    " + line for line in code.splitlines()),
        result_path=result_file,
    )

    try:
        with open(script_file, "w") as f:
            f.write(harness)

        proc = subprocess.run(
            [freecad_bin, "-c", script_file],
            timeout=timeout,
            capture_output=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )

        if proc.returncode != 0 and proc.returncode > 0:
            # Non-zero but not a signal — Python error
            stderr = proc.stderr.decode(errors="replace")[-500:]
            return False, "Sandbox: code raised an error:\n" + stderr

        if proc.returncode < 0:
            # Killed by signal (e.g. SIGSEGV = -11)
            sig = -proc.returncode
            sig_name = signal.Signals(sig).name if sig in signal.Signals._value2member_map_ else str(sig)
            return False, (
                "Sandbox: code CRASHED FreeCAD (signal {}). "
                "This code is not safe to execute.".format(sig_name)
            )

        # Read result
        if os.path.exists(result_file):
            with open(result_file) as f:
                result = json.load(f)
            if result["ok"]:
                return True, ""
            else:
                return False, "Sandbox: " + result["error"]

        return True, ""  # No result file but process exited OK

    except subprocess.TimeoutExpired:
        return False, "Sandbox: code timed out after {} seconds".format(timeout)
    except Exception as e:
        # Sandbox itself failed — don't block execution
        return True, ""
    finally:
        for f in (script_file, result_file):
            try:
                os.unlink(f)
            except OSError:
                pass


def _auto_save(namespace: dict):
    """Save a recovery copy of the active document before executing code."""
    try:
        app = namespace.get("App") or namespace.get("FreeCAD")
        if not app or not app.ActiveDocument:
            return
        doc = app.ActiveDocument
        if not doc.FileName:
            return  # Unsaved document, nothing to back up
        backup = doc.FileName + ".ai-backup"
        doc.saveAs(backup)
        # Restore the original filename so the user doesn't notice
        doc.FileName = doc.FileName.replace(".ai-backup", "")
    except Exception:
        pass  # Best-effort


def execute_code(code: str, timeout: int = 30, sandbox: bool = True) -> ExecutionResult:
    """Execute Python code in FreeCAD's context.

    The code runs with FreeCAD modules available in its namespace.
    stdout/stderr are captured and returned along with success status.

    Safety layers:
      1. Static validation (block dangerous patterns)
      2. Subprocess sandbox (test in headless FreeCAD first)
      3. Undo transactions (roll back on Python-level failure)
      4. Auto-save (backup document before execution)
    """
    # Layer 1: Static validation
    warnings = _validate_code(code)
    if warnings:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr="Pre-execution validation failed:\n" + "\n".join(warnings),
            code=code,
        )

    # Layer 2: Subprocess sandbox
    if sandbox:
        safe, sandbox_err = _sandbox_test(code, timeout=min(timeout, 15))
        if not safe:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=sandbox_err,
                code=code,
            )

    # Build execution namespace with FreeCAD modules
    namespace = _build_namespace()

    # Layer 4: Auto-save before execution
    _auto_save(namespace)

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
        # Layer 3: Undo transaction
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
            pass

        try:
            exec(code, namespace)
        finally:
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

    # FreeCAD crash-prone patterns
    has_revolution = bool(re.search(r"Revolution|Revolve|makeRevolution", code))
    if has_revolution:
        # Revolution with a full circle profile is a known crash
        has_full_circle = bool(re.search(r"Part\.Circle\s*\(", code))
        has_arc = bool(re.search(r"ArcOfCircle|Arc\s*\(", code))
        if has_full_circle and not has_arc:
            warnings.append(
                "Revolution with a full Part.Circle profile will crash FreeCAD. "
                "Use Part.ArcOfCircle (semicircle) + a closing line instead, "
                "or use Part.makeSphere() for spheres."
            )
        # Check for 360 degree revolution — always risky with sketch profiles
        if re.search(r"\.Angle\s*=\s*360", code):
            warnings.append(
                "360-degree Revolution detected. Ensure the profile is an OPEN "
                "shape (semicircle + straight line along axis), NOT a closed "
                "circle. If you want a sphere, use Part.makeSphere() instead."
            )

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
