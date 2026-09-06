"""Utility → profile mapping, as the dialog collects it.

_collect_utility_profiles takes what the dropdowns hold and produces the
config dict, so the filtering rule (inherit is stored as absent, not as a
dangling name) is testable without Qt.
"""

import pytest

# settings_dialog/chat_widget import through ui/compat.py, which needs Qt.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


class TestUtilityIdentifiers:
    def test_all_four_are_offered(self):
        assert {u for u, _ in SettingsDialog.UTILITIES} == {
            "compaction", "skill_eval", "tool_optimize", "rerank"}

    def test_each_has_a_label(self):
        assert all(label for _, label in SettingsDialog.UTILITIES)

    def test_each_label_is_extractable_for_translation(self):
        """translations/update_translations.sh runs pylupdate5, which
        extracts string *literals* only. The labels are translated at the
        use site through a loop variable, so the literal pylupdate sees has
        to be here, in the table — otherwise these four strings can never
        be translated, however correct the runtime translate() call looks.
        """
        import inspect
        from freecad_ai.ui import settings_dialog

        source = inspect.getsource(settings_dialog)
        for _utility, label in SettingsDialog.UTILITIES:
            assert f'QT_TRANSLATE_NOOP("SettingsDialog", "{label}")' in source

    def test_the_noop_leaves_the_label_untouched_at_runtime(self):
        from freecad_ai.ui.compat import QT_TRANSLATE_NOOP
        assert QT_TRANSLATE_NOOP("SettingsDialog", "Tool reranking") == \
            "Tool reranking"


class TestCollect:
    def test_inherit_is_stored_as_absent(self):
        """An empty selection means inherit; storing it as a key with an
        empty value would work but leaves noise in config.json."""
        assert SettingsDialog._collect_utility_profiles(
            {"compaction": "", "rerank": ""}) == {}

    def test_explicit_choices_are_kept(self):
        assert SettingsDialog._collect_utility_profiles(
            {"compaction": "cheap", "rerank": ""}) == {
                "compaction": "cheap"}

    def test_unknown_utilities_are_dropped(self):
        """Defends the config against a stale key from a future version
        being written back by an older one."""
        assert SettingsDialog._collect_utility_profiles(
            {"compaction": "cheap", "not_a_utility": "x"}) == {
                "compaction": "cheap"}
