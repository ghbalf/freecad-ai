"""Integration test fixtures — runs FreeCAD scripts via AppImage subprocess."""

import json
import os
import subprocess
import textwrap

import pytest

from tests.integration.freecad_matrix import APPIMAGE_DIR, default_appimage, discover

# Find FreeCAD AppImage: the newest stable one in ~/bin (FREECAD_APPIMAGE_DIR),
# compared by version number. FREECAD_APPIMAGE picks one explicitly, e.g.
#   FREECAD_APPIMAGE=~/bin/FreeCAD_26.3rc2-Linux-x86_64.AppImage pytest -m integration
# Several versions at once: python -m tests.integration.freecad_matrix
FREECAD_CMD = None
_explicit = os.environ.get("FREECAD_APPIMAGE", "")
if _explicit:
    _c = os.path.expanduser(_explicit)
    if os.path.isfile(_c) and os.access(_c, os.X_OK):
        FREECAD_CMD = _c
else:
    _default = default_appimage(discover(os.environ.get("FREECAD_APPIMAGE_DIR", APPIMAGE_DIR)))
    FREECAD_CMD = _default.path if _default else None


def pytest_configure(config):
    # A mistyped FREECAD_APPIMAGE must not turn into "all integration tests skipped".
    if _explicit and FREECAD_CMD is None:
        raise pytest.UsageError(f"FREECAD_APPIMAGE={_explicit!r} is not an executable file")


def pytest_report_header(config):
    return f"freecad: {FREECAD_CMD or 'not found (integration tests skip)'}"


def pytest_collection_modifyitems(config, items):
    """Skip integration tests if FreeCAD is not available."""
    if FREECAD_CMD is None:
        skip = pytest.mark.skip(reason="FreeCAD AppImage not found")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def run_freecad_script(tmp_path):
    """Fixture that runs a Python script in FreeCAD console mode.

    Returns a function that accepts script content and returns parsed
    JSON results from the script's output file.
    """
    def _run(script_content, timeout=60):
        script_file = tmp_path / "test_script.py"
        result_file = tmp_path / "result.json"

        # Wrapper that sets up the environment and captures results
        wrapper = textwrap.dedent(f"""\
            import sys, json, traceback
            RESULT_FILE = {str(result_file)!r}
            results = {{"ok": False, "error": "", "data": {{}}}}
            try:
                import FreeCAD as App
                import Part
                import PartDesign
                import Sketcher

                # Add project root to path for tool imports
                sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))!r})

                doc = App.newDocument("TestDoc")

        """)

        footer = textwrap.dedent(f"""\

                doc.recompute()
                results["ok"] = True
            except Exception as e:
                results["error"] = traceback.format_exc()
            finally:
                with open(RESULT_FILE, "w") as f:
                    json.dump(results, f, indent=2, default=str)
                sys.exit(0)
        """)

        # Indent user script to be inside the try block
        indented = textwrap.indent(script_content, "    ")
        full_script = wrapper + indented + footer
        script_file.write_text(full_script)

        proc = subprocess.run(
            [FREECAD_CMD, "-c", str(script_file)],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            timeout=timeout,
            capture_output=True,
        )

        if not result_file.exists():
            stderr = proc.stderr.decode(errors="replace")[-1000:]
            raise RuntimeError(
                f"FreeCAD script failed (rc={proc.returncode}).\n"
                f"stderr: {stderr}"
            )

        return json.loads(result_file.read_text())

    return _run
