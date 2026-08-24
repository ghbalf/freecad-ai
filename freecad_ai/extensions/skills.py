"""Skills registry with execution support and slash command matching.

Skills are instruction/action sets loaded from three places, in ascending
precedence: the built-in skills/ directory in this repo, the ai/skills/
ai/skills/ directory of any other installed module (see
extensions.module_assets), and
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

        Three tiers, scanned in ascending precedence: the built-in skills
        directory (in the repo), any skills shipped by other installed
        modules (Mod/<Module>/ai/skills/), and the user skills directory
        (~/.config/FreeCAD/FreeCADAI/skills/). Later tiers overwrite earlier
        ones by directory name, so the user always keeps the last word.
        """
        for skills_dir in self._skill_dirs():
            self._scan_skills_dir(skills_dir)

    @staticmethod
    def _skill_dirs() -> list[str]:
        """Skill directories in ascending precedence order."""
        dirs = [BUILTIN_SKILLS_DIR]
        try:
            from .module_assets import asset_subdirs
            dirs.extend(asset_subdirs("skills"))
        except Exception:
            pass  # Module asset discovery is optional
        dirs.append(SKILLS_DIR)
        return dirs

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

            # Extract description: prefer YAML frontmatter "description",
            # otherwise use first non-empty, non-heading content line.
            description = ""
            body = content
            if content.startswith("---\n"):
                end = content.find("\n---\n", 4)
                if end != -1:
                    frontmatter = content[4:end]
                    body = content[end + 5:]
                    for fm_line in frontmatter.splitlines():
                        if fm_line.startswith("description:"):
                            description = fm_line[12:].strip().strip("\"'")[:100]
                            break
            if not description:
                for line in body.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        description = line[:100]
                        break

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

        # Default: inject SKILL.md content into the prompt, plus a manifest of
        # any on-demand reference files the skill bundles (tier-3 disclosure).
        content = skill.content + self.render_references_manifest(skill)
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
        """Return status info for all skills across every source directory.

        Each entry: {"name", "description", "source", "has_user_copy",
                     "is_modified", "builtin_path", "module_path",
                     "user_path"}

        source: "built-in", "module", "user", or "modified" (user copy
        differs from the shipped version it shadows)
        """
        results = []
        builtin_skills = _scan_skill_files(BUILTIN_SKILLS_DIR)
        user_skills = _scan_skill_files(SKILLS_DIR)

        module_skills = {}
        try:
            from .module_assets import asset_subdirs
            for asset_dir in asset_subdirs("skills"):
                module_skills.update(_scan_skill_files(asset_dir))
        except Exception:
            pass  # Module asset discovery is optional

        all_names = sorted(
            set(builtin_skills) | set(module_skills) | set(user_skills)
        )

        for name in all_names:
            b_path = builtin_skills.get(name)
            p_path = module_skills.get(name)
            u_path = user_skills.get(name)

            # The shipped version a user copy would be shadowing; another
            # module wins over built-ins, matching _skill_dirs() order.
            shipped_path = p_path or b_path

            # Read description from whichever is active (user overrides shipped)
            active_path = u_path or shipped_path
            description = ""
            try:
                with open(active_path, "r", encoding="utf-8") as f:
                    content = f.read()
                body = content
                if content.startswith("---\n"):
                    end = content.find("\n---\n", 4)
                    if end != -1:
                        body = content[end + 5:]
                for line in body.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        description = line[:80]
                        break
            except Exception:
                pass

            shipped_source = "module" if p_path else "built-in"
            if shipped_path and u_path:
                is_modified = _file_hash(shipped_path) != _file_hash(u_path)
                source = "modified" if is_modified else shipped_source
            elif shipped_path:
                source = shipped_source
            else:
                source = "user"

            results.append({
                "name": name,
                "description": description,
                "source": source,
                "has_user_copy": u_path is not None,
                "is_modified": source == "modified",
                "builtin_path": b_path or "",
                "module_path": p_path or "",
                "user_path": u_path or "",
            })

        return results

    @staticmethod
    def reset_to_builtin(name: str) -> bool:
        """Delete the user copy of a skill, reverting to the shipped version.

        The shipped version is the built-in skill, or another module's skill
        if one shadows it. Returns True if the user copy was deleted.
        """
        user_skill_dir = os.path.join(SKILLS_DIR, name)

        if not os.path.isfile(os.path.join(BUILTIN_SKILLS_DIR, name, "SKILL.md")):
            if not _module_skill_path(name):
                return False

        if os.path.isdir(user_skill_dir):
            shutil.rmtree(user_skill_dir)
            return True
        return False


def _scan_skill_files(skills_dir: str) -> dict:
    """Map skill name -> SKILL.md path for one directory."""
    found = {}
    if not os.path.isdir(skills_dir):
        return found
    for entry in sorted(os.listdir(skills_dir)):
        skill_file = os.path.join(skills_dir, entry, "SKILL.md")
        if os.path.isfile(skill_file):
            found[entry] = skill_file
    return found


def _module_skill_path(name: str) -> str:
    """Return a module's SKILL.md path for `name`, or "" if none ships it."""
    try:
        from .module_assets import asset_subdirs
        for asset_dir in asset_subdirs("skills"):
            candidate = os.path.join(asset_dir, name, "SKILL.md")
            if os.path.isfile(candidate):
                return candidate
    except Exception:
        pass  # Module asset discovery is optional
    return ""


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
