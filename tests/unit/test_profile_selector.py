"""Profile management: rename cascades, delete never orphans.

These call the dialog's methods with a fake self carrying only the
attributes they touch, so no Qt dialog has to be constructed.
"""

import copy
import types
from unittest import mock
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

from freecad_ai.config import (  # noqa: E402
    AppConfig,
    PROVIDER_PRESETS,
    ProviderConfig,
)
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
        fake._load_model_params_table = lambda model, cfg=None, profile=None: None
        fake._read_model_params_table = (
            lambda: dict(fake._profiles[fake._current_profile_label].params))
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
        fake._read_strip_thinking_state = lambda: None
        fake._get_default_prompt_text = lambda: ""
        fake._parse_server_address = lambda host, port: ("127.0.0.1", 8765)
        fake._parse_allowed_hosts = lambda text: []
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
        # utility_combos is a plain dict on the real dialog; an unconfigured
        # MagicMock's .items() default-iterates empty, so _collect_utility_
        # profiles({}) == {} — nothing to stub for the utility dropdowns here.

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
        # self._refresh_profile_combo() and self._show_profile(...) below
        # resolve to fake's own auto-stubbed attributes (fake is a bare
        # MagicMock, not a SettingsDialog instance), so their real bodies —
        # and the widgets those bodies touch — never run here.

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


# ── Params table targets the working-copy profile (fix round 2) ────────
#
# The dialog's Model Parameters table used to read and write
# cfg.model_params only, while resolve_params() layered profile.params on
# top of it — and migration copied a profile's starting params into both
# places. So for any migrated profile the profile layer always won and
# every edit made through the dialog was silently discarded: demonstrated
# against a real config, temperature 1 -> 0.2 in the dialog, saved, runtime
# still resolved 1.
#
# The final review found the other half of the same root cause: the
# underlay itself. Nothing could write cfg.model_params, so removing a row
# deleted the key from the profile and the underlay restored it. The
# profile is now the sole source (see TestParamsTableShowsOnlyTheProfile
# and tests/unit/test_create_client.py::TestParamLayering).

class TestParamsTableEditSurvivesSaveAndResolve:
    """The regression, end to end — this is the test that would have
    caught the bug."""

    def _migrated_cfg(self):
        """A profile whose params were also copied into cfg.model_params
        by migration — the exact shape that hid the bug."""
        cfg = AppConfig()
        cfg.profiles = {
            "cloud": ProviderConfig(
                name="anthropic", model="claude-sonnet-4-6",
                params={"temperature": 1}),
        }
        cfg.active_profile = "cloud"
        cfg.model_params = {"claude-sonnet-4-6": {"temperature": 1}}
        return cfg

    def _fake_for_save(self, cfg, table_params):
        """Mirrors TestSaveWritesBackProfileState's fake — _save touches a
        lot of unrelated widgets that only need to not raise."""
        prof = cfg.profiles[cfg.active_profile]
        working = copy.deepcopy(cfg.profiles)

        fake = MagicMock()
        fake._profiles = working
        fake._active_profile = cfg.active_profile
        fake._current_profile_label = cfg.active_profile
        fake._commit_profile_fields = (
            lambda: SettingsDialog._commit_profile_fields(fake))
        fake._read_model_params_table = lambda: dict(table_params)
        fake._read_strip_thinking_state = lambda: None
        fake._get_default_prompt_text = lambda: ""
        fake._parse_server_address = lambda host, port: ("127.0.0.1", 8765)
        fake._parse_allowed_hosts = lambda text: []
        fake.accept = lambda: None

        names = get_provider_names()
        fake.provider_combo.currentIndex.return_value = names.index(prof.name)
        fake.api_key_edit.text.return_value = prof.api_key
        fake.base_url_edit.text.return_value = prof.base_url
        fake.model_edit.text.return_value = prof.model
        fake.thinking_combo.currentIndex.return_value = 0
        fake.viewport_capture_combo.currentIndex.return_value = 0
        fake.viewport_resolution_combo.currentIndex.return_value = 0
        fake.rerank_method_combo.currentIndex.return_value = 0
        fake.system_prompt_edit.toPlainText.return_value = ""
        fake.rerank_pinned_edit.text.return_value = ""
        return fake

    def test_edit_survives_save_and_resolve_params(self, monkeypatch):
        from freecad_ai.llm.client import resolve_params

        cfg = self._migrated_cfg()
        fake = self._fake_for_save(cfg, {"temperature": 0.2})

        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)

        SettingsDialog._save(fake)

        assert resolve_params(cfg, cfg.provider)["temperature"] == 0.2


