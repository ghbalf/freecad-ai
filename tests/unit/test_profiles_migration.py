"""Old-shape config.json files load into profiles without behavior change.

Migration is where the risk concentrates: a wrong call here silently
mangles a config a user has been running for months.
"""

from freecad_ai.config import AppConfig, ProviderConfig


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
        """The copy into profile.params does not consume the legacy dict.
        It stays in the JSON, unread, so a downgrade still finds it and
        other models' entries are not disturbed."""
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.model_params == {"qwen3:32b": {"temperature": 0.8, "top_p": 0.9}}

    def test_the_key_lands_on_the_profile_and_nowhere_else(self):
        """provider_keys is a hand-written per-vendor default, never
        auto-populated. Seeding it put a copy of the credential where no
        widget could see, edit or delete it, so clearing the API Key field
        to rotate a leaked key left it on disk and still sending."""
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.provider.api_key == "sk-main"
        assert cfg.provider_keys == {}

    def test_clearing_the_migrated_key_actually_clears_it(self):
        """The end of the same path: with nothing seeded, emptying the
        profile's key resolves to no key at all."""
        from freecad_ai.llm.client import create_client
        cfg = AppConfig.from_dict(_old_shape())
        cfg.provider.api_key = ""
        assert create_client(cfg).api_key == ""

    def test_a_hand_written_vendor_default_still_resolves(self):
        """The fallback itself is unchanged — only its auto-seeding is
        gone. A user who writes provider_keys into config.json by hand
        still has every keyless profile on that vendor pick it up."""
        from freecad_ai.llm.client import create_client
        cfg = AppConfig.from_dict(_old_shape(
            provider_keys={"ollama": "sk-vendor-wide"}))
        cfg.provider.api_key = ""
        assert create_client(cfg).api_key == "sk-vendor-wide"

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

    def test_non_mapping_profile_value_degrades_in_place(self):
        """A malformed *individual* profile used to raise TypeError, which
        load_config caught by discarding the entire config and returning
        bare defaults — which the next save then wrote over the top of.
        The other four malformed shapes on this path all degrade in place;
        so does this one now."""
        cfg = AppConfig.from_dict({
            "profiles": {
                "a": "nope",
                "b": {"name": "ollama", "model": "qwen3:8b",
                      "base_url": "http://localhost:11434/v1"},
            },
            "active_profile": "b",
            "max_tokens": 8192,
        })
        assert isinstance(cfg.profiles["a"], ProviderConfig)
        assert cfg.profiles["a"] == ProviderConfig()
        # The rest of the user's config survives.
        assert cfg.profiles["b"].model == "qwen3:8b"
        assert cfg.active_profile == "b"
        assert cfg.max_tokens == 8192

    def test_unknown_profile_key_degrades_in_place(self):
        """A profile carrying a field this version does not know — written
        by a *newer* version, then opened by this one — must not take the
        whole config down with it. Same failure mode as a non-mapping
        value: ProviderConfig(**p) raises TypeError, load_config catches it
        by discarding everything, and the next save overwrites the file.
        This branch is itself about to add profile fields, so the forward
        direction is the one that will actually happen."""
        cfg = AppConfig.from_dict({
            "profiles": {
                "a": {"name": "ollama", "model": "qwen3:8b",
                      "invented_by_a_later_version": True},
            },
            "active_profile": "a",
            "max_tokens": 8192,
        })
        assert cfg.profiles["a"].model == "qwen3:8b"
        assert cfg.profiles["a"].name == "ollama"
        assert not hasattr(cfg.profiles["a"], "invented_by_a_later_version")
        # The rest of the user's config survives.
        assert cfg.active_profile == "a"
        assert cfg.max_tokens == 8192

    def test_a_bad_profile_does_not_discard_the_file(self, tmp_config_dir):
        """The end of the same path: load_config must not fall back to
        bare defaults."""
        import json
        import os
        import freecad_ai.config as config_mod
        os.makedirs(os.path.dirname(config_mod.CONFIG_FILE), exist_ok=True)
        with open(config_mod.CONFIG_FILE, "w") as f:
            json.dump({
                "profiles": {"a": "nope"},
                "active_profile": "a",
                "max_tokens": 8192,
            }, f)
        cfg = config_mod.load_config()
        assert cfg.max_tokens == 8192


class TestLegacyMirrorOnSave:
    def test_save_still_emits_a_provider_key(self):
        """Downgrade safety: an older version reads `provider` and finds
        the user's live connection, not an empty dialog."""
        cfg = AppConfig.from_dict(_old_shape())
        assert cfg.to_dict()["provider"]["model"] == "qwen3:32b"

    def test_mirror_tracks_the_active_profile(self):
        cfg = AppConfig.from_dict(_old_shape())
        cfg.profiles["cloud"] = ProviderConfig(
            name="anthropic", model="claude-sonnet-4-6")
        cfg.active_profile = "cloud"
        assert cfg.to_dict()["provider"]["name"] == "anthropic"

    def test_profiles_are_serialised(self):
        d = AppConfig.from_dict(_old_shape()).to_dict()
        assert d["profiles"]["ollama"]["model"] == "qwen3:32b"
        assert d["active_profile"] == "ollama"

    def test_round_trip_is_stable(self):
        first = AppConfig.from_dict(_old_shape())
        second = AppConfig.from_dict(first.to_dict())
        assert second.active_profile == first.active_profile
        assert second.provider.model == first.provider.model
        assert second.provider.params == first.provider.params

    def test_round_trip_preserves_a_rerank_override(self):
        first = AppConfig.from_dict(_old_shape(
            rerank_llm_model="gemma3:4b",
            rerank_params={"temperature": 0.1},
        ))
        second = AppConfig.from_dict(first.to_dict())
        assert second.utility_profiles == {"rerank": "rerank"}
        assert second.profiles["rerank"].model == "gemma3:4b"
        assert second.profiles["rerank"].params == {"temperature": 0.1}
