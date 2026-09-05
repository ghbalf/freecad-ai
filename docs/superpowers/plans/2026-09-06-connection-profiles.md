# Connection Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store LLM connection settings as named profiles so each call site — chat, compaction, skill evaluation, tool optimisation, reranking — can run on its own provider and model.

**Architecture:** `ProviderConfig` gains a `params` field and becomes the profile type. `AppConfig` stores `profiles: {label: ProviderConfig}` plus an `active_profile` label, and exposes `provider` as a **property** returning the active profile. Because every one of the 44 existing `cfg.provider.*` reads and writes means "the active chat connection", that property keeps them all correct with no edit. A single `create_client(cfg, utility=...)` in `llm/client.py` replaces all five `create_client_from_config()` calls and the bespoke reranker builder.

**Tech Stack:** Python 3.11, dataclasses, PySide6 (via `freecad_ai/ui/compat.py`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-06-connection-profiles-design.md`

## Global Constraints

- **No external dependencies.** Standard library only.
- **Never hard-import PySide2 or PySide6.** Import Qt through `freecad_ai/ui/compat.py`.
- **Use flat Qt enum forms** (`QLineEdit.Password`, not `QLineEdit.EchoMode.Password`) for PySide2/PySide6 compatibility.
- **New `AppConfig` defaults must preserve prior-version behavior.** An untouched `config.json` must behave exactly as it did before.
- **Run tests as** `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`. A shell `PYTHONPATH` shadows the venv's pluggy and crashes pytest; `test_document_attach.py` segfaults under Qt on clean master too.
- **Baseline is 1196 passing.** Any task that reduces this has broken something.
- **Branch:** `design/connection-profiles`, rebased onto master **after PR #73 merges** (it edits `config.py` and `settings_dialog.py`).
- Utility identifiers are exactly `"compaction"`, `"skill_eval"`, `"tool_optimize"`, `"rerank"`.

---

## Deviations from the spec

Three findings during planning. Each preserves the spec's user-visible behavior while changing how it is built. **Get maintainer sign-off before executing.**

**1. Profiles store concrete values, not empty-means-default.** The spec had `base_url: str = ""  # empty -> preset default`. Concrete values match how `ProviderConfig` works today, let `cfg.provider` be a live handle on the active profile, and avoid silently re-pointing a user's saved URL when a preset changes upstream — which is the #12/#75 failure mode the whole design exists to end. The API-key fallback to `provider_keys` survives unchanged, because it resolves at client-construction time rather than at read time.

**2. `cfg.provider` becomes a property instead of being deleted.** The spec said the flat fields "are no longer read". There are 44 such sites; every one means the active chat connection. A property serves them all and shrinks the diff from 44 edits to 1. The `provider` key still appears in saved JSON (Task 3) so the spec's downgrade-safety promise holds.

**3. `create_client()` takes per-call-site overrides.** The spec's signature was `create_client(cfg, utility)`. The reranker needs `max_tokens=1024`, `temperature=0.0`, `thinking="off"` — properties of the *job*, not the connection. Keyword overrides carry them.

A fourth item the spec missed entirely, needing **no** work thanks to finding 2: the FreeCAD parameter-store bridge (`config.py:631-674`, the Edit → Preferences mirror) reads and writes `cfg.provider.*`. Through the property it transparently edits the active profile, which is the correct meaning. Task 1 pins this with a test.

---

### Task 1: Profile storage and the active-profile property

**Files:**
- Modify: `freecad_ai/config.py:373-390` (`ProviderConfig`, `AppConfig` head)
- Test: `tests/unit/test_profiles.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `ProviderConfig.params: dict`; `AppConfig.profiles: dict[str, ProviderConfig]`; `AppConfig.active_profile: str`; `AppConfig.provider_keys: dict[str, str]`; `AppConfig.utility_profiles: dict[str, str]`; `AppConfig.provider` property returning `ProviderConfig`; `AppConfig.__post_init__`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_profiles.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profiles.py -q`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'profiles'`

- [ ] **Step 3: Write minimal implementation**

In `freecad_ai/config.py`, add `params` to `ProviderConfig`:

```python
@dataclass
class ProviderConfig:
    """One named connection: vendor, endpoint, credential, model, params.

    ``name`` is the *vendor* key into PROVIDER_PRESETS. A profile's own
    label is its key in ``AppConfig.profiles``, not a field here.
    """
    name: str = "anthropic"
    api_key: str = ""
    base_url: str = "https://api.anthropic.com"
    model: str = "claude-sonnet-4-6"
    params: dict = field(default_factory=dict)

    def apply_preset(self, provider_name: str):
        """Apply a provider preset, updating base_url and model to defaults."""
        preset = PROVIDER_PRESETS.get(provider_name, {})
        self.name = provider_name
        self.base_url = preset.get("base_url", self.base_url)
        self.model = preset.get("default_model", self.model)
```

Replace the `provider` field on `AppConfig` (line 390) with profile storage. Delete the `provider: ProviderConfig = field(...)` line and put these four fields in its place:

```python
    profiles: dict = field(default_factory=dict)      # label -> ProviderConfig
    active_profile: str = ""                          # label chat uses
    provider_keys: dict = field(default_factory=dict) # vendor -> default api key
    utility_profiles: dict = field(default_factory=dict)  # utility -> label
```

Add to `AppConfig`, after the field block:

```python
    def __post_init__(self):
        self._ensure_profile()

    def _ensure_profile(self) -> None:
        """Guarantee at least one profile and a valid active label.

        A config must never leave the dialog unusable, so an empty or
        dangling ``active_profile`` resolves rather than raising. Cheap
        and idempotent, so the ``provider`` property can call it on every
        access and never hand back a KeyError.
        """
        if not self.profiles:
            default = ProviderConfig()
            self.profiles = {default.name: default}
            self.active_profile = default.name
        if self.active_profile not in self.profiles:
            self.active_profile = next(iter(self.profiles))

    @property
    def provider(self) -> ProviderConfig:
        """The active profile.

        Every ``cfg.provider.*`` read and write in the codebase means "the
        active chat connection", so they all keep working through here.
        Reads AND writes: the FreeCAD parameter-store bridge assigns to
        ``cfg.provider.model`` etc., and those land in the stored profile.
        """
        self._ensure_profile()
        return self.profiles[self.active_profile]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profiles.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: Pin the parameter-store bridge**

The bridge is the reason the property must be writable. Append to `tests/unit/test_profiles.py`:

```python
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
```

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profiles.py -q`
Expected: PASS (14 tests)

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/config.py tests/unit/test_profiles.py
git commit -m "feat(config): store connections as named profiles

ProviderConfig gains a params dict and becomes the profile type.
AppConfig holds profiles by label with an active_profile pointer;
cfg.provider is now a property onto the active one, so existing
cfg.provider.* readers and writers are unchanged."
```

---

### Task 2: Migrate old-shape configs on load

**Files:**
- Modify: `freecad_ai/config.py:531-538` (`AppConfig.from_dict`)
- Test: `tests/unit/test_profiles_migration.py` (create)

**Interfaces:**
- Consumes: `AppConfig.profiles`, `AppConfig.active_profile`, `AppConfig.provider_keys`, `AppConfig.utility_profiles`, `ProviderConfig.params` from Task 1.
- Produces: `AppConfig.from_dict(data: dict) -> AppConfig` handling both shapes.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_profiles_migration.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profiles_migration.py -q`
Expected: FAIL — `KeyError: 'ollama'` / `assert [] == ['ollama']`; `from_dict` still builds a `provider` object that no longer has a field to land in.

- [ ] **Step 3: Write minimal implementation**

Replace `AppConfig.from_dict` in `freecad_ai/config.py`:

```python
    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        data = dict(data)  # never mutate the caller's parsed JSON
        legacy_provider = data.pop("provider", None)

        raw_profiles = data.pop("profiles", None)
        profiles = {
            label: ProviderConfig(**p)
            for label, p in (raw_profiles or {}).items()
        }

        known = {f.name for f in cls.__dataclass_fields__.values()} - {"profiles"}
        filtered = {k: v for k, v in data.items() if k in known}
        cfg = cls(profiles=profiles, **filtered)

        if not raw_profiles:
            cls._migrate_flat_provider(cfg, legacy_provider or {}, data)
        return cfg

    @staticmethod
    def _migrate_flat_provider(cfg: "AppConfig", legacy: dict, data: dict) -> None:
        """Turn a pre-profiles config into one or two profiles.

        Only runs when the JSON carries no ``profiles`` key, so it is a
        one-time upgrade rather than something that fights an already
        migrated file on every load.
        """
        main = ProviderConfig(**{
            k: v for k, v in legacy.items()
            if k in {"name", "api_key", "base_url", "model"}
        })
        main.params = dict(cfg.model_params.get(main.model, {}))
        cfg.profiles = {main.name: main}
        cfg.active_profile = main.name
        if main.api_key:
            cfg.provider_keys[main.name] = main.api_key

        # The old reranker override inherited each empty field from the
        # main provider (chat_widget._build_rerank_llm_client). Bake those
        # `or` fallbacks into a standalone profile.
        if data.get("rerank_llm_model"):
            cfg.profiles["rerank"] = ProviderConfig(
                name=data.get("rerank_llm_provider_name") or main.name,
                base_url=data.get("rerank_llm_base_url") or main.base_url,
                api_key=data.get("rerank_llm_api_key") or main.api_key,
                model=data["rerank_llm_model"],
                params=dict(data.get("rerank_params") or {}),
            )
            cfg.utility_profiles["rerank"] = "rerank"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profiles_migration.py tests/unit/test_profiles.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py tests/unit/test_profiles_migration.py
git commit -m "feat(config): migrate flat provider config into profiles

One-time upgrade, keyed off the absence of a profiles key. The old
reranker override becomes a 'rerank' profile with its per-field
inherit-from-main fallbacks resolved."
```

---

### Task 3: Keep writing the legacy keys for one release

**Files:**
- Modify: `freecad_ai/config.py:528-529` (`AppConfig.to_dict`)
- Test: `tests/unit/test_profiles_migration.py` (append)

**Interfaces:**
- Consumes: `AppConfig.provider` property (Task 1), `AppConfig.from_dict` (Task 2).
- Produces: `AppConfig.to_dict() -> dict` emitting both shapes.

`provider` is now a property, so `asdict()` no longer emits it — a save would drop the legacy key immediately. The spec promises it survives one release so a user who downgrades gets their settings back. This task restores that.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_profiles_migration.py`:

```python
from dataclasses import asdict  # noqa: E402


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profiles_migration.py -k Legacy -q`
Expected: FAIL — `KeyError: 'provider'`

- [ ] **Step 3: Write minimal implementation**

Replace `AppConfig.to_dict` in `freecad_ai/config.py`:

```python
    def to_dict(self) -> dict:
        """Serialise, including a legacy ``provider`` mirror.

        ``provider`` is a property now, so asdict() skips it. We write it
        anyway for one release: a user who installs this version and then
        downgrades gets their connection back instead of a blank dialog.
        Drop this mirror — and the rerank_llm_*/rerank_params fields —
        one release after profiles ship.
        """
        data = asdict(self)
        data["provider"] = {
            "name": self.provider.name,
            "api_key": self.provider.api_key,
            "base_url": self.provider.base_url,
            "model": self.provider.model,
        }
        return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profiles_migration.py -q`
Expected: PASS

- [ ] **Step 5: Run the whole suite — this is the first point the change is visible everywhere**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS. `test_config.py` and `test_api_key_resolution.py` exercise `cfg.provider` heavily and are the likely first casualties — if they fail, the property or the migration is wrong, not the tests.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/config.py tests/unit/test_profiles_migration.py
git commit -m "feat(config): keep writing the legacy provider key on save

provider is a property now so asdict() skips it; write it explicitly
for one release so a downgrade recovers the user's connection."
```

---

### Task 4: The `create_client` resolver

**Files:**
- Modify: `freecad_ai/llm/client.py:898-911` (`create_client_from_config`)
- Test: `tests/unit/test_create_client.py` (create)

**Interfaces:**
- Consumes: `AppConfig.profiles`, `.active_profile`, `.provider_keys`, `.utility_profiles`, `.model_params`, `ProviderConfig.params`.
- Produces: `create_client(cfg=None, utility=None, *, max_tokens=None, temperature=None, thinking=None) -> LLMClient` and `resolve_profile(cfg, utility=None) -> ProviderConfig`. `create_client_from_config()` stays as a zero-argument alias.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_create_client.py`:

```python
"""One resolver builds every LLM client.

Each call site names a utility; an unmapped utility inherits the active
profile, which is what every call site did before profiles existed.
"""

import pytest

from freecad_ai.config import AppConfig, ProviderConfig
from freecad_ai.llm.client import create_client, resolve_profile


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


class TestResolveProfile:
    def test_returns_the_profile_object_itself(self):
        cfg = _cfg()
        assert resolve_profile(cfg) is cfg.profiles["cloud"]

    def test_named_utility_resolves_to_its_profile(self):
        cfg = _cfg()
        cfg.utility_profiles["skill_eval"] = "local"
        assert resolve_profile(cfg, "skill_eval") is cfg.profiles["local"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_create_client.py -q`
Expected: FAIL — `ImportError: cannot import name 'create_client' from 'freecad_ai.llm.client'`

- [ ] **Step 3: Write minimal implementation**

Replace `create_client_from_config` in `freecad_ai/llm/client.py`:

```python
def resolve_profile(cfg, utility: str | None = None):
    """Return the ProviderConfig a call site should use.

    ``utility`` is a call-site identifier ("compaction", "skill_eval",
    "tool_optimize", "rerank"). None, unmapped, empty, or naming a profile
    that no longer exists all mean "inherit the active profile" — a
    deleted profile must never make the workbench unusable.
    """
    if utility:
        label = cfg.utility_profiles.get(utility, "")
        if label and label in cfg.profiles:
            return cfg.profiles[label]
    return cfg.provider


def create_client(cfg=None, utility: str | None = None, *,
                  max_tokens: int | None = None,
                  temperature: float | None = None,
                  thinking: str | None = None) -> LLMClient:
    """Build an LLMClient for one call site.

    Connection settings (vendor, url, key, model, params) come from the
    resolved profile. Job settings (max_tokens, temperature, thinking)
    come from the config unless the call site overrides them — the
    reranker wants 1024 tokens and no thinking whichever profile it runs
    on.

    An empty ``api_key`` on the profile falls back to the vendor-wide
    default in ``cfg.provider_keys``, so one Anthropic secret serves every
    Anthropic profile while a gateway-authenticated Ollama profile can
    still carry its own.
    """
    from ..config import get_config
    if cfg is None:
        cfg = get_config()
    profile = resolve_profile(cfg, utility)

    params = dict(cfg.model_params.get(profile.model, {}))
    params.update(profile.params)

    return LLMClient(
        provider_name=profile.name,
        base_url=profile.base_url,
        api_key=profile.api_key or cfg.provider_keys.get(profile.name, ""),
        model=profile.model,
        max_tokens=cfg.max_tokens if max_tokens is None else max_tokens,
        temperature=cfg.temperature if temperature is None else temperature,
        thinking=cfg.thinking if thinking is None else thinking,
        model_params=params,
    )


def create_client_from_config() -> LLMClient:
    """Chat client from the active profile. Kept for third-party hooks."""
    return create_client()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_create_client.py -q`
Expected: PASS (21 tests)

- [ ] **Step 5: Confirm nothing else regressed**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/llm/client.py tests/unit/test_create_client.py
git commit -m "feat(llm): add create_client(cfg, utility) resolver

One resolver for every call site. Connection settings come from the
resolved profile, job settings (max_tokens/temperature/thinking) from
config unless the call site overrides them."
```

---

### Task 5: Route compaction, skill evaluation and tool optimisation

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py:528-529` (compaction worker)
- Modify: `freecad_ai/extensions/skill_evaluator.py:218-226`
- Modify: `freecad_ai/tools/optimize_tools.py:159-161`
- Test: `tests/unit/test_utility_call_sites.py` (create)

**Interfaces:**
- Consumes: `create_client(cfg, utility, ...)` from Task 4.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_utility_call_sites.py`:

```python
"""Each utility call site asks for its own identifier.

Testing intent rather than plumbing: patch create_client, run the call
site, assert which utility name it requested. Without this, a call site
silently keeps using the chat model and nobody notices.
"""

import pytest

# settings_dialog/chat_widget import through ui/compat.py, which needs Qt.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from unittest.mock import MagicMock, patch  # noqa: E402


class TestCompaction:
    def test_requests_the_compaction_utility(self):
        from freecad_ai.ui.chat_widget import _CompactionWorker
        worker = _CompactionWorker("some conversation text")
        fake = MagicMock()
        fake.send.return_value = "summary"
        with patch("freecad_ai.llm.client.create_client",
                   return_value=fake) as mk:
            worker.run()
        assert mk.call_args.args[1:] == ("compaction",) or \
            mk.call_args.kwargs.get("utility") == "compaction"


class TestToolOptimizer:
    def test_requests_the_tool_optimize_utility(self):
        from freecad_ai.tools.optimize_tools import _ask_llm_for_modification
        fake = MagicMock()
        fake.send.return_value = "```skill\ncontent\n```"
        with patch("freecad_ai.llm.client.create_client",
                   return_value=fake) as mk:
            _ask_llm_for_modification("skill", 1, 0.5, "results", "strategy")
        assert mk.call_args.args[1:] == ("tool_optimize",) or \
            mk.call_args.kwargs.get("utility") == "tool_optimize"
```

Skill evaluation is covered by inspection rather than a unit test — `SkillEvaluator.run` needs a tool registry and a live document. Verify by reading the diff.

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_utility_call_sites.py -q`
Expected: FAIL — `AttributeError: <module 'freecad_ai.llm.client'> does not have the attribute 'create_client'` is already fixed by Task 4, so instead: the assertion fails because the call sites still call `create_client_from_config`, and the patch is never hit (`mk.call_args is None`).

- [ ] **Step 3: Write minimal implementation**

In `freecad_ai/ui/chat_widget.py`, `_CompactionWorker.run`:

```python
            from ..llm.client import create_client
            client = create_client(utility="compaction")
```

In `freecad_ai/tools/optimize_tools.py`, `_ask_llm_for_modification`:

```python
    from ..llm.client import create_client

    client = create_client(utility="tool_optimize")
```

In `freecad_ai/extensions/skill_evaluator.py`, `SkillEvaluator.run`:

```python
        from ..llm.client import create_client
```
and
```python
        client = create_client(cfg, "skill_eval")
```

Leave `api_style = get_api_style(cfg.provider.name)` on line 224 as it is — the tool schema style follows the chat provider, which is what builds the conversation.

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_utility_call_sites.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/chat_widget.py freecad_ai/extensions/skill_evaluator.py \
        freecad_ai/tools/optimize_tools.py tests/unit/test_utility_call_sites.py
git commit -m "feat: route compaction, skill eval and tool optimisation by utility

Each now names its own profile slot instead of being locked to the
chat model."
```

---

### Task 6: Retire the bespoke reranker builder

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py:95-135` (delete `_build_rerank_llm_client`), `:156`
- Modify: `tests/unit/test_reranker_namespace.py`
- Test: `tests/unit/test_create_client.py` (append)

**Interfaces:**
- Consumes: `create_client(cfg, "rerank", max_tokens=..., temperature=..., thinking=...)`.
- Produces: `_build_rerank_llm_client` no longer exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_create_client.py`:

```python
class TestRerankerJobSettings:
    def test_reranker_settings_survive_the_move(self):
        """The old builder pinned max_tokens=1024, thinking off, and
        temperature from params defaulting to 0.0. Reranking is a
        classification job; those must not drift to the chat values."""
        cfg = _cfg()
        cfg.max_tokens = 8192
        cfg.thinking = "extended"
        cfg.utility_profiles["rerank"] = "local"
        cfg.profiles["local"].params = {"temperature": 0.1, "top_k": 20}
        params = dict(cfg.model_params.get("qwen3:8b", {}))
        params.update(cfg.profiles["local"].params)
        client = create_client(cfg, "rerank", max_tokens=1024,
                               temperature=params.get("temperature", 0.0),
                               thinking="off")
        assert client.max_tokens == 1024
        assert client.thinking == "off"
        assert client.temperature == 0.1
        assert client.model_params == {"temperature": 0.1, "top_k": 20}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_create_client.py -k Reranker -q`
Expected: PASS already — this test exercises Task 4's resolver and is a characterisation test protecting the reranker's job settings through the refactor. That is intentional; the *failing* test for this task is the rewritten `test_reranker_namespace.py` in Step 3.

- [ ] **Step 3: Rewrite the reranker namespace test against the new API**

Replace the body of `tests/unit/test_reranker_namespace.py` below its imports. Update the module docstring's closing paragraph to:

```python
"""...
Profiles retire this class of bug structurally: params are a field of a
profile, so there is no shared namespace for the reranker to reach into.
These tests now pin that the reranker reads its own profile's params and
that an inheriting reranker gets the active profile's.
"""
```

Replace the import and tests:

```python
from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
from freecad_ai.llm.client import create_client  # noqa: E402


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
```

Delete the `TestBuildRerankClientReadPath` and `SettingsDialog._resolve_rerank_params` classes — both test code this task removes.

- [ ] **Step 4: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_reranker_namespace.py -q`
Expected: FAIL — `ImportError: cannot import name '_build_rerank_llm_client'` is gone, but `create_client(cfg, "rerank")` still returns the chat model because nothing routes it yet. Confirm the failure is the assertion, not the import.

- [ ] **Step 5: Write minimal implementation**

Delete `_build_rerank_llm_client` (`freecad_ai/ui/chat_widget.py:95-135`) entirely. At line 156, replace:

```python
            client = _build_rerank_llm_client(cfg)
```

with:

```python
            from ..llm.client import create_client, resolve_profile
            # Reranking is a classification job: short output, no thinking,
            # deterministic unless the profile itself sets a temperature.
            # Those are call-site properties and stay fixed whichever
            # profile the user points the reranker at.
            profile = resolve_profile(cfg, "rerank")
            params = dict(cfg.model_params.get(profile.model, {}))
            params.update(profile.params)
            client = create_client(
                cfg, "rerank", max_tokens=1024, thinking="off",
                temperature=params.get("temperature", 0.0))
```



- [ ] **Step 6: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_reranker_namespace.py tests/unit/test_create_client.py -q`
Expected: PASS

- [ ] **Step 7: Full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add freecad_ai/ui/chat_widget.py tests/unit/test_reranker_namespace.py \
        tests/unit/test_create_client.py
git commit -m "refactor(rerank): use the profile resolver, drop the bespoke builder

The reranker's four-field override becomes a profile like any other.
Its job settings (1024 tokens, no thinking) stay at the call site."
```

---

### Task 7: Profile selector in the Settings dialog

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py` — provider page layout, `_load_from_config` (:873-895), `_on_provider_changed` (:974)
- Test: `tests/unit/test_profile_selector.py` (create)

**Interfaces:**
- Consumes: `AppConfig.profiles`, `.active_profile`, `.utility_profiles` (Task 1).
- Produces: `SettingsDialog._rename_profile(old: str, new: str) -> None`, `SettingsDialog._delete_profile(label: str) -> None`, `SettingsDialog._commit_profile_fields() -> None`, `self.profile_combo`.

Rename and delete carry the logic worth testing, so they go in plain methods that take arguments and touch `self._cfg` — not in signal handlers that read widgets. That is the same extraction pattern the input-history work used, and it is what makes these testable without a running dialog.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_profile_selector.py`:

```python
"""Profile management: rename cascades, delete never orphans.

These call the dialog's methods with a fake self carrying only the
attributes they touch, so no Qt dialog has to be constructed.
"""

import pytest

# settings_dialog/chat_widget import through ui/compat.py, which needs Qt.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


class _FakeSelf:
    def __init__(self, cfg):
        self._cfg = cfg


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
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "ollama-local")
        assert set(cfg.profiles) == {"cloud", "ollama-local"}

    def test_settings_travel_with_the_name(self):
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "ollama-local")
        assert cfg.profiles["ollama-local"].model == "qwen3:8b"

    def test_active_profile_follows(self):
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "cloud", "anthropic-main")
        assert cfg.active_profile == "anthropic-main"

    def test_utility_mappings_follow(self):
        """A rename must never silently detach a utility from the profile
        it was using."""
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "local", "rerank": "local"}
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "cheap")
        assert cfg.utility_profiles == {"compaction": "cheap", "rerank": "cheap"}

    def test_unrelated_mappings_are_untouched(self):
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "cloud", "rerank": "local"}
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "cheap")
        assert cfg.utility_profiles["compaction"] == "cloud"

    def test_ordering_is_preserved(self):
        """Rebuilding the dict must not reshuffle the combo on the user."""
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "cloud", "zzz")
        assert list(cfg.profiles) == ["zzz", "local"]

    def test_collision_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "cloud")

    def test_empty_name_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "  ")

    def test_renaming_to_itself_is_a_no_op(self):
        cfg = _cfg()
        SettingsDialog._rename_profile(_FakeSelf(cfg), "local", "local")
        assert set(cfg.profiles) == {"cloud", "local"}


class TestDelete:
    def test_profile_is_removed(self):
        cfg = _cfg()
        SettingsDialog._delete_profile(_FakeSelf(cfg), "local")
        assert set(cfg.profiles) == {"cloud"}

    def test_deleting_the_last_profile_is_refused(self):
        cfg = _cfg()
        del cfg.profiles["local"]
        with pytest.raises(ValueError):
            SettingsDialog._delete_profile(_FakeSelf(cfg), "cloud")

    def test_deleting_the_active_profile_moves_the_pointer(self):
        cfg = _cfg()
        SettingsDialog._delete_profile(_FakeSelf(cfg), "cloud")
        assert cfg.active_profile == "local"

    def test_utilities_pointing_at_it_fall_back_to_inherit(self):
        """Leaving a dangling name would work — the resolver tolerates it —
        but clearing it keeps the dialog honest about what is configured."""
        cfg = _cfg()
        cfg.utility_profiles = {"compaction": "local"}
        SettingsDialog._delete_profile(_FakeSelf(cfg), "local")
        assert cfg.utility_profiles.get("compaction", "") == ""

    def test_deleting_an_unknown_profile_is_refused(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            SettingsDialog._delete_profile(_FakeSelf(cfg), "nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profile_selector.py -q`
Expected: FAIL — `AttributeError: type object 'SettingsDialog' has no attribute '_rename_profile'`

- [ ] **Step 3: Write minimal implementation**

Add to `SettingsDialog` in `freecad_ai/ui/settings_dialog.py`:

```python
    def _rename_profile(self, old: str, new: str) -> None:
        """Rename a profile, carrying every reference to it along.

        A profile's label is its identity — utility_profiles and
        active_profile store the name, not a stable id — so a rename that
        did not cascade would silently detach a utility from the
        connection it was using.
        """
        new = (new or "").strip()
        if not new:
            raise ValueError("Profile name cannot be empty")
        if old == new:
            return
        if new in self._cfg.profiles:
            raise ValueError(f"A profile named {new!r} already exists")
        if old not in self._cfg.profiles:
            raise ValueError(f"No profile named {old!r}")
        # Rebuild in place so the combo's order does not shuffle.
        self._cfg.profiles = {
            (new if label == old else label): prof
            for label, prof in self._cfg.profiles.items()
        }
        if self._cfg.active_profile == old:
            self._cfg.active_profile = new
        for utility, label in list(self._cfg.utility_profiles.items()):
            if label == old:
                self._cfg.utility_profiles[utility] = new

    def _delete_profile(self, label: str) -> None:
        """Remove a profile, leaving nothing pointing at it."""
        if label not in self._cfg.profiles:
            raise ValueError(f"No profile named {label!r}")
        if len(self._cfg.profiles) == 1:
            raise ValueError("At least one profile is required")
        del self._cfg.profiles[label]
        if self._cfg.active_profile == label:
            self._cfg.active_profile = next(iter(self._cfg.profiles))
        for utility, mapped in list(self._cfg.utility_profiles.items()):
            if mapped == label:
                self._cfg.utility_profiles[utility] = ""
```

`self._cfg` must exist. In `__init__`, before `_load_from_config()`, add:

```python
        self._cfg = get_config()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_profile_selector.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Add the selector widgets**

On the provider page, above the existing provider combo, add:

```python
        # ── Profile selector ────────────────────────────────────────
        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip(translate(
            "SettingsDialog",
            "Named connection. Utilities below can each use a different one."))
        profile_row.addWidget(self.profile_combo, 1)
        self.profile_add_btn = QPushButton(translate("SettingsDialog", "New"))
        self.profile_rename_btn = QPushButton(translate("SettingsDialog", "Rename"))
        self.profile_delete_btn = QPushButton(translate("SettingsDialog", "Delete"))
        for b in (self.profile_add_btn, self.profile_rename_btn,
                  self.profile_delete_btn):
            profile_row.addWidget(b)
        form.addRow(translate("SettingsDialog", "Profile:"), profile_row)

        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self.profile_add_btn.clicked.connect(self._on_profile_add)
        self.profile_rename_btn.clicked.connect(self._on_profile_rename)
        self.profile_delete_btn.clicked.connect(self._on_profile_delete)
```

And the handlers:

```python
    def _refresh_profile_combo(self) -> None:
        """Repopulate the profile combo without firing its handler."""
        self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            for label in self._cfg.profiles:
                self.profile_combo.addItem(label, label)
            idx = self.profile_combo.findData(self._cfg.active_profile)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        finally:
            self.profile_combo.blockSignals(False)

    def _commit_profile_fields(self) -> None:
        """Write the visible connection widgets back into their profile.

        Called before switching away from a profile so an in-progress edit
        is not lost — the #75 complaint, from the other direction.
        """
        label = getattr(self, "_current_profile_label", None)
        prof = self._cfg.profiles.get(label)
        if prof is None:
            return
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        if 0 <= idx < len(names):
            prof.name = names[idx]
        prof.base_url = self.base_url_edit.text()
        prof.api_key = self.api_key_edit.text()
        prof.model = self.model_edit.text()

    def _show_profile(self, label: str) -> None:
        """Populate the connection widgets from a profile."""
        prof = self._cfg.profiles[label]
        self._current_profile_label = label
        names = get_provider_names()
        try:
            idx = names.index(prof.name)
        except ValueError:
            idx = 0
        # Programmatic index moves must not run _on_provider_changed —
        # that handler exists to apply a preset on a *user* switch, and
        # firing it here would overwrite the profile's saved URL (#75).
        self.provider_combo.blockSignals(True)
        try:
            self.provider_combo.setCurrentIndex(idx)
        finally:
            self.provider_combo.blockSignals(False)
        self.api_key_edit.setText(prof.api_key)
        self.base_url_edit.setText(prof.base_url)
        self.model_edit.setText(prof.model)
        self._load_model_params_table(prof.model, self._cfg)

    def _on_profile_changed(self, index):
        label = self.profile_combo.itemData(index)
        if not label or label == getattr(self, "_current_profile_label", None):
            return
        self._commit_profile_fields()
        self._cfg.active_profile = label
        self._show_profile(label)

    def _on_profile_add(self):
        base = translate("SettingsDialog", "New profile")
        label, n = base, 2
        while label in self._cfg.profiles:
            label, n = f"{base} {n}", n + 1
        self._commit_profile_fields()
        self._cfg.profiles[label] = ProviderConfig()
        self._cfg.active_profile = label
        self._refresh_profile_combo()
        self._show_profile(label)

    def _on_profile_rename(self):
        old = self._current_profile_label
        new, ok = QInputDialog.getText(
            self, translate("SettingsDialog", "Rename profile"),
            translate("SettingsDialog", "Name:"), QLineEdit.Normal, old)
        if not ok:
            return
        try:
            self._rename_profile(old, new)
        except ValueError as e:
            QMessageBox.warning(
                self, translate("SettingsDialog", "Rename profile"), str(e))
            return
        self._current_profile_label = new.strip()
        self._refresh_profile_combo()

    def _on_profile_delete(self):
        label = self._current_profile_label
        if QMessageBox.question(
                self, translate("SettingsDialog", "Delete profile"),
                translate("SettingsDialog",
                          "Delete profile '{}'?").format(label)) \
                != QMessageBox.Yes:
            return
        try:
            self._delete_profile(label)
        except ValueError as e:
            QMessageBox.warning(
                self, translate("SettingsDialog", "Delete profile"), str(e))
            return
        self._refresh_profile_combo()
        self._show_profile(self._cfg.active_profile)
```

Add `QInputDialog`, `QMessageBox`, `QPushButton`, `QHBoxLayout` to the imports from `.compat` if not already present, and `ProviderConfig` to the `..config` import.

- [ ] **Step 6: Rework `_load_from_config` to go through the profile path**

Replace lines 873-887 of `freecad_ai/ui/settings_dialog.py`:

```python
    def _load_from_config(self):
        """Populate fields from the current config."""
        cfg = self._cfg = get_config()

        self._refresh_profile_combo()
        self._show_profile(cfg.active_profile)

        self.max_tokens_spin.setValue(cfg.max_tokens)
```

The provider/key/url/model/params lines are now `_show_profile`'s job. Everything from `max_tokens_spin` onward stays as it was.

This removes the load-path fragility the spec called out: today the sequence is correct only because line 885 happens to re-set the base URL *after* line 882's `setCurrentIndex` clobbered it. `_show_profile` blocks the signal instead of racing it.

- [ ] **Step 7: Make `_on_provider_changed` write to the profile**

`_on_provider_changed` keeps its preset-applying behavior — a user picking a different vendor *should* get that vendor's URL — but must now also record it. At the end of the handler, after the params table reload, add:

```python
            # A vendor switch is an explicit "point this profile
            # elsewhere", so record it. Only a user-driven change reaches
            # here: programmatic index moves are wrapped in blockSignals.
            self._commit_profile_fields()
```

- [ ] **Step 8: Verify the whole suite still passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py tests/unit/test_profile_selector.py
git commit -m "feat(settings): profile selector with cascading rename

Adds New/Rename/Delete for connection profiles. Renames carry
active_profile and utility_profiles along; deletes never orphan a
utility. Programmatic combo moves are blockSignals-guarded so the
load path no longer depends on statement order (#75)."
```

---

### Task 8: Per-utility profile dropdowns

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py` — provider page (bottom), `_load_from_config`, `_save_settings`; delete the `rerank_llm_*` widget group (:540-570 and its load/save lines)
- Test: `tests/unit/test_utility_dropdowns.py` (create)

**Interfaces:**
- Consumes: `AppConfig.utility_profiles`, `SettingsDialog._cfg`.
- Produces: `SettingsDialog.UTILITIES: list[tuple[str, str]]`, `SettingsDialog._collect_utility_profiles(selections: dict) -> dict` (classmethod).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_utility_dropdowns.py`:

```python
"""Utility → profile mapping, as the dialog collects it.

_collect_utility_profiles takes what the dropdowns hold and produces the
config dict, so the filtering rule (inherit is stored as absent, not as a
dangling name) is testable without Qt.
"""

import pytest

# settings_dialog/chat_widget import through ui/compat.py, which needs Qt.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


class TestUtilityIdentifiers:
    def test_all_four_are_offered(self):
        assert {u for u, _ in SettingsDialog.UTILITIES} == {
            "compaction", "skill_eval", "tool_optimize", "rerank"}

    def test_each_has_a_label(self):
        assert all(label for _, label in SettingsDialog.UTILITIES)


class TestCollect:
    def test_inherit_is_stored_as_absent(self):
        """An empty selection means inherit; storing it as a key with an
        empty value would work but leaves noise in config.json."""
        assert SettingsDialog._collect_utility_profiles(
            {"compaction": "", "rerank": ""}) == {}

    def test_explicit_choices_are_kept(self):
        assert SettingsDialog._collect_utility_profiles(
            {"compaction": "cheap", "rerank": ""}) == {
                "compaction": "cheap"}

    def test_unknown_utilities_are_dropped(self):
        """Defends the config against a stale key from a future version
        being written back by an older one."""
        assert SettingsDialog._collect_utility_profiles(
            {"compaction": "cheap", "not_a_utility": "x"}) == {
                "compaction": "cheap"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_utility_dropdowns.py -q`
Expected: FAIL — `AttributeError: type object 'SettingsDialog' has no attribute 'UTILITIES'`

- [ ] **Step 3: Write minimal implementation**

Add to `SettingsDialog`, as a class attribute near the top of the class body:

```python
    # Call sites that can run on their own profile. The identifier is the
    # contract with create_client(cfg, utility); adding a new one here and
    # at its call site is the whole opt-in.
    UTILITIES = [
        ("compaction", "Context compaction"),
        ("skill_eval", "Skill evaluation"),
        ("tool_optimize", "Tool optimisation"),
        ("rerank", "Tool reranking"),
    ]

    @classmethod
    def _collect_utility_profiles(cls, selections: dict) -> dict:
        """Turn dropdown selections into the config mapping.

        An empty selection means inherit the active profile and is stored
        by omission, so config.json carries only real overrides.

        A classmethod because it touches no widgets — that is what makes
        it testable without constructing a dialog.
        """
        known = {u for u, _ in cls.UTILITIES}
        return {
            utility: label
            for utility, label in selections.items()
            if utility in known and label
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_utility_dropdowns.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Add the widgets at the bottom of the provider page**

Per the spec: below the profile fields the dropdowns refer to, so the reading order is "define connections, then say which one each job uses."

```python
        # ── Utilities ───────────────────────────────────────────────
        self.utility_group = QGroupBox(translate(
            "SettingsDialog", "Utility models"))
        util_form = QFormLayout()
        self.utility_combos = {}
        for utility, label in self.UTILITIES:
            combo = QComboBox()
            combo.setToolTip(translate(
                "SettingsDialog",
                "Which profile this job runs on. Leave inherited to use "
                "the active profile."))
            self.utility_combos[utility] = combo
            util_form.addRow(
                translate("SettingsDialog", label) + ":", combo)
        self.utility_group.setLayout(util_form)
        layout.addWidget(self.utility_group)
```

And the repopulate helper, called from `_refresh_profile_combo` so the lists track renames and deletes:

```python
    def _refresh_utility_combos(self) -> None:
        """Repopulate every utility dropdown from the current profiles."""
        for utility, combo in self.utility_combos.items():
            current = self._cfg.utility_profiles.get(utility, "")
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItem(translate(
                    "SettingsDialog", "(same as active profile)"), "")
                for label in self._cfg.profiles:
                    combo.addItem(label, label)
                idx = combo.findData(current)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                combo.blockSignals(False)
```

Call it at the end of `_refresh_profile_combo`.

- [ ] **Step 6: Save the selections**

In `_save_settings`, where the `rerank_llm_*` fields were being written, put:

```python
        self._commit_profile_fields()
        cfg.profiles = self._cfg.profiles
        cfg.active_profile = self._cfg.active_profile
        cfg.utility_profiles = self._collect_utility_profiles({
            utility: combo.currentData()
            for utility, combo in self.utility_combos.items()
        })
```

Delete the `rerank_llm_group` construction (`:540-570`), its `_load_from_config` population lines, its `_save_settings` writes, and `_resolve_rerank_params`. The reranker's connection is a profile now.

- [ ] **Step 7: Verify**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS. Any test referencing `rerank_llm_provider_combo` or `_resolve_rerank_params` should have been removed in Task 6; if one survives, it is testing deleted code — delete it.

- [ ] **Step 8: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py tests/unit/test_utility_dropdowns.py
git commit -m "feat(settings): per-utility profile dropdowns

Compaction, skill evaluation, tool optimisation and reranking each pick
a profile or inherit the active one. Replaces the reranker's bespoke
four-field override group."
```

---

### Task 9: Live verification, docs and changelog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md` (configuration section)
- Test: manual, in a real FreeCAD session

Unit tests cannot catch an invalid Qt layout or a handler wired to the wrong signal, and this task touches the dialog heavily. Verify in the GUI.

- [ ] **Step 1: Back up the real config before touching it**

```bash
cp ~/.config/FreeCAD/FreeCADAI/config.json /tmp/config.json.pre-profiles
```

- [ ] **Step 2: Launch FreeCAD and exercise the dialog**

```bash
QT_QPA_PLATFORM=xcb ~/bin/freecad
```

Walk the migration and the new UI:

1. Open AI Settings. The profile combo shows one profile named after your current provider, with your existing URL, key and model intact.
2. Create a second profile, point it at Ollama, edit its Base URL to something non-default.
3. Switch back to the first profile and return. **The edited URL is still there** — this is #75.
4. Rename the second profile. It stays selected and keeps its settings.
5. Set Context compaction to the second profile. Save, reopen: the choice persisted.
6. Send a chat message long enough to trigger compaction; confirm in the Report View that the compaction call went to the second profile's model.
7. Delete the second profile. Compaction falls back to inherited, and the dialog still opens.
8. Open Edit → Preferences. The provider, model, URL and key shown are the active profile's — confirming the parameter-store bridge still tracks it.

- [ ] **Step 3: Confirm the config round-tripped**

```bash
python3 -m json.tool ~/.config/FreeCAD/FreeCADAI/config.json | \
  grep -A3 -E '"(profiles|active_profile|utility_profiles|provider)"' | head -40
```

Expected: `profiles` and `active_profile` present; a legacy `provider` mirror matching the active profile.

- [ ] **Step 4: Update the changelog**

Add under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
### Added
- **Connection profiles.** LLM connection settings are now named profiles.
  Define as many as you like — `ollama-local` and `ollama-remote` can
  coexist with different URLs and keys — and switch between them from the
  Settings dialog without losing anything.
- **Per-utility models.** Context compaction, skill evaluation, tool
  optimisation and tool reranking each choose a profile, or inherit the
  active one. Run chat on a large cloud model and the throwaway work on a
  cheap or local one.

### Changed
- The reranker's four-field provider override is replaced by a profile.
  Existing overrides migrate automatically into a profile named `rerank`.

### Fixed
- Switching provider in the Settings dialog no longer overwrites a
  Base URL you had edited (#75). Connection settings live in the profile,
  and programmatic dropdown moves no longer fire the preset handler.
```

- [ ] **Step 5: Update the README**

In the configuration section, document the profile concept and the utility dropdowns. Keep it to a short subsection — what a profile is, that utilities can each pick one, and that existing configs migrate with no action needed.

- [ ] **Step 6: Restore the pre-test config if you were experimenting**

```bash
# Only if the live session left the config in a state you do not want:
cp /tmp/config.json.pre-profiles ~/.config/FreeCAD/FreeCADAI/config.json
```

- [ ] **Step 7: Final full-suite run**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: PASS, count at or above the 1196 baseline.

- [ ] **Step 8: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "docs: connection profiles and per-utility models"
```

- [ ] **Step 9: Close out**

Close #75 referencing the profile work, and note on #30 that the shared-params hazard is now structurally impossible rather than comment-enforced.

---

## Notes for the executor

**Do not start until PR #73 has merged and this branch is rebased onto it.** #73 edits `config.py` and `settings_dialog.py` — the two files this plan rewrites most. It has been waiting on its author for a week; restructuring underneath it would force a contributor to redo their work.

**The riskiest task is 2, not 7.** A UI mistake is visible immediately; a migration mistake silently mangles a config file the user has been running for months. If a step in Task 2 feels ambiguous, stop and ask rather than guessing.

**`cfg.provider` is load-bearing in 44 places.** If you find yourself editing one of them, ask whether the property is doing its job — the answer is usually that it is, and the edit is unnecessary.