class TestParamsTableShowsOnlyTheProfile:
    """The table shows exactly what resolve_params() will compute — which
    is the profile's own params, and nothing from the legacy
    cfg.model_params dict."""

    def _fake(self, cfg, label):
        prof = cfg.profiles[label]
        fake = types.SimpleNamespace(
            _cfg=cfg,
            _profiles=cfg.profiles,
            _current_profile_label=label,
            provider_combo=_Combo(get_provider_names().index(prof.name)),
        )
        fake._captured = {}
        fake._populate_model_params_table = fake._captured.update
        return fake

    def test_profile_params_are_shown(self):
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        prof.params = {"temperature": 0.9, "top_p": 0.5}

        fake = self._fake(cfg, "cloud")
        SettingsDialog._load_model_params_table(fake, prof.model, cfg, prof)

        assert fake._captured == {"temperature": 0.9, "top_p": 0.5}

    def test_legacy_model_params_never_reach_the_table(self):
        """Re-opening the dialog re-rendered a removed row because the
        table drew the merge. It must draw the profile."""
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        prof.params = {"temperature": 0.9}
        cfg.model_params = {prof.model: {"temperature": 0.1, "max_tokens": 999}}

        fake = self._fake(cfg, "cloud")
        SettingsDialog._load_model_params_table(fake, prof.model, cfg, prof)

        assert fake._captured == {"temperature": 0.9}

    def test_empty_profile_falls_back_to_the_provider_preset(self):
        """Both empty-case fallbacks are unchanged by the fix."""
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        prof.params = {}
        cfg.model_params = {prof.model: {"temperature": 0.1}}

        fake = self._fake(cfg, "cloud")
        SettingsDialog._load_model_params_table(fake, prof.model, cfg, prof)

        expected = dict(PROVIDER_PRESETS["anthropic"].get("default_params", {}))
        if expected:
            assert fake._captured == expected
        else:
            assert fake._captured == {"temperature": cfg.temperature}

    def test_empty_profile_and_empty_preset_falls_back_to_temperature(self):
        cfg = _cfg()
        cfg.temperature = 0.42
        prof = cfg.profiles["cloud"]
        prof.params = {}

        fake = self._fake(cfg, "cloud")
        # An index with no default_params of its own, so only the last
        # fallback can fire.
        with mock.patch.dict(
                "freecad_ai.ui.settings_dialog.PROVIDER_PRESETS",
                {"anthropic": {"base_url": "", "default_model": "",
                               "default_params": {}}}):
            SettingsDialog._load_model_params_table(fake, prof.model, cfg, prof)

        assert fake._captured == {"temperature": 0.42}


class TestParamsDoNotLeakBetweenProfiles:
    """Switching profiles must not let one profile's params bleed into,
    or overwrite, another's."""

    def test_switching_a_b_a_keeps_each_profiles_own_params(self):
        cfg = AppConfig()
        cfg.profiles = {
            "a": ProviderConfig(name="anthropic", model="model-a",
                                params={"temperature": 0.1}),
            "b": ProviderConfig(name="anthropic", model="model-b",
                                params={"temperature": 0.9}),
        }
        cfg.active_profile = "a"
        table = {}

        fake = types.SimpleNamespace(
            _cfg=cfg,
            _profiles=cfg.profiles,
            _current_profile_label="a",
            base_url_edit=_Edit(cfg.profiles["a"].base_url),
            api_key_edit=_Edit(cfg.profiles["a"].api_key),
            model_edit=_Edit(cfg.profiles["a"].model),
            provider_combo=_Combo(get_provider_names().index("anthropic")),
        )
        fake._populate_model_params_table = (
            lambda params: (table.clear(), table.update(params)))
        fake._read_model_params_table = lambda: dict(table)
        fake._load_model_params_table = (
            lambda model, cfg=None, profile=None:
                SettingsDialog._load_model_params_table(
                    fake, model, cfg, profile))

        SettingsDialog._show_profile(fake, "a")
        assert table["temperature"] == 0.1

        SettingsDialog._commit_profile_fields(fake)
        SettingsDialog._show_profile(fake, "b")
        assert table["temperature"] == 0.9

        SettingsDialog._commit_profile_fields(fake)
        SettingsDialog._show_profile(fake, "a")
        assert table["temperature"] == 0.1

        assert cfg.profiles["a"].params["temperature"] == 0.1
        assert cfg.profiles["b"].params["temperature"] == 0.9


