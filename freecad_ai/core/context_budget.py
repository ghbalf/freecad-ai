"""Does the fixed part of a request fit the context setting? (#158)

System prompt and tool schemas go out with every request, so compacting
the conversation can't make them smaller.
"""

import json

from ..i18n import translate


def estimate_tokens(part) -> int:
    """Rough estimate (chars / 4), as Conversation.estimated_tokens does."""
    if not part:
        return 0
    if not isinstance(part, str):
        part = json.dumps(part, ensure_ascii=False)
    return len(part) // 4


def fixed_part_warning(system_prompt: str, tools, window: int) -> str | None:
    """The warning text, or None if prompt + tools fit ``window``.

    Output tokens are left out: ``window`` is the compaction trigger and
    often set well below the model's real limit, so counting them would
    warn people whose requests work (e.g. the old 20,000 default).
    """
    prompt, tool_tokens = estimate_tokens(system_prompt), estimate_tokens(tools)
    total = prompt + tool_tokens
    if total <= window:
        return None
    msg = translate(
        "ChatDockWidget",
        "⚠ The system prompt (~{prompt} tokens) and tool definitions "
        "(~{tools}) add up to ~{total} tokens before any chat, more than "
        "this profile's \"Compact above\" setting ({window}). If that is "
        "your model's real context size, requests may fail or be cut off. "
        "Use a larger context on the server; if it is already larger, "
        "raise \"Compact above\".")
    if tool_tokens:
        msg += " " + translate(
            "ChatDockWidget",
            "Tool Reranking in AI Settings sends fewer tools.")
    return msg.format(prompt=prompt, tools=tool_tokens, total=total,
                      window=window)
