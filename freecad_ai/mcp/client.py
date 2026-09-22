"""MCP client — connects to one external MCP server.

Handles the initialize handshake, tool discovery, and tool invocation
over a StdioClientTransport.

Supports deferred tool loading: on connect, only tool names and descriptions
are stored. Full input schemas are fetched lazily on first access via
get_tool_schema(). A search_tools() method allows keyword-based filtering.
"""

import logging
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field

from . import protocol
from .transport import (
    StdioClientTransport,
    SSEClientTransport,
    StreamableHTTPClientTransport,
    _LOOPBACK_HOSTS,
)

logger = logging.getLogger(__name__)

# What we ASK for in initialize — the newest legacy revision we understand.
# The server's answer wins (see connect), so asking high costs nothing: a
# server that does not know it replies with one it does support. Note this is
# deliberately not protocol.DEFAULT_PROTOCOL_VERSION, which is what our own
# *server* answers a client that named no version and must stay 2025-03-26.
PROTOCOL_VERSION = protocol.LATEST_LEGACY_VERSION
CLIENT_INFO = {"name": "FreeCAD AI", "version": "0.1.0"}

# How long to leave a server alone after a re-listing fails. It overrides the
# server's own ttlMs, which is a freshness promise about a list it managed to
# send — it says nothing about how often to retry one it did not.
RELIST_RETRY_FLOOR_MS = 30_000


