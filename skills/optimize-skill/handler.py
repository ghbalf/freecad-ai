"""Handler for /optimize-skill command.

Shows the OptimizeSkillDialog, then returns an inject_prompt with the
optimizer instructions and the current SKILL.md content.
"""
import os


def execute(args: str) -> dict:
    """Launch optimization dialog and return inject_prompt."""
    try:
        from freecad_ai.extensions.skills import SkillsRegistry
        from freecad_ai.ui.optimize_dialog import OptimizeSkillDialog
        from freecad_ai.extensions.skill_evaluator import OptimizationState
        from freecad_ai.tools.optimize_tools import (
            start_optimization, OPTIMIZATION_PROMPT_TEMPLATE,
            STRATEGY_INSTRUCTIONS,
        )
        from freecad_ai.config import get_config
    except ImportError as e:
        return {"error": f"Import failed: {e}"}

    # Discover available skills
    registry = SkillsRegistry()
    available = [s.name for s in registry.get_available()]
    if not available:
        return {"error": "No skills found. Create a skill first."}

    # Pre-select if arg provided
    preselect = args.strip() if args else ""

    # Show dialog
    try:
        import FreeCADGui as Gui
        parent = Gui.getMainWindow()
    except ImportError:
        parent = None

    dlg = OptimizeSkillDialog(available, preselect=preselect, parent=parent)
    if not dlg.exec():
        return {"output": "Optimization cancelled."}

    config = dlg.result_config
    if not config:
        return {"output": "Optimization cancelled."}

    skill_name = config["skill_name"]

    # Load current SKILL.md
    skill = registry.get_skill(skill_name)
    if not skill:
        return {"error": f"Skill '{skill_name}' not found."}

    current_content = skill.content

    # Initialize optimization state
    state = OptimizationState(skill_name)
    state.save_original(current_content)

    # Check for stale config
    cfg = get_config()
    model_config = {"model": cfg.provider.model, "provider": cfg.provider.name}
    config["model_config"] = model_config

    if state.get_history() and state.is_config_stale(model_config):
        try:
            from freecad_ai.ui.compat import QtWidgets
            reply = QtWidgets.QMessageBox.question(
                parent, "Config Changed",
                "LLM configuration has changed since last optimization.\n"
                "Scores may not be comparable.\n\n"
                "Reset history and start fresh?",
            )
            if reply == QtWidgets.QMessageBox.Yes:
                optimize_dir = os.path.join(
                    os.path.dirname(skill.path), ".optimize")
                history_path = os.path.join(optimize_dir, "history.json")
                if os.path.exists(history_path):
                    os.remove(history_path)
                state = OptimizationState(skill_name)
        except ImportError:
            pass  # No Qt available, skip dialog

    # Start optimization session
    start_optimization(state, config)

    # Build test cases display
    tc_lines = []
    for i, tc in enumerate(config["test_cases"], 1):
        tc_lines.append(f"{i}. `{tc.get('args', '')}`")
    test_cases_formatted = "\n".join(tc_lines)

    # Build inject prompt
    prompt = OPTIMIZATION_PROMPT_TEMPLATE.format(
        skill_name=skill_name,
        test_cases_formatted=test_cases_formatted,
        iterations=config["iterations"],
    )

    return {"inject_prompt": prompt}
