# MCP Client Era Negotiation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the MCP client discover which protocol era a server speaks and talk to it in that era, instead of announcing `2025-03-26` forever.

**Architecture:** `initialize` stays the first request; only a `-32601` refusal triggers a `server/discover` probe. The negotiated era becomes an object (`LegacyEra` / `ModernEra`) living in `protocol.py` that decorates every outgoing request with `_meta` and mirrored headers, so no call site tests a boolean.

**Tech Stack:** Python 3.11, standard library only. No new dependencies — `freecad_ai/mcp/` is a zero-dependency package.

**Spec:** `docs/superpowers/specs/2026-09-20-mcp-client-era-negotiation-design.md`

## Global Constraints

- **Standard library only.** `freecad_ai/mcp/` and its tests import nothing outside the stdlib.
- **`protocol.py` must stay a leaf module.** `transport.py` does `from . import protocol` at line 23. `protocol.py` may import neither `transport` nor `client`; a cycle will surface as an ImportError at FreeCAD startup, not in tests.
- **Legacy behaviour is byte-identical.** Against a server that answers `initialize`, the only wire difference this plan may introduce is the requested `protocolVersion` string. No `_meta`, no extra headers, no extra requests.
- **Run tests as** `env PYTHONPATH= .venv/bin/pytest tests/unit/ -q` — a shell `PYTHONPATH` shadows the venv's pluggy and pytest crashes. Add `--ignore=tests/unit/test_document_attach.py`, which Qt-segfaults on clean master too.
- **Commit subjects must not begin with a closing keyword followed by `(#86)`.** GitHub reads `fix(mcp): … (#86)` as "closes #86" and will close the issue on merge. Use `feat(mcp):`, `refactor(mcp):`, `docs(#86):`, or put the reference in the body.
- **End every commit message with:** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- **Full suite green before each commit.** 1725 tests pass on master at `c75e0bf`.

---

## File Structure

| File | Responsibility after this plan |
|------|-------------------------------|
| `freecad_ai/mcp/protocol.py` | Owns the wire contract: revision table, `_meta` keys, the mirrored-header codec (moved here), and the two era objects. Imports nothing from the package. |
| `freecad_ai/mcp/transport.py` | Plumbing. Gains a `headers=` argument it passes through, and stops treating an HTTP error status with a JSON-RPC body as a transport failure. Drops its private copy of the codec. |
| `freecad_ai/mcp/client.py` | Negotiates once in `connect()`, then routes every request through `self._era`. |
| `tests/unit/test_mcp_header_codec.py` | New. Round-trips the codec and pins the sentinel's edge cases. |
| `tests/unit/test_mcp_client_era.py` | New. The era objects, the negotiation branches, and the end-to-end against our own server. |
| `tests/unit/test_mcp_client_protocol_version.py` | Existing. Two assertions change because the requested version changes. |

---

### Task 1: Move the mirrored-header codec into `protocol.py` and give it an encoder

**Files:**
- Modify: `freecad_ai/mcp/protocol.py` (add at the end of the helpers, after `era_of`)
- Modify: `freecad_ai/mcp/transport.py:28-44` (delete `_decode_header_value`), `transport.py:107` (call site), `transport.py:8` (`import base64`)
- Test: `tests/unit/test_mcp_header_codec.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `protocol.encode_header_value(value: str) -> str` and `protocol.decode_header_value(raw: str | None) -> str | None`. Task 2 calls the encoder; the server's `validate_modern_headers` calls the decoder.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_header_codec.py`:

```python
"""The =?base64?…?= sentinel a mirrored header uses (#86).

The client encodes, the server decodes, and a disagreement produces a -32020
that reads like an attack — so both directions live in protocol.py and are
tested against each other rather than against a restatement of the rules.
"""

import pytest

from freecad_ai.mcp import protocol


class TestRoundTrip:
    @pytest.mark.parametrize("value", [
        "create_box",                 # a plain token, must travel unchanged
        "tool with spaces",
        "wërkzeug",                   # non-ASCII
        "=?base64?not-really?=",      # already looks like the sentinel
        "trailing ",
        " leading",
        "",
    ])
    def test_decode_undoes_encode(self, value):
        assert protocol.decode_header_value(
            protocol.encode_header_value(value)) == value

    def test_a_bare_token_is_not_wrapped(self):
        """Wrapping a safe name would work but make every header unreadable."""
        assert protocol.encode_header_value("create_box") == "create_box"

    def test_a_value_that_mimics_the_sentinel_is_wrapped(self):
        """Otherwise decoding it would hand back something the caller never sent."""
        encoded = protocol.encode_header_value("=?base64?not-really?=")
        assert encoded.startswith("=?base64?")
        assert encoded != "=?base64?not-really?="


class TestDecodeRejects:
    def test_an_undecodable_payload_is_none(self):
        """None is the caller's signal to treat the header as a mismatch."""
        assert protocol.decode_header_value("=?base64?@@@@?=") is None

    def test_none_stays_none(self):
        assert protocol.decode_header_value(None) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_header_codec.py -q`
Expected: FAIL — `AttributeError: module 'freecad_ai.mcp.protocol' has no attribute 'decode_header_value'`

- [ ] **Step 3: Add the codec to `protocol.py`**

Add `import base64` to the imports at the top of `protocol.py`, then add after `era_of`:

