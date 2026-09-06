"""Tests for configuration system."""

import json

import pytest

from freecad_ai.config import (
    PROVIDER_PRESETS,
    AppConfig,
    ProviderConfig,
    get_config,
    load_config,
    reload_config,
    save_config,
    save_current_config,
)


class TestProviderConfig:
    def test_defaults(self):
        p = ProviderConfig()
        assert p.name == "anthropic"
        assert p.api_key == ""
        assert "anthropic" in p.base_url
        assert "claude" in p.model

    def test_apply_preset_ollama(self):
        p = ProviderConfig()
        p.apply_preset("ollama")
        assert p.name == "ollama"
        assert "localhost" in p.base_url
        assert p.model == "llama3"

    def test_apply_preset_openai(self):
        p = ProviderConfig()
        p.apply_preset("openai")
        assert p.name == "openai"
        assert "openai.com" in p.base_url

    def test_apply_preset_custom(self):
        p = ProviderConfig()
        p.apply_preset("custom")
        assert p.name == "custom"
        assert p.base_url == ""

    def test_apply_unknown_preset_keeps_existing(self):
        p = ProviderConfig(base_url="http://example.com", model="my-model")
        p.apply_preset("nonexistent")
        assert p.base_url == "http://example.com"
        assert p.model == "my-model"


class TestAppConfig:
    def test_defaults(self):
        c = AppConfig()
        assert c.mode == "plan"
        assert c.max_tokens == 4096
        assert c.temperature == 0.3
        assert c.auto_execute is False
        assert c.enable_tools is True
        assert c.thinking == "off"
        assert c.mcp_servers == []

    def test_to_dict_roundtrip(self):
        c = AppConfig()
        c.provider.apply_preset("ollama")
        c.max_tokens = 8192
        c.mcp_servers = [{"name": "test", "command": "echo"}]
        d = c.to_dict()
        c2 = AppConfig.from_dict(d)
        assert c2.provider.name == "ollama"
        assert c2.max_tokens == 8192
        assert len(c2.mcp_servers) == 1

    def test_from_dict_ignores_unknown_keys(self):
        d = {
            "provider": {"name": "anthropic", "api_key": "", "base_url": "", "model": ""},
            "unknown_field": "should be ignored",
            "mode": "act",
        }
        c = AppConfig.from_dict(d)
        assert c.mode == "act"
        assert not hasattr(c, "unknown_field")

    def test_from_dict_handles_empty_provider(self):
        d = {"mode": "plan"}
        c = AppConfig.from_dict(d)
        assert c.provider.name == "anthropic"  # default

    def test_from_dict_preserves_all_fields(self):
        original = AppConfig()
        original.mode = "act"
        original.temperature = 0.7
        original.thinking = "on"
        d = original.to_dict()
        restored = AppConfig.from_dict(d)
        assert restored.mode == "act"
        assert restored.temperature == 0.7
        assert restored.thinking == "on"

    def test_execution_timeout_default(self):
        # Issue #14: the execution timeout was hardcoded with no override. It is
        # now a configurable field (kept at the long-standing 30s default; the
        # real #14 fix was the sandbox no longer hanging — raising the budget
        # only helps genuinely-slow ops on large models).
        c = AppConfig()
        assert c.execution_timeout == 30

    def test_execution_timeout_roundtrip(self):
        c = AppConfig()
        c.execution_timeout = 180
        c2 = AppConfig.from_dict(c.to_dict())
        assert c2.execution_timeout == 180

    def test_chat_dock_state_defaults(self):
        c = AppConfig()
        assert c.chat_dock_floating is False
        assert c.chat_dock_area == "right"
        assert c.chat_dock_geometry == []
        assert c.chat_dock_tabified_with == []
        assert c.chat_dock_mw_state == ""

    def test_chat_dock_state_roundtrip(self):
        c = AppConfig()
        c.chat_dock_floating = True
        c.chat_dock_area = "left"
        c.chat_dock_geometry = [100, 200, 400, 600]
        c.chat_dock_tabified_with = ["Tasks", "ModelView"]
        c.chat_dock_mw_state = "aGVsbG8gd29ybGQ="  # base64 placeholder
        d = c.to_dict()
        c2 = AppConfig.from_dict(d)
        assert c2.chat_dock_floating is True
        assert c2.chat_dock_area == "left"
        assert c2.chat_dock_geometry == [100, 200, 400, 600]
        assert c2.chat_dock_tabified_with == ["Tasks", "ModelView"]
        assert c2.chat_dock_mw_state == "aGVsbG8gd29ybGQ="

    def test_keep_dock_on_workbench_switch_default(self):
        """Opt-in feature (#34): the dock hides on workbench switch by default."""
        assert AppConfig().keep_dock_on_workbench_switch is False

    def test_keep_dock_on_workbench_switch_roundtrip(self):
        """The keep-dock flag survives a JSON round-trip (#34/PR #35).

        A brand-new defaulted bool needs no migration seeding: from_dict
        falls back to the dataclass default when an older config JSON omits
        the key, and preserves it when present.
        """
        c = AppConfig()
        c.keep_dock_on_workbench_switch = True
        c2 = AppConfig.from_dict(c.to_dict())
        assert c2.keep_dock_on_workbench_switch is True
        # An older config missing the key deserializes to the default.
        legacy = AppConfig.from_dict({})
        assert legacy.keep_dock_on_workbench_switch is False

    def test_rerank_params_default_empty(self):
        """The reranker has its own param namespace, empty by default."""
        c = AppConfig()
        assert c.rerank_params == {}

    def test_rerank_params_roundtrip(self):
        """rerank_params survives a JSON round-trip independently of
        model_params — it must never collide with the main model's slot
        (issue #30: reranker save clobbered the main model's temperature)."""
        c = AppConfig()
        c.provider.model = "main-model"
        c.model_params = {"main-model": {"temperature": 0.8}}
        c.rerank_params = {"temperature": 0.0, "top_k": 20}
        c2 = AppConfig.from_dict(c.to_dict())
        assert c2.rerank_params == {"temperature": 0.0, "top_k": 20}
        # The main model's params are untouched by the reranker namespace.
        assert c2.model_params == {"main-model": {"temperature": 0.8}}


