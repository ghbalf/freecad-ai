"""Tests for the Streamable HTTP server route (#65).

The transport serves /mcp alongside the legacy /sse + /messages pair. These
tests drive a really-bound loopback socket rather than a mocked handler,
because the behaviour under test is HTTP status codes and headers.
"""

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from freecad_ai.mcp import protocol
from freecad_ai.mcp.server import MCPServer
from freecad_ai.mcp.transport import MAX_REQUEST_BODY, HTTPServerTransport
from freecad_ai.tools.registry import ToolDefinition, ToolRegistry, ToolResult


def _echo_handler(msg):
    """Answer any request with a result; stay silent for notifications."""
    if msg.get("id") is None:
        return None
    return protocol.make_response(msg["id"], {"echoed": msg.get("method")})


class _RunningServer:
    """Serve HTTPServerTransport on an ephemeral loopback port, in a thread."""

    def __init__(self, handler=_echo_handler, **kwargs):
        self._handler = handler
        self._kwargs = kwargs

    def __enter__(self):
        self.transport = HTTPServerTransport(host="127.0.0.1", port=0,
                                             **self._kwargs)
        self.transport._handler = self._handler
        self.httpd = self.transport._make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()


def _request(port, path="/mcp", method="POST", data=None, headers=None):
    """Return (status, body_bytes, headers) without raising on 4xx."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def _post(port, payload, headers=None, path="/mcp"):
    """POST a JSON-RPC message. ``payload`` is encoded verbatim if bytes."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    return _request(port, path=path, data=body, headers=hdrs)


class TestStreamableRequests:
    def test_a_request_gets_its_response_inline_as_json(self):
        with _RunningServer() as srv:
            status, body, headers = _post(
                srv.port, {"jsonrpc": "2.0", "id": 7, "method": "ping"})

        assert status == 200
        assert headers.get("Content-Type") == "application/json"
        assert json.loads(body) == {
            "jsonrpc": "2.0", "id": 7, "result": {"echoed": "ping"}}

    def test_no_session_id_is_ever_issued(self):
        """We keep no cross-request state, and 2026-07-28 removes sessions."""
        with _RunningServer() as srv:
            status, _, headers = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"})

        assert status == 200
        assert headers.get("Mcp-Session-Id") is None

    def test_a_notification_is_accepted_with_no_body(self):
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port,
                {"jsonrpc": "2.0", "method": "notifications/initialized"})

        assert status == 202
        assert body == b""

    def test_unparseable_json_is_a_parse_error(self):
        with _RunningServer() as srv:
            status, body, _ = _post(srv.port, b"{not json")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_a_batch_array_is_an_invalid_request(self):
        """2025-03-26 permits batches; /messages never handled them and
        2025-11-25 removes them again. Refusing beats half-processing."""
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.INVALID_REQUEST

    def test_a_bare_scalar_body_is_an_invalid_request(self):
        with _RunningServer() as srv:
            status, body, _ = _post(srv.port, 42)

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.INVALID_REQUEST

    def test_a_non_integer_content_length_is_a_parse_error(self):
        """int(Content-Length) must not raise ValueError past the handler.

        urllib respects an explicitly-set Content-Length header rather than
        recomputing it from the body, which is what lets this test reach the
        server with a malformed header at all.
        """
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Content-Length": "notanumber"})

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_a_negative_content_length_is_a_parse_error(self):
        """A negative length would make rfile.read() block to EOF and pin a
        worker thread until the socket timeout, answering nothing. The point
        of this test is that a response arrives at all."""
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Content-Length": "-1"})

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_an_oversized_content_length_is_a_parse_error(self):
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Content-Length": str(MAX_REQUEST_BODY + 1)})

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_a_non_utf8_body_is_a_parse_error(self):
        with _RunningServer() as srv:
            status, body, _ = _post(srv.port, b"\xff\xfe")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_a_request_the_handler_ignores_becomes_an_internal_error(self):
        """A request MUST get a response. A silent handler is a server bug —
        say so, rather than leaving the client to time out on an empty body."""
        with _RunningServer(handler=lambda msg: None) as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 3, "method": "ping"})

        assert status == 200
        decoded = json.loads(body)
        assert decoded["id"] == 3
        assert decoded["error"]["code"] == protocol.INTERNAL_ERROR

    def test_a_raising_handler_becomes_a_jsonrpc_error_not_an_http_error(self):
        def _boom(msg):
            raise RuntimeError("tool exploded")

        with _RunningServer(handler=_boom) as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 9, "method": "tools/call"})

        assert status == 200
        decoded = json.loads(body)
        assert decoded["error"]["code"] == protocol.INTERNAL_ERROR
        assert "tool exploded" in decoded["error"]["message"]


