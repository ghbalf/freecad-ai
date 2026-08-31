"""Pure decision helpers for the agentic tool loop bound."""

import json

# A model repeating a byte-identical call that keeps failing is not converging on its
# own. The first intervention is a nudge, because that is usually enough to break the
# pattern; the hard stop exists because a nudge the model ignores costs the user the
# rest of the session.
REPEAT_NUDGE_AT = 3
REPEAT_ABORT_AT = 5


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


def call_signature(name: str, arguments) -> str:
    """Stable identity of a tool call, so a byte-identical retry is recognisable.

    Arguments that will not serialise fall back to repr(): a coarser signature costs
    at worst a missed intervention, while raising here would break the tool loop.
    """
    try:
        return json.dumps([name, arguments], sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr((name, arguments))


def update_failure_streak(streak: tuple, signature: str, failed: bool) -> tuple:
    """Advance the consecutive-identical-failure streak, as (signature, count).

    Any success, and any call that differs from the last one, resets it. Only an
    unbroken run of the same failing call counts, so a model that is genuinely
    exploring - a different argument, a different tool - is never interrupted.
    """
    if not failed:
        return ("", 0)
    previous, count = streak
    if signature == previous:
        return (signature, count + 1)
    return (signature, 1)


def repeat_intervention(count: int) -> str:
    """Classify a streak length: "" to let it run, else "nudge" or "abort"."""
    if count >= REPEAT_ABORT_AT:
        return "abort"
    if count >= REPEAT_NUDGE_AT:
        return "nudge"
    return ""


def repeat_nudge(tool_name: str, count: int) -> str:
    """Note appended to a repeated failure's result.

    Addressed to the model, so it is deliberately not translated - this is prompt
    text, not UI text.
    """
    return (
        "\n\n[Loop guard: this exact {} call has now failed {} times in a row with "
        "the same error. Retrying it unchanged will fail again. Change approach, or "
        "tell the user what you are missing and ask them for it.]".format(
            tool_name, count
        )
    )
