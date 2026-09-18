"""The recovery snapshots must be readable back (#49).

#48 landed the managed backups directory: `_auto_save` writes one
hash-tagged snapshot per source document before every `execute_code`.
Nothing ever read them, so they were write-only — a crash or a bad script
still lost the work the snapshot was taken to protect.

The obstacle is that the snapshot cannot say where it came from. Probing
FreeCAD 1.1.1 settles it: a document's `FileName` is a *transient*
property, stored in `Document.xml` as `<_Property name="FileName"/>` with
no value, so an opened snapshot reports only its own path. The tag in the
filename is `sha1(original)[:8]`, which does not invert. A sidecar
manifest is therefore the only way to map a snapshot back to a document,
and this module is that manifest.

Two constraints shape it:

  * It is written from `_auto_save`, which is best-effort and must never
    break an execution. A manifest failure has to stay silent.
  * It is not the source of truth for *existence*. Retention prunes the
    directory (`max_backups`, `max_retention_age_days`) without consulting
    it, and every snapshot written before this release has no entry at
    all. The files on disk are the truth; the manifest only adds names.
"""

import json
import os
from types import SimpleNamespace

import pytest

from freecad_ai.core import backups


def _snap(tmp_path, name, content="x"):
    """Write a snapshot file the way _auto_save would name it."""
    path = os.path.join(str(tmp_path), name)
    with open(path, "w") as f:
        f.write(content)
    return path


class TestTheManifestRemembersWhereASnapshotCameFrom:
    def test_a_recorded_snapshot_lists_its_original_path(self, tmp_path):
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, "/home/alf/cad/part.FCStd",
                                label="part")

        [snap] = backups.list_snapshots(str(tmp_path))

        assert snap.original_path == "/home/alf/cad/part.FCStd"
        assert snap.label == "part"
        assert snap.path == path

    def test_re_recording_updates_in_place(self, tmp_path):
        """One stable snapshot file per document means one entry, always."""
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        for _ in range(3):
            backups.record_snapshot(str(tmp_path), path,
                                    "/home/alf/cad/part.FCStd", label="part")

        assert len(backups.list_snapshots(str(tmp_path))) == 1

    def test_two_documents_keep_separate_entries(self, tmp_path):
        a = _snap(tmp_path, "part.aaaaaaaa.ai-backup.FCStd")
        b = _snap(tmp_path, "part.bbbbbbbb.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), a, "/projects/a/part.FCStd")
        backups.record_snapshot(str(tmp_path), b, "/projects/b/part.FCStd")

        originals = {s.original_path for s in backups.list_snapshots(str(tmp_path))}

        assert originals == {"/projects/a/part.FCStd", "/projects/b/part.FCStd"}

    def test_the_manifest_lives_beside_the_snapshots(self, tmp_path):
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, "/home/alf/cad/part.FCStd")

        assert os.path.isfile(os.path.join(str(tmp_path), backups.MANIFEST_NAME))


class TestTheFilesOnDiskAreTheTruth:
    """Retention prunes snapshots without telling the manifest, and every
    snapshot written before this release has no entry at all."""

    def test_a_pruned_snapshot_is_not_offered(self, tmp_path):
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, "/home/alf/cad/part.FCStd")
        os.unlink(path)  # what prune_oldest_files does

        assert backups.list_snapshots(str(tmp_path)) == []

    def test_a_pruned_entry_is_dropped_from_the_manifest(self, tmp_path):
        """Otherwise the manifest grows forever on a machine that prunes."""
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, "/home/alf/cad/part.FCStd")
        os.unlink(path)

        backups.list_snapshots(str(tmp_path))

        with open(os.path.join(str(tmp_path), backups.MANIFEST_NAME)) as f:
            assert json.load(f) == {}

    def test_an_unrecorded_snapshot_is_still_offered(self, tmp_path):
        """Snapshots from before the manifest existed are the whole reason
        users have anything to restore today — they must not be invisible."""
        _snap(tmp_path, "enclosure.4e8d53db.ai-backup.FCStd")

        [snap] = backups.list_snapshots(str(tmp_path))

        assert snap.original_path == "", "the path is genuinely unknown"
        assert snap.label == "enclosure", "recovered from the filename stem"

    def test_foreign_files_are_ignored(self, tmp_path):
        _snap(tmp_path, "notes.txt")
        _snap(tmp_path, "part.FCStd")
        _snap(tmp_path, backups.MANIFEST_NAME, "{}")

        assert backups.list_snapshots(str(tmp_path)) == []

    def test_newest_first(self, tmp_path):
        old = _snap(tmp_path, "old.aaaaaaaa.ai-backup.FCStd")
        new = _snap(tmp_path, "new.bbbbbbbb.ai-backup.FCStd")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))

        assert [s.path for s in backups.list_snapshots(str(tmp_path))] == [new, old]

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert backups.list_snapshots(os.path.join(str(tmp_path), "nope")) == []

    def test_a_corrupt_manifest_still_lists_the_files(self, tmp_path):
        """A half-written manifest must cost names, never the snapshots."""
        _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        _snap(tmp_path, backups.MANIFEST_NAME, "{not json")

        [snap] = backups.list_snapshots(str(tmp_path))

        assert snap.label == "part"


