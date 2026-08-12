"""Settings dialog for FreeCAD AI.

Provides a GUI for configuring:
  - LLM provider (Anthropic, OpenAI, Ollama, Gemini, OpenRouter, Moonshot,
    DeepSeek, Qwen, Groq, Mistral, Together, Fireworks, xAI, Cohere,
    SambaNova, MiniMax, Custom)
  - API key, base URL, model name
  - Max tokens, temperature
  - Auto-execute toggle
  - User extension tools
  - Test connection button
"""

import os

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate, translate_branded
from ..branding import PRODUCT_NAME

QDialog = QtWidgets.QDialog
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QComboBox = QtWidgets.QComboBox
QLineEdit = QtWidgets.QLineEdit
QSpinBox = QtWidgets.QSpinBox
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
Signal = QtCore.Signal
QThread = QtCore.QThread
QDoubleValidator = QtGui.QDoubleValidator
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QHeaderView = QtWidgets.QHeaderView

QListWidget = QtWidgets.QListWidget
QListWidgetItem = QtWidgets.QListWidgetItem
QFileDialog = QtWidgets.QFileDialog
QMessageBox = QtWidgets.QMessageBox

from ..config import get_config, save_current_config, PROVIDER_PRESETS
from ..llm.providers import get_provider_names


class _TestConnectionThread(QThread):
    """Background thread for testing LLM connection and detecting capabilities."""
    finished = Signal(bool, str)        # success, message
    vision_result = Signal(bool)        # vision probe result
    capabilities_result = Signal(dict)  # full caps dict (Ollama: vision/tools/thinking)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            from ..llm.client import create_client_from_config
            client = create_client_from_config()
            response = client.test_connection()
            self.finished.emit(True, translate("SettingsDialog", "Connected! Response: ") + response)

            caps = client.detect_capabilities()
            self.vision_result.emit(bool(caps.get("vision", False)))
            self.capabilities_result.emit(caps)
        except Exception as e:
            self.finished.emit(False, str(e))


class _TestRerankerThread(QThread):
    """Background thread for testing the LLM reranker with current dialog values.

    Takes provider/URL/key/model/model_params as arguments rather than
    reading config — so the user can test before saving.
    """
    finished = Signal(bool, str)  # success, message

    def __init__(self, provider_name, base_url, api_key, model,
                 model_params, parent=None):
        super().__init__(parent)
        self._provider = provider_name
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._model_params = dict(model_params or {})

    def run(self):
        try:
            from ..llm.client import LLMClient
            from ..tools.reranker import rerank_tools_llm
            client = LLMClient(
                provider_name=self._provider,
                base_url=self._base_url,
                api_key=self._api_key,
                model=self._model,
                max_tokens=1024,
                temperature=self._model_params.get("temperature", 0.0),
                thinking="off",
                model_params=self._model_params,
            )
            # Small canonical probe set — if reranker is working, the LLM
            # should trivially pick create_sketch and pad_sketch.
            sample = [
                ("create_sketch", "Create a new sketch with geometry"),
                ("pad_sketch", "Extrude a sketch into a solid pad"),
                ("fillet_edges", "Round selected edges with a fillet"),
                ("list_objects", "List all objects in the active document"),
                ("export_stl", "Export an object as an STL mesh file"),
            ]
            messages = []

            def report(m):
                messages.append(m)

            result = rerank_tools_llm(
                sample, "extrude a new sketch into a solid",
                top_n=2, llm_client=client, report=report,
            )
            # Look for explicit failure markers in the diagnostic stream
            failure = next(
                (m for m in messages if "call failed" in m),
                None,
            )
            if failure:
                self.finished.emit(False, failure)
                return

            # Extract the parsed-count and raw-response lines for the report
            parsed_count = 0
            raw_preview = ""
            for m in messages:
                if "parsed" in m and "valid names" in m:
                    # Format: "LLM reranker: parsed N valid names ..."
                    for tok in m.split():
                        if tok.isdigit():
                            parsed_count = int(tok)
                            break
                if "raw response" in m:
                    raw_preview = m

            # An LLM that returned zero valid names (all slots filled by
            # keyword top-up) is effectively not working, even though the
            # HTTP call succeeded. Flag it as an error so the user knows
            # the reranker is doing nothing useful.
            if parsed_count == 0:
                detail = (
                    "LLM returned 0 valid tool names — all picks came from "
                    "keyword fallback. The LLM is responding but not "
                    "producing usable output for reranking. "
                    "Try a more capable or better-suited model."
                )
                if raw_preview:
                    detail += "\n" + raw_preview
                self.finished.emit(False, detail)
                return

            # Partial success: some names from LLM, rest from top-up.
            # Still a green light — LLM is contributing, just not fully.
            topup_count = len(result) - parsed_count
            detail = "Picked: {}".format(", ".join(result))
            detail += " ({} from LLM".format(parsed_count)
            if topup_count > 0:
                detail += ", {} from keyword top-up".format(topup_count)
            detail += ")"
            if raw_preview:
                detail += "\n" + raw_preview
            self.finished.emit(True, detail)
        except Exception as e:
            self.finished.emit(False, "{}: {}".format(type(e).__name__, e))


