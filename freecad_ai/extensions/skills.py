"""Skills registry with execution support and slash command matching.

Skills are user-level instruction/action sets stored under
~/.config/FreeCAD/FreeCADAI/skills/. Each skill is a directory containing:
  - SKILL.md: LLM instructions for the skill (injected into prompt)
  - handler.py: (optional) Python handler with an execute() function

Skills can be invoked via /command in the chat input.
"""

import hashlib
import importlib.util
import os
import re
import shutil
from dataclasses import dataclass, field

from ..config import SKILLS_DIR
from .skill_frontmatter import parse_frontmatter

DESCRIPTION_MAX = 1024        # Agent Skills limit
PROMPT_DESCRIPTION_MAX = 300  # what the system-prompt skill list shows
_FALLBACK_DESCRIPTION_MAX = 100

# Built-in skills directory (in the repo, alongside freecad_ai/)
BUILTIN_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "skills",
)


@dataclass
class Skill:
    """A registered skill."""
    name: str
    description: str = ""
    path: str = ""
    content: str = ""  # SKILL.md contents
    body: str = ""  # SKILL.md without frontmatter: what gets injected
    frontmatter: dict = field(default_factory=dict)
    trigger: str = ""  # Slash command, e.g. "/thread-insert"
    has_handler: bool = False
    validation_path: str = ""
    references: dict = field(default_factory=dict)  # key (lowercased stem) -> abspath


