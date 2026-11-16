# MCP token from a keyring or file (#141) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The MCP server bearer token accepts `file:` and `cmd:` like provider API keys.

**Architecture:** One stdlib resolver in `freecad_ai/utils/secret_source.py` raises on failure; `LLMClient` wraps it to keep its log-and-return-"" behaviour, `resolve_auth_token` lets the error (a `ValueError`) reach the two start paths, which already report `ValueError`. The settings page shows a placeholder for a `cmd:`/`file:` token and resolves it only on Copy.

**Tech Stack:** Python 3.11 stdlib, PySide via `freecad_ai/ui/compat.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-mcp-token-secret-sources-design.md`

## Global Constraints

- Stdlib only; no `keyring` package.
- `cmd:` keeps `shell=True`, `timeout=10`, `**hidden_process_kwargs()` and calls `subprocess.run` through the `subprocess` module (`test_no_console_window` patches `subprocess.run`).
- A configured `cmd:`/`file:` that fails, times out or yields empty never starts the server without a token.
- Resolve on the calling thread (spec option 1); no new threads.
- Error messages name the source (`cmd:` command or `file:` path), never the secret.
- Qt imports only through `freecad_ai/ui/compat.py`; flat enums.
- Tests that run a real `cmd:` (`printf`, `exit 3`, `true`) are POSIX shell: mark them `skipif(os.name != "posix")`.
- Test command: `env PYTHONPATH= .venv/bin/pytest tests/unit --ignore=tests/unit/test_document_attach.py -q`.

## Review Focus

1. `MCP_AUTH_TOKEN=cmd:…` in the environment → resolved like the config value (Task 2 test).
2. A resolved token that is non-ASCII → the existing ASCII `ValueError`, checked after resolving (Task 2 test).
3. A `cmd:` that writes the secret and exits non-zero → error names the command, stdout never appears in the message (Task 1 test).
4. Typing `cmd:secret-tool …` into the field → the command is never run until Copy (Task 3 test).
5. Generate on a `cmd:` field, user answers No → field unchanged (Task 3 test).

---

### Task 1: Shared resolver

**Files:**
- Create: `freecad_ai/utils/secret_source.py`
- Modify: `freecad_ai/llm/client.py` (`_resolve_api_key`)
- Test: `tests/unit/test_secret_source.py`; existing `tests/unit/test_api_key_resolution.py`, `tests/unit/test_no_console_window.py` stay green unchanged

