"""Read the pre-execution recovery snapshots back (#49).

#48 gave ``_auto_save`` a managed directory to write into. Writing was the
easy half: a snapshot cannot say where it came from, because FreeCAD stores
``Document.FileName`` as a *transient* property — an opened snapshot reports
its own path in ``BACKUPS_DIR`` and nothing else — and the tag in the
filename is ``sha1(original)[:8]``, which does not invert. So the mapping
back to the user's document has to be written down separately, and this
module is that sidecar.

Two properties matter more than the manifest itself:

* **Recording can fail silently.** It runs inside ``_auto_save``, which
  exists to protect the user's work, not to add a new way for an execution
  to die. Every write here is best-effort.
* **The directory, not the manifest, says what exists.** Retention
  (``max_backups`` / ``max_retention_age_days``) deletes snapshots without
  consulting us, and every snapshot written before this module existed has
  no entry at all. So listing walks the files and uses the manifest only to
  put names to them; an unrecorded snapshot is still offered, named from
  its own filename.
"""

import json
import os
import re
from dataclasses import dataclass

#: Shared with ``_auto_save``'s writer and the retention pruner — the three
#: must agree on what counts as a snapshot or they will disagree about which
#: files exist.
SNAPSHOT_SUFFIX = ".ai-backup.FCStd"

MANIFEST_NAME = "index.json"

_TAG_RE = re.compile(r"\.[0-9a-f]{8}$")


@dataclass(frozen=True)
class Snapshot:
    """One recovery snapshot, as offered to the user."""

    path: str
    #: The document this was taken from, or "" when it predates the manifest.
    original_path: str
    #: Display name: the document's Label when known, else the filename stem.
    label: str
    #: mtime of the snapshot file. The file is the truth, so this is too.
    saved_at: float

    @property
    def original_exists(self) -> bool:
        """Whether the document this came from is still on disk.

        False for an unknown original as well as a deleted one: both mean
        "we cannot show you what this would be replacing".
        """
        return bool(self.original_path) and os.path.isfile(self.original_path)


def is_snapshot(name: str) -> bool:
    return name.endswith(SNAPSHOT_SUFFIX)


def recovered_label(snapshot: Snapshot) -> str:
    """The name a restored copy opens under.

    It opens alongside the original, so it must not answer to the same name
    in the tree — that is exactly the mix-up a recovery is meant to end.
    """
    return f"{snapshot.label} (recovered)"


def _manifest_path(backups_dir: str) -> str:
    return os.path.join(backups_dir, MANIFEST_NAME)


def _load_manifest(backups_dir: str):
    """Return ``(entries, readable)``.

    *readable* is False for a missing or corrupt manifest — the caller must
    not then "clean" it, because it has no idea what it would be discarding.
    """
    try:
        with open(_manifest_path(backups_dir), "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return {}, False
    if not isinstance(entries, dict):
        return {}, False
    return entries, True


def _write_manifest(backups_dir: str, entries: dict) -> None:
    with open(_manifest_path(backups_dir), "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _label_from_filename(path: str) -> str:
    """Recover a document name from ``<stem>.<tag>.ai-backup.FCStd``."""
    stem = os.path.basename(path)[: -len(SNAPSHOT_SUFFIX)]
    without_tag = _TAG_RE.sub("", stem)
    return without_tag or stem


def record_snapshot(
    backups_dir: str,
    snapshot_path: str,
    original_path: str,
    label: str = "",
) -> None:
    """Note where *snapshot_path* came from. Never raises.

    Keyed by filename, not full path, so moving the backups directory (or
    reading it from another machine's synced copy) keeps the names attached.
    """
    try:
        entries, _ = _load_manifest(backups_dir)
        entries[os.path.basename(snapshot_path)] = {
            "original_path": original_path,
            "label": label or _label_from_filename(snapshot_path),
        }
        _write_manifest(backups_dir, entries)
    except Exception:
        pass  # Best-effort: a lost name must not cost the user an execution.


def list_snapshots(backups_dir: str):
    """Every snapshot on disk, newest first, named where we can name it."""
    try:
        names = [n for n in os.listdir(backups_dir) if is_snapshot(n)]
    except OSError:
        return []

    entries, readable = _load_manifest(backups_dir)
    if readable and set(entries) - set(names):
        # Retention deleted files behind our back. Drop their entries now, or
        # the manifest is the one thing here that grows without bound.
        try:
            _write_manifest(backups_dir, {k: entries[k] for k in names if k in entries})
        except OSError:
            pass

    snapshots = []
    for name in names:
        path = os.path.join(backups_dir, name)
        entry = entries.get(name) or {}
        try:
            saved_at = os.path.getmtime(path)
        except OSError:
            continue  # Pruned between listdir and here.
        snapshots.append(Snapshot(
            path=path,
            original_path=entry.get("original_path", ""),
            label=entry.get("label") or _label_from_filename(path),
            saved_at=saved_at,
        ))
    snapshots.sort(key=lambda s: (-s.saved_at, s.path))
    return snapshots


def default_copy_path(snapshot: Snapshot, home: str = "") -> str:
    """Where to propose putting a restored copy of *snapshot*.

    Restoring never writes to the document the snapshot came from, so the
    copy needs a path of its own: beside the original when that folder is
    still reachable, in the user's home when it is not (a deleted project,
    an unplugged drive, or a snapshot old enough to predate the manifest).
    The proposal always names a file that does not exist yet.
    """
    folder = os.path.dirname(snapshot.original_path)
    if not folder or not os.path.isdir(folder):
        folder = home or os.path.expanduser("~")

    base = recovered_label(snapshot)
    candidate = os.path.join(folder, f"{base}.FCStd")
    n = 1
    while os.path.exists(candidate):
        n += 1
        candidate = os.path.join(folder, f"{base} {n}.FCStd")
    return candidate


def _open_in_freecad(path: str):
    import FreeCAD
    return FreeCAD.openDocument(path)


def restore_copy(snapshot: Snapshot, destination: str, open_document=None):
    """Copy *snapshot* to *destination* and open it. Returns the document.

    A restore is the one operation here that writes outside the managed
    backups directory, so it is deliberately the narrowest thing that can
    work: copy, open the copy, rename it. Two things it must never do:

    * **Write to the document the snapshot came from.** A file dialog will
      happily let the user pick their own document; overwriting it would
      make the recovery feature the thing that loses their work. Every
      other destination is theirs to choose.
    * **Open the snapshot itself.** That would leave the user one Ctrl+S
      from overwriting their own backup, and the next execution's
      ``_auto_save`` would overwrite whatever they saved.
    """
    original = snapshot.original_path
    if original and os.path.abspath(destination) == os.path.abspath(original):
        raise ValueError(
            "a snapshot is restored as a copy; it cannot overwrite "
            f"{original}")

    import shutil
    shutil.copy2(snapshot.path, destination)

    doc = (open_document or _open_in_freecad)(destination)
    if doc is not None:
        # saveAs stamped the snapshot's own filename into the saved document,
        # so without this the tree reads "part.4e8d53db.ai-backup".
        doc.Label = recovered_label(snapshot)
    return doc