```python
_HEADER_SENTINEL_PREFIX = "=?base64?"
_HEADER_SENTINEL_SUFFIX = "?="


def decode_header_value(raw):
    """Decode the ``=?base64?…?=`` sentinel a mirrored header value may use.

    2026-07-28 defines it for values that cannot travel in a header raw — a
    tool name with a non-ASCII character, say. Returns None when the payload
    will not decode, which the caller treats as a mismatch: a value we cannot
    read is not a value we can confirm agrees with the body.
    """
    if raw is None:
        return None
    if raw.startswith(_HEADER_SENTINEL_PREFIX) and raw.endswith(_HEADER_SENTINEL_SUFFIX):
        try:
            return base64.b64decode(
                raw[len(_HEADER_SENTINEL_PREFIX):-len(_HEADER_SENTINEL_SUFFIX)],
                validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return raw


def encode_header_value(value):
    """The inverse of decode_header_value: what to put in a mirrored header.

    A value travels raw only when doing so is unambiguous — printable ASCII,
    no surrounding whitespace, and not itself shaped like the sentinel. That
    last case is the subtle one: a tool literally named ``=?base64?x?=`` sent
    raw would be *decoded* by the far side into something it never called.
    """
    if value is None:
        return None
    safe = (
        value != ""
        and value == value.strip()
        and all(" " <= ch <= "~" for ch in value)
        and not (value.startswith(_HEADER_SENTINEL_PREFIX)
                 and value.endswith(_HEADER_SENTINEL_SUFFIX))
    )
    if safe:
        return value
    payload = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return _HEADER_SENTINEL_PREFIX + payload + _HEADER_SENTINEL_SUFFIX
```

- [ ] **Step 4: Run the new test — it must pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_header_codec.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Point `transport.py` at the moved codec**

Delete `_decode_header_value` entirely (`transport.py:28-44`). At `transport.py:107`, change

```python
        decoded_name = _decode_header_value(raw_name)
```

to

```python
        decoded_name = protocol.decode_header_value(raw_name)
```

Then check whether `base64` is still used in `transport.py`:

```bash
grep -n "base64" freecad_ai/mcp/transport.py
```

If the only remaining hit is the `import base64` line, delete that line too.

- [ ] **Step 6: Run the server-side suites — behaviour must be unchanged**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py tests/unit/test_mcp_streamable_server.py tests/unit/test_mcp_header_codec.py -q`
Expected: PASS. The dual-era tests exercise `validate_modern_headers`, which now reaches the moved decoder — if they pass, the move is behaviour-preserving.

- [ ] **Step 7: Run the full suite and commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add freecad_ai/mcp/protocol.py freecad_ai/mcp/transport.py tests/unit/test_mcp_header_codec.py
git commit -m "refactor(mcp): the mirrored-header codec belongs to the wire contract (#86)"
```

---

### Task 2: The era objects

**Files:**
- Modify: `freecad_ai/mcp/protocol.py` (add after the codec from Task 1)
- Test: `tests/unit/test_mcp_client_era.py` (create)

**Interfaces:**
- Consumes: `protocol.encode_header_value` from Task 1.
- Produces: `protocol.LegacyEra(version: str)` and `protocol.ModernEra(version: str, client_info: dict)`, both with `.version`, `.era` (the `LEGACY` / `MODERN` constant) and `.decorate(method: str, params: dict | None) -> tuple[dict | None, dict]` returning `(params, headers)`. Task 5 and Task 6 use them.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_client_era.py`:

```python
"""Client-side era objects and negotiation (#86)."""

from freecad_ai.mcp import protocol

CLIENT_INFO = {"name": "FreeCAD AI", "version": "0.1.0"}


class TestLegacyEra:
    def test_it_changes_nothing(self):
        """The whole promise to existing servers: an untouched request."""
        era = protocol.LegacyEra("2025-03-26")
        params, headers = era.decorate("tools/list", None)
        assert params is None
        assert headers == {}

    def test_params_pass_through_by_identity(self):
        original = {"name": "create_box", "arguments": {}}
        params, _ = protocol.LegacyEra("2025-03-26").decorate("tools/call", original)
        assert params is original


