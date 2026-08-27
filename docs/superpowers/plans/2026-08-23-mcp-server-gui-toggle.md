# MCP Server GUI Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a checkable **MCP Server** command to the FreeCAD AI toolbar and menu that starts and stops the HTTP/SSE server inside the running FreeCAD, reporting the true state even when the server was started from the command line.

**Architecture:** A process-wide `ServerController` singleton in `freecad_ai/mcp/gui_server.py` becomes the single source of truth for "is a server running in this process." `SSEServerTransport` gains a split `bind()`/`serve()`/`stop()` lifecycle so bind failures surface synchronously to the clicking user instead of dying in a daemon thread. `mcp_server_http.py` and the new toolbar command both delegate to the controller.

**Tech Stack:** Python 3.11, stdlib only (`http.server`, `socketserver`, `threading`, `os`), PySide6/PySide2 via `freecad_ai/ui/compat.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-mcp-server-gui-toggle-design.md`

## Global Constraints

- **`freecad_ai/mcp/` stays zero-dependency, stdlib only.** No new third-party imports anywhere in this plan.
- **Never hard-import PySide2 or PySide6.** Always go through `freecad_ai/ui/compat.py`.
- **Use flat Qt enum forms** (`QTextCursor.End`, not `QTextCursor.MoveOperation.End`) — PySide2 only accepts flat.
- **`InitGui.py` is evaluated inline by FreeCAD.** `__file__` is undefined there and module-level names are invisible inside classes. Every import inside a command class must happen *in-method*.
- **Run tests with:** `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
  A shell `PYTHONPATH` shadows the venv's pluggy and crashes pytest; `test_document_attach.py` Qt-segfaults on clean master and must stay ignored.
- **Do not change `SSEServerTransport.run()`'s signature or observable behaviour.** `mcp_server_entry.py`, existing tests, and user scripts call it.
- **The server remains unauthenticated.** Authentication is [#59](https://github.com/ghbalf/freecad-ai/issues/59) and is explicitly out of scope. Do not add a token.
- **Do not "fix" `MCP_HOST=0.0.0.0`.** That is [#60](https://github.com/ghbalf/freecad-ai/issues/60) and must not land before #59.

---

### Task 1: Split the SSE transport lifecycle into bind / serve / stop

**Files:**
- Modify: `freecad_ai/mcp/transport.py:534-570` (`SSEServerTransport.__init__` and `run`)
- Test: `tests/unit/test_mcp_sse_transport.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SSEServerTransport.bind() -> None` (raises `OSError`), `SSEServerTransport.serve(handler=None) -> None` (raises `RuntimeError` if not bound), `SSEServerTransport.stop() -> None` (idempotent), and `run(handler)` unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_sse_transport.py`. Add `import socket` and `import time` to the existing imports at the top of the file (`pathlib`, `threading`, `urllib.error`, `urllib.request`, `pytest` are already there).

```python
# ---------------------------------------------------------------------------
# Split lifecycle — bind() / serve() / stop()
#
# run() used to build the HTTP server *inside* the serve thread, so a port
# conflict raised OSError in a daemon thread: FreeCAD carried on with no
# dialog and no status change. bind() moves that failure onto the caller.
# ---------------------------------------------------------------------------

def _free_port():
    """Pick a port that is free right now."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_bind_raises_when_the_port_is_taken():
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        transport = SSEServerTransport(host="127.0.0.1", port=port)
        with pytest.raises(OSError):
            transport.bind()
    finally:
        blocker.close()


def test_bind_is_idempotent():
    transport = SSEServerTransport(host="127.0.0.1", port=_free_port())
    try:
        transport.bind()
        transport.bind()  # must not raise "address already in use" against itself
    finally:
        transport.stop()


def test_stop_releases_the_socket():
    port = _free_port()
    first = SSEServerTransport(host="127.0.0.1", port=port)
    first.bind()
    first.stop()

    second = SSEServerTransport(host="127.0.0.1", port=port)
    try:
        second.bind()  # must not raise — the first one really let go
    finally:
        second.stop()


def test_stop_without_bind_does_not_raise():
    SSEServerTransport(host="127.0.0.1", port=_free_port()).stop()


def test_stop_after_bind_without_serve_does_not_hang():
    """BaseServer.shutdown() waits on an event only serve_forever() sets.

    Calling it on a bound-but-never-served socket blocks forever, so this
    fails as a timeout rather than an assertion if stop() gets it wrong.
    """
    transport = SSEServerTransport(host="127.0.0.1", port=_free_port())
    transport.bind()
    finished = threading.Event()

    def _stop():
        transport.stop()
        finished.set()

    threading.Thread(target=_stop, daemon=True).start()
    assert finished.wait(timeout=5), "stop() hung on a bound-but-unserved server"


def test_serve_before_bind_raises_runtime_error():
    transport = SSEServerTransport(host="127.0.0.1", port=_free_port())
    with pytest.raises(RuntimeError):
        transport.serve(lambda msg: None)


def test_run_still_binds_and_serves():
    """run() must keep working unchanged — entry scripts and users call it."""
    port = _free_port()
    transport = SSEServerTransport(host="127.0.0.1", port=port)
    thread = threading.Thread(
        target=transport.run, args=(lambda msg: None,), daemon=True)
    thread.start()

    deadline = time.time() + 5
    connected = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                connected = True
                break
        except OSError:
            time.sleep(0.05)
    assert connected, "run() never started listening"

    transport.stop()
    thread.join(timeout=5)
    assert not thread.is_alive()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_transport.py -q`
