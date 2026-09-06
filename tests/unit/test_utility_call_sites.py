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
