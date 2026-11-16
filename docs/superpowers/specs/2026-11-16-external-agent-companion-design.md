# External agents as FreeCAD AI companions — design (#139)

Status: approved approach, short design (2026-11-16)

## Intent

Let users drive FreeCAD from an agent they already run in a terminal or editor:
Claude Code, Codex, Gemini CLI, Qwen Code, Kimi CLI, OpenCode, Cline, Cursor,
or anything else that speaks MCP. The agent should be as good at FreeCAD as
our own chat. Each user signs in to their agent and pays its vendor (a
subscription, a free tier, or their own GPU) entirely outside this add-on.

**Why:** a subscription is far cheaper than API billing for open-source users.
But the feature must work for every agent, not only paid Anthropic plans.
The maintainer's rule: **available to all, or not at all.**

## Goals

1. Every MCP client that connects receives our FreeCAD tool guidance.
2. Connecting takes one copy-paste, with no wiki lookup and no hand-typed port or token.
3. The server can be up before the agent starts.
4. The docs present this as agent-neutral.

## Non-goals and anti-goals

- **No ACP backend.** The original #139 idea is dropped. Hosting Claude's ACP
  adapter with a subscription login inside our dock falls into what Anthropic's
  terms reserve for approved partners: "Anthropic does not permit third-party
  developers to offer Claude.ai login into their own applications"
  (https://code.claude.com/docs/en/legal-and-compliance). An ACP backend that
  served only non-Anthropic agents was rejected as well. We don't host any agent.
- Never handle, store or proxy an agent's credentials.
- **Anti-goal:** anything that works better for one vendor's paying users. The
  dropdown and docs list several agents equally, with Claude Code as one entry.
- The built-in chat loop stays the default path. Nothing about it changes.
- Skills offered as MCP prompts are out of scope here. That gets a follow-up issue.

## Approaches considered

| Approach | Trade-off | Verdict |
|---|---|---|
| ACP backend in the dock (Claude, Codex, …) | Chat stays inside FreeCAD; biggest job; the Claude subscription path is in a grey zone | Rejected (terms) |
| ACP for non-Anthropic agents only | Serves a minority, and leaves the subscription goal unmet | Rejected ("for all or not at all") |
| **Polish the existing MCP server for external agents** | Small; vendor-neutral; within all terms; the agent's chat lives outside FreeCAD | **Chosen** |

## Design

### 1. FreeCAD guidance in the MCP `initialize` result

- Today the modern era sends `DISCOVER_INSTRUCTIONS` (two sentences,
  `freecad_ai/mcp/server.py`). The legacy era sends no `instructions` at all.
- Replace both with one shared text, built from the tool-strategy rules our
  chat already gives the model in `core/system_prompt.py` (`ACT_MODE_TOOLS`):
  - which tool to use for what
  - preserve parametric history
  - first-class tools before `execute_code`
  - `create_body` before sketch workflows
- Single source: move the agent-neutral rules into a constant that both the
  chat prompt and the MCP server use, so they cannot drift apart.
- Leave the chat-only parts out of the server text: the "You are in Act mode"
  framing, and anything that assumes our dock.
- **Legacy era:** add `instructions` only for revisions whose
  `InitializeResult` defines it (check each revision in `PROTOCOL_REVISIONS`
  against the MCP schema). This deliberately changes the legacy
  `initialize` bytes. The #64 rule was "no *accidental* change"; this change
  gets an explicit test and a CHANGELOG line.

### 2. "Connect an external agent" box in AI Settings → MCP Servers

- **Always shown, agent-neutral:**
  - the endpoint URL (`http://HOST:PORT/mcp`), built from
    `resolve_server_address(cfg)`
  - the header line `Authorization: Bearer <token>`, present only when a token
    is configured
  - a generic JSON config block
  - each with a Copy button
- **Dropdown of ready-made commands** for a few agents, Claude Code being one:
  `claude mcp add --transport http freecad <url> -H "Authorization: Bearer <token>"`.
- Use each agent's syntax exactly as its own docs give it. Every entry
  except Claude Code is labelled "from that tool's docs, not tested by the
  maintainer".
- **Where the entries live (user decision): only in the user's config.** There
  is no built-in list read at run time. Agent CLIs change their syntax, and the
  user must be able to fix an entry without waiting for a release. (We learned
  this from the providers.)
  - Config key `mcp_agent_recipes` in `config.json`. Each entry holds:
    - `name`
    - `template`, the command with the placeholders `{url}` and `{header_args}`
    - `header_arg`, the token part with the placeholder `{token}`
    - `docs_url`
    - `tested`, a boolean
  - **Seed once:** the first time the key is absent, write the default entries
    into config. After that, never touch them. A release must not overwrite
    user entries (the #75 lesson). A user who empties the list keeps an empty
    list: absent and empty are different states.
  - **"Reset to defaults" button** next to the dropdown. It asks for
    confirmation, then replaces the list with the current defaults. It is the
    only path that overwrites.
  - **Reset saves the old list first (user decision).** `save_config` is
    atomic (temp file + `os.replace`) but keeps no previous copy, so before
    replacing anything Reset writes the current list as-is to
    `<config dir>/mcp_agent_recipes.<YYYYMMDD-HHMMSS>.json`. The confirmation
    dialog and the Report view name that file.
    - If the backup can't be written, Reset aborts and changes nothing.
    - My call: when the current list already equals the defaults, there is
      nothing to lose, so no backup file is written.
    - Old backup files are never deleted automatically. Resets are rare, and
      the files are small.
  - The defaults exist in code only to seed and to reset. The CHANGELOG notes
    when a default command changes, so users know to click Reset.
  - For now, users edit the list in `config.json`. There is no list editor in
    Settings yet.
  - Insert the token shell-quoted (`shlex.quote`) and the URL as built by us.
    Skip a malformed entry (missing field, unknown placeholder) with a
    Report-view warning; don't crash the dialog.
  - The generic URL/header/JSON block always comes from code, not from config.
    It is the fallback that is never stale.
- Values come from the settings currently in the dialog, so they update as
  the user edits the port or token.
- Note: the values apply after the server restarts. The existing behaviour is that settings are read at start.

### 3. "Start the MCP server with FreeCAD" option

- A new config flag, **default off** (new defaults must preserve existing
  behaviour).
- When on, the workbench starts the server through the same `ServerController`
  path the toolbar button uses. The button keeps reflecting real server state
  (PR #61 behaviour).
- A start failure (port taken, non-ASCII token) is logged to the Report view.
  It is never a modal at startup.

### 4. Wiki

- New section "Using FreeCAD AI from an external agent" in `MCP-Integration.md`.
  It covers:
  - why use an external agent (your own subscription, free tier or local model)
  - an agent table, untested entries flagged
  - the Settings box
  - the autostart option
  - a link to Anthropic's terms for Claude subscriptions, as one example of
    checking your agent vendor's terms
- Link to it from `Home.md`.

## Constraints

- No new dependencies. Linux-only maintainer: label untested paths.
- The seed defaults live in one place in code (append-only, like the provider list), used only for seeding and Reset.
- Qt: flat enums, imports via `ui/compat.py`. The settings box lives in the
  shared section class, so the dialog and the prefs page stay identical.

## Left to the builder

- The exact guidance text and its length. My call: aim for about 1–1.5k
  tokens, since every agent pays for it on each session.
- Which non-Claude agents to list first. Pick ones with public MCP-config docs.
- The config key name (suggestion: `mcp_server_autostart`).
- Test layout: unit tests for the instructions text in both eras, for the
  command builder (with and without a token, and a token that needs quoting),
  for seeding (absent → seeded; empty or edited → untouched), and for Reset (backup written first; backup failure aborts; no backup when already default). An integration check with real
  `claude mcp list` is manual, done by the maintainer.
