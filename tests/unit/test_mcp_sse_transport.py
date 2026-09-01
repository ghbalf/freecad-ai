"""Regression tests for the SSE MCP server transport (PR #29 review fixes).

Covers two issues found in code review:
  - SSE socket writes must be serialized under ``_sse_lock`` so the keepalive
    loop and a tool response (running on different ThreadingMixIn threads)
    cannot interleave bytes and corrupt the event stream.
  - The HTTP entry-point script must not reference ``__file__`` unguarded,
    because its documented ``exec(open(...).read())`` usage has no ``__file__``.
"""

import pathlib
import socket
import sys
import threading
import time
import types
import urllib.error
import urllib.request

import pytest

from freecad_ai.mcp.transport import SSEServerTransport


# ---------------------------------------------------------------------------
# Issue A — concurrent SSE writes must hold the lock
# ---------------------------------------------------------------------------

class _LockProbe:
    """Fake wfile that records whether ``_sse_lock`` is held during write().

    A plain ``threading.Lock`` returns ``False`` from ``acquire(blocking=False)``
    whenever it is already held, so this deterministically detects whether the
    transport serializes the actual write (not merely the pointer read).
    """

    def __init__(self, lock):
        self._lock = lock
        self.lock_held_during_write = None
        self.written = b""

    def write(self, data):
        acquired = self._lock.acquire(blocking=False)
        self.lock_held_during_write = not acquired
        if acquired:
            self._lock.release()
        self.written += data

    def flush(self):
        pass


def test_send_sse_holds_lock_during_write():
    transport = SSEServerTransport()
    probe = _LockProbe(transport._sse_lock)
    transport._sse_wfile = probe

    transport._send_sse({"jsonrpc": "2.0", "id": 1, "result": {}})

    assert probe.lock_held_during_write is True


def test_send_sse_frames_message_as_sse_event():
    transport = SSEServerTransport()
    probe = _LockProbe(transport._sse_lock)
    transport._sse_wfile = probe

    transport._send_sse({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})

    text = probe.written.decode()
    assert text.startswith("event: message\ndata: ")
    assert text.endswith("\n\n")
    assert '"id":7' in text


def test_write_locked_holds_lock_during_write():
    transport = SSEServerTransport()
    probe = _LockProbe(transport._sse_lock)
    transport._sse_wfile = probe

    assert transport._write_locked(b": keepalive\n\n") is True
    assert probe.lock_held_during_write is True
    assert probe.written == b": keepalive\n\n"


def test_write_locked_returns_false_without_client():
    transport = SSEServerTransport()
    transport._sse_wfile = None

    assert transport._write_locked(b"data") is False


def test_write_locked_clears_client_on_broken_pipe():
    transport = SSEServerTransport()

    class _Broken:
        def write(self, data):
            raise BrokenPipeError()

        def flush(self):
            pass

    transport._sse_wfile = _Broken()

    assert transport._write_locked(b"data") is False
    assert transport._sse_wfile is None


# ---------------------------------------------------------------------------
# Security — CORS / cross-origin tool invocation (drive-by RCE) guard
# ---------------------------------------------------------------------------

def test_request_allowed_for_loopback_native_client():
    transport = SSEServerTransport()
    # Native MCP clients connect over loopback and send no Origin header.
    assert transport._request_allowed("127.0.0.1:3000", None) is True
    assert transport._request_allowed("localhost:3000", None) is True


def test_request_allowed_for_ipv6_loopback():
    transport = SSEServerTransport()
    assert transport._request_allowed("[::1]:3000", None) is True


def test_request_rejected_for_cross_origin_browser_request():
    transport = SSEServerTransport()
    # A malicious web page's fetch() to localhost always carries an Origin.
    assert transport._request_allowed("127.0.0.1:3000", "https://evil.example") is False


def test_request_rejected_for_non_loopback_host_dns_rebinding():
    transport = SSEServerTransport()
    assert transport._request_allowed("evil.example", None) is False