class TestModernEra:
    def test_meta_carries_version_and_client_info(self):
        era = protocol.ModernEra("2026-07-28", CLIENT_INFO)
        params, _ = era.decorate("tools/list", None)
        assert params["_meta"] == {
            protocol.META_PROTOCOL_VERSION: "2026-07-28",
            protocol.META_CLIENT_INFO: CLIENT_INFO,
        }

    def test_the_callers_dict_is_not_mutated(self):
        """A retry must not see _meta accumulate into the caller's arguments."""
        original = {"name": "create_box", "arguments": {}}
        protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate("tools/call", original)
        assert "_meta" not in original

    def test_headers_mirror_version_and_method(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/list", None)
        assert headers == {"MCP-Protocol-Version": "2026-07-28",
                           "Mcp-Method": "tools/list"}

    def test_tools_call_names_the_tool(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/call", {"name": "create_box", "arguments": {}})
        assert headers["Mcp-Name"] == "create_box"

    def test_an_awkward_tool_name_is_encoded(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/call", {"name": "wërkzeug", "arguments": {}})
        assert protocol.decode_header_value(headers["Mcp-Name"]) == "wërkzeug"

    def test_only_tools_call_carries_a_name(self):
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "server/discover", {})
        assert "Mcp-Name" not in headers


class TestEraTagging:
    def test_each_era_reports_which_one_it_is(self):
        assert protocol.LegacyEra("2025-03-26").era == protocol.LEGACY
        assert protocol.ModernEra("2026-07-28", CLIENT_INFO).era == protocol.MODERN
```

- [ ] **Step 2: Run it and watch it fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -q`
Expected: FAIL — `AttributeError: module 'freecad_ai.mcp.protocol' has no attribute 'LegacyEra'`

- [ ] **Step 3: Implement the era objects**

Add `from dataclasses import dataclass` to `protocol.py`'s imports, then append after the codec:

```python
@dataclass(frozen=True)
class LegacyEra:
    """Pre-2026-07-28: the negotiated version lives in the session.

    decorate() is deliberately a no-op. Everything this class does not do is
    the compatibility promise: a server that answered our initialize sees the
    same bytes it saw before this era object existed.
    """

    version: str
    era: str = LEGACY

    def decorate(self, method, params):
        return params, {}


@dataclass(frozen=True)
class ModernEra:
    """2026-07-28+: every request re-states the version, in body and headers.

    There is no session, so each request carries its own metadata, and an
    HTTP intermediary gets the same facts in mirrored headers without having
    to parse the body.
    """

    version: str
    client_info: dict
    era: str = MODERN

    def decorate(self, method, params):
        params = dict(params or {})
        params["_meta"] = {
            META_PROTOCOL_VERSION: self.version,
            META_CLIENT_INFO: self.client_info,
        }
        headers = {
            "MCP-Protocol-Version": self.version,
            "Mcp-Method": method,
        }
        if method == "tools/call":
            headers["Mcp-Name"] = encode_header_value(params.get("name"))
        return params, headers
```

- [ ] **Step 4: Run the test — it must pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add freecad_ai/mcp/protocol.py tests/unit/test_mcp_client_era.py
git commit -m "feat(mcp): an era object decorates a request instead of a boolean at each call site (#86)"
```

---

### Task 3: Transports accept and send per-request headers

**Files:**
- Modify: `freecad_ai/mcp/transport.py` — `StdioClientTransport.send_request` (`:267`) and `.send_notification` (`:290`); `SSEClientTransport.send_request` (`:441`), `.send_notification` (`:451`), `._post` (`:454`); `StreamableHTTPClientTransport.send_request` (`:517`), `.send_notification` (`:559`), `._post` (`:565`)
- Test: `tests/unit/test_mcp_client_era.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: every client transport accepts `headers: dict | None = None` on `send_request` and `send_notification`. Stdio ignores it; both HTTP transports add each pair to the request. Task 5 relies on this.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_client_era.py`:

```python
import http.server
import json
import threading

from freecad_ai.mcp.transport import (
    StdioClientTransport,
    StreamableHTTPClientTransport,
)


class _HeaderRecorder(http.server.BaseHTTPRequestHandler):
    """Answers any POST with {"ok": true} and records the headers it saw."""

    seen = []        # one dict of headers per request
    versions = []    # every MCP-Protocol-Version occurrence, per request

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        # get_all, not the dict: a dict collapses a duplicated header, and
        # a duplicate is exactly what one of these tests must be able to see.
        type(self).versions.append(
            self.headers.get_all("MCP-Protocol-Version") or [])
        type(self).seen.append(dict(self.headers.items()))
        payload = json.dumps(
            protocol.make_response(body.get("id"), {"ok": True}),
            separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _Serving:
    """Run _HeaderRecorder on a free port for the duration of a with-block."""

    def __enter__(self):
        _HeaderRecorder.seen = []
        _HeaderRecorder.versions = []
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _HeaderRecorder)
        self.base = "http://127.0.0.1:%d/mcp" % self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)


class TestTransportsSendPerRequestHeaders:
    def test_streamable_sends_what_it_is_given(self):
        with _Serving() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            t.send_request("tools/list", {}, timeout=5,
                           headers={"Mcp-Method": "tools/list",
                                    "MCP-Protocol-Version": "2026-07-28"})
            t.stop()
        sent = _HeaderRecorder.seen[0]
        assert sent["Mcp-Method"] == "tools/list"
        assert sent["MCP-Protocol-Version"] == "2026-07-28"

    def test_an_era_header_overrides_the_latched_one_without_duplicating(self):
        """Our own server rejects a duplicated MCP-Protocol-Version outright."""
        with _Serving() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            t.protocol_version = "2025-03-26"        # the legacy latch
            t.send_request("tools/list", {}, timeout=5,
                           headers={"MCP-Protocol-Version": "2026-07-28"})
            t.stop()
        assert _HeaderRecorder.versions[0] == ["2026-07-28"]

    def test_stdio_ignores_headers(self):
        """No header channel exists; being handed some must not raise."""
        t = StdioClientTransport(["echo"], None)
        # Not started: we assert the signature accepts the argument, which is
        # what the client relies on when a stdio server speaks the modern era.
        import inspect
        for method in (t.send_request, t.send_notification):
            assert "headers" in inspect.signature(method).parameters
```

- [ ] **Step 2: Run it and watch it fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -k Transports -q`
Expected: FAIL — `TypeError: send_request() got an unexpected keyword argument 'headers'`

- [ ] **Step 3: Thread `headers` through the transports**

`StdioClientTransport` — accept and ignore:

```python
    def send_request(self, method: str, params: dict | None = None,
                     timeout: float = 30, headers: dict | None = None) -> dict:
        """Send a JSON-RPC request and wait for the matching response.

        ``headers`` is accepted and ignored: stdio has no header channel, and
        the modern era carries everything it needs in params._meta.
        """
```

```python
    def send_notification(self, method: str, params: dict | None = None,
                          headers: dict | None = None):
```

`SSEClientTransport`:

```python
    def send_request(self, method, params=None, timeout=30, headers=None):
        req_id = self._correlator.next_id()
        event = self._correlator.register(req_id)
        try:
            self._post(protocol.make_request(method, params, id=req_id), headers)
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
            self._correlator.cancel(req_id)
            return protocol.make_error(req_id, protocol.INTERNAL_ERROR, str(exc))
        return self._correlator.wait(req_id, event, timeout)

    def send_notification(self, method, params=None, headers=None):
        self._post(protocol.make_notification(method, params), headers)

    def _post(self, msg, headers=None):
```

and inside `_post`, immediately after the existing `if self.protocol_version:` block:

```python
        for key, value in (headers or {}).items():
            req.add_header(key, value)
```

`StreamableHTTPClientTransport`, the same shape. Change **only** the `def` line
and the `self._post(...)` call — the `except Exception` block below it closes a
response the exception may carry, and deleting that is a resource leak:

```python
    def send_request(self, method, params=None, timeout=30, headers=None):
        req_id = self._alloc_id()
        msg = protocol.make_request(method, params, id=req_id)
        try:
            resp = self._post(msg, timeout, headers)
        except Exception as exc:  # noqa: BLE001 — unchanged, keep as it is
            ...                   # the existing closer/INTERNAL_ERROR block
```

```python
    def send_notification(self, method, params=None, headers=None):
        resp = self._post(protocol.make_notification(method, params),
                          self._connect_timeout, headers)

    def _post(self, msg, timeout, headers=None):
```

with the same loop appended after its `if self.protocol_version:` block.

The era headers are added **last** deliberately. `urllib.request.Request.add_header` keys by the capitalized header name, so a second `MCP-Protocol-Version` replaces the first rather than appending — no duplicate reaches the wire, which matters because our own server rejects a repeated mirrored header outright (`transport.py:80`).

- [ ] **Step 4: Run the test — it must pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -q`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_client_era.py
git commit -m "feat(mcp): client transports carry per-request headers (#86)"
```

---

### Task 4: An HTTP error status with a JSON-RPC body is a response

**Files:**
- Modify: `freecad_ai/mcp/transport.py` — `StreamableHTTPClientTransport._post` (`:565`) and `SSEClientTransport._post` (`:454`)
- Test: `tests/unit/test_mcp_client_era.py` (append)

**Interfaces:**
- Consumes: the `_post(…, headers=None)` signatures from Task 3.
- Produces: `send_request` returns the server's JSON-RPC error object when the status is non-2xx but the body parses, on **both** HTTP transports; `protocol`-level helper `_as_json_rpc(body) -> dict | None` in `transport.py`. Task 6's negotiation depends on being able to read a `-32601` that arrived as a 404.

**Why this task exists:** a modern server reports `-32601` as **404** and the `-3202x` family as **400** (`transport.py:MODERN_ERROR_STATUS`). `urlopen` raises `HTTPError` on both, the broad `except Exception` turns it into `INTERNAL_ERROR` carrying the string `HTTP Error 404: Not Found`, and the real error code is lost. Negotiation keys on that code.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_client_era.py`:

```python
class _ErrorStatusServer(http.server.BaseHTTPRequestHandler):
    """Answers every POST with a JSON-RPC -32601 carried by a 404, the way a
    modern server reports an unknown method."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        payload = json.dumps(
            protocol.make_error(body.get("id"), protocol.METHOD_NOT_FOUND,
                                "Unknown method."),
            separators=(",", ":")).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _ServingErrors(_Serving):
    def __enter__(self):
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _ErrorStatusServer)
        self.base = "http://127.0.0.1:%d/mcp" % self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self


class TestAnErrorStatusIsStillAResponse:
    def test_a_404_with_a_json_rpc_body_keeps_its_code(self):
        """Without this, every modern error reads as INTERNAL_ERROR."""
        with _ServingErrors() as srv:
            t = StreamableHTTPClientTransport(srv.base, connect_timeout=5)
            t.start()
            resp = t.send_request("initialize", {}, timeout=5)
            t.stop()
        assert resp["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_a_status_with_no_usable_body_is_still_a_transport_error(self):
        class _Empty(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()

        srv = http.server.HTTPServer(("127.0.0.1", 0), _Empty)
        base = "http://127.0.0.1:%d/mcp" % srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            t = StreamableHTTPClientTransport(base, connect_timeout=5)
            t.start()
            resp = t.send_request("initialize", {}, timeout=5)
            t.stop()
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
        assert resp["error"]["code"] == protocol.INTERNAL_ERROR

    def test_sse_does_not_wait_for_a_reply_that_will_not_come(self):
        """SSE's reply normally arrives on the stream, so send_request blocks
        on the correlator. When the server answers on the POST instead, the
        body must short-circuit that wait rather than be drained."""
        with _ServingErrors() as srv:
            t = SSEClientTransport("http://127.0.0.1:1/sse", connect_timeout=5)
            t._endpoint_url = srv.base     # skip the GET /sse handshake
            started = time.monotonic()
            resp = t.send_request("initialize", {}, timeout=30)
            t.stop()
        assert resp["error"]["code"] == protocol.METHOD_NOT_FOUND
        assert time.monotonic() - started < 10, "it waited out the correlator"
```

Add `time` to the test file's imports, and `SSEClientTransport` to the
`freecad_ai.mcp.transport` import list.

- [ ] **Step 2: Run it and watch it fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -k ErrorStatus -q`
Expected: FAIL — the first test reports `INTERNAL_ERROR` (`-32603`) instead of
`-32601`, and the SSE test blocks for its full 30-second timeout before
failing the same way.

- [ ] **Step 3: Return the body when there is one**

Add `import urllib.error` to `transport.py`'s imports, then add one helper at
module level, above the transports:

```python
def _as_json_rpc(body):
    """Parse these bytes as a JSON-RPC message, or return None."""
    try:
        msg = protocol.decode(body.decode("utf-8"))
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    return msg if isinstance(msg, dict) and msg.get("jsonrpc") == "2.0" else None
```

**Streamable HTTP.** Its `send_request` reads `resp.headers`, may iterate the
response as SSE, then `read()`s and `close()`s it — so the recovered body has to
arrive wearing that same shape. Add beside the helper:

```python
class _ReplayedResponse:
    """An HTTPError body re-presented with the response API _post's caller uses.

    HTTPError is readable exactly once, and we have to read it to find out
    whether it is JSON-RPC at all, so the bytes travel alongside it. Only the
    members StreamableHTTPClientTransport.send_request touches are provided:
    headers, read and close. An error status always carries application/json,
    never text/event-stream, so the SSE-iteration branch is never reached.
    """

    def __init__(self, err, body):
        self.headers = err.headers
        self.status = err.code
        self._body = body

    def read(self):
        return self._body

    def close(self):
        pass
```

and wrap the `urlopen` in `StreamableHTTPClientTransport._post`:

```python
        try:
            return urllib.request.urlopen(
                req, timeout=timeout, context=self._ssl_context)
        except urllib.error.HTTPError as err:
            # A modern server reports -32601 as 404 and the -3202x family as
            # 400. A status with a JSON-RPC body is an answer, not a failed
            # POST — so hand it back instead of letting the caller's broad
            # except turn it into INTERNAL_ERROR.
            body = err.read()
            if _as_json_rpc(body) is None:
                raise
            return _ReplayedResponse(err, body)
```

**SSE is not the same shape and must not be treated as one.** Its `_post`
returns nothing: the reply arrives later over the event stream, and
`send_request` blocks on the correlator until it does. Draining a recovered
error body there would discard the server's answer and leave the caller waiting
out the full timeout for a reply the server has already decided not to send — a
worse outcome than today's `INTERNAL_ERROR`. So `_post` returns the message and
`send_request` short-circuits on it:

```python
    def send_request(self, method, params=None, timeout=30, headers=None):
        req_id = self._correlator.next_id()
        event = self._correlator.register(req_id)
        try:
            immediate = self._post(
                protocol.make_request(method, params, id=req_id), headers)
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
            self._correlator.cancel(req_id)
            return protocol.make_error(req_id, protocol.INTERNAL_ERROR, str(exc))
        if immediate is not None:
            # The server answered on the POST itself; nothing will arrive on
            # the stream, so stop waiting for it.
            self._correlator.cancel(req_id)
            return immediate
        return self._correlator.wait(req_id, event, timeout)

    def send_notification(self, method, params=None, headers=None):
        self._post(protocol.make_notification(method, params), headers)

    def _post(self, msg, headers=None):
        """POST one message. Returns a JSON-RPC reply the server sent back on
        the POST itself (an error status), or None for the normal 202."""
        if self._endpoint_url is None:
            raise RuntimeError("MCP SSE transport not connected (no endpoint)")
        req = urllib.request.Request(
            self._endpoint_url, data=protocol.encode(msg), method="POST")
        for key, value in self._headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        if self.protocol_version:
            req.add_header("MCP-Protocol-Version", self.protocol_version)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            resp = urllib.request.urlopen(
                req, timeout=self._connect_timeout, context=self._ssl_context)
        except urllib.error.HTTPError as err:
            reply = _as_json_rpc(err.read())
            if reply is None:
                raise
            return reply
        resp.read()   # drain the 202 body
        resp.close()
        return None
```

This supersedes the `_post` edit Task 3 made to `SSEClientTransport`: that task
added the header loop, and this one replaces the whole method around it.

- [ ] **Step 4: Run the test — it must pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -q`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_client_era.py
git commit -m "feat(mcp): an HTTP error status with a JSON-RPC body is a response (#86)"
```

---

### Task 5: Route every client request through the era

**Files:**
- Modify: `freecad_ai/mcp/client.py:25` (the version constant), `:71-104` (`connect`), `:113` (`_refresh_tools`), `:201` (`call_tool`)
- Modify: `tests/unit/test_mcp_client_protocol_version.py:112` (the fake transport's signature), `:170`, `:177`
- Test: `tests/unit/test_mcp_client_era.py` (append)

**Interfaces:**
- Consumes: `protocol.LegacyEra` / `protocol.ModernEra` (Task 2), the transports' `headers=` argument (Task 3).
- Produces: `MCPClient._era` (a `LegacyEra` until Task 6 negotiates) and `MCPClient._send(method, params=None, timeout=None) -> dict`, the single outgoing-request path. Task 6 adds negotiation on top.

**Note on scope:** this task changes *what version we ask for* and *how requests are built*. It does not add negotiation — after it, a modern-only server still fails. Keeping the two apart means the "legacy is byte-identical" claim gets its own review.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_client_era.py`:

```python
from freecad_ai.mcp.client import CLIENT_INFO, MCPClient


class _Recorder:
    """A transport that records every outgoing call and answers plausibly."""

    def __init__(self, init_result=None):
        self.calls = []            # (kind, method, params, headers)
        self.protocol_version = None
        self.is_alive = True
        self._init_result = init_result if init_result is not None else {
            "protocolVersion": "2025-03-26", "capabilities": {}}

    def start(self):
        pass

    def stop(self):
        self.is_alive = False

    def send_request(self, method, params=None, timeout=30, headers=None):
        self.calls.append(("request", method, params, headers))
        if method == "initialize":
            return protocol.make_response(1, self._init_result)
        if method == "tools/list":
            return protocol.make_response(1, {"tools": []})
        return protocol.make_response(1, {"content": [], "isError": False})

    def send_notification(self, method, params=None, headers=None):
        self.calls.append(("notification", method, params, headers))


class TestLegacyWireIsUnchanged:
    def test_no_meta_and_no_headers_reach_a_legacy_server(self):
        """The compatibility promise, asserted on the whole trace."""
        transport = _Recorder()
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        client.call_tool("create_box", {})
        for _kind, _method, params, headers in transport.calls:
            assert not headers
            assert params is None or "_meta" not in params

    def test_we_ask_for_the_newest_legacy_revision(self):
        transport = _Recorder()
        MCPClient("test", ["echo"], transport=transport).connect()
        _, _, params, _ = transport.calls[0]
        assert params["protocolVersion"] == protocol.LATEST_LEGACY_VERSION

    def test_initialized_is_still_sent(self):
        transport = _Recorder()
        MCPClient("test", ["echo"], transport=transport).connect()
        assert ("notification", "notifications/initialized", None, {}) in [
            (k, m, p, h or {}) for k, m, p, h in transport.calls]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -k LegacyWire -q`
Expected: FAIL on `test_we_ask_for_the_newest_legacy_revision` — it asks with `2025-03-26`, and `LATEST_LEGACY_VERSION` is `2025-11-25`.

- [ ] **Step 3: Give the client one outgoing path**

In `client.py`, replace the constant at line 25:

```python
# What we ASK for in initialize — the newest legacy revision we understand.
# The server's answer wins (see connect), so asking high costs nothing: a
# server that does not know it replies with one it does support. Note this is
# deliberately not protocol.DEFAULT_PROTOCOL_VERSION, which is what our own
# *server* answers a client that named no version and must stay 2025-03-26.
PROTOCOL_VERSION = protocol.LATEST_LEGACY_VERSION
```

Confirm `client.py` already imports `protocol`; if it imports only names from it, add `from . import protocol`.

In `__init__`, beside the other attributes:

```python
        # Until connect() negotiates, behave exactly as every earlier release.
        self._era = protocol.LegacyEra(protocol.DEFAULT_PROTOCOL_VERSION)
```

Add the single send path, just above `connect`:

```python
    def _send(self, method, params=None, timeout=None):
        """Every outgoing request goes through here, so the era is applied once."""
        params, headers = self._era.decorate(method, params)
        if timeout is None:
            return self._transport.send_request(method, params, headers=headers)
        return self._transport.send_request(
            method, params, timeout=timeout, headers=headers)

    def _notify(self, method, params=None):
        params, headers = self._era.decorate(method, params)
        self._transport.send_notification(method, params, headers=headers)
```

Then route the three call sites through it:

- `connect`: `resp = self._send("initialize", {...})`, and the notification becomes `self._notify("notifications/initialized")`
- `_refresh_tools`: `resp = self._send("tools/list")`
- `call_tool`: `resp = self._send("tools/call", {"name": name, "arguments": arguments}, timeout=timeout if timeout is not None else self._tool_call_timeout)`

In `connect`, set the era from what the handshake settled:

```python
        version = resp.get("result", {}).get("protocolVersion") or PROTOCOL_VERSION
        self._era = protocol.LegacyEra(version)
        self._transport.protocol_version = version
```

- [ ] **Step 4: Update the two assertions that pinned the old ask**

In `tests/unit/test_mcp_client_protocol_version.py`, give the fake transport the new signature (line 112):

```python
    def send_request(self, method, params=None, timeout=30, headers=None):
```

and at line 170 replace the pinned literal:

```python
        assert PROTOCOL_VERSION == protocol.LATEST_LEGACY_VERSION
        assert protocol.era_of(PROTOCOL_VERSION) == protocol.LEGACY
```

The second line is the assertion worth keeping: what matters is not which string we ask with, but that we open with a *legacy* revision, because a modern one would name a handshake the server may have removed.

- [ ] **Step 5: Run both suites — they must pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py tests/unit/test_mcp_client_protocol_version.py tests/unit/test_mcp_deferred.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add freecad_ai/mcp/client.py tests/unit/test_mcp_client_era.py tests/unit/test_mcp_client_protocol_version.py
git commit -m "feat(mcp): the client asks for the newest legacy revision and builds requests through its era (#86)"
```

---

### Task 6: Negotiation

**Files:**
- Modify: `freecad_ai/mcp/client.py` (`connect`, and a new `_negotiate_modern`)
- Test: `tests/unit/test_mcp_client_era.py` (append)

**Interfaces:**
- Consumes: `MCPClient._send` / `_era` (Task 5), `protocol.ModernEra` (Task 2), readable HTTP-status errors (Task 4).
- Produces: after `connect()`, `client._era` is a `ModernEra` against a modern-only server and a `LegacyEra` otherwise.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_client_era.py`:

```python
class _ModernOnly(_Recorder):
    """Refuses initialize the way a stateless server must, answers discover."""

    def __init__(self, supported=("2026-07-28",)):
        super().__init__()
        self._supported = list(supported)

    def send_request(self, method, params=None, timeout=30, headers=None):
        self.calls.append(("request", method, params, headers))
        if method == "initialize":
            return protocol.make_error(1, protocol.METHOD_NOT_FOUND,
                                       "This server has no handshake.")
        if method == "server/discover":
            return protocol.make_response(1, {
                "supportedVersions": self._supported,
                "capabilities": {"tools": {}},
            })
        if method == "tools/list":
            return protocol.make_response(1, {"tools": []})
        return protocol.make_response(1, {"content": [], "isError": False})


class TestModernNegotiation:
    def test_a_refused_handshake_is_followed_by_a_probe(self):
        transport = _ModernOnly()
        client = MCPClient("test", ["echo"], transport=transport)
        client.connect()
        assert [m for _k, m, _p, _h in transport.calls][:2] == [
            "initialize", "server/discover"]
        assert client._era.era == protocol.MODERN
        assert client._era.version == "2026-07-28"

    def test_the_probe_itself_is_modern(self):
        """It must carry _meta, or a modern server reads it as a legacy call."""
        transport = _ModernOnly()
        MCPClient("test", ["echo"], transport=transport).connect()
        _kind, _method, params, headers = transport.calls[1]
        assert params["_meta"][protocol.META_PROTOCOL_VERSION] in protocol.MODERN_VERSIONS
        assert headers["Mcp-Method"] == "server/discover"

    def test_initialized_is_not_sent_in_the_modern_era(self):
        """There is no session to initialize; the method was removed."""
        transport = _ModernOnly()
        MCPClient("test", ["echo"], transport=transport).connect()
        assert "notifications/initialized" not in [
            m for k, m, _p, _h in transport.calls if k == "notification"]

    def test_later_requests_carry_the_negotiated_version(self):
        transport = _ModernOnly()
        MCPClient("test", ["echo"], transport=transport).connect()
        listing = [c for c in transport.calls if c[1] == "tools/list"][0]
        assert listing[3]["MCP-Protocol-Version"] == "2026-07-28"


class TestNegotiationFailures:
    def test_a_non_32601_error_raises_without_probing(self):
        """An auth failure must not be retried as if it were an era mismatch."""

        class _Unauthorized(_Recorder):
            def send_request(self, method, params=None, timeout=30, headers=None):
                self.calls.append(("request", method, params, headers))
                return protocol.make_error(1, protocol.INTERNAL_ERROR, "401")

        transport = _Unauthorized()
        with pytest.raises(RuntimeError, match="401"):
            MCPClient("test", ["echo"], transport=transport).connect()
        assert [m for _k, m, _p, _h in transport.calls] == ["initialize"]

    def test_both_eras_refused_names_both_failures(self):
        class _Hostile(_Recorder):
            def send_request(self, method, params=None, timeout=30, headers=None):
                self.calls.append(("request", method, params, headers))
                return protocol.make_error(1, protocol.METHOD_NOT_FOUND, "no")

        with pytest.raises(RuntimeError) as exc:
            MCPClient("test", ["echo"], transport=_Hostile()).connect()
        assert "initialize" in str(exc.value)
        assert "server/discover" in str(exc.value)

    def test_no_shared_version_raises_with_both_lists(self):
        """Falling back to initialize would re-send what it just removed."""
        transport = _ModernOnly(supported=["2031-01-01"])
        with pytest.raises(RuntimeError) as exc:
            MCPClient("test", ["echo"], transport=transport).connect()
        assert "2031-01-01" in str(exc.value)
        assert "2026-07-28" in str(exc.value)
```

Add `import pytest` to the file's imports if Task 1's file did not already.

- [ ] **Step 2: Run it and watch it fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -k "Negotiation" -q`
Expected: FAIL — `RuntimeError: MCP server 'test' initialization failed` on the first test; no probe is sent.

- [ ] **Step 3: Implement negotiation**

Replace the error branch of `connect` in `client.py`:

```python
        if "error" in resp:
            error = resp["error"] or {}
            if error.get("code") != protocol.METHOD_NOT_FOUND:
                raise RuntimeError(
                    f"MCP server '{self.name}' initialization failed: {resp['error']}"
                )
            # A server that has no initialize is a 2026-07-28 server: the
            # handshake was removed, not broken. Only -32601 means that; any
            # other failure is a real one and must not be retried as an era
            # mismatch.
            self._era = self._negotiate_modern(error)
        else:
            version = resp.get("result", {}).get("protocolVersion") or PROTOCOL_VERSION
            self._era = protocol.LegacyEra(version)

        self._transport.protocol_version = self._era.version
        if self._era.era == protocol.LEGACY:
            self._notify("notifications/initialized")
```

and add the probe:

```python
    def _negotiate_modern(self, initialize_error):
        """Ask a handshake-less server what it speaks, or raise saying why not."""
        probe = protocol.ModernEra(protocol.MODERN_VERSIONS[0], CLIENT_INFO)
        params, headers = probe.decorate("server/discover", {})
        resp = self._transport.send_request(
            "server/discover", params, headers=headers)
        if "error" in resp:
            raise RuntimeError(
                f"MCP server '{self.name}' speaks neither era — "
                f"initialize: {initialize_error}; "
                f"server/discover: {resp['error']}")

        offered = resp.get("result", {}).get("supportedVersions") or []
        # Intersect against the MODERN revisions only. We are here because the
        # server removed initialize, so a legacy version in common is not one
        # we could actually use.
        shared = [v for v in protocol.MODERN_VERSIONS if v in offered]
        if not shared:
            raise RuntimeError(
                f"MCP server '{self.name}' offers {offered!r}; "
                f"this client speaks {list(protocol.MODERN_VERSIONS)!r}")
        return protocol.ModernEra(shared[0], CLIENT_INFO)
```

`MODERN_VERSIONS` is newest-first (`protocol.py:47`), so `shared[0]` is the newest revision both sides speak.

- [ ] **Step 4: Run the test — it must pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -q`
Expected: PASS, 25 tests.

- [ ] **Step 5: Commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add freecad_ai/mcp/client.py tests/unit/test_mcp_client_era.py
git commit -m "feat(mcp): negotiate the era, legacy first (#86)"
```

---

### Task 7: What a modern connection does with what it gets back

**Files:**
- Modify: `freecad_ai/mcp/client.py` (`_send`, `_refresh_tools`)
- Test: `tests/unit/test_mcp_client_era.py` (append)

**Interfaces:**
- Consumes: `MCPClient._send` (Task 5), `ModernEra` and `_negotiate_modern` (Task 6).
- Produces: `MCPClient.tools_cache_hints` — `{"ttlMs": int, "cacheScope": str}` or
  `None`. Nothing in this plan reads it; it exists so a later re-list feature has
  the values without another round trip.

**Why these two together:** both are things a *modern* reply carries that a
legacy one never does, and both are a handful of lines in the client. A reviewer
who accepted one would have no grounds to reject the other, so they share a
review surface rather than each taking a dispatch.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_client_era.py`:

```python
class TestAHeaderMismatchIsLogged:
    def test_a_32020_is_logged_with_the_headers_we_sent(self, caplog):
        """-32020 means OUR headers disagreed with OUR body: a client bug.

        Retrying would send the identical bad headers, so the only useful
        response is a log line carrying enough to debug it.
        """

        class _Picky(_Recorder):
            def send_request(self, method, params=None, timeout=30, headers=None):
                self.calls.append(("request", method, params, headers))
                if method == "initialize":
                    return protocol.make_error(1, protocol.METHOD_NOT_FOUND, "no")
                if method == "server/discover":
                    return protocol.make_response(1, {
                        "supportedVersions": ["2026-07-28"]})
                return protocol.make_error(
                    1, protocol.HEADER_MISMATCH, "Mcp-Method disagrees.")

        with caplog.at_level(logging.ERROR, logger="freecad_ai.mcp.client"):
            MCPClient("test", ["echo"], transport=_Picky()).connect()

        mismatch = [r for r in caplog.records if "-32020" in r.getMessage()
                    or "mismatch" in r.getMessage().lower()]
        assert mismatch, "a header mismatch must not pass silently"
        assert "Mcp-Method" in mismatch[0].getMessage()


class TestCacheHintsAreStored:
    def test_a_modern_tools_list_keeps_its_freshness_hints(self):
        """Stored, not acted on: nothing re-lists yet (see the spec's
        Out of scope). Keeping them costs two lines and saves the re-list
        feature a round trip."""

        class _WithHints(_ModernOnly):
            def send_request(self, method, params=None, timeout=30, headers=None):
                if method == "tools/list":
                    self.calls.append(("request", method, params, headers))
                    return protocol.make_response(1, {
                        "tools": [], "resultType": "complete",
                        "ttlMs": 60000, "cacheScope": "public"})
                return super().send_request(method, params, timeout, headers)

        client = MCPClient("test", ["echo"], transport=_WithHints())
        client.connect()
        assert client.tools_cache_hints == {"ttlMs": 60000, "cacheScope": "public"}

    def test_a_legacy_listing_leaves_them_unset(self):
        client = MCPClient("test", ["echo"], transport=_Recorder())
        client.connect()
        assert client.tools_cache_hints is None
```

Add `import logging` to the test file's imports.

- [ ] **Step 2: Run it and watch it fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -k "HeaderMismatch or CacheHints" -q`
Expected: FAIL — no log record is emitted, and `MCPClient` has no
`tools_cache_hints` attribute.

- [ ] **Step 3: Log the mismatch and keep the hints**

In `client.py`, add the check to `_send`, which every request already passes
through:

```python
    def _send(self, method, params=None, timeout=None):
        """Every outgoing request goes through here, so the era is applied once."""
        params, headers = self._era.decorate(method, params)
        if timeout is None:
            resp = self._transport.send_request(method, params, headers=headers)
        else:
            resp = self._transport.send_request(
                method, params, timeout=timeout, headers=headers)
        error = resp.get("error") or {}
        if error.get("code") == protocol.HEADER_MISMATCH:
            # Our mirrored headers disagreed with our own body. That is a bug
            # on this side by construction, and a retry would send the same
            # bad headers — so log what went out and let the error surface.
            logger.error(
                "MCP server '%s' rejected %s with -32020 (%s); headers sent: %r",
                self.name, method, error.get("message", ""), headers)
        return resp
```

In `__init__`, beside `self._era`:

```python
        # Freshness hints from a modern tools/list, kept for a future re-list
        # feature. None after a legacy listing, which carries no such fields.
        self.tools_cache_hints = None
```

and in `_refresh_tools`, after `self._raw_tools = …`:

```python
        result = resp.get("result", {})
        if "ttlMs" in result:
            self.tools_cache_hints = {
                "ttlMs": result["ttlMs"],
                "cacheScope": result.get("cacheScope",
                                         protocol.DEFAULT_CACHE_SCOPE),
            }
```

`ttlMs` is the key to test for, not `cacheScope`: both are REQUIRED on a
modern `tools/list`, and `ttlMs: 0` means "do not cache" rather than absence
(`protocol.py:64-68`), so presence must be checked by key.

- [ ] **Step 4: Run the test — it must pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -q`
Expected: PASS, 28 tests.

- [ ] **Step 5: Commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add freecad_ai/mcp/client.py tests/unit/test_mcp_client_era.py
git commit -m "feat(mcp): log a header mismatch, keep a modern listing's cache hints (#86)"
```

---

### Task 8: End to end against our own server, and the CHANGELOG

**Files:**
- Test: `tests/unit/test_mcp_client_era.py` (append)
- Modify: `CHANGELOG.md` (the `## [Unreleased]` section)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing further depends on this.

**Why it earns its own task:** every other test in this plan asserts against a fake. This one puts our client and our server on a real socket, which is the only assertion that catches the two halves disagreeing about the contract — a mirrored header our encoder produces and our own validator rejects, for instance.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_client_era.py`. The server setup copies
`_RunningServer` in `tests/unit/test_mcp_streamable_server.py` — `MCPServer` has
no `start`/`stop`, only a blocking `run()`, so a test drives `_handle` through a
hand-built `HTTPServerTransport`:

```python
from freecad_ai.mcp import server as server_mod
from freecad_ai.mcp import transport as transport_mod
from freecad_ai.tools.registry import ToolDefinition, ToolRegistry, ToolResult


class _OurServer:
    """Our own MCPServer on an ephemeral loopback port, in a thread."""

    def __init__(self, registry):
        self._registry = registry

    def __enter__(self):
        self.transport = transport_mod.HTTPServerTransport(
            host="127.0.0.1", port=0)
        self.transport._handler = server_mod.MCPServer(
            self._registry, transport=self.transport)._handle
        self.httpd = self.transport._make_server()
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()


class TestOurClientAgainstOurServer:
    def test_a_modern_tools_call_survives_our_own_header_validation(self):
        """The contract's two halves, on a real socket, in the modern era."""
        ran = []

        def _echo(text=""):
            ran.append(text)
            return ToolResult(True, "echoed %s" % text)

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            "echo_text", "Echo the text back.", [], handler=_echo))

        with _OurServer(registry) as srv:
            client = MCPClient(
                "self",
                transport=transport_mod.StreamableHTTPClientTransport(
                    srv.url, connect_timeout=5))
            client.connect()
            # Our server serves both eras, so connect() negotiates LEGACY here
            # and asserting MODERN would be asserting the wrong thing. Switch
            # by hand: the negotiation branches are covered against fakes
            # above, and what this test uniquely proves is that a modern
            # request our client BUILDS is one our server ACCEPTS.
            client._era = protocol.ModernEra("2026-07-28", CLIENT_INFO)
            client._transport.protocol_version = "2026-07-28"
            result = client.call_tool("echo_text", {"text": "hello"})
            client.disconnect()

        assert ran == ["hello"]
        assert result.is_error is False
        assert result.content[0]["text"] == "echoed hello"
