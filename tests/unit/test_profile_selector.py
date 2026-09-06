"""Profile management: rename cascades, delete never orphans.

These call the dialog's methods with a fake self carrying only the
attributes they touch, so no Qt dialog has to be constructed.
"""

import copy
import types
from unittest.mock import MagicMock

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
from freecad_ai.llm.providers import get_provider_names  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


class _FakeSelf:
    """Stand-in for the dialog as it looks post-fix: profile edits land in
    a dialog-local working copy, not in ``cfg`` directly.

    ``_profiles``/``_utility_profiles`` alias ``cfg``'s own dicts rather than
    copying them — the rename/delete methods under test here only ever
    mutate a *profile's* contents in place or the containing dict's items in
    place, never reassign the whole dict, so aliasing keeps the pre-existing
    assertions on ``cfg.profiles``/``cfg.utility_profiles`` valid. The one
    exception is ``_active_profile`` (a plain string): a method that
    reassigns it rebinds the fake's own attribute, not anything on ``cfg``,
    so callers that care about the new active profile must read
    ``fake._active_profile`` — see TestRenameCascade.test_active_profile_follows
    and TestDelete.test_deleting_the_active_profile_moves_the_pointer.
    """

    def __init__(self, cfg):
        self._cfg = cfg
        self._profiles = cfg.profiles
        self._active_profile = cfg.active_profile
        self._utility_profiles = cfg.utility_profiles


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
        fake = _FakeSelf(cfg)
        SettingsDialog._rename_profile(fake, "local", "ollama-local")
        assert set(fake._profiles) == {"cloud", "ollama-local"}

    def test_settings_travel_with_the_name(self):
        cfg = _cfg()
        fake = _FakeSelf(cfg)
        SettingsDialog._rename_profile(fake, "local", "ollama-local")
        assert fake._profiles["ollama-local"].model == "qwen3:8b"

    def test_active_profile_follows(self):
        cfg = _cfg()
        fake = _FakeSelf(cfg)
        SettingsDialog._rename_profile(fake, "cloud", "anthropic-main")
        assert fake._active_profile == "anthropic-main"

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
        fake = _FakeSelf(cfg)
        SettingsDialog._rename_profile(fake, "cloud", "zzz")
        assert list(fake._profiles) == ["zzz", "local"]

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
        fake = _FakeSelf(cfg)
        SettingsDialog._rename_profile(fake, "local", "local")
        assert set(fake._profiles) == {"cloud", "local"}


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
        fake = _FakeSelf(cfg)
        SettingsDialog._delete_profile(fake, "cloud")
        assert fake._active_profile == "local"

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
            _profiles=cfg.profiles,
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


# ── Dialog-local working state (fix round) ─────────────────────────
#
# Task 7 wired profile add/rename/delete/edit straight into the live
# `get_config()` singleton. Cancel is a bare `self.reject()` with no
# rollback, so OK and Cancel did the same thing to profiles, and an
# unrelated `save_current_config()` (the vision probe after Test
# Connection) could flush a discarded edit to disk. The fix gives the
# dialog its own `_profiles`/`_active_profile`/`_utility_profiles`
# working copy, populated by `copy.deepcopy` in `_load_from_config`,
# and only `_save` writes it back into the real config.
#
# These fakes deliberately do NOT alias `cfg` the way `_FakeSelf` above
# does — that aliasing is exactly what let the Task 7 bug hide from the
# rename/delete unit tests (singleton identity never entered an
# assertion). Here the config object and the working copy must be able
# to diverge, so each fake carries its own independent `_profiles`.

class TestCancelDiscardsProfileEdits:
    """Reviewer finding 1 / the fix's core defect: a delete during the
    dialog session must not reach `cfg` until `_save` runs."""

    def test_delete_leaves_the_config_object_untouched(self):
        cfg = _cfg()
        fake = types.SimpleNamespace(
            _profiles=copy.deepcopy(cfg.profiles),
            _active_profile=cfg.active_profile,
            _utility_profiles=dict(cfg.utility_profiles),
        )

        SettingsDialog._delete_profile(fake, "local")

        # The assertion that matters is on cfg, not the working copy —
        # Cancel (a bare reject(), no rollback) relies on cfg never having
        # been touched in the first place.
        assert "local" in cfg.profiles
        # And the working copy did register the delete, so _save (below)
        # has something real to write back on OK.
        assert "local" not in fake._profiles


