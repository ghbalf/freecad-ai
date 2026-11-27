"""Profile management: rename cascades, delete never orphans.

These call the methods with a fake self carrying only the attributes they
touch, so no Qt widget has to be constructed. The profile handling moved
from SettingsDialog into ProviderSection (#99); the tests of what stayed
in the dialog (the params table, _save, Test Connection) give their fake
a ``provider_section`` instead.
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
from freecad_ai.ui.provider_section import ProviderSection  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402
from freecad_ai.ui.settings_pages.provider_page import ProviderPage  # noqa: E402


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
        self._fallback_profiles = cfg.fallback_profiles
        self._pending_rerank_label = None   # no #10 switch recorded


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
        ProviderSection._rename_profile(fake, "local", "ollama-local")
        assert set(fake._profiles) == {"cloud", "ollama-local"}

    def test_settings_travel_with_the_name(self):
        cfg = _cfg()
        fake = _FakeSelf(cfg)
        ProviderSection._rename_profile(fake, "local", "ollama-local")
        assert fake._profiles["ollama-local"].model == "qwen3:8b"

    def test_active_profile_follows(self):
        cfg = _cfg()
        fake = _FakeSelf(cfg)
        ProviderSection._rename_profile(fake, "cloud", "anthropic-main")
        assert fake._active_profile == "anthropic-main"

    def test_utility_mappings_follow(self):
        """A rename must never silently detach a utility from the profile
        it was using."""
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "local", "rerank": "local"}
        ProviderSection._rename_profile(_FakeSelf(cfg), "local", "cheap")
        assert cfg.utility_profiles == {"compaction": "cheap", "rerank": "cheap"}

    def test_unrelated_mappings_are_untouched(self):
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "cloud", "rerank": "local"}
        ProviderSection._rename_profile(_FakeSelf(cfg), "local", "cheap")
        assert cfg.utility_profiles["compaction"] == "cloud"

    def test_ordering_is_preserved(self):
        """Rebuilding the dict must not reshuffle the combo on the user."""
        cfg = _cfg()
        fake = _FakeSelf(cfg)
        ProviderSection._rename_profile(fake, "cloud", "zzz")
        assert list(fake._profiles) == ["zzz", "local"]

    def test_collision_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            ProviderSection._rename_profile(_FakeSelf(cfg), "local", "cloud")

    def test_empty_name_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            ProviderSection._rename_profile(_FakeSelf(cfg), "local", "  ")

    def test_renaming_to_itself_is_a_no_op(self):
        cfg = _cfg()
        fake = _FakeSelf(cfg)
        ProviderSection._rename_profile(fake, "local", "local")
        assert set(fake._profiles) == {"cloud", "local"}


class TestDelete:
    def test_profile_is_removed(self):
        cfg = _cfg()
        ProviderSection._delete_profile(_FakeSelf(cfg), "local")
        assert set(cfg.profiles) == {"cloud"}

    def test_deleting_the_last_profile_is_refused(self):
        cfg = _cfg()
        del cfg.profiles["local"]
        with pytest.raises(ValueError):
            ProviderSection._delete_profile(_FakeSelf(cfg), "cloud")

    def test_deleting_the_active_profile_moves_the_pointer(self):
        cfg = _cfg()
        fake = _FakeSelf(cfg)
        ProviderSection._delete_profile(fake, "cloud")
        assert fake._active_profile == "local"

    def test_utilities_pointing_at_it_fall_back_to_inherit(self):
        """Leaving a dangling name would work — the resolver tolerates it —
        but clearing it keeps the dialog honest about what is configured."""
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "local"}
        ProviderSection._delete_profile(_FakeSelf(cfg), "local")
        assert cfg.utility_profiles.get("compaction", "") == ""

    def test_deleting_an_unknown_profile_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            ProviderSection._delete_profile(_FakeSelf(cfg), "nope")


class _Sig:
    """Stand-in for a Qt signal on a fake self."""
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Edit:
    """Minimal stand-in for QLineEdit."""

    def __init__(self, text=""):
        self._t = text

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


class _Spin:
    """Minimal stand-in for QSpinBox (the #103 Compact above field)."""

    def __init__(self, value=0):
        self._v = value

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = v


class _Combo:
    """QComboBox stand-in that records the order of calls made to it.

    The recording is the point: #75 was caused by setCurrentIndex firing
    the preset handler during a load, so the guard being present and
    correctly ordered is the behavior under test.
    """

    def __init__(self, index=0):
        self._i = index
        self.calls = []
        self.items = []          # [(text, data)] for count() and removeItem()

    def currentIndex(self):
        return self._i

    def setCurrentIndex(self, i):
        self._i = i
        self.calls.append(("index", i))

    def blockSignals(self, b):
        self.calls.append(("block", b))

    def count(self):
        return len(self.items)

    def addItem(self, text, data=None):
        self.items.append((text, data))

    def removeItem(self, index):
        if 0 <= index < len(self.items):
            self.items.pop(index)


class _Check:
    """QCheckBox stand-in that records blockSignals order."""

    def __init__(self, checked=False, enabled=True):
        self._checked = checked
        self._enabled = enabled
        self.blocked = []

    def setChecked(self, v):
        self._checked = v

    def isChecked(self):
        return self._checked

    def setEnabled(self, v):
        self._enabled = v

    def isEnabled(self):
        return self._enabled

    def blockSignals(self, b):
        self.blocked.append(b)


class _ProfileCombo:
    """QComboBox stand-in carrying a real item model.

    _refresh_profile_combo now renders a label per item and keys the
    selection off itemData, so a stand-in that only records calls can no
    longer tell the two apart.
    """

    def __init__(self):
        self.items = []          # [(text, data)]
        self._i = -1
        self.blocked = []

    def blockSignals(self, b):
        self.blocked.append(b)

    def clear(self):
        self.items = []
        self._i = -1

    def addItem(self, text, data=None):
        self.items.append((text, data))

    def findData(self, data):
        for i, (_text, d) in enumerate(self.items):
            if d == data:
                return i
        return -1

    def setCurrentIndex(self, i):
        self._i = i

    def currentIndex(self):
        return self._i

    def itemData(self, i):
        return self.items[i][1]

    def currentData(self):
        if 0 <= self._i < len(self.items):
            return self.items[self._i][1]
        return None

    def texts(self):
        return [t for t, _d in self.items]

    def data(self):
        return [d for _t, d in self.items]


class TestProfileFieldRoundTrip:
    """Editing a profile, browsing away and back preserves the edit (#75)."""

    def _fake(self, cfg, label):
        import types
        from freecad_ai.llm.providers import get_provider_names
        prof = cfg.profiles[label]
        fake = types.SimpleNamespace(
            _cfg=cfg,
            _profiles=cfg.profiles,
            _active_profile=cfg.active_profile,
            _current_profile_label=label,
            base_url_edit=_Edit(prof.base_url),
            api_key_edit=_Edit(prof.api_key),
            model_edit=_Edit(prof.model),
            compact_above_spin=_Spin(),
            provider_combo=_Combo(get_provider_names().index(prof.name)),
            profile_active_check=_Check(),
            profileShown=_Sig(),
            aboutToCommit=_Sig(),
            presetApplied=_Sig(),
        )
        fake._update_vision_ui = lambda profile: None
        fake._fill_thinking_combo = lambda api_style: None
        fake._show_thinking = lambda value: None
        fake._unknown_item_index = lambda: ProviderSection._unknown_item_index(fake)
        fake._drop_unknown_item = lambda: ProviderSection._drop_unknown_item(fake)
        return fake

    def test_edited_base_url_survives_switching_away_and_back(self):
        from freecad_ai.config import AppConfig, ProviderConfig
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
        ProviderSection._commit_profile_fields(fake)
        assert cfg.profiles["local"].base_url == "http://spark-2448:11434/v1"

        # Browse to the other profile and back.
        ProviderSection._show_profile(fake, "main")
        assert fake.base_url_edit.text() == "https://api.anthropic.com/v1"
        assert cfg.profiles["local"].base_url == "http://spark-2448:11434/v1"

        ProviderSection._show_profile(fake, "local")
        assert fake.base_url_edit.text() == "http://spark-2448:11434/v1"

    def test_programmatic_provider_move_is_signal_guarded(self):
        from freecad_ai.config import AppConfig, ProviderConfig
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
        ProviderSection._show_profile(fake, "local")

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
            _fallback_profiles=list(cfg.fallback_profiles),
        )

        ProviderSection._delete_profile(fake, "local")

        # The assertion that matters is on cfg, not the working copy —
        # Cancel (a bare reject(), no rollback) relies on cfg never having
        # been touched in the first place.
        assert "local" in cfg.profiles
        # And the working copy did register the delete, so _save (below)
        # has something real to write back on OK.
        assert "local" not in fake._profiles


