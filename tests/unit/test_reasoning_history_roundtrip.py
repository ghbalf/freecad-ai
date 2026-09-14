"""What the tool loop sent must be what the conversation stores (#47).

The prefix-cache invariant from #47 is "bytes already sent never change".
The document snapshot was one way to break it; ``reasoning_content`` is
another, in the opposite direction -- bytes that were sent and then
*disappeared*.

``_LLMWorker._tool_loop`` copies the rendered messages into a local list
and appends ``reasoning_content`` to each assistant turn (Kimi-K2.5
requires the thinking to be echoed back). What gets written into the
``Conversation`` afterwards is built from ``_tool_results``, which carried
only the text and the tool calls -- so the next user turn re-rendered the
previous turn *without* the thinking the provider had already seen. The
prefix diverged at the first assistant message of every completed turn,
which pins the cacheable prefix to the static head forever however long
the conversation grows.

Measured on moonshot/kimi-k2.6: ``cache read`` never exceeded 14336 in any
run, and the prompt *shrank* across turn boundaries -- which cannot happen
if history only ever grows.

Like the rest of #47 this rides on ``optimize_prompt_caching``: with the
flag off the conversation stores exactly what it stored before.
"""

from types import SimpleNamespace

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
from freecad_ai.core.loop_control import reasoning_to_persist  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402

THINKING = "the plate needs a pocket, not a pad"

# The assistant turn exactly as _tool_loop puts it on the wire.
SENT = {
    "role": "assistant",
    "content": "Adding the pocket.",
    "tool_calls": [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "create_primitive", "arguments": "{}"},
    }],
    "reasoning_content": THINKING,
}


def _store(conv, reasoning):
    """Run the real _store_tool_results against a real Conversation."""
    worker = SimpleNamespace(_tool_results=[{
        "assistant_text": "Adding the pocket.",
        "tool_calls": [{"id": "call_1", "name": "create_primitive",
                        "arguments": {}}],
        "results": [{"tool_call_id": "call_1", "content": "ok"}],
        "reasoning": reasoning,
    }])
    dock = SimpleNamespace(conversation=conv, _worker=worker)
    cw.ChatDockWidget._store_tool_results(dock, "Adding the pocket.Done.")


class TestTheDecisionToKeepReasoning:
    """Pure, so it can be tested without a loop, a client or a widget."""

    def test_the_default_keeps_what_was_sent(self):
        """Moonshot's engineers report a measurable drop in reply quality on
        turns whose reasoning is missing -- in ordinary multi-turn chat, not
        just tool loops -- so preservation no longer waits for the caching
        switch (forum thread 602)."""
        assert reasoning_to_persist(THINKING, False, False) == THINKING

    def test_caching_mode_keeps_what_was_sent(self):
        assert reasoning_to_persist(THINKING, False, True) == THINKING

    def test_switching_preservation_off_keeps_nothing(self):
        """The escape hatch: the history goes back to what it held before
        this release."""
        assert reasoning_to_persist(
            THINKING, False, False, "openai", False) == ""

    def test_caching_overrides_preservation_off(self):
        """Byte-for-byte re-rendering is the whole basis of the cache match,
        so the caching switch cannot be honoured while dropping thinking the
        provider was already shown."""
        assert reasoning_to_persist(
            THINKING, False, True, "openai", False) == THINKING

    def test_a_stripped_model_keeps_nothing(self):
        """Gemma never received the thinking, so storing it would be the
        same divergence in reverse."""
        assert reasoning_to_persist(THINKING, True, True) == ""

    def test_anthropic_keeps_nothing(self):
        """Anthropic carries thinking as its own signed content block; the
        loop never sends this key there, so nothing may be stored."""
        assert reasoning_to_persist(THINKING, False, True, "anthropic") == ""
        assert reasoning_to_persist(THINKING, False, True, "openai") == THINKING

    def test_a_silent_turn_keeps_nothing(self):
        assert reasoning_to_persist("", False, True) == ""
        assert reasoning_to_persist(None, False, True) == ""


class TestPreservationOffIsTheOldBehaviour:
    """The escape hatch: byte-for-byte what the conversation stored before."""

    def test_no_reasoning_reaches_the_conversation(self):
        conv = Conversation()
        conv.add_user_message("add a pocket")
        _store(conv, reasoning_to_persist(
            THINKING, False, False, "openai", False))

        assert all("reasoning_content" not in m for m in conv.messages)

    def test_no_reasoning_reaches_the_wire(self):
        conv = Conversation()
        conv.add_user_message("add a pocket")
        _store(conv, reasoning_to_persist(
            THINKING, False, False, "openai", False))

        assert all("reasoning_content" not in m
                   for m in conv.get_messages_for_api())


class TestBytesAlreadySentNeverChange:
    """The regression: re-rendering must reproduce the wire message."""

    def test_the_stored_turn_rerenders_as_it_was_sent(self):
        conv = Conversation()
        conv.add_user_message("add a pocket")
        _store(conv, reasoning_to_persist(THINKING, False, True))

        rendered = conv.get_messages_for_api()

        assert rendered[1] == SENT, \
            "the next turn re-sends this message; any difference from what " \
            "the provider already saw diverges the prefix here"

    def test_a_later_turn_does_not_rewrite_the_earlier_one(self):
        conv = Conversation()
        conv.add_user_message("add a pocket")
        _store(conv, reasoning_to_persist(THINKING, False, True))
        before = conv.get_messages_for_api()

        conv.add_user_message("now shell it")

        assert conv.get_messages_for_api()[:len(before)] == before

    def test_a_stripped_model_still_rerenders_as_it_was_sent(self):
        """Nothing was sent, so nothing may come back."""
        conv = Conversation()
        conv.add_user_message("add a pocket")
        _store(conv, reasoning_to_persist(THINKING, True, True))

        assert "reasoning_content" not in conv.get_messages_for_api()[1]

    def test_it_survives_a_session_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "freecad_ai.core.conversation.CONVERSATIONS_DIR", str(tmp_path))
        conv = Conversation()
        conv.add_user_message("add a pocket")
        _store(conv, reasoning_to_persist(THINKING, False, True))
        before = conv.get_messages_for_api()
        conv.save()

        restored = Conversation.load(conv.conversation_id)

        assert restored.get_messages_for_api() == before


class TestStoringIt:
    """Conversation.add_assistant_message is the only writer."""

    def test_it_lands_on_the_assistant_message(self):
        conv = Conversation()
        conv.add_assistant_message("hi", reasoning_content=THINKING)

        assert conv.messages[0]["reasoning_content"] == THINKING

    def test_an_empty_reasoning_adds_no_key(self):
        """An absent key and an empty string render the same today, but
        the key would still reach the session file and the tool trace."""
        conv = Conversation()
        conv.add_assistant_message("hi", reasoning_content="")

        assert "reasoning_content" not in conv.messages[0]

    def test_it_is_still_optional(self):
        conv = Conversation()
        conv.add_assistant_message("hi")

        assert conv.messages[0] == {"role": "assistant", "content": "hi"}

    def test_a_model_that_strips_thinking_never_sees_it(self):
        conv = Conversation()
        conv.add_user_message("add a pocket")
        conv.add_assistant_message("hi", reasoning_content=THINKING)

        out = conv.get_messages_for_api(strip_thinking=True)

        assert "reasoning_content" not in out[1]
