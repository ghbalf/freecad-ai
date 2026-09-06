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


# Covers the legacy override persistence path (SettingsDialog._resolve_rerank_
# params); goes away when the reranker override widgets do.
class TestResolveRerankParamsWriteRule:
    def test_override_persists_table(self):
        """With an override model set, the reranker table is persisted."""
        out = SettingsDialog._resolve_rerank_params(
            "rr-model", {"temperature": 0.1, "top_k": 20})
        assert out == {"temperature": 0.1, "top_k": 20}

    def test_inherit_persists_nothing(self):
        """Empty override → reranker stores nothing; the main Model
        Parameters table is the sole owner of the main model's slot. This is
        the exact guard that fixes the issue #30 clobber."""
        out = SettingsDialog._resolve_rerank_params(
            "", {"temperature": 0.1, "top_k": 20})
        assert out == {}

    def test_whitespace_override_treated_as_inherit(self):
        """A blank-but-spaces override field is still inherit mode."""
        out = SettingsDialog._resolve_rerank_params(
            "   ", {"temperature": 0.1})
        assert out == {}


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