class TestStreamableAuthorization:
    """The Host and Origin guards must cover /mcp, not just /messages."""

    def test_a_cross_origin_post_is_rejected(self):
        with _RunningServer() as srv:
            status, _, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Origin": "https://evil.example"})

        assert status == 403

    def test_a_non_loopback_host_header_is_rejected(self):
        with _RunningServer() as srv:
            status, _, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Host": "attacker.example"})

        assert status == 403

    def test_an_explicit_allowlist_admits_the_host_it_names(self):
        with _RunningServer(allowed_hosts=["fileserver.local"]) as srv:
            status, _, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Host": "fileserver.local"})

        assert status == 200


class TestStreamableMethods:
    def test_get_on_mcp_is_405_with_an_allow_header(self):
        """A server offering no server-to-client stream may refuse GET, and
        the spec says so explicitly. Allow: POST tells the client what to do."""
        with _RunningServer() as srv:
            status, _, headers = _request(srv.port, method="GET")

        assert status == 405
        assert headers.get("Allow") == "POST"

    def test_delete_on_mcp_is_405(self):
        """DELETE terminates a session; we never issue one."""
        with _RunningServer() as srv:
            status, _, headers = _request(srv.port, method="DELETE")

        assert status == 405
        assert headers.get("Allow") == "POST"

    def test_delete_is_authorized_like_every_other_verb(self):
        """_authorized() is called per-verb by hand, not by middleware, so a
        new verb silently skips the guard unless it calls it."""
        with _RunningServer() as srv:
            status, _, _ = _request(
                srv.port, method="DELETE",
                headers={"Host": "attacker.example"})

        assert status == 403

    def test_an_unknown_path_is_still_404(self):
        with _RunningServer() as srv:
            status, _, _ = _request(srv.port, path="/nope", method="GET")

        assert status == 404

    def test_the_legacy_messages_path_still_answers_post(self):
        """/mcp must not have stolen the legacy route on the way in."""
        with _RunningServer() as srv:
            status, _, _ = _request(
                srv.port, path="/messages",
                data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                headers={"Content-Type": "application/json"})

        assert status == 202


class TestProtocolVersionHeader:
    _PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    def test_a_missing_header_is_assumed_to_be_the_default_revision(self):
        """Spec SHOULD: absent header means 2025-03-26, which is what we speak."""
        with _RunningServer() as srv:
            status, _, _ = _post(srv.port, self._PING)

        assert status == 200

    @pytest.mark.parametrize("version",
                             ["2025-03-26", "2025-06-18", "2025-11-25"])
    def test_every_revision_we_can_serve_is_accepted(self, version):
        with _RunningServer() as srv:
            status, _, _ = _post(srv.port, self._PING,
                                 headers={"MCP-Protocol-Version": version})

        assert status == 200

    def test_the_2026_redesign_is_rejected(self):
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, self._PING,
                headers={"MCP-Protocol-Version": "2026-07-28"})

        assert status == 400
        assert json.loads(body)["error"]["code"] == \
            protocol.UNSUPPORTED_PROTOCOL_VERSION

    def test_a_garbage_version_is_rejected(self):
        with _RunningServer() as srv:
            status, _, _ = _post(
                srv.port, self._PING,
                headers={"MCP-Protocol-Version": "banana"})

        assert status == 400

    def test_the_rejection_names_what_we_support(self):
        """A 400 a client cannot act on is the #60 failure mode again."""
        with _RunningServer() as srv:
            _, body, _ = _post(
                srv.port, self._PING,
                headers={"MCP-Protocol-Version": "2026-07-28"})

        message = json.loads(body)["error"]["message"]
        for version in protocol.LEGACY_VERSIONS:
            assert version in message

    def test_the_legacy_messages_path_ignores_the_header(self):
        """Changing /messages would break clients this work is not about."""
        with _RunningServer() as srv:
            status, _, _ = _request(
                srv.port, path="/messages",
                data=json.dumps(self._PING).encode(),
                headers={"Content-Type": "application/json",
                         "MCP-Protocol-Version": "2026-07-28"})

        assert status == 202