class TestProviderPresets:
    def test_all_presets_have_required_keys(self):
        for name, preset in PROVIDER_PRESETS.items():
            assert "base_url" in preset, f"{name} missing base_url"
            assert "default_model" in preset, f"{name} missing default_model"

    def test_known_presets_exist(self):
        from freecad_ai.llm.providers import PROVIDERS
        # PROVIDER_PRESETS should have exactly the same keys as PROVIDERS
        assert set(PROVIDER_PRESETS.keys()) == set(PROVIDERS.keys())

    def test_github_preset_recommends_reranker(self):
        """Issue #10: GitHub Models has a small per-request input cap.

        The keyword reranker @ top_n=8 keeps Act-mode tool-call requests
        under that cap. The Settings dialog applies this on provider
        switch only when the reranker UI is still at factory defaults
        (so an explicit user choice is never overwritten).
        """
        gh = PROVIDER_PRESETS["github"]
        assert gh["default_rerank"] == {"method": "keyword", "top_n": 8}

    def test_default_rerank_is_empty_for_other_providers(self):
        """Only the github preset ships a reranker recommendation today.

        Other providers either have generous per-request limits (anthropic,
        openai-direct) or the right top_n is workload-dependent. Adding a
        recommendation elsewhere is intentional, not boilerplate.
        """
        for name, preset in PROVIDER_PRESETS.items():
            if name == "github":
                continue
            assert preset["default_rerank"] == {}, (
                f"{name} unexpectedly carries default_rerank — "
                f"add a justifying test or remove the preset entry."
            )


class TestSaveLoad:
    def test_save_and_load(self, tmp_config_dir):
        c = AppConfig()
        c.provider.apply_preset("ollama")
        c.max_tokens = 2048
        save_config(c)

        loaded = load_config()
        assert loaded.provider.name == "ollama"
        assert loaded.max_tokens == 2048

    def test_load_returns_defaults_when_no_file(self, tmp_config_dir):
        c = load_config()
        assert c.mode == "plan"
        assert c.provider.name == "anthropic"

    def test_load_seeds_rerank_params_from_legacy_override_slot(self, tmp_config_dir):
        """Migration: pre-namespace configs stored the reranker override
        model's params inside the shared model_params dict. Those params must
        still reach the reranker so override users don't silently lose them
        (issue #30 follow-up). They now land on the "rerank" profile, which is
        what create_client reads; the rerank_params field itself is legacy."""
        import freecad_ai.config as config_mod
        os.makedirs(os.path.dirname(config_mod.CONFIG_FILE), exist_ok=True)
        with open(config_mod.CONFIG_FILE, "w") as f:
            json.dump({
                "provider": {"name": "ollama", "model": "main-model"},
                "rerank_llm_model": "rr-model",
                "model_params": {"rr-model": {"temperature": 0.0, "top_k": 20}},
            }, f)
        c = load_config()
        assert c.profiles["rerank"].params == {"temperature": 0.0, "top_k": 20}
        assert c.utility_profiles["rerank"] == "rerank"

    def test_load_does_not_seed_rerank_params_in_inherit_mode(self, tmp_config_dir):
        """No reranker override model → nothing to migrate; rerank_params
        stays empty (inherit mode reads the main model's params at runtime)."""
        import freecad_ai.config as config_mod
        os.makedirs(os.path.dirname(config_mod.CONFIG_FILE), exist_ok=True)
        with open(config_mod.CONFIG_FILE, "w") as f:
            json.dump({
                "provider": {"name": "ollama", "model": "main-model"},
                "rerank_llm_model": "",
                "model_params": {"main-model": {"temperature": 0.7}},
            }, f)
        c = load_config()
        assert c.rerank_params == {}

    def test_load_keeps_existing_rerank_params_over_legacy_seed(self, tmp_config_dir):
        """Idempotent: a config already carrying rerank_params is not
        overwritten by the legacy model_params slot."""
        import freecad_ai.config as config_mod
        os.makedirs(os.path.dirname(config_mod.CONFIG_FILE), exist_ok=True)
        with open(config_mod.CONFIG_FILE, "w") as f:
            json.dump({
                "provider": {"name": "ollama", "model": "main-model"},
                "rerank_llm_model": "rr-model",
                "rerank_params": {"temperature": 0.2},
                "model_params": {"rr-model": {"temperature": 0.0, "top_k": 20}},
            }, f)
        c = load_config()
        assert c.rerank_params == {"temperature": 0.2}

    def test_load_returns_defaults_on_corrupt_json(self, tmp_config_dir):
        import freecad_ai.config as config_mod
        config_file = config_mod.CONFIG_FILE
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w") as f:
            f.write("not valid json {{{")

        c = load_config()
        assert c.mode == "plan"  # defaults

    def test_load_returns_defaults_on_bad_types(self, tmp_config_dir):
        import freecad_ai.config as config_mod
        config_file = config_mod.CONFIG_FILE
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w") as f:
            json.dump({"provider": "not a dict"}, f)

        c = load_config()
        assert isinstance(c, AppConfig)


class TestSingleton:
    def test_get_config_returns_same_instance(self, tmp_config_dir):
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_reload_config_creates_new_instance(self, tmp_config_dir):
        c1 = get_config()
        reload_config()
        c2 = get_config()
        assert c1 is not c2

    def test_save_current_config_writes_singleton(self, tmp_config_dir):
        c = get_config()
        c.mode = "act"
        save_current_config()

        loaded = load_config()
        assert loaded.mode == "act"

    def test_save_current_config_noop_when_no_singleton(self, tmp_config_dir):
        import freecad_ai.config as config_mod
        config_mod._config = None
        save_current_config()  # Should not raise


