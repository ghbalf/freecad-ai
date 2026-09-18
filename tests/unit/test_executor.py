"""Tests for code execution engine — extract, validate, and safety checks."""

import json
import os
import sys

import pytest

from unittest.mock import MagicMock, patch

from freecad_ai.core import backups, executor
from freecad_ai.core.executor import (
    ExecutionResult,
    extract_code_blocks,
    validate_code,
    _validate_code,
)


class TestExtractCodeBlocks:
    def test_single_block(self):
        text = "Here's code:\n```python\nprint('hello')\n```\nDone."
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert "print('hello')" in blocks[0]

    def test_multiple_blocks(self):
        text = (
            "First:\n```python\na = 1\n```\n"
            "Second:\n```python\nb = 2\n```\n"
        )
        blocks = extract_code_blocks(text)
        assert len(blocks) == 2

    def test_no_blocks(self):
        text = "No code here, just text."
        blocks = extract_code_blocks(text)
        assert blocks == []

    def test_non_python_block_ignored(self):
        text = "```javascript\nconsole.log('hi')\n```"
        blocks = extract_code_blocks(text)
        assert blocks == []

    def test_multiline_code(self):
        text = "```python\ndef foo():\n    return 42\n\nresult = foo()\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert "def foo():" in blocks[0]
        assert "result = foo()" in blocks[0]

    def test_empty_block(self):
        text = "```python\n```"
        blocks = extract_code_blocks(text)
        # Empty match
        assert len(blocks) == 1
        assert blocks[0].strip() == ""

    def test_nested_backticks_in_string(self):
        text = '```python\nx = "```"\n```'
        blocks = extract_code_blocks(text)
        # Regex matches greedily but should get at least one block
        assert len(blocks) >= 1


class TestValidateCode:
    # ── Dangerous patterns ──

    def test_blocks_os_system(self):
        warnings = _validate_code("os.system('rm -rf /')")
        assert any("os.system" in w for w in warnings)

    def test_blocks_subprocess(self):
        warnings = _validate_code("import subprocess\nsubprocess.run(['ls'])")
        assert any("subprocess" in w for w in warnings)

    def test_blocks_shutil_rmtree(self):
        warnings = _validate_code("shutil.rmtree('/home')")
        assert any("shutil.rmtree" in w for w in warnings)

    def test_blocks_dynamic_os_import(self):
        warnings = _validate_code("__import__('os').system('ls')")
        assert any("Dynamic import" in w for w in warnings)

    def test_safe_code_passes(self):
        warnings = _validate_code(
            "import FreeCAD as App\n"
            "doc = App.newDocument('Test')\n"
            "box = doc.addObject('Part::Box', 'Box')\n"
        )
        assert warnings == []

    # ── Revolution crash patterns ──

    def test_blocks_revolution_with_full_circle(self):
        code = (
            "import Part\n"
            "circle = Part.Circle()\n"
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
        )
        warnings = _validate_code(code)
        assert any("Revolution" in w or "crash" in w.lower() for w in warnings)

    def test_allows_revolution_with_arc(self):
        code = (
            "arc = Part.ArcOfCircle(circ, 0, 3.14)\n"
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
        )
        warnings = _validate_code(code)
        # ArcOfCircle should NOT trigger the revolution warning
        assert not any("crash" in w.lower() for w in warnings)

    def test_blocks_360_degree_revolution(self):
        code = (
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
            "feat.Angle = 360\n"
        )
        warnings = _validate_code(code)
        assert any("360" in w for w in warnings)

    def test_allows_partial_revolution(self):
        code = (
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
            "feat.Angle = 180\n"
        )
        warnings = _validate_code(code)
        assert not any("360" in w for w in warnings)

    # ── False positive checks ──

    def test_subprocess_in_comment_still_blocked(self):
        # The validator does simple regex matching, not AST — it blocks
        # "subprocess" anywhere in code text. This is intentional.
        code = "# We could use subprocess but we don't\nsubprocess.call(['ls'])"
        warnings = _validate_code(code)
        assert any("subprocess" in w for w in warnings)

    def test_os_in_variable_name_ok(self):
        # "os_path" should NOT trigger os.system warning
        warnings = _validate_code("os_path = '/tmp/test'")
        assert warnings == []

    def test_safe_revolution_mention_in_string(self):
        # "Revolution" in a string without Part.Circle should be fine
        code = "name = 'Revolution'\nprint(name)"
        warnings = _validate_code(code)
        assert warnings == []


