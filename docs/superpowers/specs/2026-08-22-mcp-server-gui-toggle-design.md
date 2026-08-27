# MCP Server GUI Toggle (#55 follow-up) — Design

**Issue:** follow-up to [#55 — use this addon with claude cli / other MCP clients](https://github.com/ghbalf/freecad-ai/issues/55) (closed); suggested by @s-light in the closing comment
**Date:** 2026-08-22
**Status:** Approved (design)

## Goal

Add a checkable **Start MCP Server** command to the FreeCAD AI toolbar and menu, so
the HTTP/SSE server can be started and stopped from inside a running FreeCAD instead
of only via a command-line argument or a pasted `exec(open(...))` snippet.

The toggle must report the **truth** about whether a server is running in this
process, including one started by `mcp_server_http.py` from the command line or the
Python console — all three routes land in the same process.

## Approved decisions

| Decision | Choice |
|----------|--------|
| State ownership | **Shared controller singleton**; CLI, console `exec`, and the button all start/stop the same object |
| Persistence | **None** — the toggle never auto-starts. No persisted "was on" flag, no auto-start setting |
| Host / port source | New `AppConfig` fields, editable in the Settings dialog; `MCP_HOST`/`MCP_PORT` override when set |
| Bind scope | **Host and port both freely editable**, including non-loopback, with an explicit no-authentication warning |
| Success feedback | Quiet — Report view line with the real URL, plus a status-bar message |
| Failure feedback | Modal `QMessageBox` naming the cause and pointing at the Settings row |
| `mcp_server_http.py` | Refactored to delegate to the controller (observable behaviour unchanged) |
| Dependencies | **stdlib only**, consistent with the rest of `freecad_ai/mcp/` |

## Problems this fixes

Three defects in the current code, all verified on FreeCAD 1.1.1 during design:

1. **Silent bind failure.** `SSEServerTransport.run()` builds the `ThreadedHTTPServer`
   *inside* the server thread. A port conflict raises `OSError: [Errno 98] Address
   already in use` in a daemon thread: FreeCAD carries on, no dialog, no status change.
2. **Success announced before it happens.** `mcp_server_http.py` prints
   `MCP SSE server running on http://...` *before* the bind is attempted, so it reports
   success for a server that never started.
3. **No stop path.** `run()` ends in `serve_forever()` and never stores the `httpd`
   handle, so nothing can shut it down. Today the only way to stop the server is to
   quit FreeCAD.

Defect 1 is what makes a toggle actively harmful without a fix: the button would flip
to "on" while nothing is listening.

## Non-goals

- **Authentication.** The server is unauthenticated today and stays that way here.
  Tracked separately (see *Follow-up issues*). This design only makes the existing
  exposure honestly documented at the point of use.
- **STDIO mode.** `mcp_server_entry.py` owns the process's stdin/stdout; there is no
  coherent way to toggle it from a GUI. Out of scope.
- **Auto-start on workbench activation.** Explicitly rejected — see *Approved decisions*.

## Architecture

### New: `freecad_ai/mcp/gui_server.py`

A process-wide singleton, the single source of truth for "is a server running here."

```python
get_server_controller() -> ServerController

class ServerController:
    def start(self, host: str, port: int) -> str   # returns URL; raises OSError on bind failure
    def stop(self) -> None                          # idempotent
    def is_running(self) -> bool
    @property
    def url(self) -> str | None
```

It owns the registry, executor, transport, `MCPServer`, and thread:

- registry: `create_default_registry(include_mcp=False)` — built-in tools only, so the
  server never re-exposes tools the workbench's own MCP *client* pulled in
- executor: `QtMainThreadToolExecutor`, marshalling tool calls onto the Qt main thread
  (FreeCAD's document API is not thread-safe)
- both are constructed **once, lazily, on first successful start** and reused across
  stop/start cycles

`start()` binds **synchronously on the calling thread** and only then hands the bound
server to a daemon thread. This is the fix for defect 1: an `OSError` reaches the caller.

Order within `start()` is **bind first, build second**: the bind is cheap and is the only
step that realistically fails, so a port conflict costs nothing and leaves no
half-initialised registry behind. Registry and executor are constructed only after the
socket is secured.

`start()` on an already-running controller is a no-op returning the existing URL.

`is_running()` returns `False` if the thread has died, so a crashed server does not leave
the button stuck on.

### Modified: `freecad_ai/mcp/transport.py`

`SSEServerTransport` gains a split lifecycle. Additive — no signature changes:

- `bind()` — construct and store `self._httpd` via `_make_server()`; may raise `OSError`
- `serve()` — `self._httpd.serve_forever()`
- `stop()` — `shutdown()` then `server_close()`; safe to call when not bound
- `run(handler)` — unchanged behaviour, now literally `bind(); serve()`

Keeping `run()` intact means `mcp_server_entry.py`, `test_mcp_sse_transport.py`, and any
user script calling it keep working untouched.

### Modified: `InitGui.py`

`ToggleMCPServerCommand`, modelled on the existing `ToggleKeepDockCommand`:

- `GetResources()` returns `"Checkable": True` with **static** menu text and tooltip.
  FreeCAD caches command resources at registration, so the tooltip cannot reliably carry
  a live URL; the URL is delivered via the Report view line and a transient status-bar
  message instead. (If a dynamic tooltip turns out to work during implementation, it is a
  bonus, not a requirement.)
- `IsChecked()` delegates to `get_server_controller().is_running()` — FreeCAD polls this,
  so a CLI-started server checks the button automatically
- `Activated()` starts or stops via the controller; on `OSError` shows a modal
- registered as `FreeCADAI_ToggleMCPServer`, appended to **both** `appendToolbar` and
  `appendMenu`

The command stays a thin shim on purpose. FreeCAD evaluates `InitGui.py` inline, so
`__file__` is undefined and module-level names are invisible inside classes; the file
also cannot be imported by tests. All logic that deserves a test lives in
`gui_server.py`, and every import inside the command happens in-method.

### Modified: `freecad_ai/config.py`

```python
mcp_server_host: str = "127.0.0.1"
mcp_server_port: int = 3000
```

`AppConfig` persists via `asdict()`, so both fields round-trip with no extra work. They
are **JSON + Settings dialog only** — not added to the Edit → Preferences page, so the
`Gui::Pref*` param-store mirror is untouched.

Resolution order at start time, highest first:

1. `MCP_HOST` / `MCP_PORT` environment variables, when set
2. `AppConfig.mcp_server_host` / `mcp_server_port`
3. the dataclass defaults above

Env-first preserves every documented CLI recipe, including the wiki's Flatpak
invocation and `MCP_PORT=3123`-style overrides.

### Modified: `freecad_ai/ui/settings_dialog.py`

In the MCP section: a host line edit, a **plain numeric entry** for the port
(`QLineEdit` with a `QIntValidator(1, 65535)` — not a spinbox, and deliberately no 1024
floor, so the GUI can reach exactly the same ports as `MCP_PORT`), and a warning label:

> The MCP server has no authentication. Anything that can reach this address can run
> FreeCAD tools, including arbitrary Python. Keep it on 127.0.0.1 unless you understand
> the exposure.

The warning is always visible, not conditional on a non-loopback value — the loopback
default is already reachable by every local process, so hiding the warning there would
misrepresent it.

### Modified: `mcp_server_http.py`

Replaces its inline registry/executor/transport/thread construction with a
`get_server_controller().start(host, port)` call, then prints the returned URL. Net
effect: a CLI-started server is visible to the button, and the script gains
bind-before-announce, fixing defect 2.

## Data flow

```
User clicks toolbar button
  └─ ToggleMCPServerCommand.Activated()
       └─ get_server_controller().start(resolved_host, resolved_port)
            ├─ create_default_registry(include_mcp=False)   (first start only)
            ├─ QtMainThreadToolExecutor()                   (first start only)
            ├─ SSEServerTransport(host, port).bind()        ← raises here on conflict
            └─ Thread(target=transport.serve, daemon=True).start()
       ├─ success → Report view line + status-bar message carry the URL
       └─ OSError → modal QMessageBox

MCP client → POST /messages → MCPServer._handle
  └─ QtMainThreadToolExecutor.execute()  → queued signal → Qt main thread → tool
```

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Port already in use | `bind()` raises `OSError` on the click thread; modal names the port and suggests changing it in Settings |
| Permission denied (port < 1024, unprivileged process) | `bind()` raises `PermissionError` (an `OSError` subclass); same modal path, naming the port and that privileged ports need root |
| Invalid host string | `bind()` raises `socket.gaierror` (an `OSError` subclass); same modal path |
| Port field empty or out of range | `QIntValidator` blocks out-of-range input; an empty field falls back to the configured value |
| Server thread dies after start | `is_running()` returns `False`; button unchecks on next poll |
| `stop()` with nothing running | No-op |
| `stop()` with a client attached mid-stream | `shutdown()` returns once handlers finish; the SSE stream closes and the client sees a disconnect |
| FreeCAD exits with server running | Daemon thread dies with the process, as today |

## Testing

Unit tests, no FreeCAD required (`tests/unit/`):

**`test_mcp_gui_server.py`** (new)
- `start()` returns a URL and `is_running()` is then `True`
- `start()` twice is a no-op and returns the same URL
- `start()` on a bound port raises `OSError` and leaves `is_running()` `False`
- `stop()` releases the port; a subsequent `start()` on the same port succeeds
- `stop()` is idempotent and safe before any `start()`
- `is_running()` is `False` once the serve thread has exited
- host/port resolution: env beats config, config beats default

**`test_mcp_sse_transport.py`** (extend)
- `bind()` raises on a taken port; `run()` still behaves as before
- `stop()` after `bind()` releases the socket
- `stop()` without `bind()` does not raise

**`test_config.py`** (extend)
- the two new fields round-trip through save/load and default correctly

The `ToggleMCPServerCommand` class itself is not unit-testable (FreeCAD evaluates
`InitGui.py` inline). It is kept thin enough that its logic is covered by the controller
tests; verification of the button is manual, in a real FreeCAD.

Manual verification, on FreeCAD 1.1.1:
1. Click the button → Report view shows the URL; `claude mcp add --transport sse ...` in
   a **new** session lists the tools
2. Click again → port released, client disconnects
3. Launch via `FreeCAD.AppImage mcp_server_http.py`, then open the workbench → button
   already checked; clicking it stops the CLI-started server
4. Occupy the port first, then click → modal names the conflict, button stays unchecked

## Follow-up issues (filed, not part of this work)

1. **[#59](https://github.com/ghbalf/freecad-ai/issues/59) — MCP server has no authentication.** Add an optional bearer token: generate with
   `secrets.token_urlsafe(32)`, compare with `hmac.compare_digest()` in the existing
   `_authorized()` choke point, surface the token alongside the URL. Both stdlib.
   Claude Code supports `-H "Authorization: Bearer ..."`, and the workbench's own client
   already has a headers table, so no client work is needed.
2. **[#60](https://github.com/ghbalf/freecad-ai/issues/60) — `MCP_HOST=0.0.0.0` is a confusing dead end.** The socket binds on all interfaces,
   but the `Host`-header allowlist is `loopback ∪ {"0.0.0.0"}`, and a LAN client dialling
   `192.168.1.50:3000` sends that as its `Host` — so it gets 403 while everything looks
   healthy server-side. Either widen the allowlist when the bind address is a wildcard,
   or reject `0.0.0.0` with a clear message.

## Documentation

- **Wiki `MCP-Integration.md`**: document the toggle as a third way to start the server,
  alongside the CLI and console routes; state plainly that the server is unauthenticated
  and that host/port are configurable in Settings.
- **CHANGELOG**: entry under the next version before tagging.
