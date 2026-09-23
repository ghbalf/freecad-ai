"""Explicit JSON nulls in an OpenAI-compatible response (issue #89).

``dict.get(key, default)`` returns the default only when the key is
*absent*. A gateway that sends ``"tool_calls": null`` hands back ``None``,
and every one of these used to reach an iteration or an attribute lookup.
Reported against Xiaomi MiMo in Plan mode, where the first delta of the
turn carried the null and the run died before a single token was shown.
"""

import json
from unittest.mock import patch

import pytest

from freecad_ai.llm.client import LLMClient, LLMError, LLMStreamEvent


def _make_client():
    return LLMClient(
        provider_name="custom",
        base_url="https://api.xiaomimimo.com/v1",
        api_key="test-key",
        model="mimo-v2.6-flash",
    )


class TestStreamingNulls:
    """Plan mode: ``_simple_stream`` reads this generator with tools=None."""

    def test_null_tool_calls_in_delta(self):
        """The reported crash: a text-only turn whose deltas null out tool_calls."""
        client = _make_client()
        chunks = [
            {"choices": [{"delta": {"role": "assistant", "content": None,
                                    "tool_calls": None}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "Hello", "tool_calls": None},
                          "finish_reason": None}]},
            {"choices": [{"delta": {"content": None, "tool_calls": None},
                          "finish_reason": "stop"}]},
        ]

        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            events = list(client._stream_openai_tools([], "", tools=None))

        assert "".join(e.text for e in events if e.type == "text_delta") == "Hello"
        assert [e.type for e in events if e.type == "tool_call_end"] == []
        assert events[-1].type == "done"

    def test_null_delta(self):
        """Some gateways null the whole delta on the final chunk."""
        client = _make_client()
        chunks = [
            {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]},
            {"choices": [{"delta": None, "finish_reason": "stop"}]},
        ]

        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            events = list(client._stream_openai_tools([], "", tools=None))

        assert "".join(e.text for e in events if e.type == "text_delta") == "Hi"
        assert events[-1].type == "done"

    def test_null_function_on_a_tool_call_delta(self):
        """A tool-call delta that carries only an id nulls its function."""
        client = _make_client()
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": None}]},
                "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "create_box",
                                          "arguments": '{"length": 5}'}}]},
                "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]

        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            events = list(client._stream_openai_tools([], "", tools=[]))

        ends = [e for e in events if e.type == "tool_call_end"]
        assert len(ends) == 1
        assert ends[0].tool_call.id == "call_1"
        assert ends[0].tool_call.name == "create_box"
        assert ends[0].tool_call.arguments == {"length": 5}


class TestNonStreamingNulls:
    """Act mode without streaming, and Test Connection, take this path."""

    def test_null_tool_calls_in_message(self):
        client = _make_client()
        data = {"choices": [{"message": {"role": "assistant",
                                         "content": "Plan looks fine.",
                                         "tool_calls": None},
                             "finish_reason": "stop"}]}

        with patch.object(client, "_http_post", return_value=data):
            resp = client._send_openai_tools([], "", tools=None)

        assert resp.text == "Plan looks fine."
        assert resp.tool_calls == []
        assert resp.stop_reason == "end_turn"

    def test_null_function_on_a_tool_call(self):
        """A null function has no name to call, so the call is unusable.

        It must still fail as a readable format error rather than as a bare
        TypeError with nothing to show the user.
        """
        client = _make_client()
        data = {"choices": [{"message": {"content": "",
                                         "tool_calls": [{"id": "c1",
                                                         "function": None}]},
                             "finish_reason": "tool_calls"}]}

        with patch.object(client, "_http_post", return_value=data):
            with pytest.raises(LLMError) as excinfo:
                client._send_openai_tools([], "", tools=[])

        assert "Unexpected response format" in str(excinfo.value)
        # The offending body is what makes a report like #89 diagnosable.
        assert "\"id\": \"c1\"" in str(excinfo.value)


def _anthropic_client():
    """``api_style`` is selectable for any Custom base URL, so this parser
    faces third-party gateways too -- not only api.anthropic.com."""
    client = LLMClient(
        provider_name="anthropic",
        base_url="https://gateway.example/v1",
        api_key="test-key",
        model="claude-sonnet-4",
    )
    client.api_style = "anthropic"
    return client


class TestAnthropicNonStreamingNulls:
    def test_null_content_on_a_text_reply(self):
        client = _anthropic_client()

        with patch.object(client, "_http_post", return_value={"content": None}):
            with pytest.raises(LLMError) as excinfo:
                client._send_anthropic([], "")

        assert "Unexpected response format" in str(excinfo.value)

    def test_null_content_with_tools(self):
        client = _anthropic_client()
        data = {"content": None, "stop_reason": "end_turn"}

        with patch.object(client, "_http_post", return_value=data):
            resp = client._send_anthropic_tools([], "", tools=[])

        assert resp.text == ""
        assert resp.tool_calls == []

    def test_null_input_on_a_tool_use_block(self):
        """A tool call with no arguments. The null must not reach the caller.

        This one never raised here -- it put ``arguments=None`` on the
        ToolCall and let the executor find out, which is worse than a
        parse error because the stack trace names nothing useful.
        """
        client = _anthropic_client()
        data = {"content": [{"type": "tool_use", "id": "t1",
                             "name": "list_documents", "input": None}],
                "stop_reason": "tool_use"}

        with patch.object(client, "_http_post", return_value=data):
            resp = client._send_anthropic_tools([], "", tools=[])

        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].arguments == {}

    def test_null_stop_reason(self):
        client = _anthropic_client()
        data = {"content": [{"type": "text", "text": "hi"}], "stop_reason": None}

        with patch.object(client, "_http_post", return_value=data):
            resp = client._send_anthropic_tools([], "", tools=None)

        assert resp.stop_reason == "end_turn"


class TestAnthropicStreamingNulls:
    """This generator has no exception handler at all, so a null here ends
    the turn outright rather than skipping one malformed chunk."""

    def test_null_content_block(self):
        client = _anthropic_client()
        chunks = [
            {"type": "content_block_start", "content_block": None},
            {"type": "content_block_delta",
             "delta": {"type": "text_delta", "text": "hi"}},
            {"type": "message_stop"},
        ]

        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            events = list(client._stream_anthropic_tools([], "", tools=[]))

        assert "".join(e.text for e in events if e.type == "text_delta") == "hi"
        assert events[-1].type == "done"

    def test_null_delta(self):
        client = _anthropic_client()
        chunks = [
            {"type": "content_block_delta", "delta": None},
            {"type": "message_delta", "delta": None},
            {"type": "message_stop"},
        ]

        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            events = list(client._stream_anthropic_tools([], "", tools=[]))

        assert events == [LLMStreamEvent(type="done")] or events[-1].type == "done"

    def test_null_name_on_a_tool_use_block(self):
        """A nameless tool call is unusable, but it must not be announced
        to the UI as a call to ``None``."""
        client = _anthropic_client()
        chunks = [
            {"type": "content_block_start",
             "content_block": {"type": "tool_use", "id": None, "name": None}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]

        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            events = list(client._stream_anthropic_tools([], "", tools=[]))

        starts = [e for e in events if e.type == "tool_call_start"]
        assert len(starts) == 1
        assert starts[0].tool_call.name == ""
        assert starts[0].tool_call.id == ""
