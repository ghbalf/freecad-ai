"""The wiki's Settings screenshots go stale silently. This is the tripwire.

``Configuration.md`` embeds three PNGs -- ``settings-dialog.png``,
``-2`` and ``-3`` -- which are scroll positions of one tall dialog.
Nothing connects them to the code, so a new widget makes the wiki wrong
and no test, linter or reviewer notices. It has happened twice: the
per-profile capabilities work moved "Model supports vision" out of
Behavior, and #47 added two checkboxes to the middle of that same group
while the prose described settings the picture did not show.

So this records what each group box held when the shots were last taken
and fails when that changes. It is a documentation tripwire, not a
behavioural test: the right response to a failure is to retake the named
image and update ``RECORDED`` in the same commit, never to loosen the
assertion.

Retaking needs no FreeCAD and no GUI -- ``SettingsDialog`` builds under
plain PySide6. Point ``FREECAD_AI_CONFIG_DIR`` at a throwaway directory
first, or the capture publishes the live API key and MCP bearer token::

    env PYTHONPATH=. FREECAD_AI_CONFIG_DIR=/tmp/fake QT_QPA_PLATFORM=offscreen \
        python -c "..."   # resize(619, 1150), scroll, then widget.grab().save()

Scroll each group to the top of the viewport with
``gb.mapTo(area.widget(), gb.rect().topLeft()).y()``.
"""

import os

import pytest

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
except ImportError:
    try:
        from PySide2 import QtWidgets  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402

# Which shot shows which group, from the captions in Configuration.md.
# A group in two shots means both have to be retaken.
WIKI_IMAGE = {
    "LLM Provider": ("settings-dialog.png",),
    "Utility models": ("settings-dialog.png",),
    "Model Parameters": ("settings-dialog.png",),
    "System Prompt": ("settings-dialog.png",),
    "Behavior": ("settings-dialog-2.png",),
    "Tool Reranking": ("settings-dialog-2.png",),
    "MCP Servers": ("settings-dialog-2.png", "settings-dialog-3.png"),
    "Editor": ("settings-dialog-2.png", "settings-dialog-3.png"),
    "User Tools": ("settings-dialog-2.png", "settings-dialog-3.png"),
    "Skills": ("settings-dialog-3.png",),
    "Hooks": ("settings-dialog-3.png",),
}

# Checkbox labels are spelled out because they are what a reader compares
# against the picture; the rest are counts, which is enough to catch a row
# appearing or leaving without pinning every string in the dialog.
RECORDED = {
    "LLM Provider": {
        "checkboxes": ("Use this profile for chat", "Model supports vision"),
        "combos": 2, "spins": 0, "fields": 3, "buttons": 4},
    "Utility models": {
        "checkboxes": (), "combos": 4, "spins": 0, "fields": 0, "buttons": 0},
    "Model Parameters": {
        "checkboxes": (), "combos": 0, "spins": 4, "fields": 0, "buttons": 3},
    "System Prompt": {
        "checkboxes": (), "combos": 0, "spins": 0, "fields": 0, "buttons": 1},
    "Behavior": {
        "checkboxes": (
            "Model supports tool calling (uncheck to fall back to code "
            "generation)",
            "Auto-execute code in Act mode (skip confirmation dialog)",
            "Keep chat panel open when switching workbenches",
            "Strip thinking from conversation history",
            "Keep model reasoning in conversation history",
            "Optimize prompt for caching (may change replies)",
            "Log token usage to the Report view",
        ),
        "combos": 3, "spins": 0, "fields": 0, "buttons": 0},
    "Tool Reranking": {
        "checkboxes": (), "combos": 1, "spins": 1, "fields": 1, "buttons": 1},
    "MCP Servers": {
        "checkboxes": (), "combos": 0, "spins": 0, "fields": 4, "buttons": 5},
    "Editor": {
        "checkboxes": (
            "Open hooks and user tools in the OS-default editor (instead of "
            "FreeCAD's docked script editor)",
        ),
        "combos": 0, "spins": 0, "fields": 0, "buttons": 0},
    "User Tools": {
        "checkboxes": ("Also scan FreeCAD macro directory",),
        "combos": 0, "spins": 0, "fields": 0, "buttons": 5},
    "Skills": {
        "checkboxes": (), "combos": 0, "spins": 0, "fields": 0, "buttons": 2},
    "Hooks": {
        "checkboxes": (), "combos": 0, "spins": 0, "fields": 0, "buttons": 5},
}


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


def _fields(group):
    """Text boxes the user types in.

    Every spin box owns an internal QLineEdit, so an unfiltered count
    silently means "real fields plus spin boxes" -- a number nobody could
    reconcile with the picture.
    """
    return [e for e in group.findChildren(QtWidgets.QLineEdit)
            if not isinstance(e.parent(), QtWidgets.QAbstractSpinBox)]


def _inventory(group):
    return {
        "checkboxes": tuple(
            w.text() for w in group.findChildren(QtWidgets.QCheckBox)),
        "combos": len(group.findChildren(QtWidgets.QComboBox)),
        "spins": len(group.findChildren(QtWidgets.QAbstractSpinBox)),
        "fields": len(_fields(group)),
        "buttons": len(group.findChildren(QtWidgets.QPushButton)),
    }


def _groups(dialog):
    return {g.title(): g for g in dialog.findChildren(QtWidgets.QGroupBox)}


class TestTheScreenshotsStillMatchTheDialog:

    def test_the_groups_are_the_ones_the_shots_were_taken_of(self, dialog):
        assert sorted(_groups(dialog)) == sorted(RECORDED), (
            "the Settings dialog gained or lost a group box, so every wiki "
            "screenshot below it has shifted -- retake all three")

    @pytest.mark.parametrize("title", sorted(RECORDED))
    def test_a_group_holds_what_it_held_when_the_shot_was_taken(
            self, dialog, title):
        group = _groups(dialog).get(title)
        if group is None:
            pytest.skip("covered by the group-list test")

        assert _inventory(group) == RECORDED[title], (
            "%r no longer matches the wiki screenshot. Retake %s in the "
            "wiki repo and update RECORDED in this file, in the same "
            "commit. The recipe is in this module's docstring."
            % (title, " and ".join(WIKI_IMAGE[title])))


class TestTheTripwireItself:
    """A tripwire that can quietly stop covering things is worse than none."""

    def test_every_recorded_group_names_the_image_that_shows_it(self):
        assert sorted(WIKI_IMAGE) == sorted(RECORDED), (
            "a new group needs an entry in WIKI_IMAGE saying which "
            "screenshot has to be retaken when it changes")

    def test_the_caching_switches_are_where_the_wiki_says_they_are(
            self, dialog):
        """#47's two switches are documented as Behavior settings."""
        labels = _inventory(_groups(dialog)["Behavior"])["checkboxes"]

        assert any("caching" in t for t in labels)
        assert any("token usage" in t for t in labels)
