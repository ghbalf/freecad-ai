"""Detected capabilities belong to the profile, not to the config.

``vision_detected``/``vision_override``/``tools_detected``/``thinking_detected``
described "the model" back when there was exactly one connection. With
connection profiles there are several, and Test Connection probes whichever
profile is on screen — so a probe of a reranker or utility profile used to
overwrite the chat model's capabilities globally, and persist it to disk
immediately.

The sharp edge was ``tools_detected``: ``cfg.supports_tools`` prefers it over
the provider's static flag, and chat gates Act mode on that, so probing an
embedding model on any profile silently stopped sending tools altogether.

These pin the flags to the profile they were detected on.
"""

import copy

import pytest

from freecad_ai.config import AppConfig, ProviderConfig


def _two_profiles():
    cfg = AppConfig()
    cfg.profiles = {
        "chat": ProviderConfig(name="ollama", model="qwen3-coder:30b"),
        "rerank": ProviderConfig(name="ollama", model="nomic-embed-text"),
    }
    cfg.active_profile = "chat"
    return cfg


class TestProfileCarriesItsOwnCapabilities:

    def test_fields_default_to_untested(self):
        p = ProviderConfig()
        assert p.vision_detected is None
        assert p.vision_override is None
        assert p.tools_detected is None
        assert p.thinking_detected is None

    def test_supports_vision_reads_the_active_profile(self):
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        cfg.profiles["rerank"].vision_detected = False
        assert cfg.supports_vision is True

    def test_manual_override_still_wins_over_detection(self):
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        cfg.profiles["chat"].vision_override = False
        assert cfg.supports_vision is False
        # ...and in the other direction, where a global-flag fallback
        # could not produce the right answer by accident.
        cfg.profiles["chat"].vision_detected = False
        cfg.profiles["chat"].vision_override = True
        assert cfg.supports_vision is True

    def test_supports_tools_reads_the_active_profile(self):
        cfg = _two_profiles()
        cfg.profiles["rerank"].tools_detected = False
        # The embedding profile says "no tools"; chat's is untested, so the
        # provider-wide static flag answers for chat.
        assert cfg.supports_tools is True
        cfg.profiles["chat"].tools_detected = False
        assert cfg.supports_tools is False

    def test_switching_the_active_profile_switches_the_answers(self):
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        cfg.profiles["rerank"].vision_detected = False
        assert cfg.supports_vision is True
        cfg.active_profile = "rerank"
        assert cfg.supports_vision is False


class TestMigration:

    def test_pre_profiles_config_moves_flags_onto_the_migrated_profile(self):
        cfg = AppConfig.from_dict({
            "provider": {"name": "ollama", "model": "qwen3-coder:30b"},
            "vision_detected": True,
            "tools_detected": False,
            "thinking_detected": True,
            "vision_override": True,
        })
        prof = cfg.profiles[cfg.active_profile]
        assert prof.vision_detected is True
        assert prof.tools_detected is False
        assert prof.thinking_detected is True
        assert prof.vision_override is True

    def test_profiles_era_config_moves_flags_onto_the_active_profile(self):
        """Configs written by the unreleased profiles work keep the flags at
        top level; adopt them once, into the profile chat runs on."""
        cfg = AppConfig.from_dict({
            "profiles": {
                "chat": {"name": "ollama", "model": "a"},
                "rerank": {"name": "ollama", "model": "b"},
            },
            "active_profile": "chat",
            "vision_detected": True,
            "tools_detected": True,
        })
        assert cfg.profiles["chat"].vision_detected is True
        assert cfg.profiles["chat"].tools_detected is True
        # Never guessed for a profile that was not the one probed.
        assert cfg.profiles["rerank"].vision_detected is None
        assert cfg.profiles["rerank"].tools_detected is None

    def test_a_profile_that_already_has_flags_is_left_alone(self):
        cfg = AppConfig.from_dict({
            "profiles": {"chat": {"name": "ollama", "model": "a",
                                  "vision_detected": False}},
            "active_profile": "chat",
            "vision_detected": True,
        })
        assert cfg.profiles["chat"].vision_detected is False

    def test_flags_survive_a_save_load_round_trip_per_profile(self):
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        cfg.profiles["rerank"].tools_detected = False

        back = AppConfig.from_dict(cfg.to_dict())

        assert back.profiles["chat"].vision_detected is True
        assert back.profiles["rerank"].tools_detected is False
        assert back.profiles["chat"].tools_detected is None

    def test_to_dict_mirrors_the_active_profile_for_a_downgrade(self):
        """Like the legacy ``provider`` mirror: an older version reads the
        top-level flags, so write the active profile's there too."""
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        cfg.profiles["rerank"].vision_detected = False

        data = cfg.to_dict()

        assert data["vision_detected"] is True


# --- Settings dialog: the probes write to the profile they probed ---------

try:
    import PySide6  # noqa: F401
    _HAVE_QT = True
except ImportError:  # pragma: no cover - dev venv without either binding
    try:
        import PySide2  # noqa: F401
        _HAVE_QT = True
    except ImportError:
        _HAVE_QT = False

