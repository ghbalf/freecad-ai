"""Saving a profile with no Base URL warns instead of failing at request time.

``LLMClient`` builds every request URL as ``f"{base_url}/chat/completions"``,
so a blank Base URL produces a relative path and a network-layer error far
from the mistake. The profile restructure deliberately does *not* heal this
by falling back to the provider preset (that silent substitution is the
shape of issue #75), so the dialog has to say so out loud at save time.
"""

import copy
import types
from unittest import mock
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


def _profiles(**base_urls):
    return {label: ProviderConfig(name="openai", base_url=url, model="m")
            for label, url in base_urls.items()}


class TestFindsIncompleteProfiles:
    def test_blank_base_url_is_reported_by_label(self):
        found = SettingsDialog._profiles_missing_base_url(
            _profiles(Work="https://api.openai.com/v1", Scratch=""))
        assert found == ["Scratch"]

    def test_whitespace_only_base_url_counts_as_blank(self):
        found = SettingsDialog._profiles_missing_base_url(
            _profiles(Scratch="   "))
        assert found == ["Scratch"]

    def test_complete_profiles_report_nothing(self):
        found = SettingsDialog._profiles_missing_base_url(
            _profiles(Work="https://api.openai.com/v1",
                      Local="http://localhost:11434/v1"))
        assert found == []

    def test_several_incomplete_profiles_are_all_named(self):
        found = SettingsDialog._profiles_missing_base_url(
            _profiles(Zeta="", Alpha="", Work="https://api.openai.com/v1"))
        # Sorted, so the warning reads the same way twice running.
        assert found == ["Alpha", "Zeta"]


class TestTheWarning:
    """The guard asks; it does not decide. A pre-existing config with an
    unused half-filled profile must still be able to save unrelated
    settings, so declining is a veto the user casts, not one we cast."""

    @staticmethod
    def _fake(**base_urls):
        # The real staticmethod, so these exercise the pair as it ships.
        return types.SimpleNamespace(
            _profiles=_profiles(**base_urls),
            _profiles_missing_base_url=SettingsDialog._profiles_missing_base_url,
            _profiles_with_url_placeholder=(
                SettingsDialog._profiles_with_url_placeholder))

    def test_no_warning_when_every_profile_is_complete(self):
        fake = self._fake(Work="https://api.openai.com/v1")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            assert SettingsDialog._confirm_incomplete_profiles(fake) is True
        box.question.assert_not_called()

    def test_confirming_lets_the_save_proceed(self):
        fake = self._fake(Scratch="")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            box.question.return_value = box.Yes
            assert SettingsDialog._confirm_incomplete_profiles(fake) is True
        assert box.question.called

    def test_declining_stops_the_save(self):
        fake = self._fake(Scratch="")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            box.question.return_value = box.No
            assert SettingsDialog._confirm_incomplete_profiles(fake) is False

    def test_the_warning_names_the_offending_profiles(self):
        fake = self._fake(Scratch="", Spare="")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            box.question.return_value = box.Yes
            SettingsDialog._confirm_incomplete_profiles(fake)
        text = " ".join(str(a) for a in box.question.call_args[0])
        assert "Scratch" in text and "Spare" in text


class TestSaveIsWiredToTheGuard:
    """The guard is worth nothing if _save does not consult it, and consult
    it before anything reaches the live config singleton."""

    def _fake(self, allowed):
        return types.SimpleNamespace(
            _commit_profile_fields=MagicMock(),
            _confirm_incomplete_profiles=MagicMock(return_value=allowed),
            accept=MagicMock())

    def test_declining_leaves_the_live_config_untouched(self):
        cfg = AppConfig()
        cfg.profiles = _profiles(Work="https://api.openai.com/v1")
        cfg.active_profile = "Work"
        before = copy.deepcopy(cfg.profiles)
        fake = self._fake(allowed=False)
        with mock.patch("freecad_ai.ui.settings_dialog.get_config",
                        return_value=cfg):
            SettingsDialog._save(fake)
        assert cfg.profiles == before
        fake.accept.assert_not_called()

    def test_the_visible_edits_are_committed_before_the_guard_runs(self):
        # The guard inspects _profiles, so an edit still sitting in the
        # widgets has to land there first or a just-cleared Base URL slips
        # through unremarked.
        cfg = AppConfig()
        fake = self._fake(allowed=False)
        with mock.patch("freecad_ai.ui.settings_dialog.get_config",
                        return_value=cfg):
            SettingsDialog._save(fake)
        fake._commit_profile_fields.assert_called_once()