Expected: FAIL — `AttributeError: 'SSEServerTransport' object has no attribute 'bind'`

- [ ] **Step 3: Add the `_httpd` slot**

In `freecad_ai/mcp/transport.py`, in `SSEServerTransport.__init__`, add two lines immediately after `self._sse_lock = threading.Lock()`:

```python
        self._httpd = None
        self._serving = False
```

- [ ] **Step 4: Replace `run()` with the split lifecycle**

Replace the whole existing `run` method:

```python
    def run(self, handler: Callable[[dict], dict | None]):
        """Start the HTTP server (blocking)."""
        self._handler = handler
        server = self._make_server()
        logger.info("MCP SSE server listening on http://%s:%d", self._host, self._port)
        server.serve_forever()
```

with:

```python
    def bind(self):
        """Create and bind the listening socket. Raises OSError if unavailable.

        Split out of ``run`` so a caller on the GUI thread learns about a bind
        failure (EADDRINUSE, EACCES) synchronously. When the bind happened
        inside the serve thread the traceback went to the console and nothing
        else: FreeCAD carried on as if the server had started.

        Idempotent — binding an already-bound transport is a no-op.
        """
        if self._httpd is None:
            self._httpd = self._make_server()

    def serve(self, handler: Callable[[dict], dict | None] | None = None):
        """Serve until stop(). Requires a prior bind()."""
        if handler is not None:
            self._handler = handler
        if self._httpd is None:
            raise RuntimeError("bind() must be called before serve()")
        logger.info("MCP SSE server listening on http://%s:%d", self._host, self._port)
        self._serving = True
        try:
            self._httpd.serve_forever()
        finally:
            self._serving = False

    def stop(self):
        """Shut down and release the socket. Safe when never bound.

        ``shutdown()`` is only safe once ``serve_forever()`` is running: it
        waits on an event that only serve_forever's exit path sets, so calling
        it on a bound-but-never-served socket blocks forever. Bound but never
        served therefore goes straight to ``server_close()``.
        """
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        if self._serving:
            httpd.shutdown()
        httpd.server_close()

    def run(self, handler: Callable[[dict], dict | None]):
        """Start the HTTP server (blocking). Unchanged: bind, then serve."""
        self._handler = handler
        self.bind()
        self.serve()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_transport.py -q`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_sse_transport.py
git commit -m "feat(mcp): split SSEServerTransport into bind/serve/stop

The bind used to happen inside the serve thread, so a port conflict
raised OSError in a daemon thread and vanished: no dialog, no status
change, FreeCAD carrying on as if the server were up. bind() now runs
on the caller's thread so the failure is catchable, and stop() gives
the server a shutdown path it never had.

run() keeps its exact signature and behaviour as bind() + serve(), so
mcp_server_entry.py and any user script calling it are untouched."
```

---

### Task 2: Add MCP server host/port to AppConfig

**Files:**
- Modify: `freecad_ai/config.py:414-415` (after the `mcp_servers` field and its comment)
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `AppConfig.mcp_server_host: str = "127.0.0.1"` and `AppConfig.mcp_server_port: int = 3000`, both persisted through the existing `to_dict()` / `from_dict()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
def test_mcp_server_address_defaults():
    from freecad_ai.config import AppConfig
    cfg = AppConfig()
    assert cfg.mcp_server_host == "127.0.0.1"
    assert cfg.mcp_server_port == 3000


def test_mcp_server_address_roundtrip():
    from freecad_ai.config import AppConfig
    cfg = AppConfig(mcp_server_host="0.0.0.0", mcp_server_port=8080)
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.mcp_server_host == "0.0.0.0"
    assert restored.mcp_server_port == 8080
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py -q -k mcp_server_address`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'mcp_server_host'`

- [ ] **Step 3: Add the fields**

In `freecad_ai/config.py`, directly after the existing two lines:

```python
    mcp_servers: list = field(default_factory=list)
    # Each entry: {"name": str, "command": str, "args": list, "env": dict, "enabled": bool}
```

insert:

```python
    # Address the addon listens on when acting AS an MCP server (the toolbar
    # toggle and mcp_server_http.py). Host is deliberately unrestricted,
    # including non-loopback: the server has no authentication (issue #59), so
    # the Settings dialog warns about the exposure rather than pretending a
    # restricted field made it safe. MCP_HOST / MCP_PORT override both.
    mcp_server_host: str = "127.0.0.1"
    mcp_server_port: int = 3000
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py tests/unit/test_config.py
git commit -m "feat(config): add mcp_server_host / mcp_server_port

The toolbar toggle is clicked mid-session and has no argv or env to
read, so MCP_HOST/MCP_PORT alone cannot configure it. These fields give
the GUI a source; env still wins where it is set."
```

