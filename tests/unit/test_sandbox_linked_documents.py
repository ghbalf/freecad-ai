"""The sandbox dry-run copy must keep linked documents resolvable.

Link paths inside an .FCStd are relative to the document, so copying only the
active file to a temp path made ``App.getDocument("<linked>")`` fail in the
sandbox while the live session was fine.
"""
import os

from freecad_ai.core.executor import _make_sandbox_copy


class _Doc:
    """Fake App.Document. saveCopy writes ``live`` (the unsaved state) when set."""

    def __init__(self, filename, deps=(), live=None, partial=False):
        self.FileName = filename
        self._deps = list(deps)
        self._live = live
        self.Partial = partial
        self.saved_copies = []

    def getDependentDocuments(self, sort=True):
        return [self] + self._deps

    def saveCopy(self, path):
        self.saved_copies.append(path)
        with open(path, "w") as f:
            f.write(self._live)


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


def _cleanup(tmp_dir):
    import shutil
    shutil.rmtree(tmp_dir)


def test_copy_holds_the_live_state_not_the_saved_file(tmp_path):
    """Objects created since the last save must exist in the dry-run copy."""
    main = _touch(tmp_path / "main.FCStd", "stale file on disk")
    path, tmp_dir = _make_sandbox_copy(_Doc(main, live="live in memory"))
    try:
        assert open(path).read() == "live in memory"
        assert open(main).read() == "stale file on disk"
    finally:
        _cleanup(tmp_dir)


def test_linked_document_is_snapshotted_live_too(tmp_path):
    main = _touch(tmp_path / "main.FCStd", "old")
    link = _touch(tmp_path / "ALKIS.FCStd", "old")
    path, tmp_dir = _make_sandbox_copy(
        _Doc(main, [_Doc(link, live="alkis live")], live="main live"))
    try:
        assert open(os.path.join(os.path.dirname(path), "ALKIS.FCStd")).read() == "alkis live"
    finally:
        _cleanup(tmp_dir)


def test_partial_document_falls_back_to_the_file(tmp_path):
    """A partial document would lose its unloaded objects through saveCopy."""
    main = _touch(tmp_path / "main.FCStd", "main live")
    link = _touch(tmp_path / "ALKIS.FCStd", "full file")
    partial = _Doc(link, live="partial state", partial=True)
    path, tmp_dir = _make_sandbox_copy(_Doc(main, [partial], live="main live"))
    try:
        assert partial.saved_copies == []
        assert open(os.path.join(os.path.dirname(path), "ALKIS.FCStd")).read() == "full file"
    finally:
        _cleanup(tmp_dir)


def test_failing_savecopy_falls_back_to_the_file(tmp_path):
    class _Broken(_Doc):
        def saveCopy(self, path):
            raise RuntimeError("cannot save")

    main = _touch(tmp_path / "main.FCStd", "from file")
    path, tmp_dir = _make_sandbox_copy(_Broken(main))
    try:
        assert open(path).read() == "from file"
    finally:
        _cleanup(tmp_dir)


def test_multiline_string_survives_the_child_script():
    """Continuation lines must not be indented (reported: IndentationError)."""
    from freecad_ai.core.executor import _build_child_script
    code = 's = """a\nb\nc"""\nprint(s)\n'
    script = _build_child_script(code, document_path=None,
                                 result_path="/tmp/r.json", mode="sandbox")
    assert repr(code) in script
    compile(script, "<child>", "exec")  # the harness itself must stay valid
