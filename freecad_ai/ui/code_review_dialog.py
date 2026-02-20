"""Code review dialog for Plan mode and Act mode confirmation.

Shows proposed code in a read-only editor with Execute, Edit, and Cancel
buttons. After execution, shows the result inline.
"""

from .compat import QtWidgets, QtCore, QtGui

QDialog = QtWidgets.QDialog
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextEdit = QtWidgets.QTextEdit
QLabel = QtWidgets.QLabel
QPushButton = QtWidgets.QPushButton
QFont = QtGui.QFont

from ..core.executor import execute_code


class CodeReviewDialog(QDialog):
    """Dialog for reviewing and optionally executing LLM-generated code."""

    def __init__(self, code, parent=None):
        super().__init__(parent)
        self.code = code
        self.execution_result = None
        self._editable = False

        self.setWindowTitle("Review Code")
        self.setMinimumSize(600, 450)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel("Review the proposed code before executing:")
        header.setStyleSheet("font-weight: bold; margin-bottom: 4px;")
        layout.addWidget(header)

        # Code editor
        self.code_edit = QTextEdit()
        font = QFont("Monospace", 11)
        font.setStyleHint(QFont.TypeWriter)
        self.code_edit.setFont(font)
        self.code_edit.setPlainText(self.code)
        self.code_edit.setReadOnly(True)
        self.code_edit.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: #d4d4d4; "
            "border: 1px solid #555; padding: 8px; }"
        )
        layout.addWidget(self.code_edit)

        # Result area (hidden initially)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)

        self.result_text = QTextEdit()
        self.result_text.setFont(font)
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(150)
        self.result_text.setVisible(False)
        layout.addWidget(self.result_text)

        # Buttons
        btn_layout = QHBoxLayout()

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.clicked.connect(self._toggle_edit)
        btn_layout.addWidget(self.edit_btn)

        btn_layout.addStretch()

        self.execute_btn = QPushButton("Execute")
        self.execute_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "padding: 6px 20px; font-weight: bold; }"
        )
        self.execute_btn.clicked.connect(self._execute)
        btn_layout.addWidget(self.execute_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)

    def _toggle_edit(self):
        """Toggle code editor between read-only and editable."""
        self._editable = not self._editable
        self.code_edit.setReadOnly(not self._editable)
        if self._editable:
            self.edit_btn.setText("Lock")
            self.code_edit.setStyleSheet(
                "QTextEdit { background-color: #2d2d2d; color: #d4d4d4; "
                "border: 1px solid #3daee9; padding: 8px; }"
            )
        else:
            self.edit_btn.setText("Edit")
            self.code_edit.setStyleSheet(
                "QTextEdit { background-color: #1e1e1e; color: #d4d4d4; "
                "border: 1px solid #555; padding: 8px; }"
            )

    def _execute(self):
        """Execute the code and show results."""
        self.code = self.code_edit.toPlainText()
        self.execution_result = execute_code(self.code)

        self.result_label.setVisible(True)
        if self.execution_result.success:
            self.result_label.setText("Code executed successfully.")
            self.result_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        else:
            self.result_label.setText("Execution failed:")
            self.result_label.setStyleSheet("color: #c62828; font-weight: bold;")

        output = ""
        if self.execution_result.stdout.strip():
            output += self.execution_result.stdout
        if self.execution_result.stderr.strip():
            if output:
                output += "\n"
            output += self.execution_result.stderr

        if output.strip():
            self.result_text.setPlainText(output)
            self.result_text.setVisible(True)

        # Change buttons
        self.execute_btn.setEnabled(False)
        self.cancel_btn.setText("Close")
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self.accept)

    def get_result(self):
        """Return the execution result after dialog closes."""
        return self.execution_result
