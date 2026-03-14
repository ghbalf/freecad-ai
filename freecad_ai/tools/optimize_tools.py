"""Optimize iteration tool for the skill optimizer.

Provides the optimize_iteration tool that runs the full optimization loop:
evaluate SKILL.md, use LLM to suggest modifications, re-evaluate, repeat.
Also manages optimization session state (start/stop, iteration tracking,
best-score comparison).
"""

import json
import logging
import time
from typing import Optional

from .registry import ToolDefinition, ToolParam, ToolResult

logger = logging.getLogger(__name__)

# ── Module-level optimization session state ──────────────────────────────

_active_state = None  # OptimizationState
_active_config: Optional[dict] = None
_iteration: int = 0


def start_optimization(state, config: dict) -> None:
    """Activate optimization session globals."""
    global _active_state, _active_config, _iteration
    _active_state = state
    _active_config = config
    _iteration = 0


def stop_optimization() -> None:
    """Clear optimization session globals."""
    global _active_state, _active_config, _iteration
    _active_state = None
    _active_config = None
    _iteration = 0


# ── Strategy instructions ────────────────────────────────────────────────

STRATEGY_INSTRUCTIONS = {
    "conservative": (
        "Make only small, targeted changes to the SKILL.md. Focus on fixing "
        "the single lowest-scoring metric without altering parts that already "
        "score well. Prefer adding clarifying instructions over rewriting "
        "existing ones. Avoid restructuring the overall approach."
    ),
    "balanced": (
        "Make moderate changes to improve the weakest metrics while preserving "
        "strengths. You may restructure sections that are underperforming, but "
        "keep the overall strategy intact. Consider adjusting tool call "
        "sequences, adding error-handling hints, and refining geometry "
        "parameters."
    ),
    "aggressive": (
        "You are free to make significant changes including restructuring the "
        "entire SKILL.md approach. Consider alternative geometry strategies, "
        "different tool call orderings, and fundamentally different ways to "
        "achieve the desired result. Preserve only what is clearly working."
    ),
}

# ── Prompt template for LLM skill modification ──────────────────────────

MODIFICATION_PROMPT = """\
You are improving a FreeCAD AI SKILL.md file based on test results.

## Current SKILL.md

```
{skill_content}
```

## Test Results (iteration {iteration}, score: {score:.4f})

{results_text}

## Strategy

{strategy_instruction}

## FreeCAD Naming Rules
- Sketches are named "Sketch", "Sketch001", "Sketch002" etc. (NOT "Sketch0", "Sketch1")
- Bodies may be renamed by FreeCAD (e.g., "Body" instead of "EnclosureBase")
- Always tell the LLM to use `get_document_state` to check actual names before referencing objects
- Use explicit labels in create_sketch and create_body calls

## Instructions

Analyze the errors above and modify the SKILL.md to fix them. Return the complete \
modified SKILL.md between ```skill markers. Focus on fixing the specific errors shown.

```skill
(your modified SKILL.md here)
```
"""

# ── Prompt template for inject_prompt ────────────────────────────────────

OPTIMIZATION_PROMPT_TEMPLATE = """\
# Skill Optimization Task

Optimizing skill **{skill_name}** with {iterations} iterations.

Test cases: {test_cases_formatted}

The `optimize_iteration` tool will run the full optimization loop automatically. \
Call it once — it handles all iterations internally.
"""


# ── Core optimization logic ─────────────────────────────────────────────

def _evaluate_once(skill_name, skill_content, parsed_cases, runs_per_test):
    """Run one evaluation cycle. Returns (results, score, details_text)."""
    from ..extensions.skill_evaluator import SkillEvaluator, compute_composite_score

    evaluator = SkillEvaluator(
        config=_active_config,
        tool_executor=_active_config.get("_tool_executor"),
    )
    results = evaluator.evaluate(
        skill_name=skill_name,
        skill_content=skill_content,
        test_cases=parsed_cases,
        runs_per_test=runs_per_test,
    )

    score = compute_composite_score(results, _active_config)

    # Build human-readable details
    lines = []
    for r in results:
        lines.append(f"Test case: {r.test_case}")
        lines.append(f"  completed={r.completed}, tool_calls={r.tool_calls}, "
                     f"errors={r.errors}")
        if r.error_messages:
            for msg in r.error_messages[:10]:
                lines.append(f"  ERROR: {msg}")
        if r.run_scores:
            lines.append(f"  run_scores={r.run_scores}")

    return results, score, "\n".join(lines)


def _ask_llm_for_modification(skill_content, iteration, score, results_text,
                               strategy_instruction):
    """Ask the LLM to suggest a modified SKILL.md based on results."""
    from ..llm.client import create_client_from_config

    client = create_client_from_config()
    prompt = MODIFICATION_PROMPT.format(
        skill_content=skill_content,
        iteration=iteration,
        score=score,
        results_text=results_text,
        strategy_instruction=strategy_instruction,
    )

    try:
        response = client.send(
            [{"role": "user", "content": prompt}],
            system="You are a FreeCAD AI skill optimizer. Return only the modified SKILL.md.",
        )
    except Exception as e:
        logger.error("LLM modification request failed: %s", e)
        return None

    # Extract SKILL.md from ```skill ... ``` markers
    text = response
    if "```skill" in text:
        start = text.index("```skill") + len("```skill")
        end = text.index("```", start)
        return text[start:end].strip()
    elif "```" in text:
        # Try generic code block
        start = text.index("```") + 3
        # Skip language tag if present
        newline = text.index("\n", start)
        start = newline + 1
        end = text.index("```", start)
        return text[start:end].strip()
    else:
        # No markers — return the full response if it looks like a SKILL.md
        if text.strip().startswith("#"):
            return text.strip()
        return None


