# Headless Code Execution (#114) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `execute_code_headless(code, timeout=600)` tool runs Python in a separate FreeCAD console process on a copy of the active document, so long jobs neither freeze the GUI nor can crash it.

**Architecture:** Tools gain a `main_thread` flag; `QtMainThreadToolExecutor` runs `main_thread=False` tools on the calling worker thread, and such a handler hops to the GUI thread only for the one call that needs it (`run_on_main`, used for `doc.saveCopy`). The sandbox's child-script harness moves into `_build_child_script(..., mode=...)`, byte-identical for `mode="sandbox"`, with a new `mode="headless"`. A new module `core/headless.py` owns run folders, launching the child in its own process group, the poll/kill loop and turning the run's files into a result.

**Tech Stack:** Python 3.11 stdlib (`subprocess`, `signal`, `os.killpg`), Qt via `freecad_ai/ui/compat.py` (PySide6 6.8 in FreeCAD 1.1, PySide2 in 1.0), pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-headless-exec-design.md`

## Global Constraints

- No external dependencies: stdlib plus Qt through `freecad_ai/ui/compat.py` only. Never import PySide2/PySide6 directly in product code; use flat enums (`Qt.QueuedConnection`).
- New defaults preserve prior behaviour: `ToolDefinition.main_thread` defaults to `True`; `_build_child_script(mode="sandbox")` is byte-identical to today's harness.
- Default timeout 600 s; poll every 0.5 s; `SIGTERM` to the process group, `SIGKILL` after 3 s.
- Run folders: `CONFIG_DIR/headless/<YYYYmmdd-HHMMSS>-<6 hex>/`; at most 20 exist after a new one is made.
- Output: capped at 20,000 characters per stream; stderr tails are the last 20 lines.
- Tool category `general` (same as `execute_code`), `main_thread=False`.
- The maintainer verifies on Linux only. macOS/Windows code paths exist but ship untested; the CHANGELOG says so.
- Run tests with `env PYTHONPATH= .venv/bin/pytest -q --ignore=tests/unit/test_document_attach.py` (the empty `PYTHONPATH` stops FreeCAD's site-packages leaking into the venv; `test_document_attach.py` segfaults Qt in this venv).
- Integration tests: `env PYTHONPATH= .venv/bin/pytest -q -m integration tests/integration/test_headless_integration.py` (needs the FreeCAD AppImage in `~/bin`).
- Commits are conventional (`feat(tools): …`) and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Only the final PR title carries `(#114)`.

### Refinements of the spec made while planning (flag these to the reviewer)

1. The spec sends user stdout/stderr into in-memory buffers. Those are lost when the process is killed, but the error table promises partial output on timeout and Stop. The child therefore writes them to line-buffered files `<run>/output.log` (stdout) and `<run>/stderr.log` (stderr), UTF-8.
2. The sandbox pastes user code into the harness indented by four spaces, which corrupts multi-line string literals. Headless mode instead embeds the source with `repr()` and runs it with `exec(compile(src, "<run>/user_code.py", "exec"))`; the runner also writes `user_code.py` so tracebacks show source lines.
3. Capping keeps the **last** 20,000 characters (final results and errors print last) behind a note naming the full file.
4. With no result file, an exit code 129–192 on POSIX is reported as a crash by signal `code - 128`: an AppImage's runtime reports its child's `SIGABRT` that way instead of a negative return code.
5. The child's working directory is the run folder, so relative paths land next to `WORK_DIR`.

## Review Focus

1. **Multi-line string literals in the user's code** (`"""…"""`, SVG/JSON templates) must reach the child unchanged; a reasonable person expects `print(s)` to print what they wrote. Pinned in Task 2.
2. **An AppImage child that aborts** exits 134 instead of -6; the user expects "crashed (SIGABRT)", not "exited with code 134". Pinned in Task 5.
3. **Pruning run folders** must never delete a folder the tool did not create (a user may park a result next to the runs). Pinned in Task 5.
4. **An LLM passing `timeout` as a string (`"60"`), zero or a negative number**: strings are parsed, values below 1 become 1, junk is refused with a message. Pinned in Task 6.
5. **Non-ASCII output** (`print("Ø 10 mm")`) must round-trip into the result whatever the child's locale. Pinned in Task 2 (files opened with `encoding="utf-8"`) and Task 7.

---

### Task 1: Extract the sandbox harness into `_build_child_script` (byte-identical)

**Files:**
- Create: `tests/unit/golden/sandbox_harness_new.txt`, `tests/unit/golden/sandbox_harness_open.txt`
- Create: `tests/unit/test_child_script.py`
- Modify: `freecad_ai/core/executor.py` (`_sandbox_test`, plus new module-level `_GUI_STUB`, `_SANDBOX_HARNESS`, `_open_block`, `_build_child_script` placed directly above `_sandbox_test`)

**Interfaces:**
- Produces: `_build_child_script(code: str, *, document_path: str | None, result_path: str, mode: str = "sandbox") -> str`; `_GUI_STUB: str` (indented 4 spaces, no trailing newline); `_open_block(document_path: str | None, who: str, new_name: str) -> str`. `mode="headless"` raises `ValueError` until Task 2.

- [ ] **Step 1: Capture the golden harness from the unchanged code**

Run from the repo root, before touching `executor.py`:

```bash
mkdir -p tests/unit/golden
env PYTHONPATH=$PWD .venv/bin/python - <<'EOF'
import os, tempfile
from unittest.mock import patch
from freecad_ai.core import executor

CODE = "x = 1\nif x:\n    print('hi')\n"
captured = {}

def fake_run(cmd, **kw):
    with open(cmd[2]) as f:
        captured["src"] = f.read()
    class P:
        returncode = 0
        stdout = b""
        stderr = b""
    return P()

for name, doc in (("new", None), ("open", "/tmp/fcai_in.FCStd")):
    script = os.path.join(tempfile.gettempdir(), "fcai_golden_script.py")
    with patch.object(executor, "_find_freecad_cmd", return_value="/bin/true"), \
         patch.object(executor.tempfile, "mktemp",
                      side_effect=["/tmp/fcai_r.json", script]), \
         patch.object(executor.subprocess, "run", side_effect=fake_run):
        executor._sandbox_test(CODE, timeout=1, document_path=doc)
    with open(f"tests/unit/golden/sandbox_harness_{name}.txt", "w") as f:
        f.write(captured["src"])
print("ok")
EOF
```

Expected: prints `ok`; both files exist and start with `import sys, os as _os, json, traceback`.

- [ ] **Step 2: Write the failing golden test**

`tests/unit/test_child_script.py`:

```python
"""_build_child_script: one harness for the sandbox and headless runs (#114)."""

import os

import pytest

from freecad_ai.core import executor

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
CODE = "x = 1\nif x:\n    print('hi')\n"


@pytest.mark.parametrize("name, doc", [("new", None), ("open", "/tmp/fcai_in.FCStd")])
def test_sandbox_mode_is_byte_identical_to_the_old_harness(name, doc):
    with open(os.path.join(GOLDEN, f"sandbox_harness_{name}.txt")) as f:
        expected = f.read()
    got = executor._build_child_script(
        CODE, document_path=doc, result_path="/tmp/fcai_r.json", mode="sandbox")
    assert got == expected


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        executor._build_child_script("pass", document_path=None,
                                     result_path="/tmp/r.json", mode="nope")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_child_script.py`
Expected: FAIL with `AttributeError: module 'freecad_ai.core.executor' has no attribute '_build_child_script'`.

- [ ] **Step 4: Move the harness out of `_sandbox_test`**

In `freecad_ai/core/executor.py`, directly above `def _sandbox_test`:

1. Add `_GUI_STUB` — exactly the lines of today's template from `    import types` through `    sys.modules["FreeCADGui"] = _fake_gui`, without a trailing newline:

```python
# Installed in place of the real FreeCADGui in every child script: see the
# comment above {gui_stub} in _SANDBOX_HARNESS for why the real one must
# never be imported there.
_GUI_STUB = '''    import types
    class _NoOpGui:
        def __getattr__(self, _name):
            return self
        def __call__(self, *a, **kw):
            return self
    _fake_gui = types.ModuleType("FreeCADGui")
    _fake_gui.ActiveDocument = _NoOpGui()
    _fake_gui.SendMsgToActiveView = lambda *a, **kw: None
    _fake_gui.updateGui = lambda *a, **kw: None
    sys.modules["FreeCADGui"] = _fake_gui'''
```

2. Add `_open_block`, generalising today's `open_block` construction (the `who` text replaces the literal `Sandbox` in the error message):

```python
def _open_block(document_path, who, new_name):
    """Harness lines that leave the document to work on in ``doc``."""
    if document_path:
        return (
            "    App.openDocument({path!r})\n"
            "    doc = App.ActiveDocument\n"
            "    if doc is None:\n"
            "        raise RuntimeError('{who}: openDocument did not set ActiveDocument')\n"
            "    App.setActiveDocument(doc.Name)"
        ).format(path=document_path, who=who)
    return '    doc = App.newDocument("{}")'.format(new_name)
```

3. Add `_SANDBOX_HARNESS = '''…'''`: cut the template string literal out of `_sandbox_test` (from `import sys, os as _os, json, traceback` to the closing `'''` before `.format(`), keep the comment block that precedes it in `_sandbox_test` above the constant, and inside the template replace the eleven `_GUI_STUB` lines (from `    import types` to `    sys.modules["FreeCADGui"] = _fake_gui`) with a single line `{gui_stub}`. Leave every other character as it is.

4. Add the builder:

```python
def _build_child_script(code, *, document_path, result_path, mode="sandbox"):
    """Source of the script a FreeCAD console child runs.

    ``sandbox``: the pass/fail pre-check used by execute_code.
    """
    if mode == "sandbox":
        return _SANDBOX_HARNESS.format(
            collect_fn_src=inspect.getsource(_collect_object_issues),
            begin_marker=_USER_CODE_BEGIN,
            end_marker=_USER_CODE_END,
            gui_stub=_GUI_STUB,
            open_block=_open_block(document_path, "Sandbox", "SandboxTest"),
            indented_code="\n".join("    " + line for line in code.splitlines()),
            result_path=result_path,
        )
    raise ValueError("unknown child-script mode: {!r}".format(mode))
```

5. In `_sandbox_test`, delete the `open_block` construction and the template, and build the script with:

```python
    harness = _build_child_script(
        code, document_path=document_path, result_path=result_file, mode="sandbox")
```

- [ ] **Step 5: Run the golden test and the existing sandbox tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_child_script.py tests/unit/test_executor.py`
Expected: all PASS. If the golden test fails, diff with `diff <(env PYTHONPATH=$PWD .venv/bin/python -c "from freecad_ai.core import executor as e; print(e._build_child_script(\"x = 1\nif x:\n    print('hi')\n\", document_path=None, result_path='/tmp/fcai_r.json'), end='')") tests/unit/golden/sandbox_harness_new.txt` and fix the moved text, never the golden file.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/core/executor.py tests/unit/test_child_script.py tests/unit/golden
git commit -m "refactor(executor): build the sandbox child script in _build_child_script

Byte-identical output, pinned by golden files captured before the move.
Prepares a second (headless) mode.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Headless mode of `_build_child_script`

**Files:**
- Modify: `freecad_ai/core/executor.py` (`_HEADLESS_HARNESS` constant, headless branch in `_build_child_script`)
- Test: `tests/unit/test_child_script.py`

**Interfaces:**
- Consumes: `_GUI_STUB`, `_open_block` (Task 1).
- Produces: `_build_child_script(code, document_path=..., result_path=<run>/result.json, mode="headless")`. The child: defines `WORK_DIR = dirname(result_path)`; appends user stdout to `<run>/output.log` and user stderr to `<run>/stderr.log` (UTF-8, line-buffered); writes `<run>/result.json` as `{"ok": bool, "error": str}` (`error` = traceback text); closes all documents; ends with `os._exit(0)`. `sys.exit(0)`/`sys.exit()` in user code count as completion.

- [ ] **Step 1: Write the failing tests**

The harness runs under plain Python with a stub `FreeCAD` module, so these tests need no FreeCAD. Append to `tests/unit/test_child_script.py`:

```python
import json
import subprocess
import sys

_FREECAD_STUB = '''
class _Doc:
    def __init__(self, name):
        self.Name = name
        self.FileName = ""

_docs = {}
ActiveDocument = None

def newDocument(name):
    global ActiveDocument
    ActiveDocument = _docs[name] = _Doc(name)
    return ActiveDocument

def openDocument(path):
    global ActiveDocument
    ActiveDocument = _docs["Opened"] = _Doc("Opened")
    ActiveDocument.FileName = path
    return ActiveDocument

def setActiveDocument(name):
    pass

def listDocuments():
    return dict(_docs)

def closeDocument(name):
    _docs.pop(name, None)
'''


def _run_headless_child(tmp_path, code, document_path=None):
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "FreeCAD.py").write_text(_FREECAD_STUB)
    run = tmp_path / "run"
    run.mkdir()
    result = run / "result.json"
    script = run / "script.py"
    script.write_text(executor._build_child_script(
        code, document_path=document_path, result_path=str(result),
        mode="headless"), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script)], cwd=run, timeout=60,
                          env={**os.environ, "PYTHONPATH": str(stub)})
    assert proc.returncode == 0
    out = (run / "output.log").read_text(encoding="utf-8")
    err = (run / "stderr.log").read_text(encoding="utf-8")
    return json.loads(result.read_text(encoding="utf-8")), out, err, run


