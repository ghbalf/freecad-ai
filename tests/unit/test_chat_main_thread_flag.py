"""The chat worker honours ToolDefinition.main_thread=False (#114 final review).

The chat dock does not use QtMainThreadToolExecutor: _LLMWorker._tool_loop
sends every call to the GUI thread itself. A main_thread=False tool such as
execute_code_headless must run on the worker, or it freezes FreeCAD for the
whole run and the Stop button (which interrupts the worker) cannot end it.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from freecad_ai.llm.client import LLMStreamEvent, ToolCall
from freecad_ai.tools.registry import ToolDefinition, ToolRegistry, ToolResult
from tests.unit._worker_harness import OneClientWalker, add_seams


class _TwoTurnClient:
    """First turn calls the tool, second turn finishes."""

    response_truncated = False

    def __init__(self, tool_name):
        self._turns = [
            [LLMStreamEvent(type="tool_call_end",
                            tool_call=ToolCall(id="c1", name=tool_name, arguments={})),
             LLMStreamEvent(type="done")],
            [LLMStreamEvent(type="text_delta", text="Done."),
             LLMStreamEvent(type="done")],
        ]

    def stream_with_tools(self, messages, system="", tools=None):
        yield from self._turns.pop(0)


def _run(main_thread):
    ran_on = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="probe", description="", parameters=[], main_thread=main_thread,
        handler=lambda: ran_on.append(threading.get_ident()) or ToolResult(True, "ok")))
    worker = add_seams(SimpleNamespace(
        system_prompt="", api_style="openai", registry=registry,
        _max_tool_turns=5, _full_response="", _thinking_text="",
        _strip_thinking=False, _optimize_caching=False, _preserve_reasoning=True,
        _final_reasoning="", _tool_results=[], _tool_timeline=[],
        _response_truncated=False, isInterruptionRequested=lambda: False,
        token_received=MagicMock(), thinking_received=MagicMock(),
        tool_call_started=MagicMock(), tool_call_finished=MagicMock(),
        response_finished=MagicMock(),
        _execute_tool_on_main_thread=MagicMock(
            return_value={"success": True, "output": "gui", "error": ""}),
    ))
    from freecad_ai.ui.chat_widget import _LLMWorker
    _LLMWorker._tool_loop(worker, OneClientWalker(_TwoTurnClient("probe")))  # type: ignore[arg-type]
    return worker, ran_on


def test_main_thread_false_tool_runs_on_the_worker():
    worker, ran_on = _run(main_thread=False)
    worker._execute_tool_on_main_thread.assert_not_called()
    assert ran_on == [threading.get_ident()]


def test_ordinary_tool_still_goes_to_the_gui_thread():
    worker, ran_on = _run(main_thread=True)
    worker._execute_tool_on_main_thread.assert_called_once()
    assert ran_on == []


def test_execute_code_headless_is_a_worker_tool():
    from freecad_ai.tools.freecad_tools import EXECUTE_CODE_HEADLESS
    assert EXECUTE_CODE_HEADLESS.main_thread is False
