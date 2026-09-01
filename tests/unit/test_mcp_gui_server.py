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
    resolve_allowed_hosts,
    resolve_auth_token,
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


# --- allowed-host resolution -----------------------------------------------
#
# Nothing configured must resolve to None, not to the loopback list. The
# transport's own ``allowed_hosts is None`` branch is what rejects a wildcard
# bind; handing it an explicit list — even the identical one — bypasses that
# guard and silently restores the every-client-gets-403 dead end of #60.

def test_allowed_hosts_is_none_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    assert resolve_allowed_hosts(None) is None


def test_allowed_hosts_is_none_when_the_config_list_is_empty(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    cfg = type("Cfg", (), {"mcp_server_allowed_hosts": []})()
    assert resolve_allowed_hosts(cfg) is None


def test_allowed_hosts_prefers_config_over_default(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    cfg = type("Cfg", (), {
        "mcp_server_allowed_hosts": ["fileserver.local", "192.168.1.50"]})()
    assert resolve_allowed_hosts(cfg) == ["fileserver.local", "192.168.1.50"]


def test_allowed_hosts_prefers_env_over_config(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "box.lan, 10.0.0.7")
    cfg = type("Cfg", (), {"mcp_server_allowed_hosts": ["ignored.local"]})()
    assert resolve_allowed_hosts(cfg) == ["box.lan", "10.0.0.7"]


def test_allowed_hosts_drops_blank_entries(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "box.lan, ,, 10.0.0.7,")
    assert resolve_allowed_hosts(None) == ["box.lan", "10.0.0.7"]


def test_allowed_hosts_rejects_a_wildcard_entry(monkeypatch):
    # "*" would re-open exactly the hole #60 declined to open: the Host
    # allowlist is the only thing standing between a wildcard bind and
    # unauthenticated remote tool execution until #59 lands a token.
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "box.lan, *")
    with pytest.raises(ValueError, match=r"\*"):
        resolve_allowed_hosts(None)


def test_allowed_hosts_rejects_a_wildcard_entry_from_config(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    cfg = type("Cfg", (), {"mcp_server_allowed_hosts": ["*"]})()
    with pytest.raises(ValueError, match=r"\*"):
        resolve_allowed_hosts(cfg)


# --- auth-token resolution --------------------------------------------------
#
# Unset must resolve to None, not "". The transport's own auth_token is None
# branch is what leaves the server unauthenticated, and "" would either be
# handed through as a token nobody can ever present (see the transport-level
# test for why that must not happen) or rely on every caller normalising it.

def test_auth_token_is_none_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    assert resolve_auth_token(None) is None


def test_auth_token_is_none_when_the_config_value_is_empty(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    cfg = type("Cfg", (), {"mcp_server_auth_token": ""})()
    assert resolve_auth_token(cfg) is None


def test_auth_token_prefers_config_over_default(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    cfg = type("Cfg", (), {"mcp_server_auth_token": "from-config"})()
    assert resolve_auth_token(cfg) == "from-config"


def test_auth_token_prefers_env_over_config(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "from-env")
    cfg = type("Cfg", (), {"mcp_server_auth_token": "from-config"})()
    assert resolve_auth_token(cfg) == "from-env"


# --- auth token reaches the transport ---------------------------------------

def test_start_with_an_auth_token_requires_it_on_the_transport():
    controller = _controller()
    port = _free_port()
    try:
        controller.start("127.0.0.1", port, auth_token="s3cr3t")
        transport = controller._transport
        assert transport._request_allowed(
            "127.0.0.1:%d" % port, None, None) is False
        assert transport._request_allowed(
            "127.0.0.1:%d" % port, None, "Bearer s3cr3t") is True
    finally:
        controller.stop()


def test_start_without_an_auth_token_keeps_the_server_unauthenticated():
    controller = _controller()
    port = _free_port()
    try:
        controller.start("127.0.0.1", port)
        transport = controller._transport
        assert transport._request_allowed(
            "127.0.0.1:%d" % port, None, None) is True
    finally:
        controller.stop()


# --- allowed hosts reach the transport -------------------------------------

def test_explicit_allowed_hosts_admit_a_non_loopback_client():
    # The escape hatch #66's own error message advertises: naming the host
    # clients actually dial makes a non-loopback bind usable.
    controller = _controller()
    port = _free_port()
    try:
        controller.start("127.0.0.1", port,
                         allowed_hosts=["fileserver.local"])
        transport = controller._transport
        assert transport._request_allowed("fileserver.local:%d" % port,
                                          None) is True
    finally:
        controller.stop()


def test_start_without_allowed_hosts_keeps_the_loopback_default():
    controller = _controller()
    port = _free_port()
    try:
        controller.start("127.0.0.1", port)
        transport = controller._transport
        assert transport._request_allowed("evil.example:%d" % port,
                                          None) is False
    finally:
        controller.stop()


# --- lifecycle -------------------------------------------------------------

def test_start_reports_running_and_returns_the_url():
    controller = _controller()
    port = _free_port()
    try:
        url = controller.start("127.0.0.1", port)
        assert url == "http://127.0.0.1:%d/mcp" % port
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


def test_the_advertised_url_is_the_streamable_endpoint():
    """The toolbar writes this string to the Report view, so it is the URL
    users copy into a client config. Point it at the transport that is not
    on a removal clock; /sse keeps serving for anyone already on it."""
    controller = _controller()
    port = _free_port()
    try:
        url = controller.start("127.0.0.1", port)
        assert url.endswith("/mcp")
    finally:
        controller.stop()


def test_get_server_controller_returns_one_shared_instance():
    assert get_server_controller() is get_server_controller()
