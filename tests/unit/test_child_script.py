"""_build_child_script: one harness for the sandbox and headless runs (#114)."""

import json
import os
import subprocess
import sys

import pytest

from freecad_ai.core import executor

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
CODE = "x = 1\nif x:\n    print('hi')\n"


@pytest.mark.parametrize("name, doc", [("new", None), ("open", "/tmp/fcai_in.FCStd")])
def test_sandbox_mode_is_byte_identical_to_the_old_harness(name, doc):
    with open(os.path.join(GOLDEN, f"sandbox_harness_{name}.txt")) as f:
        expected = f.read()
    got = executor._build_child_script(
        CODE, document_path=doc, result_path="/tmp/fcai_r.json", mode="sandbox")
    assert got == expected


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        executor._build_child_script("pass", document_path=None,
                                     result_path="/tmp/r.json", mode="nope")


# ── headless mode ───────────────────────────────────────────

_FREECAD_STUB = '''
class _Doc:
    def __init__(self, name):
        self.Name = name
        self.FileName = ""

_docs = {}
ActiveDocument = None

def newDocument(name):
    global ActiveDocument
    ActiveDocument = _docs[name] = _Doc(name)
    return ActiveDocument

def openDocument(path):
    global ActiveDocument
    ActiveDocument = _docs["Opened"] = _Doc("Opened")
    ActiveDocument.FileName = path
    return ActiveDocument

def setActiveDocument(name):
    pass

def listDocuments():
    return dict(_docs)

def closeDocument(name):
    _docs.pop(name, None)
'''


def _run_headless_child(tmp_path, code, document_path=None):
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "FreeCAD.py").write_text(_FREECAD_STUB)
    run = tmp_path / "run"
    run.mkdir()
    result = run / "result.json"
    script = run / "script.py"
    script.write_text(executor._build_child_script(
        code, document_path=document_path, result_path=str(result),
        mode="headless"), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script)], cwd=run, timeout=60,
                          env={**os.environ, "PYTHONPATH": str(stub), "LC_ALL": "C"})
    assert proc.returncode == 0
    out = (run / "output.log").read_text(encoding="utf-8")
    err = (run / "stderr.log").read_text(encoding="utf-8")
    return json.loads(result.read_text(encoding="utf-8")), out, err, run


def test_headless_prints_go_to_output_log(tmp_path):
    res, out, err, _ = _run_headless_child(tmp_path, "print('volume', 1000)\n")
    assert res == {"ok": True, "error": ""}
    assert out == "volume 1000\n" and err == ""


def test_headless_defines_work_dir_as_the_run_folder(tmp_path):
    code = "open(WORK_DIR + '/r.txt', 'w').write('x')\nprint(App.ActiveDocument.Name)\n"
    res, out, _, run = _run_headless_child(tmp_path, code)
    assert res["ok"] and (run / "r.txt").read_text() == "x"
    assert out == "Headless\n"


def test_headless_opens_the_given_document(tmp_path):
    res, out, _, _ = _run_headless_child(
        tmp_path, "print(App.ActiveDocument.FileName)\n", document_path="/x/in.FCStd")
    assert res["ok"] and out == "/x/in.FCStd\n"


def test_headless_exception_reports_the_traceback_and_keeps_output(tmp_path):
    res, out, _, _ = _run_headless_child(tmp_path, "print('before')\n1 / 0\n")
    assert not res["ok"]
    assert "ZeroDivisionError" in res["error"] and "user_code.py" in res["error"]
    assert out == "before\n"


def test_headless_stderr_goes_to_its_own_log(tmp_path):
    _, out, err, _ = _run_headless_child(
        tmp_path, "import sys\nprint('warn', file=sys.stderr)\n")
    assert err == "warn\n" and out == ""


def test_headless_keeps_multiline_string_literals(tmp_path):
    # Review focus 1: the sandbox indents user code, which would turn this into 'a\n    b'
    res, out, _, _ = _run_headless_child(tmp_path, 's = """a\nb"""\nprint(repr(s))\n')
    assert res["ok"] and out == "'a\\nb'\n"


def test_headless_non_ascii_output_round_trips(tmp_path):
    # Review focus 5: LC_ALL=C in the child must not break UTF-8 output
    res, out, _, _ = _run_headless_child(tmp_path, "print('Ø 10 mm, Größe')\n")
    assert res["ok"] and out == "Ø 10 mm, Größe\n"


@pytest.mark.parametrize("code, ok", [("import sys\nsys.exit()\n", True),
                                      ("import sys\nsys.exit(0)\n", True),
                                      ("import sys\nsys.exit(3)\n", False)])
def test_headless_sys_exit_zero_counts_as_completion(tmp_path, code, ok):
    res, _, _, _ = _run_headless_child(tmp_path, code)
    assert res["ok"] is ok


def test_headless_script_never_imports_the_real_gui():
    src = executor._build_child_script("pass", document_path=None,
                                       result_path="/tmp/run/result.json", mode="headless")
    assert "import FreeCADGui" not in src and 'sys.modules["FreeCADGui"]' in src
    assert "_os._exit(0)" in src