def _fake_save_dialog():
    """MagicMock fake for SettingsDialog._save. _save now only orchestrates
    the four pages, each MagicMocked here and covered by its own tests
    (TestRoundTrip in test_provider_section.py, and each page's own test
    module); this fake only needs to not raise."""
    fake = MagicMock()
    fake.accept = lambda: None
    return fake


class TestSaveHandsTheProfilesToTheSection:
    """OK persists: `_save` commits the section, confirms, then has the
    section write its working copy into the real config."""

    def test_save_hands_the_profiles_to_the_section(self, monkeypatch):
        cfg = AppConfig()
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)
        fake = _fake_save_dialog()
        fake._confirm_incomplete_profiles.return_value = True
        SettingsDialog._save(fake)
        fake.provider_page.section.commit.assert_called_once_with()
        fake._confirm_incomplete_profiles.assert_called_once_with(
            fake.provider_page.section.profiles.return_value)
        fake.provider_page.apply_to.assert_called_once_with(cfg)

    def test_commit_happens_before_the_confirmation_and_the_write(
            self, monkeypatch):
        cfg = AppConfig()
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)
        fake = _fake_save_dialog()
        order = []
        fake.provider_page.section.commit.side_effect = (
            lambda: order.append("commit"))
        fake._confirm_incomplete_profiles.side_effect = (
            lambda profiles: order.append("confirm") or True)
        fake.provider_page.apply_to.side_effect = (
            lambda c: order.append("apply"))
        SettingsDialog._save(fake)
        assert order == ["commit", "confirm", "apply"]

    def test_a_declined_confirmation_writes_nothing(self, monkeypatch):
        cfg = AppConfig()
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)
        fake = _fake_save_dialog()
        fake._confirm_incomplete_profiles.return_value = False
        SettingsDialog._save(fake)
        fake.provider_page.apply_to.assert_not_called()


