"""Reasoning must survive the turns that carry no tool calls (#84).

v0.27.0-alpha gave reasoning preservation its own switch, **Keep model
reasoning in conversation history**, on the strength of Moonshot's report
that turns missing ``reasoning_content`` measurably lose reply quality --
"in ordinary multi-turn interactions", not merely inside a tool loop
(forum thread 602).

The switch only ever reached assistant turns that carried ``tool_calls``.
Two text-only turns still stored nothing, whatever it was set to:

  * the final answer that closes an Act run, and
  * every Plan-mode reply -- which is *all* of Plan mode.

Two independent causes, one per exit:

  1. ``_simple_stream`` consumed ``client.stream()``, a ``Generator[str]``
     with nowhere to put a second kind of content; its OpenAI parser
     dropped ``reasoning_content`` on the floor by design. (That whole
     text-only parser pair lost its last caller here and was deleted;
     ``TestThePlanTruncationSignalStillReachesTheUI`` below inherited the
     #50 coverage that used to sit on it.)
  2. ``_tool_loop`` accumulated ``turn_thinking`` per turn but returned
     out of its three exit paths without carrying the last turn's copy
     anywhere ``_store_tool_results`` could see it.

Both now resolve through ``reasoning_to_persist``, so the precedence is
the one already covered in test_reasoning_history_roundtrip: a stripped
model and Anthropic are hard exclusions, caching forces preservation on,
and the switch decides the rest.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

# chat_widget imports via ui/compat.py which needs PySide6 or PySide2.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.llm.client import LLMClient  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402

THINKING = "the user asked how, not for it to be built"
ANSWER = "I would pocket it from the top face."


def _client(api_style="openai", thinking="off"):
    client = LLMClient(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o",
    )
    client.api_style = api_style  # derived from provider_name; override for the SSE shape
    client.thinking = thinking
    return client


class _Worker:
    """The _LLMWorker attributes the streaming methods actually touch.

    A real one is a QThread; the methods under test are bound to this and
    run on the calling thread, which is what the rest of this suite does.
    Every decision the streaming methods make lives in ``loop_control`` as
    a free function, so nothing here stands in for production logic — the
    precedence assertions below run the real ``reasoning_to_persist``.
    """

    def __init__(self, api_style="openai", strip=False, optimize=False,
                 preserve=True):
        self.api_style = api_style
        self._strip_thinking = strip
        self._optimize_caching = optimize
        self._preserve_reasoning = preserve
        self.messages = []
        self.system_prompt = ""
        self.tools = None
        self.registry = None
        self._full_response = ""
        self._thinking_text = ""
        self._final_reasoning = ""
        self._tool_results = []
        self._response_truncated = False
        self._max_tool_turns = 5
        self._tool_timeline = []
        self.streamed_text = []
        self.streamed_thinking = []
        self.finished_with = None
        outer = self

        class _Signal:
            def __init__(self, sink):
                self._sink = sink

            def emit(self, *args):
                self._sink(*args)

        self.token_received = _Signal(outer.streamed_text.append)
        self.thinking_received = _Signal(outer.streamed_thinking.append)
        self.response_finished = _Signal(
            lambda text: setattr(outer, "finished_with", text))
        self.tool_call_started = _Signal(lambda *a: None)
        self.tool_call_finished = _Signal(lambda *a: None)

    def isInterruptionRequested(self):
        return False


def _openai_sse(reasoning=None, content=ANSWER, finish="stop"):
    """The chunks an OpenAI-style provider sends for a text-only turn."""
    chunks = []
    if reasoning:
        chunks.append({"choices": [
            {"delta": {"reasoning_content": reasoning}, "finish_reason": None}]})
    if content:
        chunks.append({"choices": [
            {"delta": {"content": content}, "finish_reason": None}]})
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return chunks


def _run_simple(worker, client, chunks):
    with patch.object(client, "_http_stream", return_value=iter(chunks)):
        cw._LLMWorker._simple_stream(worker, client)


class TestAPlanReplyKeepsItsReasoning:
    """Cause 1: the non-tool path had no channel for reasoning at all."""

    def test_it_captures_the_reasoning(self):
        worker = _Worker()

        _run_simple(worker, _client(), _openai_sse(reasoning=THINKING))

        assert worker._final_reasoning == THINKING, \
            "a Plan reply is a whole conversation turn; dropping its " \
            "reasoning is the quality loss thread 602 measured"

    def test_it_still_streams_the_answer_text(self):
        """The switch to the event stream must not change what the user sees."""
        worker = _Worker()

        _run_simple(worker, _client(), _openai_sse(reasoning=THINKING))

        assert "".join(worker.streamed_text) == ANSWER
        assert worker.finished_with == ANSWER

    def test_it_shows_the_reasoning_as_it_arrives(self):
        """Act mode has always rendered a thinking bubble; Plan mode now
        receives the same deltas rather than discarding them."""
        worker = _Worker()

        _run_simple(worker, _client(), _openai_sse(reasoning=THINKING))

        assert "".join(worker.streamed_thinking) == THINKING

    def test_a_silent_turn_keeps_nothing(self):
        worker = _Worker()

        _run_simple(worker, _client(), _openai_sse(reasoning=None))

        assert worker._final_reasoning == ""


class TestThePlanTruncationSignalStillReachesTheUI:
    """#50 defect 3, re-homed onto the path Plan mode actually runs.

    A plan cut off at max_tokens loses its closing fence, so the Execute
    button never renders; the truncation flag is the only thing that tells
    the user why. That guarantee used to be tested against
    ``LLMClient.stream()``, which #84 left with no production caller and
    which was then deleted -- tests on an unreachable parser would have
    stayed green through a real regression here.
    """

    def _anthropic_sse(self, stop_reason):
        return [
            {"type": "content_block_delta",
             "delta": {"type": "text_delta", "text": "```python\na = 1"}},
            {"type": "message_delta", "delta": {"stop_reason": stop_reason}},
        ]

    def test_openai_length_is_reported(self):
        worker = _Worker()

        _run_simple(worker, _client(), _openai_sse(finish="length"))

        assert worker._response_truncated is True

    def test_anthropic_max_tokens_is_reported(self):
        worker = _Worker(api_style="anthropic")

        _run_simple(worker, _client(api_style="anthropic"),
                    self._anthropic_sse("max_tokens"))

        assert worker._response_truncated is True

    def test_anthropic_end_turn_is_clean(self):
        worker = _Worker(api_style="anthropic")

        _run_simple(worker, _client(api_style="anthropic"),
                    self._anthropic_sse("end_turn"))

        assert worker._response_truncated is False, \
            "a stale warning on a complete plan is its own bug"


class TestThePlanRequestIsUnchanged:
    """Moving onto the event stream must not alter a single byte on the
    wire: Plan mode sends no tools, and a reasoning model still gets its
    ``reasoning_effort``."""

    def _body_sent(self, client, worker):
        seen = {}

        def capture(url, headers, payload):
            seen["payload"] = payload
            return iter(_openai_sse(reasoning=THINKING))

        with patch.object(client, "_http_stream", side_effect=capture):
            cw._LLMWorker._simple_stream(worker, client)
        return seen["payload"]

    def test_it_sends_no_tools(self):
        body = self._body_sent(_client(), _Worker())

        assert "tools" not in body
        assert "tool_choice" not in body

    def test_a_reasoning_model_still_gets_its_effort_hint(self):
        """`_openai_body` only sets reasoning_effort on the no-tools branch,
        so a Plan request that started sending tools would silently lose it."""
        body = self._body_sent(_client(thinking="on"), _Worker())

        assert body["reasoning_effort"] == "medium"


class TestTheFinalAnswerOfAnActRunKeepsItsReasoning:
    """Cause 2: the loop's exit paths dropped the last turn's thinking."""

    def _run_loop(self, worker, client, chunks):
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            cw._LLMWorker._tool_loop(worker, client)

    def test_a_turn_that_calls_no_tools_carries_its_reasoning_out(self):
        worker = _Worker()

        self._run_loop(worker, _client(), _openai_sse(reasoning=THINKING))

        assert worker._final_reasoning == THINKING, \
            "the loop breaks on this turn, so _tool_results never sees it"

    def test_a_truncated_final_turn_carries_its_reasoning_out(self):
        worker = _Worker()

        self._run_loop(worker, _client(),
                       _openai_sse(reasoning=THINKING, finish="length"))

        assert worker._final_reasoning == THINKING


class TestThePrecedenceIsTheSameOneTheToolLoopUses:
    """No second policy: every exit resolves through reasoning_to_persist."""

    def test_a_stripped_model_keeps_nothing(self):
        worker = _Worker(strip=True)

        _run_simple(worker, _client(), _openai_sse(reasoning=THINKING))

        assert worker._final_reasoning == ""

    def test_anthropic_keeps_nothing(self):
        """Anthropic carries thinking as its own signed content block, which
        this key cannot represent."""
        worker = _Worker(api_style="anthropic")
        chunks = [
            {"type": "content_block_delta",
             "delta": {"type": "thinking_delta", "thinking": THINKING}},
            {"type": "content_block_delta",
             "delta": {"type": "text_delta", "text": ANSWER}},
            {"type": "message_stop"},
        ]

        _run_simple(worker, _client(api_style="anthropic"), chunks)

        assert worker._final_reasoning == ""
        assert "".join(worker.streamed_text) == ANSWER

    def test_switching_preservation_off_keeps_nothing(self):
        worker = _Worker(preserve=False)

        _run_simple(worker, _client(), _openai_sse(reasoning=THINKING))

        assert worker._final_reasoning == ""

    def test_caching_overrides_preservation_off(self):
        worker = _Worker(preserve=False, optimize=True)

        _run_simple(worker, _client(), _openai_sse(reasoning=THINKING))

        assert worker._final_reasoning == THINKING


class TestItReachesTheConversation:
    """The worker capturing it is half the fix; _store_tool_results is the
    only writer, and it called add_assistant_message bare on both exits."""

    def test_a_plan_reply_stores_its_reasoning(self):
        conv = Conversation()
        conv.add_user_message("how would you do it?")
        worker = SimpleNamespace(_tool_results=[], _final_reasoning=THINKING)
        dock = SimpleNamespace(conversation=conv, _worker=worker)

        cw.ChatDockWidget._store_tool_results(dock, ANSWER)

        assert conv.messages[-1]["reasoning_content"] == THINKING

    def test_the_final_act_answer_stores_its_reasoning(self):
        conv = Conversation()
        conv.add_user_message("add a pocket")
        worker = SimpleNamespace(
            _final_reasoning=THINKING,
            _tool_results=[{
                "assistant_text": "Adding the pocket.",
                "tool_calls": [{"id": "c1", "name": "create_primitive",
                                "arguments": {}}],
                "results": [{"tool_call_id": "c1", "content": "ok"}],
                "reasoning": "THINKING-FOR-THE-TOOL-TURN",
            }])
        dock = SimpleNamespace(conversation=conv, _worker=worker)

        cw.ChatDockWidget._store_tool_results(
            dock, "Adding the pocket." + ANSWER)

        assistant = [m for m in conv.messages if m["role"] == "assistant"]
        assert assistant[0]["reasoning_content"] == "THINKING-FOR-THE-TOOL-TURN"
        assert assistant[-1]["reasoning_content"] == THINKING, \
            "the turn that closes the run is a turn like any other"

    def test_it_rerenders_onto_the_wire(self):
        """The point of storing it: the next request re-sends it."""
        conv = Conversation()
        conv.add_user_message("how would you do it?")
        worker = SimpleNamespace(_tool_results=[], _final_reasoning=THINKING)
        dock = SimpleNamespace(conversation=conv, _worker=worker)
        cw.ChatDockWidget._store_tool_results(dock, ANSWER)

        rendered = conv.get_messages_for_api()

        assert rendered[-1] == {"role": "assistant", "content": ANSWER,
                                "reasoning_content": THINKING}

    def test_an_empty_reasoning_adds_no_key(self):
        """Preservation off must store byte-for-byte what it stored before."""
        conv = Conversation()
        conv.add_user_message("how would you do it?")
        worker = SimpleNamespace(_tool_results=[], _final_reasoning="")
        dock = SimpleNamespace(conversation=conv, _worker=worker)

        cw.ChatDockWidget._store_tool_results(dock, ANSWER)

        assert "reasoning_content" not in conv.messages[-1]

    def test_a_worker_that_never_ran_stores_the_text_anyway(self):
        """_store_tool_results is also reached with no worker at all."""
        conv = Conversation()
        dock = SimpleNamespace(conversation=conv, _worker=None)

        cw.ChatDockWidget._store_tool_results(dock, ANSWER)

        assert conv.messages[-1]["content"] == ANSWER
