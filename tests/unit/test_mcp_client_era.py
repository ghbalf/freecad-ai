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

    def test_a_nameless_tools_call_mirrors_no_name(self):
        """A tools/call with no name omits Mcp-Name rather than sending None.

        urllib raises TypeError on a None header value, so emitting the key
        would crash the client before the server could answer -32020.
        """
        _, headers = protocol.ModernEra("2026-07-28", CLIENT_INFO).decorate(
            "tools/call", {})
        assert "Mcp-Name" not in headers


class TestEraTagging:
    def test_each_era_reports_which_one_it_is(self):
        assert protocol.LegacyEra("2025-03-26").era == protocol.LEGACY
        assert protocol.ModernEra("2026-07-28", CLIENT_INFO).era == protocol.MODERN
