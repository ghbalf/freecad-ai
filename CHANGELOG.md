# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Connection profiles.** LLM connection settings are now named profiles.
  Define as many as you like — `ollama-local` and `ollama-remote` can
  coexist with different URLs and keys — and switch between them from the
  Settings dialog without losing anything.
- Saving a profile with an empty **Base URL** now asks first, naming the
  profiles concerned. Such a profile fails with a bare connection error at
  request time, and profile resolution deliberately does not substitute the
  provider's preset URL behind your back — so the dialog says so instead.
- **Per-utility models.** Context compaction, skill evaluation, tool
  optimisation and tool reranking each choose a profile, or inherit the
  active one. Run chat on a large cloud model and the throwaway work on a
  cheap or local one.
- A **Use this profile for chat** checkbox in the Settings dialog says
  which profile chat runs on, and the profile dropdown marks it
  `(active)`. Selecting a profile in the dropdown only opens it for
  editing — browsing what your profiles hold never re-points chat.

- **Optional bearer token for the MCP server** — a new
  **AI Settings → MCP Servers → Bearer token** field, with a **Generate**
  button, and an `MCP_AUTH_TOKEN` environment variable (env wins). When set,
  every request to the server must carry `Authorization: Bearer <token>`;
  a missing or wrong one is answered `401` with a `WWW-Authenticate: Bearer`
  challenge, so a client knows to present a credential rather than that it is
  barred outright. Empty (the default) leaves the server unauthenticated,
  exactly as before, so nothing changes for an existing setup. Until now the
  `Host`-header allowlist was the only thing limiting who could reach a
  non-loopback server, and it cannot tell one client on that host from
  another. Both start-up routes read the token — the toolbar toggle and
  `mcp_server_http.py`. The token must be ASCII: it is compared with
  `hmac.compare_digest()`, which raises on a non-ASCII operand, so a
  non-ASCII token is refused when the server starts rather than crashing the
  handler thread on every request. Contributed by @AmirF194 in
  [#73](https://github.com/ghbalf/freecad-ai/pull/73), closing
  [#59](https://github.com/ghbalf/freecad-ai/issues/59).

  Host, port, allowed hosts and the token are all read when the server
  starts, so changing any of them does not reconfigure a server that is
  already running — stop and restart it.

### Changed

- The reranker's four-field provider override is replaced by a profile.
  Existing overrides migrate automatically into a profile named `rerank`.
- The reranker's **Test reranker** button now probes whichever profile
  reranking is set to (or the active profile, if left on inherit), instead
  of its own four fields.
- **Test Connection** and **Test Reranker** now name the profile they
  probed. Both deliberately test a profile that need not be the active one
  — Test Connection tests whichever profile is open in the dialog, so you
  can verify a new one before switching chat to it, and Test Reranker
  follows the tool-reranking dropdown — and the status line previously gave
  no way to tell that apart from a failure of the profile you chat with.
  It now reads `Testing "ollama-local"...`, then
  `"ollama-local": Connected! ...` or `"ollama-local": Failed: ...`. The
  name is captured when the probe starts, so switching profiles while one
  is in flight cannot mislabel the result.

### Fixed

- Switching between profiles in the Settings dialog is lossless (#75).
  Each profile keeps its own base URL, key, model and parameters, so
  browsing to another profile and back leaves your edits intact and no
  profile can overwrite another's settings. Pointing a profile at a
  *different vendor* still loads that vendor's preset URL and model, as it
  always has — that is an explicit "point this profile elsewhere".
- Cancelling the Settings dialog now discards profile changes. Adding,
  renaming, deleting or editing a profile previously took effect
  immediately, and Test Connection could flush the change to disk before
  you ever pressed OK.
- Sampling parameters edited in Settings now take effect, including
  **Remove**. For a configuration carried over from an earlier version,
  edits were silently discarded and removed rows came back: parameters
  lived in two places at once, a per-model dict in `config.json` and the
  profile, and the dialog could only reach one of them. The profile is now
  the only source; the per-model dict is left in `config.json`, unread.
- Clearing a profile's API key now actually clears it. Upgrading copied
  the key into a second, per-vendor slot that no part of the dialog could
  edit, so a key cleared to rotate a leaked credential stayed on disk and
  kept being sent — with Test Connection reporting OK. That slot is no
  longer written on upgrade; it remains available as a hand-written
  per-vendor default in `config.json`.
- Test Connection now succeeds for a profile that leaves its API key blank
  to inherit the vendor-wide default, matching what normal chat use
  already did.

## [0.23.1-alpha] - 2026-08-31

### Fixed

- **The legacy `POST /messages` endpoint no longer answers `500` to malformed
  input** — it parsed `Content-Length` and decoded the body without guarding
  either, so a non-integer header or a body that was not valid UTF-8 escaped as
  an uncaught exception: the client got a `500` and the user got a traceback in
  the FreeCAD console. A negative `Content-Length` was worse — it made the read
  block to end-of-stream, pinning a worker thread until the socket timed out
  without answering at all. All three now return `400` with JSON-RPC `-32700`,
  matching what the `/mcp` route has done since v0.23.0-alpha. The success path
  is unchanged.
  ([#69](https://github.com/ghbalf/freecad-ai/issues/69))

- **The MCP client now sends `MCP-Protocol-Version` on every request after the
  handshake** — required of clients since the `2025-06-18` protocol revision and
  omitted since the client was written. It worked only by coincidence: a server
  seeing no header is told to assume `2025-03-26`, which is what we speak. A
  server that has dropped that revision was entitled to reject every call after
  `initialize`. The header carries the version the *server* chose during the
  handshake, not the one we asked with, so a server negotiating a newer revision
  is now answered correctly. Affects both HTTP client transports; STDIO has no
  headers and is unchanged.
  ([#64](https://github.com/ghbalf/freecad-ai/issues/64))

## [0.23.0-alpha] - 2026-08-31

### Added

- **Streamable HTTP transport for the MCP server** — the server now answers
  `POST /mcp` with the JSON-RPC reply inline, alongside the existing
  `GET /sse` + `POST /messages` pair, on the same address and port. Clients
  connect with whichever transport they speak and nothing needs reconfiguring.
  HTTP+SSE was deprecated in the `2026-07-28` protocol revision with a
  twelve-month removal window, so the URL the toolbar and
  `mcp_server_http.py` report is now `http://host:port/mcp`; existing `/sse`
  configurations keep working. No session ids are issued and `GET /mcp`
  answers `405`, which is what the newer revisions expect anyway.
  ([#65](https://github.com/ghbalf/freecad-ai/issues/65))

- **Allowed `Host` headers are configurable** — a new
  **AI Settings → MCP Servers → Allowed Host headers** field and a
  `MCP_ALLOWED_HOSTS` environment variable (comma-separated, env wins) name the
  hosts the MCP server answers to. This is what makes a non-loopback bind
  usable: clients send the address they dialled, so it has to be named here.
  Empty (the default) keeps today's behaviour exactly — loopback only, and a
  wildcard bind still refused.
  `*` is not accepted: the server has **no authentication**
  ([#59](https://github.com/ghbalf/freecad-ai/issues/59)), so this allowlist is
  the only thing limiting who can reach it.

### Fixed

- **`MCP_HOST=0.0.0.0` locked out every client it appeared to let in** — the
  server's `Host`-header allowlist was seeded from the bind address, so a
  wildcard bind added the literal string `0.0.0.0` to it. No client's `Host`
  header ever names a wildcard address, so the socket listened on every
  interface while returning 403 to every non-loopback client — with no error
  and no log line to say why. A wildcard bind is now refused at startup with a
  message naming the fix, instead of failing silently later. Reported and fixed
  by @AmirF194 in [#66](https://github.com/ghbalf/freecad-ai/pull/66),
  closing [#60](https://github.com/ghbalf/freecad-ai/issues/60).

## [0.22.0-alpha] - 2026-08-23

### Added

- **Start and stop the MCP server from the toolbar** — a checkable **MCP Server**
  command in the FreeCAD AI toolbar and menu starts the HTTP/SSE server in the
  running FreeCAD, so external clients no longer need a command-line launch or a
  pasted `exec(open(...))` snippet. Suggested by @s-light on
  [#55](https://github.com/ghbalf/freecad-ai/issues/55).
  The button reports the true state: a server started via
  `FreeCAD.AppImage mcp_server_http.py` or from the Python console shows as
  running and can be stopped from the button, because all three routes now share
  one controller.
  Host and port are configurable under **AI Settings → MCP Servers**, with
  `MCP_HOST`/`MCP_PORT` still taking precedence. Note the server has **no
  authentication** — see [#59](https://github.com/ghbalf/freecad-ai/issues/59).

### Fixed

- **A failed MCP server start was silent** — the listening socket was created
  inside the server thread, so a port conflict raised `OSError` in a daemon
  thread and vanished: no dialog, no log the user would see, FreeCAD carrying on
  as though the server were up. `mcp_server_http.py` compounded it by printing
  `MCP SSE server running on ...` *before* attempting the bind. The bind now
  happens before anything is announced, and failures reach the caller.
- **The MCP server could not be stopped** — `SSEServerTransport` never kept a
  handle on its HTTP server, so the only way to stop it was to quit FreeCAD. It
  now has a `stop()` that shuts down and releases the port.
- **MCP server reported a stale version to every client** — `SERVER_INFO` in
  `freecad_ai/mcp/server.py` hardcoded `"0.1.0"`, so `claude mcp list`, Claude
  Desktop and any other client displayed "FreeCAD AI 0.1.0" no matter which
  release was installed. It now derives from `freecad_ai.__version__`. Cosmetic,
  but actively misleading when diagnosing someone else's setup — and the value
  had been wrong for twenty releases. Found while verifying the external-client
  docs for #55; `MCPServer` had no test coverage at all, which is why nobody
  caught it.
- **"Keep Chat Panel Open" always showed a checkmark** — the menu entry's tick
  was pinned on from the moment the workbench loaded and never moved, whatever
  the setting actually was. FreeCAD 1.1.x reads a command's `Checkable`
  resource as the action's *initial* state rather than as "this action may be
  checked", and never calls a Python command's `IsChecked()`, so the tick has
  to be pushed by hand. It now is — from the command itself, from workbench
  activation, and from the Settings dialog.
  [#62](https://github.com/ghbalf/freecad-ai/issues/62)
- **A stuck MCP client could freeze FreeCAD** — SSE writes are serialized under
  a lock that `stop()` also needs, and the connection had no timeout, so a
  client that stopped reading could block the write indefinitely and hang the
  Stop button on the Qt main thread. The connection now times out, which drops
  the wedged client instead of freezing the GUI.
  [#63](https://github.com/ghbalf/freecad-ai/issues/63)

## [0.21.2-alpha] - 2026-08-15

### Fixed

- **`list_documents` raised AttributeError on every FreeCAD 1.1.x session**
  (#57, reported and fixed by @s-light in #56) — the handler read
  `doc.Modified`, but `App.Document` has no such property; the dirty flag lives
  on the *Gui* document. The tool failed for all users on 1.1.x, not just the
  Flatpak build it was reported against — confirmed locally against 1.1.1
  (AppImage). The flag now comes from `Gui.getDocument(name).Modified`, falling
  back to `False` when there is no GUI (the STDIO MCP server entry point runs
  headless) or when the document is unknown to the Gui layer.
- **Sandbox pre-check picked the wrong FreeCAD install** (#58, by @s-light) —
  `_find_freecad_cmd()` guessed from `~/bin` AppImages and `PATH`, which could
  resolve to a completely unrelated install (a Snap package on `PATH` while the
  live session runs from a Flatpak). That foreign binary loads its own
  incompatible Draft/Arch/PySide stack and segfaults. The console binary is now
  resolved from the running session's own `FreeCAD.getHomePath()` first, which
  is guaranteed to match; the existing AppImage/`PATH` chain remains as a
  fallback for builds that ship no `freecadcmd`.
- **Sandbox segfaulted on any code importing Arch/BIM** (#58, by @s-light) —
  the harness imported the real `FreeCADGui` and then patched `ActiveDocument`
  to a no-op. But the crash happens *during* the import: the real module pulls
  in PySide/Qt, and anything that later touches Arch dies in C++ where no
  Python handler can catch it — there is no display and no `QApplication` event
  loop. The harness now installs a fake `FreeCADGui` module into `sys.modules`
  instead, so the real one is never imported. Same view-cosmetics
  neutralisation as before (#14), without the crash. Note that the fake module
  defines only `ActiveDocument`, `SendMsgToActiveView` and `updateGui`; the
  no-op absorption applies *below* `Gui.ActiveDocument`, not to the module
  itself. Any other `Gui` attribute — notably `Gui.Selection` and
  `Gui.getDocument`, which the real module provides — now raises
  `AttributeError` in the pre-check, so code that reads the selection fails the
  pre-check while running fine live.

## [0.21.1-alpha] - 2026-08-05

### Fixed

- **Plan-mode Execute button missing on long plans** (#50, reported by
  @MusaAkyuz) — a plan that hit the `max_tokens` output limit was cut off
  mid-code-block, so the closing ``` fence never arrived. Both fence regexes
  require it, so the script rendered as unformatted prose and no Execute/Copy
  buttons were emitted. The button was never generated — nothing was scrolled
  off-screen. A truncated block now renders as a proper code block and gets a
  **Copy** button; **Execute is withheld**, since running a script cut off
  mid-expression raises a `SyntaxError` or leaves half-built geometry.
- **Truncated responses are now flagged** (#50) — `finish_reason="length"`
  (OpenAI-style) and `stop_reason="max_tokens"` (Anthropic) were both discarded,
  so a plan simply stopped mid-line with no explanation. The chat now shows a
  warning naming the current Max Output Tokens value and pointing at Settings.
- **`` ```py `` and untagged code blocks now get an Execute button** (#50) — the
  executor matched only `` ```python `` while the renderer styled any fence, so
  models that tag fences differently produced a code block with no way to run it.
  Non-Python fences (`` ```bash ``, `` ```json ``) remain non-executable.
- **Act mode no longer acts on a truncated turn** (#52) — the tool-carrying
  request paths discarded the same truncation signal #50 fixed for Plan mode,
  collapsing `finish_reason="length"` into a normal finish. The agentic loop
  could not tell a cut-off turn from a completed one, so it executed tool calls
  parsed from a half-formed payload and let the model build on its own
  mid-sentence output. The loop now **halts** on truncation without running that
  turn's tool calls, and shows the truncation warning. A truncated turn still
  counts against the max tool-call iteration budget — it consumed a real
  request, and refunding it would let repeated truncations run past the limit.
- **Streaming tool calls are no longer silently dropped on truncation** (#52) —
  when an OpenAI-style stream ended with `finish_reason="length"`, the handler
  matched only `"tool_calls"`/`"stop"`, so it never emitted the pending tool
  calls *or* a `done` event. Those calls are now deliberately discarded (their
  arguments JSON stops mid-object) and the turn ends cleanly.

## [0.21.0-alpha] - 2026-07-27

A fix-and-cleanup release for the pre-execution recovery snapshots that
`execute_code` writes before running generated code. Prompted by a contributor
bug report (@FairlyInconspicuous) and a design proposal (@3dyuval).
([#44](https://github.com/ghbalf/freecad-ai/pull/44), [#46](https://github.com/ghbalf/freecad-ai/issues/46), [#48](https://github.com/ghbalf/freecad-ai/pull/48))

### Changed

- **Recovery snapshots now live in a managed backups dir** (#46) — before each
  `execute_code`, `_auto_save` writes the pre-execution snapshot to
  `CONFIG_DIR/backups/<name>.<hash>.ai-backup.FCStd` instead of dropping an
  `.ai-backup.FCStd` file beside the user's document. The project folder stays
  clean; each source path maps to one stable, hash-tagged file that is
  overwritten in place (collision-safe across same-named documents in different
  folders). Builds on the #44 filename-accretion fix.

### Added

- **`max_backups` config knob** (#46, JSON-only) — count cap for the recovery
  snapshots in `CONFIG_DIR/backups`, pruned via the shared `prune_oldest_files`
  helper (also honours the existing `max_retention_age_days`). Defaults to `0`
  (keep all) to preserve prior behavior on upgrade; growth is naturally bounded
  since each document reuses one file. Recommended value if you want a hard cap:
  `50`.

### Fixed

- **Auto-save no longer compounds `.FCStd` onto the document filename** (#44,
  thanks @FairlyInconspicuous) — `_auto_save` rebuilt the document path by
  string-editing the name FreeCAD's `saveAs` had already mutated, leaving a
  trailing `.FCStd` and growing the filename by one extension on every
  `execute_code` call (and littering the folder with never-reused snapshots).
  It now captures and restores the original path verbatim. Ships with unit-lane
  regression coverage.

## [0.20.0-alpha] - 2026-07-22

A feature release: the workbench can now connect *to* MCP servers by URL — the
client-side counterpart to the v0.17.0-alpha HTTP/SSE server transport. Prompted
by a forum question from hardeeprai. ([#41](https://github.com/ghbalf/freecad-ai/issues/41), [#42](https://github.com/ghbalf/freecad-ai/pull/42))

### Added

- **Connect to MCP servers by URL** (#41) — new HTTP/SSE **client** transports
  alongside STDIO: a legacy HTTP+SSE client and a Streamable HTTP client,
  selectable per server in Add MCP Server. Supports remote `https://`, custom
  auth headers, and optional custom CA bundle / client certificate (mTLS).
  Plain `http://` is allowed only to localhost. Still zero external
  dependencies. This is the client counterpart to the v0.17.0-alpha HTTP/SSE
  server transport.

## [0.19.0-alpha] - 2026-07-22

A feature-and-fix release bundling a new skills capability with two fixes from
issues filed by @3dyuval.

### Added

- **Skill `references/` — on-demand reference files (tier-3 progressive disclosure)** (`freecad_ai/extensions/skills.py`, `freecad_ai/tools/freecad_tools.py`). A skill can now ship a `references/` subdirectory of markdown files that the model loads only when a task needs them, keeping `SKILL.md` lean. The loader scans `references/` into a per-skill `{key → path}` allowlist; `use_skill` gains an optional `resource` argument (`use_skill(name, resource="freecad-gotchas")`) that returns a reference file's contents; and a skill with references gets an auto-generated "Available references" manifest appended to its `SKILL.md` result, advertising each key and the exact call to load it. The model passes a **key**, never a path — it is looked up in the pre-scanned allowlist, so directory traversal is impossible by construction. Fully additive: skills without a `references/` directory are unchanged. `scripts/`/`assets/` remain out of scope (an execute surface is a separate proposal). ([#37](https://github.com/ghbalf/freecad-ai/issues/37); thanks @3dyuval)

### Fixed

- **Custom OpenAI-compatible providers no longer silently drop the tools array** (`freecad_ai/llm/providers.py`). The `custom` provider preset defaulted to `supports_tools: False`, and since the per-model tool-capability probe is Ollama-only, that static flag was the sole source of truth for custom endpoints — so tool calling was silently disabled on every request. Most custom servers (vLLM, llama.cpp `--jinja`, SGLang) support tool calling, so the default is now `True`; a genuinely tool-less endpoint opts out with `tools_detected: false` in `config.json`. ([#38](https://github.com/ghbalf/freecad-ai/issues/38); thanks @3dyuval)

### Changed

- **`execute_code` now tells the model its state does not persist between calls** (`freecad_ai/tools/freecad_tools.py`). Each `execute_code` call runs in a fresh namespace, but nothing signalled that, so models chained variables and imports across calls, hit a `NameError`, and looped to the tool-turn limit. The tool description now states that each call is self-contained (re-fetch objects every call; a `NameError` means a reference to an earlier call's name, not a wrong query). ([#39](https://github.com/ghbalf/freecad-ai/issues/39); thanks @3dyuval)

## [0.18.0-alpha] - 2026-07-04

A small feature-and-fix release bundling two community contributions from
@3dyuval with a tool-routing steering improvement.

### Added

- **Keep the chat panel open across workbench switches** (`InitGui.py`, `freecad_ai/config.py`, `freecad_ai/ui/settings_dialog.py`). A new opt-in `keep_dock_on_workbench_switch` setting (off by default) stops the FreeCAD AI dock from hiding when you leave the workbench, so the panel stays usable everywhere. Exposed both as a Settings checkbox and a keybindable "Keep Chat Panel Open" menu command. ([#34](https://github.com/ghbalf/freecad-ai/issues/34), [#35](https://github.com/ghbalf/freecad-ai/pull/35); thanks @3dyuval)

### Fixed

- **Custom OpenAI-compatible endpoints no longer 403 on a missing User-Agent** (`freecad_ai/llm/client.py`). `urllib` defaults to a `Python-urllib/x.y` User-Agent that some WAFs and reverse proxies in front of self-hosted gateways reject; requests now send an explicit `User-Agent: FreeCAD-AI`. Applied to both the OpenAI and Anthropic header builders. ([#33](https://github.com/ghbalf/freecad-ai/pull/33); thanks @3dyuval)

### Changed

- **Improved Act-mode steering toward `create_sketch` for face attachment** (`freecad_ai/core/system_prompt.py`, `freecad_ai/tools/freecad_tools.py`). When asked to "sketch on the selected face", the model was hand-rolling a raw `AttachmentSupport`/`MapMode` macro via `execute_code` instead of calling `create_sketch(support=…, face=…)` — which already handles attachment, planar-face validation, and offset. The Act-mode strategy now calls out the face-sketch route explicitly, `create_sketch`'s face capability is lifted to the front of its description, and `execute_code`/`run_macro` are framed as last resorts. Steering-only (prompt + tool descriptions); guarded by text-assertion regression tests. ([#28](https://github.com/ghbalf/freecad-ai/issues/28))

## [0.17.0-alpha] - 2026-06-22

A feature release adding the first non-STDIO MCP transport: the bundled MCP server can now be reached over HTTP with Server-Sent Events, alongside the existing newline-delimited STDIO transport. Contributed by @Shuenhoy ([#29](https://github.com/ghbalf/freecad-ai/pull/29)).

### Added

- **HTTP/SSE MCP server transport** (`freecad_ai/mcp/transport.py`, `freecad_ai/mcp/server.py`, `mcp_server_http.py`). `SSEServerTransport` serves the same `ToolRegistry` over HTTP: clients open an SSE stream on `GET /sse` and post JSON-RPC requests to `POST /messages`, while STDIO remains the default. Still zero external dependencies — built on the standard library's `http.server`/`socketserver`. A new `mcp_server_http.py` entry point launches it from a FreeCAD AppImage. ([#29](https://github.com/ghbalf/freecad-ai/pull/29); thanks @Shuenhoy)

### Security

- **Cross-origin tool invocation is blocked on the HTTP/SSE server** (`freecad_ai/mcp/transport.py`). `POST /messages` executes arbitrary tools (including `run_macro`), and the initial implementation replied with `Access-Control-Allow-Origin: *` and a permissive preflight. Even bound to loopback, any web page the user had open could drive FreeCAD via a cross-origin `fetch()` — a drive-by RCE / DNS-rebinding vector. Every request is now gated: the `Host` header must be loopback and any cross-origin `Origin` is rejected with 403; the wildcard CORS headers are gone. Native MCP clients send no `Origin` and are unaffected; the `allowed_hosts`/`allowed_origins` constructor params can deliberately widen this for LAN exposure.

### Fixed

- **SSE socket writes are serialized** (`freecad_ai/mcp/transport.py`). Under `ThreadingMixIn`, the keepalive loop (GET `/sse` thread) and a tool response (POST `/messages` thread) could write to the same socket concurrently and interleave bytes, corrupting the event stream. All writes now go through a single lock-held `write`+`flush`.
- **`__file__` guarded in the HTTP entry point** (`mcp_server_http.py`). The module referenced `__file__` at module scope, which raises `NameError` under the documented `exec(open(...).read())` launch mode; it now falls back via `globals().get("__file__")`.

### Tests

- Unit: `tests/unit/test_mcp_sse_transport.py` (13 tests) — covers the SSE/`/messages` round trip, write serialization under concurrency, the `__file__` exec-mode guard, and a live-server check that a cross-origin `POST` is rejected with 403 and carries no `Access-Control-Allow-Origin` header.

## [0.16.5-alpha] - 2026-06-17

A bug-fix for the temperature-persistence half of [issue #30](https://github.com/ghbalf/freecad-ai/issues/30) (@AVAVAVA1): a per-model sampling parameter set in Settings reverted to its default after Save.

### Fixed

- **Reranker params could overwrite the main model's params on save** (`freecad_ai/config.py`, `freecad_ai/ui/settings_dialog.py`, `freecad_ai/ui/chat_widget.py`). The reranker stored its sampling parameters in the *shared* `model_params` dict, keyed by model name. When the reranker inherited the main model (override field empty — including whenever reranking is off), its "effective model" *was* the main model, so the Save handler wrote the reranker's table — a stale snapshot taken when the dialog opened, before any edit — into the main model's slot, clobbering the value the user had just changed (e.g. `temperature` reverting to `0.3`). The reranker now keeps its parameters in a dedicated `rerank_params` field that can never collide with `model_params`: in override mode the reranker reads/writes its own slot; in inherit mode it reads the main model's params and persists nothing of its own. Existing configs are migrated on load (the legacy reranker-override slot seeds `rerank_params` once). ([issue #30](https://github.com/ghbalf/freecad-ai/issues/30); thanks @AVAVAVA1) ([#32](https://github.com/ghbalf/freecad-ai/pull/32))

### Tests

- Unit: `tests/unit/test_reranker_namespace.py` — the runtime read path (`_build_rerank_llm_client`) sources params from `rerank_params` when overriding and from the main model when inheriting, and the persistence rule (`SettingsDialog._resolve_rerank_params`) writes the table only in override mode. `tests/unit/test_config.py` gains the `rerank_params` default/round-trip and the migration-seed cases (override seeds, inherit no-ops, idempotent when already present).

## [0.16.4-alpha] - 2026-06-16

A bug-fix patch for the vision half of [issue #30](https://github.com/ghbalf/freecad-ai/issues/30) (@AVAVAVA1): a model without vision support errored out on every follow-up turn once an image was anywhere in the conversation history.

### Fixed

- **Images in history were sent to non-vision models** (`freecad_ai/core/conversation.py`, `freecad_ai/ui/chat_widget.py`). Once an image entered the conversation — a manual attachment, or a viewport snapshot the assistant attaches automatically — it stayed in the history and was re-sent on every later turn. With a text-only model selected (and no `describe_image` vision-fallback tool configured), the provider rejected the image block and the conversation stayed broken until it was cleared. `get_messages_for_api()` gains a `strip_images` option that replaces history image blocks with a `[Image omitted — the current model has no vision support]` placeholder; the chat send path, the auto-retry-on-error path (which attaches a viewport snapshot before resending), and the headless skill evaluator all apply it when the active model lacks vision and no describe-image fallback is available. When that fallback *is* configured, the existing describe-and-substitute behavior is unchanged. ([issue #30](https://github.com/ghbalf/freecad-ai/issues/30); thanks @AVAVAVA1)

### Tests

- Unit: `tests/unit/test_conversation.py::TestStripImagesForNonVisionModels` — image blocks are stripped to a placeholder for both OpenAI and Anthropic formats, surrounding text is preserved, images are kept when stripping isn't requested, a `describe_fn` still takes precedence, and a system message carrying a viewport snapshot (the retry path's shape) is covered.

## [0.16.3-alpha] - 2026-06-07

A bug-fix patch for two headless-sandbox false positives: the pre-check rejected valid code that runs fine in the real FreeCAD GUI, blocking common workflows. Reported on [issue #18](https://github.com/ghbalf/freecad-ai/issues/18) (@0xrushi) and [issue #14](https://github.com/ghbalf/freecad-ai/issues/14) (@JohnMcLear).

### Fixed

- **Empty sketches were flagged as broken** (`freecad_ai/core/executor.py`). "Create a sketch on the selected face" produces an empty sketch (geometry is drawn later in the editor). On FreeCAD 1.1 an empty `Sketcher::SketchObject` reports `Shape.isNull() == True` while its `State` stays `Up-to-date` — a valid intermediate state, not a defect — but the post-execution validator reported `has null shape` and failed every retry, after which the model injected a placeholder circle to satisfy the check (the stray sketch users saw instead of one on the selected face). The validator now skips the null-shape report for object types whose empty shape is valid (`Sketcher::SketchObject`, `PartDesign::Body`); the separate Invalid-state check still catches a sketch whose attachment genuinely fails. ([issue #18](https://github.com/ghbalf/freecad-ai/issues/18); thanks @0xrushi)
- **Headless view-framing calls failed the pre-check** (`freecad_ai/core/executor.py`). LLM-generated code routinely ends with view cosmetics — `Gui.ActiveDocument.ActiveView.viewIsometric()`, `fitAll()`, `SendMsgToActiveView("ViewFit")`. Headlessly `FreeCADGui` has no `ActiveDocument`, so these raised `AttributeError: module 'FreeCADGui' has no attribute 'ActiveDocument'` and the sandbox rejected otherwise-valid geometry — e.g. "generate a cube", which surfaced once the v0.16.2-alpha hang fix removed the timeout that had been masking it. The sandbox harness now stubs the whole `Gui.ActiveDocument.*` surface with a recursive no-op, so view chains are harmless while the geometry is still validated. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14); thanks @JohnMcLear)

### Tests

- Integration: `tests/integration/test_sandbox_validation.py::TestEmptySketchNullShapeNotReported` (empty sketch on a real face, and empty body, pass) and `::TestHeadlessGuiCallsAreStubbed` (verbatim "generate a cube" with view framing passes). Unit: `tests/unit/test_executor.py::TestCollectObjectIssues` gains empty-sketch/empty-body exemption and the matching safety-net cases. Existing bad-attachment and newly-invalid negative controls remain green.

## [0.16.2-alpha] - 2026-06-06

A bug-fix patch for [issue #14](https://github.com/ghbalf/freecad-ai/issues/14): the headless sandbox pre-check could time out on *any* operation — even a trivial one — whenever a saved document was open, making Act mode unusable on affected setups. This supersedes the v0.16.1-alpha timeout change, which addressed the wrong cause.

### Fixed

- **Sandbox dry-run hung against an open document** (`freecad_ai/core/executor.py`). The harness script wrote its result file but never forced the interpreter to exit. On FreeCAD builds where running a script via `-c` against an opened document leaves the process in interactive mode (the Qt/console event loop never returns), the subprocess never terminated, so the pre-check blocked until its timeout and reported a spurious "code timed out after N seconds" — regardless of what the code did (even, e.g., "render a cube"). The harness now calls `os._exit(0)` after writing its result, so the subprocess always terminates promptly. Diagnosed and first patched by @galberding; also reported by @JohnMcLear and @trougnouf. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14))

### Changed

- **`execution_timeout` default reverted to 30s** (from the 60s introduced in v0.16.1-alpha). With the hang fixed, the higher default only lengthened the wait before a genuinely-stuck operation was cancelled; the setting remains user-tunable (range 5--600s) for heavy operations on large models.

### Tests

- Unit: `tests/unit/test_executor.py::TestSandboxHarnessForcesExit` (the generated harness must force a process exit). Integration: `tests/integration/test_sandbox_validation.py::TestSandboxExitsAgainstOpenedDocument` (trivial code on an opened document returns instead of timing out).

## [0.16.1-alpha] - 2026-06-06

A bug-fix patch for [issue #14](https://github.com/ghbalf/freecad-ai/issues/14): generated code could time out on large or detailed models regardless of the operation, with no way to extend the limit.

### Fixed

- **Code-execution timeout is now configurable, and the default was raised 30 → 60s** (`freecad_ai/core/executor.py`, `freecad_ai/config.py`, `freecad_ai/ui/settings_dialog.py`). The budget applied to **both** the headless sandbox pre-check and live execution was hardcoded at 30s. Heavy-but-valid geometry operations — most notably scaling a detailed or imported model via `Shape.transformGeometry`, whose cost grows with the model's face count — genuinely exceed 30s and were killed on both paths with no recourse. The previous patch ([0.15.1-alpha](https://github.com/ghbalf/freecad-ai/releases/tag/v0.15.1-alpha)) only moved the wall from 15s to 30s. A new **"Code execution timeout"** setting (Settings, range 5–600s) now controls this budget; `execute_code` reads it when no explicit timeout is given, so large-model users can raise it as needed. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14), reported by trougnouf; still-broken reports by JohnMcLear and galberding) ([#24](https://github.com/ghbalf/freecad-ai/pull/24))

### Tests

- Unit: `tests/unit/test_config.py` (`execution_timeout` default and round-trip) and `tests/unit/test_executor.py::TestConfigurableExecutionTimeout` (`execute_code` honors the configured value and the new 60s default when no explicit timeout is passed).

## [0.16.0-alpha] - 2026-06-03

A feature release adding a datum-geometry and transform/duplicate toolset — sketching on faces and named planes, parametric datum planes and lines, relative transforms, and independent parametric copies — together with a sandbox fix that unblocks editing imported (mesh→solid) parts. This work grew out of [issue #18](https://github.com/ghbalf/freecad-ai/issues/18) (reported by 0xrushi): a snap-fit workflow on an imported solid that fell out of the parametric toolchain.

### Added

- **Sketch on a planar face or a named plane** (`freecad_ai/tools/freecad_tools.py:_handle_create_sketch`). `create_sketch` gained `support`/`face` parameters and a GUI-selection fallback: it can now attach to a planar face of an existing object — including a standalone imported mesh→solid, where it creates a standalone sketch — or to a datum/origin plane by name, not just the XY/XZ/YZ origin planes. Planar-face validation; offset along the face normal. ([#20](https://github.com/ghbalf/freecad-ai/pull/20))
- **`create_datum_plane` tool** — a parametric datum plane offset (parallel) from an origin plane, a planar face, an existing plane, or the current selection. Creates a `PartDesign::Plane` (inside a Body, or standalone), referenceable as `create_sketch(support=...)`. ([#21](https://github.com/ghbalf/freecad-ai/pull/21))
- **`create_datum_line` tool** — a datum line (axis) defined by two points, a straight edge of an object, or an origin axis (X/Y/Z). Usable as a `revolve_sketch` rotation axis or a mirror reference. ([#22](https://github.com/ghbalf/freecad-ai/pull/22))
- **`duplicate_object` tool** — an independent, history-preserving copy of an object's whole feature tree (e.g. a Body with its sketches and pads), leaving the original unchanged, with an optional relative translate/rotate to offset the copy in one call. ([#23](https://github.com/ghbalf/freecad-ai/pull/23))

### Changed

- **`transform_object` is now relative by default** (`freecad_ai/tools/freecad_tools.py:_handle_transform_object`). Translation adds to the object's current position and rotation spins it in place about its own origin; `0` means no change. Previously the tool overwrote the placement absolutely, which silently reset an object to the world origin when asked only to rotate it. Pass `relative=False` to restore the absolute-overwrite behavior. ([#23](https://github.com/ghbalf/freecad-ai/pull/23))

### Fixed

- **Sandbox validator blamed candidate code for pre-existing invalid shapes** (`freecad_ai/core/executor.py`). The headless dry-run walked every object in the document and reported any with an invalid/null shape, with no baseline — so a document already containing an OCC-invalid object (very common with imported mesh→solid conversions) made every subsequent edit fail the post-execution check, naming objects the code never touched. The validator now snapshots each object's problem state before running the candidate code and reports only objects that are **new** or **newly broken**. ([#18](https://github.com/ghbalf/freecad-ai/issues/18)/[#19](https://github.com/ghbalf/freecad-ai/pull/19), reported by 0xrushi)

### Tests

- Unit: `tests/unit/test_sketch_attachment.py`, `test_datum_plane.py`, `test_datum_line.py`, `test_transform_duplicate.py`, and `test_executor.py::TestCollectObjectIssues`.
- Integration (real FreeCAD): `tests/integration/test_sketch_attachment_integration.py`, `test_datum_plane_integration.py`, `test_datum_line_integration.py`, `test_transform_duplicate_integration.py`, and `test_sandbox_validation.py::TestSandboxIgnoresPreexistingInvalidity` — covering face/plane/line attachment, the relative-transform footgun fix, full-tree duplication, and the end-to-end "datum line + duplicate → parallel line" workflow.

## [0.15.2-alpha] - 2026-05-30

A bug-fix patch for [issue #17](https://github.com/ghbalf/freecad-ai/issues/17): cutting or fusing into an existing model could collapse its parametric feature tree, leaving the original sketches and features buried and uneditable.

### Fixed

- **Boolean operations collapsed the parametric feature tree** (`freecad_ai/tools/freecad_tools.py:_handle_boolean_operation`). Asking the AI to cut or fuse into an existing model could leave the original sketches and features buried and uneditable: a `Part::Cut`/`Fuse`/`Common` claims its operands as tree children, so the base Body was reparented underneath the boolean node and stopped being a top-level, editable object. `boolean_operation` now detects when **both** operands are PartDesign Bodies and routes through a parametric `PartDesign::Boolean` appended inside the base Body — the Body stays top-level with its full feature history intact and the result is identical geometry. Operations involving a plain Part shape still use a `Part::` boolean. ([issue #17](https://github.com/ghbalf/freecad-ai/issues/17), reported by 0xrushi)

### Changed

- **Tool-selection guidance now favors history-preserving edits** (`freecad_ai/core/system_prompt.py`). The Act-mode prompt steers the AI to modify an existing solid by appending a feature inside its Body (`create_primitive(operation="subtractive", body_name=...)`, `pocket_sketch`, etc.) rather than reaching for a Part-workbench boolean, and the `boolean_operation` tool description spells out that it is for two separate objects and auto-uses `PartDesign::Boolean` between Bodies.

### Tests

- `tests/integration/test_boolean_transform.py::TestBooleanHistoryPreservation` — cutting and fusing two PartDesign Bodies keeps the base Body top-level, produces a `PartDesign::Boolean` (never a `Part::Cut`), and yields a valid shape.

## [0.15.1-alpha] - 2026-05-29

A bug-fix patch driven by three GitHub issues: spurious sandbox timeouts on valid-but-slow code ([#14](https://github.com/ghbalf/freecad-ai/issues/14)), a stale default Anthropic model ([#15](https://github.com/ghbalf/freecad-ai/issues/15)), and a dark UI rendered with unreadable light-on-dark text when set via a StyleSheet alone ([#16](https://github.com/ghbalf/freecad-ai/issues/16)).

### Fixed

- **Spurious "Sandbox: code timed out after 15 seconds"** (`freecad_ai/core/executor.py`). The headless sandbox pre-check was hard-capped at `min(timeout, 15)`s while the live execution armed a SIGALRM for the full `timeout` (default 30s). Valid-but-slow operations — e.g. scaling a complex shape with `Shape.transformGeometry` — failed the dry-run at 15s and never ran. The sandbox now gets the same time budget as execution, matching `validate_code()`. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14), reported by trougnouf)
- **Dark UI rendered unreadable when set via StyleSheet alone** (`freecad_ai/ui/message_view.py:_read_freecad_mode_name`). A user running a dark `.qss` (e.g. `OpenDark.qss`) without selecting a PreferencePack left the `Theme` preference empty, so detection fell through to the unreliable QPalette probe and painted light-on-dark text. The detector now consults the `StyleSheet` preference as a secondary signal after `Theme`. ([issue #16](https://github.com/ghbalf/freecad-ai/issues/16), reported by JohnMcLear)

### Changed

- **Default Anthropic model bumped to `claude-sonnet-4-6`** (was the stale dated alias `claude-sonnet-4-20250514`), across the `anthropic` and `openrouter` provider presets and the `ProviderConfig` default. ([issue #15](https://github.com/ghbalf/freecad-ai/issues/15), reported by JohnMcLear)

### Docs

- **Installation path updated for FreeCAD 1.1+** version-scoped user dirs (`~/.local/share/FreeCAD/v1.1/Mod/`), with a troubleshooting note for the "installed but doesn't appear" case. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14), reported by trougnouf)

### Tests

- `tests/unit/test_executor.py::TestSandboxTimeout` — parametrized check that the sandbox dry-run receives the full configured timeout, not a value capped at 15s.
- `tests/unit/test_theme_detection.py::TestStyleSheetFallback` — StyleSheet consulted when Theme is empty, Theme takes precedence over StyleSheet, both-empty falls back to `Custom/Unknown`.

## [0.15.0-alpha] - 2026-05-23

Adds a `run_macro` tool, a configurable agentic loop count with a Stop button, an opt-in **Dangerous mode** that trades the workbench's code-safety checks for power-user reach, and shell-style Up/Down history in the chat input. The first three are driven by [issue #13](https://github.com/ghbalf/freecad-ai/issues/13).

### Added

- **`run_macro` tool** — runs an existing FreeCAD macro file and feeds its console output back to the AI. In normal mode it accepts a bare macro name resolved from FreeCAD's macro directory; file paths require Dangerous mode. ([issue #13](https://github.com/ghbalf/freecad-ai/issues/13))
- **Configurable agentic loop count** (Settings → "Max tool-loop turns"). `0` means endless; previously hardcoded at 30. Default remains 30.
- **Stop button** — the Send button becomes "Stop" while the AI is working and interrupts the loop (the only brake when the loop is set to endless).
- **Dangerous mode** — a session-scoped toggle that disables the code safety checks (static pattern blocking, headless sandbox pre-check, execution timeout) and lets `run_macro` run files from any path. Off at every launch; a red banner shows whenever it is active. **Use at your own risk** — see README.
- **Input history in the chat input** — Up/Down navigates through the current conversation's prior user messages, shell-style: first Up saves whatever you'd typed as a draft, walks newest→oldest with no wrap; Down walks back, returning the saved draft past the newest entry. Caret-position gated so multi-line editing still works. Scope follows the conversation — switching conversations gives you that conversation's history with no new storage on disk.

## [0.14.3-alpha] - 2026-05-15

A small but data-preserving patch driven by [issue #12](https://github.com/ghbalf/freecad-ai/issues/12): users on the **Custom** LLM provider lost their gateway URL and model on every FreeCAD restart, with the provider selector reverting to "anthropic" while the custom-provider field values stayed visible.

### Fixed

- **Custom-provider persistence across restart** (`freecad_ai/config.py:_write_to_param_store`). The FreeCAD `ParamGet` mirror only encodes the 11 providers that appear in the Edit → Preferences dropdown — `custom`, `github`, `huggingface`, and `zhipu` are not in that list. Previously, saving a non-prefs provider left the previous `ProviderIndex` (typically `0` = anthropic) in the param store, which then shadowed the JSON's correct `provider.name` on the next `load_config()`. The fix clears the stale index via `group.RemInt("ProviderIndex")` when the current provider isn't representable in the combo, so the load path falls back to the JSON value. Round-trip is now symmetric.
- **Settings dialog wiped custom-provider fields on switch** (`freecad_ai/ui/settings_dialog.py:_on_provider_changed`). The "custom" preset ships empty `base_url` and `default_model`, and the dialog was applying them unconditionally — switching the combo to "custom" instantly cleared any gateway URL or model name the user had typed. Now `setText` is skipped when the preset value is empty, so switching to "custom" preserves whatever's in the form. Real providers always have non-empty preset values, so their behavior is unchanged.

### Tests

- 3 new tests in `tests/unit/test_config.py::TestParamStoreBridge` covering (a) save-then-reload of a non-prefs provider with a stale `ProviderIndex` in the param store, (b) the same guarantee parametrized across every provider in `PROVIDERS` but absent from `_PARAM_PROVIDERS`, and (c) a drift guard that fails if `_PARAM_PROVIDERS` ever names a provider not registered in `providers.py`. The `FakeGroup` fixture gained `RemInt`/`RemString`/`RemBool` to mirror real `ParamGet` semantics.
- 3 new tests in `tests/unit/test_settings_dialog_provider_change.py` exercising `_on_provider_changed` via the unbound-method-with-fake-self pattern: switching to "custom" preserves fields, switching to a real provider applies preset values, out-of-range index is a no-op.

## [0.14.2-alpha] - 2026-05-09

A small UI patch: the chat panel painted dark even with FreeCAD set to a light theme.

### Fixed

- **Theme detector at `freecad_ai/ui/message_view.py:_is_dark_mode`** read `QTreeView.palette().color(Base)` before consulting FreeCAD's `Theme` preference. On Linux when the host Qt theme is dark, the palette reports dark Base even though FreeCAD's QSS stylesheet renders the workbench as light — so the chat input, MCP status banner, and message view painted dark while the rest of the FreeCAD window painted light. Reordered to name-first: trust the user's selected `Theme` ("light"/"classic"/"default" → light, "dark" → dark) and only fall back to palette introspection when the name is empty/custom. Adds 13 parametrized regression tests in `tests/unit/test_theme_detection.py`.

## [0.14.1-alpha] - 2026-05-09

A targeted patch driven by [issue #10](https://github.com/ghbalf/freecad-ai/issues/10): users picking the GitHub provider hit a confusing 400 in Act mode because (a) GitHub Models' per-request input cap is much smaller than the model's native context, and (b) two built-in tool schemas were rejected by GitHub's strict JSON Schema validation. Both are now fixed, and the github provider preset ships a sensible reranker default so first-time users don't have to debug their way to it.

### Added

- **GitHub provider preset now ships a recommended reranker default** (`{"method": "keyword", "top_n": 8}`). GitHub Models enforces a small per-request input cap independent of the underlying model's native context window — with ~50 built-in tool schemas attached, a fresh Act-mode turn overshoots it before the user types anything. The Settings dialog applies this on provider switch only when the reranker UI is still at its factory default (off + top_n=15), so an explicit user choice — even "off" — is never silently overwritten. Done via a new `default_rerank` field on the provider preset that mirrors the existing `default_params` pattern; other providers don't ship a recommendation today (anthropic and openai-direct have generous per-request budgets, and the right `top_n` is workload-dependent elsewhere).

### Fixed

- **`create_assembly` and `add_part_to_assembly` tool schemas** declared `array`-typed parameters (`part_names`, `position`) without an `items` keyword. Anthropic and Ollama silently accept this; OpenAI's marketplace API (GitHub Models) enforces the JSON Schema spec and rejects the request with `invalid_function_parameters`. Surfaced by issue #10 once the keyword reranker reduced the prompt enough to clear the input-size cap. Added a regression test in `tests/unit/test_registry.py` that walks every built-in tool and asserts no array property is missing `items`, so future tool additions can't reintroduce the same class of bug.

## [0.14.0-alpha] - 2026-05-06

Authoring hooks and user tools is now a first-class flow inside Settings: **New…** writes a starter template and opens it for editing, **Edit…** opens the selected file. A new "Editor" preference routes file edits through either FreeCAD's docked Python editor (default) or the user's OS-default editor — keeping with the workbench's principle of not constraining users to its choice of tools.

### Added

- **New… button on Hooks and User Tools panels** in `freecad_ai/ui/settings_dialog.py`. Prompts for a name (kebab-case dir for hooks, snake_case identifier for user tools), writes a starter template, opens the file in the configured editor. Templates live in `freecad_ai/extensions/file_templates.py`:
  - **Hook template** is registry-sourced from `freecad_ai/hooks/registry.VALID_EVENTS` — one `on_<event>(context)` stub per valid event, so adding a new event in the registry automatically extends the template with no manual sync.
  - **User-tool template** ships a typed example function with a docstring — passes `validate_file()` clean (no warnings) the moment the user saves it.
- **Edit… button on both panels** — opens the selected hook's `hook.py` or the selected user-tool file in the configured editor.
- **Editor preference** — `AppConfig.use_external_editor: bool = False`. New "Editor" group in Settings with a single checkbox: *"Open hooks and user tools in the OS-default editor (instead of FreeCAD's docked script editor)"*. Defaults off (FreeCAD editor); opt in for vim/VS Code/etc. workflows. Read live from the checkbox at routing time, so toggling and clicking New/Edit applies immediately even if the dialog is later cancelled (no save required for the toggle to take effect for the current action).

### Behavior

- **FreeCAD editor path** (default): clicking New/Edit prompts **Save / Discard / Cancel** because `Gui::PythonEditor` is an MDI sub-window of `MainWindow` and is unreachable while a modal dialog is up. Save and Discard both close the Settings dialog before opening the file; Cancel keeps the dialog open and aborts the action (no debris — for New, the file isn't written until after the prompt is confirmed).
- **External editor path**: opens via `QtGui.QDesktopServices.openUrl()` — no prompt, Settings dialog stays open, the list refreshes to show the new entry.

### Tests

- 10 new tests in `tests/unit/test_file_templates.py`: hook template parses, contains exactly one handler per `VALID_EVENTS` entry (in both directions — no missing, no extras), includes the hook name in its docstring; user-tool template parses, passes `validate_file()` with zero warnings, the function name matches the input. Plus `AppConfig.use_external_editor` default-is-False, JSON save/load roundtrip, and a backwards-compat assertion that older configs without the field load with the default.

## [0.13.1-alpha] - 2026-05-01

Patch release. Fixes a v0.13.0-alpha follow-up: session logs were still writing to the legacy hardcoded path after the migration. Adds bounded retention so `<FreeCADAI dir>/conversations/` and `<FreeCADAI dir>/logs/` no longer grow without limit.

### Fixed

- **Session logs now follow `CONFIG_DIR`** — `_save_session_log` and `_auto_save_log` in `freecad_ai/ui/chat_widget.py` had a hardcoded `~/.config/FreeCAD/FreeCADAI/logs` path that escaped the v0.13.0-alpha config-dir migration. After upgrade the rest of the workbench config moved to `<FreeCAD user config dir>/FreeCADAI/` but new session logs continued landing in the legacy unversioned location. Both methods now use the new `LOGS_DIR` constant from `freecad_ai/config.py`, which is computed as `os.path.join(CONFIG_DIR, "logs")` and so picks up any future config-dir change automatically.

### Added

- **Opt-in retention, configurable via `config.json`** — `Conversation.save()` and `_save_session_log()` can now prune the oldest files in their respective directories, but **disabled by default** to preserve v0.13.0-alpha behavior on upgrade. Three new `AppConfig` fields, all defaulting to `0` (= dimension disabled):
  - `max_saved_conversations` — count cap on `<FreeCADAI dir>/conversations/conv_*.json`. Suggested opt-in: `100` (the Load dialog already only shows the newest 20).
  - `max_session_logs` — count cap on `<FreeCADAI dir>/logs/session_*.json`. The auto-saved `latest_session.json` is a single file and exempt. Suggested opt-in: `50`.
  - `max_retention_age_days` — age cap applied to both directories. Files older than this are deleted regardless of count.
  - Both dimensions combine: a file is kept only if it's both within the newest-N AND younger than the age cap. Setting all three to `0` (the default) disables retention entirely — nothing is ever auto-deleted.
- **`prune_oldest_files(directory, pattern_fn, keep, max_age_days=0)`** in `freecad_ai/config.py` — generic helper used by both call sites. Files ranked by mtime (newest preserved). Best-effort: missing directories and individual `unlink` failures don't disrupt save paths.

### Tests

- 12 new tests in `TestPruneOldestFiles`, `TestLogsDir`, and `TestRetention`: mtime-ordered pruning, pattern filter, below-cap short-circuit, missing-directory no-op, age-cap-only pruning, count-and-age combined (union semantics), zero-zero disables pruning, `LOGS_DIR` invariant under `CONFIG_DIR`, `_ensure_dirs` creates `LOGS_DIR`, save-prunes-by-count, save-below-cap-keeps-everything, save-prunes-by-age, and a backwards-compat assertion that a default `AppConfig` leaves 201 pre-existing files untouched on save. Full unit suite: 763 passed, 11 skipped.

## [0.13.0-alpha] - 2026-05-01

Aligns the workbench's config dir with FreeCAD 1.1's version-scoped user dirs. Reported by @egandro on issue #9 — users on FreeCAD 1.1+ saw two `FreeCADAI/` trees side-by-side: the live unversioned one at `~/.config/FreeCAD/FreeCADAI/`, plus a stale snapshot inside `~/.config/FreeCAD/v1-1/` that FreeCAD 1.1's own first-launch migration of the legacy `~/.config/FreeCAD/` tree created. Documentation referenced the unversioned path throughout, but FreeCAD's actual version-scoped config layout drifted from where the workbench was writing.

### Changed

- **Config dir now resolves to `<FreeCAD user config dir>/FreeCADAI/`** — `freecad_ai/config.py` no longer hardcodes `~/.config/FreeCAD/FreeCADAI/`. New resolution order (highest to lowest precedence): `$FREECAD_AI_CONFIG_DIR` env var → `<user-config-dir>/FreeCADAI/` (on FreeCAD 1.1+ Linux: `~/.config/FreeCAD/v1-1/FreeCADAI/`) → `~/.config/FreeCAD/FreeCADAI/` (legacy fallback for pytest, console scripts, plain Python REPL). The user-config-dir is obtained via `FreeCAD.getUserConfigDir()` if the API exposes it, otherwise derived from `FreeCAD.Version()` plus `$XDG_CONFIG_HOME`. Constants `CONFIG_DIR`, `CONFIG_FILE`, `CONVERSATIONS_DIR`, `SKILLS_DIR`, `USER_TOOLS_DIR`, `HOOKS_DIR` are still importable module attributes — value is computed once at import time, so all 30+ consumers keep working without code changes.
- **Path lives under `XDG_CONFIG_HOME`, not `XDG_DATA_HOME`** — workbench data is config-shaped (settings, secrets, conversation logs) so it belongs alongside FreeCAD's own `FreeCAD.conf` / `user.cfg` / `system.cfg`. `Mod/` and `Macro/` (under `XDG_DATA_HOME`) are for code, not config; the FreeCAD AI workbench code itself is still installed under `Mod/freecad-ai/`.

### Migration

- **One-shot config migration on first launch of v0.13.0-alpha+** — runs lazily on first import of `freecad_ai.config` inside FreeCAD. Rename-then-move with a two-stage candidate search:
  1. If `<target>` already exists *without* a marker (e.g. a stale `FreeCADAI/` left by FreeCAD's own first-launch migration), rename it to `<target>.pre-v0.13-snapshot/` — frees the name without overwriting.
  2. Pick the first **historical candidate** with user content as migration source. Order: (a) `<FreeCAD user data dir>/FreeCADAI/` (the v0.13.0-alpha pre-release wrote here briefly under XDG_DATA_HOME, only relevant on the maintainer's machine), (b) `~/.config/FreeCAD/FreeCADAI/` (every released build before v0.13.0-alpha).
  3. `shutil.move` the source → target. Atomic same-filesystem rename when possible, copy-then-remove fallback for cross-device moves. Source ceases to exist.
  4. **Sweep**: any remaining historical candidates that still have content get renamed to `<candidate>.duplicate-cleanup/` (timestamped if name collision). Catches duplicates left by an aborted prior migration. Sweep runs on every launch, not just first migration.
  5. Drop `.freecad_ai_active_marker` in target.
- **Idempotent on subsequent runs** — marker file blocks re-migration; the sweep still runs (cheap if no candidates exist).
- **Best-effort fallback on migration failure** — if `_migrate_to_target` raises, log to stderr and fall back to a candidate path that still exists so the workbench loads. Does not crash.
- **Collision-safe** — pre-existing `*.pre-v0.13-snapshot/` or `*.duplicate-cleanup/` from a prior aborted migration is preserved with a Unix-timestamp suffix appended to the new backup name.
- **`FREECAD_AI_CONFIG_DIR` env var override** — bypasses FreeCAD-based resolution entirely. Useful for isolated profiles, sync-friendly locations, or pinning a fixed path during testing.

### Docs

- **Canonical "Configuration paths" section** in README and wiki `Configuration.md` documents the resolution order, the five-step migration, backup-dir semantics, and the env-var override. Path references throughout README + wiki use the `<FreeCADAI dir>` placeholder consistently, with the canonical section as the source of truth — no more literal `~/.config/FreeCAD/FreeCADAI/` paths that drift from the actual location on FreeCAD 1.1+.
- **Wiki**: `Configuration.md`, `Architecture.md`, `Skills.md`, `Skills-Reference.md`, `Creating-Skills.md`, `Creating-Custom-Tools.md`, `Tool-Reranking.md`, `MCP-Integration.md`, `AGENTS-md.md`, `Getting-Started.md`, `FAQ.md` updated.

## [0.12.1-alpha] - 2026-04-28

Patch release fixing the Edit → Preferences page showing blank fields after a v0.12.0 install.

### Fixed

- **Edit → Preferences → FreeCAD AI page showed empty fields when JSON config was already populated** — `Gui::Pref*` widgets read directly from FreeCAD's parameter store at `BaseApp/Preferences/Mod/FreeCADAI`, but `_write_to_param_store` only fired when the user clicked Save in the AI Settings dialog. Users who upgraded from v0.11.x or earlier (where the bridge didn't exist) saw blanks on the preferences page until they re-saved through the dialog. `load_config` now mirrors the merged JSON+ParamGet result back to the param store on every load, so both UIs stay coherent on first activation. `InitGui.py` also calls `get_config()` after registering the preferences page so the seeding happens even when the user opens Edit → Preferences before activating the workbench.

## [0.12.0-alpha] - 2026-04-28

FreeCAD addon-index conformance — preparation for Addon Manager submission. Adds a FreeCAD-native preferences page (the convention every indexed workbench follows), promotes the existing `file:` / `cmd:` API-key indirection through documentation and tooltips, and fixes a silently-degrading PySide2 hard import that broke vision detection on FreeCAD 1.1.0 for non-Ollama providers.

### Added

- **Edit → Preferences → FreeCAD AI page** — a FreeCAD-native preferences entry point with 8 fields (provider, base URL, model, API key, max tokens, mode, thinking, enable tools), registered via `Gui.addPreferencePage` and backed by `Gui::Pref*` widgets that auto-save into FreeCAD's `BaseApp/Preferences/Mod/FreeCADAI` parameter store. Coexists with the existing AI Settings dialog: the dialog remains primary and exposes the full surface (MCP, skills, hooks, system prompt, model parameters, etc.); the preferences page exposes only the basics that map naturally to FreeCAD's flat parameter store.
- **ParamGet ↔ JSON config bridge** — `_apply_param_store_overrides` on load and `_write_to_param_store` on save keep both UIs in sync without restructuring the config layer. JSON stays primary (nested `mcp_servers`, `model_params`, dock state base64 don't flatten cleanly to ParamGet). Out-of-range enum indices in ParamGet are ignored defensively in case a user hand-edited the param file.
- **Cross-OS environment-variable expansion in `file:` API key prefix** — `os.path.expandvars` runs alongside `os.path.expanduser`, so `file:%APPDATA%\\freecad-ai\\token` works on Windows in addition to the existing `file:~/...` and `file:$HOME/...` syntaxes.
- **Secure API key storage UX** — README and the new preferences page both promote the existing `file:` / `cmd:` prefixes with per-OS examples (Linux `secret-tool`, macOS `security`, Windows CredentialManager / GPG-symmetric / DPAPI). Settings dialog API-key field gets a rich tooltip with the same examples. The maintainer is Linux-only — macOS and Windows examples ship with an explicit "untested" disclaimer.

### Fixed

- **PySide2 hard import in `_generate_probe_image` silently downgraded vision detection on FreeCAD 1.1.0** — the function had `from PySide2.QtGui import QImage, ...` inside a try/except. On FreeCAD 1.1.0 (PySide6 only) the ImportError fell through to a 1×1 white-pixel fallback meant for headless unit tests, and every non-Ollama provider's vision probe ran against that pixel — getting `vision_detected = False` regardless of actual model capability. Now routes through `freecad_ai/ui/compat.py`. The 1×1 fallback still exists for genuinely Qt-less environments but now logs a warning when it activates.

### Changed

- **License SPDX identifier normalized to `LGPL-2.1-or-later`** — the bare `LGPL-2.1` form normalizes to a non-FSF-Libre identifier per the FreeCAD addon Qualities checklist. License text in `LICENSE-CODE` is unchanged; only the `package.xml` `<license>` element was updated.

### Docs

- **Install instructions corrected** — `Resources/Documents/Overview.md` previously claimed Addon Manager install was available. It is not: the workbench is not in any FreeCAD addon registry yet. Submission is in progress (this release is part of that work). Direct clone / symlink remain the only install methods.

## [0.11.1-alpha] - 2026-04-28

Patch release fixing Ollama vision detection and extending the same `/api/show` capability check to tool calling and thinking. Reported by @MuhvICo on issue #8.

### Fixed

- **Ollama vision falsely reported as unsupported** — the previous `vision_probe()` rendered a 64×32 PNG with a 3-digit number and asked the model to OCR it. Many vision-capable models (qwen2.5vl, qwen3-vl, gemma4) handle real photos fine but choke on tiny low-resolution text, producing false negatives. The probe now consults Ollama's native `/api/show` endpoint for the model's `capabilities` array — authoritative for that provider — and only falls back to the OCR probe when `/api/show` is unavailable (older Ollama, transient errors).

### Added

- **Per-model tool-calling and thinking detection** — `/api/show` capabilities also surface `"tools"` and `"thinking"` per model. `AppConfig.supports_tools` now consults `tools_detected` before the provider-wide static flag, so accidentally selecting an Ollama embedding/reranker model (`nomic-embed-text`, `*reranker*`) as the main chat model no longer ships a tools array to a model that can't use it.
- **Capabilities summary in Settings dialog** — Test Connection now appends a one-liner like "Capabilities: tools: yes, thinking: no" to the status label and persists `tools_detected` / `thinking_detected` alongside `vision_detected`. All three reset when the user changes provider or model.

### Changed

- **Behavioral OCR probe enlarged from 64×32 / 16pt to 128×64 / 32pt** — empirical sweep against `qwen3-vl:32b` and `gemma3:4b` showed 64×32 sat right at qwen3-vl's image-preprocessing cliff (smaller inputs returned empty content in 0.1s — image rejected before inference). 128×64 gives 4× area headroom, both tested models hit 100%, PNG stays under 1KB. Only matters for non-Ollama providers and older Ollama without `/api/show`.

## [0.11.0-alpha] - 2026-04-23

Plan-mode feedback loop for local-LLM users: sandbox validation that catches FreeCAD's C++ console errors, Check and Fix-with-AI buttons in the Review Code dialog, and viewport screenshots attached to error-retry messages. Plus dock layout persistence — the chat widget now remembers its area, tab siblings, and floating geometry across sessions.

### Added

- **Check button in Review Code dialog** — runs the generated Python in the existing subprocess sandbox, hooks `App.Console.AddObserver` to catch FreeCAD's C++-logged errors (topological naming, attachment, recompute failures) that never raise Python exceptions, and walks `doc.Objects` to flag invalid or null shapes. Reports issues without touching the live document.
- **Fix with AI button in Review Code dialog** — always enabled. Opens a prompt composer pre-filled with a context-aware template (error, succeeded-but-wrong-output, or blank), which the user can edit before sending. Loops the generated code + feedback back to the LLM through the existing `_handle_execution_error` retry path.
- **Viewport capture on error retries** — when `_handle_execution_error` hands code back to the LLM (either from the Act-mode agentic loop or the new Fix button), the current viewport is attached to the retry message. Vision-capable models can now see the visual effect of broken code, not just the traceback. Respects the existing `capture_mode` setting (off / every_message / after_changes).
- **Chat dock layout persistence** — the FreeCAD AI dock now remembers its last area, tab siblings (e.g. tabified with Tasks), floating state, and geometry across FreeCAD sessions. Saves via `QMainWindow.saveState()` into AppConfig on dock signals, debounced move/resize, and a 3s safety-net poll. Save-enabled and shutdown guards prevent startup/teardown transients from overwriting the last good state.

### Fixed

- **Sandbox false-positive for FreeCAD console errors** — `_sandbox_test` previously wrapped `doc.recompute()` in `try/except` and reported success when no Python exception fired, even though the C++ layer had logged multiple `subshape not found` errors to the Report View. The validation now installs a Console observer before running user code and scans `doc.Objects` for invalid/null shapes after recompute, so C++-only failures surface as sandbox errors.

## [0.10.0-alpha] - 2026-04-21

Tool reranking — keyword and LLM-based filtering to keep the tool-schema token footprint small when many tools are registered.

### Added

- **Tool reranking** — opt-in per-turn filter that sends only the top-N most relevant tools to the LLM, plus a user-configured pinned set. Two methods available in Settings:
  - **Keyword** — pure-Python IDF-weighted token match. Zero extra LLM call, zero latency, lexical-only filtering.
  - **LLM** — semantic ranking via a small/fast LLM (same provider as main by default, or a full provider override for e.g. running reranking on a local Ollama model while the main chat uses a cloud provider). Hallucinated tool names are dropped; slots not filled by the LLM are topped up from the keyword reranker so the filter set is never under-sized.
- **Test Reranker button** in Settings — sends a canonical probe to the reranker LLM with current dialog values (no save needed) and displays success (with LLM-vs-top-up breakdown) or the exact provider error.
- **Diagnostic logging for reranking** — each LLM-reranker call prints its decision points (candidates sent, raw response preview, parsed count, top-up fired) to FreeCAD's Report View.
- **Registry filter plumbing** — `to_openai_schema`/`to_anthropic_schema`/`to_mcp_schema` accept `filter_names=...`; excluded tools skip `resolve_params()`, avoiding MCP schema-fetch round-trips when the reranker filters them out.

### Fixed

- **Ollama Base URL documentation** — clarified that Ollama's OpenAI-compatible endpoints live under `/v1/*`, not `/api/*`. A `/api/` base URL previously produced silent HTTP 404s.

## [0.9.0-alpha] - 2026-04-17

Sketch editing, image-to-sketch, file attachments.

### Added

- **`edit_sketch` tool** — unified tool for modifying existing sketches: add/remove geometry and constraints, or wipe everything with `clear_all=true` and provide fresh geometry. Makes iterative sketch refinement reliable without recreating from scratch.
- **`sketch-from-image` skill** (`/sketch-from-image`) — attach an image (PNG, JPG, SVG) and convert it to a constrained FreeCAD sketch at a specified real-world size. SVG inputs are read as text so the LLM parses coordinates directly. Works with vision-capable models or via a vision-fallback MCP. Handles rectangles, circles, polygons, and lines; curves are approximated.
- **Document attachments** — chat now accepts non-image files. Text files are read and included in the message; binary files (PDF, DOCX, etc.) fire a `file_attach` hook for user-defined processing. Drag-and-drop, paste, and the attach button all work.
- **MCP timeout configuration** — per-server tool call timeout in the Add/Edit MCP server dialog (default 600s).
- **Auto-generated sketch constraints** — `create_sketch` and `edit_sketch` now automatically add `DistanceX`, `DistanceY` for rectangles and `Radius` for circles. LLMs no longer need to specify dimensional constraints by hand.

### Fixed

- **Duplicate constraints in sketches** — explicit constraints now overwrite auto-generated ones instead of creating duplicates (matched by Type + First + Second geometry indices).

### Changed

- **Tool descriptions carry Y-axis warning** — SVG/image coordinates use Y-down while FreeCAD uses Y-up. Tool descriptions now remind LLMs to negate Y values when converting.
- **50 tools total** (was 48).
- **648 unit tests** (was 626).

## [0.8.0-alpha] - 2026-04-05

Parametric modeling, per-model parameters, batch operations, and multi-document support.

### Added

- **Parametric modeling with variable sets** — `create_variable_set` creates an `App::VarSet` with typed, named variables (length, width, height, etc.) editable in the Data panel. `create_spreadsheet` creates a `Spreadsheet::Sheet` with cell aliases as an alternative. Both work the same way with expression bindings.
- **`set_expression` tool** — bind any object property to an expression (`"Variables.length"`, `"Variables.wall * 2"`). Supports indexed properties (`Constraints[N]`) and nested properties (`Placement.Base.x`).
- **Expression support in `create_sketch`** — rectangle dimensions accept expression strings directly: `width="Variables.length"`. Adds DistanceX/DistanceY constraints and binds them automatically.
- **Expression support in `pad_sketch`** — length accepts expression strings: `length="Variables.height"`.
- **Per-model parameters** — freeform key-value parameter table in Settings (temperature, top_p, top_k, etc.), saved per model name. Providers can ship default parameter presets. Replaces the single global temperature field.
- **Strip thinking history** — tristate checkbox in Settings to remove thinking/reasoning content from conversation history. Auto-enabled for Gemma models, required by models that reject thinking content in multi-turn conversations.
- **Tool call summary** — compact visualization after tool loop: tool count, elapsed time, flow diagram (tool1 → tool2 → ...), and per-tool timing with success/failure indicators.
- **Batch edge/face operations** — `fillet_edges`, `chamfer_edges`, and `shell_object` now accept filter keywords: `"all"`, `"vertical"`, `"horizontal"`, `"top"`, `"bottom"`, `"front"`, `"back"`, `"left"`, `"right"`, `"circular"`. Filters can be combined: `["top", "vertical"]`.
- **Filtered queries** — `list_edges` and `list_faces` accept an optional `filter` parameter to show only matching edges/faces.
- **Constraint solver feedback** — `create_sketch` now reports constraint status (fully constrained, under-constrained with DOF count, over-constrained) and lists all constraints with dimension ones marked `← bindable`.
- **Multi-document support** — `list_documents` shows all open documents with object counts and active indicator. `switch_document` changes the active document by name or label.
- **Relative expressions in `modify_property`** — values can be `"+10%"`, `"-20%"`, `"*1.5"`, `"+5"`, `"-3"` for relative modifications instead of requiring absolute values.

### Fixed

- **Fillet/chamfer/shell on Bodies** — when called with a `PartDesign::Body` as `object_name`, now correctly uses `Body.Tip` as the feature base. Previously `_find_body_for` returned `None` (Body doesn't contain itself), causing silent failure.
- **Moonshot temperature** — removed hardcoded temperature overrides. Moonshot's parameters are now user-editable defaults via the Model Parameters table.

### Changed

- **Settings UI** — merged "Parameters" and "Model Parameters" into a single "Model Parameters" section with fixed fields (Max Output Tokens, Context Window) above the freeform key-value table.
- **48 tools total** (was 42).
- **626 unit tests** (was 577).

## [0.7.0-alpha] - 2026-04-03

Assembly tools, geometry query tools, rate limiting, and community contributions.

### Added

- **Assembly tools** — `create_assembly`, `add_assembly_joint`, `add_part_to_assembly` using FreeCAD's native Assembly workbench solver. Supports Fixed, Revolute, Cylindrical, Slider, and Ball joint types. Includes face selection guide in tool descriptions for correct joint setup.
- **`list_faces` tool** — list all faces of an object with names, descriptive labels (top, bottom, front, etc.), normals, center positions, and areas.
- **`list_edges` tool** — list all edges with names, descriptive labels (top-front horizontal, etc.), and lengths.
- **HTTP retry with exponential backoff** — `_http_post()` and `_http_stream()` now retry on 429 rate-limit errors with configurable max retries, Retry-After header support, and jittered exponential backoff.
- **Enhanced context extraction** — Pad/Pocket features now include sketch plane, offset, and geometry count. Revolution features include axis reference and angle.
- **Dark mode** — chat widget automatically adapts to FreeCAD's light/dark theme. Color palette cached for performance with `refresh_theme_cache()` available for runtime switching. (PR #5, @yas1nsyed)
- **GUI active document resolution** — tools and `execute_code` now prefer `FreeCADGui.ActiveDocument.Document` over `App.ActiveDocument`, fixing desync when multiple documents are open. New `active_document.py` module with `resolve_active_document()` / `get_synced_active_document()`. (PR #3, @dpappo)
- **OpenAI GPT-5 support** — `max_completion_tokens` instead of `max_tokens`, temperature omitted (GPT-5 rejects non-default values). Handled via `_apply_provider_overrides()`. (PR #3, @dpappo)
- **Sandbox document copy** — `execute_code` subprocess sandbox now opens a temp copy of the saved `.FCStd` file so `getObject()`-style code validates against real document state instead of an empty `SandboxTest` doc. (PR #3, @dpappo)

### Fixed

- **Assembly ViewProvider** — joints and grounded joints now get proper ViewProvider setup for GUI integration (icons, Simulation support).
- **OpenAI reasoning_content** — preserve `reasoning_content` field in message format for models that return chain-of-thought.
- **Dark mode for all widgets** — extended theme support to all UI widgets, not just chat. Added sandbox GUI stub for headless environments.

## [0.6.0-alpha] - 2026-03-28

New tools, Skills management, skill optimizer, hooks, and snap packaging fix.

### Added

- **Skill optimizer** — `/optimize-skill` command that iteratively improves SKILL.md files by running test cases, scoring results (completion, errors, geometric correctness, efficiency, visual similarity), and using the LLM to modify instructions. Includes PySide2 configuration dialog, version history with original backup, three optimization strategies (conservative, balanced, aggressive), and configurable network retry with exponential backoff. Inspired by [autoresearch](https://github.com/karpathy/autoresearch).
- **Built-in skills auto-discovery** — SkillsRegistry now scans both the repo's `skills/` directory and the user's `~/.config/FreeCAD/FreeCADAI/skills/`. User skills override built-in skills with the same name. No more manual copying of built-in skills.
- **Hooks system** — user-defined Python hooks that fire on lifecycle events (`pre_tool_use`, `post_tool_use`, `user_prompt_submit`, `post_response`). Hooks can block actions, modify input, or log activity. Directory-based discovery at `~/.config/FreeCAD/FreeCADAI/hooks/`. Includes built-in `log-tool-calls` hook and Settings UI for managing hooks.
- **Configurable context window** — new "Context Window" setting controls when automatic conversation compaction triggers. Set to your model's context limit or lower to control API costs.
- **`describe_model` tool** — comprehensive geometry summary of an object in one call: bounding box, volume, face/edge counts, hollow/solid detection, estimated wall thickness, and PartDesign feature list.
- **`redo` tool** — redo previously undone operations.
- **`undo_history` tool** — show the undo/redo stack with named transactions, so the model can see what's available before deciding what to undo.
- **`undo` enhanced** — new `until` parameter to undo back to a named transaction (e.g., `until="Pocket"`). Returns what was undone and remaining undo/redo counts.
- **Fuzzy skill matching** — `use_skill` now does substring search on skill names and descriptions when the exact name isn't found.
- **Skills management in Settings** — new "Skills" section showing all installed skills with status indicators (built-in, modified, user). "Reset to Built-in" button reverts stale user copies to the repo version.
- **"Model supports tool calling" checkbox** — `enable_tools` config exposed in Settings UI. Uncheck for models that don't support tool calling.
- **CONTRIBUTING.md** — contributor guide with fork/clone setup, commit conventions, and how to add skills/providers/tools.

### Fixed

- **Snap-packaged FreeCAD SSL** — handle missing `_ssl` module gracefully. HTTP connections (Ollama) work without SSL; HTTPS gives a clear error suggesting Ollama.
- **Snap tabs default clearance** — changed from 0.2mm to 1.0mm so tabs have proper protrusion even when the model omits the parameter.
- **`describe_model` FreeCAD Quantity** — cast `Base.Quantity` to `float` before formatting.
- **Settings Test Connection crash** — removed leftover `prompt_style_combo` reference.

### Changed

- **`create_inner_ridge` simplified** — extracted `_add_rect` helper, 28 lines → 18 lines.
- **37 tools total** (was 34).

## [0.5.0-alpha] - 2026-03-27

Autonomous skill invocation and editable system prompt.

### Added

- **`use_skill` tool** — the model can now autonomously invoke skills when the user's request matches one. Instead of redirecting users to type `/enclosure`, the model calls `use_skill("enclosure", "120x80x60mm, screw lid")`, gets the step-by-step instructions, and executes them with tools. Natural language "create an enclosure" now works end-to-end.
- **Editable system prompt** — the full system prompt is now visible and editable in Settings, with a "Reset to Default" button. Users can customize the instructions sent to the LLM.

### Fixed

- **Enclosure skill screw geometry** — screw posts now start from the floor surface (offset=T) instead of z=0, and screw holes use fixed depth (H-T) instead of through_all so they don't exit through the bottom wall.
- **PROVIDER_PRESETS consolidation** — eliminated duplicated provider config between `config.py` and `providers.py`. Adding a new provider is now a single-file change.

### Changed

- **Skills no longer redirect** — the system prompt no longer tells the model to ask users to type slash commands. The model uses `use_skill` to load instructions and executes them directly.

## [0.4.0-alpha] - 2026-03-26

Multi-provider support and tool calling reliability.

### Added

- **16 new LLM providers** — DeepSeek, Qwen (DashScope), Groq, Mistral, Together AI, Fireworks AI, xAI (Grok), Cohere, SambaNova, MiniMax, Llama (Meta), GitHub Models, HuggingFace, Zhipu (GLM), Moonshot (Kimi). All OpenAI-compatible with tool calling support. Total: 22 providers + custom.
- **Dynamic API key resolution** — API keys support `file:/path/to/token` (re-read each call) and `cmd:command` (run command, use stdout) prefixes to avoid storing keys in plaintext.
- **Smart object name resolution** — `_get_object()` auto-resolves common LLM naming mistakes (`Sketch0`→`Sketch`, `Sketch1`→`Sketch001`, `Body1`→`Body001`). Error messages now list available objects via `_suggest_similar()` for LLM self-correction.

### Fixed

- **Streaming `finish_reason` handling** — tool calls no longer silently dropped when providers return `"stop"` instead of `"tool_calls"` as the finish reason.
- **`tool_choice="auto"` now explicit** — some providers (e.g. Moonshot/Kimi-K2.5) require this to be set explicitly or they ignore tools entirely. Now sent with every OpenAI-compatible tool-calling request.
- **`reasoning_content` preservation** — thinking models (e.g. Kimi-K2.5) that return `reasoning_content` in assistant messages now have it preserved across agentic loop turns. Without this, multi-turn tool chaining broke after the first turn.
- **Moonshot parameter constraints** — temperature, top_p, and penalty values are automatically overridden to Kimi-K2.5's required fixed values. Temperature field is greyed out in Settings when Moonshot is selected.
- **Non-streaming `stop_reason` detection** — now correctly sets `stop_reason="tool_use"` when tool calls are present regardless of the provider's `finish_reason` value.

### Changed

- **Snap tabs as PartDesign features** — `create_snap_tabs` now creates `PartDesign::AdditiveBox` features inside the lid body instead of a standalone `Part::Feature`. Tabs are individually editable and compatible with fillet, chamfer, pattern, and other PartDesign tools.
- **Better tool success messages** — `create_sketch`, `create_body`, `pad_sketch`, and `pocket_sketch` now include explicit naming hints (e.g., "Use sketch_name='Sketch001' in pad_sketch/pocket_sketch").

## [0.3.0-alpha] - 2026-03-14

Vision routing, image support, user extension tools, and deferred MCP tool loading.

### Added

- **Vision routing** — automatically detect whether the LLM supports vision via a probe image during Test Connection. Vision-capable models receive images inline; non-vision models get images auto-described via an MCP `describe_image` tool (e.g., [llm-vision-mcp](https://github.com/ghbalf/llm-vision-mcp)). When no vision path exists, image controls (Capture, Attach, drag-drop, paste) are disabled. Includes a manual override checkbox in Settings.
- **Image support** — attach viewport screenshots and images to chat messages (Capture button, Attach button, drag-drop, paste)
- **User extension tools** — register custom Python functions (`.py` / `.FCMacro`) as LLM-callable tools. Functions with type hints are auto-discovered from `~/.config/FreeCAD/FreeCADAI/tools/`, validated via AST, and registered into the tool registry. Includes Settings UI for managing tools and optional FreeCAD macro directory scanning.
- **Deferred MCP tool loading** — tool schemas are loaded lazily on first use instead of eagerly on connect, configurable per-server via the `deferred` setting (default: `true`)
- **Tool search** — `MCPClient.search_tools()`, `MCPManager.search_tools()`, and `ToolRegistry.search_tools()` for keyword-based tool discovery across all registered tools
- **Lazy parameter resolution** — `ToolDefinition.lazy_params` callable and `resolve_params()` method for on-demand schema loading
- **Settings UI** — "Deferred tool loading" checkbox in the Add MCP Server dialog; server list shows `(deferred)` / `(disabled)` tags
- **24 new unit tests** for deferred loading, lazy params, tool search, and MCP manager integration

## [0.2.0-alpha] - 2026-02-24

PartDesign-native primitives, patterns, and multi-transform.

### Changed

- **`create_primitive` converted to PartDesign** — creates AdditiveBox, SubtractiveCylinder, etc. inside a Body instead of Part::Box/Part::Cylinder. Supports `operation="additive"|"subtractive"` and `body_name` for adding to existing bodies.
- **`create_wedge` converted to PartDesign** — now uses a loft-based approach instead of Part::Wedge
- **`shell_object` defaults to `reversed=True`** — inward shelling preserves outer dimensions (more intuitive default)
- **`multi_transform` accepts multiple features** — can chain linear pattern + polar pattern + mirror in one operation

### Added

- **`mirror_feature` tool** — mirror a PartDesign feature across XY, XZ, or YZ plane (`PartDesign::Mirrored`)
- **`multi_transform` tool** — chain multiple transformation patterns (linear, polar, mirror) in a single PartDesign::MultiTransform feature
- Integration tests for PartDesign `create_primitive` and `create_wedge`

### Fixed

- LLM stringified-list bug in `shell_object`, `fillet_edges`, `chamfer_edges` — handle `"['Face1']"` strings from some LLMs
- `multi_transform` visibility — ensure intermediate features are hidden after transform
- Added missing tools to system prompt strategy list and stop-when-done instruction

## [0.1.0] - 2026-02-23

Initial alpha release.

### Added

- **Chat interface** with streaming LLM responses in a FreeCAD dock widget
- **Plan / Act modes** — review code before execution or auto-execute
- **Tool calling system** with 21 structured tools:
  - Primitives: `create_primitive`, `create_body`, `create_wedge`
  - Sketching: `create_sketch` (lines, circles, arcs, rectangles, constraints, plane offset)
  - PartDesign: `pad_sketch`, `pocket_sketch`, `revolve_sketch`, `loft_sketches`, `sweep_sketch`
  - Booleans: `boolean_operation` (fuse, cut, common)
  - Transforms: `transform_object`, `scale_object`
  - Edge ops: `fillet_edges`, `chamfer_edges`, `shell_object`
  - Patterns: `linear_pattern`, `polar_pattern`
  - Enclosure helpers: `create_inner_ridge`, `create_snap_tabs`, `create_enclosure_lid`
  - Cross-sections: `section_object`
  - Query: `measure`, `get_document_state`
  - Utility: `modify_property`, `export_model`, `execute_code`, `undo`
  - Interactive: `select_geometry` (viewport picking)
  - View: `capture_viewport`, `set_view`, `zoom_object`
- **Skills system** — reusable instruction sets invoked via `/command`:
  - `/enclosure` — parametric electronics enclosure with snap-fit lid
  - `/gear` — involute spur gear from module and tooth count
  - `/fastener-hole` — clearance, counterbore, countersink holes (ISO dims)
  - `/thread-insert` — heat-set thread insert holes (M2-M5)
  - `/lattice` — grid, honeycomb, diagonal infill patterns
  - `/skill-creator` — create new skills interactively
- **Multiple LLM providers** — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, custom endpoints
- **Thinking mode** — Off / On / Extended reasoning for complex tasks
- **Context compacting** — auto-summarize older messages near context limits
- **Session resume** — auto-save conversations, load from last 20 sessions
- **AGENTS.md support** — project-level instructions with includes and variable substitution
- **MCP support** — STDIO transport, JSON-RPC 2.0, client + server, tool namespacing
- **German translation** (i18n via Qt .ts/.qm)
- **Safety features:**
  - Undo transactions wrapping all tool operations
  - Subprocess sandbox for code execution
  - Sketcher constraint validation to prevent segfaults
  - Pocket auto-direction detection
  - Auto-hide sketches after pad/pocket
- **Test suite** — 243 unit tests
- **Dual licensing** — LGPL-2.1 (code) + CC0-1.0 (icons)
- **Zero external dependencies** — uses only Python stdlib

[0.8.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.8.0-alpha
[0.7.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.7.0-alpha
[0.6.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.6.0-alpha
[0.5.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.5.0-alpha
[0.4.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.4.0-alpha
[0.3.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.3.0-alpha
[0.2.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.2.0-alpha
[0.1.0]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.1.0