class SettingsDialog(QDialog):
    """Configuration dialog for FreeCAD AI."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate_branded("SettingsDialog", "%2 Settings"))
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.resize(650, 700)
        self._test_thread = None
        self._last_default_prompt = ""
        self._rerank_last_model = ""
        self._build_ui()
        self._load_from_config()

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)

        # Scrollable content area
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_widget = QtWidgets.QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(scroll_widget)
        outer_layout.addWidget(scroll, 1)  # stretch factor 1 — takes available space

        # Provider group
        provider_group = QGroupBox(translate("SettingsDialog", "LLM Provider"))
        provider_layout = QFormLayout()

        self.provider_combo = QComboBox()
        self.provider_combo.addItems([n.capitalize() for n in get_provider_names()])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addRow(translate("SettingsDialog", "Provider:"), self.provider_combo)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(translate("SettingsDialog", "API key, file:/path/to/token, or cmd:command"))
        self.api_key_edit.setToolTip(translate(
            "SettingsDialog",
            "For secure storage, prefix the value with:\n"
            "  file:/path/to/keyfile  — read key from a file (re-read each call)\n"
            "  cmd:some command        — run command, use stdout as the key\n"
            "Example: cmd:secret-tool lookup service freecad-ai username anthropic"
        ))
        provider_layout.addRow(translate("SettingsDialog", "API Key:"), self.api_key_edit)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        provider_layout.addRow(translate("SettingsDialog", "Base URL:"), self.base_url_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText(translate("SettingsDialog", "Model name"))
        self.model_edit.editingFinished.connect(self._on_model_changed)
        self._last_model_name = ""  # track model name for param save/load
        provider_layout.addRow(translate("SettingsDialog", "Model:"), self.model_edit)

        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)

        # Model Parameters group — fixed fields + freeform key-value table
        model_params_group = QGroupBox(translate("SettingsDialog", "Model Parameters"))
        model_params_layout = QVBoxLayout()

        # Fixed fields (max tokens, context window)
        fixed_layout = QFormLayout()

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 262144)
        self.max_tokens_spin.setSingleStep(1024)
        self.max_tokens_spin.setValue(4096)
        self.max_tokens_spin.setToolTip(
            translate("SettingsDialog",
                      "Maximum output tokens per response.\n"
                      "Context window is determined by the model/provider.")
        )
        fixed_layout.addRow(translate("SettingsDialog", "Max Output Tokens:"), self.max_tokens_spin)

        self.context_window_spin = QSpinBox()
        self.context_window_spin.setRange(4000, 1000000)
        self.context_window_spin.setSingleStep(10000)
        self.context_window_spin.setValue(20000)
        self.context_window_spin.setToolTip(
            translate("SettingsDialog",
                      "Context window size in tokens.\n"
                      "Older messages are automatically compacted\n"
                      "when the conversation exceeds this limit.\n"
                      "Set to your model's context limit or lower\n"
                      "to control API costs.")
        )
        fixed_layout.addRow(translate("SettingsDialog", "Context Window:"), self.context_window_spin)

        self.max_tool_turns_spin = QSpinBox()
        self.max_tool_turns_spin.setRange(0, 999)
        self.max_tool_turns_spin.setSpecialValueText(
            translate("SettingsDialog", "endless"))
        self.max_tool_turns_spin.setValue(30)
        self.max_tool_turns_spin.setToolTip(
            translate("SettingsDialog",
                      "Maximum number of tool-call iterations per response.\n"
                      "0 means no limit (endless). Default: 30.")
        )
        fixed_layout.addRow(
            translate("SettingsDialog", "Max tool-loop turns (0 = endless):"),
            self.max_tool_turns_spin)

        self.execution_timeout_spin = QSpinBox()
        self.execution_timeout_spin.setRange(5, 600)
        self.execution_timeout_spin.setSingleStep(5)
        self.execution_timeout_spin.setValue(30)
        self.execution_timeout_spin.setSuffix(translate("SettingsDialog", " s"))
        self.execution_timeout_spin.setToolTip(
            translate("SettingsDialog",
                      "Time budget for executing one generated code block "
                      "(sandbox dry-run and live run).\n"
                      "Raise it for heavy operations on large/detailed models "
                      "(e.g. scaling). Default: 30.")
        )
        fixed_layout.addRow(
            translate("SettingsDialog", "Code execution timeout:"),
            self.execution_timeout_spin)

        model_params_layout.addLayout(fixed_layout)

        # Freeform sampling parameters table (saved per model name)
        model_params_layout.addWidget(QLabel(
            translate("SettingsDialog",
                      "Sampling parameters sent with each request (saved per model):")
        ))

        self.model_params_table = QTableWidget(0, 2)
        self.model_params_table.setHorizontalHeaderLabels([
            translate("SettingsDialog", "Parameter"),
            translate("SettingsDialog", "Value"),
        ])
        self.model_params_table.horizontalHeader().setStretchLastSection(True)
        self.model_params_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Interactive)
        self.model_params_table.setColumnWidth(0, 160)
        self.model_params_table.setMaximumHeight(140)
        self.model_params_table.setToolTip(
            translate("SettingsDialog",
                      "Parameters are merged into the API request body.\n"
                      "Common: temperature, top_p, top_k, n,\n"
                      "presence_penalty, frequency_penalty, repetition_penalty.\n"
                      "Values are auto-detected as number or string.")
        )
        model_params_layout.addWidget(self.model_params_table)

        mp_btn_layout = QHBoxLayout()
        mp_add_btn = QPushButton(translate("SettingsDialog", "Add"))
        mp_add_btn.clicked.connect(self._add_model_param)
        mp_btn_layout.addWidget(mp_add_btn)

        mp_remove_btn = QPushButton(translate("SettingsDialog", "Remove"))
        mp_remove_btn.clicked.connect(self._remove_model_param)
        mp_btn_layout.addWidget(mp_remove_btn)

        mp_defaults_btn = QPushButton(translate("SettingsDialog", "Load Defaults"))
        mp_defaults_btn.setToolTip(
            translate("SettingsDialog",
                      "Load recommended parameters for the current provider"))
        mp_defaults_btn.clicked.connect(self._load_default_model_params)
        mp_btn_layout.addWidget(mp_defaults_btn)

        mp_btn_layout.addStretch()
        model_params_layout.addLayout(mp_btn_layout)

        model_params_group.setLayout(model_params_layout)
        layout.addWidget(model_params_group)

        # Behavior group
        behavior_group = QGroupBox(translate("SettingsDialog", "Behavior"))
        behavior_layout = QVBoxLayout()

        self.enable_tools_check = QCheckBox(
            translate("SettingsDialog", "Model supports tool calling (uncheck to fall back to code generation)")
        )
        behavior_layout.addWidget(self.enable_tools_check)

        self.auto_execute_check = QCheckBox(
            translate("SettingsDialog", "Auto-execute code in Act mode (skip confirmation dialog)")
        )
        behavior_layout.addWidget(self.auto_execute_check)

        self.keep_dock_check = QCheckBox(
            translate("SettingsDialog", "Keep chat panel open when switching workbenches")
        )
        self.keep_dock_check.setToolTip(translate_branded(
            "SettingsDialog",
            "When enabled, the %2 chat panel stays docked and usable "
            "in other workbenches instead of hiding when you leave the "
            "%2 workbench."))
        behavior_layout.addWidget(self.keep_dock_check)

        # Thinking mode
        thinking_layout = QHBoxLayout()
        thinking_layout.addWidget(QLabel(translate("SettingsDialog", "Thinking:")))
        self.thinking_combo = QComboBox()
        self.thinking_combo.addItems([
            translate("SettingsDialog", "Off"),
            translate("SettingsDialog", "On"),
            translate("SettingsDialog", "Extended"),
        ])
        self.thinking_combo.setToolTip(
            translate("SettingsDialog",
                      "Off: No reasoning (fastest)\n"
                      "On: Standard thinking/reasoning\n"
                      "Extended: Extended thinking with higher budget")
        )
        thinking_layout.addWidget(self.thinking_combo)
        thinking_layout.addStretch()
        behavior_layout.addLayout(thinking_layout)

        # Strip thinking history
        self.strip_thinking_check = QCheckBox(
            translate("SettingsDialog",
                      "Strip thinking from conversation history")
        )
        self.strip_thinking_check.setToolTip(
            translate("SettingsDialog",
                      "Remove thinking/reasoning content from previous turns\n"
                      "before sending to the API. Required by some models\n"
                      "(e.g. Gemma) that reject thinking content in history.\n\n"
                      "Auto-detected by model name. Check/uncheck to override.")
        )
        self.strip_thinking_check.setTristate(True)
        self.strip_thinking_check.stateChanged.connect(
            self._on_strip_thinking_changed)
        behavior_layout.addWidget(self.strip_thinking_check)

        # System prompt
        prompt_group = QGroupBox(translate("SettingsDialog", "System Prompt"))
        prompt_layout = QVBoxLayout()

        prompt_btn_layout = QHBoxLayout()
        self.prompt_reset_btn = QPushButton(translate("SettingsDialog", "Reset to Default"))
        self.prompt_reset_btn.clicked.connect(self._reset_system_prompt)
        prompt_btn_layout.addWidget(self.prompt_reset_btn)
        prompt_btn_layout.addStretch()
        prompt_layout.addLayout(prompt_btn_layout)

        QPlainTextEdit = QtWidgets.QPlainTextEdit
        self.system_prompt_edit = QPlainTextEdit()
        self.system_prompt_edit.setMinimumHeight(120)
        self.system_prompt_edit.setMaximumHeight(200)
        self.system_prompt_edit.setPlaceholderText(
            translate("SettingsDialog",
                      "Custom system prompt instructions. "
                      "Dynamic sections (document state, skills, AGENTS.md) "
                      "are always appended automatically."))
        prompt_layout.addWidget(self.system_prompt_edit)

        prompt_group.setLayout(prompt_layout)
        layout.addWidget(prompt_group)

        # Viewport capture settings
        viewport_layout = QHBoxLayout()
        viewport_layout.addWidget(QLabel(translate("SettingsDialog", "Viewport capture:")))
        self.viewport_capture_combo = QComboBox()
        self.viewport_capture_combo.addItems([
            translate("SettingsDialog", "Off"),
            translate("SettingsDialog", "Every Message"),
            translate("SettingsDialog", "After Changes"),
        ])
        self.viewport_capture_combo.setToolTip(
            translate("SettingsDialog",
                      "Off: No auto-capture\n"
                      "Every Message: Capture screenshot with each message\n"
                      "After Changes: Capture after tool calls modify the document")
        )
        viewport_layout.addWidget(self.viewport_capture_combo)
        viewport_layout.addStretch()
        behavior_layout.addLayout(viewport_layout)

        resolution_layout = QHBoxLayout()
        resolution_layout.addWidget(QLabel(translate("SettingsDialog", "Capture resolution:")))
        self.viewport_resolution_combo = QComboBox()
        self.viewport_resolution_combo.addItems([
            translate("SettingsDialog", "Low (400x300)"),
            translate("SettingsDialog", "Medium (800x600)"),
            translate("SettingsDialog", "High (1280x960)"),
        ])
        resolution_layout.addWidget(self.viewport_resolution_combo)
        resolution_layout.addStretch()
        behavior_layout.addLayout(resolution_layout)

        # Vision support
        vision_layout = QHBoxLayout()
        self.vision_check = QCheckBox(
            translate("SettingsDialog", "Model supports vision")
        )
        self.vision_check.setToolTip(
            translate("SettingsDialog",
                      "When enabled, images are sent directly to the LLM.\n"
                      "When disabled, images are described via MCP before sending.\n"
                      "Use Test Connection to auto-detect.")
        )
        self.vision_check.stateChanged.connect(self._on_vision_override_changed)
        vision_layout.addWidget(self.vision_check)

        self._vision_status_label = QLabel()
        self._vision_status_label.setStyleSheet("color: #888;")
        vision_layout.addWidget(self._vision_status_label)

        self._vision_reset_btn = QPushButton(translate("SettingsDialog", "Reset"))
        self._vision_reset_btn.setMaximumWidth(50)
        self._vision_reset_btn.setToolTip(
            translate("SettingsDialog", "Clear manual override, use auto-detected value")
        )
        self._vision_reset_btn.clicked.connect(self._reset_vision_override)
        self._vision_reset_btn.hide()
        vision_layout.addWidget(self._vision_reset_btn)

        vision_layout.addStretch()
        behavior_layout.addLayout(vision_layout)

        behavior_group.setLayout(behavior_layout)
        layout.addWidget(behavior_group)

        # Tool Reranking group
        rerank_group = QGroupBox(translate("SettingsDialog", "Tool Reranking"))
        rerank_layout = QVBoxLayout()

        method_layout = QHBoxLayout()
        method_layout.addWidget(QLabel(translate("SettingsDialog", "Method:")))
        self.rerank_method_combo = QComboBox()
        self.rerank_method_combo.addItems([
            translate("SettingsDialog", "Off"),
            translate("SettingsDialog", "Keyword (free, lexical)"),
            translate("SettingsDialog", "LLM (semantic)"),
        ])
        self.rerank_method_combo.setToolTip(
            translate("SettingsDialog",
                      "Off: send all tool schemas every turn\n"
                      "Keyword: IDF-weighted token match, no extra LLM call\n"
                      "LLM: semantic ranking via a small/fast LLM\n"
                      "Both keyword and LLM include pinned tools unconditionally.")
        )
        self.rerank_method_combo.currentIndexChanged.connect(
            self._on_rerank_method_changed)
        method_layout.addWidget(self.rerank_method_combo)
        method_layout.addStretch()
        rerank_layout.addLayout(method_layout)

        top_n_layout = QHBoxLayout()
        top_n_layout.addWidget(QLabel(translate("SettingsDialog", "Top N:")))
        self.rerank_top_n_spin = QSpinBox()
        self.rerank_top_n_spin.setRange(1, 200)
        self.rerank_top_n_spin.setValue(15)
        top_n_layout.addWidget(self.rerank_top_n_spin)
        top_n_layout.addStretch()
        rerank_layout.addLayout(top_n_layout)

        pinned_layout = QHBoxLayout()
        pinned_layout.addWidget(QLabel(translate("SettingsDialog", "Pinned tools:")))
        self.rerank_pinned_edit = QLineEdit()
        self.rerank_pinned_edit.setPlaceholderText(
            translate("SettingsDialog",
                      "comma-separated tool names, always included")
        )
        pinned_layout.addWidget(self.rerank_pinned_edit)
        rerank_layout.addLayout(pinned_layout)

        # LLM reranker provider override — only relevant when method == "llm".
        # Fields left empty inherit the main provider's settings.
        self.rerank_llm_group = QGroupBox(
            translate("SettingsDialog", "LLM reranker provider (empty = same as main)"))
        llm_form = QFormLayout()

        self.rerank_llm_provider_combo = QComboBox()
        self.rerank_llm_provider_combo.addItem(
            translate("SettingsDialog", "(same as main)"), "")
        for name in get_provider_names():
            self.rerank_llm_provider_combo.addItem(name.capitalize(), name)
        llm_form.addRow(translate("SettingsDialog", "Provider:"),
                        self.rerank_llm_provider_combo)

        self.rerank_llm_base_url_edit = QLineEdit()
        self.rerank_llm_base_url_edit.setPlaceholderText(
            translate("SettingsDialog", "inherit from main"))
        llm_form.addRow(translate("SettingsDialog", "Base URL:"),
                        self.rerank_llm_base_url_edit)

        self.rerank_llm_api_key_edit = QLineEdit()
        self.rerank_llm_api_key_edit.setEchoMode(QLineEdit.Password)
        self.rerank_llm_api_key_edit.setPlaceholderText(
            translate("SettingsDialog", "inherit from main"))
        llm_form.addRow(translate("SettingsDialog", "API key:"),
                        self.rerank_llm_api_key_edit)

        self.rerank_llm_model_edit = QLineEdit()
        self.rerank_llm_model_edit.setPlaceholderText(
            translate("SettingsDialog", "inherit from main"))
        # textChanged fires on every keystroke, so toggling between
        # inherit/override updates the params table live — users who type
        # a model and click Save immediately still see the right table.
        self.rerank_llm_model_edit.textChanged.connect(
            self._on_rerank_model_changed)
        llm_form.addRow(translate("SettingsDialog", "Model:"),
                        self.rerank_llm_model_edit)

        # Per-model parameters for the reranker's effective model. Written
        # back into the shared cfg.model_params dict so a given model's
        # params are consistent whether the model is used as main, reranker,
        # or both. When the reranker inherits the main model (override
        # field empty), the table is prefilled with the main model's
        # current params and locked read-only — edits belong on the main
        # Model Parameters table. When an override model is set, the table
        # is editable and writes to that model's slot in model_params.
        self._rerank_params_label = QLabel()
        llm_form.addRow(self._rerank_params_label)

        self.rerank_params_table = QTableWidget(0, 2)
        self.rerank_params_table.setHorizontalHeaderLabels([
            translate("SettingsDialog", "Parameter"),
            translate("SettingsDialog", "Value"),
        ])
        self.rerank_params_table.horizontalHeader().setStretchLastSection(True)
        self.rerank_params_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Interactive)
        self.rerank_params_table.setColumnWidth(0, 160)
        self.rerank_params_table.setMaximumHeight(120)
        self.rerank_params_table.setToolTip(
            translate("SettingsDialog",
                      "Parameters for the reranker's model. Merged into the\n"
                      "API request body just like main model parameters.\n"
                      "Common: temperature, top_p, top_k, num_predict."))
        llm_form.addRow(self.rerank_params_table)

        rp_btn_layout = QHBoxLayout()
        self._rerank_add_btn = QPushButton(translate("SettingsDialog", "Add"))
        self._rerank_add_btn.clicked.connect(self._add_rerank_param)
        rp_btn_layout.addWidget(self._rerank_add_btn)

        self._rerank_remove_btn = QPushButton(translate("SettingsDialog", "Remove"))
        self._rerank_remove_btn.clicked.connect(self._remove_rerank_param)
        rp_btn_layout.addWidget(self._rerank_remove_btn)

        self._rerank_defaults_btn = QPushButton(translate("SettingsDialog", "Load Defaults"))
        self._rerank_defaults_btn.setToolTip(
            translate("SettingsDialog",
                      "Load recommended parameters for the reranker's provider"))
        self._rerank_defaults_btn.clicked.connect(self._load_rerank_default_params)
        rp_btn_layout.addWidget(self._rerank_defaults_btn)
        rp_btn_layout.addStretch()
        llm_form.addRow(rp_btn_layout)

        # Test button — validates the reranker call without waiting for the
        # user to send a message. Uses current dialog values, not disk, so
        # the user can iterate on params before saving.
        test_layout = QHBoxLayout()
        self._rerank_test_btn = QPushButton(
            translate("SettingsDialog", "Test Reranker"))
        self._rerank_test_btn.setToolTip(
            translate("SettingsDialog",
                      "Send a small test prompt to the reranker LLM using the\n"
                      "current dialog settings. Reports success or the exact\n"
                      "error from the provider — useful for diagnosing 4xx\n"
                      "errors, timeouts, or unparseable responses."))
        self._rerank_test_btn.clicked.connect(self._test_reranker)
        test_layout.addWidget(self._rerank_test_btn)
        self._rerank_test_status = QLabel()
        self._rerank_test_status.setWordWrap(True)
        self._rerank_test_status.setStyleSheet("color: #666;")
        test_layout.addWidget(self._rerank_test_status, 1)
        llm_form.addRow(test_layout)

        self.rerank_llm_group.setLayout(llm_form)
        rerank_layout.addWidget(self.rerank_llm_group)

        rerank_group.setLayout(rerank_layout)
        layout.addWidget(rerank_group)

        # MCP Servers group
        mcp_group = QGroupBox(translate("SettingsDialog", "MCP Servers"))
        mcp_layout = QVBoxLayout()

        self.mcp_list = QListWidget()
        self.mcp_list.setMaximumHeight(100)
        mcp_layout.addWidget(self.mcp_list)

        self.mcp_list.itemDoubleClicked.connect(self._edit_mcp_server)

        mcp_btn_layout = QHBoxLayout()
        add_mcp_btn = QPushButton(translate("SettingsDialog", "Add..."))
        add_mcp_btn.clicked.connect(self._add_mcp_server)
        mcp_btn_layout.addWidget(add_mcp_btn)

        edit_mcp_btn = QPushButton(translate("SettingsDialog", "Edit..."))
        edit_mcp_btn.clicked.connect(self._edit_mcp_server)
        mcp_btn_layout.addWidget(edit_mcp_btn)

        remove_mcp_btn = QPushButton(translate("SettingsDialog", "Remove"))
        remove_mcp_btn.clicked.connect(self._remove_mcp_server)
        mcp_btn_layout.addWidget(remove_mcp_btn)

        mcp_btn_layout.addStretch()
        mcp_layout.addLayout(mcp_btn_layout)

        mcp_group.setLayout(mcp_layout)
        layout.addWidget(mcp_group)

        # Editor group — affects Edit/New buttons in User Tools and Hooks below.
        editor_group = QGroupBox(translate("SettingsDialog", "Editor"))
        editor_layout = QVBoxLayout()
        self.use_external_editor_cb = QCheckBox(translate_branded(
            "SettingsDialog",
            "Open hooks and user tools in the OS-default editor "
            "(instead of %1's docked script editor)"))
        self.use_external_editor_cb.setToolTip(translate_branded(
            "SettingsDialog",
            "When enabled, files open via the OS file association "
            "(xdg-open / Launch Services) so the Settings dialog can stay open. "
            "When disabled, files open in %1's docked Gui::PythonEditor — "
            "which requires closing this dialog first."))
        editor_layout.addWidget(self.use_external_editor_cb)
        editor_group.setLayout(editor_layout)
        layout.addWidget(editor_group)

        # User Tools group
        user_tools_group = QGroupBox(translate("SettingsDialog", "User Tools"))
        user_tools_layout = QVBoxLayout()

        self.user_tools_list = QListWidget()
        self.user_tools_list.setMaximumHeight(100)
        user_tools_layout.addWidget(self.user_tools_list)

        ut_btn_layout = QHBoxLayout()
        ut_new_btn = QPushButton(translate("SettingsDialog", "New..."))
        ut_new_btn.clicked.connect(self._new_user_tool)
        ut_btn_layout.addWidget(ut_new_btn)

        ut_add_btn = QPushButton(translate("SettingsDialog", "Add..."))
        ut_add_btn.clicked.connect(self._add_user_tool)
        ut_btn_layout.addWidget(ut_add_btn)

        ut_edit_btn = QPushButton(translate("SettingsDialog", "Edit..."))
        ut_edit_btn.clicked.connect(self._edit_user_tool)
        ut_btn_layout.addWidget(ut_edit_btn)

        ut_remove_btn = QPushButton(translate("SettingsDialog", "Remove"))
        ut_remove_btn.clicked.connect(self._remove_user_tool)
        ut_btn_layout.addWidget(ut_remove_btn)

        ut_reload_btn = QPushButton(translate("SettingsDialog", "Reload"))
        ut_reload_btn.clicked.connect(self._reload_user_tools)
        ut_btn_layout.addWidget(ut_reload_btn)

        ut_btn_layout.addStretch()
        user_tools_layout.addLayout(ut_btn_layout)

        self.scan_macros_cb = QCheckBox(
            translate_branded("SettingsDialog", "Also scan %1 macro directory")
        )
        user_tools_layout.addWidget(self.scan_macros_cb)

        user_tools_group.setLayout(user_tools_layout)
        layout.addWidget(user_tools_group)

        # Skills group
        skills_group = QGroupBox(translate("SettingsDialog", "Skills"))
        skills_layout = QVBoxLayout()

        self.skills_list = QListWidget()
        self.skills_list.setMaximumHeight(120)
        skills_layout.addWidget(self.skills_list)

        skills_btn_layout = QHBoxLayout()
        self._skills_reset_btn = QPushButton(translate("SettingsDialog", "Reset to Built-in"))
        self._skills_reset_btn.setToolTip(
            translate("SettingsDialog",
                      "Delete the user copy and revert to the built-in version"))
        self._skills_reset_btn.clicked.connect(self._reset_skill_to_builtin)
        skills_btn_layout.addWidget(self._skills_reset_btn)

        skills_reload_btn = QPushButton(translate("SettingsDialog", "Refresh"))
        skills_reload_btn.clicked.connect(self._refresh_skills_list)
        skills_btn_layout.addWidget(skills_reload_btn)

        skills_btn_layout.addStretch()
        skills_layout.addLayout(skills_btn_layout)

        skills_group.setLayout(skills_layout)
        layout.addWidget(skills_group)

        # Hooks group
        hooks_group = QGroupBox(translate("SettingsDialog", "Hooks"))
        hooks_layout = QVBoxLayout()

        self.hooks_list = QListWidget()
        self.hooks_list.setMaximumHeight(100)
        hooks_layout.addWidget(self.hooks_list)

        hooks_btn_layout = QHBoxLayout()
        hooks_new_btn = QPushButton(translate("SettingsDialog", "New..."))
        hooks_new_btn.clicked.connect(self._new_hook)
        hooks_btn_layout.addWidget(hooks_new_btn)

        hooks_add_btn = QPushButton(translate("SettingsDialog", "Add..."))
        hooks_add_btn.clicked.connect(self._add_hook)
        hooks_btn_layout.addWidget(hooks_add_btn)

        hooks_edit_btn = QPushButton(translate("SettingsDialog", "Edit..."))
        hooks_edit_btn.clicked.connect(self._edit_hook)
        hooks_btn_layout.addWidget(hooks_edit_btn)

        hooks_remove_btn = QPushButton(translate("SettingsDialog", "Remove"))
        hooks_remove_btn.clicked.connect(self._remove_hook)
        hooks_btn_layout.addWidget(hooks_remove_btn)

        hooks_reload_btn = QPushButton(translate("SettingsDialog", "Reload"))
        hooks_reload_btn.clicked.connect(self._reload_hooks)
        hooks_btn_layout.addWidget(hooks_reload_btn)

        hooks_btn_layout.addStretch()
        hooks_layout.addLayout(hooks_btn_layout)

        hooks_group.setLayout(hooks_layout)
        layout.addWidget(hooks_group)

        # Test connection (outside scroll area)
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton(translate("SettingsDialog", "Test Connection"))
        self.test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self.test_btn)

        self.test_status = QLabel()
        self.test_status.setWordWrap(True)
        test_layout.addWidget(self.test_status, 1)

        outer_layout.addLayout(test_layout)

        # Dialog buttons (outside scroll area)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = QPushButton(translate("SettingsDialog", "Save"))
        self.save_btn.setStyleSheet(
            "QPushButton { padding: 6px 24px; font-weight: bold; }"
        )
        self.save_btn.clicked.connect(self._save)
        btn_layout.addWidget(self.save_btn)

        self.cancel_btn = QPushButton(translate("SettingsDialog", "Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        outer_layout.addLayout(btn_layout)

    def _load_from_config(self):
        """Populate fields from the current config."""
        cfg = get_config()

        names = get_provider_names()
        try:
            idx = names.index(cfg.provider.name)
        except ValueError:
            idx = 0
        self.provider_combo.setCurrentIndex(idx)

        self.api_key_edit.setText(cfg.provider.api_key)
        self.base_url_edit.setText(cfg.provider.base_url)
        self.model_edit.setText(cfg.provider.model)
        self.max_tokens_spin.setValue(cfg.max_tokens)
        self.context_window_spin.setValue(cfg.context_window)
        self.max_tool_turns_spin.setValue(cfg.max_tool_turns)
        self.execution_timeout_spin.setValue(cfg.execution_timeout)

        # Model parameters table
        self._load_model_params_table(cfg.provider.model, cfg)

        self.enable_tools_check.setChecked(cfg.enable_tools)
        self.auto_execute_check.setChecked(cfg.auto_execute)
        self.keep_dock_check.setChecked(cfg.keep_dock_on_workbench_switch)

        # Tool reranking
        method_map = {"off": 0, "keyword": 1, "llm": 2}
        self.rerank_method_combo.setCurrentIndex(
            method_map.get(cfg.rerank_method, 0))
        self.rerank_top_n_spin.setValue(cfg.rerank_top_n)
        self.rerank_pinned_edit.setText(", ".join(cfg.rerank_pinned_tools))

        # LLM reranker provider override
        provider_idx = self.rerank_llm_provider_combo.findData(
            cfg.rerank_llm_provider_name)
        if provider_idx >= 0:
            self.rerank_llm_provider_combo.setCurrentIndex(provider_idx)
        else:
            self.rerank_llm_provider_combo.setCurrentIndex(0)
        self.rerank_llm_base_url_edit.setText(cfg.rerank_llm_base_url)
        self.rerank_llm_api_key_edit.setText(cfg.rerank_llm_api_key)
        self.rerank_llm_model_edit.setText(cfg.rerank_llm_model)
        # Force a fresh resolve (setText above may have fired the signal mid-load)
        self._rerank_last_model = ""
        self._on_rerank_model_changed()
        self._on_rerank_method_changed(self.rerank_method_combo.currentIndex())

        thinking_map = {"off": 0, "on": 1, "extended": 2}
        self.thinking_combo.setCurrentIndex(thinking_map.get(cfg.thinking, 0))

        # Strip thinking history — tristate: PartiallyChecked=auto, Checked=on, Unchecked=off
        self._update_strip_thinking_ui(cfg.strip_thinking_history)

        # System prompt text: show override if set, otherwise generate default
        default_prompt = self._get_default_prompt_text()
        self._last_default_prompt = default_prompt
        if cfg.system_prompt_override:
            self.system_prompt_edit.setPlainText(cfg.system_prompt_override)
        else:
            self.system_prompt_edit.setPlainText(default_prompt)

        capture_map = {"off": 0, "every_message": 1, "after_changes": 2}
        self.viewport_capture_combo.setCurrentIndex(capture_map.get(cfg.viewport_capture, 0))

        resolution_map = {"low": 0, "medium": 1, "high": 2}
        self.viewport_resolution_combo.setCurrentIndex(resolution_map.get(cfg.viewport_resolution, 1))

        # Vision
        self._original_provider = cfg.provider.name
        self._original_model = cfg.provider.model
        self._update_vision_ui(cfg)

        # MCP servers
        self.mcp_list.clear()
        self._mcp_configs = list(cfg.mcp_servers)
        for entry in self._mcp_configs:
            self.mcp_list.addItem(self._mcp_list_label(entry))

        # Editor preference
        self.use_external_editor_cb.setChecked(cfg.use_external_editor)

        # User tools
        self.scan_macros_cb.setChecked(cfg.scan_freecad_macros)
        self._cfg = cfg
        self._load_user_tools_list()

        # Skills
        self._skills_status = []
        self._refresh_skills_list()
        self.skills_list.currentRowChanged.connect(
            lambda _: self._update_skills_reset_btn())

        # Hooks
        self._refresh_hooks_list()

    def _on_provider_changed(self, index):
        """Update base URL, model, and default params when provider changes."""
        names = get_provider_names()
        if 0 <= index < len(names):
            name = names[index]
            preset = PROVIDER_PRESETS.get(name, {})
            # Only overwrite when the preset has a concrete value. The
            # "custom" preset ships empty strings — wiping the user's
            # gateway/model on every switch-to-custom is the second half
            # of #12. Real providers always have non-empty presets, so
            # behavior is unchanged there.
            new_base_url = preset.get("base_url", "")
            if new_base_url:
                self.base_url_edit.setText(new_base_url)
            new_model = preset.get("default_model", "")
            if new_model:
                self.model_edit.setText(new_model)

            # Load saved params for the (possibly preserved) model
            cfg = get_config()
            self._load_model_params_table(self.model_edit.text(), cfg)

            # Apply provider-recommended reranker settings only when the
            # reranker UI is still at its factory default (off + top_n 15).
            # This way an explicit user choice — even "off" — survives a
            # provider switch (the rerank state in the dialog moves only
            # when it currently looks untouched). Used by the github preset
            # to enable keyword/top_n=8 by default; see issue #10.
            rerank_defaults = preset.get("default_rerank", {})
            if rerank_defaults and self._rerank_at_factory_defaults():
                self._apply_rerank_defaults(rerank_defaults)

    def _rerank_at_factory_defaults(self) -> bool:
        """True if the rerank UI matches AppConfig's factory defaults."""
        return (self.rerank_method_combo.currentIndex() == 0
                and self.rerank_top_n_spin.value() == 15)

    _RERANK_METHOD_INDEX = {"off": 0, "keyword": 1, "llm": 2}

    def _apply_rerank_defaults(self, defaults: dict):
        """Push a preset's recommended reranker settings into the UI."""
        method = defaults.get("method")
        if method in self._RERANK_METHOD_INDEX:
            self.rerank_method_combo.setCurrentIndex(
                self._RERANK_METHOD_INDEX[method])
        if "top_n" in defaults:
            self.rerank_top_n_spin.setValue(int(defaults["top_n"]))

    # ── Model Parameters table helpers ─────────────────────────

    # ── Strip Thinking History helpers ─────────────────────────

    def _update_strip_thinking_ui(self, value: bool | None):
        """Set the tristate checkbox from config value.

        None=auto (PartiallyChecked), True=on (Checked), False=off (Unchecked).
        """
        self.strip_thinking_check.stateChanged.disconnect(
            self._on_strip_thinking_changed)
        if value is None:
            self.strip_thinking_check.setCheckState(QtCore.Qt.PartiallyChecked)
        elif value:
            self.strip_thinking_check.setCheckState(QtCore.Qt.Checked)
        else:
            self.strip_thinking_check.setCheckState(QtCore.Qt.Unchecked)
        self.strip_thinking_check.stateChanged.connect(
            self._on_strip_thinking_changed)

    def _on_strip_thinking_changed(self, state):
        """User toggled the checkbox — disable tristate once manually set."""
        # Once the user clicks, it cycles Unchecked↔Checked (no more partial)
        pass

    def _read_strip_thinking_state(self) -> bool | None:
        """Read the tristate checkbox as None/True/False."""
        state = self.strip_thinking_check.checkState()
        if state == QtCore.Qt.PartiallyChecked:
            return None
        return state == QtCore.Qt.Checked

    def _on_model_changed(self):
        """Save current params under the old model, load params for the new one."""
        new_model = self.model_edit.text().strip()
        if new_model == self._last_model_name or not new_model:
            return
        # Save current table under old model name (in-memory only)
        cfg = get_config()
        if self._last_model_name:
            params = self._read_model_params_table()
            if params:
                cfg.model_params[self._last_model_name] = params
        # Load params for new model
        self._load_model_params_table(new_model, cfg)

    def _load_model_params_table(self, model_name: str, cfg=None):
        """Populate the params table for the given model.

        Priority: saved params for this model > provider defaults > global temperature.
        """
        if cfg is None:
            cfg = get_config()

        params = cfg.model_params.get(model_name, {})
        if not params:
            # No saved params — try provider defaults
            names = get_provider_names()
            idx = self.provider_combo.currentIndex()
            provider_name = names[idx] if 0 <= idx < len(names) else ""
            preset = PROVIDER_PRESETS.get(provider_name, {})
            params = dict(preset.get("default_params", {}))
        if not params:
            # Fallback: just temperature from global config
            params = {"temperature": cfg.temperature}

        self._last_model_name = model_name
        self._populate_model_params_table(params)

    def _populate_model_params_table(self, params: dict):
        """Fill the table widget from a params dict."""
        self.model_params_table.setRowCount(0)
        for key, value in params.items():
            row = self.model_params_table.rowCount()
            self.model_params_table.insertRow(row)
            self.model_params_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.model_params_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _read_model_params_table(self) -> dict:
        """Read the current params table into a dict, auto-casting values."""
        params = {}
        for row in range(self.model_params_table.rowCount()):
            key_item = self.model_params_table.item(row, 0)
            val_item = self.model_params_table.item(row, 1)
            if not key_item or not val_item:
                continue
            key = key_item.text().strip()
            val_str = val_item.text().strip()
            if not key:
                continue
            # Auto-cast value: try int, then float, then keep as string
            try:
                # Distinguish int from float: "64" → int, "0.95" → float
                if "." in val_str or "e" in val_str.lower():
                    params[key] = float(val_str)
                else:
                    params[key] = int(val_str)
            except ValueError:
                # Boolean or string
                if val_str.lower() in ("true", "false"):
                    params[key] = val_str.lower() == "true"
                else:
                    params[key] = val_str
        return params

    def _add_model_param(self):
        """Add an empty row to the model params table."""
        row = self.model_params_table.rowCount()
        self.model_params_table.insertRow(row)
        self.model_params_table.setItem(row, 0, QTableWidgetItem(""))
        self.model_params_table.setItem(row, 1, QTableWidgetItem(""))
        self.model_params_table.editItem(self.model_params_table.item(row, 0))

    def _remove_model_param(self):
        """Remove the selected row from the model params table."""
        row = self.model_params_table.currentRow()
        if row >= 0:
            self.model_params_table.removeRow(row)

    def _load_default_model_params(self):
        """Reset the params table to provider defaults."""
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        provider_name = names[idx] if 0 <= idx < len(names) else ""
        preset = PROVIDER_PRESETS.get(provider_name, {})
        params = dict(preset.get("default_params", {}))
        if not params:
            params = {"temperature": 0.3}
        self._populate_model_params_table(params)

    def _get_default_prompt_text(self) -> str:
        """Generate the default system prompt for the current settings."""
        from ..core.system_prompt import get_default_system_prompt
        return get_default_system_prompt(mode="act", tools_enabled=True)

    def _reset_system_prompt(self):
        """Reset the system prompt text to the default for current settings."""
        default = self._get_default_prompt_text()
        self.system_prompt_edit.setPlainText(default)
        self._last_default_prompt = default

    def _save(self):
        """Save settings to config and close."""
        cfg = get_config()
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        cfg.provider.name = names[idx] if 0 <= idx < len(names) else "anthropic"
        cfg.provider.api_key = self.api_key_edit.text()
        cfg.provider.base_url = self.base_url_edit.text()
        cfg.provider.model = self.model_edit.text()
        cfg.max_tokens = self.max_tokens_spin.value()
        cfg.context_window = self.context_window_spin.value()
        cfg.max_tool_turns = self.max_tool_turns_spin.value()
        cfg.execution_timeout = self.execution_timeout_spin.value()

        # Save model parameters for the current model
        model_name = self.model_edit.text().strip()
        if model_name:
            params = self._read_model_params_table()
            if params:
                cfg.model_params[model_name] = params
            elif model_name in cfg.model_params:
                del cfg.model_params[model_name]
            # Keep global temperature in sync for backward compat
            cfg.temperature = params.get("temperature", cfg.temperature)

        cfg.enable_tools = self.enable_tools_check.isChecked()
        cfg.auto_execute = self.auto_execute_check.isChecked()
        cfg.keep_dock_on_workbench_switch = self.keep_dock_check.isChecked()

        thinking_values = ["off", "on", "extended"]
        cfg.thinking = thinking_values[self.thinking_combo.currentIndex()]

        # Strip thinking history — tristate checkbox
        cfg.strip_thinking_history = self._read_strip_thinking_state()

        # Save system prompt override (empty if user hasn't changed from default)
        custom_text = self.system_prompt_edit.toPlainText().strip()
        default_text = self._get_default_prompt_text().strip()
        if custom_text == default_text:
            cfg.system_prompt_override = ""
        else:
            cfg.system_prompt_override = custom_text

        capture_values = ["off", "every_message", "after_changes"]
        cfg.viewport_capture = capture_values[self.viewport_capture_combo.currentIndex()]

        resolution_values = ["low", "medium", "high"]
        cfg.viewport_resolution = resolution_values[self.viewport_resolution_combo.currentIndex()]

        # Vision override
        if hasattr(self, '_vision_override_value'):
            cfg.vision_override = self._vision_override_value
        # Reset all detected capabilities if provider or model changed —
        # the previous probe results are about a different model.
        if (hasattr(self, '_original_provider') and cfg.provider.name != self._original_provider) or \
           (hasattr(self, '_original_model') and cfg.provider.model != self._original_model):
            cfg.vision_detected = None
            cfg.tools_detected = None
            cfg.thinking_detected = None

        cfg.mcp_servers = list(self._mcp_configs) if hasattr(self, "_mcp_configs") else []
        cfg.use_external_editor = self.use_external_editor_cb.isChecked()
        cfg.scan_freecad_macros = self.scan_macros_cb.isChecked()

        # Tool reranking
        method_values = ["off", "keyword", "llm"]
        cfg.rerank_method = method_values[self.rerank_method_combo.currentIndex()]
        cfg.rerank_top_n = self.rerank_top_n_spin.value()
        pinned_text = self.rerank_pinned_edit.text().strip()
        cfg.rerank_pinned_tools = [
            s.strip() for s in pinned_text.split(",") if s.strip()
        ] if pinned_text else []

        # LLM reranker provider override
        cfg.rerank_llm_provider_name = (
            self.rerank_llm_provider_combo.currentData() or "")
        cfg.rerank_llm_base_url = self.rerank_llm_base_url_edit.text().strip()
        cfg.rerank_llm_api_key = self.rerank_llm_api_key_edit.text().strip()
        rerank_model = self.rerank_llm_model_edit.text().strip()
        cfg.rerank_llm_model = rerank_model

        # Save the reranker params into their own namespace (cfg.rerank_params),
        # never the shared model_params dict. In inherit mode the reranker has
        # no params of its own — the main Model Parameters table owns the main
        # model's slot, so the reranker can't clobber it (issue #30).
        cfg.rerank_params = self._resolve_rerank_params(
            rerank_model, self._read_rerank_params_table())

        save_current_config()
        self.accept()

    def _on_rerank_method_changed(self, index: int):
        """Show the LLM provider subgroup only when 'LLM' is selected."""
        self.rerank_llm_group.setVisible(index == 2)

    @staticmethod
    def _resolve_rerank_params(rerank_model: str, table_params: dict) -> dict:
        """Reranker params to persist into cfg.rerank_params.

        Override mode (a distinct model is set) → persist the table. Inherit
        mode (empty override) → persist nothing; the reranker reads the main
        model's params at runtime and the main Model Parameters table owns
        that slot, so the reranker can never overwrite it (issue #30).
        """
        return dict(table_params) if rerank_model.strip() else {}

    def _on_rerank_model_changed(self, *_args):
        """Refresh the reranker params table for the current model state.

        - Empty override → mirror the main Model Parameters table's live
          values (edits belong on the main table; the reranker inherits them).
        - Non-empty override → show the reranker's own params. Renaming the
          override model keeps the current edits (params are a single set, not
          keyed per model); entering override from inherit loads the saved
          cfg.rerank_params, falling back to provider defaults.
        """
        cfg = get_config()
        prev_model = getattr(self, "_rerank_last_model", "") or ""
        model = self.rerank_llm_model_edit.text().strip()
        self._rerank_last_model = model

        if not model:
            # Inherit: show the main model's live params (reflects in-dialog
            # edits there). These are not persisted under the reranker — the
            # main table is authoritative.
            self._populate_rerank_params_table(self._read_model_params_table())
            self._rerank_params_label.setText(translate(
                "SettingsDialog",
                "Model parameters (inheriting main model — edit on the main table):"))
            return

        if prev_model:
            # Was already overriding — the user just renamed the model.
            # Keep the current edits (single param set, not per-model).
            self._rerank_params_label.setText(translate(
                "SettingsDialog", "Model parameters (reranker override):"))
            return

        # Entering override from inherit: load saved reranker params, else
        # provider defaults.
        params = dict(cfg.rerank_params)
        if not params:
            provider_name = (
                self.rerank_llm_provider_combo.currentData()
                or cfg.provider.name
            )
            preset = PROVIDER_PRESETS.get(provider_name, {})
            params = dict(preset.get("default_params", {}))
        self._populate_rerank_params_table(params)
        self._rerank_params_label.setText(translate(
            "SettingsDialog", "Model parameters (reranker override):"))

    def _populate_rerank_params_table(self, params: dict):
        self.rerank_params_table.setRowCount(0)
        for key, value in params.items():
            row = self.rerank_params_table.rowCount()
            self.rerank_params_table.insertRow(row)
            self.rerank_params_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.rerank_params_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _read_rerank_params_table(self) -> dict:
        params = {}
        for row in range(self.rerank_params_table.rowCount()):
            key_item = self.rerank_params_table.item(row, 0)
            val_item = self.rerank_params_table.item(row, 1)
            if not key_item or not val_item:
                continue
            key = key_item.text().strip()
            val_str = val_item.text().strip()
            if not key:
                continue
            try:
                if "." in val_str or "e" in val_str.lower():
                    params[key] = float(val_str)
                else:
                    params[key] = int(val_str)
            except ValueError:
                if val_str.lower() in ("true", "false"):
                    params[key] = val_str.lower() == "true"
                else:
                    params[key] = val_str
        return params

    def _add_rerank_param(self):
        row = self.rerank_params_table.rowCount()
        self.rerank_params_table.insertRow(row)
        self.rerank_params_table.setItem(row, 0, QTableWidgetItem(""))
        self.rerank_params_table.setItem(row, 1, QTableWidgetItem(""))
        self.rerank_params_table.editItem(self.rerank_params_table.item(row, 0))

    def _remove_rerank_param(self):
        row = self.rerank_params_table.currentRow()
        if row >= 0:
            self.rerank_params_table.removeRow(row)

    def _test_reranker(self):
        """Send a small probe prompt to the reranker LLM using dialog values.

        Surfaces success or the exact error so the user can debug a broken
        reranker config (HTTP 4xx, auth failure, timeouts, hallucinations)
        without sending a real chat message and parsing the Report View.
        """
        # Resolve effective provider/URL/key/model from dialog state.
        # Empty fields inherit from the main fields (same as runtime behavior).
        provider = (self.rerank_llm_provider_combo.currentData() or "").strip()
        if not provider:
            names = get_provider_names()
            idx = self.provider_combo.currentIndex()
            provider = names[idx] if 0 <= idx < len(names) else ""

        base_url = self.rerank_llm_base_url_edit.text().strip() \
            or self.base_url_edit.text().strip()
        api_key = self.rerank_llm_api_key_edit.text().strip() \
            or self.api_key_edit.text().strip()
        model = self.rerank_llm_model_edit.text().strip() \
            or self.model_edit.text().strip()
        # Effective params: use the reranker table (reflects current state
        # for both inherit and override modes).
        model_params = self._read_rerank_params_table()

        if not model:
            self._rerank_test_status.setText(translate(
                "SettingsDialog", "No model configured"))
            self._rerank_test_status.setStyleSheet("color: #c62828;")
            return

        self._rerank_test_btn.setEnabled(False)
        self._rerank_test_status.setText(translate(
            "SettingsDialog", "Testing..."))
        self._rerank_test_status.setStyleSheet("color: #666;")

        self._rerank_test_thread = _TestRerankerThread(
            provider, base_url, api_key, model, model_params, self,
        )
        self._rerank_test_thread.finished.connect(
            self._on_rerank_test_finished)
        self._rerank_test_thread.start()

    def _on_rerank_test_finished(self, success: bool, message: str):
        """Render the reranker test outcome in the status label."""
        self._rerank_test_btn.setEnabled(True)
        if success:
            self._rerank_test_status.setText(
                translate("SettingsDialog", "OK") + " — " + message)
            self._rerank_test_status.setStyleSheet("color: #2e7d32;")
        else:
            self._rerank_test_status.setText(
                translate("SettingsDialog", "Error") + ": " + message)
            self._rerank_test_status.setStyleSheet("color: #c62828;")

    def _load_rerank_default_params(self):
        """Reset the reranker params table to the reranker provider's defaults."""
        provider_name = (
            self.rerank_llm_provider_combo.currentData()
            or get_config().provider.name
        )
        preset = PROVIDER_PRESETS.get(provider_name, {})
        params = dict(preset.get("default_params", {}))
        if not params:
            params = {"temperature": 0.3}
        self._populate_rerank_params_table(params)

    def _test_connection(self):
        """Test the LLM connection in a background thread."""
        self._save_temp()

        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.test_status.setText(translate("SettingsDialog", "Testing..."))
        self.test_status.setStyleSheet("color: #666;")

        self._test_thread = _TestConnectionThread(self)
        self._test_thread.finished.connect(self._on_test_finished)
        self._test_thread.vision_result.connect(self._on_vision_probed)
        self._test_thread.capabilities_result.connect(self._on_capabilities_detected)
        self._test_thread.start()

    def _on_test_finished(self, success, message):
        """Handle test connection result."""
        if success:
            # Keep buttons disabled — vision probe is still running
            self.test_status.setText(message)
            self.test_status.setStyleSheet("color: #2e7d32;")
        else:
            # No vision probe on failure — re-enable buttons now
            self.test_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self.test_status.setText(translate("SettingsDialog", "Failed: ") + message)
            self.test_status.setStyleSheet("color: #c62828;")

    def _on_vision_probed(self, supports_vision: bool):
        """Handle vision probe result — persists to config immediately."""
        self.test_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        cfg = get_config()
        cfg.vision_detected = supports_vision
        save_current_config()
        self._update_vision_ui(cfg)
        # Append vision status to test output
        current = self.test_status.text()
        if supports_vision:
            vision_msg = translate("SettingsDialog", "Vision: supported")
        else:
            vision_msg = translate("SettingsDialog", "Vision: not supported")
        self.test_status.setText(current + "\n" + vision_msg)
        # Log to FreeCAD console
        try:
            import FreeCAD
            FreeCAD.Console.PrintMessage(f"{PRODUCT_NAME}: {vision_msg}\n")
        except ImportError:
            pass

    def _on_capabilities_detected(self, caps: dict):
        """Handle full capabilities dict (Ollama only emits tools/thinking).

        Persists tools_detected/thinking_detected to config and appends a
        readable summary to the test status. Non-Ollama providers emit
        only "vision" — tools/thinking stay None to keep falling back to
        the provider-wide static flag.
        """
        cfg = get_config()
        if "tools" in caps:
            cfg.tools_detected = bool(caps["tools"])
        if "thinking" in caps:
            cfg.thinking_detected = bool(caps["thinking"])
        save_current_config()

        # Build a single-line summary for the status label
        parts = []
        if "tools" in caps:
            parts.append(f"tools: {'yes' if caps['tools'] else 'no'}")
        if "thinking" in caps:
            parts.append(f"thinking: {'yes' if caps['thinking'] else 'no'}")
        if parts:
            line = translate("SettingsDialog", "Capabilities: ") + ", ".join(parts)
            self.test_status.setText(self.test_status.text() + "\n" + line)
            try:
                import FreeCAD
                FreeCAD.Console.PrintMessage(f"{PRODUCT_NAME}: {line}\n")
            except ImportError:
                pass

    def _save_temp(self):
        """Temporarily apply current UI values to config (for test connection)."""
        cfg = get_config()
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        cfg.provider.name = names[idx] if 0 <= idx < len(names) else "anthropic"
        cfg.provider.api_key = self.api_key_edit.text()
        cfg.provider.base_url = self.base_url_edit.text()
        cfg.provider.model = self.model_edit.text()

        try:
            cfg.max_tokens = self.max_tokens_spin.value()
        except Exception:
            pass
        try:
            cfg.context_window = self.context_window_spin.value()
        except Exception:
            pass
        try:
            cfg.max_tool_turns = self.max_tool_turns_spin.value()
        except Exception:
            pass

        # Apply model params temporarily for test connection
        model_name = self.model_edit.text().strip()
        if model_name:
            params = self._read_model_params_table()
            if params:
                cfg.model_params[model_name] = params
                cfg.temperature = params.get("temperature", cfg.temperature)

        thinking_values = ["off", "on", "extended"]
        cfg.thinking = thinking_values[self.thinking_combo.currentIndex()]

        custom_text = self.system_prompt_edit.toPlainText().strip()
        default_text = self._get_default_prompt_text().strip()
        if custom_text != default_text:
            cfg.system_prompt_override = custom_text
        else:
            cfg.system_prompt_override = ""

    @staticmethod
    def _mcp_list_label(entry: dict) -> str:
        """Build display label for an MCP server entry."""
        tags = []
        if not entry.get("enabled", True):
            tags.append("disabled")
        if entry.get("deferred", True):
            tags.append("deferred")
        timeout = int(entry.get("timeout", 600))
        if timeout != 600:
            tags.append(f"{timeout}s")
        prefix = f"({', '.join(tags)}) " if tags else ""
        transport = entry.get("transport", "stdio")
        if transport in ("sse", "http"):
            target = f"[{transport}] {entry.get('url', '')}"
        else:
            args = " ".join(entry.get("args", []))
            target = f"{entry.get('command', '')} {args}".strip()
        return f"{prefix}{entry.get('name', '?')} — {target}"

    def _add_mcp_server(self):
        """Show a dialog to add a new MCP server configuration."""
        dlg = _AddMCPServerDialog(self)
        if dlg.exec():
            entry = dlg.get_config()
            if not hasattr(self, "_mcp_configs"):
                self._mcp_configs = []
            self._mcp_configs.append(entry)
            self.mcp_list.addItem(self._mcp_list_label(entry))

    def _edit_mcp_server(self):
        """Edit the selected MCP server configuration."""
        row = self.mcp_list.currentRow()
        if row < 0 or not hasattr(self, "_mcp_configs") or row >= len(self._mcp_configs):
            return
        existing = self._mcp_configs[row]
        dlg = _AddMCPServerDialog(self, existing=existing)
        if dlg.exec():
            updated = dlg.get_config()
            self._mcp_configs[row] = updated
            self.mcp_list.item(row).setText(self._mcp_list_label(updated))

    def _remove_mcp_server(self):
        """Remove the selected MCP server from the list."""
        row = self.mcp_list.currentRow()
        if row >= 0 and hasattr(self, "_mcp_configs"):
            self.mcp_list.takeItem(row)
            if row < len(self._mcp_configs):
                self._mcp_configs.pop(row)

    # --- User Tools methods ---

    def _load_user_tools_list(self):
        """Scan user tools directory and populate the list widget."""
        from ..config import USER_TOOLS_DIR
        from ..extensions.user_tools import validate_file

        self.user_tools_list.clear()
        self._user_tool_files = []

        if not os.path.isdir(USER_TOOLS_DIR):
            return

        disabled = set(getattr(self._cfg, "user_tools_disabled", []))

        for fname in sorted(os.listdir(USER_TOOLS_DIR)):
            if not (fname.endswith(".py") or fname.endswith(".FCMacro")):
                continue
            fpath = os.path.join(USER_TOOLS_DIR, fname)
            if not os.path.isfile(fpath):
                continue

            vr = validate_file(fpath)
            self._user_tool_files.append(fname)

            if not vr.valid:
                label = f"\u2717 {fname} \u2014 {vr.error}"
            elif vr.warnings:
                func_names = ", ".join(f.name for f in vr.functions)
                label = f"\u26a0 {fname} ({func_names}) \u2014 {'; '.join(vr.warnings)}"
            else:
                func_names = ", ".join(f.name for f in vr.functions)
                label = f"\u2713 {fname} ({func_names})"

            if fname in disabled:
                label = f"(disabled) {label}"

            self.user_tools_list.addItem(QListWidgetItem(label))

    def _add_user_tool(self):
        """Open file picker and copy selected file to user tools dir."""
        from ..config import USER_TOOLS_DIR

        path, _ = QFileDialog.getOpenFileName(
            self,
            translate("SettingsDialog", "Select Tool File"),
            "",
            translate("SettingsDialog", "Python Files (*.py *.FCMacro)"),
        )
        if not path:
            return

        import shutil
        os.makedirs(USER_TOOLS_DIR, exist_ok=True)
        dest = os.path.join(USER_TOOLS_DIR, os.path.basename(path))
        if os.path.exists(dest):
            QMessageBox.warning(
                self,
                translate("SettingsDialog", "File Exists"),
                f"'{os.path.basename(path)}' already exists in tools directory.",
            )
            return
        shutil.copy2(path, dest)
        self._reload_user_tools()

    def _edit_user_tool(self):
        """Open the selected user tool file in the configured editor."""
        from ..config import USER_TOOLS_DIR

        row = self.user_tools_list.currentRow()
        if row < 0 or row >= len(self._user_tool_files):
            return
        fpath = os.path.join(USER_TOOLS_DIR, self._user_tool_files[row])
        if not os.path.isfile(fpath):
            return
        if not self._prepare_editor_open():
            return
        self._open_path(fpath)

    def _new_user_tool(self):
        """Create a new user tool from a template and open it in the configured editor."""
        from ..config import USER_TOOLS_DIR
        from ..extensions.file_templates import render_user_tool_template

        name, ok = QtWidgets.QInputDialog.getText(
            self, translate("SettingsDialog", "New User Tool"),
            translate("SettingsDialog",
                      "Enter a function name (used as filename and function name):"))
        if not ok or not name.strip():
            return
        name = name.strip().lower().replace(" ", "_").replace("-", "_")
        if not name.isidentifier():
            QMessageBox.warning(
                self,
                translate("SettingsDialog", "Invalid Name"),
                translate("SettingsDialog",
                          "Name must be a valid Python identifier (letters, digits, underscore)."))
            return
        fpath = os.path.join(USER_TOOLS_DIR, f"{name}.py")
        if os.path.exists(fpath):
            QMessageBox.warning(
                self,
                translate("SettingsDialog", "File Exists"),
                f"'{name}.py' " + translate(
                    "SettingsDialog", "already exists in tools directory."))
            return
        if not self._prepare_editor_open():
            return
        os.makedirs(USER_TOOLS_DIR, exist_ok=True)
        with open(fpath, "w") as f:
            f.write(render_user_tool_template(name))
        if self._use_external_editor_now():
            # Dialog stayed open; refresh the list so the new tool appears.
            self._reload_user_tools()
        self._open_path(fpath)

    def _remove_user_tool(self):
        """Remove selected tool file from user tools dir."""
        from ..config import USER_TOOLS_DIR

        row = self.user_tools_list.currentRow()
        if row < 0 or row >= len(self._user_tool_files):
            return

        fname = self._user_tool_files[row]
        fpath = os.path.join(USER_TOOLS_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)
        self._reload_user_tools()

    def _reload_user_tools(self):
        """Re-scan and refresh the user tools list."""
        self._load_user_tools_list()

    def _update_vision_ui(self, cfg):
        """Update vision checkbox and label from config state."""
        self._vision_override_value = cfg.vision_override
        # Temporarily disconnect to avoid triggering _on_vision_override_changed
        self.vision_check.stateChanged.disconnect(self._on_vision_override_changed)
        if cfg.vision_override is not None:
            self.vision_check.setChecked(cfg.vision_override)
            self._vision_status_label.setText(
                translate("SettingsDialog", "(manual override)")
            )
            self._vision_reset_btn.show()
        elif cfg.vision_detected is not None:
            self.vision_check.setChecked(cfg.vision_detected)
            self._vision_status_label.setText(
                translate("SettingsDialog", "(auto-detected)")
            )
            self._vision_reset_btn.hide()
        else:
            self.vision_check.setChecked(False)
            self._vision_status_label.setText(
                translate("SettingsDialog", "(not tested)")
            )
            self._vision_reset_btn.hide()
        self.vision_check.stateChanged.connect(self._on_vision_override_changed)

    def _on_vision_override_changed(self, state):
        """User toggled the vision checkbox — set manual override.

        PySide2 QCheckBox.stateChanged emits int (0=Unchecked, 2=Checked).
        """
        self._vision_override_value = (state != 0)
        self._vision_status_label.setText(
            translate("SettingsDialog", "(manual override)")
        )
        self._vision_reset_btn.show()

    def _reset_vision_override(self):
        """Clear the manual override, revert to auto-detected value."""
        cfg = get_config()
        self._vision_override_value = None
        self._update_vision_ui(cfg)

    # --- Hooks methods ---

    def _refresh_hooks_list(self):
        """Refresh the hooks list from the registry."""
        self.hooks_list.clear()
        try:
            from ..hooks import get_hook_registry
            for hook in get_hook_registry().discovered_hooks:
                if hook["has_error"]:
                    label = f"\u2717 {hook['name']} ({hook['error_message'][:50]})"
                else:
                    events = ", ".join(hook["events"])
                    label = f"\u2713 {hook['name']} ({events})"
                self.hooks_list.addItem(label)
        except Exception:
            pass

    def _add_hook(self):
        """Add a hook by copying a hook.py file into a new directory."""
        from ..config import HOOKS_DIR
        path, _ = QFileDialog.getOpenFileName(
            self, translate("SettingsDialog", "Select hook.py file"), "",
            translate("SettingsDialog", "Python files (*.py)"))
        if not path:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, translate("SettingsDialog", "Hook Name"),
            translate("SettingsDialog", "Enter a name for this hook:"))
        if not ok or not name.strip():
            return
        name = name.strip().lower().replace(" ", "-")
        hook_dir = os.path.join(HOOKS_DIR, name)
        os.makedirs(hook_dir, exist_ok=True)
        import shutil
        shutil.copy2(path, os.path.join(hook_dir, "hook.py"))
        self._reload_hooks()

    def _new_hook(self):
        """Create a new hook from a template and open it in the configured editor."""
        from ..config import HOOKS_DIR
        from ..extensions.file_templates import render_hook_template

        name, ok = QtWidgets.QInputDialog.getText(
            self, translate("SettingsDialog", "New Hook"),
            translate("SettingsDialog", "Enter a name for this hook:"))
        if not ok or not name.strip():
            return
        name = name.strip().lower().replace(" ", "-")
        hook_dir = os.path.join(HOOKS_DIR, name)
        hook_file = os.path.join(hook_dir, "hook.py")
        if os.path.exists(hook_file):
            QMessageBox.warning(
                self,
                translate("SettingsDialog", "Hook Exists"),
                translate("SettingsDialog", "A hook named '") + name + translate(
                    "SettingsDialog", "' already exists."))
            return
        if not self._prepare_editor_open():
            return
        os.makedirs(hook_dir, exist_ok=True)
        with open(hook_file, "w") as f:
            f.write(render_hook_template(name))
        if self._use_external_editor_now():
            # Dialog stayed open; refresh the list so the new hook appears.
            self._reload_hooks()
        self._open_path(hook_file)

    def _open_in_freecad_editor(self, path):
        """Open a .py/.FCMacro file in FreeCAD's built-in script editor.

        Falls back to the OS-default handler via QDesktopServices if FreeCADGui
        isn't available or rejects the file.
        """
        try:
            import FreeCADGui as Gui
            Gui.open(path)
            return
        except Exception:
            pass
        url = QtCore.QUrl.fromLocalFile(path)
        QtGui.QDesktopServices.openUrl(url)

    def _open_in_external_editor(self, path):
        """Open a path using the OS-default handler (xdg-open / file association)."""
        url = QtCore.QUrl.fromLocalFile(path)
        QtGui.QDesktopServices.openUrl(url)

    def _use_external_editor_now(self) -> bool:
        """Live checkbox state — governs the current New/Edit action regardless
        of whether the user later Saves or Discards the dialog. Reading from
        the widget (not get_config()) means an in-session toggle takes effect
        immediately, matching what the user just clicked.
        """
        return self.use_external_editor_cb.isChecked()

    def _prepare_editor_open(self) -> bool:
        """Prepare to open a file in the user's preferred editor.

        Returns True if the slot may proceed (write file, then call _open_path),
        False if the user cancelled. For the FreeCAD editor this prompts to
        save/discard the dialog state since the docked editor is unreachable
        while the modal is up. For external editors this is a no-op.
        """
        if self._use_external_editor_now():
            return True
        return self._confirm_close_for_editor()

    def _open_path(self, path):
        """Dispatch to FreeCAD or external editor based on the live checkbox."""
        if self._use_external_editor_now():
            self._open_in_external_editor(path)
        else:
            self._open_in_freecad_editor(path)

    def _confirm_close_for_editor(self) -> bool:
        """Prompt to close this modal dialog so the docked editor is reachable.

        Returns True if the dialog was closed (caller may proceed), False if
        the user cancelled.
        """
        choice = QMessageBox.question(
            self,
            translate("SettingsDialog", "Open Script Editor"),
            translate_branded(
                "SettingsDialog",
                "%1's script editor is docked behind this dialog. "
                "The dialog must close so you can reach it.\n\n"
                "Save your pending settings changes first?"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if choice == QMessageBox.Save:
            self._save()
            return True
        if choice == QMessageBox.Discard:
            self.reject()
            return True
        return False

    def _edit_hook(self):
        """Open the selected hook's hook.py in the configured editor."""
        row = self.hooks_list.currentRow()
        if row < 0:
            return
        try:
            from ..hooks import get_hook_registry
            hooks = get_hook_registry().discovered_hooks
            if row >= len(hooks):
                return
            hook_path = os.path.join(hooks[row]["path"], "hook.py")
        except Exception:
            return
        if not self._prepare_editor_open():
            return
        self._open_path(hook_path)

    def _remove_hook(self):
        """Remove the selected hook directory."""
        row = self.hooks_list.currentRow()
        if row < 0:
            return
        try:
            from ..hooks import get_hook_registry
            hooks = get_hook_registry().discovered_hooks
            if row >= len(hooks):
                return
            hook = hooks[row]
            if hook.get("builtin"):
                QMessageBox.information(
                    self, translate("SettingsDialog", "Cannot Remove"),
                    translate("SettingsDialog",
                              "Built-in hooks cannot be removed. You can disable them instead."))
                return
            reply = QMessageBox.question(
                self, translate("SettingsDialog", "Remove Hook"),
                translate("SettingsDialog", "Remove hook '") + hook["name"] + "'?")
            if reply != QMessageBox.Yes:
                return
            import shutil
            shutil.rmtree(hook["path"], ignore_errors=True)
            self._reload_hooks()
        except Exception:
            pass

    def _reload_hooks(self):
        """Reload all hooks and refresh the list."""
        try:
            from ..hooks import get_hook_registry
            get_hook_registry().reload()
        except Exception:
            pass
        self._refresh_hooks_list()


    # ── Skills management ──────────────────────────────────────

    def _refresh_skills_list(self):
        """Populate the skills list with status indicators."""
        from ..extensions.skills import SkillsRegistry

        self.skills_list.clear()
        self._skills_status = SkillsRegistry.get_skill_status()

        for info in self._skills_status:
            source = info["source"]
            name = info["name"]
            desc = info["description"]

            if source == "modified":
                icon = "\u26a0"  # ⚠
                tag = "modified"
            elif source == "user":
                icon = "\u2606"  # ☆
                tag = "user"
            else:
                icon = "\u2713"  # ✓
                tag = "built-in"

            label = f"{icon} {name} ({tag})"
            if desc:
                label += f" — {desc}"
            self.skills_list.addItem(label)

        self._update_skills_reset_btn()

    def _update_skills_reset_btn(self):
        """Enable/disable the reset button based on selection."""
        idx = self.skills_list.currentRow()
        can_reset = False
        if 0 <= idx < len(self._skills_status):
            info = self._skills_status[idx]
            # Can reset if there's a user copy AND a built-in exists
            can_reset = info["has_user_copy"] and bool(info["builtin_path"])
        self._skills_reset_btn.setEnabled(can_reset)

    def _reset_skill_to_builtin(self):
        """Reset the selected skill to its built-in version."""
        idx = self.skills_list.currentRow()
        if idx < 0 or idx >= len(self._skills_status):
            return

        info = self._skills_status[idx]
        name = info["name"]

        reply = QMessageBox.question(
            self,
            translate("SettingsDialog", "Reset Skill"),
            translate("SettingsDialog",
                      f"Delete user copy of '{name}' and revert to the built-in version?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from ..extensions.skills import SkillsRegistry
        if SkillsRegistry.reset_to_builtin(name):
            self._refresh_skills_list()


class _AddMCPServerDialog(QDialog):
    """Dialog for adding or editing an MCP server configuration."""

    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        editing = existing is not None
        self.setWindowTitle(
            translate("AddMCPServerDialog", "Edit MCP Server") if editing
            else translate("AddMCPServerDialog", "Add MCP Server")
        )
        self.setMinimumWidth(400)
        self._build_ui(editing)
        if existing:
            self._populate(existing)

    def _build_ui(self, editing=False):
        layout = QFormLayout(self)

        self.transport_combo = QComboBox()
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "Command (stdio)"), "stdio")
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "SSE (URL)"), "sse")
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "Streamable HTTP (URL)"), "http")
        self.transport_combo.currentIndexChanged.connect(
            lambda _=0: self._apply_transport_visibility(
                self.transport_combo.currentData()))
        layout.addRow(translate("AddMCPServerDialog", "Transport:"),
                      self.transport_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. filesystem"))
        layout.addRow(translate("AddMCPServerDialog", "Name:"), self.name_edit)

        # --- stdio group ---
        self._stdio_widget = QWidget()
        stdio_form = QFormLayout(self._stdio_widget)
        stdio_form.setContentsMargins(0, 0, 0, 0)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. npx"))
        stdio_form.addRow(translate("AddMCPServerDialog", "Command:"),
                          self.command_edit)
        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText(translate(
            "AddMCPServerDialog", "e.g. -y @modelcontextprotocol/server-filesystem /tmp"))
        self.args_edit.setToolTip(
            translate("AddMCPServerDialog", "Space-separated arguments"))
        stdio_form.addRow(translate("AddMCPServerDialog", "Args:"), self.args_edit)
        layout.addRow(self._stdio_widget)

        # --- url group (sse / http) ---
        self._url_widget = QWidget()
        url_form = QFormLayout(self._url_widget)
        url_form.setContentsMargins(0, 0, 0, 0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. https://host/sse"))
        url_form.addRow(translate("AddMCPServerDialog", "URL:"), self.url_edit)

        self.headers_table = QTableWidget(0, 2)
        self.headers_table.setHorizontalHeaderLabels([
            translate("AddMCPServerDialog", "Header"),
            translate("AddMCPServerDialog", "Value")])
        self.headers_table.setMaximumHeight(100)
        url_form.addRow(translate("AddMCPServerDialog", "Headers:"),
                        self.headers_table)
        headers_btns = QHBoxLayout()
        add_hdr = QPushButton(translate("AddMCPServerDialog", "Add header"))
        add_hdr.clicked.connect(
            lambda: self.headers_table.insertRow(self.headers_table.rowCount()))
        del_hdr = QPushButton(translate("AddMCPServerDialog", "Remove header"))
        del_hdr.clicked.connect(
            lambda: self.headers_table.removeRow(self.headers_table.currentRow()))
        headers_btns.addWidget(add_hdr)
        headers_btns.addWidget(del_hdr)
        headers_btns.addStretch()
        headers_wrap = QWidget()
        headers_wrap.setLayout(headers_btns)
        url_form.addRow("", headers_wrap)

        self.ca_edit = QLineEdit()
        self.ca_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional CA bundle path (.pem)"))
        url_form.addRow(translate("AddMCPServerDialog", "CA bundle:"), self.ca_edit)
        self.cert_edit = QLineEdit()
        self.cert_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional client cert path (.pem)"))
        url_form.addRow(translate("AddMCPServerDialog", "Client cert:"), self.cert_edit)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional client key path"))
        url_form.addRow(translate("AddMCPServerDialog", "Client key:"), self.key_edit)

        self._url_warning = QLabel()
        self._url_warning.setStyleSheet("color: red;")
        self._url_warning.setWordWrap(True)
        self._url_warning.setVisible(False)
        url_form.addRow("", self._url_warning)
        layout.addRow(self._url_widget)

        # --- shared rows ---
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setValue(600)
        self.timeout_spin.setSuffix(translate("AddMCPServerDialog", " s"))
        self.timeout_spin.setToolTip(
            translate("AddMCPServerDialog",
                      "Maximum time to wait for a tool call to complete.\n"
                      "Raise for slow tools (vision models, large builds).\n"
                      "Lower for fast tools where you want to fail quickly.")
        )
        layout.addRow(translate("AddMCPServerDialog", "Tool call timeout:"), self.timeout_spin)

        self.deferred_check = QCheckBox(translate("AddMCPServerDialog", "Deferred tool loading"))
        self.deferred_check.setChecked(True)
        self.deferred_check.setToolTip(
            translate("AddMCPServerDialog",
                      "Load tool schemas lazily on first use instead of\n"
                      "fetching all schemas eagerly on connect.\n"
                      "Faster startup when the server exposes many tools.")
        )
        layout.addRow("", self.deferred_check)

        self.enabled_check = QCheckBox(translate("AddMCPServerDialog", "Enabled"))
        self.enabled_check.setChecked(True)
        layout.addRow("", self.enabled_check)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ok_label = translate("AddMCPServerDialog", "Save") if editing \
            else translate("AddMCPServerDialog", "Add")
        ok_btn = QPushButton(ok_label)
        ok_btn.clicked.connect(self._on_accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton(translate("AddMCPServerDialog", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addRow(btn_layout)

        self._apply_transport_visibility(self.transport_combo.currentData())

    def _apply_transport_visibility(self, transport):
        is_stdio = (transport == "stdio")
        self._stdio_widget.setVisible(is_stdio)
        self._url_widget.setVisible(not is_stdio)

    @staticmethod
    def _url_error_message(url):
        """Return an error string if the URL is invalid for a URL transport, else ''."""
        if not url:
            return translate("AddMCPServerDialog", "URL is required.")
        from ..mcp.client import _validate_url
        try:
            _validate_url(url)
        except ValueError as e:
            return str(e)
        return ""

    def _on_accept(self):
        """Validate a URL-transport server's URL before accepting the dialog."""
        transport = self.transport_combo.currentData()
        if transport in ("sse", "http"):
            msg = _AddMCPServerDialog._url_error_message(self.url_edit.text().strip())
            if msg:
                self._url_warning.setText(msg)
                self._url_warning.setVisible(True)
                return
        self.accept()

    def _collect_headers(self):
        headers = {}
        for row in range(self.headers_table.rowCount()):
            key_item = self.headers_table.item(row, 0)
            val_item = self.headers_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            val = val_item.text().strip() if val_item else ""
            if key:
                headers[key] = val
        return headers

    def _populate_headers(self, headers):
        self.headers_table.setRowCount(0)
        for key, value in (headers or {}).items():
            row = self.headers_table.rowCount()
            self.headers_table.insertRow(row)
            self.headers_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.headers_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _populate(self, entry: dict):
        """Pre-populate fields from an existing MCP server config."""
        self.name_edit.setText(entry.get("name", ""))
        transport = entry.get("transport", "stdio")
        idx = self.transport_combo.findData(transport)
        if idx >= 0:
            self.transport_combo.setCurrentIndex(idx)
        self.command_edit.setText(entry.get("command", ""))
        self.args_edit.setText(" ".join(entry.get("args", [])))
        self.url_edit.setText(entry.get("url", ""))
        self._populate_headers(entry.get("headers", {}))
        self.ca_edit.setText(entry.get("ca_bundle", ""))
        self.cert_edit.setText(entry.get("client_cert", ""))
        self.key_edit.setText(entry.get("client_key", ""))
        self.deferred_check.setChecked(entry.get("deferred", True))
        self.enabled_check.setChecked(entry.get("enabled", True))
        self.timeout_spin.setValue(int(entry.get("timeout", 600)))
        self._apply_transport_visibility(transport)

    def get_config(self) -> dict:
        transport = self.transport_combo.currentData()
        cfg = {
            "name": self.name_edit.text().strip(),
            "transport": transport,
            "enabled": self.enabled_check.isChecked(),
            "deferred": self.deferred_check.isChecked(),
            "timeout": self.timeout_spin.value(),
        }
        if transport == "stdio":
            args_text = self.args_edit.text().strip()
            cfg["command"] = self.command_edit.text().strip()
            cfg["args"] = args_text.split() if args_text else []
            cfg["env"] = {}
        else:
            cfg["url"] = self.url_edit.text().strip()
            cfg["headers"] = self._collect_headers()
            ca = self.ca_edit.text().strip()
            cert = self.cert_edit.text().strip()
            key = self.key_edit.text().strip()
            if ca:
                cfg["ca_bundle"] = ca
            if cert:
                cfg["client_cert"] = cert
            if key:
                cfg["client_key"] = key
        return cfg
