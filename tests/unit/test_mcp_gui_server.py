"""Tests for the process-wide MCP server controller.

Three routes start this server in one FreeCAD process — the
mcp_server_http.py command-line argument, the documented
exec(open(...).read()) console snippet, and the toolbar toggle. They share
one controller so the toggle cannot misreport a server it did not start.
"""

import socket
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


def test_start_reaps_the_socket_a_dead_serve_thread_left_open():
    """A serve thread that dies must not squat the port for the session.

    ``serve_forever()`` returning without ``server_close()`` is exactly what
    an exception inside the serve thread leaves behind: is_running() goes
    False while the listening socket is still open. Before the reap in
    start(), every later start() on that port raised EADDRINUSE until FreeCAD
    was restarted.
    """
    controller = _controller()
    port = _free_port()
    try:
        controller.start("127.0.0.1", port)
        controller._transport._httpd.shutdown()  # thread exits, socket stays
        deadline = time.time() + 5
        while controller.is_running() and time.time() < deadline:
            time.sleep(0.05)
        assert controller.is_running() is False

        controller.start("127.0.0.1", port)  # must not raise EADDRINUSE
        assert controller.is_running() is True
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass  # and the new server really owns the port
    finally:
        controller.stop()


def test_get_server_controller_returns_one_shared_instance():
    assert get_server_controller() is get_server_controller()
