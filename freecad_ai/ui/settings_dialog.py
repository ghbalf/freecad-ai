"""The workbench's Settings dialog (gear button, FreeCADAI_OpenSettings).

A frame around the four settings pages that Edit → Preferences → FreeCAD
AI also shows (#101): it stacks them in one scroll area and adds OK/Cancel
plus the one thing only a dialog can do, vetoing OK on a profile that
cannot work. The pages own every field; OK writes only what changed.
"""

from .compat import QtWidgets
from ..i18n import translate

QDialog = QtWidgets.QDialog
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QPushButton = QtWidgets.QPushButton

QMessageBox = QtWidgets.QMessageBox

from ..config import get_config, notify_config_changed, save_current_config
from .provider_section import ProviderSection
from .settings_pages import BehaviorPage, McpPage, ProviderPage, ToolsPage


class SettingsDialog(QDialog):
    """Configuration dialog for FreeCAD AI."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("SettingsDialog", "FreeCAD AI Settings"))
        self.setMinimumHeight(400)
        self._build_ui()
        # Width comes from the built layout, never a constant. The profile
        # row (combo + New/Rename/Delete) is the widest thing on the form
        # and its buttons are translated, so a hardcoded width clips the
        # rightmost one in some locale — which is how a 540 predating that
        # row came to hide Delete behind a horizontal scrollbar. Must run
        # after _build_ui(): sizeHint() before it describes an empty dialog.
        # Height stays fixed; the content is taller than most screens and
        # is meant to scroll — which is also why the vertical scrollbar is
        # always there, and why its width has to be added on: sizeHint()
        # does not reserve room for it, leaving the form overflowing by
        # exactly one scrollbar and growing a horizontal one to say so.
        width = self.sizeHint().width() + self.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent)
        self.setMinimumWidth(width)
        w, h = self._saved_size() or (width, 700)
        avail = self.screen().availableGeometry()
        # Never below the layout's width (#78), never beyond the screen
        # (saved on a big monitor, reopened on a laptop).
        self.resize(max(width, min(w, avail.width())),
                    max(self.minimumHeight(), min(h, avail.height())))
        # Only a size the user changed is written back: an untouched
        # dialog changes no config field, size included.
        self._opened_size = (self.width(), self.height())
        self._load_from_config()

    @staticmethod
    def _saved_size():
        """The remembered (w, h), or None for a missing or hand-mangled value."""
        size = get_config().settings_dialog_size
        if (isinstance(size, list) and len(size) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) and v > 0
                        for v in size)):
            return tuple(size)
        return None

    def _remember_size(self) -> bool:
        """Put a user-changed size into the config. True if it changed."""
        size = [self.width(), self.height()]
        cfg = get_config()
        if tuple(size) == self._opened_size or cfg.settings_dialog_size == size:
            return False
        cfg.settings_dialog_size = size
        return True

    def done(self, result):
        """Remember the size on every close: OK, Cancel, Esc or the X.

        OK has already folded it into its own save, so this writes only
        when nothing else did.
        """
        if self._remember_size():
            save_current_config()
        super().done(result)

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

        # LLM Provider + Utility models, Model Parameters table, and the
        # two connection probes: shared with Edit → Preferences (#99, #101).
        self.provider_page = ProviderPage()
        # Kept as a name: tests and the #78 geometry check reach the
        # profile row through it.
        self.provider_section = self.provider_page.section
        self.provider_page.busyChanged.connect(
            lambda busy: (self.save_btn.setEnabled(not busy),
                          self.cancel_btn.setEnabled(not busy)))
        layout.addWidget(self.provider_page)

        self.behavior_page = BehaviorPage()
        layout.addWidget(self.behavior_page)

        self.tools_page = ToolsPage()
        layout.addWidget(self.tools_page)
        self.tools_page.closeHostRequested.connect(
            lambda save: self._on_close_requested(save))

        self.mcp_page = McpPage()
        layout.addWidget(self.mcp_page)

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

    def _pages(self):
        return (self.provider_page, self.behavior_page, self.tools_page,
                self.mcp_page)

    def _load_from_config(self):
        cfg = get_config()
        for page in self._pages():
            # A page that cannot show the config (say a hand-edited
            # "rerank_top_n": "8") keeps its baseline None and writes
            # nothing; the others must still open and save. Same rule as
            # the Preferences pages (_PrefsPageBase._load).
            try:
                page.load(cfg)
            except Exception as e:
                try:
                    import FreeCAD
                    FreeCAD.Console.PrintError(
                        f"FreeCAD AI: {type(page).__name__} settings failed "
                        f"to load: {e}\n")
                except ImportError:
                    pass

    @staticmethod
    def _profiles_missing_base_url(profiles) -> list:
        """Sorted labels of profiles with no Base URL, which cannot work.

        LLMClient builds every request URL as base_url + a path, so a blank
        one yields a relative path and a network error nowhere near the
        cause. Resolution deliberately does not substitute the provider
        preset here — a silent substitution is issue #75's shape — so the
        blank has to be surfaced instead.
        """
        return sorted(
            label for label, prof in profiles.items()
            if not (getattr(prof, "base_url", "") or "").strip())

    def _confirm_incomplete_profiles(self, profiles) -> bool:
        """Ask before saving a profile that cannot work. True to proceed.

        Covers both ways a Base URL is unusable — blank, or still carrying a
        preset placeholder — in a single question, so a config with one of
        each is not two dialogs deep before it can be saved.

        A question rather than a refusal: a config may already carry a
        half-filled profile the user never selects, and blocking OK on it
        would strand every unrelated setting in this dialog.
        """
        problems = []
        blank = self._profiles_missing_base_url(profiles)
        if blank:
            problems.append(translate(
                "SettingsDialog",
                "No Base URL is set for: %s.") % ", ".join(blank))
        unfilled = ProviderSection._profiles_with_url_placeholder(profiles)
        if unfilled:
            problems.append(translate(
                "SettingsDialog",
                "The Base URL for %s still contains a placeholder such as "
                "{ACCOUNT_ID}. Replace it with the value from your provider "
                "account.") % ", ".join(unfilled))
        if not problems:
            return True
        return QMessageBox.question(
            self,
            translate("SettingsDialog", "Profile cannot be used as set up"),
            "\n\n".join(problems) + "\n\n" + translate(
                "SettingsDialog",
                "Requests made with such a profile fail with a connection "
                "error rather than a clear message. Save anyway?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def _on_close_requested(self, save):
        """A page's editor prompt: Save → the OK path, Discard → Cancel."""
        if save:
            self._save()
        else:
            self.reject()

    def _save(self):
        """OK: veto check, every page's changes, one save, one notify."""
        # Commit the visible profile widgets first, so the veto sees the
        # Base URL as typed.
        section = self.provider_page.section
        section.commit()
        if not self._confirm_incomplete_profiles(section.profiles()):
            return
        cfg = get_config()
        # Provider last: its #10 reranker default applies only while the
        # live config is still at factory defaults, so an explicit choice
        # the Tools page just wrote wins.
        for page in (self.behavior_page, self.tools_page, self.mcp_page,
                     self.provider_page):
            page.apply_to(cfg)
        self._remember_size()  # one write for OK, not a second in done()
        save_current_config()
        # The chat panel (and anything else showing config-derived state)
        # refreshes from this, whichever window saved (#99).
        notify_config_changed()
        for page in self._pages():
            page.after_save(cfg)
        self.accept()
