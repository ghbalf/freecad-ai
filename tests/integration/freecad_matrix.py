"""Pick FreeCAD AppImages for the integration suite, and run it across versions.

Versions come from the AppImage filename (``FreeCAD_1.1.4-…``,
``FreeCAD_26.3rc2-…``) and are compared numerically, never alphabetically.

* Default for ``pytest -m integration``: the newest stable AppImage.
* Matrix (``python -m tests.integration.freecad_matrix``): the oldest
  supported stable (>= ``<freecadmin>`` in package.xml), the newest stable,
  and every pre-release newer than that. A failing pre-release is reported
  as an upcoming incompatibility but does not fail the run; a failing
  stable does.

Stdlib only; no FreeCAD import.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from dataclasses import dataclass

APPIMAGE_DIR = "~/bin"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_NAME = re.compile(
    r"^FreeCAD[_-]((\d+)\.(\d+)(?:\.(\d+))?([A-Za-z][A-Za-z0-9]*)?)(?:[-_]|\.AppImage$)"
)


@dataclass(frozen=True)
class AppImage:
    path: str
    version: tuple[int, int, int]
    pre: str = ""  # "" for a stable release, else e.g. "rc2", "beta"
    label: str = ""  # the version as the filename spells it, e.g. "26.3rc2"

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    def sort_key(self):
        # A final release sorts after its own pre-releases.
        return (self.version, not self.pre, self.pre)


def parse(path: str) -> AppImage | None:
    name = os.path.basename(path)
    m = _NAME.match(name) if name.endswith(".AppImage") else None
    if not m:
        return None
    label, major, minor, patch, pre = m.groups()
    return AppImage(path, (int(major), int(minor), int(patch or 0)), pre or "", label)


def discover(directory: str = APPIMAGE_DIR) -> list[AppImage]:
    """Executable, versioned FreeCAD AppImages in *directory*, oldest first."""
    found = []
    for path in glob.glob(os.path.join(os.path.expanduser(directory), "FreeCAD*.AppImage")):
        img = parse(path)
        if img and os.path.isfile(path) and os.access(path, os.X_OK):
            found.append(img)
    return sorted(found, key=AppImage.sort_key)


def default_appimage(images: list[AppImage]) -> AppImage | None:
    """Newest stable; the newest pre-release only if there is no stable."""
    stable = [i for i in images if not i.is_prerelease]
    pool = stable or images
    return max(pool, key=AppImage.sort_key) if pool else None


def freecad_min(package_xml: str = os.path.join(_ROOT, "package.xml")) -> tuple[int, ...]:
    with open(package_xml, encoding="utf-8") as f:
        m = re.search(r"<freecadmin>([\d.]+)</freecadmin>", f.read())
    return tuple(int(p) for p in m.group(1).split(".")) if m else (0,)


def matrix(images: list[AppImage], minimum: tuple[int, ...]) -> list[AppImage]:
    stable = sorted((i for i in images if not i.is_prerelease and i.version >= minimum),
                    key=AppImage.sort_key)
    chosen = []
    if stable:
        chosen.append(stable[0])
        if stable[-1] is not stable[0]:
            chosen.append(stable[-1])
    newest = stable[-1].version if stable else (0, 0, 0)
    chosen += sorted((i for i in images if i.is_prerelease and i.version > newest),
                     key=AppImage.sort_key)
    return chosen


def run_matrix(images: list[AppImage], run, out=print) -> int:
    """Run ``run(img) -> returncode`` per image; 1 if any stable failed."""
    if not images:
        out("No FreeCAD AppImages found for the matrix.")
        return 1
    results = [(img, run(img)) for img in images]
    out("\nFreeCAD integration matrix:")
    for img, rc in results:
        kind = "pre-release" if img.is_prerelease else "stable"
        out(f"  {img.label:<12} {kind:<12} {'PASS' if rc == 0 else 'FAIL'}  {img.path}")
    for img, rc in results:
        if rc and img.is_prerelease:
            out(f"WARNING: FreeCAD {img.label} fails the suite: an upcoming incompatibility "
                f"(adapt early, or report it upstream). Not blocking.")
    return 1 if any(rc and not img.is_prerelease for img, rc in results) else 0


def _pytest(img: AppImage, extra: list[str]) -> int:
    print(f"\n=== FreeCAD {img.label}: {img.path}", flush=True)
    cmd = [sys.executable, "-m", "pytest", "-m", "integration", "tests/integration",
           "-q", "-p", "no:cacheprovider", *extra]
    return subprocess.run(cmd, cwd=_ROOT, env={**os.environ, "FREECAD_APPIMAGE": img.path}).returncode


def main(argv: list[str]) -> int:
    """Usage: python -m tests.integration.freecad_matrix [--list] [-- PYTEST_ARGS...]"""
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    images = matrix(discover(os.environ.get("FREECAD_APPIMAGE_DIR", APPIMAGE_DIR)), freecad_min())
    if "--list" in argv:
        for img in images:
            print(f"{img.label:<12} {'pre-release' if img.is_prerelease else 'stable'}  {img.path}")
        return 0
    return run_matrix(images, lambda img: _pytest(img, extra))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
