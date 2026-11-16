"""The tool rules our chat sends are shared with the MCP server (#139)."""

import hashlib

from freecad_ai.core import system_prompt, tool_guidance


def test_act_mode_tools_unchanged():
    # Recorded 2026-11-16 before the split. Prompt caching (#47) needs the
    # chat prompt byte-identical, so moving the text must not change it.
    h = hashlib.sha256(system_prompt.ACT_MODE_TOOLS.encode()).hexdigest()
    assert h == "b2c8f885d26391c25c98a6ebf9d81bd75f96101188320def0bb05556d10f3b95"


def test_act_mode_tools_ends_with_the_shared_guidance():
    assert system_prompt.ACT_MODE_TOOLS.endswith(tool_guidance.TOOL_GUIDANCE)
    assert tool_guidance.TOOL_GUIDANCE.startswith("**Tool calling strategy:**")


def test_guidance_has_no_chat_framing():
    assert "Act** mode" not in tool_guidance.TOOL_GUIDANCE
    assert "## Mode" not in tool_guidance.TOOL_GUIDANCE
