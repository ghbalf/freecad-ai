"""Transports for MCP communication.

StdioClientTransport — manages a subprocess MCP server (client side).
StdioServerTransport — reads stdin / writes stdout (server side).
SSEServerTransport  — serves MCP over HTTP with Server-Sent Events.
"""

import json
import logging
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Callable

from . import protocol

logger = logging.getLogger(__name__)


def _iter_sse_events(fp):
    """Yield (event, data) tuples from a streaming SSE file object.

    Parses the subset of the text/event-stream format MCP uses: ``event:`` and
    ``data:`` fields terminated by a blank line. Multiple ``data:`` lines join
    with a newline. Comment lines (leading ``:``) and other fields (``id:``,
    ``retry:``) are ignored. The event name defaults to ``"message"`` when only
    ``data`` is present.
    """
    event = None
    data_lines = []
    for raw in fp:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.rstrip("\n").rstrip("\r")
        if line == "":
            if data_lines:
                yield (event or "message", "\n".join(data_lines))
            event = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    # A trailing frame with no terminating blank line is dropped (matches the
    # wire convention that events are terminated by a blank line).


class _RequestCorrelator:
    """Matches asynchronous JSON-RPC responses to blocked callers by id.

    Used by ``SSEClientTransport`` (whose replies arrive on a separate reader
    thread). ``StdioClientTransport`` keeps its own equivalent inline copy.
    """

    def __init__(self):
        self._pending = {}   # id -> {"event": Event, "response": dict|None}
        self._lock = threading.Lock()
        self._next_id = 1

    def next_id(self):
        with self._lock:
            rid = self._next_id
            self._next_id += 1
        return rid

    def register(self, req_id):
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "response": None}
        return event

    def resolve(self, msg):
        msg_id = msg.get("id")
        if msg_id is None:
            return
        with self._lock:
            entry = self._pending.get(msg_id)
            if entry is not None:
                entry["response"] = msg
                entry["event"].set()

    def wait(self, req_id, event, timeout):
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request id={req_id} timed out after {timeout}s")
        with self._lock:
            entry = self._pending.pop(req_id)
        return entry["response"]

    def cancel(self, req_id):
        with self._lock:
            self._pending.pop(req_id, None)

    def fail_all(self, error):
        with self._lock:
            for entry in self._pending.values():
                entry["response"] = error
                entry["event"].set()


