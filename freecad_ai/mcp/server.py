"""MCP Server — exposes FreeCAD tools to external MCP clients.

Answers both eras from one endpoint: the legacy initialize/tools.list/
tools.call/ping handshake world, and the stateless 2026-07-28 per-request
_meta world. Served over transport (STDIO or HTTP/SSE).
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
# Kept as the name the rest of the tree already imports; the value now lives
# in the revision table.
PROTOCOL_VERSION = protocol.DEFAULT_PROTOCOL_VERSION

# Shown to a model before it picks a tool. Short on purpose: it is prepended
# to a context window that the tool schemas already fill.
DISCOVER_INSTRUCTIONS = (
    "Tools for inspecting and modifying geometry in a running FreeCAD "
    "session. Every call acts on the document that is open right now.")


def _coerce_ttl(value, fallback):
    if value is None or value == "":
        return fallback
    # An env value is always a str, so int()/ValueError below is the only
    # path that matters there. A hand-edited config.json can hold a bool or
    # a float, and int() accepts both silently: int(True) == 1, int(3.7) ==
    # 3. Neither is a TTL a client asked for, even though isinstance(True,
    # int) is True in Python — reject both explicitly rather than let
    # int() coerce them into a value that looks intentional.
    if isinstance(value, bool):
        logger.warning("Ignoring non-numeric MCP tools TTL %r", value)
        return fallback
    if isinstance(value, float):
        logger.warning("Ignoring non-integer MCP tools TTL %r", value)
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

    def __init__(self, registry: ToolRegistry, transport=None, executor=None,
                 cache_hints=None):
        self._registry = registry
        self._transport = transport
        self._executor = executor
        # Resolved once: a per-request config read would put a JSON file in
        # the path of every tools/list, and these values cannot change without
        # a restart anyway.
        self._cache_hints = cache_hints or resolve_cache_hints()

    def run(self):
        """Start the server (blocking)."""
        transport = self._transport or StdioServerTransport()
        logger.info("MCP server starting with %d tools", len(self._registry.list_tools()))
        transport.run(self._handle)

    def _handle(self, msg: dict) -> dict | None:
        """Route a JSON-RPC message, choosing an era by the request's shape.

        2026-07-28 removed the handshake, so there is no negotiated state to
        consult: each request says which era it speaks, or says nothing and is
        legacy. One endpoint serving both is what the spec calls a dual-era
        server, and it is why existing clients see no change at all.
        """
        method = msg.get("method", "")
        msg_id = msg.get("id")
        # `or {}` let a non-dict `params` (e.g. `5`, `[]`, a bare string)
        # through intact; the legacy `initialize` branch then does
        # `"protocolVersion" not in params` followed by `params.get(...)`,
        # which raises for exactly those shapes. An isinstance guard makes
        # every downstream reader see a plain dict instead.
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}

        if not protocol.is_modern_request(msg):
            return self._handle_legacy(msg_id, method, params)

        version = protocol.request_protocol_version(msg)
        if version not in protocol.MODERN_VERSIONS:
            if msg_id is None:
                return None
            return protocol.unsupported_version_error(
                msg_id, version, protocol.MODERN_VERSIONS)
        return self._handle_modern(msg_id, method, params)

    def _handle_legacy(self, msg_id, method: str, params: dict) -> dict | None:
        """The 2025-era handshake world. Output here must not move."""
        if method == "initialize":
            # Echo the client's revision when we speak it. Replying with a
            # fixed 2025-03-26 made every newer client negotiate down for no
            # reason. Clamped to the legacy era: initialize exists in no
            # modern revision, so echoing one would promise a shape that
            # revision deleted. A client naming no version at all gets the
            # historical default rather than our newest legacy revision —
            # that default is what every existing configuration negotiated,
            # and nothing about naming no version asks for an upgrade.
            if "protocolVersion" not in params:
                speak = protocol.DEFAULT_PROTOCOL_VERSION
            else:
                want = params.get("protocolVersion")
                speak = (want if want in protocol.LEGACY_VERSIONS
                         else protocol.LATEST_LEGACY_VERSION)
            return protocol.make_response(msg_id, {
                "protocolVersion": speak,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })

        if method == "notifications/initialized":
            return None  # Notification, no response

        if method == "tools/list":
            return protocol.make_response(msg_id, {
                "tools": self._tools_schema(),
            })

        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        if method == "ping":
            return protocol.make_response(msg_id, {})

        if method == "server/discover":
            return self._handle_discover(msg_id)

        return self._unknown_method(msg_id, method)

    def _handle_modern(self, msg_id, method: str, params: dict) -> dict | None:
        """The 2026-07-28 stateless world.

        initialize, notifications/initialized and ping are all gone from this
        revision, so they fall through to METHOD_NOT_FOUND — which the
        transport renders as a 404, not a 200. The revision drops
        logging/setLevel too, but naming it here would imply we ever served
        it; we did not.
        """
        if method == "tools/list":
            ttl, scope = self._cache_hints
            return protocol.make_response(msg_id, protocol.modern_result(
                {"tools": self._tools_schema()}, SERVER_INFO,
                ttl_ms=ttl, cache_scope=scope))

        if method == "tools/call":
            return self._handle_tool_call(msg_id, params, modern=True)

        if method == "server/discover":
            return self._handle_discover(msg_id)

        return self._unknown_method(msg_id, method)

    def _handle_discover(self, msg_id) -> dict:
        """Answer server/discover — how a modern client bootstraps.

        Always the modern DiscoverResult shape, in either era: the method
        exists in no legacy revision, so there is no older shape to preserve,
        and a client that has not yet learned what we speak cannot be expected
        to address us correctly first.
        """
        ttl, scope = self._cache_hints
        return protocol.make_response(msg_id, protocol.modern_result({
            "supportedVersions": list(protocol.MODERN_VERSIONS),
            "capabilities": {"tools": {}},
            "instructions": DISCOVER_INSTRUCTIONS,
        }, SERVER_INFO, ttl_ms=ttl, cache_scope=scope))

    def _unknown_method(self, msg_id, method: str) -> dict | None:
        if msg_id is None:
            return None  # Unknown notification, ignore
        return protocol.make_error(
            msg_id, protocol.METHOD_NOT_FOUND,
            f"Method not found: {method}",
        )

    def _tools_schema(self):
        """The registry's tools, sorted by name.

        to_mcp_schema() yields registration order, which is import order —
        it moves when nothing about the tools has. A SHOULD since 2025-11-25,
        and per #47 a stable prefix is what a provider's prompt cache needs.
        """
        return sorted(self._registry.to_mcp_schema(), key=lambda t: t["name"])

    def _handle_tool_call(self, msg_id, params: dict, modern: bool = False) -> dict:
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
            payload = {"content": content, "isError": False}
        else:
            payload = {
                "content": [{"type": "text",
                             "text": result.error or "Unknown error"}],
                "isError": True,
            }

        if modern:
            # No cache hints: a tool call mutates the document, and a client
            # replaying a cached answer would be acting on a model that has
            # since changed. resultType stays "complete" even when isError —
            # the call finished; isError is the tool's verdict, not the
            # round-trip's.
            return protocol.make_response(
                msg_id, protocol.modern_result(payload, SERVER_INFO))
        return protocol.make_response(msg_id, payload)
