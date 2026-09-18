"""Code execution engine for FreeCAD AI.

Extracts Python code from LLM responses and executes them in FreeCAD's
interpreter with the appropriate modules in scope.

Safety layers:
  1. Static validation — block dangerous patterns
  2. Subprocess sandbox — test code in a headless FreeCAD process first
  3. Undo transactions — roll back failed operations
  4. Auto-save — save document before execution so crashes don't lose work
"""

import inspect
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Written to fd 2 by the harness to bracket the user's code. FreeCAD's C++
# layer writes its console errors straight to the same descriptor, so an
# unbuffered os.write here interleaves in true order with them — which is what
# lets the parent tell "this document was already broken" from "this code
# broke it".
_USER_CODE_BEGIN = "__FCAI_USER_CODE_BEGIN__"
_USER_CODE_END = "__FCAI_USER_CODE_END__"

# A single bad feature can log the same line on every recompute iteration, and
# a bad document can log dozens of distinct ones. The message goes to an LLM as
# context, so it is truncated rather than allowed to crowd out the code.
_MAX_CONSOLE_ERRORS = 10

# Set once the sandbox has reported a broken console-capture channel, so the
# warning names the problem on the first pre-check of the session instead of
# on every one. Degradation is a property of the FreeCAD build, not of any
# single execution — repeating it per call would only teach the reader to
# scroll past it.
_CONSOLE_CAPTURE_WARNED = False


def _console_errors_from_stderr(stderr) -> list:
    """Return the FreeCAD console errors the executed code is responsible for.

    ``stderr`` is the sandbox subprocess's captured fd 2. The harness turns the
    console's ``Wrn`` level off and brackets the user's code with markers, so
    everything between them is an error rather than a warning.

    Two things are filtered out, both for the same reason — the code under test
    did not cause them:

    * output before the opening marker, which belongs to opening and first
      recomputing the document;
    * output inside the window whose text also appeared in that baseline. An
      object that failed to recompute stays ``Touched``, so it re-emits its
      error on *every* later recompute, including the one the user's code
      triggers. Position alone does not separate those — only the text does.
      This is the false positive reported as issue #82.

    Missing markers mean the run died before the window could be delimited. The
    output is then unattributable, and guessing would blame the user's code for
    the document's pre-existing state, so nothing is reported.
    """
    if not stderr:
        return []
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if _USER_CODE_BEGIN not in stderr or _USER_CODE_END not in stderr:
        return []

    before, _, rest = stderr.partition(_USER_CODE_BEGIN)
    window, _, _after = rest.partition(_USER_CODE_END)

    baseline = {line.strip() for line in before.splitlines() if line.strip()}
    errors = []
    for line in window.splitlines():
        line = line.strip()
        if not line or line in baseline or line in errors:
            continue
        errors.append(line)
        if len(errors) >= _MAX_CONSOLE_ERRORS:
            break
    return errors


def _console_capture_warning(result) -> str | None:
    """Return a warning if the sandbox could not install its console observer.

    The harness hooks ``App.Console.AddObserver`` to catch errors the C++ layer
    *logs* without raising anything Python can see — a failed attachment or
    recompute that leaves the document quietly wrong. That hook is optional by
    construction, and when it does not attach the sandbox keeps validating
    object state and reports success as though both channels had run.

    ``result`` is the JSON the harness wrote. A missing ``console_capture`` key
    means the harness predates this field, not that capture failed, so it is
    not treated as a problem. Returns ``None`` when there is nothing to say.
    """
    status = (result or {}).get("console_capture")
    if not status or status == "ok":
        return None
    return (
        "Sandbox console capture unavailable ({}). Pre-execution checks still "
        "validate object state, but FreeCAD errors printed by the C++ layer "
        "without raising a Python exception will not be detected.".format(status)
    )


