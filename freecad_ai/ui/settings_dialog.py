"""Settings dialog for FreeCAD AI.

Provides a GUI for configuring:
  - LLM provider (Anthropic, OpenAI, Ollama, Gemini, OpenRouter, Custom)
  - API key, base URL, model name
  - Max tokens, temperature
  - Auto-execute toggle
  - User extension tools
  - Test connection button
"""

import os

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate

QDialog = QtWidgets.QDialog
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

QListWidget = QtWidgets.QListWidget
QListWidgetItem = QtWidgets.QListWidgetItem
QFileDialog = QtWidgets.QFileDialog
QMessageBox = QtWidgets.QMessageBox

from ..config import get_config, save_current_config, PROVIDER_PRESETS
from ..llm.providers import get_provider_names


class _TestConnectionThread(QThread):
    """Background thread for testing LLM connection and vision capability."""
    finished = Signal(bool, str)        # success, message
    vision_result = Signal(bool)        # vision probe result

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            from ..llm.client import create_client_from_config
            client = create_client_from_config()
            response = client.test_connection()
            self.finished.emit(True, translate("SettingsDialog", "Connected! Response: ") + response)

            # Run vision probe after successful connection
            vision_ok = client.vision_probe()
            self.vision_result.emit(vision_ok)
        except Exception as e:
            self.finished.emit(False, str(e))


