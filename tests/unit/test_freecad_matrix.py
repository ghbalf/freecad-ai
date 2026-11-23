"""Tests for the FreeCAD AppImage version picker used by the integration suite."""

import os

import pytest

from tests.integration.freecad_matrix import (
    default_appimage,
    discover,
    matrix,
    parse,
    run_matrix,
)


@pytest.mark.parametrize("name, version, pre", [
    ("FreeCAD_1.1.4-Linux-x86_64-py311.AppImage", (1, 1, 4), ""),
    ("FreeCAD_1.0.2-conda-Linux-x86_64-py311.AppImage", (1, 0, 2), ""),
    ("FreeCAD_26.3rc2-Linux-x86_64.AppImage", (26, 3, 0), "rc2"),
    ("FreeCAD_26.3-Linux-x86_64.AppImage", (26, 3, 0), ""),
    ("FreeCAD_1.1.0rc1-Linux-x86_64-py311.AppImage", (1, 1, 0), "rc1"),
    ("FreeCAD_1.1.0beta-Linux-x86_64.AppImage", (1, 1, 0), "beta"),
    ("FreeCAD_1.1.4.AppImage", (1, 1, 4), ""),
])
def test_parse(name, version, pre):
    img = parse("/x/" + name)
    assert img.version == version
    assert img.pre == pre
    assert img.is_prerelease == bool(pre)


@pytest.mark.parametrize("name", [
    "FreeCAD_weekly-2026.11.10-Linux-x86_64.AppImage",
    "FreeCAD.AppImage",
    "OtherApp_1.0.AppImage",
    "FreeCAD_1.1.4-Linux-x86_64-py311.zsync",
])
def test_parse_rejects_unversioned_or_foreign(name):
    assert parse("/x/" + name) is None


def _imgs(*names):
    return [parse("/x/" + n) for n in names]


DISK = (
    "FreeCAD_1.0.1-conda-Linux-x86_64-py311.AppImage",
    "FreeCAD_1.0.2-conda-Linux-x86_64-py311.AppImage",
    "FreeCAD_1.1.0-Linux-x86_64-py311.AppImage",
    "FreeCAD_1.1.1-Linux-x86_64-py311.AppImage",
    "FreeCAD_1.1.4-Linux-x86_64-py311.AppImage",
    "FreeCAD_26.3rc2-Linux-x86_64.AppImage",
)


def test_default_is_newest_stable_not_alphabetical():
    # Alphabetically "26.3rc2" > "1.1.4"; numerically 26 > 1 too, but it's an RC.
    assert default_appimage(_imgs(*DISK)).version == (1, 1, 4)


def test_default_moves_to_new_stable_without_code_change():
    imgs = _imgs(*DISK, "FreeCAD_26.3-Linux-x86_64.AppImage")
    assert default_appimage(imgs).version == (26, 3, 0)
    assert not default_appimage(imgs).is_prerelease


def test_default_compares_numerically():
    assert default_appimage(_imgs("FreeCAD_1.9.0-a.AppImage",
                                  "FreeCAD_1.10.0-a.AppImage")).version == (1, 10, 0)


def test_default_falls_back_to_prerelease_when_nothing_else():
    assert default_appimage(_imgs("FreeCAD_26.3rc2-a.AppImage")).pre == "rc2"
    assert default_appimage([]) is None


def test_matrix_oldest_supported_latest_stable_and_newer_prereleases():
    got = [(i.version, i.pre) for i in matrix(_imgs(*DISK), minimum=(1, 0))]
    assert got == [((1, 0, 1), ""), ((1, 1, 4), ""), ((26, 3, 0), "rc2")]


def test_matrix_respects_minimum():
    got = [i.version for i in matrix(_imgs(*DISK), minimum=(1, 1))]
    assert got == [(1, 1, 0), (1, 1, 4), (26, 3, 0)]


def test_matrix_skips_stale_prereleases():
    # 1.1.0rc1 is older than the 1.1.4 stable: nothing to learn from it.
    imgs = _imgs("FreeCAD_1.1.0rc1-a.AppImage", "FreeCAD_1.1.4-a.AppImage")
    assert [i.pre for i in matrix(imgs, minimum=(1, 0))] == [""]


def test_matrix_single_stable_listed_once():
    assert len(matrix(_imgs("FreeCAD_1.1.4-a.AppImage"), minimum=(1, 0))) == 1


def test_discover_needs_executable_files(tmp_path):
    good = tmp_path / "FreeCAD_1.1.4-Linux-x86_64.AppImage"
    noexec = tmp_path / "FreeCAD_1.1.1-Linux-x86_64.AppImage"
    other = tmp_path / "notes.txt"
    for f in (good, noexec, other):
        f.write_text("")
    os.chmod(good, 0o755)
    assert [i.path for i in discover(str(tmp_path))] == [str(good)]


def test_run_matrix_rc_failure_warns_but_stable_failure_blocks():
    imgs = _imgs("FreeCAD_1.1.4-a.AppImage", "FreeCAD_26.3rc2-a.AppImage")
    lines = []
    rc_fails = {"FreeCAD_26.3rc2-a.AppImage": 1}
    run = lambda img: rc_fails.get(os.path.basename(img.path), 0)
    assert run_matrix(imgs, run, out=lines.append) == 0
    assert any("WARNING" in line and "26.3rc2" in line for line in lines)

    stable_fails = {"FreeCAD_1.1.4-a.AppImage": 1}
    run = lambda img: stable_fails.get(os.path.basename(img.path), 0)
    assert run_matrix(imgs, run, out=lines.append) == 1


def test_run_matrix_without_appimages_fails():
    assert run_matrix([], lambda img: 0, out=lambda s: None) == 1
