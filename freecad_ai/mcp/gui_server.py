"""Process-wide controller for the MCP server hosted inside FreeCAD.

Three routes start this server, and all of them land in the same process:

  * ``FreeCAD.AppImage /path/to/mcp_server_http.py`` on the command line
  * ``exec(open(".../mcp_server_http.py").read())`` in the Python console
  * the FreeCAD AI toolbar toggle

They must share one object. Without it the toggle renders unchecked next to a
server that is already listening, and clicking it builds a second transport
that dies on EADDRINUSE inside a daemon thread — visible only as a console
traceback. Port-probing is not a substitute: "something is listening on 3000"
does not mean it is ours.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000


def resolve_server_address(cfg=None):
    """Return ``(host, port)``: env beats config, config beats defaults.

    Env wins so every documented command-line recipe keeps working unchanged,
    including the wiki's ``MCP_PORT=…`` and Flatpak invocations.
    """
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    if cfg is not None:
        host = getattr(cfg, "mcp_server_host", "") or DEFAULT_HOST
        port = getattr(cfg, "mcp_server_port", 0) or DEFAULT_PORT

    env_host = os.environ.get("MCP_HOST")
    if env_host:
        host = env_host

    env_port = os.environ.get("MCP_PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            logger.warning("Ignoring non-numeric MCP_PORT=%r", env_port)

    return host, port


def _default_backend():
    """Build the tool registry and the Qt main-thread executor.

    ``include_mcp=False``: this registry is what we *serve*, so it must not
    re-export tools the workbench's own MCP client pulled in from elsewhere.
    The executor marshals every call onto the Qt main thread because FreeCAD's
    document API is not thread-safe.
    """
    from ..tools.setup import create_default_registry
    from ..tools.executor_utils import QtMainThreadToolExecutor

    registry = create_default_registry(include_mcp=False)
    executor = QtMainThreadToolExecutor()
    executor.set_registry(registry)
    return registry, executor


class ServerController:
    """Owns the one MCP server that may run in this process."""

    def __init__(self, backend_factory=None):
        self._backend_factory = backend_factory or _default_backend
        self._transport = None
        self._thread = None
        self._registry = None
        self._executor = None
        self._url = None

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self):
        return self._url if self.is_running() else None

    def start(self, host, port):
        """Start serving and return the URL. Raises OSError if the bind fails.

        Binds *before* building the registry: the bind is the only step that
        realistically fails, it is cheap, and failing first leaves no
        half-initialised backend behind.
        """
        if self.is_running():
            return self._url

        # A serve thread that died on its own leaves is_running() False while the
        # listening socket is still open. Reap it, or every later start() on this
        # port fails with EADDRINUSE until FreeCAD restarts.
        if self._transport is not None:
            self._transport.stop()
            self._transport = None
            self._url = None

        from .transport import SSEServerTransport

        transport = SSEServerTransport(host=host, port=port)
        transport.bind()  # raises OSError on the caller's thread — the point

        if self._registry is None or self._executor is None:
            self._registry, self._executor = self._backend_factory()

        from .server import MCPServer

        server = MCPServer(self._registry, transport=transport,
                           executor=self._executor)
        thread = threading.Thread(
            target=self._serve, args=(server,), daemon=True,
            name="mcp-sse-server")
        thread.start()

        self._transport = transport
        self._thread = thread
        self._url = "http://%s:%d/sse" % (host, port)
        logger.info("MCP SSE server listening on %s", self._url)
        return self._url

    def _serve(self, server):
        # MCPServer.run() calls transport.run(), which is bind() + serve();
        # bind() is idempotent, so the socket we already secured is reused.
        try:
            server.run()
        except Exception:
            logger.exception("MCP SSE server stopped unexpectedly")

    def stop(self):
        """Shut the server down and release the port. Idempotent."""
        transport, self._transport = self._transport, None
        thread, self._thread = self._thread, None
        self._url = None
        if transport is not None:
            transport.stop()
        if thread is not None:
            thread.join(timeout=5)


_controller = None


def get_server_controller():
    """Return the process-wide controller, creating it on first use."""
    global _controller
    if _controller is None:
        _controller = ServerController()
    return _controller
