"""JSON-RPC 2.0 message helpers for MCP protocol.

Provides encode/decode functions and message constructors for the
Model Context Protocol, which uses JSON-RPC 2.0 over STDIO.
"""

import base64
import json
from typing import Any, NamedTuple

# Standard JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Renumbered in 2026-07-28. -32021 is defined but never raised by us: we
# require no client capability. It is here so the transport's status map can
# classify it as 400 the day a revision gives us a reason to send it.
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022

MODERN = "modern"
LEGACY = "legacy"


class ProtocolRevision(NamedTuple):
    """One MCP revision and the era whose shape it speaks."""

    version: str
    era: str


# Newest first — LATEST_LEGACY_VERSION reads LEGACY_VERSIONS[0]. Adding a
# future revision is one line here; nothing else in the codebase names a
# version literal.
PROTOCOL_REVISIONS = (
    ProtocolRevision("2026-07-28", MODERN),
    ProtocolRevision("2025-11-25", LEGACY),
    ProtocolRevision("2025-06-18", LEGACY),
    ProtocolRevision("2025-03-26", LEGACY),
)

SUPPORTED_PROTOCOL_VERSIONS = frozenset(r.version for r in PROTOCOL_REVISIONS)
MODERN_VERSIONS = tuple(r.version for r in PROTOCOL_REVISIONS if r.era == MODERN)
LEGACY_VERSIONS = tuple(r.version for r in PROTOCOL_REVISIONS if r.era == LEGACY)
LATEST_LEGACY_VERSION = LEGACY_VERSIONS[0]

# What a legacy client gets when it names no version. Not the newest revision
# we serve: 2025-03-26 is what every existing configuration negotiated, and
# moving this default would change their wire shape for no request of theirs.
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

_ERA_BY_VERSION = {r.version: r.era for r in PROTOCOL_REVISIONS}

# The reserved _meta namespace 2026-07-28 uses for protocol metadata. Spelled
# out because a typo here degrades silently to "legacy client".
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# Freshness hints for CacheableResult. Both fields are REQUIRED on tools/list,
# so "do not cache" is ttlMs=0, never omission.
DEFAULT_TOOLS_TTL_MS = 300000
CACHE_SCOPES = ("public", "private")
DEFAULT_CACHE_SCOPE = "private"


def era_of(version):
    """Return MODERN, LEGACY, or None for a revision we do not serve."""
    return _ERA_BY_VERSION.get(version)


_HEADER_SENTINEL_PREFIX = "=?base64?"
_HEADER_SENTINEL_SUFFIX = "?="


def decode_header_value(raw):
    """Decode the ``=?base64?…?=`` sentinel a mirrored header value may use.

    2026-07-28 defines it for values that cannot travel in a header raw — a
    tool name with a non-ASCII character, say. Returns None when the payload
    will not decode, which the caller treats as a mismatch: a value we cannot
    read is not a value we can confirm agrees with the body.
    """
    if raw is None:
        return None
    if raw.startswith(_HEADER_SENTINEL_PREFIX) and raw.endswith(_HEADER_SENTINEL_SUFFIX):
        try:
            return base64.b64decode(
                raw[len(_HEADER_SENTINEL_PREFIX):-len(_HEADER_SENTINEL_SUFFIX)],
                validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return raw


def encode_header_value(value):
    """The inverse of decode_header_value: what to put in a mirrored header.

    A value travels raw only when doing so is unambiguous — printable ASCII,
    no surrounding whitespace, and not itself shaped like the sentinel. That
    last case is the subtle one: a tool literally named ``=?base64?x?=`` sent
    raw would be *decoded* by the far side into something it never called.
    """
    if value is None:
        return None
    safe = (
        value != ""
        and value == value.strip()
        and all(" " <= ch <= "~" for ch in value)
        and not (value.startswith(_HEADER_SENTINEL_PREFIX)
                 and value.endswith(_HEADER_SENTINEL_SUFFIX))
    )
    if safe:
        return value
    payload = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return _HEADER_SENTINEL_PREFIX + payload + _HEADER_SENTINEL_SUFFIX


def _request_meta(msg: dict):
    """The per-request ``_meta`` mapping, or None when there is not one."""
    params = msg.get("params")
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else None


def request_protocol_version(msg: dict):
    """Return the revision named in per-request ``_meta``, or None.

    None is returned both when there is no ``_meta`` at all and when
    ``_meta`` carries the key with a null value — callers that need to tell
    those two apart use ``is_modern_request``, which checks for the key's
    presence rather than relying on this return value.
    """
    meta = _request_meta(msg)
    return meta.get(META_PROTOCOL_VERSION) if meta is not None else None


def is_modern_request(msg: dict) -> bool:
    """True when the request carries 2026-07-28 per-request metadata.

    Presence of the KEY decides the era, not its value: only a modern client
    sends this key at all, so a null or unknown value is a modern request to
    refuse with UNSUPPORTED_PROTOCOL_VERSION — never a legacy request to
    answer in the old shape. Testing ``request_protocol_version(msg) is not
    None`` would conflate "no key" with "key present, value null", and the
    spec requires the second to be a refusal, not a silent fall-through to
    legacy semantics.
    """
    meta = _request_meta(msg)
    return meta is not None and META_PROTOCOL_VERSION in meta


def unsupported_version_error(msg_id, requested, supported):
    """Build the -32022 a caller can act on: what it asked for, what we serve."""
    return make_error(
        msg_id, UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported MCP protocol version %r. This server speaks %s."
        % (requested, ", ".join(supported)),
        {"requested": requested, "supported": list(supported)})


def modern_result(payload: dict, server_info: dict,
                  ttl_ms: int | None = None,
                  cache_scope: str | None = None) -> dict:
    """Shape a ``result`` object for a modern (2026-07-28) request.

    ``resultType`` is required on every result. Ours is always ``complete``:
    the other value, ``input_required``, belongs to multi-round tool responses,
    and no FreeCAD tool asks the caller a question mid-call.

    ``ttl_ms``/``cache_scope`` are passed only for a CacheableResult — as of
    this revision, ``tools/list`` and ``server/discover``. Emitting them on
    ``tools/call`` would invite a client to cache a geometry mutation.
    """
    # resultType is assigned AFTER the payload update: the envelope's one
    # invariant (we are always "complete") must not depend on the payload
    # not happening to carry its own resultType key.
    result: dict[str, Any] = {}
    result.update(payload)
    result["resultType"] = "complete"
    result["_meta"] = {META_SERVER_INFO: server_info}
    if ttl_ms is not None:
        result["ttlMs"] = ttl_ms
    if cache_scope is not None:
        result["cacheScope"] = cache_scope
    return result


def encode(msg: dict) -> bytes:
    """Serialize a JSON-RPC message to bytes (JSON + newline)."""
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: str) -> dict:
    """Parse a JSON-RPC message from a line of text."""
    return json.loads(line.strip())


def make_request(method: str, params: dict | None = None, id: Any = None) -> dict:
    """Create a JSON-RPC 2.0 request message."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return msg


def make_response(id: Any, result: Any) -> dict:
    """Create a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(id: Any, code: int, message: str, data: Any = None) -> dict:
    """Create a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


def make_notification(method: str, params: dict | None = None) -> dict:
    """Create a JSON-RPC 2.0 notification (no id, no response expected)."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg
