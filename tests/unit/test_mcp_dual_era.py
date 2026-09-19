"""Dual-era MCP: one endpoint answering the 2025-03-26 handshake and the
stateless 2026-07-28 per-request _meta era (#64 phase 3)."""

import base64
import os
from email.message import Message

import pytest

from freecad_ai.mcp import protocol
from freecad_ai.mcp import server as server_mod
from freecad_ai.mcp import transport as transport_mod
from freecad_ai.tools.registry import ToolRegistry


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

    def test_an_empty_meta_is_legacy(self):
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
             "params": {"_meta": {}}})

    def test_a_null_meta_is_legacy(self):
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
             "params": {"_meta": None}})

    def test_a_present_but_null_protocol_version_is_modern(self):
        """The spec's decision table: era is modern iff params._meta CARRIES
        the key, whatever its value. A key present with a null value is a
        modern client that sent a broken version, not an absent key — and
        must not be indistinguishable from one, or it is served the legacy
        way with no header validation at all."""
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
               "params": {"_meta": {protocol.META_PROTOCOL_VERSION: None}}}
        assert protocol.is_modern_request(msg)
        assert protocol.request_protocol_version(msg) is None

    def test_a_non_dict_params_does_not_raise(self):
        """The endpoint is unauthenticated (#59); malformed input must not 500."""
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": []})
        assert not protocol.is_modern_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
             "params": {"_meta": "nonsense"}})

    @pytest.mark.parametrize("bad_params", [5, [], ["protocolVersion"],
                                            "protocolVersion"])
    @pytest.mark.parametrize("method", ["initialize", "tools/call"])
    def test_a_non_dict_params_does_not_raise_through_handle(
            self, bad_params, method):
        """The endpoint is unauthenticated (#59); malformed input must not
        500. This drives MCPServer._handle itself — is_modern_request alone
        (above) never touches the code path where the raise actually
        happens: ``or {}`` lets a non-dict ``params`` through, and the
        legacy ``initialize`` branch is the one that then does
        ``"protocolVersion" not in params`` / ``params.get(...)``."""
        msg = {"jsonrpc": "2.0", "id": 1, "method": method,
               "params": bad_params}
        resp = server_mod.MCPServer(ToolRegistry(),
                                    cache_hints=(300000, "private"))._handle(msg)
        assert isinstance(resp, dict)
        if method == "initialize":
            assert resp["result"]["protocolVersion"] == \
                protocol.DEFAULT_PROTOCOL_VERSION


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

    def test_the_payload_cannot_overwrite_resulttype(self):
        """Not reachable from any current caller, but the envelope's one
        invariant should not depend on callers behaving: a payload carrying
        its own resultType must not leak onto the wire."""
        result = protocol.modern_result(
            {"resultType": "input_required"}, self._INFO)
        assert result["resultType"] == "complete"


class _Cfg:
    """Config stand-in: getattr-compatible, and nothing else is required."""

    def __init__(self, ttl=None, scope=None):
        if ttl is not None:
            self.mcp_server_tools_ttl_ms = ttl
        if scope is not None:
            self.mcp_server_tools_cache_scope = scope


