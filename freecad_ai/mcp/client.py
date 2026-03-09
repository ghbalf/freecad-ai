"""MCP client — connects to one external MCP server.

Handles the initialize handshake, tool discovery, and tool invocation
over a StdioClientTransport.

Supports deferred tool loading: on connect, only tool names and descriptions
are stored. Full input schemas are fetched lazily on first access via
get_tool_schema(). A search_tools() method allows keyword-based filtering.
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
    """Connection to a single MCP server.

    When ``deferred=True`` (the default), the initial ``tools/list`` call
    stores only tool names and descriptions.  Full input schemas are fetched
    on demand via :meth:`get_tool_schema` and cached for subsequent calls.
    Set ``deferred=False`` to eagerly load all schemas on connect (legacy
    behaviour).
    """

    def __init__(self, name: str, command: list[str], env: dict | None = None,
                 *, deferred: bool = True):
        self.name = name
        self._transport = StdioClientTransport(command, env)
        self._tools: list[MCPToolInfo] = []
        self._connected = False
        self._deferred = deferred
        # Cache for lazily-loaded full schemas: tool_name -> inputSchema dict
        self._schema_cache: dict[str, dict] = {}
        # Raw server response stored for deferred schema extraction
        self._raw_tools: list[dict] = []

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
            "MCP client '%s' connected — %d tools available%s",
            self.name, len(self._tools),
            " (deferred schemas)" if self._deferred else "",
        )

    def _refresh_tools(self):
        """Fetch the tool list from the server.

        When deferred, stores raw tool dicts for later schema extraction
        but only populates MCPToolInfo with name + description (no schema).
        """
        resp = self._transport.send_request("tools/list")
        if "error" in resp:
            logger.warning("MCP tools/list failed for '%s': %s", self.name, resp["error"])
            self._tools = []
            self._raw_tools = []
            return

        self._raw_tools = resp.get("result", {}).get("tools", [])

        if self._deferred:
            # Store only name + description; schemas loaded on demand
            self._tools = [
                MCPToolInfo(
                    name=t["name"],
                    description=t.get("description", ""),
                    # input_schema left empty — loaded lazily
                )
                for t in self._raw_tools
            ]
        else:
            # Eager: load everything immediately (legacy behaviour)
            self._tools = [
                MCPToolInfo(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                )
                for t in self._raw_tools
            ]

    @property
    def tools(self) -> list[MCPToolInfo]:
        return list(self._tools)

    def get_tool_schema(self, name: str) -> dict:
        """Get the full input schema for a tool, loading it lazily if needed.

        Returns the inputSchema dict, or an empty dict if the tool is unknown.
        """
        # Check cache first
        if name in self._schema_cache:
            return self._schema_cache[name]

        # Look up from raw tools (avoids a second server round-trip)
        for raw in self._raw_tools:
            if raw.get("name") == name:
                schema = raw.get("inputSchema", {})
                self._schema_cache[name] = schema
                # Also update the MCPToolInfo object
                for tool in self._tools:
                    if tool.name == name:
                        tool.input_schema = schema
                        break
                return schema

        # Tool not found in cached raw list — try refreshing
        self._refresh_tools()
        for raw in self._raw_tools:
            if raw.get("name") == name:
                schema = raw.get("inputSchema", {})
                self._schema_cache[name] = schema
                for tool in self._tools:
                    if tool.name == name:
                        tool.input_schema = schema
                        break
                return schema

        return {}

    def search_tools(self, query: str) -> list[MCPToolInfo]:
        """Search tools by keyword, matching against name and description.

        Returns matching MCPToolInfo entries (with schemas loaded for matches).
        Case-insensitive substring search.
        """
        query_lower = query.lower()
        results = []
        for tool in self._tools:
            if (query_lower in tool.name.lower()
                    or query_lower in tool.description.lower()):
                # Ensure schema is loaded for matched tools
                if not tool.input_schema:
                    self.get_tool_schema(tool.name)
                results.append(tool)
        return results

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
