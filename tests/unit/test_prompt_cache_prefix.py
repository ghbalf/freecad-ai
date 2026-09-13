"""The cacheable prefix must not move when the document changes (#47).

Every provider that discounts repeated prompts -- Anthropic's explicit
``cache_control``, OpenAI's automatic prefix cache, and the OpenAI-style
endpoints that copy it -- matches on a *prefix*: it compares from the
first token and stops at the first byte that differs. So anything
volatile placed ahead of the stable bulk doesn't cost a slice of the
discount, it costs all of it.

``build_system_prompt`` appends ``get_document_context()`` -- the live
object tree, the active Body, the selection -- into the system prompt,
which is the very first thing in every request. One added pad and the
next turn's prefix diverges at the top, taking the ~12.5k tool block
behind it down too. On the automatic-caching providers that is a
discount the workbench already qualified for and threw away, and since
nothing reads ``usage`` (see test_token_usage_accounting.py) it did so
invisibly.

Moving that block to the tail changes what the model sees -- same text,
later position -- so it lives behind ``optimize_prompt_caching``, which
defaults off. The first class below pins the default so the opt-in can
never quietly become the default.
"""

import pytest

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.core.system_prompt import (  # noqa: E402
    build_document_context_block,
    build_system_prompt,
)

DOC_EMPTY = 'Document: "Unnamed"\nFile: (unsaved)\nObjects: (none)'
DOC_ONE_PAD = (
    'Document: "Unnamed"\nFile: (unsaved)\n'
    "Active Body: Body\nObjects (2):\n  Body\n    Pad"
)


@pytest.fixture
def doc_state(monkeypatch):
    """Swap the live FreeCAD inspector for a value we control."""

    def _set(text):
        monkeypatch.setattr(
            "freecad_ai.core.system_prompt.get_document_context",
            lambda: text,
        )

    return _set


class TestTheDefaultIsUnchanged:
    """Characterisation. Nobody's prompt moves until they ask for it."""

    def test_document_state_is_in_the_system_prompt_by_default(self, doc_state):
        doc_state(DOC_ONE_PAD)

        prompt = build_system_prompt(mode="act", tools_enabled=True)

        assert "## Current Document State" in prompt
        assert "Pad" in prompt

    def test_the_default_prefix_still_moves_with_the_document(self, doc_state):
        """The bug itself, pinned as the documented default behaviour."""
        doc_state(DOC_EMPTY)
        before = build_system_prompt(mode="act", tools_enabled=True)
        doc_state(DOC_ONE_PAD)
        after = build_system_prompt(mode="act", tools_enabled=True)

        assert before != after


class TestCachingModeKeepsThePrefixStable:

    def test_two_document_states_give_byte_identical_prompts(self, doc_state):
        doc_state(DOC_EMPTY)
        before = build_system_prompt(
            mode="act", tools_enabled=True, include_document_context=False)
        doc_state(DOC_ONE_PAD)
        after = build_system_prompt(
            mode="act", tools_enabled=True, include_document_context=False)

        assert before == after

    def test_the_document_section_is_gone_entirely(self, doc_state):
        """Not merely equal -- the volatile heading must be absent, or two
        identical-but-present states would pass this vacuously."""
        doc_state(DOC_ONE_PAD)

        prompt = build_system_prompt(
            mode="act", tools_enabled=True, include_document_context=False)

        assert "## Current Document State" not in prompt
        assert "Pad" not in prompt

    def test_the_instructions_survive(self, doc_state):
        """Guard against 'stable' being achieved by returning nothing."""
        doc_state(DOC_ONE_PAD)

        prompt = build_system_prompt(
            mode="act", tools_enabled=True, include_document_context=False)

        assert len(prompt) > 200
        assert "App.ActiveDocument" in prompt


class TestTheDocumentStateIsStillDelivered:
    """Dropping the context would 'fix' caching by breaking the assistant."""

    def test_the_block_carries_the_document_state(self, doc_state):
        doc_state(DOC_ONE_PAD)

        block = build_document_context_block()

        assert "## Document State" in block
        assert "Pad" in block

    def test_an_empty_document_context_yields_no_block(self, doc_state):
        doc_state("")

        assert build_document_context_block() == ""

    def test_the_heading_does_not_claim_to_be_current(self, doc_state):
        """A snapshot stays on the turn it was taken for, so later turns
        carry older ones. Calling every copy "current" would contradict
        the copy below it."""
        doc_state(DOC_ONE_PAD)

        assert "at the time of this message" in build_document_context_block()


class TestBytesAlreadySentNeverChange:
    """The invariant the first implementation missed (#47).

    A prefix cache compares from token zero and stops at the first byte
    that differs, so a message that was already sent must render the same
    way forever. The original fix grafted the document block onto the
    *last* user message at send time, which meant turn 2 rendered turn 1
    without the block turn 1 had been sent with. The divergence landed on
    messages[0] -- as early as it can land -- so the whole conversation
    behind it was re-billed at full price on every request, which is what
    two live runs measured (89.8% -> 89.0%, no change).
    """

    def _turn(self, conv, text, block):
        conv.add_user_message(text)
        conv.attach_document_context(block)
        return conv.get_messages_for_api()

    def test_the_first_turn_is_byte_identical_two_turns_later(self):
        conv = Conversation()
        first = self._turn(conv, "make a box", "## Document State\nBox")
        conv.add_assistant_message("done")

        later = self._turn(conv, "now a hole", "## Document State\nBox, Hole")

        assert later[0] == first[0]

    def test_each_turn_keeps_the_snapshot_it_was_sent_with(self):
        conv = Conversation()
        self._turn(conv, "make a box", "## Document State\nBox")
        conv.add_assistant_message("done")

        out = self._turn(conv, "now a hole", "## Document State\nBox, Hole")

        assert out[0]["content"].endswith("## Document State\nBox")
        assert out[-1]["content"].endswith("## Document State\nBox, Hole")

    def test_the_newest_snapshot_is_the_last_thing_the_model_reads(self):
        conv = Conversation()
        self._turn(conv, "make a box", "## Document State\nBox")
        conv.add_assistant_message("done")

        out = self._turn(conv, "now a hole", "## Document State\nBox, Hole")

        assert "Box, Hole" in out[-1]["content"]

    def test_a_turn_with_no_snapshot_renders_unchanged(self):
        """Caching off records nothing, so nothing is appended."""
        conv = Conversation()
        conv.add_user_message("make a box")

        out = conv.get_messages_for_api()

        assert out[0]["content"] == "make a box"


