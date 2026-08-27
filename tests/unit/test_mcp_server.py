"""Tests for the MCP server's JSON-RPC handling (freecad_ai.mcp.server)."""

import freecad_ai
from freecad_ai.mcp import server as server_mod
from freecad_ai.mcp.server import PROTOCOL_VERSION, SERVER_INFO, MCPServer
from freecad_ai.tools.registry import ToolDefinition, ToolParam, ToolRegistry, ToolResult


def _server(registry=None):
    return MCPServer(registry if registry is not None else ToolRegistry())


def _handle(srv, msg: dict) -> dict:
    """Call the handler and assert a response came back, for the typed paths."""
    resp = srv._handle(msg)
    assert resp is not None
    return resp


def _initialize(srv) -> dict:
    return _handle(srv, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}},
    })


class TestServerInfo:
    def test_reports_the_installed_version(self):
        """serverInfo.version must track the addon version.

        It was hardcoded to "0.1.0", so every MCP client displayed
        "FreeCAD AI 0.1.0" no matter which release was installed — actively
        misleading when debugging someone else's setup.
        """
        assert SERVER_INFO["version"] == freecad_ai.__version__

    def test_version_is_not_the_stale_literal(self):
        """Guards the specific regression: a literal that never moves.

        Pinning only against __version__ would still pass if someone pasted
        today's version back in as a literal, which is how this drifted in the
        first place.
        """
        assert SERVER_INFO["version"] != "0.1.0" or freecad_ai.__version__ == "0.1.0"
        assert server_mod.SERVER_INFO["version"] is not None

    def test_name_is_stable(self):
        """Clients key display (and sometimes config) off the server name."""
        assert SERVER_INFO["name"] == "FreeCAD AI"

    def test_initialize_response_carries_server_info(self):
        """The value must actually reach the wire, not just the module."""
        resp = _initialize(_server())
        info = resp["result"]["serverInfo"]
        assert info["name"] == "FreeCAD AI"
        assert info["version"] == freecad_ai.__version__

    def test_initialize_reports_protocol_and_capabilities(self):
        resp = _initialize(_server())
        assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert "tools" in resp["result"]["capabilities"]
        assert resp["id"] == 1


class TestHandleRouting:
    def test_initialized_notification_returns_nothing(self):
        """Notifications have no id and must not produce a response."""
        assert _server()._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_tools_list_exposes_registered_tools(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            "do_thing", "Does a thing",
            [ToolParam("x", "number", "X")],
            handler=lambda x=0: ToolResult(True, f"did {x}"),
        ))
        resp = _handle(_server(reg), {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == ["do_thing"]
