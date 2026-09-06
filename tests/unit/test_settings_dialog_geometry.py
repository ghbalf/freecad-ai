"""The Settings dialog must open wide enough to show everything on it.

Geometry is one of the few things no fake-self test can reach: the bug
this guards against (the profile row's Delete button falling off the right
edge) is invisible to every assertion that does not lay real widgets out.
"""

import os

import pytest

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtWidgets  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def dialog(qapp, tmp_config_dir):
    dlg = SettingsDialog()
    dlg.show()
    qapp.processEvents()
    yield dlg
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def _right_edge_in(widget, container):
    return widget.mapTo(
        container, QtCore.QPoint(widget.width(), 0)).x()


@pytest.mark.parametrize(
    "button", ["profile_add_btn", "profile_rename_btn", "profile_delete_btn"])
def test_every_profile_button_is_visible_at_the_default_size(dialog, button):
    # Delete is the rightmost and so the first to be clipped, but the row is
    # translated: in another locale a different one runs off the edge first.
    btn = getattr(dialog, button)
    assert _right_edge_in(btn, dialog) <= dialog.width(), (
        "%s extends past the dialog's right edge" % button)


def test_the_default_size_is_at_least_what_the_layout_asks_for(dialog):
    # The regression was a hardcoded resize() that had fallen behind the
    # content it was sizing, so compare against the layout's own answer
    # rather than against another constant.
    assert dialog.width() >= dialog.sizeHint().width()


def test_the_content_fits_without_a_horizontal_scrollbar(dialog):
    areas = dialog.findChildren(QtWidgets.QScrollArea)
    assert areas, "expected the settings body to be scrollable"
    for area in areas:
        assert (area.horizontalScrollBar().isVisible() is False
                or area.widget().sizeHint().width()
                <= area.viewport().width()), (
            "settings content is wider than its viewport")
