"""Tools page: reranking, user tools, skills, hooks, and the editor
preference that governs how the last two open a file."""

import os

from ..compat import QtWidgets, QtCore, QtGui
from ...i18n import translate
from .base import SettingsPage

QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QGroupBox = QtWidgets.QGroupBox
QComboBox = QtWidgets.QComboBox
QLineEdit = QtWidgets.QLineEdit
QSpinBox = QtWidgets.QSpinBox
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
QListWidget = QtWidgets.QListWidget
QListWidgetItem = QtWidgets.QListWidgetItem
QFileDialog = QtWidgets.QFileDialog
QMessageBox = QtWidgets.QMessageBox
QPlainTextEdit = QtWidgets.QPlainTextEdit

_RERANK_METHODS = ["off", "keyword", "llm"]


class ToolsPage(SettingsPage):
    """Tool Reranking, User Tools, Skills, Hooks, and Editor groups."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._user_tool_files = []
        self._skills_status = []
        self.host_can_close = lambda: True
        self._cfg = None
        # The reranker pair as read from the config, which may hold a
        # value the combo cannot show (see apply_to).
        self._loaded_rerank = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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

        rerank_group.setLayout(rerank_layout)
        layout.addWidget(rerank_group)

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
            translate("SettingsDialog", "Also scan FreeCAD macro directory")
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
        self.skills_list.currentRowChanged.connect(
            lambda _: self._update_skills_reset_btn())

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

        extra_label = QLabel(translate(
            "SettingsDialog",
            "Extra skill folders (one per line), e.g. ~/.claude/skills. "
            "Skills there can run Python inside FreeCAD; only add folders you trust."))
        extra_label.setWordWrap(True)
        skills_layout.addWidget(extra_label)
        extra_row = QHBoxLayout()
        self.extra_skill_dirs_edit = QPlainTextEdit()
        self.extra_skill_dirs_edit.setMaximumHeight(60)
        extra_row.addWidget(self.extra_skill_dirs_edit)
        extra_browse_btn = QPushButton(translate("SettingsDialog", "Browse\u2026"))
        extra_browse_btn.clicked.connect(self._browse_extra_skill_dir)
        extra_row.addWidget(extra_browse_btn, 0, QtCore.Qt.AlignTop)
        skills_layout.addLayout(extra_row)

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

        # Editor group — affects Edit/New buttons in User Tools and Hooks above.
        editor_group = QGroupBox(translate("SettingsDialog", "Editor"))
        editor_layout = QVBoxLayout()
        self.use_external_editor_cb = QCheckBox(translate(
            "SettingsDialog",
            "Open hooks and user tools in the OS-default editor "
            "(instead of FreeCAD's docked script editor)"))
        self.use_external_editor_cb.setToolTip(translate(
            "SettingsDialog",
            "When enabled, files open via the OS file association "
            "(xdg-open / Launch Services) so this window can stay open. "
            "When disabled, files open in FreeCAD's docked Gui::PythonEditor — "
            "which requires closing this window first."))
        editor_layout.addWidget(self.use_external_editor_cb)
        editor_group.setLayout(editor_layout)
        layout.addWidget(editor_group)

    def _show(self, cfg, label):
        self._cfg = cfg
        self._loaded_rerank = {"rerank_method": cfg.rerank_method,
                               "rerank_top_n": cfg.rerank_top_n}
        m = cfg.rerank_method
        self.rerank_method_combo.setCurrentIndex(
            _RERANK_METHODS.index(m) if m in _RERANK_METHODS else 0)
        self.rerank_top_n_spin.setValue(cfg.rerank_top_n)
        self.rerank_pinned_edit.setText(", ".join(cfg.rerank_pinned_tools))
        self.use_external_editor_cb.setChecked(cfg.use_external_editor)
        self.scan_macros_cb.setChecked(cfg.scan_freecad_macros)
        self._load_user_tools_list()
        self.extra_skill_dirs_edit.setPlainText("\n".join(cfg.extra_skill_dirs))
        self._refresh_skills_list()
        self._refresh_hooks_list()

    def _values(self):
        return {
            "rerank_method": _RERANK_METHODS[self.rerank_method_combo.currentIndex()],
            "rerank_top_n": self.rerank_top_n_spin.value(),
            "rerank_pinned_tools": [s.strip() for s in
                                    self.rerank_pinned_edit.text().split(",")
                                    if s.strip()],
            "use_external_editor": self.use_external_editor_cb.isChecked(),
            "scan_freecad_macros": self.scan_macros_cb.isChecked(),
            "extra_skill_dirs": self._extra_dirs_from_widget(),
        }

    def apply_to(self, cfg):
        """Only-changed writes, except the reranker method and top_n,
        which go as a pair: the Provider page's #10 preset default fills
        whichever of the two is still at its factory value, so writing
        just the edited one would let the preset's other half survive
        when Provider saves first. Written together, the pair on screen
        wins whichever page saves last. The unedited half is the value
        as loaded, not as the widget shows it, so a hand-edited method
        the combo cannot show (say "semantic") survives a top_n edit;
        it still predates any preset the Provider page just applied."""
        super().apply_to(cfg)
        if self._baseline is None:
            return
        values = self._values()
        pair = ("rerank_method", "rerank_top_n")
        edited = [k for k in pair if values[k] != self._baseline.get(k)]
        if edited:
            for k in pair:
                setattr(cfg, k, values[k] if k in edited
                        else self._loaded_rerank[k])

    # --- User Tools methods ---

    def _load_user_tools_list(self):
        """Scan user tools directory and populate the list widget."""
        from ...config import USER_TOOLS_DIR
        from ...extensions.user_tools import validate_file

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
        from ...config import USER_TOOLS_DIR

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
        from ...config import USER_TOOLS_DIR

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
        from ...config import USER_TOOLS_DIR
        from ...extensions.file_templates import render_user_tool_template

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
        from ...config import USER_TOOLS_DIR

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

    def _refresh_hooks_list(self):
        """Refresh the hooks list from the registry."""
        self.hooks_list.clear()
        try:
            from ...hooks import get_hook_registry
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
        from ...config import HOOKS_DIR
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
        from ...config import HOOKS_DIR
        from ...extensions.file_templates import render_hook_template

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
        """Live checkbox state (governs this Edit/New, saved or not), or a
        host that cannot close to reveal the docked editor."""
        return self.use_external_editor_cb.isChecked() or not self.host_can_close()

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
        """Ask to close the host window so the docked editor is reachable.

        The host decides what closing means: the Settings dialog saves or
        rejects itself, Preferences accepts or rejects FreeCAD's dialog.
        """
        choice = QMessageBox.question(
            self,
            translate("SettingsDialog", "Open Script Editor"),
            translate(
                "SettingsDialog",
                "FreeCAD's script editor is docked behind this dialog. "
                "The dialog must close so you can reach it.\n\n"
                "Save your pending settings changes first?"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if choice == QMessageBox.Save:
            self.closeHostRequested.emit(True)
            return True
        if choice == QMessageBox.Discard:
            self.closeHostRequested.emit(False)
            return True
        return False

    def _edit_hook(self):
        """Open the selected hook's hook.py in the configured editor."""
        row = self.hooks_list.currentRow()
        if row < 0:
            return
        try:
            from ...hooks import get_hook_registry
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
            from ...hooks import get_hook_registry
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
            from ...hooks import get_hook_registry
            get_hook_registry().reload()
        except Exception:
            pass
        self._refresh_hooks_list()


    # ── Skills management ──────────────────────────────────────

    def _extra_dirs_from_widget(self):
        return [line.strip() for line in
                self.extra_skill_dirs_edit.toPlainText().splitlines() if line.strip()]

    def _browse_extra_skill_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, translate("SettingsDialog", "Choose a skills folder"))
        if path:
            self.extra_skill_dirs_edit.setPlainText(
                "\n".join(self._extra_dirs_from_widget() + [path]))
            self._refresh_skills_list()

    def _refresh_skills_list(self):
        """Populate the skills list with status indicators."""
        from ...extensions.skills import SkillsRegistry

        self.skills_list.clear()
        self._skills_status = SkillsRegistry.get_skill_status(
            extra_dirs=self._extra_dirs_from_widget())

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
            elif source == "external":
                icon = "\u2197"  # ↗
                tag = "external"
            else:
                icon = "\u2713"  # ✓
                tag = "built-in"

            label = f"{icon} {name} ({tag})"
            if desc:
                label += f" — {desc[:80]}"
            item = QListWidgetItem(label)
            if info.get("compatibility"):
                item.setToolTip(translate("SettingsDialog", "Compatibility: ")
                                + info["compatibility"])
            self.skills_list.addItem(item)

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

        from ...extensions.skills import SkillsRegistry
        if SkillsRegistry.reset_to_builtin(name):
            self._refresh_skills_list()