class TestLoadHandsTheConfigToTheSection:
    """The working copy is the section's now: ProviderSection.load deep-
    copies (TestRoundTrip::test_mutating_the_working_copy_leaves_cfg_alone
    in test_provider_section.py pins the deepcopy). The dialog's part is
    handing it the config."""

    def test_load_from_config_loads_the_section(self, monkeypatch):
        cfg = _cfg()
        fake = MagicMock()
        fake._pages = lambda: SettingsDialog._pages(fake)
        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog.get_config", lambda: cfg)

        SettingsDialog._load_from_config(fake)

        fake.provider_page.load.assert_called_once_with(cfg)


# Test Connection used to stage the visible widget values in the config
# singleton via _save_temp, and two classes here pinned the values it must
# leave alone (the connection fields, then model_params/temperature). #76
# removed the helper outright — the probe thread is handed its inputs — so
# the successor assertion is that _test_connection writes *nothing* into the
# singleton at all. It lives in test_test_connection_config_scope.py.


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

# The end-to-end regression (table edit -> _save -> resolve_params) needs
# the section and the table actually wired together, so it runs on a real
# dialog: test_settings_dialog_section_wiring.py::
# test_edit_survives_save_and_resolve_params.


class TestParamsTableShowsOnlyTheProfile:
    """The table shows exactly what resolve_params() will compute — which
    is the profile's own params, and nothing from the legacy
    cfg.model_params dict."""

    def _fake(self, cfg, label):
        prof = cfg.profiles[label]
        section = MagicMock()
        section.current_provider_name.return_value = prof.name
        section.current_profile.return_value = prof
        fake = types.SimpleNamespace(_cfg=cfg, section=section)
        fake._captured = {}
        fake._populate_model_params_table = fake._captured.update
        return fake

    def test_profile_params_are_shown(self):
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        prof.params = {"temperature": 0.9, "top_p": 0.5}

        fake = self._fake(cfg, "cloud")
        ProviderPage._load_model_params_table(fake, prof.model, cfg, prof)

        assert fake._captured == {"temperature": 0.9, "top_p": 0.5}

    def test_legacy_model_params_never_reach_the_table(self):
        """Re-opening the dialog re-rendered a removed row because the
        table drew the merge. It must draw the profile."""
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        prof.params = {"temperature": 0.9}
        cfg.model_params = {prof.model: {"temperature": 0.1, "max_tokens": 999}}

        fake = self._fake(cfg, "cloud")
        ProviderPage._load_model_params_table(fake, prof.model, cfg, prof)

        assert fake._captured == {"temperature": 0.9}

    def test_empty_profile_falls_back_to_the_provider_preset(self):
        """Both empty-case fallbacks are unchanged by the fix."""
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        prof.params = {}
        cfg.model_params = {prof.model: {"temperature": 0.1}}

        fake = self._fake(cfg, "cloud")
        ProviderPage._load_model_params_table(fake, prof.model, cfg, prof)

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
                "freecad_ai.ui.settings_pages.provider_page.PROVIDER_PRESETS",
                {"anthropic": {"base_url": "", "default_model": "",
                               "default_params": {}}}):
            ProviderPage._load_model_params_table(fake, prof.model, cfg, prof)

        assert fake._captured == {"temperature": 0.42}


