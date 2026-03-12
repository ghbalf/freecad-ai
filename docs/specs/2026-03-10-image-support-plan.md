# Image Support — Implementation Plan

**Spec:** `2026-03-10-image-support-design.md`

## Step 1: Config fields

**File:** `freecad_ai/config.py`

Add to `AppConfig`:
```python
viewport_capture: str = "off"      # "off" | "every_message" | "after_changes"
viewport_resolution: str = "medium" # "low" | "medium" | "high"
```

The `from_dict()` method already filters unknown keys, so old configs load fine.

## Step 2: Viewport capture utility

**New file:** `freecad_ai/utils/viewport.py`

```python
def capture_viewport_image(width: int = 800, height: int = 600) -> bytes | None:
    """Capture current 3D viewport as PNG bytes. Returns None if no active view."""
```

- Uses `view.saveImage()` to a temp file, reads bytes, deletes temp file
- Also: `resize_image_bytes(data: bytes, max_width: int, max_height: int) -> bytes` using QImage
- Resolution presets: `RESOLUTION_PRESETS = {"low": (400, 300), "medium": (800, 600), "high": (1280, 960)}`
- Also: `image_to_base64_png(image_bytes: bytes) -> str` helper

**Refactor:** Update `freecad_tools.py` `_handle_capture_viewport` to call the utility instead of duplicating the logic.

**Tests:** `tests/unit/test_viewport_utils.py` — test `resize_image_bytes` with a small synthetic PNG (can create a 2x2 QImage in tests). Test resolution presets dict.

## Step 3: Conversation content blocks

**File:** `freecad_ai/core/conversation.py`

### 3a: `add_user_message()`

```python
def add_user_message(self, content: str, images: list[dict] | None = None):
    if images:
        blocks = [{"type": "text", "text": content}]
        blocks.extend(images)  # each: {"type": "image", "source": "base64", "media_type": "image/png", "data": "..."}
        self.messages.append({"role": "user", "content": blocks})
    else:
        self.messages.append({"role": "user", "content": content})
```

### 3b: `_to_openai_format()`

For user messages where `isinstance(content, list)`:
```python
oai_blocks = []
for block in content:
    if block["type"] == "text":
        oai_blocks.append({"type": "text", "text": block["text"]})
    elif block["type"] == "image":
        oai_blocks.append({"type": "image_url", "image_url": {"url": f"data:{block['media_type']};base64,{block['data']}"}})
result.append({"role": "user", "content": oai_blocks})
```

### 3c: `_to_anthropic_format()`

For user messages where `isinstance(content, list)`:
```python
anth_blocks = []
for block in content:
    if block["type"] == "text":
        anth_blocks.append({"type": "text", "text": block["text"]})
    elif block["type"] == "image":
        anth_blocks.append({"type": "image", "source": {"type": "base64", "media_type": block["media_type"], "data": block["data"]}})
result.append({"role": "user", "content": anth_blocks})
```

### 3d: Ollama special case

In `_to_openai_format()`, detect Ollama messages (need to pass `provider_name` or add a method parameter). For Ollama, images go in a separate `images` field:
```python
{"role": "user", "content": "text", "images": ["base64..."]}
```

Option: add `provider_name` parameter to `get_messages_for_api()` and pass it through to format methods. Currently it uses `api_style` — extend this to also carry `provider_name` for the Ollama case. Or: handle in `client.py` at send time by post-processing messages.

**Decision:** Post-process in `client.py` `_openai_body()` — simpler, keeps conversation.py clean. Check `self.provider_name == "ollama"` and convert content blocks to Ollama format before sending.

### 3e: `estimated_tokens()`

Update to handle list content:
```python
if isinstance(content, list):
    for block in content:
        if block.get("type") == "text":
            total_chars += len(block.get("text", ""))
        elif block.get("type") == "image":
            total_chars += 1000  # rough estimate for image tokens
```

### 3f: Compaction

Update `_load_chat` preview extraction and compaction summary builder to handle content blocks (extract text from lists, skip images).

**Tests:** `tests/unit/test_conversation.py` — add tests for:
- `add_user_message` with images creates content blocks
- `add_user_message` without images stays as string
- `_to_openai_format` converts image blocks correctly
- `_to_anthropic_format` converts image blocks correctly
- `estimated_tokens` with image blocks
- Loading old sessions (string content) still works

## Step 4: LLM client Ollama post-processing

**File:** `freecad_ai/llm/client.py`

In `_openai_body()`, after `msgs.extend(messages)`, if `self.provider_name == "ollama"`:
- Walk through `msgs` and convert any message with content block lists to Ollama format:
  ```python
  for msg in msgs:
      if isinstance(msg.get("content"), list):
          text_parts = []
          images = []
          for block in msg["content"]:
              if block["type"] == "text":
                  text_parts.append(block["text"])
              elif block["type"] == "image":
                  images.append(block["data"])
              elif block["type"] == "image_url":
                  # extract base64 from data URI
                  ...
          msg["content"] = "\n".join(text_parts)
          if images:
              msg["images"] = images
  ```

No changes needed for Anthropic or OpenAI — they natively support content block arrays.

## Step 5: Message display — image thumbnails

**File:** `freecad_ai/ui/message_view.py`

### 5a: Update `render_message()`

Accept content as either `str` or `list`. If list, render each block:
```python
def render_message(role: str, content) -> str:
    if isinstance(content, list):
        formatted_content = _format_content_blocks(content)
    else:
        formatted_content = _format_content(content)
    ...
```

### 5b: New `_format_content_blocks()`