class TestRecordingNeverBreaksAnExecution:
    """`_auto_save` swallows everything for a reason: a failed backup must
    not stop the user's code from running. The manifest inherits that."""

    def test_an_unwritable_directory_is_swallowed(self, tmp_path):
        target = os.path.join(str(tmp_path), "not-a-dir")
        with open(target, "w") as f:
            f.write("")

        backups.record_snapshot(target, "/x/part.ai-backup.FCStd", "/x/part.FCStd")

    def test_a_corrupt_manifest_is_replaced_not_raised(self, tmp_path):
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        _snap(tmp_path, backups.MANIFEST_NAME, "{not json")

        backups.record_snapshot(str(tmp_path), path, "/home/alf/cad/part.FCStd")

        [snap] = backups.list_snapshots(str(tmp_path))
        assert snap.original_path == "/home/alf/cad/part.FCStd"


class TestWhatTheUserIsShown:
    def test_the_original_is_flagged_when_it_is_gone(self, tmp_path):
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, "/deleted/part.FCStd")

        [snap] = backups.list_snapshots(str(tmp_path))

        assert snap.original_exists is False

    def test_the_original_is_found_when_it_is_there(self, tmp_path):
        original = _snap(tmp_path, "part.FCStd")
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, original)

        [snap] = backups.list_snapshots(str(tmp_path))

        assert snap.original_exists is True

    def test_an_unknown_original_does_not_claim_to_exist(self, tmp_path):
        _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")

        [snap] = backups.list_snapshots(str(tmp_path))

        assert snap.original_exists is False

    def test_the_restored_document_gets_a_distinguishable_name(self, tmp_path):
        """It opens alongside the original, so 'part' twice in the tree is
        exactly the confusion to avoid."""
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, "/home/alf/cad/part.FCStd",
                                label="part")

        [snap] = backups.list_snapshots(str(tmp_path))

        assert backups.recovered_label(snap) == "part (recovered)"

    def test_an_orphan_is_named_from_its_file(self, tmp_path):
        _snap(tmp_path, "enclosure.4e8d53db.ai-backup.FCStd")

        [snap] = backups.list_snapshots(str(tmp_path))

        assert backups.recovered_label(snap) == "enclosure (recovered)"


class TestWhereARestoredCopyGoes:
    """The user chose restore-as-a-copy: the original is never written to, so
    the copy needs somewhere sensible of its own to land."""

    def _snapshot(self, tmp_path, original_path, label="part"):
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd")
        backups.record_snapshot(str(tmp_path), path, original_path, label=label)
        return backups.list_snapshots(str(tmp_path))[0]

    def test_it_lands_beside_the_document_it_came_from(self, tmp_path):
        original = os.path.join(str(tmp_path), "part.FCStd")
        snap = self._snapshot(tmp_path, original)

        assert backups.default_copy_path(snap) == os.path.join(
            str(tmp_path), "part (recovered).FCStd")

    def test_it_never_proposes_an_existing_file(self, tmp_path):
        """Restoring twice to compare two attempts must not silently clobber
        the first — the whole point of copy-mode is that nothing is lost."""
        original = os.path.join(str(tmp_path), "part.FCStd")
        _snap(tmp_path, "part (recovered).FCStd")
        snap = self._snapshot(tmp_path, original)

        assert backups.default_copy_path(snap) == os.path.join(
            str(tmp_path), "part (recovered) 2.FCStd")

    def test_an_unknown_original_falls_back_to_home(self, tmp_path):
        snap = self._snapshot(tmp_path, "", label="enclosure")

        assert backups.default_copy_path(snap, home=str(tmp_path)) == \
            os.path.join(str(tmp_path), "enclosure (recovered).FCStd")

    def test_a_vanished_folder_falls_back_to_home(self, tmp_path):
        """The document's folder can be an unplugged drive or a deleted
        project — proposing a path there would fail at write time."""
        home = os.path.join(str(tmp_path), "home")
        os.makedirs(home)
        snap = self._snapshot(tmp_path, "/mnt/gone/part.FCStd")

        assert backups.default_copy_path(snap, home=home) == \
            os.path.join(home, "part (recovered).FCStd")


