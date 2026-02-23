"""MCP Manager — owns all client connections and integrates MCP tools into the registry.

Singleton pattern: use get_mcp_manager() to get the global instance.
"""

import logging
from typing import Any

from ..tools.registry import ToolDefinition, ToolParam, ToolResult, ToolRegistry
from .client import MCPClient, MCPToolResult

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages all MCP client connections and tool registration."""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}

    def connect_all(self, server_configs: list[dict]):
        """Connect to all configured MCP servers.

        Each config: {"name": str, "command": str, "args": list, "env": dict, "enabled": bool}
        """
        for cfg in server_configs:
            if not cfg.get("enabled", True):
                continue

            name = cfg.get("name", "")
            if not name:
                continue

            command = [cfg["command"]] + cfg.get("args", [])
            env = cfg.get("env") or None

            try:
                client = MCPClient(name, command, env)
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
        """Register all MCP tools into a ToolRegistry as regular ToolDefinitions."""
        for server_name, client in self._clients.items():
            if not client.is_connected:
                continue
            for tool_info in client.tools:
                namespaced = f"{server_name}__{tool_info.name}"
                params = _json_schema_to_tool_params(tool_info.input_schema)

                # Capture variables for closure
                _client = client
                _tool_name = tool_info.name

                def make_handler(c, tn):
                    def handler(**kwargs) -> ToolResult:
                        mcp_result = c.call_tool(tn, kwargs)
                        return _mcp_result_to_tool_result(mcp_result)
                    return handler

                tool_def = ToolDefinition(
                    name=namespaced,
                    description=f"[{server_name}] {tool_info.description}",
                    parameters=params,
                    handler=make_handler(_client, _tool_name),
                    category="mcp",
                )
                registry.register(tool_def)

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
