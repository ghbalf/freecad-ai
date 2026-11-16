# External Agent Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make the built-in MCP server a good, vendor-neutral companion for external agents (Claude Code, Codex, Gemini CLI, …). Three parts: FreeCAD guidance in the server's `instructions`, a "Connect an external agent" box in Settings, and an autostart option.

**Architecture:**
- The agent-neutral tool rules move into an import-free module. The chat prompt and the MCP server both read it.
- Agent commands live only in `config.json`. They are seeded once from defaults in a small top-level module (`config.py` must not import `freecad_ai.mcp`). Reset backs up the old list before replacing it.
- Autostart reuses one extracted start function, shared with the toolbar command.

**Tech Stack:** Python 3.11 stdlib, PySide2/6 via `ui/compat.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-external-agent-companion-design.md`

## Global Constraints

- No new dependencies.
- Qt only through `freecad_ai/ui/compat.py`, flat enums.
- New defaults preserve behaviour: `mcp_server_autostart` defaults to `False`.
- Agent entries live only in config (`mcp_agent_recipes`). Code defaults are used only to seed (key absent) and to Reset.
- Every entry except Claude Code has `tested: false`, and the UI shows "from that tool's docs, not tested by the maintainer".
- Never handle or proxy agent credentials; the only secret shown is our own MCP bearer token.
- Run the tests with: `env PYTHONPATH= .venv/bin/pytest tests/unit --ignore=tests/unit/test_document_attach.py -q`
- Commits use conventional style with `(#139)`, and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **The chat prompt must not change by one byte.** Prompt caching (#47) depends on `ACT_MODE_TOOLS` staying identical. Pinned by `test_act_mode_tools_unchanged` (Task 1).
2. **A wildcard bind host (`0.0.0.0`, `::`) is not a URL anyone can dial.** The connect box must show `127.0.0.1` / `::1` instead. Pinned by `test_wildcard_host_shows_loopback` (Task 2).
3. **A hand-edited entry can be malformed:** not a dict, a missing field, an unknown placeholder, or `tested` as a string. It is skipped with a warning, and the dialog still opens. Pinned by `test_malformed_entries_are_skipped` (Task 2) and `test_page_survives_bad_recipes` (Task 5).
4. **A user who emptied the list (`[]`) must not be reseeded on the next start.** Absent and empty are different states. Pinned by `test_empty_list_is_not_reseeded` (Task 3).
5. **A broken `config.json` must not get seeds written over it.** Seeding follows the same `config_loaded_ok` guard as the param-store migration. Pinned by `test_broken_config_is_not_seeded` (Task 3).

---

### Task 1: Shared tool guidance, sent in `instructions` in both eras

**Files:**
- Create: `freecad_ai/core/tool_guidance.py` (no imports)
- Modify: `freecad_ai/core/system_prompt.py` (`ACT_MODE_TOOLS`)
- Modify: `freecad_ai/mcp/server.py` (`DISCOVER_INSTRUCTIONS`, legacy `initialize` branch)
- Test: `tests/unit/test_tool_guidance.py`; extend `tests/unit/test_mcp_dual_era.py`

**Interfaces:**
- Produces: `tool_guidance.TOOL_GUIDANCE: str`. It is exactly the text of `ACT_MODE_TOOLS` from `**Tool calling strategy:**` to the end.
- Produces: `server.SERVER_INSTRUCTIONS: str`, defined as the current two-sentence intro, then `"\n\n"`, then `TOOL_GUIDANCE`. `DISCOVER_INSTRUCTIONS` stays as an alias of it (other code imports that name).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tool_guidance.py
import hashlib
from freecad_ai.core import system_prompt, tool_guidance

def test_act_mode_tools_unchanged():
    # Recorded 2026-11-16 before the split. Prompt caching (#47) needs byte identity.
    h = hashlib.sha256(system_prompt.ACT_MODE_TOOLS.encode()).hexdigest()
    assert h == "b2c8f885d26391c25c98a6ebf9d81bd75f96101188320def0bb05556d10f3b95"

def test_act_mode_tools_ends_with_the_shared_guidance():
    assert system_prompt.ACT_MODE_TOOLS.endswith(tool_guidance.TOOL_GUIDANCE)
    assert tool_guidance.TOOL_GUIDANCE.startswith("**Tool calling strategy:**")

def test_guidance_has_no_chat_framing():
    assert "Act** mode" not in tool_guidance.TOOL_GUIDANCE
    assert "## Mode" not in tool_guidance.TOOL_GUIDANCE
```

In `test_mcp_dual_era.py`:
- `test_legacy_initialize_carries_instructions`, parametrized over `protocol.LEGACY_VERSIONS`. Send `_legacy("initialize", protocolVersion=v)` and assert `result["instructions"] == server_mod.SERVER_INSTRUCTIONS`.
- `test_initialize_without_version_carries_instructions`.
- Extend `test_it_carries_instructions` (discover) to assert `TOOL_GUIDANCE in result["instructions"]`.

- [ ] **Step 2: Run them and confirm they fail**

Run `env PYTHONPATH= .venv/bin/pytest tests/unit/test_tool_guidance.py tests/unit/test_mcp_dual_era.py -q`.
Expected: an ImportError for `tool_guidance`, and a KeyError on `instructions`.

- [ ] **Step 3: Check the schema, then implement**

First check that the `InitializeResult` of each revision in `PROTOCOL_REVISIONS` defines an optional `instructions` string. The schemas are at `https://modelcontextprotocol.io/specification/<version>/schema` for 2025-03-26, 2025-06-18 and 2025-11-25. If a revision lacks the field, send `instructions` only for revisions that have it, and narrow the parametrize list to match.

Then:
- Move the text into `TOOL_GUIDANCE`, and rebuild `ACT_MODE_TOOLS` as the unchanged header plus `TOOL_GUIDANCE`. The hash test proves it is identical.
- Add `"instructions": SERVER_INSTRUCTIONS` to the legacy `initialize` result dict, and use it in `_handle_discover` too.
- Add a code comment that this deliberately changes the legacy bytes (#139 versus the "unchanged" rule of #64).

- [ ] **Step 4: Run the full suite and confirm it passes**

Expected: all pass. Fix any test that pinned the exact legacy `initialize` dict by adding the key; don't loosen the test.

- [ ] **Step 5: Commit**

Message: `feat(mcp): send FreeCAD tool guidance as server instructions (#139)`

---

### Task 2: Agent recipes, rendering and the generic snippets (pure module)

**Files:**
- Create: `freecad_ai/agent_recipes.py`. It is top-level, imports only stdlib, and must not import `freecad_ai.mcp`.
- Test: `tests/unit/test_agent_recipes.py`

**Interfaces:**
- Produces: `DEFAULT_AGENT_RECIPES: tuple[dict, ...]`. Each entry has the keys `name: str`, `template: str`, `header_arg: str`, `docs_url: str` and `tested: bool`.
  - `template` may contain only `{url}` and `{header_args}`.
  - `header_arg` may contain only `{header}`. That placeholder is filled with `shlex.quote("Authorization: Bearer <token>")`. (This deviates from the spec's `{token}` wording: quoting the whole header is the only form that's safe for any token.)
  - Seed the defaults with Claude Code (`tested: True`): template `claude mcp add --transport http freecad {url}{header_args}`, header_arg ` -H {header}`, and docs_url `https://code.claude.com/docs/en/mcp`.
  - Add entries for Codex, Gemini CLI and Qwen Code only if their **current** MCP docs page gives an add-server command for a remote HTTP server with a header. Copy the syntax verbatim, set `tested: False`, and use that page as `docs_url`. If an agent's page doesn't give such a command, leave the agent out. Never guess the syntax.
- Produces: `dialable_url(host: str, port: int) -> str`. It returns `http://HOST:PORT/mcp`, with `0.0.0.0` mapped to `127.0.0.1`, `::` to `::1`, and IPv6 hosts in brackets.
- Produces: `valid_recipes(entries) -> tuple[list[dict], list[str]]`. It returns the usable entries plus one warning string per skipped entry. It never raises, whatever it is given.
- Produces: `render_command(recipe: dict, url: str, token: str | None) -> str`. With no token, `{header_args}` becomes `""`.
- Produces: `generic_snippets(url: str, token: str | None) -> dict`. It returns the keys `url`, `header` (`""` when there is no token) and `json`. `json` is `json.dumps(..., indent=2)` of `{"mcpServers": {"freecad": {"type": "http", "url": url, "headers": {"Authorization": "Bearer <token>"}}}}`, with the `headers` key omitted when there is no token.

- [ ] **Step 1: Write the failing tests**

- `test_claude_command_without_token`: renders exactly `claude mcp add --transport http freecad http://127.0.0.1:3000/mcp`.
- `test_claude_command_with_token`: ends with `-H 'Authorization: Bearer tok'`.
- `test_token_needing_quotes`: a token `a'b $x` gives a command where `shlex.split(cmd)[-1] == "Authorization: Bearer a'b $x"`.
- `test_wildcard_host_shows_loopback`: `dialable_url("0.0.0.0", 3000) == "http://127.0.0.1:3000/mcp"`, and `dialable_url("::", 3000) == "http://[::1]:3000/mcp"`.
- `test_malformed_entries_are_skipped`: input `[42, {"name": "x"}, {...valid, "template": "{nope}"}, {...valid, "tested": "yes"}, valid]`. Exactly one entry is kept, and there are four warnings.
- `test_defaults_are_valid`: `valid_recipes(DEFAULT_AGENT_RECIPES)` returns no warnings; every entry has an `https://` docs_url, and only `name == "Claude Code"` has `tested is True`.
- `test_generic_json_omits_headers_without_token`, and `test_generic_json_with_token`: the JSON parses, and the header value is `Bearer tok`.

- [ ] **Step 2: Run them and confirm they fail** (ImportError)

- [ ] **Step 3: Implement the module.** Check placeholders with `string.Formatter().parse`.

- [ ] **Step 4: Run them and confirm they pass**

- [ ] **Step 5: Commit**

Message: `feat: agent recipe rendering for the external-agent box (#139)`

---

### Task 3: Config field, seeding once, and Reset with a backup

**Files:**
- Modify: `freecad_ai/config.py`. Add the fields next to `mcp_server_auth_token`, and add the seeding to `load_config` inside the `if config_loaded_ok:` branch.
- Modify: `freecad_ai/agent_recipes.py`, adding `reset_recipes`.
- Test: `tests/unit/test_agent_recipes_config.py`. Use the conftest config sandbox; never the real config dir.

**Interfaces:**
- Produces: the config field `mcp_agent_recipes: list | None = None`. `None` means the key is absent, so seeding applies. `from_dict` must keep `[]` as `[]`.
- Produces: the config field `mcp_server_autostart: bool = False`. Task 4 uses it.
- Produces: `seed_recipes(cfg) -> bool` in `agent_recipes.py`. If `cfg.mcp_agent_recipes is None`, it sets a deep copy of the defaults and returns `True`. `load_config` calls it and, when it returns `True`, calls `save_config(cfg)`.
- Produces: `reset_recipes(current: list, config_dir: str, now: datetime) -> tuple[list, str | None]`, returning `(new_list, backup_path)`.
  - It never mutates `current`, and never calls `save_config`; the caller decides that.
  - If `current` equals the defaults, it returns `(defaults_copy, None)` and writes no file.
  - Otherwise, it first writes `current` as JSON to `<config_dir>/mcp_agent_recipes.<YYYYMMDD-HHMMSS>.json`. If that name exists, it appends `-1`, `-2`, and so on. Only then does it return `(defaults_copy, path)`.
  - If the write raises `OSError`, it re-raises, and no new list is returned.

- [ ] **Step 1: Write the failing tests**

- `test_absent_key_is_seeded_and_saved`: a `config.json` without the key leads to `load_config().mcp_agent_recipes == list(DEFAULT_AGENT_RECIPES)`, and the file on disk now has the key.
- `test_empty_list_is_not_reseeded`: a file with `"mcp_agent_recipes": []` loads as `[]`.
- `test_edited_list_is_kept`: a custom entry survives the load unchanged.
- `test_broken_config_is_not_seeded`: a file with invalid JSON stays byte-identical after `load_config()`.
- `test_reset_writes_backup_first`: an edited list, `now=datetime(2026, 11, 16, 12, 0, 0)` → the returned path ends with `mcp_agent_recipes.20261116-120000.json`, the file holds the old list, the returned list equals the defaults, and the input list is unchanged.
- `test_reset_backup_failure_changes_nothing`: with `config_dir` pointing at a nonexistent path, it raises `OSError` and the input list is unchanged.
- `test_reset_when_already_default_writes_nothing`: returns `None`, and the directory listing is unchanged.
- `test_reset_same_second_does_not_overwrite`: two resets with the same `now` produce two distinct files.

- [ ] **Step 2: Run them and confirm they fail**

- [ ] **Step 3: Implement.** Keep the `agent_recipes` import inside `load_config`, so `config.py` stays import-light.

- [ ] **Step 4: Run the full suite and confirm it passes.** `test_tests_stay_out_of_real_config.py` must still pass.

- [ ] **Step 5: Commit**

Message: `feat(config): seed agent recipes once; reset keeps a backup (#139)`

---

### Task 4: Autostart, with the shared start path

**Files:**
- Modify: `freecad_ai/mcp/gui_server.py`. Add `start_from_config`.
- Modify: `InitGui.py`. `ToggleMCPServerCommand.Activated` uses `start_from_config`, and an autostart hook is added at module level after `Gui.addWorkbench(...)`.
- Test: extend `tests/unit/test_mcp_gui_server.py` and `tests/unit/test_initgui_commands.py`.

**Interfaces:**
- Consumes: `cfg.mcp_server_autostart` (Task 3).
- Produces: `start_from_config(cfg, controller=None) -> tuple[str, bool]` in `gui_server.py`. It returns `(url, token_required)` and raises `OSError` or `ValueError` as `Activated` does today. The body is the resolve-then-start code moved out of `ToggleMCPServerCommand.Activated` unchanged.
- Produces: `autostart_mcp_server(cfg, controller=None, log=None) -> str | None` in `gui_server.py`.
  - It returns `None` and does nothing when the flag is off or the server is already running.
  - On `OSError` or `ValueError`, it logs one warning line naming the host, the port and the error, then returns `None`. It never raises, and never shows a dialog.
  - On success, it logs the URL and returns it.
- In `InitGui.py`, the hook schedules `autostart_mcp_server(get_config())` with `QtCore.QTimer.singleShot(0, …)` (via compat), wrapped in `try/except Exception`.

- [ ] **Step 1: Write the failing tests**

All of these use a fake controller (backend_factory stub, as the existing gui_server tests do):
- `test_autostart_off_does_nothing`: no start call.
- `test_autostart_on_starts`: returns the URL.
- `test_autostart_bind_failure_logs_not_raises`: the controller raises `OSError("in use")`. The result is `None`, and the log holds a line containing `"in use"`.
- `test_autostart_skips_running_server`.

In `test_initgui_commands.py`:
- `test_toggle_still_reports_failure`: the existing failure path still unticks and reports after the refactor. Reuse that file's fixture.

- [ ] **Step 2: Run them and confirm they fail**

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the full suite and confirm it passes**

- [ ] **Step 5: Commit**

Message: `feat(mcp): optional start of the MCP server with FreeCAD (#139)`

---

### Task 5: Settings UI — the connect box, Reset, and the autostart checkbox

**Files:**
- Modify: `freecad_ai/ui/settings_pages/mcp_page.py`
- Test: `tests/unit/test_mcp_page.py`. Update `test_two_group_boxes` to the three titles.

**Interfaces:**
- Consumes: `dialable_url`, `valid_recipes`, `render_command`, `generic_snippets` and `reset_recipes` (Tasks 2–3), plus `config.CONFIG_DIR`.

**The "Built-in MCP Server" group** gains a checkbox, `self.mcp_server_autostart_check`, labelled "Start the MCP server when FreeCAD starts". It round-trips through `_show` / `_values` as `mcp_server_autostart`.

**The new group "Connect an external agent"** goes below "Built-in MCP Server" and contains, in order:
- A read-only `QLineEdit` for the URL, `self.agent_url_edit`, with a Copy button.
- A read-only header line, `self.agent_header_edit`. It shows "none — no bearer token set" when no token is set, and has a Copy button.
- A read-only `QPlainTextEdit` for the JSON, `self.agent_json_edit`, with a Copy button.
- A `QComboBox` of recipe names, `self.agent_combo`.
- A read-only command field, `self.agent_command_edit`, with a Copy button.
- A note label, `self.agent_note_label`:
  - when `tested` is false: "From that tool's docs, not tested by the maintainer."
  - always: a docs link.
- A "Reset to defaults" button.

All fields refresh on `textChanged` of the host, port and token fields, using the page's `_parse_server_address` and `dialable_url`. The values reflect the next server start; the existing warning label already says so.

Copy uses `QApplication.clipboard().setText`.

**Reset:**
1. Ask for confirmation with a `QMessageBox` question.
2. Call `reset_recipes(self._agent_recipes, CONFIG_DIR, datetime.now())`. `self._agent_recipes` is the raw list kept by `_show`.
3. On `OSError`, show a warning box and change nothing.
4. Otherwise, set `self._agent_recipes` to the new list and refresh the combo. If there is a backup path, show it in an info box and log it.

The new list is part of `_values()["mcp_agent_recipes"]`. It is saved with the rest of the dialog on OK, and the confirm text says so.

**`_show`** keeps the raw `cfg.mcp_agent_recipes` list for `_values`, so unedited malformed entries are written back unchanged and never silently deleted. It displays only `valid_recipes(...)[0]`, and logs the warnings through `logging.getLogger(__name__).warning`.

- [ ] **Step 1: Write the failing tests**

All use the `page` fixture and `_cfg()`, with the port at 30000 and the token `tok`.
- `test_three_group_boxes`: titles `["MCP Servers", "Built-in MCP Server", "Connect an external agent"]`.
- `test_autostart_round_trips`.
- `test_url_follows_port_field`: `setText("31000")` on the port field makes `agent_url_edit.text()` equal `http://127.0.0.1:31000/mcp`.
- `test_command_shows_token`: the Claude command ends with `-H 'Authorization: Bearer tok'`; after clearing the token, there is no `-H`.
- `test_untested_note`: a cfg holding one `tested: False` entry shows "not tested by the maintainer" in the note.
- `test_page_survives_bad_recipes`: `cfg.mcp_agent_recipes = [42, {"name": "x"}]` → `_show` doesn't raise, the combo is empty, and `_values()["mcp_agent_recipes"]` equals the raw list.
- `test_reset_backup_failure_keeps_list`: monkeypatch `reset_recipes` to raise `OSError`, and `QMessageBox` to auto-accept. The list is unchanged.

- [ ] **Step 2: Run them and confirm they fail**

- [ ] **Step 3: Implement.** The page is shared by the dialog and the prefs page (#101), so there's no second copy.

- [ ] **Step 4: Run the full suite and confirm it passes**, including `test_settings_dialog_*` and `test_prefs_page.py`.

- [ ] **Step 5: Manual check (maintainer):**
  1. Open the Settings dialog in FreeCAD.
  2. Copy the Claude command, then run it with the server running.
  3. `claude mcp list` should show `freecad … Connected`.
  4. In a new `claude` session, the server's instructions are present.

  Retake the MCP-page screenshot per `reference_settings_dialog_screenshots`, if the wiki shows that page.

- [ ] **Step 6: Commit**

Message: `feat(settings): Connect an external agent box (#139)`

---

### Task 6: CHANGELOG and wiki

**Files:**
- Modify: `CHANGELOG.md`, under `## [Unreleased]`
  - **Added:** the external-agent box, autostart, and server instructions.
  - **Changed:** legacy `initialize` now carries `instructions`.
  - Plus a note that the agent commands live in `config.json` and that Reset keeps a backup file.
- Wiki repo `../freecad-ai-wiki`:
  - `MCP-Integration.md`: new section "Using FreeCAD AI from an external agent". It covers:
    - why: your own subscription, a free tier or a local model
    - the agent table, untested entries flagged
    - the Settings box
    - autostart
    - editing `mcp_agent_recipes`, Reset and the backup file
    - a link to https://code.claude.com/docs/en/legal-and-compliance, as one example of checking your agent vendor's terms
  - `Home.md`: a link to that section.

- [ ] **Step 1: Write the entries.** Grep the CHANGELOG to confirm Unreleased holds all three features.
- [ ] **Step 2: Commit both repos.**
  - Main repo message: `docs: external agent companion (#139)`
  - Wiki: commit with a matching message. Push only when the maintainer asks.
