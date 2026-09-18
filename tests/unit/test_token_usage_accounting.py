"""Token usage, including cache hits, is read back off the response (#47).

Until now every ``usage`` block both API styles return was parsed for
content and discarded, so nothing in the workbench could say what a turn
cost. That made the prompt-caching work of #47 unfalsifiable — flip the
switch and there is no number that moves — and it is why #43's "$4 for a
hello world" could be neither confirmed nor refuted from inside the app.

Capture happens in ``_http_post``/``_http_stream`` rather than in the
eight ``_send_*``/``_stream_*`` methods, because every request funnels
through those two. The counters are normalised across the two styles so
callers never branch on ``api_style``.

One asymmetry drives the design: Anthropic reports usage unasked, but an
OpenAI-style *stream* omits it entirely unless the request carries
``stream_options.include_usage``. Adding that is a request change, so it
happens only when the user opted in — hence ``log_usage`` on the client
rather than always-on capture.
"""

import json

import pytest

from freecad_ai.llm.client import LLMClient  # noqa: E402


def _client(provider="anthropic", **kw):
    return LLMClient(
        provider_name=provider, base_url="https://example.invalid",
        api_key="k", model="m", **kw)


class TestNothingIsRecordedUntilAResponseArrives:

    def test_last_usage_starts_empty(self):
        assert _client().last_usage is None

    def test_an_unparseable_usage_block_is_ignored_not_raised(self):
        """A thin proxy returning junk must not break the chat."""
        c = _client()

        c._record_usage({"usage": "not-a-dict"})
        c._record_usage({"usage": {"input_tokens": "many"}})

        assert c.last_usage is None


class TestAnthropicCounters:

    def test_message_start_usage_is_recorded(self):
        c = _client()

        c._record_usage({"type": "message_start", "message": {"usage": {
            "input_tokens": 12,
            "output_tokens": 1,
            "cache_creation_input_tokens": 14000,
            "cache_read_input_tokens": 0,
        }}})

        assert c.last_usage == {
            "input": 12, "output": 1, "cache_write": 14000, "cache_read": 0}

    def test_a_cache_hit_is_recorded(self):
        c = _client()

        c._record_usage({"type": "message_start", "message": {"usage": {
            "input_tokens": 12,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 14000,
        }}})

        assert c.last_usage["cache_read"] == 14000
        assert c.last_usage["cache_write"] == 0

    def test_message_delta_tops_up_the_output_count(self):
        """Anthropic sends input at message_start and the final output
        count at message_delta; the second must not wipe the first."""
        c = _client()
        c._record_usage({"type": "message_start", "message": {"usage": {
            "input_tokens": 12, "output_tokens": 1,
            "cache_read_input_tokens": 14000}}})

        c._record_usage({"type": "message_delta",
                         "usage": {"output_tokens": 480}})

        assert c.last_usage["output"] == 480
        assert c.last_usage["input"] == 12
        assert c.last_usage["cache_read"] == 14000

    def test_a_non_streaming_response_is_recorded(self):
        c = _client()

        c._record_usage({"content": [], "usage": {
            "input_tokens": 9, "output_tokens": 3}})

        assert c.last_usage == {
            "input": 9, "output": 3, "cache_write": 0, "cache_read": 0}


class TestOpenAICounters:

    def test_prompt_and_completion_tokens_map_across(self):
        c = _client("openai")

        c._record_usage({"usage": {
            "prompt_tokens": 14012, "completion_tokens": 480}})

        assert c.last_usage == {
            "input": 14012, "output": 480, "cache_write": 0, "cache_read": 0}

    def test_cached_tokens_are_read_from_the_details_block(self):
        """OpenAI reports cache hits nested, and has no write counter —
        its cache is automatic and writes are not billed separately."""
        c = _client("openai")

        c._record_usage({"usage": {
            "prompt_tokens": 14012, "completion_tokens": 480,
            "prompt_tokens_details": {"cached_tokens": 13824}}})

        assert c.last_usage["cache_read"] == 13824
        assert c.last_usage["cache_write"] == 0

    def test_a_provider_omitting_details_still_records(self):
        c = _client("openai")

        c._record_usage({"usage": {
            "prompt_tokens": 5, "completion_tokens": 5,
            "prompt_tokens_details": None}})

        assert c.last_usage["cache_read"] == 0