class TestClientServerRoundTrip:
    """Our own Streamable HTTP client against our own Streamable HTTP server.

    The client has spoken this transport since #41 and already sends
    ``Accept: application/json, text/event-stream`` and parses an inline JSON
    body, so the two halves exercise each other with no external MCP client.
    """

    @staticmethod
    def _mcp_handler(msg):
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:
            return None
        if method == "initialize":
            return protocol.make_response(msg_id, {
                "protocolVersion": protocol.DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "0"},
            })
        if method == "tools/list":
            return protocol.make_response(msg_id, {
                "tools": [{"name": "ping", "description": "",
                           "inputSchema": {"type": "object"}}]})
        if method == "tools/call":
            return protocol.make_response(msg_id, {
                "content": [{"type": "text", "text": "pong"}],
                "isError": False})
        return protocol.make_error(msg_id, protocol.METHOD_NOT_FOUND, method)

    def test_handshake_list_and_call(self):
        from freecad_ai.mcp.transport import StreamableHTTPClientTransport

        with _RunningServer(handler=self._mcp_handler) as srv:
            client = StreamableHTTPClientTransport(
                f"http://127.0.0.1:{srv.port}/mcp", connect_timeout=5)
            client.start()
            try:
                init = client.send_request(
                    "initialize",
                    {"protocolVersion": protocol.DEFAULT_PROTOCOL_VERSION},
                    timeout=5)
                assert init["result"]["protocolVersion"] == \
                    protocol.DEFAULT_PROTOCOL_VERSION

                tools = client.send_request("tools/list", timeout=5)
                assert tools["result"]["tools"][0]["name"] == "ping"

                call = client.send_request(
                    "tools/call", {"name": "ping", "arguments": {}}, timeout=5)
                assert call["result"]["content"][0]["text"] == "pong"
            finally:
                client.stop()

    def test_a_notification_does_not_raise(self):
        from freecad_ai.mcp.transport import StreamableHTTPClientTransport

        with _RunningServer(handler=self._mcp_handler) as srv:
            client = StreamableHTTPClientTransport(
                f"http://127.0.0.1:{srv.port}/mcp", connect_timeout=5)
            client.start()
            try:
                # Reads and closes the 202; must not raise on the empty body.
                client.send_notification("notifications/initialized")
            finally:
                client.stop()

    def test_the_client_never_picks_up_a_session_id(self):
        from freecad_ai.mcp.transport import StreamableHTTPClientTransport

        with _RunningServer(handler=self._mcp_handler) as srv:
            client = StreamableHTTPClientTransport(
                f"http://127.0.0.1:{srv.port}/mcp", connect_timeout=5)
            client.start()
            try:
                resp = client.send_request("initialize", {}, timeout=5)
                # The server answered (has a result, not an error).
                assert "result" in resp
                # The Streamable HTTP server issues no session ID; it stays None.
                assert client._session_id is None
            finally:
                client.stop()


