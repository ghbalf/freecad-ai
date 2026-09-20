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