class TestTheOpenAIStreamOptInIsOnlySentWhenAsked:

    def test_absent_by_default(self):
        body = _client("openai")._openai_body([], "sys", stream=True)

        assert "stream_options" not in body

    def test_present_when_logging_is_on(self):
        c = _client("openai", log_usage=True)

        body = c._openai_body([], "sys", stream=True)

        assert body["stream_options"] == {"include_usage": True}

    def test_never_sent_on_a_non_streaming_request(self):
        """include_usage is meaningless off-stream and some proxies 400
        on unknown keys — don't send it where it buys nothing."""
        c = _client("openai", log_usage=True)

        body = c._openai_body([], "sys", stream=False)

        assert "stream_options" not in body


class TestAnthropicCacheBreakpoint:

    TOOLS = [{"name": "create_primitive", "input_schema": {}}]

    def test_off_by_default_the_body_is_untouched(self):
        c = _client()

        body = c._anthropic_body([], "sys", stream=True, tools=self.TOOLS)

        assert body["system"] == "sys"
        assert body["tools"] == self.TOOLS
        assert "cache_control" not in json.dumps(body)

    def test_the_system_block_carries_the_breakpoint(self):
        """Anthropic caches tools, then system, then messages — so one
        breakpoint on system covers the tool block behind it too."""
        c = _client(prompt_caching=True)

        body = c._anthropic_body([], "sys", stream=True, tools=self.TOOLS)

        assert body["system"] == [{
            "type": "text", "text": "sys",
            "cache_control": {"type": "ephemeral"}}]

    def test_the_tools_are_left_as_they_were(self):
        c = _client(prompt_caching=True)

        body = c._anthropic_body([], "sys", stream=True, tools=self.TOOLS)

        assert body["tools"] == self.TOOLS

    def test_no_tools_means_no_breakpoint(self):
        """Plan mode sends no tools, so the prefix is just the system
        prompt — too small to re-read, and a cache write costs 1.25x."""
        c = _client(prompt_caching=True)

        body = c._anthropic_body([], "sys", stream=True, tools=None)

        assert body["system"] == "sys"
        assert "cache_control" not in json.dumps(body)

    def test_an_empty_system_falls_back_to_marking_the_last_tool(self):
        c = _client(prompt_caching=True)

        body = c._anthropic_body([], "", stream=True, tools=self.TOOLS)

        assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_marking_the_last_tool_does_not_mutate_the_caller_s_list(self):
        """chat_widget reuses the schema list across turns; a breakpoint
        accumulating into it would mark every tool over time."""
        c = _client(prompt_caching=True)
        tools = [dict(t) for t in self.TOOLS]

        c._anthropic_body([], "", stream=True, tools=tools)

        assert "cache_control" not in tools[-1]

    @pytest.mark.parametrize("thinking", ["off", "on", "extended"])
    def test_the_breakpoint_survives_every_thinking_mode(self, thinking):
        c = _client(prompt_caching=True, thinking=thinking)

        body = c._anthropic_body([], "sys", stream=True, tools=self.TOOLS)

        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


class TestCountersDoNotLeakBetweenRequests:
    """last_usage is per-request state on a client that outlives the
    request. Without a reset, a response that reports nothing would be
    logged with the previous turn's numbers -- the worst kind of wrong,
    because it looks plausible."""

    def test_a_new_request_clears_the_previous_counts(self):
        c = _client()
        c._record_usage({"usage": {"input_tokens": 9, "output_tokens": 3}})

        c._begin_request()

        assert c.last_usage is None

    def test_the_truncation_flag_is_not_disturbed(self):
        """#52's flag is reset by its own owner, not by this."""
        c = _client()
        c.response_truncated = True

        c._begin_request()

        assert c.response_truncated is True