class TestRestoringNeverTouchesTheOriginal:
    """The chosen design: a restore opens a *copy*. The document the snapshot
    was taken from is never written to, so a restore can never be the thing
    that loses work — which would make the recovery feature its own hazard."""

    def _snapshot(self, tmp_path, original_path, body="snapshot contents"):
        path = _snap(tmp_path, "part.4e8d53db.ai-backup.FCStd", body)
        backups.record_snapshot(str(tmp_path), path, original_path, label="part")
        return backups.list_snapshots(str(tmp_path))[0]

    def test_the_copy_gets_the_snapshots_contents(self, tmp_path):
        original = _snap(tmp_path, "part.FCStd", "the live document")
        snap = self._snapshot(tmp_path, original)
        dest = os.path.join(str(tmp_path), "part (recovered).FCStd")

        backups.restore_copy(snap, dest, open_document=lambda p: None)

        with open(dest) as f:
            assert f.read() == "snapshot contents"

    def test_the_original_is_left_untouched(self, tmp_path):
        original = _snap(tmp_path, "part.FCStd", "the live document")
        snap = self._snapshot(tmp_path, original)
        dest = os.path.join(str(tmp_path), "part (recovered).FCStd")

        backups.restore_copy(snap, dest, open_document=lambda p: None)

        with open(original) as f:
            assert f.read() == "the live document"

    def test_restoring_over_the_original_is_refused(self, tmp_path):
        """A save dialog will happily let the user pick their own document.
        Everything else is their call; this one is not."""
        original = _snap(tmp_path, "part.FCStd", "the live document")
        snap = self._snapshot(tmp_path, original)

        with pytest.raises(ValueError):
            backups.restore_copy(snap, original, open_document=lambda p: None)

        with open(original) as f:
            assert f.read() == "the live document"

    def test_the_refusal_sees_through_a_roundabout_path(self, tmp_path):
        original = _snap(tmp_path, "part.FCStd", "the live document")
        snap = self._snapshot(tmp_path, original)
        sneaky = os.path.join(str(tmp_path), "sub", "..", "part.FCStd")
        os.makedirs(os.path.join(str(tmp_path), "sub"))

        with pytest.raises(ValueError):
            backups.restore_copy(snap, sneaky, open_document=lambda p: None)

    def test_the_snapshot_survives_its_own_restore(self, tmp_path):
        """It stays the backup: restoring must not consume it."""
        snap = self._snapshot(tmp_path, os.path.join(str(tmp_path), "part.FCStd"))
        dest = os.path.join(str(tmp_path), "part (recovered).FCStd")

        backups.restore_copy(snap, dest, open_document=lambda p: None)

        assert os.path.isfile(snap.path)
        assert backups.list_snapshots(str(tmp_path))[0].path == snap.path

    def test_the_copy_is_opened_not_the_snapshot(self, tmp_path):
        """Opening the snapshot in place would put the user one Ctrl+S away
        from overwriting their own backup — and the next execution would
        overwrite the result."""
        snap = self._snapshot(tmp_path, os.path.join(str(tmp_path), "part.FCStd"))
        dest = os.path.join(str(tmp_path), "part (recovered).FCStd")
        opened = []

        backups.restore_copy(snap, dest, open_document=opened.append)

        assert opened == [dest]

    def test_the_restored_document_is_renamed(self, tmp_path):
        """saveAs stamped the snapshot's filename into the document, so the
        copy opens as ``part.4e8d53db.ai-backup`` unless we say otherwise."""
        snap = self._snapshot(tmp_path, os.path.join(str(tmp_path), "part.FCStd"))
        dest = os.path.join(str(tmp_path), "part (recovered).FCStd")
        doc = SimpleNamespace(Label="part.4e8d53db.ai-backup")

        assert backups.restore_copy(snap, dest, open_document=lambda p: doc) is doc
        assert doc.Label == "part (recovered)"