class SettingsDialog(QDialog):
    """Configuration dialog for FreeCAD AI."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("SettingsDialog", "FreeCAD AI Settings"))
        self.setMinimumWidth(500)
        self._test_thread = None
        self._build_ui()
        self._load_from_config()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Provider group
        provider_group = QGroupBox(translate("SettingsDialog", "LLM Provider"))
        provider_layout = QFormLayout()

        self.provider_combo = QComboBox()
        self.provider_combo.addItems([n.capitalize() for n in get_provider_names()])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addRow(translate("SettingsDialog", "Provider:"), self.provider_combo)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(translate("SettingsDialog", "Enter API key (stored in plaintext)"))
        provider_layout.addRow(translate("SettingsDialog", "API Key:"), self.api_key_edit)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        provider_layout.addRow(translate("SettingsDialog", "Base URL:"), self.base_url_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText(translate("SettingsDialog", "Model name"))
        provider_layout.addRow(translate("SettingsDialog", "Model:"), self.model_edit)

        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)

        # Parameters group
        params_group = QGroupBox(translate("SettingsDialog", "Parameters"))
        params_layout = QFormLayout()

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 262144)
        self.max_tokens_spin.setSingleStep(1024)
        self.max_tokens_spin.setValue(4096)
        self.max_tokens_spin.setToolTip(
            translate("SettingsDialog",
                      "Maximum output tokens per response.\n"
                      "Context window is determined by the model/provider.")
        )
        params_layout.addRow(translate("SettingsDialog", "Max Output Tokens:"), self.max_tokens_spin)

        self.temperature_edit = QLineEdit()
        self.temperature_edit.setValidator(QDoubleValidator(0.0, 2.0, 2))
        self.temperature_edit.setText("0.3")
        params_layout.addRow(translate("SettingsDialog", "Temperature:"), self.temperature_edit)

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
        params_layout.addRow(translate("SettingsDialog", "Context Window:"), self.context_window_spin)

        params_group.setLayout(params_layout)
        layout.addWidget(params_group)

        # Behavior group
        behavior_group = QGroupBox(translate("SettingsDialog", "Behavior"))
        behavior_layout = QVBoxLayout()

        self.auto_execute_check = QCheckBox(
            translate("SettingsDialog", "Auto-execute code in Act mode (skip confirmation dialog)")
        )
        behavior_layout.addWidget(self.auto_execute_check)

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

        # User Tools group
        user_tools_group = QGroupBox(translate("SettingsDialog", "User Tools"))
        user_tools_layout = QVBoxLayout()

        self.user_tools_list = QListWidget()
        self.user_tools_list.setMaximumHeight(100)
        user_tools_layout.addWidget(self.user_tools_list)

        ut_btn_layout = QHBoxLayout()
        ut_add_btn = QPushButton(translate("SettingsDialog", "Add..."))
        ut_add_btn.clicked.connect(self._add_user_tool)
        ut_btn_layout.addWidget(ut_add_btn)

        ut_remove_btn = QPushButton(translate("SettingsDialog", "Remove"))
        ut_remove_btn.clicked.connect(self._remove_user_tool)
        ut_btn_layout.addWidget(ut_remove_btn)

        ut_reload_btn = QPushButton(translate("SettingsDialog", "Reload"))
        ut_reload_btn.clicked.connect(self._reload_user_tools)
        ut_btn_layout.addWidget(ut_reload_btn)

        ut_btn_layout.addStretch()
        user_tools_layout.addLayout(ut_btn_layout)

        self.scan_macros_cb = QCheckBox(
            translate("SettingsDialog", "Also scan FreeCAD macro directory")
        )
        user_tools_layout.addWidget(self.scan_macros_cb)

        user_tools_group.setLayout(user_tools_layout)
        layout.addWidget(user_tools_group)

        # Hooks group
        hooks_group = QGroupBox(translate("SettingsDialog", "Hooks"))
        hooks_layout = QVBoxLayout()

        self.hooks_list = QListWidget()
        self.hooks_list.setMaximumHeight(100)
        hooks_layout.addWidget(self.hooks_list)

        hooks_btn_layout = QHBoxLayout()
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

        # Test connection
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton(translate("SettingsDialog", "Test Connection"))
        self.test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self.test_btn)

        self.test_status = QLabel()
        self.test_status.setWordWrap(True)
        test_layout.addWidget(self.test_status, 1)

        layout.addLayout(test_layout)

        # Dialog buttons
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

        layout.addLayout(btn_layout)

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
        self.temperature_edit.setText(str(cfg.temperature))
        self.auto_execute_check.setChecked(cfg.auto_execute)

        thinking_map = {"off": 0, "on": 1, "extended": 2}
        self.thinking_combo.setCurrentIndex(thinking_map.get(cfg.thinking, 0))

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

        # User tools
        self.scan_macros_cb.setChecked(cfg.scan_freecad_macros)
        self._cfg = cfg
        self._load_user_tools_list()

        # Hooks
        self._refresh_hooks_list()

    def _on_provider_changed(self, index):
        """Update base URL and model when provider selection changes."""
        names = get_provider_names()
        if 0 <= index < len(names):
            name = names[index]
            preset = PROVIDER_PRESETS.get(name, {})
            self.base_url_edit.setText(preset.get("base_url", ""))
            self.model_edit.setText(preset.get("default_model", ""))

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

        try:
            cfg.temperature = float(self.temperature_edit.text())
        except ValueError:
            cfg.temperature = 0.3

        cfg.auto_execute = self.auto_execute_check.isChecked()

        thinking_values = ["off", "on", "extended"]
        cfg.thinking = thinking_values[self.thinking_combo.currentIndex()]

        capture_values = ["off", "every_message", "after_changes"]
        cfg.viewport_capture = capture_values[self.viewport_capture_combo.currentIndex()]

        resolution_values = ["low", "medium", "high"]
        cfg.viewport_resolution = resolution_values[self.viewport_resolution_combo.currentIndex()]

        # Vision override
        if hasattr(self, '_vision_override_value'):
            cfg.vision_override = self._vision_override_value
        # Reset vision_detected if provider or model changed
        if (hasattr(self, '_original_provider') and cfg.provider.name != self._original_provider) or \
           (hasattr(self, '_original_model') and cfg.provider.model != self._original_model):
            cfg.vision_detected = None

        cfg.mcp_servers = list(self._mcp_configs) if hasattr(self, "_mcp_configs") else []
        cfg.scan_freecad_macros = self.scan_macros_cb.isChecked()

        save_current_config()
        self.accept()

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
            FreeCAD.Console.PrintMessage(f"FreeCAD AI: {vision_msg}\n")
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
            cfg.temperature = float(self.temperature_edit.text())
        except ValueError:
            pass

        thinking_values = ["off", "on", "extended"]
        cfg.thinking = thinking_values[self.thinking_combo.currentIndex()]

    @staticmethod
    def _mcp_list_label(entry: dict) -> str:
        """Build display label for an MCP server entry."""
        tags = []
        if not entry.get("enabled", True):
            tags.append("disabled")
        if entry.get("deferred", True):
            tags.append("deferred")
        prefix = f"({', '.join(tags)}) " if tags else ""
        args = " ".join(entry.get("args", []))
        return f"{prefix}{entry.get('name', '?')} — {entry.get('command', '')} {args}"

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

    def _edit_hook(self):
        """Open the selected hook's hook.py in the default editor."""
        row = self.hooks_list.currentRow()
        if row < 0:
            return
        try:
            from ..hooks import get_hook_registry
            hooks = get_hook_registry().discovered_hooks
            if row >= len(hooks):
                return
            hook_path = os.path.join(hooks[row]["path"], "hook.py")
            url = QtCore.QUrl.fromLocalFile(hook_path)
            QtGui.QDesktopServices.openUrl(url)
        except Exception:
            pass

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

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(translate("AddMCPServerDialog", "e.g. filesystem"))
        layout.addRow(translate("AddMCPServerDialog", "Name:"), self.name_edit)

        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText(translate("AddMCPServerDialog", "e.g. npx"))
        layout.addRow(translate("AddMCPServerDialog", "Command:"), self.command_edit)

        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText(translate("AddMCPServerDialog", "e.g. -y @modelcontextprotocol/server-filesystem /tmp"))
        self.args_edit.setToolTip(translate("AddMCPServerDialog", "Space-separated arguments"))
        layout.addRow(translate("AddMCPServerDialog", "Args:"), self.args_edit)

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
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton(translate("AddMCPServerDialog", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addRow(btn_layout)

    def _populate(self, entry: dict):
        """Pre-populate fields from an existing MCP server config."""
        self.name_edit.setText(entry.get("name", ""))
        self.command_edit.setText(entry.get("command", ""))
        self.args_edit.setText(" ".join(entry.get("args", [])))
        self.deferred_check.setChecked(entry.get("deferred", True))
        self.enabled_check.setChecked(entry.get("enabled", True))

    def get_config(self) -> dict:
        args_text = self.args_edit.text().strip()
        return {
            "name": self.name_edit.text().strip(),
            "command": self.command_edit.text().strip(),
            "args": args_text.split() if args_text else [],
            "env": {},
            "enabled": self.enabled_check.isChecked(),
            "deferred": self.deferred_check.isChecked(),
        }