def test_request_rejected_for_missing_host():
    transport = SSEServerTransport()
    assert transport._request_allowed(None, None) is False
    assert transport._request_allowed("", None) is False


def test_request_allows_explicitly_configured_bind_host():
    transport = SSEServerTransport(host="192.168.1.5")
    assert transport._request_allowed("192.168.1.5:3000", None) is True


@pytest.mark.parametrize("wildcard_host", ["0.0.0.0", "::"])
def test_wildcard_bind_host_rejected_instead_of_locking_out_every_client(
        wildcard_host):
    # A wildcard bind address is not a name any real client's Host header
    # ever carries, so seeding the default allowlist from it locks out every
    # LAN client instead of the loopback-only client it was meant to guard.
    with pytest.raises(OSError, match="MCP_HOST"):
        SSEServerTransport(host=wildcard_host)


def test_wildcard_bind_host_allowed_with_explicit_allowed_hosts():
    transport = SSEServerTransport(
        host="0.0.0.0", allowed_hosts=["fileserver.local"])
    assert transport._request_allowed("fileserver.local:3000", None) is True


# ---------------------------------------------------------------------------
# Optional bearer token (#59): access control on top of the Host/Origin
# checks above, which are DNS-rebinding/CSRF guards, not authentication.
# ---------------------------------------------------------------------------

def test_no_token_configured_keeps_prior_behaviour():
    # Host/Origin alone still authorize the request when auth_token is unset
    # (the default), matching every currently documented start-up recipe.
    transport = SSEServerTransport()
    assert transport._request_allowed("127.0.0.1:3000", None) is True
    assert transport._request_allowed("127.0.0.1:3000", None, None) is True


def test_token_configured_rejects_a_request_with_no_authorization_header():
    transport = SSEServerTransport(auth_token="s3cr3t")
    assert transport._request_allowed("127.0.0.1:3000", None, None) is False


def test_token_configured_accepts_the_matching_bearer_header():
    transport = SSEServerTransport(auth_token="s3cr3t")
    assert transport._request_allowed(
        "127.0.0.1:3000", None, "Bearer s3cr3t") is True


def test_token_configured_rejects_a_wrong_token():
    transport = SSEServerTransport(auth_token="s3cr3t")
    assert transport._request_allowed(
        "127.0.0.1:3000", None, "Bearer wrong") is False


def test_token_configured_rejects_a_non_bearer_scheme():
    transport = SSEServerTransport(auth_token="s3cr3t")
    assert transport._request_allowed(
        "127.0.0.1:3000", None, "Basic s3cr3t") is False


def test_token_check_is_case_insensitive_on_the_scheme():
    # RFC 7235 auth-schemes are case-insensitive; a strict "Bearer" match
    # would reject a spec-compliant "bearer" from a client that lowercases it.
    transport = SSEServerTransport(auth_token="s3cr3t")
    assert transport._request_allowed(
        "127.0.0.1:3000", None, "bearer s3cr3t") is True


def test_empty_string_token_disables_auth_rather_than_requiring_an_empty_one():
    # An empty token can never be presented by any client (there is no header
    # value that parses to ""), so treating "" as "enforce an empty token"
    # would make the endpoint permanently unreachable. "" must mean disabled,
    # same as None.
    transport = SSEServerTransport(auth_token="")
    assert transport._request_allowed("127.0.0.1:3000", None, None) is True


def test_host_and_origin_checks_still_apply_alongside_a_token():
    # The token is an additional gate, not a replacement for the existing
    # DNS-rebinding/CSRF checks.
    transport = SSEServerTransport(auth_token="s3cr3t")
    assert transport._request_allowed(
        "evil.example:3000", None, "Bearer s3cr3t") is False
    assert transport._request_allowed(
        "127.0.0.1:3000", "https://evil.example", "Bearer s3cr3t") is False


