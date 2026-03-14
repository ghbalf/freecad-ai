# Skill Optimizer — Design Spec

**Date**: 2026-03-15
**Status**: Draft
**Inspired by**: [autoresearch](https://github.com/karpathy/autoresearch) by Andrej Karpathy

## Overview

Automated skill optimization loop, inspired by autoresearch's iterate-evaluate-keep/discard cycle. The optimizer takes an existing SKILL.md, runs it against test cases, evaluates the results with a composite scoring metric, uses the LLM to modify the SKILL.md based on failure analysis, and repeats until the score converges or iterations are exhausted.

The user invokes `/optimize-skill` from the chat, a dialog collects configuration (skill, test cases, metrics, strategy), and the optimization runs as a standard agentic tool-calling loop — the LLM calls an `optimize_iteration` tool that handles deterministic evaluation.

## Architecture

```
/optimize-skill invocation
        │
        ▼
OptimizeSkillDialog (PySide2)
  - Skill picker, test cases, metrics, strategy
  - Returns config dict
        │
        ▼
handler.py → {"inject_prompt": optimizer_prompt}
        │
        ▼
LLM (optimizer conversation)
  │
  ├── Calls optimize_iteration(skill_content, test_cases, ...)
  │       │
  │       ▼
  │   SkillEvaluator
  │     For each test case × runs_per_test:
  │       1. Fresh document (App.newDocument, unique name)
  │       2. Headless agentic loop (separate Conversation + LLMClient)
  │          Tool calls dispatched to main thread via MainThreadToolExecutor
  │       3. Collect metrics (errors, retries, tool calls, measurements)
  │       4. Optional: capture_viewport → vision comparison
  │       5. Close document (App.closeDocument)
  │     Compute composite score
  │     Keep/discard based on all-time best score
  │     Save version to .optimize/
  │       │
  │       ▼
  │   Returns EvalResult to LLM
  │
  ├── Analyzes failures, modifies SKILL.md
  │
  └── Calls optimize_iteration again with modified SKILL.md
        ... repeats for N iterations ...
```

## Components

### 1. `/optimize-skill` Skill

**Location**: `skills/optimize-skill/`

- `SKILL.md` — description and fallback instructions (in case handler fails)
- `handler.py` — shows `OptimizeSkillDialog`, builds inject_prompt from config

The handler:
1. Launches `OptimizeSkillDialog`
2. If user clicks Start, collects config
3. Loads current SKILL.md of the target skill
4. Saves `SKILL.md.original` if not already saved
5. Returns `{"inject_prompt": formatted_optimizer_prompt}`

If the user passes arguments directly (e.g., `/optimize-skill enclosure`), the handler pre-selects the skill in the dialog.

### 2. OptimizeSkillDialog

**Location**: `freecad_ai/ui/optimize_dialog.py`

PySide2 QDialog with the following fields:

| Field | Widget | Default | Notes |
|-------|--------|---------|-------|
| Skill | QComboBox | First skill | From `SkillsRegistry.get_available()` |
| Test cases | QListWidget + QLineEdit + Add/Remove buttons | Empty | At least one required |
| Iterations | QSpinBox | 10 | Range: 1–50 |
| Runs per test | QSpinBox | 2 | Range: 1–5 |
| Strategy | QComboBox | Balanced | Conservative / Balanced / Aggressive |
| Completion | QCheckBox | Checked | Always recommended |
| Error rate | QCheckBox | Checked | |
| Geometric checks | QCheckBox | Checked | |
| Efficiency | QCheckBox | Checked | |
| Visual similarity | QCheckBox | Unchecked | Enables reference image picker |
| Reference image | QLabel + QPushButton | None | File picker, enabled when visual checked |

**Advanced section** (collapsed by default):

| Field | Widget | Default |
|-------|--------|---------|
| Metric weights (×6) | QDoubleSpinBox | See scoring section |
| Keep tolerance | QDoubleSpinBox | 0.05 |
| Tool call budget | QSpinBox | 30 |
| Run timeout | QSpinBox | 300 (seconds) |

**Validation on Start:**
- At least one test case
- At least two metrics enabled
- Warning dialog if iterations > 20

**Test case format**: Each test case is a dict with:

```python
{
    "args": "100x60x40mm, 2mm walls, snap-fit lid",   # passed to skill
    "expected_bbox": [100, 60, 40],                    # optional, for geometric correctness
    "expected_measurements": {"wall_thickness": 2.0},  # optional, extra checks
}
```

The "Add" button opens a small sub-dialog where the user enters the skill arguments (free text) and optionally specifies expected bounding box dimensions and measurements. If the user leaves the expected fields empty, geometric correctness is skipped for that test case.

### 3. `optimize_iteration` Tool

**Location**: `freecad_ai/tools/optimize_tools.py`

**Registration**: Registered conditionally — only added to the tool schema when an optimization session is active. The caller in `chat_widget.py` passes the extra tool via `create_default_registry(extra_tools=[optimize_iteration_tool])` when the optimization flag is set. This keeps the tools layer independent of the UI layer. During normal conversations, `extra_tools` is empty and the LLM never sees `optimize_iteration`.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `skill_name` | string | Yes | Name of the skill to evaluate |
| `skill_content` | string | Yes | Complete SKILL.md content to evaluate |
| `test_cases` | array of strings | Yes | Test case arguments (JSON-encoded dicts) |
| `runs_per_test` | integer | No | Runs per test case (default: 2) |

**Behavior:**
1. Write `skill_content` to a temp file as the candidate SKILL.md
2. For each test case × runs_per_test:
   a. Create fresh FreeCAD document: `App.newDocument(f"OptEval_{iteration}_{test}_{run}")`
   b. Run skill via headless agentic loop (tool calls dispatched to main thread)
   c. Collect metrics: errors, retries, tool_calls, measurements (via `measure`)
   d. If visual enabled: `capture_viewport` on the active view → vision LLM comparison
   e. Close document: `App.closeDocument(doc.Name)`
3. Average metrics across runs for each test case
4. Compute composite score
5. Compare to all-time best score → keep or discard
6. Save version to `.optimize/vN.md`
7. Update `history.json`
8. Return structured result

**Return value:**

```python
{
    "iteration": int,
    "composite_score": float,        # 0.0–1.0
    "best_score": float,             # all-time best
    "kept": bool,
    "strategy": str,                 # "failure_driven" or "holistic"
    "test_results": [
        {
            "test_case": str,
            "error_rate": float,
            "retries": int,
            "tool_calls": int,
            "errors": [str],         # specific error messages
            "measurements": dict,    # from measure tool
            "visual_assessment": str, # from vision LLM (if enabled)
            "correctness_score": float,
            "score_variance": float, # across runs for this test case
        }
    ],
    "metric_history": [{"iteration": int, "score": float}],
    # current_skill_md intentionally omitted — LLM already has it from the call
}
```

Note: `current_skill_md` is NOT included in the return value to avoid doubling the SKILL.md content in the conversation each iteration. The LLM already has the content from the `skill_content` parameter it just passed. If the version was discarded and the best was restored, a `"restored_skill_md"` field is included instead.

### 4. SkillEvaluator

**Location**: `freecad_ai/extensions/skill_evaluator.py`

Core engine with three classes:

#### `EvalResult` (dataclass)

Per-test-case evaluation result:

```python
@dataclass
class EvalResult:
    test_case: str
    tool_calls: int = 0
    errors: int = 0
    retries: int = 0
    error_messages: list[str] = field(default_factory=list)
    measurements: dict = field(default_factory=dict)
    completed: bool = False
    visual_score: float | None = None
    visual_assessment: str = ""
    run_scores: list[float] = field(default_factory=list)  # per-run scores for variance
```

#### `OptimizationState`

Manages version history and best-score tracking:

```python
class OptimizationState:
    def __init__(self, skill_name: str):
        # Creates .optimize/ directory
        # Loads history.json if exists (resume support)
        # Loads config metadata (model, provider) for stale detection

    def save_original(self, content: str)
    def save_version(self, iteration: int, content: str, score: float, kept: bool)
    def get_best(self) -> tuple[str, float]  # (content, score) — all-time best
    def get_history(self) -> list[dict]
    def restore_best(self)  # write best version to SKILL.md
    def is_config_stale(self, current_config: dict) -> bool  # model/provider changed?
```

**Resume with stale detection**: `history.json` stores the model name and provider alongside results. On resume, `is_config_stale()` checks if the current config differs. If so, a warning is shown: "LLM configuration has changed since last optimization. Scores may not be comparable. Reset history?" The user can choose to reset or continue.

#### `SkillEvaluator`

Runs a skill headless and collects metrics:

```python
class SkillEvaluator:
    def __init__(self, config: dict, tool_executor: callable):
        # config from dialog: metrics, weights, budget, reference_image, etc.
        # tool_executor: main-thread dispatch function

    def evaluate(self, skill_name: str, skill_content: str,
                 test_cases: list[dict], runs_per_test: int) -> list[EvalResult]

    def compute_score(self, results: list[EvalResult]) -> float
```

### 5. MainThreadToolExecutor

**Location**: `freecad_ai/tools/executor_utils.py`

**Problem**: FreeCAD tool execution must happen on the main (GUI) thread. The existing agentic loop in `_LLMWorker._tool_loop()` solves this with a signal/mutex pattern: `_execute_tool_on_main_thread()`. The headless evaluator loop runs from a tool handler (which runs on the worker thread), so it needs the same mechanism.

**Solution**: Extract the main-thread dispatch pattern into a shared utility:

```python
class MainThreadToolExecutor(QObject):
    """Dispatches tool calls to the main thread and waits for results.

    Used by both _LLMWorker (for the chat agentic loop) and
    SkillEvaluator (for headless evaluation runs).
    """
    _execute_signal = Signal(str, dict, object)  # tool_name, args, result_holder

    def __init__(self):
        super().__init__()
        self._execute_signal.connect(self._do_execute, Qt.QueuedConnection)
        self._mutex = QMutex()
        self._condition = QWaitCondition()
        self._registry = None

    def set_registry(self, registry):
        self._registry = registry

    def execute(self, tool_name: str, args: dict) -> ToolResult:
        """Call from any thread. Blocks until execution completes on main thread."""
        holder = {"result": None}
        self._mutex.lock()
        self._execute_signal.emit(tool_name, args, holder)
        self._condition.wait(self._mutex)
        self._mutex.unlock()
        return holder["result"]

    def _do_execute(self, tool_name, args, holder):
        """Runs on main thread via signal. Must always wake condition to prevent deadlock."""
        try:
            holder["result"] = self._registry.execute(tool_name, args)
        except Exception as e:
            holder["result"] = ToolResult(success=False, error=str(e))
        finally:
            self._mutex.lock()
            self._condition.wakeAll()
            self._mutex.unlock()
```

Both `_LLMWorker` and `SkillEvaluator` use a `MainThreadToolExecutor` instance. The existing signal/mutex code in `chat_widget.py` is refactored to use this shared class.

**Modified files**: `freecad_ai/ui/chat_widget.py` (refactor to use `MainThreadToolExecutor`).

**Headless agentic loop** (`_run_skill_headless`):

The evaluator creates a fresh `Conversation` + `LLMClient` for each test run, completely isolated from the optimizer conversation. The loop:

1. Create fresh document: `App.newDocument(unique_name)` (dispatched to main thread)
2. Build system prompt (same as normal Act mode)
3. Inject skill content as user message with test case args
4. Send to LLM via `client.send_with_tools()` (non-streaming), receive response
5. If response has tool calls, dispatch to main thread via `MainThreadToolExecutor`, add results to conversation
6. Repeat until LLM stops calling tools, budget reached, or wall-clock timeout exceeded
7. After completion, call `measure` and `get_document_state` to collect metrics (via main thread)
8. Close document: `App.closeDocument(doc.Name)` (via main thread)
9. Return `EvalResult`

**Timeout and cancellation**:
- Per-run wall-clock timeout (default: 300 seconds, configurable in Advanced). If exceeded, the run is marked as `completed=False` and metrics collected so far are used.
- A cancellation flag (`_cancelled`) is checked between tool calls. Connected to the chat's stop button — clicking stop during optimization cancels the current evaluation run gracefully.
- Progress is reported back to the optimizer conversation via the tool return value: `"progress": "test_case 2/4, run 1/2"`.

## Scoring System

### Individual Metrics (each 0.0–1.0)

| Metric | Formula |
|--------|---------|
| Completion | `1.0` if finished within budget and timeout, `0.0` if stuck/timed out |
| Error rate | `1.0 - (errors / total_tool_calls)`. If `total_tool_calls == 0`: `0.0` |
| Retries | `1.0 - min(retries / 5, 1.0)` |
| Efficiency | `1.0 - min(tool_calls / budget, 1.0)` |
| Geometric correctness | See below |
| Visual similarity | See below |

### Geometric Correctness

Expected dimensions come from the structured test case format (see Section 2). The user specifies `expected_bbox` and/or `expected_measurements` in the test case dialog.

After the skill runs, the evaluator calls `measure` (bounding box) on the result body and compares actual vs expected:

```python
score = 1.0 - avg(abs(actual[i] - expected[i]) / expected[i] for i in dims)
score = max(0.0, min(1.0, score))
```

If the test case has no expected dimensions, geometric correctness is skipped for that test case and its weight is redistributed to other metrics.

### Visual Similarity

Two modes:

1. **With reference image**: Sends reference + `capture_viewport` result to vision LLM. Prompt: "Rate the similarity of these two 3D models on a scale of 0 to 10. Consider shape, proportions, features, and overall structure. Reply with just the number."

2. **Without reference** (sanity check): Sends only the captured viewport. Prompt: "Does this look like a well-formed {skill_name}? Rate from 0 (broken/wrong) to 10 (correct, clean). Reply with just the number."

Uses the existing vision routing infrastructure: inline images for vision LLMs, `llm-vision-mcp` fallback for non-vision LLMs.

**Noise reduction**: Visual assessment runs multiple times per evaluation (default: 1, configurable up to 3 in Advanced) and takes the median score to reduce LLM rating variance. Note: with `runs_per_test=2` and 4 test cases, a visual repeat count of 3 produces 24 vision LLM calls per iteration. Use higher repeat counts only when visual accuracy is critical.

**Viewport management**: After tool execution completes, the evaluator explicitly sets the view to isometric via `set_view("isometric")` (which already calls `Gui.SendMsgToActiveView("ViewFit")` to fit all objects) via main-thread dispatch before calling `capture_viewport`. This ensures consistent viewport state regardless of what the skill's tool calls did to the view.

### Composite Score

```python
DEFAULT_WEIGHTS = {
    "completion":  0.30,
    "error_rate":  0.25,
    "correctness": 0.20,
    "efficiency":  0.10,
    "retries":     0.10,
    "visual":      0.05,
}
```

Only enabled metrics participate. Weights are re-normalized to sum to 1.0. Multiple test cases are averaged. Multiple runs per test case are averaged, with variance reported.

### Keep/Discard Logic

```python
all_time_best = state.get_best()  # always compare against all-time best

if score >= all_time_best:
    keep, update best
elif score >= all_time_best - tolerance:  # default tolerance: 0.05
    keep as candidate (may enable future improvement), do NOT update best score
else:
    discard, restore all-time best SKILL.md
```

The tolerance allows lateral moves (keeping a version that scores slightly below best) but **never updates the best score** for within-tolerance keeps. This prevents score ratcheting — a sequence of small regressions can never accumulate because the best score reference point is always the true peak.

## Modification Strategy

### Failure-Driven (default)

The LLM sees specific errors and makes targeted fixes. Prompt instructs:
- Fix the SPECIFIC failures shown
- Check: wrong parameter name? missing body_name? wrong order?
- Keep working steps unchanged
- Add warnings where the LLM stumbled

### Holistic (periodic)

The LLM sees full history and aggregated error categories. Prompt instructs:
- Can steps be reordered to avoid failures?
- Are there redundant or contradictory instructions?
- Can warnings be moved closer to relevant steps?
- Can instruction count be reduced while preserving correctness?

### Strategy Options

| Strategy | Failure-driven | Holistic every N |
|----------|---------------|-----------------|
| Conservative | Always | Never |
| Balanced | Default | Every 5 iterations |
| Aggressive | Default | Every 2 iterations |

### Error Aggregation

The evaluator tracks error categories across iterations:

```python
{
    "sketch_not_found": {"count": 4, "examples": [...]},
    "body_naming": {"count": 2, "examples": [...]},
    "pocket_direction": {"count": 3, "examples": [...]},
}
```

Holistic passes receive these aggregated categories for a birds-eye view of recurring problems.

### High Variance Flagging

When `runs_per_test > 1`, the evaluator computes per-test-case score variance. If variance > 0.15 (i.e., the skill is flaky — sometimes works, sometimes fails), this is flagged in the results:

```python
"flaky_test_cases": ["100x60x40mm — scores ranged from 0.3 to 0.9"]
```

The modification prompt specifically asks the LLM to address flakiness: "This test case is unreliable — the skill sometimes succeeds and sometimes fails. Add more explicit instructions to reduce ambiguity."

## Optimizer Prompt

The inject_prompt returned by the handler includes:
- Current SKILL.md content
- Structured test cases (with expected dimensions)
- Optimization config (iterations, strategy, metrics)
- Strategy-specific instructions
- Workflow instructions (call `optimize_iteration`, analyze, modify, repeat)
- Rules (pass complete SKILL.md, focus on lowest-scoring metric, plateau detection)

**Context management**: The optimizer conversation does NOT use context compacting. Compaction would destroy the iteration history that the LLM needs for trend analysis. This is enforced by adding a `compaction_enabled` flag to `Conversation` (default `True`). The `needs_compaction()` method returns `False` when this flag is `False`. The optimizer sets `conversation.compaction_enabled = False` before starting the loop. The `optimize_iteration` return value is kept lean (no `current_skill_md` echo) and the prompt instructs the LLM to keep its own analysis brief. With 10 iterations at ~2k tokens per iteration (tool call + result + analysis), the total is ~20k tokens — within the context window of most models.

For models with small context windows (e.g., some Ollama models), the dialog's Advanced section includes a "Max iterations" note: "Recommended: ≤5 iterations for models with <32k context."

## Version Management

```
~/.config/FreeCAD/FreeCADAI/skills/{skill_name}/
├── SKILL.md              # current best
├── SKILL.md.original     # backup before optimization (saved once, never overwritten)
└── .optimize/
    ├── config.json       # last optimization config + model/provider info
    ├── history.json      # [{iteration, score, kept, strategy, timestamp, model}]
    ├── v1.md             # each iteration's SKILL.md
    ├── v2.md
    └── ...
```

`SKILL.md.original` is created when optimization starts for the first time. It is never overwritten — the user can always restore the original skill.

`history.json` persists across optimization sessions. Re-running `/optimize-skill` on the same skill can resume from where it left off (iteration numbering continues, best score is loaded). On resume, if the model or provider has changed, a warning is shown and the user can choose to reset history or continue.

## File Layout

### New Files

| File | Purpose |
|------|---------|
| `freecad_ai/extensions/skill_evaluator.py` | `SkillEvaluator`, `EvalResult`, `OptimizationState` |
| `freecad_ai/tools/optimize_tools.py` | `optimize_iteration` tool definition and handler |
| `freecad_ai/tools/executor_utils.py` | `MainThreadToolExecutor` (shared thread-dispatch utility) |
| `freecad_ai/ui/optimize_dialog.py` | `OptimizeSkillDialog` (PySide2) |
| `skills/optimize-skill/SKILL.md` | Skill description |
| `skills/optimize-skill/handler.py` | Dialog launcher, inject_prompt builder |

### Modified Files

| File | Change |
|------|--------|
| `freecad_ai/tools/setup.py` | Add `extra_tools` parameter to `create_default_registry()` |
| `freecad_ai/ui/chat_widget.py` | Refactor `_LLMWorker` to use `MainThreadToolExecutor`; pass `extra_tools` during optimization |
| `freecad_ai/core/conversation.py` | Add `compaction_enabled` flag, check in `needs_compaction()` |

### No Changes Required

Skills registry, tool registry, existing tools, conversation system, LLM client, vision routing — all unchanged.

## Key Design Decisions

1. **Skill as entry point** — no new CLI, no new infrastructure. Works within existing system.
2. **Dialog for config** — user-friendly, discoverable, no flags to memorize. Structured test cases with optional expected dimensions.
3. **`optimize_iteration` tool** — deterministic evaluation, not LLM-discretion. Consistent metrics. Conditionally registered to avoid hallucinated calls.
4. **MainThreadToolExecutor** — shared utility for main-thread tool dispatch. Used by both the chat agentic loop and the headless evaluator. Solves the FreeCAD main-thread requirement.
5. **Headless agentic loop** — minimal reimplementation with timeout and cancellation. Isolated per-run documents (`App.newDocument` / `App.closeDocument`).
6. **Composite scoring** — single number for keep/discard, with interpretable sub-metrics for LLM analysis. All-time best comparison prevents score ratcheting.
7. **Failure-driven + holistic** — targeted fixes by default, periodic restructuring to consolidate. Flaky test cases flagged explicitly.
8. **Version history** — full traceability, original backup, resume support with stale config detection.
9. **Visual similarity opt-in** — uses existing vision routing, median of 3 ratings for noise reduction.
10. **No context compacting** — optimizer conversation keeps full history for trend analysis. Lean return values prevent excessive context growth.