class TestTheStreamIsDrainedPastTheFinishReason:
    """An OpenAI-style usage chunk arrives *after* the finish_reason one.

    `stream_options.include_usage` appends a final chunk carrying only
    `usage` (its `choices` list is empty), and it comes after the chunk
    that closes the turn. `_stream_openai_tools` ends the turn the moment
    it sees a finish_reason, so returning there closes the underlying
    generator before that last chunk is ever pulled -- and `_log_usage`,
    firing from the generator's `finally`, finds nothing to report.

    Found live: a five-turn Act session on moonshot/kimi-k2.6 with the
    switch on printed not one line, because every turn in Act mode takes
    this path. Plan mode was on its own parser then and ran the stream to
    exhaustion, so it was never affected; since #84 it shares this one and
    is covered by the same drain.
    """

    @staticmethod
    def _sse(chunks):
        """The SSE body a provider would send, as _http_stream reads it."""
        out = b""
        for c in chunks:
            out += b"data: " + json.dumps(c).encode() + b"\n\n"
        return out + b"data: [DONE]\n\n"

    def _run(self, monkeypatch, chunks):
        c = _client("openai", log_usage=True)
        body = self._sse(chunks)

        def fake_stream(url, headers, payload):
            try:
                for line in body.split(b"\n"):
                    text = line.decode()
                    if not text.startswith("data: "):
                        continue
                    raw = text[6:]
                    if raw == "[DONE]":
                        continue
                    chunk = json.loads(raw)
                    c._record_usage(chunk)
                    yield chunk
            finally:
                c._log_usage()

        monkeypatch.setattr(c, "_http_stream", fake_stream)
        list(c._stream_openai_tools([], "sys", tools=[{"name": "t"}]))
        return c

    def test_usage_after_a_tool_call_finish_is_still_captured(self, monkeypatch):
        c = self._run(monkeypatch, [
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "c1",
                "function": {"name": "create_primitive", "arguments": "{}"}}]},
                "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {
                "prompt_tokens": 14012, "completion_tokens": 480,
                "prompt_tokens_details": {"cached_tokens": 13824}}},
        ])

        assert c.last_usage is not None, "usage chunk was never pulled"
        assert c.last_usage["input"] == 14012
        assert c.last_usage["cache_read"] == 13824

    def test_usage_after_a_plain_stop_is_still_captured(self, monkeypatch):
        """Moonshot/Kimi returns "stop" even when the turn carried tools."""
        c = self._run(monkeypatch, [
            {"choices": [{"delta": {"content": "done"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {
                "prompt_tokens": 900, "completion_tokens": 12}},
        ])

        assert c.last_usage is not None, "usage chunk was never pulled"
        assert c.last_usage["input"] == 900

    def test_usage_after_a_truncated_turn_is_still_captured(self, monkeypatch):
        c = self._run(monkeypatch, [
            {"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]},
            {"choices": [], "usage": {
                "prompt_tokens": 7, "completion_tokens": 4096}},
        ])

        assert c.last_usage is not None, "usage chunk was never pulled"
        assert c.last_usage["output"] == 4096
        assert c.response_truncated is True

    def test_the_drain_is_skipped_when_logging_is_off(self, monkeypatch):
        """With the switch off nothing asked for a usage chunk, so the turn
        must still end at the finish_reason rather than wait on the socket."""
        c = _client("openai", log_usage=False)
        pulled = []

        def fake_stream(url, headers, payload):
            for chunk in (
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 1}},
            ):
                pulled.append(chunk)
                yield chunk

        monkeypatch.setattr(c, "_http_stream", fake_stream)
        list(c._stream_openai_tools([], "sys", tools=[{"name": "t"}]))

        assert len(pulled) == 1


class TestSilenceIsItselfReported:
    """Asking for counters and getting none must not look like a no-op.

    The live test that found the drain bug failed twice over: nothing was
    logged, and nothing said why. A provider that ignores
    `stream_options.include_usage` produces the same empty Report view as
    a broken build, and the user cannot tell them apart.
    """

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        monkeypatch.setattr(
            "freecad_ai.llm.client._USAGE_SILENCE_REPORTED", False)

    def test_a_provider_reporting_nothing_says_so(self, monkeypatch):
        c = _client("openai", log_usage=True)
        said = []
        monkeypatch.setattr(type(c), "_emit", staticmethod(said.append))

        c._log_usage()

        assert len(said) == 1
        assert "no usage counters" in said[0]
        assert "openai" in said[0]

    def test_it_is_said_once_a_session_not_once_a_turn(self, monkeypatch):
        """The chat builds a client per send, so a per-instance flag would
        repeat this on every message of a long Act conversation."""
        said = []
        for _ in range(4):
            c = _client("openai", log_usage=True)
            monkeypatch.setattr(type(c), "_emit", staticmethod(said.append))
            c._log_usage()

        assert len(said) == 1

    def test_nothing_is_said_when_logging_is_off(self, monkeypatch):
        c = _client("openai", log_usage=False)
        said = []
        monkeypatch.setattr(type(c), "_emit", staticmethod(said.append))

        c._log_usage()

        assert said == []

    def test_real_counters_still_log_the_numbers(self, monkeypatch):
        c = _client("openai", log_usage=True)
        said = []
        monkeypatch.setattr(type(c), "_emit", staticmethod(said.append))

        c._record_usage({"usage": {
            "prompt_tokens": 1000, "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 900}}})
        c._log_usage()

        assert len(said) == 1
        assert "prompt=1000" in said[0]
        assert "cache read 900 = 90%" in said[0]
