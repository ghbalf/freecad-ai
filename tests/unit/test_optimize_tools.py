"""Tests for optimize_iteration tool."""
from freecad_ai.tools.optimize_tools import (
    get_optimize_iteration_tool, OPTIMIZATION_PROMPT_TEMPLATE, STRATEGY_INSTRUCTIONS,
)
from freecad_ai.tools.registry import ToolDefinition


class TestOptimizeIterationTool:
    def test_tool_definition(self):
        tool = get_optimize_iteration_tool()
        assert isinstance(tool, ToolDefinition)
        assert tool.name == "optimize_iteration"
        assert len(tool.parameters) >= 3

    def test_prompt_templates_exist(self):
        assert "optimize_iteration" in OPTIMIZATION_PROMPT_TEMPLATE
        assert "conservative" in STRATEGY_INSTRUCTIONS
        assert "balanced" in STRATEGY_INSTRUCTIONS
        assert "aggressive" in STRATEGY_INSTRUCTIONS

    def test_strategy_instructions_all_have_content(self):
        for key, value in STRATEGY_INSTRUCTIONS.items():
            assert len(value) > 20, f"Strategy '{key}' instruction is too short"
