# Agent Skills compatibility

**Goal:** skill folders written for other agent harnesses (the open Agent
Skills format used by Claude Code, Codex, Gemini CLI, Copilot, Cursor) load
and work in FreeCAD AI unchanged, and our built-in skills are valid Agent
Skills — without changing how any existing FreeCAD AI skill behaves.
**Predecessor:** `13320a0` (a failing `/skill` command now shows its error).

## Decisions (maintainer, 2026-10-01)

1. **Both directions.** Import foreign skills *and* make ours conformant.
   `handler.py` and `VALIDATION.md` stay, as optional extensions the
   standard permits (extra files other harnesses ignore).
2. **`scripts/`: `.py` runs inside FreeCAD** through a new
   `run_skill_script` tool, with `run_macro`'s trust level and
   `execute_code`'s safety layers. Other languages are readable only.
   No subprocess execution.
3. **Discovery is opt-in:** `extra_skill_dirs`, default empty. No
   auto-scanning of `~/.claude/skills` etc.
4. **Approach A:** files are reached through the existing load-time key
   allowlist, extended so the keys *are* the standard's relative paths.
   No path-based file tool.
5. **Injection becomes body-only** (frontmatter stripped), as in other
   harnesses. This is the one visible change for existing skills.

## What exists today

`freecad_ai/extensions/skills.py`:

- A skill is a folder with `SKILL.md`, scanned from the repo's `skills/`
  then `SKILLS_DIR` (user wins).
- Description: frontmatter `description:` (single line, cut to 100 chars)
  or the first content line. `get_skill_status()` re-derives it separately
  (cut to 80, ignores frontmatter).
- `references/` top level only → `{lowercased stem: abspath}`; the model
  names a key, never a path. The manifest is appended on invocation unless a
  `handler.py` returned a result.
- `handler.py` is imported via `spec_from_file_location`; its directory is
  not on `sys.path`.
- The whole `SKILL.md`, frontmatter included, is injected.

Gaps against the standard: description truncation, no nested or root-level
files, `scripts/` and `assets/` ignored, 5 of 9 built-ins lack frontmatter.

## Design

### 1. Frontmatter

A stdlib parser (no PyYAML — no external deps) for the subset the standard
uses: `key: value` with plain/quoted scalars, `>` and `|` block scalars,
one nested level for `metadata:`, `allowed-tools` as string or list.
Unparseable frontmatter is ignored; a skill never fails to load because of
it.

