"""Main chat dock widget for FreeCAD AI.

Provides the primary user interface: a scrollable chat history,
input field, mode toggle (Plan/Act), and settings access.

LLM calls run in a QThread to keep the UI responsive, with
streaming text pushed via signals. When tools are enabled,
the worker implements an agentic loop: stream response, execute
tool calls on the main thread, feed results back to the LLM.
"""

import json

from .compat import QtWidgets, QtCore, QtGui

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

from ..config import get_config, save_current_config
from ..core.conversation import Conversation
from ..core.executor import extract_code_blocks, execute_code
from .message_view import render_message, render_code_block, render_execution_result, render_tool_call
from .code_review_dialog import CodeReviewDialog


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
    response_finished = Signal(str)        # Full response text (final turn only)
    error_occurred = Signal(str)           # Error message
    tool_call_started = Signal(str, str)   # (tool_name, call_id)
    tool_call_finished = Signal(str, str, bool, str)  # (tool_name, call_id, success, output)
    tool_exec_requested = Signal(str, str) # (tool_name, arguments_json) — dispatches to main thread

    def __init__(self, messages, system_prompt, tools=None, registry=None,
                 api_style="openai", parent=None):
        super().__init__(parent)
        self.messages = list(messages)
        self.system_prompt = system_prompt
        self.tools = tools
        self.registry = registry
        self.api_style = api_style
        self._full_response = ""
        self._tool_results = []
        self._tool_result_ready = QtCore.QMutex()
        self._tool_result_wait = QtCore.QWaitCondition()
        self._pending_result = None
        self._max_tool_turns = 10  # Safety limit

    def run(self):
        try:
            from ..llm.client import create_client_from_config
            client = create_client_from_config()

            if not self.tools:
                # Simple non-tool streaming (backward compat)
                self._simple_stream(client)
                return

            # Agentic tool loop
            self._tool_loop(client)

        except Exception as e:
            self.error_occurred.emit(str(e))

    def _simple_stream(self, client):
        """Stream without tools (original behavior)."""
        for chunk in client.stream(self.messages, system=self.system_prompt):
            self._full_response += chunk
            self.token_received.emit(chunk)
        self.response_finished.emit(self._full_response)

    def _tool_loop(self, client):
        """Agentic loop: stream -> execute tools -> feed results -> repeat."""
        messages = list(self.messages)

        for turn in range(self._max_tool_turns):
            text_parts = []
            tool_calls = []

            # Stream with tools
            for event in client.stream_with_tools(
                messages, system=self.system_prompt, tools=self.tools
            ):
                if event.type == "text_delta":
                    text_parts.append(event.text)
                    self._full_response += event.text
                    self.token_received.emit(event.text)
                elif event.type == "tool_call_start":
                    if event.tool_call:
                        self.tool_call_started.emit(event.tool_call.name, event.tool_call.id)
                elif event.type == "tool_call_end":
                    if event.tool_call:
                        tool_calls.append(event.tool_call)
                elif event.type == "done":
                    break

            turn_text = "".join(text_parts)

            if not tool_calls:
                # No tool calls — we're done
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
                messages.append({
                    "role": "assistant",
                    "content": turn_text or None,
                    "tool_calls": oai_tcs,
                })

            # Execute each tool call on the main thread
            tool_result_messages = []
            for tc in tool_calls:
                result = self._execute_tool_on_main_thread(tc.name, tc.arguments)
                success = result.get("success", False)
                output = result.get("output", "")
                error = result.get("error", "")
                result_text = output if success else f"Error: {error}"

                self.tool_call_finished.emit(tc.name, tc.id, success, result_text)

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

        # If we reach here, we hit the max turns limit
        self._full_response += "\n\n[Reached maximum tool call iterations]"
        self.token_received.emit("\n\n[Reached maximum tool call iterations]")
        self.response_finished.emit(self._full_response)

    def _execute_tool_on_main_thread(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch tool execution to the main thread and wait for the result.

        Emits tool_exec_requested signal (runs slot on main thread via
        Qt.QueuedConnection), then blocks on a mutex until the main thread
        calls set_tool_result().
        """
        self._pending_result = None
        self.tool_exec_requested.emit(tool_name, json.dumps(arguments))

        # Wait for result with timeout (30s) to avoid deadlock
        self._tool_result_ready.lock()
        deadline = 30000  # ms
        while self._pending_result is None:
            if not self._tool_result_wait.wait(self._tool_result_ready, deadline):
                # Timed out
                self._tool_result_ready.unlock()
                return {"success": False, "output": "", "error": "Tool execution timed out (main thread did not respond)"}
        self._tool_result_ready.unlock()

        return self._pending_result

    def set_tool_result(self, result: dict):
        """Called from the main thread to provide a tool execution result."""
        self._tool_result_ready.lock()
        self._pending_result = result
        self._tool_result_wait.wakeAll()
        self._tool_result_ready.unlock()


# ── Chat Dock Widget ────────────────────────────────────────

class ChatDockWidget(QDockWidget):
    """Main chat dock widget for FreeCAD AI."""

    def __init__(self, parent=None):
        super().__init__("FreeCAD AI", parent)
        self.setObjectName("FreeCADAIChatDock")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self.conversation = Conversation()
        self._worker = None
        self._streaming_html = ""
        self._retry_count = 0
        self._anchor_connected = False
        self._tool_registry = None

        self._build_ui()

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Header bar ──
        header = QHBoxLayout()

        title = QLabel("<b>FreeCAD AI</b>")
        header.addWidget(title)
        header.addStretch()

        # Mode toggle
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Plan", "Act"])
        cfg = get_config()
        self.mode_combo.setCurrentIndex(0 if cfg.mode == "plan" else 1)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header.addWidget(QLabel("Mode:"))
        header.addWidget(self.mode_combo)

        # Settings button
        settings_btn = QPushButton("Settings")
        settings_btn.setMaximumWidth(80)
        settings_btn.clicked.connect(self._open_settings)
        header.addWidget(settings_btn)

        layout.addLayout(header)

        # ── Chat display ──
        self.chat_display = QTextBrowser()
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.setOpenLinks(False)
        self.chat_display.setFont(QFont("Sans", 10))
        self.chat_display.setStyleSheet(
            "QTextBrowser { border: 1px solid #ccc; background-color: #ffffff; }"
        )
        self.chat_display.anchorClicked.connect(self._handle_anchor_click)
        layout.addWidget(self.chat_display, 1)

        # ── Input area ──
        input_layout = QHBoxLayout()

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("Describe what you want to create...")
        self.input_edit.setMaximumHeight(80)
        self.input_edit.setFont(QFont("Sans", 10))
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit, 1)

        self.send_btn = QPushButton("Send")
        self.send_btn.setMinimumHeight(40)
        self.send_btn.setStyleSheet(
            "QPushButton { background-color: #3daee9; color: white; "
            "font-weight: bold; padding: 8px 16px; }"
        )
        self.send_btn.clicked.connect(self._send_message)
        input_layout.addWidget(self.send_btn)

        layout.addLayout(input_layout)

        # ── Footer ──
        footer = QHBoxLayout()

        new_chat_btn = QPushButton("+ New Chat")
        new_chat_btn.setMaximumWidth(100)
        new_chat_btn.clicked.connect(self._new_chat)
        footer.addWidget(new_chat_btn)

        save_log_btn = QPushButton("Save Log")
        save_log_btn.setMaximumWidth(80)
        save_log_btn.setToolTip("Save session log for debugging (tool calls, arguments, results)")
        save_log_btn.clicked.connect(self._save_session_log)
        footer.addWidget(save_log_btn)

        footer.addStretch()

        self.token_label = QLabel("tokens: ~0")
        self.token_label.setStyleSheet("color: #888; font-size: 11px;")
        footer.addWidget(self.token_label)

        layout.addLayout(footer)

        self.setWidget(container)

    # ── Event filter (Enter to send) ────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    return False  # Shift+Enter: newline
                else:
                    self._send_message()
                    return True
        return super().eventFilter(obj, event)

    # ── Actions ─────────────────────────────────────────────

    def _send_message(self):
        """Send the current input to the LLM."""
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if self._worker and self._worker.isRunning():
            return

        self.input_edit.clear()
        self._retry_count = 0  # Reset retries for new user message

        # Check for skill commands
        if text.startswith("/"):
            handled = self._handle_skill_command(text)
            if handled:
                return

        # Add to conversation and display
        self.conversation.add_user_message(text)
        self._append_html(render_message("user", text))

        # Build system prompt
        from ..core.system_prompt import build_system_prompt
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        cfg = get_config()

        # Determine if we should use tools
        use_tools = cfg.enable_tools and mode == "act"
        tools_schema = None
        api_style = "openai"

        if use_tools:
            from ..tools.setup import create_default_registry
            from ..llm.providers import get_api_style
            self._tool_registry = create_default_registry()
            api_style = get_api_style(cfg.provider.name)
            if api_style == "anthropic":
                tools_schema = self._tool_registry.to_anthropic_schema()
            else:
                tools_schema = self._tool_registry.to_openai_schema()
            system_prompt = build_system_prompt(mode=mode, tools_enabled=True)
        else:
            self._tool_registry = None
            system_prompt = build_system_prompt(mode=mode)

        # Get messages for API
        messages = self.conversation.get_messages_for_api(api_style=api_style)

        # Start streaming
        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )

        self._worker = _LLMWorker(
            messages, system_prompt,
            tools=tools_schema, registry=self._tool_registry,
            api_style=api_style, parent=self,
        )
        self._worker.token_received.connect(self._on_token)
        self._worker.response_finished.connect(self._on_response_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.tool_call_started.connect(self._on_tool_call_started)
        self._worker.tool_call_finished.connect(self._on_tool_call_finished)
        self._worker.tool_exec_requested.connect(self._execute_tool_call)
        self._worker.start()

    def _on_mode_changed(self, index):
        """Update config when mode is toggled."""
        cfg = get_config()
        cfg.mode = "plan" if index == 0 else "act"
        save_current_config()

    def _open_settings(self):
        """Open the settings dialog."""
        from .settings_dialog import SettingsDialog
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        dlg = SettingsDialog(parent)
        dlg.exec()

    def _new_chat(self):
        """Start a new conversation."""
        if self.conversation.messages:
            self.conversation.save()

        self.conversation = Conversation()
        self.chat_display.clear()
        self._update_token_count()

    def _save_session_log(self):
        """Save the current session log as JSON for debugging."""
        import os
        from datetime import datetime

        log_dir = os.path.expanduser("~/.config/FreeCAD/FreeCADAI/logs")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(log_dir, f"session_{timestamp}.json")

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

            self._append_html(render_message(
                "system", f"Session log saved to: {filepath}"
            ))
        except Exception as e:
            self._append_html(render_message(
                "system", f"Failed to save log: {e}"
            ))

    def _auto_save_log(self):
        """Auto-save tool trace after each tool-using response."""
        import os
        from datetime import datetime

        log_dir = os.path.expanduser("~/.config/FreeCAD/FreeCADAI/logs")
        os.makedirs(log_dir, exist_ok=True)

        filepath = os.path.join(log_dir, "latest_session.json")

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
    def _on_token(self, chunk):
        """Handle a streamed token — append to the display."""
        import html as html_mod
        escaped = html_mod.escape(chunk)
        escaped = escaped.replace("\n", "<br>")
        self._streaming_html += chunk

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(escaped)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    @Slot(str)
    def _on_response_finished(self, full_response):
        """Handle completion of LLM response."""
        self._set_loading(False)

        # Close the streaming div
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml("</div></div>")

        # Store in conversation - include any tool call info from the worker
        if self._worker and self._worker._tool_results:
            # Store each intermediate tool turn
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
        else:
            self.conversation.add_assistant_message(full_response)

        self._update_token_count()

        # Auto-save session log when tool calls were used
        if self._worker and self._worker._tool_results:
            self._auto_save_log()

        # Re-render the full chat to get proper code block formatting
        self._rerender_chat()

        # Handle code execution based on mode (only if tools were NOT used)
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        if not (self._worker and self._worker._tool_results):
            code_blocks = extract_code_blocks(full_response)
            if code_blocks and mode == "act":
                self._handle_act_mode(code_blocks)

    @Slot(str)
    def _on_error(self, error_msg):
        """Handle LLM communication error."""
        self._set_loading(False)

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml("</div></div>")

        self._append_html(render_message("system", "Error: " + error_msg))
        self._rerender_chat()

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
                "Max retries ({}) reached. "
                "Please review the error and provide guidance.".format(
                    get_config().max_retries)
            ))
            self._retry_count = 0
            return

        self._retry_count += 1
        error_msg = (
            "The code failed with the following error:\n\n"
            "{}\n\n"
            "Please fix the code and try again. (Attempt {}/{})".format(
                result.stderr, self._retry_count, get_config().max_retries)
        )

        self.conversation.add_system_message(error_msg)
        self._append_html(render_message("system", error_msg))

        from ..core.system_prompt import build_system_prompt
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        system_prompt = build_system_prompt(mode=mode)
        messages = self.conversation.get_messages_for_api()

        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )

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
        result = dlg.get_result()

        if result:
            self._append_html(render_execution_result(
                result.success, result.stdout, result.stderr
            ))
            if result.success:
                self.conversation.add_system_message(
                    "Code executed successfully.\n" + result.stdout
                )
            else:
                self.conversation.add_system_message(
                    "Code execution failed:\n" + result.stderr
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

        # Display the command
        self.conversation.add_user_message(text)
        self._append_html(render_message("user", text))

        # Execute the skill
        exec_result = registry.execute_skill(skill_name, args)
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
        """Send the current conversation to the LLM (used after skill injection)."""
        from ..core.system_prompt import build_system_prompt
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        system_prompt = build_system_prompt(mode=mode)
        messages = self.conversation.get_messages_for_api()

        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )

        self._worker = _LLMWorker(messages, system_prompt, parent=self)
        self._worker.token_received.connect(self._on_token)
        self._worker.response_finished.connect(self._on_response_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

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
                content = msg.get("content", "")
                code_blocks = extract_code_blocks(content)
                for code in code_blocks:
                    html_parts.append(self._make_plan_buttons_html(code))

        self.chat_display.setHtml("".join(html_parts))

        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _make_plan_buttons_html(self, code):
        """Create HTML for Plan mode Execute/Copy buttons."""
        import base64
        encoded = base64.b64encode(code.encode()).decode()
        return (
            '<div style="margin: 2px 0 8px 0;">'
            '<a href="execute:{}" style="text-decoration: none; '
            'background-color: #2e7d32; color: white; padding: 3px 12px; '
            'border-radius: 3px; font-size: 12px; margin-right: 6px;">'
            'Execute</a> '
            '<a href="copy:{}" style="text-decoration: none; '
            'background-color: #666; color: white; padding: 3px 12px; '
            'border-radius: 3px; font-size: 12px;">Copy</a>'
            '</div>'.format(encoded, encoded)
        )

    def _handle_anchor_click(self, url):
        """Handle clicks on anchor links in the chat (Execute/Copy buttons)."""
        import base64
        url_str = url.toString() if hasattr(url, "toString") else str(url)

        if url_str.startswith("execute:"):
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

    def _set_loading(self, loading):
        """Enable/disable input while LLM is processing."""
        self.send_btn.setEnabled(not loading)
        self.input_edit.setReadOnly(loading)
        if loading:
            self.send_btn.setText("...")
        else:
            self.send_btn.setText("Send")

    def _update_token_count(self):
        """Update the token estimate display."""
        tokens = self.conversation.estimated_tokens()
        if tokens >= 1000:
            self.token_label.setText("tokens: ~{:.1f}k".format(tokens / 1000))
        else:
            self.token_label.setText("tokens: ~{}".format(tokens))

    def closeEvent(self, event):
        """Save conversation when widget is closed."""
        if self.conversation.messages:
            self.conversation.save()
        super().closeEvent(event)


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
        mw.addDockWidget(Qt.RightDockWidgetArea, _dock_widget)

    return _dock_widget
