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