class TestResolveCacheHints:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Every test in this class assumes an unconfigured environment. The
        wiki documents MCP_TOOLS_TTL_MS / MCP_TOOLS_CACHE_SCOPE as the two
        env overrides users are told to set, so a developer with either
        exported would otherwise see most of this class fail — env beats
        config beats defaults, and a leaked value beats the test's own
        _Cfg."""
        monkeypatch.delenv("MCP_TOOLS_TTL_MS", raising=False)
        monkeypatch.delenv("MCP_TOOLS_CACHE_SCOPE", raising=False)

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

    def test_a_float_ttl_falls_back(self):
        """int(3.7) silently becomes 3 — only reachable from a hand-edited
        config.json (an env value is always a string), but the spec calls a
        non-integer TTL invalid, and a float is one."""
        assert server_mod.resolve_cache_hints(_Cfg(3.7, "private"))[0] == \
            protocol.DEFAULT_TOOLS_TTL_MS

    def test_a_bool_ttl_falls_back(self):
        """isinstance(True, int) is True in Python, but a bool is not a TTL —
        int(True) silently becoming 1 would be exactly as wrong as int(3.7)
        becoming 3."""
        assert server_mod.resolve_cache_hints(_Cfg(True, "private"))[0] == \
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

    def test_a_null_meta_protocol_version_is_refused_not_downgraded(self):
        """A present-but-null protocolVersion key is a modern request to
        refuse (-32022), never a silent fall-through to the legacy shape —
        straight through _handle with no transport header check in the way."""
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
               "params": {"_meta": {protocol.META_PROTOCOL_VERSION: None}}}
        resp = _server()._handle(msg)
        assert resp["error"]["code"] == protocol.UNSUPPORTED_PROTOCOL_VERSION
        assert resp["error"]["data"]["requested"] is None

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


def _headers(mapping):
    """A case-insensitive header object, as http.server hands the handler."""
    msg = Message()
    for key, value in mapping.items():
        msg[key] = value
    return msg


def _headers_with_duplicate(base, name, first, second):
    """Like ``_headers``, but ``name`` is assigned twice (a genuine
    duplicate, as ``email.message.Message`` records it) instead of once.

    ``base`` supplies every other header; ``name`` must not be a key in it.
    """
    msg = Message()
    for key, value in base.items():
        if key.lower() != name.lower():
            msg[key] = value
    msg[name] = first
    msg[name] = second
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

    def test_an_undecodable_name_is_rejected_even_with_no_name_in_the_body(self):
        """A value we cannot read is not a value we can confirm agrees.

        Both sides being None is not agreement: the header says the client
        meant to name a tool, and we could not read which."""
        err = transport_mod.validate_modern_headers(
            self._ok_headers(**{"Mcp-Name": "=?base64?!!!notb64!!!?="}),
            _modern("tools/call"))
        assert err is not None
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    @pytest.mark.parametrize("first,second", [
        ("create_box", "read_document"), ("read_document", "create_box")])
    def test_a_duplicated_mcp_name_is_rejected_in_either_order(
            self, first, second):
        """``headers.get()`` on an ``email.message.Message`` returns only the
        FIRST occurrence. A smuggled second ``Mcp-Name`` therefore validates
        or not depending purely on which copy comes first — and the body
        always calls ``create_box`` here, so a policy layer reading a
        different copy than we dispatch is exactly the bypass this function
        exists to prevent. Both orders must be rejected, not just one."""
        headers = _headers_with_duplicate(
            {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call"},
            "Mcp-Name", first, second)
        err = transport_mod.validate_modern_headers(headers, self._CALL)
        assert err is not None
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    @pytest.mark.parametrize("first,second", [
        ("tools/call", "tools/list"), ("tools/list", "tools/call")])
    def test_a_duplicated_mcp_method_is_rejected_in_either_order(
            self, first, second):
        headers = _headers_with_duplicate(
            {"MCP-Protocol-Version": "2026-07-28", "Mcp-Name": "create_box"},
            "Mcp-Method", first, second)
        err = transport_mod.validate_modern_headers(headers, self._CALL)
        assert err is not None
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    @pytest.mark.parametrize("first,second", [
        ("2026-07-28", "2025-03-26"), ("2025-03-26", "2026-07-28")])
    def test_a_duplicated_protocol_version_is_rejected_in_either_order(
            self, first, second):
        headers = _headers_with_duplicate(
            {"Mcp-Method": "tools/call", "Mcp-Name": "create_box"},
            "MCP-Protocol-Version", first, second)
        err = transport_mod.validate_modern_headers(headers, self._CALL)
        assert err is not None
        assert err["error"]["code"] == protocol.HEADER_MISMATCH

    def test_a_plain_mapping_with_no_get_all_still_works(self):
        """A caller passing a plain dict (no ``get_all``, so a duplicate
        cannot even be represented) must degrade to the old single-value
        behaviour rather than raising."""
        assert transport_mod.validate_modern_headers(
            {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call",
             "Mcp-Name": "create_box"}, self._CALL) is None
