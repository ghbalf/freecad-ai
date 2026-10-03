# Headless code execution (#114)

**Goal:** long-running Python (heavy booleans, batch exports, and later FEM
solves, #113) runs in a separate FreeCAD console process, so the FreeCAD GUI
stays responsive and a crash in the script cannot take FreeCAD down.
**Follow-up that depends on this:** #113 `run_fem_analysis`.

## Decisions (maintainer, 2026-10-03)

1. **Copy in, files out.** The child works on a copy of the active document
   (including unsaved edits). The GUI document is never modified. Results
   come back as output text, exit status, and files the script writes.
   Heavy edits to the open model still go through `execute_code`.
2. **No `reload_document`.** Proposed in the issue, but nothing changes the
   GUI document, so there is nothing to reload.
3. **Approach A: a per-tool `main_thread` flag.** Tools marked
   `main_thread=False` run on the calling worker thread instead of being
   dispatched to the GUI thread. Rejected: B (nested event loop while
   waiting: re-entrancy hazards) and C (async job registry: deferred by
   the issue until headless proves insufficient).

## What exists today

- **Every tool call runs on the GUI thread.** `QtMainThreadToolExecutor`
  (`tools/executor_utils.py`) dispatches each call from the chat worker or
  the MCP server thread to the GUI thread and blocks until it returns. A
  handler that waits on a subprocess therefore freezes FreeCAD just as long
  as running the code in-process would.
- **`_find_freecad_cmd()`** (`core/executor.py`) finds the console binary
  shipped with the *running* FreeCAD (`getHomePath()/bin/freecadcmd`),
  falling back to AppImages and PATH.
- **`_sandbox_test(code, timeout, document_path)`** already runs code in a
  child: a harness opens the document (or a new one), installs a no-op
  `FreeCADGui` stub (the real module segfaults the console binary when
  Arch/Draft pull in PySide), gates console warnings so fd 2 carries
  errors only (#82/#83), runs the code, writes a JSON result file, closes
  all documents, and ends with `os._exit(0)` (some builds never exit after
  `-c` on an opened document, #14). It is a pass/fail pre-check: it
  discards the output, and when no binary is found it lets code through.
- The sandbox copies the **saved file** (`doc.FileName`), so unsaved edits
  are not in its copy.
- `subprocess.run(timeout=...)` kills only the direct child. An AppImage
  runs the real binary as a grandchild, so a timeout can leave it running.
- The chat Stop button calls `requestInterruption()` on the `_LLMWorker`
  QThread.

## Design

### 1. `ToolDefinition.main_thread`

`main_thread: bool = True` on `ToolDefinition`. `QtMainThreadToolExecutor.
execute` looks the tool up in its registry and, when the flag is False,
runs `_do_execute_sync` directly on the calling thread. Unknown tools and
all existing tools keep today's behaviour.

`run_on_main(fn)` in `executor_utils.py` runs a callable on the GUI thread
and returns its result (or re-raises its exception) using the same
signal + wait-condition pattern. Called on the GUI thread, it calls `fn`
directly. Without Qt (unit tests, plain Python) it calls `fn` directly.

### 2. `execute_code_headless(code, timeout=600)`

Tool category `general` (as `execute_code`), available in chat and over MCP,
`main_thread=False`.

1. **Static validation** with `_validate_code`, unless Dangerous mode is
   on. A failure is refused with the warnings, as in `execute_code`.
2. **Binary:** `_find_freecad_cmd()`. If none is found, error listing what
   was searched. Unlike the sandbox, headless cannot fall back to letting
   the code through.
3. **Run folder:** `CONFIG_DIR/headless/<YYYYmmdd-HHMMSS>-<6 hex>/`. Before
   creating it, delete all but the newest 19 existing run folders, so at
   most 20 exist after the new one is made.
4. **Document copy** via `run_on_main`: if there is an active document,
   `doc.saveCopy(<run>/input.FCStd)`. That includes unsaved edits and works
   for documents never saved. With no active document, the child starts
   from a new empty document.
5. **Child:** the harness (section 3) runs with
   `[bin, "-c", <run>/script.py]`, `start_new_session=True`,
   `QT_QPA_PLATFORM=offscreen`, stdout and stderr to files in the run
   folder. The handler polls every 0.5 s until the process ends, the
   timeout passes, or the calling QThread reports
   `isInterruptionRequested()` (chat Stop; the MCP server thread is a plain
   thread and only times out). On timeout or Stop it sends `SIGTERM` to the
   process group, then `SIGKILL` after 3 s.
6. **Result.** `success` is True only when the user code completed without
   raising. `output` holds the user code's stdout (and stderr, when
   non-empty), capped at 20,000 characters each with a note that the full
   text is in `<run>/output.log`. `data` = `{"run_dir", "exit_code",
   "duration_s"}`. Messages for the other outcomes are in "Error handling".

The tool description says: the open document is not changed; write result
files under `WORK_DIR`; use `execute_code` to modify the model; pass a
smaller `timeout` for quick jobs because some MCP clients give up after
about 60 s.

### 3. One harness, two users

The harness body in `_sandbox_test` is moved into
`_build_child_script(code, *, document_path, result_path, mode)`.
`mode="sandbox"` produces today's script exactly (so all sandbox tests
pass unchanged). `mode="headless"` differs in three ways:

- `WORK_DIR` (the run folder) is defined for the user code.
- The user code's `sys.stdout` / `sys.stderr` are redirected into buffers
  that go into the result JSON and `<run>/output.log`, so FreeCAD's own
  console noise stays out of the result.
- The object-problem snapshot and console-error baselining of the sandbox
  are not used: headless reports what the script printed and whether it
  raised, not a safety verdict.

Both modes keep the `FreeCADGui` stub, `closeDocument` cleanup and the
final `os._exit(0)`.

## Error handling

| Situation | Result |
|---|---|
| No console binary | `success=False`, names the places searched |
| Validation fails (not Dangerous mode) | Refused with the warnings |
| Saving the copy fails | `success=False`, `Could not copy the document: <e>` |
| Script raises | `success=False`, the child's traceback plus its stdout |
| Timeout | Process group killed; `timed out after N s` plus partial stdout |
| Stop in chat | Process group killed; `cancelled` plus partial stdout |
| Killed by a signal (segfault, abort) | `crashed (SIGSEGV)` plus the last 20 lines of stderr; FreeCAD keeps running |
| Exits without a result file | `success=False`, exit code plus the last 20 lines of stderr |

## Testing

**Unit:**
- The executor runs a `main_thread=False` tool on the calling thread and
  still dispatches an ordinary tool to the GUI thread. `run_on_main`
  returns values and re-raises exceptions.
- The handler, with the child launcher faked: success, script exception,
  timeout, Stop, crash signal, no result file, missing binary, refused
  validation, Dangerous mode skipping validation, output capping, and
  pruning to 20 run folders.
- `_build_child_script(mode="sandbox")` is byte-identical to the harness
  before the refactor. The existing sandbox tests pass unchanged.

**Integration (`-m integration`, real AppImage):**
- A script builds a box in the copy and prints its volume. The output
  holds the volume and the GUI-side document file is unchanged.
- `time.sleep` past the timeout is killed and leaves no FreeCAD process
  behind.
- `os.abort()` in the child is reported as a crash.

**Live (Xvfb, over MCP):** start `execute_code_headless` with a 15 s
`time.sleep`, and while it runs call `get_document_state`. The second call
answering within about a second proves the GUI is not frozen.

## Docs

- CHANGELOG `Added` entry.
- Wiki: a short section on when to use `execute_code_headless` versus
  `execute_code` (pushed together with the next release, like the other
  wiki changes).

## Out of scope

- Async jobs with a job ID and status polling (issue item 3).
- `reload_document` (decision 2).
- Changing the GUI document from the child.
- Running FEM (#113, builds on this).
- macOS and Windows: the code paths exist (`_find_freecad_cmd` already
  covers them, and process groups fall back to killing the process on
  Windows) but ship untested.
