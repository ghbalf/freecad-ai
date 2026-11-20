"""The Settings dialog reopens at the size it was closed at.

Real widgets, laid out offscreen: the size a dialog ends up at depends on
its minimum width (#78) and on the screen, neither of which a fake reaches.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
except ImportError:
    try:
        from PySide2 import QtWidgets  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from unittest.mock import patch  # noqa: E402

from freecad_ai import config as config_mod  # noqa: E402
from freecad_ai.ui import settings_dialog as sd  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_config_dir, monkeypatch):
    """A config loaded from the temp dir, not one left by an earlier test."""
    monkeypatch.setattr(config_mod, "_config", None)
    return tmp_config_dir


def _open(qapp):
    dlg = sd.SettingsDialog()
    dlg.show()
    qapp.processEvents()
    return dlg


def _close(qapp, dlg, how="reject"):
    getattr(dlg, how)()
    dlg.deleteLater()
    qapp.processEvents()


def _avail(dlg):
    return dlg.screen().availableGeometry()


def test_first_open_keeps_the_layout_default(qapp, tmp_config_dir):
    dlg = _open(qapp)
    try:
        assert dlg.width() == dlg.minimumWidth()
        assert dlg.height() == min(700, _avail(dlg).height())
    finally:
        _close(qapp, dlg)


@pytest.mark.parametrize("how", ["reject", "accept"])
def test_closing_remembers_the_size(qapp, tmp_config_dir, how):
    dlg = _open(qapp)
    w, h = dlg.minimumWidth() + 50, 520
    dlg.resize(w, h)
    qapp.processEvents()
    _close(qapp, dlg, how)
    assert config_mod.get_config().settings_dialog_size == [w, h]
    # On disk too: Cancel must not lose it either.
    config_mod.reload_config()
    assert config_mod.get_config().settings_dialog_size == [w, h]

    dlg = _open(qapp)
    try:
        assert (dlg.width(), dlg.height()) == (w, h)
    finally:
        _close(qapp, dlg)


def test_an_unchanged_size_does_not_rewrite_the_config(qapp, tmp_config_dir):
    dlg = _open(qapp)
    dlg.resize(dlg.minimumWidth() + 10, 500)
    qapp.processEvents()
    _close(qapp, dlg)
    dlg = _open(qapp)
    with patch.object(sd, "save_current_config") as save:
        _close(qapp, dlg)
    save.assert_not_called()


def test_a_saved_width_below_the_layout_minimum_is_widened(qapp, tmp_config_dir):
    """#78: the profile row must fit, whatever was saved by an older layout."""
    config_mod.get_config().settings_dialog_size = [200, 500]
    dlg = _open(qapp)
    try:
        assert dlg.width() == dlg.minimumWidth()
        assert dlg.height() == 500
    finally:
        _close(qapp, dlg)


def test_a_saved_size_larger_than_the_screen_is_shrunk(qapp, tmp_config_dir):
    """Saved on a big monitor, reopened on a laptop."""
    config_mod.get_config().settings_dialog_size = [20000, 20000]
    dlg = _open(qapp)
    try:
        avail = _avail(dlg)
        assert dlg.height() <= avail.height()
        assert dlg.width() <= max(avail.width(), dlg.minimumWidth())
    finally:
        _close(qapp, dlg)


@pytest.mark.parametrize("bad", [[], [800], "800x600", [0, 0], [-5, 300],
                                 ["a", "b"], None])
def test_a_hand_edited_size_that_makes_no_sense_is_ignored(
        qapp, tmp_config_dir, bad):
    config_mod.get_config().settings_dialog_size = bad
    dlg = _open(qapp)
    try:
        assert dlg.width() == dlg.minimumWidth()
        assert dlg.height() == min(700, _avail(dlg).height())
    finally:
        _close(qapp, dlg)