```

- [ ] **Step 2: Run it**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_client_era.py -k OurClient -q`

If it fails with `-32020`, the encoder and the validator disagree — that is the
bug this task exists to find; fix the encoder, not the test. If it passes first
time, prove it is load-bearing: temporarily make `ModernEra.decorate` put
`"wrong_name"` in `Mcp-Name`, confirm the test fails with `-32020`, then revert.

- [ ] **Step 3: Write the CHANGELOG entry**

Under `## [Unreleased]`, add:

```markdown
- **MCP client: protocol era negotiation.** The client announced `2025-03-26`
  on every connection and never asked whether the server spoke anything newer,
  so a stateless `2026-07-28` server — which has no `initialize` at all — could
  not be used. It now opens with the newest legacy revision it understands, and
  treats a `-32601` refusal as the signal to probe `server/discover` and switch
  to the modern era, carrying `_meta` and mirrored headers on every later
  request. Servers that answer `initialize` see exactly the bytes they saw
  before, apart from the requested version string. (#86)
- **Fixed: an HTTP error status no longer hides the error.** Both HTTP client
  transports treated any non-2xx response as a failed POST, so a JSON-RPC error
  a server reported with a 400 or 404 reached callers as a generic internal
  error with the text `HTTP Error 404: Not Found`. The body is now read and
  returned. (#86)
```

- [ ] **Step 4: Run the full suite and commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q
git add tests/unit/test_mcp_client_era.py CHANGELOG.md
git commit -m "test(mcp): our client and our server agree on a modern tools/call (#86)"
```

---

## After the plan

- The wiki lives in a separate repository (`/home/alf/Projects/programming/misc/freecad-ai-wiki`). `MCP-Integration.md` documents the server's behaviour; the client's negotiation belongs there too, but it is not a task in this plan — the wiki is pushed separately once this work merges.
- `~/bin/probe-dual-era.sh` probes the *server*. A client-side equivalent is not needed: Task 7 covers the same ground in the suite, on a real socket.
