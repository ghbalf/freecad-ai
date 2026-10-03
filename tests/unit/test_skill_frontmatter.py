"""Agent Skills frontmatter subset, parsed without PyYAML."""

from freecad_ai.extensions.skill_frontmatter import parse_frontmatter


def test_no_frontmatter_returns_text_as_body():
    assert parse_frontmatter("# Title\nBody\n") == ({}, "# Title\nBody\n")


def test_plain_and_quoted_scalars():
    fm, body = parse_frontmatter(
        "---\nname: gear\ndescription: \"Holes: clearance\"\n"
        "license: 'It''s MIT'\n---\n# Gear\n")
    assert fm == {"name": "gear", "description": "Holes: clearance",
                  "license": "It's MIT"}
    assert body == "# Gear\n"


def test_folded_block_scalar():
    fm, _ = parse_frontmatter(
        "---\ndescription: >\n  Line one\n  continues here.\n\n  Second para.\nname: x\n---\n")
    assert fm["description"] == "Line one continues here.\nSecond para."
    assert fm["name"] == "x"


def test_literal_block_scalar_with_chomping_indicator():
    fm, _ = parse_frontmatter("---\ndescription: |-\n  a\n  b\n---\n")
    assert fm["description"] == "a\nb"


def test_metadata_map_and_allowed_tools_list():
    fm, _ = parse_frontmatter(
        "---\nmetadata:\n  author: alf\n  version: \"1.0\"\n"
        "allowed-tools:\n  - Read\n  - Bash\n---\n")
    assert fm["metadata"] == {"author": "alf", "version": "1.0"}
    assert fm["allowed-tools"] == ["Read", "Bash"]


def test_inline_list_and_string_allowed_tools():
    assert parse_frontmatter("---\nallowed-tools: [Read, Grep]\n---\n")[0] == {
        "allowed-tools": ["Read", "Grep"]}
    assert parse_frontmatter("---\nallowed-tools: Read Grep\n---\n")[0] == {
        "allowed-tools": "Read Grep"}


def test_crlf_frontmatter_is_parsed():
    fm, body = parse_frontmatter("---\r\nname: win\r\ndescription: From Windows\r\n---\r\n# W\r\n")
    assert fm == {"name": "win", "description": "From Windows"}
    assert body == "# W\r\n"


def test_unterminated_frontmatter_is_body():
    text = "---\nname: x\n# never closed\n"
    assert parse_frontmatter(text) == ({}, text)


def test_garbage_lines_are_ignored_not_raised():
    fm, _ = parse_frontmatter("---\n: nope\n  stray indent\nname: ok\n{weird\n---\n")
    assert fm == {"name": "ok"}


def test_zero_indent_list_is_parsed():
    assert parse_frontmatter("---\nallowed-tools:\n- Read\n- Bash\nname: x\n---\n")[0] == {
        "allowed-tools": ["Read", "Bash"], "name": "x"}


def test_utf8_bom_is_stripped():
    fm, body = parse_frontmatter("﻿---\nname: bom\n---\n# B\n")
    assert fm == {"name": "bom"}
    assert body == "# B\n"