class TestOnModelChangedDoesNotMutateLiveConfig:
    """Same class of defect round 1 removed elsewhere: this must write to
    the working-copy profile, never cfg.model_params on the singleton."""

    def test_stashes_into_profile_not_live_config(self):
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        original_model_params = copy.deepcopy(cfg.model_params)

        fake = types.SimpleNamespace(
            _cfg=cfg,
            _profiles=cfg.profiles,
            _current_profile_label="cloud",
            _last_model_name=prof.model,
            model_edit=_Edit("new-model-name"),
            provider_combo=_Combo(get_provider_names().index(prof.name)),
        )
        fake._read_model_params_table = lambda: {"temperature": 0.42}
        fake._populate_model_params_table = lambda params: None

        def _stub_load(model, cfg=None, profile=None):
            fake._last_model_name = model
        fake._load_model_params_table = _stub_load

        SettingsDialog._on_model_changed(fake)

        assert cfg.model_params == original_model_params
        assert prof.params == {"temperature": 0.42}
        assert fake._last_model_name == "new-model-name"


class TestSaveTempLeavesParamsAlone:
    """_save_temp's model-params write fed nothing (_test_connection reads
    the table directly) and only leaked edits to disk via the vision
    probe's save. It must leave cfg.model_params/cfg.temperature alone."""

    def test_save_temp_does_not_touch_model_params_or_temperature(
            self, monkeypatch):
        cfg = _cfg()
        cfg.model_params = {"claude-sonnet-4-6": {"temperature": 0.1}}
        cfg.temperature = 0.1
        original_model_params = copy.deepcopy(cfg.model_params)

        fake = MagicMock()
        fake.model_edit.text.return_value = "claude-sonnet-4-6"
        fake._read_model_params_table.return_value = {"temperature": 0.9}
        fake.thinking_combo.currentIndex.return_value = 0
        fake.system_prompt_edit.toPlainText.return_value = ""
        fake._get_default_prompt_text = lambda: ""

        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)

        SettingsDialog._save_temp(fake)

        assert cfg.model_params == original_model_params
        assert cfg.temperature == 0.1


class TestTestConnectionKeyResolution:
    """Defect A: the probe must resolve the key exactly as create_client()
    does, or a profile that inherits the vendor-wide key fails Test
    Connection even though real chat works."""

    def _fake(self, cfg, api_key_text, provider_name="anthropic"):
        idx = get_provider_names().index(provider_name)
        fake = MagicMock()
        fake._cfg = cfg
        fake.provider_combo.currentIndex.return_value = idx
        fake.base_url_edit.text.return_value = "http://example/v1"
        fake.api_key_edit.text.return_value = api_key_text
        fake.model_edit.text.return_value = "some-model"
        fake._read_model_params_table.return_value = {}
        return fake

    def _capture_thread_api_key(self, monkeypatch, captured):
        def fake_thread(provider_name, base_url, api_key, model,
                        model_params, parent):
            captured["api_key"] = api_key
            return MagicMock()
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog._TestConnectionThread", fake_thread)

    def test_profile_key_wins_over_provider_keys(self, monkeypatch):
        cfg = _cfg()
        cfg.provider_keys = {"anthropic": "vendor-default"}
        fake = self._fake(cfg, "profile-key")
        captured = {}
        self._capture_thread_api_key(monkeypatch, captured)

        SettingsDialog._test_connection(fake)

        assert captured["api_key"] == "profile-key"

    def test_blank_profile_key_falls_back_to_provider_keys(self, monkeypatch):
        cfg = _cfg()
        cfg.provider_keys = {"anthropic": "vendor-default"}
        fake = self._fake(cfg, "")
        captured = {}
        self._capture_thread_api_key(monkeypatch, captured)

        SettingsDialog._test_connection(fake)

        assert captured["api_key"] == "vendor-default"

    def test_both_blank_yields_empty_string(self, monkeypatch):
        cfg = _cfg()
        cfg.provider_keys = {}
        fake = self._fake(cfg, "")
        captured = {}
        self._capture_thread_api_key(monkeypatch, captured)

        SettingsDialog._test_connection(fake)

        assert captured["api_key"] == ""
