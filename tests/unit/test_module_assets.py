"""Tests for module asset discovery — modules shipping their own AI assets."""

import os

import pytest

from freecad_ai.extensions import module_assets


@pytest.fixture(autouse=True)
def reset_asset_cache():
    """Discovery is cached process-wide; clear it around every test."""
    module_assets.reset_cache()
    yield
    module_assets.reset_cache()


@pytest.fixture
def fake_mod_tree(tmp_path, monkeypatch):
    """Build a Mod/ tree with two asset-bearing modules and one plain module."""
    mod = tmp_path / "Mod"
    for name in ("AlphaWB", "BetaWB"):
        (mod / name / "ai" / "skills").mkdir(parents=True)
        (mod / name / "ai" / "tools").mkdir(parents=True)
    (mod / "PlainWB").mkdir(parents=True)  # no ai/ dir — ships no assets

    monkeypatch.setattr(module_assets, "_mod_roots", lambda: [str(mod)])
    return mod


class TestDiscovery:
    def test_finds_asset_dirs(self, fake_mod_tree):
        found = module_assets.discover_asset_dirs()
        assert [os.path.basename(os.path.dirname(p)) for p in found] == [
            "AlphaWB",
            "BetaWB",
        ]

    def test_module_without_ai_dir_is_skipped(self, fake_mod_tree):
        found = module_assets.discover_asset_dirs()
        assert not any("PlainWB" in p for p in found)

    def test_no_freecad_yields_nothing(self, monkeypatch):
        """Discovery is silent outside FreeCAD rather than raising."""
        monkeypatch.delenv(module_assets.ASSET_DIRS_ENV, raising=False)
        assert module_assets.discover_asset_dirs() == []

    def test_env_var_adds_dirs(self, tmp_path, monkeypatch):
        dev_tree = tmp_path / "checkout" / "ai"
        dev_tree.mkdir(parents=True)
        monkeypatch.setenv(module_assets.ASSET_DIRS_ENV, str(dev_tree))
        monkeypatch.setattr(module_assets, "_mod_roots", lambda: [])

        assert module_assets.discover_asset_dirs() == [str(dev_tree)]

    def test_env_var_accepts_multiple_paths(self, tmp_path, monkeypatch):
        first = tmp_path / "one" / "ai"
        second = tmp_path / "two" / "ai"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        monkeypatch.setenv(
            module_assets.ASSET_DIRS_ENV,
            os.pathsep.join([str(first), str(second)]),
        )
        monkeypatch.setattr(module_assets, "_mod_roots", lambda: [])

        assert module_assets.discover_asset_dirs() == [str(first), str(second)]

    def test_nonexistent_env_paths_are_dropped(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            module_assets.ASSET_DIRS_ENV, str(tmp_path / "does-not-exist")
        )
        monkeypatch.setattr(module_assets, "_mod_roots", lambda: [])

        assert module_assets.discover_asset_dirs() == []

    def test_result_is_cached(self, fake_mod_tree):
        first = module_assets.discover_asset_dirs()
        (fake_mod_tree / "GammaWB" / "ai").mkdir(parents=True)

        assert module_assets.discover_asset_dirs() == first
        assert len(module_assets.discover_asset_dirs(refresh=True)) == 3

    def test_asset_subdirs_filters_missing(self, fake_mod_tree):
        (fake_mod_tree / "AlphaWB" / "ai" / "hooks").mkdir()

        hooks = module_assets.asset_subdirs("hooks")
        assert len(hooks) == 1
        assert "AlphaWB" in hooks[0]
        assert len(module_assets.asset_subdirs("skills")) == 2

    def test_owning_module(self, fake_mod_tree):
        asset_dir = module_assets.discover_asset_dirs()[0]
        assert module_assets.owning_module(asset_dir) == "AlphaWB"


