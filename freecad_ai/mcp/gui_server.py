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


def resolve_allowed_hosts(cfg=None):
    """Return the ``Host``-header allowlist, or ``None`` for the default.

    ``None`` is not the same as the loopback list. The transport derives its
    own default only when ``allowed_hosts is None``, and that branch is where
    a wildcard bind is rejected. Passing an explicit list — even one identical
    to the default — is the documented opt-in to a wider policy, so returning
    a list when the user configured nothing would silently disarm that guard
    and restore #60's every-client-gets-403 dead end.

    Env beats config, matching MCP_HOST / MCP_PORT. Entries are comma
    separated.

    Raises ValueError on a ``*`` entry: the allowlist is the only thing
    standing between a wildcard bind and unauthenticated remote tool
    execution until #59 lands a bearer token, so widening it to "any Host"
    is refused rather than quietly honoured.
    """
    raw = os.environ.get("MCP_ALLOWED_HOSTS")
    if raw:
        hosts = raw.split(",")
    elif cfg is not None:
        hosts = list(getattr(cfg, "mcp_server_allowed_hosts", None) or [])
    else:
        hosts = []

    hosts = [h.strip() for h in hosts if h and h.strip()]
    if any(h == "*" for h in hosts):
        raise ValueError(
            "'*' is not accepted in the MCP allowed-hosts list. Unless a "
            "bearer token is configured (MCP_AUTH_TOKEN or the AI Settings "
            "dialog), this allowlist is the only thing keeping a wildcard "
            "bind from serving arbitrary Python to anything that can reach "
            "it. Name the concrete hosts clients dial instead.")
    return hosts or None


def resolve_auth_token(cfg=None):
    """Return the bearer token every request must present, or ``None`` to
    leave the server unauthenticated: the historical default (#59).

    Env beats config, matching MCP_HOST / MCP_PORT / MCP_ALLOWED_HOSTS. An
    empty string from either source means "no token configured", not "an
    empty token is required": the transport must never be handed a token it
    would enforce against a header nobody can send.
    """
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token and cfg is not None:
        token = getattr(cfg, "mcp_server_auth_token", "")
    return token or None


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

    def start(self, host, port, allowed_hosts=None, auth_token=None):
        """Start serving and return the URL.

        Raises OSError if the bind fails, or if ``host`` is a wildcard address
        while ``allowed_hosts`` is None — see HTTPServerTransport.

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

        from .transport import HTTPServerTransport

        transport = HTTPServerTransport(host=host, port=port,
                                        allowed_hosts=allowed_hosts,
                                        auth_token=auth_token)
        transport.bind()  # raises OSError on the caller's thread — the point

        if self._registry is None or self._executor is None:
            self._registry, self._executor = self._backend_factory()

        from .server import MCPServer

        server = MCPServer(self._registry, transport=transport,
                           executor=self._executor)
        thread = threading.Thread(
            target=self._serve, args=(server,), daemon=True,
            name="mcp-http-server")
        thread.start()

        self._transport = transport
        self._thread = thread
        # The advertised URL is the Streamable HTTP endpoint: HTTP+SSE is
        # deprecated with a removal window (#65), so new configurations should
        # not be pointed at it. The legacy pair keeps serving regardless.
        self._url = "http://%s:%d/mcp" % (host, port)
        logger.info(
            "MCP server listening on %s (legacy HTTP+SSE also served at "
            "http://%s:%d/sse)", self._url, host, port)
        return self._url

    def _serve(self, server):
        # MCPServer.run() calls transport.run(), which is bind() + serve();
        # bind() is idempotent, so the socket we already secured is reused.
        try:
            server.run()
        except Exception:
            logger.exception("MCP server stopped unexpectedly")

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
