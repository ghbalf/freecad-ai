# MCP client era negotiation

**Issue:** #86 — the MCP client hardcodes `2025-03-26` and never asks a server
whether it speaks anything newer.
**Companion spec:** `2026-09-19-mcp-dual-era-server-design.md`, the server half
of the same contract. Where the two could differ, this one follows it.

## The problem

`freecad_ai/mcp/client.py:25` declares one version string:

```python
PROTOCOL_VERSION = "2025-03-26"
```

It is announced in `initialize` (`client.py:77`) and used as the fallback when
a server's reply omits its own (`client.py:92`). So when the workbench connects
*to* an external MCP server it names a revision three releases old, and a
server that removed the handshake entirely — a `2026-07-28` stateless server —
cannot be talked to at all. Our own server has served both eras since
`c75e0bf`; our client speaks only one of them.

## Negotiation

`connect()` keeps its current first move. What changes is what it asks for and
what it does with a refusal.

```
initialize { protocolVersion: LATEST_LEGACY_VERSION, capabilities: {}, clientInfo }
 ├─ result           → LEGACY era, version = result.protocolVersion or what we asked
 ├─ error -32601     → modern probe
 └─ any other error  → raise (unchanged from today)

server/discover { _meta: { …/protocolVersion: MODERN_VERSIONS[0] } }
 ├─ result → MODERN era, version = newest of
 │           set(result.supportedVersions) & SUPPORTED_PROTOCOL_VERSIONS
 └─ error  → raise, naming both failures
```

**Legacy first, not modern first.** Under `2025-03-26` a client must not send
anything before `initialize`, and a strict legacy server may error, ignore, or
drop the connection on an unsolicited `server/discover`. Legacy servers are
approximately all servers in the wild today, so probing first would tax the
common case and risk odd behaviour, to save a round trip against the rare one.
A modern-only server pays one wasted round trip instead. Reverse this if the
population ever inverts; nothing else in the design depends on the order.

**Ask for `LATEST_LEGACY_VERSION`** (`protocol.py:48`, currently `2025-11-25`),
not the current `2025-03-26`. A server that does not know it answers with a
version it does support, and `client.py:92` already latches the server's answer
over ours. Note the deliberate asymmetry with `DEFAULT_PROTOCOL_VERSION` two
lines below: the *server* still answers `2025-03-26` to a client that named no
version, because moving that would change the wire shape for configurations
that never asked. The client has no such constraint — nobody negotiated against
our request string.

**`notifications/initialized` is legacy-only.** The modern era has no session to
initialize, so sending it there names a method the server never agreed to.

**Only `-32601` upgrades.** An `initialize` that fails for any other reason —
auth, TLS, transport, a malformed reply — raises exactly as today. Treating an
auth failure as "perhaps it is a modern server" turns one clear error into two
confusing ones.

## The era object

Two small classes in `protocol.py`, beside the existing era model, since that
module already owns what an era *is* and the server's half reads from it:

```python
class LegacyEra:
    """Pre-2026-07-28: the negotiated version lives in the session."""
    version: str
    def decorate(self, method, params):
        return params, {}


class ModernEra:
    """2026-07-28+: every request re-states the version, in body and headers."""
    version: str
    client_info: dict            # passed in; see "Import direction" below
    def decorate(self, method, params):
        params = dict(params or {})
        params["_meta"] = {
            META_PROTOCOL_VERSION: self.version,
            META_CLIENT_INFO: self.client_info,
        }
        headers = {"MCP-Protocol-Version": self.version, "Mcp-Method": method}
        if method == "tools/call":
            headers["Mcp-Name"] = encode_header_value(params["name"])
        return params, headers
```

`MCPClient` holds one, defaulting to `LegacyEra(DEFAULT_PROTOCOL_VERSION)` so an
un-negotiated client behaves exactly as it does now. Every outgoing call becomes

```python
params, headers = self._era.decorate(method, params)
```

Three request sites (`client.py:76`, `:113`, `:201`) and one notification. A
future revision adds a class and a row in `PROTOCOL_REVISIONS`; no call site
changes.

**Why an object rather than an `if self._modern:` at each site:** the branch
form needs remembering at every method added later, and forgetting it is
silent — the request simply goes out shaped as the wrong era. That is the shape
of the `-32020` bug the server half shipped and had to fix. It also keeps
"what a modern request looks like" in one place instead of three transports.

## Mirrored headers

