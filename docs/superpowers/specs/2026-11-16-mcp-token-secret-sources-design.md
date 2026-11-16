# MCP server token from a keyring or file (#141) — design

Approved 2026-11-16. A small change; follow-up for the agent side is #146.

## Intent

Provider API keys accept `file:/path` and `cmd:command`; the MCP server's
bearer token should too, so it can live in a keyring (`secret-tool`, `pass`,
`op read`, macOS `security`) instead of in plain text in `config.json`.
Protects the secret at rest only, not against code running as the same user.

**Non-goals:** the agent side still receives the literal token when the user
copies a snippet (#146). No `keyring` package (no external deps).

## Approaches

- **Chosen (option a):** keep the token out of `config.json` only. Small.
- Rejected for now (option b): agent snippets that fetch the token
  themselves (Claude Code `headersHelper`, Codex `bearer_token_env_var`).
  Doubles the work, each agent needs testing; tracked as #146.
- **Startup, chosen (option 1):** resolve on the main thread at server
  start, autostart included. A locked keyring freezes FreeCAD for up to the
  10 s timeout while its own unlock prompt is up; on timeout the server does
  not start and a warning says why. Rejected: a background-thread resolve
  (more moving parts in the start path shared by autostart and the toolbar
  toggle, for a case that only happens with a locked keyring).

## Decided

1. One shared resolver, moved out of `LLMClient._resolve_api_key`, stdlib
   only, raising on failure. `LLMClient` keeps its behaviour: it logs and
   returns `""` on a failure, as today.
2. `resolve_auth_token`: `MCP_AUTH_TOKEN` still beats the config, and both
   accept the prefixes. The ASCII check runs on the resolved value.
3. A configured `cmd:`/`file:` that fails, times out or yields an empty
   value **stops the server from starting** with an error naming the
   source. It never falls back to running without a token. (A blank field
   still means "no token", as today.)
4. Connect box: while the token field holds `cmd:`/`file:`, the URL,
   header, JSON and command show a placeholder (`<token from cmd:>` /
   `<token from file:>`). The source is read only when the user presses a
   Copy button whose text contains the token; a failure shows a warning and
   copies nothing. Nothing runs while typing.
5. Generate still writes a literal token; when the field holds
   `cmd:`/`file:` it asks for confirmation first.

## Left to the builder

Module location and names, the exact placeholder and message wording, and
the hint/tooltip text that mentions the prefixes. Docs: CHANGELOG
Unreleased; wiki MCP-Integration (token from a keyring, with a
`secret-tool` example).
