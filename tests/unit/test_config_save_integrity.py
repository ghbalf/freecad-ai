"""A save that cannot serialise must not destroy the config (#88).

``save_config`` opened CONFIG_FILE for writing and *then* called
``to_dict()`` as the argument to ``json.dump``. Python evaluates that
argument after ``open(..., "w")`` has already truncated the file, so any
serialisation failure left a zero-byte config.json behind -- every
setting, every profile and every API key gone, with the traceback in the
Report view the only sign of it.

That is what #88 does. Its `RecursionError` comes from ``asdict()``,
which deep-copies every leaf it does not recognise and has no cycle
detection at all, so one unexpected object in the config tree takes the
whole file with it.

Two separate defects, fixed separately:

  * serialise first, write second, and write through a temp file, so a
    failure of any kind leaves what is already on disk untouched; and
  * serialise for JSON rather than with ``asdict``, so an object that
    cannot be written is dropped and *named* instead of raising.
"""

import json
import logging
from dataclasses import asdict
from unittest.mock import patch

import pytest

from freecad_ai import config as config_mod
from freecad_ai.config import AppConfig, ProviderConfig


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


class TestAFailedSaveKeepsTheOldFile:
    def test_a_raising_to_dict_does_not_truncate(self, staged):
        staged.write_text('{"mode": "act", "max_tokens": 1234}')
        before = staged.read_text()

        with patch.object(AppConfig, "to_dict", side_effect=RecursionError("boom")):
            with pytest.raises(RecursionError):
                config_mod.save_config(AppConfig())

        assert staged.read_text() == before

    def test_a_raising_json_dump_does_not_truncate(self, staged):
        staged.write_text('{"mode": "act"}')
        before = staged.read_text()

        with patch.object(config_mod.json, "dump", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                config_mod.save_config(AppConfig())

        assert staged.read_text() == before

    def test_no_temp_file_is_left_behind_on_success(self, staged):
        config_mod.save_config(AppConfig())
        assert json.loads(staged.read_text())["mode"] == "plan"
        assert list(staged.parent.glob("*.tmp")) == []


class TestSerialisingForJson:
    def test_a_normal_config_is_unchanged(self):
        """The rewrite must not alter what a healthy config serialises to."""
        cfg = AppConfig()
        cfg.profiles = {"anthropic": ProviderConfig(params={"temperature": 0.3})}
        cfg.active_profile = "anthropic"
        cfg.mcp_servers = [{"name": "x", "url": "http://h/mcp"}]

        data = cfg.to_dict()
        expected = asdict(cfg)
        for key in expected:
            assert data[key] == expected[key], key
        # The legacy mirror to_dict adds on top is still there.
        assert data["provider"]["model"] == ProviderConfig().model

    def test_a_reference_cycle_is_broken_not_followed(self, caplog):
        """asdict() has no cycle detection; one cycle is a RecursionError."""
        cfg = AppConfig()
        prof = ProviderConfig()
        cfg.profiles = {"a": prof}
        cfg.active_profile = "a"
        prof.params["loop"] = cfg.profiles          # dict -> dataclass -> dict

        with caplog.at_level(logging.WARNING, logger="freecad_ai.config"):
            data = cfg.to_dict()

        assert "loop" not in data["profiles"]["a"]["params"]
        assert "profiles.a.params.loop" in caplog.text

    def test_an_unwritable_value_is_dropped_and_named(self, caplog):
        cfg = AppConfig()
        prof = ProviderConfig()
        cfg.profiles = {"a": prof}
        cfg.active_profile = "a"
        prof.params["widget"] = object()

        with caplog.at_level(logging.WARNING, logger="freecad_ai.config"):
            data = cfg.to_dict()

        assert "widget" not in data["profiles"]["a"]["params"]
        assert "profiles.a.params.widget" in caplog.text
        assert "object" in caplog.text

    def test_a_dropped_value_costs_only_itself(self, caplog):
        """The rest of the config still has to reach disk."""
        cfg = AppConfig()
        cfg.max_tokens = 8192
        prof = ProviderConfig(model="mimo-v2.6-flash")
        prof.params["bad"] = object()
        prof.params["good"] = 0.7
        cfg.profiles = {"a": prof}
        cfg.active_profile = "a"

        with caplog.at_level(logging.WARNING, logger="freecad_ai.config"):
            data = cfg.to_dict()

        assert data["max_tokens"] == 8192
        assert data["profiles"]["a"]["model"] == "mimo-v2.6-flash"
        assert data["profiles"]["a"]["params"] == {"good": 0.7}
        assert json.dumps(data)  # the whole point: it is writable

    def test_the_result_survives_a_round_trip(self):
        cfg = AppConfig()
        cfg.profiles = {"a": ProviderConfig(model="m", params={"top_p": 0.9})}
        cfg.active_profile = "a"
        cfg.mcp_server_allowed_hosts = ["localhost"]

        back = AppConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))

        assert back.active_profile == "a"
        assert back.profiles["a"].model == "m"
        assert back.profiles["a"].params == {"top_p": 0.9}
        assert back.mcp_server_allowed_hosts == ["localhost"]