@dataclass
class MCPToolInfo:
    """Metadata for a tool discovered from an MCP server."""
    name: str
    description: str
    input_schema: dict | None = None


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

    def __init__(self, name: str, command: list | None = None,
                 env: dict | None = None, *, transport=None,
                 deferred: bool = True, tool_call_timeout: float = 600):
        self.name = name
        if transport is not None:
            self._transport = transport
        else:
            self._transport = StdioClientTransport(command, env)
        self._tools: list[MCPToolInfo] = []
        self._connected = False
        self._deferred = deferred
        self._tool_call_timeout = tool_call_timeout
        # Cache for lazily-loaded full schemas: tool_name -> inputSchema dict
        self._schema_cache: dict[str, dict] = {}
        # Raw server response stored for deferred schema extraction
        self._raw_tools: list[dict] = []
        # Until connect() negotiates, behave exactly as every earlier release.
        self._era = protocol.LegacyEra(protocol.DEFAULT_PROTOCOL_VERSION)
        # Freshness hints from a modern tools/list, kept for a future re-list
        # feature. Normally None after a legacy listing, which defines no such
        # fields — the store is keyed on the fields being there, not on the
        # era, so a legacy server that volunteers them is taken at its word.
        self.tools_cache_hints = None
        # When the current listing was fetched, on the monotonic clock.
        self._tools_listed_at = 0.0
        # Monotonic deadline before which no re-listing is attempted.
        self._relist_not_before = 0.0

    def _send(self, method, params=None, timeout=None):
        """Every outgoing request goes through here, so the era is applied once."""
        params, headers = self._era.decorate(method, params)
        if timeout is None:
            resp = self._transport.send_request(method, params, headers=headers)
        else:
            resp = self._transport.send_request(
                method, params, timeout=timeout, headers=headers)
        # A non-conformant server can send a non-dict error body (a bare
        # string, a list); coerce it to {} first so .get("code") below
        # cannot crash (same guard as connect()'s initialize check).
        error = resp.get("error")
        if not isinstance(error, dict):
            error = {}
        if error.get("code") == protocol.HEADER_MISMATCH:
            # Our mirrored headers disagreed with our own body. That is a bug
            # on this side by construction, and a retry would send the same
            # bad headers — so log what went out and let the error surface.
            logger.error(
                "MCP server '%s' rejected %s with -32020 (%s); headers sent: %r",
                self.name, method, error.get("message", ""), headers)
        return resp

    def _notify(self, method, params=None):
        params, headers = self._era.decorate(method, params)
        self._transport.send_notification(method, params, headers=headers)

    def connect(self):
        """Start transport, perform initialize handshake, discover tools."""
        self._transport.start()

        # Initialize handshake
        resp = self._send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })

        if "error" in resp:
            error = resp["error"]
            # A non-conformant server can send a non-dict error body (a bare
            # string, a list). error.get("code") below would crash on that,
            # so coerce it to {} first: .get("code") then returns None, which
            # is not METHOD_NOT_FOUND, so we cannot tell whether this was a
            # -32601 and it is not treated as an era mismatch — it raises
            # below like any other real failure, quoting the original value.
            if not isinstance(error, dict):
                error = {}
            if error.get("code") != protocol.METHOD_NOT_FOUND:
                raise RuntimeError(
                    f"MCP server '{self.name}' initialization failed: {resp['error']}"
                )
            # A server that has no initialize is a 2026-07-28 server: the
            # handshake was removed, not broken. Only -32601 means that; any
            # other failure is a real one and must not be retried as an era
            # mismatch.
            self._era = self._negotiate_modern(error)
        else:
            version = resp.get("result", {}).get("protocolVersion") or PROTOCOL_VERSION
            self._era = protocol.LegacyEra(version)

        # Latch the negotiated revision before anything else goes out. The
        # server's choice wins over what we asked for; HTTP transports send it
        # as MCP-Protocol-Version on every later request (a client MUST as of
        # 2025-06-18). Stdio has no headers and simply ignores it. The era
        # object is set from the same negotiated value, so every request from
        # here on is decorated (or not) according to what the server actually
        # answered — legacy via initialize, or modern via the discover probe.
        self._transport.protocol_version = self._era.version

        # Send initialized notification — but only in the legacy era: a
        # modern server has no session to initialize, and the method was
        # removed along with the handshake that used to precede it.
        if self._era.era == protocol.LEGACY:
            self._notify("notifications/initialized")

        # Discover tools
        self._refresh_tools()
        self._connected = True
        logger.info(
            "MCP client '%s' connected — %d tools available%s",
            self.name, len(self._tools),
            " (deferred schemas)" if self._deferred else "",
        )

    def _negotiate_modern(self, initialize_error):
        """Ask a handshake-less server what it speaks, or raise saying why not."""
        probe = protocol.ModernEra(protocol.MODERN_VERSIONS[0], CLIENT_INFO)
        params, headers = probe.decorate("server/discover", {})
        # Deliberately bypasses _send: at this point self._era is still the
        # LegacyEra we are trying to replace, and _send would decorate with
        # that, not with the candidate `probe` era — sending a bare request
        # with no _meta, which a modern server reads as a legacy call.
        resp = self._transport.send_request(
            "server/discover", params, headers=headers)
        if "error" in resp:
            raise RuntimeError(
                f"MCP server '{self.name}' speaks neither era — "
                f"initialize: {initialize_error}; "
                f"server/discover: {resp['error']}")

        # A non-conformant server can answer with a non-dict result (a bare
        # list) or a non-list supportedVersions (a bare string); neither may
        # crash the negotiation (same guard as connect()'s initialize check
        # and _send's error check). `offered` is what we quote back, `usable`
        # is what we may intersect, and they differ exactly when the server
        # was non-conformant: a non-dict result names no versions at all, and
        # a bare string is not a list — `v in offered` on a string is a
        # SUBSTRING test, so prose merely containing a version would
        # negotiate. Anything else therefore shares nothing and raises below.
        result = resp.get("result")
        offered = result.get("supportedVersions") if isinstance(result, dict) else result
        usable = offered if isinstance(result, dict) and isinstance(offered, list) else []
        # Intersect against the MODERN revisions only. We are here because the
        # server removed initialize, so a legacy version in common is not one
        # we could actually use.
        shared = [v for v in protocol.MODERN_VERSIONS if v in usable]
        if not shared:
            raise RuntimeError(
                f"MCP server '{self.name}' offers {offered!r}; "
                f"this client speaks {list(protocol.MODERN_VERSIONS)!r}")
        return protocol.ModernEra(shared[0], CLIENT_INFO)

    def _refresh_tools(self) -> bool:
        """Fetch the tool list from the server. True when the server answered.

        When deferred, stores raw tool dicts for later schema extraction
        but only populates MCPToolInfo with name + description (no schema).
        """
        resp = self._send("tools/list")
        self._tools_listed_at = time.monotonic()
        if "error" in resp:
            logger.warning("MCP tools/list failed for '%s': %s", self.name, resp["error"])
            self._tools = []
            self._raw_tools = []
            return False

        result = resp.get("result", {})
        self._raw_tools = result.get("tools", [])
        if "ttlMs" in result:
            self.tools_cache_hints = {
                "ttlMs": result["ttlMs"],
                "cacheScope": result.get("cacheScope",
                                         protocol.DEFAULT_CACHE_SCOPE),
            }

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
        return True

    @property
    def tools(self) -> list[MCPToolInfo]:
        if self._tools_are_stale():
            self._re_list_tools()
        return list(self._tools)

    def _tools_are_stale(self) -> bool:
        """Has the server's own ttlMs elapsed since we listed?"""
        if not self._connected or not self.tools_cache_hints:
            return False
        now = time.monotonic()
        if now < self._relist_not_before:
            return False
        return (now - self._tools_listed_at) * 1000 >= self.tools_cache_hints["ttlMs"]

    def _re_list_tools(self):
        """Refresh an expired listing without ever losing the current one.

        connect() can afford to end with an empty list; a session already
        under way cannot — every tool this server contributes would vanish
        from the next turn over what may be a momentary blip.
        """
        previous, previous_raw = self._tools, self._raw_tools
        try:
            answered = self._refresh_tools()
        except Exception as exc:          # transport-level: reset, timeout, ...
            logger.warning("MCP re-list failed for '%s': %s", self.name, exc)
            answered = False
        if not answered:
            self._tools, self._raw_tools = previous, previous_raw
            # Back off. Stamping alone would not do it: under ttlMs 0 every
            # read is stale by definition, so a server that is down would be
            # re-probed on each one — a full request timeout at a time.
            self._tools_listed_at = time.monotonic()
            self._relist_not_before = (
                self._tools_listed_at + RELIST_RETRY_FLOOR_MS / 1000)

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
        # self.tools, not self._tools: the other public reader of the list,
        # and so bound by the same freshness hint.
        for tool in self.tools:
            if (query_lower in tool.name.lower()
                    or query_lower in tool.description.lower()):
                # Ensure schema is loaded for matched tools
                if tool.input_schema is None:
                    self.get_tool_schema(tool.name)
                results.append(tool)
        return results

    def call_tool(self, name: str, arguments: dict, timeout: float | None = None) -> MCPToolResult:
        """Invoke a tool on the MCP server."""
        resp = self._send("tools/call", {
            "name": name,
            "arguments": arguments,
        }, timeout=timeout if timeout is not None else self._tool_call_timeout)

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
        # Back to the __init__ default. A latent invariant, not a fix for an
        # observed failure: MCPManager always builds a fresh MCPClient, so no
        # second connect() happens today. If one ever did, a client that had
        # negotiated modern would open it with a modern-decorated initialize
        # — _meta and mirrored headers at a server it has not yet negotiated
        # with.
        self._era = protocol.LegacyEra(protocol.DEFAULT_PROTOCOL_VERSION)
        self._transport.stop()
        logger.info("MCP client '%s' disconnected", self.name)

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport.is_alive


