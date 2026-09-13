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

from freecad_ai.core.system_prompt import (  # noqa: E402
    append_document_context,
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

        assert "## Current Document State" in block
        assert "Pad" in block

    def test_an_empty_document_context_yields_no_block(self, doc_state):
        doc_state("")

        assert build_document_context_block() == ""

    def test_it_is_appended_to_the_last_user_message(self):
        msgs = [
            {"role": "user", "content": "make a box"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "now a hole"},
        ]

        out = append_document_context(msgs, "## Current Document State\nBody")

        assert out[-1]["content"] == (
            "now a hole\n\n## Current Document State\nBody")

    def test_the_earlier_turns_are_untouched(self):
        msgs = [
            {"role": "user", "content": "make a box"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "now a hole"},
        ]

        out = append_document_context(msgs, "## Current Document State\nBody")

        assert out[0]["content"] == "make a box"
        assert out[1]["content"] == "done"

    def test_the_caller_s_list_is_not_mutated(self):
        """chat_widget reuses the conversation it passes in."""
        msgs = [{"role": "user", "content": "make a box"}]

        append_document_context(msgs, "## Current Document State\nBody")

        assert msgs[0]["content"] == "make a box"

    def test_a_vision_message_gets_a_trailing_text_block(self):
        """Content is a block list when an image is attached; appending a
        string to a list would corrupt the message."""
        msgs = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        }]

        out = append_document_context(msgs, "## Current Document State\nBody")

        assert out[0]["content"][-1] == {
            "type": "text", "text": "## Current Document State\nBody"}
        assert len(out[0]["content"]) == 3

    def test_an_empty_block_changes_nothing(self):
        msgs = [{"role": "user", "content": "make a box"}]

        assert append_document_context(msgs, "") == msgs

    def test_no_user_message_is_a_no_op(self):
        """Defensive: never invent a turn the provider didn't expect."""
        msgs = [{"role": "assistant", "content": "hi"}]

        assert append_document_context(msgs, "## D\nx") == msgs
