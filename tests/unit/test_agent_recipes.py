"""Commands for connecting an external agent to our MCP server (#139)."""

import json
import shlex

from freecad_ai.agent_recipes import (
    DEFAULT_AGENT_RECIPES, dialable_url, generic_snippets, render_command,
    valid_recipes)

URL = "http://127.0.0.1:3000/mcp"
CLAUDE = next(r for r in DEFAULT_AGENT_RECIPES if r["name"] == "Claude Code")
VALID = {"name": "Agent", "template": "agent add {url}{header_args}",
         "header_arg": " -H {header}", "docs_url": "https://example.org",
         "tested": False}


def test_claude_command_without_token():
    assert render_command(CLAUDE, URL, None) == (
        "claude mcp add --transport http freecad http://127.0.0.1:3000/mcp")


def test_claude_command_with_token():
    assert render_command(CLAUDE, URL, "tok").endswith(
        "-H 'Authorization: Bearer tok'")


def test_token_needing_quotes():
    cmd = render_command(CLAUDE, URL, "a'b $x")
    assert shlex.split(cmd)[-1] == "Authorization: Bearer a'b $x"


def test_wildcard_host_shows_loopback():
    assert dialable_url("0.0.0.0", 3000) == "http://127.0.0.1:3000/mcp"
    assert dialable_url("::", 3000) == "http://[::1]:3000/mcp"


def test_malformed_entries_are_skipped():
    entries = [42, {"name": "x"}, {**VALID, "template": "{nope}"},
               {**VALID, "tested": "yes"}, VALID]
    kept, warnings = valid_recipes(entries)
    assert kept == [VALID]
    assert len(warnings) == 4


def test_valid_recipes_never_raises_on_a_non_list():
    assert valid_recipes(None)[0] == []
    assert valid_recipes("claude")[0] == []


def test_defaults_are_valid():
    kept, warnings = valid_recipes(DEFAULT_AGENT_RECIPES)
    assert warnings == []
    assert len(kept) == len(DEFAULT_AGENT_RECIPES)
    assert all(r["docs_url"].startswith("https://") for r in kept)
    assert [r["name"] for r in kept if r["tested"] is True] == ["Claude Code"]


def test_generic_json_omits_headers_without_token():
    snip = generic_snippets(URL, None)
    assert snip["header"] == ""
    assert json.loads(snip["json"]) == {
        "mcpServers": {"freecad": {"type": "http", "url": URL}}}


def test_generic_json_with_token():
    snip = generic_snippets(URL, "tok")
    assert snip["url"] == URL
    assert snip["header"] == "Authorization: Bearer tok"
    server = json.loads(snip["json"])["mcpServers"]["freecad"]
    assert server["headers"]["Authorization"] == "Bearer tok"
