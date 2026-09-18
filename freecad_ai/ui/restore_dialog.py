"""Restore a document from a pre-execution recovery snapshot (#49).

``_auto_save`` has written these snapshots since #48, but nothing could read
them back, so a bad script or a crash still lost the work they were taken to
protect. This dialog is the missing half.

It restores by *copying*: the snapshot is copied to a path the user picks and
that copy is opened, leaving both the original document and the snapshot
untouched. Restoring over the live document would make the recovery feature
the thing that destroys work, which is the one outcome a recovery feature
cannot have — and it would also strand anyone who opened a snapshot only to
compare it against what they have now.
"""

import os
from datetime import datetime

from ..core import backups
from .compat import QtCore, QtGui, QtWidgets

QDialog = QtWidgets.QDialog
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QListWidget = QtWidgets.QListWidget
QPushButton = QtWidgets.QPushButton
QMessageBox = QtWidgets.QMessageBox
QFileDialog = QtWidgets.QFileDialog

_tr = lambda text: QtCore.QCoreApplication.translate("RestoreBackupDialog", text)


def _describe(snapshot) -> str:
    """The one line the user picks a snapshot by.

    Its job is to answer "which of my documents is this, and how old?" — and,
    when the source document is gone, to say so, because that is what turns a
    curiosity into the only remaining copy.
    """
    when = datetime.fromtimestamp(snapshot.saved_at).strftime("%Y-%m-%d %H:%M")
    if not snapshot.original_path:
        origin = _tr("original unknown")
    elif snapshot.original_exists:
        origin = snapshot.original_path
    else:
        origin = _tr("{0} (missing)").format(snapshot.original_path)
    return f"{snapshot.label}  —  {when}  —  {origin}"


def _selected_snapshot(list_widget, snapshots):
    """The snapshot for the highlighted row, or None.

    A free function so the dialog's decisions can be exercised against a
    stand-in self: ``QDialog`` needs a ``QApplication`` to construct, and a
    method that dispatches through ``self`` cannot be called unbound.
    """
    row = list_widget.currentRow()
    if row < 0 or row >= len(snapshots):
        return None
    return snapshots[row]


class RestoreBackupDialog(QDialog):
    """Lists the recovery snapshots and opens one as a copy."""

    def __init__(self, snapshots, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Restore from Backup"))
        self._snapshots = list(snapshots)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_tr(
            "Snapshots taken automatically before the AI ran code.\n"
            "Restoring opens a copy — your current document is not changed.")))

        self._list = QListWidget()
        # A document path easily outruns any sensible dialog width. Eliding in
        # the middle keeps both ends -- the folder it lived in and the filename
        # -- where a scrollbar would have hidden the filename entirely. The
        # flat enum form is the one PySide2 also accepts.
        self._list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._list.setTextElideMode(QtCore.Qt.ElideMiddle)
        for snapshot in self._snapshots:
            self._list.addItem(_describe(snapshot))
            self._list.item(self._list.count() - 1).setToolTip(_describe(snapshot))
        if self._snapshots:
            self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        if not self._snapshots:
            layout.addWidget(QLabel(_tr(
                "No snapshots yet. One is taken each time the AI executes "
                "code on a saved document.")))

        buttons = QHBoxLayout()
        folder_btn = QPushButton(_tr("Open Backups Folder"))
        folder_btn.clicked.connect(self._on_open_folder)
        buttons.addWidget(folder_btn)
        buttons.addStretch()

        self._restore_btn = QPushButton(_tr("Restore a Copy..."))
        self._restore_btn.setDefault(True)
        self._restore_btn.setEnabled(bool(self._snapshots))
        self._restore_btn.clicked.connect(self._on_restore)
        buttons.addWidget(self._restore_btn)

        close_btn = QPushButton(_tr("Close"))
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        # Sized from the layout rather than a hardcoded resize(): the rows
        # carry full document paths and the widest one decides (#78).
        self.resize(max(self.sizeHint().width(), 640), self.sizeHint().height())

    def _on_restore(self):
        snapshot = _selected_snapshot(self._list, self._snapshots)
        if snapshot is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, _tr("Save Restored Copy As"),
            backups.default_copy_path(snapshot),
            _tr("FreeCAD Document (*.FCStd)"))
        if not destination:
            return
        try:
            backups.restore_copy(snapshot, destination)
        except Exception as exc:
            # Includes the deliberate refusal to overwrite the source
            # document. The user stays in the dialog and can pick again.
            QMessageBox.warning(self, _tr("Restore Failed"), str(exc))
            return
        self.accept()

    def _on_open_folder(self):
        from ..config import BACKUPS_DIR
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(BACKUPS_DIR))


def show_restore_dialog(parent=None):
    """Entry point for the workbench command."""
    from ..config import BACKUPS_DIR
    return RestoreBackupDialog(backups.list_snapshots(BACKUPS_DIR), parent).exec_()
