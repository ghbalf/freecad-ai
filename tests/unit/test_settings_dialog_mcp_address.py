"""Tests for the MCP server address fields in the Settings dialog.

Only the pure parsing helper is tested. Building the dialog needs a
QApplication; extracting the normalisation into a staticmethod keeps the part
with actual logic testable without one.
"""

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.mcp.gui_server import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402

parse = SettingsDialog._parse_server_address


def test_parses_a_normal_address():
    assert parse("192.168.1.50", "8080") == ("192.168.1.50", 8080)


def test_strips_surrounding_whitespace():
    assert parse("  127.0.0.1  ", "  3000 ") == ("127.0.0.1", 3000)


def test_empty_host_falls_back_to_the_default():
    assert parse("", "8080") == (DEFAULT_HOST, 8080)


def test_empty_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "") == ("127.0.0.1", DEFAULT_PORT)


def test_non_numeric_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "abc") == ("127.0.0.1", DEFAULT_PORT)


def test_out_of_range_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "70000") == ("127.0.0.1", DEFAULT_PORT)
    assert parse("127.0.0.1", "0") == ("127.0.0.1", DEFAULT_PORT)


def test_privileged_ports_are_allowed():
    """No 1024 floor -- the GUI reaches exactly the ports MCP_PORT does.

    Binding one unprivileged fails with PermissionError, which surfaces
    through the same modal as any other bind failure.
    """
    assert parse("127.0.0.1", "80") == ("127.0.0.1", 80)