class StdioClientTransport:
    """Manages a subprocess MCP server via stdin/stdout pipes."""

    def __init__(self, command: list[str], env: dict | None = None):
        self._command = command
        self._env = env
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._pending: dict[Any, dict] = {}  # id -> {"event": Event, "response": dict|None}
        self._lock = threading.Lock()
        self._next_id = 1
        self._running = False

    def start(self):
        """Launch the subprocess and start the reader thread."""
        import os
        env = os.environ.copy()

        # FreeCAD's AppImage sets PYTHONHOME/PYTHONPATH to its bundled
        # Python, which breaks any subprocess that uses a different Python.
        # Strip these so the subprocess inherits a clean environment.
        for key in ("PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)

        # Restore a sane PATH — the AppImage prepends its own bin dirs.
        # Keep system paths so npx/node/python3 are findable.
        path = env.get("PATH", "")
        clean_parts = [p for p in path.split(os.pathsep)
                       if ".mount_FreeCA" not in p]
        if clean_parts:
            env["PATH"] = os.pathsep.join(clean_parts)

        if self._env:
            env.update(self._env)

        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def send_request(self, method: str, params: dict | None = None,
                     timeout: float = 30) -> dict:
        """Send a JSON-RPC request and wait for the matching response."""
        with self._lock:
            req_id = self._next_id
            self._next_id += 1

        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "response": None}

        msg = protocol.make_request(method, params, id=req_id)
        self._write(msg)

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request '{method}' timed out after {timeout}s")

        with self._lock:
            entry = self._pending.pop(req_id)
        return entry["response"]

    def send_notification(self, method: str, params: dict | None = None):
        """Send a JSON-RPC notification (fire-and-forget)."""
        msg = protocol.make_notification(method, params)
        self._write(msg)

    def _write(self, msg: dict):
        """Write a JSON-RPC message to the subprocess stdin."""
        if self._process and self._process.stdin:
            data = protocol.encode(msg)
            self._process.stdin.write(data)
            self._process.stdin.flush()

    def _read_loop(self):
        """Background thread: read stdout line-by-line, match responses."""
        while self._running and self._process and self._process.stdout:
            try:
                line = self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8").strip()
                if not text:
                    continue
                msg = protocol.decode(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            except Exception:
                break

            # Match response to pending request by id
            msg_id = msg.get("id")
            if msg_id is not None:
                with self._lock:
                    entry = self._pending.get(msg_id)
                    if entry:
                        entry["response"] = msg
                        entry["event"].set()

        self._running = False

    def stop(self):
        """Terminate the subprocess."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        # Unblock any pending requests
        with self._lock:
            for entry in self._pending.values():
                entry["response"] = protocol.make_error(
                    None, protocol.INTERNAL_ERROR, "Transport stopped"
                )
                entry["event"].set()
            self._pending.clear()

    @property
    def is_alive(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None


class SSEClientTransport:
    """Client transport speaking the legacy MCP HTTP+SSE protocol.

    ``start()`` opens ``GET <url>`` as a streaming response on a reader thread,
    reads the advertised ``endpoint`` event, then POSTs JSON-RPC requests to
    that endpoint; responses arrive back over the GET stream and are matched by
    id via ``_RequestCorrelator``.
    """

    def __init__(self, url, headers=None, *, ssl_context=None,
                 connect_timeout=30, read_timeout=None):
        self._url = url
        self._headers = dict(headers or {})
        self._ssl_context = ssl_context
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._correlator = _RequestCorrelator()
        self._resp = None
        self._reader_thread = None
        self._endpoint_url = None
        self._endpoint_ready = threading.Event()
        self._running = False

    def start(self):
        req = urllib.request.Request(self._url, method="GET")
        for key, value in self._headers.items():
            req.add_header(key, value)
        req.add_header("Accept", "text/event-stream")
        self._resp = urllib.request.urlopen(
            req, timeout=self._connect_timeout, context=self._ssl_context)
        self._set_stream_timeout(self._read_timeout)
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        if not self._endpoint_ready.wait(self._connect_timeout):
            self.stop()
            raise TimeoutError(
                f"MCP SSE server '{self._url}' sent no endpoint event "
                f"within {self._connect_timeout}s")
        if self._endpoint_url is None:
            self.stop()
            raise RuntimeError(f"MCP SSE stream '{self._url}' closed before handshake")

    def _set_stream_timeout(self, timeout):
        """Reset the stream socket timeout after the connect phase.

        urllib applies ``connect_timeout`` to the whole socket, which would make
        an idle SSE stream time out after ``connect_timeout`` seconds. Once the
        response headers are in (connect is done), switch the socket to
        ``read_timeout`` (None = block, no idle cap) so a quiet-but-healthy
        stream is not killed. Best-effort: if the socket isn't reachable, leave
        the connect timeout in place.
        """
        sock = getattr(getattr(getattr(self._resp, "fp", None), "raw", None),
                       "_sock", None)
        if sock is not None:
            try:
                sock.settimeout(timeout)
            except OSError:
                pass

    def _read_loop(self):
        try:
            for event, data in _iter_sse_events(self._resp):
                if event == "endpoint":
                    self._endpoint_url = urllib.parse.urljoin(self._url, data)
                    self._endpoint_ready.set()
                elif event == "message":
                    try:
                        msg = protocol.decode(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    self._correlator.resolve(msg)
        except Exception:
            pass
        finally:
            self._running = False
            self._endpoint_ready.set()  # unblock start() if the stream died early
            self._correlator.fail_all(protocol.make_error(
                None, protocol.INTERNAL_ERROR, "SSE stream closed"))

    def send_request(self, method, params=None, timeout=30):
        req_id = self._correlator.next_id()
        event = self._correlator.register(req_id)
        try:
            self._post(protocol.make_request(method, params, id=req_id))
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
            self._correlator.cancel(req_id)
            return protocol.make_error(req_id, protocol.INTERNAL_ERROR, str(exc))
        return self._correlator.wait(req_id, event, timeout)

    def send_notification(self, method, params=None):
        self._post(protocol.make_notification(method, params))

    def _post(self, msg):
        if self._endpoint_url is None:
            raise RuntimeError("MCP SSE transport not connected (no endpoint)")
        req = urllib.request.Request(
            self._endpoint_url, data=protocol.encode(msg), method="POST")
        for key, value in self._headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(
            req, timeout=self._connect_timeout, context=self._ssl_context)
        resp.read()   # drain the 202 body
        resp.close()

    def stop(self):
        self._running = False
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None
        self._correlator.fail_all(
            protocol.make_error(None, protocol.INTERNAL_ERROR, "Transport stopped"))

    @property
    def is_alive(self):
        return (self._running and self._reader_thread is not None
                and self._reader_thread.is_alive())


class StreamableHTTPClientTransport:
    """Client transport speaking the MCP Streamable HTTP protocol.

    Each ``send_request`` POSTs JSON-RPC to a single endpoint; the reply is read
    synchronously on the calling thread — either an inline ``application/json``
    body or a ``text/event-stream`` walked until the matching id. The
    ``Mcp-Session-Id`` returned at ``initialize`` is echoed on later requests.
    """

    def __init__(self, url, headers=None, *, ssl_context=None, connect_timeout=30):
        self._url = url
        self._headers = dict(headers or {})
        self._ssl_context = ssl_context
        self._connect_timeout = connect_timeout
        self._session_id = None
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True

    def _alloc_id(self):
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
        return rid

    def send_request(self, method, params=None, timeout=30):
        req_id = self._alloc_id()
        msg = protocol.make_request(method, params, id=req_id)
        try:
            resp = self._post(msg, timeout)
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
            closer = getattr(exc, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 — cleanup must not break the never-raise contract
                    pass
            return protocol.make_error(req_id, protocol.INTERNAL_ERROR, str(exc))

        session = resp.headers.get("Mcp-Session-Id")
        if session:
            self._session_id = session
        content_type = resp.headers.get("Content-Type", "")
        try:
            if "text/event-stream" in content_type:
                for event, data in _iter_sse_events(resp):
                    if event != "message":
                        continue
                    try:
                        candidate = protocol.decode(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if candidate.get("id") == req_id:
                        return candidate
                return protocol.make_error(
                    req_id, protocol.INTERNAL_ERROR,
                    "MCP HTTP stream closed before a matching response")
            body = resp.read().decode("utf-8")
            try:
                return protocol.decode(body)
            except (json.JSONDecodeError, ValueError):
                return protocol.make_error(
                    req_id, protocol.INTERNAL_ERROR,
                    "MCP HTTP response was not valid JSON")
        finally:
            resp.close()

    def send_notification(self, method, params=None):
        resp = self._post(protocol.make_notification(method, params),
                          self._connect_timeout)
        resp.read()
        resp.close()

    def _post(self, msg, timeout):
        req = urllib.request.Request(
            self._url, data=protocol.encode(msg), method="POST")
        for key, value in self._headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        return urllib.request.urlopen(
            req, timeout=timeout, context=self._ssl_context)

    def stop(self):
        self._running = False

    @property
    def is_alive(self):
        return self._running


class StdioServerTransport:
    """Server-side transport: reads JSON-RPC from stdin, writes to stdout."""

    def run(self, handler: Callable[[dict], dict | None]):
        """Blocking loop: read requests from stdin, dispatch to handler, write responses."""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                text = line.strip()
                if not text:
                    continue
                msg = protocol.decode(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._write(protocol.make_error(
                    None, protocol.PARSE_ERROR, "Parse error"
                ))
                continue
            except Exception:
                break

            try:
                response = handler(msg)
            except Exception as e:
                msg_id = msg.get("id")
                if msg_id is not None:
                    response = protocol.make_error(
                        msg_id, protocol.INTERNAL_ERROR, str(e)
                    )
                else:
                    response = None

            if response is not None:
                self._write(response)

    def _write(self, msg: dict):
        """Write a JSON-RPC message to stdout."""
        data = json.dumps(msg, separators=(",", ":")) + "\n"
        sys.stdout.write(data)
        sys.stdout.flush()


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class SSEServerTransport:
    """Server-side transport: serves MCP over HTTP + Server-Sent Events.

    Endpoints:
        GET  /sse       — SSE event stream (client subscribes here)
        POST /messages  — JSON-RPC requests (responses arrive via SSE)

    Designed for a single connected client at a time (typical for a
    desktop-app MCP server like FreeCAD).

    Because ``POST /messages`` executes arbitrary tools (including run_macro),
    every request is gated: the ``Host`` header must be loopback (a
    DNS-rebinding guard) and any cross-origin ``Origin`` is rejected. Native
    MCP clients send no ``Origin``; a malicious web page's ``fetch()`` always
    does, so this blocks browser drive-by tool invocation without breaking the
    documented local client. ``allowed_hosts``/``allowed_origins`` widen the
    policy for advanced (e.g. deliberately LAN-exposed) deployments.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 3000,
                 allowed_hosts=None, allowed_origins=()):
        self._host = host
        self._port = port
        self._handler: Callable[[dict], dict | None] | None = None
        self._sse_wfile: Any = None
        self._sse_lock = threading.Lock()
        self._httpd = None
        self._serving = False
        self._lifecycle_lock = threading.Lock()
        if allowed_hosts is None:
            allowed_hosts = _LOOPBACK_HOSTS | {host.lower()}
        self._allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self._allowed_origins = frozenset(allowed_origins)

    @staticmethod
    def _hostname_of(host_header) -> str:
        """Extract the bare hostname (no port) from a Host header value."""
        if not host_header:
            return ""
        value = host_header.strip()
        if value.startswith("["):  # IPv6 literal, e.g. [::1]:3000
            return value[1:].split("]", 1)[0].lower()
        return value.split(":", 1)[0].lower()

    def _request_allowed(self, host_header, origin_header) -> bool:
        """Authorize a request by its Host (DNS-rebinding) and Origin (CSRF)."""
        if self._hostname_of(host_header) not in self._allowed_hosts:
            return False
        if origin_header is not None and origin_header not in self._allowed_origins:
            return False
        return True

    def bind(self):
        """Create and bind the listening socket. Raises OSError if unavailable.

        Split out of ``run`` so a caller on the GUI thread learns about a bind
        failure (EADDRINUSE, EACCES) synchronously. When the bind happened
        inside the serve thread the traceback went to the console and nothing
        else: FreeCAD carried on as if the server had started.

        Idempotent — binding an already-bound transport is a no-op.
        """
        with self._lifecycle_lock:
            if self._httpd is None:
                self._httpd = self._make_server()

    def serve(self, handler: Callable[[dict], dict | None] | None = None):
        """Serve until stop(). Requires a prior bind()."""
        with self._lifecycle_lock:
            if handler is not None:
                self._handler = handler
            httpd = self._httpd
            if httpd is None:
                raise RuntimeError("bind() must be called before serve()")
            self._serving = True
        logger.info("MCP SSE server listening on http://%s:%d", self._host, self._port)
        try:
            httpd.serve_forever()
        finally:
            with self._lifecycle_lock:
                self._serving = False

    def stop(self):
        """Shut down and release the socket. Safe when never bound.

        ``shutdown()`` is only safe once ``serve_forever()`` is running: it
        waits on an event that only serve_forever's exit path sets, so calling
        it on a bound-but-never-served socket blocks forever. Bound but never
        served therefore goes straight to ``server_close()``.

        The two values are captured under the lock and then released before
        any blocking call: holding the lock across ``shutdown()`` would
        deadlock against ``serve()``'s ``finally`` clause, which needs the
        same lock to clear ``_serving``.

        Shutting the listening socket down does not touch an already-attached
        SSE client: its ``process_request_thread`` sits in the keepalive loop
        and keeps writing, so the client never learns the server went away and
        its later POSTs are answered 202 by a transport that drops the reply.
        Closing ``_sse_wfile`` here makes the next keepalive write fail, which
        ends that thread and gives the client a clean EOF. Everything else
        already treats a None ``_sse_wfile`` as "no client attached", which is
        exactly the state left behind. Done outside ``_lifecycle_lock`` for
        the same deadlock reason as above.
        """
        with self._lifecycle_lock:
            httpd, self._httpd = self._httpd, None
            serving = self._serving

        with self._sse_lock:
            wfile, self._sse_wfile = self._sse_wfile, None
        if wfile is not None:
            try:
                wfile.close()
            except Exception:
                pass

        if httpd is None:
            return
        if serving:
            httpd.shutdown()
        httpd.server_close()

    def run(self, handler: Callable[[dict], dict | None]):
        """Start the HTTP server (blocking). Unchanged: bind, then serve."""
        self._handler = handler
        self.bind()
        self.serve()

    def _make_server(self):
        """Build the threaded HTTP server (split out for testability)."""
        transport = self

        class RequestHandler(BaseHTTPRequestHandler):
            # Without a timeout the connection socket blocks forever, and
            # ``_write_locked`` holds ``_sse_lock`` across its write: a client
            # that stops reading would pin that lock and freeze ``stop()`` —
            # which runs on the Qt main thread — hanging all of FreeCAD (#63).
            # ``StreamRequestHandler.setup()`` applies this via settimeout().
            # Generous enough that a merely slow client is not dropped; a
            # timed-out write surfaces as ``socket.timeout``, which is
            # ``TimeoutError`` and so already handled as a dropped client.
            timeout = 30

            def log_message(self, fmt, *args):
                logger.debug(fmt, *args)

            def _base_path(self):
                return self.path.split("?")[0].rstrip("/")

            def _authorized(self):
                if transport._request_allowed(
                    self.headers.get("Host"), self.headers.get("Origin")
                ):
                    return True
                self.send_error(403)
                return False

            def do_GET(self):
                if not self._authorized():
                    return
                if self._base_path() == "/sse":
                    self._handle_sse()
                else:
                    self.send_error(404)

            def do_POST(self):
                if not self._authorized():
                    return
                if self._base_path() == "/messages":
                    self._handle_messages()
                else:
                    self.send_error(404)

            def _handle_sse(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                session_id = uuid.uuid4().hex
                with transport._sse_lock:
                    transport._sse_wfile = self.wfile

                try:
                    endpoint_data = f"/messages?sessionId={session_id}"
                    endpoint_event = (
                        f"event: endpoint\ndata: {endpoint_data}\n\n".encode()
                    )
                    if not transport._write_locked(endpoint_event):
                        return
                    while transport._write_locked(b": keepalive\n\n"):
                        time.sleep(15)
                finally:
                    with transport._sse_lock:
                        if transport._sse_wfile is self.wfile:
                            transport._sse_wfile = None

            def _handle_messages(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")

                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    err = protocol.make_error(
                        None, protocol.PARSE_ERROR, "Parse error"
                    )
                    self._send_json(400, err)
                    return

                try:
                    response = transport._handler(msg) if transport._handler else None
                except Exception as e:
                    msg_id = msg.get("id")
                    response = protocol.make_error(
                        msg_id, protocol.INTERNAL_ERROR, str(e)
                    ) if msg_id is not None else None

                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"accepted":true}')
                self.wfile.flush()

                if response is not None:
                    transport._send_sse(response)

            def _send_json(self, code: int, msg: dict):
                data = json.dumps(msg, separators=(",", ":")).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                # No permissive CORS: a cross-origin preflight gets no
                # Access-Control-Allow-Origin, so the browser blocks the
                # follow-up request (do_POST also rejects it server-side).
                self.send_response(204)
                self.end_headers()

        class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        return ThreadedHTTPServer((self._host, self._port), RequestHandler)

    def _send_sse(self, msg: dict):
        """Send a JSON-RPC message to the connected SSE client."""
        data = json.dumps(msg, separators=(",", ":"))
        payload = f"event: message\ndata: {data}\n\n".encode()
        self._write_locked(payload)

    def _write_locked(self, payload: bytes) -> bool:
        """Write raw bytes to the SSE client, serialized by ``_sse_lock``.

        The lock is held across the write *and* flush (not just the pointer
        read), so the keepalive loop and tool responses — which run on
        separate ThreadingMixIn request threads — cannot interleave bytes and
        corrupt the event stream. Returns False if there is no connected
        client or the connection has dropped (the client is then cleared).
        """
        with self._sse_lock:
            wfile = self._sse_wfile
            if wfile is None:
                return False
            try:
                wfile.write(payload)
                wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._sse_wfile = None
                return False
