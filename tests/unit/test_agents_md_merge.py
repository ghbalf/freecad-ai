"""Global and project AGENTS.md files: replace, or concatenate (#94).

The loader has always returned the *first* file it found, so a project
`AGENTS.md` next to the .FCStd silently stops the user's global one from
being sent -- no warning, the prompt just gets smaller. The two files are
different scopes, not alternatives: the config-dir copy holds facts about
the user, the project copy holds facts about the project.

``merge_agents_md`` opts into concatenating them instead. It defaults to
False, so an existing install behaves exactly as it did before.
"""

import pytest

from freecad_ai.config import AppConfig
from freecad_ai.extensions import agents_md as mod
from freecad_ai.extensions.agents_md import load_agents_md


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A config dir, a project root, and a document dir two levels down.

    Returns the three paths; each test writes whichever files it needs.
    """
    config_dir = tmp_path / "userconfig"
    project = tmp_path / "work" / "enclosure"
    doc_dir = project / "parts"
    config_dir.mkdir()
    doc_dir.mkdir(parents=True)

    monkeypatch.setattr(mod, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(mod, "_get_document_directory", lambda: str(doc_dir))
    # Keep {{...}} substitution out of the assertions.
    monkeypatch.setattr(mod, "_get_variables", dict)
    return config_dir, project, doc_dir


class TestTheDefault:

    def test_it_ships_off(self):
        assert AppConfig().merge_agents_md is False

    def test_a_config_written_before_this_release_still_loads(self):
        assert AppConfig.from_dict({"max_tokens": 20000}).merge_agents_md is False


class TestReplaceIsUnchanged:
    """The pre-#94 behaviour, pinned so the new mode cannot disturb it."""

    def test_the_project_file_wins(self, tree):
        config_dir, _, doc_dir = tree
        (config_dir / "AGENTS.md").write_text("GLOBAL")
        (doc_dir / "AGENTS.md").write_text("PROJECT")

        result = load_agents_md(merge=False)

        assert "PROJECT" in result
        assert "GLOBAL" not in result

    def test_the_global_file_is_used_when_no_project_file_exists(self, tree):
        config_dir, _, _ = tree
        (config_dir / "AGENTS.md").write_text("GLOBAL")

        assert "GLOBAL" in load_agents_md(merge=False)

    def test_nothing_anywhere_is_an_empty_string(self, tree):
        assert load_agents_md(merge=False) == ""


class TestMergeConcatenates:

    def test_both_files_are_sent(self, tree):
        config_dir, _, doc_dir = tree
        (config_dir / "AGENTS.md").write_text("GLOBAL")
        (doc_dir / "AGENTS.md").write_text("PROJECT")

        result = load_agents_md(merge=True)

        assert "GLOBAL" in result
        assert "PROJECT" in result

    def test_the_most_specific_file_comes_last(self, tree):
        """Later text in a prompt carries more weight, so the project file
        must still be able to override a global default -- which is what
        the old shadowing achieved by brute force."""
        config_dir, _, doc_dir = tree
        (config_dir / "AGENTS.md").write_text("GLOBAL")
        (doc_dir / "AGENTS.md").write_text("PROJECT")

        result = load_agents_md(merge=True)

        assert result.index("GLOBAL") < result.index("PROJECT")

    def test_a_parent_directory_sits_between_them(self, tree):
        config_dir, project, doc_dir = tree
        (config_dir / "AGENTS.md").write_text("GLOBAL")
        (project / "AGENTS.md").write_text("WORKSPACE")
        (doc_dir / "AGENTS.md").write_text("PROJECT")

        result = load_agents_md(merge=True)

        assert (result.index("GLOBAL") < result.index("WORKSPACE")
                < result.index("PROJECT"))

    def test_the_parts_are_separated(self, tree):
        """Two files run together would join a trailing bullet to a heading."""
        config_dir, _, doc_dir = tree
        (config_dir / "AGENTS.md").write_text("- global rule")
        (doc_dir / "AGENTS.md").write_text("# Project")

        assert "- global rule\n\n# Project" in load_agents_md(merge=True)

    def test_one_file_merges_to_itself(self, tree):
        config_dir, _, _ = tree
        (config_dir / "AGENTS.md").write_text("GLOBAL")

        assert load_agents_md(merge=True).strip() == "GLOBAL"

    def test_nothing_anywhere_is_still_an_empty_string(self, tree):
        assert load_agents_md(merge=True) == ""

    def test_still_only_one_filename_per_directory(self, tree):
        """AGENTS.md keeps priority; merging is across directories only."""
        config_dir, _, _ = tree
        (config_dir / "AGENTS.md").write_text("PREFERRED")
        (config_dir / "FREECAD_AI.md").write_text("IGNORED")

        result = load_agents_md(merge=True)

        assert "PREFERRED" in result
        assert "IGNORED" not in result