# Switching profiles a -> b -> a must not let one profile's params bleed
# into another's. That is the section's signals driving the dialog's table,
# so it runs on a real dialog: test_settings_dialog_section_wiring.py::
# test_a_b_a_keeps_each_profiles_own_params.


class TestOnModelChangedDoesNotMutateLiveConfig:
    """Same class of defect round 1 removed elsewhere: this must write to
    the working-copy profile, never cfg.model_params on the singleton."""

    def test_stashes_into_profile_not_live_config(self):
        cfg = _cfg()
        prof = cfg.profiles["cloud"]
        original_model_params = copy.deepcopy(cfg.model_params)

        section = MagicMock()
        section.current_profile.return_value = prof
        fake = types.SimpleNamespace(
            _cfg=cfg,
            _last_model_name=prof.model,
            section=section,
            _loaded_params={},
        )
        fake._read_model_params_table = lambda: {"temperature": 0.42}
        fake._populate_model_params_table = lambda params: None

        def _stub_load(model, cfg=None, profile=None):
            fake._last_model_name = model
        fake._load_model_params_table = _stub_load

        ProviderPage._on_model_changed(fake, "new-model-name")

        assert cfg.model_params == original_model_params
        assert prof.params == {"temperature": 0.42}
        assert fake._last_model_name == "new-model-name"


class TestTestConnectionKeyResolution:
    """Defect A: the probe must resolve the key exactly as create_client()
    does, or a profile that inherits the vendor-wide key fails Test
    Connection even though real chat works."""

    def _fake(self, cfg, api_key_text, provider_name="anthropic", monkeypatch=None):
        fake = MagicMock()
        fake._cfg = cfg
        fake.section.current_profile.return_value = ProviderConfig(
            name=provider_name, base_url="http://example/v1",
            api_key=api_key_text, model="some-model")
        fake.section.current_label.return_value = "cloud"
        if monkeypatch is not None:
            monkeypatch.setattr(
                "freecad_ai.ui.settings_pages.provider_page.get_config",
                lambda: cfg)
        return fake

    def _capture_thread_api_key(self, monkeypatch, captured):
        def fake_thread(provider_name, base_url, api_key, model,
                        model_params, **kwargs):
            captured["api_key"] = api_key
            return MagicMock()
        monkeypatch.setattr(
            "freecad_ai.ui.settings_pages.provider_page._TestConnectionThread",
            fake_thread)

    def test_profile_key_wins_over_provider_keys(self, monkeypatch):
        cfg = _cfg()
        cfg.provider_keys = {"anthropic": "vendor-default"}
        fake = self._fake(cfg, "profile-key", monkeypatch=monkeypatch)
        captured = {}
        self._capture_thread_api_key(monkeypatch, captured)

        ProviderPage._test_connection(fake)

        assert captured["api_key"] == "profile-key"

    def test_blank_profile_key_falls_back_to_provider_keys(self, monkeypatch):
        cfg = _cfg()
        cfg.provider_keys = {"anthropic": "vendor-default"}
        fake = self._fake(cfg, "", monkeypatch=monkeypatch)
        captured = {}
        self._capture_thread_api_key(monkeypatch, captured)

        ProviderPage._test_connection(fake)

        assert captured["api_key"] == "vendor-default"

    def test_both_blank_yields_empty_string(self, monkeypatch):
        cfg = _cfg()
        cfg.provider_keys = {}
        fake = self._fake(cfg, "", monkeypatch=monkeypatch)
        captured = {}
        self._capture_thread_api_key(monkeypatch, captured)

        ProviderPage._test_connection(fake)

        assert captured["api_key"] == ""


