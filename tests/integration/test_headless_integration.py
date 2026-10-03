"""execute_code_headless against a real FreeCAD console binary (#114)."""

import os
import sys

import pytest

from freecad_ai.core import headless
from freecad_ai.core.executor import _find_freecad_cmd

pytestmark = pytest.mark.integration


@pytest.fixture
def freecad_bin():
    found = _find_freecad_cmd()
    if not found:
        pytest.skip("no FreeCAD console binary")
    return found


def _go(tmp_path, freecad_bin, code, timeout=120, document_path=None):
    run_dir = headless.new_run_dir(str(tmp_path / "headless"))
    return headless.run_headless(code, freecad_bin=freecad_bin, run_dir=run_dir,
                                 document_path=document_path, timeout=timeout)


def test_box_volume_and_a_result_file(tmp_path, freecad_bin):
    code = ("import Part\n"
            "b = App.ActiveDocument.addObject('Part::Box', 'B')\n"
            "App.ActiveDocument.recompute()\n"
            "print('volume', round(b.Shape.Volume))\n"
            "b.Shape.exportStep(WORK_DIR + '/box.step')\n"
            "print('Ø ok')\n")
    run = _go(tmp_path, freecad_bin, code)
    assert run.status == "ok", run.error
    assert "volume 1000" in run.stdout and "Ø ok" in run.stdout
    assert os.path.getsize(os.path.join(run.run_dir, "box.step")) > 0


def test_opens_the_given_document_without_saving_it(tmp_path, freecad_bin):
    src = tmp_path / "src.FCStd"
    make = ("App.ActiveDocument.addObject('Part::Box', 'B')\n"
            "App.ActiveDocument.saveAs({!r})\n".format(str(src)))
    assert _go(tmp_path, freecad_bin, make).status == "ok"
    before = src.read_bytes()
    run = _go(tmp_path, freecad_bin,
              "App.ActiveDocument.addObject('Part::Cylinder', 'C')\n"
              "print(sorted(o.Name for o in App.ActiveDocument.Objects))\n",
              document_path=str(src))
    assert run.status == "ok", run.error
    assert "['B', 'C']" in run.stdout
    assert src.read_bytes() == before


@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc")
def test_timeout_leaves_no_process_behind(tmp_path, freecad_bin):
    run = _go(tmp_path, freecad_bin, "import time\nprint('start')\ntime.sleep(120)\n",
              timeout=15)
    assert run.status == "timeout" and "start" in run.stdout
    script = os.path.join(run.run_dir, "script.py").encode()
    alive = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if script in f.read():
                    alive.append(pid)
        except OSError:
            pass
    assert alive == []


def test_abort_is_reported_as_a_crash(tmp_path, freecad_bin):
    run = _go(tmp_path, freecad_bin, "import os\nos.abort()\n")
    assert run.status == "crashed" and "SIGABRT" in run.error, (run.exit_code, run.error)
