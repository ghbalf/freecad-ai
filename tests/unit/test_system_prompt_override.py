"""A custom system prompt must not cost the other modes their instructions (#161)."""

import pytest

from freecad_ai.core.system_prompt import (
    ACT_MODE,
    ACT_MODE_TOOLS,
    CODE_CONVENTIONS,
    FREECAD_API_REFERENCE,
    PLAN_MODE,
    RESPONSE_FORMAT,
    build_system_prompt,
    get_default_system_prompt,
)

REFERENCE = (FREECAD_API_REFERENCE, CODE_CONVENTIONS, RESPONSE_FORMAT)
NO_TOOLS = [("plan", PLAN_MODE), ("act", ACT_MODE)]
ACT_TOOLS_HEADING = ACT_MODE_TOOLS.splitlines()[0]


def _build(mode, override, **kw):
    return build_system_prompt(
        mode=mode, override=override, include_document_context=False, **kw)


@pytest.mark.parametrize("mode, section", NO_TOOLS)
def test_short_override_gets_mode_and_reference(mode, section):
    prompt = _build(mode, "Be terse.")
    assert prompt.startswith("Be terse.\n")
    for part in (section, *REFERENCE):
        assert prompt.count(part) == 1


@pytest.mark.parametrize("mode, section", NO_TOOLS)
def test_edited_default_swaps_mode_section(mode, section):
    # What Settings saves after a one-line edit: the Act+tools default plus it.
    edited = "House rules first.\n" + get_default_system_prompt() + "\nBe terse."
    prompt = _build(mode, edited)
    assert ACT_TOOLS_HEADING not in prompt
    assert "House rules first." in prompt
    assert "Be terse." in prompt
    assert "## Important FreeCAD Notes" in prompt
    for part in (section, *REFERENCE):
        assert prompt.count(part) == 1
    # swapped in place, not appended after the user's text
    assert prompt.index(section) < prompt.index("Be terse.")


def test_mode_section_ending_the_text_is_swapped():
    prompt = _build("plan", "Intro.\n\n## Mode: Act (with Tools)\nUse tools.")
    assert "Use tools." not in prompt
    assert prompt.startswith("Intro.\n")
    assert prompt.count(PLAN_MODE) == 1


def test_own_section_for_the_current_mode_is_kept():
    prompt = _build("plan", "## Mode: Act (with Tools)\nUse tools.\n"
                            "## Mode: Plan\nMy own plan rules.\n## Style\nTerse.")
    assert "My own plan rules." in prompt
    assert "Terse." in prompt
    assert "Use tools." not in prompt
    assert PLAN_MODE not in prompt


def test_own_reference_section_is_not_duplicated():
    prompt = _build("plan", "Hi.\n\n## Response Format\nOnly German.")
    assert prompt.count("## Response Format") == 1
    assert "Only German." in prompt
    assert FREECAD_API_REFERENCE in prompt


def test_code_comment_in_a_fence_is_not_a_heading():
    prompt = _build("plan", "## Mode: Act (with Tools)\n```python\n"
                            "## Response Format\nleftover()\n```\nUse tools.\n"
                            "## Style\nTerse.")
    assert "leftover()" not in prompt
    assert "Terse." in prompt
    assert RESPONSE_FORMAT in prompt


def test_override_is_used_unchanged_in_act_mode_with_tools():
    for text in ("Be terse.", get_default_system_prompt() + "\nBe terse."):
        assert _build("act", text, tools_enabled=True).split(text)[0] == ""
        assert PLAN_MODE not in _build("act", text, tools_enabled=True)


def test_default_prompt_is_composed_as_before():
    for mode, section in NO_TOOLS:
        prompt = _build(mode, "")
        for part in (section, *REFERENCE):
            assert prompt.count(part) == 1
