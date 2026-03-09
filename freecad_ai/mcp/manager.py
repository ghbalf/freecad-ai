"""MCP Manager — owns all client connections and integrates MCP tools into the registry.

Singleton pattern: use get_mcp_manager() to get the global instance.

Supports deferred tool loading: MCP tools are registered into the ToolRegistry
with a lazy parameter loader. Full input schemas are only fetched from the
MCP server when a tool is first used or explicitly searched for.
"""

import logging
from typing import Any

from ..tools.registry import ToolDefinition, ToolParam, ToolResult, ToolRegistry
from .client import MCPClient, MCPToolInfo, MCPToolResult

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages all MCP client connections and tool registration."""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}

    def connect_all(self, server_configs: list[dict]):
        """Connect to all configured MCP servers.

        Each config: {"name": str, "command": str, "args": list,
                      "env": dict, "enabled": bool, "deferred": bool}

        The per-server ``deferred`` flag (default True) controls whether tool
        schemas are loaded lazily on demand or eagerly on connect.
        """
        for cfg in server_configs:
            if not cfg.get("enabled", True):
                continue

            name = cfg.get("name", "")
            if not name:
                continue

            command = [cfg["command"]] + cfg.get("args", [])
            env = cfg.get("env") or None
            deferred = cfg.get("deferred", True)

            try:
                client = MCPClient(name, command, env, deferred=deferred)
                client.connect()
                self._clients[name] = client
            except Exception as e:
                logger.error("Failed to connect MCP server '%s': %s", name, e)

    def disconnect_all(self):
        """Disconnect all MCP clients."""
        for client in self._clients.values():
            try:
                client.disconnect()
            except Exception as e:
                logger.warning("Error disconnecting MCP client '%s': %s", client.name, e)
        self._clients.clear()

    def register_tools_into(self, registry: ToolRegistry):
        """Register all MCP tools into a ToolRegistry as regular ToolDefinitions.

        When the client uses deferred loading, tools are registered with a lazy
        parameter loader — the full input schema is only fetched from the MCP
        server when the tool's parameters are first accessed.
        """
        for server_name, client in self._clients.items():
            if not client.is_connected:
                continue
            for tool_info in client.tools:
                namespaced = f"{server_name}__{tool_info.name}"

                # Build params eagerly if schema is already loaded,
                # otherwise set up lazy loading
                if tool_info.input_schema:
                    params = _json_schema_to_tool_params(tool_info.input_schema)
                    lazy_params = None
                else:
                    params = []
                    # Capture for closure
                    _client = client
                    _tool_name = tool_info.name

                    def make_lazy_loader(c, tn):
                        def loader() -> list[ToolParam]:
                            schema = c.get_tool_schema(tn)
                            return _json_schema_to_tool_params(schema)
                        return loader

                    lazy_params = make_lazy_loader(_client, _tool_name)

                # Capture variables for handler closure
                _client_h = client
                _tool_name_h = tool_info.name

                def make_handler(c, tn):
                    def handler(**kwargs) -> ToolResult:
                        mcp_result = c.call_tool(tn, kwargs)
                        return _mcp_result_to_tool_result(mcp_result)
                    return handler

                tool_def = ToolDefinition(
                    name=namespaced,
                    description=f"[{server_name}] {tool_info.description}",
                    parameters=params,
                    handler=make_handler(_client_h, _tool_name_h),
                    category="mcp",
                    lazy_params=lazy_params,
                )
                registry.register(tool_def)

    def search_tools(self, query: str) -> dict[str, list[MCPToolInfo]]:
        """Search for tools across all connected MCP servers.

        Returns a dict mapping server name to matching MCPToolInfo entries.
        Schemas are loaded for all matching tools.
        """
        results = {}
        for server_name, client in self._clients.items():
            if not client.is_connected:
                continue
            matches = client.search_tools(query)
            if matches:
                results[server_name] = matches
        return results

    def get_tool_schema(self, namespaced_name: str) -> dict:
        """Get the full input schema for a namespaced MCP tool.

        The name should be in "server__tool" format.
        Returns empty dict if not found.
        """
        if "__" not in namespaced_name:
            return {}
        server_name, tool_name = namespaced_name.split("__", 1)
        client = self._clients.get(server_name)
        if client and client.is_connected:
            return client.get_tool_schema(tool_name)
        return {}

    def is_mcp_tool(self, name: str) -> bool:
        """Check if a tool name belongs to an MCP server."""
        return "__" in name and name.split("__", 1)[0] in self._clients

    @property
    def connected_servers(self) -> list[str]:
        return [n for n, c in self._clients.items() if c.is_connected]


def _mcp_result_to_tool_result(mcp_result: MCPToolResult) -> ToolResult:
    """Convert an MCPToolResult to a ToolResult."""
    text_parts = []
    for item in mcp_result.content:
        if item.get("type") == "text":
            text_parts.append(item.get("text", ""))
        else:
            text_parts.append(str(item))

    output = "\n".join(text_parts)

    if mcp_result.is_error:
        return ToolResult(success=False, output="", error=output)
    return ToolResult(success=True, output=output)


def _json_schema_to_tool_params(schema: dict) -> list[ToolParam]:
    """Convert a JSON Schema object to a list of ToolParam."""
    if not schema or schema.get("type") != "object":
        return []

    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    params = []

    for name, prop in properties.items():
        param = ToolParam(
            name=name,
            type=prop.get("type", "string"),
            description=prop.get("description", ""),
            required=name in required_set,
            enum=prop.get("enum"),
            default=prop.get("default"),
            items=prop.get("items"),
        )
        params.append(param)

    return params


# Singleton
_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """Get the global MCPManager singleton."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
