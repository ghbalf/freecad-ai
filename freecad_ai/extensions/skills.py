"""Skills registry (stub for future extensibility).

Skills are project-level or user-level instruction sets that can be
loaded into the system prompt. Each skill is a directory under
~/.config/FreeCAD/FreeCADAI/skills/ containing a SKILL.md file.

V1: Only loads skill descriptions for inclusion in the system prompt.
Actual skill execution is deferred to v2.
"""

import os

from ..config import SKILLS_DIR


class SkillsRegistry:
    """Registry of available skills."""

    def __init__(self):
        self._skills: dict[str, dict] = {}
        self._load_skills()

    def _load_skills(self):
        """Scan skills directory and load SKILL.md files."""
        if not os.path.isdir(SKILLS_DIR):
            return

        for entry in os.listdir(SKILLS_DIR):
            skill_dir = os.path.join(SKILLS_DIR, entry)
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if os.path.isdir(skill_dir) and os.path.isfile(skill_file):
                try:
                    with open(skill_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    self._skills[entry] = {
                        "name": entry,
                        "path": skill_dir,
                        "content": content,
                    }
                except (OSError, UnicodeDecodeError):
                    continue

    def register(self, name: str, content: str):
        """Register a skill programmatically."""
        self._skills[name] = {
            "name": name,
            "path": "",
            "content": content,
        }

    def get_available(self) -> list[dict]:
        """Return list of available skills with their metadata."""
        return list(self._skills.values())

    def get_descriptions(self) -> str:
        """Return a formatted string of all skill descriptions for the system prompt."""
        if not self._skills:
            return ""
        parts = ["## Available Skills"]
        for skill in self._skills.values():
            parts.append(f"\n### {skill['name']}")
            parts.append(skill["content"])
        return "\n".join(parts)
