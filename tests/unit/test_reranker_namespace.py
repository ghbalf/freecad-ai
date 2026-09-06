"""Reranker params get their own namespace, never the main model's slot.

Issue #30 (AVAVAVA1): editing the main model's temperature and saving reverted
it to 0.3. Root cause: the reranker save path wrote its table into the shared
``cfg.model_params`` dict keyed by model name; in inherit mode that key *is* the
main model, so the (stale) reranker snapshot clobbered the main model's params.

The fix gives the reranker its own ``cfg.rerank_params`` field:
  - override mode (a distinct reranker model is set) → reranker uses
    ``cfg.rerank_params``;
  - inherit mode (override field empty) → reranker uses the main model's
    params (``cfg.model_params[provider.model]``), and saves nothing of its own.

Profiles retire this class of bug structurally: params are a field of a
profile, so there is no shared namespace for the reranker to reach into.
These tests now pin that the reranker reads its own profile's params and
that an inheriting reranker gets the active profile's.
"""

from unittest import mock

import pytest

# settings_dialog/chat_widget import through ui/compat.py which needs PySide.
# In dev venvs without either, skip — the dialog can't be imported.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
from freecad_ai.llm.client import create_client  # noqa: E402
from freecad_ai.ui.chat_widget import _run_reranker  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


def _cfg_with_params():
    cfg = AppConfig()
    cfg.profiles = {
        "main": ProviderConfig(name="ollama",
                               base_url="http://localhost:11434/v1",
                               model="main-model",
                               params={"temperature": 0.8, "top_p": 0.9}),
    }
    cfg.active_profile = "main"
    return cfg


class TestRerankProfileParams:
    def test_override_uses_its_own_profile_params(self):
        cfg = _cfg_with_params()
        cfg.profiles["rr"] = ProviderConfig(
            name="ollama", base_url="http://localhost:11434/v1",
            model="rr-model", params={"temperature": 0.1, "top_k": 20})
        cfg.utility_profiles["rerank"] = "rr"
        client = create_client(cfg, "rerank")
        assert client.model == "rr-model"
        assert client.model_params == {"temperature": 0.1, "top_k": 20}

    def test_inherit_uses_the_active_profile(self):
        cfg = _cfg_with_params()
        client = create_client(cfg, "rerank")
        assert client.model == "main-model"
        assert client.model_params == {"temperature": 0.8, "top_p": 0.9}

    def test_reranker_params_cannot_reach_the_main_profile(self):
        """The #30 regression, now impossible by construction."""
        cfg = _cfg_with_params()
        cfg.profiles["rr"] = ProviderConfig(
            name="ollama", base_url="http://localhost:11434/v1",
            model="main-model", params={"temperature": 0.1})
        cfg.utility_profiles["rerank"] = "rr"
        create_client(cfg, "rerank")
        assert cfg.profiles["main"].params == {"temperature": 0.8, "top_p": 0.9}


def _cfg_for_call_site():
    cfg = AppConfig()
    cfg.rerank_method = "llm"
    cfg.profiles = {
        "main": ProviderConfig(name="ollama",
                               base_url="http://localhost:11434/v1",
                               model="main-model"),
        "rr": ProviderConfig(name="ollama",
                             base_url="http://localhost:11434/v1",
                             model="rr-model",
                             params={"temperature": 0.1, "top_k": 20}),
    }
    cfg.active_profile = "main"
    cfg.utility_profiles["rerank"] = "rr"
    return cfg