def test_headless_prints_go_to_output_log(tmp_path):
    res, out, err, _ = _run_headless_child(tmp_path, "print('volume', 1000)\n")
    assert res == {"ok": True, "error": ""}
    assert out == "volume 1000\n" and err == ""


def test_headless_defines_work_dir_as_the_run_folder(tmp_path):
    code = "open(WORK_DIR + '/r.txt', 'w').write('x')\nprint(App.ActiveDocument.Name)\n"
    res, out, _, run = _run_headless_child(tmp_path, code)
    assert res["ok"] and (run / "r.txt").read_text() == "x"
    assert out == "Headless\n"


def test_headless_opens_the_given_document(tmp_path):
    res, out, _, _ = _run_headless_child(
        tmp_path, "print(App.ActiveDocument.FileName)\n", document_path="/x/in.FCStd")
    assert res["ok"] and out == "/x/in.FCStd\n"


def test_headless_exception_reports_the_traceback_and_keeps_output(tmp_path):
    res, out, _, _ = _run_headless_child(tmp_path, "print('before')\n1 / 0\n")
    assert not res["ok"]
    assert "ZeroDivisionError" in res["error"] and "user_code.py" in res["error"]
    assert out == "before\n"


def test_headless_stderr_goes_to_its_own_log(tmp_path):
    _, out, err, _ = _run_headless_child(
        tmp_path, "import sys\nprint('warn', file=sys.stderr)\n")
    assert err == "warn\n" and out == ""


def test_headless_keeps_multiline_string_literals(tmp_path):
    # Review focus 1: the sandbox indents user code, which would turn this into 'a\n    b'
    res, out, _, _ = _run_headless_child(tmp_path, 's = """a\nb"""\nprint(repr(s))\n')
    assert res["ok"] and out == "'a\\nb'\n"


def test_headless_non_ascii_output_round_trips(tmp_path):
    # Review focus 5
    res, out, _, _ = _run_headless_child(tmp_path, "print('Ø 10 mm, Größe')\n")
    assert res["ok"] and out == "Ø 10 mm, Größe\n"


@pytest.mark.parametrize("code, ok", [("import sys\nsys.exit()\n", True),
                                      ("import sys\nsys.exit(0)\n", True),
                                      ("import sys\nsys.exit(3)\n", False)])
def test_headless_sys_exit_zero_counts_as_completion(tmp_path, code, ok):
    res, _, _, _ = _run_headless_child(tmp_path, code)
    assert res["ok"] is ok


