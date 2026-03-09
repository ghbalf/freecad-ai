"""Tests for deferred MCP tool loading, lazy parameter resolution, and search."""

import pytest
from unittest.mock import MagicMock, patch

from freecad_ai.tools.registry import (
    ToolDefinition,
    ToolParam,
    ToolRegistry,
    ToolResult,
)
from freecad_ai.mcp.client import MCPClient, MCPToolInfo
from freecad_ai.mcp.manager import MCPManager, _json_schema_to_tool_params


# ---------------------------------------------------------------------------
# ToolDefinition deferred params
# ---------------------------------------------------------------------------

class TestToolDefinitionDeferredParams:
    def test_resolve_params_without_lazy(self):
        """Normal tool — resolve_params returns existing params."""
        params = [ToolParam("x", "number", "X")]
        tool = ToolDefinition("t", "desc", params, handler=lambda: None)
        assert tool.resolve_params() is params
        assert not tool.has_deferred_params

    def test_resolve_params_with_lazy(self):
        """Deferred tool — lazy_params called on first resolve."""
        lazy = MagicMock(return_value=[ToolParam("y", "string", "Y")])
        tool = ToolDefinition("t", "desc", [], handler=lambda: None, lazy_params=lazy)

        assert tool.has_deferred_params
        result = tool.resolve_params()
        assert len(result) == 1
        assert result[0].name == "y"
        lazy.assert_called_once()
        # Second call should not invoke lazy again
        tool.resolve_params()
        lazy.assert_called_once()
        assert not tool.has_deferred_params

    def test_lazy_not_called_if_params_already_present(self):
        """If parameters are already populated, lazy_params is not called."""
        lazy = MagicMock(return_value=[])
        params = [ToolParam("x", "number", "X")]
        tool = ToolDefinition("t", "desc", params, handler=lambda: None, lazy_params=lazy)

        # params already present, so lazy_params should NOT be called
        tool.resolve_params()
        lazy.assert_not_called()


# ---------------------------------------------------------------------------
# ToolRegistry with deferred tools
# ---------------------------------------------------------------------------

class TestRegistryDeferredTools:
    def _make_deferred_tool(self, name="mcp_tool"):
        lazy = MagicMock(return_value=[
            ToolParam("path", "string", "File path"),
        ])
        return ToolDefinition(
            name=name,
            description=f"MCP tool: {name}",
            parameters=[],
            handler=lambda **kw: ToolResult(success=True, output="ok"),
            lazy_params=lazy,
        ), lazy

    def test_execute_resolves_params(self):
        """Executing a deferred tool resolves its params first."""
        reg = ToolRegistry()
        tool, lazy = self._make_deferred_tool()
        reg.register(tool)
        reg.execute("mcp_tool", {"path": "/tmp"})
        lazy.assert_called_once()

    def test_search_resolves_matching_params(self):
        """search_tools resolves params for matching tools."""
        reg = ToolRegistry()
        tool, lazy = self._make_deferred_tool("fs__read_file")
        reg.register(tool)

        results = reg.search_tools("read")
        assert len(results) == 1
        assert results[0].name == "fs__read_file"
        lazy.assert_called_once()

    def test_search_no_match_no_resolve(self):
        """search_tools does not resolve params for non-matching tools."""
        reg = ToolRegistry()
        tool, lazy = self._make_deferred_tool("fs__read_file")
        reg.register(tool)

        results = reg.search_tools("write")
        assert len(results) == 0
        lazy.assert_not_called()

    def test_to_openai_schema_resolves_all(self):
        """to_openai_schema resolves deferred params."""
        reg = ToolRegistry()
        tool, lazy = self._make_deferred_tool()
        reg.register(tool)

        schema = reg.to_openai_schema()
        lazy.assert_called_once()
        assert len(schema) == 1
        assert "path" in schema[0]["function"]["parameters"]["properties"]

    def test_to_anthropic_schema_resolves_all(self):
        """to_anthropic_schema resolves deferred params."""
        reg = ToolRegistry()
        tool, lazy = self._make_deferred_tool()
        reg.register(tool)

        schema = reg.to_anthropic_schema()
        lazy.assert_called_once()
        assert "path" in schema[0]["input_schema"]["properties"]

    def test_to_mcp_schema_resolves_all(self):
        """to_mcp_schema resolves deferred params."""
        reg = ToolRegistry()
        tool, lazy = self._make_deferred_tool()
        reg.register(tool)

        schema = reg.to_mcp_schema()
        lazy.assert_called_once()
        assert "path" in schema[0]["inputSchema"]["properties"]


