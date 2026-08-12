"""Main chat dock widget for FreeCAD AI.

Provides the primary user interface: a scrollable chat history,
input field, mode toggle (Plan/Act), and settings access.

LLM calls run in a QThread to keep the UI responsive, with
streaming text pushed via signals. When tools are enabled,
the worker implements an agentic loop: stream response, execute
tool calls on the main thread, feed results back to the LLM.
"""

import json
import time

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate

QDockWidget = QtWidgets.QDockWidget
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextBrowser = QtWidgets.QTextBrowser
QTextEdit = QtWidgets.QTextEdit
QPushButton = QtWidgets.QPushButton
QComboBox = QtWidgets.QComboBox
QLabel = QtWidgets.QLabel
QApplication = QtWidgets.QApplication
Qt = QtCore.Qt
Signal = QtCore.Signal
QThread = QtCore.QThread
Slot = QtCore.Slot
QFont = QtGui.QFont
QTextCursor = QtGui.QTextCursor

from ..config import LOGS_DIR, get_config, prune_oldest_files, save_current_config
from ..core.conversation import Conversation
from ..core.executor import extract_code_blocks, extract_truncated_block, execute_code
from ..core.loop_control import resolve_turn_outcome, should_continue_loop
from ..core.input_history import InputHistory
from .message_view import (
    _get_theme_colors,
    get_chat_display_stylesheet,
    get_freecad_mode_name,
    refresh_theme_cache,
    render_message,
    render_code_block,
    render_execution_result,
    render_plan_buttons,
    render_tool_call,
    render_truncation_warning,
)
from .code_review_dialog import CodeReviewDialog


# Known binary file magic bytes — prevents misdetecting binary files as text
_BINARY_MAGIC = (
    b"%PDF",          # PDF
    b"PK\x03\x04",    # ZIP, DOCX, XLSX, PPTX, ODT, JAR
    b"PK\x05\x06",    # ZIP (empty archive)
    b"\x89PNG",        # PNG
    b"\xff\xd8\xff",   # JPEG
    b"GIF8",           # GIF
    b"RIFF",           # WEBP, AVI, WAV
    b"\x7fELF",        # ELF binary
    b"\xd0\xcf\x11",   # MS Office legacy (DOC, XLS, PPT)
    b"\x1f\x8b",       # gzip
    b"BZ",             # bzip2
    b"\xfd7zXZ",       # xz
    b"Rar!",           # RAR
    b"\x00\x00\x01\x00",  # ICO
    b"\x00asm",        # WebAssembly
)

# Themes that ship a global QPushButton stylesheet which overrides
# padding/margins and clips the labels of buttons in this dock.
_STYLESHEET_CONFLICT_THEMES = frozenset({"opendark", "openlight"})

# Color rules per viewport-capture mode (applied to _capture_btn).
_CAPTURE_MODE_COLORS = {
    "off": "",
    "every_message": "font-weight: bold; color: #4fc3f7;",  # light blue
    "after_changes": "font-weight: bold; color: #aed581;",  # light green
}


def _is_binary_content(data: bytes) -> bool:
    """Detect binary content by magic bytes and null-byte presence."""
    header = data[:8]
    for magic in _BINARY_MAGIC:
        if header[:len(magic)] == magic:
            return True
    if b"\x00" in data[:8192]:
        return True
    return False


def _build_rerank_llm_client(cfg):
    """Construct the LLMClient used for LLM-based reranking.

    Each override field is inherited from the main provider when empty,
    so the common case (same provider, maybe a different model) is a
    one-field change. Full override (different provider entirely) works
    too, for e.g. running reranking on a local Ollama model while the
    main chat uses a cloud provider.

    Model params come from one of two disjoint namespaces, so the reranker
    can never overwrite the main model's params (issue #30):
      - Inherited model (no override) → the main model's params from
        ``cfg.model_params`` (handles provider quirks like Moonshot's locked
        ``temperature=1``)
      - Override model → the reranker's own ``cfg.rerank_params`` (important
        for small Ollama models that need ``num_predict`` / ``top_k`` /
        ``repeat_penalty`` etc.)
    """
    from ..llm.client import LLMClient
    provider_name = cfg.rerank_llm_provider_name or cfg.provider.name
    base_url = cfg.rerank_llm_base_url or cfg.provider.base_url
    api_key = cfg.rerank_llm_api_key or cfg.provider.api_key
    model = cfg.rerank_llm_model or cfg.provider.model

    if cfg.rerank_llm_model:  # override → reranker's own param namespace
        model_params = dict(cfg.rerank_params)
    else:  # inherit → use the main model's params
        model_params = dict(cfg.model_params.get(model, {}))

    return LLMClient(
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=1024,
        temperature=model_params.get("temperature", 0.0),
        thinking="off",
        model_params=model_params,
    )


def _freecad_log(msg: str):
    """Print a line to FreeCAD's Report View, if FreeCAD is available."""
    try:
        import FreeCAD as _App
        _App.Console.PrintMessage("[FreeCAD AI] {}\n".format(msg))
    except Exception:
        pass


def _run_reranker(cfg, pairs, user_text):
    """Dispatch to the configured reranker method.

    Returns a list of tool names to include. LLM method falls back to
    keyword on any failure (handled inside ``rerank_tools_llm``).
    """
    from ..tools.reranker import rerank_tools, rerank_tools_llm
    if cfg.rerank_method == "llm":
        try:
            client = _build_rerank_llm_client(cfg)
        except Exception as e:
            _freecad_log("LLM reranker: cannot build client ({}); using keyword".format(e))
            return rerank_tools(
                pairs, user_text,
                top_n=cfg.rerank_top_n,
                pinned=cfg.rerank_pinned_tools,
            )
        return rerank_tools_llm(
            pairs, user_text,
            top_n=cfg.rerank_top_n,
            pinned=cfg.rerank_pinned_tools,
            llm_client=client,
            report=_freecad_log,
        )
    return rerank_tools(
        pairs, user_text,
        top_n=cfg.rerank_top_n,
        pinned=cfg.rerank_pinned_tools,
    )


def _extract_latest_user_text(conversation) -> str:
    """Return the text of the most recent user-authored message.

    Skips "[System] ..." synthetic messages injected by the framework —
    those contain tool/execution chatter, not user intent.
    Handles both plain string content and the block-list form used when
    images or documents are attached.
    """
    for msg in reversed(conversation.messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content.startswith("[System] "):
                continue
            return content
        if isinstance(content, list):
            parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p).strip()
            if joined and not joined.startswith("[System] "):
                return joined
    return ""


# ── LLM Worker Thread ───────────────────────────────────────

