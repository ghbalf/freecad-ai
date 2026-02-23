"""STDIO transports for MCP communication.

StdioClientTransport — manages a subprocess MCP server (client side).
StdioServerTransport — reads stdin / writes stdout (server side).
"""

import json
import subprocess
import sys
import threading
from typing import Any, Callable

from . import protocol


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