---

### Task 3: ServerController singleton

**Files:**
- Create: `freecad_ai/mcp/gui_server.py`
- Test: `tests/unit/test_mcp_gui_server.py`

**Interfaces:**
- Consumes: `SSEServerTransport.bind()` / `serve()` / `stop()` from Task 1; `AppConfig.mcp_server_host` / `mcp_server_port` from Task 2.
- Produces:
  - `DEFAULT_HOST: str = "127.0.0.1"`, `DEFAULT_PORT: int = 3000`
  - `resolve_server_address(cfg=None) -> tuple[str, int]`
  - `ServerController.start(host: str, port: int) -> str` (the URL; raises `OSError`)
  - `ServerController.stop() -> None`, `ServerController.is_running() -> bool`, `ServerController.url -> str | None`
  - `get_server_controller() -> ServerController`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_gui_server.py`:

```python
"""Tests for the process-wide MCP server controller.

Three routes start this server in one FreeCAD process — the
mcp_server_http.py command-line argument, the documented
exec(open(...).read()) console snippet, and the toolbar toggle. They share
one controller so the toggle cannot misreport a server it did not start.
"""

import socket
import threading
import time

import pytest

from freecad_ai.mcp.gui_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ServerController,
    get_server_controller,
    resolve_server_address,
)


def _free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class _FakeRegistry:
    """Minimal stand-in for ToolRegistry.

    MCPServer.run() logs ``len(registry.list_tools())`` on the way up, so a
    bare object() would raise inside the serve thread and the controller would
    report not-running for reasons that have nothing to do with the lifecycle
    being tested.
    """

    def list_tools(self):
        return []

    def to_mcp_schema(self):
        return []


def _fake_backend():
    """Stand in for the FreeCAD tool registry and Qt executor.

    Lifecycle tests must not depend on tool loading; a failure there should
    break tool tests, not these.
    """
    return _FakeRegistry(), object()


def _controller():
    return ServerController(backend_factory=_fake_backend)


# --- address resolution ----------------------------------------------------

def test_resolve_falls_back_to_defaults_without_config_or_env(monkeypatch):
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    assert resolve_server_address(None) == (DEFAULT_HOST, DEFAULT_PORT)


def test_resolve_prefers_config_over_defaults(monkeypatch):
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    cfg = type("Cfg", (), {"mcp_server_host": "10.0.0.5", "mcp_server_port": 9000})()
    assert resolve_server_address(cfg) == ("10.0.0.5", 9000)


def test_resolve_prefers_env_over_config(monkeypatch):
    monkeypatch.setenv("MCP_HOST", "192.168.1.50")
    monkeypatch.setenv("MCP_PORT", "3131")
    cfg = type("Cfg", (), {"mcp_server_host": "10.0.0.5", "mcp_server_port": 9000})()
    assert resolve_server_address(cfg) == ("192.168.1.50", 3131)


def test_resolve_ignores_a_non_numeric_env_port(monkeypatch):
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.setenv("MCP_PORT", "not-a-port")
    cfg = type("Cfg", (), {"mcp_server_host": "10.0.0.5", "mcp_server_port": 9000})()
    assert resolve_server_address(cfg) == ("10.0.0.5", 9000)


# --- lifecycle -------------------------------------------------------------

def test_start_reports_running_and_returns_the_url():
    controller = _controller()
    port = _free_port()
    try:
        url = controller.start("127.0.0.1", port)
        assert url == "http://127.0.0.1:%d/sse" % port
        assert controller.is_running() is True
        assert controller.url == url
    finally:
        controller.stop()


def test_a_fresh_controller_is_not_running():
    controller = _controller()
    assert controller.is_running() is False
    assert controller.url is None


def test_second_start_is_a_no_op_returning_the_same_url():
    controller = _controller()
    port = _free_port()
    try:
        first = controller.start("127.0.0.1", port)
        second = controller.start("127.0.0.1", port)  # must not raise EADDRINUSE
        assert first == second
    finally:
        controller.stop()


def test_start_on_a_taken_port_raises_and_stays_stopped():
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    controller = _controller()
    try:
        with pytest.raises(OSError):
            controller.start("127.0.0.1", port)
        assert controller.is_running() is False
        assert controller.url is None
    finally:
        blocker.close()
        controller.stop()


def test_stop_releases_the_port_for_a_later_start():
    controller = _controller()
    port = _free_port()
    controller.start("127.0.0.1", port)
    controller.stop()
    assert controller.is_running() is False
    try:
        controller.start("127.0.0.1", port)  # must not raise
    finally:
        controller.stop()


def test_stop_is_idempotent_and_safe_before_any_start():
    controller = _controller()
    controller.stop()
    controller.stop()
    assert controller.is_running() is False


def test_the_server_actually_listens_after_start():
    controller = _controller()
    port = _free_port()
    try:
        controller.start("127.0.0.1", port)
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass  # connecting is the assertion
    finally:
        controller.stop()


