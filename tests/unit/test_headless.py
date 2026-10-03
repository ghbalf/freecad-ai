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


# ── final review: FreeCAD quitting mid-run (#114) ───────────

def test_a_running_child_is_tracked_and_released(tmp_path):
    proc = FakeProc(polls=2)
    seen = []
    _run(tmp_path, proc, {}, is_cancelled=lambda: seen.append(proc in headless._live) or False)
    assert seen and all(seen)
    assert proc not in headless._live


def test_exit_hook_kills_every_live_child(signals):
    a, b = FakeProc(polls=None), FakeProc(polls=None)
    signals.procs += [a, b]
    headless._live.update({a, b})
    try:
        headless.kill_live_runs()
    finally:
        headless._live.difference_update({a, b})
    assert a.returncode is not None and b.returncode is not None
    assert signals.sent.count(signal.SIGTERM) == 2


def test_exit_hook_is_registered():
    import atexit
    from unittest.mock import patch
    import importlib
    with patch.object(atexit, "register") as reg:
        importlib.reload(headless)
    reg.assert_any_call(headless.kill_live_runs)
