"""Pure decision helper for the agentic tool loop bound."""


def should_continue_loop(max_turns: int, turn: int, interrupted: bool) -> bool:
    """Return whether the agentic loop should run another turn.

    max_turns == 0 means endless. An interruption always stops the loop.
    """
    if interrupted:
        return False
    if max_turns == 0:
        return True
    return turn < max_turns


def resolve_turn_outcome(truncated: bool, tool_calls: list, interrupted: bool) -> str:
    """Classify a finished turn: "stopped", "truncated", "done" or "continue".

    Precedence matters. An interruption is the user's explicit stop and outranks
    everything. Truncation halts next: a response cut off at the output limit can
    carry half-formed tool calls, and acting on a partial payload is worse than
    stopping (issue #52). Only an intact turn earns the right to continue.
    """
    if interrupted:
        return "stopped"
    if truncated:
        return "truncated"
    return "continue" if tool_calls else "done"


def reasoning_to_persist(turn_thinking: str | None, strip_thinking: bool,
                         optimize_caching: bool,
                         api_style: str = "openai",
                         preserve_history: bool = True) -> str:
    """Return the thinking to carry from a finished turn into the history.

    The answer is "whatever the provider was actually shown, and nothing
    else". ``_tool_loop`` echoes ``reasoning_content`` back on every
    assistant turn unless the model rejects it, so leaving it out of the
    stored turn makes the next request re-render bytes the provider has
    already cached -- the #47 invariant, broken from the other side.

    ``strip_thinking`` means it was never sent (Gemma), so storing it would
    be the same divergence in reverse, and the same goes for Anthropic --
    it carries thinking as its own signed content block, which this key
    cannot represent and the loop never sends. Those two are hard
    exclusions: no switch may override them.

    Everything else is ``preserve_history``, and it defaults on. This used
    to ride on ``optimize_caching``, on the theory that keeping thinking was
    a cache optimisation. It is not. Moonshot's engineers report a clear,
    measurable drop in reply quality on turns whose ``reasoning_content`` is
    absent -- in ordinary multi-turn chat, not merely in tool loops -- and
    recommend preserving every turn's reasoning regardless of caching
    (forum thread 602). Keeping the flag as an escape hatch costs nothing;
    leaving quality behind a switch labelled for billing was the mistake.

    ``optimize_caching`` still forces preservation on, because a byte-for-
    byte prefix is exactly what that switch promises and it cannot be kept
    while dropping bytes the provider has already seen.
    """
    if strip_thinking or api_style == "anthropic":
        return ""
    if not (preserve_history or optimize_caching):
        return ""
    return turn_thinking or ""
