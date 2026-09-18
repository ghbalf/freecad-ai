"""The dialog that makes the snapshots reachable (#49).

Everything here is exercised through unbound methods against a fake self:
the dialog is a ``QDialog`` and constructing one needs a ``QApplication``,
but none of the decisions below need a widget to be real. What they do need
is to be pinned — this is the layer where a recovery feature would quietly
do the one thing it must never do.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.core.backups import Snapshot
from freecad_ai.ui import restore_dialog
from freecad_ai.ui.restore_dialog import RestoreBackupDialog


def _snapshot(original_path="/home/alf/cad/part.FCStd", label="part",
              saved_at=1758196980.0, path="/cfg/backups/part.4e8d53db.ai-backup.FCStd"):
    return Snapshot(path=path, original_path=original_path, label=label,
                    saved_at=saved_at)


class TestWhatEachRowSays:
    def test_it_names_the_document_and_where_it_lived(self, tmp_path):
        original = os.path.join(str(tmp_path), "part.FCStd")
        open(original, "w").close()

        text = restore_dialog._describe(_snapshot(original_path=original))

        assert "part" in text
        assert original in text

    def test_it_dates_the_snapshot(self):
        text = restore_dialog._describe(_snapshot())

        assert __import__("re").search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text), text

    def test_a_missing_original_is_flagged(self):
        """Knowing the document is gone is what tells the user this snapshot
        is the only copy left — the difference between 'compare' and 'rescue'."""
        text = restore_dialog._describe(_snapshot(original_path="/deleted/part.FCStd"))

        assert "missing" in text.lower()

    def test_an_unknown_original_says_so(self):
        text = restore_dialog._describe(_snapshot(original_path=""))

        assert "unknown" in text.lower()
        assert "part" in text


class TestRestoring:
    def _dialog(self, snapshots, current_row=0):
        listw = MagicMock()
        listw.currentRow.return_value = current_row
        return SimpleNamespace(
            _snapshots=snapshots,
            _list=listw,
            accept=MagicMock(),
        )

    def test_nothing_selected_restores_nothing(self):
        d = self._dialog([_snapshot()], current_row=-1)

        with patch.object(restore_dialog.backups, "restore_copy") as restore:
            RestoreBackupDialog._on_restore(d)

        restore.assert_not_called()

    def test_a_cancelled_save_dialog_restores_nothing(self):
        d = self._dialog([_snapshot()])

        with patch.object(restore_dialog.QFileDialog, "getSaveFileName",
                          return_value=("", "")), \
                patch.object(restore_dialog.backups, "restore_copy") as restore:
            RestoreBackupDialog._on_restore(d)

        restore.assert_not_called()

    def test_the_chosen_path_is_what_gets_written(self):
        snap = _snapshot()
        d = self._dialog([snap])

        with patch.object(restore_dialog.QFileDialog, "getSaveFileName",
                          return_value=("/home/alf/cad/rescued.FCStd", "")), \
                patch.object(restore_dialog.backups, "restore_copy") as restore:
            RestoreBackupDialog._on_restore(d)

        restore.assert_called_once_with(snap, "/home/alf/cad/rescued.FCStd")
        d.accept.assert_called_once()

    def test_refusing_to_overwrite_the_original_is_reported_not_raised(self):
        """restore_copy refuses that one destination. The dialog has to turn
        it into a sentence, not a traceback in the Report view."""
        d = self._dialog([_snapshot()])

        with patch.object(restore_dialog.QFileDialog, "getSaveFileName",
                          return_value=("/home/alf/cad/part.FCStd", "")), \
                patch.object(restore_dialog.backups, "restore_copy",
                             side_effect=ValueError("cannot overwrite")), \
                patch.object(restore_dialog.QMessageBox, "warning") as warn:
            RestoreBackupDialog._on_restore(d)

        warn.assert_called_once()
        d.accept.assert_not_called(), "the user stays in the dialog to retry"

    def test_a_failed_copy_is_reported_not_raised(self):
        d = self._dialog([_snapshot()])

        with patch.object(restore_dialog.QFileDialog, "getSaveFileName",
                          return_value=("/mnt/readonly/part.FCStd", "")), \
                patch.object(restore_dialog.backups, "restore_copy",
                             side_effect=OSError("Read-only file system")), \
                patch.object(restore_dialog.QMessageBox, "warning") as warn:
            RestoreBackupDialog._on_restore(d)

        warn.assert_called_once()


class TestSelection:
    def _list(self, row):
        listw = MagicMock()
        listw.currentRow.return_value = row
        return listw

    def test_the_highlighted_row_is_the_one_restored(self):
        a, b = _snapshot(label="a"), _snapshot(label="b")

        assert restore_dialog._selected_snapshot(self._list(1), [a, b]) is b

    def test_no_highlight_selects_nothing(self):
        """An empty list reports row -1, which indexes the *last* snapshot in
        Python — restoring the wrong document from a stray click."""
        assert restore_dialog._selected_snapshot(self._list(-1), [_snapshot()]) is None

    def test_a_stale_row_selects_nothing(self):
        assert restore_dialog._selected_snapshot(self._list(3), [_snapshot()]) is None