class TestLegacyMessagesParsing:
    """The same malformed input the /mcp route rejects cleanly (#69).

    /messages parsed Content-Length and decoded the body outside its try, and
    caught only JSONDecodeError — so a bad header or a non-UTF-8 body escaped
    as a 500 with a traceback in the user's FreeCAD console, and a negative
    length pinned a worker thread reading to EOF with no answer at all. #65
    fixed this for /mcp only, under a constraint not to touch legacy
    behaviour; this is the legacy side getting the same treatment.
    """

    def test_a_valid_message_still_gets_the_legacy_202(self):
        """Control: the success path must be untouched by the error-path fix."""
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                path="/messages")

        assert status == 202
        assert json.loads(body) == {"accepted": True}

    def test_a_non_integer_content_length_is_a_parse_error(self):
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Content-Length": "notanumber"}, path="/messages")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_a_negative_content_length_is_a_parse_error(self):
        """rfile.read(-1) would block to EOF and answer nothing. The point of
        this test is that a response arrives at all."""
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Content-Length": "-1"}, path="/messages")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_an_oversized_content_length_is_a_parse_error(self):
        with _RunningServer() as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Content-Length": str(MAX_REQUEST_BODY + 1)},
                path="/messages")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_a_non_utf8_body_is_a_parse_error(self):
        with _RunningServer() as srv:
            status, body, _ = _post(srv.port, b"\xff\xfe", path="/messages")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR

    def test_unparseable_json_is_still_a_parse_error(self):
        """Pre-existing behaviour: JSONDecodeError is a ValueError subclass,
        so broadening the except must not change this case."""
        with _RunningServer() as srv:
            status, body, _ = _post(srv.port, b"{not json", path="/messages")

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.PARSE_ERROR


def _dual_era_handler(msg):
    """A handler with the shape the real server has after #64."""
    msg_id = msg.get("id")
    if msg_id is None:
        return None
    if protocol.is_modern_request(msg):
        version = protocol.request_protocol_version(msg)
        if version not in protocol.MODERN_VERSIONS:
            return protocol.unsupported_version_error(
                msg_id, version, protocol.MODERN_VERSIONS)
        if msg.get("method") == "tools/list":
            return protocol.make_response(msg_id, protocol.modern_result(
                {"tools": []}, {"name": "t", "version": "0"},
                ttl_ms=0, cache_scope="private"))
        return protocol.make_error(msg_id, protocol.METHOD_NOT_FOUND,
                                   msg.get("method"))
    if msg.get("method") == "ping":
        return protocol.make_response(msg_id, {})
    return protocol.make_error(msg_id, protocol.METHOD_NOT_FOUND,
                               msg.get("method"))


def _modern_body(method, version="2026-07-28", **params):
    params["_meta"] = {protocol.META_PROTOCOL_VERSION: version}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def _modern_headers(method, name=None, version="2026-07-28"):
    headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


class TestDualEraStatusCodes:
    def test_a_modern_request_is_served_from_the_same_endpoint(self):
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("tools/list"),
                headers=_modern_headers("tools/list"))

        assert status == 200
        assert json.loads(body)["result"]["resultType"] == "complete"

    def test_an_unknown_modern_method_is_a_404(self):
        """2026-07-28 maps method-not-found onto HTTP, so an intermediary can
        see it without parsing the body. Legacy keeps answering 200."""
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("nonsense/method"),
                headers=_modern_headers("nonsense/method"))

        assert status == 404
        assert json.loads(body)["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_an_unknown_legacy_method_stays_a_200(self):
        """Changing this would break every client this work is not about."""
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "nope"})

        assert status == 200
        assert json.loads(body)["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_an_unservable_modern_version_is_a_400(self):
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("tools/list", version="2027-05-01"),
                headers=_modern_headers("tools/list", version="2027-05-01"))
        # The body names 2027-05-01 in _meta and the header agrees, so this is
        # the server refusing the version, not the header check firing.
        assert status == 400
        assert json.loads(body)["error"]["code"] == \
            protocol.UNSUPPORTED_PROTOCOL_VERSION

    def test_a_header_body_mismatch_is_a_400_before_the_handler_runs(self):
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("tools/call", name="create_box"),
                headers=_modern_headers("tools/call", name="read_document"))

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.HEADER_MISMATCH

    def test_the_body_is_drained_before_a_rejection(self):
        """The old early 400 skipped the body, safe only while
        protocol_version stayed HTTP/1.0. After the reorder a client can send
        a large body and still read its rejection cleanly."""
        payload = _modern_body("tools/call", name="create_box",
                               arguments={"pad": "x" * 100000})
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, payload,
                headers=_modern_headers("tools/call", name="other"))

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.HEADER_MISMATCH