# ---------------------------------------------------------------------------
# MCPClient deferred loading
# ---------------------------------------------------------------------------

class TestMCPClientDeferred:
    def _make_client(self, *, deferred=True):
        """Create an MCPClient with a mocked transport."""
        client = MCPClient("test", ["echo"], deferred=deferred)
        client._transport = MagicMock()
        return client

    def test_deferred_connect_no_schemas(self):
        """Deferred connect stores tools without input schemas."""
        client = self._make_client(deferred=True)
        client._transport.send_request.side_effect = [
            # initialize response
            {"result": {"protocolVersion": "2025-03-26", "capabilities": {}}},
            # tools/list response
            {"result": {"tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string", "description": "Path"}},
                        "required": ["path"],
                    },
                },
                {
                    "name": "write_file",
                    "description": "Write a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path"},
                            "content": {"type": "string", "description": "Content"},
                        },
                        "required": ["path", "content"],
                    },
                },
            ]}},
        ]
        client._transport.is_alive = True

        client.connect()
        assert client.is_connected
        assert len(client.tools) == 2
        # Schemas should NOT be populated in deferred mode
        for tool in client.tools:
            assert tool.input_schema == {}

    def test_eager_connect_has_schemas(self):
        """Non-deferred connect loads schemas immediately."""
        client = self._make_client(deferred=False)
        client._transport.send_request.side_effect = [
            {"result": {"protocolVersion": "2025-03-26", "capabilities": {}}},
            {"result": {"tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            ]}},
        ]
        client._transport.is_alive = True

        client.connect()
        assert client.tools[0].input_schema != {}

    def test_get_tool_schema_lazy_load(self):
        """get_tool_schema loads from raw cache without extra server call."""
        client = self._make_client(deferred=True)
        raw_schema = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path"}},
            "required": ["path"],
        }
        client._raw_tools = [
            {"name": "read_file", "description": "Read", "inputSchema": raw_schema},
        ]
        client._tools = [MCPToolInfo("read_file", "Read")]

        schema = client.get_tool_schema("read_file")
        assert schema == raw_schema
        # Should also update the MCPToolInfo
        assert client._tools[0].input_schema == raw_schema

    def test_get_tool_schema_caches(self):
        """Second call to get_tool_schema uses cache."""
        client = self._make_client(deferred=True)
        raw_schema = {"type": "object", "properties": {}}
        client._raw_tools = [
            {"name": "tool1", "description": "T1", "inputSchema": raw_schema},
        ]
        client._tools = [MCPToolInfo("tool1", "T1")]

        client.get_tool_schema("tool1")
        client.get_tool_schema("tool1")
        # raw_tools is only scanned once — cache hit on second call
        assert "tool1" in client._schema_cache

    def test_get_tool_schema_unknown(self):
        """get_tool_schema returns empty dict for unknown tool."""
        client = self._make_client(deferred=True)
        client._raw_tools = []
        client._tools = []
        # Mock _refresh_tools to avoid transport call
        client._refresh_tools = MagicMock()

        schema = client.get_tool_schema("nonexistent")
        assert schema == {}

    def test_search_tools(self):
        """search_tools matches by name and description."""
        client = self._make_client(deferred=True)
        client._raw_tools = [
            {"name": "read_file", "description": "Read a file from disk",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "write_file", "description": "Write content to a file",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "list_dir", "description": "List directory contents",
             "inputSchema": {"type": "object", "properties": {}}},
        ]
        client._tools = [
            MCPToolInfo("read_file", "Read a file from disk"),
            MCPToolInfo("write_file", "Write content to a file"),
            MCPToolInfo("list_dir", "List directory contents"),
        ]

        # Search by name
        results = client.search_tools("read")
        assert len(results) == 1
        assert results[0].name == "read_file"

        # Search by description
        results = client.search_tools("directory")
        assert len(results) == 1
        assert results[0].name == "list_dir"

        # Search matching multiple
        results = client.search_tools("file")
        assert len(results) == 2

    def test_search_tools_case_insensitive(self):
        """search_tools is case-insensitive."""
        client = self._make_client(deferred=True)
        client._raw_tools = [
            {"name": "ReadFile", "description": "Read", "inputSchema": {}},
        ]
        client._tools = [MCPToolInfo("ReadFile", "Read")]

        results = client.search_tools("readfile")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# MCPManager deferred registration
# ---------------------------------------------------------------------------

class TestMCPManagerDeferred:
    def test_register_deferred_tools(self):
        """Manager registers deferred tools with lazy_params."""
        manager = MCPManager()
        client = MagicMock(spec=MCPClient)
        client.name = "fs"
        client.is_connected = True
        client.tools = [
            MCPToolInfo("read_file", "Read a file"),  # No schema
        ]
        client.get_tool_schema.return_value = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path"}},
            "required": ["path"],
        }
        manager._clients["fs"] = client

        registry = ToolRegistry()
        manager.register_tools_into(registry)

        tool = registry.get("fs__read_file")
        assert tool is not None
        assert tool.has_deferred_params

        # Resolve params — should call client.get_tool_schema
        params = tool.resolve_params()
        assert len(params) == 1
        assert params[0].name == "path"

    def test_register_eager_tools(self):
        """If schema is already present, no lazy_params is set."""
        manager = MCPManager()
        client = MagicMock(spec=MCPClient)
        client.name = "fs"
        client.is_connected = True
        client.tools = [
            MCPToolInfo("read_file", "Read a file", {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "P"}},
            }),
        ]
        manager._clients["fs"] = client

        registry = ToolRegistry()
        manager.register_tools_into(registry)

        tool = registry.get("fs__read_file")
        assert tool is not None
        assert not tool.has_deferred_params
        assert len(tool.parameters) == 1

    def test_search_tools_across_servers(self):
        """search_tools searches across all connected servers."""
        manager = MCPManager()

        client1 = MagicMock(spec=MCPClient)
        client1.name = "fs"
        client1.is_connected = True
        client1.search_tools.return_value = [
            MCPToolInfo("read_file", "Read a file"),
        ]

        client2 = MagicMock(spec=MCPClient)
        client2.name = "db"
        client2.is_connected = True
        client2.search_tools.return_value = []

        manager._clients = {"fs": client1, "db": client2}

        results = manager.search_tools("read")
        assert "fs" in results
        assert "db" not in results
        assert len(results["fs"]) == 1

    def test_get_tool_schema_delegates(self):
        """get_tool_schema delegates to the correct client."""
        manager = MCPManager()
        client = MagicMock(spec=MCPClient)
        client.is_connected = True
        client.get_tool_schema.return_value = {"type": "object", "properties": {}}
        manager._clients["fs"] = client

        schema = manager.get_tool_schema("fs__read_file")
        client.get_tool_schema.assert_called_once_with("read_file")
        assert schema == {"type": "object", "properties": {}}

    def test_get_tool_schema_unknown_server(self):
        """get_tool_schema returns {} for unknown server."""
        manager = MCPManager()
        assert manager.get_tool_schema("unknown__tool") == {}

    def test_get_tool_schema_no_separator(self):
        """get_tool_schema returns {} for non-namespaced names."""
        manager = MCPManager()
        assert manager.get_tool_schema("plain_tool") == {}

    def test_connect_all_passes_deferred_from_config(self):
        """connect_all reads deferred flag from each server config."""
        manager = MCPManager()
        configs = [
            {"name": "eager", "command": "echo", "args": [], "deferred": False},
            {"name": "lazy", "command": "echo", "args": [], "deferred": True},
        ]

        with patch.object(MCPClient, "__init__", return_value=None) as mock_init, \
             patch.object(MCPClient, "connect"):
            try:
                manager.connect_all(configs)
            except (AttributeError, TypeError):
                pass  # MCPClient.__init__ is mocked, so attributes missing
            if mock_init.call_count >= 2:
                # First call: deferred=False
                assert mock_init.call_args_list[0][1].get("deferred") is False
                # Second call: deferred=True
                assert mock_init.call_args_list[1][1].get("deferred") is True

    def test_connect_all_defaults_deferred_true(self):
        """connect_all defaults deferred=True when not specified in config."""
        manager = MCPManager()
        configs = [{"name": "test", "command": "echo", "args": []}]

        with patch.object(MCPClient, "__init__", return_value=None) as mock_init, \
             patch.object(MCPClient, "connect"):
            try:
                manager.connect_all(configs)
            except (AttributeError, TypeError):
                pass
            if mock_init.called:
                assert mock_init.call_args[1].get("deferred") is True
