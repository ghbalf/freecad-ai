"""MCP Server — exposes FreeCAD tools to external MCP clients.

Handles initialize, tools/list, tools/call, and ping requests over
transport (STDIO or HTTP/SSE).
"""

import logging
import os

from .. import __version__
from ..tools.registry import ToolRegistry
from . import protocol
from .transport import StdioServerTransport

logger = logging.getLogger(__name__)

# Derived, never a literal: this was pinned at "0.1.0" for twenty releases, so
# every MCP client reported "FreeCAD AI 0.1.0" regardless of what was installed.
SERVER_INFO = {"name": "FreeCAD AI", "version": __version__}
PROTOCOL_VERSION = "2025-03-26"


def _coerce_ttl(value, fallback):
    if value is None or value == "":
        return fallback
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric MCP tools TTL %r", value)
        return fallback
    if ttl < 0:
        logger.warning("Ignoring negative MCP tools TTL %r", value)
        return fallback
    return ttl


def _coerce_scope(value, fallback):
    if value is None or value == "":
        return fallback
    if value not in protocol.CACHE_SCOPES:
        logger.warning("Ignoring unknown MCP cacheScope %r (expected %s)",
                       value, " or ".join(protocol.CACHE_SCOPES))
        return fallback
    return value


def resolve_cache_hints(cfg=None):
    """Return ``(ttl_ms, cache_scope)`` for tools/list: env beats config,
    config beats defaults — the same precedence as MCP_HOST / MCP_PORT.

    A malformed value falls back and warns instead of reaching the wire.
    Both fields are REQUIRED in 2026-07-28, so serialising nonsense would
    break conformance for every client rather than only for whoever set it.

    ``cfg`` is loaded lazily and its absence is survivable: the STDIO entry
    point builds MCPServer(registry) in a headless process where the config
    layer may not be importable at all.
    """
    ttl = protocol.DEFAULT_TOOLS_TTL_MS
    scope = protocol.DEFAULT_CACHE_SCOPE

    if cfg is None:
        try:
            from ..config import get_config
            cfg = get_config()
        except Exception:
            logger.debug("No config available; using default MCP cache hints")

    if cfg is not None:
        ttl = _coerce_ttl(getattr(cfg, "mcp_server_tools_ttl_ms", None), ttl)
        scope = _coerce_scope(
            getattr(cfg, "mcp_server_tools_cache_scope", None), scope)

    ttl = _coerce_ttl(os.environ.get("MCP_TOOLS_TTL_MS"), ttl)
    scope = _coerce_scope(os.environ.get("MCP_TOOLS_CACHE_SCOPE"), scope)
    return ttl, scope


class MCPServer:
    """Exposes a ToolRegistry as an MCP server."""

    def __init__(self, registry: ToolRegistry, transport=None, executor=None):
        self._registry = registry
        self._transport = transport
        self._executor = executor

    def run(self):
        """Start the server (blocking)."""
        transport = self._transport or StdioServerTransport()
        logger.info("MCP server starting with %d tools", len(self._registry.list_tools()))
        transport.run(self._handle)

    def _handle(self, msg: dict) -> dict | None:
        """Route a JSON-RPC message to the appropriate handler."""
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            return protocol.make_response(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })

        if method == "notifications/initialized":
            return None  # Notification, no response

        if method == "tools/list":
            return protocol.make_response(msg_id, {
                "tools": self._registry.to_mcp_schema(),
            })

        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        if method == "ping":
            return protocol.make_response(msg_id, {})

        # Unknown method
        if msg_id is not None:
            return protocol.make_error(
                msg_id, protocol.METHOD_NOT_FOUND,
                f"Method not found: {method}",
            )
        return None  # Unknown notification, ignore

    def _handle_tool_call(self, msg_id, params: dict) -> dict:
        """Execute a tool and return the result in MCP format."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if self._executor:
            result = self._executor.execute(tool_name, arguments)
        else:
            result = self._registry.execute(tool_name, arguments)

        if result.success:
            content = [{"type": "text", "text": result.output}]
            if result.data:
                content.append({"type": "text", "text": str(result.data)})
            return protocol.make_response(msg_id, {
                "content": content,
                "isError": False,
            })
        else:
            return protocol.make_response(msg_id, {
                "content": [{"type": "text", "text": result.error or "Unknown error"}],
                "isError": True,
            })
