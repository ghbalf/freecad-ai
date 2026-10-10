"""Behavior page: Limits, Behavior, and System Prompt."""

from ..compat import QtWidgets, QtCore
from ...i18n import translate
from .base import SettingsPage

QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QComboBox = QtWidgets.QComboBox
QSpinBox = QtWidgets.QSpinBox
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
QPlainTextEdit = QtWidgets.QPlainTextEdit

_THINKING = ["off", "on", "extended"]
_CAPTURE = ["off", "every_message", "after_changes"]
_RESOLUTION = ["low", "medium", "high"]


def _index(values, value, default=0):
    return values.index(value) if value in values else default


class BehaviorPage(SettingsPage):
    """Limits, Behavior, and System Prompt groups."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_default_prompt = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Limits group — the fixed fields formerly under Model Parameters
        limits_group = QGroupBox(translate("SettingsDialog", "Limits"))
        fixed_layout = QFormLayout()

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 262144)
        self.max_tokens_spin.setSingleStep(1024)
        self.max_tokens_spin.setValue(4096)
        self.max_tokens_spin.setToolTip(
            translate("SettingsDialog",
                      "Default output cap per response.\n"
                      "A profile can set its own with a max_tokens row\n"
                      "in its Model Parameters table.")
        )
        fixed_layout.addRow(translate("SettingsDialog", "Max Output Tokens:"), self.max_tokens_spin)

        self.context_window_spin = QSpinBox()
        self.context_window_spin.setRange(4000, 1000000)
        self.context_window_spin.setSingleStep(10000)
        self.context_window_spin.setValue(64000)
        self.context_window_spin.setToolTip(
            translate("SettingsDialog",
                      "Older messages are compacted once the conversation\n"
                      "is estimated above this many tokens.\n"
                      "Default for profiles that don't set their own\n"
                      "on the Provider page.")
        )
        fixed_layout.addRow(translate("SettingsDialog", "Compact above:"), self.context_window_spin)

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

        limits_group.setLayout(fixed_layout)
        layout.addWidget(limits_group)

        # Behavior group
        behavior_group = QGroupBox(translate("SettingsDialog", "Behavior"))
        behavior_layout = QVBoxLayout()

        self.enable_tools_check = QCheckBox(
            translate("SettingsDialog", "Use tool calling (uncheck to fall back to code generation)")
        )
        behavior_layout.addWidget(self.enable_tools_check)

        self.auto_execute_check = QCheckBox(
            translate("SettingsDialog", "Auto-execute code in Act mode (skip confirmation dialog)")
        )
        behavior_layout.addWidget(self.auto_execute_check)

        self.keep_dock_check = QCheckBox(
            translate("SettingsDialog", "Keep chat panel open when switching workbenches")
        )
        self.keep_dock_check.setToolTip(translate(
            "SettingsDialog",
            "When enabled, the FreeCAD AI chat panel stays docked and usable "
            "in other workbenches instead of hiding when you leave the "
            "FreeCAD AI workbench."))
        behavior_layout.addWidget(self.keep_dock_check)

        # Thinking mode
        thinking_layout = QHBoxLayout()
        thinking_layout.addWidget(QLabel(
            translate("SettingsDialog", "Thinking (default for profiles):")))
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

        # Keeping reasoning in history is a reply-quality setting, not a
        # caching one, which is why it sits here and ships ticked.
        self.preserve_reasoning_check = QCheckBox(
            translate("SettingsDialog",
                      "Keep model reasoning in conversation history")
        )
        self.preserve_reasoning_check.setToolTip(
            translate("SettingsDialog",
                      "Store the thinking a model produced for a turn and send\n"
                      "it back with that turn on later requests, which is what\n"
                      "the provider already saw.\n\n"
                      "Moonshot report a measurable drop in reply quality on\n"
                      "turns whose reasoning is missing, so this is on by\n"
                      "default. Untick it to keep the history as it was before\n"
                      "v0.27.0-alpha.\n\n"
                      "Models that reject reasoning in history (e.g. Gemma) are\n"
                      "unaffected -- \"Strip thinking from conversation\n"
                      "history\" above still applies.")
        )
        behavior_layout.addWidget(self.preserve_reasoning_check)

        # Prompt caching (#47). Both default off, so an existing install
        # behaves exactly as it did before the upgrade.
        self.prompt_cache_check = QCheckBox(
            translate("SettingsDialog",
                      "Optimize prompt for caching (may change replies)")
        )
        self.prompt_cache_check.setToolTip(
            translate("SettingsDialog",
                      "Providers discount a prompt they have seen before, but\n"
                      "only while its opening stays byte-identical. The live\n"
                      "document state sits at the top of the prompt, so it\n"
                      "changes every time you add a feature and the discount\n"
                      "is lost -- including on the much larger tool list\n"
                      "behind it.\n\n"
                      "This moves the document state to the end of your last\n"
                      "message instead, and marks a cache point on Anthropic.\n\n"
                      "The model still sees the same information, but in a\n"
                      "different place, so its replies may differ. That is why\n"
                      "this is off by default.")
        )
        behavior_layout.addWidget(self.prompt_cache_check)

        self.log_usage_check = QCheckBox(
            translate("SettingsDialog", "Log token usage to the Report view")
        )
        self.log_usage_check.setToolTip(
            translate("SettingsDialog",
                      "Print one line per reply with the prompt and completion\n"
                      "token counts, and how much of the prompt was served\n"
                      "from cache.\n\n"
                      "Turn this on first to see what your requests cost now,\n"
                      "then turn on the caching option above and compare.\n\n"
                      "On OpenAI-style providers this adds a field to the\n"
                      "request asking for the counts, which a few unusual\n"
                      "endpoints may reject.")
        )
        behavior_layout.addWidget(self.log_usage_check)

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

        behavior_group.setLayout(behavior_layout)
        layout.addWidget(behavior_group)

        # System prompt
        prompt_group = QGroupBox(translate("SettingsDialog", "System Prompt"))
        prompt_layout = QVBoxLayout()

        prompt_btn_layout = QHBoxLayout()
        self.prompt_reset_btn = QPushButton(translate("SettingsDialog", "Reset to Default"))
        self.prompt_reset_btn.clicked.connect(self._reset_system_prompt)
        prompt_btn_layout.addWidget(self.prompt_reset_btn)
        prompt_btn_layout.addStretch()
        prompt_layout.addLayout(prompt_btn_layout)

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

    def _show(self, cfg, label):
        self.max_tokens_spin.setValue(cfg.max_tokens)
        self.context_window_spin.setValue(cfg.context_window)
        self.max_tool_turns_spin.setValue(cfg.max_tool_turns)
        self.execution_timeout_spin.setValue(cfg.execution_timeout)
        self.enable_tools_check.setChecked(cfg.enable_tools)
        self.auto_execute_check.setChecked(cfg.auto_execute)
        self.keep_dock_check.setChecked(cfg.keep_dock_on_workbench_switch)
        self.thinking_combo.setCurrentIndex(_index(_THINKING, cfg.thinking))
        self._update_strip_thinking_ui(cfg.strip_thinking_history)
        self.preserve_reasoning_check.setChecked(cfg.preserve_reasoning_history)
        self.prompt_cache_check.setChecked(cfg.optimize_prompt_caching)
        self.log_usage_check.setChecked(cfg.log_token_usage)
        self.viewport_capture_combo.setCurrentIndex(
            _index(_CAPTURE, cfg.viewport_capture))
        self.viewport_resolution_combo.setCurrentIndex(
            _index(_RESOLUTION, cfg.viewport_resolution, 1))
        default_prompt = self._get_default_prompt_text()
        self._last_default_prompt = default_prompt
        self.system_prompt_edit.setPlainText(
            cfg.system_prompt_override or default_prompt)

    def _values(self):
        # The override is computed: text equal to the generated default
        # saves as "" (no override). An unedited prompt compares equal to
        # its baseline and writes nothing at all.
        text = self.system_prompt_edit.toPlainText().strip()
        default = self._get_default_prompt_text().strip()
        return {
            "max_tokens": self.max_tokens_spin.value(),
            "context_window": self.context_window_spin.value(),
            "max_tool_turns": self.max_tool_turns_spin.value(),
            "execution_timeout": self.execution_timeout_spin.value(),
            "enable_tools": self.enable_tools_check.isChecked(),
            "auto_execute": self.auto_execute_check.isChecked(),
            "keep_dock_on_workbench_switch": self.keep_dock_check.isChecked(),
            "thinking": _THINKING[self.thinking_combo.currentIndex()],
            "strip_thinking_history": self._read_strip_thinking_state(),
            "preserve_reasoning_history":
                self.preserve_reasoning_check.isChecked(),
            "optimize_prompt_caching": self.prompt_cache_check.isChecked(),
            "log_token_usage": self.log_usage_check.isChecked(),
            "viewport_capture":
                _CAPTURE[self.viewport_capture_combo.currentIndex()],
            "viewport_resolution":
                _RESOLUTION[self.viewport_resolution_combo.currentIndex()],
            "system_prompt_override": "" if text == default else text,
        }

    def after_save(self, cfg):
        # The menu's "Keep Chat Panel Open" tick mirrors this flag, and
        # FreeCAD never re-asks the command for its state, so a save from
        # either window would otherwise leave the checkmark stale.
        from ..command_state import set_command_checked
        set_command_checked("FreeCADAI_ToggleKeepDock",
                            cfg.keep_dock_on_workbench_switch)

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

    def _get_default_prompt_text(self) -> str:
        """Generate the default system prompt for the current settings."""
        from ...core.system_prompt import get_default_system_prompt
        return get_default_system_prompt(mode="act", tools_enabled=True)

    def _reset_system_prompt(self):
        """Reset the system prompt text to the default for current settings."""
        default = self._get_default_prompt_text()
        self.system_prompt_edit.setPlainText(default)
        self._last_default_prompt = default