| Field | Use |
|---|---|
| `name` | Stored. The **folder name stays the skill's identity and `/command`**; a differing `name` changes nothing. |
| `description` | Stored up to 1024 chars. The system-prompt list shows up to 300, then `…`. Fallback: first content line, as today. |
| `license`, `compatibility`, `metadata` | Stored. `compatibility` shown in the Skills settings list tooltip. |
| `allowed-tools` | Stored, not enforced (names are other harnesses' tools). |

One helper parses the file; `_scan_skills_dir` and `get_skill_status` both
use it (removes today's 100/80 inconsistency).

`Skill.content` stays the full file (the optimizer and status code read
it); a new `Skill.body` is what `execute_skill` injects.

### 2. Files inside a skill

**Scan:** the skill folder is walked recursively at load time. Skipped:
`SKILL.md`, `handler.py`, names starting with `.`, `__pycache__`,
`node_modules`, `.venv`, `.git`. At most 500 files per skill (further files
are ignored, logged once). A file is allowlisted only if its realpath is
inside the skill folder's realpath — symlinks that escape are dropped at
scan time.

**Keys:** POSIX relative path (`references/tables/m3.md`, `forms.md`,
`scripts/fill.py`). Lookup: strip, `\` → `/`, drop leading `./`; exact
match, then case-insensitive match.

**Legacy aliases:** each file directly in `references/` also gets today's
key (lowercased stem; extension optional on lookup), with today's collision
rule (last in sorted order wins). Every existing `resource='m3'` call keeps
working.

**Reading** (`use_skill(name, resource=…)`): UTF-8 text returned, capped at
100 KB with a truncation note. Non-text (NUL byte in the first 8 KB, or
decode failure) returns an error naming the size and saying it is meant for
skill scripts, not for reading.

**Manifest** (appended on invocation, replaces "Available references"):
three groups — *Documents* (key + summary line, as today), *Scripts* (key +
the `run_skill_script` call), *Assets* (keys only). At most 40 entries, then
`…and N more`. Handler results still own their output;
`render_references_manifest` stays public for handlers that want it.

### 3. `run_skill_script(skill, script, args="")`

- `script`: an allowlist key ending in `.py`. Any other file → error
  pointing at `use_skill(resource=…)`.
- `args`: `shlex.split` (POSIX) → `sys.argv[1:]`.
- Same tool category as `run_macro`.

**Execution:** `execute_code` receives a generated wrapper, not the script
text, so `from __future__` imports, real tracebacks (file + line) and
`__file__` work:

```python
import sys, runpy
_argv, _path = sys.argv[:], sys.path[:]
sys.argv = [SCRIPT, *ARGS]; sys.path.insert(0, SCRIPT_DIR)
try:
    runpy.run_path(SCRIPT, run_name="__main__",
                   init_globals={k: v for k, v in globals().items()
                                 if not k.startswith("__")})
except SystemExit as e:
    if e.code not in (None, 0):
        raise RuntimeError(f"script exited with status {e.code}")
finally:
    sys.argv, sys.path[:] = _argv, _path
```

`SCRIPT`, `ARGS`, `SCRIPT_DIR` are inserted with `repr()`. Built by a pure
function `build_script_wrapper(path, argv) -> str`.

**Safety:** the static validator would only see the wrapper, so
`run_skill_script` runs `_validate_code` on the **script's text** first
(skipped in Dangerous mode) and passes `skip_safety` through as `run_macro`
does. The sandbox dry-run, undo transaction, auto-save and timeout apply
unchanged (the sandbox subprocess runs the same wrapper on the same file).

**Trust:** same as `handler.py` / `run_macro` — the user installed the
folder. Documented: only add `extra_skill_dirs` you trust.

### 4. Settings

- `AppConfig.extra_skill_dirs: list[str] = []` — stored as typed, `~`
  expanded at read time. Missing directories are skipped silently.
- Scan order: built-in → extra dirs (list order) → user dir; later wins.
- UI: Tools page, Skills group (shared by the Settings dialog and
  Preferences since #101): a one-path-per-line field plus *Browse…*.
- `get_skill_status()` gains source `"external"`; *Reset to Built-in*
  stays disabled for it.

### 5. Built-in skills

- Add `name` + `description` frontmatter to enclosure, fastener-hole, gear,
  lattice, thread-insert (description = current first line); add `name` to
  sketch-from-image.
- skill-creator's `SKILL.md` teaches the standard layout (frontmatter,
  `references/`/`scripts/`/`assets/`, relative links, `run_skill_script`).

## Error handling

| Case | Result |
|---|---|
| Broken frontmatter | Skill loads with fallback description |
| Unknown resource / script key | Error listing up to 20 available keys |
| Non-`.py` script | Error: read it with `use_skill(resource=…)` |
| Binary resource | Error with size; "for skill scripts" |
| Script fails validation | `Pre-execution validation failed: …` (unchanged format) |
| `sys.exit(n≠0)` | Tool error `script exited with status n` |
| Extra dir missing / unreadable | Skipped |

## Testing

1. Parser: scalars, block scalars, `metadata`, list `allowed-tools`, broken
   frontmatter, 1024 cap.
2. Scan: recursive keys, skip list, 500 cap, escaping symlink dropped,
   legacy aliases + collision rule.
3. Conformance (both directions): a standard-layout fixture skill (root
   docs, nested `references/`, `scripts/`, `assets/`) and **every built-in
   skill** — every relative link in the `SKILL.md` body resolves via
   `get_skill_resource`; name = folder, `[a-z0-9-]{1,64}`; description 1–1024.
4. Wrapper, exec'd in plain Python: `__name__`, `__file__`, argv,
   `__future__` import, `sys.exit(0)` ok, `sys.exit(2)` error, argv/path
   restored after an exception, sibling import.
5. Safety regression: a script containing `subprocess` is rejected;
   Dangerous mode skips validation.
6. `extra_skill_dirs`: precedence, `~` expansion, missing dir; settings
   widget round-trip.
7. Body-only injection; existing `test_skills.py` assertions that pin the
   injected text are updated and listed in the PR.
8. Integration (`-m integration`): a skill script creates a box through
   `run_skill_script`.

## Docs

Wiki *Creating-Skills* and *Skills-Reference*: layout, links, scripts,
`extra_skill_dirs` + trust warning, using our skills in Claude Code
(useful with the MCP server). CHANGELOG `[Unreleased]`, including the
body-only injection change.

## Out of scope

- Enforcing `allowed-tools`.
- Running non-Python scripts.
- Auto-discovering other harnesses' skill directories.
- Installing skills from URLs / marketplaces.
