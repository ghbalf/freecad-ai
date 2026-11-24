# Shorter Act Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development.

**Goal:** Remove redundant lines from the Act prompt without worse tool choice.

**Architecture:** Delete lines in two string constants. A standalone script compares first tool calls for the old and new text.

**Tech Stack:** Python stdlib, pytest.

**Spec:** `docs/superpowers/specs/2026-11-24-shorter-act-prompt-design.md`

## Global Constraints
- Kept text stays byte-identical.
- No tool is executed during the comparison. The endpoint and model are named by the user; no default.

## Review Focus
- A removed line whose tool is missing from the registry: Task 1 tests every tool named by a removed line.
- MCP instructions lose a kept block: covered by the existing `test_server_text_reorders_but_drops_nothing`.
- Model answers without a tool call, or the request fails: Task 2 counts them separately, never as a match.

### Task 1: Remove the lines
**Files:** `freecad_ai/core/tool_guidance.py`, `freecad_ai/core/system_prompt.py`, `tests/unit/test_tool_guidance.py`, `tests/unit/test_no_document.py`, `CHANGELOG.md`

- [ ] Tests first: `test_every_tool_dropped_from_the_guidance_is_described` (each name registered, description non-empty); `test_kept_rules_are_present` (the kept bullets, verbatim); `test_default_act_prompt_is_short` (`len(get_default_system_prompt()) < 4900`; 6777 today). The last one fails first.
- [ ] Replace `test_guidance_names_create_document` with a check that `create_document`'s description says "Use this when no document is open". The #149 tool errors keep their own test.
- [ ] Delete the lines; re-record the `ACT_MODE_TOOLS` hash, with "#159" in its comment.
- [ ] Full unit suite passes; CHANGELOG "Changed" entry; commit.

### Task 2: Compare tool choice
**Files:** scratchpad script, not in the repo.

- [ ] Script takes `--base-url`, `--model`, `--repeat` (all required) and 25 requests, each with the set of acceptable first tools. At least two each for the removed mappings most likely to matter: fillet, pattern, mirror, shell, section, export, spreadsheet, single-solid hole, sketch on face.
- [ ] Old text from `git show 3c80192:…`, new text from the branch; same schemas, temperature and document context; order alternated. Report match, wrong tool, no tool, and error per variant and request.
- [ ] Gate: new matches at least as many as old, and no request drops by more than one repeat. Otherwise restore the line concerned and rerun.
- [ ] Put the numbers in the PR. PR "Closes #159"; merge on the user's word.
