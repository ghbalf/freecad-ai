"""Each utility call site asks for its own identifier.

Testing intent rather than plumbing: patch create_client, run the call
site, assert which utility name it requested. Without this, a call site
silently keeps using the chat model and nobody notices.
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

from unittest.mock import MagicMock, patch  # noqa: E402


class TestCompaction:
    def test_requests_the_compaction_utility(self):
        from freecad_ai.ui.chat_widget import _CompactionWorker
        worker = _CompactionWorker("some conversation text")
        fake = MagicMock()
        fake.send.return_value = "summary"
        with patch("freecad_ai.llm.client.create_client",
                   return_value=fake) as mk:
            worker.run()
        assert mk.call_args.args[1:] == ("compaction",) or \
            mk.call_args.kwargs.get("utility") == "compaction"


class TestToolOptimizer:
    def test_requests_the_tool_optimize_utility(self):
        from freecad_ai.tools.optimize_tools import _ask_llm_for_modification
        fake = MagicMock()
        fake.send.return_value = "```skill\ncontent\n```"
        with patch("freecad_ai.llm.client.create_client",
                   return_value=fake) as mk:
            _ask_llm_for_modification("skill", 1, 0.5, "results", "strategy")
        assert mk.call_args.args[1:] == ("tool_optimize",) or \
            mk.call_args.kwargs.get("utility") == "tool_optimize"


class TestSkillEvaluator:
    def test_skill_eval_schema_follows_its_own_profile_not_the_active_one(self):
        from freecad_ai.config import AppConfig, ProviderConfig
        from freecad_ai.extensions.skill_evaluator import SkillEvaluator

        cfg = AppConfig()
        cfg.profiles = {
            "cloud": ProviderConfig(name="openai", api_key="sk-cloud",
                                     base_url="https://api.openai.com/v1",
                                     model="gpt-4o"),
            "eval": ProviderConfig(name="anthropic", api_key="sk-eval",
                                    base_url="https://api.anthropic.com",
                                    model="claude-sonnet-4-6"),
        }
        cfg.active_profile = "cloud"
        cfg.utility_profiles["skill_eval"] = "eval"

        fake_client = MagicMock()
        fake_client.api_style = "anthropic"

        fake_registry = MagicMock()
        fake_registry.to_anthropic_schema.return_value = []
        fake_registry.to_openai_schema.return_value = []

        evaluator = SkillEvaluator({}, tool_executor=None)
        with patch("freecad_ai.config.get_config", return_value=cfg), \
             patch("freecad_ai.llm.client.create_client",
                   return_value=fake_client) as mk, \
             patch("freecad_ai.tools.setup.create_default_registry",
                   return_value=fake_registry), \
             patch("freecad_ai.core.system_prompt.build_system_prompt",
                   return_value="system prompt"):
            evaluator.evaluate("s", "content", test_cases=[])

        # The identifier, like the other three call sites. Without this the
        # test passes whether production asks for "skill_eval" or takes the
        # active profile, because the patched client answers "anthropic"
        # either way.
        assert mk.call_args.args[1:] == ("skill_eval",) or \
            mk.call_args.kwargs.get("utility") == "skill_eval"
        # And the schema still follows that client's own api_style — the
        # Task 5 guard, which is a different failure.
        assert fake_registry.to_anthropic_schema.called is True
        assert fake_registry.to_openai_schema.called is False
