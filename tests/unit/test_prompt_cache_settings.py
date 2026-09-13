"""The two prompt-caching switches reach the config and come back (#47).

A settings widget with a reader but no writer -- or the reverse -- is a
silent no-op: the checkbox moves, nothing happens, and nothing reports an
error. That has caught this dialog repeatedly, so both directions are
pinned here rather than assumed.

The defaults matter as much as the plumbing. Both flags ship off, so an
existing install behaves after the upgrade exactly as it did before it;
the caching one in particular changes what the model is shown, which is
not a thing to switch on for someone without asking.
"""

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
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    """A throwaway config, with the disk flush stubbed out."""
    c = AppConfig()
    monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config", lambda: c)
    monkeypatch.setattr(
        "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)
    return c


def _fake_save_dialog(prompt_cache, log_usage):
    """A fake self carrying what _save indexes or reads for real.

    Every combo below is used as a list index, so a MagicMock would raise
    TypeError rather than reach the lines under test.
    """
    fake = mock.MagicMock()
    for combo in ("thinking_combo", "viewport_capture_combo",
                  "viewport_resolution_combo", "rerank_method_combo"):
        getattr(fake, combo).currentIndex.return_value = 0
    fake.rerank_pinned_edit.text.return_value = ""
    # Unpacked into two names, so a bare MagicMock is a ValueError.
    fake._parse_server_address.return_value = ("127.0.0.1", 8765)
    fake.utility_combos = {}
    fake.prompt_cache_check.isChecked.return_value = prompt_cache
    fake.log_usage_check.isChecked.return_value = log_usage
    return fake


class TestTheDefaults:

    def test_both_switches_ship_off(self):
        fresh = AppConfig()

        assert fresh.optimize_prompt_caching is False
        assert fresh.log_token_usage is False

    def test_a_config_written_before_this_release_still_loads(self):
        """Upgrading must not fail on JSON that predates these keys."""
        restored = AppConfig.from_dict({"max_tokens": 20000})

        assert restored.optimize_prompt_caching is False
        assert restored.log_token_usage is False


class TestOKWritesThemToTheConfig:

    def test_caching_on(self, cfg):
        SettingsDialog._save(_fake_save_dialog(True, False))

        assert cfg.optimize_prompt_caching is True

    def test_usage_logging_on(self, cfg):
        SettingsDialog._save(_fake_save_dialog(False, True))

        assert cfg.log_token_usage is True

    def test_unchecked_writes_false_not_merely_leaves_the_default(self, cfg):
        """Start from True, so an absent write leaves True and fails."""
        cfg.optimize_prompt_caching = True
        cfg.log_token_usage = True

        SettingsDialog._save(_fake_save_dialog(False, False))

        assert cfg.optimize_prompt_caching is False
        assert cfg.log_token_usage is False

    def test_the_two_are_independent(self, cfg):
        SettingsDialog._save(_fake_save_dialog(True, False))

        assert cfg.optimize_prompt_caching is True
        assert cfg.log_token_usage is False


class TestReopeningTheDialogShowsWhatWasSaved:

    def test_both_checkboxes_are_restored(self, cfg):
        cfg.optimize_prompt_caching = True
        cfg.log_token_usage = True
        fake = mock.MagicMock()
        fake._cfg = cfg

        SettingsDialog._load_from_config(fake)

        fake.prompt_cache_check.setChecked.assert_called_once_with(True)
        fake.log_usage_check.setChecked.assert_called_once_with(True)

    def test_an_off_config_leaves_them_unticked(self, cfg):
        fake = mock.MagicMock()
        fake._cfg = cfg

        SettingsDialog._load_from_config(fake)

        fake.prompt_cache_check.setChecked.assert_called_once_with(False)
        fake.log_usage_check.setChecked.assert_called_once_with(False)


class TestTheClientIsBuiltFromThoseFlags:
    """The switches are inert unless create_client passes them on."""

    def test_both_flags_reach_the_client(self, cfg, monkeypatch):
        from freecad_ai.llm.client import create_client
        cfg.optimize_prompt_caching = True
        cfg.log_token_usage = True

        client = create_client(cfg)

        assert client.prompt_caching is True
        assert client.log_usage is True

    def test_a_utility_client_never_caches(self, cfg):
        """The reranker and the probes send a different prefix every call,
        so a cache write there is paid for and never read back."""
        from freecad_ai.llm.client import create_client
        cfg.optimize_prompt_caching = True

        client = create_client(cfg, utility="rerank")

        assert client.prompt_caching is False

    def test_off_by_default_the_client_agrees(self, cfg):
        from freecad_ai.llm.client import create_client

        client = create_client(cfg)

        assert client.prompt_caching is False
        assert client.log_usage is False
