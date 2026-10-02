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
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(skills_dir))

        reg = SkillsRegistry()
        assert reg.get_available() == []

    def test_missing_skills_dir(self, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", "/nonexistent/skills")
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", "/nonexistent/builtin")

        reg = SkillsRegistry()
        assert reg.get_available() == []

    def test_skips_dir_without_skill_md(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "not-a-skill").mkdir()
        (skills_dir / "not-a-skill" / "readme.txt").write_text("nope")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(skills_dir))

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
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", "/nonexistent/builtin")

        reg = SkillsRegistry()
        assert reg.get_descriptions() == ""


class TestSkillReferences:
    def _make_skill_with_refs(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "debug-model"
        sd.mkdir()
        (sd / "SKILL.md").write_text("# Debug Model\n\nDiagnose broken models.\n")
        refs = sd / "references"
        refs.mkdir()
        (refs / "freecad-gotchas.md").write_text(
            "# FreeCAD gotchas\n\ngetObjectsByLabel vs getObject, AttachmentSupport.\n"
        )
        (refs / "fix-attachment.md").write_text(
            "# Fix attachment\n\nRe-attach a face or datum plane.\n"
        )
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "nonexistent"))
        return skills_dir

    def test_scans_reference_files_into_dict(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        skill = reg.get_skill("debug-model")
        assert set(skill.references) == {"freecad-gotchas", "fix-attachment"}
        assert skill.references["freecad-gotchas"].endswith(
            os.path.join("references", "freecad-gotchas.md")
        )

    def test_skill_without_references_has_empty_dict(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(mock_skills_dir))
        reg = SkillsRegistry()
        assert reg.get_skill("test-skill").references == {}

    def test_get_resource_returns_file_contents(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("debug-model", "freecad-gotchas")
        assert "output" in result
        assert "AttachmentSupport" in result["output"]

    def test_get_resource_accepts_md_extension(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("debug-model", "fix-attachment.md")
        assert "output" in result
        assert "datum plane" in result["output"]

    def test_get_resource_unknown_key_lists_available(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("debug-model", "nope")
        assert "error" in result
        assert "freecad-gotchas" in result["error"]
        assert "fix-attachment" in result["error"]

    def test_get_resource_skill_without_references(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(mock_skills_dir))
        reg = SkillsRegistry()
        result = reg.get_skill_resource("test-skill", "anything")
        assert "error" in result
        assert "no references" in result["error"].lower()

    def test_get_resource_unknown_skill(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("no-such-skill", "freecad-gotchas")
        assert "error" in result

    def test_get_resource_traversal_is_not_found(self, tmp_path, monkeypatch):
        """Model input is a key, never a path — traversal keys just miss."""
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        for evil in ["../SKILL", "../../conftest", "/etc/passwd", "..\\SKILL"]:
            result = reg.get_skill_resource("debug-model", evil)
            assert "error" in result, f"{evil!r} should not resolve"

    def test_execute_skill_appends_references_manifest(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.execute_skill("debug-model")
        content = result["inject_prompt"]
        assert "Diagnose broken models." in content            # original SKILL.md
        assert "Available references" in content                # manifest heading
        assert "freecad-gotchas" in content and "fix-attachment" in content
        # advertises the exact invocation syntax
        assert "resource='freecad-gotchas'" in content
        # includes a one-line summary drawn from the file
        assert "getObjectsByLabel" in content

    def test_execute_skill_no_manifest_without_references(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(mock_skills_dir))
        reg = SkillsRegistry()
        result = reg.execute_skill("test-skill")
        assert "Available references" not in result["inject_prompt"]


class TestUseSkillResource:
    def _refs_skill(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "debug-model"
        sd.mkdir()
        (sd / "SKILL.md").write_text("# Debug Model\n\nDiagnose broken models.\n")
        refs = sd / "references"
        refs.mkdir()
        (refs / "freecad-gotchas.md").write_text("# Gotchas\n\nAttachmentSupport not Support.\n")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))

    def test_use_skill_resource_returns_file(self, tmp_path, monkeypatch):
        from freecad_ai.tools.freecad_tools import _handle_use_skill
        self._refs_skill(tmp_path, monkeypatch)
        result = _handle_use_skill("debug-model", resource="freecad-gotchas")
        assert result.success is True
        assert "AttachmentSupport" in result.output

    def test_use_skill_resource_unknown_errors(self, tmp_path, monkeypatch):
        from freecad_ai.tools.freecad_tools import _handle_use_skill
        self._refs_skill(tmp_path, monkeypatch)
        result = _handle_use_skill("debug-model", resource="missing")
        assert result.success is False
        assert "freecad-gotchas" in result.error

    def test_use_skill_without_resource_still_loads_skill(self, tmp_path, monkeypatch):
        from freecad_ai.tools.freecad_tools import _handle_use_skill
        self._refs_skill(tmp_path, monkeypatch)
        result = _handle_use_skill("debug-model")
        assert result.success is True
        assert "Diagnose broken models." in result.output


class TestAgentSkillsFrontmatter:
    def _registry(self, tmp_path, monkeypatch, skill_md, name="fm"):
        import freecad_ai.extensions.skills as skills_mod
        sd = tmp_path / "skills" / name
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(skill_md)
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
        return SkillsRegistry()

    def test_long_description_kept_up_to_1024(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             f"---\ndescription: {'x' * 2000}\n---\n# T\n")
        assert len(reg.get_skill("fm").description) == 1024

    def test_prompt_list_shortens_to_300(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             f"---\ndescription: {'y' * 500}\n---\n# T\n")
        text = reg.get_descriptions()
        assert "y" * 299 + "…" in text and "y" * 300 not in text

    def test_folded_description(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             "---\ndescription: >\n  Make gears.\n  Use for spur gears.\n---\n# T\n")
        assert reg.get_skill("fm").description == "Make gears. Use for spur gears."

    def test_injection_is_body_only(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             "---\nname: fm\ndescription: D\n---\n# Body title\nSteps.\n")
        injected = reg.execute_skill("fm")["inject_prompt"]
        assert injected.startswith("# Body title")
        assert "description: D" not in injected
        assert reg.get_skill("fm").content.startswith("---\nname: fm")  # optimizer reads this

    def test_frontmatter_fields_stored(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path, monkeypatch,
                             "---\nname: other-name\ndescription: D\ncompatibility: FreeCAD 1.1\n"
                             "metadata:\n  author: alf\n---\n# T\n")
        skill = reg.get_skill("fm")
        assert skill.trigger == "/fm"                     # folder wins over name:
        assert skill.frontmatter["compatibility"] == "FreeCAD 1.1"
        assert skill.frontmatter["metadata"] == {"author": "alf"}

    def test_status_reads_frontmatter_description(self, tmp_path, monkeypatch):
        self._registry(tmp_path, monkeypatch,
                       "---\ndescription: From frontmatter\ncompatibility: FreeCAD 1.1\n---\n# T\nBody line\n")
        (info,) = SkillsRegistry.get_skill_status()
        assert info["description"] == "From frontmatter"
        assert info["compatibility"] == "FreeCAD 1.1"


class TestSkillFiles:
    def _skill(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        sd = tmp_path / "skills" / "pdfish"
        (sd / "references" / "tables").mkdir(parents=True)
        (sd / "scripts" / "__pycache__").mkdir(parents=True)
        (sd / "assets").mkdir()
        (sd / ".git").mkdir()
        (sd / "SKILL.md").write_text("---\nname: pdfish\ndescription: D\n---\nSee [forms](forms.md).\n")
        (sd / "handler.py").write_text("x = 1\n")
        (sd / "VALIDATION.md").write_text("# V\n")
        (sd / "forms.md").write_text("# Forms\nHow to fill forms.\n")
        (sd / "references" / "m3.md").write_text("# M3\nM3 table.\n")
        (sd / "references" / "tables" / "m4.md").write_text("# M4\nM4 table.\n")
        (sd / "scripts" / "fill.py").write_text("print('fill')\n")
        (sd / "scripts" / "__pycache__" / "fill.pyc").write_bytes(b"\0")
        (sd / "assets" / "logo.png").write_bytes(b"\x89PNG\0\0data")
        (sd / ".hidden").write_text("no")
        (sd / ".git" / "HEAD").write_text("no")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
        return sd

    def test_recursive_keys_and_skip_list(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        skill = SkillsRegistry().get_skill("pdfish")
        assert set(skill.files) == {"forms.md", "references/m3.md",
                                    "references/tables/m4.md", "scripts/fill.py",
                                    "assets/logo.png"}
        assert set(skill.references) == {"m3"}   # legacy: top level of references/ only

    def test_symlink_leaving_the_skill_is_dropped(self, tmp_path, monkeypatch):
        sd = self._skill(tmp_path, monkeypatch)
        secret = tmp_path / "secret.txt"
        secret.write_text("TOKEN")
        os.symlink(secret, sd / "references" / "leak.md")
        os.symlink(sd / "forms.md", sd / "references" / "inside.md")
        skill = SkillsRegistry().get_skill("pdfish")
        assert "references/leak.md" not in skill.files
        assert "references/inside.md" in skill.files

    def test_file_cap(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        self._skill(tmp_path, monkeypatch)
        monkeypatch.setattr(skills_mod, "MAX_SKILL_FILES", 2)
        assert len(SkillsRegistry().get_skill("pdfish").files) == 2

    def test_path_keys_resolve_with_normalisation(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        for key in ["references/tables/m4.md", "./references/tables/m4.md",
                    "references\\tables\\M4.md"]:
            assert "M4 table." in reg.get_skill_resource("pdfish", key)["output"], key
        assert "How to fill" in reg.get_skill_resource("pdfish", "forms.md")["output"]

    def test_legacy_alias_still_resolves(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        assert "M3 table." in SkillsRegistry().get_skill_resource("pdfish", "m3")["output"]

    def test_binary_resource_is_refused_with_size(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        err = SkillsRegistry().get_skill_resource("pdfish", "assets/logo.png")["error"]
        assert "bytes" in err and "script" in err

    def test_large_resource_is_truncated(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        self._skill(tmp_path, monkeypatch)
        monkeypatch.setattr(skills_mod, "MAX_RESOURCE_BYTES", 5)
        out = SkillsRegistry().get_skill_resource("pdfish", "forms.md")["output"]
        assert out.startswith("# For") and "truncated" in out

    def test_unknown_key_lists_at_most_20(self, tmp_path, monkeypatch):
        sd = self._skill(tmp_path, monkeypatch)
        for i in range(30):
            (sd / "references" / f"r{i:02d}.md").write_text("x\n")
        err = SkillsRegistry().get_skill_resource("pdfish", "nope")["error"]
        listed = err.split("Available: ", 1)[1].split(", ")
        assert len(listed) == 21 and listed[-1] == "…and 15 more"   # 35 keys

    def test_manifest_groups(self, tmp_path, monkeypatch):
        self._skill(tmp_path, monkeypatch)
        text = SkillsRegistry().execute_skill("pdfish")["inject_prompt"]
        docs, scripts, assets = (text.index("### Documents"), text.index("### Scripts"),
                                 text.index("### Assets"))
        assert docs < text.index("`forms.md`") < scripts
        assert "How to fill forms." in text
        assert "(resource='m3')" in text                     # legacy hint kept
        assert scripts < text.index("`scripts/fill.py`") < assets
        assert "run_skill_script(skill='pdfish'" in text
        assert assets < text.index("`assets/logo.png`")

    def test_manifest_cap(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        self._skill(tmp_path, monkeypatch)
        monkeypatch.setattr(skills_mod, "MANIFEST_MAX", 2)
        text = SkillsRegistry().execute_skill("pdfish")["inject_prompt"]
        assert "…and 3 more" in text


class TestExtraSkillDirs:
    def _write(self, root, name, desc):
        sd = root / name
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(f"---\ndescription: {desc}\n---\n# {name}\n")

    def _dirs(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        builtin, extra, user = (tmp_path / "builtin", tmp_path / "extra", tmp_path / "user")
        self._write(builtin, "gear", "built-in gear")
        self._write(builtin, "lattice", "built-in lattice")
        self._write(extra, "gear", "external gear")
        self._write(extra, "lattice", "external lattice")
        self._write(user, "lattice", "user lattice")
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(builtin))
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user))
        return extra

    def test_default_is_empty(self):
        from freecad_ai.config import AppConfig
        assert AppConfig().extra_skill_dirs == []
        assert AppConfig.from_dict({"extra_skill_dirs": ["~/x"]}).extra_skill_dirs == ["~/x"]

    def test_precedence_builtin_extra_user(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        reg = SkillsRegistry(extra_dirs=[str(extra)])
        assert reg.get_skill("gear").description == "external gear"
        assert reg.get_skill("lattice").description == "user lattice"

    def test_reads_config_when_not_given(self, tmp_path, monkeypatch):
        import freecad_ai.config as config_mod
        extra = self._dirs(tmp_path, monkeypatch)
        cfg = config_mod.AppConfig()
        cfg.extra_skill_dirs = [str(extra)]
        monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
        assert SkillsRegistry().get_skill("gear").description == "external gear"

    def test_tilde_expanded_and_missing_dir_skipped(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        reg = SkillsRegistry(extra_dirs=["~/extra", str(tmp_path / "missing")])
        assert reg.get_skill("gear").description == "external gear"

    def test_string_instead_of_list_is_one_dir(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        reg = SkillsRegistry(extra_dirs=str(extra))
        assert reg.get_skill("gear").description == "external gear"

    def test_extra_dir_that_is_itself_a_skill(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        self._write(tmp_path / "solo", "my-skill", "solo skill")
        reg = SkillsRegistry(extra_dirs=[str(tmp_path / "solo" / "my-skill")])
        assert reg.get_skill("my-skill").description == "solo skill"

    def test_status_marks_external(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        status = {s["name"]: s for s in SkillsRegistry.get_skill_status(extra_dirs=[str(extra)])}
        assert status["gear"]["source"] == "external"
        assert status["gear"]["description"] == "external gear"
        assert status["gear"]["has_user_copy"] is False   # Reset stays disabled
        assert status["lattice"]["source"] == "modified"  # user copy still wins

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root can read any dir")
    def test_unreadable_extra_dir_is_skipped(self, tmp_path, monkeypatch):
        extra = self._dirs(tmp_path, monkeypatch)
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0)
        try:
            reg = SkillsRegistry(extra_dirs=[str(locked), str(extra)])
            assert reg.get_skill("gear").description == "external gear"
            assert reg.get_skill("lattice").description == "user lattice"
            status = SkillsRegistry.get_skill_status(extra_dirs=[str(locked)])
            assert "gear" in {s["name"] for s in status}
        finally:
            locked.chmod(0o755)


class TestFinalReviewFixes:
    def _reg(self, tmp_path, monkeypatch, name="s"):
        import freecad_ai.extensions.skills as skills_mod
        sd = tmp_path / "skills" / name
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("# S\nBody.\n")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))
        return sd

    def test_optimizer_backup_is_not_a_skill_file(self, tmp_path, monkeypatch):
        sd = self._reg(tmp_path, monkeypatch)
        (sd / "SKILL.md.original").write_text("old\n")
        skill = SkillsRegistry().get_skill("s")
        assert "SKILL.md.original" not in skill.files
        assert "SKILL.md.original" not in SkillsRegistry().render_references_manifest(skill)

    def test_truncated_latin1_is_not_silently_corrupted(self, tmp_path, monkeypatch):
        sd = self._reg(tmp_path, monkeypatch)
        (sd / "big.txt").write_bytes(b"caf\xe9 " * 30000)
        result = SkillsRegistry().get_skill_resource("s", "big.txt")
        assert "error" in result and "binary" in result["error"]

    def test_truncated_utf8_cut_mid_character_is_kept_clean(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        sd = self._reg(tmp_path, monkeypatch)
        n = skills_mod.MAX_RESOURCE_BYTES
        (sd / "big.txt").write_bytes(b"a" * (n - 1) + "é".encode() + b"tail" * 10)
        out = SkillsRegistry().get_skill_resource("s", "big.txt")["output"]
        assert out.startswith("a" * (n - 1)) and "�" not in out

    def test_cross_drive_commonpath_is_treated_as_outside(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        sd = self._reg(tmp_path, monkeypatch)
        (sd / "f.md").write_text("x")
        def boom(paths):
            raise ValueError("Paths don't have the same drive")
        monkeypatch.setattr(skills_mod.os.path, "commonpath", boom)
        skill = SkillsRegistry().get_skill("s")  # must not raise
        assert skill is not None and "f.md" not in skill.files
