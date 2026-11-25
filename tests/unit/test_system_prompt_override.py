"""A custom system prompt must not cost the other modes their instructions (#161)."""

import pytest

from freecad_ai.core.system_prompt import (
    ACT_MODE,
    ACT_MODE_TOOLS,
    CODE_CONVENTIONS,
    FREECAD_API_REFERENCE,
    IDENTITY,
    PLAN_MODE,
    RESPONSE_FORMAT,
    _adapt_override,
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


@pytest.mark.parametrize("text", [
    "Be terse.",
    "Be terse.\r\n  ",
    get_default_system_prompt() + "\nBe terse.",
    "## Mode: Plan\nMine.\n## Mode: Act (with Tools)\nMine too.",
])
def test_override_is_used_unchanged_in_act_mode_with_tools(text):
    assert _adapt_override(text, "act", True) == text


@pytest.mark.parametrize("before", [
    "Reply with\n```one line```\n",
    "```python\nx=1\n\n",
    "````\n```python\nx\n````\n",
    "~~~\n```\n~~~\n",
    "```\n~~~\n```\n",
    "  ```python\n  ## x\n  ```\n",
], ids=["inline", "unclosed", "nested", "tilde", "tilde-inside", "indented"])
def test_odd_fences_do_not_hide_the_mode_section(before):
    prompt = _build("plan", before + get_default_system_prompt())
    assert ACT_TOOLS_HEADING not in prompt
    assert prompt.startswith(before)
    assert prompt.count(PLAN_MODE) == 1


def test_inline_code_does_not_pair_with_a_later_fence():
    prompt = _build("plan", "```one line```\n## Mode: Act (with Tools)\n"
                            "Use tools.\n## Style\n```\nx\n```")
    assert "Use tools." not in prompt


def test_unclosed_fence_in_the_mode_section_keeps_later_rules():
    prompt = _build("plan", "## Mode: Act (with Tools)\n```python\nx\n"
                            "## My rules\nKeep me.")
    assert "## My rules\nKeep me." in prompt
    assert PLAN_MODE in prompt


@pytest.mark.parametrize("text", [
    "~~~\n## Mode: Act (with Tools)\nUSER\n~~~",
    "  ```\n## Mode: Act (with Tools)\nUSER\n  ```",
    "````\n```\n## Mode: Act (with Tools)\nUSER\n````",
], ids=["tilde", "indented", "longer"])
def test_mode_heading_inside_a_fence_is_text(text):
    assert _build("plan", text).startswith(text + "\n")


@pytest.mark.parametrize("heading", [
    "##  Mode:  Act (with Tools)", "##\tMode: Act (with Tools)",
    "  ## Mode: Act (with Tools)  ", "## Mode: Act (with Tools)\r",
])
def test_heading_whitespace_does_not_matter(heading):
    prompt = _build("plan", heading + "\nUse tools.\n## Style\nTerse.")
    assert "Use tools." not in prompt
    assert "Terse." in prompt


def test_subheadings_stay_inside_their_section():
    prompt = _build("plan", "## Mode: Act (with Tools)\n### Sub\nUse tools.")
    assert "Use tools." not in prompt


def test_duplicate_act_sections_give_one_plan_section():
    prompt = _build("plan", "## Mode: Act (with Tools)\nA\n"
                            "## Mode: Act\nB\n## Style\nTerse.")
    assert prompt.count(PLAN_MODE) == 1


def test_sections_are_separated_by_a_blank_line():
    prompt = _build("plan", get_default_system_prompt() + "\nBe terse.")
    assert PLAN_MODE + "\n\n## Important FreeCAD Notes" in prompt
    for part in (PLAN_MODE, *REFERENCE):
        i = prompt.index(part)
        assert prompt[i - 2:i] == "\n\n"
        assert prompt[i + len(part):].startswith("\n")


@pytest.mark.parametrize("mode, section", NO_TOOLS)
def test_default_static_prompt_layout(mode, section):
    expected = "\n".join(
        x for part in (IDENTITY, section, *REFERENCE) for x in (part, ""))
    assert get_default_system_prompt(mode, False) == expected


def test_default_prompt_is_composed_as_before():
    for mode, section in NO_TOOLS:
        prompt = _build(mode, "")
        for part in (section, *REFERENCE):
            assert prompt.count(part) == 1
