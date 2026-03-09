"""Tests for skills registry — discovery, matching, and execution."""

import os
from unittest.mock import patch

import pytest

from freecad_ai.extensions.skills import Skill, SkillsRegistry


class TestSkillDataclass:
    def test_defaults(self):
        s = Skill(name="test")
        assert s.name == "test"
        assert s.description == ""
        assert s.content == ""
        assert s.trigger == ""
        assert s.has_handler is False


class TestSkillsRegistryLoad:
    def test_loads_skills_from_directory(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))

        reg = SkillsRegistry()
        skill = reg.get_skill("test-skill")
        assert skill is not None
        assert "sample skill" in skill.description.lower()
        assert skill.trigger == "/test-skill"

    def test_detects_handler(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))

        reg = SkillsRegistry()
        skill = reg.get_skill("handled-skill")
        assert skill is not None
        assert skill.has_handler is True

    def test_empty_skills_dir(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "empty_skills"
        skills_dir.mkdir()
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        assert reg.get_available() == []

    def test_missing_skills_dir(self, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", "/nonexistent/skills")

        reg = SkillsRegistry()
        assert reg.get_available() == []

    def test_skips_dir_without_skill_md(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "not-a-skill").mkdir()
        (skills_dir / "not-a-skill" / "readme.txt").write_text("nope")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        assert reg.get_available() == []

    def test_description_from_first_content_line(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "my-skill"
        sd.mkdir()
        (sd / "SKILL.md").write_text("# Title\n\nThis is the description.\n\nMore text.\n")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        skill = reg.get_skill("my-skill")
        assert skill.description == "This is the description."

    def test_description_from_yaml_frontmatter(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "fm-skill"
        sd.mkdir()
        (sd / "SKILL.md").write_text(
            "---\nname: fm-skill\n"
            "description: Create things from frontmatter.\n"
            "---\n\n# FM Skill\n\nBody text here.\n"
        )
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        skill = reg.get_skill("fm-skill")
        assert skill.description == "Create things from frontmatter."

    def test_frontmatter_description_preferred_over_body(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "pref-skill"
        sd.mkdir()
        (sd / "SKILL.md").write_text(
            "---\nname: pref-skill\n"
            "description: From frontmatter\n"
            "---\n\n# Title\n\nFrom body.\n"
        )
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        skill = reg.get_skill("pref-skill")
        assert skill.description == "From frontmatter"

    def test_frontmatter_without_description_falls_back_to_body(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "no-desc"
        sd.mkdir()
        (sd / "SKILL.md").write_text(
            "---\nname: no-desc\n---\n\n# Title\n\nBody description.\n"
        )
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        skill = reg.get_skill("no-desc")
        assert skill.description == "Body description."


class TestRegisterProgrammatic:
    def test_register_skill(self, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", "/nonexistent")

        reg = SkillsRegistry()
        reg.register("custom", content="# Custom\nDo custom things.", trigger="/custom")
        skill = reg.get_skill("custom")
        assert skill is not None
        assert skill.trigger == "/custom"


class TestMatchCommand:
    def _make_registry(self, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", "/nonexistent")
        reg = SkillsRegistry()
        reg.register("gear", content="# Gear", trigger="/gear")
        reg.register("thread-insert", content="# Thread", trigger="/thread-insert")
        return reg

    def test_matches_exact_command(self, monkeypatch):
        reg = self._make_registry(monkeypatch)
        result = reg.match_command("/gear")
        assert result == ("gear", "")

    def test_matches_with_args(self, monkeypatch):
        reg = self._make_registry(monkeypatch)
        result = reg.match_command("/gear module=2 teeth=20")
        assert result == ("gear", "module=2 teeth=20")

    def test_no_match_returns_none(self, monkeypatch):
        reg = self._make_registry(monkeypatch)
        result = reg.match_command("/unknown-command")
        assert result is None

    def test_non_slash_returns_none(self, monkeypatch):
        reg = self._make_registry(monkeypatch)
        result = reg.match_command("just a regular message")
        assert result is None

    def test_matches_hyphenated_command(self, monkeypatch):
        reg = self._make_registry(monkeypatch)
        result = reg.match_command("/thread-insert M3")
        assert result == ("thread-insert", "M3")


class TestExecuteSkill:
    def test_execute_returns_inject_prompt(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))

        reg = SkillsRegistry()
        result = reg.execute_skill("test-skill")
        assert "inject_prompt" in result
        assert "# Test Skill" in result["inject_prompt"]

    def test_execute_calls_handler(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))

        reg = SkillsRegistry()
        result = reg.execute_skill("handled-skill", args="test-args")
        assert "output" in result
        assert "Handled: test-args" in result["output"]

    def test_execute_unknown_skill(self, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", "/nonexistent")

        reg = SkillsRegistry()
        result = reg.execute_skill("nonexistent")
        assert "error" in result

    def test_handler_error_returns_error_dict(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "broken"
        sd.mkdir()
        (sd / "SKILL.md").write_text("# Broken Skill\nA skill that crashes.\n")
        (sd / "handler.py").write_text("def execute(args):\n    raise RuntimeError('boom')\n")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        result = reg.execute_skill("broken")
        assert "error" in result
        assert "boom" in result["error"]


class TestGetDescriptions:
    def test_returns_formatted_string(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))

        reg = SkillsRegistry()
        desc = reg.get_descriptions()
        assert "## Available Skills" in desc
        assert "test-skill" in desc
        assert "/test-skill" in desc

    def test_empty_when_no_skills(self, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", "/nonexistent")

        reg = SkillsRegistry()
        assert reg.get_descriptions() == ""
