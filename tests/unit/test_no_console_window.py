"""No child process may open a console window on Windows.

FreeCAD is a GUI process, so Windows allocates a fresh console for every
console child it starts. Every child this workbench launches is headless and
speaks over pipes or files, so the window was empty and served no purpose —
and on Windows it appeared on nearly every command, because the sandbox
pre-check runs on every ``execute_code``.

The per-site tests below are the regression guard: they fail if a spawn site
loses its arguments, and they are the place to add a new one.
"""

import subprocess

import pytest

from freecad_ai.utils.proc import (
    hidden_process_group_kwargs,
    hidden_process_kwargs,
)


# From the Windows headers. CREATE_NO_WINDOW does not exist on Linux, so the
# fixture below presents it the way Windows exposes it.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


class _WindowsSubprocess:
    """Just the two constants the helper reads off the subprocess module."""

    CREATE_NO_WINDOW = CREATE_NO_WINDOW
    CREATE_NEW_PROCESS_GROUP = CREATE_NEW_PROCESS_GROUP


class _WindowsOs:
    name = "nt"


@pytest.fixture
def windows(monkeypatch):
    """Make the helper believe it is on Windows.

    Patching the global ``os.name`` would have pytest instantiate WindowsPath
    while formatting its own tracebacks, so only the helper's own references
    are swapped.
    """
    from freecad_ai.utils import proc

    monkeypatch.setattr(proc, "os", _WindowsOs)
    monkeypatch.setattr(proc, "subprocess", _WindowsSubprocess)
    return monkeypatch


class TestHiddenProcessKwargs:
    def test_posix_passes_nothing_extra(self):
        """POSIX children never get a window, so every call site keeps the
        arguments it already passed."""
        assert hidden_process_kwargs() == {}

    def test_windows_suppresses_the_console(self, windows):
        assert hidden_process_kwargs() == {"creationflags": CREATE_NO_WINDOW}

    def test_a_build_without_the_constant_still_spawns(self, windows, monkeypatch):
        """A platform or Python build without CREATE_NO_WINDOW must not break
        process spawning."""
        from freecad_ai.utils import proc

        class _NoConstant:
            CREATE_NEW_PROCESS_GROUP = CREATE_NEW_PROCESS_GROUP

        monkeypatch.setattr(proc, "subprocess", _NoConstant)

        assert hidden_process_kwargs() == {}


class TestHiddenProcessGroupKwargs:
    def test_posix_keeps_its_own_session(self):
        """An AppImage runs the real binary as a grandchild, which killing the
        direct child alone would leave running."""
        assert hidden_process_group_kwargs() == {"start_new_session": True}

    def test_windows_keeps_the_group_and_adds_no_window(self, windows):
        """The two flags are combined, not swapped: dropping the group flag
        would change how the child is killed."""
        flags = hidden_process_group_kwargs()["creationflags"]

        assert flags & CREATE_NO_WINDOW
        assert flags & CREATE_NEW_PROCESS_GROUP

    def test_posix_does_not_leak_a_creationflags_key(self):
        assert "creationflags" not in hidden_process_group_kwargs()


class TestSandboxPreCheckSpawn:
    """core/executor.py — the sandbox dry-run, which runs on every
    execute_code and is therefore the window users see most."""

    def test_requests_no_window(self, windows, monkeypatch, tmp_path):
        from freecad_ai.core import executor

        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(cmd, 1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(executor, "_find_freecad_cmd", lambda: "/usr/bin/freecadcmd")
        monkeypatch.setattr(executor.tempfile, "mktemp",
                            lambda suffix="": str(tmp_path / f"f{suffix}"))

        executor._sandbox_test("pass", timeout=1)

        assert "creationflags" in seen
        assert seen["creationflags"] & CREATE_NO_WINDOW

    def test_still_captures_output_on_posix(self, monkeypatch, tmp_path):
        """The fix must not disturb the captured-stdout contract the sandbox
        relies on to read FreeCAD's console errors."""
        from freecad_ai.core import executor

        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(cmd, 1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(executor, "_find_freecad_cmd", lambda: "/usr/bin/freecadcmd")
        monkeypatch.setattr(executor.tempfile, "mktemp",
                            lambda suffix="": str(tmp_path / f"f{suffix}"))

        executor._sandbox_test("pass", timeout=1)

        assert seen["capture_output"] is True
        assert "creationflags" not in seen


class TestHeadlessSpawn:
    """core/headless.py — execute_code_headless and the FEM tools."""

    @staticmethod
    def _fake_proc():
        class FakeProc:
            pid = 4242
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

        return FakeProc()

    def test_requests_no_window_alongside_the_group(self, windows, tmp_path):
        from freecad_ai.core import headless

        seen = {}

        def fake_launch(cmd, **kwargs):
            seen.update(kwargs)
            return self._fake_proc()

        headless.run_headless(
            "pass", freecad_bin="/usr/bin/freecadcmd", run_dir=str(tmp_path),
            document_path=None, timeout=1, launch=fake_launch)

        assert seen["creationflags"] & CREATE_NO_WINDOW
        assert seen["creationflags"] & CREATE_NEW_PROCESS_GROUP

    def test_posix_still_uses_its_own_session(self, tmp_path):
        from freecad_ai.core import headless

        seen = {}

        def fake_launch(cmd, **kwargs):
            seen.update(kwargs)
            return self._fake_proc()

        headless.run_headless(
            "pass", freecad_bin="/usr/bin/freecadcmd", run_dir=str(tmp_path),
            document_path=None, timeout=1, launch=fake_launch)

        assert seen["start_new_session"] is True
        assert "creationflags" not in seen


class TestStdioMcpServerSpawn:
    """mcp/transport.py — FreeCAD AI as an MCP client, launching e.g. npx."""

    def test_requests_no_window(self, windows, monkeypatch):
        from freecad_ai.mcp import transport

        seen = {}

        class FakeProc:
            stdin = stdout = stderr = None

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def fake_popen(cmd, **kwargs):
            seen.update(kwargs)
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        client = transport.StdioClientTransport(["npx", "some-server"])
        client._running = False
        try:
            client.start()
        except Exception:
            # The reader thread may object to the fake pipes; the arguments
            # are what this test is about.
            pass

        assert seen["creationflags"] & CREATE_NO_WINDOW


class TestApiKeyCommandSpawn:
    """llm/client.py — the cmd: API key prefix, which runs through cmd.exe."""

    def test_requests_no_window(self, windows, monkeypatch):
        from freecad_ai.llm.client import LLMClient

        seen = {}

        def fake_run(command, **kwargs):
            seen.update(kwargs)

            class R:
                returncode = 0
                stdout = "s3cret\n"
                stderr = ""

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)

        client = LLMClient(provider_name="custom", base_url="http://localhost",
                           api_key="cmd:my-token-helper", model="test")
        resolved = client._resolve_api_key()

        assert resolved == "s3cret"
        assert seen["creationflags"] & CREATE_NO_WINDOW
        assert seen["shell"] is True
