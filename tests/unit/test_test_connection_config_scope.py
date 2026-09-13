"""Test Connection must not stage its inputs in the live config (#76).

``_save_temp`` pushed five Behavior-tab widget values into the ``get_config()``
singleton so ``_TestConnectionThread.run()`` could read two of them back off
it. Cancel is a bare ``reject()`` with no snapshot, so the writes outlived the
dialog, and the two unrelated ``save_current_config()`` calls in
``chat_widget.py`` (dock-layout change, Plan/Act toggle) then flushed them to
disk — settings the user explicitly cancelled, persisted.

The fix hands the probe its inputs directly instead of staging them in global
state, so there is nothing left to roll back. The other three values
(``context_window``, ``max_tool_turns``, ``system_prompt_override``) were
never read by the probe at all; they simply stop being written.

The fake self carries only what ``_test_connection`` touches, so no Qt dialog
has to be constructed.
"""

import dataclasses
from unittest.mock import MagicMock

import pytest

# settings_dialog imports through ui/compat.py, which needs Qt.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


# Every widget value below deliberately differs from the AppConfig default it
# would overwrite. If they matched, a leak would write the value already there
# and every assertion in TestTheLiveConfigIsLeftAlone would pass vacuously.
EDITED_MAX_TOKENS = 1234
EDITED_CONTEXT_WINDOW = 55000
EDITED_MAX_TOOL_TURNS = 7
EDITED_THINKING_INDEX = 2          # "extended"; the default is "off"
EDITED_SYSTEM_PROMPT = "you are a lathe"


def _cfg():
    cfg = AppConfig()
    cfg.profiles = {
        "cloud": ProviderConfig(name="anthropic", model="claude-sonnet-4-6"),
    }
    cfg.active_profile = "cloud"
    cfg.provider_keys = {"anthropic": "vendor-default"}
    return cfg


def _fake(cfg):
    fake = MagicMock()
    fake._cfg = cfg
    fake._current_profile_label = "cloud"
    fake.provider_combo.currentIndex.return_value = 0
    fake.base_url_edit.text.return_value = "https://api.anthropic.com"
    fake.api_key_edit.text.return_value = "typed-key"
    fake.model_edit.text.return_value = "claude-sonnet-4-6"
    fake._read_model_params_table.return_value = {}
    fake.max_tokens_spin.value.return_value = EDITED_MAX_TOKENS
    fake.context_window_spin.value.return_value = EDITED_CONTEXT_WINDOW
    fake.max_tool_turns_spin.value.return_value = EDITED_MAX_TOOL_TURNS
    fake.thinking_combo.currentIndex.return_value = EDITED_THINKING_INDEX
    fake.system_prompt_edit.toPlainText.return_value = EDITED_SYSTEM_PROMPT
    fake._get_default_prompt_text.return_value = "the stock prompt"

    # A MagicMock self no-ops every collaborator method, so `self._save_temp()`
    # inside _test_connection would do nothing and every leak assertion below
    # would pass vacuously. Bind the real helper while it still exists; after
    # the fix there is none to bind and _test_connection calls no stand-in.
    if hasattr(SettingsDialog, "_save_temp"):
        fake._save_temp = lambda: SettingsDialog._save_temp(fake)
    else:
        del fake._save_temp

    return fake


def _run(monkeypatch, cfg):
    """Drive _test_connection, returning the kwargs the probe thread got."""
    captured = {}

    def fake_thread(*args, **kwargs):
        captured.update(kwargs)
        captured["positional"] = args
        return MagicMock()

    monkeypatch.setattr(
        "freecad_ai.ui.settings_dialog._TestConnectionThread", fake_thread)
    # _save_temp reaches for the singleton, not self._cfg — point it at the
    # same object so a leak is visible on cfg either way.
    monkeypatch.setattr(
        "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)

    SettingsDialog._test_connection(_fake(cfg))
    return captured


class TestTheLiveConfigIsLeftAlone:
    """The bug: five Behavior-tab values written into the singleton, with no
    rollback on Cancel."""

    def test_max_tokens_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.max_tokens == AppConfig().max_tokens

    def test_context_window_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.context_window == AppConfig().context_window

    def test_max_tool_turns_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.max_tool_turns == AppConfig().max_tool_turns

    def test_thinking_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.thinking == AppConfig().thinking

    def test_system_prompt_override_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.system_prompt_override == AppConfig().system_prompt_override

    def test_the_whole_config_is_untouched(self, monkeypatch):
        """Catches any field the five assertions above don't enumerate."""
        cfg = _cfg()
        before = dataclasses.asdict(cfg)

        _run(monkeypatch, cfg)

        assert dataclasses.asdict(cfg) == before


class TestTheProbeStillUsesTheEditedValues:
    """Removing the staging write must not silently demote the probe to the
    saved values — the point of testing before saving is that it runs with
    what is on screen."""

    def test_max_tokens_comes_from_the_spinbox(self, monkeypatch):
        captured = _run(monkeypatch, _cfg())
        assert captured["max_tokens"] == EDITED_MAX_TOKENS

    def test_thinking_comes_from_the_combo(self, monkeypatch):
        captured = _run(monkeypatch, _cfg())
        assert captured["thinking"] == "extended"

    def test_temperature_comes_from_the_config(self, monkeypatch):
        """_save_temp never wrote temperature, so the saved value is what the
        probe has always used. The model-params table still outranks it inside
        LLMClient."""
        cfg = _cfg()
        cfg.temperature = 0.42
        captured = _run(monkeypatch, cfg)
        assert captured["temperature"] == 0.42


class TestTheStagingHelperIsGone:
    """A rollback-on-Cancel fix would have left _save_temp in place and the
    hazard one forgotten caller away. Pin that it has no way back."""

    def test_save_temp_no_longer_exists(self):
        assert not hasattr(SettingsDialog, "_save_temp")