```python
def _format_content_blocks(blocks: list) -> str:
    parts = []
    for i, block in enumerate(blocks):
        if block.get("type") == "text":
            parts.append(_format_content(block["text"]))
        elif block.get("type") == "image":
            data_uri = f"data:{block['media_type']};base64,{block['data']}"
            parts.append(
                f'<a href="image:{i}">'
                f'<img src="{data_uri}" '
                f'style="max-width:150px; max-height:150px; border-radius:4px; cursor:pointer;" '
                f'title="Click to enlarge" />'
                f'</a>'
            )
    return "".join(parts)
```

### 5c: Image enlarge dialog

**File:** `freecad_ai/ui/chat_widget.py`

In `_handle_anchor_click()`, handle `image:N` URLs:
```python
def _handle_anchor_click(self, url):
    url_str = url.toString()
    if url_str.startswith("image:"):
        self._show_image_dialog(url_str)
        return
    ...
```

New method `_show_image_dialog()`:
- Extract image index from URL
- Find the message content block with that index
- Decode base64 to QPixmap
- Show in a simple QDialog with QLabel

## Step 6: Chat input — image attachment

**File:** `freecad_ai/ui/chat_widget.py`

### 6a: Pending attachments state

Add to `__init__`:
```python
self._pending_images = []  # list of {"media_type": "image/png", "data": "base64..."}
```

### 6b: Custom input widget

Replace `QTextEdit` with a subclass `_ImageAwareTextEdit(QTextEdit)` that overrides:
- `insertFromMimeData(source)` — if `source.hasImage()`, extract QImage, resize, convert to PNG base64, emit signal
- `dragEnterEvent(event)` — accept image drops
- `dropEvent(event)` — handle dropped images/files

Signals: `image_pasted = Signal(str, str)  # (media_type, base64_data)`

### 6c: Attachment strip widget

New class `_AttachmentStrip(QWidget)`:
- Horizontal layout with 48x48 thumbnail labels
- Each has an "x" button overlay
- `add_image(media_type, base64_data)` and `clear()` methods
- Signal: `image_removed = Signal(int)`

Place between input and chat display.

### 6d: File picker button

Add a paperclip button next to send button:
```python
attach_btn = QPushButton("📎")  # or use icon
attach_btn.clicked.connect(self._attach_image)
```

`_attach_image()` opens `QFileDialog.getOpenFileName` with image filter, reads file, resizes, converts to base64, adds to strip.

### 6e: Update `_send_message()`

Before `self.conversation.add_user_message(text)`:
```python
images = list(self._pending_images) if self._pending_images else None
self.conversation.add_user_message(text, images=images)
self._append_html(render_message("user", self.conversation.messages[-1]["content"]))
self._attachment_strip.clear()
self._pending_images.clear()
```

## Step 7: Viewport auto-capture

**File:** `freecad_ai/ui/chat_widget.py`

### 7a: Toolbar toggle button

Add camera button in header bar:
```python
self._capture_btn = QPushButton("📷")
self._capture_btn.setCheckable(False)
self._capture_btn.setToolTip("Viewport capture: off")
self._capture_btn.clicked.connect(self._cycle_capture_mode)
```

`_cycle_capture_mode()` cycles through off → every_message → after_changes → off, updates tooltip.

Store session override: `self._capture_mode_override = None`

### 7b: Every-message capture

In `_send_message()`, after building text but before adding to conversation:
```python
capture_mode = self._capture_mode_override or get_config().viewport_capture
if capture_mode == "every_message":
    img = self._capture_viewport_for_chat()
    if img:
        images = (images or []) + [img]
```

### 7c: After-changes capture

In `_on_response_finished()`, if mode is "after_changes" and worker had tool calls that modified the document:
- Capture screenshot
- Store it so next `_send_message()` auto-prepends it

Track: `self._pending_viewport_image = None`

### 7d: Capture helper

```python
def _capture_viewport_for_chat(self) -> dict | None:
    from ..utils.viewport import capture_viewport_image, image_to_base64_png, RESOLUTION_PRESETS
    cfg = get_config()
    w, h = RESOLUTION_PRESETS.get(cfg.viewport_resolution, (800, 600))
    img_bytes = capture_viewport_image(w, h)
    if img_bytes:
        return {"type": "image", "source": "base64", "media_type": "image/png", "data": image_to_base64_png(img_bytes)}
    return None
```

## Step 8: Settings UI

**File:** `freecad_ai/ui/settings_dialog.py`

Add "Viewport" group:
- Dropdown: "Auto-capture mode" — Off / Every Message / After Changes
- Dropdown: "Capture resolution" — Low (400x300) / Medium (800x600) / High (1280x960)

Wire to `viewport_capture` and `viewport_resolution` config fields.

## Step 9: Tests

New and updated test files:
- `tests/unit/test_viewport_utils.py` — resize, base64 conversion, presets
- `tests/unit/test_conversation.py` — content blocks, API format conversion
- `tests/unit/test_message_view.py` — rendering with image blocks

## Build sequence

1. Config fields (Step 1) — no dependencies
2. Viewport utility (Step 2) — depends on step 1 for presets
3. Conversation content blocks (Step 3) — core data model change
4. LLM client Ollama handling (Step 4) — depends on step 3
5. Message display (Step 5) — depends on step 3
6. Chat input (Step 6) — depends on steps 2, 3, 5
7. Auto-capture (Step 7) — depends on steps 2, 6
8. Settings UI (Step 8) — depends on step 1
9. Tests throughout each step
