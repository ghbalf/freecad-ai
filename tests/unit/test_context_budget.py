"""Warn when prompt + tool schemas alone exceed the context setting (#158)."""

import pytest

from freecad_ai.config import ProviderConfig, get_config
from freecad_ai.core.context_budget import estimate_tokens, fixed_part_warning
from freecad_ai.llm.client import create_client


def test_text_is_estimated_like_the_conversation():
    assert estimate_tokens("x" * 400) == 100


def test_tools_are_estimated_as_json():
    assert estimate_tokens([{"a": "é"}]) == len('[{"a": "é"}]') // 4


def test_no_tools_cost_nothing():
    assert estimate_tokens(None) == 0


def test_a_fixed_part_that_fits_gives_no_warning():
    assert fixed_part_warning("x" * 400, None, 100) is None


def test_the_warning_names_the_sizes_and_the_settings():
    msg = fixed_part_warning("x" * 8000, [{"d": "y" * 20000}], 6000)
    for part in ("~2000", "~5002", "~7002", "6000",
                 "Tool Reranking", "Compact above"):
        assert part in msg


def test_without_tools_reranking_is_not_suggested():
    assert "Tool Reranking" not in fixed_part_warning("x" * 8000, None, 100)


def test_the_shipped_tools_fit_the_old_20k_default():
    """Configs saved before #123 still hold 20,000; they must not start warning."""
    from freecad_ai.core.system_prompt import build_system_prompt
    from freecad_ai.tools.setup import create_default_registry
    tools = create_default_registry(include_mcp=False).to_openai_schema()
    prompt = build_system_prompt(mode="act", tools_enabled=True)
    assert fixed_part_warning(prompt, tools, 20000) is None
    assert fixed_part_warning(prompt, tools, 8000)


def test_the_client_carries_its_profiles_window(tmp_config_dir):
    cfg = get_config()
    cfg.profiles = {"chat": ProviderConfig(name="ollama"),
                    "b": ProviderConfig(name="ollama", context_window=8000)}
    cfg.active_profile = "chat"
    cfg.context_window = 64000
    assert create_client(cfg).context_window == 64000
    assert create_client(cfg, profile="b").context_window == 8000


# --- worker and dock -------------------------------------------------------

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.llm.client import LLMError  # noqa: E402
from freecad_ai.tools.registry import (  # noqa: E402
    ToolDefinition, ToolRegistry, ToolResult)
from freecad_ai.ui import chat_widget as cw  # noqa: E402
from tests.unit._worker_harness import (  # noqa: E402
    DONE, ScriptedClient, call, run_worker, text)


@pytest.fixture(scope="module")
def qapp():
    from freecad_ai.ui.compat import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _registry():
    reg = ToolRegistry()
    for name, size in (("small", 40), ("big", 20000)):
        reg.register(ToolDefinition(
            name=name, description="d" * size, parameters=[],
            handler=lambda **kw: ToolResult(True, "ok")))
    return reg


def _run(rounds, window, **kw):
    conv = Conversation(messages=[{"role": "user", "content": "hi"}])
    worker = cw._LLMWorker(conv, "S", **kw)
    notes = []
    worker.context_note.connect(lambda key, msg: notes.append((key, msg)))
    client = ScriptedClient(rounds)
    client.context_window = window
    run_worker(worker, client)
    return notes


def test_the_worker_warns_about_the_tools_it_sends(qapp, tmp_config_dir):
    notes = _run([[text("ok"), DONE]], 4000, registry=_registry())
    assert len(notes) == 1 and "4000" in notes[0][1]


def test_reranked_out_tools_do_not_count(qapp, tmp_config_dir):
    assert _run([[text("ok"), DONE]], 4000, registry=_registry(),
                filter_names={"small"}) == []


def test_the_warning_comes_before_a_failing_request(qapp, tmp_config_dir):
    def fails():
        raise LLMError("context length exceeded")
        yield  # pragma: no cover

    assert len(_run([fails()], 4000, registry=_registry())) == 1


def test_one_note_per_turn_across_tool_rounds(qapp, tmp_config_dir):
    rounds = [[call("c1", name="small"), DONE], [text("ok"), DONE]]
    assert len(_run(rounds, 4000, registry=_registry())) == 1


def _dock(conv_id="c1"):
    fake = SimpleNamespace(
        _turn_notes=[], _append_html=MagicMock(), _context_warned=set(),
        conversation=SimpleNamespace(conversation_id=conv_id), _worker=None,
        sender=lambda: None)
    fake._from_current_worker = lambda: (
        cw.ChatDockWidget._from_current_worker(fake))
    return fake


_slot = cw.ChatDockWidget._on_context_note


def test_the_dock_shows_and_keeps_the_note():
    fake = _dock()
    _slot(fake, "m/4000", "⚠ a<b")
    assert "a&lt;b" in fake._append_html.call_args[0][0]
    assert fake._turn_notes == ["⚠ a<b"]


def test_the_same_warning_shows_once_per_chat():
    fake = _dock()
    _slot(fake, "m/4000", "one")
    _slot(fake, "m/4000", "two")
    assert fake._turn_notes == ["one"]


def test_another_window_or_chat_warns_again():
    fake = _dock()
    _slot(fake, "m/4000", "one")
    _slot(fake, "m/8000", "two")
    fake.conversation.conversation_id = "c2"
    _slot(fake, "m/4000", "three")
    assert fake._turn_notes == ["one", "two", "three"]


def test_a_stale_workers_note_is_ignored():
    fake = _dock()
    fake.sender = lambda: object()
    _slot(fake, "m/4000", "late")
    assert fake._turn_notes == [] and fake._context_warned == set()


def test_the_slot_is_connected():
    assert ("context_note", "_on_context_note") in cw.ChatDockWidget._WORKER_SLOTS
