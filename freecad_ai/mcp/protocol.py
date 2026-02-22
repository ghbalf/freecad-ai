"""JSON-RPC 2.0 message helpers for MCP protocol.

Provides encode/decode functions and message constructors for the
Model Context Protocol, which uses JSON-RPC 2.0 over STDIO.
"""

import json
from typing import Any

# Standard JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


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
