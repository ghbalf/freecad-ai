"""Commands that connect an external agent to our MCP server (#139).

The recipes a user sees live in their config (``mcp_agent_recipes``), not
here: agent CLIs change their syntax faster than we release, so the user
must be able to fix an entry. ``DEFAULT_AGENT_RECIPES`` only seeds that
key once and backs "Reset to defaults". Append-only, like the provider list.

Stdlib only, and no ``freecad_ai.mcp`` import: ``config`` reads this module.
"""

import copy
import json
import os
import shlex
import string

# Each syntax is copied from the docs_url page. Only Claude Code was run by
# the maintainer. Codex is absent on purpose: `codex mcp add` takes a bearer
# token only via an environment-variable name, not as a header.
DEFAULT_AGENT_RECIPES = (
    {
        "name": "Claude Code",
        "template": "claude mcp add --transport http freecad {url}{header_args}",
        "header_arg": " -H {header}",
        "docs_url": "https://code.claude.com/docs/en/mcp",
        "tested": True,
    },
    {
        "name": "Gemini CLI",
        "template": "gemini mcp add --transport http{header_args} freecad {url}",
        "header_arg": " --header {header}",
        "docs_url": "https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md",
        "tested": False,
    },
    {
        "name": "Qwen Code",
        "template": "qwen mcp add --transport http freecad {url}{header_args}",
        "header_arg": " --header {header}",
        "docs_url": "https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/mcp.md",
        "tested": False,
    },
)

_FIELDS = {"name": str, "template": str, "header_arg": str,
           "docs_url": str, "tested": bool}
_ALLOWED = {"template": {"url", "header_args"}, "header_arg": {"header"}}


def dialable_url(host: str, port: int) -> str:
    """The URL an agent on this machine dials; a wildcard bind is not one."""
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/mcp"


def _placeholders(text: str) -> set:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}


def _problem(entry) -> str | None:
    if not isinstance(entry, dict):
        return "not an object"
    for key, kind in _FIELDS.items():
        if not isinstance(entry.get(key), kind):
            return f"field {key!r} missing or not {kind.__name__}"
    for key, allowed in _ALLOWED.items():
        try:
            unknown = _placeholders(entry[key]) - allowed
        except ValueError as e:
            return f"field {key!r}: {e}"
        if unknown:
            return f"field {key!r} has unknown placeholder(s) {sorted(unknown)}"
    return None


def valid_recipes(entries) -> tuple[list[dict], list[str]]:
    """Usable entries, and one warning per skipped one. Never raises."""
    if not isinstance(entries, (list, tuple)):
        return [], [f"mcp_agent_recipes is not a list: {type(entries).__name__}"]
    kept, warnings = [], []
    for i, entry in enumerate(entries):
        problem = _problem(entry)
        if problem:
            warnings.append(f"mcp_agent_recipes[{i}] skipped: {problem}")
        else:
            kept.append(entry)
    return kept, warnings


def _auth_header(token: str) -> str:
    return f"Authorization: Bearer {token}"


def render_command(recipe: dict, url: str, token: str | None) -> str:
    header_args = ""
    if token:
        header_args = recipe["header_arg"].format(
            header=shlex.quote(_auth_header(token)))
    return recipe["template"].format(url=url, header_args=header_args)


def generic_snippets(url: str, token: str | None) -> dict:
    """URL, header line and a JSON block: built in code, never stale."""
    server: dict = {"type": "http", "url": url}
    if token:
        server["headers"] = {"Authorization": f"Bearer {token}"}
    return {
        "url": url,
        "header": _auth_header(token) if token else "",
        "json": json.dumps({"mcpServers": {"freecad": server}}, indent=2),
    }


def _defaults() -> list[dict]:
    return copy.deepcopy(list(DEFAULT_AGENT_RECIPES))


def seed_recipes(cfg) -> bool:
    """Fill an absent key (None) with the defaults, once. ``[]`` stays empty."""
    if cfg.mcp_agent_recipes is not None:
        return False
    cfg.mcp_agent_recipes = _defaults()
    return True


def reset_recipes(current: list, config_dir: str, now) -> tuple[list, str | None]:
    """The defaults, after backing ``current`` up when it differs from them.

    The backup goes to ``<config_dir>/mcp_agent_recipes.<YYYYMMDD-HHMMSS>.json``
    (``-1``, ``-2`` … on a same-second clash; never overwritten). An OSError
    from writing it propagates, so a caller cannot replace what it failed to
    save. Neither mutates ``current`` nor saves the config.
    """
    if current == list(DEFAULT_AGENT_RECIPES):
        return _defaults(), None
    stem = os.path.join(
        config_dir, f"mcp_agent_recipes.{now.strftime('%Y%m%d-%H%M%S')}")
    data = json.dumps(current, indent=2)
    n = 0
    while True:
        path = f"{stem}{f'-{n}' if n else ''}.json"
        try:
            with open(path, "x") as f:  # "x": fail rather than overwrite
                f.write(data)
            return _defaults(), path
        except FileExistsError:
            n += 1
