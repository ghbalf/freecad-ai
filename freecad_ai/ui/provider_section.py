"""The LLM Provider and Utility models sections, shared by two windows.

The workbench's Settings dialog and Edit → Preferences → FreeCAD AI both
embed this widget, so the two can never offer different providers or
write different things (#12, #97, #99). It edits a private copy of the
profiles; nothing reaches the config until apply_to().

Strings keep the "SettingsDialog" translation context they had before
the move, so existing translations still apply.
"""

import copy
import dataclasses
import re

from .compat import QtWidgets, QtCore
from ..i18n import translate, QT_TRANSLATE_NOOP
from ..config import PROVIDER_PRESETS, ProviderConfig
from ..llm.providers import get_api_style, get_provider_names

QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QComboBox = QtWidgets.QComboBox
QLineEdit = QtWidgets.QLineEdit
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
QMessageBox = QtWidgets.QMessageBox
QInputDialog = QtWidgets.QInputDialog
QSpinBox = QtWidgets.QSpinBox
QListWidget = QtWidgets.QListWidget
Signal = QtCore.Signal

_URL_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z0-9_]+\}")

# The Behavior page's "Compact above" spin starts here too. Applied only to
# values typed on this page, so a smaller hand edit in config.json survives.
_COMPACT_ABOVE_FLOOR = 4000

# Suggestions only (#108): anything typed is saved and sent as-is, so a
# level a vendor adds next year needs no release.
_THINKING_SUGGESTIONS = {
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
    "openai": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
}


