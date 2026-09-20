"""The client MUST send MCP-Protocol-Version after the handshake (#64 phase 2).

Required of clients since the 2025-06-18 revision: every HTTP request after
``initialize`` carries the *negotiated* protocol version — what the server
returned, not what we asked for. Omitting it works only because a server
seeing no header is told to assume 2025-03-26, which happens to be what we
speak; a server that has dropped that revision is entitled to reject us.
"""

import http.server
import json
import threading

from freecad_ai.mcp import protocol
from freecad_ai.mcp.client import MCPClient, PROTOCOL_VERSION
from freecad_ai.mcp.transport import (
    SSEClientTransport,
    StdioClientTransport,
    StreamableHTTPClientTransport,
)

HEADER = "MCP-Protocol-Version"


class _StreamableStub(http.server.BaseHTTPRequestHandler):
    """Records the MCP-Protocol-Version header of every POST it receives."""
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).seen.append(self.headers.get(HEADER))
        payload = json.dumps(
            protocol.make_response(body.get("id"), {"ok": True}),
            separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _SSEStub(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP+SSE server: advertises /messages, echoes every request."""
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
        type(self).seen.append(self.headers.get(HEADER))
        self.send_response(202)
        self.end_headers()
        if body.get("id") is not None:
            reply = json.dumps(
                protocol.make_response(body["id"], {"ok": True}),
                separators=(",", ":"))
            type(self).stream.write(f"event: message\ndata: {reply}\n\n".encode())
            type(self).stream.flush()


class _Running:
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        self.handler.seen = []
        if self.handler is _SSEStub:
            _SSEStub.ready = threading.Event()
            _SSEStub.done = threading.Event()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"
        return self

    def __exit__(self, *exc):
        if self.handler is _SSEStub:
            _SSEStub.done.set()
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()


class _RecordingTransport:
    """Stands in for a transport that accepts a negotiated version."""
    protocol_version = None

    def __init__(self, initialize_result):
        self._initialize_result = initialize_result
        self.is_alive = True

    def start(self):
        pass

    def send_request(self, method, params=None, timeout=30, headers=None):
        if method == "initialize":
            return {"result": self._initialize_result}
        return {"result": {"tools": []}}

    def send_notification(self, method, params=None, headers=None):
        pass


class TestStreamableHTTPClientSendsHeader:
    def test_header_absent_before_a_version_is_negotiated(self):
        with _Running(_StreamableStub) as srv:
            t = StreamableHTTPClientTransport(f"{srv.base}/mcp", connect_timeout=5)
            t.start()
            t.send_request("tools/list", timeout=5)
            t.stop()
        assert _StreamableStub.seen == [None]

    def test_negotiated_version_is_sent_on_every_later_request(self):
        with _Running(_StreamableStub) as srv:
            t = StreamableHTTPClientTransport(f"{srv.base}/mcp", connect_timeout=5)
            t.start()
            t.protocol_version = "2025-06-18"
            t.send_request("tools/list", timeout=5)
            t.send_request("tools/call", timeout=5)
            t.stop()
        assert _StreamableStub.seen == ["2025-06-18", "2025-06-18"]

    def test_notifications_carry_the_header_too(self):
        with _Running(_StreamableStub) as srv:
            t = StreamableHTTPClientTransport(f"{srv.base}/mcp", connect_timeout=5)
            t.start()
            t.protocol_version = "2025-11-25"
            t.send_notification("notifications/initialized")
            t.stop()
        assert _StreamableStub.seen == ["2025-11-25"]


class TestSSEClientSendsHeader:
    def test_negotiated_version_is_sent_on_posts(self):
        with _Running(_SSEStub) as srv:
            t = SSEClientTransport(f"{srv.base}/sse", connect_timeout=5)
            t.start()
            t.protocol_version = "2025-06-18"
            t.send_request("tools/list", timeout=5)
            t.stop()
        assert _SSEStub.seen == ["2025-06-18"]


class TestClientLatchesNegotiatedVersion:
    def test_server_choice_wins_over_what_we_asked_for(self):
        """We ask for the newest legacy revision; a server answering
        2025-06-18 sets the header to what it chose, not to what we asked."""
        transport = _RecordingTransport(
            {"protocolVersion": "2025-06-18", "capabilities": {}})
        client = MCPClient("test", ["echo"])
        client._transport = transport
        client.connect()
        assert transport.protocol_version == "2025-06-18"
        assert PROTOCOL_VERSION == protocol.LATEST_LEGACY_VERSION
        assert protocol.era_of(PROTOCOL_VERSION) == protocol.LEGACY

    def test_falls_back_to_our_version_when_server_omits_it(self):
        transport = _RecordingTransport({"capabilities": {}})
        client = MCPClient("test", ["echo"])
        client._transport = transport
        client.connect()
        assert transport.protocol_version == PROTOCOL_VERSION

    def test_stdio_transport_tolerates_the_latch(self):
        """Stdio sends no headers; latching a version must not break it."""
        t = StdioClientTransport(["echo"], None)
        t.protocol_version = "2025-06-18"   # must not raise
        assert t.protocol_version == "2025-06-18"
