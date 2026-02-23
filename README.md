# FreeCAD AI

> **Alpha software** — this project is in early development. Expect rough edges, breaking changes, and the occasional FreeCAD crash from LLM-generated code. Use with caution and save your work frequently.

An AI-powered assistant workbench for FreeCAD that generates and executes Python code to create 3D models from natural language descriptions.

## Features

- **Chat interface** — dock widget with streaming LLM responses
- **Plan / Act modes** — review code before execution (Plan) or auto-execute (Act)
- **Tool calling** — structured FreeCAD operations (Act mode) for safer, more reliable modeling
- **Skills** — reusable instruction sets invoked via `/command` (enclosure, gear, fastener holes, etc.)
- **Thinking mode** — enable LLM reasoning for complex multi-step tasks (Off / On / Extended)
- **Context compacting** — automatically summarizes older messages when approaching context limits
- **Session resume** — save and load chat sessions to continue work later
- **Multiple LLM providers** — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, or any OpenAI-compatible endpoint
- **Context-aware** — automatically includes document state (objects, properties, selection) in prompts
- **Error self-correction** — failed code is sent back to the LLM for automatic retry (up to 3 attempts)
- **AGENTS.md support** — project-level instructions with include directives and variable substitution
- **Zero external dependencies** — uses only Python stdlib (`urllib`, `json`, `threading`, `ssl`)

## Requirements

- FreeCAD 1.0+ (tested with 1.0.2)
- An LLM provider (local Ollama, or an API key for a cloud provider)

## Installation

Clone or copy this repository into FreeCAD's Mod directory:

```bash
# Option 1: symlink (recommended for development)
ln -s /path/to/freecad-ai ~/.local/share/FreeCAD/Mod/freecad-ai

# Option 2: copy
cp -r /path/to/freecad-ai ~/.local/share/FreeCAD/Mod/freecad-ai
```

Restart FreeCAD. The **FreeCAD AI** workbench will appear in the workbench selector.

## Configuration

1. Switch to the FreeCAD AI workbench
2. Click the gear icon to open settings
3. Select your LLM provider and enter your API key (if needed)
4. Click **Test Connection** to verify

Configuration is stored at `~/.config/FreeCAD/FreeCADAI/config.json`.

### Supported Providers

| Provider | API Key Required | Notes |
|----------|-----------------|-------|
| Ollama | No | Local models, default `http://localhost:11434` |
| Anthropic | Yes | Claude models via native API |
| OpenAI | Yes | GPT models |
| Gemini | Yes | Google AI via OpenAI-compatible endpoint |
| OpenRouter | Yes | Multi-provider gateway |
| Custom | Varies | Any OpenAI-compatible endpoint |

## Usage

### Plan Mode

Type a request like *"Create a box 50mm x 30mm x 20mm"*. The AI generates Python code and displays it for review. Click **Execute** to run it, or **Copy** to copy to clipboard.

### Act Mode

Same workflow, but with two execution paths:

- **Tool calling** (default): The LLM invokes structured tools like `create_primitive`, `boolean_operation`, `fillet_edges`, etc. These are pre-validated operations wrapped in undo transactions — safer and more reliable than raw code.
- **Code generation** (fallback): When tools are disabled or the LLM generates code blocks instead, code executes with the same safety layers as before (static validation, subprocess sandbox, undo transactions).

Tool calling is enabled by default. Disable it by setting `enable_tools: false` in `~/.config/FreeCAD/FreeCADAI/config.json`.

### Available Tools

| Tool | Description |
|------|-------------|
| `create_body` | Create a PartDesign Body for parametric modeling |
| `create_primitive` | Box, Cylinder, Sphere, Cone, Torus |
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
| `create_inner_ridge` | Add a snap-fit ridge inside a rectangular hollow |
| `create_snap_tabs` | Add snap tabs on a lid lip (pairs with ridge) |
| `measure` | Volume, area, bounding box, distance, edge listing |
| `get_document_state` | Inspect current objects and properties |
| `modify_property` | Change any object property |
| `export_model` | Export to STL, STEP, or IGES |
| `execute_code` | Fallback: run arbitrary Python |
| `undo` | Undo last N operations |

### Skills

Skills are reusable instruction sets stored in `~/.config/FreeCAD/FreeCADAI/skills/`. Invoke them by typing `/command` in the chat.

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
~/.config/FreeCAD/FreeCADAI/skills/
  my-skill/
    SKILL.md          # Instructions injected into the LLM prompt
    handler.py        # Optional: Python handler with execute(args) function
```

The `SKILL.md` file contains instructions for the LLM. When invoked, these are injected into the prompt along with any arguments you provide. For example:

```
/enclosure 100x60x40mm, 2mm walls, snap-fit lid
```

If a `handler.py` exists with an `execute(args)` function, it runs directly instead of prompting the LLM.

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

### AGENTS.md

Place an `AGENTS.md` or `FREECAD_AI.md` file next to your `.FCStd` file to provide project-specific instructions:

```markdown
# AGENTS.md
This project uses metric units (mm).
All parts should have 1mm fillets on external edges.
Use PartDesign workflow (Body -> Sketch -> Pad), not Part primitives.
```

**Search order:** document directory → parent directories (up to 3 levels) → `~/.config/FreeCAD/FreeCADAI/AGENTS.md`

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
│   │   ├── freecad_tools.py   # 20 FreeCAD tool handlers
│   │   └── setup.py           # Default registry factory
│   ├── ui/
│   │   ├── compat.py          # PySide2/PySide6 shim
│   │   ├── chat_widget.py     # Chat dock + agentic tool loop
│   │   ├── message_view.py    # Message + tool call rendering
│   │   ├── code_review_dialog.py
│   │   └── settings_dialog.py
│   ├── core/
│   │   ├── executor.py        # Code execution with safety layers
│   │   ├── context.py         # Document state inspector
│   │   ├── system_prompt.py   # System prompt builder
│   │   └── conversation.py    # Conversation history + compacting + save/load
│   └── extensions/
│       ├── agents_md.py       # AGENTS.md loader (multi-location, includes, vars)
│       └── skills.py          # Skills registry + execution
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
└── resources/
    └── icons/
        └── freecad_ai.svg
```

## License

LGPL-2.1 — see [LICENSE](LICENSE).