class TestIncludesResolvePerFile:
    """The trap: one base directory for all of them.

    Both files below include `shared.md` by the same relative name, and
    each has its own copy next to it. Resolving both against a single base
    directory would give one file the other's text, or lose it entirely.
    """

    def test_each_file_gets_its_own_neighbour(self, tree):
        config_dir, _, doc_dir = tree
        (config_dir / "AGENTS.md").write_text(
            "global: <!-- include: shared.md -->")
        (config_dir / "shared.md").write_text("GLOBAL-SHARED")
        (doc_dir / "AGENTS.md").write_text(
            "project: <!-- include: shared.md -->")
        (doc_dir / "shared.md").write_text("PROJECT-SHARED")

        result = load_agents_md(merge=True)

        assert "global: GLOBAL-SHARED" in result
        assert "project: PROJECT-SHARED" in result

    def test_replace_mode_resolves_against_the_file_it_picked(self, tree):
        config_dir, _, doc_dir = tree
        (config_dir / "shared.md").write_text("GLOBAL-SHARED")
        (doc_dir / "AGENTS.md").write_text(
            "project: <!-- include: shared.md -->")
        (doc_dir / "shared.md").write_text("PROJECT-SHARED")

        assert "project: PROJECT-SHARED" in load_agents_md(merge=False)


class TestVariablesStillWork:

    def test_substitution_covers_every_merged_part(self, tree, monkeypatch):
        config_dir, _, doc_dir = tree
        monkeypatch.setattr(mod, "_get_variables",
                            lambda: {"document_name": "Widget"})
        (config_dir / "AGENTS.md").write_text("global {{document_name}}")
        (doc_dir / "AGENTS.md").write_text("project {{document_name}}")

        result = load_agents_md(merge=True)

        assert "global Widget" in result
        assert "project Widget" in result


class TestTheConfigIsTheDefaultSource:

    @pytest.mark.parametrize("enabled", [True, False])
    def test_an_unspecified_flag_follows_the_config(self, tree,
                                                    tmp_config_dir, enabled):
        from freecad_ai.config import get_config
        get_config().merge_agents_md = enabled
        config_dir, _, doc_dir = tree
        (config_dir / "AGENTS.md").write_text("GLOBAL")
        (doc_dir / "AGENTS.md").write_text("PROJECT")

        result = load_agents_md()

        assert ("GLOBAL" in result) is enabled
        assert "PROJECT" in result


class TestUnsavedDocuments:

    def test_only_the_global_file_is_reachable(self, tree, monkeypatch):
        """No FileName means no directory chain to walk."""
        config_dir, _, doc_dir = tree
        monkeypatch.setattr(mod, "_get_document_directory", lambda: "")
        (config_dir / "AGENTS.md").write_text("GLOBAL")
        (doc_dir / "AGENTS.md").write_text("PROJECT")

        result = load_agents_md(merge=True)

        assert "GLOBAL" in result
        assert "PROJECT" not in result
