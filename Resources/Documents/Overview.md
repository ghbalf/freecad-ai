# RioD AI

An AI-powered assistant workbench for RioD that creates and modifies
3D models from natural language descriptions.

## Features

- Chat interface with streaming LLM responses
- 50 structured tools for safe, undo-wrapped modeling operations
- Optional tool reranking (keyword or LLM-based) to filter tools sent to the LLM per turn
- Skills system with reusable `/commands` (enclosure, gear, fastener holes, sketch-from-image, etc.)
- MCP integration — connect external Model Context Protocol servers to extend available tools
- 20+ LLM providers: Anthropic, OpenAI, Ollama, Gemini, OpenRouter, and more
- Thinking mode for complex multi-step reasoning
- File attachments (images, text, PDFs via hooks)
- Zero external dependencies
- Plan-mode Check/Fix buttons for local-LLM users (sandbox validation + LLM code correction)
- Persistent dock layout (tabs, position, floating geometry) across sessions

## Requirements

- RioD (or FreeCAD 1.0+)
- An LLM provider (local Ollama, or a cloud API key)

## Getting Started

1. Clone or symlink this repository into `~/.local/share/FreeCAD/Mod/freecad-ai` (see README for OS-specific paths). The workbench isn't in the FreeCAD Addon Manager registry yet.
2. Switch to the **RioD AI** workbench
3. Open settings (gear icon) and configure your LLM provider
4. Start chatting — ask it to create geometry, modify parts, or explain modeling concepts
