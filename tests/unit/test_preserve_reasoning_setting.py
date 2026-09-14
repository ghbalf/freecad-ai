"""The switch that keeps a turn's reasoning in the history.

This is deliberately *not* one of the #47 caching switches, and it is the
one Behavior switch in this release that ships **on**. Moonshot's engineers
report a clear, measurable drop in reply quality on turns whose
``reasoning_content`` is missing, in ordinary multi-turn chat and not merely
in tool loops, and recommend preserving every turn's reasoning whether or
not you care about caching (forum thread 602). A default that quietly
degrades replies is not a conservative default, so the flag exists as an
escape hatch rather than as an opt-in.

A widget with a reader and no writer is a silent no-op that reports no
error, which has caught this dialog repeatedly, so both directions are
pinned here rather than assumed.
"""

import inspect
from unittest import mock

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    c = AppConfig()
    monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config", lambda: c)
    monkeypatch.setattr(
        "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)
    return c


def _fake_save_dialog(preserve):
    """A fake self carrying what _save indexes or reads for real."""
    fake = mock.MagicMock()
    for combo in ("thinking_combo", "viewport_capture_combo",
                  "viewport_resolution_combo", "rerank_method_combo"):
        getattr(fake, combo).currentIndex.return_value = 0
    fake.rerank_pinned_edit.text.return_value = ""
    fake._parse_server_address.return_value = ("127.0.0.1", 8765)
    fake.utility_combos = {}
    fake.prompt_cache_check.isChecked.return_value = False
    fake.log_usage_check.isChecked.return_value = False
    fake.preserve_reasoning_check.isChecked.return_value = preserve
    return fake


class TestTheDefault:

    def test_it_ships_on(self):
        assert AppConfig().preserve_reasoning_history is True

    def test_a_config_written_before_this_release_still_loads_on(self):
        """Upgrading must not fail on JSON that predates the key, and must
        not read its absence as "the user turned this off"."""
        restored = AppConfig.from_dict({"max_tokens": 20000})

        assert restored.preserve_reasoning_history is True


class TestOKWritesItToTheConfig:

    def test_unticked_turns_it_off(self, cfg):
        SettingsDialog._save(_fake_save_dialog(False))

        assert cfg.preserve_reasoning_history is False

    def test_ticked_writes_true_not_merely_leaves_the_default(self, cfg):
        """Start from False, so an absent write leaves False and fails."""
        cfg.preserve_reasoning_history = False

        SettingsDialog._save(_fake_save_dialog(True))

        assert cfg.preserve_reasoning_history is True


class TestReopeningTheDialogShowsWhatWasSaved:

    def test_an_off_config_leaves_it_unticked(self, cfg):
        cfg.preserve_reasoning_history = False
        fake = mock.MagicMock()
        fake._cfg = cfg

        SettingsDialog._load_from_config(fake)

        fake.preserve_reasoning_check.setChecked.assert_called_once_with(False)

    def test_an_on_config_ticks_it(self, cfg):
        fake = mock.MagicMock()
        fake._cfg = cfg

        SettingsDialog._load_from_config(fake)

        fake.preserve_reasoning_check.setChecked.assert_called_once_with(True)


class TestTheWorkerActuallyConsultsIt:
    """Without this the escape hatch is inert: ``preserve_history`` defaults
    on, so a call site that forgets to pass it keeps working -- and unticking
    the box would silently do nothing."""

    def test_run_resolves_the_flag_from_the_config(self):
        src = inspect.getsource(cw._LLMWorker.run)

        assert "preserve_reasoning_history" in src

    def test_the_tool_loop_passes_the_resolved_flag_on(self):
        src = inspect.getsource(cw._LLMWorker._tool_loop)

        assert "self._preserve_reasoning" in src