def _warn_console_capture_once(result) -> None:
    """Log ``_console_capture_warning`` at most once per session."""
    global _CONSOLE_CAPTURE_WARNED
    if _CONSOLE_CAPTURE_WARNED:
        return
    message = _console_capture_warning(result)
    if message:
        _CONSOLE_CAPTURE_WARNED = True
        logger.warning(message)


@dataclass
class ExecutionResult:
    success: bool
    stdout: str
    stderr: str
    code: str


# Fence tags we treat as executable Python. Deliberately narrow: this text is
# handed to exec(), so ```bash / ```json must never match. A bare ``` counts,
# since models routinely omit the tag (issue #50).
PYTHON_FENCE_TAGS = ("python", "py", "python3", "")

# Regex to extract ```<tag> ... ``` code blocks. Tag is filtered afterwards.
CODE_BLOCK_RE = re.compile(r"```(\w*)[ \t]*\n(.*?)```", re.DOTALL)

# A fence opened but never closed — the response was cut off mid-block
# (typically at max_tokens). Anchored to the end of the text.
TRUNCATED_BLOCK_RE = re.compile(r"```(\w*)[ \t]*\n((?:(?!```).)*)\Z", re.DOTALL)


def _is_python_fence(tag: str) -> bool:
    return tag.lower() in PYTHON_FENCE_TAGS


def extract_code_blocks(text: str) -> list[str]:
    """Extract all complete Python code blocks from markdown-formatted text.

    Only closed blocks are returned — a block cut off mid-expression must never
    reach exec(). Use :func:`extract_truncated_block` to recover those for display.
    """
    return [code for tag, code in CODE_BLOCK_RE.findall(text) if _is_python_fence(tag)]


def extract_truncated_block(text: str) -> str | None:
    """Return the body of a trailing unterminated code fence, if any.

    A plan that hits ``max_tokens`` ends mid-code-block with no closing fence, so
    the normal extractor finds nothing and the user is left with an unusable wall
    of text (issue #50). This recovers the partial script so it can still be
    rendered and copied — never executed.
    """
    # Strip complete blocks first so their content can't be mistaken for an
    # unterminated one, then look for a fence opening in what remains.
    remainder = CODE_BLOCK_RE.sub("", text)
    match = TRUNCATED_BLOCK_RE.search(remainder)
    if not match or not _is_python_fence(match.group(1)):
        return None
    code = match.group(2)
    return code if code.strip() else None


