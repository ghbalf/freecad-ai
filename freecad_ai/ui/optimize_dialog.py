"""Optimize Skill dialog for configuring skill optimization runs.

Allows users to select a skill, define test cases, configure iteration
settings, choose metrics, and set advanced parameters before launching
an optimization run.
"""

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

_tr = lambda text: QtCore.QCoreApplication.translate("OptimizeSkillDialog", text)


class OptimizeSkillDialog(QDialog):
    """Dialog for configuring a skill optimization run."""

    def __init__(self, skills_list: list, preselect: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Optimize Skill"))
        self.setMinimumWidth(520)
        self._result_config = None
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
        skill_layout.addRow(_tr("Skill:"), self._skill_combo)
        layout.addLayout(skill_layout)

        # -- Test Cases --
        tc_group = QGroupBox(_tr("Test Cases"))
        tc_layout = QVBoxLayout()

        self._test_list = QListWidget()
        tc_layout.addWidget(self._test_list)

        input_row = QHBoxLayout()
        self._test_input = QLineEdit()
        self._test_input.setPlaceholderText(_tr("Enter test case arguments..."))
        input_row.addWidget(self._test_input)

        add_btn = QPushButton(_tr("Add"))
        add_btn.clicked.connect(self._add_test_case)
        input_row.addWidget(add_btn)

        remove_btn = QPushButton(_tr("Remove"))
        remove_btn.clicked.connect(self._remove_test_case)
        input_row.addWidget(remove_btn)

        tc_layout.addLayout(input_row)
        tc_group.setLayout(tc_layout)
        layout.addWidget(tc_group)

        # -- Settings --
        settings_group = QGroupBox(_tr("Settings"))
        settings_layout = QFormLayout()

        self._iterations_spin = QSpinBox()
        self._iterations_spin.setRange(1, 50)
        self._iterations_spin.setValue(10)
        settings_layout.addRow(_tr("Iterations:"), self._iterations_spin)

        self._runs_spin = QSpinBox()
        self._runs_spin.setRange(1, 5)
        self._runs_spin.setValue(2)
        settings_layout.addRow(_tr("Runs per test:"), self._runs_spin)

        self._strategy_combo = QComboBox()
        self._strategy_combo.addItems([
            _tr("Conservative"),
            _tr("Balanced"),
            _tr("Aggressive"),
        ])
        self._strategy_combo.setCurrentIndex(1)  # Balanced
        settings_layout.addRow(_tr("Strategy:"), self._strategy_combo)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # -- Metrics --
        metrics_group = QGroupBox(_tr("Metrics"))
        metrics_layout = QVBoxLayout()

        self._metric_checks = {}
        for name, label, default in [
            ("completion", _tr("Completion"), True),
            ("error_rate", _tr("Error rate"), True),
            ("geometric_checks", _tr("Geometric checks"), True),
            ("efficiency", _tr("Efficiency"), True),
            ("visual_similarity", _tr("Visual similarity"), False),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(default)
            self._metric_checks[name] = cb
            metrics_layout.addWidget(cb)

        # Reference image row (enabled when visual_similarity is checked)
        ref_row = QHBoxLayout()
        self._ref_image_edit = QLineEdit()
        self._ref_image_edit.setPlaceholderText(_tr("Reference image path..."))
        self._ref_image_edit.setEnabled(False)
        ref_row.addWidget(self._ref_image_edit)

        self._browse_btn = QPushButton(_tr("Browse..."))
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
        self._advanced_btn = QPushButton(_tr("Advanced \u25b6"))
        self._advanced_btn.setFlat(True)
        self._advanced_btn.setStyleSheet("text-align: left; font-weight: bold;")
        self._advanced_btn.clicked.connect(self._toggle_advanced)
        layout.addWidget(self._advanced_btn)

        self._advanced_group = QGroupBox()
        advanced_layout = QFormLayout()

        self._budget_spin = QSpinBox()
        self._budget_spin.setRange(5, 100)
        self._budget_spin.setValue(30)
        advanced_layout.addRow(_tr("Tool call budget:"), self._budget_spin)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 1800)
        self._timeout_spin.setValue(300)
        self._timeout_spin.setSuffix("s")
        advanced_layout.addRow(_tr("Run timeout:"), self._timeout_spin)

        self._tolerance_spin = QDoubleSpinBox()
        self._tolerance_spin.setRange(0.0, 0.20)
        self._tolerance_spin.setSingleStep(0.01)
        self._tolerance_spin.setValue(0.05)
        self._tolerance_spin.setDecimals(2)
        advanced_layout.addRow(_tr("Keep tolerance:"), self._tolerance_spin)

        self._retries_spin = QSpinBox()
        self._retries_spin.setRange(0, 5)
        self._retries_spin.setValue(2)
        self._retries_spin.setToolTip(
            _tr("Extra retry attempts per test case on network/timeout errors"))
        advanced_layout.addRow(_tr("Network retries:"), self._retries_spin)

        self._advanced_group.setLayout(advanced_layout)
        self._advanced_group.setVisible(False)
        layout.addWidget(self._advanced_group)

        # -- Buttons --
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        start_btn = QPushButton(_tr("Start Optimization"))
        start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(start_btn)

        close_btn = QPushButton(_tr("Close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

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
            _tr("Select Reference Image"),
            "",
            _tr("Images (*.png *.jpg *.jpeg *.bmp)"),
        )
        if path:
            self._ref_image_edit.setText(path)

    def _toggle_advanced(self):
        visible = not self._advanced_group.isVisible()
        self._advanced_group.setVisible(visible)
        arrow = "\u25bc" if visible else "\u25b6"
        self._advanced_btn.setText(_tr("Advanced") + " " + arrow)

    def _on_start(self):
        # Validate: at least one test case
        if self._test_list.count() == 0:
            QMessageBox.warning(
                self,
                _tr("Validation Error"),
                _tr("Please add at least one test case."),
            )
            return

        # Validate: at least two metrics enabled
        enabled_metrics = [
            name for name, cb in self._metric_checks.items() if cb.isChecked()
        ]
        if len(enabled_metrics) < 2:
            QMessageBox.warning(
                self,
                _tr("Validation Error"),
                _tr("Please enable at least two metrics."),
            )
            return

        # Warning if iterations > 20
        if self._iterations_spin.value() > 20:
            answer = QMessageBox.question(
                self,
                _tr("High Iteration Count"),
                _tr(
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
            test_cases.append({"args": self._test_list.item(i).text()})

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
