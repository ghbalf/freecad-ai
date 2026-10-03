"""core.fem: run_fem_analysis building blocks (#113)."""

import pytest

from freecad_ai.core import fem


def test_solve_code_compiles_and_embeds_the_values_safely():
    # Review focus 1
    name = "An'al\"ysis Ø"
    code = fem.build_solve_code(name, "3 mm")
    assert code.startswith("ANALYSIS = {!r}\nMESH_SIZE = {!r}\n".format(name, "3 mm"))
    compile(code, "solve.py", "exec")


def test_solve_code_writes_the_summary_into_the_run_folder():
    code = fem.build_solve_code("Analysis", "")
    assert 'os.path.join(WORK_DIR, "summary.json")' in code
    assert "UserString" not in code  # locale-formatted, see Global Constraints
    assert "Gmsh not found" in code and "Netgen" in code  # spec error table
