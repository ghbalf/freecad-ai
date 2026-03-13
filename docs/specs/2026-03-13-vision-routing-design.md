# Vision Routing — Design Spec

**Date**: 2026-03-13
**Status**: Approved

## Overview

Route image handling based on LLM vision capability. Vision-capable models receive images inline as content blocks (existing behavior). Non-vision models get images auto-described via an MCP `describe_image` tool, with the text description substituted into the message. When no vision path exists at all (no native vision, no MCP fallback), image UI controls are disabled.

## Vision Detection

### Probe via Test Connection

When the user clicks "Test Connection" in the settings dialog and the connection test succeeds, a second request is sent:

1. Generate a small image (64x32) with a random 3-digit number (100–999) rendered as text using `QPainter` on a `QImage`. This avoids false positives from non-vision LLMs guessing (~0.1% chance).
2. Send the image with the prompt: "What number is shown in this image? Reply with only the number."
3. The probe uses a dedicated `LLMClient.vision_probe()` method that builds provider-appropriate content blocks internally (OpenAI-style `image_url` vs Anthropic-style `image` source), similar to how `Conversation.get_messages_for_api()` formats images per `api_style`.
4. Check if the response contains the exact 3-digit number as a substring.
5. Set `vision_detected = True` or `False` accordingly.
6. Show result in test output: "Vision: supported" (green) or "Vision: not supported" (gray).

If the connection test itself fails, the vision probe is skipped and `vision_detected` is unchanged.

The probe runs **only** on explicit "Test Connection" clicks — never automatically on first message.

### MCP Fallback Discovery

After the probe, if `vision_detected == False`:

1. Search registered MCP tools for one with `describe_image` in the tool name (substring match).
2. If found, store its full registry name (e.g. `"llm-vision-mcp__describe_image"`) in `vision_fallback_tool`.
3. If not found, set `vision_fallback_tool = None`.

Since MCP servers connect lazily (on first message, not at startup), the fallback search also runs when MCP servers connect if `vision_fallback_tool` is still `None` and `supports_vision` is `False`.

## Config Changes

### New fields in `AppConfig`

```python
vision_detected: bool | None = None   # None=not tested, True/False=probe result
vision_override: bool | None = None   # user manual override, takes precedence
```

`vision_fallback_tool` is **not persisted** in config — it is a runtime-only attribute computed by searching the tool registry. MCP tool names are ephemeral (depend on which servers are running), so persisting them would produce stale references on next launch. It is stored as an instance attribute set after MCP connection or fallback search, defaulting to `None`.

### Derived property

```python
@property
def supports_vision(self) -> bool:
    if self.vision_override is not None:
        return self.vision_override
    if self.vision_detected is not None:
        return self.vision_detected
    return False  # unknown = assume no
```

### Reset logic

Resets are triggered in the settings dialog save handler (`_save_settings()`), which compares the new provider/model values against the previous ones before saving.