def test_is_running_is_false_once_the_serve_thread_exits():
    controller = _controller()
    port = _free_port()
    controller.start("127.0.0.1", port)
    controller._transport.stop()  # kill the server behind the controller's back
    deadline = time.time() + 5
    while controller.is_running() and time.time() < deadline:
        time.sleep(0.05)
    assert controller.is_running() is False
    controller.stop()


def test_get_server_controller_returns_one_shared_instance():
    assert get_server_controller() is get_server_controller()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_gui_server.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'freecad_ai.mcp.gui_server'`

- [ ] **Step 3: Write the controller**

Create `freecad_ai/mcp/gui_server.py`:

```python
"""Process-wide controller for the MCP server hosted inside FreeCAD.

Three routes start this server, and all of them land in the same process:

  * ``FreeCAD.AppImage /path/to/mcp_server_http.py`` on the command line
  * ``exec(open(".../mcp_server_http.py").read())`` in the Python console
  * the FreeCAD AI toolbar toggle

They must share one object. Without it the toggle renders unchecked next to a
server that is already listening, and clicking it builds a second transport
that dies on EADDRINUSE inside a daemon thread — visible only as a console
traceback. Port-probing is not a substitute: "something is listening on 3000"
does not mean it is ours.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000


def resolve_server_address(cfg=None):
    """Return ``(host, port)``: env beats config, config beats defaults.

    Env wins so every documented command-line recipe keeps working unchanged,
    including the wiki's ``MCP_PORT=…`` and Flatpak invocations.
    """
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    if cfg is not None:
        host = getattr(cfg, "mcp_server_host", "") or DEFAULT_HOST
        port = getattr(cfg, "mcp_server_port", 0) or DEFAULT_PORT

    env_host = os.environ.get("MCP_HOST")
    if env_host:
        host = env_host

    env_port = os.environ.get("MCP_PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            logger.warning("Ignoring non-numeric MCP_PORT=%r", env_port)

    return host, port


def _default_backend():
    """Build the tool registry and the Qt main-thread executor.

    ``include_mcp=False``: this registry is what we *serve*, so it must not
    re-export tools the workbench's own MCP client pulled in from elsewhere.
    The executor marshals every call onto the Qt main thread because FreeCAD's
    document API is not thread-safe.
    """
    from ..tools.setup import create_default_registry
    from ..tools.executor_utils import QtMainThreadToolExecutor

    registry = create_default_registry(include_mcp=False)
    executor = QtMainThreadToolExecutor()
    executor.set_registry(registry)
    return registry, executor


class ServerController:
    """Owns the one MCP server that may run in this process."""

    def __init__(self, backend_factory=None):
        self._backend_factory = backend_factory or _default_backend
        self._transport = None
        self._thread = None
        self._registry = None
        self._executor = None
        self._url = None

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self):
        return self._url if self.is_running() else None

    def start(self, host, port):
        """Start serving and return the URL. Raises OSError if the bind fails.

        Binds *before* building the registry: the bind is the only step that
        realistically fails, it is cheap, and failing first leaves no
        half-initialised backend behind.
        """
        if self.is_running():
            return self._url

        from .transport import SSEServerTransport

        transport = SSEServerTransport(host=host, port=port)
        transport.bind()  # raises OSError on the caller's thread — the point

        if self._registry is None or self._executor is None:
            self._registry, self._executor = self._backend_factory()

        from .server import MCPServer

        server = MCPServer(self._registry, transport=transport,
                           executor=self._executor)
        thread = threading.Thread(
            target=self._serve, args=(server,), daemon=True,
            name="mcp-sse-server")
        thread.start()

        self._transport = transport
        self._thread = thread
        self._url = "http://%s:%d/sse" % (host, port)
        logger.info("MCP SSE server listening on %s", self._url)
        return self._url

    def _serve(self, server):
        # MCPServer.run() calls transport.run(), which is bind() + serve();
        # bind() is idempotent, so the socket we already secured is reused.
        try:
            server.run()
        except Exception:
            logger.exception("MCP SSE server stopped unexpectedly")

    def stop(self):
        """Shut the server down and release the port. Idempotent."""
        transport, self._transport = self._transport, None
        thread, self._thread = self._thread, None
        self._url = None
        if transport is not None:
            transport.stop()
        if thread is not None:
            thread.join(timeout=5)


_controller = None


def get_server_controller():
    """Return the process-wide controller, creating it on first use."""
    global _controller
    if _controller is None:
        _controller = ServerController()
    return _controller
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_gui_server.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Run the whole suite for regressions**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/mcp/gui_server.py tests/unit/test_mcp_gui_server.py
git commit -m "feat(mcp): process-wide ServerController for the SSE server

The server can be started three ways -- CLI argument, console exec, and
(next) a toolbar toggle -- and all three land in the same process. One
controller means the toggle reflects a CLI-started server instead of
offering to start a second one on a port that is already ours.

Binds before building the registry so a port conflict costs nothing and
raises on the caller's thread."
```

---

### Task 4: Delegate `mcp_server_http.py` to the controller

**Files:**
- Modify: `mcp_server_http.py:44-68`
- Test: `tests/unit/test_mcp_sse_transport.py`

**Interfaces:**
- Consumes: `get_server_controller()` and `resolve_server_address()` from Task 3.
- Produces: no new API. The script keeps printing `MCP SSE server running on <url>` and keeps its `__file__` guard.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_sse_transport.py`. Add `import sys` and `import types` to the file's imports.

```python
def test_http_entry_point_delegates_to_the_shared_controller(monkeypatch):
    """The script must not build its own server.

    A server it owned privately would be invisible to the toolbar toggle,
    which would then render unchecked and try to bind the same port again.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    source = (repo_root / "mcp_server_http.py").read_text()

    fake_freecad = types.ModuleType("FreeCAD")
    fake_freecad.ActiveDocument = object()
    fake_freecad.newDocument = lambda name: None
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)

    started = []

    class _FakeController:
        def start(self, host, port):
            started.append((host, port))
            return "http://%s:%d/sse" % (host, port)

    import freecad_ai.mcp.gui_server as gui_server
    monkeypatch.setattr(gui_server, "get_server_controller",
                        lambda: _FakeController())

    # Pin both so the assertion cannot depend on the developer's config.json.
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "3131")

    exec(compile(source, "mcp_server_http.py", "exec"), {})

    assert started == [("127.0.0.1", 3131)]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_transport.py -q -k delegates`
Expected: FAIL — `assert [] == [('127.0.0.1', 3131)]`, because the script still builds its own transport.

- [ ] **Step 3: Rewrite the script's body**

In `mcp_server_http.py`, replace everything from `from freecad_ai.tools.setup import create_default_registry` to the end of the file:

```python
from freecad_ai.tools.setup import create_default_registry
from freecad_ai.tools.executor_utils import QtMainThreadToolExecutor
from freecad_ai.mcp.server import MCPServer
from freecad_ai.mcp.transport import SSEServerTransport

