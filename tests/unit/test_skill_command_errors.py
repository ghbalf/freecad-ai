"""A /command whose skill returns {"error": ...} must say so in the chat.

Regression: _handle_skill_command only knew inject_prompt and output, so a
handler that raised (or optimize-skill's "No skills found") did nothing
visible at all.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.extensions import skills as skills_mod  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402


@pytest.fixture
def broken_skill(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Broken\nA skill whose handler raises.\n")
    (skill_dir / "handler.py").write_text(
        "def execute(args):\n    raise RuntimeError('boom from handler')\n")
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))


def _fake_dock():
    strip = MagicMock()
    strip.get_images.return_value = []
    strip.get_documents.return_value = []
    return SimpleNamespace(
        conversation=Conversation(), _attachment_strip=strip,
        _append_html=MagicMock(), _send_with_injected_prompt=MagicMock())


def test_handler_error_is_shown_in_chat(broken_skill):
    dock = _fake_dock()

    assert cw.ChatDockWidget._handle_skill_command(dock, "/broken") is True

    shown = "".join(c.args[0] for c in dock._append_html.call_args_list)
    assert "boom from handler" in shown
    dock._send_with_injected_prompt.assert_not_called()
