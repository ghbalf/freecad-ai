from freecad_ai.core.loop_control import (
    REPEAT_ABORT_AT,
    REPEAT_NUDGE_AT,
    call_signature,
    repeat_intervention,
    repeat_nudge,
    should_continue_loop,
    update_failure_streak,
)


def test_bounded_continues_until_limit():
    assert should_continue_loop(30, 0, False) is True
    assert should_continue_loop(30, 29, False) is True
    assert should_continue_loop(30, 30, False) is False


def test_endless_always_continues():
    assert should_continue_loop(0, 0, False) is True
    assert should_continue_loop(0, 100000, False) is True


def test_interrupt_stops_regardless():
    assert should_continue_loop(30, 0, True) is False
    assert should_continue_loop(0, 0, True) is False


class TestCallSignature:
    def test_identical_calls_share_a_signature(self):
        a = call_signature("execute_code", {"code": "print(1)"})
        b = call_signature("execute_code", {"code": "print(1)"})
        assert a == b

    def test_key_order_does_not_matter(self):
        """The model may serialise the same arguments in either order."""
        a = call_signature("t", {"x": 1, "y": 2})
        b = call_signature("t", {"y": 2, "x": 1})
        assert a == b

    def test_different_arguments_differ(self):
        a = call_signature("execute_code", {"code": "print(1)"})
        b = call_signature("execute_code", {"code": "print(2)"})
        assert a != b

    def test_same_arguments_to_a_different_tool_differ(self):
        assert call_signature("a", {"x": 1}) != call_signature("b", {"x": 1})

    def test_unserialisable_arguments_do_not_raise(self):
        """A coarse signature is acceptable; an exception here would break the loop."""
        assert call_signature("t", {"obj": object()})


class TestFailureStreak:
    def test_a_success_clears_the_streak(self):
        streak = update_failure_streak(("sig", 4), "sig", failed=False)
        assert streak == ("", 0)

    def test_repeated_failure_accumulates(self):
        streak = ("", 0)
        for expected in (1, 2, 3):
            streak = update_failure_streak(streak, "sig", failed=True)
            assert streak == ("sig", expected)

    def test_a_different_failing_call_restarts_the_count(self):
        """Exploring counts as progress even when every attempt fails."""
        streak = update_failure_streak(("sig", 4), "other", failed=True)
        assert streak == ("other", 1)


class TestIntervention:
    def test_early_failures_are_left_alone(self):
        assert repeat_intervention(0) == ""
        assert repeat_intervention(REPEAT_NUDGE_AT - 1) == ""

    def test_nudge_then_abort(self):
        assert repeat_intervention(REPEAT_NUDGE_AT) == "nudge"
        assert repeat_intervention(REPEAT_ABORT_AT - 1) == "nudge"
        assert repeat_intervention(REPEAT_ABORT_AT) == "abort"
        assert repeat_intervention(REPEAT_ABORT_AT + 25) == "abort"

    def test_the_nudge_names_the_tool_and_the_count(self):
        text = repeat_nudge("execute_code", 3)
        assert "execute_code" in text
        assert "3" in text


def test_the_session_that_motivated_this():
    """A real session sent one byte-identical execute_code call 30 times.

    Replaying it: the guard nudges partway in and stops the loop well before the
    turn limit, instead of letting it run until the user gives up and hits stop.
    """
    call = {"code": "types = App.Type.getTypeIdList()"}
    streak = ("", 0)
    outcomes = []
    for _ in range(30):
        streak = update_failure_streak(
            streak, call_signature("execute_code", call), failed=True
        )
        outcomes.append(repeat_intervention(streak[1]))

    assert outcomes.index("nudge") == REPEAT_NUDGE_AT - 1
    assert outcomes.index("abort") == REPEAT_ABORT_AT - 1