- **Provider or model changes**: `vision_detected` resets to `None`, `vision_fallback_tool` resets to `None`. `vision_override` is preserved (user's explicit choice persists across model changes).
- **MCP server config changes** (add/remove): `vision_fallback_tool` resets to `None` (re-searched on next connect).

## Image Interception

### In `Conversation`, before provider formatting

Interception happens in `Conversation.get_messages_for_api()`, before provider-specific formatting (OpenAI vs Anthropic content blocks). This method already iterates over messages and transforms content blocks per `api_style`. A new step is inserted at the beginning: if `supports_vision == False` and a message contains image blocks, those blocks are replaced with text blocks containing the MCP description.

The caller (LLM worker) passes a `describe_fn` callback to `get_messages_for_api()` when vision fallback is active. This keeps `Conversation` decoupled from MCP — it just calls the function with base64 data and gets text back. The worker constructs this callback from `registry.execute()`.

### Interception flow

When `config.supports_vision == False` and a message contains image content blocks:

1. Images are processed **serially** (one at a time) to avoid overwhelming the MCP vision server.
2. For each image block, call `describe_fn(base64_data)` which wraps `registry.execute(vision_fallback_tool, {"image": "<base64_data>", "prompt": "Describe this image in detail."})`.
3. Replace the image block with a text block: `[Image described by llm-vision-mcp: <description>]`.
4. Emit `vision_note` signal (signature: `str`) so the chat UI shows a subtle note per image.
5. Send the modified (text-only) message to the LLM.

### Error handling

If `registry.execute()` fails for an image (MCP server crash, timeout, tool error):
- The failed image is replaced with: `[Image: description unavailable — MCP error: <error message>]`.
- Processing continues for remaining images — partial failure does not abort the message.
- The chat UI shows the error note.

### Thread safety

The LLM worker already calls `registry.execute()` for MCP tools during the normal agentic loop (tool call dispatch crosses from worker thread to main thread via signal+slot). The same mechanism is used here — `describe_fn` is dispatched the same way. No additional thread-safety concerns.

### Behavior by scenario

| `supports_vision` | `vision_detected` | MCP fallback | Behavior | UI |
|---|---|---|---|---|
| `True` | any | any | Images sent inline as base64 content blocks | All image controls enabled |
| `False` | `False` | found | Images auto-described via MCP, sent as text | All enabled, note shown in chat |
| `False` | `False` | `None` | Images dropped with warning in chat | Capture, Attach, drag-drop, paste disabled |
| `False` | `None` (untested) | `None` | Same as above, but controls stay **enabled** (optimistic) | Hint shown once per session on first image use: "Tip: click Test Connection in Settings to enable vision auto-detection." |

The "untested" row is a special case: when `vision_detected is None`, image controls are **not** disabled even though `supports_vision` returns `False`. This avoids confusing new users who haven't run Test Connection yet. The gating logic checks `vision_detected is not None and not supports_vision and vision_fallback_tool is None` before disabling controls.

## UI Changes

### Settings Dialog — Behavior section

New row: **"Model supports vision"**

- Checkbox with a label showing detection state:
  - `☐ Model supports vision (not tested)` — `vision_detected` is `None`, no override
  - `☑ Model supports vision (auto-detected)` — probe returned `True`
  - `☐ Model supports vision (auto-detected)` — probe returned `False`
  - `☑ Model supports vision (manual override)` / `☐ ... (manual override)` — user toggled
- A "Reset" button appears when `vision_override is not None`, clears it back to auto-detected value.

### Chat Widget — image control gating

Gating condition: `vision_detected is not None and not config.supports_vision and vision_fallback_tool is None`.

When gating is active (no vision path available):

- **Capture button**: disabled, tooltip "No vision support — configure a vision MCP server or enable in Settings"
- **Attach button**: disabled, same tooltip
- **Drag-drop**: disabled (ignore drop events for images)
- **Paste-image**: disabled (ignore image mime data)

When `supports_vision == False` AND `vision_fallback_tool` exists:

- All image controls enabled — images will be auto-described via MCP.

When `supports_vision == True`:

- All image controls enabled — current behavior, images sent inline.

When `vision_detected is None` (untested):

- All image controls enabled (optimistic). On first image use per session, show a one-time hint: "Tip: click Test Connection in Settings to enable vision auto-detection." Tracked by a `_vision_hint_shown: bool` instance variable on `ChatDockWidget`.

### Refresh timing

Image control state refreshes:

- After settings are saved (chat widget reads updated config)
- After MCP servers connect (fallback tool may become available)

### New signal

`_LLMWorker` gets a new signal:

```python
vision_note = Signal(str)  # e.g. "Image auto-described by llm-vision-mcp"
```

Connected to a slot on `ChatDockWidget` that appends a subtle system-style note in the chat view.

## Files to Modify

| File | Change |
|---|---|
| `freecad_ai/config.py` | Add `vision_detected`, `vision_override` fields, `supports_vision` property |
| `freecad_ai/ui/settings_dialog.py` | Add vision checkbox + reset button to Behavior section, vision probe in test connection flow, reset logic in save handler |
| `freecad_ai/ui/chat_widget.py` | Gate Capture/Attach/drag-drop/paste on vision state, `vision_note` signal, hint for untested state, `_vision_hint_shown` flag, pass `describe_fn` to conversation |
| `freecad_ai/llm/client.py` | `vision_probe()` method — generates random 3-digit number image via QPainter, builds provider-appropriate content blocks, sends probe, verifies number in response, returns bool |
| `freecad_ai/mcp/manager.py` | `find_vision_fallback()` method — searches registry for `describe_image` tool |
| `freecad_ai/core/conversation.py` | Accept optional `describe_fn` in `get_messages_for_api()`, intercept image blocks before formatting |
| `tests/unit/test_vision_routing.py` | Unit tests for config logic, probe parsing, interception, UI gating, error handling |