pytestmark_qt = pytest.mark.skipif(
    not _HAVE_QT, reason="PySide6/PySide2 not available")


def _fake_dialog(cfg, shown="chat"):
    """A fake self carrying only what the methods under test touch."""
    from unittest import mock
    fake = mock.MagicMock()
    fake._cfg = cfg
    fake._profiles = cfg.profiles
    fake._active_profile = cfg.active_profile
    fake._current_profile_label = shown
    prof = cfg.profiles[shown]
    fake.provider_combo.currentIndex.return_value = -1  # keep prof.name
    fake.base_url_edit.text.return_value = prof.base_url
    fake.api_key_edit.text.return_value = prof.api_key
    fake.model_edit.text.return_value = prof.model
    fake._read_model_params_table.return_value = dict(prof.params)
    # Real method, not a MagicMock stand-in — it is code under test.
    from freecad_ai.ui.settings_dialog import SettingsDialog
    fake._probed_profile = lambda: SettingsDialog._probed_profile(fake)
    return fake


@pytestmark_qt
class TestProbesWriteToTheProbedProfile:
    """The user's report: creating a reranker profile and pressing Test
    Connection on it rewrote the *chat* model's vision flag."""

    def test_vision_probe_writes_to_the_probed_profile(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        fake = _fake_dialog(cfg, shown="rerank")
        fake._test_profile_label = "rerank"

        SettingsDialog._on_vision_probed(fake, True)

        assert cfg.profiles["rerank"].vision_detected is True
        assert cfg.profiles["chat"].vision_detected is None

    def test_capability_probe_writes_to_the_probed_profile(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        fake = _fake_dialog(cfg, shown="rerank")
        fake._test_profile_label = "rerank"

        SettingsDialog._on_capabilities_detected(
            fake, {"vision": False, "tools": False, "thinking": True})

        assert cfg.profiles["rerank"].tools_detected is False
        assert cfg.profiles["rerank"].thinking_detected is True
        assert cfg.profiles["chat"].tools_detected is None
        assert cfg.profiles["chat"].thinking_detected is None

    def test_probe_results_do_not_touch_the_live_config(self):
        """They land in the dialog's working copy, like every other
        profile field, and reach the real config on OK."""
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        working = copy.deepcopy(cfg.profiles)
        fake = _fake_dialog(cfg, shown="rerank")
        fake._profiles = working
        fake._test_profile_label = "rerank"

        SettingsDialog._on_vision_probed(fake, True)

        assert working["rerank"].vision_detected is True
        assert cfg.profiles["rerank"].vision_detected is None


@pytestmark_qt
class TestStaleDetectionIsClearedPerProfile:
    """A probe result describes one model. Retype the model and the old
    answer is about something else."""

    def test_changing_the_model_clears_that_profiles_detection(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        cfg.profiles["chat"].tools_detected = True
        cfg.profiles["chat"].thinking_detected = True
        fake = _fake_dialog(cfg, shown="chat")
        fake.model_edit.text.return_value = "some-other-model"

        SettingsDialog._commit_profile_fields(fake)

        prof = cfg.profiles["chat"]
        assert prof.model == "some-other-model"
        assert prof.vision_detected is None
        assert prof.tools_detected is None
        assert prof.thinking_detected is None

    def test_unchanged_model_keeps_the_detection(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        cfg.profiles["chat"].tools_detected = False
        fake = _fake_dialog(cfg, shown="chat")

        SettingsDialog._commit_profile_fields(fake)

        assert cfg.profiles["chat"].vision_detected is True
        assert cfg.profiles["chat"].tools_detected is False

    def test_changing_the_provider_clears_that_profiles_detection(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        from freecad_ai.llm.providers import get_provider_names
        cfg = _two_profiles()
        cfg.profiles["chat"].vision_detected = True
        fake = _fake_dialog(cfg, shown="chat")
        names = get_provider_names()
        fake.provider_combo.currentIndex.return_value = names.index("anthropic")

        SettingsDialog._commit_profile_fields(fake)

        assert cfg.profiles["chat"].name == "anthropic"
        assert cfg.profiles["chat"].vision_detected is None

    def test_the_other_profiles_detection_is_untouched(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        cfg.profiles["rerank"].vision_detected = False
        fake = _fake_dialog(cfg, shown="chat")
        fake.model_edit.text.return_value = "some-other-model"

        SettingsDialog._commit_profile_fields(fake)

        assert cfg.profiles["rerank"].vision_detected is False


@pytestmark_qt
class TestVisionOverrideIsAProfileField:
    def test_commit_writes_the_override_into_the_shown_profile(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        fake = _fake_dialog(cfg, shown="rerank")
        fake._vision_override_value = True

        SettingsDialog._commit_profile_fields(fake)

        assert cfg.profiles["rerank"].vision_override is True
        assert cfg.profiles["chat"].vision_override is None

    def test_showing_a_profile_renders_its_own_flags(self):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        cfg = _two_profiles()
        cfg.profiles["rerank"].vision_detected = False
        fake = _fake_dialog(cfg, shown="chat")

        SettingsDialog._show_profile(fake, "rerank")

        fake._update_vision_ui.assert_called_once_with(cfg.profiles["rerank"])
