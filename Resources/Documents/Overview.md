# FreeCAD AI

An AI-powered assistant workbench for FreeCAD that creates and modifies
3D models from natural language descriptions.

## Features

- Chat interface with streaming LLM responses
- 21 structured tools for safe, undo-wrapped modeling operations
- Skills system with reusable `/commands` (enclosure, gear, fastener holes, etc.)
- Multiple LLM providers: Anthropic, OpenAI, Ollama, Gemini, OpenRouter
- Thinking mode for complex multi-step reasoning
- Zero external dependencies

## Requirements

- FreeCAD 1.0+
- An LLM provider (local Ollama, or a cloud API key)

## Getting Started

1. Install via the Addon Manager or clone into `~/.local/share/FreeCAD/Mod/`
2. Switch to the **FreeCAD AI** workbench
3. Open settings (gear icon) and configure your LLM provider
4. Start chatting — ask it to create geometry, modify parts, or explain FreeCAD concepts