class TestValidateCodePublic:
    """validate_code() is the Check-button entry point — returns ExecutionResult."""

    def test_static_failure_returns_error_result(self):
        dangerous = "os" + ".system('rm -rf /')"
        result = validate_code(dangerous)
        assert isinstance(result, ExecutionResult)
        assert result.success is False
        assert "os.system" in result.stderr
        assert result.code == dangerous

    def test_static_failure_mentions_static_validation(self):
        # The stderr prefix distinguishes static from sandbox failures so the
        # UI (and the LLM, when Fix fires) knows which layer complained.
        result = validate_code("subprocess.run(['x'])")
        assert "Static validation" in result.stderr

    def test_passes_when_sandbox_unavailable(self):
        # If no FreeCAD binary is on the system, _sandbox_test returns
        # (True, "") — validate_code should surface that as a pass.
        with patch("freecad_ai.core.executor._find_freecad_cmd", return_value=""):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                result = validate_code("import FreeCAD as App\ndoc = App.newDocument()")
        assert result.success is True
        assert result.stderr == ""

    def test_sandbox_failure_propagates_error(self):
        # Simulate a sandbox-detected error; validate_code should wrap it.
        with patch("freecad_ai.core.executor._sandbox_test", return_value=(False, "boom")):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                result = validate_code("x = 1")
        assert result.success is False
        assert "boom" in result.stderr

    def test_returns_execution_result_shape(self):
        # The Fix button feeds last_error_result into _handle_execution_error,
        # which reads .stderr and .success — this contract must not drift.
        with patch("freecad_ai.core.executor._sandbox_test", return_value=(False, "err")):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                result = validate_code("x = 1")
        assert hasattr(result, "success")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert hasattr(result, "code")


class TestSkipSafety:
    """skip_safety bypasses static validation, the sandbox, and the timeout, while keeping the undo transaction."""

    def test_safe_mode_blocks_dangerous_code(self):
        code = "import subprocess\nsubprocess.run(['ls'])"
        res = executor.execute_code(code, sandbox=False, skip_safety=False)
        assert res.success is False
        assert "validation failed" in res.stderr.lower()

    def test_skip_safety_bypasses_static_validation(self):
        # With skip_safety=True the static deny-list is skipped, so execution does
        # NOT short-circuit at static validation. With no active document it falls
        # through to the active-document guard — proving validation did not block.
        code = "import subprocess\nsubprocess.run(['ls'])"
        with patch(
            "freecad_ai.core.active_document.get_synced_active_document",
            return_value=None,
        ):
            res = executor.execute_code(code, sandbox=False, skip_safety=True)
        assert res.success is False
        assert "no active document" in res.stderr.lower()

    def test_validate_code_skip_safety_returns_pass(self):
        code = "import subprocess\nsubprocess.run(['ls'])"
        res = executor.validate_code(code, skip_safety=True)
        assert res.success is True


class TestSandboxTimeout:
    """The headless sandbox dry-run must get the same time budget as the
    real execution. Issue #14: execute_code() previously capped the sandbox
    at min(timeout, 15)s, so a valid-but-slow operation (e.g. scaling a
    complex shape with Shape.transformGeometry) failed the pre-check with
    "Sandbox: code timed out after 15 seconds" and never ran — even though
    the live execution would have allowed the full timeout.
    """

    @pytest.mark.parametrize("configured", [20, 30, 45])
    def test_sandbox_receives_full_configured_timeout(self, configured):
        seen = {}

        def _capture(code, timeout=15, document_path=None):
            seen["timeout"] = timeout
            return True, ""

        with patch("freecad_ai.core.executor._sandbox_test", side_effect=_capture):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                executor.execute_code("x = 1", timeout=configured)

        assert seen["timeout"] == configured, (
            "sandbox dry-run was throttled below the configured execution "
            "timeout — slow-but-valid code will falsely time out"
        )


class TestSandboxHarnessForcesExit:
    """Issue #14: the sandbox harness wrote its result file but never forced the
    interpreter to exit. On FreeCAD builds where running a script via `-c`
    against an OPENED document leaves the process in interactive mode (the
    Qt/console event loop never returns), the subprocess never terminated, so
    `subprocess.run()` blocked until its timeout and the sandbox reported a
    spurious "code timed out" — even for trivial code.

    The hang itself is build-/timing-dependent and not reliably reproducible in
    CI, so this guards the invariant instead: the generated harness must force a
    process exit after writing its result. Diagnosed and first patched by
    @galberding on the issue thread.
    """

    def test_generated_harness_forces_process_exit(self):
        captured = {}

        class _FakeProc:
            returncode = 0

        def _fake_run(cmd, **kwargs):
            # cmd == [freecad_bin, "-c", script_file]; capture the harness the
            # sandbox wrote before it would have run FreeCAD.
            with open(cmd[2]) as fh:
                captured["harness"] = fh.read()
            return _FakeProc()

        with patch(
            "freecad_ai.core.executor._find_freecad_cmd",
            return_value="/usr/bin/freecadcmd",
        ):
            with patch(
                "freecad_ai.core.executor.subprocess.run", side_effect=_fake_run
            ):
                executor._sandbox_test("x = 1", timeout=5)

        harness = captured.get("harness", "")
        assert harness, "sandbox did not generate a harness script"
        forces_exit = any(
            tok in harness for tok in ("os._exit(", "sys.exit(")
        )
        assert forces_exit, (
            "sandbox harness must force the interpreter to exit after writing "
            "its result, or the FreeCAD subprocess can hang until timeout "
            "(issue #14)"
        )


