"""McpPage: the MCP client list and the built-in server settings (#101)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

try:
    from PySide6 import QtWidgets
except ImportError:
    try:
        from PySide2 import QtWidgets
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui.settings_pages.mcp_page import McpPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp):
    p = McpPage()
    yield p
    p.deleteLater()


def _cfg():
    c = AppConfig()
    c.mcp_servers = [{"name": "a", "command": "x", "args": []}]
    c.mcp_server_host = "127.0.0.1"
    c.mcp_server_port = 30000
    c.mcp_server_allowed_hosts = ["localhost"]
    c.mcp_server_auth_token = "tok"
    return c


def test_three_group_boxes(page):
    titles = [g.title() for g in page.findChildren(QtWidgets.QGroupBox)]
    assert titles == ["MCP Servers", "Built-in MCP Server",
                      "Connect an external agent"]


def test_untouched_load_writes_nothing(page):
    page.load(_cfg())
    target = _cfg()
    target.mcp_server_port = 4242     # a newer value from elsewhere
    page.apply_to(target)
    assert target.mcp_server_port == 4242
    assert page.is_dirty() is False


def test_retyping_the_same_host_is_not_a_change(page):
    page.load(_cfg())
    page.mcp_server_host_edit.setText("  127.0.0.1 ")
    page.mcp_server_allowed_hosts_edit.setText("localhost ,")
    assert page.is_dirty() is False


def test_a_port_edit_writes_only_the_port(page):
    page.load(_cfg())
    page.mcp_server_port_edit.setText("31000")
    target = _cfg()
    target.mcp_server_auth_token = "other"
    page.apply_to(target)
    assert target.mcp_server_port == 31000
    assert target.mcp_server_auth_token == "other"


def test_removing_a_server_is_a_change(page):
    page.load(_cfg())
    page.mcp_list.setCurrentRow(0)
    page._remove_mcp_server()
    target = _cfg()
    page.apply_to(target)
    assert target.mcp_servers == []


def test_an_empty_allowed_hosts_field_saves_an_empty_list(page):
    """Empty must stay empty (the transport default), not the loopback list."""
    page.load(_cfg())
    page.mcp_server_allowed_hosts_edit.setText("")
    target = _cfg()
    page.apply_to(target)
    assert target.mcp_server_allowed_hosts == []


# --- Connect an external agent (#139) ---------------------------------------

from freecad_ai.agent_recipes import DEFAULT_AGENT_RECIPES  # noqa: E402

UNTESTED = {"name": "Other", "template": "other add {url}{header_args}",
            "header_arg": " -H {header}", "docs_url": "https://example.org",
            "tested": False}


def _agent_cfg(recipes=None):
    c = _cfg()
    c.mcp_agent_recipes = (list(DEFAULT_AGENT_RECIPES) if recipes is None
                           else recipes)
    return c


def test_autostart_round_trips(page):
    cfg = _agent_cfg()
    cfg.mcp_server_autostart = True
    page.load(cfg)
    assert page.mcp_server_autostart_check.isChecked()
    page.mcp_server_autostart_check.setChecked(False)
    target = _agent_cfg()
    target.mcp_server_autostart = True
    page.apply_to(target)
    assert target.mcp_server_autostart is False


def test_url_follows_port_field(page):
    page.load(_agent_cfg())
    assert page.agent_url_edit.text() == "http://127.0.0.1:30000/mcp"
    page.mcp_server_port_edit.setText("31000")
    assert page.agent_url_edit.text() == "http://127.0.0.1:31000/mcp"


def test_command_shows_token(page):
    page.load(_agent_cfg())
    page.agent_combo.setCurrentIndex(page.agent_combo.findText("Claude Code"))
    assert page.agent_command_edit.text().endswith(
        "-H 'Authorization: Bearer tok'")
    assert page.agent_header_edit.text() == "Authorization: Bearer tok"
    page.mcp_server_auth_token_edit.setText("")
    assert "-H" not in page.agent_command_edit.text()
    assert "Authorization" not in page.agent_json_edit.toPlainText()


def test_untested_note(page):
    page.load(_agent_cfg([UNTESTED]))
    assert "not tested by the maintainer" in page.agent_note_label.text()
    assert "https://example.org" in page.agent_note_label.text()


def test_page_survives_bad_recipes(page):
    raw = [42, {"name": "x"}]
    page.load(_agent_cfg(raw))
    assert page.agent_combo.count() == 0
    assert page._values()["mcp_agent_recipes"] == raw


def test_reset_backup_failure_keeps_list(page, monkeypatch):
    from freecad_ai.ui.settings_pages import mcp_page
    page.load(_agent_cfg([UNTESTED]))

    def _fail(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(mcp_page, "reset_recipes", _fail)
    monkeypatch.setattr(mcp_page.QMessageBox, "question",
                        lambda *a, **k: mcp_page.QMessageBox.Yes)
    warned = []
    monkeypatch.setattr(mcp_page.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))
    page._reset_agent_recipes()
    assert page._values()["mcp_agent_recipes"] == [UNTESTED]
    assert warned


def test_reset_replaces_list_and_marks_dirty(page, monkeypatch, tmp_path):
    from freecad_ai.ui.settings_pages import mcp_page
    page.load(_agent_cfg([UNTESTED]))
    monkeypatch.setattr(mcp_page, "_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(mcp_page.QMessageBox, "question",
                        lambda *a, **k: mcp_page.QMessageBox.Yes)
    monkeypatch.setattr(mcp_page.QMessageBox, "information",
                        lambda *a, **k: None)
    page._reset_agent_recipes()
    assert page._values()["mcp_agent_recipes"] == list(DEFAULT_AGENT_RECIPES)
    assert page.is_dirty()
    assert len(list(tmp_path.iterdir())) == 1
    assert page.agent_combo.count() == len(DEFAULT_AGENT_RECIPES)
