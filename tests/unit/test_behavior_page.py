"""BehaviorPage: Limits, Behavior, System Prompt (#101)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtWidgets
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui.settings_pages.behavior_page import BehaviorPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp, tmp_config_dir):
    p = BehaviorPage()
    yield p
    p.deleteLater()


def _group_of(widget):
    w = widget.parent()
    while w is not None and not isinstance(w, QtWidgets.QGroupBox):
        w = w.parent()
    return w.title() if w is not None else None


def test_group_boxes_in_order(page):
    titles = [g.title() for g in page.findChildren(QtWidgets.QGroupBox)]
    assert titles == ["Limits", "Behavior", "Saved Files", "System Prompt"]


def test_the_limits_live_in_limits(page):
    for w in (page.max_tokens_spin, page.context_window_spin,
              page.max_tool_turns_spin, page.execution_timeout_spin):
        assert _group_of(w) == "Limits"


def test_the_viewport_combos_live_in_behavior(page):
    assert _group_of(page.viewport_capture_combo) == "Behavior"
    assert _group_of(page.viewport_resolution_combo) == "Behavior"


def test_the_tool_calling_label(page):
    assert page.enable_tools_check.text() == \
        "Use tool calling (uncheck to fall back to code generation)"


def test_no_mode_control_and_mode_survives(page):
    assert not hasattr(page, "mode_combo")
    cfg = AppConfig()
    cfg.mode = "plan"
    page.load(cfg)
    page.max_tokens_spin.setValue(8192)
    page.apply_to(cfg)
    assert cfg.mode == "plan"


def test_limits_load_and_apply(page):
    cfg = AppConfig()
    cfg.max_tokens, cfg.context_window = 65536, 200000
    cfg.max_tool_turns, cfg.execution_timeout = 0, 120
    page.load(cfg)
    assert page.max_tokens_spin.value() == 65536
    assert page.max_tool_turns_spin.value() == 0
    page.execution_timeout_spin.setValue(90)
    target = AppConfig()
    page.apply_to(target)
    assert target.execution_timeout == 90
    assert target.max_tokens == AppConfig().max_tokens   # untouched


def test_untouched_load_writes_nothing(page):
    page.load(AppConfig())
    target = AppConfig()
    target.thinking = "extended"
    page.apply_to(target)
    assert target.thinking == "extended"


def test_an_unshowable_thinking_survives_an_unrelated_edit(page):
    cfg = AppConfig()
    cfg.thinking = "max"
    cfg.viewport_capture = "sometimes"
    page.load(cfg)
    page.auto_execute_check.setChecked(not cfg.auto_execute)
    page.apply_to(cfg)
    assert (cfg.thinking, cfg.viewport_capture) == ("max", "sometimes")


def test_strip_thinking_auto_round_trips(page):
    cfg = AppConfig()
    cfg.strip_thinking_history = None
    page.load(cfg)
    assert page.strip_thinking_check.checkState() == QtCore.Qt.PartiallyChecked
    assert page.is_dirty() is False


class TestSystemPrompt:
    def test_unedited_default_writes_nothing(self, page):
        page.load(AppConfig())
        target = AppConfig()
        target.system_prompt_override = "set elsewhere"
        page.apply_to(target)
        assert target.system_prompt_override == "set elsewhere"

    def test_an_edit_is_saved(self, page):
        page.load(AppConfig())
        page.system_prompt_edit.setPlainText("Be terse.")
        target = AppConfig()
        page.apply_to(target)
        assert target.system_prompt_override == "Be terse."

    def test_editing_back_to_the_default_clears_the_override(self, page):
        cfg = AppConfig()
        cfg.system_prompt_override = "Be terse."
        page.load(cfg)
        page._reset_system_prompt()
        page.apply_to(cfg)
        assert cfg.system_prompt_override == ""


def test_after_save_sets_the_keep_dock_checkmark(page, monkeypatch):
    calls = []
    monkeypatch.setattr("freecad_ai.ui.command_state.set_command_checked",
                        lambda name, value: calls.append((name, value)))
    cfg = AppConfig()
    cfg.keep_dock_on_workbench_switch = True
    page.after_save(cfg)
    assert calls == [("FreeCADAI_ToggleKeepDock", True)]


def _row_label(widget):
    form = widget.parentWidget().layout()
    return form.labelForField(widget).text()


def test_context_window_is_labelled_compact_above(page):
    assert _row_label(page.context_window_spin) == "Compact above:"
    tip = page.context_window_spin.toolTip()
    assert "compacted" in tip and "Provider page" in tip


def test_max_output_tokens_tooltip_mentions_the_row(page):
    assert _row_label(page.max_tokens_spin) == "Max Output Tokens:"
    tip = page.max_tokens_spin.toolTip()
    assert "max_tokens row" in tip
    assert "determined by the model" not in tip


# --- Saved files / retention (forum promise: the knobs were JSON-only) -----

_RETENTION = {
    "max_saved_conversations": "conversations_keep_spin",
    "max_session_logs": "session_logs_keep_spin",
    "max_backups": "backups_keep_spin",
    "max_retention_age_days": "retention_days_spin",
}


def test_the_retention_settings_live_in_saved_files(page):
    for attr in _RETENTION.values():
        assert _group_of(getattr(page, attr)) == "Saved Files"


def test_retention_shows_the_config(page):
    cfg = AppConfig(max_saved_conversations=50, max_session_logs=20,
                    max_backups=10, max_retention_age_days=90)
    page.load(cfg)
    for key, attr in _RETENTION.items():
        assert getattr(page, attr).value() == getattr(cfg, key)


def test_zero_reads_as_keep_all(page):
    """0 disables the rule (config.prune_oldest_files); the box must say so."""
    page.load(AppConfig())
    for attr in _RETENTION.values():
        spin = getattr(page, attr)
        assert spin.value() == 0
        assert spin.minimum() == 0
        assert spin.specialValueText()  # shown instead of a bare "0"


def test_retention_edits_write_only_what_changed(page):
    page.load(AppConfig())
    page.backups_keep_spin.setValue(5)
    page.retention_days_spin.setValue(30)
    target = AppConfig(max_saved_conversations=7)
    page.apply_to(target)
    assert target.max_backups == 5
    assert target.max_retention_age_days == 30
    assert target.max_saved_conversations == 7  # untouched
    assert target.max_session_logs == 0


def test_retention_ranges_hold_realistic_values(page):
    page.load(AppConfig(max_saved_conversations=10000,
                        max_retention_age_days=3650))
    assert page.conversations_keep_spin.value() == 10000
    assert page.retention_days_spin.value() == 3650
