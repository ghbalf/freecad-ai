"""Optimize Skill dialog for configuring skill optimization runs.

Allows users to select a skill, define test cases, configure iteration
settings, choose metrics, and set advanced parameters before launching
an optimization run.
"""

import re

from ..i18n import translate
from .compat import QtWidgets, QtCore

QDialog = QtWidgets.QDialog
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QComboBox = QtWidgets.QComboBox
QLineEdit = QtWidgets.QLineEdit
QSpinBox = QtWidgets.QSpinBox
QDoubleSpinBox = QtWidgets.QDoubleSpinBox
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
QListWidget = QtWidgets.QListWidget
QMessageBox = QtWidgets.QMessageBox
QFileDialog = QtWidgets.QFileDialog
QWidget = QtWidgets.QWidget


class OptimizeSkillDialog(QDialog):
    """Dialog for configuring a skill optimization run."""

    def __init__(self, skills_list: list, preselect: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("OptimizeSkillDialog", "Optimize Skill"))
        self.setMinimumWidth(520)
        self._result_config = None
        self._validation_content = ""
        self._param_defs = {}
        self._param_widgets = {}
        self._param_layout = None
        self._structured_widget = None
        self._build_ui(skills_list, preselect)

    # ---- UI construction ------------------------------------------------

    def _build_ui(self, skills_list, preselect):
        layout = QVBoxLayout(self)

        # -- Skill selection --
        skill_layout = QFormLayout()
        self._skill_combo = QComboBox()
        self._skill_combo.addItems(skills_list)
        if preselect and preselect in skills_list:
            self._skill_combo.setCurrentIndex(skills_list.index(preselect))
        skill_layout.addRow(translate("OptimizeSkillDialog", "Skill:"), self._skill_combo)
        layout.addLayout(skill_layout)

        self._skill_combo.currentIndexChanged.connect(self._on_skill_changed)

        # -- Test Cases --
        tc_group = QGroupBox(translate("OptimizeSkillDialog", "Test Cases"))
        tc_layout = QVBoxLayout()

        self._test_list = QListWidget()
        tc_layout.addWidget(self._test_list)

        # Container for structured parameter fields (hidden by default)
        self._structured_widget = QWidget()
        self._param_layout = QFormLayout(self._structured_widget)
        self._param_layout.setContentsMargins(0, 0, 0, 0)
        self._structured_widget.setVisible(False)
        tc_layout.addWidget(self._structured_widget)

        # Quick-add row (shown when structured mode is active)
        self._quick_add_row = QWidget()
        quick_layout = QHBoxLayout(self._quick_add_row)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        self._quick_add_input = QLineEdit()
        self._quick_add_input.setPlaceholderText(
            translate("OptimizeSkillDialog", "Quick add: L=100, W=80, H=40, T=2")
        )
        quick_layout.addWidget(self._quick_add_input)
        quick_add_btn = QPushButton(translate("OptimizeSkillDialog", "Add"))
        quick_add_btn.clicked.connect(self._quick_add_test_case)
        quick_layout.addWidget(quick_add_btn)
        self._quick_add_row.setVisible(False)
        tc_layout.addWidget(self._quick_add_row)

        # Plain text input row (shown when no VALIDATION.md)
        self._plain_input_row = QWidget()
        input_layout = QHBoxLayout(self._plain_input_row)
        input_layout.setContentsMargins(0, 0, 0, 0)
        self._test_input = QLineEdit()
        self._test_input.setPlaceholderText(translate("OptimizeSkillDialog", "Enter test case arguments..."))
        input_layout.addWidget(self._test_input)

        add_btn = QPushButton(translate("OptimizeSkillDialog", "Add"))
        add_btn.clicked.connect(self._add_test_case)
        input_layout.addWidget(add_btn)

        tc_layout.addWidget(self._plain_input_row)

        # Remove button (always visible)
        remove_row = QHBoxLayout()
        remove_row.addStretch()
        remove_btn = QPushButton(translate("OptimizeSkillDialog", "Remove"))
        remove_btn.clicked.connect(self._remove_test_case)
        remove_row.addWidget(remove_btn)
        tc_layout.addLayout(remove_row)

        # Structured "Add Test Case" button (shown in structured mode)
        self._structured_add_btn = QPushButton(translate("OptimizeSkillDialog", "Add Test Case"))
        self._structured_add_btn.clicked.connect(self._add_structured_test_case)
        self._structured_add_btn.setVisible(False)
        tc_layout.addWidget(self._structured_add_btn)

        tc_group.setLayout(tc_layout)
        layout.addWidget(tc_group)

        # -- Settings --
        settings_group = QGroupBox(translate("OptimizeSkillDialog", "Settings"))
        settings_layout = QFormLayout()

        self._iterations_spin = QSpinBox()
        self._iterations_spin.setRange(1, 50)
        self._iterations_spin.setValue(10)
        settings_layout.addRow(translate("OptimizeSkillDialog", "Iterations:"), self._iterations_spin)

        self._runs_spin = QSpinBox()
        self._runs_spin.setRange(1, 5)
        self._runs_spin.setValue(2)
        settings_layout.addRow(translate("OptimizeSkillDialog", "Runs per test:"), self._runs_spin)

        self._strategy_combo = QComboBox()
        self._strategy_combo.addItems([
            translate("OptimizeSkillDialog", "Conservative"),
            translate("OptimizeSkillDialog", "Balanced"),
            translate("OptimizeSkillDialog", "Aggressive"),
        ])
        self._strategy_combo.setCurrentIndex(1)  # Balanced
        settings_layout.addRow(translate("OptimizeSkillDialog", "Strategy:"), self._strategy_combo)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # -- Metrics --
        metrics_group = QGroupBox(translate("OptimizeSkillDialog", "Metrics"))
        metrics_layout = QVBoxLayout()

        self._metric_checks = {}
        for name, label, default in [
            ("completion", translate("OptimizeSkillDialog", "Completion"), True),
            ("error_rate", translate("OptimizeSkillDialog", "Error rate"), True),
            ("correctness", translate("OptimizeSkillDialog", "Correctness (geometry validation)"), True),
            ("efficiency", translate("OptimizeSkillDialog", "Efficiency"), True),
            ("visual_similarity", translate("OptimizeSkillDialog", "Visual similarity"), False),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(default)
            self._metric_checks[name] = cb
            metrics_layout.addWidget(cb)

        # Reference image row (enabled when visual_similarity is checked)
        ref_row = QHBoxLayout()
        self._ref_image_edit = QLineEdit()
        self._ref_image_edit.setPlaceholderText(translate("OptimizeSkillDialog", "Reference image path..."))
        self._ref_image_edit.setEnabled(False)
        ref_row.addWidget(self._ref_image_edit)

        self._browse_btn = QPushButton(translate("OptimizeSkillDialog", "Browse..."))
        self._browse_btn.setEnabled(False)
        self._browse_btn.clicked.connect(self._browse_reference_image)
        ref_row.addWidget(self._browse_btn)

        metrics_layout.addLayout(ref_row)

        self._metric_checks["visual_similarity"].toggled.connect(
            self._ref_image_edit.setEnabled
        )
        self._metric_checks["visual_similarity"].toggled.connect(
            self._browse_btn.setEnabled
        )

        metrics_group.setLayout(metrics_layout)
        layout.addWidget(metrics_group)

        # -- Advanced (collapsed by default) --
        self._advanced_btn = QPushButton(translate("OptimizeSkillDialog", "Advanced") + " \u25b6")
        self._advanced_btn.setFlat(True)
        self._advanced_btn.setStyleSheet("text-align: left; font-weight: bold;")
        self._advanced_btn.clicked.connect(self._toggle_advanced)
        layout.addWidget(self._advanced_btn)

        self._advanced_group = QGroupBox()
        advanced_layout = QFormLayout()

        self._budget_spin = QSpinBox()
        self._budget_spin.setRange(5, 100)
        self._budget_spin.setValue(30)
        advanced_layout.addRow(translate("OptimizeSkillDialog", "Tool call budget:"), self._budget_spin)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 1800)
        self._timeout_spin.setValue(300)
        self._timeout_spin.setSuffix("s")
        advanced_layout.addRow(translate("OptimizeSkillDialog", "Run timeout:"), self._timeout_spin)

        self._tolerance_spin = QDoubleSpinBox()
        self._tolerance_spin.setRange(0.0, 0.20)
        self._tolerance_spin.setSingleStep(0.01)
        self._tolerance_spin.setValue(0.05)
        self._tolerance_spin.setDecimals(2)
        advanced_layout.addRow(translate("OptimizeSkillDialog", "Keep tolerance:"), self._tolerance_spin)

        self._retries_spin = QSpinBox()
        self._retries_spin.setRange(0, 5)
        self._retries_spin.setValue(2)
        self._retries_spin.setToolTip(
            translate("OptimizeSkillDialog", "Extra retry attempts per test case on network/timeout errors"))
        advanced_layout.addRow(translate("OptimizeSkillDialog", "Network retries:"), self._retries_spin)

        self._advanced_group.setLayout(advanced_layout)
        self._advanced_group.setVisible(False)
        layout.addWidget(self._advanced_group)

        # -- Buttons --
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        start_btn = QPushButton(translate("OptimizeSkillDialog", "Start Optimization"))
        start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(start_btn)

        close_btn = QPushButton(translate("OptimizeSkillDialog", "Close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    # ---- Structured parameter fields ------------------------------------

    def _on_skill_changed(self, index):
        """Update test case UI when skill selection changes."""
        skill_name = self._skill_combo.currentText()
        self._clear_param_fields()
        self._validation_content = ""
        self._param_defs = {}

        try:
            from freecad_ai.extensions.skills import SkillsRegistry
            registry = SkillsRegistry()
            skill = registry.get_skill(skill_name)
            if skill and skill.validation_path:
                with open(skill.validation_path) as f:
                    content = f.read()
                from freecad_ai.extensions.skill_validator import parse_validation_md
                param_defs, rules = parse_validation_md(content)
                if param_defs:
                    self._param_defs = param_defs
                    self._validation_content = content
                    self._build_param_fields(param_defs, rules)
                    return
        except Exception:
            pass

    def _extract_str_values(self, param_name, rules):
        """Extract possible string values for a param from 'when' conditions."""
        values = set()
        for rule in rules:
            if not rule.condition:
                continue
            # Parse conditions like: lid_type == "screw"
            m = re.match(
                r'(\w+)\s*==\s*["\']([^"\']+)["\']',
                rule.condition.strip(),
            )
            if m and m.group(1) == param_name:
                values.add(m.group(2))
        return sorted(values)

    def _build_param_fields(self, param_defs, rules):
        """Create structured input widgets for each parameter."""
        self._param_widgets = {}

        for name, pdef in param_defs.items():
            if pdef.type == "float":
                widget = QDoubleSpinBox()
                widget.setRange(0, 10000)
                widget.setDecimals(2)
                if pdef.default is not None:
                    widget.setValue(float(pdef.default))
            elif pdef.type == "int":
                widget = QSpinBox()
                widget.setRange(0, 10000)
                if pdef.default is not None:
                    widget.setValue(int(pdef.default))
            elif pdef.type == "str":
                # Check if this param appears in 'when' conditions
                possible_values = self._extract_str_values(name, rules)
                if possible_values:
                    widget = QComboBox()
                    widget.addItems(possible_values)
                    if pdef.default is not None and pdef.default in possible_values:
                        widget.setCurrentIndex(possible_values.index(pdef.default))
                else:
                    widget = QLineEdit()
                    if pdef.default is not None:
                        widget.setText(str(pdef.default))
            elif pdef.type == "bool":
                widget = QCheckBox()
                if pdef.default is not None:
                    widget.setChecked(bool(pdef.default))
            else:
                widget = QLineEdit()
                if pdef.default is not None:
                    widget.setText(str(pdef.default))

            self._param_layout.addRow(QLabel(name + ":"), widget)
            self._param_widgets[name] = widget

        # Build quick-add placeholder from param names/defaults
        parts = []
        for name, pdef in param_defs.items():
            if pdef.default is not None:
                parts.append(f"{name}={pdef.default}")
            else:
                parts.append(name)
        self._quick_add_input.setPlaceholderText(", ".join(parts))

        # Show structured UI, hide plain text
        self._structured_widget.setVisible(True)
        self._structured_add_btn.setVisible(True)
        self._quick_add_row.setVisible(True)
        self._plain_input_row.setVisible(False)

    def _clear_param_fields(self):
        """Remove all dynamically created param widgets."""
        self._param_widgets = {}
        # Clear the form layout
        if self._param_layout is not None:
            while self._param_layout.count():
                item = self._param_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

        # Hide structured UI, show plain text
        if self._structured_widget is not None:
            self._structured_widget.setVisible(False)
        if self._structured_add_btn is not None:
            self._structured_add_btn.setVisible(False)
        if self._quick_add_row is not None:
            self._quick_add_row.setVisible(False)
        if self._plain_input_row is not None:
            self._plain_input_row.setVisible(True)

    def _read_param_values(self):
        """Read current values from structured param widgets into a dict."""
        params = {}
        for name, widget in self._param_widgets.items():
            pdef = self._param_defs.get(name)
            if isinstance(widget, QDoubleSpinBox):
                params[name] = widget.value()
            elif isinstance(widget, QSpinBox):
                params[name] = widget.value()
            elif isinstance(widget, QComboBox):
                params[name] = widget.currentText()
            elif isinstance(widget, QCheckBox):
                params[name] = widget.isChecked()
            elif isinstance(widget, QLineEdit):
                val = widget.text().strip()
                # Try to coerce to the declared type
                if pdef and pdef.type == "float":
                    try:
                        val = float(val)
                    except ValueError:
                        pass
                elif pdef and pdef.type == "int":
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                params[name] = val
        return params

    def _format_params_str(self, params):
        """Format a params dict as a display string like 'L=100, W=80'."""
        parts = []
        for name, value in params.items():
            parts.append(f"{name}={value}")
        return ", ".join(parts)

    def _add_structured_test_case(self):
        """Add a test case from the structured parameter widgets."""
        params = self._read_param_values()
        display = self._format_params_str(params)
        if display:
            from .compat import QtWidgets as _Qw
            item = _Qw.QListWidgetItem(display)
            item.setData(QtCore.Qt.UserRole, params)
            self._test_list.addItem(item)

    def _quick_add_test_case(self):
        """Parse quick-add text and add a test case."""
        text = self._quick_add_input.text().strip()
        if not text:
            return

        # Start from defaults
        params = {}
        for name, pdef in self._param_defs.items():
            if pdef.default is not None:
                params[name] = pdef.default

        # Parse overrides: "L=100, W=80, lid_type=screw"
        for part in text.split(","):
            part = part.strip()
            if "=" in part:
                key, val = part.split("=", 1)
                key = key.strip()
                val = val.strip()
                pdef = self._param_defs.get(key)
                if pdef:
                    if pdef.type == "float":
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                    elif pdef.type == "int":
                        try:
                            val = int(val)
                        except ValueError:
                            pass
                    elif pdef.type == "bool":
                        val = val.lower() in ("true", "1", "yes")
                params[key] = val

        display = self._format_params_str(params)
        if display:
            from .compat import QtWidgets as _Qw
            item = _Qw.QListWidgetItem(display)
            item.setData(QtCore.Qt.UserRole, params)
            self._test_list.addItem(item)
            self._quick_add_input.clear()

    # ---- Slots ----------------------------------------------------------

    def _add_test_case(self):
        text = self._test_input.text().strip()
        if text:
            self._test_list.addItem(text)
            self._test_input.clear()

    def _remove_test_case(self):
        row = self._test_list.currentRow()
        if row >= 0:
            self._test_list.takeItem(row)

    def _browse_reference_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            translate("OptimizeSkillDialog", "Select Reference Image"),
            "",
            translate("OptimizeSkillDialog", "Images (*.png *.jpg *.jpeg *.bmp)"),
        )
        if path:
            self._ref_image_edit.setText(path)

    def _toggle_advanced(self):
        visible = not self._advanced_group.isVisible()
        self._advanced_group.setVisible(visible)
        arrow = "\u25bc" if visible else "\u25b6"
        self._advanced_btn.setText(translate("OptimizeSkillDialog", "Advanced") + " " + arrow)

    def _on_start(self):
        # Validate: at least one test case
        if self._test_list.count() == 0:
            QMessageBox.warning(
                self,
                translate("OptimizeSkillDialog", "Validation Error"),
                translate("OptimizeSkillDialog", "Please add at least one test case."),
            )
            return

        # Validate: at least two metrics enabled
        enabled_metrics = [
            name for name, cb in self._metric_checks.items() if cb.isChecked()
        ]
        if len(enabled_metrics) < 2:
            QMessageBox.warning(
                self,
                translate("OptimizeSkillDialog", "Validation Error"),
                translate("OptimizeSkillDialog", "Please enable at least two metrics."),
            )
            return

        # Warning if iterations > 20
        if self._iterations_spin.value() > 20:
            answer = QMessageBox.question(
                self,
                translate("OptimizeSkillDialog", "High Iteration Count"),
                translate("OptimizeSkillDialog",
                    "Running more than 20 iterations may take a very long time. "
                    "Continue?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self._result_config = self.get_config()
        self.accept()

    # ---- Public API -----------------------------------------------------

    def get_config(self) -> dict:
        """Return the current dialog configuration as a dict."""
        strategy_map = {0: "conservative", 1: "balanced", 2: "aggressive"}

        test_cases = []
        for i in range(self._test_list.count()):
            item = self._test_list.item(i)
            tc = {"args": item.text()}
            params = item.data(QtCore.Qt.UserRole)
            if params:
                tc["params"] = params
            test_cases.append(tc)

        metrics = [
            name for name, cb in self._metric_checks.items() if cb.isChecked()
        ]

        return {
            "skill_name": self._skill_combo.currentText(),
            "test_cases": test_cases,
            "iterations": self._iterations_spin.value(),
            "runs_per_test": self._runs_spin.value(),
            "strategy": strategy_map.get(
                self._strategy_combo.currentIndex(), "balanced"
            ),
            "metrics": metrics,
            "budget": self._budget_spin.value(),
            "timeout": self._timeout_spin.value(),
            "tolerance": self._tolerance_spin.value(),
            "max_retries": self._retries_spin.value(),
            "reference_image": self._ref_image_edit.text(),
            "weights": {},
        }

    @property
    def result_config(self) -> dict | None:
        """Return the config dict after dialog accepted, None if cancelled."""
        return self._result_config