class SkillsRegistry:
    """Registry of available skills with execution support."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._load_skills()

    def _load_skills(self):
        """Scan skills directories and load skill definitions.

        Scans both the built-in skills directory (in the repo) and the user
        skills directory (~/.config/FreeCAD/FreeCADAI/skills/). User skills
        take precedence over built-in skills with the same name.
        """
        # Load built-in first, then user (user overrides built-in)
        for skills_dir in (BUILTIN_SKILLS_DIR, SKILLS_DIR):
            self._scan_skills_dir(skills_dir)

    def _scan_skills_dir(self, skills_dir: str):
        """Scan a single directory for skill definitions."""
        if not os.path.isdir(skills_dir):
            return

        for entry in os.listdir(skills_dir):
            skill_dir = os.path.join(skills_dir, entry)
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isdir(skill_dir) or not os.path.isfile(skill_file):
                continue

            try:
                with open(skill_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            frontmatter, body = parse_frontmatter(content)
            description = _describe(frontmatter, body)

            handler_path = os.path.join(skill_dir, "handler.py")

            validation_path = ""
            val_file = os.path.join(skill_dir, "VALIDATION.md")
            if os.path.isfile(val_file):
                validation_path = val_file

            # Tier-3 progressive disclosure: scan a sibling references/ dir
            # (top level only) into a {key -> abspath} allowlist. The model
            # later names a key, never a path, so traversal is impossible.
            references = {}
            refs_dir = os.path.join(skill_dir, "references")
            if os.path.isdir(refs_dir):
                for ref_entry in sorted(os.listdir(refs_dir)):
                    ref_path = os.path.join(refs_dir, ref_entry)
                    if not os.path.isfile(ref_path):
                        continue
                    key = os.path.splitext(ref_entry)[0].lower()
                    references[key] = ref_path

            self._skills[entry] = Skill(
                name=entry,
                description=description,
                path=skill_dir,
                content=content,
                body=body,
                frontmatter=frontmatter,
                trigger=f"/{entry}",
                has_handler=os.path.isfile(handler_path),
                validation_path=validation_path,
                references=references,
            )

    def register(self, name: str, content: str, trigger: str = ""):
        """Register a skill programmatically."""
        self._skills[name] = Skill(
            name=name,
            content=content,
            body=content,
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
                parts.append(_shorten(skill.description, PROMPT_DESCRIPTION_MAX))
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

        # Default: inject SKILL.md content into the prompt, plus a manifest of
        # any on-demand reference files the skill bundles (tier-3 disclosure).
        content = skill.body + self.render_references_manifest(skill)
        return {"inject_prompt": content}

    def render_references_manifest(self, skill: Skill) -> str:
        """Markdown block advertising a skill's on-demand reference files."""
        if not skill.references:
            return ""
        lines = [
            "\n\n## Available references",
            f"Load one when needed with "
            f"use_skill(name='{skill.name}', resource='<key>'):",
        ]
        for key in sorted(skill.references):
            summary = _reference_summary(skill.references[key])
            bullet = f"- `{key}` (resource='{key}')"
            if summary:
                bullet += f" — {summary}"
            lines.append(bullet)
        return "\n".join(lines)

    def get_skill_resource(self, name: str, resource: str) -> dict:
        """Return the contents of a skill's reference file.

        `resource` is a KEY into the pre-scanned Skill.references allowlist —
        it is never treated as a filesystem path, so directory traversal is
        impossible. The key may be given with or without an extension and is
        matched case-insensitively.

        Returns {"output": contents} or {"error": message}.
        """
        skill = self._skills.get(name)
        if not skill:
            return {"error": f"Unknown skill: {name}"}
        if not skill.references:
            return {"error": f"Skill '{name}' has no references."}

        key = os.path.splitext(resource.strip())[0].lower()
        path = skill.references.get(key)
        if not path:
            available = ", ".join(sorted(skill.references))
            return {
                "error": (
                    f"Reference '{resource}' not found in skill '{name}'. "
                    f"Available: {available}"
                )
            }
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {"output": f.read()}
        except (OSError, UnicodeDecodeError) as e:
            return {"error": f"Could not read reference '{resource}': {e}"}

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

    @staticmethod
    def get_skill_status() -> list[dict]:
        """Return status info for all skills across built-in and user dirs.

        Each entry: {"name", "description", "source", "has_user_copy",
                     "is_modified", "builtin_path", "user_path"}

        source: "built-in", "user", or "modified" (user copy differs from built-in)
        """
        results = []
        builtin_skills = {}
        user_skills = {}

        # Scan built-in
        if os.path.isdir(BUILTIN_SKILLS_DIR):
            for entry in sorted(os.listdir(BUILTIN_SKILLS_DIR)):
                skill_file = os.path.join(BUILTIN_SKILLS_DIR, entry, "SKILL.md")
                if os.path.isfile(skill_file):
                    builtin_skills[entry] = skill_file

        # Scan user
        if os.path.isdir(SKILLS_DIR):
            for entry in sorted(os.listdir(SKILLS_DIR)):
                skill_file = os.path.join(SKILLS_DIR, entry, "SKILL.md")
                if os.path.isfile(skill_file):
                    user_skills[entry] = skill_file

        all_names = sorted(set(builtin_skills) | set(user_skills))

        for name in all_names:
            b_path = builtin_skills.get(name)
            u_path = user_skills.get(name)

            # Read description from whichever is active (user overrides built-in)
            active_path = u_path or b_path
            description = compatibility = ""
            try:
                with open(active_path, "r", encoding="utf-8") as f:
                    frontmatter, body = parse_frontmatter(f.read())
                description = _describe(frontmatter, body)
                compat = frontmatter.get("compatibility")
                compatibility = compat if isinstance(compat, str) else ""
            except Exception:
                pass

            if b_path and u_path:
                is_modified = _file_hash(b_path) != _file_hash(u_path)
                source = "modified" if is_modified else "built-in"
            elif b_path:
                source = "built-in"
            else:
                source = "user"

            results.append({
                "name": name,
                "description": description,
                "compatibility": compatibility,
                "source": source,
                "has_user_copy": u_path is not None,
                "is_modified": source == "modified",
                "builtin_path": b_path or "",
                "user_path": u_path or "",
            })

        return results

    @staticmethod
    def reset_to_builtin(name: str) -> bool:
        """Delete the user copy of a skill, reverting to the built-in version.

        Returns True if the user copy was deleted.
        """
        user_skill_dir = os.path.join(SKILLS_DIR, name)
        builtin_skill = os.path.join(BUILTIN_SKILLS_DIR, name, "SKILL.md")

        if not os.path.isfile(builtin_skill):
            return False

        if os.path.isdir(user_skill_dir):
            shutil.rmtree(user_skill_dir)
            return True
        return False


def _describe(frontmatter: dict, body: str) -> str:
    """Frontmatter description, else the body's first non-heading line."""
    desc = frontmatter.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()[:DESCRIPTION_MAX]
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:_FALLBACK_DESCRIPTION_MAX]
    return ""


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _reference_summary(path: str) -> str:
    """One-line summary of a reference file for the manifest.

    Prefer the first non-empty, non-heading line (matching how skill
    descriptions are extracted in _scan_skills_dir); fall back to the first
    heading's text if the file is heading-only.
    """
    heading = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    if not heading:
                        heading = stripped.lstrip("#").strip()
                    continue
                return stripped[:100]
    except (OSError, UnicodeDecodeError):
        pass
    return heading[:100]


def _file_hash(path: str) -> str:
    """Return MD5 hex digest of a file's contents."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""
