# Design: module assets — workbenches shipping their own assistant assets

- **Date:** 2026-08-14
- **Status:** Implemented
- **Scope:** discovery of `Mod/<Module>/ai/` directories, wired into skills,
  extension tools, hooks and the system prompt. No change to any asset format —
  a module's skill is a normal skill, a module's tool is a normal user tool.
- **Base:** branch off `master`.

## Background

Assistant assets can live in exactly two places today:

1. **Built-in** — `skills/` and `hooks/` in this repository, and the tools
   hard-coded in `freecad_ai/tools/freecad_tools.py`.
2. **User** — `<config dir>/{skills,tools,hooks}/`, scanned by
   `SkillsRegistry._load_skills`, `load_user_tools(USER_TOOLS_DIR, ...)` and
   `HookRegistry._load_hooks`.

Neither fits a third case that keeps coming up: **a workbench that knows things
the generic assistant cannot infer.** A module defining its own document types
knows their property semantics, the object hierarchy they require, and the order
its operations must run in. None of that is discoverable from `addObject()` and
a property list.

Today that knowledge has one route into the assistant — someone hand-copies a
`SKILL.md` into the user config directory. That is unversioned, untested,
invisible to the module's own CI, silently divergent from the code it describes,
and gone on the next machine. Alternatively the module's assets get merged into
*this* repository, which does not scale and makes every downstream fork carry
vendor-specific content it has no use for.

## Design

Any installed FreeCAD module may carry an `ai/` directory:

```
Mod/<AnyModule>/ai/
    skills/<name>/SKILL.md    same layout and parser as built-in skills
    tools/*.py                same contract as user extension tools
    hooks/<name>/hook.py      same contract as user hooks
    INSTRUCTIONS.md           appended to the system prompt
```

`freecad_ai/extensions/module_assets.py` walks `Mod/` under both
`App.getUserAppDataDir()` and `App.getResourceDir()` — the same probe order as
`paths.get_wb_dir()` — and returns each `ai/` directory it finds. An
os.pathsep-separated `FREECAD_AI_ASSET_DIRS` adds directories directly, so a
development checkout can be pointed at without installing; it mirrors the
existing `FREECAD_AI_CONFIG_DIR` escape hatch and is what the tests use.

### Naming

"Provider" was the obvious word and is unavailable: `freecad_ai/llm/providers.py`,
`cfg.provider`, `rerank_llm_provider_name` and CONTRIBUTING's "New Providers"
section all mean *LLM provider*. These are **module assets**, and the discovery
module is named for that.

### Precedence

**Built-in < module < user.** A module may extend the assistant; the user always
keeps the last word over both. This falls out of the existing scan order —
skills overwrite by name as directories are scanned, so module skills are
inserted between built-in and user.

Hooks invert this (`_load_hooks` keeps the *first* name it sees), so the module
list is inserted at the same position and the same built-in-wins-over-user
precedence is preserved.

### Why nothing vendor-specific enters this repository

The mechanism names no module. It is a directory convention plus four call
sites. A workbench that wants to teach the assistant does so entirely from its
own repository, where its skills are versioned and tested next to the code they
document. This repository gains a general capability, not a customer.

## Changes

| File | Change |
|---|---|
| `freecad_ai/extensions/module_assets.py` | New. `discover_asset_dirs`, `asset_subdirs`, `owning_module`, `load_module_instructions`, `reset_cache`. |
| `freecad_ai/extensions/skills.py` | `_skill_dirs()` inserts module skill dirs between built-in and user. `get_skill_status()` gains a `"module"` source and a `module_path` key. `reset_to_builtin()` also reverts to a module copy. |
| `freecad_ai/tools/setup.py` | Module tool dirs appended to the `extra_dirs` already passed to `load_user_tools`. |
| `freecad_ai/hooks/registry.py` | `_hook_dirs()` inserts module hook dirs. |
| `freecad_ai/core/system_prompt.py` | `## Workbench Instructions` section from each `INSTRUCTIONS.md`. |
| `freecad_ai/ui/settings_dialog.py` | Skills list shows a `module` tag; reset enables against a module copy too. |

Every call site wraps discovery in `try/except` — an assistant that cannot scan
for module assets still starts.

## Supporting changes to the user-tool loader

A module's tools are ordinary user tools, and writing real ones surfaced three
limits in `extensions/user_tools.py` that apply equally to user-authored tools:

1. **No sequence types.** `_TYPE_MAP` accepted only `float|int|str|bool`, so a
   tool taking a list of names could not be expressed. Added `list[str]`,
   `list[float]` and `list[int]`, each emitting `items` alongside `array` —
   required by strict providers and asserted by
   `tests/unit/test_registry.py::test_every_array_property_has_items`.
   Unsupported generics still warn, and now name themselves accurately
   (`tuple[int]` rather than an empty string).
2. **Placeholder parameter descriptions.** Every parameter was described as
   `"Parameter: {name}"`. The docstring's Google-style `Args:` block is now
   parsed and each parameter carries the author's own prose. Parameters the
   author did not document keep the placeholder.
3. **Fixed `user_` prefix.** A module may set `__tool_prefix__` to namespace its
   tools. It is read from the AST, so it is honoured without executing the file,
   and a non-string value falls back to the default.

The tool description is now the full docstring up to `Args:` rather than only
its first line — a one-line summary is rarely enough for the model to decide
whether a tool applies. `Returns:`/`Raises:` are dropped; the model sees the
return value directly in the tool result.

## Caching

`create_default_registry()` runs on every message, so the directory scan is
cached process-wide. `discover_asset_dirs(refresh=True)` and `reset_cache()`
exist for tests and for installing a module into a running session.

## Testing

- `tests/unit/test_module_assets.py` — 20 tests covering discovery, the env
  override, caching, instruction collection, and the integration of all three
  asset types including precedence and reset behaviour.
- `tests/unit/test_user_tools.py` — 20 added tests for sequence params, the
  tool prefix, and docstring parsing.

FreeCAD is never imported: the tests monkeypatch `_mod_roots`, matching how the
rest of the unit suite stays FreeCAD-free.
