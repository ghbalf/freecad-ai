# Image Support for FreeCAD AI Chat

**Date:** 2026-03-10
**Status:** Approved

## Summary

Add image support to the FreeCAD AI chat: users can paste, drag-drop, or pick images from files, and the viewport can auto-capture screenshots. Images flow through the message pipeline as content blocks and are sent to LLM providers in their native format.

## Design Decisions

- **Content-block pipeline** — messages with images use `content: [{"type": "text", ...}, {"type": "image", ...}]` instead of a side-channel attachment system. Text-only messages keep plain string content for backward compatibility.
- **All providers** — images are sent to all providers in their expected format. No vision capability flag; API errors surface naturally if a model doesn't support images.
- **Auto-capture modes** — configurable: off, every message, after changes. Setting in preferences sets default, toolbar button overrides per-session.
- **Image resolution** — configurable: low (400x300), medium (800x600, default), high (1280x960). Images resized on attach, stored as base64 PNG.
- **Display** — inline 150px thumbnails in chat bubbles, click to enlarge in a dialog.

## Internal Message Format

Text-only (unchanged):
```python
{"role": "user", "content": "some text"}
```

With images:
```python
{"role": "user", "content": [
    {"type": "text", "text": "Can you fillet those edges?"},
    {"type": "image", "source": "base64", "media_type": "image/png", "data": "iVBOR..."}
]}
```

Rules:
- Text-only messages stay as plain strings (backward compatible with saved sessions)
- `add_user_message()` gains optional `images: list[dict]` parameter
- API formatters detect `isinstance(content, list)` and convert per provider

## Provider Image Formats

**Anthropic:**
```python
{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
```

**OpenAI / OpenRouter / Gemini:**
```python
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
```

**Ollama:**
```python
{"role": "user", "content": "text", "images": ["base64data..."]}
```

## Chat Input

Three input methods:

1. **Clipboard paste (Ctrl+V)** — override `insertFromMimeData()`, extract QImage if present
2. **File picker** — paperclip button, `QFileDialog` with image filter
3. **Drag and drop** — override `dragEnterEvent()`/`dropEvent()`, accept image types

Pending attachments shown as 48x48 thumbnails in a horizontal strip below the input, each with an "x" remove button. Strip clears on send.

All images resized to configured max resolution (preserving aspect ratio), converted to PNG base64 on attach.

## Viewport Auto-Capture

Config fields:
```python
viewport_capture: str = "off"     # "off" | "every_message" | "after_changes"
viewport_resolution: str = "medium"  # "low" | "medium" | "high"
```

- **every_message**: capture screenshot in `_send_message()`, prepend as image content block
- **after_changes**: after tool calls complete, if any tool modified the document, attach screenshot to next user message automatically

New utility `freecad_ai/utils/viewport.py` with `capture_viewport_image(w, h) -> bytes`. Existing `capture_viewport` tool refactored to use it.

Toolbar toggle: camera icon button, cycles off → every message → after changes → off. Session-only override.

## Message Display

- Content block lists detected in `render_message()`
- Image blocks rendered as `<img>` with base64 data URI, max 150px, border-radius 4px
- Wrapped in `<a href="image:N">` for click handling
- Click opens `QDialog` with `QLabel`+`QPixmap` at full resolution

## Files to Change

| File | Change |
|------|--------|
| `freecad_ai/core/conversation.py` | Content block format, `add_user_message(images=)`, API formatters |
| `freecad_ai/llm/client.py` | Provider-specific content block conversion |
| `freecad_ai/ui/chat_widget.py` | Paste/drop/file-picker, attachments strip, auto-capture, toolbar toggle |
| `freecad_ai/ui/message_view.py` | Image thumbnail rendering, click-to-enlarge dialog |
| `freecad_ai/utils/viewport.py` | **New** — `capture_viewport_image()` utility |
| `freecad_ai/tools/freecad_tools.py` | Refactor `capture_viewport` to use utility |
| `freecad_ai/core/config.py` | `viewport_capture`, `viewport_resolution` fields |
| `freecad_ai/ui/settings_dialog.py` | Viewport capture/resolution dropdowns |
| `tests/unit/test_conversation.py` | Content block format, API conversion with images |
| `tests/unit/test_viewport.py` | Image resize logic |