class TestSaveWritesBackProfileState:
    """OK persists: `_save` must copy the dialog's working profile state
    into the real config, including a profile added during the session."""

    def test_save_writes_profiles_and_active_profile_back(self, monkeypatch):
        cfg = _cfg()
        working = copy.deepcopy(cfg.profiles)
        working["added"] = ProviderConfig(
            name="ollama", base_url="http://localhost:11434/v1",
            model="added-model")
        names = get_provider_names()
        idx = names.index("ollama")

        fake = MagicMock()
        fake._profiles = working
        fake._active_profile = "added"
        fake._current_profile_label = "added"
        # _commit_profile_fields is the real method — it is what reads the
        # (fake) widgets below into the working profile before the write-back.
        fake._commit_profile_fields = (
            lambda: SettingsDialog._commit_profile_fields(fake))
        fake._read_model_params_table = lambda: {}
        fake._read_rerank_params_table = lambda: {}
        fake._read_strip_thinking_state = lambda: None
        fake._get_default_prompt_text = lambda: ""
        fake._parse_server_address = lambda host, port: ("127.0.0.1", 8765)
        fake._parse_allowed_hosts = lambda text: []
        fake._resolve_rerank_params = lambda model, params: {}
        fake.accept = lambda: None

        fake.provider_combo.currentIndex.return_value = idx
        fake.api_key_edit.text.return_value = "k-added"
        fake.base_url_edit.text.return_value = "http://localhost:11434/v1"
        fake.model_edit.text.return_value = "added-model"
        fake.thinking_combo.currentIndex.return_value = 0
        fake.viewport_capture_combo.currentIndex.return_value = 0
        fake.viewport_resolution_combo.currentIndex.return_value = 0
        fake.rerank_method_combo.currentIndex.return_value = 0
        fake.system_prompt_edit.toPlainText.return_value = ""
        fake.rerank_pinned_edit.text.return_value = ""
        fake.rerank_llm_provider_combo.currentData.return_value = ""
        fake.rerank_llm_base_url_edit.text.return_value = ""
        fake.rerank_llm_api_key_edit.text.return_value = ""
        fake.rerank_llm_model_edit.text.return_value = ""

        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)

        SettingsDialog._save(fake)

        assert set(cfg.profiles) == {"cloud", "local", "added"}
        assert cfg.active_profile == "added"
        assert cfg.profiles["added"].model == "added-model"


class TestWorkingCopyIsIndependent:
    """A shallow `dict(cfg.profiles)` would pass both tests above and still
    fail this one — the ProviderConfig objects would be shared, so editing
    the working copy would edit cfg through the back door. Pins `deepcopy`
    specifically."""

    def test_mutating_the_working_copy_leaves_cfg_alone(self, monkeypatch):
        cfg = _cfg()
        fake = MagicMock()
        # The one read-back _load_from_config makes: MagicMock's default
        # return value doesn't compare to an int, so the widget needs a
        # real one here or `if provider_idx >= 0` raises.
        fake.rerank_llm_provider_combo.findData.return_value = -1

        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)

        SettingsDialog._load_from_config(fake)

        # First pin that a working copy was actually populated (fails loudly,
        # rather than vacuously passing below, if _load_from_config never
        # sets self._profiles at all).
        assert fake._profiles["local"].model == cfg.profiles["local"].model

        fake._profiles["local"].base_url = "http://mutated:9999/v1"

        assert cfg.profiles["local"].base_url != "http://mutated:9999/v1"


class TestSaveTempDoesNotMutateStoredProfile:
    """Change 3: Test Connection must stop smuggling the visible widget
    values into the active profile through `_save_temp`. Once the working
    state can diverge from cfg, the visible profile may not even be
    cfg.active_profile — see the fix brief for the concrete hazard."""

    def test_save_temp_leaves_provider_untouched(self, monkeypatch):
        cfg = _cfg()
        original_base_url = cfg.provider.base_url
        original_name = cfg.provider.name

        fake = MagicMock()
        fake.provider_combo.currentIndex.return_value = 0
        fake.model_edit.text.return_value = "typed-model"
        fake.base_url_edit.text.return_value = "http://typed:1234/v1"
        fake.api_key_edit.text.return_value = "typed-key"
        fake.thinking_combo.currentIndex.return_value = 0
        fake.system_prompt_edit.toPlainText.return_value = ""
        fake._read_model_params_table = lambda: {}
        fake._get_default_prompt_text = lambda: ""

        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)

        SettingsDialog._save_temp(fake)

        assert cfg.provider.base_url == original_base_url
        assert cfg.provider.name == original_name