def _find_freecad_cmd() -> str:
    """Find the FreeCAD executable for console-mode subprocess runs.

    Handles AppImages, wrapper scripts, and standard installs.
    """
    import glob

    # 0. Ask the running FreeCAD for its own install directory and use the
    # console binary that ships next to it. A PATH-based guess can resolve to
    # a completely unrelated FreeCAD install (e.g. a Snap package sitting on
    # PATH while the live session runs from a Flatpak) — that binary imports
    # its own, incompatible Draft/Arch/PySide stack and segfaults on import,
    # permanently blocking the sandbox pre-check for anything Arch-related.
    # The home path is guaranteed to match the live session exactly.
    try:
        import FreeCAD as _App
        home = _App.getHomePath()
        if home:
            for name in ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "FreeCADCmd.exe"):
                candidate = os.path.join(home, "bin", name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
    except Exception:
        pass

    # 1. Look for AppImages in ~/bin (preferred — direct binary, not a wrapper script)
    appimage_patterns = [
        os.path.expanduser("~/bin/FreeCAD*.AppImage"),
        "/usr/local/bin/FreeCAD*.AppImage",
    ]
    for pattern in appimage_patterns:
        matches = sorted(glob.glob(pattern), reverse=True)  # newest version first
        if matches:
            return matches[0]

    # 2. Check standard install locations
    candidates = [
        "/usr/bin/freecadcmd",
        "/usr/bin/freecad",
        "/usr/local/bin/freecad",
        os.path.expanduser("~/bin/freecad"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    # 3. Fallback: try PATH
    for name in ("freecadcmd", "freecad"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _collect_object_issues(objects_state, baseline_bad):
    """Return post-execution validation issues the executed code is responsible for.

    ``objects_state`` is a list of ``{"name", "null", "invalid", "invalid_state"}``
    dicts captured AFTER the user code ran. ``baseline_bad`` is the set of object
    names that already had a problem (null/invalid shape or Invalid state) BEFORE
    it ran.

    Objects already broken in the baseline are suppressed: the code didn't
    introduce their invalidity, so it must not be blamed for it. An STL imported
    and converted to a solid yields an OCC-invalid Part::Feature, and the sandbox
    dry-runs against a copy of the saved document — so that invalid solid is
    present on every run. Reporting it failed unrelated code (e.g. a sketch on a
    selected face) and sent the model chasing a phantom bug across all retries.

    Only new objects (names absent from ``baseline_bad``) or objects the code
    newly broke are reported. The shape source of truth lives here so the
    subprocess harness and the unit tests exercise identical logic.
    """
    # Object types whose Shape is legitimately null in a valid, complete state.
    # An empty sketch ("create a sketch on the selected face" — geometry is
    # drawn later in the editor) and an empty body (a container before its
    # first feature) both report Shape.isNull() == True while State stays
    # "Up-to-date". Flagging their null shape produced a false positive that
    # failed the sketch-on-a-face workflow; the model then injected junk
    # placeholder geometry to defeat the check (issue #18). A genuinely failed
    # object of these types (e.g. a sketch whose attachment didn't resolve)
    # still lands in an Invalid state and is caught by the invalid_state report
    # below. Kept local so it ships with the function source into the sandbox
    # harness (inspect.getsource doesn't carry module globals).
    # Arch/BIM containers (Site, Building, BuildingPart/Floor) are pure
    # organizational groups — they hold no geometry of their own and report a
    # null Shape for their entire lifetime, empty or fully populated. Flagging
    # that blocked every Arch.makeSite/makeBuilding/makeFloor call. These are
    # scripted objects (Part::FeaturePython / App::GeometryPython) — the
    # generic TypeId is shared with countless unrelated object types, so the
    # semantic type has to come from Proxy.Type instead (e.g. "Site",
    # "BuildingPart" — Building and Floor are both BuildingPart under the hood).
    null_shape_ok_types = {"Sketcher::SketchObject", "PartDesign::Body"}
    null_shape_ok_proxy_types = {"Site", "Building", "BuildingPart", "Floor"}
    issues = []
    for st in objects_state:
        name = st["name"]
        if name in baseline_bad:
            continue
        if st.get("null"):
            if (st.get("type") not in null_shape_ok_types
                    and st.get("proxy_type") not in null_shape_ok_proxy_types):
                issues.append("Object '" + name + "' has null shape")
        elif st.get("invalid"):
            issues.append("Object '" + name + "' has invalid shape")
        if st.get("invalid_state"):
            issues.append("Object '" + name + "' is in Invalid state")
    return issues


def _sandbox_test(code: str, timeout: int = 15, document_path: str | None = None) -> tuple:
    """Test code in a headless FreeCAD subprocess.

    Returns (safe: bool, error_message: str).
    If FreeCAD console is not available, returns (True, "") to skip sandboxing.
    """
    freecad_bin = _find_freecad_cmd()
    if not freecad_bin:
        return True, ""  # Can't sandbox, let it through

    result_file = tempfile.mktemp(suffix=".json")
    script_file = tempfile.mktemp(suffix=".py")

    if document_path:
        open_block = (
            "    App.openDocument({path!r})\n"
            "    doc = App.ActiveDocument\n"
            "    if doc is None:\n"
            "        raise RuntimeError('Sandbox: openDocument did not set ActiveDocument')\n"
            "    App.setActiveDocument(doc.Name)"
        ).format(path=document_path)
    else:
        open_block = '    doc = App.newDocument("SandboxTest")'

    # Harness: run user code, then close all documents without saving (temp copy is disposable).
    # Stub FreeCADGui view methods that only work in a graphical session.
    # Harness captures two classes of failure that Python exceptions miss:
    #   1. Errors the C++ layer logs to fd 2 (PositionBySupport attachment
    #      failures, topological naming mismatches) — bracketed by markers
    #      here, read and baselined by the parent after the process exits
    #   2. Features that build a null/invalid Shape without raising
    harness = '''import sys, os as _os, json, traceback
{collect_fn_src}
result = {{"ok": False, "error": ""}}
try:
    import FreeCAD as App

    # Console channel: FreeCAD's C++ layer logs attachment and recompute
    # failures without raising anything Python can catch, and writes them to
    # fd 2. The parent reads that stream (see _console_errors_from_stderr).
    #
    # PrintError and PrintWarning both land there with no severity prefix, so
    # the stream cannot be sorted out after the fact — a redundant-constraint
    # warning would read exactly like a failure. SetStatus drops the warnings
    # at the source instead, leaving fd 2 carrying errors only. If that call
    # fails the parent is told, and ignores the stream rather than guessing.
    #
    # An App.Console.AddObserver hook used to do this job. It never ran: the
    # method does not exist on __FreeCADConsole__ in console mode, so it raised
    # into a bare except and the channel was silently dead (#83).
    _console_capture_status = "ok"
    try:
        App.Console.SetStatus("Console", "Wrn", False)
    except Exception as _gate_err:
        _console_capture_status = "{{}}: {{}}".format(
            type(_gate_err).__name__, _gate_err)
    result["console_capture"] = _console_capture_status

    # Console mode: install a fake FreeCADGui module instead of importing the
    # real one. LLM-generated code routinely ends with view-framing cosmetics
    # (Gui.ActiveDocument.ActiveView.viewIsometric(), fitAll(),
    # SendMsgToActiveView("ViewFit")); a real headless FreeCADGui has no
    # ActiveDocument, so these raise AttributeError and fail the pre-check for
    # geometry that runs fine in the user's real GUI (#14). A plain no-op stub
    # covers that. Importing the *real* FreeCADGui and then anything that
    # touches Arch/Draft (which pull in PySide) segfaults this console
    # binary — no display, no QApplication event loop — so the real module
    # must never be imported here at all, not just patched afterward.
    import types
    class _NoOpGui:
        def __getattr__(self, _name):
            return self
        def __call__(self, *a, **kw):
            return self
    _fake_gui = types.ModuleType("FreeCADGui")
    _fake_gui.ActiveDocument = _NoOpGui()
    _fake_gui.SendMsgToActiveView = lambda *a, **kw: None
    _fake_gui.updateGui = lambda *a, **kw: None
    sys.modules["FreeCADGui"] = _fake_gui
{open_block}

    # Per-object problem snapshot — shared by the baseline (pre-code) and the
    # post-execution walk so both judge invalidity identically.
    def _snap(_obj):
        _null = False
        _invalid = False
        _shape = getattr(_obj, "Shape", None)
        if _shape is not None:
            try:
                _null = bool(_shape.isNull())
                _invalid = (not _null) and (not _shape.isValid())
            except Exception:
                pass
        _state = getattr(_obj, "State", None)
        _bad_state = bool(_state and "Invalid" in _state)
        _proxy_type = getattr(getattr(_obj, "Proxy", None), "Type", "")
        return {{"name": _obj.Name, "type": getattr(_obj, "TypeId", ""),
                 "proxy_type": _proxy_type,
                 "null": _null, "invalid": _invalid, "invalid_state": _bad_state}}

    # Baseline: objects already broken in the opened document BEFORE user code
    # runs. The sandbox dry-runs against a copy of the saved document, so an
    # imported-and-converted mesh→solid that OCC considers invalid is present
    # on every run; without this snapshot it would fail unrelated code.
    #
    # Recompute once here, BEFORE snapshotting the baseline. The document was
    # opened fresh (its on-disk cached shapes reflect whatever environment
    # last saved it — typically the real GUI process), but the post-execution
    # check further down forces its own doc.recompute() unconditionally. Some
    # objects (e.g. a complex Arch::Wall whose Draft/OCC boolean fuse is
    # sensitive to the fake-FreeCADGui/headless environment here) recompute
    # differently under this sandbox than they did live — genuinely fine in
    # the user's document, but landing in an Invalid state the moment this
    # harness recomputes it. Without matching that recompute before the
    # baseline snapshot, such an object is absent from `_baseline_bad` and
    # every subsequent call — even a no-op read — gets its Invalid state
    # blamed on that call's code.
    doc.recompute()
    _baseline_bad = set()
    for _obj in doc.Objects:
        _s = _snap(_obj)
        if _s["null"] or _s["invalid"] or _s["invalid_state"]:
            _baseline_bad.add(_s["name"])

    # Everything logged up to here describes the document as it was opened.
    _os.write(2, b"\\n{begin_marker}\\n")

    # --- user code ---
{indented_code}
    # --- end user code ---
    doc.recompute()
    _os.write(2, b"\\n{end_marker}\\n")

    # Post-execution validation: collect console errors + flag only the shapes
    # this code created or newly broke. Either signal means the code "ran" but
    # broke the model — the case where Python-exception-only checking fails.
    # Console errors are merged by the parent, which is the only side that can
    # read this process's stderr.
    _issues = []
    _objects_state = [_snap(_obj) for _obj in doc.Objects]
    _issues.extend(_collect_object_issues(_objects_state, _baseline_bad))

    if _issues:
        result["error"] = "Post-execution validation found issues:\\n" + "\\n".join(_issues)
    else:
        result["ok"] = True
except Exception as e:
    result["error"] = traceback.format_exc()
finally:
    try:
        import FreeCAD as App
        for _dn in list(App.listDocuments().keys()):
            App.closeDocument(_dn)
    except Exception:
        pass
    with open({result_path!r}, "w") as f:
        json.dump(result, f)
    # Force the interpreter to exit. On some FreeCAD builds, running a script
    # via `-c` against an OPENED document leaves the process in interactive
    # mode (Qt/console event loop never returns), so subprocess.run() would
    # block until its timeout and the sandbox always reported a spurious
    # "timed out" — even for trivial code (issue #14). os._exit skips atexit
    # handlers / lingering non-daemon threads that a plain sys.exit can wait on.
    import os as _os
    _os._exit(0)
'''.format(
        collect_fn_src=inspect.getsource(_collect_object_issues),
        begin_marker=_USER_CODE_BEGIN,
        end_marker=_USER_CODE_END,
        open_block=open_block,
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
            _warn_console_capture_once(result)

            # Merged here rather than in the harness because this is the only
            # side that can read the subprocess's stderr. Trusted only when the
            # harness confirmed it gated warnings off — otherwise the stream
            # mixes severities, and reporting a redundant-constraint warning as
            # a failure is precisely the false positive of issue #82.
            console_errors = []
            if result.get("console_capture") == "ok":
                console_errors = _console_errors_from_stderr(proc.stderr)

            if result["ok"] and not console_errors:
                return True, ""

            problems = []
            if not result["ok"]:
                problems.append(result["error"])
            if console_errors:
                problems.append(
                    "FreeCAD logged errors while running this code:\n"
                    + "\n".join(console_errors))
            return False, "Sandbox: " + "\n".join(problems)

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
    """Save a recovery copy of the active document before executing code.

    Snapshots live in the managed ``BACKUPS_DIR`` under the extension's config
    tree (#46), not beside the user's document — the project folder stays clean
    and disk use is bounded. Each source path maps to one stable, hash-tagged
    file that is overwritten in place, so same-named documents in different
    folders never collide and no snapshot accretes across executions.
    """
    try:
        import hashlib
        from ..config import BACKUPS_DIR, get_config, prune_oldest_files
        from . import backups as backups_index
        from .active_document import resolve_active_document
        doc = resolve_active_document()
        if not doc or not doc.FileName:
            return  # Unsaved document, nothing to back up
        original = doc.FileName
        original_label = getattr(doc, "Label", None)
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        stem = os.path.splitext(os.path.basename(original))[0]
        tag = hashlib.sha1(original.encode("utf-8")).hexdigest()[:8]
        backup = os.path.join(
            BACKUPS_DIR, f"{stem}.{tag}{backups_index.SNAPSHOT_SUFFIX}")
        doc.saveAs(backup)
        # saveAs repoints FileName at the snapshot; restore the exact original
        # so the path can't compound across calls (the #45 accretion).
        doc.FileName = original
        # It renames the document too — Label becomes the snapshot's stem, so
        # the tree would read "part.4e8d53db.ai-backup" and the user's next
        # ordinary save would write that name into their file.
        if original_label is not None:
            doc.Label = original_label
        # Only now: the snapshot cannot say where it came from (FileName is a
        # transient property, the tag is a one-way hash), so this note is the
        # whole of what makes it restorable (#49). It is still the lesser
        # guarantee -- record it after the document is safely back to itself.
        backups_index.record_snapshot(
            BACKUPS_DIR, backup, original, label=original_label or "")
        cfg = get_config()
        prune_oldest_files(
            BACKUPS_DIR,
            backups_index.is_snapshot,
            cfg.max_backups,
            cfg.max_retention_age_days,
        )
    except Exception:
        pass  # Best-effort


_DEFAULT_EXECUTION_TIMEOUT = 30


def _configured_timeout(default: int = _DEFAULT_EXECUTION_TIMEOUT) -> int:
    """Resolve the execution timeout (seconds) from user config.

    Heavy-but-valid geometry ops (e.g. scaling a detailed model via
    Shape.transformGeometry) can exceed a fixed budget; sourcing it from
    config lets users raise it for large models instead of hitting a
    hardcoded wall on both the sandbox and live paths (issue #14). Falls
    back to ``default`` if config is unavailable or holds a bad value.
    """
    try:
        from ..config import get_config
        val = int(getattr(get_config(), "execution_timeout", default))
        return val if val > 0 else default
    except Exception:
        return default


def execute_code(code: str, timeout: int | None = None, sandbox: bool = True,
                 skip_safety: bool = False) -> ExecutionResult:
    """Execute Python code in FreeCAD's context.

    The code runs with FreeCAD modules available in its namespace.
    stdout/stderr are captured and returned along with success status.

    Safety layers:
      1. Static validation (block dangerous patterns)
      2. Subprocess sandbox (test in headless FreeCAD first)
      3. Undo transactions (roll back on Python-level failure)
      4. Auto-save (backup document before execution)

    timeout: Wall-clock budget (seconds) applied to both the sandbox dry-run
        and the live SIGALRM. ``None`` (the default) resolves it from
        ``AppConfig.execution_timeout`` so the GUI's setting is honored.

    skip_safety: When True, skip static validation, the headless sandbox
        pre-check, and the SIGALRM timeout. The undo transaction (rollback on
        failure) and auto-save remain active. Used by Dangerous mode only.
    """
    if timeout is None:
        timeout = _configured_timeout()
    # Layer 1: Static validation (skipped in dangerous mode)
    if not skip_safety:
        warnings = _validate_code(code)
        if warnings:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Pre-execution validation failed:\n" + "\n".join(warnings),
                code=code,
            )

    from .active_document import get_synced_active_document, refresh_gui_for_document

    # Layer 2: Subprocess sandbox (skipped in dangerous mode) — optional copy of saved document so getObject-style code validates safely
    sandbox_copy_path = None
    if sandbox and not skip_safety:
        pre_doc = get_synced_active_document()
        fn = getattr(pre_doc, "FileName", "") if pre_doc else ""
        if fn and os.path.isfile(fn):
            try:
                fd, sandbox_copy_path = tempfile.mkstemp(suffix=".FCStd")
                os.close(fd)
                shutil.copy2(fn, sandbox_copy_path)
            except OSError as e:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Sandbox: could not copy document for validation: {e}",
                    code=code,
                )
        try:
            # The dry-run must get the same budget as the live execution
            # (which arms a SIGALRM for the full `timeout`). Capping it lower
            # made valid-but-slow code — e.g. scaling a complex shape with
            # Shape.transformGeometry — fail the pre-check with a spurious
            # "timed out after 15 seconds" and never run (issue #14).
            safe, sandbox_err = _sandbox_test(
                code, timeout=timeout, document_path=sandbox_copy_path
            )
            if not safe:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=sandbox_err,
                    code=code,
                )
        finally:
            if sandbox_copy_path:
                try:
                    os.unlink(sandbox_copy_path)
                except OSError:
                    pass

    target_doc = get_synced_active_document()
    if target_doc is None:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=(
                "No active document — open a document in FreeCAD or click "
                "its tab so it is the focused window."
            ),
            code=code,
        )
    doc_name = target_doc.Name

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

    doc = target_doc
    success = True
    try:
        # Layer 3: Undo transaction
        if doc:
            doc.openTransaction("AI Code Execution")

        # Set an alarm timeout to catch infinite loops / hangs
        _old_handler = None
        if not skip_safety:
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
            if not skip_safety:
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
        import FreeCAD as App
        d = App.getDocument(doc_name)
        if d is None:
            raise RuntimeError(
                "Target document is no longer available after execution."
            )
        refresh_gui_for_document(d)
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


def validate_code(code: str, timeout: int = 15, skip_safety: bool = False) -> ExecutionResult:
    """Run static + sandbox validation without touching the live document.

    Runs Layer 1 (static pattern check) and Layer 2 (headless subprocess
    against a temp copy of the active document). Skips Layer 3/4. Returns
    an ExecutionResult so callers can reuse the same error-surfacing path
    they use for actual execution.

    If no FreeCAD console binary is available, the sandbox is skipped and
    the result is a pass — matches execute_code()'s fallback behavior.

    skip_safety: When True, return success immediately without running any
        validation (Dangerous mode).
    """
    if skip_safety:
        return ExecutionResult(success=True, stdout="", stderr="", code=code)
    warnings = _validate_code(code)
    if warnings:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr="Static validation failed:\n" + "\n".join(warnings),
            code=code,
        )

    from .active_document import get_synced_active_document
    pre_doc = get_synced_active_document()
    fn = getattr(pre_doc, "FileName", "") if pre_doc else ""
    sandbox_copy_path = None
    if fn and os.path.isfile(fn):
        try:
            fd, sandbox_copy_path = tempfile.mkstemp(suffix=".FCStd")
            os.close(fd)
            shutil.copy2(fn, sandbox_copy_path)
        except OSError as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Sandbox: could not copy document for validation: {e}",
                code=code,
            )
    try:
        safe, err = _sandbox_test(code, timeout=timeout, document_path=sandbox_copy_path)
    finally:
        if sandbox_copy_path:
            try:
                os.unlink(sandbox_copy_path)
            except OSError:
                pass
    if safe:
        return ExecutionResult(success=True, stdout="", stderr="", code=code)
    return ExecutionResult(success=False, stdout="", stderr=err, code=code)


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
    """Recompute the GUI-aligned active document if available."""
    from .active_document import resolve_active_document
    doc = resolve_active_document()
    if doc:
        doc.recompute()