Our own server requires them on every modern HTTP request:
`MCP-Protocol-Version` and `Mcp-Method` always, `Mcp-Name` on `tools/call`
(`transport.py:84-111`). A modern client that sends `_meta` alone gets `-32020`
and a 400.

`encode_header_value` is the missing mirror of `_decode_header_value`
(`transport.py:28`). It returns the value unchanged when it is a bare HTTP
token, and the `=?base64?…?=` sentinel otherwise. Both belong together so
neither can drift: a client and server that disagree here produce a header
mismatch that reads like an attack.

### Import direction

`transport.py` imports `protocol` (`transport.py:23`), so `protocol` must stay
a leaf — it may import neither `transport` nor `client`. Two consequences,
both of which move code toward the module that should already own it:

- **The codec moves to `protocol.py`** as public `encode_header_value` /
  `decode_header_value`. `transport.py` imports them and drops its private
  copy, updating its one call site (`transport.py:107`). The mirrored-header
  sentinel is part of the wire contract, not of HTTP plumbing, and this is
  what lets both the client's encoder and the server's validator read from one
  definition. It has no direct test coverage today; assertion 4 below is its
  first.
- **`ModernEra` takes `client_info` as a field** rather than importing
  `CLIENT_INFO` from `client.py`. `MCPClient` passes its own constant in when
  it builds the era.

## Transport

`send_request(method, params=None, timeout=30)` and
`send_notification(method, params=None)` gain `headers=None`.

- **STDIO** ignores it. There is no header channel, and the modern era carries
  everything it needs in `_meta`.
- **SSE and Streamable HTTP** add each pair. The existing
  `if self.protocol_version:` lines (`transport.py:462`, `:574`) stay for the
  legacy path; in the modern era the era object supplies the same header and
  the values agree by construction.

### An HTTP status is not a transport failure

A modern server reports errors *in the status line*: our own maps `-32601` to
404 and the `-3202x` family to 400 (`transport.py:MODERN_ERROR_STATUS`). Both
HTTP client transports currently treat any non-2xx as a failed POST —
`urlopen` raises `HTTPError`, the broad `except Exception` catches it, and the
caller receives `INTERNAL_ERROR` carrying the text `HTTP Error 404: Not
Found`.

That makes every modern error unreadable, including the `-32601` this design
negotiates on. Both transports must therefore treat an `HTTPError` whose body
parses as a JSON-RPC message as **the response**, and keep the
`INTERNAL_ERROR` path only for a status with no usable body. `HTTPError` is
itself a readable response object, so this is a `try/except` around the
`urlopen`, not a new code path.

This is a pre-existing bug rather than one this work introduces; it is in
scope because modern-era negotiation cannot function without it.

## Error handling

| Case | Behaviour |
|------|-----------|
| `initialize` → `-32601` | Probe `server/discover`. The only upgrade trigger. |
| `initialize` → any other error | Raise, unchanged. |
| `server/discover` → error | Raise, naming both failures, e.g. `initialize: -32601; server/discover: -32601`. |
| `supportedVersions` ∩ ours = ∅ | Raise with both lists. Falling back to `initialize` would re-send a method the server just told us it removed. |
| Modern `-32020` from a server | Log at error with the headers sent. A client bug by construction, and a retry would send the identical bad headers. |

## Testing

Fake servers in `tests/unit/`, following `test_mcp_dual_era.py`: a legacy one
that answers `initialize`; a modern-only one that `-32601`s `initialize` and
answers `server/discover`; a hostile one that `-32601`s both.

The assertions that catch a regression rather than restate the code:

1. Against a legacy server the wire trace is **byte-identical to today's**
   except the requested version string — no `_meta`, no extra headers, no probe.
2. `notifications/initialized` is present in the legacy era and absent in the
   modern one.
3. A modern `tools/call` produces headers our **own** `validate_modern_headers`
   accepts — the real function, not a restatement of its rules.
4. `decode_header_value(encode_header_value(x)) == x` over awkward tool names
   (spaces, non-ASCII, a name that is already a valid token).
5. End to end: our client against our server on a live port, modern era, one
   tool called and its result read.
6. An `initialize` failing with `-32603` raises without any `server/discover`
   being sent.

## Out of scope

**Cache hints.** A modern `tools/list` returns `ttlMs` and `cacheScope`, and
#86 proposed honouring them. `_refresh_tools()` is called exactly once, from
`connect()`, and nothing re-lists — so a TTL has no consumer, and honouring it
means first building a re-list mechanism nothing has asked for. The client
stores the hints where they can be seen; acting on them needs a re-list trigger
and a reason to want one, and belongs to whichever issue produces that reason.
