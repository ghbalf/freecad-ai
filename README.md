# FreeCAD AI

> **Alpha software** — this project is in early development. Expect rough edges, breaking changes, and the occasional FreeCAD crash from LLM-generated code. Use with caution and save your work frequently.

An AI-powered assistant workbench for FreeCAD that generates and executes Python code to create 3D models from natural language descriptions.

## Features

- **Chat interface** — dock widget with streaming LLM responses
- **Plan / Act modes** — review code before execution (Plan) or auto-execute (Act)
- **Tool calling** — 50 structured FreeCAD operations (Act mode) for safer, more reliable modeling
- **Tool reranking** — optional keyword or LLM-based filter sends only the top-N most relevant tools to the LLM per turn, saving prompt tokens when many tools + MCP servers are registered
- **Skills** — reusable instruction sets the model invokes autonomously or via `/command` (enclosure, gear, fastener holes, sketch-from-image, etc.)
- **Skill optimizer** — automatically improve skill instructions via iterative test-evaluate-modify loop (`/optimize-skill`)
- **Hooks** — user-defined Python hooks for lifecycle events (block tools, modify input, log activity, convert file attachments)
- **User extension tools** — register your own Python functions as LLM-callable tools (`.py` or `.FCMacro`)
- **Vision routing** — auto-detects LLM vision capability; non-vision models use MCP fallback via [llm-vision-mcp](https://github.com/ghbalf/llm-vision-mcp), no-vision-path disables image controls
- **File attachments** — attach images, text files, and documents to chat messages (capture, attach, drag-drop, paste); extensible via `file_attach` hook for binary formats (PDF, DOCX) or MCP servers for rich conversion
- **Image support** — viewport screenshots and images sent as vision blocks; auto-detected binary format rejection (PDF, ZIP, Office docs)
- **Thinking mode** — enable LLM reasoning for complex multi-step tasks (Off / On / Extended)
- **Context compacting** — automatically summarizes older messages when approaching context limits
- **Session resume** — save and load chat sessions to continue work later
- **22 LLM providers** — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, Moonshot, DeepSeek, Qwen, Groq, Mistral, Together, Fireworks, xAI, Cohere, SambaNova, MiniMax, Llama, GitHub Models, HuggingFace, Zhipu, OpenCode (Zen/Go), plus any OpenAI-compatible endpoint via Custom
- **Context-aware** — automatically includes document state (objects, properties, selection) in prompts
- **Error self-correction** — failed code is sent back to the LLM for automatic retry (up to 3 attempts)
- **AGENTS.md support** — project-level instructions with include directives and variable substitution
- **Dark mode** — chat widget automatically adapts to FreeCAD's light/dark theme (theme changes require FreeCAD restart)
- **Persistent dock layout** — the chat dock remembers its position, tab siblings (e.g. tabified with the Tasks panel), and floating geometry across FreeCAD sessions
- **Zero external dependencies** — uses only Python stdlib (`urllib`, `json`, `threading`, `ssl`)

## Requirements

- FreeCAD 1.0+ (tested with 1.0.2 and 1.1.0)
- An LLM provider (local Ollama, or an API key for a cloud provider)

## Installation

Clone or copy this repository into FreeCAD's Mod directory:

### Linux

```bash
ln -s /path/to/freecad-ai ~/.local/share/FreeCAD/Mod/freecad-ai
```

### macOS

```bash
ln -s /path/to/freecad-ai ~/Library/Application\ Support/FreeCAD/Mod/freecad-ai
```

### Windows

```powershell
# Run as Administrator
New-Item -ItemType SymbolicLink -Path "$env:APPDATA\FreeCAD\Mod\freecad-ai" -Target "C:\path\to\freecad-ai"
```

Or manually copy the repository into `%APPDATA%\FreeCAD\Mod\freecad-ai`.

> **FreeCAD 1.1+ uses version-scoped user dirs.** The `Mod` directory moved
> under a per-version folder, so on Linux the path is
> `~/.local/share/FreeCAD/v1.1/Mod/freecad-ai` (e.g. `v1.1`, `v1.2`), with the
> equivalent `…/FreeCAD/v1.1/Mod/` layout on macOS and Windows. If the
> workbench doesn't appear after a restart, check that you symlinked into the
> versioned `Mod/` for the FreeCAD release you're running. The legacy
> unversioned path above still works on many builds but is no longer canonical.

---

Restart FreeCAD. The **FreeCAD AI** workbench will appear in the workbench selector.

## Configuration

There are two ways to configure FreeCAD AI:

- **Edit → Preferences → FreeCAD AI** — provider, model, API key, max tokens, mode, thinking, and tool calling. Persists to FreeCAD's parameter store and mirrors into the workbench config on next load.
- **Workbench Settings dialog** (gear icon in the chat panel) — everything above plus MCP servers, tool reranking, viewport capture, model parameters, hooks, system prompt overrides, and dock layout.

Both UIs stay in sync. Configuration is stored at `<FreeCADAI dir>/config.json`.

### Configuration paths

`<FreeCADAI dir>` is resolved on workbench load. The order is:

1. **`$FREECAD_AI_CONFIG_DIR`** environment variable, if set. Used as-is.
2. **`<FreeCAD user config dir>/FreeCADAI/`** when the workbench is running inside FreeCAD. On FreeCAD 1.1+ Linux this is `~/.config/FreeCAD/v1-1/FreeCADAI/` (version-scoped, top-level alongside FreeCAD's own `FreeCAD.conf`, `user.cfg`, `system.cfg`). The path is obtained via `FreeCAD.getUserConfigDir()` when available, otherwise derived from `FreeCAD.Version()` plus `$XDG_CONFIG_HOME` (default `~/.config`).
3. **`~/.config/FreeCAD/FreeCADAI/`** (legacy) when FreeCAD is not importable — pytest, console scripts, plain Python REPL.

> **Why under `.config/FreeCAD/v1-1/`?** FreeCAD 1.1 introduced version-scoped user dirs so different FreeCAD installs don't share settings. The workbench's data is config-shaped (settings, secrets, conversation logs) so it belongs under `XDG_CONFIG_HOME` (default `~/.config/`), not `XDG_DATA_HOME` (`~/.local/share/`, where `Mod/` and `Macro/` live). v0.13.0-alpha aligns with that XDG split.

#### One-shot migration on first launch of v0.13.0-alpha+

Users upgrading from v0.12.x and earlier are auto-migrated on first launch. Migration is rename-then-move with a two-stage candidate search and an automatic sweep:

| # | Action |
|---|---|
| 1 | If a stale `FreeCADAI/` already exists at the new target (e.g. created by FreeCAD 1.1's own first-launch migration of `~/.config/FreeCAD/`), rename it to `FreeCADAI.pre-v0.13-snapshot/`. Frees the target name without overwriting. |
| 2 | Pick the first **historical candidate** that has content as the migration source: (a) `<FreeCAD user data dir>/FreeCADAI/` (the v0.13.0-alpha pre-release wrote here briefly — only relevant on the maintainer's machine; released builds skip this), (b) `~/.config/FreeCAD/FreeCADAI/` (every released build before v0.13.0-alpha). |
| 3 | **Move** (`shutil.move` — atomic same-filesystem rename when possible) the source → `<FreeCAD user config dir>/FreeCADAI/`. The source ceases to exist; no copy is left behind. |
| 4 | **Sweep** any remaining historical candidates that *still* have content — rename them to `<candidate>.duplicate-cleanup/` (or `.duplicate-cleanup-<unix-ts>/` if the suffix is taken). Catches data left behind by an aborted/copy-based prior migration. |
| 5 | Drop a marker file `<target>/.freecad_ai_active_marker` so subsequent imports skip migration. |

The sweep also runs on **every** subsequent launch (cheap when no candidate dirs exist), so any leftover state from a partially-completed prior migration gets cleaned up automatically as soon as the new code runs.

The renamed `*.pre-v0.13-snapshot/` and `*.duplicate-cleanup/` directories are kept untouched as a safety net — safe to delete after you've confirmed the migration succeeded. The migration is idempotent (marker file blocks re-runs), best-effort (logs to stderr and falls back to a candidate path on failure rather than crashing the workbench), and collision-safe (pre-existing backup names get a Unix timestamp appended).

#### Forcing a custom location

Set `FREECAD_AI_CONFIG_DIR` in your environment to bypass the FreeCAD-based resolution entirely. Useful for:

- Running multiple isolated profiles for different LLM accounts
- Pointing at a synced location (Syncthing, Dropbox)
- Pinning a fixed path while testing v0.13.0+ migration

```bash
FREECAD_AI_CONFIG_DIR=~/work/freecad-ai-profile-1 freecad
```

### Secure API key storage

API keys can be entered as plain text, but for shared machines, repos, or backups, the key field accepts two prefixes that keep the secret out of the config file. Both work on Linux, macOS, and Windows.

> **Note on platform coverage:** the workbench is developed and tested on Linux. The macOS and Windows examples below follow the documented behavior of the underlying tools (`security`, `CredentialManager`, `expanduser`/`expandvars`, `subprocess.run(shell=True)`) but have **not** been verified on those OSes by the maintainer. If you run into a platform-specific issue, please [open an issue](https://github.com/ghbalf/freecad-ai/issues) — patches and corrections welcome.

**`file:` — read key from a file** (re-read on every API call). Path supports `~` and OS env vars (`$HOME`, `%APPDATA%`).

| OS | Example |
|---|---|
| Linux/macOS | `file:~/.config/anthropic-key` |
| Windows | `file:%APPDATA%\freecad-ai\anthropic-key.txt` |

Lock the file down with filesystem permissions (`chmod 600` on Unix, file-properties → security on Windows).

**`cmd:` — run a command, use its stdout as the key.** Lets you pull from the OS's native secret store via that store's CLI:

| OS | Native store | Example |
|---|---|---|
| Linux | libsecret (GNOME Keyring, KWallet) via `libsecret-tools` | `cmd:secret-tool lookup service freecad-ai account anthropic` |
| macOS | Keychain | `cmd:security find-generic-password -a anthropic -s freecad-ai -w` |
| Windows | Credential Manager (needs the [`CredentialManager` PowerShell module][CM]) | `cmd:powershell -Command "(Get-StoredCredential -Target 'freecad-ai-anthropic').GetNetworkCredential().Password"` |
| Any | GPG | `cmd:gpg --decrypt ~/keys/anthropic.gpg` |

To populate the stores once:

- **Linux:** `secret-tool store --label="freecad-ai anthropic" service freecad-ai account anthropic`
- **macOS:** `security add-generic-password -a anthropic -s freecad-ai -w 'sk-ant-…'`
- **Windows (PowerShell):** `New-StoredCredential -Target 'freecad-ai-anthropic' -UserName anthropic -Password (Read-Host -AsSecureString)`

[CM]: https://www.powershellgallery.com/packages/CredentialManager

### Supported Providers

| Provider | API Key Required | Notes |
|----------|-----------------|-------|
| Ollama | No | Local models, default `http://localhost:11434` |
| Anthropic | Yes | Claude models via native API |
| OpenAI | Yes | GPT models |
| Gemini | Yes | Google AI via OpenAI-compatible endpoint |
| OpenRouter | Yes | Multi-provider gateway |
| Moonshot | Yes | Kimi models (default params pre-configured) |
| DeepSeek | Yes | DeepSeek-V3 |
| Qwen | Yes | Alibaba DashScope (international) |
| Groq | Yes | Ultra-fast inference |
| Mistral | Yes | Mistral models |
| Together | Yes | Open model hosting |
| Fireworks | Yes | Fast inference |
| xAI | Yes | Grok models |
| Cohere | Yes | Command models |
| SambaNova | Yes | Fast inference |
| MiniMax | Yes | MiniMax models |
| Llama | Yes | Meta's official Llama API |
| GitHub | Yes (PAT) | GitHub Models marketplace |
| HuggingFace | Yes (`hf_...`) | Serverless inference API |
| Zhipu | Yes | GLM models (z.ai international) |
| OpenCode Zen | Yes | Pay-as-you-go via opencode.ai; dynamic model list, reasoning variants |
| OpenCode Go | Yes | $10/mo subscription via opencode.ai; dynamic model list, reasoning variants |
| Custom | Varies | Any OpenAI-compatible endpoint |

### Model Parameters

The **Model Parameters** table in Settings lets you set arbitrary sampling parameters (temperature, top_p, top_k, etc.) that are sent with each API request. Parameters are saved per model name — when you switch models, the saved parameters for that model are loaded automatically.

Click **Load Defaults** to reset to the provider's recommended values. For most providers the only default is `temperature: 0.3`. Moonshot ships with pre-configured values required by Kimi-K2.5.

#### Strip thinking from history

The **"Strip thinking from conversation history"** checkbox in Settings controls whether thinking/reasoning content is removed from previous turns before sending to the API. Some models (e.g. Gemma 3/4) reject thinking content in multi-turn conversations, while others (e.g. Kimi-K2.5) require it.

The checkbox is tristate: partially checked = auto-detect by model name (Gemma models strip automatically), checked = always strip, unchecked = never strip.

## Usage

### Plan Mode

Type a request like *"Create a box 50mm x 30mm x 20mm"*. The AI generates Python code and displays it for review. Click **Execute** to run it, **Check** to run the sandbox validation (catches FreeCAD C++-console errors and invalid shapes without touching the live document), or **Fix with AI** to loop the code plus a prompt back to the LLM for correction. **Copy** copies to clipboard.

### Act Mode

Same workflow, but with two execution paths:

- **Tool calling** (default): The LLM invokes structured tools like `create_primitive`, `boolean_operation`, `fillet_edges`, etc. These are pre-validated operations wrapped in undo transactions — safer and more reliable than raw code.
- **Code generation** (fallback): When tools are disabled or the LLM generates code blocks instead, code executes with the same safety layers as before (static validation, subprocess sandbox, undo transactions).

Tool calling is enabled by default. Disable it by setting `enable_tools: false` in `<FreeCADAI dir>/config.json`.

### Available Tools

| Tool | Description |
|------|-------------|
| `create_primitive` | Box, Cylinder, Sphere, Cone, Torus |
| `create_body` | Create a PartDesign Body for parametric modeling |
| `create_sketch` | Sketch with lines, circles, arcs, rectangles + constraints (supports plane offset) |
| `pad_sketch` | Extrude a sketch |
| `pocket_sketch` | Cut a pocket from a sketch (auto-detects correct direction) |
| `revolve_sketch` | Revolve a sketch around an axis (vase, wheel, bottle) |
| `loft_sketches` | Loft between sketches for tapered/organic shapes |
| `sweep_sketch` | Sweep a profile along a spine path (pipe, tube) |
| `boolean_operation` | Fuse, Cut, or Common between two objects |
| `transform_object` | Move and/or rotate an object |
| `fillet_edges` | Round edges |
| `chamfer_edges` | Chamfer edges |
| `shell_object` | Hollow out a solid (PartDesign::Thickness) |
| `mirror_feature` | Mirror a PartDesign feature across a plane |
| `create_wedge` | Create a wedge/ramp shape |
| `scale_object` | Scale an object uniformly or per-axis |
| `section_object` | Cross-section through a plane or another object |
| `linear_pattern` | Repeat a feature in a line |
| `polar_pattern` | Repeat a feature in a circular pattern |
| `multi_transform` | Chain linear pattern + polar pattern + mirror in one feature |
| `create_inner_ridge` | Add a snap-fit ridge inside a rectangular hollow |
| `create_snap_tabs` | Add snap tabs on a lid lip (pairs with ridge) |
| `create_enclosure_lid` | Generate a snap-fit enclosure lid with correct dimensions |
| `measure` | Volume, area, bounding box, distance, edge listing |
| `describe_model` | Comprehensive geometry summary: dimensions, wall thickness, hollow/solid detection |
| `list_faces` | List all faces with names, labels, normals, areas (optional filter) |
| `list_edges` | List all edges with names, labels, lengths (optional filter) |
| `list_documents` | List all open documents with object counts and active indicator |
| `switch_document` | Switch the active FreeCAD document by name or label |
| `get_document_state` | Inspect current objects and properties |
| `create_variable_set` | Create a VarSet with named variables for parametric modeling |
| `create_spreadsheet` | Create a Spreadsheet with named variables for parametric modeling |
| `set_expression` | Bind object properties to expressions (parametric relationships) |
| `modify_property` | Change any object property (supports relative: +10%, *1.5) |
| `export_model` | Export to STL, STEP, or IGES |
| `execute_code` | Fallback: run arbitrary Python |
| `undo` | Undo last N operations, or undo until a named transaction |
| `redo` | Redo previously undone operations |
| `undo_history` | Show the undo/redo stack with named transactions |
| `capture_viewport` | Save a screenshot of the 3D viewport |
| `set_view` | Set camera orientation (front, top, isometric, etc.) |
| `zoom_object` | Zoom the viewport to a specific object |
| `use_skill` | Load a skill's instructions for complex tasks (enclosure, gear, etc.) |
| `create_assembly` | Create an Assembly with grounded base part |
| `add_assembly_joint` | Joint two parts (Fixed, Revolute, Cylindrical, Slider, Ball) |
| `add_part_to_assembly` | Add a part to an existing assembly |
| `select_geometry` | Interactive viewport picking for edges, faces, vertices |

### Connecting external MCP clients (Claude Code, Claude Desktop, …)

These same tools can be exposed as a standalone MCP server, so any MCP-capable client can drive FreeCAD — not just this addon's own chat panel. Driving it from a client you already subscribe to means no provider API key and no per-token cost on top of it.

Three ways to start it, differing in who owns the FreeCAD process:

- **The toolbar** — no paths, no command line. In the **FreeCAD AI** workbench, click **MCP Server** on the toolbar (or the matching entry in the FreeCAD AI menu) to start the server inside the running FreeCAD; click again to stop it. The URL is printed to the Report view, and the address lives in **AI Settings → MCP Servers**. The button reflects the real server state, so a server started either of the ways below shows as running and can be stopped from it too.

  ```bash
  claude mcp add --transport sse freecad http://127.0.0.1:3000/sse
  ```

- **`mcp_server_http.py`** — the same HTTP/SSE server, started from the command line. FreeCAD is already running with your document open and the client attaches to it, so you watch the model change live in the viewport.

  ```bash
  /path/to/FreeCAD.AppImage /path/to/freecad-ai/mcp_server_http.py
  claude mcp add --transport sse freecad http://127.0.0.1:3000/sse
  ```

- **`mcp_server_entry.py`** — STDIO, headless. The client spawns FreeCAD, which lives and dies with the session.

  ```bash
  claude mcp add freecad -- bash -c \
    'exec 3>&1 1>&2 && /path/to/FreeCAD.AppImage -c /path/to/freecad-ai/mcp_server_entry.py'
  ```

> **The MCP server has no authentication.** Anything that can reach the address it is bound to can run FreeCAD tools, including arbitrary Python. On the `127.0.0.1` default that means any process on your machine. Tracked in [#59](https://github.com/ghbalf/freecad-ai/issues/59).

Note that Claude Code loads MCP tools **at session start** — a server added mid-session won't appear until the next launch.

Full setup, including Flatpak paths, `MCP_HOST`/`MCP_PORT`, and why the STDIO mode needs the `bash -c` wrapper: [MCP Integration](https://github.com/ghbalf/freecad-ai/wiki/MCP-Integration#mcp-server-exposing-freecad-tools) in the wiki.

### Skills

Skills are reusable instruction sets stored in `<FreeCADAI dir>/skills/`. Invoke them by typing `/command` in the chat.

**Built-in skills:**

| Command | Description |
|---------|-------------|
| `/enclosure` | Parametric electronics enclosure with base, lid, screw posts |
| `/thread-insert` | Heat-set thread insert holes (M2–M5) with correct dimensions |
| `/gear` | Involute spur gear from module and tooth count |
| `/fastener-hole` | Clearance, counterbore, or countersink holes (ISO dimensions) |
| `/lattice` | Grid, honeycomb, or diagonal infill patterns |
| `/skill-creator` | Create new skills interactively from the chat |

**Creating custom skills:**

You can create skills manually (see below) or use `/skill-creator` in the chat to have the AI walk you through it.

```
<FreeCADAI dir>/skills/
  my-skill/
    SKILL.md          # Instructions injected into the LLM prompt
    handler.py        # Optional: Python handler with execute(args) function
```

The `SKILL.md` file contains instructions for the LLM. When invoked, these are injected into the prompt along with any arguments you provide. For example:

```
/enclosure 100x60x40mm, 2mm walls, snap-fit lid
```

If a `handler.py` exists with an `execute(args)` function, it runs directly instead of prompting the LLM.

### User Extension Tools

You can register your own Python functions as tools that the LLM can call. Place `.py` or `.FCMacro` files in `<FreeCADAI dir>/tools/` (see [Configuration paths](#configuration-paths) for what `<FreeCADAI dir>` resolves to on your setup). Functions with type hints are automatically discovered and registered:

```python
import math

def bolt_circle(diameter: float, count: int = 8, bolt_size: float = 6.5) -> str:
    """Create a bolt hole circle pattern on the XY plane."""
    import Part
    import FreeCAD as App
    # ... create geometry ...
    return f"Created {count} bolt holes on {diameter}mm PCD"
```

**Requirements:**
- Public functions (no `_` prefix) with type-hinted parameters
- Supported types: `float`, `int`, `str`, `bool`
- Return a `str` (success message) or `dict` with `output`/`data` keys
- Docstring first line becomes the tool description

User tools are prefixed with `user_` and available to the LLM, skills, and MCP server. Manage them in Settings → User Tools (Add, Remove, Reload). Enable "Scan FreeCAD macro directory" to also pick up compatible macros from FreeCAD's macro folder.

### Thinking Mode

Enable LLM reasoning in Settings (gear icon). Three levels:

| Level | Description |
|-------|-------------|
| Off | Standard responses (default) |
| On | LLM shows reasoning before responding |
| Extended | Longer reasoning budget for complex tasks |

Thinking is displayed dimmed in the chat. Provider support varies — Anthropic Claude, OpenAI o-series, and some Ollama models (qwen3) support thinking. Models that don't support it will silently ignore the setting.

### Session Resume

Conversations are auto-saved after each LLM response. Click the **Load** button in the chat footer to resume a previous session from the last 20 saved conversations.

### File Attachments

Attach files to chat messages via the **Attach** button, drag-and-drop, or paste:

| File type | Handling |
|-----------|----------|
| Images (PNG, JPG, etc.) | Sent as vision blocks (requires vision-capable model or MCP fallback) |
| Text files (TXT, MD, CSV, PY, JSON, etc.) | Read as text and included in the message |
| Binary files (PDF, DOCX, XLSX, etc.) | Converted via `file_attach` hook or MCP server |

**Why no built-in PDF/DOCX support?** FreeCAD AI has a strict zero-external-dependencies policy — it uses only Python stdlib and runs inside FreeCAD's bundled Python. PDF/DOCX parsing requires external C libraries that cannot be bundled portably. Instead, two extensibility paths are provided:

1. **Hooks** — lightweight, user-installed shell commands. Create a `file_attach` hook that calls CLI tools like `pdftotext` or `pandoc`. See `docs/hooks/file-attach-example/` for a ready-to-use example. Text-only output.
2. **MCP servers** — for rich conversion with images and structure preservation. Install a server like [markdownify-mcp](https://github.com/zcaceres/markdownify-mcp) in Settings → MCP Servers.

To install the example PDF hook:

```bash
# Replace <FreeCADAI dir> with your actual path — see "Configuration paths" above.
# On FreeCAD 1.1+ Linux this is ~/.config/FreeCAD/v1-1/FreeCADAI/.
cp -r docs/hooks/file-attach-example/ "<FreeCADAI dir>/hooks/file-attach/"
# Requires: sudo apt install poppler-utils  (provides pdftotext)
```

Binary files are detected by magic bytes (PDF, ZIP/Office, PNG, JPEG, ELF, etc.) and null-byte content scanning.

### AGENTS.md

Place an `AGENTS.md` or `FREECAD_AI.md` file next to your `.FCStd` file to provide project-specific instructions:

```markdown
# AGENTS.md
This project uses metric units (mm).
All parts should have 1mm fillets on external edges.
Use PartDesign workflow (Body -> Sketch -> Pad), not Part primitives.
```

**Search order:** document directory → parent directories (up to 3 levels) → `<FreeCADAI dir>/AGENTS.md`

**Include directives** — split instructions across files:
```markdown
<!-- include: materials.md -->
<!-- include: conventions.md -->
```

**Variable substitution** — use live document values:
```markdown
Document: {{document_name}}
Objects: {{object_count}}
Active body: {{active_body}}
```

## Running macros & Dangerous mode

The `run_macro` tool lets the assistant run an existing FreeCAD macro and read
its console output directly — useful for AI-written test harnesses. By default
it only runs a macro by **name** from FreeCAD's macro directory.

### ⚠️ Dangerous mode — read this

Dangerous mode is an opt-in escape hatch that **removes the safety checks**
FreeCAD AI normally applies to code it runs. Enable it from the chat panel
(a confirmation dialog explains the risks; it resets to OFF every time FreeCAD
restarts, and a red banner shows while it is active).

While Dangerous mode is active:

- **Arbitrary code/commands:** AI-run code can call shell commands, delete
  files, and do anything your user account is allowed to do.
- **No timeout:** a macro containing an infinite loop will **freeze FreeCAD's
  main window with no way to recover — any unsaved work is lost.**
- **No sandbox pre-check:** broken or destructive code runs straight against
  your live document.
- **Endless loops:** if you also set the tool-loop count to `0` (endless), the
  AI can keep acting until you press **Stop**. There is no other limit, and it
  can consume a large number of tokens.

**You are solely responsible for anything you run in this mode.** It is provided
as a power-user convenience and is not tested against malicious or destructive
input. Document integrity (undo rollback) is the only safeguard that remains on.

To make Dangerous mode persist across restarts (not recommended), set
`"dangerous_skip_safety": true` in your `config.json` by hand. The GUI will
never write this for you.

> Note: behavior described here is verified on Linux; the macro-directory
> resolution uses FreeCAD's cross-platform API but is untested on macOS/Windows.

## Translations

The UI supports internationalization via Qt's `.ts`/`.qm` translation system. Currently available:

| Language | Status |
|----------|--------|
| English  | Default (built-in) |
| German   | Complete |

To add a new language, copy `translations/freecad_ai_de.ts` to `freecad_ai_<lang>.ts`, translate the strings (or use [Qt Linguist](https://doc.qt.io/qt-5/qtlinguist-index.html)), and compile with `lrelease`.

To re-extract strings after code changes:
```bash
cd translations && bash update_translations.sh
```

## Project Structure

```
freecad-ai/
├── Init.py                    # Non-GUI init
├── InitGui.py                 # Workbench registration + commands
├── package.xml                # FreeCAD addon metadata
├── freecad_ai/
│   ├── config.py              # Settings (provider, API key, mode, tools, thinking)
│   ├── i18n.py                # Internationalization helpers
│   ├── paths.py               # Path utilities
│   ├── llm/
│   │   ├── client.py          # HTTP client with SSE streaming + tool calling
│   │   └── providers.py       # Provider registry
│   ├── tools/
│   │   ├── registry.py        # Tool abstractions + registry
│   │   ├── freecad_tools.py   # 39 FreeCAD tool handlers
│   │   └── setup.py           # Default registry factory
│   ├── ui/
│   │   ├── compat.py          # PySide2/PySide6 shim
│   │   ├── chat_widget.py     # Chat dock + agentic tool loop
│   │   ├── message_view.py    # Message + tool call rendering
│   │   ├── code_review_dialog.py
│   │   └── settings_dialog.py
│   ├── core/
│   │   ├── active_document.py # GUI-aligned active document resolution
│   │   ├── executor.py        # Code execution with safety layers
│   │   ├── context.py         # Document state inspector
│   │   ├── system_prompt.py   # System prompt builder
│   │   └── conversation.py    # Conversation history + compacting + save/load
│   ├── hooks/
│   │   ├── __init__.py        # Hooks package
│   │   └── registry.py        # Hook lifecycle event dispatch
│   └── extensions/
│       ├── agents_md.py       # AGENTS.md loader (multi-location, includes, vars)
│       ├── skills.py          # Skills registry + execution
│       └── user_tools.py      # User extension tools (discover, validate, register)
├── translations/
│   ├── freecad_ai_de.ts       # German translation source
│   └── update_translations.sh # Re-extract strings with pylupdate5
├── skills/                    # Built-in skill definitions
│   ├── enclosure/SKILL.md
│   ├── gear/SKILL.md
│   ├── fastener-hole/SKILL.md
│   ├── thread-insert/SKILL.md
│   ├── lattice/SKILL.md
│   └── skill-creator/SKILL.md
├── hooks/                     # Built-in hook examples
│   └── log-tool-calls/
└── resources/
    └── icons/
        └── freecad_ai.svg
```

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a dev environment, submit pull requests, and create new skills or providers.

## License

- **Code:** LGPL-2.1 — see [LICENSE-CODE](LICENSE-CODE)
- **Icons:** CC0-1.0 (public domain) — see [LICENSE-ICON](LICENSE-ICON)