def test_headless_script_never_imports_the_real_gui():
    src = executor._build_child_script("pass", document_path=None,
                                       result_path="/tmp/run/result.json", mode="headless")
    assert "import FreeCADGui" not in src and 'sys.modules["FreeCADGui"]' in src
    assert "_os._exit(0)" in src
```

- [ ] **Step 2: Run them to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_child_script.py -k headless`
Expected: FAIL with `ValueError: unknown child-script mode: 'headless'`.

- [ ] **Step 3: Implement the headless harness**

In `freecad_ai/core/executor.py`, below `_SANDBOX_HARNESS`:

```python
# Headless runs (#114) report what the code printed and whether it raised;
# unlike the sandbox they pass no verdict on the model. User output goes to
# line-buffered files rather than memory so a run killed for its timeout still
# leaves what it printed. The code is exec'd from its source rather than
# pasted in indented, which would change multi-line string literals.
_HEADLESS_HARNESS = '''import sys, os as _os, json, traceback
WORK_DIR = {work_dir!r}
result = {{"ok": False, "error": ""}}
_out = open(_os.path.join(WORK_DIR, "output.log"), "w", encoding="utf-8", buffering=1)
_err = open(_os.path.join(WORK_DIR, "stderr.log"), "w", encoding="utf-8", buffering=1)
try:
    import FreeCAD as App
{gui_stub}
{open_block}
    _src = {code!r}
    _path = _os.path.join(WORK_DIR, "user_code.py")
    _ns = {{"__name__": "__main__", "__file__": _path, "__builtins__": __builtins__,
           "App": App, "FreeCAD": App, "WORK_DIR": WORK_DIR,
           "Gui": sys.modules["FreeCADGui"], "FreeCADGui": sys.modules["FreeCADGui"]}}
    for _name in ("Part", "PartDesign", "Sketcher", "Draft", "Mesh", "BOPTools", "math"):
        try:
            _ns[_name] = __import__(_name)
        except Exception:
            pass
    _saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _out, _err
    try:
        exec(compile(_src, _path, "exec"), _ns)
    except SystemExit as _exit:
        if _exit.code not in (None, 0):
            raise
    finally:
        sys.stdout, sys.stderr = _saved
    result["ok"] = True
except BaseException:
    result["error"] = traceback.format_exc()
finally:
    try:
        import FreeCAD as App
        for _dn in list(App.listDocuments().keys()):
            App.closeDocument(_dn)
    except BaseException:
        pass
    for _f in (_out, _err):
        try:
            _f.close()
        except Exception:
            pass
    with open({result_path!r}, "w", encoding="utf-8") as f:
        json.dump(result, f)
    # See _SANDBOX_HARNESS: some builds never exit after -c on an opened document (#14).
    _os._exit(0)
'''
```

