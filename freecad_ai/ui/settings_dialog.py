"""Settings dialog for FreeCAD AI.

Provides a GUI for configuring:
  - LLM provider (Anthropic, OpenAI, Ollama, Gemini, OpenRouter, Custom)
  - API key, base URL, model name
  - Max tokens, temperature
  - Auto-execute toggle
  - Test connection button
"""

from .compat import QtWidgets, QtCore, QtGui

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

from ..config import get_config, save_current_config, PROVIDER_PRESETS
from ..llm.providers import get_provider_names


class _TestConnectionThread(QThread):
    """Background thread for testing LLM connection."""
    finished = Signal(bool, str)  # success, message

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            from ..llm.client import create_client_from_config
            client = create_client_from_config()
            response = client.test_connection()
            self.finished.emit(True, "Connected! Response: " + response)
        except Exception as e:
            self.finished.emit(False, str(e))


class SettingsDialog(QDialog):
    """Configuration dialog for FreeCAD AI."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FreeCAD AI Settings")
        self.setMinimumWidth(500)
        self._test_thread = None
        self._build_ui()
        self._load_from_config()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Provider group
        provider_group = QGroupBox("LLM Provider")
        provider_layout = QFormLayout()

        self.provider_combo = QComboBox()
        self.provider_combo.addItems([n.capitalize() for n in get_provider_names()])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addRow("Provider:", self.provider_combo)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Enter API key (stored in plaintext)")
        provider_layout.addRow("API Key:", self.api_key_edit)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        provider_layout.addRow("Base URL:", self.base_url_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("Model name")
        provider_layout.addRow("Model:", self.model_edit)

        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)

        # Parameters group
        params_group = QGroupBox("Parameters")
        params_layout = QFormLayout()

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 262144)
        self.max_tokens_spin.setSingleStep(1024)
        self.max_tokens_spin.setValue(4096)
        self.max_tokens_spin.setToolTip(
            "Maximum output tokens per response.\n"
            "Context window is determined by the model/provider."
        )
        params_layout.addRow("Max Output Tokens:", self.max_tokens_spin)

        self.temperature_edit = QLineEdit()
        self.temperature_edit.setValidator(QDoubleValidator(0.0, 2.0, 2))
        self.temperature_edit.setText("0.3")
        params_layout.addRow("Temperature:", self.temperature_edit)

        params_group.setLayout(params_layout)
        layout.addWidget(params_group)

        # Behavior group
        behavior_group = QGroupBox("Behavior")
        behavior_layout = QVBoxLayout()

        self.auto_execute_check = QCheckBox(
            "Auto-execute code in Act mode (skip confirmation dialog)"
        )
        behavior_layout.addWidget(self.auto_execute_check)

        # Thinking mode
        thinking_layout = QHBoxLayout()
        thinking_layout.addWidget(QLabel("Thinking:"))
        self.thinking_combo = QComboBox()
        self.thinking_combo.addItems(["Off", "On", "Extended"])
        self.thinking_combo.setToolTip(
            "Off: No reasoning (fastest)\n"
            "On: Standard thinking/reasoning\n"
            "Extended: Extended thinking with higher budget"
        )
        thinking_layout.addWidget(self.thinking_combo)
        thinking_layout.addStretch()
        behavior_layout.addLayout(thinking_layout)

        behavior_group.setLayout(behavior_layout)
        layout.addWidget(behavior_group)

        # MCP Servers group
        mcp_group = QGroupBox("MCP Servers")
        mcp_layout = QVBoxLayout()

        self.mcp_list = QListWidget()
        self.mcp_list.setMaximumHeight(100)
        mcp_layout.addWidget(self.mcp_list)

        mcp_btn_layout = QHBoxLayout()
        add_mcp_btn = QPushButton("Add...")
        add_mcp_btn.clicked.connect(self._add_mcp_server)
        mcp_btn_layout.addWidget(add_mcp_btn)

        remove_mcp_btn = QPushButton("Remove")
        remove_mcp_btn.clicked.connect(self._remove_mcp_server)
        mcp_btn_layout.addWidget(remove_mcp_btn)

        mcp_btn_layout.addStretch()
        mcp_layout.addLayout(mcp_btn_layout)

        mcp_group.setLayout(mcp_layout)
        layout.addWidget(mcp_group)

        # Test connection
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self.test_btn)

        self.test_status = QLabel()
        self.test_status.setWordWrap(True)
        test_layout.addWidget(self.test_status, 1)

        layout.addLayout(test_layout)

        # Dialog buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(
            "QPushButton { padding: 6px 24px; font-weight: bold; }"
        )
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

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
        self.temperature_edit.setText(str(cfg.temperature))
        self.auto_execute_check.setChecked(cfg.auto_execute)

        thinking_map = {"off": 0, "on": 1, "extended": 2}
        self.thinking_combo.setCurrentIndex(thinking_map.get(cfg.thinking, 0))

        # MCP servers
        self.mcp_list.clear()
        self._mcp_configs = list(cfg.mcp_servers)
        for entry in self._mcp_configs:
            enabled = entry.get("enabled", True)
            prefix = "" if enabled else "(disabled) "
            args = " ".join(entry.get("args", []))
            self.mcp_list.addItem(f"{prefix}{entry.get('name', '?')} — {entry.get('command', '')} {args}")

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

        try:
            cfg.temperature = float(self.temperature_edit.text())
        except ValueError:
            cfg.temperature = 0.3

        cfg.auto_execute = self.auto_execute_check.isChecked()

        thinking_values = ["off", "on", "extended"]
        cfg.thinking = thinking_values[self.thinking_combo.currentIndex()]

        cfg.mcp_servers = list(self._mcp_configs) if hasattr(self, "_mcp_configs") else []

        save_current_config()
        self.accept()

    def _test_connection(self):
        """Test the LLM connection in a background thread."""
        self._save_temp()

        self.test_btn.setEnabled(False)
        self.test_status.setText("Testing...")
        self.test_status.setStyleSheet("color: #666;")

        self._test_thread = _TestConnectionThread(self)
        self._test_thread.finished.connect(self._on_test_finished)
        self._test_thread.start()

    def _on_test_finished(self, success, message):
        """Handle test connection result."""
        self.test_btn.setEnabled(True)
        if success:
            self.test_status.setText(message)
            self.test_status.setStyleSheet("color: #2e7d32;")
        else:
            self.test_status.setText("Failed: " + message)
            self.test_status.setStyleSheet("color: #c62828;")

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
            cfg.temperature = float(self.temperature_edit.text())
        except ValueError:
            pass

        thinking_values = ["off", "on", "extended"]
        cfg.thinking = thinking_values[self.thinking_combo.currentIndex()]

    def _add_mcp_server(self):
        """Show a dialog to add a new MCP server configuration."""
        dlg = _AddMCPServerDialog(self)
        if dlg.exec():
            entry = dlg.get_config()
            if not hasattr(self, "_mcp_configs"):
                self._mcp_configs = []
            self._mcp_configs.append(entry)
            args = " ".join(entry.get("args", []))
            self.mcp_list.addItem(f"{entry['name']} — {entry['command']} {args}")

    def _remove_mcp_server(self):
        """Remove the selected MCP server from the list."""
        row = self.mcp_list.currentRow()
        if row >= 0 and hasattr(self, "_mcp_configs"):
            self.mcp_list.takeItem(row)
            if row < len(self._mcp_configs):
                self._mcp_configs.pop(row)


class _AddMCPServerDialog(QDialog):
    """Dialog for adding a new MCP server configuration."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add MCP Server")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout(self)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. filesystem")
        layout.addRow("Name:", self.name_edit)

        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("e.g. npx")
        layout.addRow("Command:", self.command_edit)

        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText("e.g. -y @modelcontextprotocol/server-filesystem /tmp")
        self.args_edit.setToolTip("Space-separated arguments")
        layout.addRow("Args:", self.args_edit)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(True)
        layout.addRow("", self.enabled_check)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ok_btn = QPushButton("Add")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addRow(btn_layout)

    def get_config(self) -> dict:
        args_text = self.args_edit.text().strip()
        return {
            "name": self.name_edit.text().strip(),
            "command": self.command_edit.text().strip(),
            "args": args_text.split() if args_text else [],
            "env": {},
            "enabled": self.enabled_check.isChecked(),
        }