registry = create_default_registry(include_mcp=False)

executor = QtMainThreadToolExecutor()
executor.set_registry(registry)

host = os.environ.get("MCP_HOST", "127.0.0.1")
port = int(os.environ.get("MCP_PORT", "3000"))

transport = SSEServerTransport(host=host, port=port)
server = MCPServer(registry, transport=transport, executor=executor)

server_thread = threading.Thread(target=server.run, daemon=True)
server_thread.start()

print(f"MCP SSE server running on http://{host}:{port}/sse", flush=True)
```

with:

```python
from freecad_ai.mcp.gui_server import get_server_controller, resolve_server_address

# Config is only a fallback here; MCP_HOST / MCP_PORT still win. Reading it
# can fail outside a configured install, which must not stop the server.
try:
    from freecad_ai.config import get_config
    _cfg = get_config()
except Exception:
    _cfg = None

host, port = resolve_server_address(_cfg)

# start() binds before returning, so this line can no longer announce a
# server that never came up.
url = get_server_controller().start(host, port)

print(f"MCP SSE server running on {url}", flush=True)
```

Then delete the now-unused `import threading` from the top of the file. Keep `import os`, `import sys`, `import logging`, the `logging.basicConfig` call, the `__file__` guard, and the `import FreeCAD` block exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_sse_transport.py -q`
Expected: PASS — including the pre-existing `test_http_entry_point_safe_under_exec`, which proves the `__file__` guard survived.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_http.py tests/unit/test_mcp_sse_transport.py
git commit -m "refactor(mcp): route mcp_server_http.py through the controller

A server the script owned privately is invisible to the toolbar toggle.
Going through the shared controller means launching FreeCAD with this
script shows up as a checked button that can also stop it.

Side effect worth having: start() binds before returning, so the script
no longer prints 'MCP SSE server running on ...' before attempting the
bind -- it used to announce success for a server that never started."
```

---

### Task 5: Settings dialog host / port fields and the exposure warning

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py` — Qt alias block near line 33, MCP group near line 648, `_load_from_config` near line 899, `_save` near line 1169
- Test: `tests/unit/test_settings_dialog_mcp_address.py`

**Interfaces:**
- Consumes: `AppConfig.mcp_server_host` / `mcp_server_port` from Task 2; `DEFAULT_HOST` / `DEFAULT_PORT` from Task 3.
- Produces: `SettingsDialog._parse_server_address(host_text, port_text) -> tuple[str, int]` (a `@staticmethod`), plus the widgets `self.mcp_server_host_edit` and `self.mcp_server_port_edit`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_settings_dialog_mcp_address.py`:

```python
"""Tests for the MCP server address fields in the Settings dialog.

Only the pure parsing helper is tested. Building the dialog needs a
QApplication; extracting the normalisation into a staticmethod keeps the part
with actual logic testable without one.
"""

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.mcp.gui_server import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402

parse = SettingsDialog._parse_server_address


def test_parses_a_normal_address():
    assert parse("192.168.1.50", "8080") == ("192.168.1.50", 8080)


def test_strips_surrounding_whitespace():
    assert parse("  127.0.0.1  ", "  3000 ") == ("127.0.0.1", 3000)


