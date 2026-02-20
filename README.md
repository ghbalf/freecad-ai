# FreeCAD AI

An AI-powered assistant workbench for FreeCAD that generates and executes Python code to create 3D models from natural language descriptions.

## Features

- **Chat interface** — dock widget with streaming LLM responses
- **Plan / Act modes** — review code before execution (Plan) or auto-execute (Act)
- **Multiple LLM providers** — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, or any OpenAI-compatible endpoint
- **Context-aware** — automatically includes document state (objects, properties, selection) in prompts
- **Error self-correction** — failed code is sent back to the LLM for automatic retry (up to 3 attempts)
- **AGENTS.md support** — project-level instructions loaded from files alongside your `.FCStd` documents
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

Same workflow, but code executes automatically (with a confirmation dialog unless `auto_execute` is enabled in settings).

### AGENTS.md

Place an `AGENTS.md` or `FREECAD_AI.md` file next to your `.FCStd` file to provide project-specific instructions:

```markdown
# AGENTS.md
This project uses metric units (mm).
All parts should have 1mm fillets on external edges.
Use PartDesign workflow (Body -> Sketch -> Pad), not Part primitives.
```

## Project Structure

```
freecad-ai/
├── Init.py                    # Non-GUI init
├── InitGui.py                 # Workbench registration + commands
├── package.xml                # FreeCAD addon metadata
├── freecad_ai/
│   ├── config.py              # Settings (provider, API key, mode)
│   ├── paths.py               # Path utilities
│   ├── llm/
│   │   ├── client.py          # HTTP client with SSE streaming
│   │   └── providers.py       # Provider registry
│   ├── ui/
│   │   ├── compat.py          # PySide2/PySide6 shim
│   │   ├── chat_widget.py     # Main chat dock widget
│   │   ├── message_view.py    # Message rendering
│   │   ├── code_review_dialog.py
│   │   └── settings_dialog.py
│   ├── core/
│   │   ├── executor.py        # Code execution engine
│   │   ├── context.py         # Document state inspector
│   │   ├── system_prompt.py   # System prompt builder
│   │   └── conversation.py    # Conversation history
│   └── extensions/
│       ├── agents_md.py       # AGENTS.md loader
│       └── skills.py          # Skills registry (stub)
└── resources/
    └── icons/
        └── freecad_ai.svg
```

## License

LGPL-2.1 — see [LICENSE](LICENSE).
