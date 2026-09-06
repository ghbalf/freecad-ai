"""Both connection probes name the profile they tested.

Reported during the connection-profiles GUI pass: Test Connection on a
freshly created (not yet active) profile shows a red ``HTTP 401`` and
nothing on the row says *which* of the configured profiles the error is
about. The probe is behaving correctly -- it deliberately tests the
profile on screen rather than the active one -- but the status text gave
the user no way to tell that apart from a failure of the active profile.

The same gap applies to Test Reranker, which resolves through the rerank
utility dropdown and so routinely tests a profile that is neither active
nor displayed.

The label is captured when the probe *starts*, so switching profiles
while a probe is in flight cannot mislabel the result that comes back.
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

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


def _cfg():
    cfg = AppConfig()
    cfg.profiles = {
        "moonshot": ProviderConfig(
            name="moonshot", api_key="k", base_url="https://api.moonshot.ai/v1",
            model="kimi-k2.6", params={}),
        "rerank-ollama": ProviderConfig(
            name="ollama", api_key="", base_url="http://localhost:11434/v1",
            model="gemma3:4b", params={}),
    }
    cfg.active_profile = "moonshot"
    cfg.utility_profiles = {}
    return cfg


def _bind_helpers(fake):
    """The status formatters are the code under test — the fake self must
    carry the real ones, not MagicMock stand-ins."""
    fake._probe_running_text = SettingsDialog._probe_running_text
    fake._probe_result_text = SettingsDialog._probe_result_text


def _fake_rerank_dialog(cfg, selection, monkeypatch):
    """A fake self carrying only what _test_reranker touches."""
    monkeypatch.setattr(
        "freecad_ai.ui.settings_dialog._TestRerankerThread",
        lambda *a, **kw: mock.MagicMock())
    combo = mock.MagicMock()
    combo.currentData.return_value = selection
    fake = mock.MagicMock()
    fake._cfg = cfg
    fake._profiles = cfg.profiles
    fake._active_profile = cfg.active_profile
    fake.utility_combos = {"rerank": combo}
    _bind_helpers(fake)
    return fake


def _fake_connection_dialog(cfg, displayed, monkeypatch):
    """A fake self carrying only what _test_connection touches."""
    monkeypatch.setattr(
        "freecad_ai.ui.settings_dialog._TestConnectionThread",
        lambda *a, **kw: mock.MagicMock())
    fake = mock.MagicMock()
    fake._cfg = cfg
    fake._current_profile_label = displayed
    fake.provider_combo.currentIndex.return_value = 0
    fake.base_url_edit.text.return_value = "https://api.anthropic.com"
    fake.api_key_edit.text.return_value = ""
    fake.model_edit.text.return_value = "claude-sonnet-4-6"
    fake._read_model_params_table.return_value = {}
    _bind_helpers(fake)
    return fake


class TestRerankerProbeNamesProfile:

    def test_running_status_names_the_selected_profile(self, monkeypatch):
        cfg = _cfg()
        fake = _fake_rerank_dialog(cfg, "rerank-ollama", monkeypatch)

        SettingsDialog._test_reranker(fake)

        assert fake._rerank_test_status.setText.call_args[0][0] == \
            'Testing "rerank-ollama"...'

    def test_running_status_names_active_profile_when_inheriting(
            self, monkeypatch):
        cfg = _cfg()
        fake = _fake_rerank_dialog(cfg, "", monkeypatch)

        SettingsDialog._test_reranker(fake)

        assert fake._rerank_test_status.setText.call_args[0][0] == \
            'Testing "moonshot"...'

    def test_result_is_labelled_with_the_profile_that_was_probed(
            self, monkeypatch):
        cfg = _cfg()
        fake = _fake_rerank_dialog(cfg, "rerank-ollama", monkeypatch)
        SettingsDialog._test_reranker(fake)

        SettingsDialog._on_rerank_test_finished(fake, True, "Picked: a, b")

        assert fake._rerank_test_status.setText.call_args[0][0] == \
            '"rerank-ollama": OK — Picked: a, b'

    def test_error_is_labelled_with_the_profile_that_was_probed(
            self, monkeypatch):
        cfg = _cfg()
        fake = _fake_rerank_dialog(cfg, "rerank-ollama", monkeypatch)
        SettingsDialog._test_reranker(fake)

        SettingsDialog._on_rerank_test_finished(fake, False, "call failed")

        assert fake._rerank_test_status.setText.call_args[0][0] == \
            '"rerank-ollama": Error: call failed'


class TestConnectionProbeNamesProfile:

    def test_running_status_names_the_displayed_profile(self, monkeypatch):
        cfg = _cfg()
        fake = _fake_connection_dialog(cfg, "New profile", monkeypatch)

        SettingsDialog._test_connection(fake)

        assert fake.test_status.setText.call_args[0][0] == \
            'Testing "New profile"...'

    def test_failure_names_the_profile_not_the_active_one(self, monkeypatch):
        """The #401-on-a-new-profile case from the GUI pass."""
        cfg = _cfg()
        fake = _fake_connection_dialog(cfg, "New profile", monkeypatch)
        SettingsDialog._test_connection(fake)

        SettingsDialog._on_test_finished(fake, False, "HTTP 401: Unauthorized")

        assert fake.test_status.setText.call_args[0][0] == \
            '"New profile": Failed: HTTP 401: Unauthorized'

    def test_success_names_the_displayed_profile(self, monkeypatch):
        cfg = _cfg()
        fake = _fake_connection_dialog(cfg, "moonshot", monkeypatch)
        SettingsDialog._test_connection(fake)

        SettingsDialog._on_test_finished(fake, True, "Connected! Response: hello")

        assert fake.test_status.setText.call_args[0][0] == \
            '"moonshot": Connected! Response: hello'

    def test_label_is_captured_at_start_not_at_finish(self, monkeypatch):
        """Switching profiles mid-probe must not relabel the result."""
        cfg = _cfg()
        fake = _fake_connection_dialog(cfg, "New profile", monkeypatch)
        SettingsDialog._test_connection(fake)

        fake._current_profile_label = "moonshot"  # user switched while testing
        SettingsDialog._on_test_finished(fake, False, "HTTP 401: Unauthorized")

        assert fake.test_status.setText.call_args[0][0] == \
            '"New profile": Failed: HTTP 401: Unauthorized'


class TestUnnamedProfileDegradesToPlainText:
    """No profile label -> today's exact wording, no stray quotes."""

    def test_running_status_without_a_label(self, monkeypatch):
        cfg = _cfg()
        fake = _fake_connection_dialog(cfg, "", monkeypatch)

        SettingsDialog._test_connection(fake)

        assert fake.test_status.setText.call_args[0][0] == "Testing..."

    def test_result_without_a_label(self, monkeypatch):
        cfg = _cfg()
        fake = _fake_connection_dialog(cfg, "", monkeypatch)
        SettingsDialog._test_connection(fake)

        SettingsDialog._on_test_finished(fake, False, "boom")

        assert fake.test_status.setText.call_args[0][0] == "Failed: boom"
