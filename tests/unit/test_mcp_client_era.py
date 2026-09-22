"""Client-side era objects and negotiation (#86)."""

import http.server
import json
import logging
import threading
import time
import urllib.error

import pytest

from freecad_ai.mcp import protocol
from freecad_ai.mcp import server as server_mod
from freecad_ai.mcp import transport as transport_mod
from freecad_ai.mcp.client import CLIENT_INFO, MCPClient
from freecad_ai.mcp.transport import (
    SSEClientTransport,
    StdioClientTransport,
    StreamableHTTPClientTransport,
)
from freecad_ai.tools.registry import ToolDefinition, ToolRegistry, ToolResult


class TestLegacyEra:
    def test_it_changes_nothing(self):
        """The whole promise to existing servers: an untouched request."""
        era = protocol.LegacyEra("2025-03-26")
        params, headers = era.decorate("tools/list", None)
        assert params is None
        assert headers == {}

    def test_params_pass_through_by_identity(self):
        original = {"name": "create_box", "arguments": {}}
        params, _ = protocol.LegacyEra("2025-03-26").decorate("tools/call", original)
        assert params is original


class TestModernEra:
    def test_meta_carries_version_and_client_info(self):
        era = protocol.ModernEra("2026-07-28", CLIENT_INFO)
        params, _ = era.decorate("tools/list", None)
        assert params["_meta"] == {
            protocol.META_PROTOCOL_VERSION: "2026-07-28",
            protocol.META_CLIENT_INFO: CLIENT_INFO,
        }

    def test_the_callers_dict_is_not_mutated(self):
        """A retry must not see _meta accumulate into the caller's arguments."""
        original = {"name": "create_box", "arguments": {}}
        protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate("tools/call", original)
        assert "_meta" not in original

    def test_headers_mirror_version_and_method(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/list", None)
        assert headers == {"MCP-Protocol-Version": "2026-07-28",
                           "Mcp-Method": "tools/list"}

    def test_tools_call_names_the_tool(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/call", {"name": "create_box", "arguments": {}})
        assert headers["Mcp-Name"] == "create_box"

    def test_an_awkward_tool_name_is_encoded(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/call", {"name": "wërkzeug", "arguments": {}})
        assert protocol.decode_header_value(headers["Mcp-Name"]) == "wërkzeug"

    def test_only_tools_call_carries_a_name(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "server/discover", {})
        assert "Mcp-Name" not in headers

    def test_a_nameless_tools_call_mirrors_no_name(self):
        """A tools/call with no name omits Mcp-Name rather than sending None.

        urllib raises TypeError on a None header value, so emitting the key
        would crash the client before the server could answer -32020.
        """
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/call", {})
        assert "Mcp-Name" not in headers


class TestEraTagging:
    def test_each_era_reports_which_one_it_is(self):
        assert protocol.LegacyEra("2025-03-26").era == protocol.LEGACY
        assert protocol.ModernEra("2026-07-28", CLIENT_INFO).era == protocol.MODERN


class _HeaderRecorder(http.server.BaseHTTPRequestHandler):
    """Answers any POST with {"ok": true} and records the headers it saw."""

    seen = []        # one dict of headers per request
    versions = []    # every MCP-Protocol-Version occurrence, per request

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        # get_all, not the dict: a dict collapses a duplicated header, and
        # a duplicate is exactly what one of these tests must be able to see.
        type(self).versions.append(
            self.headers.get_all("MCP-Protocol-Version") or [])
        type(self).seen.append(dict(self.headers.items()))
        payload = json.dumps(
            protocol.make_response(body.get("id"), {"ok": True}),
            separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _Serving:
    """Run _HeaderRecorder on a free port for the duration of a with-block."""

    def __enter__(self):
        _HeaderRecorder.seen = []
        _HeaderRecorder.versions = []
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _HeaderRecorder)
        self.base = "http://127.0.0.1:%d/mcp" % self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)


class _SSEHeaderRecorder(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP+SSE stub: advertises /messages, pushes each POST's reply
    back over the event stream, and records the full header set it saw.

    Same shape as ``_SSEStub`` in test_mcp_client_protocol_version.py, but
    recording the whole header dict rather than one named header — this test
    needs to see several headers land, not just MCP-Protocol-Version.
    """

    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
        self.wfile.flush()
        type(self).stream = self.wfile
        type(self).ready.set()
        type(self).done.wait(10)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).seen.append(dict(self.headers.items()))
        self.send_response(202)
        self.end_headers()
        if body.get("id") is not None:
            reply = json.dumps(
                protocol.make_response(body["id"], {"ok": True}),
                separators=(",", ":"))
            type(self).stream.write(f"event: message\ndata: {reply}\n\n".encode())
            type(self).stream.flush()


class _SSERunning:
    """Run _SSEHeaderRecorder on a free port for the duration of a with-block."""

    def __enter__(self):
        _SSEHeaderRecorder.seen = []
        _SSEHeaderRecorder.ready = threading.Event()
        _SSEHeaderRecorder.done = threading.Event()
        self._srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _SSEHeaderRecorder)
        self.base = "http://127.0.0.1:%d" % self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        _SSEHeaderRecorder.done.set()
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)


class _FakeStdin:
    """Collects what a transport writes, in place of a subprocess pipe."""

    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()


def _stdio_writes(headers):
    """Every byte a fresh stdio transport writes for one request and one
    notification carrying ``headers``.

    A fresh transport per call keeps the request ids aligned, so the two
    traces differ only if the headers themselves changed the bytes. The
    request times out by design — nothing answers a fake pipe — and the
    write under test has already happened by then.
    """
    t = StdioClientTransport(["echo"], None)
    t._process = _FakeProcess()
    try:
        t.send_request("tools/list", {"cursor": "a"}, timeout=0.01,
                       headers=headers)
    except TimeoutError:
        pass
    t.send_notification("notifications/initialized", {"n": 1}, headers=headers)
    return t._process.stdin.writes


class TestTransportsSendPerRequestHeaders:
    def test_streamable_sends_what_it_is_given(self):
        with _Serving() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            t.send_request("tools/list", {}, timeout=5,
                           headers={"Mcp-Method": "tools/list",
                                    "MCP-Protocol-Version": "2026-07-28"})
            t.stop()
        sent = _HeaderRecorder.seen[0]
        assert sent["Mcp-Method"] == "tools/list"
        # urllib.request.AbstractHTTPHandler.do_open() unconditionally
        # str.title()-cases every header name right before it hits the wire,
        # regardless of the case passed to add_header() — so "MCP-..." always
        # arrives as "Mcp-...". Verified against the stdlib directly; not
        # something our _post() can or should fight.
        assert sent["Mcp-Protocol-Version"] == "2026-07-28"

    def test_sse_sends_what_it_is_given(self):
        with _SSERunning() as srv:
            t = SSEClientTransport(f"{srv.base}/sse", connect_timeout=5)
            t.start()
            resp = t.send_request(
                "tools/list", {}, timeout=5,
                headers={"Mcp-Method": "tools/list",
                         "MCP-Protocol-Version": "2026-07-28"})
            t.stop()
        assert resp["result"] == {"ok": True}
        sent = _SSEHeaderRecorder.seen[0]
        assert sent["Mcp-Method"] == "tools/list"
        # Same title-casing behaviour as the Streamable transport's test.
        assert sent["Mcp-Protocol-Version"] == "2026-07-28"

    def test_an_era_header_overrides_the_latched_one_without_duplicating(self):
        """Our own server rejects a duplicated MCP-Protocol-Version outright."""
        with _Serving() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            t.protocol_version = "2025-03-26"        # the legacy latch
            t.send_request("tools/list", {}, timeout=5,
                           headers={"MCP-Protocol-Version": "2026-07-28"})
            t.stop()
        assert _HeaderRecorder.versions[0] == ["2026-07-28"]

    def test_stdio_accepts_headers_in_its_signature(self):
        """The interface the client relies on when a stdio server is modern."""
        t = StdioClientTransport(["echo"], None)
        import inspect
        for method in (t.send_request, t.send_notification):
            assert "headers" in inspect.signature(method).parameters

    def test_stdio_emits_the_same_bytes_with_and_without_headers(self):
        """Stdio has no header channel, so headers must reach the wire nowhere.

        Asserting only that the signature accepts them proves nothing: a
        transport that folded them into the JSON body would pass that. So
        compare the bytes actually written to the subprocess stdin.
        """
        headers = {"Mcp-Method": "tools/list",
                   "MCP-Protocol-Version": "2026-07-28"}
        assert _stdio_writes(headers) == _stdio_writes(None)
        # And the era's value never appears in them at all.
        assert all(b"2026-07-28" not in w for w in _stdio_writes(headers))


class _ErrorStatusServer(http.server.BaseHTTPRequestHandler):
    """Answers every POST with a JSON-RPC -32601 carried by a 404, the way a
    modern server reports an unknown method."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        payload = json.dumps(
            protocol.make_error(body.get("id"), protocol.METHOD_NOT_FOUND,
                                "Unknown method."),
            separators=(",", ":")).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _ServingErrors(_Serving):
    def __enter__(self):
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _ErrorStatusServer)
        self.base = "http://127.0.0.1:%d/mcp" % self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self


def _respond_with(status, body=b"", content_type="application/json"):
    """Build a handler whose POST always answers a fixed status/body, for
    exercising HTTPError bodies that _as_json_rpc must reject."""
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(status)
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
    return _Handler


def _error_from(handler_cls):
    """Run handler_cls for one request and return the JSON-RPC error
    StreamableHTTPClientTransport.send_request produced for it."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    base = "http://127.0.0.1:%d/mcp" % srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        t = StreamableHTTPClientTransport(base, connect_timeout=5)
        t.start()
        resp = t.send_request("initialize", {}, timeout=5)
        t.stop()
        return resp
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


class TestAnErrorStatusIsStillAResponse:
    def test_a_404_with_a_json_rpc_body_keeps_its_code(self):
        """Without this, every modern error reads as INTERNAL_ERROR."""
        with _ServingErrors() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            resp = t.send_request("initialize", {}, timeout=5)
            t.stop()
        assert resp["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_a_status_with_no_usable_body_is_still_a_transport_error(self):
        class _Empty(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()

        srv = http.server.HTTPServer(("127.0.0.1", 0), _Empty)
        base = "http://127.0.0.1:%d/mcp" % srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            t = StreamableHTTPClientTransport(base, connect_timeout=5)
            t.start()
            resp = t.send_request("initialize", {}, timeout=5)
            t.stop()
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
        assert resp["error"]["code"] == protocol.INTERNAL_ERROR

    def test_sse_does_not_wait_for_a_reply_that_will_not_come(self):
        """SSE's reply normally arrives on the stream, so send_request blocks
        on the correlator. When the server answers on the POST instead, the
        body must short-circuit that wait rather than be drained."""
        with _ServingErrors() as srv:
            t = SSEClientTransport("http://127.0.0.1:1/sse", connect_timeout=5)
            t._endpoint_url = srv.base     # skip the GET /sse handshake
            started = time.monotonic()
            resp = t.send_request("initialize", {}, timeout=30)
            t.stop()
        assert resp["error"]["code"] == protocol.METHOD_NOT_FOUND
        assert time.monotonic() - started < 10, "it waited out the correlator"

    def test_a_non_json_body_is_still_a_transport_error(self):
        """An HTML error page from a proxy or gateway is not JSON-RPC."""
        resp = _error_from(_respond_with(
            500, b"<html>Internal Server Error</html>", "text/html"))
        assert resp["error"]["code"] == protocol.INTERNAL_ERROR

    def test_json_that_is_not_json_rpc_is_still_a_transport_error(self):
        """Valid JSON, but missing the jsonrpc envelope, is not an answer."""
        resp = _error_from(_respond_with(
            400, json.dumps({"message": "bad request"}).encode()))
        assert resp["error"]["code"] == protocol.INTERNAL_ERROR


class _Recorder:
    """A transport that records every outgoing call and answers plausibly."""

    def __init__(self, init_result=None):
        self.calls = []            # (kind, method, params, headers)
        self.protocol_version = None
        self.is_alive = True
        self._init_result = init_result if init_result is not None else {
            "protocolVersion": "2025-03-26", "capabilities": {}}

    def start(self):
        pass

    def stop(self):
        self.is_alive = False

    def send_request(self, method, params=None, timeout=30, headers=None):
        self.calls.append(("request", method, params, headers))
        if method == "initialize":
            return protocol.make_response(1, self._init_result)
        if method == "tools/list":
            return protocol.make_response(1, {"tools": []})
        return protocol.make_response(1, {"content": [], "isError": False})

    def send_notification(self, method, params=None, headers=None):
        self.calls.append(("notification", method, params, headers))


class TestLegacyWireIsUnchanged:
    def test_no_meta_and_no_headers_reach_a_legacy_server(self):
        """The compatibility promise, asserted on the whole trace."""
        transport = _Recorder()
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        client.call_tool("create_box", {})
        for _kind, _method, params, headers in transport.calls:
            assert not headers
            assert params is None or "_meta" not in params

    def test_we_ask_for_the_newest_legacy_revision(self):
        transport = _Recorder()
        MCPClient("test", ["echo"], transport=transport).connect()
        _, _, params, _ = transport.calls[0]
        assert params["protocolVersion"] == protocol.LATEST_LEGACY_VERSION

    def test_initialized_is_still_sent(self):
        transport = _Recorder()
        MCPClient("test", ["echo"], transport=transport).connect()
        assert ("notification", "notifications/initialized", None, {}) in [
            (k, m, p, h or {}) for k, m, p, h in transport.calls]


class _ModernOnly(_Recorder):
    """Refuses initialize the way a stateless server must, answers discover."""

    def __init__(self, supported=("2026-07-28",)):
        super().__init__()
        self._supported = list(supported)

    def send_request(self, method, params=None, timeout=30, headers=None):
        self.calls.append(("request", method, params, headers))
        if method == "initialize":
            return protocol.make_error(1, protocol.METHOD_NOT_FOUND,
                                       "This server has no handshake.")
        if method == "server/discover":
            return protocol.make_response(1, {
                "supportedVersions": self._supported,
                "capabilities": {"tools": {}},
            })
        if method == "tools/list":
            return protocol.make_response(1, {"tools": []})
        return protocol.make_response(1, {"content": [], "isError": False})


class TestModernNegotiation:
    def test_a_refused_handshake_is_followed_by_a_probe(self):
        transport = _ModernOnly()
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        assert [m for _k, m, _p, _h in transport.calls][:2] == [
            "initialize", "server/discover"]
        assert client._era.era == protocol.MODERN
        assert client._era.version == "2026-07-28"

    def test_the_probe_itself_is_modern(self):
        """It must carry _meta, or a modern server reads it as a legacy call."""
        transport = _ModernOnly()
        MCPClient("test", ["echo"], transport=transport).connect()
        _kind, _method, params, headers = transport.calls[1]
        assert params["_meta"][protocol.META_PROTOCOL_VERSION] in protocol.MODERN_VERSIONS
        assert headers["Mcp-Method"] == "server/discover"

    def test_initialized_is_not_sent_in_the_modern_era(self):
        """There is no session to initialize; the method was removed."""
        transport = _ModernOnly()
        MCPClient("test", ["echo"], transport=transport).connect()
        assert "notifications/initialized" not in [
            m for k, m, _p, _h in transport.calls if k == "notification"]

    def test_later_requests_carry_the_negotiated_version(self):
        transport = _ModernOnly()
        MCPClient("test", ["echo"], transport=transport).connect()
        listing = [c for c in transport.calls if c[1] == "tools/list"][0]
        assert listing[3]["MCP-Protocol-Version"] == "2026-07-28"


class TestNegotiationFailures:
    def test_a_non_32601_error_raises_without_probing(self):
        """An auth failure must not be retried as if it were an era mismatch."""

        class _Unauthorized(_Recorder):
            def send_request(self, method, params=None, timeout=30, headers=None):
                self.calls.append(("request", method, params, headers))
                return protocol.make_error(1, protocol.INTERNAL_ERROR, "401")

        transport = _Unauthorized()
        with pytest.raises(RuntimeError, match="401"):
            MCPClient("test", ["echo"], transport=transport).connect()
        assert [m for _k, m, _p, _h in transport.calls] == ["initialize"]

    def test_both_eras_refused_names_both_failures(self):
        class _Hostile(_Recorder):
            def send_request(self, method, params=None, timeout=30, headers=None):
                self.calls.append(("request", method, params, headers))
                return protocol.make_error(1, protocol.METHOD_NOT_FOUND, "no")

        with pytest.raises(RuntimeError) as exc:
            MCPClient("test", ["echo"], transport=_Hostile()).connect()
        assert "initialize" in str(exc.value)
        assert "server/discover" in str(exc.value)

    def test_a_non_dict_discover_result_raises_instead_of_crashing(self):
        """``result`` is REQUIRED to be an object; a bare list must not crash.

        The AttributeError this used to raise reached the user through
        MCPManager.connect_all's broad except as "'list' object has no
        attribute 'get'", which names neither the server's fault nor ours.
        """

        class _ListResult(_ModernOnly):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "server/discover":
                    self.calls.append(("request", method, params, headers))
                    return {"jsonrpc": "2.0", "id": 1, "result": ["2026-07-28"]}
                return super().send_request(method, params, timeout, headers)

        with pytest.raises(RuntimeError) as exc:
            MCPClient("test", ["echo"], transport=_ListResult()).connect()
        assert "2026-07-28" in str(exc.value)
        assert "this client speaks" in str(exc.value)

    def test_a_string_supported_versions_does_not_negotiate(self):
        """``v in offered`` on a string is a SUBSTRING test, not membership.

        A server answering the bare string — or prose that merely contains a
        version — would otherwise negotiate the modern era successfully.
        """

        class _StringVersions(_ModernOnly):
            def __init__(self, offered):
                super().__init__()
                self._offered = offered

            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "server/discover":
                    self.calls.append(("request", method, params, headers))
                    return protocol.make_response(
                        1, {"supportedVersions": self._offered})
                return super().send_request(method, params, timeout, headers)

        for offered in ("2026-07-28", "we speak 2026-07-28 here"):
            with pytest.raises(RuntimeError) as exc:
                MCPClient("test", ["echo"],
                          transport=_StringVersions(offered)).connect()
            assert repr(offered) in str(exc.value)

    def test_no_shared_version_raises_with_both_lists(self):
        """Falling back to initialize would re-send what it just removed."""
        transport = _ModernOnly(supported=["2031-01-01"])
        with pytest.raises(RuntimeError) as exc:
            MCPClient("test", ["echo"], transport=transport).connect()
        assert "2031-01-01" in str(exc.value)
        assert "2026-07-28" in str(exc.value)

    def test_a_non_dict_error_body_raises_instead_of_crashing(self):
        """A non-conformant server's error body must not crash the era check.

        Covers both a truthy non-dict (a bare string) and a falsy one (an
        empty list) — the empty list is the interesting case because
        ``error or {}`` already tolerates it by accident; the guard must not
        regress that case while fixing the truthy one.
        """

        class _MalformedError(_Recorder):
            def __init__(self, error_body):
                super().__init__()
                self._error_body = error_body

            def send_request(self, method, params=None, timeout=30, headers=None):
                self.calls.append(("request", method, params, headers))
                return {"jsonrpc": "2.0", "id": 1, "error": self._error_body}

        for error_body in ("boom", []):
            transport = _MalformedError(error_body)
            with pytest.raises(RuntimeError) as exc:
                MCPClient("test", ["echo"], transport=transport).connect()
            assert str(error_body) in str(exc.value)
            assert [m for _k, m, _p, _h in transport.calls] == ["initialize"]


class TestAHeaderMismatchIsLogged:
    def test_a_32020_is_logged_with_the_headers_we_sent(self, caplog):
        """-32020 means OUR headers disagreed with OUR body: a client bug.

        Retrying would send the identical bad headers, so the only useful
        response is a log line carrying enough to debug it.
        """

        class _Picky(_Recorder):
            def send_request(self, method, params=None, timeout=30, headers=None):
                self.calls.append(("request", method, params, headers))
                if method == "initialize":
                    return protocol.make_error(1, protocol.METHOD_NOT_FOUND, "no")
                if method == "server/discover":
                    return protocol.make_response(1, {
                        "supportedVersions": ["2026-07-28"]})
                # Deliberately says nothing a header is named after: the
                # server's message is interpolated into the same log line, so
                # a message mentioning "Mcp-Method" would satisfy the
                # assertions below even with the headers dropped from the call.
                return protocol.make_error(
                    1, protocol.HEADER_MISMATCH, "Rejected.")

        with caplog.at_level(logging.ERROR, logger="freecad_ai.mcp.client"):
            MCPClient("test", ["echo"], transport=_Picky()).connect()

        mismatch = [r for r in caplog.records if "-32020" in r.getMessage()
                    or "mismatch" in r.getMessage().lower()]
        assert mismatch, "a header mismatch must not pass silently"
        # Both of these reach the record only through the headers we sent.
        logged = mismatch[0].getMessage()
        assert "Mcp-Method" in logged
        assert protocol.MODERN_VERSIONS[0] in logged


class TestDisconnectClearsTheEra:
    def test_a_reconnect_would_not_open_modern(self):
        """Latent invariant: nothing reconnects an MCPClient today.

        MCPManager builds a fresh client every time, so this closes a trap
        rather than a live bug — a second connect() on a client that had
        negotiated modern would send a modern-decorated initialize.
        """
        transport = _ModernOnly()
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        assert client._era.era == protocol.MODERN
        client.disconnect()
        assert client._era.era == protocol.LEGACY
        assert client._era.version == protocol.DEFAULT_PROTOCOL_VERSION


class TestCacheHintsAreStored:
    def test_a_modern_tools_list_keeps_its_freshness_hints(self):
        """Stored, not acted on: nothing re-lists yet (see the spec's
        Out of scope). Keeping them costs two lines and saves the re-list
        feature a round trip."""

        class _WithHints(_ModernOnly):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "tools/list":
                    self.calls.append(("request", method, params, headers))
                    return protocol.make_response(1, {
                        "tools": [], "resultType": "complete",
                        "ttlMs": 60000, "cacheScope": "public"})
                return super().send_request(method, params, timeout, headers)

        client = MCPClient("test", ["echo"], transport=_WithHints())
        client.connect()
        assert client.tools_cache_hints == {"ttlMs": 60000, "cacheScope": "public"}

    def test_a_zero_ttl_is_kept_because_it_means_do_not_cache(self):
        """ttlMs: 0 is an instruction, not an absent hint — so the check is
        presence-by-key, and a truthiness test would silently discard it."""

        class _NoCache(_ModernOnly):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "tools/list":
                    self.calls.append(("request", method, params, headers))
                    return protocol.make_response(1, {
                        "tools": [], "resultType": "complete", "ttlMs": 0})
                return super().send_request(method, params, timeout, headers)

        client = MCPClient("test", ["echo"], transport=_NoCache())
        client.connect()
        assert client.tools_cache_hints == {
            "ttlMs": 0, "cacheScope": protocol.DEFAULT_CACHE_SCOPE}

    def test_a_legacy_listing_leaves_them_unset(self):
        client = MCPClient("test", ["echo"], transport=_Recorder())
        client.connect()
        assert client.tools_cache_hints is None


class _WithTTL(_ModernOnly):
    """A modern server that stamps its listing with ttlMs.

    ``tools_payload`` is mutable so a test can change the server's mind
    between listings, which is the only reason re-listing exists at all.
    """

    def __init__(self, ttl_ms, tools=()):
        super().__init__()
        self._ttl_ms = ttl_ms
        self.tools_payload = [{"name": n, "description": ""} for n in tools]

    def send_request(self, method, params=None, timeout=30, headers=None):
        if method == "tools/list":
            self.calls.append(("request", method, params, headers))
            return protocol.make_response(1, {
                "tools": list(self.tools_payload),
                "resultType": "complete",
                "ttlMs": self._ttl_ms,
            })
        return super().send_request(method, params, timeout, headers)


def _listings(transport):
    """How many tools/list requests the client has sent."""
    return len([m for _k, m, _p, _h in transport.calls if m == "tools/list"])


class TestCacheHintsDriveReListing:
    def test_an_expired_ttl_re_lists_on_the_next_read(self):
        transport = _WithTTL(60_000)
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        assert _listings(transport) == 1

        # Age the listing past the server's own ttlMs.
        client._tools_listed_at -= 61
        _ = client.tools
        assert _listings(transport) == 2


    def test_a_listing_inside_its_ttl_is_reused(self):
        """The hint is a licence to cache, not an instruction to poll."""
        transport = _WithTTL(60_000)
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        for _ in range(5):
            _ = client.tools
        assert _listings(transport) == 1

    def test_a_zero_ttl_re_lists_every_time(self):
        """ttlMs: 0 means do not cache — the one hint with teeth today."""
        transport = _WithTTL(0)
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        _ = client.tools
        _ = client.tools
        assert _listings(transport) == 3

    def test_a_server_that_sends_no_hints_is_never_re_listed(self):
        """The compatibility promise: a legacy server sees one tools/list,
        however long the session runs."""
        transport = _Recorder()
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        client._tools_listed_at -= 86_400
        for _ in range(5):
            _ = client.tools
        assert _listings(transport) == 1

    def test_the_re_listing_surfaces_the_servers_new_tools(self):
        """The point of the whole feature, asserted on the tool names."""
        transport = _WithTTL(60_000, tools=["old_tool"])
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        assert [t.name for t in client.tools] == ["old_tool"]

        transport.tools_payload = [{"name": "new_tool", "description": ""}]
        client._tools_listed_at -= 61
        assert [t.name for t in client.tools] == ["new_tool"]


    def test_a_refused_re_listing_keeps_the_tools_we_already_had(self):
        """A transient error must not empty a working tool list: the LLM
        would silently lose every tool this server contributes."""

        class _FailsSecondTime(_WithTTL):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "tools/list" and _listings(self):
                    self.calls.append(("request", method, params, headers))
                    return protocol.make_error(1, protocol.INTERNAL_ERROR, "boom")
                return super().send_request(method, params, timeout, headers)

        transport = _FailsSecondTime(60_000, tools=["old_tool"])
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        client._tools_listed_at -= 61
        assert [t.name for t in client.tools] == ["old_tool"]

    def test_a_raising_transport_does_not_escape_the_tools_property(self):
        """register_tools_into() iterates .tools inside a bare except, so an
        exception here drops every MCP tool without a word to the user."""

        class _Explodes(_WithTTL):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "tools/list" and _listings(self):
                    self.calls.append(("request", method, params, headers))
                    raise ConnectionResetError("server went away")
                return super().send_request(method, params, timeout, headers)

        transport = _Explodes(60_000, tools=["old_tool"])
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        client._tools_listed_at -= 61
        assert [t.name for t in client.tools] == ["old_tool"]

    def test_a_failed_re_listing_is_not_retried_on_every_read(self):
        """Without a fresh stamp, a dead server gets one tools/list per read
        — and .tools is read once per chat turn plus once per tool search."""

        class _Explodes(_WithTTL):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "tools/list" and _listings(self):
                    self.calls.append(("request", method, params, headers))
                    raise ConnectionResetError("server went away")
                return super().send_request(method, params, timeout, headers)

        transport = _Explodes(60_000, tools=["old_tool"])
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        client._tools_listed_at -= 61
        for _ in range(5):
            _ = client.tools
        assert _listings(transport) == 2

    def test_a_dead_server_is_not_re_probed_even_at_ttl_zero(self):
        """ttlMs: 0 plus a server that is down is the hammering case the
        stamp alone cannot fix: every read is stale by definition, and each
        attempt can block for the full request timeout on the UI thread."""

        class _Explodes(_WithTTL):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "tools/list" and _listings(self):
                    self.calls.append(("request", method, params, headers))
                    raise ConnectionResetError("server went away")
                return super().send_request(method, params, timeout, headers)

        transport = _Explodes(0, tools=["old_tool"])
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        for _ in range(5):
            _ = client.tools
        assert _listings(transport) == 2
        assert [t.name for t in client.tools] == ["old_tool"]

    def test_a_tool_search_honours_the_ttl_too(self):
        """.tools and search_tools() are both public readers of the same
        list; one of them silently serving a stale answer is a trap."""
        transport = _WithTTL(60_000, tools=["old_tool"])
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        transport.tools_payload = [{"name": "new_tool", "description": ""}]
        client._tools_listed_at -= 61
        assert [t.name for t in client.search_tools("new")] == ["new_tool"]

    def test_a_disconnected_client_does_not_re_list(self):
        transport = _WithTTL(0)
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        before = _listings(transport)
        client.disconnect()
        _ = client.tools
        assert _listings(transport) == before


class _OurServer:
    """Our own MCPServer on an ephemeral loopback port, in a thread."""

    def __init__(self, registry):
        self._registry = registry

    def __enter__(self):
        self.transport = transport_mod.HTTPServerTransport(
            host="127.0.0.1", port=0)
        self.transport._handler = server_mod.MCPServer(
            self._registry, transport=self.transport)._handle
        self.httpd = self.transport._make_server()
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()


class TestOurClientAgainstOurServer:
    def test_a_modern_tools_call_survives_our_own_header_validation(self):
        """The contract's two halves, on a real socket, in the modern era."""
        ran = []

        def _echo(text=""):
            ran.append(text)
            return ToolResult(True, "echoed %s" % text)

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            "echo_text", "Echo the text back.", [], handler=_echo))

        with _OurServer(registry) as srv:
            client = MCPClient(
                "self",
                transport=transport_mod.StreamableHTTPClientTransport(
                    srv.url, connect_timeout=5))
            client.connect()
            # Our server serves both eras, so connect() negotiates LEGACY here
            # and asserting MODERN would be asserting the wrong thing. Switch
            # by hand: the negotiation branches are covered against fakes
            # above, and what this test uniquely proves is that a modern
            # request our client BUILDS is one our server ACCEPTS.
            client._era = protocol.ModernEra("2026-07-28", CLIENT_INFO)
            client._transport.protocol_version = "2026-07-28"
            # Bounded well under the 600s default: a handler that deadlocks
            # should fail this test in seconds, not hang CI for ten minutes.
            result = client.call_tool("echo_text", {"text": "hello"}, timeout=5)
            client.disconnect()

        # is_error first, and carrying the content: a rejected request comes
        # back with str(resp["error"]) as its text, so a header-contract break
        # names itself ("-32020, Mcp-Name ... does not name the tool"). Leading
        # with `ran == []` instead would report a header mismatch, a handler
        # crash and a dropped connection with the same bare message.
        assert result.is_error is False, result.content
        assert result.content[0]["text"] == "echoed hello"
        assert ran == ["hello"]


    def test_our_servers_ttl_is_the_one_our_client_acts_on(self, monkeypatch):
        """Both halves live in this repo, so nothing but a test keeps the
        field name and the unit agreed across them."""
        monkeypatch.setenv("MCP_TOOLS_TTL_MS", "60000")

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            "echo_text", "Echo the text back.", [],
            handler=lambda text="": ToolResult(True, text)))

        with _OurServer(registry) as srv:
            client = MCPClient(
                "self",
                transport=transport_mod.StreamableHTTPClientTransport(
                    srv.url, connect_timeout=5))
            client.connect()
            # As above: our server answers initialize, so connect() lands in
            # the legacy era, which defines no hints. Switch by hand to see
            # the modern envelope this test is about.
            client._era = protocol.ModernEra("2026-07-28", CLIENT_INFO)
            client._transport.protocol_version = "2026-07-28"
            client._refresh_tools()
            try:
                # Asserted as a number, not just a key: a server sending
                # seconds would still populate the dict, and the client would
                # then re-list a thousand times too often.
                assert client.tools_cache_hints == {
                    "ttlMs": 60000, "cacheScope": protocol.DEFAULT_CACHE_SCOPE}
                assert client._tools_are_stale() is False
                client._tools_listed_at -= 61
                assert client._tools_are_stale() is True
            finally:
                client.disconnect()


