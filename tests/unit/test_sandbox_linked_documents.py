"""The sandbox dry-run copy must keep linked documents resolvable.

Link paths inside an .FCStd are relative to the document, so copying only the
active file to a temp path made ``App.getDocument("<linked>")`` fail in the
sandbox while the live session was fine.
"""
import os

from freecad_ai.core.executor import _make_sandbox_copy


class _Doc:
    def __init__(self, filename, deps=()):
        self.FileName = filename
        self._deps = list(deps)

    def getDependentDocuments(self, sort=True):
        return [self] + self._deps


def _touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return str(path)


def test_no_saved_file_returns_none(tmp_path):
    assert _make_sandbox_copy(_Doc("")) == (None, None)
    assert _make_sandbox_copy(None) == (None, None)
    assert _make_sandbox_copy(_Doc(str(tmp_path / "missing.FCStd"))) == (None, None)


def test_copies_main_file_alone(tmp_path):
    main = _touch(tmp_path / "proj" / "main.FCStd", "main")
    path, tmp_dir = _make_sandbox_copy(_Doc(main))
    try:
        assert os.path.basename(path) == "main.FCStd"
        assert open(path).read() == "main"
        assert path != main
    finally:
        import shutil
        shutil.rmtree(tmp_dir)


def test_linked_sibling_keeps_relative_layout(tmp_path):
    main = _touch(tmp_path / "proj" / "main.FCStd", "main")
    link = _touch(tmp_path / "proj" / "ALKIS.FCStd", "alkis")
    path, tmp_dir = _make_sandbox_copy(_Doc(main, [_Doc(link)]))
    try:
        assert open(os.path.join(os.path.dirname(path), "ALKIS.FCStd")).read() == "alkis"
    finally:
        import shutil
        shutil.rmtree(tmp_dir)


def test_linked_file_outside_main_dir_keeps_dotdot_relation(tmp_path):
    main = _touch(tmp_path / "proj" / "sub" / "main.FCStd", "main")
    link = _touch(tmp_path / "proj" / "data" / "ALKIS.FCStd", "alkis")
    path, tmp_dir = _make_sandbox_copy(_Doc(main, [_Doc(link)]))
    try:
        rel = os.path.relpath(os.path.join(os.path.dirname(path), "..", "data", "ALKIS.FCStd"))
        assert open(rel).read() == "alkis"
        # the copy stays inside the temp dir
        assert os.path.commonpath([tmp_dir, path]) == tmp_dir
    finally:
        import shutil
        shutil.rmtree(tmp_dir)


def test_unsaved_linked_doc_is_skipped(tmp_path):
    main = _touch(tmp_path / "main.FCStd")
    path, tmp_dir = _make_sandbox_copy(_Doc(main, [_Doc("")]))
    try:
        assert os.listdir(os.path.dirname(path)) == ["main.FCStd"]
    finally:
        import shutil
        shutil.rmtree(tmp_dir)