class ProviderSection(QWidget):
    """Profiles, their connection fields, and utility routing."""

    # object, not ProviderConfig/dict: PySide2 and PySide6 both carry any
    # Python object through Signal(object) by reference, which is what lets
    # an aboutToCommit slot write into the profile before emit() returns.
    profileShown = Signal(object)
    aboutToCommit = Signal(object)
    presetApplied = Signal(object)
    modelChanged = Signal(str)

    # Call sites that can run on their own profile. The identifier is the
    # contract with create_client(cfg, utility); adding a new one here and
    # at its call site is the whole opt-in.
    # The labels are QT_TRANSLATE_NOOP-wrapped so pylupdate5 (which
    # extracts string literals only) finds them here; the use site below
    # runs them through translate() to resolve them at runtime.
    UTILITIES = [
        ("compaction",
         QT_TRANSLATE_NOOP("SettingsDialog", "Context compaction")),
        ("skill_eval",
         QT_TRANSLATE_NOOP("SettingsDialog", "Skill evaluation")),
        ("tool_optimize",
         QT_TRANSLATE_NOOP("SettingsDialog", "Tool optimisation")),
        ("rerank",
         QT_TRANSLATE_NOOP("SettingsDialog", "Tool reranking")),
    ]

    @classmethod
    def _collect_utility_profiles(cls, selections: dict) -> dict:
        """Turn dropdown selections into the config mapping.

        An empty selection means inherit the active profile and is stored
        by omission, so config.json carries only real overrides.

        A classmethod because it touches no widgets — that is what makes
        it testable without constructing a dialog.
        """
        known = {u for u, _ in cls.UTILITIES}
        return {
            utility: label
            for utility, label in selections.items()
            if utility in known and label
        }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profiles = {}
        self._active_profile = ""
        self._utility_profiles = {}
        self._fallback_profiles = []
        self._current_profile_label = None
        self._baseline = None
        self._pending_rerank = None
        # The profile whose provider switch recorded _pending_rerank.
        self._pending_rerank_label = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Provider group
        provider_group = QGroupBox(translate("SettingsDialog", "LLM Provider"))
        provider_layout = QFormLayout()

        # ── Profile selector ────────────────────────────────────────
        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip(translate(
            "SettingsDialog",
            "Named connection. Utilities below can each use a different one."))
        profile_row.addWidget(self.profile_combo, 1)
        self.profile_add_btn = QPushButton(translate("SettingsDialog", "New"))
        self.profile_rename_btn = QPushButton(translate("SettingsDialog", "Rename"))
        self.profile_delete_btn = QPushButton(translate("SettingsDialog", "Delete"))
        for b in (self.profile_add_btn, self.profile_rename_btn,
                  self.profile_delete_btn):
            profile_row.addWidget(b)
        provider_layout.addRow(translate("SettingsDialog", "Profile:"), profile_row)

        # Selecting a profile in the combo means "edit this one". Chat runs
        # on the profile this box is ticked for, and nothing else moves it.
        self.profile_active_check = QCheckBox(translate(
            "SettingsDialog", "Use this profile for chat"))
        self.profile_active_check.setToolTip(translate(
            "SettingsDialog",
            "The ticked profile is the one the main chat runs on.\n"
            "Utilities below inherit it unless they name their own.\n"
            "To move it, tick a different profile — there is always\n"
            "exactly one."))
        self.profile_active_check.toggled.connect(
            self._on_profile_active_toggled)
        provider_layout.addRow("", self.profile_active_check)

        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self.profile_add_btn.clicked.connect(self._on_profile_add)
        self.profile_rename_btn.clicked.connect(self._on_profile_rename)
        self.profile_delete_btn.clicked.connect(self._on_profile_delete)

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
        self.model_edit.editingFinished.connect(self._on_model_edited)
        provider_layout.addRow(translate("SettingsDialog", "Model:"), self.model_edit)

        # Vision support. A profile field, not a global one: it describes
        # this profile's model, and Test Connection probes whichever
        # profile is on screen.
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
        provider_layout.addRow(translate("SettingsDialog", "Vision:"),
                               vision_layout)

        # Compaction threshold for this profile's model (#103). A profile
        # field, not a params row: it is client-side only and must never
        # reach a vendor, where a thin proxy may 400 on an unknown key.
        self.compact_above_spin = QSpinBox()
        self.compact_above_spin.setRange(0, 1000000)
        self.compact_above_spin.setSingleStep(10000)
        self.compact_above_spin.setSpecialValueText(
            translate("SettingsDialog", "Use global"))
        self.compact_above_spin.setToolTip(
            translate("SettingsDialog",
                      "Compact older messages once the conversation is "
                      "estimated above this many tokens.\n"
                      "\"Use global\" takes the value from Behavior → Limits."))
        provider_layout.addRow(translate("SettingsDialog", "Compact above:"),
                               self.compact_above_spin)

        # Thinking for this profile's model (#108). Editable: the list is
        # suggestions, and whatever is typed goes to the vendor verbatim.
        self.thinking_combo = QtWidgets.QComboBox()
        self.thinking_combo.setEditable(True)
        self.thinking_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        # Qt's default completer is case-insensitive, and Enter or a focus
        # change then snaps "Low" to the "low" suggestion. Values are sent
        # as typed, so the case must survive.
        self.thinking_combo.completer().setCaseSensitivity(
            QtCore.Qt.CaseSensitive)
        self.thinking_combo.setToolTip(
            translate("SettingsDialog",
                      "Sent to the vendor as-is. Anthropic: a level uses "
                      "adaptive thinking, a number is a token budget.\n"
                      "Use Test Connection to check a value."))
        provider_layout.addRow(translate("SettingsDialog", "Thinking:"),
                               self.thinking_combo)
        self._global_thinking = "off"
        self._fill_thinking_combo("anthropic")

        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)

        # ── Utilities ───────────────────────────────────────────────
        # Below the profile fields they refer to, so the reading order is
        # "define connections, then say which one each job uses."
        self.utility_group = QGroupBox(translate(
            "SettingsDialog", "Utility models"))
        util_form = QFormLayout()
        self.utility_combos = {}
        for utility, ulabel in self.UTILITIES:
            combo = QComboBox()
            combo.setToolTip(translate(
                "SettingsDialog",
                "Which profile this job runs on. Leave inherited to use "
                "the active profile."))
            combo.currentIndexChanged.connect(
                lambda index, u=utility: self._on_utility_combo_changed(u, index))
            self.utility_combos[utility] = combo
            util_form.addRow(
                translate("SettingsDialog", ulabel) + ":", combo)
        self.utility_group.setLayout(util_form)
        layout.addWidget(self.utility_group)

        # ── Fallback ────────────────────────────────────────────────
        # One global ordered list, walked once per turn after the chat
        # profile fails to answer (#104).
        self.fallback_group = QGroupBox(translate(
            "SettingsDialog", "Fallback when the chat model can't be reached"))
        self.fallback_group.setToolTip(translate(
            "SettingsDialog",
            "Tried once, in this order, after the chat profile fails to "
            "answer. A paid profile in this list is used without asking."))
        fb_layout = QVBoxLayout()
        self.fallback_list = QListWidget()
        fb_layout.addWidget(self.fallback_list)
        fb_row = QHBoxLayout()
        self.fallback_picker = QComboBox()
        fb_row.addWidget(self.fallback_picker, 1)
        self.fallback_add_btn = QPushButton(translate("SettingsDialog", "Add"))
        self.fallback_remove_btn = QPushButton(
            translate("SettingsDialog", "Remove"))
        self.fallback_up_btn = QPushButton(translate("SettingsDialog", "Up"))
        self.fallback_down_btn = QPushButton(translate("SettingsDialog", "Down"))
        for btn in (self.fallback_add_btn, self.fallback_remove_btn,
                    self.fallback_up_btn, self.fallback_down_btn):
            fb_row.addWidget(btn)
        fb_layout.addLayout(fb_row)
        self.fallback_group.setLayout(fb_layout)
        layout.addWidget(self.fallback_group)
        self.fallback_add_btn.clicked.connect(self._on_fallback_add)
        self.fallback_remove_btn.clicked.connect(self._on_fallback_remove)
        self.fallback_up_btn.clicked.connect(lambda: self._move_fallback(-1))
        self.fallback_down_btn.clicked.connect(lambda: self._move_fallback(1))

    # ── Public interface ───────────────────────────────────────────

    def load(self, cfg, label=None):
        """Fill the widget from ``cfg`` and set the is_dirty() baseline.

        Deep-copies the profiles: the config is the live singleton, and an
        unrelated save_current_config() elsewhere must not flush an edit
        the user later cancels. Shows ``label`` when it names a profile,
        else the active one.
        """
        self._pending_rerank = None
        self._pending_rerank_label = None
        # Named in the "Use global" row's tooltip.
        self._global_thinking = cfg.thinking
        self._profiles = copy.deepcopy(cfg.profiles)
        self._active_profile = cfg.active_profile
        self._utility_profiles = dict(cfg.utility_profiles)
        self._fallback_profiles = list(cfg.fallback_profiles)
        shown = label if label in self._profiles else self._active_profile
        # Set before the refresh, which selects the profile being edited.
        self._current_profile_label = shown
        self._refresh_profile_combo()
        self._show_profile(shown)
        self._baseline = self._state()

    def apply_to(self, cfg):
        """Commit the visible fields, then write the scratch copy into cfg."""
        self._commit_profile_fields()
        cfg.profiles = copy.deepcopy(self._profiles)
        cfg.active_profile = self._active_profile
        cfg.utility_profiles = self._collect_utility_profiles(
            self._utility_profiles)
        cfg.fallback_profiles = list(self._fallback_profiles)
        # Factory defaults = off + 15. Checked on the *live* config: if the
        # Tools page saved an explicit reranker choice first, the pair is
        # no longer off/15 and the preset is skipped. If this runs first,
        # the preset lands and the Tools page then overwrites it — it
        # writes method and top_n together whenever either was edited, so
        # the pair on screen wins in either order. An explicit off/15 is
        # indistinguishable from untouched, as it was when this check read
        # the widgets.
        pending, self._pending_rerank = self._pending_rerank, None
        label, self._pending_rerank_label = self._pending_rerank_label, None
        if label not in self._profiles:   # the switched profile was deleted
            pending = None
        if (pending and cfg.rerank_method == "off"
                and cfg.rerank_top_n == 15):
            if pending.get("method") in ("off", "keyword", "llm"):
                cfg.rerank_method = pending["method"]
            if "top_n" in pending:
                cfg.rerank_top_n = int(pending["top_n"])

    def commit(self):
        """Write the visible fields into the profile being edited."""
        self._commit_profile_fields()

    def profiles(self) -> dict:
        return self._profiles

    def current_label(self):
        return self._current_profile_label

    def current_profile(self):
        return self._profiles.get(self._current_profile_label)

    def active_label(self) -> str:
        return self._active_profile

    def current_provider_name(self) -> str:
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        return names[idx] if 0 <= idx < len(names) else ""

    def utility_selection(self, name: str) -> str:
        combo = self.utility_combos.get(name)
        return (combo.currentData() or "") if combo is not None else ""

    def set_probe_result(self, label, *, vision=None, tools=None,
                         thinking=None):
        """Record a probe's findings on the profile it probed.

        ``label`` is captured when the probe starts, so a profile switch
        mid-probe cannot land the answer on the wrong profile; a profile
        deleted meanwhile is ignored. None means "not reported" — False is
        an answer and is recorded.
        """
        prof = self._profiles.get(label)
        if prof is None:
            return
        if vision is not None:
            prof.vision_detected = vision
        if tools is not None:
            prof.tools_detected = tools
        if thinking is not None:
            prof.thinking_detected = thinking
        if vision is not None and label == self._current_profile_label:
            self._update_vision_ui(prof)

    def is_dirty(self) -> bool:
        """Whether anything differs from the load() baseline.

        Compares state, not edit counts, so a value typed and then
        restored is clean.
        """
        if self._baseline is None:
            return False
        self._commit_profile_fields()
        return self._state() != self._baseline

    def _state(self):
        return (
            {label: dataclasses.asdict(prof)
             for label, prof in self._profiles.items()},
            self._active_profile,
            self._collect_utility_profiles(self._utility_profiles),
            list(self._fallback_profiles),
        )

    # ── Moved verbatim from SettingsDialog ─────────────────────────

    def _rename_profile(self, old: str, new: str) -> None:
        """Rename a profile, carrying every reference to it along.

        A profile's label is its identity — utility_profiles and
        active_profile store the name, not a stable id — so a rename that
        did not cascade would silently detach a utility from the
        connection it was using.
        """
        new = (new or "").strip()
        if not new:
            raise ValueError("Profile name cannot be empty")
        if old == new:
            return
        if new in self._profiles:
            raise ValueError(f"A profile named {new!r} already exists")
        if old not in self._profiles:
            raise ValueError(f"No profile named {old!r}")
        # Rebuild in place so the combo's order does not shuffle.
        self._profiles = {
            (new if label == old else label): prof
            for label, prof in self._profiles.items()
        }
        if self._active_profile == old:
            self._active_profile = new
        if self._pending_rerank_label == old:
            self._pending_rerank_label = new
        for utility, label in list(self._utility_profiles.items()):
            if label == old:
                self._utility_profiles[utility] = new
        # In place: callers and tests may hold this list.
        self._fallback_profiles[:] = [
            new if label == old else label for label in self._fallback_profiles]

    def _delete_profile(self, label: str) -> None:
        """Remove a profile, leaving nothing pointing at it."""
        if label not in self._profiles:
            raise ValueError(f"No profile named {label!r}")
        if len(self._profiles) == 1:
            raise ValueError("At least one profile is required")
        del self._profiles[label]
        if self._active_profile == label:
            self._active_profile = next(iter(self._profiles))
        for utility, mapped in list(self._utility_profiles.items()):
            if mapped == label:
                self._utility_profiles[utility] = ""
        self._fallback_profiles[:] = [
            entry for entry in self._fallback_profiles if entry != label]

    def _refresh_profile_combo(self) -> None:
        """Repopulate the profile combo without firing its handler.

        The active profile is marked in the item *text* only; the item
        data stays the bare label, because _on_profile_changed and
        findData both key off it.

        The selection follows the profile being edited, not the active
        one. Browsing no longer moves active, so re-selecting by
        _active_profile here would yank the combo back to it after every
        add, rename and delete. Falls back to the active profile, and
        then to the first entry, for the delete path — where the label
        being edited is the one that just went away.
        """
        self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            for label in self._profiles:
                text = label
                if label == self._active_profile:
                    text = translate("SettingsDialog", "{} (active)").format(label)
                self.profile_combo.addItem(text, label)
            for candidate in (getattr(self, "_current_profile_label", None),
                              self._active_profile):
                idx = self.profile_combo.findData(candidate) if candidate else -1
                if idx >= 0:
                    self.profile_combo.setCurrentIndex(idx)
                    break
            else:
                self.profile_combo.setCurrentIndex(0)
        finally:
            self.profile_combo.blockSignals(False)
        self._refresh_utility_combos()
        self._refresh_fallback_widgets()

    def _refresh_utility_combos(self) -> None:
        """Repopulate every utility dropdown from the working copy.

        Reads self._profiles, not self._cfg.profiles: a profile added or
        renamed in this dialog session must appear in these lists before
        the user presses OK.
        """
        for utility, combo in self.utility_combos.items():
            current = self._utility_profiles.get(utility, "")
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItem(translate(
                    "SettingsDialog", "(same as active profile)"), "")
                for label in self._profiles:
                    combo.addItem(label, label)
                idx = combo.findData(current)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                combo.blockSignals(False)

    def _refresh_fallback_widgets(self) -> None:
        """Repopulate the fallback list and its picker from the working copy."""
        row = self.fallback_list.currentRow()
        self.fallback_list.clear()
        for label in self._fallback_profiles:
            self.fallback_list.addItem(label)
        if 0 <= row < self.fallback_list.count():
            self.fallback_list.setCurrentRow(row)
        self.fallback_picker.clear()
        for label in self._profiles:
            if label not in self._fallback_profiles:
                self.fallback_picker.addItem(label, label)

    def _on_fallback_add(self) -> None:
        label = self.fallback_picker.currentData()
        if label and label not in self._fallback_profiles:
            self._fallback_profiles.append(label)
            self._refresh_fallback_widgets()
            self.fallback_list.setCurrentRow(len(self._fallback_profiles) - 1)

    def _on_fallback_remove(self) -> None:
        row = self.fallback_list.currentRow()
        if 0 <= row < len(self._fallback_profiles):
            del self._fallback_profiles[row]
            self._refresh_fallback_widgets()

    def _move_fallback(self, step: int) -> None:
        row = self.fallback_list.currentRow()
        target = row + step
        entries = self._fallback_profiles
        if 0 <= row < len(entries) and 0 <= target < len(entries):
            entries[row], entries[target] = entries[target], entries[row]
            self._refresh_fallback_widgets()
            self.fallback_list.setCurrentRow(target)

    def _on_utility_combo_changed(self, utility: str, index: int) -> None:
        """Track a utility dropdown's live selection in the working copy.

        Without this, _utility_profiles only reflects what was loaded when
        the dialog opened, and both _refresh_utility_combos (on a rename or
        delete elsewhere in the dialog) and the rerank probe would read
        stale state instead of the user's in-progress choice.
        """
        combo = self.utility_combos[utility]
        self._utility_profiles[utility] = combo.itemData(index) or ""

    def _on_profile_active_toggled(self, checked: bool) -> None:
        """Point chat at the profile currently being edited.

        Only the off->on transition is reachable — _show_profile disables
        the box while it is ticked — so an untick is a no-op rather than
        a way to end up with no active profile.
        """
        if not checked:
            return
        self._active_profile = self._current_profile_label
        self.profile_active_check.setEnabled(False)
        self._refresh_profile_combo()

    def _on_profile_changed(self, index):
        # Selects a profile for editing. It deliberately does NOT make it
        # active: browsing the profiles to see what they hold must not
        # silently re-point chat on OK.
        label = self.profile_combo.itemData(index)
        if not label or label == getattr(self, "_current_profile_label", None):
            return
        self._commit_profile_fields()
        self._show_profile(label)

    def _on_profile_add(self):
        base = translate("SettingsDialog", "New profile")
        label, n = base, 2
        while label in self._profiles:
            label, n = f"{base} {n}", n + 1
        self._commit_profile_fields()
        self._profiles[label] = ProviderConfig()
        # Selected for editing, not made active. _show_profile first, so
        # the refresh below finds _current_profile_label already pointing
        # at the new profile and selects it.
        self._show_profile(label)
        self._refresh_profile_combo()

    def _on_profile_rename(self):
        old = self._current_profile_label
        new, ok = QInputDialog.getText(
            self, translate("SettingsDialog", "Rename profile"),
            translate("SettingsDialog", "Name:"), QLineEdit.Normal, old)
        if not ok:
            return
        try:
            self._rename_profile(old, new)
        except ValueError as e:
            QMessageBox.warning(
                self, translate("SettingsDialog", "Rename profile"), str(e))
            return
        self._current_profile_label = new.strip()
        self._refresh_profile_combo()

    def _on_profile_delete(self):
        label = self._current_profile_label
        if QMessageBox.question(
                self, translate("SettingsDialog", "Delete profile"),
                translate("SettingsDialog",
                          "Delete profile '{}'?").format(label)) \
                != QMessageBox.Yes:
            return
        try:
            self._delete_profile(label)
        except ValueError as e:
            QMessageBox.warning(
                self, translate("SettingsDialog", "Delete profile"), str(e))
            return
        self._refresh_profile_combo()
        self._show_profile(self._active_profile)

    @staticmethod
    def _profiles_with_url_placeholder(profiles) -> list:
        """Sorted labels of profiles whose Base URL still holds a preset marker.

        A few vendor endpoints are per-account, so their preset cannot ship a
        complete URL and carries a ``{ACCOUNT_ID}``-style marker for the user
        to replace. Nothing substitutes it: the literal braces travel in the
        request path and come back as a 404 naming neither the field nor the
        fix, so the unreplaced marker has to be caught here instead.
        """
        return sorted(
            label for label, prof in profiles.items()
            if _URL_PLACEHOLDER_RE.search(
                getattr(prof, "base_url", "") or ""))

    def _fill_thinking_combo(self, api_style: str) -> None:
        """Rebuild the suggestions for an API style, keeping the text."""
        combo = self.thinking_combo
        text = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(translate("SettingsDialog", "Use global"))
            combo.setItemData(
                0, translate("SettingsDialog", "Currently: %s")
                % self._global_thinking, QtCore.Qt.ToolTipRole)
            combo.addItem(translate("SettingsDialog", "Model default"))
            combo.addItem("off")
            for level in _THINKING_SUGGESTIONS.get(
                    api_style, _THINKING_SUGGESTIONS["openai"]):
                combo.addItem(level)
            if text:
                combo.setEditText(text)
        finally:
            combo.blockSignals(False)

    def _show_thinking(self, value) -> None:
        if value is None:
            self.thinking_combo.setCurrentIndex(0)
        elif value == "default":
            self.thinking_combo.setCurrentIndex(1)
        else:
            self.thinking_combo.setEditText(value)

    def _thinking_value(self):
        """The combo's value as stored: None, "default" or the typed text."""
        text = self.thinking_combo.currentText().strip()
        if not text or text == self.thinking_combo.itemText(0):
            return None
        if text == self.thinking_combo.itemText(1):
            return "default"
        return text

    def _update_vision_ui(self, profile):
        """Update vision checkbox and label from one profile's state."""
        self._vision_override_value = profile.vision_override
        # Temporarily disconnect to avoid triggering _on_vision_override_changed
        self.vision_check.stateChanged.disconnect(self._on_vision_override_changed)
        if profile.vision_override is not None:
            self.vision_check.setChecked(profile.vision_override)
            self._vision_status_label.setText(
                translate("SettingsDialog", "(manual override)")
            )
            self._vision_reset_btn.show()
        elif profile.vision_detected is not None:
            self.vision_check.setChecked(profile.vision_detected)
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
        profile = self._profiles.get(self._current_profile_label)
        if profile is None:
            return
        profile.vision_override = None
        self._update_vision_ui(profile)

    # ── Moved with changes ─────────────────────────────────────────

    def _commit_profile_fields(self) -> None:
        """Write the visible connection widgets back into their profile.

        Called before switching away from a profile so an in-progress edit
        is not lost — the #75 complaint, from the other direction.
        """
        label = getattr(self, "_current_profile_label", None)
        prof = self._profiles.get(label)
        if prof is None:
            return
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        # Index past the registry = the temporary unknown-provider item:
        # not a choice, so the stored name stays (#97 in another shape).
        new_name = names[idx] if 0 <= idx < len(names) else prof.name
        new_model = self.model_edit.text()
        # A probe result describes one provider+model pair. Retype either
        # and the stored answer is about something else, so drop it —
        # per profile, since another profile's probe is still valid.
        if new_name != prof.name or new_model != prof.model:
            prof.vision_detected = None
            prof.tools_detected = None
            prof.thinking_detected = None
        prof.name = new_name
        prof.base_url = self.base_url_edit.text()
        prof.api_key = self.api_key_edit.text()
        prof.model = new_model
        # The vision checkbox is a profile widget like the four above; the
        # section holds its pending value so a tri-state (None) survives.
        if hasattr(self, "_vision_override_value"):
            prof.vision_override = self._vision_override_value
        if hasattr(self, "_compact_shown"):
            typed = self.compact_above_spin.value()
            if typed == self._compact_shown:
                # Unchanged, or typed back: restore exactly what was loaded.
                prof.context_window = self._compact_shown_value
            elif typed == 0:
                prof.context_window = None
            else:
                prof.context_window = max(typed, _COMPACT_ABOVE_FLOOR)
        if hasattr(self, "thinking_combo"):
            prof.thinking = self._thinking_value()
        # The Settings dialog writes its params table into prof.params
        # here; the preferences page has no table and leaves them alone.
        self.aboutToCommit.emit(prof)

    def _unknown_item_index(self):
        """Index of the temporary "<name> (unknown provider)" item, if any.

        It is always the last item, past the registry's providers, so the
        registry indices every other method relies on never shift.
        """
        n = len(get_provider_names())
        return n if self.provider_combo.count() > n else None

    def _drop_unknown_item(self):
        idx = self._unknown_item_index()
        if idx is not None:
            self.provider_combo.blockSignals(True)
            try:
                self.provider_combo.removeItem(idx)
            finally:
                self.provider_combo.blockSignals(False)

    def _show_profile(self, label: str) -> None:
        """Populate the connection widgets from a profile."""
        prof = self._profiles[label]
        self._current_profile_label = label
        names = get_provider_names()
        self._drop_unknown_item()
        if prof.name in names:
            idx = names.index(prof.name)
        else:
            # A provider the registry lacks (a hand edit, or a config from a
            # newer version) is shown as itself, never as a real provider it
            # is not, which would make picking that provider a no-op.
            self.provider_combo.blockSignals(True)
            try:
                self.provider_combo.addItem(
                    translate("SettingsDialog", "%s (unknown provider)")
                    % prof.name, prof.name)
            finally:
                self.provider_combo.blockSignals(False)
            idx = len(names)
        # Programmatic index moves must not run _on_provider_changed —
        # that handler exists to apply a preset on a *user* switch, and
        # firing it here would overwrite the profile's saved URL (#75).
        self.provider_combo.blockSignals(True)
        try:
            self.provider_combo.setCurrentIndex(idx)
        finally:
            self.provider_combo.blockSignals(False)
        self.api_key_edit.setText(prof.api_key)
        self.base_url_edit.setText(prof.base_url)
        self.model_edit.setText(prof.model)
        self._update_vision_ui(prof)
        # Remember what was shown: _commit_profile_fields writes the spin
        # back only when the user changed it, so an untouched hand edit
        # (3000, below the typed floor) is never re-clamped.
        self._compact_shown_value = prof.context_window
        self.compact_above_spin.setValue(prof.context_window or 0)
        self._compact_shown = self.compact_above_spin.value()
        self._fill_thinking_combo(get_api_style(prof.name))
        self._show_thinking(prof.thinking)

        is_active = label == self._active_profile
        # blockSignals, or populating the widgets would itself re-point
        # chat through _on_profile_active_toggled.
        self.profile_active_check.blockSignals(True)
        try:
            self.profile_active_check.setChecked(is_active)
        finally:
            self.profile_active_check.blockSignals(False)
        # Disabled while ticked: there is always exactly one active
        # profile, so the way to move it is to tick a different one, not
        # to untick this one.
        self.profile_active_check.setEnabled(not is_active)
        self.profileShown.emit(prof)

    def _on_provider_changed(self, index):
        """Apply the new provider's preset URL and model on a user switch."""
        names = get_provider_names()
        if not 0 <= index < len(names):
            return
        self._drop_unknown_item()
        preset = PROVIDER_PRESETS.get(names[index], {})
        # #10: remember the preset's reranker recommendation; apply_to
        # decides at save time whether the reranker is still untouched.
        # Every user switch overwrites it, so the last switch wins.
        self._pending_rerank = dict(preset.get("default_rerank") or {}) or None
        # Kept with it, so deleting that profile before OK drops the record.
        self._pending_rerank_label = self._current_profile_label
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
        # The dialog reloads its params table here, before the commit
        # below writes the table back.
        self.presetApplied.emit(preset)
        # New vendor, new suggestions; the typed value stays (#108).
        self._fill_thinking_combo(get_api_style(names[index]))
        # A vendor switch is an explicit "point this profile
        # elsewhere", so record it. Only a user-driven change reaches
        # here: programmatic index moves are wrapped in blockSignals.
        self._commit_profile_fields()

    def _on_model_edited(self):
        self.modelChanged.emit(self.model_edit.text().strip())