class TestConfigurableExecutionTimeout:
    """Issue #14 (reopened): the execution timeout was hardcoded at 30s with no
    user override, so heavy-but-valid operations — scaling a detailed model via
    Shape.transformGeometry, whose cost is O(geometry complexity) — exceeded 30s
    and failed on BOTH the sandbox dry-run and the live SIGALRM path. The timeout
    is now sourced from AppConfig.execution_timeout (default 60) whenever the
    caller passes no explicit timeout, so users can raise it for big models.
    """

    def _captured_timeout(self, configured):
        from freecad_ai.config import AppConfig

        seen = {}

        def _capture(code, timeout=15, document_path=None):
            seen["timeout"] = timeout
            return True, ""

        cfg = AppConfig()
        if configured is not None:
            cfg.execution_timeout = configured

        with patch("freecad_ai.config.get_config", return_value=cfg):
            with patch(
                "freecad_ai.core.executor._sandbox_test", side_effect=_capture
            ):
                with patch(
                    "freecad_ai.core.active_document.get_synced_active_document",
                    return_value=None,
                ):
                    executor.execute_code("x = 1")  # no explicit timeout
        return seen["timeout"]

    def test_default_execution_timeout_is_30(self):
        assert self._captured_timeout(None) == 30, (
            "execute_code() with no explicit timeout must use the 30s default"
        )

    def test_configured_execution_timeout_is_honored(self):
        assert self._captured_timeout(120) == 120, (
            "execute_code() must source its timeout from "
            "AppConfig.execution_timeout when the caller passes none"
        )


