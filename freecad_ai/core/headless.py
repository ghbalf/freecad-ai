"""Run Python in a separate FreeCAD console process (#114).

The child works on a copy of the active document; the GUI document is never
changed. Results come back as what the code printed, whether it raised, and
the files it wrote into its run folder. Used by execute_code_headless, whose
handler runs on a worker thread (main_thread=False) so the wait here does not
freeze the GUI.
"""

import atexit
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


# Children still running. The child has its own session, so it gets no
# SIGHUP when FreeCAD quits, and only the waiting thread enforces the
# timeout; the exit hook kills whatever is left.
_live = set()


def kill_live_runs():
    """Kill the process group of every child still running (at exit)."""
    for proc in list(_live):
        _kill_group(proc, time.monotonic, time.sleep)
        _live.discard(proc)


atexit.register(kill_live_runs)


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
        _live.add(proc)
        try:
            while proc.poll() is None:
                if clock() - start >= timeout:
                    stopped = "timeout"
                elif is_cancelled():
                    stopped = "cancelled"
                if stopped:
                    _kill_group(proc, clock, sleep)
                    break
                sleep(POLL_S)
        finally:
            _live.discard(proc)
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
