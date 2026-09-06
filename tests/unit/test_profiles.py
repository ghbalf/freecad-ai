"""Named connection profiles.

A profile is a ProviderConfig (vendor, url, key, model) plus its own params
dict. AppConfig holds them in a label-keyed dict; ``cfg.provider`` is a
property returning the active one, so the 44 existing ``cfg.provider.*``
sites keep meaning "the active chat connection".

Note the two different names in play: ``profile.name`` is the *vendor*
(inherited from ProviderConfig, e.g. "ollama"), while a profile's own
*label* is its key in ``cfg.profiles`` (e.g. "ollama-remote").
"""

from freecad_ai.config import AppConfig, ProviderConfig


class TestDefaultProfile:
    def test_bare_appconfig_has_exactly_one_profile(self):
        cfg = AppConfig()
        assert len(cfg.profiles) == 1

    def test_active_profile_names_that_profile(self):
        cfg = AppConfig()
        assert cfg.active_profile in cfg.profiles

    def test_provider_property_returns_the_active_profile(self):
        cfg = AppConfig()
        assert cfg.provider is cfg.profiles[cfg.active_profile]

    def test_default_profile_matches_prior_provider_defaults(self):
        """Prior-version behavior: a fresh config is still Anthropic."""
        cfg = AppConfig()
        assert cfg.provider.name == "anthropic"
        assert cfg.provider.base_url == "https://api.anthropic.com"
        assert cfg.provider.model == "claude-sonnet-4-6"

    def test_writes_through_the_property_reach_the_profile(self):
        """The param-store bridge and settings dialog both assign through
        cfg.provider.*; those writes must land in the stored profile."""
        cfg = AppConfig()
        cfg.provider.model = "gemma4:27b"
        assert cfg.profiles[cfg.active_profile].model == "gemma4:27b"


class TestSwitchingActiveProfile:
    def test_provider_follows_active_profile(self):
        cfg = AppConfig()
        cfg.profiles["local"] = ProviderConfig(
            name="ollama", base_url="http://localhost:11434/v1", model="qwen3:8b")
        cfg.active_profile = "local"
        assert cfg.provider.model == "qwen3:8b"

    def test_editing_one_profile_leaves_the_other_intact(self):
        """The #75 shape: browsing between connections destroys nothing."""
        cfg = AppConfig()
        original = cfg.active_profile
        cfg.profiles["remote"] = ProviderConfig(
            name="ollama", base_url="https://gpu.example.net/v1", model="qwen3:32b")
        cfg.active_profile = "remote"
        cfg.provider.base_url = "https://gpu.example.net/v1/edited"
        cfg.active_profile = original
        cfg.active_profile = "remote"
        assert cfg.provider.base_url == "https://gpu.example.net/v1/edited"
        assert cfg.profiles[original].name == "anthropic"


class TestProfileParams:
    def test_params_default_empty(self):
        assert ProviderConfig().params == {}

    def test_two_profiles_have_independent_params(self):
        """Retires #30 structurally: there is no shared params namespace."""
        a = ProviderConfig(model="m", params={"temperature": 0.8})
        b = ProviderConfig(model="m", params={"temperature": 0.1})
        assert a.params == {"temperature": 0.8}
        assert b.params == {"temperature": 0.1}


class TestUtilityMapping:
    def test_utility_profiles_default_empty(self):
        """Empty means every utility inherits the active profile — the
        prior-version behavior."""
        assert AppConfig().utility_profiles == {}

    def test_provider_keys_default_empty(self):
        assert AppConfig().provider_keys == {}


class TestParamStoreBridgeWritesReachTheProfile:
    def test_apply_overrides_pattern_edits_active_profile(self):
        """_apply_param_store_overrides assigns cfg.provider.model /
        .base_url / .api_key / .name. Simulate those assignments and
        assert they land in the active profile rather than a temporary."""
        cfg = AppConfig()
        cfg.provider.name = "ollama"
        cfg.provider.model = "qwen3:8b"
        cfg.provider.base_url = "http://localhost:11434/v1"
        cfg.provider.api_key = "sk-test"
        stored = cfg.profiles[cfg.active_profile]
        assert (stored.name, stored.model, stored.base_url, stored.api_key) == (
            "ollama", "qwen3:8b", "http://localhost:11434/v1", "sk-test")
