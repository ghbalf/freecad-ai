"""The tool rules our chat sends are shared with the MCP server (#139)."""

import hashlib

from freecad_ai.core import system_prompt, tool_guidance


def test_act_mode_tools_unchanged():
    # Prompt caching (#47) needs the chat prompt byte-identical across
    # unrelated changes. Re-record only on a deliberate rule change (each one
    # costs every user one cache miss) — last: create/open_document (#149).
    h = hashlib.sha256(system_prompt.ACT_MODE_TOOLS.encode()).hexdigest()
    assert h == "8128b4f51256e615f7cf02f3332b3fae0024ef5a053026314cd45b8af313aa37"


def test_act_mode_tools_ends_with_the_shared_guidance():
    assert system_prompt.ACT_MODE_TOOLS.endswith(tool_guidance.TOOL_GUIDANCE)
    assert tool_guidance.TOOL_GUIDANCE.startswith("**Tool calling strategy:**")


def test_guidance_has_no_chat_framing():
    assert "Act** mode" not in tool_guidance.TOOL_GUIDANCE
    assert "## Mode" not in tool_guidance.TOOL_GUIDANCE


# Claude Code 2.1.325 cut our instructions at ~4 KB ("[truncated]" after
# char ~4081 of 5268); measured with a live `claude -p` session (#144).
CLIENT_CAP = 4000
KEY_RULES = (
    "Always create a PartDesign Body with `create_body`",
    "**Important — preserve parametric history:**",
    "Execute only what the user requests.",
    "**Important — first-class tools over raw code:**",
)


def _paragraph_end(text, start):
    end = text.find("\n\n", start)
    return len(text) if end == -1 else end


def test_key_rules_survive_a_4k_client_cap():
    from freecad_ai.mcp.server import SERVER_INSTRUCTIONS
    for rule in KEY_RULES:
        start = SERVER_INSTRUCTIONS.find(rule)
        assert start != -1, rule
        assert _paragraph_end(SERVER_INSTRUCTIONS, start) <= CLIENT_CAP, rule


def test_server_text_reorders_but_drops_nothing():
    from freecad_ai.mcp.server import SERVER_INSTRUCTIONS
    chat_blocks = sorted(tool_guidance.TOOL_GUIDANCE.split("\n\n"))
    server_blocks = SERVER_INSTRUCTIONS.split("\n\n")[1:]  # minus the intro
    assert sorted(server_blocks) == chat_blocks


def test_enclosure_pattern_comes_last_for_agents():
    """Least general block last: a cap cuts it first."""
    from freecad_ai.mcp.server import SERVER_INSTRUCTIONS
    assert SERVER_INSTRUCTIONS.split("\n\n")[-1].startswith(
        "**Enclosure construction pattern**")