def _validate_url(url: str):
    """Reject non-http(s) schemes and plaintext http to non-loopback hosts."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"MCP URL must be http or https, got '{parsed.scheme}'")
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            raise ValueError(
                "plaintext http:// is only allowed to localhost; use https://")


def _build_ssl_context(cfg: dict):
    """Return an ssl.SSLContext for custom CA / client cert, or None.

    None => the transport passes context=None to urlopen (urllib's default
    context = system CA store). A context is built only when at least one of
    ca_bundle / client_cert is set.
    """
    ca = cfg.get("ca_bundle") or None
    cert = cfg.get("client_cert") or None
    key = cfg.get("client_key") or None
    if not ca and not cert:
        return None
    context = ssl.create_default_context(cafile=ca)  # cafile=None => system defaults
    if cert:
        context.load_cert_chain(certfile=cert, keyfile=key or None)
    return context


def make_client_transport(cfg: dict):
    """Build the client transport for one MCP server config.

    transport ∈ {"stdio","sse","http"}; absent defaults to "stdio". Raises
    ValueError on a bad URL or unknown transport (caught by connect_all).
    """
    transport = cfg.get("transport", "stdio")
    if transport == "stdio":
        command = [cfg["command"]] + cfg.get("args", [])
        return StdioClientTransport(command, cfg.get("env") or None)
    url = cfg["url"]
    headers = cfg.get("headers") or {}
    _validate_url(url)
    ssl_context = _build_ssl_context(cfg)
    if transport == "sse":
        return SSEClientTransport(url, headers=headers, ssl_context=ssl_context)
    if transport == "http":
        return StreamableHTTPClientTransport(
            url, headers=headers, ssl_context=ssl_context)
    raise ValueError(f"unknown MCP transport '{transport}'")