class _LLMWorker(QThread):
    """Background thread that streams LLM responses with optional tool loop.

    When tools are provided, implements an agentic loop:
      1. Stream LLM response, collecting text + tool calls
      2. If no tool calls -> done
      3. For each tool call, dispatch to main thread and wait for result
      4. Append results to messages, loop back to step 1
    """

    token_received = Signal(str)           # Text delta
    thinking_received = Signal(str)        # Thinking/reasoning delta
    response_finished = Signal(str)        # Full response text (final turn only)
    error_occurred = Signal(str)           # Error message
    tool_call_started = Signal(str, str)   # (tool_name, call_id)
    tool_call_finished = Signal(str, str, bool, str)  # (tool_name, call_id, success, output)
    tool_exec_requested = Signal(str, str) # (tool_name, arguments_json) — dispatches to main thread
    vision_note = Signal(str)              # Vision description status note

    def __init__(self, messages, system_prompt, tools=None, registry=None,
                 api_style="openai", conversation=None, describe_fn=None, parent=None):
        super().__init__(parent)
        self.messages = list(messages)
        self.system_prompt = system_prompt
        self.tools = tools
        self.registry = registry
        self.api_style = api_style
        self.conversation = conversation
        self.describe_fn = describe_fn
        self._full_response = ""
        self._thinking_text = ""
        self._tool_results = []
        self._tool_result_ready = QtCore.QMutex()
        self._tool_result_wait = QtCore.QWaitCondition()
        self._pending_result = None
        self._max_tool_turns = get_config().max_tool_turns  # 0 = endless
        self._strip_thinking = False  # resolved in run()
        self._tool_timeline = []  # timing data for summary visualization
        self._response_truncated = False  # response hit the output-token limit

    def run(self):
        try:
            from ..llm.client import create_client_from_config, should_strip_thinking
            from ..config import get_config as _get_config
            client = create_client_from_config()
            self._strip_thinking = should_strip_thinking(
                client.model, _get_config().strip_thinking_history)

            # Re-format messages with image interception on worker thread
            if self.conversation and self.describe_fn:
                wrapped = self._wrap_describe_fn(self.describe_fn)
                self.messages = self.conversation.get_messages_for_api(
                    api_style=self.api_style, describe_fn=wrapped,
                    strip_thinking=self._strip_thinking,
                )

            if not self.tools:
                # Simple non-tool streaming (backward compat)
                self._simple_stream(client)
                return

            # Agentic tool loop
            self._tool_loop(client)

        except Exception as e:
            self.error_occurred.emit(str(e))

    def _wrap_describe_fn(self, describe_fn):
        """Wrap describe_fn to emit vision_note signals."""
        def wrapped(b64_data):
            try:
                result = describe_fn(b64_data)
                self.vision_note.emit("Image auto-described by llm-vision-mcp")
                return result
            except Exception as e:
                self.vision_note.emit(f"Image description failed: {e}")
                raise
        return wrapped

    def _simple_stream(self, client):
        """Stream without tools (original behavior)."""
        for chunk in client.stream(self.messages, system=self.system_prompt):
            if self.isInterruptionRequested():
                break
            self._full_response += chunk
            self.token_received.emit(chunk)
        self._response_truncated = client.response_truncated
        self.response_finished.emit(self._full_response)

    def _tool_loop(self, client):
        """Agentic loop: stream -> execute tools -> feed results -> repeat."""
        messages = list(self.messages)

        turn = 0
        while should_continue_loop(self._max_tool_turns, turn, self.isInterruptionRequested()):
            text_parts = []
            thinking_parts = []
            tool_calls = []

            # Stream with tools
            for event in client.stream_with_tools(
                messages, system=self.system_prompt, tools=self.tools
            ):
                if self.isInterruptionRequested():
                    break
                if event.type == "text_delta":
                    text_parts.append(event.text)
                    self._full_response += event.text
                    self.token_received.emit(event.text)
                elif event.type == "thinking_delta":
                    thinking_parts.append(event.text)
                    self._thinking_text += event.text
                    self.thinking_received.emit(event.text)
                elif event.type == "tool_call_start":
                    if event.tool_call:
                        self.tool_call_started.emit(event.tool_call.name, event.tool_call.id)
                elif event.type == "tool_call_end":
                    if event.tool_call:
                        tool_calls.append(event.tool_call)
                elif event.type == "done":
                    break

            turn_text = "".join(text_parts)
            turn_thinking = "".join(thinking_parts)

            outcome = resolve_turn_outcome(
                client.response_truncated, tool_calls, self.isInterruptionRequested())
            if outcome == "stopped":
                self._full_response += "\n\n_⏹ Stopped by user._"
                self.response_finished.emit(self._full_response)
                return
            if outcome == "truncated":
                # Cut off at the output limit. Any tool calls in this turn came
                # from a half-formed payload, so the loop halts here instead of
                # acting on them; the UI shows the truncation warning (#52).
                self._response_truncated = True
                self.response_finished.emit(self._full_response)
                return
            if outcome == "done":
                self.response_finished.emit(self._full_response)
                return

            # Store the assistant message with tool calls in the conversation
            tc_dicts = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in tool_calls
            ]

            # Add assistant message to local messages for next turn
            if self.api_style == "anthropic":
                content_blocks = []
                if turn_text:
                    content_blocks.append({"type": "text", "text": turn_text})
                for tc in tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                messages.append({"role": "assistant", "content": content_blocks})
            else:
                oai_tcs = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
                assistant_msg = {
                    "role": "assistant",
                    "content": turn_text or None,
                    "tool_calls": oai_tcs,
                }
                # Preserve reasoning_content unless the model wants it stripped
                # (e.g. Gemma strips thinking; Kimi-K2.5 requires it)
                if turn_thinking and not self._strip_thinking:
                    assistant_msg["reasoning_content"] = turn_thinking
                messages.append(assistant_msg)

            # Execute each tool call on the main thread
            # Exception: optimize_iteration runs on worker thread (long-running
            # LLM calls would freeze the UI if dispatched to main thread).
            # Its inner tool calls dispatch to main thread via QtMainThreadToolExecutor.
            tool_result_messages = []
            for tc in tool_calls:
                # Pre-tool-use hook
                from ..hooks import fire_hook as _fire_hook
                hook_result = _fire_hook("pre_tool_use", {
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                    "turn": turn,
                })
                t0 = time.time()
                if hook_result.get("block"):
                    result = {"success": False, "output": "",
                              "error": f"Blocked by hook: {hook_result.get('reason', '')}"}
                elif tc.name == "optimize_iteration" and self.registry:
                    tr = self.registry.execute(tc.name, tc.arguments)
                    result = {"success": tr.success, "output": tr.output, "error": tr.error}
                else:
                    result = self._execute_tool_on_main_thread(tc.name, tc.arguments)
                elapsed = time.time() - t0
                success = result.get("success", False)
                output = result.get("output", "")
                error = result.get("error", "")
                result_text = output if success else f"Error: {error}"

                # Track timing for summary
                self._tool_timeline.append({
                    "name": tc.name, "success": success,
                    "elapsed": elapsed, "turn": turn,
                })

                self.tool_call_finished.emit(tc.name, tc.id, success, result_text)

                # Post-tool-use hook
                _fire_hook("post_tool_use", {
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                    "success": success,
                    "output": output,
                    "error": error,
                    "turn": turn,
                })

                if self.api_style == "anthropic":
                    tool_result_messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": result_text,
                            }
                        ],
                    })
                else:
                    tool_result_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })

            messages.extend(tool_result_messages)

            # Store tool call info so the parent can update the conversation
            self._tool_results.append({
                "assistant_text": turn_text,
                "tool_calls": tc_dicts,
                "results": [
                    {"tool_call_id": tc.id, "content": r["content"] if self.api_style != "anthropic" else r["content"][0]["content"]}
                    for tc, r in zip(tool_calls, tool_result_messages)
                ],
            })
            turn += 1

        # If a tool was interrupted mid-wait, the user already saw a tool-failure
        # bubble; this chat-level note is an intentional, clearer second signal.
        if self.isInterruptionRequested():
            self._full_response += "\n\n_⏹ Stopped by user._"
            self.response_finished.emit(self._full_response)
            return

        # If we reach here, we hit the max turns limit
        limit_msg = "\n\n[{}]".format(
            translate("ChatDockWidget", "Reached maximum tool call iterations"))
        self._full_response += limit_msg
        self.token_received.emit(limit_msg)
        self.response_finished.emit(self._full_response)

    def _execute_tool_on_main_thread(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch tool execution to the main thread and wait for the result.

        Emits tool_exec_requested signal (runs slot on main thread via
        Qt.QueuedConnection), then blocks on a mutex until the main thread
        calls set_tool_result().
        """
        self._pending_result = None
        self.tool_exec_requested.emit(tool_name, json.dumps(arguments))

        self._tool_result_ready.lock()
        elapsed = 0
        deadline = 300000  # ms (5 min) — backstop against a hung/crashed main thread
        while self._pending_result is None:
            if self.isInterruptionRequested():
                self._tool_result_ready.unlock()
                return {"success": False, "output": "", "error": "Stopped by user"}
            if elapsed >= deadline:
                self._tool_result_ready.unlock()
                return {"success": False, "output": "", "error": "Tool execution timed out (main thread did not respond)"}
            # Wake every 250 ms so a Stop request is noticed promptly while the
            # cumulative deadline still guards against a hung main thread.
            self._tool_result_wait.wait(self._tool_result_ready, 250)
            elapsed += 250
        self._tool_result_ready.unlock()

        return self._pending_result

    def set_tool_result(self, result: dict):
        """Called from the main thread to provide a tool execution result."""
        self._tool_result_ready.lock()
        self._pending_result = result
        self._tool_result_wait.wakeAll()
        self._tool_result_ready.unlock()


class _CompactionWorker(QThread):
    """Background thread that summarizes older messages for context compaction."""
    finished = Signal(str)  # summary text

    def __init__(self, conversation_text, parent=None):
        super().__init__(parent)
        self.conversation_text = conversation_text

    def run(self):
        try:
            from ..llm.client import create_client_from_config
            client = create_client_from_config()

            messages = [
                {
                    "role": "user",
                    "content": (
                        "Summarize the following conversation concisely. "
                        "Focus on: what the user asked for, what was created/modified "
                        "(object names, dimensions, operations), any errors encountered "
                        "and how they were resolved, and the current state of the project. "
                        "Keep technical details (names, numbers, tool calls) that would be "
                        "needed to continue the conversation.\n\n"
                        "CONVERSATION:\n" + self.conversation_text
                    ),
                }
            ]
            summary = client.send(
                messages,
                system="You are a conversation summarizer. Be concise but preserve key technical details."
            )
            self.finished.emit(summary)
        except Exception as e:
            # On failure, emit empty string (compaction will be skipped)
            self.finished.emit("")


# ── Image-aware input widgets ──────────────────────────────

class _ImageAwareTextEdit(QTextEdit):
    """Text input that accepts pasted/dropped images."""

    image_added = Signal(str, str)  # (media_type, base64_data)
    document_added = Signal(str, str)  # (filename, text_content)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images_enabled = True

    def set_images_enabled(self, enabled: bool):
        """Enable or disable image paste and drag-drop."""
        self._images_enabled = enabled
        self.setAcceptDrops(enabled)

    def insertFromMimeData(self, source):
        """Handle paste — extract image or text file if present."""
        if source.hasImage() and self._images_enabled:
            self._process_image_from_mime(source)
        elif source.hasUrls():
            for url in source.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                if self._is_image_file(path) and self._images_enabled:
                    self._process_image_file(path)
                    return
                # Try any non-image file as text
                if self._process_text_file(path):
                    return
            super().insertFromMimeData(source)
        else:
            super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasImage() and self._images_enabled:
            self._process_image_from_mime(mime)
        elif mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                if self._is_image_file(path) and self._images_enabled:
                    self._process_image_file(path)
                    return
                # Try any non-image file as text (detect by reading)
                if self._process_text_file(path):
                    return
            # Not handled (binary file etc.) — forward to ChatDockWidget
            parent = self.parent()
            while parent and not isinstance(parent, ChatDockWidget):
                parent = parent.parent()
            if parent:
                parent.dropEvent(event)
        else:
            super().dropEvent(event)

    def _process_image_from_mime(self, source):
        """Extract QImage from mime data, resize, and emit."""
        if not self._images_enabled:
            return
        img = source.imageData()
        if img is None or img.isNull():
            return
        from ..utils.viewport import resize_image_bytes, image_to_base64_png, RESOLUTION_PRESETS
        from ..config import get_config
        w, h = RESOLUTION_PRESETS.get(get_config().viewport_resolution, (800, 600))
        # Convert QImage to bytes
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        img.save(buf, "PNG")
        raw = bytes(buf.data())
        resized = resize_image_bytes(raw, w, h)
        self.image_added.emit("image/png", image_to_base64_png(resized))

    def _process_image_file(self, path: str):
        """Read an image file, resize, and emit."""
        if not self._images_enabled:
            return
        from ..utils.viewport import resize_image_bytes, image_to_base64_png, RESOLUTION_PRESETS
        from ..config import get_config
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            return
        w, h = RESOLUTION_PRESETS.get(get_config().viewport_resolution, (800, 600))
        resized = resize_image_bytes(raw, w, h)
        self.image_added.emit("image/png", image_to_base64_png(resized))

    @staticmethod
    def _is_image_file(path: str) -> bool:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return ext in ("png", "jpg", "jpeg", "bmp", "gif", "webp")

    @staticmethod
    def _is_text_file(path: str) -> bool:
        import os
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        name = os.path.basename(path).lower()
        return ext in ChatDockWidget._TEXT_EXTENSIONS or name in ("makefile", "dockerfile")

    def _process_text_file(self, path: str) -> bool:
        """Try to read a file as text and emit document_added signal.

        Rejects known binary formats (by magic bytes) and files
        containing null bytes. Returns True if successfully read.
        """
        import os
        try:
            size = os.path.getsize(path)
            if size > 512_000:
                return False
            with open(path, "rb") as f:
                raw = f.read()
            if _is_binary_content(raw):
                return False
            text = raw.decode("utf-8", errors="replace")
            self.document_added.emit(os.path.basename(path), text)
            return True
        except OSError:
            return False


class _AttachmentStrip(QtWidgets.QWidget):
    """Horizontal strip of attachment previews (image thumbnails and document chips)."""

    image_removed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(False)  # Drops handled by ChatDockWidget
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch()
        # Each item: (widget, kind, data_dict)
        #   kind="image" → data_dict = {"media_type": str, "data": str}
        #   kind="document" → data_dict = {"filename": str, "text": str}
        self._items: list[tuple[QtWidgets.QWidget, str, dict]] = []
        self.hide()

    def add_image(self, media_type: str, base64_data: str):
        """Add an image thumbnail to the strip."""
        import base64 as b64

        container = QtWidgets.QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Thumbnail
        label = QLabel()
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(b64.b64decode(base64_data))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(pixmap)
        colors = _get_theme_colors()
        label.setStyleSheet(f"border: 1px solid {colors['chat_border']}; border-radius: 3px;")
        container_layout.addWidget(label)

        # Remove button
        remove_btn = QPushButton("x")
        remove_btn.setMaximumSize(16, 16)
        remove_btn.setStyleSheet(f"font-size: 10px; padding: 0; border: none; color: {colors['tool_error_text']};")
        idx = len(self._items)
        remove_btn.clicked.connect(lambda checked=False, i=idx: self._remove(i))
        container_layout.addWidget(remove_btn, alignment=Qt.AlignCenter)

        # Insert before the stretch
        self._layout.insertWidget(self._layout.count() - 1, container)
        self._items.append((container, "image", {"media_type": media_type, "data": base64_data}))
        self.show()

    def add_document(self, filename: str, text: str):
        """Add a document chip (filename badge) to the strip."""
        container = QtWidgets.QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(4, 2, 4, 2)
        container_layout.setSpacing(4)

        colors = _get_theme_colors()

        # Filename label with truncation
        display_name = filename if len(filename) <= 24 else filename[:10] + "..." + filename[-10:]
        label = QLabel(display_name)
        label.setToolTip(filename)
        label.setStyleSheet(
            f"font-size: 10px; color: {colors['chat_text']}; "
            f"background: {colors['chat_bg']}; "
            f"border: 1px solid {colors['chat_border']}; "
            f"border-radius: 3px; padding: 2px 6px;"
        )
        container_layout.addWidget(label)

        # Remove button
        remove_btn = QPushButton("x")
        remove_btn.setMaximumSize(16, 16)
        remove_btn.setStyleSheet(f"font-size: 10px; padding: 0; border: none; color: {colors['tool_error_text']};")
        idx = len(self._items)
        remove_btn.clicked.connect(lambda checked=False, i=idx: self._remove(i))
        container_layout.addWidget(remove_btn)

        self._layout.insertWidget(self._layout.count() - 1, container)
        self._items.append((container, "document", {"filename": filename, "text": text}))
        self.show()

    def get_images(self) -> list[dict]:
        """Return list of image content block dicts."""
        return [
            {"type": "image", "source": "base64", "media_type": d["media_type"], "data": d["data"]}
            for _, kind, d in self._items if kind == "image"
        ]

    def get_documents(self) -> list[dict]:
        """Return list of document attachment dicts."""
        return [
            {"filename": d["filename"], "text": d["text"]}
            for _, kind, d in self._items if kind == "document"
        ]

    def clear(self):
        """Remove all attachments."""
        for widget, _, _ in self._items:
            widget.deleteLater()
        self._items.clear()
        self.hide()

    def _remove(self, idx: int):
        if 0 <= idx < len(self._items):
            widget, _, _ = self._items.pop(idx)
            widget.deleteLater()
            self.image_removed.emit(idx)
            # Re-bind remaining remove buttons
            for new_idx, (w, _, _) in enumerate(self._items):
                btn = w.findChild(QPushButton)
                if btn:
                    btn.clicked.disconnect()
                    btn.clicked.connect(lambda checked=False, i=new_idx: self._remove(i))
            if not self._items:
                self.hide()


# ── Chat Dock Widget ────────────────────────────────────────

class ChatDockWidget(QDockWidget):
    """Main chat dock widget for FreeCAD AI."""

    def __init__(self, parent=None):
        super().__init__(translate("ChatDockWidget", "FreeCAD AI"), parent)
        self.setObjectName("FreeCADAIChatDock")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self.conversation = Conversation()
        self._worker = None
        self._input_history = InputHistory()
        self._suppress_history_reset = False  # set True around programmatic
                                              # _set_input_text() to guard a
                                              # future textChanged-based reset
        self._streaming_html = ""
        self._retry_count = 0
        self._anchor_connected = False
        self._tool_registry = None
        self._in_thinking = False  # Whether currently rendering thinking content
        self._capture_mode_override = None  # Session-only viewport capture override
        self._pending_viewport_image = None  # Viewport image queued by after_changes mode
        self._mcp_connected = False
        self._vision_fallback_tool = None   # runtime-only, found after MCP connect
        self._vision_hint_shown = False      # one-time hint for untested state
        self._optimization_active = False
        self._validate_pending = False
        self._active_skill_name = ""

        # Initialize hook registry on main thread (before any worker threads)
        from ..hooks import get_hook_registry
        get_hook_registry()

        self._build_ui()
        self._ensure_vision_fallback()
        self._refresh_image_controls()
        self.setAcceptDrops(True)

        self._shutting_down = False
        # Starts disabled: get_chat_dock flips this on after the restore runs.
        # Otherwise addDockWidget emits dockLocationChanged BEFORE restore can
        # read the saved state from disk, and our first save overwrites the
        # previous session's good state with the current (default) state.
        self._saves_enabled = False

        self.dockLocationChanged.connect(self._save_dock_state)
        self.topLevelChanged.connect(self._save_dock_state)
        # visibilityChanged catches tabify when our dock becomes a background tab
        self.visibilityChanged.connect(self._save_dock_state)

        # Debounced save for tabify-by-drag. Tabification emits no dedicated
        # Qt signal, but the dock's geometry changes when it joins a tab group,
        # which triggers resizeEvent. Debounce to avoid thrashing on active drag.
        self._dock_save_timer = QtCore.QTimer(self)
        self._dock_save_timer.setSingleShot(True)
        self._dock_save_timer.setInterval(500)
        self._dock_save_timer.timeout.connect(self._save_dock_state)

        # Periodic poll — tabify-by-drag may not fire any signal we can hook,
        # so snapshot layout every 3s as a safety net. Cheap: only writes to
        # disk when state actually changes.
        self._dock_poll_timer = QtCore.QTimer(self)
        self._dock_poll_timer.setInterval(3000)
        self._dock_poll_timer.timeout.connect(self._save_dock_state)
        self._dock_poll_timer.start()

        # Shutdown detection. During FreeCAD close the layout can transiently
        # un-tabify docks before teardown completes; if we save during that
        # window we overwrite the last good state. Install an event filter on
        # the main window to catch its Close event and freeze saves from then
        # on. aboutToQuit is a belt-and-suspenders backstop for the same flag.
        try:
            mw_local = self._get_main_window()
            if mw_local is not None:
                mw_local.installEventFilter(self)
        except Exception:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._mark_shutdown)
        except Exception:
            pass
        self._refresh_input_history()

    def _mark_shutdown(self):
        self._shutting_down = True
        t = getattr(self, "_dock_poll_timer", None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def eventFilter(self, obj, event):
        try:
            if event.type() == QtCore.QEvent.Close:
                mw = self._get_main_window()
                if obj is mw:
                    self._mark_shutdown()
        except Exception:
            pass
        return False

    def _get_main_window(self):
        """Resolve the QMainWindow. self.parent() returns None when floating."""
        try:
            import FreeCADGui as Gui
            mw = Gui.getMainWindow()
            if mw is not None:
                return mw
        except Exception:
            pass
        return self.parent()

    def _save_dock_state(self, *_):
        """Snapshot dock layout so get_chat_dock can restore it next startup.

        FreeCAD restores main-window state before our workbench activates,
        which means our dock misses the restore. Saving our own state here
        and reapplying on creation is the workaround.
        """
        if getattr(self, "_shutting_down", False):
            return
        if not getattr(self, "_saves_enabled", False):
            return
        try:
            import base64
            cfg = get_config()
            prev_area = cfg.chat_dock_area
            prev_floating = cfg.chat_dock_floating
            prev_tabified = list(cfg.chat_dock_tabified_with or [])
            prev_state = cfg.chat_dock_mw_state

            cfg.chat_dock_floating = bool(self.isFloating())
            if self.isFloating():
                g = self.geometry()
                cfg.chat_dock_geometry = [g.x(), g.y(), g.width(), g.height()]

            mw = self._get_main_window()
            area = None
            if mw is not None and hasattr(mw, "dockWidgetArea"):
                try:
                    area = mw.dockWidgetArea(self)
                except Exception:
                    area = None
            cfg.chat_dock_area = _area_to_str(area) or cfg.chat_dock_area

            tabified = []
            if mw is not None and hasattr(mw, "tabifiedDockWidgets"):
                try:
                    for s in mw.tabifiedDockWidgets(self) or []:
                        n = s.objectName()
                        if n:
                            tabified.append(n)
                except Exception:
                    pass
            cfg.chat_dock_tabified_with = tabified

            new_state = prev_state
            if mw is not None and hasattr(mw, "saveState"):
                try:
                    raw = bytes(mw.saveState())
                    new_state = base64.b64encode(raw).decode("ascii")
                    cfg.chat_dock_mw_state = new_state
                except Exception:
                    pass

            changed = (
                prev_area != cfg.chat_dock_area
                or prev_floating != cfg.chat_dock_floating
                or prev_tabified != tabified
                or prev_state != new_state
            )
            if changed:
                save_current_config()
        except Exception:
            pass

    def moveEvent(self, event):
        super().moveEvent(event)
        timer = getattr(self, "_dock_save_timer", None)
        if timer is not None:
            timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        timer = getattr(self, "_dock_save_timer", None)
        if timer is not None:
            timer.start()

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Header bar ──
        header = QHBoxLayout()

        title = QLabel("<b>{}</b>".format(translate("ChatDockWidget", "FreeCAD AI")))
        header.addWidget(title)
        header.addStretch()

        # Mode toggle
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            translate("ChatDockWidget", "Plan"),
            translate("ChatDockWidget", "Act"),
        ])
        self.mode_combo.setMinimumWidth(80)
        cfg = get_config()
        self.mode_combo.setCurrentIndex(0 if cfg.mode == "plan" else 1)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header.addWidget(QLabel(translate("ChatDockWidget", "Mode:")))
        header.addWidget(self.mode_combo)

        # Viewport capture toggle
        self._capture_btn = QPushButton(translate("ChatDockWidget", "Capture"))
        self._capture_btn.setMinimumWidth(self._capture_btn.sizeHint().width())
        self._capture_btn.setToolTip(translate("ChatDockWidget", "Viewport capture: off"))
        self._capture_btn.clicked.connect(self._cycle_capture_mode)
        header.addWidget(self._capture_btn)

        # Dangerous-mode session toggle
        self.danger_toggle = QtWidgets.QCheckBox(
            translate("ChatDockWidget", "⚠ Dangerous mode"))
        self.danger_toggle.setToolTip(
            translate("ChatDockWidget",
                      "Disable code safety checks and allow running macros from any path. "
                      "Session-only — resets when FreeCAD restarts."))
        self.danger_toggle.toggled.connect(self._on_danger_toggled)
        header.addWidget(self.danger_toggle)

        # Settings button
        settings_btn = QPushButton(translate("ChatDockWidget", "Settings"))
        settings_btn.setMinimumWidth(settings_btn.sizeHint().width())
        settings_btn.clicked.connect(self._open_settings)
        header.addWidget(settings_btn)

        # ── Dangerous-mode banner (inserted before header) ──
        self.danger_banner = QtWidgets.QLabel(
            translate("ChatDockWidget",
                      "⚠ DANGEROUS MODE ACTIVE — safety checks disabled"))
        self.danger_banner.setStyleSheet(
            "background-color: #b00020; color: white; font-weight: bold; "
            "padding: 4px;")
        self.danger_banner.setAlignment(QtCore.Qt.AlignCenter)
        self.danger_banner.setVisible(False)
        layout.addWidget(self.danger_banner)

        layout.addLayout(header)

        # ── Chat display ──
        self.chat_display = QTextBrowser()
        self.chat_display.setAcceptDrops(False)  # Drops handled by ChatDockWidget
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.setOpenLinks(False)
        self.chat_display.setFont(QFont("Sans", 10))
        self.chat_display.setStyleSheet(get_chat_display_stylesheet())
        self.chat_display.anchorClicked.connect(self._handle_anchor_click)
        layout.addWidget(self.chat_display, 1)

        # ── Attachment strip ──
        self._attachment_strip = _AttachmentStrip()
        layout.addWidget(self._attachment_strip)

        # ── Input area ──
        input_layout = QHBoxLayout()

        self.input_edit = _ImageAwareTextEdit()
        self.input_edit.setPlaceholderText(translate("ChatDockWidget", "Describe what you want to create..."))
        self.input_edit.setMaximumHeight(80)
        self.input_edit.setFont(QFont("Sans", 10))
        colors = _get_theme_colors()
        self.input_edit.setStyleSheet(
            f"QTextEdit {{ background-color: {colors['chat_bg']}; color: {colors['chat_text']}; "
            f"border: 1px solid {colors['chat_border']}; }}"
        )
        self.input_edit.installEventFilter(self)
        self.input_edit.image_added.connect(self._on_image_added)
        self.input_edit.document_added.connect(self._on_document_added)
        input_layout.addWidget(self.input_edit, 1)

        # Button column: attach + send
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(2)

        self._attach_btn = QPushButton(translate("ChatDockWidget", "Attach"))
        self._attach_btn.setToolTip(translate("ChatDockWidget", "Attach a file (image, text, or document)"))
        self._attach_btn.clicked.connect(self._attach_file)
        btn_layout.addWidget(self._attach_btn)

        self.send_btn = QPushButton(translate("ChatDockWidget", "Send"))
        self.send_btn.setMinimumHeight(30)
        self.send_btn.setStyleSheet(
            f"QPushButton {{ background-color: {colors['tool_success_border']}; color: white; "
            f"font-weight: bold; padding: 8px 16px; }}"
        )
        self.send_btn.clicked.connect(self._send_message)
        btn_layout.addWidget(self.send_btn)

        input_layout.addLayout(btn_layout)

        layout.addLayout(input_layout)

        # ── Footer ──
        footer = QHBoxLayout()

        new_chat_btn = QPushButton(translate("ChatDockWidget", "+ New Chat"))
        new_chat_btn.setMinimumWidth(new_chat_btn.sizeHint().width())
        new_chat_btn.clicked.connect(self._new_chat)
        footer.addWidget(new_chat_btn)

        load_chat_btn = QPushButton(translate("ChatDockWidget", "Load"))
        load_chat_btn.setMinimumWidth(load_chat_btn.sizeHint().width())
        load_chat_btn.setToolTip(translate("ChatDockWidget", "Load a previous chat session"))
        load_chat_btn.clicked.connect(self._load_chat)
        footer.addWidget(load_chat_btn)

        save_log_btn = QPushButton(translate("ChatDockWidget", "Save Log"))
        save_log_btn.setMinimumWidth(save_log_btn.sizeHint().width())
        save_log_btn.setToolTip(translate("ChatDockWidget", "Save session log for debugging"))
        save_log_btn.clicked.connect(self._save_session_log)
        footer.addWidget(save_log_btn)

        # _capture_btn is intentionally excluded — its stylesheet is
        # composed in _capture_btn_stylesheet() so that mode color and
        # conflict-busting padding share a single setStyleSheet call.
        self._theme_ui_conflict_buttons = [
            settings_btn,
            new_chat_btn,
            load_chat_btn,
            save_log_btn,
        ]

        footer.addStretch()

        self.token_label = QLabel(translate("ChatDockWidget", "tokens: ~0"))
        self.token_label.setStyleSheet(f"color: {colors['thinking_text']}; font-size: 11px;")
        footer.addWidget(self.token_label)

        layout.addLayout(footer)

        self.setWidget(container)

        # Sync banner/toggle with current dangerous-mode state
        # (shows banner at startup if dangerous_skip_safety was hand-edited in config.json)
        self._update_danger_banner()

    # ── Dangerous-mode toggle ──────────────────────────────

    def _on_danger_toggled(self, checked):
        from ..core.dangerous_mode import get_dangerous_mode
        dm = get_dangerous_mode()
        if checked:
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle(translate("ChatDockWidget", "Enable Dangerous mode?"))
            box.setText(translate(
                "ChatDockWidget",
                "Dangerous mode disables the safety checks built into FreeCAD AI."))
            box.setInformativeText(translate(
                "ChatDockWidget",
                "While active:\n"
                "• AI-run code may call shell commands, delete files, and touch "
                "anything your user account can.\n"
                "• A macro with an infinite loop will FREEZE FreeCAD with no "
                "recovery — unsaved work will be lost.\n"
                "• Generated code runs against your live document without the "
                "headless sandbox pre-check.\n\n"
                "You are solely responsible for what you run. Continue?"))
            box.setStandardButtons(
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            box.setDefaultButton(QtWidgets.QMessageBox.No)
            if box.exec() != QtWidgets.QMessageBox.Yes:
                self.danger_toggle.blockSignals(True)
                self.danger_toggle.setChecked(False)
                self.danger_toggle.blockSignals(False)
                return
            dm.arm()
        else:
            dm.disarm()
        self._update_danger_banner()

    def _update_danger_banner(self):
        from ..core.dangerous_mode import get_dangerous_mode
        active = get_dangerous_mode().active
        self.danger_banner.setVisible(active)
        if active and not self.danger_toggle.isChecked():
            self.danger_toggle.blockSignals(True)
            self.danger_toggle.setChecked(True)
            self.danger_toggle.blockSignals(False)
        elif not active and self.danger_toggle.isChecked():
            self.danger_toggle.blockSignals(True)
            self.danger_toggle.setChecked(False)
            self.danger_toggle.blockSignals(False)

    # ── Theme refresh on show ──────────────────────────────

    def showEvent(self, event):
        """Refresh theme colors when the widget becomes visible."""
        super().showEvent(event)
        refresh_theme_cache()
        self._apply_theme()

    def _resolve_stylesheet_conflict(self, theme_name: str):
        """OpenDark/OpenLight theme packs inject global QPushButton styles that
        override padding/margins, causing button text to be clipped.
        Re-applying explicit padding via setStyleSheet restores correct sizing.
        Each button keeps its construction-time setMaximumWidth(); only the
        padding stylesheet is reapplied here.
        """
        if theme_name.casefold() in _STYLESHEET_CONFLICT_THEMES:
            for btn in self._theme_ui_conflict_buttons:
                btn.setStyleSheet(
                    "QPushButton { padding: 4px 16px; margin: 1px; }"
                )

    def _capture_btn_stylesheet(self) -> str:
        """Build the _capture_btn stylesheet by composing capture-mode
        color and (under conflicting themes) explicit padding.

        Both rule sets are applied via a single setStyleSheet call so
        that capture-mode cycling and theme refresh can never overwrite
        each other's contribution.
        """
        mode = (
            getattr(self, "_capture_mode_override", None)
            or get_config().viewport_capture
        )
        color = _CAPTURE_MODE_COLORS.get(mode, "")
        needs_padding = (
            get_freecad_mode_name().casefold() in _STYLESHEET_CONFLICT_THEMES
        )
        if not color and not needs_padding:
            return ""
        rules = []
        if needs_padding:
            rules.append("padding: 4px 16px; margin: 1px;")
        if color:
            rules.append(color)
        return "QPushButton { " + " ".join(rules) + " }"

    def _apply_theme(self):
        """Reapply all theme-dependent stylesheets."""
        colors = _get_theme_colors()
        theme_name = get_freecad_mode_name(force_refresh=True)
        self._resolve_stylesheet_conflict(theme_name)
        self._capture_btn.setStyleSheet(self._capture_btn_stylesheet())
        self.chat_display.setStyleSheet(get_chat_display_stylesheet())
        self.input_edit.setStyleSheet(
            f"QTextEdit {{ background-color: {colors['chat_bg']}; color: {colors['chat_text']}; "
            f"border: 1px solid {colors['chat_border']}; }}"
        )
        if not self.send_btn.isEnabled():
            # Loading state
            self.send_btn.setStyleSheet(
                f"QPushButton {{ background-color: {colors['system_label']}; color: white; "
                f"font-weight: bold; padding: 8px 16px; }}"
            )
        else:
            self.send_btn.setStyleSheet(
                f"QPushButton {{ background-color: {colors['tool_success_border']}; color: white; "
                f"font-weight: bold; padding: 8px 16px; }}"
            )
        self.token_label.setStyleSheet(f"color: {colors['thinking_text']}; font-size: 11px;")

    # ── Input history ───────────────────────────────────────

    def _refresh_input_history(self) -> None:
        """Rebuild the input-history entries from the current conversation.

        Filters to user messages whose content is a plain string (skips
        multipart messages that carry image attachments). Also skips system
        messages — Conversation.add_system_message stores them as role=user
        with a "[System] " prefix (see freecad_ai/core/conversation.py), and
        those are not real user prompts that belong in the history.
        """
        entries = [
            m["content"]
            for m in self.conversation.messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and not m["content"].startswith("[System] ")
        ]
        self._input_history.set_entries(entries)

    def _set_input_text(self, text: str) -> None:
        """Replace input contents and place caret at end without tripping the
        history-reset path that user typing goes through."""
        self._suppress_history_reset = True
        try:
            self.input_edit.setPlainText(text)
            cur = self.input_edit.textCursor()
            cur.movePosition(QTextCursor.End)
            self.input_edit.setTextCursor(cur)
        finally:
            self._suppress_history_reset = False

    # ── Event filter (Enter to send, Up/Down for history) ───

    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            if self._handle_input_keypress(event):
                return True
        return super().eventFilter(obj, event)

    def _handle_input_keypress(self, event) -> bool:
        """Return True if the KeyPress was consumed by dock-level handling.

        Covers (1) Enter/Return send, (2) Up/Down history navigation gated on
        caret position, and (3) cycle reset on input-editing keys. Returning
        False lets the keystroke proceed to Qt's default text-edit handling.
        """
        # 1. Enter / Return — existing send behavior (unchanged).
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                return False  # Shift+Enter: newline
            self._send_message()
            return True

        # 2. History navigation — only when the input is editable.
        if not self.input_edit.isReadOnly():
            cursor = self.input_edit.textCursor()
            # Up/Down at the document edge: consume the event even when the
            # helper returns None (empty / clamped history) — at the edge there
            # is nowhere else for the caret to move, so swallowing the key
            # avoids a dead-feeling keystroke. atStart()/atEnd() are
            # document-level, so in a multi-line draft history only triggers
            # when the caret is at the very first/last position; mid-document
            # arrows fall through below to Qt's default caret movement.
            if event.key() == Qt.Key_Up and cursor.atStart():
                result = self._input_history.up(self.input_edit.toPlainText())
                if result is not None:
                    self._set_input_text(result)
                return True
            if event.key() == Qt.Key_Down and cursor.atEnd():
                result = self._input_history.down()
                if result is not None:
                    self._set_input_text(result)
                return True

            # 3. Reset the history cycle on any input-editing key.
            if self._is_history_reset_key(event):
                if not self._suppress_history_reset:
                    self._input_history.reset()

        return False  # Let Qt handle any non-history keystroke.

    @staticmethod
    def _is_history_reset_key(event) -> bool:
        """Return True if a KeyPress should end the history navigation cycle.

        Triggers on any key that produces a character (event.text() non-empty)
        or any editing key (Backspace/Delete/Home/End). Bare modifier presses
        (Shift/Ctrl/Alt) produce empty text and so do NOT trigger a reset.
        Up/Down are explicitly excluded — they drive the cycle.
        """
        k = event.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            return False
        if k in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Home, Qt.Key_End):
            return True
        return bool(event.text())

    # ── Actions ─────────────────────────────────────────────

    def _send_message(self):
        """Send the current input to the LLM."""
        if self._worker and self._worker.isRunning():
            # Button is in "Stop" state — interrupt the in-flight run instead
            # of sending. Input is usually empty here, so this must run before
            # the empty-text guard below.
            self._worker.requestInterruption()
            return

        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        self.input_edit.clear()
        self._retry_count = 0  # Reset retries for new user message
        self._active_skill_name = ""

        # Check for --validate flag
        self._validate_pending = False
        if "--validate" in text:
            text = text.replace("--validate", "").strip()
            self._validate_pending = True

        # Check for skill commands
        if text.startswith("/"):
            handled = self._handle_skill_command(text)
            if handled:
                return

        # Fire user_prompt_submit hook
        from ..hooks import fire_hook
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        hook_result = fire_hook("user_prompt_submit", {
            "text": text, "images": [], "mode": mode,
        })
        if hook_result.get("block"):
            self._append_html(render_message("system",
                f"Blocked by hook: {hook_result.get('reason', 'no reason given')}"))
            return
        if hook_result.get("modify"):
            text = hook_result["modify"]

        # Show one-time hint if vision not tested and user is sending images
        pending_images = self._attachment_strip.get_images()
        cfg = get_config()
        if pending_images and cfg.vision_detected is None and not self._vision_hint_shown:
            self._vision_hint_shown = True
            self._append_html(
                '<div style="color: #888; font-size: 9pt; margin: 4px 12px;">'
                'Tip: click Test Connection in Settings to enable vision auto-detection.'
                '</div>'
            )

        # Collect attached images
        images = pending_images or None

        # Collect attached documents
        pending_docs = self._attachment_strip.get_documents()
        documents = pending_docs or None

        # Auto-capture viewport if configured
        capture_mode = getattr(self, "_capture_mode_override", None) or get_config().viewport_capture
        if capture_mode == "every_message":
            vp_img = self._capture_viewport_for_chat()
            if vp_img:
                images = (images or []) + [vp_img]

        # Prepend pending viewport image (from after_changes mode)
        if getattr(self, "_pending_viewport_image", None):
            images = (images or []) + [self._pending_viewport_image]
            self._pending_viewport_image = None

        # Add to conversation and display
        self.conversation.add_user_message(text, images=images, documents=documents)
        self._refresh_input_history()
        display_content = self.conversation.messages[-1]["content"]
        self._append_html(render_message("user", display_content))
        self._attachment_strip.clear()

        # Check if conversation needs compaction
        cfg = get_config()
        if self.conversation.needs_compaction(cfg.context_window):
            self._compact_and_send()
            return

        self._continue_send()

    def _on_image_added(self, media_type: str, base64_data: str):
        """Handle image added via paste or drop."""
        self._attachment_strip.add_image(media_type, base64_data)

    def _on_document_added(self, filename: str, text: str):
        """Handle text file added via paste or drop."""
        self._attachment_strip.add_document(filename, text)

    # ── Dock-level drag-and-drop (accepts drops anywhere on the panel) ──

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """Accept drag move so the drop cursor stays valid."""
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Handle files dropped anywhere on the chat panel."""
        import os
        mime = event.mimeData()
        if mime.hasImage() and self.input_edit._images_enabled:
            self.input_edit._process_image_from_mime(mime)
            event.acceptProposedAction()
            return
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                filename = os.path.basename(path)
                ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
                # Image files
                if ext in ("png", "jpg", "jpeg", "bmp", "gif", "webp"):
                    if self.input_edit._images_enabled:
                        self.input_edit._process_image_file(path)
                    else:
                        self._append_html(render_message("system",
                            "Cannot attach images — no vision support detected. Check Settings or use a vision-capable model."))
                    event.acceptProposedAction()
                    return
                # Try reading as text
                text = self._read_text_file(path)
                if text is not None:
                    self._attachment_strip.add_document(filename, text)
                    event.acceptProposedAction()
                    return
                # Binary file — try hook
                self._process_file_with_hook(path, filename, ext)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    # File extensions that can be read as text without external tools.
    _TEXT_EXTENSIONS = {
        "txt", "md", "csv", "tsv", "json", "xml", "yaml", "yml",
        "ini", "cfg", "conf", "toml", "log", "py", "js", "ts",
        "html", "htm", "css", "sql", "sh", "bash", "bat", "ps1",
        "c", "cpp", "h", "hpp", "java", "rs", "go", "rb", "lua",
        "r", "m", "tex", "bib", "svg", "makefile", "dockerfile",
    }

    def _attach_file(self):
        """Open file picker to attach an image or document.

        Routing logic:
        - Image files → sent as base64 vision blocks (handled by LLM vision)
        - Text files → read content, included as text in the message
        - Other files → fire 'file_attach' hook for user-defined conversion;
          if no hook handles the file, show a helpful message
        """
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            translate("ChatDockWidget", "Attach File"),
            "",
            translate("ChatDockWidget",
                      "All supported files (*.png *.jpg *.jpeg *.bmp *.gif *.webp "
                      "*.txt *.md *.csv *.tsv *.json *.xml *.yaml *.yml "
                      "*.ini *.cfg *.conf *.toml *.log *.py *.js *.ts "
                      "*.html *.htm *.css *.sql *.sh *.bash *.svg "
                      "*.c *.cpp *.h *.hpp *.java *.rs *.go *.rb *.lua "
                      "*.pdf *.docx *.xlsx *.odt *.rtf);;"
                      "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;"
                      "Text files (*.txt *.md *.csv *.json *.xml *.yaml *.py *.js *.ts);;"
                      "All files (*)"),
        )
        if not path:
            return
        import os
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        filename = os.path.basename(path)

        # Route 1: Image files → vision block (requires vision support)
        if ext in ("png", "jpg", "jpeg", "bmp", "gif", "webp"):
            if not self.input_edit._images_enabled:
                self._append_html(render_message("system",
                    "Cannot attach images — no vision support detected. "
                    "Check Settings or use a vision-capable model."))
                return
            self.input_edit._process_image_file(path)
            return

        # Route 2: Try to read as text — known extensions first, then probe
        text = self._read_text_file(path)
        if text is not None:
            self._attachment_strip.add_document(filename, text)
            return

        # Route 3: Binary/unknown files → fire file_attach hook
        self._process_file_with_hook(path, filename, ext)

    def _read_text_file(self, path: str, max_size: int = 512_000) -> str | None:
        """Read a file as text, return content or None if binary/error.

        Rejects known binary formats (by magic bytes) and files
        containing null bytes.
        """
        import os
        try:
            size = os.path.getsize(path)
            if size > max_size:
                self._append_html(render_message("system",
                    f"File too large ({size // 1024} KB). Maximum is {max_size // 1024} KB."))
                return None
            with open(path, "rb") as f:
                raw = f.read()
            if _is_binary_content(raw):
                return None  # Binary file — let the hook handle it
            return raw.decode("utf-8", errors="replace")
        except OSError as e:
            self._append_html(render_message("system", f"Cannot read file: {e}"))
            return None

    def _process_file_with_hook(self, path: str, filename: str, ext: str):
        """Try to convert a file via the file_attach hook."""
        from ..hooks import fire_hook
        import mimetypes
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        result = fire_hook("file_attach", {
            "path": path,
            "filename": filename,
            "extension": ext,
            "mime_type": mime_type,
        })
        if result.get("block"):
            self._append_html(render_message("system",
                f"Attachment blocked: {result.get('reason', 'no reason given')}"))
            return
        if result.get("text"):
            self._attachment_strip.add_document(filename, result["text"])
            return
        # No hook handled it
        self._append_html(render_message("system",
            f"No converter for .{ext} files. To handle this format, either:\n"
            f"- Add a file_attach hook (see docs/hooks/file-attach-example/)\n"
            f"- Install an MCP server like markdownify-mcp for rich conversion"))

    def _capture_viewport_for_chat(self) -> dict | None:
        """Capture the viewport and return an image content block dict."""
        from ..utils.viewport import capture_viewport_image, make_image_content_block, RESOLUTION_PRESETS
        cfg = get_config()
        w, h = RESOLUTION_PRESETS.get(cfg.viewport_resolution, (800, 600))
        img_bytes = capture_viewport_image(w, h)
        if img_bytes:
            return make_image_content_block(img_bytes)
        return None

    def _cycle_capture_mode(self):
        """Cycle viewport capture mode: off -> every_message -> after_changes -> off."""
        modes = ["off", "every_message", "after_changes"]
        labels = {
            "off": translate("ChatDockWidget", "Viewport capture: off"),
            "every_message": translate("ChatDockWidget", "Viewport capture: every message"),
            "after_changes": translate("ChatDockWidget", "Viewport capture: after changes"),
        }
        current = getattr(self, "_capture_mode_override", None) or get_config().viewport_capture
        try:
            idx = modes.index(current)
        except ValueError:
            idx = 0
        next_mode = modes[(idx + 1) % len(modes)]
        self._capture_mode_override = next_mode
        self._capture_btn.setToolTip(labels.get(next_mode, next_mode))
        # Visual feedback: distinct colors per active mode (composed with
        # conflict-theme padding via _capture_btn_stylesheet()).
        self._capture_btn.setStyleSheet(self._capture_btn_stylesheet())

    def _on_mode_changed(self, index):
        """Update config when mode is toggled."""
        cfg = get_config()
        cfg.mode = "plan" if index == 0 else "act"
        save_current_config()

    def _ensure_vision_fallback(self):
        """Connect non-deferred MCP servers and search for a vision fallback.

        Called on startup and after settings changes so that image controls
        can be enabled/disabled correctly without waiting for the first message.
        Non-deferred servers are connected eagerly; deferred servers wait for
        the first Act-mode message.
        """
        cfg = get_config()
        if cfg.supports_vision or not cfg.mcp_servers:
            return
        if self._vision_fallback_tool is not None:
            return
        # Only connect non-deferred servers at this point
        has_non_deferred = any(
            not s.get("deferred", True) and s.get("enabled", True)
            for s in cfg.mcp_servers
        )
        if has_non_deferred:
            self._connect_mcp_servers(cfg, only_deferred=False)
        # Build registry (with whatever is connected so far) and search
        from ..mcp.manager import get_mcp_manager
        manager = get_mcp_manager()
        if manager.connected_servers:
            from ..tools.setup import create_default_registry
            from ..mcp.manager import find_vision_fallback
            self._tool_registry = create_default_registry()
            self._vision_fallback_tool = find_vision_fallback(self._tool_registry)

    def _refresh_image_controls(self):
        """Enable/disable image controls based on vision capability."""
        cfg = get_config()
        # Disable only when we know there's no vision AND no fallback
        disable = (cfg.vision_detected is not None
                   and not cfg.supports_vision
                   and self._vision_fallback_tool is None)

        no_vision_tip = translate(
            "ChatDockWidget",
            "No vision support \u2014 configure a vision MCP server or enable in Settings"
        )

        self._capture_btn.setEnabled(not disable)
        self.input_edit.set_images_enabled(not disable)
        # Attach button always enabled — supports text/document files regardless of vision
        self._attach_btn.setEnabled(True)

        if disable:
            self._capture_btn.setToolTip(no_vision_tip)
            self._attach_btn.setToolTip(translate("ChatDockWidget",
                "Attach a file (text/document — image attach requires vision)"))
        else:
            self._capture_btn.setToolTip(translate("ChatDockWidget", "Viewport capture: off"))
            self._attach_btn.setToolTip(translate("ChatDockWidget",
                "Attach a file (image, text, or document)"))

    def _open_settings(self):
        """Open the settings dialog."""
        from .settings_dialog import SettingsDialog
        cfg = get_config()
        old_provider = cfg.provider.name
        old_model = cfg.provider.model
        old_mcp = list(cfg.mcp_servers)
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        dlg = SettingsDialog(parent)
        dlg.exec()
        # Refresh after settings may have changed
        cfg = get_config()
        if cfg.provider.name != old_provider or cfg.provider.model != old_model:
            self._vision_fallback_tool = None
        if cfg.mcp_servers != old_mcp:
            self._vision_fallback_tool = None
            self._mcp_connected = False
            # Disconnect old MCP servers so stale connections don't linger
            from ..mcp.manager import get_mcp_manager
            get_mcp_manager().disconnect_all()
        self._ensure_vision_fallback()
        self._refresh_image_controls()

    def _new_chat(self):
        """Start a new conversation."""
        # Clean up optimization state
        if self._optimization_active:
            try:
                from ..tools.optimize_tools import stop_optimization
                stop_optimization()
            except ImportError:
                pass
            self._optimization_active = False

        if self.conversation.messages:
            self.conversation.save()

        self.conversation = Conversation()
        self._refresh_input_history()
        self.chat_display.clear()
        self._update_token_count()

    def _load_chat(self):
        """Show a dialog to load a previous chat session."""
        saved = Conversation.list_saved()
        if not saved:
            self._append_html(render_message("system", translate("ChatDockWidget", "No saved sessions found.")))
            return

        # Build display items with timestamps and preview
        items = []
        for conv_id in saved[:20]:  # Show last 20
            try:
                conv = Conversation.load(conv_id)
                # Get timestamp from conversation
                import time
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(conv.created_at)) if conv.created_at else "?"
                # Get first user message as preview
                preview = ""
                for m in conv.messages:
                    text = Conversation.extract_text(m.get("content", ""))
                    if m["role"] == "user" and not text.startswith("["):
                        preview = text[:60].replace("\n", " ")
                        break
                item_text = f"{ts} | {preview or conv_id}"
                items.append((item_text, conv_id))
            except Exception:
                items.append((conv_id, conv_id))

        # Use QInputDialog to pick a session
        item_labels = [item[0] for item in items]

        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self

        from .compat import QtWidgets as _QtWidgets
        selected, ok = _QtWidgets.QInputDialog.getItem(
            parent, translate("ChatDockWidget", "Load Chat Session"),
            translate("ChatDockWidget", "Select a session to resume:"),
            item_labels, 0, False
        )

        if ok and selected:
            idx = item_labels.index(selected)
            conv_id = items[idx][1]

            # Save current conversation first
            if self.conversation.messages:
                self.conversation.save()

            # Load the selected conversation
            try:
                self.conversation = Conversation.load(conv_id)
                self._refresh_input_history()
                self._rerender_chat()
                self._update_token_count()
                self._append_html(render_message(
                    "system",
                    translate("ChatDockWidget", "Resumed session from {}").format(
                        items[idx][0].split(' | ')[0])
                ))
            except Exception as e:
                self._append_html(render_message(
                    "system",
                    translate("ChatDockWidget", "Failed to load session: {}").format(e)
                ))

    def _compact_and_send(self):
        """Compact conversation by summarizing older messages, then continue sending."""
        self._append_html(
            '<div style="margin: 4px 0; padding: 6px 10px; '
            'background-color: #fff3e0; border-left: 3px solid #ff9800; '
            'border-radius: 0 4px 4px 0; font-size: 12px; color: #e65100;">'
            '{}</div>'.format(
                translate("ChatDockWidget", "Compacting context (~{}k tokens)...").format(
                    self.conversation.estimated_tokens() // 1000))
        )

        # Build summary of older messages (all except last 4)
        keep_recent = 4
        older = self.conversation.messages[:-keep_recent] if len(self.conversation.messages) > keep_recent else []
        if not older:
            # Nothing to compact, just send normally
            self._continue_send()
            return

        # Build a text summary of older messages for the LLM to compress
        summary_parts = []
        for msg in older:
            role = msg["role"]
            content = msg.get("content", "")
            if role == "tool_result":
                # Truncate long tool results for the summary request
                if len(content) > 500:
                    content = content[:500] + "..."
                summary_parts.append(f"[Tool Result] {content}")
            elif role == "assistant" and msg.get("tool_calls"):
                tc_names = [tc["name"] for tc in msg["tool_calls"]]
                summary_parts.append(f"[Assistant] Called tools: {', '.join(tc_names)}")
                if content:
                    summary_parts.append(f"  Text: {content[:300]}")
            else:
                label = "User" if role == "user" else "Assistant" if role == "assistant" else "System"
                if len(content) > 500:
                    content = content[:500] + "..."
                summary_parts.append(f"[{label}] {content}")

        summary_text = "\n".join(summary_parts)

        # Use a background thread to generate the summary
        self._set_loading(True)
        self._compaction_worker = _CompactionWorker(summary_text, parent=self)
        self._compaction_worker.finished.connect(self._on_compaction_finished)
        self._compaction_worker.start()

    def _on_compaction_finished(self, summary):
        """Handle compaction result and continue sending."""
        if summary:
            self.conversation.compact(summary, keep_recent=4)
            self._append_html(
                '<div style="margin: 4px 0; padding: 6px 10px; '
                'background-color: #e8f5e9; border-left: 3px solid #4caf50; '
                'border-radius: 0 4px 4px 0; font-size: 12px; color: #2e7d32;">'
                '{}</div>'.format(
                    translate("ChatDockWidget", "Context compacted to ~{}k tokens").format(
                        self.conversation.estimated_tokens() // 1000))
            )
        self._set_loading(False)
        self._update_token_count()
        # Continue with the normal send flow
        self._continue_send()

    def _continue_send(self):
        """Continue the send flow after optional compaction."""
        from ..core.system_prompt import build_system_prompt
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        cfg = get_config()

        # Determine if we should use tools. cfg.supports_tools combines the
        # provider-wide flag with per-model detection from /api/show — so an
        # Ollama embedding/reranker accidentally selected as the main model
        # won't get tools sent to it.
        use_tools = cfg.enable_tools and mode == "act" and cfg.supports_tools
        tools_schema = None
        api_style = "openai"

        if use_tools:
            # Connect MCP servers on first tool-enabled send
            if not self._mcp_connected:
                self._connect_mcp_servers(cfg)

            from ..tools.setup import create_default_registry
            from ..llm.providers import get_api_style

            # Build extra tools for active optimization
            extra_tools = []
            if self._optimization_active:
                try:
                    from ..tools.optimize_tools import get_optimize_iteration_tool, _active_config
                    extra_tools = [get_optimize_iteration_tool()]
                    # Pass the tool executor to the active config so evaluator can dispatch
                    if _active_config is not None:
                        from ..tools.executor_utils import (
                            MainThreadToolExecutor, _HAS_QT,
                        )
                        if _HAS_QT:
                            from ..tools.executor_utils import QtMainThreadToolExecutor
                            executor = QtMainThreadToolExecutor()
                        else:
                            executor = MainThreadToolExecutor()
                        executor.set_registry(None)  # will be set after registry creation
                        _active_config["_tool_executor"] = executor
                except ImportError:
                    pass

            self._tool_registry = create_default_registry(include_mcp=True, extra_tools=extra_tools)

            # Update executor registry if optimization active
            if self._optimization_active and extra_tools:
                try:
                    from ..tools.optimize_tools import _active_config
                    if _active_config and "_tool_executor" in _active_config:
                        _active_config["_tool_executor"].set_registry(self._tool_registry)
                except ImportError:
                    pass

            # Search for vision fallback after registry (with MCP tools) is created
            if not cfg.supports_vision and self._vision_fallback_tool is None:
                from ..mcp.manager import find_vision_fallback
                self._vision_fallback_tool = find_vision_fallback(self._tool_registry)
                self._refresh_image_controls()
            api_style = get_api_style(cfg.provider.name)

            # Optional tool reranking: filter schemas down to the top-N
            # relevant tools (+ pinned) based on the latest user message.
            filter_names = None
            if cfg.rerank_method in ("keyword", "llm"):
                user_text = _extract_latest_user_text(self.conversation)
                pairs = self._tool_registry.list_name_description_pairs()
                ranked = _run_reranker(cfg, pairs, user_text)
                filter_names = set(ranked)
                try:
                    import FreeCAD as _App
                    _App.Console.PrintMessage(
                        "[FreeCAD AI] Reranker ({}): {} of {} tools -> {}\n".format(
                            cfg.rerank_method, len(ranked), len(pairs),
                            ", ".join(ranked))
                    )
                except Exception:
                    pass

            if api_style == "anthropic":
                tools_schema = self._tool_registry.to_anthropic_schema(filter_names)
            else:
                tools_schema = self._tool_registry.to_openai_schema(filter_names)
            system_prompt = build_system_prompt(
                mode=mode, tools_enabled=True,
                override=cfg.system_prompt_override)
        else:
            self._tool_registry = None
            system_prompt = build_system_prompt(
                mode=mode, override=cfg.system_prompt_override)

        # Build describe_fn for non-vision LLMs
        describe_fn = None
        conversation_ref = None
        if not cfg.supports_vision:
            fallback = getattr(self, '_vision_fallback_tool', None)
            if fallback and self._tool_registry:
                _reg = self._tool_registry
                _tool = fallback
                def _make_describe(reg, tool_name):
                    def describe(b64_data):
                        result = reg.execute(
                            tool_name, {"image": b64_data, "prompt": "Describe this image in detail."}
                        )
                        if result.success:
                            return result.output
                        raise RuntimeError(result.error or "describe_image failed")
                    return describe
                describe_fn = _make_describe(_reg, _tool)
                conversation_ref = self.conversation

        # Get messages for API
        from ..llm.client import should_strip_thinking
        strip = should_strip_thinking(
            cfg.provider.model, cfg.strip_thinking_history)
        # When the model has no vision and no describe_image fallback is
        # available, drop history image blocks to a placeholder so they aren't
        # sent raw to a provider that would reject them (issue #30). When a
        # describe_fn exists, the worker rebuilds messages with descriptions.
        strip_images = not cfg.supports_vision and describe_fn is None
        messages = self.conversation.get_messages_for_api(
            api_style=api_style, strip_images=strip_images, strip_thinking=strip)

        # Start streaming
        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )

        self._in_thinking = False
        self._tool_results_stored = False
        self._summary_rendered = False
        self._worker = _LLMWorker(
            messages, system_prompt,
            tools=tools_schema, registry=self._tool_registry,
            api_style=api_style, conversation=conversation_ref,
            describe_fn=describe_fn, parent=self,
        )
        self._worker.token_received.connect(self._on_token)
        self._worker.thinking_received.connect(self._on_thinking)
        self._worker.response_finished.connect(self._on_response_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.tool_call_started.connect(self._on_tool_call_started)
        self._worker.tool_call_finished.connect(self._on_tool_call_finished)
        self._worker.tool_exec_requested.connect(self._execute_tool_call)
        self._worker.vision_note.connect(self._on_vision_note)
        self._worker.start()

    def _save_session_log(self):
        """Save the current session log as JSON for debugging."""
        import os
        from datetime import datetime

        os.makedirs(LOGS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(LOGS_DIR, f"session_{timestamp}.json")

        # Build the log from conversation messages
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "messages": [],
        }

        for msg in self.conversation.messages:
            entry = {"role": msg["role"]}
            if "content" in msg and msg["content"]:
                entry["content"] = msg["content"]
            if "tool_calls" in msg:
                entry["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                entry["tool_call_id"] = msg["tool_call_id"]
            log_data["messages"].append(entry)

        # Also include the last worker's tool results if available
        if self._worker and hasattr(self._worker, "_tool_results") and self._worker._tool_results:
            log_data["tool_trace"] = self._worker._tool_results

        try:
            with open(filepath, "w") as f:
                json.dump(log_data, f, indent=2, default=str)

            cfg = get_config()
            prune_oldest_files(
                LOGS_DIR,
                lambda n: n.startswith("session_") and n.endswith(".json"),
                cfg.max_session_logs,
                cfg.max_retention_age_days,
            )

            self._append_html(render_message(
                "system",
                translate("ChatDockWidget", "Session log saved to: {}").format(filepath)
            ))
        except Exception as e:
            self._append_html(render_message(
                "system",
                translate("ChatDockWidget", "Failed to save log: {}").format(e)
            ))

    def _auto_save_log(self):
        """Auto-save tool trace after each tool-using response."""
        import os
        from datetime import datetime

        os.makedirs(LOGS_DIR, exist_ok=True)

        filepath = os.path.join(LOGS_DIR, "latest_session.json")

        log_data = {
            "timestamp": datetime.now().isoformat(),
            "tool_trace": [],
        }

        if self._worker and hasattr(self._worker, "_tool_results"):
            for turn_idx, turn in enumerate(self._worker._tool_results):
                turn_data = {
                    "turn": turn_idx + 1,
                    "assistant_text": turn["assistant_text"],
                    "tool_calls": [],
                }
                for tc, result in zip(turn["tool_calls"], turn["results"]):
                    turn_data["tool_calls"].append({
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                        "result": result["content"],
                    })
                log_data["tool_trace"].append(turn_data)

        try:
            with open(filepath, "w") as f:
                json.dump(log_data, f, indent=2, default=str)
        except Exception:
            pass  # Don't disrupt the UI for auto-save failures

    # ── Streaming handlers ──────────────────────────────────

    @Slot(str)
    def _on_thinking(self, chunk):
        """Handle a thinking/reasoning delta — render dimmed."""
        import html as html_mod
        if not self._in_thinking:
            self._in_thinking = True
            # Start a thinking block
            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml(
                '<div style="margin: 4px 0; padding: 4px 8px; '
                'background-color: #f0f0f0; border-left: 2px solid #ccc; '
                'font-size: 11px; color: #888; font-style: italic;">'
                '<span style="color: #aaa;">{}</span><br>'.format(
                    translate("ChatDockWidget", "Thinking..."))
            )
            self.chat_display.setTextCursor(cursor)

        escaped = html_mod.escape(chunk)
        escaped = escaped.replace("\n", "<br>")

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(f'<span style="color: #999; font-size: 11px;">{escaped}</span>')
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    @Slot(str)
    def _on_token(self, chunk):
        """Handle a streamed token — append to the display."""
        import html as html_mod

        # Close thinking block if transitioning from thinking to regular content
        if self._in_thinking:
            self._in_thinking = False
            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml('</div>')
            self.chat_display.setTextCursor(cursor)

        escaped = html_mod.escape(chunk)
        escaped = escaped.replace("\n", "<br>")
        self._streaming_html += chunk

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(escaped)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _store_tool_results(self, full_response=""):
        """Store tool results from worker into conversation. Idempotent — skips if already stored."""
        if not (self._worker and self._worker._tool_results):
            if full_response:
                self.conversation.add_assistant_message(full_response)
            return

        # Guard against double-storage (e.g., if both response_finished and error fire)
        if getattr(self, '_tool_results_stored', False):
            return
        self._tool_results_stored = True

        try:
            for turn_info in self._worker._tool_results:
                tc_dicts = turn_info["tool_calls"]
                self.conversation.add_assistant_message(
                    turn_info["assistant_text"], tool_calls=tc_dicts
                )
                for r in turn_info["results"]:
                    self.conversation.add_tool_result(r["tool_call_id"], r["content"])
            # Store the final text-only response
            # Extract just the final part (after last tool round)
            last_tool_end = sum(
                len(t["assistant_text"]) for t in self._worker._tool_results
            )
            final_text = full_response[last_tool_end:] if last_tool_end < len(full_response) else full_response
            if final_text.strip():
                self.conversation.add_assistant_message(final_text)
        except Exception as e:
            try:
                import FreeCAD
                FreeCAD.Console.PrintError(f"_store_tool_results error: {e}\n")
            except Exception:
                pass
            # Fallback: store at least the full response text
            if full_response.strip():
                self.conversation.add_assistant_message(full_response)

    @Slot(str)
    def _on_response_finished(self, full_response):
        """Handle completion of LLM response."""
        self._set_loading(False)

        # Close the streaming div
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml("</div></div>")

        # Store in conversation - include any tool call info from the worker
        self._store_tool_results(full_response)

        self._update_token_count()

        # Auto-save conversation for resume capability
        self.conversation.save()

        # Post-response hook
        from ..hooks import fire_hook
        fire_hook("post_response", {
            "response_text": full_response,
            "tool_calls_count": len(self._worker._tool_results) if self._worker and self._worker._tool_results else 0,
            "mode": "plan" if self.mode_combo.currentIndex() == 0 else "act",
        })

        # Auto-save session log when tool calls were used
        if self._worker and self._worker._tool_results:
            self._auto_save_log()

        # Re-render the full chat to get proper code block formatting
        self._rerender_chat()

        # Warn when the model ran out of output budget mid-answer. Without this
        # the plan just stops mid-line with no explanation (issue #50).
        if self._worker and self._worker._response_truncated:
            self._append_html(render_truncation_warning(get_config().max_tokens))

        # Tool call summary (after re-render so it's not wiped)
        if self._worker and self._worker._tool_timeline and not getattr(self, '_summary_rendered', False):
            self._summary_rendered = True
            from .message_view import render_tool_summary
            self._append_html(render_tool_summary(self._worker._tool_timeline))

        # Handle code execution based on mode (only if tools were NOT used)
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        if not (self._worker and self._worker._tool_results):
            code_blocks = extract_code_blocks(full_response)
            if code_blocks and mode == "act":
                self._handle_act_mode(code_blocks)

        # After-changes viewport capture: queue screenshot for next message
        capture_mode = self._capture_mode_override or get_config().viewport_capture
        if capture_mode == "after_changes" and self._worker and self._worker._tool_results:
            vp_img = self._capture_viewport_for_chat()
            if vp_img:
                self._pending_viewport_image = vp_img

        # Run geometry validation if --validate was requested
        if getattr(self, "_validate_pending", False):
            self._validate_pending = False
            self._run_post_validation()

    def _run_post_validation(self):
        """Run geometry validation after skill completes."""
        from .message_view import render_message

        skill_name = getattr(self, "_active_skill_name", "")
        if not skill_name:
            self._append_html(render_message("system",
                "No skill detected \u2014 cannot validate without VALIDATION.md."))
            return

        try:
            from ..extensions.skills import SkillsRegistry
            registry = SkillsRegistry()
            skill = registry.get_skill(skill_name)
        except Exception:
            self._append_html(render_message("system",
                f"Could not load skill '{skill_name}'."))
            return

        if not skill or not skill.validation_path:
            self._append_html(render_message("system",
                f"Skill '{skill_name}' has no VALIDATION.md \u2014 skipping validation."))
            return

        try:
            with open(skill.validation_path) as f:
                validation_content = f.read()
        except OSError as e:
            self._append_html(render_message("system",
                f"Could not read VALIDATION.md: {e}"))
            return

        # Get params from report_skill_params tool
        from ..tools.freecad_tools import (
            get_reported_skill_params, clear_reported_skill_params,
        )
        params = get_reported_skill_params() or {}
        clear_reported_skill_params()

        if not params:
            self._append_html(render_message("system",
                "No parameters reported \u2014 LLM did not call report_skill_params. "
                "Cannot validate."))
            return

        try:
            import FreeCAD as App
            doc = App.ActiveDocument
        except ImportError:
            self._append_html(render_message("system",
                "FreeCAD not available \u2014 cannot validate."))
            return

        if not doc:
            self._append_html(render_message("system",
                "No active document \u2014 cannot validate."))
            return

        from ..extensions.skill_validator import validate_skill, compute_pass_rate
        results = validate_skill(doc, params, validation_content)

        if not results:
            self._append_html(render_message("system",
                "No validation checks found."))
            return

        # Format results
        passed = sum(1 for r in results if r.passed)
        lines = [f"Validation: {passed}/{len(results)} checks passed"]
        for r in results:
            icon = "\u2713" if r.passed else "\u2717"
            lines.append(f"  {icon}  {r.message}")

        self._append_html(render_message("system", "\n".join(lines)))

    @Slot(str)
    def _on_error(self, error_msg):
        """Handle LLM communication error.

        Preserves any tool results from earlier turns, then appends the error
        without re-rendering (to keep the streaming HTML intact).
        """
        self._set_loading(False)

        # Close the streaming div
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml("</div></div>")

        # Store any tool results that were collected before the error
        self._store_tool_results()

        # Save conversation so tool results aren't lost
        if len(self.conversation.messages) > 1:
            self.conversation.save()
            if self._worker and self._worker._tool_results:
                self._auto_save_log()

        # If tools ran successfully but the final LLM turn failed,
        # generate a summary from the tool trace instead of just showing an error.
        if self._worker and self._worker._tool_results:
            summary_parts = []
            for turn in self._worker._tool_results:
                for tc, r in zip(turn["tool_calls"], turn["results"]):
                    summary_parts.append(f"- **{tc['name']}**: {r['content']}")
            summary = "\n".join(summary_parts)
            self._append_html(render_message(
                "assistant",
                translate("ChatDockWidget",
                          "All operations completed successfully:") + "\n\n" + summary
            ))
            # Store the summary in conversation
            self.conversation.add_assistant_message(
                translate("ChatDockWidget",
                          "All operations completed successfully:") + "\n\n" + summary
            )
            self.conversation.save()
        else:
            # No tool results — show the raw error
            self._append_html(render_message("system", translate("ChatDockWidget", "Error: ") + error_msg))

    # ── Tool call handlers ──────────────────────────────────

    @Slot(str, str)
    def _on_tool_call_started(self, tool_name, call_id):
        """Render tool call start in the chat."""
        self._append_html(render_tool_call(tool_name, call_id, started=True))

    @Slot(str, str, bool, str)
    def _on_tool_call_finished(self, tool_name, call_id, success, output):
        """Render tool call result in the chat."""
        self._append_html(render_tool_call(
            tool_name, call_id, started=False, success=success, output=output
        ))

    def _on_vision_note(self, message: str):
        """Show a subtle note when images are auto-described."""
        self._append_html(
            f'<div style="color: #888; font-size: 9pt; margin: 2px 12px;">'
            f'{message}</div>'
        )

    @Slot(str, str)
    def _execute_tool_call(self, tool_name, arguments_json):
        """Execute a tool call on the main thread. Connected to worker's tool_exec_requested signal."""
        if not self._tool_registry:
            result = {"success": False, "output": "", "error": "No tool registry"}
        else:
            try:
                arguments = json.loads(arguments_json)
            except json.JSONDecodeError:
                arguments = {}
            tool_result = self._tool_registry.execute(tool_name, arguments)
            result = {
                "success": tool_result.success,
                "output": tool_result.output,
                "error": tool_result.error,
            }

        # Signal the worker thread that the result is ready
        if self._worker:
            self._worker.set_tool_result(result)

    # ── Code execution ──────────────────────────────────────

    def _handle_act_mode(self, code_blocks):
        """Execute code blocks in Act mode."""
        cfg = get_config()

        for code in code_blocks:
            if cfg.auto_execute:
                result = execute_code(code)
            else:
                try:
                    import FreeCADGui as Gui
                    parent = Gui.getMainWindow()
                except ImportError:
                    parent = self
                dlg = CodeReviewDialog(code, parent)
                dlg.exec()
                if dlg.fix_requested and dlg.last_error_result:
                    self._handle_execution_error(dlg.last_error_result)
                    return
                result = dlg.get_result()
                if not result:
                    continue

            self._append_html(render_execution_result(
                result.success, result.stdout, result.stderr
            ))

            if result.success:
                # Reset retry counter on success
                self._retry_count = 0
            else:
                self._handle_execution_error(result)
                break

    def _handle_execution_error(self, result):
        """Handle code execution failure — send error back to LLM for self-correction."""
        if self._retry_count >= get_config().max_retries:
            self._append_html(render_message(
                "system",
                translate("ChatDockWidget",
                          "Max retries ({}) reached. "
                          "Please review the error and provide guidance.").format(
                    get_config().max_retries)
            ))
            self._retry_count = 0
            return

        self._retry_count += 1
        error_msg = translate(
            "ChatDockWidget",
            "The code failed with the following error:\n\n"
            "{}\n\n"
            "Please fix the code and try again. (Attempt {}/{})").format(
                result.stderr, self._retry_count, get_config().max_retries)

        # Attach a viewport snapshot so vision-capable LLMs can see the state
        # that produced the error — especially useful for "runs but result is
        # wrong" cases the user flagged via the Fix-with-AI composer.
        capture_mode = (getattr(self, "_capture_mode_override", None)
                        or get_config().viewport_capture)
        vp_img = (self._capture_viewport_for_chat()
                  if capture_mode != "off" else None)
        self.conversation.add_system_message(
            error_msg, images=[vp_img] if vp_img else None)
        self._append_html(render_message("system", error_msg))

        from ..core.system_prompt import build_system_prompt
        from ..llm.client import should_strip_thinking
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        system_prompt = build_system_prompt(mode=mode)
        cfg = get_config()
        strip = should_strip_thinking(
            cfg.provider.model, cfg.strip_thinking_history)
        # This retry attached a viewport snapshot above; drop history images
        # for non-vision models so they aren't sent raw (issue #30).
        messages = self.conversation.get_messages_for_api(
            strip_images=not cfg.supports_vision, strip_thinking=strip)

        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )

        self._tool_results_stored = False
        self._worker = _LLMWorker(messages, system_prompt, parent=self)
        self._worker.token_received.connect(self._on_token)
        self._worker.response_finished.connect(self._on_response_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def execute_code_from_plan(self, code):
        """Execute a code block from Plan mode (called from Execute button)."""
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        dlg = CodeReviewDialog(code, parent)
        dlg.exec()
        if dlg.fix_requested and dlg.last_error_result:
            self._handle_execution_error(dlg.last_error_result)
            return
        result = dlg.get_result()

        if result:
            self._append_html(render_execution_result(
                result.success, result.stdout, result.stderr
            ))
            if result.success:
                self.conversation.add_system_message(
                    translate("ChatDockWidget", "Code executed successfully.") + "\n" + result.stdout
                )
            else:
                self.conversation.add_system_message(
                    translate("ChatDockWidget", "Code execution failed:") + "\n" + result.stderr
                )

    # ── Skill commands ──────────────────────────────────────

    def _handle_skill_command(self, text):
        """Handle /command-style skill invocations. Returns True if handled."""
        from ..extensions.skills import SkillsRegistry
        registry = SkillsRegistry()
        result = registry.match_command(text)
        if not result:
            return False

        skill_name, args = result
        skill = registry.get_skill(skill_name)
        if not skill:
            return False

        # Collect attachments (images/documents) from the strip and attach
        # them to the visible user message, same as a regular send.
        pending_images = self._attachment_strip.get_images() or None
        pending_docs = self._attachment_strip.get_documents() or None

        # Display the command (with any attachments)
        self.conversation.add_user_message(text, images=pending_images,
                                           documents=pending_docs)
        display_content = self.conversation.messages[-1]["content"]
        self._append_html(render_message("user", display_content))
        self._attachment_strip.clear()

        # Execute the skill
        exec_result = registry.execute_skill(skill_name, args)

        # Check if this is the optimize-skill handler
        if skill_name == "optimize-skill":
            self._optimization_active = True

        self._active_skill_name = skill_name

        if exec_result.get("inject_prompt"):
            # Inject skill prompt and send to LLM
            prompt_text = exec_result["inject_prompt"]
            if args:
                prompt_text += f"\n\nUser request: {args}"
            self.conversation.add_user_message(prompt_text)
            # Trigger LLM with the injected prompt
            self._send_with_injected_prompt()
        elif exec_result.get("output"):
            self._append_html(render_message("system", exec_result["output"]))
            self.conversation.add_system_message(exec_result["output"])

        return True

    def _send_with_injected_prompt(self):
        """Send the current conversation to the LLM (used after skill injection).

        Reuses _continue_send to ensure tools are available in Act mode.
        """
        self._continue_send()

    # ── UI helpers ──────────────────────────────────────────

    def _append_html(self, html_str):
        """Append HTML to the chat display and scroll to bottom."""
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html_str)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _rerender_chat(self):
        """Re-render the entire chat history with proper formatting."""
        try:
            html_parts = []
            mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"

            for msg in self.conversation.messages:
                if msg["role"] == "tool_result":
                    # Tool results are rendered inline via tool_call_finished signals
                    continue
                elif msg["role"] == "assistant" and msg.get("tool_calls"):
                    # Render assistant text + tool call indicators
                    if msg.get("content"):
                        html_parts.append(render_message("assistant", msg["content"]))
                    for tc in msg["tool_calls"]:
                        html_parts.append(render_tool_call(
                            tc["name"], tc["id"], started=False, success=True,
                            output=f"Called with: {json.dumps(tc['arguments'], indent=2)}"
                        ))
                else:
                    html_parts.append(render_message(msg["role"], msg.get("content", "")))

                if mode == "plan" and msg["role"] == "assistant":
                    content = Conversation.extract_text(msg.get("content", ""))
                    for code in extract_code_blocks(content):
                        html_parts.append(render_plan_buttons(code))
                    # A block cut off at max_tokens gets Copy but not Execute —
                    # running a half-written script would fail or leave partial
                    # geometry behind (issue #50).
                    partial = extract_truncated_block(content)
                    if partial:
                        html_parts.append(render_plan_buttons(partial, allow_execute=False))

            full_html = "".join(html_parts)
            self.chat_display.setHtml(full_html)

            scrollbar = self.chat_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except Exception:
            pass  # Keep existing display content on error

    def _handle_anchor_click(self, url):
        """Handle clicks on anchor links in the chat (Execute/Copy/Image buttons)."""
        import base64
        url_str = url.toString() if hasattr(url, "toString") else str(url)

        if url_str.startswith("image:"):
            self._show_image_dialog(url_str)
            return
        elif url_str.startswith("execute:"):
            encoded = url_str[8:]
            try:
                code = base64.b64decode(encoded).decode()
                self.execute_code_from_plan(code)
            except Exception:
                pass
        elif url_str.startswith("copy:"):
            encoded = url_str[5:]
            try:
                code = base64.b64decode(encoded).decode()
                clipboard = QApplication.clipboard()
                clipboard.setText(code)
            except Exception:
                pass

    def _show_image_dialog(self, url_str: str):
        """Show a full-size image in a dialog when a thumbnail is clicked."""
        import base64 as b64
        try:
            block_idx = int(url_str.split(":", 1)[1])
        except (ValueError, IndexError):
            return

        # Find the most recent message with content blocks containing this index
        for msg in reversed(self.conversation.messages):
            content = msg.get("content")
            if isinstance(content, list) and block_idx < len(content):
                block = content[block_idx]
                if block.get("type") == "image":
                    img_data = b64.b64decode(block["data"])
                    pixmap = QtGui.QPixmap()
                    pixmap.loadFromData(img_data)
                    if pixmap.isNull():
                        return

                    dlg = QtWidgets.QDialog(self)
                    dlg.setWindowTitle("Image")
                    dlg_layout = QVBoxLayout(dlg)
                    label = QLabel()
                    # Scale down if very large
                    try:
                        screen_size = QtWidgets.QApplication.primaryScreen().availableGeometry()
                        max_w = int(screen_size.width() * 0.8)
                        max_h = int(screen_size.height() * 0.8)
                    except Exception:
                        max_w, max_h = 1024, 768
                    if pixmap.width() > max_w or pixmap.height() > max_h:
                        pixmap = pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation)
                    label.setPixmap(pixmap)
                    dlg_layout.addWidget(label)
                    dlg.show()
                    return

    def _set_loading(self, loading):
        """Enable/disable input while LLM is processing."""
        colors = _get_theme_colors()
        self.send_btn.setEnabled(True)
        self.input_edit.setReadOnly(loading)
        if loading:
            self.send_btn.setText("Stop")
            self.send_btn.setStyleSheet(
                f"QPushButton {{ background-color: {colors['system_label']}; color: white; "
                f"font-weight: bold; padding: 8px 16px; }}"
            )
        else:
            self.send_btn.setText(translate("ChatDockWidget", "Send"))
            self.send_btn.setStyleSheet(
                f"QPushButton {{ background-color: {colors['tool_success_border']}; color: white; "
                f"font-weight: bold; padding: 8px 16px; }}"
            )

    def _update_token_count(self):
        """Update the token estimate display."""
        tokens = self.conversation.estimated_tokens()
        if tokens >= 1000:
            self.token_label.setText(
                translate("ChatDockWidget", "tokens: ~{:.1f}k").format(tokens / 1000))
        else:
            self.token_label.setText(
                translate("ChatDockWidget", "tokens: ~{}").format(tokens))

    def _connect_mcp_servers(self, cfg, *, only_deferred=None):
        """Connect to configured MCP servers.

        Args:
            only_deferred: If True, connect only deferred servers.
                If False, connect only non-deferred servers.
                If None, connect all servers.
        """
        if not cfg.mcp_servers:
            self._mcp_connected = True
            return
        try:
            from ..mcp.manager import get_mcp_manager
            manager = get_mcp_manager()
            prev_servers = set(manager.connected_servers)
            manager.connect_all(cfg.mcp_servers, only_deferred=only_deferred)
            if only_deferred is None or only_deferred is True:
                self._mcp_connected = True
            new_servers = set(manager.connected_servers) - prev_servers
            if new_servers:
                self._append_html(
                    '<div style="margin: 4px 0; padding: 4px 8px; '
                    'background-color: #e8f5e9; border-left: 3px solid #4caf50; '
                    'border-radius: 0 4px 4px 0; font-size: 11px; color: #2e7d32;">'
                    '{}</div>'.format(
                        translate("ChatDockWidget", "MCP: connected to {}").format(
                            ", ".join(sorted(new_servers))))
                )
        except Exception as e:
            if only_deferred is None or only_deferred is True:
                self._mcp_connected = True  # Don't retry on failure
            self._append_html(
                '<div style="margin: 4px 0; padding: 4px 8px; '
                'background-color: #fff3e0; border-left: 3px solid #ff9800; '
                'border-radius: 0 4px 4px 0; font-size: 11px; color: #e65100;">'
                '{}</div>'.format(
                    translate("ChatDockWidget", "MCP connection error: {}").format(str(e)))
            )

    def closeEvent(self, event):
        """Save conversation, dock layout, and disconnect MCP when widget is closed."""
        if self.conversation.messages:
            self.conversation.save()
        # Snapshot final dock layout — dockLocationChanged/topLevelChanged
        # don't always fire for tabify-by-drag, so closeEvent is our backstop.
        self._save_dock_state()
        # Disconnect MCP servers
        if self._mcp_connected:
            try:
                from ..mcp.manager import get_mcp_manager
                get_mcp_manager().disconnect_all()
            except Exception:
                pass
        super().closeEvent(event)


# ── Dock layout persistence helpers ─────────────────────────

def _area_to_str(area):
    """Convert Qt.DockWidgetArea to a JSON-friendly string."""
    mapping = {
        Qt.LeftDockWidgetArea: "left",
        Qt.RightDockWidgetArea: "right",
        Qt.TopDockWidgetArea: "top",
        Qt.BottomDockWidgetArea: "bottom",
    }
    return mapping.get(area, "")


def _str_to_area(s):
    """Inverse of _area_to_str. Defaults to right on unknown."""
    mapping = {
        "left": Qt.LeftDockWidgetArea,
        "right": Qt.RightDockWidgetArea,
        "top": Qt.TopDockWidgetArea,
        "bottom": Qt.BottomDockWidgetArea,
    }
    return mapping.get(s, Qt.RightDockWidgetArea)


def _apply_saved_dock_state(mw, dock):
    """Reposition dock per saved AppConfig fields.

    Must be called after mw.addDockWidget. Prefers the full mw.saveState()
    blob (captures tabification); falls back to the surgical fields if the
    blob is absent or restoreState rejects it.
    """
    cfg = get_config()

    restored_via_state = False
    if cfg.chat_dock_mw_state:
        try:
            import base64
            raw = base64.b64decode(cfg.chat_dock_mw_state.encode("ascii"))
            ba = QtCore.QByteArray(raw)
            restored_via_state = bool(mw.restoreState(ba))
        except Exception:
            restored_via_state = False

    if restored_via_state:
        # Qt handled area, tabification, and splitter sizes. Apply floating
        # geometry only — restoreState sometimes loses the exact window rect
        # for floating docks.
        if cfg.chat_dock_floating and len(cfg.chat_dock_geometry) == 4:
            try:
                dock.setFloating(True)
                x, y, w, h = cfg.chat_dock_geometry
                dock.setGeometry(int(x), int(y), int(w), int(h))
            except Exception:
                pass
        return

    # Fallback path: surgical fields used only when mw.saveState blob is
    # absent (first run) or restoreState fails.
    try:
        for name in cfg.chat_dock_tabified_with or []:
            if not name:
                continue
            sibling = mw.findChild(QDockWidget, name)
            if sibling is not None and sibling is not dock:
                mw.tabifyDockWidget(sibling, dock)
        if cfg.chat_dock_floating and len(cfg.chat_dock_geometry) == 4:
            dock.setFloating(True)
            x, y, w, h = cfg.chat_dock_geometry
            dock.setGeometry(int(x), int(y), int(w), int(h))
    except Exception:
        pass


# ── Singleton access ────────────────────────────────────────

_dock_widget = None


def get_chat_dock(create=True):
    """Get or create the singleton chat dock widget."""
    global _dock_widget

    if _dock_widget is not None:
        return _dock_widget

    if not create:
        return None

    try:
        import FreeCADGui as Gui
        mw = Gui.getMainWindow()
    except ImportError:
        mw = None

    _dock_widget = ChatDockWidget(mw)

    if mw:
        cfg = get_config()
        area = _str_to_area(cfg.chat_dock_area)
        mw.addDockWidget(area, _dock_widget)
        _apply_saved_dock_state(mw, _dock_widget)
        # Enable state persistence now that restore has finished. Prevents
        # the addDockWidget-triggered signal avalanche from overwriting the
        # previous session's saved state before we've had a chance to read it.
        _dock_widget._saves_enabled = True

    return _dock_widget
