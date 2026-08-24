# Contributing to FreeCAD AI

Thank you for your interest in contributing! This guide explains how to set up a development environment, submit changes, and create new skills.

## Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/freecad-ai.git
   cd freecad-ai
   ```
3. **Symlink** to FreeCAD's Mod directory:
   ```bash
   ln -s $(pwd) ~/.local/share/FreeCAD/Mod/freecad-ai
   ```
4. **Set up the dev environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install pytest
   ```
5. **Run tests:**
   ```bash
   .venv/bin/pytest tests/unit/ -v
   ```

## Submitting Changes

1. Create a **branch** for your change:
   ```bash
   git checkout -b my-feature
   ```
2. Make your changes and run tests
3. **Commit** with a descriptive message:
   ```bash
   git commit -m "feat: add support for ..."
   ```
4. **Push** to your fork:
   ```bash
   git push origin my-feature
   ```
5. Open a **Pull Request** on GitHub against `master`

### Pull Request Guidelines

- **Use a feature branch** — don't PR from your fork's `master`. Name it descriptively (e.g., `feat/dark-mode`, `fix/gpt5-params`, `contrib/active-document`).
- **Squash into clean commits** — one logical change per commit. Merge commits from your own fork add noise.
- **Write a PR description** — include a Summary (what and why), list of changes, and how you tested.
- **Keep scope focused** — one PR per feature or fix. Large PRs that touch many unrelated areas are harder to review.
- **Expect review feedback** — maintainers may request changes before merging. This is normal and collaborative.
- **Tests** — add unit tests for new code when possible. Run `pytest tests/unit/ -v` before submitting.

### Commit Message Convention

We use short prefixes for commit messages:

| Prefix | Use for |
|--------|---------|
| `feat:` | New features |
| `fix:` | Bug fixes |
| `refactor:` | Code changes that don't add features or fix bugs |
| `docs:` | Documentation only |
| `ui:` | UI changes |
| `revert:` | Reverting a previous change |

## What to Contribute

### Bug Fixes

Found a bug? Fix it and submit a PR. If you're not sure how to fix it, open an issue first.

### New Skills

Skills are the easiest way to contribute. Each skill is a directory under `skills/` with a `SKILL.md` file:

```
skills/
└── my-skill/
    ├── SKILL.md          # Step-by-step instructions for the LLM
    ├── VALIDATION.md     # (optional) Test cases for geometry validation
    └── handler.py        # (optional) Deterministic Python handler
```

The `SKILL.md` file contains instructions the LLM follows using tool calls. See existing skills (`enclosure`, `gear`, `fastener-hole`) for examples.

**Tips for good skills:**
- Use exact tool names and parameter values in the instructions
- Include a "Critical rules" section at the end
- Specify default values for all parameters
- Test with multiple LLM providers (tool-calling quality varies)

### Assets shipped by another workbench

A FreeCAD module can ship its own skills, tools and hooks instead of adding them
here. Put them in an `ai/` directory inside the module, and they are discovered
automatically once it is installed:

```
Mod/<YourModule>/ai/
├── skills/<name>/SKILL.md    # same format as skills/ in this repo
├── tools/*.py                # same format as user tools
├── hooks/<name>/hook.py      # same format as user hooks
└── INSTRUCTIONS.md           # (optional) appended to the system prompt
```

Precedence is built-in < module < user, so a user copy still overrides yours.
Set `__tool_prefix__ = "yourwb_"` at the top of a tool file to namespace its
tools instead of inheriting the default `user_` prefix. Point
`FREECAD_AI_ASSET_DIRS` at an `ai/` directory to test one without installing.

This is the right home for workbench-specific knowledge — it stays versioned and
tested next to the code it describes. See
`docs/superpowers/specs/2026-08-14-module-assets-design.md`.

### New Providers

Adding an LLM provider is a one-file change. Add an entry to `PROVIDERS` in `freecad_ai/llm/providers.py`:

```python
"my-provider": {
    "base_url": "https://api.example.com/v1",
    "default_model": "model-name",
    "api_style": "openai",      # "openai" or "anthropic"
    "supports_tools": True,
},
```

The provider automatically appears in the Settings dropdown.

### New Tools

Tools are Python functions registered in `freecad_ai/tools/freecad_tools.py`. Each tool has:
- A handler function (e.g., `_handle_create_something`)
- A `ToolDefinition` with name, description, parameters, and handler
- An entry in the `ALL_TOOLS` list

See [Creating Custom Tools](https://github.com/ghbalf/freecad-ai/wiki/Creating-Custom-Tools) for the user-tool convention, which also applies to built-in tools.

## Testing

```bash
# Unit tests (no FreeCAD needed)
.venv/bin/pytest tests/unit/ -v

# Integration tests (needs FreeCAD AppImage)
.venv/bin/pytest tests/integration/ -v -m integration
```

Unit tests should pass without FreeCAD installed. Integration tests require a FreeCAD AppImage and run actual FreeCAD operations.

**Add tests** for new features when possible. Test files go in `tests/unit/` and should start with `test_`.

## Code Style

- Python 3.11+, type hints encouraged
- No external dependencies (stdlib only: `urllib`, `json`, `ssl`, `threading`)
- PySide2/PySide6 compatibility via `freecad_ai/ui/compat.py` shim
- Keep FreeCAD API calls inside tool handlers (not in UI code)

## Questions?

Open an [issue](https://github.com/ghbalf/freecad-ai/issues) or start a [discussion](https://github.com/ghbalf/freecad-ai/discussions).
