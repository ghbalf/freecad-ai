# Dual-Era MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the MCP server we host inside FreeCAD to answer both the legacy `initialize` handshake era (`2025-03-26` … `2025-11-25`) and the stateless `2026-07-28` per-request `_meta` era from the same endpoint, without moving a byte of what legacy clients see today.

**Architecture:** A single ordered revision table in `protocol.py` is the only place a version literal appears; every set, era lookup and error message derives from it. `server.py` picks an era per request **by the shape of the request** — modern iff `params._meta` carries `io.modelcontextprotocol/protocolVersion` — and routes to one of two handler methods that share the tool plumbing. `transport.py` gains a module-level `validate_modern_headers()` (testable without a socket) plus an era-dependent HTTP status map, because a modern unknown method is a 404 while a legacy one stays 200-with-error.

**Tech Stack:** Python 3.11, stdlib only (no external dependencies — project rule), `unittest`-free plain-`pytest` tests, `http.server`.

**Spec:** `docs/superpowers/specs/2026-09-19-mcp-dual-era-server-design.md`

## Global Constraints

- **No external dependencies.** stdlib only, in both `freecad_ai/mcp/` and the tests.
- **No version literals outside the table.** After Task 1, `"2026-07-28"`, `"2025-11-25"`, `"2025-06-18"` and `"2025-03-26"` appear exactly once each in `freecad_ai/mcp/protocol.py`. Everything else derives from `PROTOCOL_REVISIONS`. Tests may name a literal when the literal *is* the assertion.
- **Legacy output does not move**, with exactly two deliberate exceptions the spec approves: an unsupported version is now `-32022` where it used to be `-32600` (Task 1/8), and `tools/list` is sorted by name in both eras (Task 6). Everything else a `_meta`-less request receives must be byte-identical to v0.28.0-alpha.
- **Exact `_meta` keys:** `io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientInfo`, `io.modelcontextprotocol/clientCapabilities`, `io.modelcontextprotocol/serverInfo`.
- **Exact error codes:** `-32020` HeaderMismatch, `-32021` MissingRequiredClientCapability, `-32022` UnsupportedProtocolVersion. (The numbering in the #64 issue body is an obsolete draft — do not copy it.)
- **`ttlMs` and `cacheScope` are REQUIRED** on `tools/list` in `2026-07-28`. "Opting out" is `ttlMs: 0`, never omission.
- **Test command:** `env PYTHONPATH= .venv/bin/pytest …` — a shell `PYTHONPATH` shadows the venv's pluggy and pytest crashes.
- **Full suite:** `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q` — that file Qt-segfaults on clean master too.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Sequencing Note

Task 1 widens `SUPPORTED_PROTOCOL_VERSIONS` to include `2026-07-28`. The transport reads that constant to decide what it accepts, so Task 1 also repoints that one check at `LEGACY_VERSIONS` — the same three revisions as before — keeping every intermediate tree green and behaviourally unchanged. Task 8 rewrites the check for real. **The branch is only conformant after Task 8**; do not merge or release before it. Work on a branch:

```bash
git checkout -b feat/64-dual-era-mcp-server
```

## File Structure

| File | Responsibility after this work |
|---|---|
| `freecad_ai/mcp/protocol.py` (modify) | Pure wire shapes: the revision table, era detection from a message, `_meta` key names, error codes, the modern result envelope, cache-hint *defaults*. Knows nothing about this application. |
| `freecad_ai/mcp/server.py` (modify) | Era routing, the two handler bodies, `server/discover`, and `resolve_cache_hints()` — application knowledge (config, env). |
| `freecad_ai/mcp/transport.py` (modify) | HTTP-level concerns only: mirrored-header validation and the era-dependent status map. |
| `freecad_ai/config.py` (modify) | Two new persisted fields. |
| `tests/unit/test_mcp_dual_era.py` (create) | Everything era-specific at the protocol/server layer. |
| `tests/unit/test_mcp_streamable_server.py` (modify) | The HTTP-level half: headers and status codes. |
| `tests/unit/test_protocol.py` (modify) | One existing test inverts; see Task 1. |

---

### Task 1: The revision table and era detection

**Files:**
- Modify: `freecad_ai/mcp/protocol.py:19-33`
- Modify: `freecad_ai/mcp/transport.py:919-926` (constant swap only — see Step 5)
- Modify: `tests/unit/test_protocol.py:157-160`
- Modify: `tests/unit/test_mcp_streamable_server.py:301-310`
- Test: `tests/unit/test_mcp_dual_era.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `MODERN: str`, `LEGACY: str`, `ProtocolRevision(NamedTuple)` with fields `version: str` and `era: str`, `PROTOCOL_REVISIONS: tuple[ProtocolRevision, ...]`, `SUPPORTED_PROTOCOL_VERSIONS: frozenset[str]`, `MODERN_VERSIONS: tuple[str, ...]`, `LEGACY_VERSIONS: tuple[str, ...]`, `LATEST_LEGACY_VERSION: str`, `DEFAULT_PROTOCOL_VERSION: str`, `era_of(version: str) -> str | None`, `request_protocol_version(msg: dict) -> str | None`, `is_modern_request(msg: dict) -> bool`, `unsupported_version_error(msg_id, requested: str | None, supported) -> dict`, the constants `META_PROTOCOL_VERSION`, `META_CLIENT_INFO`, `META_CLIENT_CAPABILITIES`, `META_SERVER_INFO`, `HEADER_MISMATCH = -32020`, `MISSING_REQUIRED_CLIENT_CAPABILITY = -32021`, `UNSUPPORTED_PROTOCOL_VERSION = -32022`, `DEFAULT_TOOLS_TTL_MS = 300000`, `CACHE_SCOPES = ("public", "private")`, `DEFAULT_CACHE_SCOPE = "private"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_dual_era.py`:

```python
"""Dual-era MCP: one endpoint answering the 2025-03-26 handshake and the
stateless 2026-07-28 per-request _meta era (#64 phase 3)."""

from freecad_ai.mcp import protocol


class TestRevisionTable:
    def test_every_derived_set_comes_from_the_table(self):
        """The table is the single source of truth; nothing may be hand-listed.

        A hand-maintained second list is how the server ends up advertising a
        revision it does not route, which is exactly the lie #64 exists to
        remove.
        """
        assert protocol.SUPPORTED_PROTOCOL_VERSIONS == frozenset(
            r.version for r in protocol.PROTOCOL_REVISIONS)
        assert protocol.MODERN_VERSIONS == tuple(
            r.version for r in protocol.PROTOCOL_REVISIONS
            if r.era == protocol.MODERN)
        assert protocol.LEGACY_VERSIONS == tuple(
            r.version for r in protocol.PROTOCOL_REVISIONS
            if r.era == protocol.LEGACY)

    def test_the_table_is_newest_first(self):
        """LATEST_LEGACY_VERSION is LEGACY_VERSIONS[0], so order is load-bearing."""
        versions = [r.version for r in protocol.PROTOCOL_REVISIONS]
        assert versions == sorted(versions, reverse=True)
        assert protocol.LATEST_LEGACY_VERSION == protocol.LEGACY_VERSIONS[0]

    def test_the_2026_redesign_is_the_modern_era(self):
        assert protocol.era_of("2026-07-28") == protocol.MODERN
        assert protocol.era_of("2025-11-25") == protocol.LEGACY
        assert protocol.era_of("2025-03-26") == protocol.LEGACY

    def test_an_unknown_revision_has_no_era(self):
        assert protocol.era_of("2027-05-01") is None

    def test_the_handshake_default_did_not_move(self):
        """Clients that send no version still get the revision they got before."""
        assert protocol.DEFAULT_PROTOCOL_VERSION == "2025-03-26"


class TestEraDetection:
    def test_a_request_without_meta_is_legacy(self):
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})

    def test_a_request_with_no_params_at_all_is_legacy(self):
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    def test_a_request_naming_a_version_in_meta_is_modern(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
               "params": {"_meta": {
                   protocol.META_PROTOCOL_VERSION: "2026-07-28"}}}
        assert protocol.is_modern_request(msg)
        assert protocol.request_protocol_version(msg) == "2026-07-28"

    def test_an_unknown_version_in_meta_is_still_modern(self):
        """Presence decides the era, not the value.

        Only a modern client sends this key. Treating an unservable version as
        legacy would silently answer a 2027 client with a 2025 handshake shape
        instead of telling it we cannot serve it.
        """
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
               "params": {"_meta": {
                   protocol.META_PROTOCOL_VERSION: "2027-05-01"}}}
        assert protocol.is_modern_request(msg)
        assert protocol.request_protocol_version(msg) == "2027-05-01"

    def test_a_meta_without_our_key_is_legacy(self):
        """_meta is a general extension point; other keys are not our signal."""
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
             "params": {"_meta": {"vendor.example/trace": "abc"}}})

    def test_a_non_dict_params_does_not_raise(self):
        """The endpoint is unauthenticated (#59); malformed input must not 500."""
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": []})
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
             "params": {"_meta": "nonsense"}})


