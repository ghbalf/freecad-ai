"""Skills registry with execution support and slash command matching.

Skills are user-level instruction/action sets stored under
~/.config/FreeCAD/FreeCADAI/skills/. Each skill is a directory containing:
  - SKILL.md: LLM instructions for the skill (injected into prompt)
  - handler.py: (optional) Python handler with an execute() function

Skills can be invoked via /command in the chat input.
"""

import importlib.util
import os
import re
from dataclasses import dataclass, field

from ..config import SKILLS_DIR


@dataclass
class Skill:
    """A registered skill."""
    name: str
    description: str = ""
    path: str = ""
    content: str = ""  # SKILL.md contents
    trigger: str = ""  # Slash command, e.g. "/thread-insert"
    has_handler: bool = False


class SkillsRegistry:
    """Registry of available skills with execution support."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._load_skills()

    def _load_skills(self):
        """Scan skills directory and load skill definitions."""
        if not os.path.isdir(SKILLS_DIR):
            return

        for entry in os.listdir(SKILLS_DIR):
            skill_dir = os.path.join(SKILLS_DIR, entry)
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isdir(skill_dir) or not os.path.isfile(skill_file):
                continue

            try:
                with open(skill_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            # Extract description from first non-empty, non-heading line
            description = ""
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    description = line[:100]
                    break

            handler_path = os.path.join(skill_dir, "handler.py")

            self._skills[entry] = Skill(
                name=entry,
                description=description,
                path=skill_dir,
                content=content,
                trigger=f"/{entry}",
                has_handler=os.path.isfile(handler_path),
            )

    def register(self, name: str, content: str, trigger: str = ""):
        """Register a skill programmatically."""
        self._skills[name] = Skill(
            name=name,
            content=content,
            trigger=trigger or f"/{name}",
        )

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_available(self) -> list[Skill]:
        """Return list of available skills."""
        return list(self._skills.values())

    def get_descriptions(self) -> str:
        """Return a formatted string of all skill descriptions for the system prompt."""
        if not self._skills:
            return ""
        parts = ["## Available Skills"]
        for skill in self._skills.values():
            parts.append(f"\n### {skill.name}")
            if skill.description:
                parts.append(skill.description)
            if skill.trigger:
                parts.append(f"Invoke with: `{skill.trigger}`")
        return "\n".join(parts)

    def match_command(self, user_input: str) -> tuple | None:
        """Check if user input matches a skill command.

        Returns (skill_name, remaining_args) or None.
        """
        text = user_input.strip()
        if not text.startswith("/"):
            return None

        # Split into command and args
        parts = text.split(None, 1)
        command = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        for skill in self._skills.values():
            if skill.trigger == command:
                return (skill.name, args)

        return None

    def execute_skill(self, name: str, args: str = "") -> dict:
        """Execute a skill.

        If the skill has a handler.py with an execute() function, call it.
        Otherwise, return the SKILL.md content for prompt injection.

        Returns:
            dict with either:
              - {"inject_prompt": str} — content to inject into the LLM prompt
              - {"output": str} — direct output to display
              - {"error": str} — error message
        """
        skill = self._skills.get(name)
        if not skill:
            return {"error": f"Unknown skill: {name}"}

        # Try to run handler.py if it exists
        if skill.has_handler:
            handler_result = self._run_handler(skill, args)
            if handler_result is not None:
                return handler_result

        # Default: inject SKILL.md content into the prompt
        return {"inject_prompt": skill.content}

    def _run_handler(self, skill: Skill, args: str) -> dict | None:
        """Try to load and run a skill's handler.py.

        The handler module should have an execute(args: str) -> dict function.
        Returns None if the handler can't be loaded or doesn't have execute().
        """
        handler_path = os.path.join(skill.path, "handler.py")
        if not os.path.isfile(handler_path):
            return None

        try:
            spec = importlib.util.spec_from_file_location(
                f"skill_{skill.name}_handler", handler_path
            )
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "execute"):
                result = module.execute(args)
                if isinstance(result, dict):
                    return result
                elif isinstance(result, str):
                    return {"output": result}

        except Exception as e:
            return {"error": f"Skill handler error: {e}"}

        return None
