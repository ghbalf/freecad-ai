"""The tool rules our chat sends are shared with the MCP server (#139)."""

import hashlib

from freecad_ai.core import system_prompt, tool_guidance


def test_act_mode_tools_unchanged():
    # Prompt caching (#47) needs the chat prompt byte-identical across
    # unrelated changes. Re-record only on a deliberate rule change (each one
    # costs every user one cache miss) — last: shorter prompt (#159).
    h = hashlib.sha256(system_prompt.ACT_MODE_TOOLS.encode()).hexdigest()
    assert h == "da12d188946a16139f0644ceb5c1f460d63fc022cddf360026be9383807224f8"


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


# --- shorter Act prompt (#159) ------------------------------------------------

# Tools whose task→tool line was dropped: the tool description must carry it.
DROPPED = (
    "loft_sketches", "sweep_sketch", "transform_object", "fillet_edges",
    "chamfer_edges", "create_wedge", "scale_object", "section_object",
    "linear_pattern", "polar_pattern", "mirror_feature", "shell_object",
    "measure", "list_faces", "list_edges", "create_document", "open_document",
    "save_document", "close_document", "get_document_state",
    "delete_object", "undo", "select_geometry",
    "capture_viewport", "set_view", "zoom_object", "execute_code",
)

KEPT_BULLETS = (
    "- For solid shapes (box, cylinder, sphere, cone, torus): use `create_primitive` (auto-creates a Body; pass body_name to add to an existing Body, operation=\"subtractive\" to cut)",
    "- For complex profiles or swept shapes: `create_body` → `create_sketch` → `pad_sketch` / `pocket_sketch` / `revolve_sketch`",
    "- To sketch on a SELECTED or named planar face of an existing solid (e.g. \"sketch on the selected face\"): use `create_sketch` with `support` + `face` (get the face name from `list_faces`). It handles the attachment, planar-face validation, and offset — do NOT hand-write a macro that sets `AttachmentSupport`/`MapMode`.",
    "- To drill a hole / cut a feature into a SINGLE existing solid: add a subtractive feature INSIDE that Body — `create_primitive(operation=\"subtractive\", body_name=...)` or `pocket_sketch`. Do NOT use `boolean_operation` for this; a Part boolean buries the original sketch/pad and makes the model history uneditable.",
    "- For booleans between two SEPARATE objects: use `boolean_operation` (it auto-uses a parametric PartDesign::Boolean when both are Bodies, preserving history)",
    "- For chaining multiple transformations (e.g. linear pattern + mirror) into one feature: use `multi_transform` — accepts multiple features to transform as a group (order matters: last feature = tip)",
    "- For changing object properties (length, width, label, visibility, etc.): use `modify_property`",
    "- For reading or changing parameters in a VarSet or Spreadsheet: use `read_variable_set`/`edit_variable_set` or `read_spreadsheet`/`edit_spreadsheet` — never `modify_property` on a spreadsheet cell (the change is lost on reload)",
    "- For exporting to STEP, STL, or other formats: use `export_model`",
)


def test_every_tool_dropped_from_the_guidance_is_described():
    from freecad_ai.tools.setup import create_default_registry
    tools = {t.name: t for t in
             create_default_registry(include_mcp=False).list_tools()}
    for name in DROPPED:
        assert name in tools, name
        assert len(tools[name].description.strip()) > 20, name


def test_kept_rules_are_present():
    """Word for word, and nothing else in the strategy list."""
    strategy, rest = tool_guidance.TOOL_GUIDANCE.split("\n\n", 1)
    assert strategy.split("\n")[1:] == list(KEPT_BULLETS)
    for rule in KEY_RULES:
        assert rule in rest
    assert rest.split("\n\n")[-1].startswith("**Enclosure construction pattern**")


def test_default_act_prompt_is_short():
    # 6777 characters before #159.
    assert len(system_prompt.get_default_system_prompt()) < 4900
