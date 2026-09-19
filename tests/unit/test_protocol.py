"""Tests for MCP JSON-RPC 2.0 protocol helpers."""

import json

from freecad_ai.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    decode,
    encode,
    make_error,
    make_notification,
    make_request,
    make_response,
)


class TestEncode:
    def test_returns_bytes(self):
        msg = {"jsonrpc": "2.0", "method": "test"}
        result = encode(msg)
        assert isinstance(result, bytes)

    def test_ends_with_newline(self):
        result = encode({"jsonrpc": "2.0"})
        assert result.endswith(b"\n")

    def test_compact_json(self):
        result = encode({"a": 1, "b": 2})
        text = result.decode("utf-8").strip()
        assert " " not in text  # compact separators, no spaces

    def test_roundtrip(self):
        original = {"jsonrpc": "2.0", "method": "tools/list", "id": 42}
        encoded = encode(original)
        decoded = decode(encoded.decode("utf-8"))
        assert decoded == original


class TestDecode:
    def test_parses_json_line(self):
        line = '{"jsonrpc":"2.0","id":1}\n'
        result = decode(line)
        assert result == {"jsonrpc": "2.0", "id": 1}

    def test_strips_whitespace(self):
        line = '  {"a": 1}  \n'
        result = decode(line)
        assert result == {"a": 1}

    def test_raises_on_invalid_json(self):
        import pytest
        with pytest.raises(json.JSONDecodeError):
            decode("not json")


class TestMakeRequest:
    def test_minimal_request(self):
        msg = make_request("tools/list")
        assert msg == {"jsonrpc": "2.0", "method": "tools/list"}
        assert "params" not in msg
        assert "id" not in msg

    def test_with_params(self):
        msg = make_request("tools/call", params={"name": "test"})
        assert msg["params"] == {"name": "test"}

    def test_with_id(self):
        msg = make_request("tools/list", id=7)
        assert msg["id"] == 7

    def test_with_params_and_id(self):
        msg = make_request("test", params={"k": "v"}, id="abc")
        assert msg["params"] == {"k": "v"}
        assert msg["id"] == "abc"


class TestMakeResponse:
    def test_success_response(self):
        msg = make_response(1, {"tools": []})
        assert msg == {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}

    def test_null_result(self):
        msg = make_response(2, None)
        assert msg["result"] is None

    def test_string_id(self):
        msg = make_response("req-1", "ok")
        assert msg["id"] == "req-1"


class TestMakeError:
    def test_error_without_data(self):
        msg = make_error(1, PARSE_ERROR, "Parse error")
        assert msg["error"]["code"] == -32700
        assert msg["error"]["message"] == "Parse error"
        assert "data" not in msg["error"]

    def test_error_with_data(self):
        msg = make_error(2, INTERNAL_ERROR, "Oops", data={"detail": "stack trace"})
        assert msg["error"]["data"] == {"detail": "stack trace"}

    def test_null_id_for_parse_errors(self):
        msg = make_error(None, PARSE_ERROR, "Bad JSON")
        assert msg["id"] is None


class TestMakeNotification:
    def test_no_id(self):
        msg = make_notification("notifications/initialized")
        assert "id" not in msg
        assert msg["method"] == "notifications/initialized"

    def test_with_params(self):
        msg = make_notification("progress", params={"pct": 50})
        assert msg["params"] == {"pct": 50}

    def test_without_params(self):
        msg = make_notification("ping")
        assert "params" not in msg


class TestErrorCodes:
    def test_parse_error(self):
        assert PARSE_ERROR == -32700

    def test_invalid_request(self):
        assert INVALID_REQUEST == -32600

    def test_method_not_found(self):
        assert METHOD_NOT_FOUND == -32601

    def test_invalid_params(self):
        assert INVALID_PARAMS == -32602

    def test_internal_error(self):
        assert INTERNAL_ERROR == -32603


def test_default_protocol_version_matches_what_the_server_advertises():
    """The header default and the initialize reply must not drift apart."""
    from freecad_ai.mcp import server
    from freecad_ai.mcp import protocol

    assert protocol.DEFAULT_PROTOCOL_VERSION == server.PROTOCOL_VERSION


def test_supported_protocol_versions_contain_the_default():
    from freecad_ai.mcp import protocol

    assert protocol.DEFAULT_PROTOCOL_VERSION in protocol.SUPPORTED_PROTOCOL_VERSIONS


def test_supported_protocol_versions_include_the_2026_redesign():
    """Was excluded until #64 phase 3: we now route the modern era for real.

    The old test guarded against advertising a revision we did not serve. The
    guard that replaces it is test_every_derived_set_comes_from_the_table in
    test_mcp_dual_era.py — the advertised set cannot drift from the routed one
    because both read PROTOCOL_REVISIONS.
    """
    from freecad_ai.mcp import protocol

    assert "2026-07-28" in protocol.SUPPORTED_PROTOCOL_VERSIONS
    assert protocol.era_of("2026-07-28") == protocol.MODERN