def test_empty_host_falls_back_to_the_default():
    assert parse("", "8080") == (DEFAULT_HOST, 8080)


def test_empty_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "") == ("127.0.0.1", DEFAULT_PORT)


def test_non_numeric_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "abc") == ("127.0.0.1", DEFAULT_PORT)


def test_out_of_range_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "70000") == ("127.0.0.1", DEFAULT_PORT)
    assert parse("127.0.0.1", "0") == ("127.0.0.1", DEFAULT_PORT)


def test_privileged_ports_are_allowed():
    """No 1024 floor -- the GUI reaches exactly the ports MCP_PORT does.

    Binding one unprivileged fails with PermissionError, which surfaces
    through the same modal as any other bind failure.
    """
    assert parse("127.0.0.1", "80") == ("127.0.0.1", 80)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_dialog_mcp_address.py -q`
Expected: FAIL — `AttributeError: type object 'SettingsDialog' has no attribute '_parse_server_address'`

- [ ] **Step 3: Add the `QIntValidator` alias**

In `freecad_ai/ui/settings_dialog.py`, next to the existing `QDoubleValidator = QtGui.QDoubleValidator` line, add:

```python
QIntValidator = QtGui.QIntValidator
```

- [ ] **Step 4: Add the parsing helper**

Add this `@staticmethod` to the `SettingsDialog` class (place it directly above `_add_mcp_server`, near line 1515):

```python
    @staticmethod
    def _parse_server_address(host_text, port_text):
        """Normalise the MCP server host/port fields into (host, port).

        Anything unusable falls back to the default rather than raising: the
        dialog must always be closable. The validator already blocks
        out-of-range typing, so this is the belt to its braces.
        """
        from ..mcp.gui_server import DEFAULT_HOST, DEFAULT_PORT
        host = (host_text or "").strip() or DEFAULT_HOST
        try:
            port = int((port_text or "").strip())
        except ValueError:
            return host, DEFAULT_PORT
        if not 1 <= port <= 65535:
            return host, DEFAULT_PORT
        return host, port
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_dialog_mcp_address.py -q`
Expected: PASS (7 tests)

- [ ] **Step 6: Add the widgets to the MCP group**

In `SettingsDialog._build_ui` (starts at line 187), where the MCP group is assembled around line 648: immediately after `mcp_layout.addLayout(mcp_btn_layout)` and before `mcp_group.setLayout(mcp_layout)`, insert:

```python
        # Address this addon listens on when acting AS an MCP server.
        # A plain numeric entry, not a spinbox: no artificial 1024 floor, so
        # the GUI reaches exactly the ports MCP_PORT does. A privileged port
        # fails at bind time with a real message instead of being unreachable.
        server_form = QFormLayout()

        self.mcp_server_host_edit = QLineEdit()
        self.mcp_server_host_edit.setPlaceholderText("127.0.0.1")
        server_form.addRow(
            translate("SettingsDialog", "Server host:"),
            self.mcp_server_host_edit)

        self.mcp_server_port_edit = QLineEdit()
        self.mcp_server_port_edit.setValidator(QIntValidator(1, 65535, self))
        self.mcp_server_port_edit.setPlaceholderText("3000")
        server_form.addRow(
            translate("SettingsDialog", "Server port:"),
            self.mcp_server_port_edit)

        mcp_layout.addLayout(server_form)

        # Unconditional, not shown only for non-loopback values: the loopback
        # default is already reachable by every local process, so hiding the
        # warning there would imply the default is authenticated. It is not.
        mcp_server_warning = QLabel(translate(
            "SettingsDialog",
            "The MCP server has no authentication. Anything that can reach "
            "this address can run FreeCAD tools, including arbitrary Python. "
            "Keep it on 127.0.0.1 unless you understand the exposure."))
        mcp_server_warning.setWordWrap(True)
        mcp_layout.addWidget(mcp_server_warning)
```

- [ ] **Step 7: Load the values**

In `_load_from_config`, after the existing MCP servers block (`for entry in self._mcp_configs: ...`), add:

```python
        self.mcp_server_host_edit.setText(cfg.mcp_server_host)
        self.mcp_server_port_edit.setText(str(cfg.mcp_server_port))
```

- [ ] **Step 8: Save the values**

In `_save`, directly after the existing `cfg.mcp_servers = ...` line, add:

```python
        cfg.mcp_server_host, cfg.mcp_server_port = self._parse_server_address(
            self.mcp_server_host_edit.text(),
            self.mcp_server_port_edit.text())
```

- [ ] **Step 9: Run the whole suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py tests/unit/test_settings_dialog_mcp_address.py
git commit -m "feat(ui): MCP server host/port fields with an exposure warning

A plain numeric entry rather than a spinbox, deliberately without a 1024
floor, so the GUI can reach exactly the ports MCP_PORT can. Privileged
ports fail at bind with PermissionError through the normal modal path
instead of being silently unreachable.

The no-authentication warning is unconditional. Showing it only for
non-loopback addresses would imply the 127.0.0.1 default is
authenticated -- it is not; every local process can reach it (#59)."
```

