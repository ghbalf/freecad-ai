# Dual-Era MCP Server (#64 phase 3) — Design

**Issue:** [#64 — MCP protocol: we implement 2025-03-26, current is 2026-07-28](https://github.com/ghbalf/freecad-ai/issues/64)
**Date:** 2026-09-19
**Status:** Approved (design)

## Goal

Serve the modern, stateless `2026-07-28` revision and the legacy
`initialize`-handshake revisions from the same endpoint and the same process, so
a client gets a working connection whichever era it speaks.

The spec calls this a **dual-era** server and explicitly permits it: *"A dual-era
server MAY serve both eras concurrently on the same endpoint or process."* It
selects behaviour from how the client opens — a request carrying modern
per-request `_meta` is served statelessly; an `initialize` request selects legacy
semantics.

This is phase 3 of #64. Phase 1 shipped as #65/PR #68 (`POST /mcp`), and phase 2
reduced to one real gap, closed by `03285f5` (the client now sends
`MCP-Protocol-Version` after the handshake).

## Why now, when nothing is broken

Nothing breaks today and nothing will break on a schedule. The exposure is that
it breaks *silently and one-directionally*: we keep working exactly as long as
every client we talk to keeps its legacy fall-back, and the spec is blunt about
what happens after that — *"Legacy clients have no fall-forward mechanism."*
When a counterparty goes modern-only, our end fails with no diagnostic we
control.

`protocol.py` already encodes this as deliberate debt: `2026-07-28` is excluded
from `SUPPORTED_PROTOCOL_VERSIONS` with the comment *"accepting it would promise
behaviour we do not have (#64)."* This design is what lets that exclusion be
removed truthfully.

## Approved decisions

| Decision | Choice |
|----------|--------|
| Structure | Version model in **`protocol.py`**, routing in `server.py`, header validation in `transport.py` |
| Extensibility | An ordered **revision table** carrying each version's era. Adding a revision is a row; no code below the table compares a version literal |
| Era detection | **By shape, not by version** — modern iff `params._meta` carries `io.modelcontextprotocol/protocolVersion` |
| Unknown modern version | `UnsupportedProtocolVersionError` (`-32022`), **never** a silent fall-through to legacy |
| `supported` list in `-32022` | Era-matched: `MODERN_VERSIONS` for a modern-shaped request, `LEGACY_VERSIONS` for a legacy-shaped one |
| `server/discover` | Answered in **both** eras; advertises modern versions only |
| `initialize` | Now **echoes** the client's requested version when it is one we speak *in the legacy era*; a version we do not speak gets `2025-11-25`, and a request naming no version at all keeps the historical `2025-03-26` — it has no requested version to echo, so answering it anything else would move legacy output. Clamped to the legacy era because `initialize` exists in no modern revision — echoing `2026-07-28` would promise the shape that revision deleted |
| `ping` | Legacy only. Modern gets `-32601` — the revision removed it |
| Legacy results | **Byte-identical to v0.28.0-alpha.** No `resultType`, no `_meta` |
| Cache hints | `ttlMs` / `cacheScope` **configurable** via config + env, no GUI. Invalid values fall back and warn |
| `tools/list` order | **Sorted by name**, both eras |
| HTTP status | **Era-dependent.** Legacy always `200`; modern maps `-32601` to `404` and `-3202x` to `400` |
| Scope | Server only. Client-side `server/discover` probing is a follow-up |
| Dependencies | **stdlib only**, consistent with the rest of `freecad_ai/mcp/` |

## The version model

`SUPPORTED_PROTOCOL_VERSIONS` is a flat `frozenset` today. It can answer *"do we
serve this?"* but not *"how should we interpret it?"*, which is the question
dual-era turns on — and the question every future revision will also raise.

It becomes an ordered table, newest first, where `ProtocolRevision` is a
`NamedTuple` of `(version, era)`:

```python
MODERN, LEGACY = "modern", "legacy"

PROTOCOL_REVISIONS = (
    ProtocolRevision("2026-07-28", era=MODERN),
    ProtocolRevision("2025-11-25", era=LEGACY),
    ProtocolRevision("2025-06-18", era=LEGACY),
    ProtocolRevision("2025-03-26", era=LEGACY),
)
```

`SUPPORTED_PROTOCOL_VERSIONS`, `MODERN_VERSIONS`, `LEGACY_VERSIONS`,
`LATEST_LEGACY_VERSION` and `era_of(version)` all derive from it.
`DEFAULT_PROTOCOL_VERSION` stays `2025-03-26`: it means *"what a header-less HTTP
request is assumed to be"*, which the spec still pins to that value.

The four `_meta` key names — `io.modelcontextprotocol/protocolVersion`,
`clientInfo`, `clientCapabilities`, `serverInfo` — become module constants. They
are long, they appear in several places, and they are exactly the kind of string
that rots when typed twice.

New error codes, from the range `2026-07-28` reserved for the specification:

| Code | Name |
|------|------|
| `-32020` | `HeaderMismatch` |
| `-32021` | `MissingRequiredClientCapability` |
| `-32022` | `UnsupportedProtocolVersion` |

These were renumbered in this revision (`-32004` → `-32022`, etc.). Anything
written against the draft numbering is wrong.

`-32021` is defined but never emitted: we require no client capability, so there
is none to be missing. It exists because the status map has to classify it as a
`400` if a future revision ever gives us a reason to send it.

### Why era is detected by shape

Only a modern client sends `_meta.protocolVersion`, so its presence — not its
value — is what identifies the era. The consequence is the extensibility
property we want: a request naming `2027-05-01` is modern-shaped but unserveable,
so it receives `-32022` listing what we do serve, rather than being mistaken for
a legacy request and answered with legacy semantics. A revision we have never
heard of fails loudly and actionably on day one, with no code change.

### Why `supported` is era-matched

A modern client can only use modern revisions. Offering it `2025-03-26` would
invite it to put a legacy version into a modern `_meta`, which is incoherent —
that version has no per-request metadata to carry. So `-32022` answers a
modern-shaped request with `MODERN_VERSIONS` and a legacy-shaped one with
`LEGACY_VERSIONS`. The same reasoning makes `server/discover` advertise modern
versions only, even though the server serves four.

## Routing

`_handle` computes the era once per message, then dispatches from an era-scoped
route table.

| Method | Legacy | Modern |
|--------|--------|--------|
| `initialize` | echoes requested version; `2025-11-25` for an unknown one, `2025-03-26` for none | `-32601` |
| `notifications/initialized` | silent | ignored |
| `ping` | answered | `-32601` — removed in this revision |
| `tools/list` | today's shape | `+ resultType`, `ttlMs`, `cacheScope` |
| `tools/call` | today's shape | `+ resultType` |
| `server/discover` | answered | answered |

`server/discover` is answered in **both** eras because its whole purpose is
letting a client discover which era it is talking to. Refusing it to a client
that has not yet proven itself modern defeats the probe the spec designed it for.

`ping` is **not** served under modern. The revision removed it, so answering it
would advertise a method that does not exist in the revision the client asked
for.

`server/discover` always returns a **modern-shaped** `DiscoverResult`, including
`resultType`, in both eras. This does not contradict the rule below: the method
does not exist in any legacy revision, so there is no legacy shape of it to hold
still. A client that asks the modern question gets the modern answer regardless
of how it framed the request.

### Legacy output does not move

Legacy results stay byte-identical to what v0.28.0-alpha emits: no `resultType`,
no `_meta`. The spec instructs modern clients to treat a missing `resultType` as
`"complete"`, so adding it buys nothing, and holding the legacy bytes still is
what keeps every existing regression test meaningful.

The one intended legacy change is `initialize` echoing the requested version
instead of hardcoding `2025-03-26`. We are conformant with `2025-11-25`
semantics — established by the research comment on #64 and verified against the
code — so telling a `2025-11-25` client to downgrade is a pointless loss.

### Modern result envelope

Every modern result carries `resultType: "complete"` and
`_meta['io.modelcontextprotocol/serverInfo']`, derived from `__version__` the way
`SERVER_INFO` already is. We never return `input_required`: no tool of ours needs
to ask the user for anything mid-call.

`tools/list` additionally carries `ttlMs` and `cacheScope`, both **configurable**
— see below.

The defaults are `ttlMs: 300000` and `cacheScope: "private"`. Private rather than
public because the server supports bearer-token auth (#59), which makes a
response potentially authorization-scoped; on a localhost listener there is no
shared intermediary for `public` to benefit anyway. Five minutes is the spec's
own example value, and our tool set is fixed for the life of the process.

Tools are sorted by name in both eras. It is a `SHOULD` from `2025-11-25`, and
per #47 a stable prefix is what lets a provider's prompt cache hit.

### The cache hints are configurable

Both values are tuning knobs whose right setting depends on a deployment we
cannot see from here — how far the server is exposed, whether anything caches in
front of it, how a given client polls. So they are configuration rather than
constants:

| Field | Env override | Default |
|-------|--------------|---------|
| `mcp_server_tools_ttl_ms` | `MCP_TOOLS_TTL_MS` | `300000` |
| `mcp_server_tools_cache_scope` | `MCP_TOOLS_CACHE_SCOPE` | `"private"` |

Env overrides rather than config alone, because that is what the other four MCP
server settings do (`MCP_HOST`, `MCP_PORT`, `MCP_ALLOWED_HOSTS`,
`MCP_AUTH_TOKEN`) and what makes the headless `mcp_server_http.py` entry point
configurable without editing `config.json`.

**No GUI.** These belong with `dangerous_mode` and the retention knobs:
documented as hand-edit-only rather than half-surfaced. The MCP section of the
Settings dialog is already dense, and a spinbox labelled "cache scope" helps
nobody who does not already know what the field does. The wiki documents both,
with their defaults.

**`ttlMs: 0` means "do not cache" and is still emitted.** The revision makes both
fields REQUIRED on `tools/list`, so opting out of caching is expressed as a zero
TTL, never as an absent field. There is no configuration that makes the result
non-conformant.

**Invalid values fall back to the default and log a warning.** A negative or
non-integer TTL, or a `cacheScope` outside `{"public", "private"}`, would put a
malformed value on the wire and break conformance for every client — so
resolution validates before use. This lives in `server.py` as
`resolve_cache_hints(cfg=None)`, not in `protocol.py`: reading config and the
environment is application knowledge, and `protocol.py` stays pure JSON-RPC
shape. `protocol.py` holds only `DEFAULT_TOOLS_TTL_MS`, `DEFAULT_CACHE_SCOPE`
and the `CACHE_SCOPES` tuple.

Resolution happens **once, at `MCPServer.__init__`**, and is injectable
(`MCPServer(registry, ..., cache_hints=None)`) so tests never touch a config
file. Per-request resolution would mean a config read on every `tools/list`,
which buys a liveness nobody asked for.

## Transport

`RequestHandler` is defined inside a method of `HTTPServerTransport`, so nothing
in it is reachable from a test without binding a socket. Header validation is
pure logic over `(headers, msg)`, so it lands at module level:

```python
def validate_modern_headers(headers, msg) -> dict | None:
    """Return a JSON-RPC error if the mirrored headers disagree with the body."""
```

The handler sends whatever comes back. The header matrix then tests as a plain
table of cases.

### Check order changes

Today the `MCP-Protocol-Version` check runs before the body is read
(`transport.py:917`), which is sound while the header alone decides. The spec now
requires the header to **match** `_meta`'s version and `Mcp-Method` to match
`method` — both need the body. Parsing moves first.

That retires the subtlety in the current comment: rejecting without draining was
safe only while `self.protocol_version` stays at the stdlib default `HTTP/1.0`.
After the reorder the body is always drained and the caveat goes away.

### Modern checks

All `-32020` / HTTP `400` unless noted:

- `MCP-Protocol-Version` missing on a modern-shaped body, or unequal to `_meta`'s version
- version not in `MODERN_VERSIONS` → **`-32022`** with `data: {supported, requested}`
- `Mcp-Method` missing or unequal to `method`
- `Mcp-Name` missing or unequal to `params.name` on `tools/call`
- any of the three mirrored headers sent **more than once**, whatever the
  copies say

A repeat is refused rather than resolved, and that is the whole point of the
check. `email.message.Message.get()` returns the first copy; nginx's
`$http_mcp_name` also takes the first, Envoy joins duplicates with a comma,
and some WAFs take the last. Picking any one of those makes the header agree
with whichever intermediary happens to share our choice, which is not a
guarantee — a mirrored header is only worth routing on when there is exactly
one of it.

`Mcp-Name` is compared **after** decoding the `=?base64?…?=` sentinel. All 56 of
our tool names are plain ASCII, but a conforming client may encode any value
matching that pattern, and comparing raw would reject a correct client.

Legacy-shaped bodies keep today's lenient handling: an absent header means
`2025-03-26` and no mirrored headers are expected.

### One behaviour change reaches the legacy path

A named-but-unsupported version now answers `-32022` instead of `-32600`. This is
required rather than cosmetic: a recognized modern error is precisely the signal
that tells a dual-era client *"modern server, retry with a supported version"*
instead of *"legacy server, fall back to initialize."* The machine-readable
`data` is what a client acts on, and it is new.

Three smaller things move with it, and they are sanctioned here rather than
discovered later. The rejection now carries the request's real `id` instead of
a null one, because the body is parsed before the check and the client can
finally correlate the answer with what it sent. The revisions are listed in
table order, newest first, rather than `sorted()` — the useful order for a
client choosing what to retry with. And the human-readable `message` is
rebuilt by the shared `-32022` helper, so its wording changes from
`Unsupported MCP-Protocol-Version %r.` to `Unsupported MCP protocol version
%r.`; one helper phrasing both eras is worth more than a string no client
parses. All of it churns assertions in `test_mcp_streamable_server.py`.

One further movement, not designed but accepted: a legacy request whose
`params` is not an object — `"params": 5` — used to reach `params.get()` and
raise, which the transport rendered as `-32603`. It is now coerced to `{}`
before any handler sees it, so `tools/call` answers `isError` with an empty
tool name instead. Both are error answers to malformed input; replacing an
internal error with a well-formed one on an endpoint that is unauthenticated
by default is the right direction, and `-32602` would be more precise still
if this ever matters to a real client.

### Status mapping is era-dependent

| Outcome | Legacy | Modern |
|---------|--------|--------|
| success | `200` | `200` |
| `-32601` method not found | `200` | **`404`** |
| `-32020` / `-32021` / `-32022` | `400` | `400` |
| notification | `202` | `202` — unless its mirrored headers disagree |

Legacy must keep answering `200`-with-error, because that is what every revision
through `2025-11-25` specifies. Returning `404` there would break clients that
work today.

The one qualification on `202`: a modern notification is header-validated before
its missing `id` is noticed, so a mirrored-header mismatch answers `400` with a
`-32020` body carrying `"id": null` rather than `202`. JSON-RPC says never to
answer a notification, but the header contract is an HTTP-layer one — a request
an intermediary could have mis-routed must be refused whether or not it wanted a
reply, and staying silent would leave the client believing a smuggled
notification was accepted.

### The other two transports come free

**STDIO** has no headers, so era detection from `_meta` is the entire mechanism
and the routing change covers it.

The deprecated **`/sse`** pair shares `MCPServer._handle`, so a modern-shaped
message posted there is served modern *without* header validation. That is not a
hole — header mirroring exists so intermediaries can route without parsing the
body, and a localhost SSE socket has none. It gets a comment in the code so the
asymmetry does not read as an oversight later.

## Error handling

| Condition | Response |
|-----------|----------|
| Modern-shaped, unknown version | `-32022`, `data: {supported: MODERN_VERSIONS, requested}`, HTTP `400` |
| Legacy-shaped, unknown version | `-32022`, `data: {supported: LEGACY_VERSIONS, requested}`, HTTP `400` |
| Header/body mismatch or missing required header | `-32020`, HTTP `400` |
| Unknown method, modern | `-32601`, HTTP `404` |
| Unknown method, legacy | `-32601`, HTTP `200` |
| Tool failure | `isError: true` inside a success result — unchanged (SEP-1303) |

Tool execution failures keep returning as results rather than protocol errors.
`ToolRegistry.execute` has always done this; `2025-11-25` later made it the
required behaviour.

## Testing

**New — `tests/unit/test_mcp_dual_era.py`:**

- era detection: modern-shaped, legacy-shaped, and modern-shaped naming an unknown version
- `server/discover` result checked against the spec's published example
- `resultType` present on modern results and **absent** on legacy ones
- `-32022` payload shape for both eras' `supported` lists
- `ping` answered under legacy, `-32601` under modern
- `initialize` echoing each of the three legacy versions, falling back to `2025-11-25` for an unknown one and to `2025-03-26` when no version is named

**New — cache-hint resolution** (in the dual-era test file): the default pair,
a config override, an env override winning over config, `0` accepted and emitted
verbatim, and each invalid form (negative TTL, non-integer TTL, unknown
`cacheScope`) falling back to the default rather than reaching the wire.

**Extended — `test_mcp_streamable_server.py`:** the header matrix as a
parametrised table (each mismatch, plus a base64-encoded `Mcp-Name`), and the
status-code mapping for both eras.

**Regression:** every existing MCP test must pass untouched apart from the
`-32600` → `-32022` assertions. That is the guard on *"legacy stays
byte-identical"* — if anything else moves, the change is wrong.

Full suite before committing:

```
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py
```

**Live probe**, not only unit tests — a mock cannot catch a wrong HTTP status.
Start the GUI server under Xvfb, then `curl` one modern `tools/call` carrying all
three headers and one legacy `initialize` + `tools/call` against the same `/mcp`
endpoint: both must report 56 tools, and the modern call must produce a real
geometry change in the document.

## Out of scope

- **A GUI for the cache hints.** Deliberate, per the decision above; the wiki
  carries them instead.
- **Client-side dual-era.** Teaching `client.py` to probe with `server/discover`
  and fall back is the follow-up. Our client keeps speaking `2025-03-26` to our
  own server over the legacy path, so nothing self-breaks.
- **`subscriptions/listen`.** We never declare `listChanged`, so it would open a
  stream that can never carry anything.
- **MRTR / `input_required`.** No tool of ours needs input mid-call.
- **`Mcp-Param-*` custom headers.** We emit no `x-mcp-header` annotations, so
  there is no recognised header to validate.
- **Roots, sampling, logging.** Never implemented, and `2026-07-28` deprecates
  all three.
- **`Mcp-Session-Id` / `Last-Event-ID`.** Ignored rather than rejected, which is
  what the spec prescribes.

## Risks

**Legacy regression is the real risk.** Every currently-configured MCP client
speaks legacy, including our own. The mitigation is structural rather than
careful: legacy responses are unchanged bytes, and the existing test suite is the
assertion of that.

**The `-32600` → `-32022` change touches a shipped path.** It is the one
deliberate exception to the rule above, and the reason is interop rather than
tidiness.

**No modern counterparty exists to test against.** Every modern test is
self-authored, so a misreading of the spec would be tested in twice rather than
caught. Mitigation: result shapes are asserted against the examples published in
the spec pages, quoted rather than paraphrased.

**`transport.py` is already 1028 lines.** Header validation goes in as a
module-level function rather than another branch inside the nested handler, which
adds test reach instead of removing it.