class TestTurningItBackOffUndoesIt:
    """The warning on the switch says to flip it back if replies get
    worse. That escape hatch only works if flipping it back is complete."""

    def test_clearing_restores_the_original_rendering(self):
        conv = Conversation()
        conv.add_user_message("make a box")
        before = conv.get_messages_for_api()
        conv.attach_document_context("## D\nBody")

        conv.clear_document_context()

        assert conv.get_messages_for_api() == before

    def test_clearing_an_untouched_conversation_is_harmless(self):
        conv = Conversation()
        conv.add_user_message("make a box")
        conv.add_assistant_message("done")

        conv.clear_document_context()

        assert conv.messages == [
            {"role": "user", "content": "make a box"},
            {"role": "assistant", "content": "done"},
        ]


class TestAttachingTheSnapshot:

    def test_it_lands_on_the_last_user_message(self):
        conv = Conversation()
        conv.add_user_message("make a box")
        conv.add_assistant_message("done")
        conv.add_user_message("now a hole")

        conv.attach_document_context("## D\nBody")

        assert conv.messages[2]["doc_context"] == "## D\nBody"
        assert "doc_context" not in conv.messages[0]

    def test_attaching_twice_replaces_rather_than_duplicates(self):
        """The retry path re-sends the same turn after an error."""
        conv = Conversation()
        conv.add_user_message("make a box")

        conv.attach_document_context("## D\nBox")
        conv.attach_document_context("## D\nBox, Hole")

        out = conv.get_messages_for_api()
        assert out[0]["content"].count("## D") == 1
        assert out[0]["content"].endswith("## D\nBox, Hole")

    def test_an_empty_block_records_nothing(self):
        conv = Conversation()
        conv.add_user_message("make a box")

        conv.attach_document_context("")

        assert "doc_context" not in conv.messages[0]

    def test_no_user_message_is_a_no_op(self):
        """Defensive: never invent a turn the provider didn't expect."""
        conv = Conversation()
        conv.add_assistant_message("hi")

        conv.attach_document_context("## D\nBody")

        assert conv.messages == [{"role": "assistant", "content": "hi"}]

    def test_the_users_own_words_are_left_alone_in_the_log(self):
        """The chat pane and the session file render msg["content"]."""
        conv = Conversation()
        conv.add_user_message("make a box")

        conv.attach_document_context("## D\nBody")

        assert conv.messages[0]["content"] == "make a box"

    def test_a_vision_message_gets_a_trailing_text_block(self):
        """Content is a block list when an image is attached; appending a
        string to a list would corrupt the message."""
        conv = Conversation()
        conv.add_user_message("what is this", images=[{
            "type": "image", "source": "base64",
            "media_type": "image/png", "data": "AAAA"}])

        conv.attach_document_context("## D\nBody")

        out = conv.get_messages_for_api()
        assert out[0]["content"][-1] == {"type": "text", "text": "## D\nBody"}

    def test_the_bookkeeping_key_never_reaches_the_wire(self):
        conv = Conversation()
        conv.add_user_message("make a box")
        conv.attach_document_context("## D\nBody")

        for style in ("openai", "anthropic"):
            for msg in conv.get_messages_for_api(api_style=style):
                assert "doc_context" not in msg

    def test_the_vision_fallback_rerender_keeps_it(self):
        """_LLMWorker.run re-renders from the conversation when a
        describe_fn is in play. The grafted-on version was lost there, so
        non-vision users' models saw no document state at all."""
        conv = Conversation()
        conv.add_user_message("make a box")
        conv.attach_document_context("## D\nBody")

        out = conv.get_messages_for_api(describe_fn=lambda b64: "a box")

        assert out[0]["content"].endswith("## D\nBody")

    def test_the_snapshot_counts_against_the_truncation_budget(self):
        """It is billed like any other text; ignoring it would let a
        conversation quietly overshoot max_chars."""
        conv = Conversation()
        conv.add_user_message("a")
        conv.attach_document_context("x" * 500)
        conv.add_assistant_message("b")
        conv.add_user_message("c")

        out = conv.get_messages_for_api(max_chars=200)

        assert all("x" * 500 not in str(m["content"]) for m in out)

    def test_it_survives_a_session_round_trip(self, tmp_path, monkeypatch):
        """A reloaded session must re-render byte-identically or the first
        request after the reload throws the cache away."""
        monkeypatch.setattr(
            "freecad_ai.core.conversation.CONVERSATIONS_DIR", str(tmp_path))
        conv = Conversation()
        conv.add_user_message("make a box")
        conv.attach_document_context("## D\nBody")
        before = conv.get_messages_for_api()
        conv.save()

        restored = Conversation.load(conv.conversation_id)

        assert restored.get_messages_for_api() == before
