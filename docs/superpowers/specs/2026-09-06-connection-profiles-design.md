# Connection Profiles — Design

**Issues:** [#75 — Switching provider silently overwrites a user-edited Base URL](https://github.com/ghbalf/freecad-ai/issues/75) (resolved by this design)
**Date:** 2026-09-06
**Status:** Draft (awaiting review)

## Goal

Let each LLM call site — the chat itself and the utility calls it makes — use its
own connection settings, so a cheap or local model can do the throwaway work
while chat runs on an expensive one.

Today exactly one utility can be redirected. The LLM reranker reads a bespoke
four-field override (`chat_widget.py:116-119`); compaction, skill evaluation and
tool optimisation all call bare `create_client_from_config()` and are locked to
the chat model. Adding a second override group per utility would mean five
config fields hand-wired in four places, so this replaces the pattern rather
than repeating it.

The same change resolves #75. That bug exists because one flat set of provider
fields is overwritten from a preset whenever the provider dropdown moves; once
connection settings are stored per named profile, browsing between them stops
destroying anything.

## Approved decisions

| Decision | Choice |
|----------|--------|
| Unit of configuration | **Named profile** — an arbitrary label over `{provider, base_url, api_key, model, params}` |
| Several profiles per vendor | **Supported.** `ollama-local` and `ollama-remote` may coexist |
| API key location | **In the profile**, falling back to a per-provider default when the profile's own key is empty |
| Main chat config | **Is a profile.** The flat `provider.*` fields migrate into one and are no longer read |
| Utility selection | `utility_profiles: {utility_name: profile_name}`; absent or empty means *inherit the active profile* |
| Extensibility | **By name, not by schema.** A new call site opts in by choosing an identifier; no new config fields |
| Sampling params | **Inside the profile.** Two profiles cannot share a params namespace |
| Legacy keys | **Left in the JSON, unread, for one release.** Dropped a version later |
| Sequencing | **After PR #73 merges** — it edits the same two files |
| Load-path guard | `blockSignals` around `setCurrentIndex` ships regardless |

## Schema

```python
@dataclass
class Profile:
    provider: str = "anthropic"      # key into PROVIDER_PRESETS
    base_url: str = ""               # empty -> preset default
    api_key:  str = ""               # empty -> provider_keys[provider]
    model:    str = ""               # empty -> preset default
    params:   dict = field(default_factory=dict)


@dataclass
class AppConfig:
    profiles:         dict = field(default_factory=dict)   # name -> Profile
    active_profile:   str  = ""                            # what chat uses
    provider_keys:    dict = field(default_factory=dict)   # vendor -> default key
    utility_profiles: dict = field(default_factory=dict)   # utility -> profile name
```

Utility identifiers are fixed strings: `"compaction"`, `"skill_eval"`,
`"tool_optimize"`, `"rerank"`.

### Why the API key sits in the profile with a provider-level fallback

Both pure positions lose something. A key held only per vendor cannot express
`ollama-local` needing no credential while `ollama-remote` sits behind an
authenticating gateway. A key held only per profile makes you re-enter the same
Anthropic secret for every Anthropic profile.

Resolution is therefore `profile.api_key or provider_keys[profile.provider]`.
The common case costs one entry; the divergent case stays expressible. This is
the `or`-fallback idiom the codebase already uses for the reranker override and
for the MCP env-over-config resolvers, so it should read as familiar.

## Resolution

One function replaces every current client construction:

```python
def create_client(cfg, utility: str | None = None) -> LLMClient:
    """Build a client for a call site.

    ``utility`` names a call site (``"compaction"``, ``"rerank"``, ...).
    None means the chat client. An unmapped or empty entry inherits the
    active profile, which is the default for every utility.
    """
```

Field resolution, in order:

| Field | Source |
|-------|--------|
| `provider` | `profile.provider` |
| `base_url` | `profile.base_url` or `PROVIDER_PRESETS[provider]["base_url"]` |
| `api_key` | `profile.api_key` or `provider_keys[provider]` |
| `model` | `profile.model` or `PROVIDER_PRESETS[provider]["default_model"]` |
| `params` | `model_params.get(model, {})` overlaid with `profile.params` |

`model_params` keeps its current meaning — global per-model defaults, keyed by
model name — and `profile.params` layers on top. Existing per-model settings
therefore continue to apply, and a profile only states what it changes.

### Call sites

| Site | Today | After |
|------|-------|-------|
| `chat_widget.py:251` | `create_client_from_config()` | `create_client(cfg)` |
| `settings_dialog.py:60` | `create_client_from_config()` | `create_client(cfg)` |
| `chat_widget.py:529` | `create_client_from_config()` | `create_client(cfg, "compaction")` |
| `skill_evaluator.py:226` | `create_client_from_config()` | `create_client(cfg, "skill_eval")` |
| `optimize_tools.py:161` | `create_client_from_config()` | `create_client(cfg, "tool_optimize")` |
| `chat_widget.py:116-119` | bespoke 4-field override | `create_client(cfg, "rerank")` |

The bespoke reranker block is deleted, not kept alongside.

### This retires issue #30 structurally

`rerank_params` exists as a separate dict because the reranker once overwrote
the chat model's sampling parameters. Today a comment in `config.py` is what
stops that recurring — the invariant lives in prose, and a future contributor
reusing `model_params` would reintroduce it.

Under profiles, params are a field *of* a profile. Two profiles cannot share a
params namespace, because there is no shared namespace to reach. The rule moves
out of a comment and into the shape of the data.

## Migration

Triggered on load when `profiles` is absent.

1. Create a profile named after the current provider (e.g. `"anthropic"`) from
   `provider.{name, base_url, api_key, model}` and `model_params[provider.model]`.
   Set `active_profile` to that name.
2. Copy `provider.api_key` into `provider_keys[provider.name]`.
3. If `rerank_llm_model` is set, create a second profile named `"rerank"` from
   the `rerank_llm_*` fields plus `rerank_params`, and set
   `utility_profiles["rerank"] = "rerank"`. If it is unset, the reranker
   inherits, matching today's behavior.

   `rerank_llm_api_key` maps onto `Profile.api_key` and so keeps working. This
   is the field that would have had no home had keys been stored per vendor
   only, and it is the concrete case that settled that decision.
4. Leave `provider.*`, `rerank_llm_*` and `rerank_params` in the JSON, unread.

Step 4 is deliberate. A user who installs this version and then downgrades gets
their previous configuration back rather than an empty dialog. The dead keys are
removed one release later, once the new shape has shipped.

An untouched `config.json` must produce a single profile equivalent to its
previous flat configuration — no behavior change on upgrade, per the project
rule that new `AppConfig` defaults preserve prior-version behavior.

### Failure handling

A profile name in `active_profile` or `utility_profiles` that does not exist in
`profiles` resolves to the active profile, and to the first profile if the
active one is also missing. A config with no profiles at all is migrated as
above; if there is nothing to migrate from either, one profile is created from
`ProviderConfig` defaults. Configuration must never leave the dialog unusable.

## What this does and does not do for #75

Switching between profiles never writes to a field, so the accidental data loss
in #75 disappears: browsing to another connection and back leaves both intact.

Changing a profile's **provider** dropdown still resets that profile's base URL
and model to the new vendor's preset. That is intended — it is an explicit
"point this profile at a different vendor" action, not a side effect of
looking around — and it matches how the field behaves for the 21 presets whose
URLs are complete.

#75 is therefore closed as resolved by design rather than by a targeted patch.
The `blockSignals` guard around `setCurrentIndex` in `_load_settings` ships
anyway: today the load path is correct only because line 885 happens to run
after line 882, and that fragility is worth removing independently.

## UI

The Provider page gains a profile selector — a combo plus `[+] [rename]
[delete]` — and the existing provider, base URL, API key, model and parameter
widgets become the fields of whichever profile is selected. Deleting the last
profile is refused; deleting the active one moves `active_profile` to another.

Renaming a profile rewrites every reference to it in the same operation:
`active_profile` and any matching `utility_profiles` values follow the new name.
A rename must never silently detach a utility from the profile it was using. A
name that is already taken is refused rather than merged.

A Utilities section adds one dropdown per identifier (compaction, skill
evaluation, tool optimisation, reranking), each listing *inherit active* plus
every profile name. The existing reranker override widgets are removed, since
their configuration now lives in a profile.

The Settings dialog is already large, and this adds a button row plus a section.
The Utilities section goes at the bottom of the Provider page, below the profile
fields it refers to, so the reading order is "define connections, then say which
one each job uses."

## Testing

Migration carries the most risk and gets the most coverage.

- Real old-shape `config.json` fixtures asserted against expected profile output:
  no rerank override, a rerank override present, a `custom` provider with empty
  preset fields, and a config already in the new shape (must be left alone).
- Round trip proving legacy keys survive a load/save cycle unmodified.
- Resolution per utility: inherit when unmapped, override when mapped, fallback
  when a mapped profile name is missing.
- API key fallback: profile key wins; empty profile key falls through to
  `provider_keys`; both empty yields empty.
- Params layering: `model_params` applies, `profile.params` overrides it, and
  one profile's params never appear in another's client.
- A #75-shaped regression: edit a profile's base URL, select another profile,
  return, and assert the edit survives.
- Renaming a profile that `active_profile` and a `utility_profiles` entry both
  point at: both references follow the new name.
- One test per converted call site asserting it requests the right identifier.

## Sequencing

PR #73 (`fix/59-mcp-bearer-token-auth`) edits `config.py` and
`settings_dialog.py` and is one bug fix from mergeable. It lands first; this
work rebases onto it. Restructuring config underneath a contributor who has
already waited a week is not a trade worth making for ordering convenience.

PR #74 (Cloudflare) is unaffected either way — it adds a preset entry, and
presets are untouched by this design.