class TestRunRerankerCallSite:
    """_run_reranker (freecad_ai/ui/chat_widget.py) is the actual call site
    this task rewrote; nothing else in this file exercises it."""

    def test_call_site_passes_job_settings_and_merged_params(self):
        cfg = _cfg_for_call_site()
        pairs = [("tool_a", "does a thing")]
        with mock.patch("freecad_ai.llm.client.create_client") as mock_create, \
                mock.patch("freecad_ai.tools.reranker.rerank_tools_llm") as mock_llm:
            mock_llm.return_value = ["tool_a"]
            _run_reranker(cfg, pairs, "hello")

        assert mock_create.called
        _, kwargs = mock_create.call_args
        assert kwargs["max_tokens"] == 1024
        assert kwargs["thinking"] == "off"
        assert kwargs["temperature"] == 0.1

    def test_broken_client_falls_back_to_keyword_reranking(self):
        cfg = _cfg_for_call_site()
        pairs = [("tool_a", "does a thing")]
        with mock.patch("freecad_ai.llm.client.create_client",
                        side_effect=RuntimeError("boom")), \
                mock.patch("freecad_ai.tools.reranker.rerank_tools_llm") as mock_llm, \
                mock.patch("freecad_ai.tools.reranker.rerank_tools") as mock_kw:
            mock_kw.return_value = ["tool_a"]
            result = _run_reranker(cfg, pairs, "hello")

        assert mock_kw.called
        assert not mock_llm.called
        assert result == ["tool_a"]


class TestTestRerankerProbeMatchesCreateClient:
    """The dialog's "Test Reranker" button must resolve its five values
    (provider, base_url, api_key, model, params) exactly the way
    create_client(cfg, "rerank") does. Two prior fix rounds on this file
    were both caused by a probe or editor drifting from create_client —
    this pins the reranker probe specifically.

    Uses a fake self (only the attributes _test_reranker touches) rather
    than constructing a real dialog, and patches _TestRerankerThread to
    capture its constructor args instead of starting a real QThread.
    """

    def _run_probe(self, cfg, rerank_selection, monkeypatch):
        captured = {}

        class _CapturingThread:
            def __init__(self, provider_name, base_url, api_key, model,
                         model_params, parent=None):
                captured.update(
                    provider_name=provider_name, base_url=base_url,
                    api_key=api_key, model=model, model_params=model_params)
                self.finished = mock.MagicMock()

            def start(self):
                pass

        monkeypatch.setattr(
            "freecad_ai.ui.settings_dialog._TestRerankerThread",
            _CapturingThread)

        combo = mock.MagicMock()
        combo.currentData.return_value = rerank_selection

        fake = mock.MagicMock()
        fake._cfg = cfg
        fake._profiles = cfg.profiles
        fake._active_profile = cfg.active_profile
        fake.utility_combos = {"rerank": combo}

        SettingsDialog._test_reranker(fake)
        return captured

    def test_override_matches_resolve_profile_and_resolve_params(
            self, monkeypatch):
        from freecad_ai.llm.client import resolve_profile, resolve_params

        cfg = _cfg_for_call_site()
        captured = self._run_probe(cfg, "rr", monkeypatch)

        profile = resolve_profile(cfg, "rerank")
        assert profile.name == "ollama" and profile.model == "rr-model"
        assert captured["provider_name"] == profile.name
        assert captured["base_url"] == profile.base_url
        assert captured["api_key"] == (
            profile.api_key or cfg.provider_keys.get(profile.name, ""))
        assert captured["model"] == profile.model
        assert captured["model_params"] == resolve_params(cfg, profile) \
            == {"temperature": 0.1, "top_k": 20}

    def test_inherit_falls_back_to_active_profile(self, monkeypatch):
        from freecad_ai.llm.client import resolve_profile, resolve_params

        cfg = _cfg_for_call_site()
        del cfg.utility_profiles["rerank"]  # dropdown on "same as active"
        captured = self._run_probe(cfg, "", monkeypatch)

        profile = resolve_profile(cfg, "rerank")
        assert profile is cfg.profiles["main"]
        assert captured["provider_name"] == profile.name == "ollama"
        assert captured["model"] == profile.model == "main-model"
        assert captured["model_params"] == resolve_params(cfg, profile)

    def test_vendor_default_key_fallback_matches_create_client(
            self, monkeypatch):
        """A profile with no api_key of its own falls back to the
        vendor-wide provider_keys entry — exactly like create_client()."""
        from freecad_ai.llm.client import create_client

        cfg = _cfg_for_call_site()
        cfg.provider_keys["ollama"] = "vendor-wide-key"
        captured = self._run_probe(cfg, "rr", monkeypatch)

        client = create_client(cfg, "rerank")
        assert captured["api_key"] == client.api_key == "vendor-wide-key"
