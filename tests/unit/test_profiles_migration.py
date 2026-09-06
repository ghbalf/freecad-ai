"""Old-shape config.json files load into profiles without behavior change.

Migration is where the risk concentrates: a wrong call here silently
mangles a config a user has been running for months.
"""

import pytest

from freecad_ai.config import AppConfig


def _old_shape(**extra):
    """A config.json as written by v0.23.1-alpha and earlier."""
    data = {
        "provider": {
            "name": "ollama",
            "api_key": "sk-main",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3:32b",
        },
        "model_params": {"qwen3:32b": {"temperature": 0.8, "top_p": 0.9}},
        "max_tokens": 8192,
    }
    data.update(extra)
    return data


class TestPlainMigration:
    def test_creates_one_profile_labelled_by_vendor(self):
        cfg = AppConfig.from_dict(_old_shape())
        assert list(cfg.profiles) == ["ollama"]

    def test_that_profile_is_active(self):
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.active_profile == "ollama"

    def test_connection_fields_survive(self):
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.provider.name == "ollama"
        assert cfg.provider.api_key == "sk-main"
        assert cfg.provider.base_url == "http://localhost:11434/v1"
        assert cfg.provider.model == "qwen3:32b"

    def test_model_params_seed_the_profile_params(self):
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.provider.params == {"temperature": 0.8, "top_p": 0.9}

    def test_model_params_dict_is_left_intact(self):
        """profile.params layers ON TOP of model_params; it does not
        replace it, and other models' entries must not be disturbed."""
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.model_params == {"qwen3:32b": {"temperature": 0.8, "top_p": 0.9}}

    def test_api_key_also_seeds_the_provider_default(self):
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.provider_keys["ollama"] == "sk-main"

    def test_no_utility_overrides_without_a_rerank_model(self):
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.utility_profiles == {}

    def test_unrelated_fields_still_load(self):
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.max_tokens == 8192


class TestRerankOverrideMigration:
    def _cfg(self):
        return AppConfig.from_dict(_old_shape(
            rerank_llm_provider_name="",
            rerank_llm_base_url="",
            rerank_llm_api_key="",
            rerank_llm_model="gemma3:4b",
            rerank_params={"temperature": 0.1, "top_k": 20},
        ))

    def test_creates_a_second_profile(self):
        assert set(self._cfg().profiles) == {"ollama", "rerank"}

    def test_rerank_utility_points_at_it(self):
        assert self._cfg().utility_profiles == {"rerank": "rerank"}

    def test_empty_override_fields_inherit_from_the_main_profile(self):
        """The old builder used `or` fallbacks per field; an empty
        provider/url/key meant "same as main". Migration must bake that in."""
        rr = self._cfg().profiles["rerank"]
        assert rr.name == "ollama"
        assert rr.base_url == "http://localhost:11434/v1"
        assert rr.api_key == "sk-main"

    def test_override_model_is_kept(self):
        assert self._cfg().profiles["rerank"].model == "gemma3:4b"

    def test_rerank_params_become_the_profile_params(self):
        assert self._cfg().profiles["rerank"].params == {
            "temperature": 0.1, "top_k": 20}

    def test_full_override_keeps_its_own_vendor(self):
        cfg = AppConfig.from_dict(_old_shape(
            rerank_llm_provider_name="openai",
            rerank_llm_base_url="https://api.openai.com/v1",
            rerank_llm_api_key="sk-rr",
            rerank_llm_model="gpt-4o-mini",
        ))
        rr = cfg.profiles["rerank"]
        assert (rr.name, rr.base_url, rr.api_key) == (
            "openai", "https://api.openai.com/v1", "sk-rr")


class TestEdgeCases:
    def test_new_shape_config_is_left_alone(self):
        """Idempotence: loading an already-migrated config must not
        re-migrate it from the stale legacy mirror."""
        cfg = AppConfig.from_dict({
            "profiles": {
                "cloud": {"name": "anthropic", "api_key": "sk-a",
                          "base_url": "https://api.anthropic.com",
                          "model": "claude-sonnet-4-6", "params": {}},
                "local": {"name": "ollama", "api_key": "",
                          "base_url": "http://localhost:11434/v1",
                          "model": "qwen3:8b", "params": {"top_k": 40}},
            },
            "active_profile": "local",
            "utility_profiles": {"compaction": "local"},
            "provider": {"name": "stale", "api_key": "", "base_url": "",
                         "model": "stale-model"},
        })
        assert set(cfg.profiles) == {"cloud", "local"}
        assert cfg.active_profile == "local"
        assert cfg.provider.model == "qwen3:8b"
        assert cfg.utility_profiles == {"compaction": "local"}

    def test_custom_provider_with_empty_preset_fields_migrates(self):
        cfg = AppConfig.from_dict({
            "provider": {"name": "custom", "api_key": "k",
                         "base_url": "https://gw.example.net/v1",
                         "model": "my-model"},
        })
        assert cfg.active_profile == "custom"
        assert cfg.provider.base_url == "https://gw.example.net/v1"

    def test_empty_dict_yields_a_usable_default(self):
        cfg = AppConfig.from_dict({})
        assert len(cfg.profiles) == 1
        assert cfg.provider.name == "anthropic"

    def test_unknown_keys_are_still_ignored(self):
        cfg = AppConfig.from_dict(_old_shape(nonsense_key=1))
        assert not hasattr(cfg, "nonsense_key")

    def test_non_mapping_profiles_degrades_to_defaults(self):
        """A hand-edited config.json with 'profiles' as the wrong JSON type
        (e.g. a list) must not crash config loading — treat it as absent,
        same as a missing key."""
        cfg = AppConfig.from_dict({"profiles": ["a"]})
        assert len(cfg.profiles) == 1
        assert cfg.provider.name == "anthropic"

    def test_non_mapping_rerank_params_degrades_to_empty(self):
        """A hand-edited 'rerank_params' that isn't an object (e.g. a
        string) must not crash migration — treat it as no override params."""
        cfg = AppConfig.from_dict(_old_shape(
            rerank_llm_model="gemma3:4b", rerank_params="nope"))
        assert cfg.profiles["rerank"].params == {}

    def test_non_mapping_profile_value_still_raises_typeerror(self):
        """A malformed *individual* profile (not the profiles dict itself)
        is a distinct failure mode: it already raised TypeError before this
        change (caught by load_config's except tuple), and must keep doing
        so — the exception type is part of the contract even though nothing
        declares it, so a later refactor can't silently change it."""
        with pytest.raises(TypeError):
            AppConfig.from_dict({"profiles": {"a": "nope"}})
