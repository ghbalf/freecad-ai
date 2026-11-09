import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from freecad_ai.mcp import protocol
from freecad_ai.mcp.client import MCPClient
from freecad_ai.mcp.transport import StreamableHTTPClientTransport


class _StubHandler(BaseHTTPRequestHandler):
    """Configurable Streamable-HTTP server stub. Behaviour set on the class."""
    mode = "json"          # "json" | "sse" | "error"
    seen_session = []      # records Mcp-Session-Id sent by the client

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.seen_session.append(self.headers.get("Mcp-Session-Id"))
        req_id = body.get("id")

        if body.get("method") == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return

        if type(self).mode == "error":
            self.send_error(500, "boom")
            return

        if type(self).mode == "badjson":
            payload = b"this is not valid json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if type(self).mode in {"truncated", "invalid-utf8-json", "invalid-utf8-sse"}:
            payload = b'{"jsonrpc":"2.0",'
            content_type = "application/json"
            if type(self).mode.startswith("invalid-utf8"):
                payload = b"\xff\n\n"
                if type(self).mode.endswith("sse"):
                    content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(
                len(payload) + (100 if type(self).mode == "truncated" else 0)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if type(self).mode in {"stalled-json", "stalled-sse"}:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"
                             if type(self).mode.endswith("sse") else "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.flush()
            type(self).release.wait(5)
            return

        reply = protocol.make_response(req_id, {"echo": body.get("method")})

        if type(self).mode == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if body.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "sess-123")
            self.end_headers()
            data = json.dumps(reply, separators=(",", ":"))
            self.wfile.write(f"event: message\ndata: {data}\n\n".encode())
            self.wfile.flush()
        else:  # json
            payload = json.dumps(reply, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if body.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "sess-123")
            self.end_headers()
            self.wfile.write(payload)


class _RunningStub:
    def __init__(self, mode):
        self.mode = mode

    def __enter__(self):
        _StubHandler.mode = self.mode
        _StubHandler.seen_session = []
        _StubHandler.release = threading.Event()
        self.httpd = HTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        return self

    def __exit__(self, *exc):
        _StubHandler.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class TestStreamableHTTPClient:
    def test_truncated_tool_response_returns_tool_error(self):
        with _RunningStub("truncated") as srv:
            transport = StreamableHTTPClientTransport(srv.url)
            client = MCPClient("truncated-peer", transport=transport)
            result = client.call_tool("inspect", {}, timeout=5)
            assert result.is_error is True
            assert "IncompleteRead" in result.content[0]["text"]
            assert len(_StubHandler.seen_session) == 1

    @pytest.mark.parametrize("mode", [
        "truncated", "invalid-utf8-json", "invalid-utf8-sse",
        "stalled-json", "stalled-sse",
    ])
    def test_response_read_failure_returns_request_error(self, mode):
        with _RunningStub(mode) as srv:
            transport = StreamableHTTPClientTransport(srv.url)
            response = transport.send_request("tools/call", {"name": "inspect"}, timeout=0.2)
            assert response["id"] == 1
            assert response["error"]["code"] == protocol.INTERNAL_ERROR
            # The failed call must not be replayed; a later explicit call can recover.
            assert len(_StubHandler.seen_session) == 1
            _StubHandler.release.set()
            _StubHandler.mode = "json"
            recovered = transport.send_request("tools/list", timeout=5)
            assert recovered["id"] == 2
            assert recovered["result"]["echo"] == "tools/list"

    def test_inline_json_response(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            resp = t.send_request("tools/list", timeout=5)
            assert resp["result"]["echo"] == "tools/list"
            t.stop()

    def test_event_stream_response(self):
        with _RunningStub("sse") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            resp = t.send_request("tools/list", timeout=5)
            assert resp["result"]["echo"] == "tools/list"
            t.stop()

    def test_session_id_captured_and_echoed(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            t.send_request("initialize", {"protocolVersion": "2025-03-26"}, timeout=5)
            t.send_request("tools/list", timeout=5)
            # First POST sent no session id; second echoed the captured one.
            assert _StubHandler.seen_session[0] is None
            assert _StubHandler.seen_session[1] == "sess-123"
            t.stop()

    def test_notification_posts_without_reply(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            t.send_notification("notifications/initialized")  # must not raise
            t.stop()

    def test_http_error_surfaces_as_error_dict(self):
        with _RunningStub("error") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            resp = t.send_request("tools/list", timeout=5)
            assert "error" in resp
            t.stop()

    def test_is_alive_transitions(self):
        with _RunningStub("json") as srv:
            t = StreamableHTTPClientTransport(srv.url)
            assert t.is_alive is False
            t.start()
            assert t.is_alive is True
            t.stop()
            assert t.is_alive is False

    def test_malformed_json_response_surfaces_error(self):
        # A 200 application/json response with a non-JSON body must return an
        # error dict, not raise out of send_request.
        with _RunningStub("badjson") as srv:
            t = StreamableHTTPClientTransport(srv.url, connect_timeout=5)
            t.start()
            resp = t.send_request("tools/list", timeout=5)
            assert "error" in resp
            t.stop()

    def test_transport_error_closes_closeable_exception(self):
        # On a transport failure whose exception is file-like (e.g. HTTPError),
        # send_request must close it rather than leak the socket.
        from unittest.mock import patch

        closed = {"v": False}

        class ClosableError(Exception):
            def close(self):
                closed["v"] = True

        t = StreamableHTTPClientTransport("https://h/mcp")
        t.start()
        with patch.object(t, "_post", side_effect=ClosableError("boom")):
            resp = t.send_request("tools/list", timeout=1)
        assert "error" in resp
        assert closed["v"] is True
        t.stop()

    def test_transport_error_with_raising_close_still_returns_error(self):
        from unittest.mock import patch

        class BadCloseError(Exception):
            def close(self):
                raise OSError("close failed")

        t = StreamableHTTPClientTransport("https://h/mcp")
        t.start()
        with patch.object(t, "_post", side_effect=BadCloseError("boom")):
            resp = t.send_request("tools/list", timeout=1)  # must not raise
        assert "error" in resp
        t.stop()