---

### Task 6: The toolbar and menu toggle

**Files:**
- Modify: `InitGui.py` — add the command class after `ToggleKeepDockCommand` (line 150), extend `Initialize` (line 23), register near line 198
- Test: none (see note)

**Interfaces:**
- Consumes: `get_server_controller()` and `resolve_server_address()` from Task 3; `AppConfig` fields from Task 2.
- Produces: the FreeCAD command `FreeCADAI_ToggleMCPServer`.

**Note on testing:** `InitGui.py` cannot be imported by pytest — FreeCAD evaluates it inline with `FreeCADGui` bound, and `__file__` is undefined. The command is kept thin so all real logic sits in `gui_server.py` under test from Task 3. Verification here is manual, in Step 5.

- [ ] **Step 1: Add the command class**

In `InitGui.py`, after `ToggleKeepDockCommand` and before the `# Register translation path early` comment block, add:

```python
class ToggleMCPServerCommand:
    """Start/stop the HTTP+SSE MCP server inside this FreeCAD process.

    Checkable, and IsChecked() asks the shared controller rather than any
    state of its own — so a server started with
    ``FreeCAD.AppImage mcp_server_http.py`` or from the Python console shows
    as on here, and can be stopped from this button.
    """

    def GetResources(self):
        from freecad_ai.i18n import translate
        return {
            "GroupName": "FreeCAD AI",
            "MenuText": translate("ToggleMCPServerCommand", "MCP Server"),
            "ToolTip": translate(
                "ToggleMCPServerCommand",
                "Start or stop the MCP server, letting external clients such "
                "as Claude Code drive this FreeCAD session. The server has no "
                "authentication; set its address in AI Settings."),
            "Checkable": True,
        }

    def Activated(self, index=0):
        from freecad_ai.mcp.gui_server import (
            get_server_controller, resolve_server_address)
        controller = get_server_controller()

        if controller.is_running():
            controller.stop()
            App.Console.PrintMessage("FreeCAD AI: MCP server stopped\n")
            return

        from freecad_ai.config import get_config
        host, port = resolve_server_address(get_config())
        try:
            url = controller.start(host, port)
        except OSError as exc:
            self._report_failure(host, port, exc)
            return

        App.Console.PrintMessage(
            "FreeCAD AI: MCP server listening on %s\n" % url)
        window = Gui.getMainWindow()
        if window:
            window.statusBar().showMessage(
                "MCP server listening on %s" % url, 10000)

    def _report_failure(self, host, port, exc):
        """Modal, because the click has to visibly fail.

        This used to be a traceback in a daemon thread that nothing surfaced.
        """
        from freecad_ai.i18n import translate
        from freecad_ai.ui.compat import QtWidgets
        message = translate(
            "ToggleMCPServerCommand",
            "Could not start the MCP server on {address}.\n\n{error}\n\n"
            "Change the address in FreeCAD AI → AI Settings → "
            "MCP Servers.").format(address="%s:%d" % (host, port), error=exc)
        App.Console.PrintError("FreeCAD AI: %s\n" % message.replace("\n\n", " "))
        QtWidgets.QMessageBox.warning(
            Gui.getMainWindow(),
            translate("ToggleMCPServerCommand", "MCP server failed to start"),
            message)

    def IsChecked(self):
        try:
            from freecad_ai.mcp.gui_server import get_server_controller
            return get_server_controller().is_running()
        except Exception:
            return False

    def IsActive(self):
        return True
```

- [ ] **Step 2: Register the command**

Next to the existing `Gui.addCommand` calls, add:

```python
Gui.addCommand("FreeCADAI_ToggleMCPServer", ToggleMCPServerCommand())
```

It must come before the existing `Gui.addWorkbench(FreeCADAIWorkbench())` line.

- [ ] **Step 3: Put it on the toolbar and the menu**

In `FreeCADAIWorkbench.Initialize`, replace:

```python
        self.appendToolbar("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings"])
        self.appendMenu("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings",
                                       "FreeCADAI_ToggleKeepDock"])
```

with:

```python
        self.appendToolbar("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings",
                                          "FreeCADAI_ToggleMCPServer"])
        self.appendMenu("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings",
                                       "FreeCADAI_ToggleMCPServer",
                                       "FreeCADAI_ToggleKeepDock"])
```

- [ ] **Step 4: Check the suite still passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS (no new tests here; this guards against an import-time mistake elsewhere).

- [ ] **Step 5: Verify manually in FreeCAD**