class _ExpiredSessionServer(http.server.BaseHTTPRequestHandler):
    """Answers ``initialize`` 200, then 404s everything after it with a
    JSON-RPC body — the shape a restarted Streamable HTTP server uses once the
    ``Mcp-Session-Id`` it issued no longer exists."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if body.get("method") == "initialize":
            payload = json.dumps(
                protocol.make_response(body.get("id"), {
                    "protocolVersion": "2025-03-26", "capabilities": {}}),
                separators=(",", ":")).encode()
            status = 200
        else:
            payload = json.dumps(
                protocol.make_error(body.get("id"), -32001, "Session expired"),
                separators=(",", ":")).encode()
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _ServingExpiredSession(_Serving):
    def __enter__(self):
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _ExpiredSessionServer)
        self.base = "http://127.0.0.1:%d/mcp" % self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self


class TestAnErrorStatusOnANotificationIsLoud:
    """Recovering a JSON-RPC body from an HTTPError is for REQUESTS only.

    A notification has no legitimate reply, so a body arriving on one is never
    good news. Before this branch the HTTPError came straight out of
    send_notification — and therefore out of connect() — and an expired
    session or a rejected token left the server out of the manager's clients.
    Swallowing it registers a connected server with no tools instead.
    """

    def test_streamable_notification_raises_on_an_error_status(self):
        with _ServingErrors() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            with pytest.raises(urllib.error.HTTPError):
                t.send_notification("notifications/initialized")
            t.stop()

    def test_sse_notification_raises_on_an_error_status(self):
        with _ServingErrors() as srv:
            t = SSEClientTransport("http://127.0.0.1:1/sse", connect_timeout=5)
            t._endpoint_url = srv.base     # skip the GET /sse handshake
            with pytest.raises(urllib.error.HTTPError):
                t.send_notification("notifications/initialized")
            t.stop()

    def test_a_normal_notification_still_goes_through_quietly(self):
        """The 200/202 path must be untouched by the guard above."""
        with _Serving() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            t.send_notification("notifications/initialized")
            t.stop()
        with _SSERunning() as srv:
            t = SSEClientTransport(f"{srv.base}/sse", connect_timeout=5)
            t.start()
            t.send_notification("notifications/initialized")
            t.stop()

    def test_connect_fails_loudly_when_the_session_has_expired(self):
        """End to end: the failure must not read as 'connected, 0 tools'."""
        with _ServingExpiredSession() as srv:
            client = MCPClient(
                "expired",
                transport=StreamableHTTPClientTransport(
                    srv.base, connect_timeout=5))
            with pytest.raises(urllib.error.HTTPError):
                client.connect()
            assert client.is_connected is False
