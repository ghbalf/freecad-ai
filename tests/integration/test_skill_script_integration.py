"""A skill script runs under real FreeCAD through the runpy wrapper."""

import pytest

from freecad_ai.core.executor import _find_freecad_cmd, _sandbox_test
from freecad_ai.extensions.skill_scripts import build_script_wrapper

pytestmark = pytest.mark.integration

_BOX_SCRIPT = '''\
from __future__ import annotations
import sys
import FreeCAD as App

def main(length: float) -> None:
    doc = App.ActiveDocument
    box = doc.addObject("Part::Box", "SkillBox")
    box.Length = length
    doc.recompute()
    if abs(box.Shape.Volume - length * 100) > 1e-6:
        raise RuntimeError(f"unexpected volume {box.Shape.Volume}")

if __name__ == "__main__":
    main(float(sys.argv[1]))
'''


@pytest.fixture(scope="module")
def freecad_available():
    if not _find_freecad_cmd():
        pytest.skip("No FreeCAD binary available for sandbox tests")


def test_skill_script_creates_a_box(tmp_path, freecad_available):
    script = tmp_path / "scripts" / "box.py"
    script.parent.mkdir()
    script.write_text(_BOX_SCRIPT)
    safe, err = _sandbox_test(build_script_wrapper(str(script), ["20"]), timeout=60)
    assert safe, err


def test_nonzero_exit_fails_in_freecad(tmp_path, freecad_available):
    script = tmp_path / "scripts" / "fail.py"
    script.parent.mkdir()
    script.write_text("import sys\nsys.exit(3)\n")
    safe, err = _sandbox_test(build_script_wrapper(str(script), []), timeout=60)
    assert not safe and "status 3" in err
