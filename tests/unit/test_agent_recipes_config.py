"""Agent recipes are seeded into config once; Reset backs up first (#139)."""

import json
import os
from datetime import datetime

import pytest

from freecad_ai import config as config_mod
from freecad_ai.agent_recipes import DEFAULT_AGENT_RECIPES, reset_recipes

NOW = datetime(2026, 11, 16, 12, 0, 0)
CUSTOM = {"name": "Mine", "template": "mine {url}{header_args}",
          "header_arg": " -H {header}", "docs_url": "https://example.org",
          "tested": False}


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """Point the module's paths at tmp_path and keep the param store out."""
    cfg_dir = tmp_path / "FreeCADAI"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    for name in ("CONFIG_DIR", "CONVERSATIONS_DIR", "SKILLS_DIR",
                 "USER_TOOLS_DIR", "HOOKS_DIR", "LOGS_DIR", "BACKUPS_DIR"):
        monkeypatch.setattr(config_mod, name, str(cfg_dir / name.lower()))
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(config_mod, "_get_param_group", lambda: None)
    return cfg_file


class TestSeedOnce:
    def test_absent_key_is_seeded_and_saved(self, staged):
        staged.write_text('{"mode": "act"}')
        assert config_mod.load_config().mcp_agent_recipes == list(
            DEFAULT_AGENT_RECIPES)
        on_disk = json.loads(staged.read_text())
        assert on_disk["mcp_agent_recipes"] == list(DEFAULT_AGENT_RECIPES)

    def test_seeded_list_is_a_copy(self, staged):
        staged.write_text('{"mode": "act"}')
        config_mod.load_config().mcp_agent_recipes[0]["name"] = "changed"
        assert DEFAULT_AGENT_RECIPES[0]["name"] == "Claude Code"

    def test_empty_list_is_not_reseeded(self, staged):
        staged.write_text('{"mcp_agent_recipes": []}')
        assert config_mod.load_config().mcp_agent_recipes == []

    def test_edited_list_is_kept(self, staged):
        staged.write_text(json.dumps({"mcp_agent_recipes": [CUSTOM]}))
        assert config_mod.load_config().mcp_agent_recipes == [CUSTOM]

    def test_broken_config_is_not_seeded(self, staged):
        staged.write_text('{"mode": ')
        before = staged.read_bytes()
        config_mod.load_config()
        assert staged.read_bytes() == before


class TestReset:
    def test_reset_writes_backup_first(self, tmp_path):
        current = [CUSTOM]
        new, path = reset_recipes(current, str(tmp_path), NOW)
        assert path.endswith("mcp_agent_recipes.20261116-120000.json")
        with open(path) as f:
            assert json.load(f) == [CUSTOM]
        assert new == list(DEFAULT_AGENT_RECIPES)
        assert current == [CUSTOM]

    def test_reset_backup_failure_changes_nothing(self, tmp_path):
        current = [CUSTOM]
        with pytest.raises(OSError):
            reset_recipes(current, str(tmp_path / "missing"), NOW)
        assert current == [CUSTOM]

    def test_reset_when_already_default_writes_nothing(self, tmp_path):
        new, path = reset_recipes(list(DEFAULT_AGENT_RECIPES), str(tmp_path), NOW)
        assert path is None
        assert new == list(DEFAULT_AGENT_RECIPES)
        assert os.listdir(tmp_path) == []

    def test_reset_same_second_does_not_overwrite(self, tmp_path):
        _, first = reset_recipes([CUSTOM], str(tmp_path), NOW)
        _, second = reset_recipes([{**CUSTOM, "name": "Other"}], str(tmp_path), NOW)
        assert first != second
        with open(first) as f:
            assert json.load(f)[0]["name"] == "Mine"