def _handle_optimize_iteration(
    skill_name: str,
    skill_content: str,
    test_cases: list,
    runs_per_test: int = 2,
) -> ToolResult:
    """Run the full optimization loop: evaluate → modify → re-evaluate → repeat."""
    global _iteration

    if _active_config is None:
        return ToolResult(
            success=False,
            output="No active optimization session. Call start_optimization first.",
            error="No active optimization session",
        )

    max_iterations = _active_config.get("iterations", 5)
    strategy = _active_config.get("strategy", "balanced")
    strategy_instruction = STRATEGY_INSTRUCTIONS.get(strategy, STRATEGY_INSTRUCTIONS["balanced"])
    tolerance = _active_config.get("tolerance", 0.01)

    # Parse test_cases
    parsed_cases = []
    for tc in test_cases:
        if isinstance(tc, str):
            try:
                parsed = json.loads(tc)
                if isinstance(parsed, dict):
                    parsed_cases.append(parsed)
                else:
                    parsed_cases.append({"args": tc})
            except (json.JSONDecodeError, ValueError):
                parsed_cases.append({"args": tc})
        elif isinstance(tc, dict):
            parsed_cases.append(tc)
        else:
            parsed_cases.append({"args": str(tc)})

    current_content = skill_content
    all_output_lines = []
    start_time = time.time()

    try:
        for i in range(max_iterations):
            _iteration += 1
            iteration = _iteration
            elapsed = time.time() - start_time

            logger.info("=== Optimization iteration %d/%d (%.0fs elapsed) ===",
                        i + 1, max_iterations, elapsed)

            # 1. Evaluate
            results, score, details_text = _evaluate_once(
                skill_name, current_content, parsed_cases, runs_per_test,
            )

            # 2. Keep/discard
            best_content, best_score = _active_state.get_best()
            if best_score == 0.0 or score >= best_score - tolerance:
                kept = True
                comparison = "new_best" if score > best_score else "within_tolerance"
            else:
                kept = False
                comparison = "discarded"

            _active_state.save_version(
                iteration=iteration,
                content=current_content,
                score=score,
                kept=kept,
                config=_active_config.get("model_config", {}),
            )

            iter_summary = (
                f"Iteration {iteration}: score={score:.4f} "
                f"(best={max(score, best_score):.4f}, {comparison})"
            )
            all_output_lines.append(iter_summary)
            all_output_lines.append(details_text)
            all_output_lines.append("")

            logger.info(iter_summary)

            # If discarded, restore best
            if not kept:
                current_content, _ = _active_state.get_best()
                all_output_lines.append("  → Restored previous best version")

            # 3. Ask LLM to modify (skip on last iteration)
            if i < max_iterations - 1:
                logger.info("Asking LLM for SKILL.md modifications...")
                modified = _ask_llm_for_modification(
                    current_content, iteration, score, details_text,
                    strategy_instruction,
                )
                if modified and modified != current_content:
                    current_content = modified
                    all_output_lines.append("  → SKILL.md modified by LLM")
                    logger.info("SKILL.md modified (%d chars)", len(modified))
                else:
                    all_output_lines.append("  → LLM returned no changes")
                    logger.info("LLM returned no changes")

    except Exception as e:
        logger.error("Optimization loop error: %s", e, exc_info=True)
        all_output_lines.append(f"\nOptimization stopped due to error: {e}")

    # Write final best version to SKILL.md
    _active_state.restore_best()
    final_content, final_score = _active_state.get_best()

    all_output_lines.append(f"\n=== Optimization complete ===")
    all_output_lines.append(f"Final best score: {final_score:.4f}")
    all_output_lines.append(f"Total iterations: {_iteration}")
    all_output_lines.append(f"Best version saved to SKILL.md")

    return ToolResult(
        success=True,
        output="\n".join(all_output_lines),
    )


# ── Tool definition factory ─────────────────────────────────────────────

def get_optimize_iteration_tool() -> ToolDefinition:
    """Return the optimize_iteration tool definition."""
    return ToolDefinition(
        name="optimize_iteration",
        description=(
            "Run the full skill optimization loop: evaluate the SKILL.md "
            "against test cases, use the LLM to suggest improvements, "
            "re-evaluate, and repeat for the configured number of iterations. "
            "Returns a summary of all iterations with scores."
        ),
        parameters=[
            ToolParam(
                name="skill_name",
                type="string",
                description="Name of the skill being optimized.",
                required=True,
            ),
            ToolParam(
                name="skill_content",
                type="string",
                description="Complete SKILL.md content to start optimizing.",
                required=True,
            ),
            ToolParam(
                name="test_cases",
                type="array",
                description="List of test case strings to run.",
                required=True,
                items={"type": "string"},
            ),
            ToolParam(
                name="runs_per_test",
                type="integer",
                description="Number of runs per test case for averaging.",
                required=False,
                default=2,
            ),
        ],
        handler=_handle_optimize_iteration,
        category="optimization",
    )
