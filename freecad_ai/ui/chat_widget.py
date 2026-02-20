"""Main chat dock widget for FreeCAD AI.

Provides the primary user interface: a scrollable chat history,
input field, mode toggle (Plan/Act), and settings access.

LLM calls run in a QThread to keep the UI responsive, with
streaming text pushed via signals.
"""

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
from .message_view import render_message, render_code_block, render_execution_result
from .code_review_dialog import CodeReviewDialog


# ── LLM Worker Thread ───────────────────────────────────────

class _LLMWorker(QThread):
    """Background thread that streams LLM responses."""

    token_received = Signal(str)       # Single token/chunk
    response_finished = Signal(str)    # Full response text
    error_occurred = Signal(str)       # Error message

    def __init__(self, messages, system_prompt, parent=None):
        super().__init__(parent)
        self.messages = messages
        self.system_prompt = system_prompt
        self._full_response = ""

    def run(self):
        try:
            from ..llm.client import create_client_from_config
            client = create_client_from_config()

            for chunk in client.stream(self.messages, system=self.system_prompt):
                self._full_response += chunk
                self.token_received.emit(chunk)

            self.response_finished.emit(self._full_response)
        except Exception as e:
            self.error_occurred.emit(str(e))


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

        # Add to conversation and display
        self.conversation.add_user_message(text)
        self._append_html(render_message("user", text))

        # Build system prompt
        from ..core.system_prompt import build_system_prompt
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        system_prompt = build_system_prompt(mode=mode)

        # Get messages for API
        messages = self.conversation.get_messages_for_api()

        # Start streaming
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

        # Store in conversation
        self.conversation.add_assistant_message(full_response)
        self._update_token_count()

        # Re-render the full chat to get proper code block formatting
        self._rerender_chat()

        # Handle code execution based on mode
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        code_blocks = extract_code_blocks(full_response)

        if code_blocks:
            if mode == "act":
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

            if not result.success:
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
            html_parts.append(render_message(msg["role"], msg["content"]))

            if mode == "plan" and msg["role"] == "assistant":
                code_blocks = extract_code_blocks(msg["content"])
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
            self._retry_count_for_success = self._retry_count
        else:
            self.send_btn.setText("Send")
            if self._retry_count == getattr(self, "_retry_count_for_success", 0):
                self._retry_count = 0

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