class TestInstructions:
    def test_collects_and_labels_instructions(self, fake_mod_tree):
        (fake_mod_tree / "AlphaWB" / "ai" / "INSTRUCTIONS.md").write_text(
            "Prefer alpha_* tools.", encoding="utf-8"
        )
        (fake_mod_tree / "BetaWB" / "ai" / "INSTRUCTIONS.md").write_text(
            "Beta objects need a Program first.", encoding="utf-8"
        )

        text = module_assets.load_module_instructions()
        assert "### AlphaWB" in text
        assert "Prefer alpha_* tools." in text
        assert "### BetaWB" in text
        assert text.index("AlphaWB") < text.index("BetaWB")

    def test_no_instructions_yields_empty(self, fake_mod_tree):
        assert module_assets.load_module_instructions() == ""

    def test_empty_file_contributes_nothing(self, fake_mod_tree):
        (fake_mod_tree / "AlphaWB" / "ai" / "INSTRUCTIONS.md").write_text(
            "   \n", encoding="utf-8"
        )
        assert module_assets.load_module_instructions() == ""


class TestSkillsIntegration:
    def test_module_skills_load(self, fake_mod_tree, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod

        skill = fake_mod_tree / "AlphaWB" / "ai" / "skills" / "alpha-thing"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\ndescription: Build an alpha thing\n---\n\n# Alpha\n",
            encoding="utf-8",
        )
        user_dir = tmp_path / "user-skills"
        user_dir.mkdir()
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user_dir))

        registry = skills_mod.SkillsRegistry()
        loaded = registry.get_skill("alpha-thing")
        assert loaded is not None
        assert loaded.description == "Build an alpha thing"
        assert loaded.trigger == "/alpha-thing"

    def test_user_skill_shadows_module(self, fake_mod_tree, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod

        module_skill = fake_mod_tree / "AlphaWB" / "ai" / "skills" / "shared"
        module_skill.mkdir()
        (module_skill / "SKILL.md").write_text(
            "---\ndescription: From the module\n---\n", encoding="utf-8"
        )

        user_dir = tmp_path / "user-skills"
        (user_dir / "shared").mkdir(parents=True)
        (user_dir / "shared" / "SKILL.md").write_text(
            "---\ndescription: From the user\n---\n", encoding="utf-8"
        )
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user_dir))

        registry = skills_mod.SkillsRegistry()
        assert registry.get_skill("shared").description == "From the user"

    def test_status_reports_module_source(self, fake_mod_tree, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod

        module_skill = fake_mod_tree / "AlphaWB" / "ai" / "skills" / "alpha-thing"
        module_skill.mkdir()
        (module_skill / "SKILL.md").write_text(
            "---\ndescription: Build an alpha thing\n---\n", encoding="utf-8"
        )
        user_dir = tmp_path / "user-skills"
        user_dir.mkdir()
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user_dir))

        entry = next(
            info
            for info in skills_mod.SkillsRegistry.get_skill_status()
            if info["name"] == "alpha-thing"
        )
        assert entry["source"] == "module"
        assert entry["module_path"].endswith("SKILL.md")
        assert entry["builtin_path"] == ""
        assert entry["has_user_copy"] is False

    def test_status_flags_modified_module_skill(
        self, fake_mod_tree, tmp_path, monkeypatch
    ):
        import freecad_ai.extensions.skills as skills_mod

        module_skill = fake_mod_tree / "AlphaWB" / "ai" / "skills" / "shared"
        module_skill.mkdir()
        (module_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

        user_dir = tmp_path / "user-skills"
        (user_dir / "shared").mkdir(parents=True)
        (user_dir / "shared" / "SKILL.md").write_text("edited\n", encoding="utf-8")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user_dir))

        entry = next(
            info
            for info in skills_mod.SkillsRegistry.get_skill_status()
            if info["name"] == "shared"
        )
        assert entry["source"] == "modified"
        assert entry["is_modified"] is True

    def test_reset_reverts_to_module_copy(self, fake_mod_tree, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod

        module_skill = fake_mod_tree / "AlphaWB" / "ai" / "skills" / "shared"
        module_skill.mkdir()
        (module_skill / "SKILL.md").write_text("original\n", encoding="utf-8")

        user_dir = tmp_path / "user-skills"
        (user_dir / "shared").mkdir(parents=True)
        (user_dir / "shared" / "SKILL.md").write_text("edited\n", encoding="utf-8")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user_dir))

        assert skills_mod.SkillsRegistry.reset_to_builtin("shared") is True
        assert not (user_dir / "shared").exists()

    def test_reset_refuses_when_nothing_ships_the_skill(self, tmp_path, monkeypatch):
        """A purely-user skill has no shipped version to fall back to."""
        import freecad_ai.extensions.skills as skills_mod

        monkeypatch.setattr(module_assets, "_mod_roots", lambda: [])
        user_dir = tmp_path / "user-skills"
        (user_dir / "mine").mkdir(parents=True)
        (user_dir / "mine" / "SKILL.md").write_text("mine\n", encoding="utf-8")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(user_dir))

        assert skills_mod.SkillsRegistry.reset_to_builtin("mine") is False
        assert (user_dir / "mine").exists()


