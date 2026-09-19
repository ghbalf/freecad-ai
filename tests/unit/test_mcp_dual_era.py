"""Dual-era MCP: one endpoint answering the 2025-03-26 handshake and the
stateless 2026-07-28 per-request _meta era (#64 phase 3)."""

import os

import pytest

from freecad_ai.mcp import protocol
from freecad_ai.mcp import server as server_mod
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
