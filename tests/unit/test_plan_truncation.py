"""Regression tests for issue #50 — Plan-mode Execute button missing on long plans.

Root cause: a plan that hits max_tokens is cut off mid-code-block, so the closing
``` fence never arrives. Both fence regexes require it, so the block rendered as
plain text and no Execute/Copy buttons were emitted — with no signal to the user
that the response had been truncated at all.

Covers three defects:
  1. executor's fence regex only matched ```python, so ```py / bare ``` blocks
     rendered as code but never got an Execute button (independent of truncation).
  2. A truncated (unterminated) block produced no code block and no buttons.
  3. finish_reason="length" / stop_reason="max_tokens" was discarded, so nothing
     told the user the plan was cut off. That half now lives in
     test_reasoning_on_text_only_turns.py: #84 moved Plan mode onto the event
     stream, and the parser these tests drove no longer runs in production.
"""

from freecad_ai.core.executor import extract_code_blocks, extract_truncated_block
from freecad_ai.ui.message_view import render_message, render_plan_buttons

TRUNCATED_PLAN = """Here is the plan:

```python
import Part, math
bolt_circle_dia = 46.0
for angle_deg in [45, 135, 225, 315]:
    z = motor"""


class TestFenceLanguageTolerance:
    """Defect 1 — executor must match the same Python blocks the renderer styles."""

    def test_python_tag(self):
        blocks = extract_code_blocks("```python\na = 1\n```")
        assert blocks == ["a = 1\n"]

    def test_py_tag(self):
        blocks = extract_code_blocks("```py\na = 1\n```")
        assert blocks == ["a = 1\n"], "```py must yield an Execute button"

    def test_bare_fence(self):
        blocks = extract_code_blocks("```\na = 1\n```")
        assert blocks == ["a = 1\n"], "untagged fence must yield an Execute button"

    def test_uppercase_tag(self):
        assert extract_code_blocks("```Python\na = 1\n```") == ["a = 1\n"]

    def test_non_python_languages_are_ignored(self):
        """Must NOT broaden to every language — these get executed as Python."""
        for lang in ("bash", "json", "sh", "xml", "javascript"):
            text = f"```{lang}\nrm -rf /\n```"
            assert extract_code_blocks(text) == [], f"```{lang} must not be executable"


class TestTruncatedBlock:
    """Defect 2 — a cut-off block is recoverable for Copy, but never executable."""

    def test_truncated_block_is_not_executable(self):
        assert extract_code_blocks(TRUNCATED_PLAN) == [], \
            "a block cut off mid-expression must never get an Execute button"

    def test_truncated_block_is_extracted_for_copy(self):
        partial = extract_truncated_block(TRUNCATED_PLAN)
        assert partial is not None
        assert "bolt_circle_dia = 46.0" in partial
        assert partial.rstrip().endswith("z = motor")

    def test_closed_block_is_not_reported_as_truncated(self):
        assert extract_truncated_block("```python\na = 1\n```\nDone.") is None

    def test_prose_without_fences_is_not_truncated(self):
        assert extract_truncated_block("Just a plan, no code at all.") is None

    def test_trailing_prose_after_closed_block_is_not_truncated(self):
        text = "```python\na = 1\n```\nThen do the rest by hand."
        assert extract_truncated_block(text) is None

    def test_inline_backticks_are_not_mistaken_for_a_fence(self):
        assert extract_truncated_block("Set the `radius` to 5mm.") is None

    def test_second_block_truncated_after_a_complete_one(self):
        text = "```python\na = 1\n```\nthen:\n```python\nb = 2"
        assert extract_code_blocks(text) == ["a = 1\n"]
        partial = extract_truncated_block(text)
        assert partial is not None
        assert partial.strip() == "b = 2"


class TestPlanButtons:
    """Defect 2 — truncated code gets Copy, but Execute is withheld."""

    def test_complete_block_offers_execute_and_copy(self):
        html = render_plan_buttons("a = 1")
        assert "execute:" in html
        assert "copy:" in html

    def test_truncated_block_offers_copy_only(self):
        html = render_plan_buttons("a = 1", allow_execute=False)
        assert "execute:" not in html, "truncated code must not be executable"
        assert "copy:" in html


class TestTruncatedRendering:
    """Defect 2 — the partial script still renders as a styled code block."""

    def test_unterminated_fence_renders_as_code_block(self):
        html = render_message("assistant", TRUNCATED_PLAN)
        assert "font-family: monospace" in html, \
            "truncated code must render as a code block, not plain prose"
        assert "bolt_circle_dia = 46.0" in html

    def test_prose_before_truncated_fence_is_preserved(self):
        html = render_message("assistant", TRUNCATED_PLAN)
        assert "Here is the plan:" in html