class TestParamStoreBridge:
    """Bridge between FreeCAD's BaseApp/Preferences/Mod/FreeCADAI store and AppConfig."""

    def _fake_param_group(self, ints=None, strings=None, bools=None):
        """Mimic the relevant parts of a FreeCAD ParamGet group object."""
        ints = dict(ints or {})
        strings = dict(strings or {})
        bools = dict(bools or {})

        class _FakeGroup:
            def GetInts(_self):  # noqa: N802 — mimicking FreeCAD camelCase
                return list(ints.keys())
            def GetStrings(_self):
                return list(strings.keys())
            def GetBools(_self):
                return list(bools.keys())
            def GetInt(_self, key, default=0):
                return ints.get(key, default)
            def GetString(_self, key, default=""):
                return strings.get(key, default)
            def GetBool(_self, key, default=False):
                return bools.get(key, default)
            def SetInt(_self, key, value):
                ints[key] = value
            def SetString(_self, key, value):
                strings[key] = value
            def SetBool(_self, key, value):
                bools[key] = value
            def RemInt(_self, key):
                ints.pop(key, None)
            def RemString(_self, key):
                strings.pop(key, None)
            def RemBool(_self, key):
                bools.pop(key, None)

        return _FakeGroup(), ints, strings, bools

    def test_overrides_skipped_when_param_store_unavailable(self):
        """Outside FreeCAD, _get_param_group returns None — cfg unchanged."""
        from freecad_ai.config import AppConfig, _apply_param_store_overrides
        cfg = AppConfig()
        cfg.provider.name = "anthropic"
        _apply_param_store_overrides(cfg)  # no FreeCAD → no-op
        assert cfg.provider.name == "anthropic"

    def test_apply_overrides_provider_index(self):
        from freecad_ai.config import AppConfig, _apply_param_store_overrides
        from unittest.mock import patch
        cfg = AppConfig()
        cfg.provider.name = "anthropic"
        group, _, _, _ = self._fake_param_group(ints={"ProviderIndex": 2})  # ollama
        with patch("freecad_ai.config._get_param_group", return_value=group):
            _apply_param_store_overrides(cfg)
        assert cfg.provider.name == "ollama"

    def test_apply_overrides_strings(self):
        from freecad_ai.config import AppConfig, _apply_param_store_overrides
        from unittest.mock import patch
        cfg = AppConfig()
        group, _, _, _ = self._fake_param_group(strings={
            "Model": "qwen3-vl:32b",
            "BaseUrl": "http://spark:11434/v1",
            "ApiKey": "cmd:secret-tool lookup service freecad-ai",
        })
        with patch("freecad_ai.config._get_param_group", return_value=group):
            _apply_param_store_overrides(cfg)
        assert cfg.provider.model == "qwen3-vl:32b"
        assert cfg.provider.base_url == "http://spark:11434/v1"
        assert cfg.provider.api_key == "cmd:secret-tool lookup service freecad-ai"

    def test_apply_overrides_bool_and_int(self):
        from freecad_ai.config import AppConfig, _apply_param_store_overrides
        from unittest.mock import patch
        cfg = AppConfig()
        cfg.enable_tools = True
        cfg.max_tokens = 4096
        group, _, _, _ = self._fake_param_group(
            bools={"EnableTools": False},
            ints={"MaxTokens": 8192, "ModeIndex": 1, "ThinkingIndex": 2},
        )
        with patch("freecad_ai.config._get_param_group", return_value=group):
            _apply_param_store_overrides(cfg)
        assert cfg.enable_tools is False
        assert cfg.max_tokens == 8192
        assert cfg.mode == "act"
        assert cfg.thinking == "extended"

    def test_apply_overrides_skips_untouched_keys(self):
        """Param store with no relevant keys → cfg untouched."""
        from freecad_ai.config import AppConfig, _apply_param_store_overrides
        from unittest.mock import patch
        cfg = AppConfig()
        cfg.provider.name = "anthropic"
        cfg.max_tokens = 4096
        group, _, _, _ = self._fake_param_group()  # all empty
        with patch("freecad_ai.config._get_param_group", return_value=group):
            _apply_param_store_overrides(cfg)
        assert cfg.provider.name == "anthropic"
        assert cfg.max_tokens == 4096

    def test_apply_ignores_out_of_range_index(self):
        """Defensive — corrupt param store with bad enum index leaves cfg alone."""
        from freecad_ai.config import AppConfig, _apply_param_store_overrides
        from unittest.mock import patch
        cfg = AppConfig()
        cfg.mode = "plan"
        group, _, _, _ = self._fake_param_group(ints={"ModeIndex": 99})
        with patch("freecad_ai.config._get_param_group", return_value=group):
            _apply_param_store_overrides(cfg)
        assert cfg.mode == "plan"

    def test_load_config_seeds_empty_param_store_from_json(self, tmp_path, monkeypatch):
        """Regression: Edit → Preferences was showing blank fields when JSON
        had values but the param store was empty (e.g., user upgraded from
        v0.11.x where ParamGet bridge didn't exist). load_config must seed
        the param store from JSON so Gui::Pref* widgets see current values.
        """
        from unittest.mock import patch
        import freecad_ai.config as config_mod

        cfg_dir = tmp_path / "FreeCADAI"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.json"
        cfg_file.write_text(json.dumps({
            "provider": {
                "name": "ollama",
                "model": "qwen3-vl:32b",
                "base_url": "http://spark:11434/v1",
                "api_key": "cmd:secret-tool lookup service freecad-ai",
            },
            "mode": "act",
            "thinking": "on",
            "max_tokens": 8192,
            "enable_tools": False,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(config_mod, "CONFIG_DIR", str(cfg_dir))

        group, ints, strings, bools = self._fake_param_group()  # empty store
        with patch.object(config_mod, "_get_param_group", return_value=group):
            cfg = config_mod.load_config()

        # JSON values land in the in-memory cfg
        assert cfg.provider.name == "ollama"
        assert cfg.provider.model == "qwen3-vl:32b"
        assert cfg.provider.base_url == "http://spark:11434/v1"
        assert cfg.mode == "act"

        # Param store now mirrors JSON — Edit → Preferences will read these
        assert ints.get("ProviderIndex") == config_mod._PARAM_PROVIDERS.index("ollama")
        assert strings.get("Model") == "qwen3-vl:32b"
        assert strings.get("BaseUrl") == "http://spark:11434/v1"
        assert strings.get("ApiKey") == "cmd:secret-tool lookup service freecad-ai"
        assert ints.get("ModeIndex") == config_mod._PARAM_MODES.index("act")
        assert ints.get("ThinkingIndex") == config_mod._PARAM_THINKING.index("on")
        assert ints.get("MaxTokens") == 8192
        assert bools.get("EnableTools") is False

    def test_load_config_param_store_wins_over_json(self, tmp_path, monkeypatch):
        """If the user changed a value in Edit → Preferences (param store)
        and JSON has a different value, the param-store value wins on load.
        After seeding, both surfaces reflect the param-store value.
        """
        from unittest.mock import patch
        import freecad_ai.config as config_mod

        cfg_dir = tmp_path / "FreeCADAI"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.json"
        cfg_file.write_text(json.dumps({
            "provider": {"name": "anthropic", "model": "claude-sonnet-4-20250514"},
            "max_tokens": 4096,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(config_mod, "CONFIG_DIR", str(cfg_dir))

        group, ints, strings, bools = self._fake_param_group(
            ints={"ProviderIndex": config_mod._PARAM_PROVIDERS.index("ollama"), "MaxTokens": 16384},
            strings={"Model": "qwen3-vl:32b"},
        )
        with patch.object(config_mod, "_get_param_group", return_value=group):
            cfg = config_mod.load_config()

        # ParamGet wins — preference page changes survive
        assert cfg.provider.name == "ollama"
        assert cfg.provider.model == "qwen3-vl:32b"
        assert cfg.max_tokens == 16384

    def test_write_to_param_store_round_trips(self):
        """Write then re-apply via overrides — values come back identical."""
        from freecad_ai.config import (
            AppConfig, _apply_param_store_overrides, _write_to_param_store,
        )
        from unittest.mock import patch
        group, ints, strings, bools = self._fake_param_group()

        cfg_out = AppConfig()
        cfg_out.provider.name = "ollama"
        cfg_out.provider.model = "gemma3:4b"
        cfg_out.provider.base_url = "http://spark:11434/v1"
        cfg_out.provider.api_key = "file:/etc/keys/api"
        cfg_out.mode = "act"
        cfg_out.thinking = "on"
        cfg_out.max_tokens = 16384
        cfg_out.enable_tools = False

        with patch("freecad_ai.config._get_param_group", return_value=group):
            _write_to_param_store(cfg_out)

        cfg_in = AppConfig()  # fresh defaults
        with patch("freecad_ai.config._get_param_group", return_value=group):
            _apply_param_store_overrides(cfg_in)

        assert cfg_in.provider.name == "ollama"
        assert cfg_in.provider.model == "gemma3:4b"
        assert cfg_in.provider.base_url == "http://spark:11434/v1"
        assert cfg_in.provider.api_key == "file:/etc/keys/api"
        assert cfg_in.mode == "act"
        assert cfg_in.thinking == "on"
        assert cfg_in.max_tokens == 16384
        assert cfg_in.enable_tools is False

    def test_write_clears_stale_provider_index_for_custom(self):
        """Issue #12: saving a non-prefs provider must clear ProviderIndex.

        Scenario: user previously had anthropic (ProviderIndex=0 in the
        param store), then switched to "custom" via the main Settings
        dialog. Without clearing, the stale index would shadow the JSON
        name on next load and the provider selector would revert to
        anthropic with the custom URL/model still attached.
        """
        from freecad_ai.config import (
            AppConfig, _apply_param_store_overrides, _write_to_param_store,
        )
        from unittest.mock import patch

        group, ints, strings, _ = self._fake_param_group(
            ints={"ProviderIndex": 0},  # stale: anthropic from before
            strings={"Model": "claude-sonnet-4", "BaseUrl": "https://api.anthropic.com"},
        )

        cfg_out = AppConfig()
        cfg_out.provider.name = "custom"
        cfg_out.provider.model = "my-local-model"
        cfg_out.provider.base_url = "http://gateway.example/v1"
        cfg_out.provider.api_key = "secret"

        with patch("freecad_ai.config._get_param_group", return_value=group):
            _write_to_param_store(cfg_out)

        # ProviderIndex must be cleared so the load path doesn't shadow JSON
        assert "ProviderIndex" not in ints
        # Other fields still mirrored
        assert strings["Model"] == "my-local-model"
        assert strings["BaseUrl"] == "http://gateway.example/v1"

        # Round-trip: applying overrides onto a fresh cfg loaded from JSON
        # must keep "custom" — the absent ProviderIndex means no override.
        cfg_in = AppConfig()
        cfg_in.provider.name = "custom"  # as it would be after JSON load
        cfg_in.provider.model = "my-local-model"
        cfg_in.provider.base_url = "http://gateway.example/v1"
        with patch("freecad_ai.config._get_param_group", return_value=group):
            _apply_param_store_overrides(cfg_in)

        assert cfg_in.provider.name == "custom"
        assert cfg_in.provider.model == "my-local-model"
        assert cfg_in.provider.base_url == "http://gateway.example/v1"

    def test_write_clears_stale_provider_index_for_all_non_prefs_providers(self):
        """Same guarantee for github/huggingface/zhipu — any provider in
        PROVIDERS but not in the prefs combo must clear the stale index.
        """
        from freecad_ai.config import (
            AppConfig, _PARAM_PROVIDERS, _write_to_param_store,
        )
        from freecad_ai.llm.providers import PROVIDERS
        from unittest.mock import patch

        non_prefs = [n for n in PROVIDERS if n not in _PARAM_PROVIDERS]
        assert non_prefs, "expected at least one provider absent from prefs combo"

        for name in non_prefs:
            group, ints, _, _ = self._fake_param_group(ints={"ProviderIndex": 0})
            cfg = AppConfig()
            cfg.provider.name = name
            with patch("freecad_ai.config._get_param_group", return_value=group):
                _write_to_param_store(cfg)
            assert "ProviderIndex" not in ints, (
                f"writing provider={name!r} must clear stale ProviderIndex")

    def test_param_providers_subset_of_real_providers(self):
        """Guard against drift: every name in _PARAM_PROVIDERS must exist
        in the real PROVIDERS registry. If we drop a provider from
        providers.py without trimming this list, the prefs combo would
        offer a phantom choice.
        """
        from freecad_ai.config import _PARAM_PROVIDERS
        from freecad_ai.llm.providers import PROVIDERS
        missing = [n for n in _PARAM_PROVIDERS if n not in PROVIDERS]
        assert not missing, f"_PARAM_PROVIDERS lists unknown providers: {missing}"


class TestConfigDirResolution:
    """Migration of config dir for issue #9.

    Pre-v0.13: workbench hardcoded ``~/.config/FreeCAD/FreeCADAI/``. v0.13.0+
    moves user data to ``<FreeCAD user config dir>/FreeCADAI/`` (on FreeCAD
    1.1+ Linux: ``~/.config/FreeCAD/v1-1/FreeCADAI/``) so the workbench
    config lives in the right XDG namespace (XDG_CONFIG_HOME) and follows
    FreeCAD's version-scoping convention.

    Migration is a one-shot rename-then-move: source candidate(s) → new
    target. A marker file blocks re-runs. A sweep on every launch renames
    any historical candidate that still has data to ``.duplicate-cleanup-<ts>/``
    to recover from an aborted/buggy prior migration.
    """

    @staticmethod
    def _stage_config_dir(tmp_path):
        """Set up a fake FreeCAD user config dir under tmp_path."""
        cfg = tmp_path / "config" / "FreeCAD" / "v1-1"
        cfg.mkdir(parents=True)
        return cfg

    @staticmethod
    def _stage_uad(tmp_path):
        """Set up a fake FreeCAD user app data dir under tmp_path (XDG_DATA_HOME)."""
        uad = tmp_path / "data" / "FreeCAD" / "v1-1"
        uad.mkdir(parents=True)
        return uad

    def test_legacy_config_dir_path(self):
        from freecad_ai.config import _legacy_config_dir
        assert _legacy_config_dir() == os.path.join(
            os.path.expanduser("~"), ".config", "FreeCAD", "FreeCADAI"
        )

    def test_get_freecad_user_app_data_dir_returns_none_outside_freecad(self):
        """Pytest can't import FreeCAD — function should return None, not raise."""
        from freecad_ai.config import _get_freecad_user_app_data_dir
        assert _get_freecad_user_app_data_dir() is None

    def test_new_target_dir_is_user_config_slash_freecadai(self, tmp_path):
        """Target sits at ``<user config dir>/FreeCADAI/`` — under
        XDG_CONFIG_HOME (where settings belong), version-scoped, top-level
        in the FreeCAD config dir alongside FreeCAD's own ``FreeCAD.conf``."""
        from freecad_ai.config import _new_target_dir
        from unittest.mock import patch

        cfg = self._stage_config_dir(tmp_path)
        with patch(
            "freecad_ai.config._get_freecad_user_config_dir",
            return_value=str(cfg),
        ):
            assert _new_target_dir() == str(cfg / "FreeCADAI")

    def test_new_target_dir_returns_none_when_freecad_unavailable(self):
        from freecad_ai.config import _new_target_dir
        from unittest.mock import patch

        with patch(
            "freecad_ai.config._get_freecad_user_config_dir",
            return_value=None,
        ):
            assert _new_target_dir() is None

    def test_get_freecad_user_config_dir_falls_back_to_version_derivation(
        self, tmp_path, monkeypatch
    ):
        """When FreeCAD.getUserConfigDir() doesn't exist (e.g. older FreeCAD
        APIs), derive ``$XDG_CONFIG_HOME/FreeCAD/v<M>-<m>/`` from
        FreeCAD.Version()."""
        from freecad_ai.config import _get_freecad_user_config_dir

        fake_xdg = tmp_path / "xdg-config"
        fake_xdg.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))

        class _FakeFreeCAD:
            @staticmethod
            def Version():
                return ["1", "1", "1", "20260414", "Unknown", "...", "...", "..."]
            # Note: no getUserConfigDir — forces fallback path

        monkeypatch.setitem(__import__("sys").modules, "FreeCAD", _FakeFreeCAD)
        result = _get_freecad_user_config_dir()
        assert result == str(fake_xdg / "FreeCAD" / "v1-1")

    def test_historical_candidate_paths_orders_pre_release_before_legacy(
        self, tmp_path
    ):
        """v0.13.0-alpha pre-release wrote to ``<UAD>/FreeCADAI/`` (under
        XDG_DATA_HOME, the wrong namespace) only on the maintainer's machine.
        Listed first so its data wins over the legacy unversioned ~/.config
        path if both exist (the pre-release write is more recent)."""
        from freecad_ai.config import _historical_candidate_paths
        from unittest.mock import patch

        uad = self._stage_uad(tmp_path)
        with patch(
            "freecad_ai.config._get_freecad_user_app_data_dir",
            return_value=str(uad),
        ):
            paths = _historical_candidate_paths()
        assert paths[0] == str(uad / "FreeCADAI")  # pre-release intermediate
        assert paths[-1] == os.path.join(
            os.path.expanduser("~"), ".config", "FreeCAD", "FreeCADAI"
        )  # legacy unversioned

    def test_resolve_config_dir_honors_env_var(self, tmp_path, monkeypatch):
        from freecad_ai.config import _resolve_config_dir
        custom = tmp_path / "custom-config"
        monkeypatch.setenv("FREECAD_AI_CONFIG_DIR", str(custom))
        result = _resolve_config_dir()
        assert result == str(custom)
        assert os.path.isdir(result)

    def test_resolve_config_dir_falls_back_to_legacy_outside_freecad(
        self, tmp_path, monkeypatch
    ):
        """When FreeCAD isn't importable (pytest), use the legacy unversioned
        path with no migration. Tests must not touch the real legacy dir."""
        from freecad_ai.config import _resolve_config_dir
        from unittest.mock import patch

        monkeypatch.delenv("FREECAD_AI_CONFIG_DIR", raising=False)
        with patch(
            "freecad_ai.config._get_freecad_user_config_dir",
            return_value=None,
        ):
            result = _resolve_config_dir()
        assert result == os.path.join(
            os.path.expanduser("~"), ".config", "FreeCAD", "FreeCADAI"
        )

    def test_migrate_fresh_install_creates_target_and_marker(self, tmp_path):
        """No data anywhere — create empty target with marker."""
        from freecad_ai.config import _ACTIVE_MARKER_FILE, _migrate_to_target

        target = tmp_path / "Mod" / "FreeCADAI"
        # Candidates that don't exist
        legacy = tmp_path / "legacy" / "FreeCADAI"
        prerelease = tmp_path / "uad" / "FreeCADAI"

        _migrate_to_target([str(prerelease), str(legacy)], str(target))

        assert target.is_dir()
        assert (target / _ACTIVE_MARKER_FILE).exists()
        assert not legacy.exists()
        assert not prerelease.exists()

    def test_migrate_legacy_only_moves_to_target(self, tmp_path):
        """Standard pre-v0.13 user: only the legacy unversioned dir has data.
        Move it to the new target. Legacy ceases to exist."""
        from freecad_ai.config import _ACTIVE_MARKER_FILE, _migrate_to_target

        legacy = tmp_path / "config" / "FreeCAD" / "FreeCADAI"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_text('{"mode": "act"}')
        (legacy / "conversations").mkdir()
        (legacy / "conversations" / "abc123.json").write_text("[]")

        prerelease = tmp_path / "uad" / "FreeCADAI"  # doesn't exist
        target = tmp_path / "uad" / "Mod" / "FreeCADAI"

        _migrate_to_target([str(prerelease), str(legacy)], str(target))

        assert target.is_dir()
        assert (target / "config.json").read_text() == '{"mode": "act"}'
        assert (target / "conversations" / "abc123.json").exists()
        assert (target / _ACTIVE_MARKER_FILE).exists()
        assert not legacy.exists()
        assert not prerelease.exists()

    def test_migrate_picks_first_candidate_with_content_and_sweeps_others(
        self, tmp_path
    ):
        """Maintainer's recovery case: both ``<UAD>/FreeCADAI/`` (from buggy
        v0.13.0-alpha pre-release) and ``~/.config/FreeCAD/FreeCADAI/`` (legacy)
        have content. The pre-release path wins as source (priority order);
        the legacy path is renamed to ``.duplicate-cleanup``."""
        from freecad_ai.config import (
            _ACTIVE_MARKER_FILE,
            _DUPLICATE_CLEANUP_SUFFIX,
            _migrate_to_target,
        )

        prerelease = tmp_path / "uad" / "FreeCADAI"
        prerelease.mkdir(parents=True)
        (prerelease / "config.json").write_text('{"src": "prerelease"}')

        legacy = tmp_path / "config" / "FreeCAD" / "FreeCADAI"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_text('{"src": "legacy"}')

        target = tmp_path / "uad" / "Mod" / "FreeCADAI"
        _migrate_to_target([str(prerelease), str(legacy)], str(target))

        # Source moved to target
        assert (target / "config.json").read_text() == '{"src": "prerelease"}'
        assert (target / _ACTIVE_MARKER_FILE).exists()
        assert not prerelease.exists()
        # Legacy renamed as duplicate-cleanup backup
        assert not legacy.exists()
        legacy_backup = legacy.parent / f"FreeCADAI{_DUPLICATE_CLEANUP_SUFFIX}"
        assert legacy_backup.is_dir()
        assert (legacy_backup / "config.json").read_text() == '{"src": "legacy"}'

    def test_migrate_renames_existing_target_without_marker(self, tmp_path):
        """Edge case: something exists at target without our marker (manual
        placement, weird setup). Rename to .pre-v0.13-snapshot before moving."""
        from freecad_ai.config import (
            _ACTIVE_MARKER_FILE,
            _SNAPSHOT_BACKUP_SUFFIX,
            _migrate_to_target,
        )

        legacy = tmp_path / "config" / "FreeCADAI"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_text('{"src": "legacy"}')

        target = tmp_path / "uad" / "Mod" / "FreeCADAI"
        target.mkdir(parents=True)
        (target / "config.json").write_text('{"unexpected": true}')

        _migrate_to_target([str(legacy)], str(target))

        # Pre-existing target preserved with snapshot suffix
        snapshot = target.parent / f"FreeCADAI{_SNAPSHOT_BACKUP_SUFFIX}"
        assert snapshot.is_dir()
        assert (snapshot / "config.json").read_text() == '{"unexpected": true}'
        # Legacy data is now at target
        assert (target / "config.json").read_text() == '{"src": "legacy"}'
        assert (target / _ACTIVE_MARKER_FILE).exists()

    def test_migrate_collision_safe_with_timestamp_suffix(self, tmp_path):
        """Pre-existing .pre-v0.13-snapshot AND .duplicate-cleanup dirs (from
        a prior aborted migration) get a timestamp suffix appended on rerun
        so no data is overwritten."""
        from freecad_ai.config import (
            _DUPLICATE_CLEANUP_SUFFIX,
            _SNAPSHOT_BACKUP_SUFFIX,
            _migrate_to_target,
        )

        # Set up: legacy, prerelease both have content; target has stale data.
        legacy = tmp_path / "config" / "FreeCADAI"
        legacy.mkdir(parents=True)
        (legacy / "marker.txt").write_text("legacy v2")
        prerelease = tmp_path / "uad" / "FreeCADAI"
        prerelease.mkdir(parents=True)
        (prerelease / "marker.txt").write_text("prerelease v2")

        target = tmp_path / "uad" / "Mod" / "FreeCADAI"
        target.mkdir(parents=True)
        (target / "marker.txt").write_text("stale v2")

        # Stale prior backups
        prior_snapshot = target.parent / f"FreeCADAI{_SNAPSHOT_BACKUP_SUFFIX}"
        prior_snapshot.mkdir()
        (prior_snapshot / "marker.txt").write_text("prior snapshot v1")
        prior_dup = legacy.parent / f"FreeCADAI{_DUPLICATE_CLEANUP_SUFFIX}"
        prior_dup.mkdir()
        (prior_dup / "marker.txt").write_text("prior dup v1")

        _migrate_to_target([str(prerelease), str(legacy)], str(target))

        # Prior backups preserved untouched
        assert (prior_snapshot / "marker.txt").read_text() == "prior snapshot v1"
        assert (prior_dup / "marker.txt").read_text() == "prior dup v1"
        # New backups created with timestamp suffix — both prior + new coexist
        snapshot_siblings = [
            p.name for p in target.parent.iterdir()
            if p.name.startswith(f"FreeCADAI{_SNAPSHOT_BACKUP_SUFFIX}")
        ]
        dup_siblings = [
            p.name for p in legacy.parent.iterdir()
            if p.name.startswith(f"FreeCADAI{_DUPLICATE_CLEANUP_SUFFIX}")
        ]
        assert len(snapshot_siblings) == 2
        assert len(dup_siblings) == 2

    def test_migrate_skips_marker_only_dir_as_source(self, tmp_path):
        """A candidate that only contains the marker file (and nothing else)
        is NOT a real source — skip and try next candidate. Avoids picking
        up an empty placeholder dir that a prior bad migration left behind."""
        from freecad_ai.config import _ACTIVE_MARKER_FILE, _migrate_to_target

        # Pre-release path: only contains a stale marker, no real data
        prerelease = tmp_path / "uad" / "FreeCADAI"
        prerelease.mkdir(parents=True)
        (prerelease / _ACTIVE_MARKER_FILE).write_text("stale marker")

        # Legacy path: real data
        legacy = tmp_path / "config" / "FreeCADAI"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_text('{"src": "legacy"}')

        target = tmp_path / "uad" / "Mod" / "FreeCADAI"
        _migrate_to_target([str(prerelease), str(legacy)], str(target))

        # Legacy was moved (not the marker-only prerelease)
        assert (target / "config.json").read_text() == '{"src": "legacy"}'
        assert not legacy.exists()

    def test_resolve_skips_migration_when_marker_exists_but_still_sweeps(
        self, tmp_path, monkeypatch
    ):
        """When marker exists at target, no full migration runs — but the
        sweep still fires on every launch and renames any historical
        candidate that still has content. Recovers from a buggy prior
        migration that left duplicates."""
        from freecad_ai.config import (
            _ACTIVE_MARKER_FILE,
            _DUPLICATE_CLEANUP_SUFFIX,
            _resolve_config_dir,
        )
        from unittest.mock import patch

        monkeypatch.delenv("FREECAD_AI_CONFIG_DIR", raising=False)
        cfg = self._stage_config_dir(tmp_path)
        uad = self._stage_uad(tmp_path)
        target = cfg / "FreeCADAI"
        target.mkdir()
        (target / _ACTIVE_MARKER_FILE).write_text("already migrated")
        (target / "config.json").write_text('{"src": "target"}')

        # Stale duplicate at the legacy unversioned location (from a buggy
        # copy-based migration in the v0.13.0-alpha pre-release)
        fake_legacy = tmp_path / "fake-home" / ".config" / "FreeCAD" / "FreeCADAI"
        fake_legacy.mkdir(parents=True)
        (fake_legacy / "config.json").write_text("stale duplicate from copy migration")

        with patch(
            "freecad_ai.config._get_freecad_user_config_dir",
            return_value=str(cfg),
        ), patch(
            "freecad_ai.config._get_freecad_user_app_data_dir",
            return_value=str(uad),
        ), patch(
            "freecad_ai.config._legacy_config_dir",
            return_value=str(fake_legacy),
        ):
            result = _resolve_config_dir()

        assert result == str(target)
        # Target untouched
        assert (target / "config.json").read_text() == '{"src": "target"}'
        # Stale duplicate renamed out of the way
        assert not fake_legacy.exists()
        legacy_backup = fake_legacy.parent / f"FreeCADAI{_DUPLICATE_CLEANUP_SUFFIX}"
        assert legacy_backup.is_dir()
        assert (legacy_backup / "config.json").read_text() == "stale duplicate from copy migration"

    def test_resolve_no_op_when_marker_present_and_no_stale_legacy(
        self, tmp_path, monkeypatch
    ):
        """Steady state: marker present, no leftover candidates. Resolution
        returns target without touching the filesystem at all."""
        from freecad_ai.config import _ACTIVE_MARKER_FILE, _resolve_config_dir
        from unittest.mock import patch

        monkeypatch.delenv("FREECAD_AI_CONFIG_DIR", raising=False)
        cfg = self._stage_config_dir(tmp_path)
        uad = self._stage_uad(tmp_path)
        target = cfg / "FreeCADAI"
        target.mkdir()
        (target / _ACTIVE_MARKER_FILE).write_text("already migrated")
        (target / "config.json").write_text('{"mode": "act"}')

        # Legacy points somewhere that doesn't exist
        fake_legacy = tmp_path / "doesnt-exist" / "FreeCADAI"

        with patch(
            "freecad_ai.config._get_freecad_user_config_dir",
            return_value=str(cfg),
        ), patch(
            "freecad_ai.config._get_freecad_user_app_data_dir",
            return_value=str(uad),
        ), patch(
            "freecad_ai.config._legacy_config_dir",
            return_value=str(fake_legacy),
        ):
            marker_mtime_before = (target / _ACTIVE_MARKER_FILE).stat().st_mtime
            result = _resolve_config_dir()
            marker_mtime_after = (target / _ACTIVE_MARKER_FILE).stat().st_mtime

        assert result == str(target)
        assert marker_mtime_before == marker_mtime_after  # marker not rewritten

    def test_resolve_runs_full_migration_then_marker_blocks_rerun(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: first call moves first-priority candidate → target,
        sweeps remaining candidates, drops marker. Second call is a no-op
        (no candidates left, marker present)."""
        from freecad_ai.config import (
            _ACTIVE_MARKER_FILE,
            _DUPLICATE_CLEANUP_SUFFIX,
            _resolve_config_dir,
        )
        from unittest.mock import patch

        monkeypatch.delenv("FREECAD_AI_CONFIG_DIR", raising=False)

        # Maintainer-recovery scenario: data at both the v0.13.0-alpha
        # pre-release path (UAD/FreeCADAI) AND the legacy unversioned path.
        # Pre-release wins as migration source.
        cfg = self._stage_config_dir(tmp_path)
        uad = self._stage_uad(tmp_path)
        prerelease = uad / "FreeCADAI"
        prerelease.mkdir()
        (prerelease / "config.json").write_text('{"src": "prerelease"}')

        fake_legacy = tmp_path / "fake-home" / ".config" / "FreeCAD" / "FreeCADAI"
        fake_legacy.mkdir(parents=True)
        (fake_legacy / "config.json").write_text('{"src": "legacy"}')

        target = cfg / "FreeCADAI"

        with patch(
            "freecad_ai.config._get_freecad_user_config_dir",
            return_value=str(cfg),
        ), patch(
            "freecad_ai.config._get_freecad_user_app_data_dir",
            return_value=str(uad),
        ), patch(
            "freecad_ai.config._legacy_config_dir",
            return_value=str(fake_legacy),
        ):
            first = _resolve_config_dir()
            assert first == str(target)
            assert (target / _ACTIVE_MARKER_FILE).exists()
            assert (target / "config.json").read_text() == '{"src": "prerelease"}'
            assert not prerelease.exists()
            assert not fake_legacy.exists()
            # Legacy was swept (not deleted, not pure-moved — renamed for safety)
            legacy_backup = fake_legacy.parent / f"FreeCADAI{_DUPLICATE_CLEANUP_SUFFIX}"
            assert legacy_backup.is_dir()
            assert (legacy_backup / "config.json").read_text() == '{"src": "legacy"}'

            marker_mtime = (target / _ACTIVE_MARKER_FILE).stat().st_mtime
            second = _resolve_config_dir()
            assert second == str(target)
            assert (target / _ACTIVE_MARKER_FILE).stat().st_mtime == marker_mtime


class TestPruneOldestFiles:
    def test_prunes_oldest_by_mtime(self, tmp_path):
        from freecad_ai.config import prune_oldest_files

        for i in range(5):
            p = tmp_path / f"f{i}.json"
            p.write_text("{}")
            os.utime(p, (1000.0 + i, 1000.0 + i))
        # Mtime order f0 < f1 < f2 < f3 < f4 (newest)

        deleted = prune_oldest_files(str(tmp_path), lambda n: n.endswith(".json"), keep=2)
        assert deleted == 3

        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert remaining == ["f3.json", "f4.json"]

    def test_pattern_filter(self, tmp_path):
        from freecad_ai.config import prune_oldest_files

        for i in range(3):
            (tmp_path / f"keep-{i}.txt").write_text("x")
            (tmp_path / f"prune-{i}.json").write_text("{}")

        prune_oldest_files(str(tmp_path), lambda n: n.endswith(".json"), keep=1)

        remaining = sorted(p.name for p in tmp_path.iterdir())
        # All 3 .txt kept, only newest .json kept.
        assert "keep-0.txt" in remaining
        assert "keep-1.txt" in remaining
        assert "keep-2.txt" in remaining
        assert sum(1 for n in remaining if n.endswith(".json")) == 1

    def test_below_cap_short_circuits(self, tmp_path):
        from freecad_ai.config import prune_oldest_files

        for i in range(3):
            (tmp_path / f"f{i}.json").write_text("{}")
        deleted = prune_oldest_files(str(tmp_path), lambda n: n.endswith(".json"), keep=10)
        assert deleted == 0
        assert len(list(tmp_path.iterdir())) == 3

    def test_missing_directory_is_noop(self, tmp_path):
        from freecad_ai.config import prune_oldest_files
        deleted = prune_oldest_files(str(tmp_path / "does-not-exist"), lambda n: True, keep=0)
        assert deleted == 0

    def test_age_cap_deletes_files_older_than_threshold(self, tmp_path):
        import time as _time

        from freecad_ai.config import prune_oldest_files

        now = _time.time()
        # 3 old files (~10 days), 2 recent (~1 day).
        for i in range(3):
            p = tmp_path / f"old-{i}.json"
            p.write_text("{}")
            old = now - (10 * 86400)
            os.utime(p, (old, old))
        for i in range(2):
            p = tmp_path / f"new-{i}.json"
            p.write_text("{}")
            recent = now - (1 * 86400)
            os.utime(p, (recent, recent))

        # keep=0 disables count cap; only age cap fires.
        deleted = prune_oldest_files(
            str(tmp_path), lambda n: n.endswith(".json"), keep=0, max_age_days=7
        )
        assert deleted == 3
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert remaining == ["new-0.json", "new-1.json"]

    def test_count_and_age_caps_combine(self, tmp_path):
        import time as _time

        from freecad_ai.config import prune_oldest_files

        now = _time.time()
        # 5 files: 2 within both caps, 1 over count, 2 over age.
        # mtime order (newest → oldest): a, b, c, d, e
        for name, age_days in [
            ("a", 0.5),
            ("b", 1.0),
            ("c", 2.0),
            ("d", 10.0),  # over age
            ("e", 20.0),  # over age
        ]:
            p = tmp_path / f"{name}.json"
            p.write_text("{}")
            mtime = now - (age_days * 86400)
            os.utime(p, (mtime, mtime))

        # keep=2 → c, d, e are over count. age=7 → d, e are over age.
        # Union deleted: c, d, e. Survivors: a, b.
        deleted = prune_oldest_files(
            str(tmp_path), lambda n: n.endswith(".json"), keep=2, max_age_days=7
        )
        assert deleted == 3
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert remaining == ["a.json", "b.json"]

    def test_zero_caps_disable_pruning(self, tmp_path):
        from freecad_ai.config import prune_oldest_files

        for i in range(5):
            (tmp_path / f"f{i}.json").write_text("{}")
        deleted = prune_oldest_files(
            str(tmp_path), lambda n: n.endswith(".json"), keep=0, max_age_days=0
        )
        assert deleted == 0
        assert len(list(tmp_path.iterdir())) == 5


class TestLogsDir:
    def test_logs_dir_lives_under_config_dir(self):
        """Regression: session logs must follow CONFIG_DIR migrations.

        v0.13.0-alpha shipped with hardcoded ~/.config/FreeCAD/FreeCADAI/logs
        in chat_widget.py — the migration moved the rest of the workbench
        config but session logs continued writing to the legacy path. Asserting
        the constant relationship here ensures any future config-dir change
        carries logs along automatically.
        """
        from freecad_ai import config

        assert config.LOGS_DIR == os.path.join(config.CONFIG_DIR, "logs")

    def test_ensure_dirs_creates_logs_dir(self, tmp_config_dir):
        """_ensure_dirs() must create LOGS_DIR alongside the others."""
        from freecad_ai import config
        config._ensure_dirs()
        assert os.path.isdir(config.LOGS_DIR)


import os


def test_max_tool_turns_default():
    from freecad_ai.config import AppConfig
    assert AppConfig().max_tool_turns == 30


def test_dangerous_skip_safety_default():
    from freecad_ai.config import AppConfig
    assert AppConfig().dangerous_skip_safety is False


def test_new_fields_roundtrip():
    from freecad_ai.config import AppConfig
    cfg = AppConfig(max_tool_turns=0, dangerous_skip_safety=True)
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.max_tool_turns == 0
    assert restored.dangerous_skip_safety is True


def test_mcp_server_address_defaults():
    from freecad_ai.config import AppConfig
    cfg = AppConfig()
    assert cfg.mcp_server_host == "127.0.0.1"
    assert cfg.mcp_server_port == 3000


def test_mcp_server_address_roundtrip():
    from freecad_ai.config import AppConfig
    cfg = AppConfig(mcp_server_host="0.0.0.0", mcp_server_port=8080)
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.mcp_server_host == "0.0.0.0"
    assert restored.mcp_server_port == 8080


def test_mcp_server_allowed_hosts_defaults_to_empty():
    """Empty means "let the transport pick", which is today's behaviour.

    A non-empty default would be handed to SSEServerTransport as an explicit
    allowlist, skipping the branch that rejects a wildcard bind (#60/#66).
    """
    from freecad_ai.config import AppConfig
    assert AppConfig().mcp_server_allowed_hosts == []


def test_mcp_server_auth_token_defaults_to_empty():
    """Empty means "no token configured", which leaves the server exactly as
    unauthenticated as before this field existed (#59): opt-in, not on by
    default.
    """
    from freecad_ai.config import AppConfig
    assert AppConfig().mcp_server_auth_token == ""


def test_mcp_server_auth_token_roundtrip():
    from freecad_ai.config import AppConfig
    cfg = AppConfig(mcp_server_auth_token="s3cr3t")
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.mcp_server_auth_token == "s3cr3t"