class TestFindsPlaceholderBaseUrls:
    """A preset URL the user is expected to complete by hand.

    Cloudflare Workers AI is per-account, so its preset ships a
    ``{ACCOUNT_ID}`` marker. Nothing substitutes it, so leaving it in place
    sends literal braces in the request path — a 404 that names neither the
    field nor the fix.
    """

    def test_unsubstituted_placeholder_is_reported_by_label(self):
        found = SettingsDialog._profiles_with_url_placeholder(_profiles(
            Work="https://api.openai.com/v1",
            CF="https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1"))
        assert found == ["CF"]

    def test_a_substituted_url_reports_nothing(self):
        found = SettingsDialog._profiles_with_url_placeholder(_profiles(
            CF="https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"))
        assert found == []

    def test_blank_url_is_not_a_placeholder(self):
        # That case belongs to the other detector; reporting it twice would
        # name the same profile in two warnings.
        assert SettingsDialog._profiles_with_url_placeholder(
            _profiles(Scratch="")) == []

    def test_several_placeholder_profiles_are_all_named(self):
        found = SettingsDialog._profiles_with_url_placeholder(
            _profiles(Zeta="https://h/{ID}/v1", Alpha="https://h/{ID}/v1"))
        assert found == ["Alpha", "Zeta"]

    def test_the_shipped_cloudflare_preset_trips_the_guard(self):
        # Ties the guard to the preset: if the preset ever ships a complete
        # URL, or the marker is renamed, this is where it surfaces.
        from freecad_ai.llm.providers import PROVIDERS
        preset = PROVIDERS["cloudflare-workers-ai"]["base_url"]
        assert SettingsDialog._profiles_with_url_placeholder(
            _profiles(CF=preset)) == ["CF"]


class TestThePlaceholderWarning:
    @staticmethod
    def _fake(**base_urls):
        return types.SimpleNamespace(
            _profiles=_profiles(**base_urls),
            _profiles_missing_base_url=SettingsDialog._profiles_missing_base_url,
            _profiles_with_url_placeholder=(
                SettingsDialog._profiles_with_url_placeholder))

    def test_a_placeholder_profile_is_warned_about(self):
        # The regression: before this guard the URL was non-blank, so the
        # save went through silently and failed at request time instead.
        fake = self._fake(CF="https://h/accounts/{ACCOUNT_ID}/ai/v1")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            box.question.return_value = box.Yes
            assert SettingsDialog._confirm_incomplete_profiles(fake) is True
        text = " ".join(str(a) for a in box.question.call_args[0])
        assert "CF" in text and "ACCOUNT_ID" in text

    def test_declining_stops_the_save(self):
        fake = self._fake(CF="https://h/accounts/{ACCOUNT_ID}/ai/v1")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            box.question.return_value = box.No
            assert SettingsDialog._confirm_incomplete_profiles(fake) is False

    def test_both_kinds_of_problem_are_named_in_one_warning(self):
        fake = self._fake(Scratch="", CF="https://h/accounts/{ACCOUNT_ID}/ai/v1")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            box.question.return_value = box.Yes
            SettingsDialog._confirm_incomplete_profiles(fake)
        assert box.question.call_count == 1
        text = " ".join(str(a) for a in box.question.call_args[0])
        assert "Scratch" in text and "CF" in text

    def test_complete_profiles_still_raise_nothing(self):
        fake = self._fake(Work="https://api.openai.com/v1")
        with mock.patch("freecad_ai.ui.settings_dialog.QMessageBox") as box:
            assert SettingsDialog._confirm_incomplete_profiles(fake) is True
        box.question.assert_not_called()