In `_build_child_script`, before the `raise ValueError`, and extend the docstring with `` ``headless``: execute_code_headless (#114). ``:

```python
    if mode == "headless":
        return _HEADLESS_HARNESS.format(
            work_dir=os.path.dirname(result_path),
            gui_stub=_GUI_STUB,
            open_block=_open_block(document_path, "Headless", "Headless"),
            code=code,
            result_path=result_path,
        )
```

- [ ] **Step 4: Run the tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_child_script.py`
Expected: all PASS (the golden tests too: sandbox output must not move).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/core/executor.py tests/unit/test_child_script.py
git commit -m "feat(executor): headless mode for the child-script harness

WORK_DIR, user output in line-buffered UTF-8 logs, the code exec'd from
its source, sys.exit(0) counted as completion.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `ToolDefinition.main_thread` and the executor branch

**Files:**
- Modify: `freecad_ai/tools/registry.py` (`ToolDefinition`)
- Modify: `freecad_ai/tools/executor_utils.py` (`MainThreadToolExecutor._runs_on_caller`, `QtMainThreadToolExecutor.execute`)
- Test: `tests/unit/test_executor_utils.py`

**Interfaces:**
- Produces: `ToolDefinition.main_thread: bool = True`. `MainThreadToolExecutor._runs_on_caller(tool_name) -> bool` (True only when the registered tool has `main_thread is False`; MagicMock registries and unknown tools give False).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_executor_utils.py`:

```python
import threading
import time

import pytest

from freecad_ai.tools.registry import ToolDefinition, ToolRegistry


def _thread_recording_registry():
    seen = {}
    reg = ToolRegistry()
    for name, main in (("gui_tool", True), ("worker_tool", False)):
        reg.register(ToolDefinition(
            name=name, description=name, parameters=[], main_thread=main,
            handler=lambda _n=name: (seen.__setitem__(_n, threading.get_ident())
                                     or ToolResult(success=True, output=_n))))
    return reg, seen


def test_main_thread_defaults_to_true():
    tool = ToolDefinition(name="t", description="", parameters=[],
                          handler=lambda: None)
    assert tool.main_thread is True


def test_runs_on_caller_only_for_main_thread_false():
    executor = MainThreadToolExecutor()
    reg, _ = _thread_recording_registry()
    executor.set_registry(reg)
    assert executor._runs_on_caller("worker_tool") is True
    assert executor._runs_on_caller("gui_tool") is False
    assert executor._runs_on_caller("unknown") is False


def test_runs_on_caller_is_false_for_mock_registries():
    executor = MainThreadToolExecutor()
    executor.set_registry(MagicMock())
    assert executor._runs_on_caller("anything") is False


class TestQtDispatch:
    @pytest.fixture(scope="class")
    def qapp(self):
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def _call_from_worker(qapp, fn):
        box = {}
        worker = threading.Thread(target=lambda: box.update(value=fn()))
        worker.start()
        deadline = time.monotonic() + 5
        while worker.is_alive() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        worker.join(1)
        assert not worker.is_alive(), "worker never finished"
        return box["value"], worker.ident

    def test_ordinary_tool_runs_on_the_gui_thread(self, qapp):
        from freecad_ai.tools.executor_utils import QtMainThreadToolExecutor
        reg, seen = _thread_recording_registry()
        executor = QtMainThreadToolExecutor()
        executor.set_registry(reg)
        result, _ = self._call_from_worker(qapp, lambda: executor.execute("gui_tool", {}))
        assert result.success and seen["gui_tool"] == threading.get_ident()

    def test_main_thread_false_tool_runs_on_the_calling_thread(self, qapp):
        from freecad_ai.tools.executor_utils import QtMainThreadToolExecutor
        reg, seen = _thread_recording_registry()
        executor = QtMainThreadToolExecutor()
        executor.set_registry(reg)
        result, worker_ident = self._call_from_worker(
            qapp, lambda: executor.execute("worker_tool", {}))
        assert result.success and seen["worker_tool"] == worker_ident
```

- [ ] **Step 2: Run them to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_executor_utils.py`
Expected: FAIL with `TypeError: ToolDefinition.__init__() got an unexpected keyword argument 'main_thread'`.

- [ ] **Step 3: Implement**

`freecad_ai/tools/registry.py`, in `ToolDefinition` after `lazy_params`:

```python
    # False: the executor runs the handler on the calling worker thread instead
    # of the GUI thread. For handlers that wait a long time (a subprocess) and
    # touch FreeCAD only through executor_utils.run_on_main.
    main_thread: bool = True
```

`freecad_ai/tools/executor_utils.py`, in `MainThreadToolExecutor` after `set_registry`:

```python
    def _runs_on_caller(self, tool_name):
        """True for tools registered with main_thread=False."""
        tool = self._registry.get(tool_name) if self._registry is not None else None
        return getattr(tool, "main_thread", True) is False
```

In `QtMainThreadToolExecutor.execute`, change the direct-execution condition:

```python
            app = QtCore.QCoreApplication.instance()
            on_main = app and QtCore.QThread.currentThread() == app.thread()
            if on_main or self._runs_on_caller(tool_name):
                # On the main thread already (avoids deadlock), or a tool that
                # must not block it (main_thread=False)
```

(keep the three lines below it unchanged).

- [ ] **Step 4: Run the tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_executor_utils.py tests/unit/test_worker_render_per_client.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/registry.py freecad_ai/tools/executor_utils.py tests/unit/test_executor_utils.py
git commit -m "feat(tools): main_thread=False tools run on the calling thread

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `run_on_main` and `current_thread_interrupted`

**Files:**
- Modify: `freecad_ai/tools/executor_utils.py`
- Test: `tests/unit/test_executor_utils.py`

**Interfaces:**
- Produces: `run_on_main(fn: Callable[[], T]) -> T` (re-raises `fn`'s exception in the caller; calls `fn` directly without Qt, without a `QCoreApplication`, or on the GUI thread). `current_thread_interrupted() -> bool` (the calling `QThread`'s `isInterruptionRequested()`; False for plain threads and without Qt).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_executor_utils.py`:

```python
from freecad_ai.tools import executor_utils


class TestRunOnMain:
    @pytest.fixture(scope="class")
    def qapp(self):
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_runs_on_the_gui_thread_and_returns_the_value(self, qapp):
        value, _ = TestQtDispatch._call_from_worker(
            qapp, lambda: executor_utils.run_on_main(threading.get_ident))
        assert value == threading.get_ident()

    def test_reraises_in_the_caller(self, qapp):
        def boom():
            raise RuntimeError("no doc")

        def call():
            try:
                executor_utils.run_on_main(boom)
            except RuntimeError as e:
                return str(e)

        value, _ = TestQtDispatch._call_from_worker(qapp, call)
        assert value == "no doc"

    def test_on_the_gui_thread_it_calls_directly(self, qapp):
        assert executor_utils.run_on_main(threading.get_ident) == threading.get_ident()

    def test_without_qt_it_calls_directly(self, monkeypatch):
        monkeypatch.setattr(executor_utils, "_HAS_QT", False)
        assert executor_utils.run_on_main(lambda: 42) == 42

    def test_interruption_of_a_qthread_is_seen(self, qapp):
        from freecad_ai.ui.compat import QtCore

        class T(QtCore.QThread):
            def run(self):
                self.before = executor_utils.current_thread_interrupted()
                self.requestInterruption()
                self.after = executor_utils.current_thread_interrupted()

        t = T()
        t.start()
        assert t.wait(5000)
        assert (t.before, t.after) == (False, True)

    def test_plain_thread_is_never_interrupted(self, qapp):
        box = {}
        t = threading.Thread(
            target=lambda: box.update(v=executor_utils.current_thread_interrupted()))
        t.start()
        t.join(5)
        assert box["v"] is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_executor_utils.py -k "RunOnMain"`
Expected: FAIL with `AttributeError: module 'freecad_ai.tools.executor_utils' has no attribute 'run_on_main'`.

- [ ] **Step 3: Implement**

`freecad_ai/tools/executor_utils.py`: add `import threading` below `import logging`, then append at the end of the module:

```python
_caller = None
_caller_lock = threading.Lock()

if _HAS_QT:
    class _MainThreadCaller(QObject):
        """Runs callables on the GUI thread for run_on_main."""
        _call = Signal(object)

        def __init__(self):
            super().__init__()
            self._call.connect(self._run, Qt.QueuedConnection)

        def _run(self, box):
            try:
                box["value"] = box["fn"]()
            except BaseException as e:  # handed to the waiting thread
                box["error"] = e
            finally:
                box["done"].set()

        def call(self, fn):
            box = {"fn": fn, "done": threading.Event()}
            self._call.emit(box)
            box["done"].wait()
            if "error" in box:
                raise box["error"]
            return box.get("value")


def run_on_main(fn):
    """Call ``fn()`` on the GUI thread from any thread and return its result.

    For main_thread=False tool handlers, which run on a worker thread but
    still need FreeCAD's document API for a moment. Calls ``fn`` directly
    without Qt, without an application object, or on the GUI thread itself.
    """
    global _caller
    if not _HAS_QT:
        return fn()
    app = QtCore.QCoreApplication.instance()
    if app is None or QtCore.QThread.currentThread() == app.thread():
        return fn()
    with _caller_lock:
        if _caller is None:
            _caller = _MainThreadCaller()
            # Created on whichever thread got here first; queued calls must
            # be delivered on the GUI thread.
            _caller.moveToThread(app.thread())
    return _caller.call(fn)


def current_thread_interrupted():
    """True once the chat's Stop button asked the calling QThread to stop."""
    if not _HAS_QT:
        return False
    try:
        return bool(QtCore.QThread.currentThread().isInterruptionRequested())
    except Exception:
        return False
```

- [ ] **Step 4: Run the tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_executor_utils.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/executor_utils.py tests/unit/test_executor_utils.py
git commit -m "feat(tools): run_on_main and current_thread_interrupted helpers

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `core/headless.py` — run folders, launch, wait, kill, collect

**Files:**
- Create: `freecad_ai/core/headless.py`
- Test: `tests/unit/test_headless.py`

**Interfaces:**
- Consumes: `executor._build_child_script(..., mode="headless")` (Task 2); `freecad_ai.config.CONFIG_DIR` (read at call time).
- Produces:
  - `HeadlessRun` dataclass: `status: str` (`"ok" | "error" | "timeout" | "cancelled" | "crashed" | "no_result" | "launch_failed"`), `stdout: str`, `stderr: str`, `error: str` (human message for every non-ok status), `run_dir: str`, `exit_code: int | None`, `duration_s: float`.
  - `new_run_dir(base: str | None = None, keep: int = KEEP_RUNS) -> str`
  - `save_active_copy(path: str) -> str | None` (GUI thread only)
  - `run_headless(code, *, freecad_bin, run_dir, document_path, timeout, is_cancelled=lambda: False, launch=subprocess.Popen, clock=time.monotonic, sleep=time.sleep) -> HeadlessRun`
  - `cap_text(text: str, path: str, limit: int = OUTPUT_CAP) -> str`
  - Constants `KEEP_RUNS = 20`, `OUTPUT_CAP = 20_000`, `POLL_S = 0.5`, `KILL_GRACE_S = 3.0`, `TAIL_LINES = 20`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_headless.py`:

```python
"""core.headless: the separate FreeCAD process behind execute_code_headless (#114)."""

import json
import os
import re
import signal
from types import SimpleNamespace

import pytest

from freecad_ai.core import headless


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeProc:
    """Exits with ``code`` after ``polls`` polls; ``polls=None`` never exits."""

    def __init__(self, code=0, polls=0):
        self.pid = 424242
        self.returncode = None
        self._code, self._polls = code, polls

    def poll(self):
        if self.returncode is None and self._polls is not None:
            if self._polls <= 0:
                self.returncode = self._code
            self._polls -= 1
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = self._code
        return self.returncode


@pytest.fixture
def signals(monkeypatch):
    """Never signal a real process group; record and 'deliver' instead."""
    rec = SimpleNamespace(sent=[], procs=[])

    def killpg(pid, sig):
        rec.sent.append(sig)
        for p in rec.procs:
            if p.returncode is None:
                p.returncode = -sig

    monkeypatch.setattr(headless.os, "killpg", killpg)
    return rec


def _launcher(proc, files=None, signals=None, calls=None):
    def launch(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        for name, text in (files or {}).items():
            with open(os.path.join(kw["cwd"], name), "w", encoding="utf-8") as f:
                f.write(text)
        if signals is not None:
            signals.procs.append(proc)
        return proc
    return launch


def _run(tmp_path, proc, files=None, signals=None, timeout=600,
         is_cancelled=lambda: False, calls=None):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    clock = FakeClock()
    return headless.run_headless(
        "print(1)", freecad_bin="/opt/fc/bin/freecadcmd", run_dir=str(run_dir),
        document_path=None, timeout=timeout, is_cancelled=is_cancelled,
        launch=_launcher(proc, files, signals, calls), clock=clock, sleep=clock.sleep)


OK = {"result.json": json.dumps({"ok": True, "error": ""}), "output.log": "1\n"}


def test_success_reads_output_and_exit_code(tmp_path):
    calls = []
    run = _run(tmp_path, FakeProc(0, polls=3), OK, calls=calls)
    assert run.status == "ok" and run.stdout == "1\n" and run.exit_code == 0
    assert run.duration_s == pytest.approx(1.5)
    cmd, kw = calls[0]
    assert cmd == ["/opt/fc/bin/freecadcmd", "-c", os.path.join(run.run_dir, "script.py")]
    assert kw["cwd"] == run.run_dir and kw["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert kw["start_new_session"] is True


def test_writes_the_script_and_the_user_code(tmp_path):
    run = _run(tmp_path, FakeProc(0), OK)
    with open(os.path.join(run.run_dir, "user_code.py")) as f:
        assert f.read() == "print(1)"
    with open(os.path.join(run.run_dir, "script.py")) as f:
        assert "WORK_DIR = " + repr(run.run_dir) in f.read()


def test_script_exception_is_an_error_with_the_traceback(tmp_path):
    files = {"result.json": json.dumps({"ok": False, "error": "Traceback…ZeroDivisionError"}),
             "output.log": "before\n"}
    run = _run(tmp_path, FakeProc(0), files)
    assert run.status == "error" and "ZeroDivisionError" in run.error
    assert run.stdout == "before\n"


def test_timeout_kills_the_group_and_keeps_partial_output(tmp_path, signals):
    run = _run(tmp_path, FakeProc(polls=None), {"output.log": "half\n"},
               signals=signals, timeout=5)
    assert run.status == "timeout" and "timed out after 5 s" in run.error
    assert run.stdout == "half\n"
    assert signals.sent[0] == signal.SIGTERM and signals.sent[-1] == signal.SIGKILL


def test_stop_cancels(tmp_path, signals):
    asked = iter([False, False, True])
    run = _run(tmp_path, FakeProc(polls=None), {}, signals=signals,
               is_cancelled=lambda: next(asked, True))
    assert run.status == "cancelled" and "cancelled" in run.error.lower()
    assert signal.SIGTERM in signals.sent


def test_negative_return_code_is_a_crash(tmp_path):
    files = {"child.err": "\n".join(f"line {i}" for i in range(30))}
    run = _run(tmp_path, FakeProc(-signal.SIGSEGV), files)
    assert run.status == "crashed" and "SIGSEGV" in run.error
    assert "line 29" in run.error and "line 9\n" not in run.error  # last 20 lines


def test_appimage_style_128_plus_signal_is_a_crash(tmp_path):
    # Review focus 2: the AppImage runtime reports its child's SIGABRT as 134
    run = _run(tmp_path, FakeProc(128 + signal.SIGABRT), {})
    assert run.status == "crashed" and "SIGABRT" in run.error


def test_exit_without_result_reports_code_and_stderr_tail(tmp_path):
    run = _run(tmp_path, FakeProc(1), {"child.err": "ImportError: no FreeCAD\n"})
    assert run.status == "no_result"
    assert "code 1" in run.error and "ImportError: no FreeCAD" in run.error


def test_launch_failure_is_reported(tmp_path):
    def launch(cmd, **kw):
        raise PermissionError("not executable")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = headless.run_headless("pass", freecad_bin="/x", run_dir=str(run_dir),
                                document_path=None, timeout=5, launch=launch)
    assert run.status == "launch_failed" and "not executable" in run.error


def test_cap_text_keeps_the_tail_and_names_the_file():
    text = "a" * 10 + "b" * 20
    capped = headless.cap_text(text, "/r/output.log", limit=20)
    assert capped.endswith("b" * 20) and "/r/output.log" in capped
    assert "10 characters" in capped
    assert headless.cap_text("short", "/r/x", limit=20) == "short"


def test_new_run_dir_name_and_pruning(tmp_path):
    base = tmp_path / "headless"
    base.mkdir()
    for i in range(25):
        (base / f"20260101-0000{i:02d}-abcdef").mkdir()
    path = headless.new_run_dir(str(base))
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", os.path.basename(path))
    runs = sorted(os.listdir(base))
    assert len(runs) == 20 and "20260101-000024-abcdef" in runs
    assert "20260101-000005-abcdef" not in runs


def test_pruning_never_touches_foreign_folders(tmp_path):
    # Review focus 3
    base = tmp_path / "headless"
    base.mkdir()
    (base / "my-results").mkdir()
    for i in range(25):
        (base / f"20260101-0000{i:02d}-abcdef").mkdir()
    headless.new_run_dir(str(base))
    assert (base / "my-results").is_dir()


def test_new_run_dir_defaults_under_config_dir(tmp_config_dir):
    import freecad_ai.config as config_mod
    path = headless.new_run_dir()
    assert os.path.dirname(path) == os.path.join(config_mod.CONFIG_DIR, "headless")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_headless.py`
Expected: FAIL with `ImportError: cannot import name 'headless' from 'freecad_ai.core'`.

- [ ] **Step 3: Implement `freecad_ai/core/headless.py`**

```python
"""Run Python in a separate FreeCAD console process (#114).

The child works on a copy of the active document; the GUI document is never
changed. Results come back as what the code printed, whether it raised, and
the files it wrote into its run folder. Used by execute_code_headless, whose
handler runs on a worker thread (main_thread=False) so the wait here does not
freeze the GUI.
"""

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass

KEEP_RUNS = 20
OUTPUT_CAP = 20_000
POLL_S = 0.5
KILL_GRACE_S = 3.0
TAIL_LINES = 20

_RUN_NAME = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")


@dataclass
class HeadlessRun:
    status: str  # ok | error | timeout | cancelled | crashed | no_result | launch_failed
    stdout: str
    stderr: str
    error: str
    run_dir: str
    exit_code: int | None
    duration_s: float


def new_run_dir(base=None, keep=KEEP_RUNS):
    """Create a fresh run folder, first pruning old runs to ``keep - 1``.

    Only folders named like a run are ever deleted.
    """
    if base is None:
        from .. import config
        base = os.path.join(config.CONFIG_DIR, "headless")
    os.makedirs(base, exist_ok=True)
    runs = sorted(d for d in os.listdir(base)
                  if _RUN_NAME.fullmatch(d) and os.path.isdir(os.path.join(base, d)))
    for old in runs[:max(0, len(runs) - (keep - 1))]:
        shutil.rmtree(os.path.join(base, old), ignore_errors=True)
    path = os.path.join(base, time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3))
    os.makedirs(path)
    return path


def save_active_copy(path):
    """Save a copy of the active document (unsaved edits included) to ``path``.

    Must run on the GUI thread. Returns ``path``, or None with no document.
    """
    from .active_document import resolve_active_document
    doc = resolve_active_document()
    if doc is None:
        return None
    doc.saveCopy(path)
    return path


def cap_text(text, path, limit=OUTPUT_CAP):
    """Keep the last ``limit`` characters, noting where the full text is."""
    if len(text) <= limit:
        return text
    return "[first {} characters cut; full text in {}]\n{}".format(
        len(text) - limit, path, text[-limit:])


def run_headless(code, *, freecad_bin, run_dir, document_path, timeout,
                 is_cancelled=lambda: False, launch=subprocess.Popen,
                 clock=time.monotonic, sleep=time.sleep):
    """Run ``code`` in a FreeCAD console child and wait for it."""
    from .executor import _build_child_script

    script = os.path.join(run_dir, "script.py")
    with open(os.path.join(run_dir, "user_code.py"), "w", encoding="utf-8") as f:
        f.write(code)
    with open(script, "w", encoding="utf-8") as f:
        f.write(_build_child_script(
            code, document_path=document_path,
            result_path=os.path.join(run_dir, "result.json"), mode="headless"))

    if os.name == "posix":
        # Own process group: an AppImage runs the real binary as a grandchild,
        # which killing the direct child alone would leave running.
        group = {"start_new_session": True}
    else:
        group = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}

    start = clock()
    stopped = None
    with open(os.path.join(run_dir, "child.out"), "wb") as out, \
            open(os.path.join(run_dir, "child.err"), "wb") as err:
        try:
            proc = launch([freecad_bin, "-c", script], cwd=run_dir, stdout=out,
                          stderr=err, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                          **group)
        except OSError as e:
            return HeadlessRun("launch_failed", "", "",
                               "Could not start {}: {}".format(freecad_bin, e),
                               run_dir, None, 0.0)
        while proc.poll() is None:
            if clock() - start >= timeout:
                stopped = "timeout"
            elif is_cancelled():
                stopped = "cancelled"
            if stopped:
                _kill_group(proc, clock, sleep)
                break
            sleep(POLL_S)
    return _collect(run_dir, proc.returncode, stopped, clock() - start, timeout)


def _kill_group(proc, clock, sleep):
    """SIGTERM the child's process group, then SIGKILL whatever is left."""
    def send(sig):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except OSError:  # already gone
            pass

    send(signal.SIGTERM)
    deadline = clock() + KILL_GRACE_S
    while proc.poll() is None and clock() < deadline:
        sleep(0.1)
    # Unconditional: the direct child may have exited on SIGTERM while a
    # grandchild ignored it.
    send(getattr(signal, "SIGKILL", signal.SIGTERM))
    proc.wait()


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _signal_name(num):
    try:
        return signal.Signals(num).name
    except ValueError:
        return "signal {}".format(num)


def _collect(run_dir, returncode, stopped, duration, timeout):
    stdout = _read(os.path.join(run_dir, "output.log"))
    stderr = _read(os.path.join(run_dir, "stderr.log"))
    tail = "\n".join(_read(os.path.join(run_dir, "child.err")).splitlines()[-TAIL_LINES:])
    try:
        with open(os.path.join(run_dir, "result.json"), encoding="utf-8") as f:
            res = json.load(f)
    except (OSError, ValueError):
        res = None

    sig = None
    if returncode is not None and returncode < 0:
        sig = -returncode
    elif res is None and os.name == "posix" and returncode and 128 < returncode <= 192:
        sig = returncode - 128  # how an AppImage runtime reports a dead child

    if stopped == "timeout":
        status, error = stopped, (
            "timed out after {} s; the process was killed. Output so far is "
            "included; full logs in {}".format(timeout, run_dir))
    elif stopped == "cancelled":
        status, error = stopped, "Cancelled; the process was killed."
    elif res is not None:
        status = "ok" if res.get("ok") else "error"
        error = "" if status == "ok" else res.get("error", "")
    elif sig is not None:
        status, error = "crashed", (
            "The FreeCAD process crashed ({}); FreeCAD itself is unaffected.\n"
            "Last lines of its stderr:\n{}".format(_signal_name(sig), tail))
    else:
        status, error = "no_result", (
            "The FreeCAD process exited with code {} without reporting a result.\n"
            "Last lines of its stderr:\n{}".format(returncode, tail))
    return HeadlessRun(status, stdout, stderr, error, run_dir, returncode,
                       round(duration, 1))
```

- [ ] **Step 4: Run the tests**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_headless.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/core/headless.py tests/unit/test_headless.py
git commit -m "feat(core): headless runner - run folders, process-group kill, results

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The `execute_code_headless` tool

**Files:**
- Modify: `freecad_ai/core/executor.py` (`FREECAD_CMD_SEARCHED` constant above `_find_freecad_cmd`)
- Modify: `freecad_ai/tools/freecad_tools.py` (`_headless_tool_result`, `_handle_execute_code_headless`, `EXECUTE_CODE_HEADLESS` after `EXECUTE_CODE`; `EXECUTE_CODE_HEADLESS,` after `EXECUTE_CODE,` in `ALL_TOOLS`)
- Test: `tests/unit/test_execute_code_headless.py`

**Interfaces:**
- Consumes: `headless.new_run_dir`, `headless.save_active_copy`, `headless.run_headless`, `headless.cap_text`, `HeadlessRun` (Task 5); `run_on_main`, `current_thread_interrupted` (Task 4); `ToolDefinition.main_thread` (Task 3); `executor._find_freecad_cmd`, `executor._validate_code`.
- Produces: tool `execute_code_headless` with parameters `code` (string, required) and `timeout` (integer, optional, default 600); `ToolResult.data == {"run_dir", "exit_code", "duration_s"}` whenever the child was started.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_execute_code_headless.py`:

```python
"""execute_code_headless: the tool handler (#114)."""

from types import SimpleNamespace

import pytest

import freecad_ai.core.executor as ex
from freecad_ai.core import headless
from freecad_ai.tools import executor_utils
from freecad_ai.tools import freecad_tools as ft


def _run(status="ok", stdout="", stderr="", error="", rc=0, run_dir="/r"):
    return headless.HeadlessRun(status, stdout, stderr, error, run_dir, rc, 1.2)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Binary found, safe mode, no document, the child faked."""
    import freecad_ai.core.dangerous_mode as dm
    state = {"calls": [], "dangerous": False, "run": _run(stdout="1000\n")}
    monkeypatch.setattr(dm, "get_dangerous_mode",
                        lambda: SimpleNamespace(active=state["dangerous"]))
    monkeypatch.setattr(ex, "_find_freecad_cmd", lambda: "/opt/fc/bin/freecadcmd")
    monkeypatch.setattr(headless, "new_run_dir", lambda: str(tmp_path))
    monkeypatch.setattr(headless, "save_active_copy", lambda path: None)

    def fake_run(code, **kw):
        state["calls"].append({"code": code, **kw})
        return state["run"]

    monkeypatch.setattr(headless, "run_headless", fake_run)
    return state


def test_success_returns_output_and_run_data(env, tmp_path):
    result = ft._handle_execute_code_headless("print(1000)")
    assert result.success and result.output == "1000"
    assert result.data == {"run_dir": "/r", "exit_code": 0, "duration_s": 1.2}
    call = env["calls"][0]
    assert call["timeout"] == 600 and call["document_path"] is None
    assert call["freecad_bin"] == "/opt/fc/bin/freecadcmd"
    assert call["run_dir"] == str(tmp_path)
    assert call["is_cancelled"] is executor_utils.current_thread_interrupted


def test_no_output_says_so(env):
    env["run"] = _run(stdout="")
    assert ft._handle_execute_code_headless("x = 1").output == "Code ran without output"


def test_stderr_is_appended(env):
    env["run"] = _run(stdout="a\n", stderr="careful\n")
    assert ft._handle_execute_code_headless("x").output == "a\n--- stderr ---\ncareful"


def test_failure_keeps_output_and_error(env):
    env["run"] = _run("error", stdout="before\n", error="Traceback…ZeroDivisionError")
    result = ft._handle_execute_code_headless("1/0")
    assert not result.success and result.output == "before"
    assert "ZeroDivisionError" in result.error and result.data["run_dir"] == "/r"


@pytest.mark.parametrize("status", ["timeout", "cancelled", "crashed", "no_result",
                                    "launch_failed"])
def test_every_failure_status_is_unsuccessful(env, status):
    env["run"] = _run(status, error="msg for " + status)
    result = ft._handle_execute_code_headless("x")
    assert not result.success and result.error == "msg for " + status


def test_output_is_capped(env):
    env["run"] = _run(stdout="x" * 25_000)
    out = ft._handle_execute_code_headless("x").output
    assert len(out) < 20_200 and "output.log" in out


def test_active_document_copy_is_passed_on(env, monkeypatch):
    seen = {}

    def save(path):
        seen["path"] = path
        return path

    monkeypatch.setattr(headless, "save_active_copy", save)
    ft._handle_execute_code_headless("x")
    assert seen["path"].endswith("input.FCStd")
    assert env["calls"][0]["document_path"] == seen["path"]


def test_copy_failure_is_reported(env, monkeypatch):
    def save(path):
        raise OSError("disk full")

    monkeypatch.setattr(headless, "save_active_copy", save)
    result = ft._handle_execute_code_headless("x")
    assert not result.success and result.error == "Could not copy the document: disk full"
    assert not env["calls"]


def test_copy_runs_through_run_on_main(env, monkeypatch):
    used = []
    monkeypatch.setattr(executor_utils, "run_on_main", lambda fn: used.append(fn) or fn())
    ft._handle_execute_code_headless("x")
    assert used


def test_missing_binary_names_the_places_searched(env, monkeypatch):
    monkeypatch.setattr(ex, "_find_freecad_cmd", lambda: "")
    result = ft._handle_execute_code_headless("x")
    assert not result.success and "freecadcmd" in result.error
    assert "AppImage" in result.error and not env["calls"]


def test_validation_refuses_in_safe_mode(env):
    result = ft._handle_execute_code_headless("import subprocess")
    assert not result.success and "subprocess" in result.error and not env["calls"]


def test_dangerous_mode_skips_validation(env):
    env["dangerous"] = True
    assert ft._handle_execute_code_headless("import subprocess").success


@pytest.mark.parametrize("given, used", [("60", 60), (0, 1), (-5, 1), (12.7, 12)])
def test_timeout_is_coerced(env, given, used):
    # Review focus 4
    ft._handle_execute_code_headless("x", timeout=given)
    assert env["calls"][0]["timeout"] == used


def test_junk_timeout_is_refused(env):
    result = ft._handle_execute_code_headless("x", timeout="soon")
    assert not result.success and "timeout" in result.error and not env["calls"]


def test_registered_off_the_main_thread():
    tool = next(t for t in ft.ALL_TOOLS if t.name == "execute_code_headless")
    assert tool.main_thread is False and tool.category == "general"
    assert [(p.name, p.required) for p in tool.parameters] == [("code", True),
                                                               ("timeout", False)]
    for phrase in ("WORK_DIR", "execute_code", "not changed", "60 s"):
        assert phrase in tool.description
```

- [ ] **Step 2: Run them to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_execute_code_headless.py`
Expected: FAIL with `AttributeError: module 'freecad_ai.tools.freecad_tools' has no attribute '_handle_execute_code_headless'`.

- [ ] **Step 3: Add the searched-places message**

`freecad_ai/core/executor.py`, directly above `def _find_freecad_cmd`:

```python
# Keep in step with _find_freecad_cmd below.
FREECAD_CMD_SEARCHED = (
    "No FreeCAD console binary found. Searched: bin/freecadcmd next to the "
    "running FreeCAD, ~/bin/FreeCAD*.AppImage, /usr/local/bin/FreeCAD*.AppImage, "
    "/usr/bin/freecadcmd, /usr/bin/freecad, /usr/local/bin/freecad, "
    "~/bin/freecad, and freecadcmd/freecad on PATH.")
```

- [ ] **Step 4: Add the handler and the tool**

`freecad_ai/tools/freecad_tools.py`, directly after the `EXECUTE_CODE = ToolDefinition(...)` block:

```python
# ── execute_code_headless ───────────────────────────────────

def _headless_tool_result(run):
    """Turn a core.headless.HeadlessRun into a ToolResult."""
    from ..core.headless import cap_text
    parts = [cap_text(run.stdout, os.path.join(run.run_dir, "output.log")).strip()]
    if run.stderr.strip():
        parts.append("--- stderr ---\n" + cap_text(
            run.stderr, os.path.join(run.run_dir, "stderr.log")).strip())
    output = "\n".join(p for p in parts if p)
    data = {"run_dir": run.run_dir, "exit_code": run.exit_code,
            "duration_s": run.duration_s}
    if run.status == "ok":
        return ToolResult(success=True, output=output or "Code ran without output",
                          data=data)
    return ToolResult(success=False, output=output, data=data, error=run.error)


def _handle_execute_code_headless(code: str, timeout=600) -> ToolResult:
    """Run code in a separate FreeCAD console process (#114).

    Registered with main_thread=False: this runs on the calling worker thread
    and only the document copy goes to the GUI thread.
    """
    from ..core import executor, headless
    from ..core.dangerous_mode import get_dangerous_mode
    from . import executor_utils

    try:
        timeout = max(1, int(float(timeout)))
    except (TypeError, ValueError):
        return ToolResult(success=False, output="",
                          error="timeout must be a number of seconds, got {!r}".format(timeout))
    if not get_dangerous_mode().active:
        warnings = executor._validate_code(code)
        if warnings:
            return ToolResult(success=False, output="",
                              error="Static validation failed:\n" + "\n".join(warnings))
    freecad_bin = executor._find_freecad_cmd()
    if not freecad_bin:
        return ToolResult(success=False, output="", error=executor.FREECAD_CMD_SEARCHED)

    run_dir = headless.new_run_dir()
    copy_path = os.path.join(run_dir, "input.FCStd")
    try:
        document_path = executor_utils.run_on_main(
            lambda: headless.save_active_copy(copy_path))
    except Exception as e:
        return ToolResult(success=False, output="",
                          error="Could not copy the document: {}".format(e))
    run = headless.run_headless(
        code, freecad_bin=freecad_bin, run_dir=run_dir, document_path=document_path,
        timeout=timeout, is_cancelled=executor_utils.current_thread_interrupted)
    return _headless_tool_result(run)


EXECUTE_CODE_HEADLESS = ToolDefinition(
    name="execute_code_headless",
    description=(
        "Run Python in a separate FreeCAD console process, for long jobs "
        "(heavy booleans, batch exports, analyses) that would freeze the GUI "
        "under execute_code. It works on a COPY of the active document "
        "(unsaved edits included; App.ActiveDocument), or on a new empty "
        "document if none is open. The open document is not changed: use "
        "execute_code to modify the model. Write result files under WORK_DIR "
        "(the run folder, also the working directory); they stay there after "
        "the call. Returns what the code printed. FreeCADGui is a stub: view "
        "calls do nothing and Gui.Selection does not exist. "
        "A crash in the code cannot take FreeCAD down. timeout defaults to 600 "
        "seconds; pass a smaller one for quick jobs, because some MCP clients "
        "give up after about 60 s."),
    category="general",
    parameters=[
        ToolParam("code", "string", "Python code to run"),
        ToolParam("timeout", "integer",
                  "Seconds before the process is killed (default 600)",
                  required=False, default=600),
    ],
    handler=_handle_execute_code_headless,
    main_thread=False,
)
```

In `ALL_TOOLS`, add `    EXECUTE_CODE_HEADLESS,` on the line after `    EXECUTE_CODE,`.

- [ ] **Step 5: Run the new tests, then the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest -q tests/unit/test_execute_code_headless.py`
Expected: all PASS.

Run: `env PYTHONPATH= .venv/bin/pytest -q --ignore=tests/unit/test_document_attach.py`
Expected: all PASS (the count is 2417 plus this branch's new tests). If a test pins the tool list or the tool count, update it to include `execute_code_headless`.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/core/executor.py freecad_ai/tools/freecad_tools.py tests/unit/test_execute_code_headless.py
git commit -m "feat(tools): execute_code_headless runs code in a separate FreeCAD process

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Integration tests against the real FreeCAD console

**Files:**
- Create: `tests/integration/test_headless_integration.py`

**Interfaces:**
- Consumes: `headless.new_run_dir`, `headless.run_headless`, `executor._find_freecad_cmd`.

- [ ] **Step 1: Write the tests**

```python
"""execute_code_headless against a real FreeCAD console binary (#114)."""

import os
import sys

import pytest

from freecad_ai.core import headless
from freecad_ai.core.executor import _find_freecad_cmd

pytestmark = pytest.mark.integration


@pytest.fixture
def freecad_bin():
    found = _find_freecad_cmd()
    if not found:
        pytest.skip("no FreeCAD console binary")
    return found


def _go(tmp_path, freecad_bin, code, timeout=120, document_path=None):
    run_dir = headless.new_run_dir(str(tmp_path / "headless"))
    return headless.run_headless(code, freecad_bin=freecad_bin, run_dir=run_dir,
                                 document_path=document_path, timeout=timeout)


def test_box_volume_and_a_result_file(tmp_path, freecad_bin):
    code = ("import Part\n"
            "b = App.ActiveDocument.addObject('Part::Box', 'B')\n"
            "App.ActiveDocument.recompute()\n"
            "print('volume', round(b.Shape.Volume))\n"
            "b.Shape.exportStep(WORK_DIR + '/box.step')\n"
            "print('Ø ok')\n")
    run = _go(tmp_path, freecad_bin, code)
    assert run.status == "ok", run.error
    assert "volume 1000" in run.stdout and "Ø ok" in run.stdout
    assert os.path.getsize(os.path.join(run.run_dir, "box.step")) > 0


def test_opens_the_given_document_without_saving_it(tmp_path, freecad_bin):
    src = tmp_path / "src.FCStd"
    make = ("App.ActiveDocument.addObject('Part::Box', 'B')\n"
            "App.ActiveDocument.saveAs({!r})\n".format(str(src)))
    assert _go(tmp_path, freecad_bin, make).status == "ok"
    before = src.read_bytes()
    run = _go(tmp_path, freecad_bin,
              "App.ActiveDocument.addObject('Part::Cylinder', 'C')\n"
              "print(sorted(o.Name for o in App.ActiveDocument.Objects))\n",
              document_path=str(src))
    assert run.status == "ok", run.error
    assert "['B', 'C']" in run.stdout
    assert src.read_bytes() == before


@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc")
def test_timeout_leaves_no_process_behind(tmp_path, freecad_bin):
    run = _go(tmp_path, freecad_bin, "import time\nprint('start')\ntime.sleep(120)\n",
              timeout=15)
    assert run.status == "timeout" and "start" in run.stdout
    script = os.path.join(run.run_dir, "script.py").encode()
    alive = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if script in f.read():
                    alive.append(pid)
        except OSError:
            pass
    assert alive == []


def test_abort_is_reported_as_a_crash(tmp_path, freecad_bin):
    run = _go(tmp_path, freecad_bin, "import os\nos.abort()\n")
    assert run.status == "crashed" and "SIGABRT" in run.error, (run.exit_code, run.error)
```

The timeout test gives the child 15 s so FreeCAD finishes starting before `sleep` begins; `start` in the output proves the code was reached.

- [ ] **Step 2: Run them**

Run: `env PYTHONPATH= .venv/bin/pytest -q -m integration tests/integration/test_headless_integration.py`
Expected: 4 passed. If `test_abort_is_reported_as_a_crash` fails, the assertion message shows the real exit code: fix the crash classification in `headless._collect` with a unit test in `tests/unit/test_headless.py` reproducing that code first.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_headless_integration.py
git commit -m "test(headless): integration tests against the FreeCAD console

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Live GUI check over MCP, docs, PR

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Added`)
- Modify (wiki repo, commit locally, do not push): `/home/alf/Projects/programming/misc/freecad-ai-wiki/Tool-Reference.md`

- [ ] **Step 1: Live check — the GUI answers while a headless run sleeps**

Save as `$SCRATCH/live_headless.sh` (`$SCRATCH` = the session scratchpad) and run it with `bash`:

```bash
#!/usr/bin/env bash
set -uo pipefail
PORT=30000
REPO=/home/alf/Projects/programming/misc/freecad-ai
URL="http://127.0.0.1:$PORT/mcp"
post() { curl -sS -X POST "$URL" -H 'Content-Type: application/json' -d "$1"; }
call() { post "{\"jsonrpc\":\"2.0\",\"id\":$1,\"method\":\"tools/call\",\"params\":{\"name\":\"$2\",\"arguments\":$3}}"; }

MCP_PORT="$PORT" setsid xvfb-run -a env QT_QPA_PLATFORM=xcb \
  ~/bin/freecad "$REPO/mcp_server_http.py" >/tmp/live-headless.log 2>&1 &
FCPID=$!
cleanup() {
  kill -TERM -"$FCPID" 2>/dev/null
  for _ in $(seq 10); do kill -0 -"$FCPID" 2>/dev/null || break; sleep 1; done
  kill -KILL -"$FCPID" 2>/dev/null
}
trap cleanup EXIT INT TERM
for _ in $(seq 60); do ss -ltn | grep -q ":$PORT " && break; sleep 1; done

echo "1. an unsaved box in the GUI document"
call 1 create_primitive '{"shape_type":"box","body_name":""}' | head -c 300; echo

echo "2. headless run: sees the box, adds a cylinder, sleeps 15 s"
CODE='import time\nApp.ActiveDocument.addObject(\"Part::Cylinder\",\"HeadlessCyl\")\nprint(sorted(o.Name for o in App.ActiveDocument.Objects))\ntime.sleep(15)\nprint(\"done\")'
( time call 2 execute_code_headless "{\"code\":\"$CODE\",\"timeout\":60}" ) >/tmp/live-headless-run.txt 2>&1 &
RUNPID=$!
sleep 4

echo "3. the GUI answers meanwhile (expect well under 2 s)"
time call 3 get_document_state '{}' | head -c 400; echo
wait "$RUNPID"
echo "4. headless result"; cat /tmp/live-headless-run.txt
echo "5. GUI document unchanged (no HeadlessCyl)"
call 4 get_document_state '{}' | grep -o 'HeadlessCyl' || echo "no HeadlessCyl: ok"
```

Expected: step 3's `real` time is about 1 s or less while step 2's is about 15–20 s; step 4 shows the box and `HeadlessCyl` in the child's object list plus `done`; step 5 prints `no HeadlessCyl: ok`. Paste the timings into the PR description.

- [ ] **Step 2: CHANGELOG**

Under `## [Unreleased]` → `### Added`, append:

```markdown
- **`execute_code_headless` runs long Python jobs in a separate FreeCAD
  process** (#114). Heavy booleans, batch exports or analyses no longer
  freeze the GUI, and a crash in the code cannot take FreeCAD down. The
  process works on a copy of the active document (unsaved edits included)
  and never changes the open one; results come back as printed output plus
  any files written under `WORK_DIR`, kept in the last 20 run folders under
  the config directory's `headless/`. Default timeout 600 s; the chat's Stop
  button ends a run. Verified on Linux only: the macOS and Windows code paths
  exist but are untested.
```

- [ ] **Step 3: Wiki section (local commit only)**

In `/home/alf/Projects/programming/misc/freecad-ai-wiki/Tool-Reference.md`, between the `---` that closes the `execute_code` entry and `### run_macro`, add (followed by its own `---` line):

```markdown
### execute_code_headless

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `code` | string | **Yes** | -- | Python code to run |
| `timeout` | integer | No | 600 | Seconds before the process is killed |

Runs Python in a separate FreeCAD console process. Use it instead of
`execute_code` for jobs that take long enough to freeze the GUI (heavy
booleans, batch exports, analyses), or that might crash FreeCAD.

- Works on a **copy** of the active document (unsaved edits included); the
  open document is never changed. To change the model, use `execute_code`.
- Write result files under `WORK_DIR`. Each run has its own folder under
  `<config dir>/headless/`; the last 20 are kept.
- `timeout` (default 600 s) kills the process, and so does the chat's Stop
  button. Some MCP clients give up after about 60 s, so pass a smaller
  timeout for quick jobs.
- `FreeCADGui` is the same stub as in the `execute_code` sandbox pre-check:
  view calls do nothing, and `Gui.Selection` does not exist.
```

```bash
git -C /home/alf/Projects/programming/misc/freecad-ai-wiki add Tool-Reference.md
git -C /home/alf/Projects/programming/misc/freecad-ai-wiki commit -m "docs: execute_code_headless (#114)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Full suite, commit, push, PR**

Run: `env PYTHONPATH= .venv/bin/pytest -q --ignore=tests/unit/test_document_attach.py`
Expected: all PASS.

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): execute_code_headless (#114)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push -u origin feat/114-headless-exec
gh pr create --base master --title "feat(tools): execute_code_headless - run code in a separate FreeCAD process (#114)" --body "<summary, the spec's planning refinements, unit/integration counts, live timings>

Closes #114

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

The PR body is written from the actual test output and live timings; never fill in counts before running.