# ── Selected profile vs active profile (final review, finding 3) ───────
#
# The combo meant both "edit this one" and "chat runs on this one":
# _on_profile_changed and _on_profile_add each assigned _active_profile,
# and _save committed it. So opening Settings to raise Max Tokens,
# clicking another profile to see which model it used, setting Max Tokens
# and pressing OK moved chat onto that other profile with nothing having
# said so. An explicit "Use this profile for chat" checkbox separates the
# two; the combo shows which profile is active by labelling it.

def _selector_fake(cfg, label="cloud"):
    """Fake self carrying the widgets the profile-selector methods touch."""
    fake = types.SimpleNamespace(
        _cfg=cfg,
        _profiles=cfg.profiles,
        _active_profile=cfg.active_profile,
        _utility_profiles=cfg.utility_profiles,
        _fallback_profiles=cfg.fallback_profiles,
        _current_profile_label=label,
        _pending_rerank_label=None,
        profile_combo=_ProfileCombo(),
        profile_active_check=_Check(),
        api_key_edit=_Edit(),
        base_url_edit=_Edit(),
        model_edit=_Edit(),
        compact_above_spin=_Spin(),
        provider_combo=_Combo(get_provider_names().index("anthropic")),
        utility_combos={},
        profileShown=_Sig(),
        aboutToCommit=_Sig(),
        presetApplied=_Sig(),
    )
    fake._commit_profile_fields = (
        lambda: ProviderSection._commit_profile_fields(fake))
    fake._show_profile = lambda lbl: ProviderSection._show_profile(fake, lbl)
    fake._refresh_profile_combo = (
        lambda: ProviderSection._refresh_profile_combo(fake))
    fake._refresh_utility_combos = lambda: None
    fake._refresh_fallback_widgets = lambda: None
    fake._rename_profile = (
        lambda old, new: ProviderSection._rename_profile(fake, old, new))
    fake._update_vision_ui = lambda profile: None
    fake._fill_thinking_combo = lambda api_style: None
    fake._show_thinking = lambda value: None
    fake._unknown_item_index = lambda: ProviderSection._unknown_item_index(fake)
    fake._drop_unknown_item = lambda: ProviderSection._drop_unknown_item(fake)
    return fake


class TestBrowsingDoesNotMoveActive:
    def test_selecting_another_profile_leaves_active_alone(self):
        cfg = _cfg()
        fake = _selector_fake(cfg)
        fake.profile_combo.addItem("local", "local")

        ProviderSection._on_profile_changed(fake, 0)

        assert fake._current_profile_label == "local"
        assert fake._active_profile == "cloud"

    def test_adding_a_profile_leaves_active_alone(self):
        cfg = _cfg()
        fake = _selector_fake(cfg)

        ProviderSection._on_profile_add(fake)

        assert fake._active_profile == "cloud"
        assert fake._current_profile_label not in ("cloud", "local")
        assert fake._current_profile_label in fake._profiles

    def test_the_new_profile_is_the_one_selected_for_editing(self):
        """The combo must land on the profile just added, not snap back to
        the active one — the trap in re-selecting by _active_profile."""
        cfg = _cfg()
        fake = _selector_fake(cfg)

        ProviderSection._on_profile_add(fake)

        assert fake.profile_combo.currentData() == fake._current_profile_label

    def test_rename_leaves_the_combo_on_the_renamed_profile(self, monkeypatch):
        cfg = _cfg()
        fake = _selector_fake(cfg, label="local")
        monkeypatch.setattr(
            "freecad_ai.ui.provider_section.QInputDialog",
            types.SimpleNamespace(getText=lambda *a, **k: ("cheap", True)))

        ProviderSection._on_profile_rename(fake)

        assert fake._active_profile == "cloud"
        assert fake.profile_combo.currentData() == "cheap"


