"""_build_child_script: one harness for the sandbox and headless runs (#114)."""

import os

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