**Interfaces:**
- Produces: `class SecretSourceError(ValueError)`; `secret_source_kind(value: str) -> str | None` (`"cmd"`, `"file"` or `None`, after `.strip()`); `resolve_secret(value: str) -> str` — literal returned as-is (stripped), `file:`/`cmd:` resolved and stripped, raises `SecretSourceError` on read error, non-zero exit, timeout, OSError or empty result.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_secret_source.py`:
  `test_literal_is_returned`, `test_file_is_read_and_stripped` (tmp file `"tok\n"` → `"tok"`), `test_file_expands_user_and_env`, `test_missing_file_raises` (message contains the path), `test_cmd_stdout_is_stripped` (`cmd:printf 'tok\n'` → `"tok"`), `test_cmd_nonzero_raises_without_leaking_stdout` (`cmd:echo s3cret; exit 3` → raises, `"s3cret" not in str(e)`, command in message), `test_cmd_timeout_raises` (monkeypatch `subprocess.run` to raise `TimeoutExpired`), `test_empty_output_raises` (`cmd:true`, and an empty file), `test_kind` (`"cmd:x"`→`"cmd"`, `" file:/x"`→`"file"`, `"abc"`→`None`, `""`→`None`), `test_error_is_a_value_error`.
- [ ] **Step 2: Run** `env PYTHONPATH= .venv/bin/pytest tests/unit/test_secret_source.py -q` — Expected: collection error, module missing.
- [ ] **Step 3: Implement** the module from the body of `LLMClient._resolve_api_key`; then make `_resolve_api_key` return `""` for an empty key, else `resolve_secret(self.api_key)`, catching `SecretSourceError` → `logger.error(...)` and `""`.
- [ ] **Step 4: Run** the new file plus `tests/unit/test_api_key_resolution.py tests/unit/test_no_console_window.py` — Expected: all pass.
- [ ] **Step 5: Commit** `refactor(secrets): share the file:/cmd: resolver with the MCP token (#141)`.

### Task 2: Server token resolves its source

**Files:**
- Modify: `freecad_ai/mcp/gui_server.py` (`resolve_auth_token`, its docstring)
- Test: `tests/unit/test_mcp_gui_server.py`
- Docs: `CHANGELOG.md` Unreleased `### Added`

**Interfaces:**
- Consumes: `resolve_secret`, `SecretSourceError` (Task 1).
- Produces: `resolve_auth_token(cfg=None) -> str | None` — unchanged signature; blank → `None`; a `cmd:`/`file:` value is resolved; failure raises `SecretSourceError` (a `ValueError`).

- [ ] **Step 1: Write the failing tests:** `test_config_file_token_is_resolved` (tmp file → its content), `test_env_cmd_token_is_resolved` (`MCP_AUTH_TOKEN="cmd:printf envtok"` → `"envtok"`), `test_failing_source_raises_value_error` (`file:/nonexistent` → `pytest.raises(ValueError)`), `test_resolved_non_ascii_token_is_rejected` (file containing `"tökén"` → `ValueError` with "ASCII"), `test_autostart_reports_a_failing_source` (`autostart_mcp_server` with a fake controller and `file:/nonexistent`: returns `None`, controller never started, `warn` got one line containing the path).
- [ ] **Step 2: Run** `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_gui_server.py -q` — Expected: the first, second and fourth fail (token returned raw); the autostart one may fail (server started with a literal `file:` token).
- [ ] **Step 3: Implement:** resolve `token` with `resolve_secret` when non-blank, before the ASCII check; an empty resolution cannot happen (it raises).
- [ ] **Step 4: Run** the file — Expected: pass. Add the CHANGELOG entry (token accepts `file:`/`cmd:`, failure stops the server, locked keyring can hold FreeCAD for up to 10 s at autostart, the agent side still gets a literal copy — #146).
- [ ] **Step 5: Commit** `feat(mcp): read the server token from a file or command (#141)`.

### Task 3: Settings page

**Files:**
- Modify: `freecad_ai/ui/settings_pages/mcp_page.py` (`_build_agent_group`, `_copy_row` callers, `_refresh_agent_box`, `_generate_mcp_auth_token`, token field placeholder/tooltip)
- Test: `tests/unit/test_mcp_page.py`

**Interfaces:**
- Consumes: `secret_source_kind`, `resolve_secret`, `SecretSourceError`.
- Produces: `_agent_texts(self, token: str | None) -> dict` with keys `url`, `header`, `json`, `command` (command `""` when no recipe); display uses the placeholder `<token from cmd:>` / `<token from file:>`; each Copy button calls `_copy_agent_text(key)` which resolves the field (only when it is `cmd:`/`file:`), re-renders with the real token and sets the clipboard, or shows `QMessageBox.warning` naming the error and copies nothing.

- [ ] **Step 1: Write the failing tests** (patch `freecad_ai.utils.secret_source.subprocess.run` with a counter where a command must not run):
  `test_cmd_token_shows_placeholder` (field `cmd:printf tok` → header edit text `"Authorization: Bearer <token from cmd:>"`, `"tok"` not in any agent widget), `test_typing_a_cmd_token_runs_nothing` (counter stays 0 after `setText`), `test_copy_resolves_the_token` (copy `"command"` → clipboard contains `tok`, not the placeholder), `test_copy_failure_warns_and_copies_nothing` (`file:/nonexistent`; patched `QMessageBox.warning` called once; clipboard unchanged), `test_literal_token_copy_unchanged`, `test_generate_asks_before_replacing_a_source` (field `cmd:x`, patched `QMessageBox.question` → `No` → field still `cmd:x`; → `Yes` → literal token without `:`), `test_generate_on_literal_does_not_ask`.
- [ ] **Step 2: Run** `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_page.py -q` — Expected: the new tests fail.
- [ ] **Step 3: Implement** per Interfaces. Token field placeholder/tooltip mentions `file:/path` and `cmd:command (e.g. a keyring lookup)`.
- [ ] **Step 4: Run** the full suite — Expected: all pass.
- [ ] **Step 5: Commit** `feat(settings): keyring-backed MCP token in the connect box (#141)`.

After the last task: wiki `MCP-Integration.md` gets a "Token from a keyring" paragraph with a `secret-tool` example, pushed only with the user's OK.