Launch: `QT_QPA_PLATFORM=xcb ~/bin/freecad` (or `xvfb-run -a env QT_QPA_PLATFORM=xcb ~/bin/freecad` to keep it off the user's desktop), switch to the FreeCAD AI workbench, then confirm each of:

1. **Start.** Click **MCP Server**. The Report view shows `MCP server listening on http://127.0.0.1:3000/sse` and the button is checked. `ss -ltn | grep 3000` shows a listener.
2. **Serves tools.** `claude mcp add --transport sse freecad http://127.0.0.1:3000/sse`, then in a **new** `claude` session confirm the FreeCAD tools are listed.
3. **Stop.** Click again — button unchecks, the port is released, the client disconnects.
4. **Reflects a CLI-started server.** Quit, relaunch as `~/bin/freecad /path/to/freecad-ai/mcp_server_http.py`, open the workbench: the button is **already checked**. Clicking it stops that server.
5. **Fails loudly.** Occupy the port first (`python3 -m http.server 3000`), then click: a modal names the conflict and points at Settings, and the button stays **unchecked**.
6. **Alternate port.** Set port 3123 in AI Settings, click, and confirm it listens on 3123.

- [ ] **Step 6: Commit**

```bash
git add InitGui.py
git commit -m "feat(ui): toolbar and menu toggle for the MCP server

Suggested by @s-light in the closing comment on #55: starting the
server should not require knowing a filesystem path.

IsChecked() delegates to the shared controller rather than tracking
state locally, so the button tells the truth about a server started
from the command line or the Python console, and can stop it. Failures
are modal -- a bind error used to be a daemon-thread traceback that
nothing surfaced."
```

---

### Task 7: Documentation

**Files:**
- Modify: `CHANGELOG.md` (the `## [Unreleased]` section)
- Modify: `/home/alf/Projects/programming/misc/freecad-ai-wiki/MCP-Integration.md` (separate git repo)

**Interfaces:**
- Consumes: the finished feature.
- Produces: no code.

- [ ] **Step 1: Add the CHANGELOG entry**

Under `## [Unreleased]`, add an `### Added` section above the existing `### Fixed` (create it if absent):

```markdown
### Added

- **Start and stop the MCP server from the toolbar** — a checkable **MCP Server**
  command in the FreeCAD AI toolbar and menu starts the HTTP/SSE server in the
  running FreeCAD, so external clients no longer need a command-line launch or a
  pasted `exec(open(...))` snippet. Suggested by @s-light on
  [#55](https://github.com/ghbalf/freecad-ai/issues/55).
  The button reports the true state: a server started via
  `FreeCAD.AppImage mcp_server_http.py` or from the Python console shows as
  running and can be stopped from the button, because all three routes now share
  one controller.
  Host and port are configurable under **AI Settings → MCP Servers**, with
  `MCP_HOST`/`MCP_PORT` still taking precedence. Note the server has **no
  authentication** — see [#59](https://github.com/ghbalf/freecad-ai/issues/59).

### Fixed

- **A failed MCP server start was silent** — the listening socket was created
  inside the server thread, so a port conflict raised `OSError` in a daemon
  thread and vanished: no dialog, no log the user would see, FreeCAD carrying on
  as though the server were up. `mcp_server_http.py` compounded it by printing
  `MCP SSE server running on ...` *before* attempting the bind. The bind now
  happens before anything is announced, and failures reach the caller.
- **The MCP server could not be stopped** — `SSEServerTransport` never kept a
  handle on its HTTP server, so the only way to stop it was to quit FreeCAD. It
  now has a `stop()` that shuts down and releases the port.
```

- [ ] **Step 2: Commit the CHANGELOG**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): MCP server toolbar toggle and the silent-failure fixes"
```

- [ ] **Step 3: Document the toggle in the wiki**

In `/home/alf/Projects/programming/misc/freecad-ai-wiki/MCP-Integration.md`, in the `### HTTP/SSE Mode (watch FreeCAD update live)` section, immediately **before** the paragraph beginning `Set `MCP_HOST` / `MCP_PORT``, insert:

```markdown
**From the toolbar.** The simplest route needs no path and no command line:
switch to the **FreeCAD AI** workbench and click **MCP Server** on the toolbar
(also in the FreeCAD AI menu). It starts the server in the running FreeCAD and
prints the URL to the Report view; click again to stop it. Host and port are
under **AI Settings → MCP Servers**.

The button reflects reality rather than its own state, so a server started by
either method below shows as already running and can be stopped from the same
button.

> **The MCP server has no authentication.** Anything that can reach the address
> it is bound to can run FreeCAD tools, including arbitrary Python. On the
> `127.0.0.1` default that means any process on your machine. Tracked in
> [#59](https://github.com/ghbalf/freecad-ai/issues/59).
```

- [ ] **Step 4: Commit and push the wiki**

```bash
cd /home/alf/Projects/programming/misc/freecad-ai-wiki
git add MCP-Integration.md
git commit -m "wiki: document the MCP server toolbar toggle

Third way to start the server, and the one that needs no path. Also
states the no-authentication exposure at the point where someone is
deciding what to bind to (#59)."
git push
```

---

## Done when

- `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q` is green, with the new `test_mcp_gui_server.py`, `test_settings_dialog_mcp_address.py`, and the additions to `test_mcp_sse_transport.py` and `test_config.py` included.
- All six manual checks in Task 6 Step 5 pass in a real FreeCAD 1.1.1.
- `CHANGELOG.md` and the wiki are updated; the wiki is pushed.
- Nothing in this plan added authentication (#59) or changed `0.0.0.0` handling (#60).
