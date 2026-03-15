"""Skill evaluation framework for the skill optimizer."""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Result of evaluating a skill on a single test case."""

    test_case: str
    tool_calls: int = 0
    errors: int = 0
    retries: int = 0
    error_messages: list[str] = field(default_factory=list)
    measurements: dict = field(default_factory=dict)
    completed: bool = False
    visual_score: Optional[float] = None
    visual_assessment: str = ""
    run_scores: list[float] = field(default_factory=list)
    llm_error: bool = False  # True if run failed due to network/timeout (not skill quality)


class OptimizationState:
    """Manages the .optimize/ directory, history.json, and version files for a skill."""

    def __init__(self, skill_name: str, base_dir: str = ""):
        if not base_dir:
            from ..config import SKILLS_DIR
            base_dir = SKILLS_DIR
        self._skill_dir = os.path.join(base_dir, skill_name)
        self._opt_dir = os.path.join(self._skill_dir, ".optimize")
        self._history_path = os.path.join(self._opt_dir, "history.json")
        self._original_path = os.path.join(self._skill_dir, "SKILL.md.original")
        self._skill_path = os.path.join(self._skill_dir, "SKILL.md")

        os.makedirs(self._opt_dir, exist_ok=True)

        # Load existing history
        self._history: list[dict] = []
        if os.path.isfile(self._history_path):
            with open(self._history_path, "r") as f:
                self._history = json.load(f)

    def save_original(self, content: str) -> None:
        """Save original SKILL.md content. Does not overwrite if already exists."""
        if os.path.isfile(self._original_path):
            return
        with open(self._original_path, "w") as f:
            f.write(content)

    def save_version(
        self,
        iteration: int,
        content: str,
        score: float,
        kept: bool,
        config: Optional[dict] = None,
    ) -> None:
        """Save a versioned SKILL.md and append to history."""
        version_path = os.path.join(self._opt_dir, f"v{iteration}.md")
        with open(version_path, "w") as f:
            f.write(content)

        entry = {
            "iteration": iteration,
            "score": score,
            "kept": kept,
            "timestamp": time.time(),
            "file": f"v{iteration}.md",
        }
        if config is not None:
            entry["config"] = config

        self._history.append(entry)
        self._save_history()

    def get_best(self) -> tuple[str, float]:
        """Return (content, score) of the highest-scoring kept version."""
        best_entry = None
        for entry in self._history:
            if entry.get("kept", False):
                if best_entry is None or entry["score"] > best_entry["score"]:
                    best_entry = entry

        if best_entry is None:
            return "", 0.0

        version_path = os.path.join(self._opt_dir, best_entry["file"])
        with open(version_path, "r") as f:
            content = f.read()
        return content, best_entry["score"]

    def get_history(self) -> list[dict]:
        """Return the full history list."""
        return list(self._history)

    def restore_best(self) -> None:
        """Write the best version content to SKILL.md."""
        content, score = self.get_best()
        if content:
            with open(self._skill_path, "w") as f:
                f.write(content)

    def is_config_stale(self, current_config: dict) -> bool:
        """Check if the model/provider changed since the last recorded version."""
        for entry in reversed(self._history):
            if "config" in entry:
                return entry["config"] != current_config
        # No config recorded yet — treat as stale
        return True

    def _save_history(self) -> None:
        with open(self._history_path, "w") as f:
            json.dump(self._history, f, indent=2)


DEFAULT_WEIGHTS = {
    "completion":  0.30,
    "error_rate":  0.25,
    "correctness": 0.20,
    "efficiency":  0.10,
    "retries":     0.10,
    "visual":      0.05,
}


def _score_single(result: EvalResult, config: dict) -> tuple[float, float]:
    """Score a single test case. Returns (score, total_weight)."""
    metrics = config.get("metrics", list(DEFAULT_WEIGHTS.keys()))
    weights = config.get("weights", DEFAULT_WEIGHTS)
    budget = config.get("budget", 30)
    test_cases = config.get("test_cases", [])

    # Find expected dims for this test case
    expected_bbox = None
    for tc in test_cases:
        if tc.get("args") == result.test_case:
            expected_bbox = tc.get("expected_bbox")
            break

    scores = {}
    active_weights = {}

    if "completion" in metrics:
        scores["completion"] = 1.0 if result.completed else 0.0
        active_weights["completion"] = weights.get("completion", 0.30)

    if "error_rate" in metrics:
        if result.tool_calls > 0:
            scores["error_rate"] = 1.0 - (result.errors / result.tool_calls)
        else:
            scores["error_rate"] = 0.0
        active_weights["error_rate"] = weights.get("error_rate", 0.25)

    if "retries" in metrics:
        scores["retries"] = 1.0 - min(result.retries / 5, 1.0)
        active_weights["retries"] = weights.get("retries", 0.10)

    if "efficiency" in metrics:
        scores["efficiency"] = 1.0 - min(result.tool_calls / budget, 1.0)
        active_weights["efficiency"] = weights.get("efficiency", 0.10)

    if "correctness" in metrics and expected_bbox and result.measurements.get("bbox"):
        actual = result.measurements["bbox"]
        if len(actual) == len(expected_bbox) and all(e > 0 for e in expected_bbox):
            diffs = [abs(a - e) / e for a, e in zip(actual, expected_bbox)]
            scores["correctness"] = max(0.0, min(1.0, 1.0 - sum(diffs) / len(diffs)))
            active_weights["correctness"] = weights.get("correctness", 0.20)

    if "visual" in metrics and result.visual_score is not None:
        scores["visual"] = result.visual_score
        active_weights["visual"] = weights.get("visual", 0.05)

    total_weight = sum(active_weights.values())
    if total_weight == 0:
        return 0.0, 0.0

    weighted_sum = sum(
        scores[k] * active_weights[k] / total_weight
        for k in scores
    )
    return weighted_sum, total_weight


class SkillEvaluator:
    """Runs a skill in a headless agentic loop and collects metrics."""

    def __init__(self, config: dict, tool_executor=None):
        self._config = config
        self._tool_executor = tool_executor
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def evaluate(self, skill_name: str, skill_content: str,
                 test_cases: list[dict], runs_per_test: int = 2) -> list[EvalResult]:
        """Run skill against all test cases and return results."""
        from ..llm.client import create_client_from_config
        from ..core.system_prompt import build_system_prompt
        from ..llm.providers import get_api_style
        from ..config import get_config

        cfg = get_config()
        api_style = get_api_style(cfg.provider.name)
        system = build_system_prompt(mode="act", tools_enabled=True)
        client = create_client_from_config()

        from ..tools.setup import create_default_registry
        from ..tools.optimize_tools import get_eval_tools
        registry = create_default_registry(
            include_mcp=False, extra_tools=get_eval_tools())
        if self._tool_executor:
            logger.info("Tool executor: %s", type(self._tool_executor).__name__)
            self._tool_executor.set_registry(registry)
        # Build schema excluding internal eval tools (not for the LLM)
        all_schema = registry.to_openai_schema() if api_style != "anthropic" \
            else registry.to_anthropic_schema()
        tools_schema = [
            t for t in all_schema
            if not t.get("function", {}).get("name", "").startswith("_eval_")
        ]

        max_retries = self._config.get("max_retries", 2)

        results = []
        for tc_idx, tc in enumerate(test_cases):
            args = tc.get("args", "")
            logger.info("Test case %d/%d: %s", tc_idx + 1, len(test_cases), args)
            valid_results = []
            attempt = 0
            while len(valid_results) < runs_per_test and attempt < runs_per_test + max_retries:
                if self._cancelled:
                    break
                attempt += 1
                is_retry = attempt > runs_per_test
                if is_retry:
                    retry_num = attempt - runs_per_test
                    retry_delay = 5 * (2 ** (retry_num - 1))  # 5s, 10s, 20s, 40s...
                    logger.info("  Retry %d/%d (waiting %ds before retry)",
                                retry_num, max_retries, retry_delay)
                    time.sleep(retry_delay)
                else:
                    logger.info("  Run %d/%d", attempt, runs_per_test)
                doc_name = f"OptEval_{tc_idx}_{attempt}"
                self._create_document(doc_name)
                try:
                    result = self._run_skill_headless(
                        skill_content=skill_content,
                        test_args=args,
                        client=client,
                        tools_schema=tools_schema,
                        system_prompt=system,
                        api_style=api_style,
                    )
                    result.test_case = args
                    if result.llm_error:
                        logger.warning("  Run failed due to LLM/network error, "
                                       "will retry (%d retries left)",
                                       runs_per_test + max_retries - attempt)
                    else:
                        valid_results.append(result)
                finally:
                    self._close_document(doc_name)
            if valid_results:
                avg = self._average_results(valid_results, args)
                results.append(avg)
            else:
                # All runs failed with LLM errors — report it
                logger.error("  All runs failed for test case '%s'", args)
                results.append(EvalResult(
                    test_case=args,
                    completed=False,
                    llm_error=True,
                    error_messages=["All runs failed due to network/timeout errors"],
                ))
        return results

    def _create_document(self, name: str):
        """Create a fresh FreeCAD document via internal tool (main thread)."""
        if not self._tool_executor:
            return
        result = self._tool_executor.execute(
            "_eval_create_doc", {"name": name})
        if result.success:
            logger.info("Created eval document: %s", name)
        else:
            logger.error("Failed to create document '%s': %s", name, result.error)

    def _close_document(self, name: str):
        """Close a FreeCAD document via internal tool (main thread)."""
        if not self._tool_executor:
            return
        result = self._tool_executor.execute(
            "_eval_close_doc", {"name": name})
        if result.success:
            logger.info("Closed eval document: %s", name)
        else:
            logger.error("Failed to close document '%s': %s", name, result.error)

    def _run_skill_headless(self, skill_content: str, test_args: str,
                            client, tools_schema, system_prompt: str,
                            api_style: str = "openai") -> EvalResult:
        """Run a single skill execution and collect metrics."""
        from ..core.conversation import Conversation

        conv = Conversation()
        conv.compaction_enabled = False
        conv.add_user_message(f"{skill_content}\n\nArguments: {test_args}")

        budget = self._config.get("budget", 30)
        timeout = self._config.get("timeout", 300)
        start_time = time.time()

        tool_calls = 0
        errors = 0
        error_messages = []

        for _turn in range(budget):
            if self._cancelled:
                logger.info("Evaluation cancelled")
                break
            elapsed = time.time() - start_time
            if elapsed > timeout:
                logger.info("Evaluation timed out after %.0fs", elapsed)
                break

            logger.info("Eval turn %d/%d (%.0fs elapsed, %d tool calls)",
                        _turn + 1, budget, elapsed, tool_calls)
            messages = conv.get_messages_for_api(api_style=api_style)
            try:
                response = client.send_with_tools(
                    messages, system=system_prompt, tools=tools_schema
                )
            except Exception as e:
                logger.error("LLM call failed in eval: %s", e)
                error_messages.append(f"LLM error: {e}")
                return EvalResult(
                    test_case=test_args,
                    tool_calls=tool_calls,
                    errors=errors,
                    error_messages=error_messages,
                    completed=False,
                    llm_error=True,
                )

            if not response.tool_calls:
                conv.add_assistant_message(response.text)
                logger.info("Eval completed: %d tool calls, %d errors",
                            tool_calls, errors)
                return EvalResult(
                    test_case=test_args,
                    tool_calls=tool_calls,
                    errors=errors,
                    error_messages=error_messages,
                    completed=True,
                )

            # Add assistant message with tool calls
            conv.add_assistant_message(response.text, tool_calls=[
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ])

            for tc in response.tool_calls:
                if self._cancelled:
                    break
                tool_calls += 1
                logger.info("  Tool call #%d: %s", tool_calls, tc.name)
                if self._tool_executor:
                    result = self._tool_executor.execute(tc.name, tc.arguments)
                else:
                    from ..tools.registry import ToolResult as TR
                    result = TR(success=True, output="(no executor)")

                if not result.success:
                    errors += 1
                    error_messages.append(f"{tc.name}: {result.error}")

                conv.add_tool_result(
                    tc.id,
                    result.output if result.success else result.error,
                )

        return EvalResult(
            test_case=test_args,
            tool_calls=tool_calls,
            errors=errors,
            error_messages=error_messages,
            completed=False,
        )

    def _average_results(self, run_results: list[EvalResult],
                         test_case: str) -> EvalResult:
        """Average metrics across multiple runs of the same test case."""
        n = len(run_results)
        per_run_scores = [
            _score_single(r, self._config)[0] for r in run_results
        ]
        return EvalResult(
            test_case=test_case,
            tool_calls=sum(r.tool_calls for r in run_results) // n,
            errors=sum(r.errors for r in run_results) // n,
            retries=sum(r.retries for r in run_results) // n,
            error_messages=[msg for r in run_results for msg in r.error_messages],
            measurements=run_results[-1].measurements,
            completed=any(r.completed for r in run_results),
            visual_score=(
                sum(r.visual_score for r in run_results if r.visual_score is not None) / n
                if any(r.visual_score is not None for r in run_results) else None
            ),
            run_scores=per_run_scores,
        )


def compute_composite_score(results: list[EvalResult], config: dict) -> float:
    """Compute composite score across all test cases. Returns 0.0-1.0."""
    if not results:
        return 0.0
    scores = [_score_single(r, config)[0] for r in results]
    return sum(scores) / len(scores)
