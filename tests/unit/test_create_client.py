"""One resolver builds every LLM client.

Each call site names a utility; an unmapped utility inherits the active
profile, which is what every call site did before profiles existed.
"""

from freecad_ai.config import AppConfig, ProviderConfig
from freecad_ai.llm.client import (
    create_client,
    create_client_from_config,
    resolve_profile,
)


def _cfg():
    cfg = AppConfig()
    cfg.profiles = {
        "cloud": ProviderConfig(name="anthropic", api_key="sk-cloud",
                                base_url="https://api.anthropic.com",
                                model="claude-sonnet-4-6"),
        "local": ProviderConfig(name="ollama", api_key="",
                                base_url="http://localhost:11434/v1",
                                model="qwen3:8b", params={"top_k": 40}),
    }
    cfg.active_profile = "cloud"
    return cfg


class TestUtilityRouting:
    def test_no_utility_uses_the_active_profile(self):
        assert create_client(_cfg()).model == "claude-sonnet-4-6"

    def test_unmapped_utility_inherits_the_active_profile(self):
        assert create_client(_cfg(), "compaction").model == "claude-sonnet-4-6"

    def test_mapped_utility_uses_its_own_profile(self):
        cfg = _cfg()
        cfg.utility_profiles["compaction"] = "local"
        client = create_client(cfg, "compaction")
        assert client.model == "qwen3:8b"
        assert client.base_url == "http://localhost:11434/v1"

    def test_one_utility_override_does_not_move_the_others(self):
        cfg = _cfg()
        cfg.utility_profiles["compaction"] = "local"
        assert create_client(cfg, "rerank").model == "claude-sonnet-4-6"

    def test_empty_mapping_means_inherit(self):
        cfg = _cfg()
        cfg.utility_profiles["rerank"] = ""
        assert create_client(cfg, "rerank").model == "claude-sonnet-4-6"

    def test_dangling_profile_name_falls_back_to_active(self):
        """Never leave the user unable to chat because a profile was
        deleted while a utility still pointed at it."""
        cfg = _cfg()
        cfg.utility_profiles["rerank"] = "deleted-profile"
        assert create_client(cfg, "rerank").model == "claude-sonnet-4-6"

    def test_same_provider_different_model_is_expressible(self):
        """The original request: cheap model for compaction, same vendor."""
        cfg = _cfg()
        cfg.profiles["cheap"] = ProviderConfig(
            name="anthropic", api_key="sk-cloud",
            base_url="https://api.anthropic.com", model="claude-haiku-4-5")
        cfg.utility_profiles["compaction"] = "cheap"
        chat = create_client(cfg)
        compact = create_client(cfg, "compaction")
        assert chat.provider_name == compact.provider_name == "anthropic"
        assert chat.model == "claude-sonnet-4-6"
        assert compact.model == "claude-haiku-4-5"


class TestApiKeyFallback:
    def test_profile_key_wins(self):
        cfg = _cfg()
        cfg.provider_keys["anthropic"] = "sk-vendor"
        assert create_client(cfg).api_key == "sk-cloud"

    def test_empty_profile_key_falls_through_to_the_vendor_default(self):
        cfg = _cfg()
        cfg.active_profile = "local"
        cfg.provider_keys["ollama"] = "sk-gateway"
        assert create_client(cfg).api_key == "sk-gateway"

    def test_both_empty_yields_empty(self):
        cfg = _cfg()
        cfg.active_profile = "local"
        assert create_client(cfg).api_key == ""

    def test_two_profiles_on_one_vendor_can_hold_different_keys(self):
        """Why keys live in the profile: ollama-local needs none while
        ollama-remote sits behind an authenticating gateway."""
        cfg = _cfg()
        cfg.profiles["remote"] = ProviderConfig(
            name="ollama", api_key="sk-remote",
            base_url="https://gpu.example.net/v1", model="qwen3:32b")
        cfg.active_profile = "local"
        assert create_client(cfg).api_key == ""
        cfg.active_profile = "remote"
        assert create_client(cfg).api_key == "sk-remote"