class TestToolsIntegration:
    def test_module_tools_register(self, fake_mod_tree, tmp_path, monkeypatch):
        import freecad_ai.config as config_mod

        (fake_mod_tree / "AlphaWB" / "ai" / "tools" / "alpha_tools.py").write_text(
            '__tool_prefix__ = "alpha_"\n'
            "\n"
            "def make_widget(size: float) -> str:\n"
            '    """Make a widget."""\n'
            '    return "made"\n',
            encoding="utf-8",
        )
        empty = tmp_path / "no-user-tools"
        empty.mkdir()
        monkeypatch.setattr(config_mod, "USER_TOOLS_DIR", str(empty))

        from freecad_ai.tools.setup import create_default_registry

        registry = create_default_registry(include_mcp=False)
        assert registry.get("alpha_make_widget") is not None
        assert registry.get("user_make_widget") is None


class TestHooksIntegration:
    def test_module_hooks_load(self, fake_mod_tree, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod

        hook_dir = fake_mod_tree / "AlphaWB" / "ai" / "hooks" / "alpha-guard"
        hook_dir.mkdir(parents=True)
        (hook_dir / "hook.py").write_text(
            "def on_pre_tool_use(context):\n"
            '    return {"block": True, "reason": "nope"}\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(tmp_path / "no-user-hooks"))

        registry = hooks_mod.HookRegistry()
        result = registry.fire("pre_tool_use", {"tool_name": "x"})
        assert result.get("block") is True


class TestModRoots:
    """_mod_roots is the one part discovery cannot fake, so it gets its own tests.

    A branded build can return a getResourceDir() that is not the module root - RioD's
    points at a data/ subdirectory holding only per-module Resources - so probing that
    alone finds nothing and the whole feature silently does nothing.
    """

    @staticmethod
    def _fake_freecad(monkeypatch, *, home="", resource="", user=""):
        import sys
        import types

        fake = types.ModuleType("FreeCAD")
        fake.getHomePath = lambda: home
        fake.getResourceDir = lambda: resource
        fake.getUserAppDataDir = lambda: user
        monkeypatch.setitem(sys.modules, "FreeCAD", fake)

    def test_home_path_is_probed(self, tmp_path, monkeypatch):
        # The case that matters on a packaged build: only getHomePath is right.
        (tmp_path / "install" / "Mod").mkdir(parents=True)
        (tmp_path / "install" / "data").mkdir()
        self._fake_freecad(
            monkeypatch,
            home=str(tmp_path / "install"),
            resource=str(tmp_path / "install" / "data"),
        )

        assert module_assets._mod_roots() == [str(tmp_path / "install" / "Mod")]

    def test_all_three_bases_are_probed(self, tmp_path, monkeypatch):
        for name in ("user", "home", "resource"):
            (tmp_path / name / "Mod").mkdir(parents=True)
        self._fake_freecad(
            monkeypatch,
            user=str(tmp_path / "user"),
            home=str(tmp_path / "home"),
            resource=str(tmp_path / "resource"),
        )

        roots = module_assets._mod_roots()
        assert roots == [
            str(tmp_path / "user" / "Mod"),
            str(tmp_path / "home" / "Mod"),
            str(tmp_path / "resource" / "Mod"),
        ]

    def test_duplicate_bases_are_collapsed(self, tmp_path, monkeypatch):
        # A stock build can return the same directory for two of them.
        (tmp_path / "shared" / "Mod").mkdir(parents=True)
        self._fake_freecad(
            monkeypatch, home=str(tmp_path / "shared"), resource=str(tmp_path / "shared")
        )

        assert module_assets._mod_roots() == [str(tmp_path / "shared" / "Mod")]

    def test_missing_and_empty_bases_are_skipped(self, tmp_path, monkeypatch):
        self._fake_freecad(monkeypatch, home=str(tmp_path / "nope"), resource="")

        assert module_assets._mod_roots() == []
