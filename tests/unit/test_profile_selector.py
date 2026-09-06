"""Profile management: rename cascades, delete never orphans.

These call the dialog's methods with a fake self carrying only the
attributes they touch, so no Qt dialog has to be constructed.
"""

import pytest

# settings_dialog/chat_widget import through ui/compat.py, which needs Qt.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


class _FakeSelf:
    def __init__(self, cfg):
        self._cfg = cfg


def _cfg():
    cfg = AppConfig()
    cfg.profiles = {
        "cloud": ProviderConfig(name="anthropic", model="claude-sonnet-4-6"),
        "local": ProviderConfig(name="ollama", model="qwen3:8b",
                                base_url="http://localhost:11434/v1"),
    }
    cfg.active_profile = "cloud"
    return cfg


class TestRenameCascade:
    def test_profile_is_renamed(self):
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "ollama-local")
        assert set(cfg.profiles) == {"cloud", "ollama-local"}

    def test_settings_travel_with_the_name(self):
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "ollama-local")
        assert cfg.profiles["ollama-local"].model == "qwen3:8b"

    def test_active_profile_follows(self):
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "cloud", "anthropic-main")
        assert cfg.active_profile == "anthropic-main"

    def test_utility_mappings_follow(self):
        """A rename must never silently detach a utility from the profile
        it was using."""
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "local", "rerank": "local"}
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "cheap")
        assert cfg.utility_profiles == {"compaction": "cheap", "rerank": "cheap"}

    def test_unrelated_mappings_are_untouched(self):
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "cloud", "rerank": "local"}
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "cheap")
        assert cfg.utility_profiles["compaction"] == "cloud"

    def test_ordering_is_preserved(self):
        """Rebuilding the dict must not reshuffle the combo on the user."""
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "cloud", "zzz")
        assert list(cfg.profiles) == ["zzz", "local"]

    def test_collision_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "cloud")

    def test_empty_name_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "  ")

    def test_renaming_to_itself_is_a_no_op(self):
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "local")
        assert set(cfg.profiles) == {"cloud", "local"}


class TestDelete:
    def test_profile_is_removed(self):
        cfg = _cfg()
        SettingsDialog._delete_profile(_FakeSelf(cfg), "local")
        assert set(cfg.profiles) == {"cloud"}

    def test_deleting_the_last_profile_is_refused(self):
        cfg = _cfg()
        del cfg.profiles["local"]
        with pytest.raises(ValueError):
            SettingsDialog._delete_profile(_FakeSelf(cfg), "cloud")

    def test_deleting_the_active_profile_moves_the_pointer(self):
        cfg = _cfg()
        SettingsDialog._delete_profile(_FakeSelf(cfg), "cloud")
        assert cfg.active_profile == "local"

    def test_utilities_pointing_at_it_fall_back_to_inherit(self):
        """Leaving a dangling name would work — the resolver tolerates it —
        but clearing it keeps the dialog honest about what is configured."""
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "local"}
        SettingsDialog._delete_profile(_FakeSelf(cfg), "local")
        assert cfg.utility_profiles.get("compaction", "") == ""

    def test_deleting_an_unknown_profile_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            SettingsDialog._delete_profile(_FakeSelf(cfg), "nope")


class _Edit:
    """Minimal stand-in for QLineEdit."""

    def __init__(self, text=""):
        self._t = text

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


class _Combo:
    """QComboBox stand-in that records the order of calls made to it.

    The recording is the point: #75 was caused by setCurrentIndex firing
    the preset handler during a load, so the guard being present and
    correctly ordered is the behavior under test.
    """

    def __init__(self, index=0):
        self._i = index
        self.calls = []

    def currentIndex(self):
        return self._i

    def setCurrentIndex(self, i):
        self._i = i
        self.calls.append(("index", i))

    def blockSignals(self, b):
        self.calls.append(("block", b))


class TestProfileFieldRoundTrip:
    """Editing a profile, browsing away and back preserves the edit (#75)."""

    def _fake(self, cfg, label):
        import types
        from freecad_ai.llm.providers import get_provider_names
        prof = cfg.profiles[label]
        fake = types.SimpleNamespace(
            _cfg=cfg,
            _current_profile_label=label,
            base_url_edit=_Edit(prof.base_url),
            api_key_edit=_Edit(prof.api_key),
            model_edit=_Edit(prof.model),
            provider_combo=_Combo(get_provider_names().index(prof.name)),
        )
        fake._load_model_params_table = lambda model, cfg=None: None
        return fake

    def test_edited_base_url_survives_switching_away_and_back(self):
        from freecad_ai.config import AppConfig, ProviderConfig
        from freecad_ai.ui.settings_dialog import SettingsDialog

        cfg = AppConfig()
        cfg.profiles = {
            "main": ProviderConfig(name="anthropic",
                                   base_url="https://api.anthropic.com/v1",
                                   api_key="k1", model="m1"),
            "local": ProviderConfig(name="ollama",
                                    base_url="http://localhost:11434/v1",
                                    api_key="", model="m2"),
        }
        cfg.active_profile = "main"

        fake = self._fake(cfg, "local")
        fake.base_url_edit.setText("http://spark-2448:11434/v1")
        SettingsDialog._commit_profile_fields(fake)
        assert cfg.profiles["local"].base_url == "http://spark-2448:11434/v1"

        # Browse to the other profile and back.
        SettingsDialog._show_profile(fake, "main")
        assert fake.base_url_edit.text() == "https://api.anthropic.com/v1"
        assert cfg.profiles["local"].base_url == "http://spark-2448:11434/v1"

        SettingsDialog._show_profile(fake, "local")
        assert fake.base_url_edit.text() == "http://spark-2448:11434/v1"

    def test_programmatic_provider_move_is_signal_guarded(self):
        from freecad_ai.config import AppConfig, ProviderConfig
        from freecad_ai.ui.settings_dialog import SettingsDialog

        cfg = AppConfig()
        cfg.profiles = {
            "main": ProviderConfig(name="anthropic", base_url="u1",
                                   api_key="k1", model="m1"),
            "local": ProviderConfig(name="ollama", base_url="u2",
                                    api_key="", model="m2"),
        }
        cfg.active_profile = "main"

        fake = self._fake(cfg, "main")
        fake.provider_combo.calls.clear()
        SettingsDialog._show_profile(fake, "local")

        # setCurrentIndex must happen between block(True) and block(False),
        # or _on_provider_changed fires and overwrites the profile's URL
        # with the new vendor's preset — which is exactly bug #75.
        kinds = [c[0] for c in fake.provider_combo.calls]
        assert kinds == ["block", "index", "block"]
        assert fake.provider_combo.calls[0][1] is True
        assert fake.provider_combo.calls[2][1] is False