class TestActiveProfileCheckbox:
    def test_showing_the_active_profile_checks_and_locks_the_box(self):
        """Exactly one profile is always active, so the box is disabled
        while checked: the way to move it is to check a different one."""
        cfg = _cfg()
        fake = _selector_fake(cfg)

        ProviderSection._show_profile(fake, "cloud")

        assert fake.profile_active_check.isChecked() is True
        assert fake.profile_active_check.isEnabled() is False
        assert fake.profile_active_check.blocked == [True, False]

    def test_showing_another_profile_unchecks_and_unlocks_the_box(self):
        cfg = _cfg()
        fake = _selector_fake(cfg)

        ProviderSection._show_profile(fake, "local")

        assert fake.profile_active_check.isChecked() is False
        assert fake.profile_active_check.isEnabled() is True

    def test_ticking_the_box_moves_active(self):
        cfg = _cfg()
        fake = _selector_fake(cfg, label="local")

        ProviderSection._on_profile_active_toggled(fake, True)

        assert fake._active_profile == "local"
        assert fake.profile_active_check.isEnabled() is False

    def test_ticking_the_box_relabels_the_combo(self):
        cfg = _cfg()
        fake = _selector_fake(cfg, label="local")

        ProviderSection._on_profile_active_toggled(fake, True)

        assert fake.profile_combo.texts() == ["cloud", "local (active)"]

    def test_an_untick_is_a_no_op(self):
        """Unreachable through the UI (the widget is disabled while
        checked), and must stay a no-op if it ever arrives anyway —
        there is no such thing as no active profile."""
        cfg = _cfg()
        fake = _selector_fake(cfg, label="local")

        ProviderSection._on_profile_active_toggled(fake, False)

        assert fake._active_profile == "cloud"


class TestProfileComboRendering:
    def test_exactly_one_entry_is_marked_active(self):
        cfg = _cfg()
        fake = _selector_fake(cfg)

        ProviderSection._refresh_profile_combo(fake)

        assert fake.profile_combo.texts() == ["cloud (active)", "local"]

    def test_the_active_marker_is_translated(self, monkeypatch):
        import freecad_ai.ui.provider_section as ps
        monkeypatch.setattr(ps, "translate", lambda c, t: {
            ("SettingsDialog", "{} (active)"): "{} (aktiv)"}.get((c, t), t))
        fake = _selector_fake(_cfg())

        ProviderSection._refresh_profile_combo(fake)

        assert fake.profile_combo.texts() == ["cloud (aktiv)", "local"]

    def test_item_data_stays_the_bare_label(self):
        """_on_profile_changed and findData both key off it."""
        cfg = _cfg()
        fake = _selector_fake(cfg)

        ProviderSection._refresh_profile_combo(fake)

        assert fake.profile_combo.data() == ["cloud", "local"]
        assert fake.profile_combo.findData("cloud") == 0

    def test_selection_follows_the_profile_being_edited(self):
        cfg = _cfg()
        fake = _selector_fake(cfg, label="local")

        ProviderSection._refresh_profile_combo(fake)

        assert fake.profile_combo.currentData() == "local"

    def test_selection_falls_back_to_active_when_the_edited_label_is_gone(self):
        """The delete path: _current_profile_label names the profile that
        was just removed."""
        cfg = _cfg()
        fake = _selector_fake(cfg, label="deleted")

        ProviderSection._refresh_profile_combo(fake)

        assert fake.profile_combo.currentData() == "cloud"

    def test_selection_falls_back_to_the_first_entry(self):
        cfg = _cfg()
        fake = _selector_fake(cfg, label="deleted")
        fake._active_profile = "also-gone"

        ProviderSection._refresh_profile_combo(fake)

        assert fake.profile_combo.currentIndex() == 0

    def test_the_handler_is_blocked_while_repopulating(self):
        cfg = _cfg()
        fake = _selector_fake(cfg)

        ProviderSection._refresh_profile_combo(fake)

        assert fake.profile_combo.blocked == [True, False]