class TestErrorCodes:
    def test_the_2026_numbering(self):
        """The #64 issue body carries an obsolete draft numbering (-32004…)."""
        assert protocol.HEADER_MISMATCH == -32020
        assert protocol.MISSING_REQUIRED_CLIENT_CAPABILITY == -32021
        assert protocol.UNSUPPORTED_PROTOCOL_VERSION == -32022

    def test_the_rejection_names_what_the_caller_may_use(self):
        """A 400 the client cannot act on is #60's failure mode again."""
        err = protocol.unsupported_version_error(
            7, "2027-05-01", protocol.MODERN_VERSIONS)
        assert err["id"] == 7
        assert err["error"]["code"] == protocol.UNSUPPORTED_PROTOCOL_VERSION
        assert err["error"]["data"]["requested"] == "2027-05-01"
        assert err["error"]["data"]["supported"] == list(protocol.MODERN_VERSIONS)
        for version in protocol.MODERN_VERSIONS:
            assert version in err["error"]["message"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q`
Expected: FAIL — `AttributeError: module 'freecad_ai.mcp.protocol' has no attribute 'PROTOCOL_REVISIONS'`.

- [ ] **Step 3: Replace the version block in `protocol.py`**

Replace lines 19-33 (`DEFAULT_PROTOCOL_VERSION` through the end of the `SUPPORTED_PROTOCOL_VERSIONS` literal and its comment) with:

```python
MODERN = "modern"
LEGACY = "legacy"


class ProtocolRevision(NamedTuple):
    """One MCP revision and the era whose shape it speaks."""

    version: str
    era: str


# Newest first — LATEST_LEGACY_VERSION reads LEGACY_VERSIONS[0]. Adding a
# future revision is one line here; nothing else in the codebase names a
# version literal.
PROTOCOL_REVISIONS = (
    ProtocolRevision("2026-07-28", MODERN),
    ProtocolRevision("2025-11-25", LEGACY),
    ProtocolRevision("2025-06-18", LEGACY),
    ProtocolRevision("2025-03-26", LEGACY),
)

SUPPORTED_PROTOCOL_VERSIONS = frozenset(r.version for r in PROTOCOL_REVISIONS)
MODERN_VERSIONS = tuple(r.version for r in PROTOCOL_REVISIONS if r.era == MODERN)
LEGACY_VERSIONS = tuple(r.version for r in PROTOCOL_REVISIONS if r.era == LEGACY)
LATEST_LEGACY_VERSION = LEGACY_VERSIONS[0]

# What a legacy client gets when it names no version. Not the newest revision
# we serve: 2025-03-26 is what every existing configuration negotiated, and
# moving this default would change their wire shape for no request of theirs.
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

_ERA_BY_VERSION = {r.version: r.era for r in PROTOCOL_REVISIONS}

# The reserved _meta namespace 2026-07-28 uses for protocol metadata. Spelled
# out because a typo here degrades silently to "legacy client".
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# Freshness hints for CacheableResult. Both fields are REQUIRED on tools/list,
# so "do not cache" is ttlMs=0, never omission.
DEFAULT_TOOLS_TTL_MS = 300000
CACHE_SCOPES = ("public", "private")
DEFAULT_CACHE_SCOPE = "private"


def era_of(version):
    """Return MODERN, LEGACY, or None for a revision we do not serve."""
    return _ERA_BY_VERSION.get(version)


def request_protocol_version(msg: dict):
    """Return the revision named in per-request ``_meta``, or None."""
    params = msg.get("params")
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    return meta.get(META_PROTOCOL_VERSION)


def is_modern_request(msg: dict) -> bool:
    """True when the request carries 2026-07-28 per-request metadata.

    Presence decides the era, not the value: only a modern client sends this
    key, so a request naming a revision we cannot serve is a modern request to
    refuse with UNSUPPORTED_PROTOCOL_VERSION — never a legacy request to
    answer in the old shape.
    """
    return request_protocol_version(msg) is not None


def unsupported_version_error(msg_id, requested, supported):
    """Build the -32022 a caller can act on: what it asked for, what we serve."""
    return make_error(
        msg_id, UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported MCP protocol version %r. This server speaks %s."
        % (requested, ", ".join(supported)),
        {"requested": requested, "supported": list(supported)})
```

Add `from typing import NamedTuple` to the imports. Add the three new error codes next to the existing JSON-RPC codes:

```python
# Renumbered in 2026-07-28. -32021 is defined but never raised by us: we
# require no client capability. It is here so the transport's status map can
# classify it as 400 the day a revision gives us a reason to send it.
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022
```

`make_error(id, code, message, data=None)` already takes `data` and attaches it only when it is not None (`protocol.py:60`), so `unsupported_version_error` needs no change there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q`
Expected: PASS.

- [ ] **Step 5: Keep the transport's accepted set where it was**

Widening `SUPPORTED_PROTOCOL_VERSIONS` silently widens what `_handle_streamable`
accepts, because line 919 reads that constant. Point it at the set that means
what it meant before, so this task changes no behaviour at all:

```python
                version = self.headers.get("MCP-Protocol-Version")
                if (version is not None
                        and version not in protocol.LEGACY_VERSIONS):
                    self._send_json(400, protocol.make_error(
                        None, protocol.INVALID_REQUEST,
                        "Unsupported MCP-Protocol-Version %r. This server "
                        "speaks %s." % (
                            version,
                            ", ".join(sorted(protocol.LEGACY_VERSIONS)))))
                    return
```

`LEGACY_VERSIONS` holds exactly the three revisions the old frozenset did, so
the wire behaviour is unchanged. Task 8 rewrites this block properly; this step
only stops Task 1 from changing a behaviour it has no business changing.

Then make `test_the_rejection_names_what_we_support` in
`tests/unit/test_mcp_streamable_server.py` loop over `protocol.LEGACY_VERSIONS`
instead of `protocol.SUPPORTED_PROTOCOL_VERSIONS` — it posts a
`2026-07-28` header, which that set now contains.

- [ ] **Step 6: Invert the now-false test in `test_protocol.py`**

`test_supported_protocol_versions_exclude_the_2026_redesign` asserted the debt this task pays off. Replace it with the assertion that replaces it:

```python
def test_supported_protocol_versions_include_the_2026_redesign():
    """Was excluded until #64 phase 3: we now route the modern era for real.

    The old test guarded against advertising a revision we did not serve. The
    guard that replaces it is test_every_derived_set_comes_from_the_table in
    test_mcp_dual_era.py — the advertised set cannot drift from the routed one
    because both read PROTOCOL_REVISIONS.
    """
    from freecad_ai.mcp import protocol

    assert "2026-07-28" in protocol.SUPPORTED_PROTOCOL_VERSIONS
    assert protocol.era_of("2026-07-28") == protocol.MODERN
```

- [ ] **Step 7: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS. Step 5 is what makes this true — without it the streamable
tests fail on a header the transport has silently started accepting.

- [ ] **Step 8: Commit**

```bash
git add freecad_ai/mcp/protocol.py freecad_ai/mcp/transport.py \
        tests/unit/test_mcp_dual_era.py tests/unit/test_protocol.py \
        tests/unit/test_mcp_streamable_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): a revision table with an era per protocol version (#64)

One ordered table replaces the hand-maintained version set. Era detection
reads the request shape (params._meta carrying the reserved protocolVersion
key), not the version string, so a revision we cannot serve is refused as a
modern request rather than silently answered in the legacy shape.

Nothing routes the modern era yet, so the transport keeps accepting exactly
the three revisions it accepted before — the widened set is not yet what the
HTTP layer gates on.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The modern result envelope

**Files:**
- Modify: `freecad_ai/mcp/protocol.py` (append after `unsupported_version_error`)
- Test: `tests/unit/test_mcp_dual_era.py`

**Interfaces:**
- Consumes: `META_SERVER_INFO` from Task 1.
- Produces: `modern_result(payload: dict, server_info: dict, ttl_ms: int | None = None, cache_scope: str | None = None) -> dict` — the `result` object only, not a full JSON-RPC response. Callers wrap it in `make_response(msg_id, …)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mcp_dual_era.py`:

```python
class TestModernResultEnvelope:
    _INFO = {"name": "FreeCAD AI", "version": "0.28.0-alpha"}

    def test_every_modern_result_declares_a_result_type(self):
        """resultType is required in 2026-07-28. We are always "complete":
        no tool of ours asks for input mid-call, so MRTR never applies."""
        result = protocol.modern_result({"tools": []}, self._INFO)
        assert result["resultType"] == "complete"

    def test_server_info_travels_in_meta(self):
        """The handshake is gone; per-result _meta is where identity lives now."""
        result = protocol.modern_result({"tools": []}, self._INFO)
        assert result["_meta"][protocol.META_SERVER_INFO] == self._INFO

    def test_the_payload_is_carried_through(self):
        result = protocol.modern_result(
            {"content": [{"type": "text", "text": "ok"}], "isError": False},
            self._INFO)
        assert result["content"] == [{"type": "text", "text": "ok"}]
        assert result["isError"] is False

    def test_cache_hints_are_omitted_when_not_asked_for(self):
        """tools/call is not a CacheableResult; emitting ttlMs there is noise."""
        result = protocol.modern_result({"isError": False}, self._INFO)
        assert "ttlMs" not in result
        assert "cacheScope" not in result

    def test_cache_hints_are_emitted_when_given(self):
        result = protocol.modern_result(
            {"tools": []}, self._INFO, ttl_ms=0, cache_scope="public")
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "public"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py::TestModernResultEnvelope -q`
Expected: FAIL — `module 'freecad_ai.mcp.protocol' has no attribute 'modern_result'`.

- [ ] **Step 3: Implement it**

```python
def modern_result(payload: dict, server_info: dict,
                  ttl_ms=None, cache_scope=None) -> dict:
    """Shape a ``result`` object for a modern (2026-07-28) request.

    ``resultType`` is required on every result. Ours is always ``complete``:
    the other value, ``input_required``, belongs to multi-round tool responses,
    and no FreeCAD tool asks the caller a question mid-call.

    ``ttl_ms``/``cache_scope`` are passed only for a CacheableResult — as of
    this revision, ``tools/list`` and ``server/discover``. Emitting them on
    ``tools/call`` would invite a client to cache a geometry mutation.
    """
    result = {"resultType": "complete"}
    result.update(payload)
    result["_meta"] = {META_SERVER_INFO: server_info}
    if ttl_ms is not None:
        result["ttlMs"] = ttl_ms
    if cache_scope is not None:
        result["cacheScope"] = cache_scope
    return result
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/protocol.py tests/unit/test_mcp_dual_era.py
git commit -m "$(cat <<'EOF'
feat(mcp): the 2026-07-28 result envelope (#64)

resultType plus serverInfo in per-result _meta, with the cache hints optional
so tools/call does not advertise a mutation as cacheable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Configurable cache hints

**Files:**
- Modify: `freecad_ai/config.py:470-500` (the MCP server field block)
- Modify: `freecad_ai/mcp/server.py` (add the resolver above the class)
- Test: `tests/unit/test_mcp_dual_era.py`

**Interfaces:**
- Consumes: `protocol.DEFAULT_TOOLS_TTL_MS`, `protocol.DEFAULT_CACHE_SCOPE`, `protocol.CACHE_SCOPES` from Task 1.
- Produces: `server.resolve_cache_hints(cfg=None) -> tuple[int, str]` returning `(ttl_ms, cache_scope)`; config fields `mcp_server_tools_ttl_ms: int` and `mcp_server_tools_cache_scope: str`.

**Why `server.py` and not `gui_server.py`:** the three sibling resolvers live in `gui_server.py`, but `mcp_server_entry.py:75` constructs `MCPServer(registry)` with no config in a headless STDIO process. Importing `gui_server` there would drag Qt into it. The resolver therefore lives beside its only consumer and loads config lazily, tolerating its absence.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_dual_era.py` (add `import os` and `from freecad_ai.mcp import server as server_mod` at the top of the file):

```python
class _Cfg:
    """Config stand-in: getattr-compatible, and nothing else is required."""

    def __init__(self, ttl=None, scope=None):
        if ttl is not None:
            self.mcp_server_tools_ttl_ms = ttl
        if scope is not None:
            self.mcp_server_tools_cache_scope = scope


class TestResolveCacheHints:
    def test_defaults_when_nothing_is_configured(self):
        assert server_mod.resolve_cache_hints(_Cfg()) == (
            protocol.DEFAULT_TOOLS_TTL_MS, protocol.DEFAULT_CACHE_SCOPE)

    def test_config_beats_the_default(self):
        assert server_mod.resolve_cache_hints(_Cfg(60000, "public")) == (
            60000, "public")

    def test_env_beats_config(self, monkeypatch):
        """Matches MCP_HOST / MCP_PORT / MCP_AUTH_TOKEN, so every documented
        command-line recipe keeps working the same way."""
        monkeypatch.setenv("MCP_TOOLS_TTL_MS", "0")
        monkeypatch.setenv("MCP_TOOLS_CACHE_SCOPE", "public")
        assert server_mod.resolve_cache_hints(_Cfg(60000, "private")) == (
            0, "public")

    def test_zero_is_honoured_not_treated_as_unset(self):
        """ttlMs=0 is the only way to say "do not cache" — the field is
        REQUIRED, so falling back to 300000 here would silently ignore the
        one setting a user reaches for."""
        assert server_mod.resolve_cache_hints(_Cfg(0, "private")) == (
            0, "private")

    def test_a_non_numeric_ttl_falls_back_and_warns(self, caplog):
        """Both fields are REQUIRED on the wire: serialising a bad value would
        break conformance for every client, not just for whoever set it."""
        with caplog.at_level("WARNING"):
            ttl, _ = server_mod.resolve_cache_hints(_Cfg("soon", "private"))
        assert ttl == protocol.DEFAULT_TOOLS_TTL_MS
        assert "soon" in caplog.text

    def test_a_negative_ttl_falls_back(self):
        assert server_mod.resolve_cache_hints(_Cfg(-1, "private"))[0] == \
            protocol.DEFAULT_TOOLS_TTL_MS

    def test_an_unknown_scope_falls_back_and_warns(self, caplog):
        with caplog.at_level("WARNING"):
            _, scope = server_mod.resolve_cache_hints(_Cfg(60000, "shared"))
        assert scope == protocol.DEFAULT_CACHE_SCOPE
        assert "shared" in caplog.text

    def test_no_config_at_all_still_resolves(self, monkeypatch):
        """mcp_server_entry.py builds MCPServer(registry) with no config."""
        monkeypatch.delenv("MCP_TOOLS_TTL_MS", raising=False)
        monkeypatch.delenv("MCP_TOOLS_CACHE_SCOPE", raising=False)
        ttl, scope = server_mod.resolve_cache_hints()
        assert isinstance(ttl, int)
        assert scope in protocol.CACHE_SCOPES


def test_the_config_defaults_match_the_protocol_defaults():
    """config.py cannot import freecad_ai.mcp, so its defaults are literals.
    This is the only thing keeping the two copies from drifting."""
    from freecad_ai.config import AppConfig

    cfg = AppConfig()
    assert cfg.mcp_server_tools_ttl_ms == protocol.DEFAULT_TOOLS_TTL_MS
    assert cfg.mcp_server_tools_cache_scope == protocol.DEFAULT_CACHE_SCOPE
```

If the config class is not named `AppConfig`, use whatever `load_config()` returns — check `freecad_ai/config.py:775` and fix the import.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q -k "CacheHints or config_defaults"`
Expected: FAIL — no `resolve_cache_hints`.

- [ ] **Step 3: Add the config fields**

In the MCP server block of the config dataclass (near `mcp_server_auth_token`):

```python
    # tools/list freshness hints (2026-07-28 CacheableResult). Both fields are
    # REQUIRED on the wire, so "do not cache" is ttl 0, not an empty value.
    # Defaults duplicated from freecad_ai.mcp.protocol: config.py must not
    # import the mcp package. A test pins the two together.
    mcp_server_tools_ttl_ms: int = 300000
    mcp_server_tools_cache_scope: str = "private"
```

- [ ] **Step 4: Add the resolver to `server.py`**

Above the `MCPServer` class, with `import logging`, `import os` and `logger = logging.getLogger(__name__)` if they are not already there:

```python
def _coerce_ttl(value, fallback):
    if value is None or value == "":
        return fallback
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric MCP tools TTL %r", value)
        return fallback
    if ttl < 0:
        logger.warning("Ignoring negative MCP tools TTL %r", value)
        return fallback
    return ttl


def _coerce_scope(value, fallback):
    if value is None or value == "":
        return fallback
    if value not in protocol.CACHE_SCOPES:
        logger.warning("Ignoring unknown MCP cacheScope %r (expected %s)",
                       value, " or ".join(protocol.CACHE_SCOPES))
        return fallback
    return value


def resolve_cache_hints(cfg=None):
    """Return ``(ttl_ms, cache_scope)`` for tools/list: env beats config,
    config beats defaults — the same precedence as MCP_HOST / MCP_PORT.

    A malformed value falls back and warns instead of reaching the wire.
    Both fields are REQUIRED in 2026-07-28, so serialising nonsense would
    break conformance for every client rather than only for whoever set it.

    ``cfg`` is loaded lazily and its absence is survivable: the STDIO entry
    point builds MCPServer(registry) in a headless process where the config
    layer may not be importable at all.
    """
    ttl = protocol.DEFAULT_TOOLS_TTL_MS
    scope = protocol.DEFAULT_CACHE_SCOPE

    if cfg is None:
        try:
            from ..config import get_config
            cfg = get_config()
        except Exception:
            logger.debug("No config available; using default MCP cache hints")

    if cfg is not None:
        ttl = _coerce_ttl(getattr(cfg, "mcp_server_tools_ttl_ms", None), ttl)
        scope = _coerce_scope(
            getattr(cfg, "mcp_server_tools_cache_scope", None), scope)

    ttl = _coerce_ttl(os.environ.get("MCP_TOOLS_TTL_MS"), ttl)
    scope = _coerce_scope(os.environ.get("MCP_TOOLS_CACHE_SCOPE"), scope)
    return ttl, scope
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/config.py freecad_ai/mcp/server.py tests/unit/test_mcp_dual_era.py
git commit -m "$(cat <<'EOF'
feat(mcp): make the tools/list cache hints configurable (#64)

ttlMs and cacheScope through config.json or MCP_TOOLS_TTL_MS /
MCP_TOOLS_CACHE_SCOPE, env beating config as everywhere else. A malformed
value warns and falls back: both fields are REQUIRED on the wire, so a bad
one would break every client rather than only the one who set it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Era routing, the `initialize` echo, and the era split on `ping`

**Files:**
- Modify: `freecad_ai/mcp/server.py:19-69` (the constant and `_handle`)
- Test: `tests/unit/test_mcp_dual_era.py`

**Interfaces:**
- Consumes: `protocol.is_modern_request`, `protocol.request_protocol_version`, `protocol.MODERN_VERSIONS`, `protocol.LEGACY_VERSIONS`, `protocol.LATEST_LEGACY_VERSION`, `protocol.unsupported_version_error` (Task 1); `resolve_cache_hints` (Task 3).
- Produces: `MCPServer.__init__(self, registry, transport=None, executor=None, cache_hints=None)` storing `self._cache_hints: tuple[int, str]`; `MCPServer._handle_legacy(msg_id, method, params) -> dict | None`; `MCPServer._handle_modern(msg_id, method, params) -> dict | None`; `MCPServer._unknown_method(msg_id, method) -> dict | None`. Task 5 adds `_handle_discover`; Task 6 changes `_tools_schema` and `_handle_tool_call`.

**Correction to the approved sketch:** the `initialize` echo clamps to `LEGACY_VERSIONS`, not `SUPPORTED_PROTOCOL_VERSIONS`. `initialize` exists only in the legacy era, so echoing `2026-07-28` back over a handshake would promise a wire shape that revision does not have — the handshake is exactly what it deleted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_dual_era.py` (add `import pytest` and `from freecad_ai.tools.registry import ToolRegistry` at the top):

```python
def _server(registry=None, **kw):
    kw.setdefault("cache_hints", (300000, "private"))
    return server_mod.MCPServer(registry or ToolRegistry(), **kw)


def _modern(method, msg_id=1, version="2026-07-28", **params):
    params["_meta"] = {protocol.META_PROTOCOL_VERSION: version}
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}


def _legacy(method, msg_id=1, **params):
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}


class TestInitializeEcho:
    def test_a_client_naming_no_version_gets_the_historical_default(self):
        resp = _server()._handle(_legacy("initialize", capabilities={}))
        assert resp["result"]["protocolVersion"] == protocol.DEFAULT_PROTOCOL_VERSION

    @pytest.mark.parametrize("version", protocol.LEGACY_VERSIONS)
    def test_every_legacy_version_we_speak_is_echoed(self, version):
        """Replying 2025-03-26 to a 2025-11-25 client made it negotiate down
        for no reason: we speak its revision, we just never said so.

        Parametrised over the table rather than a fixed three, so adding a
        legacy revision cannot leave this test behind."""
        resp = _server()._handle(
            _legacy("initialize", protocolVersion=version))
        assert resp["result"]["protocolVersion"] == version

    def test_an_unknown_version_gets_the_newest_legacy_revision(self):
        resp = _server()._handle(
            _legacy("initialize", protocolVersion="2019-01-01"))
        assert resp["result"]["protocolVersion"] == protocol.LATEST_LEGACY_VERSION

    def test_a_modern_version_over_the_handshake_is_not_echoed(self):
        """initialize exists in no modern revision. Echoing 2026-07-28 here
        would promise the very shape that revision deleted."""
        resp = _server()._handle(
            _legacy("initialize", protocolVersion="2026-07-28"))
        assert resp["result"]["protocolVersion"] == protocol.LATEST_LEGACY_VERSION

    def test_the_rest_of_the_handshake_is_unchanged(self):
        result = _server()._handle(_legacy("initialize"))["result"]
        assert result["capabilities"] == {"tools": {}}
        assert result["serverInfo"] == server_mod.SERVER_INFO


class TestEraRouting:
    def test_initialize_is_gone_in_the_modern_era(self):
        """2026-07-28 removed the handshake; answering it would tell a modern
        client we are something we are not."""
        resp = _server()._handle(_modern("initialize"))
        assert resp["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_ping_is_gone_in_the_modern_era(self):
        resp = _server()._handle(_modern("ping"))
        assert resp["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_ping_still_answers_a_legacy_client(self):
        assert _server()._handle(_legacy("ping"))["result"] == {}

    def test_an_unservable_modern_version_is_refused(self):
        resp = _server()._handle(_modern("tools/list", version="2027-05-01"))
        assert resp["error"]["code"] == protocol.UNSUPPORTED_PROTOCOL_VERSION
        assert resp["error"]["data"]["supported"] == list(protocol.MODERN_VERSIONS)

    def test_a_legacy_version_named_in_meta_is_refused_not_downgraded(self):
        """_meta means the client speaks the modern era. A legacy revision
        there is a contradiction, and answering it in the legacy shape would
        hide a client bug behind output that looks fine."""
        resp = _server()._handle(_modern("tools/list", version="2025-03-26"))
        assert resp["error"]["code"] == protocol.UNSUPPORTED_PROTOCOL_VERSION

    def test_an_unservable_version_on_a_notification_is_silent(self):
        """A notification has no id; JSON-RPC forbids answering it at all."""
        msg = _modern("notifications/whatever", version="2027-05-01")
        del msg["id"]
        assert _server()._handle(msg) is None

    def test_the_initialized_notification_is_still_silent(self):
        assert _server()._handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_an_unknown_legacy_method_still_errors(self):
        resp = _server()._handle(_legacy("nonsense/method"))
        assert resp["error"]["code"] == protocol.METHOD_NOT_FOUND
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q -k "InitializeEcho or EraRouting"`
Expected: FAIL — `initialize` currently replies with the hardcoded `PROTOCOL_VERSION` in both eras, and `_handle` has no `cache_hints` keyword.

- [ ] **Step 3: Rewrite the routing**

Replace `server.py:19` and the whole of `_handle` (lines 36-69):

```python
# Kept as the name the rest of the tree already imports; the value now lives
# in the revision table.
PROTOCOL_VERSION = protocol.DEFAULT_PROTOCOL_VERSION
```

```python
    def __init__(self, registry: ToolRegistry, transport=None, executor=None,
                 cache_hints=None):
        self._registry = registry
        self._transport = transport
        self._executor = executor
        # Resolved once: a per-request config read would put a JSON file in
        # the path of every tools/list, and these values cannot change without
        # a restart anyway.
        self._cache_hints = cache_hints or resolve_cache_hints()

    def _handle(self, msg: dict) -> dict | None:
        """Route a JSON-RPC message, choosing an era by the request's shape.

        2026-07-28 removed the handshake, so there is no negotiated state to
        consult: each request says which era it speaks, or says nothing and is
        legacy. One endpoint serving both is what the spec calls a dual-era
        server, and it is why existing clients see no change at all.
        """
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if not protocol.is_modern_request(msg):
            return self._handle_legacy(msg_id, method, params)

        version = protocol.request_protocol_version(msg)
        if version not in protocol.MODERN_VERSIONS:
            if msg_id is None:
                return None
            return protocol.unsupported_version_error(
                msg_id, version, protocol.MODERN_VERSIONS)
        return self._handle_modern(msg_id, method, params)

    def _handle_legacy(self, msg_id, method: str, params: dict) -> dict | None:
        """The 2025-era handshake world. Output here must not move."""
        if method == "initialize":
            # Echo the client's revision when we speak it. Replying with a
            # fixed 2025-03-26 made every newer client negotiate down for no
            # reason. Clamped to the legacy era: initialize exists in no
            # modern revision, so echoing one would promise a shape that
            # revision deleted.
            want = params.get("protocolVersion")
            speak = (want if want in protocol.LEGACY_VERSIONS
                     else protocol.LATEST_LEGACY_VERSION)
            return protocol.make_response(msg_id, {
                "protocolVersion": speak,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })

        if method == "notifications/initialized":
            return None  # Notification, no response

        if method == "tools/list":
            return protocol.make_response(msg_id, {
                "tools": self._tools_schema(),
            })

        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)

        if method == "ping":
            return protocol.make_response(msg_id, {})

        return self._unknown_method(msg_id, method)

    def _handle_modern(self, msg_id, method: str, params: dict) -> dict | None:
        """The 2026-07-28 stateless world.

        initialize, notifications/initialized, ping and logging/setLevel are
        all gone from this revision, so they fall through to METHOD_NOT_FOUND
        — which the transport renders as a 404, not a 200.
        """
        if method == "tools/list":
            ttl, scope = self._cache_hints
            return protocol.make_response(msg_id, protocol.modern_result(
                {"tools": self._tools_schema()}, SERVER_INFO,
                ttl_ms=ttl, cache_scope=scope))

        if method == "tools/call":
            # No `modern=True` yet: the keyword arrives in Task 6, which also
            # updates this call site. Passing it now would be a TypeError.
            return self._handle_tool_call(msg_id, params)

        return self._unknown_method(msg_id, method)

    def _unknown_method(self, msg_id, method: str) -> dict | None:
        if msg_id is None:
            return None  # Unknown notification, ignore
        return protocol.make_error(
            msg_id, protocol.METHOD_NOT_FOUND,
            f"Method not found: {method}",
        )

    def _tools_schema(self):
        """The registry's tools in MCP schema form."""
        return self._registry.to_mcp_schema()
```

`_tools_schema()` is a passthrough for now; Task 6 gives it the sort. Introducing it here keeps both eras reading one place.

Also update the module docstring: it says "Handles initialize, tools/list, tools/call, and ping requests" — replace with a sentence naming both eras.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py tests/unit/test_mcp_server.py -q`
Expected: PASS — including the existing `test_mcp_server.py`, which is the check that legacy output did not move.

- [ ] **Step 5: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/mcp/server.py tests/unit/test_mcp_dual_era.py
git commit -m "$(cat <<'EOF'
feat(mcp): route each request by its era (#64)

A request carrying per-request _meta is served the 2026-07-28 way; one
without it gets exactly what it got before. initialize and ping are legacy
only — 2026-07-28 deleted both, so answering them would misrepresent us.

initialize now echoes the client's revision when we speak it, clamped to the
legacy era, instead of replying 2025-03-26 to everyone.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `server/discover`

**Files:**
- Modify: `freecad_ai/mcp/server.py` (add `_handle_discover`, route it in both eras)
- Test: `tests/unit/test_mcp_dual_era.py`

**Interfaces:**
- Consumes: `protocol.modern_result` (Task 2), `self._cache_hints` (Task 4).
- Produces: `MCPServer._handle_discover(msg_id) -> dict`; module constant `DISCOVER_INSTRUCTIONS: str`.

**Why it answers in both eras:** `server/discover` is how a modern client learns what we speak without a handshake, so a server that only answered it *after* being addressed in the modern era would be useless. It exists in no legacy revision, so there is no legacy shape to hold still — it always returns the modern-shaped `DiscoverResult`, in both eras.

- [ ] **Step 1: Write the failing tests**

```python
class TestServerDiscover:
    def test_it_advertises_only_the_modern_era(self):
        """supportedVersions answers "what can you be addressed as, now".
        Listing the legacy revisions would invite a modern client to send
        _meta naming one, which Task 4 refuses."""
        result = _server()._handle(_modern("server/discover"))["result"]
        assert result["supportedVersions"] == list(protocol.MODERN_VERSIONS)

    def test_it_is_answerable_before_any_era_is_declared(self):
        """A modern client has no handshake to announce itself with, so
        discover must work from a bare request or it cannot bootstrap."""
        result = _server()._handle(_legacy("server/discover"))["result"]
        assert result["supportedVersions"] == list(protocol.MODERN_VERSIONS)
        assert result["resultType"] == "complete"

    def test_it_reports_our_capabilities_and_identity(self):
        result = _server()._handle(_modern("server/discover"))["result"]
        assert result["capabilities"] == {"tools": {}}
        assert result["_meta"][protocol.META_SERVER_INFO] == server_mod.SERVER_INFO

    def test_it_is_cacheable(self):
        result = _server(cache_hints=(60000, "public"))._handle(
            _modern("server/discover"))["result"]
        assert result["ttlMs"] == 60000
        assert result["cacheScope"] == "public"

    def test_it_carries_instructions(self):
        result = _server()._handle(_modern("server/discover"))["result"]
        assert "FreeCAD" in result["instructions"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py::TestServerDiscover -q`
Expected: FAIL — `server/discover` currently falls through to METHOD_NOT_FOUND, so `resp["result"]` raises `KeyError`.

- [ ] **Step 3: Implement it**

Beside `SERVER_INFO`:

```python
# Shown to a model before it picks a tool. Short on purpose: it is prepended
# to a context window that the tool schemas already fill.
DISCOVER_INSTRUCTIONS = (
    "Tools for inspecting and modifying geometry in a running FreeCAD "
    "session. Every call acts on the document that is open right now.")
```

Add the method:

```python
    def _handle_discover(self, msg_id) -> dict:
        """Answer server/discover — how a modern client bootstraps.

        Always the modern DiscoverResult shape, in either era: the method
        exists in no legacy revision, so there is no older shape to preserve,
        and a client that has not yet learned what we speak cannot be expected
        to address us correctly first.
        """
        ttl, scope = self._cache_hints
        return protocol.make_response(msg_id, protocol.modern_result({
            "supportedVersions": list(protocol.MODERN_VERSIONS),
            "capabilities": {"tools": {}},
            "instructions": DISCOVER_INSTRUCTIONS,
        }, SERVER_INFO, ttl_ms=ttl, cache_scope=scope))
```

Route it in both handlers, above their `_unknown_method` fall-through:

```python
        if method == "server/discover":
            return self._handle_discover(msg_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/server.py tests/unit/test_mcp_dual_era.py
git commit -m "$(cat <<'EOF'
feat(mcp): implement server/discover (#64)

The bootstrap a stateless client needs in place of the handshake: which
revisions we serve, what we can do, who we are. Answered in either era —
it exists in no legacy revision, so there is no older shape to keep, and a
client that has not discovered us yet cannot address us correctly first.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Modern result shapes on `tools/list` and `tools/call`

**Files:**
- Modify: `freecad_ai/mcp/server.py` (`_tools_schema`, `_handle_tool_call`)
- Test: `tests/unit/test_mcp_dual_era.py`

**Interfaces:**
- Consumes: `protocol.modern_result` (Task 2).
- Produces: `MCPServer._handle_tool_call(msg_id, params: dict, modern: bool = False) -> dict`; `_tools_schema()` now returns the list sorted by `name`.

- [ ] **Step 1: Write the failing tests**

```python
def _registry_with(*names):
    from freecad_ai.tools.registry import ToolDefinition, ToolResult

    reg = ToolRegistry()
    for name in names:
        reg.register(ToolDefinition(
            name, "does %s" % name, [],
            handler=lambda _n=name: ToolResult(True, "ran %s" % _n)))
    return reg


class TestToolsListShape:
    def test_tools_are_sorted_by_name_in_both_eras(self):
        """A SHOULD from 2025-11-25, and per #47 a stable prefix is what lets
        a provider's prompt cache hit: registration order is an accident of
        import order, so it moves when nothing about the tools has."""
        reg = _registry_with("zeta", "alpha", "mid")
        for msg in (_legacy("tools/list"), _modern("tools/list")):
            names = [t["name"] for t in
                     _server(reg)._handle(msg)["result"]["tools"]]
            assert names == ["alpha", "mid", "zeta"]

    def test_the_legacy_result_gains_nothing(self):
        """Byte-identical to v0.28.0-alpha apart from the ordering above."""
        result = _server(_registry_with("a"))._handle(_legacy("tools/list"))["result"]
        assert set(result) == {"tools"}

    def test_the_modern_result_is_a_cacheable_result(self):
        result = _server(_registry_with("a"), cache_hints=(0, "public"))._handle(
            _modern("tools/list"))["result"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "public"
        assert result["_meta"][protocol.META_SERVER_INFO] == server_mod.SERVER_INFO
        assert [t["name"] for t in result["tools"]] == ["a"]


class TestToolsCallShape:
    def test_the_legacy_result_gains_nothing(self):
        result = _server(_registry_with("a"))._handle(
            _legacy("tools/call", name="a", arguments={}))["result"]
        assert set(result) == {"content", "isError"}
        assert result["isError"] is False

    def test_the_modern_result_declares_its_type(self):
        result = _server(_registry_with("a"))._handle(
            _modern("tools/call", name="a", arguments={}))["result"]
        assert result["resultType"] == "complete"
        assert result["content"][0]["text"] == "ran a"
        assert result["_meta"][protocol.META_SERVER_INFO] == server_mod.SERVER_INFO

    def test_a_modern_call_is_never_advertised_as_cacheable(self):
        """tools/call mutates a document. A ttlMs here would invite a client
        to replay a stale answer for a model that has since changed."""
        result = _server(_registry_with("a"))._handle(
            _modern("tools/call", name="a", arguments={}))["result"]
        assert "ttlMs" not in result
        assert "cacheScope" not in result

    def test_a_failing_modern_call_still_carries_the_envelope(self):
        result = _server(_registry_with("a"))._handle(
            _modern("tools/call", name="missing", arguments={}))["result"]
        assert result["isError"] is True
        assert result["resultType"] == "complete"
```

`resultType` stays `"complete"` on a failed call: the tool ran and produced an answer. `isError` reports the tool's verdict; `resultType` reports the round-trip's.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q -k "ToolsListShape or ToolsCallShape"`
Expected: FAIL — unsorted names, and no `resultType` on a modern `tools/call`.

- [ ] **Step 3: Implement it**

```python
    def _tools_schema(self):
        """The registry's tools, sorted by name.

        to_mcp_schema() yields registration order, which is import order —
        it moves when nothing about the tools has. A SHOULD since 2025-11-25,
        and per #47 a stable prefix is what a provider's prompt cache needs.
        """
        return sorted(self._registry.to_mcp_schema(), key=lambda t: t["name"])
```

First, update the call site in `_handle_modern` to pass the new keyword:

```python
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params, modern=True)
```

Then the handler itself:

```python
    def _handle_tool_call(self, msg_id, params: dict, modern: bool = False) -> dict:
        """Execute a tool and return the result in MCP format."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if self._executor:
            result = self._executor.execute(tool_name, arguments)
        else:
            result = self._registry.execute(tool_name, arguments)

        if result.success:
            content = [{"type": "text", "text": result.output}]
            if result.data:
                content.append({"type": "text", "text": str(result.data)})
            payload = {"content": content, "isError": False}
        else:
            payload = {
                "content": [{"type": "text",
                             "text": result.error or "Unknown error"}],
                "isError": True,
            }

        if modern:
            # No cache hints: a tool call mutates the document, and a client
            # replaying a cached answer would be acting on a model that has
            # since changed. resultType stays "complete" even when isError —
            # the call finished; isError is the tool's verdict, not the
            # round-trip's.
            return protocol.make_response(
                msg_id, protocol.modern_result(payload, SERVER_INFO))
        return protocol.make_response(msg_id, payload)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS. If `test_mcp_server.py::test_tools_list_exposes_registered_tools` fails on ordering, that is the sort working as designed — but it registers one tool, so it should not.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/server.py tests/unit/test_mcp_dual_era.py
git commit -m "$(cat <<'EOF'
feat(mcp): modern result shapes for tools/list and tools/call (#64)

tools/list becomes a CacheableResult in the modern era and is sorted by name
in both — registration order is import order, and #47 wants a stable prefix.
tools/call declares resultType but carries no cache hints: it mutates the
document, and a replayed answer would describe a model that has changed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Mirrored-header validation

**Files:**
- Modify: `freecad_ai/mcp/transport.py` (add two module-level functions near the top, after `logger`)
- Test: `tests/unit/test_mcp_dual_era.py`

**Interfaces:**
- Consumes: `protocol.request_protocol_version`, `protocol.HEADER_MISMATCH`, `protocol.make_error` (Task 1).
- Produces: `transport.validate_modern_headers(headers, msg) -> dict | None` — a JSON-RPC error object on a mismatch, `None` when the headers agree with the body; `transport._decode_header_value(raw: str | None) -> str | None`.

**Why module level:** `RequestHandler` is defined inside a method of `HTTPServerTransport` and returned to `ThreadedHTTPServer`. Nothing can reach a method of it without binding a socket. A free function is the difference between testing this logic in microseconds and testing it through a TCP round trip.

`headers` is whatever exposes a case-insensitive `.get()` — in production `self.headers`, an `email.message.Message`. The function must use only `.get()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_dual_era.py` (add `import base64`, `from email.message import Message`, `from freecad_ai.mcp import transport as transport_mod`):

```python
def _headers(mapping):
    """A case-insensitive header object, as http.server hands the handler."""
    msg = Message()
    for key, value in mapping.items():
        msg[key] = value
    return msg


class TestModernHeaderValidation:
    _CALL = _modern("tools/call", name="create_box", arguments={})

    def _ok_headers(self, **overrides):
        base = {"MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "create_box"}
        base.update(overrides)
        return _headers(base)

    def test_agreeing_headers_pass(self):
        assert transport_mod.validate_modern_headers(
            self._ok_headers(), self._CALL) is None

    def test_header_casing_does_not_matter(self):
        """Clients and proxies normalise header case freely."""
        assert transport_mod.validate_modern_headers(
            _headers({"mcp-protocol-version": "2026-07-28",
                      "mcp-method": "tools/call",
                      "mcp-name": "create_box"}), self._CALL) is None

    def test_a_disagreeing_version_is_rejected(self):
        """The headers mirror the body so an intermediary can route without
        parsing it. A mismatch means the two readers see different requests —
        a routing bug at best and a bypass at worst, so the spec makes it a
        MUST-reject rather than a preference for the body."""
        err = transport_mod.validate_modern_headers(
            self._ok_headers(**{"MCP-Protocol-Version": "2025-03-26"}),
            self._CALL)
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    def test_a_missing_version_header_is_rejected(self):
        err = transport_mod.validate_modern_headers(
            _headers({"Mcp-Method": "tools/call", "Mcp-Name": "create_box"}),
            self._CALL)
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    def test_a_disagreeing_method_is_rejected(self):
        err = transport_mod.validate_modern_headers(
            self._ok_headers(**{"Mcp-Method": "tools/list"}), self._CALL)
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    def test_a_disagreeing_tool_name_is_rejected(self):
        """The one that matters: Mcp-Name is what a policy layer in front of
        us would allow or deny on, so a body naming a different tool is how
        such a layer gets walked past."""
        err = transport_mod.validate_modern_headers(
            self._ok_headers(**{"Mcp-Name": "read_document"}), self._CALL)
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    def test_a_missing_name_on_tools_call_is_rejected(self):
        err = transport_mod.validate_modern_headers(
            _headers({"MCP-Protocol-Version": "2026-07-28",
                      "Mcp-Method": "tools/call"}), self._CALL)
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    def test_no_name_header_is_required_off_tools_call(self):
        msg = _modern("tools/list")
        assert transport_mod.validate_modern_headers(
            _headers({"MCP-Protocol-Version": "2026-07-28",
                      "Mcp-Method": "tools/list"}), msg) is None

    def test_a_base64_encoded_name_is_decoded_before_comparing(self):
        """The sentinel encoding exists for values that cannot go in a header
        raw. Comparing it undecoded would reject every client that uses it."""
        encoded = "=?base64?%s?=" % base64.b64encode(b"create_box").decode()
        assert transport_mod.validate_modern_headers(
            self._ok_headers(**{"Mcp-Name": encoded}), self._CALL) is None

    def test_undecodable_base64_is_rejected_not_crashed(self):
        """This endpoint is unauthenticated (#59)."""
        err = transport_mod.validate_modern_headers(
            self._ok_headers(**{"Mcp-Name": "=?base64?!!!notb64!!!?="}),
            self._CALL)
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    def test_the_error_carries_the_request_id(self):
        err = transport_mod.validate_modern_headers(
            self._ok_headers(**{"Mcp-Method": "tools/list"}), self._CALL)
        assert err["id"] == self._CALL["id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py::TestModernHeaderValidation -q`
Expected: FAIL — no `validate_modern_headers`.

- [ ] **Step 3: Implement them**

In `transport.py`, after `logger = logging.getLogger(__name__)`, and add `import base64` to the imports:

```python
def _decode_header_value(raw):
    """Decode the ``=?base64?…?=`` sentinel a mirrored header value may use.

    2026-07-28 defines it for values that cannot travel in a header raw — a
    tool name with a non-ASCII character, say. Returns None when the payload
    will not decode, which the caller treats as a mismatch: a value we cannot
    read is not a value we can confirm agrees with the body.
    """
    if raw is None:
        return None
    if raw.startswith("=?base64?") and raw.endswith("?="):
        try:
            return base64.b64decode(raw[len("=?base64?"):-len("?=")],
                                    validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return raw


def validate_modern_headers(headers, msg):
    """Return a JSON-RPC error when the mirrored headers disagree with the
    body, or None when they agree.

    2026-07-28 mirrors selected body fields into headers so an intermediary
    can route, authorize or rate-limit without parsing the body. That only
    holds if the two always say the same thing: a request whose header names
    one tool and whose body names another is how a policy layer in front of
    this server gets walked past. So a mismatch is a MUST-reject, not a
    preference for one source over the other.

    Module level, not a method of the nested RequestHandler: that class is
    built inside HTTPServerTransport._make_server() and cannot be reached
    without binding a socket.
    """
    msg_id = msg.get("id")

    def mismatch(text):
        return protocol.make_error(msg_id, protocol.HEADER_MISMATCH, text)

    version = protocol.request_protocol_version(msg)
    header_version = headers.get("MCP-Protocol-Version")
    if header_version is None:
        return mismatch("Missing required MCP-Protocol-Version header.")
    if header_version != version:
        return mismatch(
            "Header mismatch: MCP-Protocol-Version %r does not match the %r "
            "in params._meta." % (header_version, version))

    method = msg.get("method", "")
    header_method = headers.get("Mcp-Method")
    if header_method is None:
        return mismatch("Missing required Mcp-Method header.")
    if header_method != method:
        return mismatch(
            "Header mismatch: Mcp-Method %r does not match the body's method "
            "%r." % (header_method, method))

    if method == "tools/call":
        raw_name = headers.get("Mcp-Name")
        if raw_name is None:
            return mismatch("Missing required Mcp-Name header on tools/call.")
        wanted = (msg.get("params") or {}).get("name")
        decoded_name = _decode_header_value(raw_name)
        # A decode failure is a mismatch on its own. Comparing the two
        # directly would call it agreement when the body names no tool
        # either: None from "unreadable" is not None from "absent".
        if decoded_name is None or decoded_name != wanted:
            return mismatch(
                "Header mismatch: Mcp-Name %r does not name the tool the body "
                "calls (%r)." % (raw_name, wanted))

    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_dual_era.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_dual_era.py
git commit -m "$(cat <<'EOF'
feat(mcp): validate the 2026-07-28 mirrored headers (#64)

The headers exist so an intermediary can route without parsing the body,
which only holds while the two agree. A header naming one tool over a body
calling another is how a policy layer in front of this server gets walked
past, so a mismatch is rejected rather than resolved in the body's favour.

Module level, because RequestHandler lives inside a method and cannot be
reached without a socket. Nothing calls this yet.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Wire the transport — check order and era-dependent status

**Files:**
- Modify: `freecad_ai/mcp/transport.py:894-973` (`_handle_streamable`)
- Modify: `tests/unit/test_mcp_streamable_server.py:301-310` (the rejection test)
- Test: `tests/unit/test_mcp_streamable_server.py`

**Interfaces:**
- Consumes: `validate_modern_headers` (Task 7), `protocol.is_modern_request`, `protocol.unsupported_version_error`, `protocol.LEGACY_VERSIONS`, the three new error codes (Task 1).
- Produces: module constant `MODERN_ERROR_STATUS: dict[int, int]`.

**Two changes, both forced by the spec:**

1. **Check order.** The version header is currently validated *before* the body is read (line ~917). The modern rule is that the header must *match* the body, so the body has to be parsed first. This retires the HTTP/1.0 keep-alive caveat in the comment there: after the reorder the body is always drained before any 400.
2. **Status mapping.** Legacy answers 200-with-error, always. Modern maps `-32601` to **404** and the three `-3202x` codes to **400**. This is era-dependent, so it cannot be a single table applied to every response.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_streamable_server.py` (add `from freecad_ai.mcp.transport import validate_modern_headers  # noqa: F401` only if you assert on it; otherwise no new imports beyond `protocol`, already there):

```python
def _dual_era_handler(msg):
    """A handler with the shape the real server has after #64."""
    msg_id = msg.get("id")
    if msg_id is None:
        return None
    if protocol.is_modern_request(msg):
        version = protocol.request_protocol_version(msg)
        if version not in protocol.MODERN_VERSIONS:
            return protocol.unsupported_version_error(
                msg_id, version, protocol.MODERN_VERSIONS)
        if msg.get("method") == "tools/list":
            return protocol.make_response(msg_id, protocol.modern_result(
                {"tools": []}, {"name": "t", "version": "0"},
                ttl_ms=0, cache_scope="private"))
        return protocol.make_error(msg_id, protocol.METHOD_NOT_FOUND,
                                   msg.get("method"))
    if msg.get("method") == "ping":
        return protocol.make_response(msg_id, {})
    return protocol.make_error(msg_id, protocol.METHOD_NOT_FOUND,
                               msg.get("method"))


def _modern_body(method, **params):
    params["_meta"] = {protocol.META_PROTOCOL_VERSION: "2026-07-28"}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def _modern_headers(method, name=None, version="2026-07-28"):
    headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


class TestDualEraStatusCodes:
    def test_a_modern_request_is_served_from_the_same_endpoint(self):
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("tools/list"),
                headers=_modern_headers("tools/list"))

        assert status == 200
        assert json.loads(body)["result"]["resultType"] == "complete"

    def test_an_unknown_modern_method_is_a_404(self):
        """2026-07-28 maps method-not-found onto HTTP, so an intermediary can
        see it without parsing the body. Legacy keeps answering 200."""
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("nonsense/method"),
                headers=_modern_headers("nonsense/method"))

        assert status == 404
        assert json.loads(body)["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_an_unknown_legacy_method_stays_a_200(self):
        """Changing this would break every client this work is not about."""
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, {"jsonrpc": "2.0", "id": 1, "method": "nope"})

        assert status == 200
        assert json.loads(body)["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_an_unservable_modern_version_is_a_400(self):
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("tools/list"),
                headers=_modern_headers("tools/list", version="2027-05-01"))
        # The body names 2027-05-01 in _meta and the header agrees, so this is
        # the server refusing the version, not the header check firing.
        assert status == 400
        assert json.loads(body)["error"]["code"] == \
            protocol.UNSUPPORTED_PROTOCOL_VERSION

    def test_a_header_body_mismatch_is_a_400_before_the_handler_runs(self):
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, _modern_body("tools/call", name="create_box"),
                headers=_modern_headers("tools/call", name="read_document"))

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.HEADER_MISMATCH

    def test_the_body_is_drained_before_a_rejection(self):
        """The old early 400 skipped the body, safe only while
        protocol_version stayed HTTP/1.0. After the reorder a client can send
        a large body and still read its rejection cleanly."""
        payload = _modern_body("tools/call", name="create_box",
                               arguments={"pad": "x" * 100000})
        with _RunningServer(handler=_dual_era_handler) as srv:
            status, body, _ = _post(
                srv.port, payload,
                headers=_modern_headers("tools/call", name="other"))

        assert status == 400
        assert json.loads(body)["error"]["code"] == protocol.HEADER_MISMATCH
```

- [ ] **Step 2: Fix the test that now asserts the old behaviour**

`test_the_rejection_names_what_we_support` (line ~301) posts `MCP-Protocol-Version: 2026-07-28` on a legacy body and asserts every member of `SUPPORTED_PROTOCOL_VERSIONS` appears in the message. That set now contains `2026-07-28` itself. Rewrite it:

```python
    def test_the_rejection_names_what_we_support(self):
        """A 400 a client cannot act on is the #60 failure mode again.

        The legacy path accepts only legacy revisions: a body with no _meta
        that nonetheless names 2026-07-28 in the header is a broken client,
        and the actionable answer is the list it may actually hand this path.
        """
        with _RunningServer() as srv:
            _, body, _ = _post(
                srv.port, self._PING,
                headers={"MCP-Protocol-Version": "2026-07-28"})

        error = json.loads(body)["error"]
        assert error["code"] == protocol.UNSUPPORTED_PROTOCOL_VERSION
        for version in protocol.LEGACY_VERSIONS:
            assert version in error["message"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py -q`
Expected: FAIL — modern requests are rejected 400 by the pre-body version check, and unknown modern methods come back 200.

- [ ] **Step 4: Rewrite the head of `_handle_streamable`**

Delete the whole pre-body version check (the comment block plus the `version = self.headers.get(...)` guard, lines ~908-928) so the `try:` that reads the body becomes the first statement after the docstring. Then, immediately after the `isinstance(msg, dict)` guard, insert:

```python
                # Era is decided by the body, so the body must be read first.
                # This is why the version check no longer runs before it —
                # which also retires the old caveat about answering without
                # draining, safe only while protocol_version stayed HTTP/1.0.
                modern = protocol.is_modern_request(msg)
                if modern:
                    err = validate_modern_headers(self.headers, msg)
                    if err is not None:
                        self._send_json(400, err)
                        return
                else:
                    # Absent means "assume 2025-03-26" (spec SHOULD), which is
                    # what we speak. A body with no _meta naming a modern
                    # revision in the header is a broken client: the list it
                    # can act on is the legacy one.
                    version = self.headers.get("MCP-Protocol-Version")
                    if (version is not None
                            and version not in protocol.LEGACY_VERSIONS):
                        self._send_json(400, protocol.unsupported_version_error(
                            msg.get("id"), version, protocol.LEGACY_VERSIONS))
                        return
```

Replace the final `self._send_json(200, response)` with:

```python
                self._send_json(_status_for(response, modern), response)
```

And add, beside the other module-level helpers:

```python
# 2026-07-28 maps these onto HTTP so an intermediary can act on them without
# parsing the body. Legacy answers 200-with-error for everything, so this map
# is applied only to a modern response.
MODERN_ERROR_STATUS = {
    protocol.METHOD_NOT_FOUND: 404,
    protocol.HEADER_MISMATCH: 400,
    protocol.MISSING_REQUIRED_CLIENT_CAPABILITY: 400,
    protocol.UNSUPPORTED_PROTOCOL_VERSION: 400,
}


def _status_for(response, modern):
    """The HTTP status a JSON-RPC response travels under."""
    if not modern or "error" not in response:
        return 200
    return MODERN_ERROR_STATUS.get(response["error"].get("code"), 200)
```

Note `unsupported_version_error(msg.get("id"), …)` on the legacy path: the old code passed `None` because it had not parsed the body. Now it has, so the client can correlate the rejection with its request.

- [ ] **Step 5: Document the `/sse` asymmetry in the code**

A modern-shaped message posted to the deprecated `/messages` endpoint is
served the modern way — it funnels through the same `MCPServer._handle` —
but gets **no** header validation, because only `_handle_streamable` calls
`validate_modern_headers`. That is deliberate, and it must not read as an
oversight to whoever finds it next. Add to `_handle_messages`, above its
handler dispatch:

```python
                # A modern-shaped message is served modern here too (both
                # endpoints share MCPServer._handle), but without the header
                # validation /mcp applies. Deliberate: mirrored headers exist
                # so an intermediary can route without parsing the body, and
                # this deprecated localhost SSE pair (#65) has none. Not worth
                # extending a transport on a removal clock.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_streamable_server.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS. **This is the first point at which the branch is conformant** — the header is accepted and the era behind it is served.

- [ ] **Step 8: Commit**

```bash
git add freecad_ai/mcp/transport.py tests/unit/test_mcp_streamable_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): serve both eras from POST /mcp (#64)

The version check moves below the body read, because the modern rule is that
the header must match the body rather than stand in for it — which also
retires the caveat about answering without draining. A modern response now
carries its error onto HTTP: 404 for an unknown method, 400 for a header
mismatch or a version we cannot serve. Legacy still answers 200 with the
error in the body, as every existing client expects.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Documentation and a live probe

**Files:**
- Modify: `CHANGELOG.md` (a new `## [Unreleased]` section above `## [0.28.0-alpha]`)
- Modify: `/home/alf/Projects/programming/misc/freecad-ai-wiki/MCP-Integration.md`
- Modify: `/home/alf/Projects/programming/misc/freecad-ai-wiki/Configuration.md`

- [ ] **Step 1: Write the CHANGELOG entry**

Insert above `## [0.28.0-alpha] - 2026-09-18`:

```markdown
## [Unreleased]

### Added

- **Dual-era MCP server (#64).** The MCP server now answers the stateless
  `2026-07-28` revision alongside the `initialize` handshake it has always
  spoken, from the same `POST /mcp` endpoint. A client says which era it
  speaks per request, by carrying `io.modelcontextprotocol/protocolVersion`
  in `params._meta`; a request without it is served exactly as before.

  `server/discover` is implemented — the bootstrap a stateless client uses in
  place of the handshake. Modern results carry `resultType` and the server's
  identity in per-result `_meta`, and `tools/list` carries the `ttlMs` and
  `cacheScope` freshness hints the revision requires.

- **Configurable `tools/list` cache hints.** `mcp_server_tools_ttl_ms`
  (default `300000`) and `mcp_server_tools_cache_scope` (default `private`),
  overridable with `MCP_TOOLS_TTL_MS` and `MCP_TOOLS_CACHE_SCOPE`. Set the
  TTL to `0` to tell clients not to cache the tool list at all.

### Changed

- **`initialize` echoes the client's protocol revision** when it is one we
  speak, instead of always replying `2025-03-26`. A `2025-11-25` client used
  to be told to negotiate down for no reason.

- **`tools/list` is sorted by name.** It followed registration order, which
  is import order, so it moved when nothing about the tools had.

- **An unsupported protocol version is now `-32022`** (`UnsupportedProtocol\
Version`) rather than `-32600`, and the rejection carries the request's id.
```

- [ ] **Step 2: Update the wiki**

In `MCP-Integration.md`, add a section under the server documentation:

```markdown
### Protocol revisions

The server speaks two eras from one endpoint:

| Revision | Era | How a client addresses it |
|---|---|---|
| `2026-07-28` | modern | `params._meta["io.modelcontextprotocol/protocolVersion"]` on every request, mirrored in the `MCP-Protocol-Version` header |
| `2025-11-25`, `2025-06-18`, `2025-03-26` | legacy | the `initialize` handshake |

A request with no `_meta` is served the legacy way, so existing
configurations need no change. Modern clients start with `server/discover`,
which reports the revisions we serve, our capabilities and our identity —
there is no handshake in that era to learn it from.

Modern requests mirror `method` into `Mcp-Method`, and `params.name` into
`Mcp-Name` on `tools/call`. The server rejects a request whose headers
disagree with its body with `-32020` and HTTP 400: the headers exist so
something in front of the server can route on them, which only holds while
the two agree. An unknown method in the modern era is HTTP 404; in the
legacy era it stays HTTP 200 with the error in the body.
```

In `Configuration.md`, add the two new fields to the MCP server table, with
the env overrides and the note that `ttlMs: 0` means "do not cache".

- [ ] **Step 3: Probe it live**

A mocked handler cannot catch a wrong HTTP status or a tool that does not
actually run. This box can drive a FreeCAD GUI; use Xvfb so nothing appears
on the maintainer's desktop:

```bash
MCP_PORT=3117 xvfb-run -a env QT_QPA_PLATFORM=xcb \
  ~/bin/freecad /home/alf/Projects/programming/misc/freecad-ai/mcp_server_http.py &
sleep 45 && ss -ltn | grep 3117    # confirm it is listening before probing
```

The maintainer usually has their own FreeCAD running with `--single-instance`.
When cleaning up, match on the script name and check PIDs — never `pkill
FreeCAD`.

```bash
M='{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}'

# 1. Modern discover.
curl -sS -X POST http://127.0.0.1:3117/mcp -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: server/discover' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"server/discover\",\"params\":{\"_meta\":$M}}"

# 2. Both eras must report the same tool count (56 as of v0.28.0-alpha).
curl -sS -X POST http://127.0.0.1:3117/mcp -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: tools/list' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{\"_meta\":$M}}" \
  | python3 -c 'import json,sys; print("modern", len(json.load(sys.stdin)["result"]["tools"]))'

curl -sS -X POST http://127.0.0.1:3117/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}' \
  | python3 -c 'import json,sys; print("legacy", len(json.load(sys.stdin)["result"]["tools"]))'

# 3. A modern tools/call that really changes the document.
#    create_primitive takes shape_type, not primitive_type.
curl -sS -X POST http://127.0.0.1:3117/mcp -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: create_primitive' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"_meta\":$M,\"name\":\"create_primitive\",\"arguments\":{\"shape_type\":\"box\",\"body_name\":\"\"}}}"
# then confirm with a legacy list_objects that the box is really there

# 4. Status codes.
for probe in \
  '404:nonsense/method:2026-07-28' \
  '400:tools/list:2027-05-01'; do
  code=${probe%%:*}; rest=${probe#*:}; meth=${rest%%:*}; ver=${rest##*:}
  got=$(curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:3117/mcp \
    -H 'Content-Type: application/json' -H "MCP-Protocol-Version: $ver" \
    -H "Mcp-Method: $meth" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"$meth\",\"params\":{\"_meta\":{\"io.modelcontextprotocol/protocolVersion\":\"$ver\"}}}")
  echo "expect $code got $got ($meth @ $ver)"
done

# 5. A header/body mismatch is a 400.
curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:3117/mcp \
  -H 'Content-Type: application/json' -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' -H 'Mcp-Name: list_objects' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":6,\"method\":\"tools/call\",\"params\":{\"_meta\":$M,\"name\":\"create_primitive\",\"arguments\":{}}}"

# 6. Legacy is untouched: the echo, and a 200 on an unknown method.
curl -sS -X POST http://127.0.0.1:3117/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":7,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}'
```

Record what the probe actually printed — both tool counts, each status code,
and the geometry confirmation — in the commit message. Do not write down a
number you did not read off the output.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(#64): changelog for the dual-era MCP server

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
cd /home/alf/Projects/programming/misc/freecad-ai-wiki
git add MCP-Integration.md Configuration.md
git commit -m "$(cat <<'EOF'
docs: the MCP server speaks two protocol eras (#64)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Out of Scope

Named here so no task drifts into them:

- **Client-side `server/discover` probing.** Our MCP *client* keeps using the
  handshake against other servers. That is the explicit follow-up.
- **MRTR** (`resultType: "input_required"`, `inputRequests`). No FreeCAD tool
  asks its caller a question mid-call.
- **The `/sse` + `/messages` pair.** Deprecated with a removal window (#65).
  It inherits modern support for free because it funnels through the same
  `MCPServer._handle`, but no transport work is done for it.
- **A GUI for the cache hints.** JSON and env only, by decision.