class TestBaseUrlAndModelFallback:
    """Mirrors TestApiKeyFallback: an empty profile field falls back to the
    vendor preset, never to an unusable empty string."""

    def test_empty_base_url_falls_back_to_the_preset(self):
        cfg = _cfg()
        cfg.profiles["p"] = ProviderConfig(name="anthropic", base_url="", model="claude-sonnet-4-6")
        cfg.active_profile = "p"
        assert create_client(cfg).base_url == "https://api.anthropic.com"

    def test_empty_model_falls_back_to_the_preset_default(self):
        cfg = _cfg()
        cfg.profiles["p"] = ProviderConfig(name="anthropic", base_url="https://api.anthropic.com", model="")
        cfg.active_profile = "p"
        assert create_client(cfg).model == "claude-sonnet-4-6"

    def test_unknown_provider_with_empty_fields_degrades_gracefully(self):
        """apply_preset already tolerates an unmapped vendor via .get(name,
        {}); resolution must too — never raise KeyError for 'custom' or a
        typo'd provider name."""
        cfg = _cfg()
        cfg.profiles["p"] = ProviderConfig(name="not-a-real-vendor", base_url="", model="")
        cfg.active_profile = "p"
        client = create_client(cfg)
        assert client.base_url == ""
        assert client.model == ""


class TestParamLayering:
    def test_model_params_apply(self):
        cfg = _cfg()
        cfg.model_params = {"claude-sonnet-4-6": {"temperature": 0.7}}
        assert create_client(cfg).model_params["temperature"] == 0.7

    def test_profile_params_override_model_params(self):
        cfg = _cfg()
        cfg.model_params = {"qwen3:8b": {"top_k": 10, "top_p": 0.9}}
        cfg.active_profile = "local"
        params = create_client(cfg).model_params
        assert params["top_k"] == 40      # profile wins
        assert params["top_p"] == 0.9     # model_params still contributes

    def test_one_profile_params_never_leak_into_another(self):
        """Issue #30, now structural."""
        cfg = _cfg()
        cfg.utility_profiles["rerank"] = "local"
        assert create_client(cfg, "rerank").model_params["top_k"] == 40
        assert "top_k" not in create_client(cfg).model_params


class TestCallSiteOverrides:
    def test_defaults_come_from_config(self):
        cfg = _cfg()
        cfg.max_tokens = 8192
        cfg.temperature = 0.3
        client = create_client(cfg)
        assert client.max_tokens == 8192
        assert client.temperature == 0.3

    def test_overrides_win(self):
        """Job properties, not connection properties: the reranker wants
        1024 tokens and no thinking regardless of which profile it uses."""
        cfg = _cfg()
        cfg.max_tokens = 8192
        cfg.thinking = "extended"
        client = create_client(cfg, "rerank", max_tokens=1024,
                               temperature=0.0, thinking="off")
        assert client.max_tokens == 1024
        assert client.temperature == 0.0
        assert client.thinking == "off"


class TestCreateClientFromConfigIsUnchanged:
    """create_client_from_config() is now create_client() under the hood.
    Every existing call site still uses it until Tasks 5-6 convert them, so
    its observable output must match what the old hand-rolled version
    produced: provider/base_url/api_key/model straight from cfg.provider,
    max_tokens/temperature/thinking straight from cfg, and model_params
    keyed only by cfg.model_params[cfg.provider.model]."""

    def test_matches_the_pre_refactor_construction(self, monkeypatch):
        cfg = _cfg()
        cfg.max_tokens = 4096
        cfg.temperature = 0.5
        cfg.thinking = "on"
        cfg.model_params = {"claude-sonnet-4-6": {"top_p": 0.8}}
        monkeypatch.setattr("freecad_ai.config.get_config", lambda: cfg)

        client = create_client_from_config()

        assert client.provider_name == cfg.provider.name
        assert client.base_url == cfg.provider.base_url.rstrip("/")
        assert client.api_key == cfg.provider.api_key
        assert client.model == cfg.provider.model
        assert client.max_tokens == cfg.max_tokens
        assert client.temperature == cfg.temperature
        assert client.thinking == cfg.thinking
        assert client.model_params == cfg.model_params[cfg.provider.model]


class TestResolveProfile:
    def test_returns_the_profile_object_itself(self):
        cfg = _cfg()
        assert resolve_profile(cfg) is cfg.profiles["cloud"]

    def test_named_utility_resolves_to_its_profile(self):
        cfg = _cfg()
        cfg.utility_profiles["skill_eval"] = "local"
        assert resolve_profile(cfg, "skill_eval") is cfg.profiles["local"]
