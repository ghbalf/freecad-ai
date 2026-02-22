"""MCP client — connects to one external MCP server.

Handles the initialize handshake, tool discovery, and tool invocation
over a StdioClientTransport.
"""

import logging
from dataclasses import dataclass, field

from .transport import StdioClientTransport

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "FreeCAD AI", "version": "0.1.0"}


@dataclass
class MCPToolInfo:
    """Metadata for a tool discovered from an MCP server."""
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


@dataclass
class MCPToolResult:
    """Result of calling a tool on an MCP server."""
    content: list[dict] = field(default_factory=list)
    is_error: bool = False


class MCPClient:
    """Connection to a single MCP server."""

    def __init__(self, name: str, command: list[str], env: dict | None = None):
        self.name = name
        self._transport = StdioClientTransport(command, env)
        self._tools: list[MCPToolInfo] = []
        self._connected = False

    def connect(self):
        """Start transport, perform initialize handshake, discover tools."""
        self._transport.start()

        # Initialize handshake
        resp = self._transport.send_request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })

        if "error" in resp:
            raise RuntimeError(
                f"MCP server '{self.name}' initialization failed: {resp['error']}"
            )

        # Send initialized notification
        self._transport.send_notification("notifications/initialized")

        # Discover tools
        self._refresh_tools()
        self._connected = True
        logger.info(
            "MCP client '%s' connected — %d tools available",
            self.name, len(self._tools),
        )

    def _refresh_tools(self):
        """Fetch the tool list from the server."""
        resp = self._transport.send_request("tools/list")
        if "error" in resp:
            logger.warning("MCP tools/list failed for '%s': %s", self.name, resp["error"])
            self._tools = []
            return

        raw_tools = resp.get("result", {}).get("tools", [])
        self._tools = [
            MCPToolInfo(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in raw_tools
        ]

    @property
    def tools(self) -> list[MCPToolInfo]:
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict) -> MCPToolResult:
        """Invoke a tool on the MCP server."""
        resp = self._transport.send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        if "error" in resp:
            return MCPToolResult(
                content=[{"type": "text", "text": str(resp["error"])}],
                is_error=True,
            )

        result = resp.get("result", {})
        return MCPToolResult(
            content=result.get("content", []),
            is_error=result.get("isError", False),
        )

    def disconnect(self):
        """Stop the transport."""
        self._connected = False
        self._transport.stop()
        logger.info("MCP client '%s' disconnected", self.name)

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport.is_alive