def test_cross_origin_post_rejected_and_no_wildcard_cors_header():
    transport = SSEServerTransport(host="127.0.0.1", port=0)
    transport._handler = lambda msg: {
        "jsonrpc": "2.0", "id": msg.get("id"), "result": {}
    }
    server = transport._make_server()
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # A cross-origin browser POST is rejected server-side.
        cross = urllib.request.Request(
            f"http://127.0.0.1:{port}/messages",
            data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
            headers={"Content-Type": "application/json",
                     "Origin": "https://evil.example"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(cross, timeout=5)
        assert exc.value.code == 403

        # A native client POST (no Origin) is accepted and exposes no
        # permissive CORS header.
        native = urllib.request.Request(
            f"http://127.0.0.1:{port}/messages",
            data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(native, timeout=5)
        assert resp.status == 202
        assert resp.headers.get("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        server.server_close()


def test_live_request_with_a_token_configured_requires_it():
    transport = SSEServerTransport(host="127.0.0.1", port=0, auth_token="s3cr3t")
    transport._handler = lambda msg: {
        "jsonrpc": "2.0", "id": msg.get("id"), "result": {}
    }
    server = transport._make_server()
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        no_token = urllib.request.Request(
            f"http://127.0.0.1:{port}/messages",
            data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(no_token, timeout=5)
        assert exc.value.code == 403

        wrong_token = urllib.request.Request(
            f"http://127.0.0.1:{port}/messages",
            data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer wrong"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(wrong_token, timeout=5)
        assert exc.value.code == 403

        right_token = urllib.request.Request(
            f"http://127.0.0.1:{port}/messages",
            data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer s3cr3t"},
            method="POST",
        )
        resp = urllib.request.urlopen(right_token, timeout=5)
        assert resp.status == 202
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Issue D — entry-point script must be safe under exec() (no __file__)
# ---------------------------------------------------------------------------

def test_http_entry_point_safe_under_exec():
    """exec(open('mcp_server_http.py').read()) must not raise NameError on __file__.

    The script's docstring documents this exact usage. ``exec`` of source text
    defines no ``__file__``; the script must guard the reference. Running it in
    a unit env can't import the FreeCAD C++ module, so reaching ``import
    FreeCAD`` (ImportError) proves we got past the ``__file__`` handling.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    source = (repo_root / "mcp_server_http.py").read_text()
    code = compile(source, "mcp_server_http.py", "exec")

    namespace = {}  # mimics exec(open(...).read()): no __file__ defined
    try:
        exec(code, namespace)
    except (ImportError, ModuleNotFoundError):
        pass  # reached `import FreeCAD` — past the __file__ guard, as intended
    except NameError as exc:
        if "__file__" in str(exc):
            pytest.fail(
                "mcp_server_http.py references __file__ unguarded — "
                "breaks the documented exec(open(...).read()) usage"
            )
        raise


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
        def start(self, host, port, allowed_hosts=None, auth_token=None):
            started.append((host, port, allowed_hosts, auth_token))
            return "http://%s:%d/sse" % (host, port)

    import freecad_ai.mcp.gui_server as gui_server
    monkeypatch.setattr(gui_server, "get_server_controller",
                        lambda: _FakeController())

    # Pin all four so the assertion cannot depend on the developer's
    # config.json or environment.
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "3131")
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

    exec(compile(source, "mcp_server_http.py", "exec"), {})

    # allowed_hosts stays None and auth_token stays None with nothing
    # configured, so the transport keeps deriving its own default (and with
    # it the wildcard-bind rejection) and stays unauthenticated.
    assert started == [("127.0.0.1", 3131, None, None)]


def test_http_entry_point_forwards_the_allowed_hosts_env_var(monkeypatch):
    """MCP_ALLOWED_HOSTS must reach the transport, not just parse cleanly."""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    source = (repo_root / "mcp_server_http.py").read_text()

    fake_freecad = types.ModuleType("FreeCAD")
    fake_freecad.ActiveDocument = object()
    fake_freecad.newDocument = lambda name: None
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)

    started = []

    class _FakeController:
        def start(self, host, port, allowed_hosts=None, auth_token=None):
            started.append((host, port, allowed_hosts, auth_token))
            return "http://%s:%d/sse" % (host, port)

    import freecad_ai.mcp.gui_server as gui_server
    monkeypatch.setattr(gui_server, "get_server_controller",
                        lambda: _FakeController())

    monkeypatch.setenv("MCP_HOST", "192.168.1.50")
    monkeypatch.setenv("MCP_PORT", "3131")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "fileserver.local, 192.168.1.50")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

    exec(compile(source, "mcp_server_http.py", "exec"), {})

    assert started == [("192.168.1.50", 3131,
                        ["fileserver.local", "192.168.1.50"], None)]


def test_http_entry_point_forwards_the_auth_token_env_var(monkeypatch):
    """MCP_AUTH_TOKEN must reach the transport, not just parse cleanly."""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    source = (repo_root / "mcp_server_http.py").read_text()

    fake_freecad = types.ModuleType("FreeCAD")
    fake_freecad.ActiveDocument = object()
    fake_freecad.newDocument = lambda name: None
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)

    started = []

    class _FakeController:
        def start(self, host, port, allowed_hosts=None, auth_token=None):
            started.append((host, port, allowed_hosts, auth_token))
            return "http://%s:%d/sse" % (host, port)

    import freecad_ai.mcp.gui_server as gui_server
    monkeypatch.setattr(gui_server, "get_server_controller",
                        lambda: _FakeController())

    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "3131")
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("MCP_AUTH_TOKEN", "s3cr3t")

    exec(compile(source, "mcp_server_http.py", "exec"), {})

    assert started == [("127.0.0.1", 3131, None, "s3cr3t")]


# ---------------------------------------------------------------------------
# Issue #63 — a wedged SSE client must not be able to freeze stop()
# ---------------------------------------------------------------------------
#
# ``_write_locked`` holds ``_sse_lock`` across the write *and* flush (that is
# deliberate — see Issue A above), and ``stop()`` needs the same lock to detach
# the client. With no socket timeout, a client that stops reading lets the send
# buffer fill and blocks the writing thread inside the lock forever, so a
# ``stop()`` called from the Qt main thread freezes the whole FreeCAD GUI.
#
# The end-to-end freeze is not reproducible in a unit test: it needs a real
# peer that has stopped reading plus megabytes of payload to fill the socket
# buffers. These two tests pin the mechanism that prevents it instead — the
# connection has a finite timeout, and a timed-out write is treated as a
# dropped client rather than propagating.

def test_sse_connection_has_a_send_timeout():
    """Without this the write blocks forever and stop() can never get the lock."""
    transport = SSEServerTransport(port=0)
    transport.bind()
    try:
        handler_cls = transport._httpd.RequestHandlerClass
        # StreamRequestHandler.setup() only calls settimeout() when this is
        # not None, so None means "block indefinitely".
        assert handler_cls.timeout is not None
        assert handler_cls.timeout > 0
    finally:
        transport.stop()


def test_write_locked_drops_the_client_on_timeout():
    """socket.timeout is TimeoutError (a subclass of OSError) since Python 3.10,
    so the existing handler already covers it — this pins that coupling, because
    narrowing the except clause would reintroduce the hang."""

    class _TimingOutWfile:
        def write(self, data):
            raise socket.timeout("timed out")

        def flush(self):  # pragma: no cover - never reached
            pass

    transport = SSEServerTransport()
    transport._sse_wfile = _TimingOutWfile()

    assert transport._write_locked(b"payload") is False
    assert transport._sse_wfile is None
    # The lock must be free afterwards, or stop() would still block on it.
    assert transport._sse_lock.acquire(blocking=False)
    transport._sse_lock.release()


def test_sse_server_transport_is_an_alias_of_http_server_transport():
    """The class serves /mcp too now, but the old name must keep importing.

    mcp_server_entry.py, the wiki and user scripts all name SSEServerTransport.
    """
    from freecad_ai.mcp.transport import HTTPServerTransport, SSEServerTransport

    assert SSEServerTransport is HTTPServerTransport
