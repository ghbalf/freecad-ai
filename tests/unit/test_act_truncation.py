"""Regression tests for issue #52 — Act mode discards the truncation signal.

#51 surfaced output-token truncation in Plan mode. The tool-carrying paths still
collapsed finish_reason="length" / stop_reason="max_tokens" into a clean finish,
so `_tool_loop` could not tell a truncated turn from a completed one and would
proceed on a half-formed message — running tool calls parsed from a truncated
payload, or letting the model build on its own mid-sentence output.

Decided behaviour: halt the loop on truncation (do NOT execute that turn's tool
calls), warn the user, and let the truncated turn count against max_tool_turns.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from freecad_ai.core.loop_control import resolve_turn_outcome, should_continue_loop
from freecad_ai.llm.client import LLMClient, LLMStreamEvent, ToolCall


class TestResolveTurnOutcome:
    """The per-turn decision: stop, halt-on-truncation, finish, or keep going."""

    def _tc(self):
        return [ToolCall(id="call_1", name="create_body", arguments={})]

    def test_tool_calls_continue_the_loop(self):
        assert resolve_turn_outcome(False, self._tc(), False) == "continue"

    def test_no_tool_calls_finishes_normally(self):
        assert resolve_turn_outcome(False, [], False) == "done"

    def test_truncated_turn_halts_even_with_tool_calls(self):
        assert resolve_turn_outcome(True, self._tc(), False) == "truncated", \
            "a truncated turn's tool calls must never be executed"

    def test_truncated_turn_halts_without_tool_calls(self):
        assert resolve_turn_outcome(True, [], False) == "truncated"

    def test_interrupt_beats_truncation(self):
        assert resolve_turn_outcome(True, self._tc(), True) == "stopped"

    def test_interrupt_beats_everything(self):
        assert resolve_turn_outcome(False, self._tc(), True) == "stopped"


class TestTruncatedTurnCountsAgainstBudget:
    """A truncated turn consumed a request; it must not be refunded."""

    def test_turn_budget_is_unchanged_by_truncation(self):
        # should_continue_loop has no truncation concept: the turn counter is
        # incremented by the caller for every turn, truncated or not.
        assert should_continue_loop(30, 29, False) is True
        assert should_continue_loop(30, 30, False) is False


def _make_client(api_style="openai"):
    client = LLMClient(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o",
    )
    client.api_style = api_style  # derived from provider_name; override for the SSE shape
    return client


class TestStreamingToolPathsRecordTruncation:
    def test_openai_tools_stream_records_length(self):
        client = _make_client()
        chunks = [
            {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        ]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            list(client.stream_with_tools([], "", tools=[]))
        assert client.response_truncated is True

    def test_openai_tools_stream_normal_finish_is_clean(self):
        client = _make_client()
        chunks = [
            {"choices": [{"delta": {"content": "all done"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            list(client.stream_with_tools([], "", tools=[]))
        assert client.response_truncated is False

    def test_anthropic_tools_stream_records_max_tokens(self):
        client = _make_client(api_style="anthropic")
        chunks = [
            {"type": "content_block_delta",
             "delta": {"type": "text_delta", "text": "partial"}},
            {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
        ]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            list(client.stream_with_tools([], "", tools=[]))
        assert client.response_truncated is True

    def test_anthropic_tools_stream_normal_end_turn_is_clean(self):
        client = _make_client(api_style="anthropic")
        chunks = [
            {"type": "content_block_delta",
             "delta": {"type": "text_delta", "text": "done"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            {"type": "message_stop"},
        ]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            list(client.stream_with_tools([], "", tools=[]))
        assert client.response_truncated is False

    def test_flag_resets_between_tool_streams(self):
        client = _make_client()
        truncated = [{"choices": [{"delta": {}, "finish_reason": "length"}]}]
        with patch.object(client, "_http_stream", return_value=iter(truncated)):
            list(client.stream_with_tools([], "", tools=[]))
        assert client.response_truncated is True

        clean = [{"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}]
        with patch.object(client, "_http_stream", return_value=iter(clean)):
            list(client.stream_with_tools([], "", tools=[]))
        assert client.response_truncated is False, "stale warning must not persist"


class TestNonStreamingToolPathsRecordTruncation:
    def test_send_openai_tools_records_length(self):
        client = _make_client()
        data = {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}
        with patch.object(client, "_http_post", return_value=data):
            client.send_with_tools([], "", tools=[])
        assert client.response_truncated is True

    def test_send_openai_tools_normal_finish_is_clean(self):
        client = _make_client()
        data = {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
        with patch.object(client, "_http_post", return_value=data):
            client.send_with_tools([], "", tools=[])
        assert client.response_truncated is False

    def test_send_anthropic_tools_records_max_tokens(self):
        client = _make_client(api_style="anthropic")
        data = {"content": [{"type": "text", "text": "partial"}],
                "stop_reason": "max_tokens"}
        with patch.object(client, "_http_post", return_value=data):
            client.send_with_tools([], "", tools=[])
        assert client.response_truncated is True

    def test_send_anthropic_tools_normal_end_turn_is_clean(self):
        client = _make_client(api_style="anthropic")
        data = {"content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn"}
        with patch.object(client, "_http_post", return_value=data):
            client.send_with_tools([], "", tools=[])
        assert client.response_truncated is False


class _FakeClient:
    """Yields a fixed event sequence, then reports whether it was truncated."""

    def __init__(self, events, truncated):
        self._events = events
        self.response_truncated = truncated

    def stream_with_tools(self, messages, system="", tools=None):
        yield from self._events


def _fake_worker(max_turns=30):
    """A stand-in for _LLMWorker carrying only what _tool_loop touches.

    _LLMWorker is a QThread; running the real one needs an event loop and a live
    provider. _tool_loop never calls super(), so it can be invoked unbound
    against this fake to exercise the halt decision on its own.
    """
    return SimpleNamespace(
        messages=[{"role": "user", "content": "make a box"}],
        system_prompt="",
        tools=[],
        api_style="openai",
        registry=None,
        conversation=None,
        _max_tool_turns=max_turns,
        _full_response="",
        _thinking_text="",
        _strip_thinking=False,
        # Every exit path now resolves the turn's reasoning before it
        # returns, so the halt paths read these too (#84).
        _optimize_caching=False,
        _preserve_reasoning=True,
        _final_reasoning="",
        _tool_results=[],
        _tool_timeline=[],
        _response_truncated=False,
        isInterruptionRequested=lambda: False,
        token_received=MagicMock(),
        thinking_received=MagicMock(),
        tool_call_started=MagicMock(),
        tool_call_finished=MagicMock(),
        response_finished=MagicMock(),
        _execute_tool_on_main_thread=MagicMock(
            side_effect=AssertionError(
                "a truncated turn's tool calls must never be executed")),
    )


class TestToolLoopHaltsOnTruncation:
    """The behaviour issue #52 is actually about: don't act on a partial turn."""

    _TOOL_TURN = [
        LLMStreamEvent(type="text_delta", text="I'll create the body."),
        LLMStreamEvent(
            type="tool_call_end",
            tool_call=ToolCall(id="call_1", name="create_body", arguments={}),
        ),
        LLMStreamEvent(type="done"),
    ]

    def _run(self, truncated, events=None):
        from freecad_ai.ui.chat_widget import _LLMWorker
        worker = _fake_worker()
        client = _FakeClient(events or self._TOOL_TURN, truncated)
        _LLMWorker._tool_loop(worker, client)  # type: ignore[arg-type]
        return worker

    def test_truncated_turn_does_not_execute_tool_calls(self):
        worker = self._run(truncated=True)
        worker._execute_tool_on_main_thread.assert_not_called()

    def test_truncated_turn_flags_the_worker_for_the_warning(self):
        worker = self._run(truncated=True)
        assert worker._response_truncated is True, \
            "the UI reads this flag to render the truncation warning"

    def test_truncated_turn_finishes_the_response(self):
        worker = self._run(truncated=True)
        worker.response_finished.emit.assert_called_once()

    def test_clean_turn_without_tool_calls_does_not_flag_truncation(self):
        events = [
            LLMStreamEvent(type="text_delta", text="All done."),
            LLMStreamEvent(type="done"),
        ]
        worker = self._run(truncated=False, events=events)
        assert worker._response_truncated is False
        worker.response_finished.emit.assert_called_once()