class TestRealServerOverRealTransport:
    """Wires the actual MCPServer._handle into a really-bound
    HTTPServerTransport, instead of the hand-written _dual_era_handler stand-
    in every other test in this module uses. That stand-in tests the
    transport's HTTP-status half honestly, but if MCPServer._handle stopped
    returning -32601 for a modern ping, every test above would still pass —
    nothing in CI would notice the two halves had drifted apart."""

    @staticmethod
    def _server():
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            "a", "does a", [],
            handler=lambda: ToolResult(True, "ran a")))
        return MCPServer(registry, cache_hints=(0, "private"))

    def test_a_modern_tools_list_is_a_200(self):
        with _RunningServer(handler=self._server()._handle) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("tools/list"),
                headers=_modern_headers("tools/list"))

        assert status == 200
        assert json.loads(body)["result"]["resultType"] == "complete"

    def test_a_modern_ping_is_a_404_the_method_is_legacy_only(self):
        with _RunningServer(handler=self._server()._handle) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("ping"),
                headers=_modern_headers("ping"))

        assert status == 404
        assert json.loads(body)["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_a_legacy_unknown_method_is_a_200(self):
        with _RunningServer(handler=self._server()._handle) as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "nonsense"})

        assert status == 200
        assert json.loads(body)["error"]["code"] == protocol.METHOD_NOT_FOUND


def _raw_post(port, headers, body):
    """POST with the header lines exactly as given, duplicates included.

    urllib collapses repeated headers, so the case this module most needs to
    cover cannot be sent through it: a client that smuggles a second Mcp-Name
    past a proxy which read only the first.
    """
    payload = json.dumps(body).encode()
    lines = ["POST /mcp HTTP/1.1",
             "Host: 127.0.0.1:%d" % port,
             "Content-Type: application/json",
             "Content-Length: %d" % len(payload),
             "Connection: close"]
    lines += ["%s: %s" % pair for pair in headers]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + payload)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    head, _, tail = b"".join(chunks).partition(b"\r\n\r\n")
    return int(head.split(b"\r\n", 1)[0].split()[1]), tail


class TestADuplicateHeaderNeverReachesATool:
    """The end-to-end half of the duplicate-header rejection.

    validate_modern_headers is unit-tested directly, and a header *mismatch*
    is covered over a real socket. Neither proves _handle_streamable still
    calls the check for a duplicate, which is the shape that actually walks
    past a policy layer: intermediaries disagree about which copy they read,
    so a rule enforced anywhere but on the request that runs is not a rule.
    """

    @staticmethod
    def _server(ran):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            "create_box", "creates a box", [],
            handler=lambda: (ran.append("create_box"),
                             ToolResult(True, "made one"))[1]))
        return MCPServer(registry, cache_hints=(0, "private"))

    _CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "_meta": {protocol.META_PROTOCOL_VERSION: "2026-07-28"},
        "name": "create_box", "arguments": {}}}

    @pytest.mark.parametrize("names", [
        ("create_box", "read_document"),
        ("read_document", "create_box"),
    ])
    def test_a_smuggled_second_name_is_refused_in_either_order(self, names):
        """Both orders, because the bug WAS the order: get() returned the
        first copy, so whether the smuggle worked depended only on which one
        the client put first."""
        ran = []
        with _RunningServer(handler=self._server(ran)._handle) as srv:
            status, body = _raw_post(srv.port, [
                ("MCP-Protocol-Version", "2026-07-28"),
                ("Mcp-Method", "tools/call"),
                ("Mcp-Name", names[0]),
                ("Mcp-Name", names[1]),
            ], self._CALL)

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.HEADER_MISMATCH
        assert ran == []

    def test_the_same_call_with_one_name_header_runs_the_tool(self):
        """The control: without it, a test that rejects everything passes."""
        ran = []
        with _RunningServer(handler=self._server(ran)._handle) as srv:
            status, body = _raw_post(srv.port, [
                ("MCP-Protocol-Version", "2026-07-28"),
                ("Mcp-Method", "tools/call"),
                ("Mcp-Name", "create_box"),
            ], self._CALL)

        assert status == 200
        assert json.loads(body)["result"]["isError"] is False
        assert ran == ["create_box"]