class TestCollectObjectIssues:
    """Post-execution validation must blame the code only for shapes it
    created or newly broke — never for objects that were already invalid
    before the code ran.

    Issue: an STL imported and converted to a solid yields an OCC-invalid
    Part::Feature. The sandbox opens a copy of the saved document, so that
    pre-existing invalid solid is present on every dry-run. The validator
    used to walk *all* objects and report it, failing code (e.g. a sketch on
    a selected face) that never touched the solid — sending the model to
    chase a phantom bug across all retries.
    """

    def test_preexisting_invalid_shape_is_suppressed(self):
        # The imported mesh→solid was already invalid before the code ran.
        objects_state = [
            {"name": "roundedBox_solid", "null": False,
             "invalid": True, "invalid_state": False},
        ]
        baseline_bad = {"roundedBox_solid"}
        issues = executor._collect_object_issues(objects_state, baseline_bad)
        assert issues == [], (
            "code that never touched a pre-existing invalid object must not "
            "be blamed for it"
        )

    def test_newly_created_invalid_object_is_reported(self):
        # A brand-new object the code created has a broken shape — its fault.
        objects_state = [
            {"name": "roundedBox_solid", "null": False,
             "invalid": True, "invalid_state": False},
            {"name": "SnapFitBox", "null": False,
             "invalid": True, "invalid_state": False},
        ]
        baseline_bad = {"roundedBox_solid"}
        issues = executor._collect_object_issues(objects_state, baseline_bad)
        assert issues == ["Object 'SnapFitBox' has invalid shape"]

    def test_object_newly_broken_by_code_is_reported(self):
        # Object existed and was fine before; the code broke it.
        objects_state = [
            {"name": "Pad", "null": False,
             "invalid": True, "invalid_state": False},
        ]
        baseline_bad = set()  # Pad was valid before the code ran
        issues = executor._collect_object_issues(objects_state, baseline_bad)
        assert issues == ["Object 'Pad' has invalid shape"]

    def test_null_shape_on_new_object_is_reported(self):
        objects_state = [
            {"name": "Pocket", "null": True,
             "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'Pocket' has null shape"]

    def test_invalid_state_on_new_object_is_reported(self):
        objects_state = [
            {"name": "Sketch", "null": False,
             "invalid": False, "invalid_state": True},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'Sketch' is in Invalid state"]

    def test_valid_object_never_reported(self):
        objects_state = [
            {"name": "Box", "null": False,
             "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == []

    def test_empty_sketch_null_shape_is_not_reported(self):
        # Issue #18 follow-up: "create a sketch on the selected face" makes an
        # empty sketch (geometry is added later in the editor). On FreeCAD 1.1
        # an empty Sketcher::SketchObject reports Shape.isNull() == True while
        # State stays "Up-to-date" — a valid, complete intermediate state. The
        # validator must not flag it; otherwise the model injects junk
        # placeholder geometry to defeat the false positive.
        objects_state = [
            {"name": "Sketch_Face1996", "type": "Sketcher::SketchObject",
             "null": True, "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == [], (
            "an empty but valid sketch (null shape, Up-to-date) must not be "
            "reported as broken"
        )

    def test_empty_body_null_shape_is_not_reported(self):
        # A PartDesign::Body before its first feature also has a null shape
        # while Up-to-date — same benign null as an empty sketch.
        objects_state = [
            {"name": "Body", "type": "PartDesign::Body",
             "null": True, "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == []

    def test_failed_sketch_attachment_still_reported(self):
        # Safety net: a sketch whose attachment did not resolve lands in an
        # Invalid state (null shape AND invalid_state). The null-shape
        # exemption for sketches must NOT swallow this — the separate
        # invalid_state report still catches the genuine failure.
        objects_state = [
            {"name": "Sketch", "type": "Sketcher::SketchObject",
             "null": True, "invalid": False, "invalid_state": True},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'Sketch' is in Invalid state"]

    def test_null_shape_on_non_exempt_new_object_still_reported(self):
        # A solid-producing feature (e.g. a Pad) that silently builds nothing
        # is a real defect and must still be reported — the exemption is
        # narrow, keyed on object type.
        objects_state = [
            {"name": "Pad", "type": "PartDesign::Pad",
             "null": True, "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'Pad' has null shape"]

    def test_arch_container_null_shape_is_not_reported(self):
        # PR #81: Arch/BIM containers are organizational groups that hold no
        # geometry of their own — Shape.isNull() stays True for their whole
        # lifetime, empty or fully populated, while State stays "Up-to-date".
        # Flagging that failed every Arch.makeSite/makeBuilding/makeFloor call.
        # Their TypeId (Part::FeaturePython / App::GeometryPython) is shared
        # with countless unrelated scripted objects, so the exemption is keyed
        # on Proxy.Type. Values below are as observed on FreeCAD 1.1.1:
        # makeSite -> "Site", makeBuilding AND makeFloor -> "BuildingPart".
        objects_state = [
            {"name": "Site", "type": "Part::FeaturePython",
             "proxy_type": "Site",
             "null": True, "invalid": False, "invalid_state": False},
            {"name": "BuildingPart", "type": "App::GeometryPython",
             "proxy_type": "BuildingPart",
             "null": True, "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == [], (
            "Arch containers (null shape, Up-to-date) must not be reported as "
            "broken; this blocked all Arch/BIM tooling"
        )

    def test_scripted_object_without_arch_proxy_type_still_reported(self):
        # Guards the exemption's narrowness AND the key name itself: _snap()
        # writes "proxy_type" and this predicate reads it, with no other
        # coupling between them. A typo on either side would silently exempt
        # nothing (or everything) — here the same TypeId as an Arch container,
        # carrying the empty Proxy.Type of a plain non-scripted object, must
        # still be reported.
        objects_state = [
            {"name": "SomeFeature", "type": "Part::FeaturePython",
             "proxy_type": "",
             "null": True, "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'SomeFeature' has null shape"]

    def test_broken_arch_container_still_reported(self):
        # Safety net, mirroring the sketch case above: the null-shape
        # exemption must not swallow a container that genuinely failed to
        # recompute — the separate invalid_state report still catches it.
        objects_state = [
            {"name": "Site", "type": "Part::FeaturePython",
             "proxy_type": "Site",
             "null": True, "invalid": False, "invalid_state": True},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'Site' is in Invalid state"]


class _FakeDoc:
    """Minimal stand-in for the App::Document slice ``_auto_save`` touches.

    Mirrors the surprising part of FreeCAD's ``saveAs``: it writes the file
    *and* repoints ``FileName`` at the saved path, appending ``.FCStd`` when
    the target lacks that extension (``.ai-backup`` -> ``.ai-backup.FCStd``).

    It also renames the document: ``Label`` becomes the saved file's stem.
    Verified against FreeCAD 1.1.1 -- saving ``probe49c.FCStd`` as
    ``probe49c.deadbeef.ai-backup.FCStd`` left the *still-open* document
    labelled ``probe49c.deadbeef.ai-backup``. A fake that models only the
    ``FileName`` half cannot see that half of the damage.
    """

    def __init__(self, filename):
        self.FileName = filename
        self.Label = os.path.splitext(os.path.basename(filename))[0]
        self.saved_paths = []

    def saveAs(self, path):
        if not path.endswith(".FCStd"):
            path += ".FCStd"
        self.saved_paths.append(path)
        with open(path, "w") as f:
            f.write("<FCStd/>")  # a real file: listing walks the directory
        self.FileName = path
        self.Label = os.path.splitext(os.path.basename(path))[0]


class TestAutoSave:
    """Tests for ``_auto_save`` — issue #46 (managed backups dir) and the #45 /
    PR #44 regression (the snapshot must not compound ``.FCStd`` onto the
    document filename, and must overwrite one stable file rather than accrete)."""

    def _run(self, doc, backups_dir):
        with patch("freecad_ai.config.BACKUPS_DIR", backups_dir), patch(
            "freecad_ai.core.active_document.resolve_active_document",
            return_value=doc,
        ):
            executor._auto_save({})

    def test_preserves_document_filename(self, tmp_path):
        # After a backup the document must point at exactly the original path.
        # The old code rebuilt it with ``.replace(".ai-backup", "")``, which
        # left the ``.FCStd`` that saveAs appended, growing the name by one
        # extension every call (#45).
        doc = _FakeDoc("/tmp/part.FCStd")
        self._run(doc, str(tmp_path))
        assert doc.FileName == "/tmp/part.FCStd"

    def test_preserves_document_label(self, tmp_path):
        # saveAs renames the open document after the snapshot file, so without
        # a restore the user's document is silently relabelled
        # ``part.<hash>.ai-backup`` in the tree -- and the next ordinary save
        # writes that name into their file. FileName was restored from the
        # start (#45); Label is the same omission on the sibling property.
        doc = _FakeDoc("/tmp/part.FCStd")
        self._run(doc, str(tmp_path))
        assert doc.Label == "part", \
            "a recovery snapshot must not rename the user's document"

    def test_backup_written_to_managed_dir(self, tmp_path):
        # #46: the snapshot lands in the managed BACKUPS_DIR, not beside the
        # user's document, and keeps the ``.ai-backup.FCStd`` suffix.
        doc = _FakeDoc("/home/user/project/part.FCStd")
        self._run(doc, str(tmp_path))
        assert len(doc.saved_paths) == 1
        saved = doc.saved_paths[0]
        assert os.path.dirname(saved) == str(tmp_path)
        assert saved.endswith(".ai-backup.FCStd")
        # never written next to the source document
        assert not saved.startswith("/home/user/project/")

    def test_backup_path_is_stable_across_calls(self, tmp_path):
        # Two executions overwrite one stable snapshot, never accrete (#45).
        doc = _FakeDoc("/tmp/part.FCStd")
        self._run(doc, str(tmp_path))
        self._run(doc, str(tmp_path))
        assert len(set(doc.saved_paths)) == 1

    def test_collision_safe_for_same_basename(self, tmp_path):
        # Two documents sharing a basename in different folders must map to
        # distinct snapshot files (the hash tag prevents collisions).
        doc_a = _FakeDoc("/projects/a/part.FCStd")
        doc_b = _FakeDoc("/projects/b/part.FCStd")
        self._run(doc_a, str(tmp_path))
        self._run(doc_b, str(tmp_path))
        assert doc_a.saved_paths[0] != doc_b.saved_paths[0]
        for p in doc_a.saved_paths + doc_b.saved_paths:
            assert os.path.dirname(p) == str(tmp_path)

    def test_prunes_managed_dir(self, tmp_path):
        # #46: bounded disk use — _auto_save prunes the managed dir with the
        # shared helper, matching only .ai-backup.FCStd snapshots.
        doc = _FakeDoc("/tmp/part.FCStd")
        with patch("freecad_ai.config.BACKUPS_DIR", str(tmp_path)), patch(
            "freecad_ai.config.prune_oldest_files"
        ) as mock_prune, patch(
            "freecad_ai.core.active_document.resolve_active_document",
            return_value=doc,
        ):
            executor._auto_save({})
        assert mock_prune.called
        assert mock_prune.call_args[0][0] == str(tmp_path)
        # the pattern predicate must accept our snapshots and reject foreign files
        pattern_fn = mock_prune.call_args[0][1]
        assert pattern_fn("part.abc12345.ai-backup.FCStd") is True
        assert pattern_fn("something-else.json") is False

    def test_no_backup_for_unsaved_document(self, tmp_path):
        # An unsaved document (empty FileName) has nothing to snapshot.
        doc = _FakeDoc("")
        self._run(doc, str(tmp_path))
        assert doc.saved_paths == []


class TestTheSnapshotIsRecordedForRestore:
    """#49: until this, a snapshot could not be mapped back to a document.

    ``FileName`` is transient and the filename tag is a one-way hash, so the
    only record of where a snapshot came from is the one written here, at the
    moment the snapshot is taken.
    """

    _run = TestAutoSave._run

    def test_the_snapshot_is_offered_with_its_original(self, tmp_path):
        doc = _FakeDoc("/home/user/project/part.FCStd")
        self._run(doc, str(tmp_path))

        [snap] = backups.list_snapshots(str(tmp_path))

        assert snap.original_path == "/home/user/project/part.FCStd"
        assert snap.path == doc.saved_paths[0]

    def test_the_documents_own_label_is_recorded(self, tmp_path):
        # A Label need not match the filename stem -- FreeCAD renames freely,
        # and the tree is what the user recognises their document by.
        doc = _FakeDoc("/home/user/project/part.FCStd")
        doc.Label = "Enclosure Base"

        self._run(doc, str(tmp_path))

        [snap] = backups.list_snapshots(str(tmp_path))
        assert snap.label == "Enclosure Base"

    def test_recording_happens_after_the_document_is_restored(self, tmp_path):
        # _auto_save swallows exceptions wholesale, so anything between the
        # saveAs and the FileName/Label restore can leave the user's document
        # renamed. Recording is a nice-to-have; the restore is not.
        doc = _FakeDoc("/tmp/part.FCStd")
        with patch.object(backups, "record_snapshot",
                          side_effect=OSError("disk full")):
            self._run(doc, str(tmp_path))

        assert doc.FileName == "/tmp/part.FCStd"
        assert doc.Label == "part"


class TestFindFreecadCmd:
    """Regression tests for console-binary discovery (#58).

    A PATH/glob-based guess can resolve to a completely unrelated FreeCAD
    install — a Snap package on PATH while the live session runs from a
    Flatpak. That foreign binary imports its own incompatible Draft/Arch/PySide
    stack and segfaults, permanently blocking the sandbox pre-check for
    anything BIM-related. The running session's own ``FreeCAD.getHomePath()``
    is the only source guaranteed to match.
    """

    @staticmethod
    def _make_home(tmp_path, name="freecadcmd"):
        """Build a fake FreeCAD home with an executable console binary."""
        bin_dir = tmp_path / "usr" / "bin"
        bin_dir.mkdir(parents=True)
        binary = bin_dir / name
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        return str(tmp_path / "usr"), str(binary)

    @staticmethod
    def _app_module(home):
        app = MagicMock()
        app.getHomePath.return_value = home
        return app

    def test_prefers_running_sessions_own_console_binary(self, tmp_path):
        # A decoy AppImage is present and would win under the old glob-first
        # ordering; the live session's binary must take precedence.
        home, binary = self._make_home(tmp_path)
        with patch.dict(sys.modules, {"FreeCAD": self._app_module(home)}), patch(
            "glob.glob", return_value=["/home/someone/bin/FreeCAD_9.9.9.AppImage"]
        ):
            assert executor._find_freecad_cmd() == binary

    def test_accepts_capitalised_binary_name(self, tmp_path):
        home, binary = self._make_home(tmp_path, name="FreeCADCmd")
        with patch.dict(sys.modules, {"FreeCAD": self._app_module(home)}), patch(
            "glob.glob", return_value=[]
        ):
            assert executor._find_freecad_cmd() == binary

    def test_non_executable_binary_is_ignored(self, tmp_path):
        home, binary = self._make_home(tmp_path)
        os.chmod(binary, 0o644)
        decoy = "/home/someone/bin/FreeCAD_9.9.9.AppImage"
        with patch.dict(sys.modules, {"FreeCAD": self._app_module(home)}), patch(
            "glob.glob", return_value=[decoy]
        ):
            assert executor._find_freecad_cmd() == decoy

    def test_falls_back_when_home_has_no_console_binary(self, tmp_path):
        # Some builds ship no freecadcmd next to the GUI binary — the existing
        # AppImage/PATH chain must still be reachable.
        (tmp_path / "usr" / "bin").mkdir(parents=True)
        decoy = "/home/someone/bin/FreeCAD_9.9.9.AppImage"
        with patch.dict(
            sys.modules, {"FreeCAD": self._app_module(str(tmp_path / "usr"))}
        ), patch("glob.glob", return_value=[decoy]):
            assert executor._find_freecad_cmd() == decoy

    def test_falls_back_when_freecad_is_not_importable(self, tmp_path):
        # Unit-test context, or any process without FreeCAD on sys.path.
        decoy = "/home/someone/bin/FreeCAD_9.9.9.AppImage"
        with patch.dict(sys.modules, {"FreeCAD": None}), patch(
            "glob.glob", return_value=[decoy]
        ):
            assert executor._find_freecad_cmd() == decoy


class TestSandboxGuiStub:
    """Regression tests for the headless FreeCADGui stub (#58).

    Importing the *real* FreeCADGui in the console sandbox and then anything
    that pulls in Arch segfaults — no display, no QApplication event loop. The
    crash happens during the import itself, so patching attributes afterwards
    is too late; the real module must never be imported at all.
    """

    def _generated_script(self):
        """Run _sandbox_test far enough to capture the generated harness."""
        captured = {}
        real_open = open

        def spy_open(path, mode="r", *a, **kw):
            handle = real_open(path, mode, *a, **kw)
            if "w" in mode and str(path).endswith(".py"):
                original_write = handle.write

                def write(text):
                    captured["src"] = text
                    return original_write(text)

                handle.write = write
            return handle

        with patch("freecad_ai.core.executor._find_freecad_cmd", return_value="/bin/true"), \
                patch("builtins.open", spy_open), \
                patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            executor._sandbox_test("pass", timeout=1)
        return captured.get("src", "")

    def test_harness_installs_a_fake_gui_module(self):
        src = self._generated_script()
        assert 'sys.modules["FreeCADGui"]' in src

    def test_harness_never_imports_the_real_gui_module(self):
        # `import FreeCADGui` is the exact statement that segfaults.
        src = self._generated_script()
        assert "import FreeCADGui" not in src


class TestConsoleCaptureVisibility:
    """Issue #82 follow-up: the sandbox's C++ console channel is dead, and
    said nothing about it.

    The harness installs an `App.Console.AddObserver` observer to catch errors
    the C++ layer logs without raising a Python exception — attachment and
    recompute failures that would otherwise pass validation silently. On
    FreeCAD 1.1.1 in console mode (`-c`) that method does not exist:
    `App.Console` exposes GetObservers/Print*/SetStatus and no AddObserver at
    all. The registration raised AttributeError into a bare `except: pass`, so
    `_observer_installed` was False on every run and the channel collected
    nothing — while the sandbox went on reporting success as though it had
    checked.

    Losing the capability is one bug (tracked separately). Losing it *silently*
    is the one these tests close: a degraded fallback has to say which path it
    actually took.
    """

    def test_ok_status_produces_no_warning(self):
        assert executor._console_capture_warning({"console_capture": "ok"}) is None

    def test_failure_status_is_reported(self):
        msg = executor._console_capture_warning(
            {"console_capture": "AttributeError: module '__FreeCADConsole__' "
                                "has no attribute 'AddObserver'"})
        assert msg is not None
        # The reason has to survive into the message, or the next person gets
        # "something failed" and starts over from nothing.
        assert "AddObserver" in msg

    def test_the_warning_says_what_is_no_longer_checked(self):
        msg = executor._console_capture_warning({"console_capture": "boom"})
        assert msg and "console" in msg.lower()

    def test_a_result_without_the_key_is_not_treated_as_a_failure(self):
        # An older harness, or a result file from a version that predates the
        # field; absence is unknown, not broken.
        assert executor._console_capture_warning({}) is None
        assert executor._console_capture_warning(None) is None

    def test_the_harness_records_why_the_observer_did_not_install(self):
        captured = {}

        class _FakeProc:
            returncode = 0

        def _fake_run(cmd, **kwargs):
            with open(cmd[2]) as fh:
                captured["harness"] = fh.read()
            return _FakeProc()

        with patch("freecad_ai.core.executor._find_freecad_cmd",
                   return_value="/usr/bin/freecadcmd"):
            with patch("freecad_ai.core.executor.subprocess.run",
                       side_effect=_fake_run):
                executor._sandbox_test("x = 1", timeout=5)

        harness = captured.get("harness", "")
        assert harness, "sandbox did not generate a harness script"
        assert "console_capture" in harness, (
            "the harness must report whether console capture was installed; "
            "swallowing the failure is what hid the dead channel")
        # The reason, not just a boolean — 'it broke' is not actionable.
        assert "_console_capture_status" in harness

    def test_the_warning_is_logged_once_not_per_execution(self):
        # execute_code runs constantly in a session; a per-run warning would
        # train the reader to ignore it.
        executor._CONSOLE_CAPTURE_WARNED = False
        bad = {"console_capture": "AttributeError: no AddObserver"}
        with patch("freecad_ai.core.executor.logger") as log:
            executor._warn_console_capture_once(bad)
            executor._warn_console_capture_once(bad)
        assert log.warning.call_count == 1

    def test_a_healthy_result_never_warns(self):
        executor._CONSOLE_CAPTURE_WARNED = False
        with patch("freecad_ai.core.executor.logger") as log:
            executor._warn_console_capture_once({"console_capture": "ok"})
        log.warning.assert_not_called()



# Captured verbatim from FreeCAD_1.1.1-Linux-x86_64-py311.AppImage running the
# sandbox harness over a document holding a sketch attached to a face that does
# not exist. Note the SAME error appears in the baseline region and twice more
# inside the user-code window: a broken object stays Touched, so every later
# recompute re-emits it. That repetition is issue #82's false positive, and it
# is why the baseline has to suppress by message text and not merely by
# position relative to the marker.
_REAL_STDERR = """\
PositionBySupport: AttachEngine3D: subshape not found Box.Face99
StaleAttach: AttachEngine3D: subshape not found Box.Face99
__FCAI_USER_CODE_BEGIN__
StaleAttach: AttachEngine3D: subshape not found Box.Face99
NoProfilePad: No object linked
StaleAttach: AttachEngine3D: subshape not found Box.Face99
__FCAI_USER_CODE_END__
"""


class TestConsoleErrorsFromStderr:
    """Issue #83's revived channel, carrying issue #82's fix from day one.

    The sandbox subprocess writes FreeCAD's C++ console errors to fd 2. The
    harness brackets the user's code with markers on that same descriptor
    (``os.write``, unbuffered, so ordering against the C++ writes holds) and
    turns the console's ``Wrn`` level off, leaving errors alone on the stream.

    Everything before the opening marker belongs to the document as it was
    opened, not to the code under test.
    """

    def _errs(self, text):
        return executor._console_errors_from_stderr(text)

    def test_an_error_in_the_user_window_is_reported(self):
        assert self._errs(
            "__FCAI_USER_CODE_BEGIN__\nPad: No object linked\n"
            "__FCAI_USER_CODE_END__\n") == ["Pad: No object linked"]

    def test_baseline_errors_are_not_blamed_on_the_user_code(self):
        assert self._errs(
            "Box: something was already wrong\n__FCAI_USER_CODE_BEGIN__\n"
            "__FCAI_USER_CODE_END__\n") == []

    def test_a_baseline_error_repeated_inside_the_window_is_suppressed(self):
        # The #82 regression, and the one that actually bites: the object is
        # still Touched, so the user's recompute re-emits its error verbatim.
        assert self._errs(_REAL_STDERR) == ["NoProfilePad: No object linked"]

    def test_repeats_within_the_window_are_collapsed(self):
        assert self._errs(
            "__FCAI_USER_CODE_BEGIN__\nPad: boom\nPad: boom\n"
            "__FCAI_USER_CODE_END__\n") == ["Pad: boom"]

    def test_output_after_the_closing_marker_is_ignored(self):
        # Document teardown in the harness finally-block runs after the end
        # marker and can log; that is not the user's code either.
        assert self._errs(
            "__FCAI_USER_CODE_BEGIN__\n__FCAI_USER_CODE_END__\n"
            "Closing: teardown complaint\n") == []

    def test_no_markers_means_nothing_is_attributed(self):
        # A crash before the markers were written leaves undelimited output.
        # Blaming the user's code for all of it is the false positive we are
        # removing, so the honest answer is to report nothing.
        assert self._errs("Some startup noise\nAnd more\n") == []

    def test_a_missing_closing_marker_attributes_nothing(self):
        assert self._errs(
            "__FCAI_USER_CODE_BEGIN__\nPad: boom\n") == []

    def test_blank_lines_are_not_errors(self):
        assert self._errs(
            "__FCAI_USER_CODE_BEGIN__\n\n   \n__FCAI_USER_CODE_END__\n") == []

    def test_empty_input_is_handled(self):
        assert self._errs("") == []
        assert self._errs(None) == []

    def test_the_report_is_capped(self):
        body = "".join("Obj{}: boom\n".format(i) for i in range(50))
        found = self._errs(
            "__FCAI_USER_CODE_BEGIN__\n" + body + "__FCAI_USER_CODE_END__\n")
        assert 0 < len(found) <= 10


class TestTheHarnessDelimitsAndGatesTheStream:
    def _harness(self, code="x = 1"):
        captured = {}

        class _FakeProc:
            returncode = 0
            stderr = b""

        def _fake_run(cmd, **kwargs):
            with open(cmd[2]) as fh:
                captured["h"] = fh.read()
            return _FakeProc()

        with patch("freecad_ai.core.executor._find_freecad_cmd",
                   return_value="/usr/bin/freecadcmd"):
            with patch("freecad_ai.core.executor.subprocess.run",
                       side_effect=_fake_run):
                executor._sandbox_test(code, timeout=5)
        return captured["h"]

    def test_warnings_are_gated_off_so_stderr_carries_errors_only(self):
        # PrintError and PrintWarning both reach fd 2 with no severity prefix,
        # so the stream cannot be filtered after the fact. SetStatus is how
        # FreeCAD 1.1.1 lets us drop the warnings at the source.
        h = self._harness()
        assert "SetStatus" in h and '"Wrn"' in h

    def test_both_markers_are_written_to_fd_2(self):
        h = self._harness()
        assert h.count("__FCAI_USER_CODE_BEGIN__") == 1
        assert h.count("__FCAI_USER_CODE_END__") == 1
        # On fd 2 directly: a buffered sys.stderr write would not interleave
        # correctly with the C++ layer's own unbuffered writes.
        assert "os.write(2" in h or "_os.write(2" in h

    def test_the_opening_marker_comes_after_the_baseline_recompute(self):
        h = self._harness("MY_UNIQUE_USER_CODE = 1")
        begin = h.index("__FCAI_USER_CODE_BEGIN__")
        user = h.index("MY_UNIQUE_USER_CODE")
        end = h.index("__FCAI_USER_CODE_END__")
        baseline = h.index("_baseline_bad = set()")
        assert baseline < begin < user < end

    def test_the_dead_observer_path_is_gone(self):
        # The call, not the word: the harness still names AddObserver in a
        # comment explaining why it was removed, and that note is worth
        # keeping for whoever wonders why the channel reads stderr.
        h = self._harness()
        assert "App.Console.AddObserver(" not in h, (
            "App.Console has no AddObserver in console mode; keeping the call "
            "leaves a path that cannot run")
        assert "_err_obs" not in h


class TestStderrErrorsReachTheVerdict:
    """The parent, not the harness, owns this verdict.

    The harness cannot read its own stderr, so the console channel is merged
    on the parent side after the subprocess exits. That is also why the seam
    is a pure function over a string and needs no FreeCAD to test.
    """

    def _run(self, payload, stderr, tmp_path):
        result_file = str(tmp_path / "result.json")
        script_file = str(tmp_path / "harness.py")

        class _FakeProc:
            returncode = 0

        _FakeProc.stderr = stderr

        def _fake_run(cmd, **kwargs):
            with open(result_file, "w") as fh:
                json.dump(payload, fh)
            return _FakeProc()

        with patch("freecad_ai.core.executor.tempfile.mktemp",
                   side_effect=[result_file, script_file]):
            with patch("freecad_ai.core.executor._find_freecad_cmd",
                       return_value="/usr/bin/freecadcmd"):
                with patch("freecad_ai.core.executor.subprocess.run",
                           side_effect=_fake_run):
                    return executor._sandbox_test("x = 1", timeout=5)

    def test_a_window_error_fails_an_otherwise_clean_run(self, tmp_path):
        ok, msg = self._run(
            {"ok": True, "error": "", "console_capture": "ok"},
            b"__FCAI_USER_CODE_BEGIN__\nPad: No object linked\n"
            b"__FCAI_USER_CODE_END__\n", tmp_path)
        assert ok is False
        assert "No object linked" in msg

    def test_a_clean_stream_still_passes(self, tmp_path):
        ok, msg = self._run(
            {"ok": True, "error": "", "console_capture": "ok"},
            b"__FCAI_USER_CODE_BEGIN__\n__FCAI_USER_CODE_END__\n", tmp_path)
        assert ok is True and msg == ""

    def test_baseline_noise_alone_still_passes(self, tmp_path):
        ok, msg = self._run(
            {"ok": True, "error": "", "console_capture": "ok"},
            b"Box: already broken\n__FCAI_USER_CODE_BEGIN__\n"
            b"Box: already broken\n__FCAI_USER_CODE_END__\n", tmp_path)
        assert ok is True and msg == ""

    def test_errors_are_ignored_when_warning_gating_failed(self, tmp_path):
        # Without the gate the stream carries warnings too, and reporting
        # those as errors is exactly issue #82's false positive. Degrade to
        # the object-state channel rather than guess at severity.
        ok, msg = self._run(
            {"ok": True, "error": "", "console_capture": "AttributeError: nope"},
            b"__FCAI_USER_CODE_BEGIN__\nSketch: redundant constraints\n"
            b"__FCAI_USER_CODE_END__\n", tmp_path)
        assert ok is True

    def test_object_state_issues_and_console_errors_are_both_reported(self, tmp_path):
        ok, msg = self._run(
            {"ok": False,
             "error": "Post-execution validation found issues:\nBox has an invalid shape",
             "console_capture": "ok"},
            b"__FCAI_USER_CODE_BEGIN__\nBox: subshape not found\n"
            b"__FCAI_USER_CODE_END__\n", tmp_path)
        assert ok is False
        assert "invalid shape" in msg and "subshape not found" in msg
