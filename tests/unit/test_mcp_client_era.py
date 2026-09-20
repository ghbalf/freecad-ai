"""Client-side era objects and negotiation (#86)."""

from freecad_ai.mcp import protocol

CLIENT_INFO = {"name": "FreeCAD AI", "version": "0.1.0"}


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


import http.server
import json
import threading

from freecad_ai.mcp.transport import (
    StdioClientTransport,
    StreamableHTTPClientTransport,
)


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

    def test_stdio_ignores_headers(self):
        """No header channel exists; being handed some must not raise."""
        t = StdioClientTransport(["echo"], None)
        # Not started: we assert the signature accepts the argument, which is
        # what the client relies on when a stdio server speaks the modern era.
        import inspect
        for method in (t.send_request, t.send_notification):
            assert "headers" in inspect.signature(method).parameters
