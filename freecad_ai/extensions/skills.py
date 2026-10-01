"""Skills registry with execution support and slash command matching.

Skills are user-level instruction/action sets stored under
~/.config/FreeCAD/FreeCADAI/skills/. Each skill is a directory containing:
  - SKILL.md: LLM instructions for the skill (injected into prompt)
  - handler.py: (optional) Python handler with an execute() function

Skills can be invoked via /command in the chat input.
"""

import hashlib
import importlib.util
import logging
import os
import re
import shutil
from dataclasses import dataclass, field

from ..config import SKILLS_DIR
from .skill_frontmatter import parse_frontmatter

DESCRIPTION_MAX = 1024        # Agent Skills limit
PROMPT_DESCRIPTION_MAX = 300  # what the system-prompt skill list shows
_FALLBACK_DESCRIPTION_MAX = 100

MAX_SKILL_FILES = 500
MAX_RESOURCE_BYTES = 100_000
MANIFEST_MAX = 40
_SKIP_ROOT_FILES = {"SKILL.md", "handler.py", "VALIDATION.md"}
_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".git"}

_log = logging.getLogger(__name__)

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
    files: dict = field(default_factory=dict)  # "dir/file.ext" -> abspath (allowlist)


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

            # Tier-3 progressive disclosure: every file in the folder, keyed by
            # its relative path (Agent Skills links), plus today's bare-stem
            # aliases for files directly in references/ (last sorted wins).
            files = _scan_skill_files(skill_dir)
            references = {}
            for key in sorted(files):
                parts = key.split("/")
                if len(parts) == 2 and parts[0] == "references":
                    references[os.path.splitext(parts[1])[0].lower()] = files[key]

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
                files=files,
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
        """Markdown block advertising a skill's files (tier-3 disclosure)."""
        if not skill.files:
            return ""
        alias_of = {path: alias for alias, path in skill.references.items()}
        groups = {"Documents": [], "Scripts": [], "Assets": []}
        for key in sorted(skill.files):
            if key.startswith("scripts/"):
                groups["Scripts"].append(f"- `{key}`")
            elif key.startswith("assets/"):
                groups["Assets"].append(f"- `{key}`")
            else:
                path = skill.files[key]
                bullet = f"- `{key}`"
                if path in alias_of:
                    bullet += f" (resource='{alias_of[path]}')"
                summary = _reference_summary(path)
                if summary:
                    bullet += f" — {summary}"
                groups["Documents"].append(bullet)
        lines = [
            "\n\n## Available references",
            f"Read a file with use_skill(name='{skill.name}', resource='<path>').",
        ]
        if groups["Scripts"]:
            lines.append(f"Run a Python script with run_skill_script(skill='{skill.name}', "
                         f"script='<path>', args='<command-line args>').")
        shown = 0
        for title, bullets in groups.items():
            if not bullets or shown >= MANIFEST_MAX:
                continue
            take = bullets[:MANIFEST_MAX - shown]
            lines.append(f"### {title}")
            lines.extend(take)
            shown += len(take)
        if len(skill.files) > shown:
            lines.append(f"…and {len(skill.files) - shown} more")
        return "\n".join(lines)

    @staticmethod
    def _resolve_key(skill: Skill, requested: str) -> "str | None":
        """Map a requested path or legacy alias onto an allowlist key."""
        r = requested.strip().replace("\\", "/")
        while r.startswith("./"):
            r = r[2:]
        if r in skill.files:
            return r
        lowered = r.lower()
        for key in skill.files:
            if key.lower() == lowered:
                return key
        alias_path = skill.references.get(os.path.splitext(r)[0].lower())
        if alias_path:
            for key, path in skill.files.items():
                if path == alias_path:
                    return key
        return None

    def get_skill_resource(self, name: str, resource: str) -> dict:
        """Return the contents of one file from a skill's allowlist.

        `resource` is a relative path or a legacy references/ alias; it is
        only ever looked up in the pre-scanned Skill.files, never opened as
        a path, so directory traversal is impossible.

        Returns {"output": contents} or {"error": message}.
        """
        skill = self._skills.get(name)
        if not skill:
            return {"error": f"Unknown skill: {name}"}
        if not skill.files:
            return {"error": f"Skill '{name}' has no references."}
        key = self._resolve_key(skill, resource)
        if key is None:
            listed = _list_keys(skill.files)
            return {"error": (f"Reference '{resource}' not found in skill "
                              f"'{name}'. Available: {listed}")}
        path = skill.files[key]
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                data = f.read(MAX_RESOURCE_BYTES + 1)
        except OSError as e:
            return {"error": f"Could not read reference '{resource}': {e}"}
        truncated = len(data) > MAX_RESOURCE_BYTES
        data = data[:MAX_RESOURCE_BYTES]
        binary = b"\0" in data[:8192]
        if not binary:
            try:
                text = data.decode("utf-8", "ignore" if truncated else "strict")
            except UnicodeDecodeError:
                binary = True
        if binary:
            return {"error": (f"'{key}' is a binary file ({size} bytes). It is "
                              f"meant for the skill's scripts, not for reading.")}
        if truncated:
            text += (f"\n\n[truncated: showing the first {MAX_RESOURCE_BYTES} "
                     f"of {size} bytes]")
        return {"output": text}

    def resolve_script(self, name: str, script: str) -> tuple:
        """Resolve a runnable script from a skill's allowlist.

        Returns (abspath, "", py_paths) or ("", error, []). py_paths lists
        every .py file in the skill, so the caller can validate the modules
        a script may import, not just the script itself.
        """
        skill = self._skills.get(name)
        if not skill:
            return "", f"Unknown skill: {name}", []
        key = self._resolve_key(skill, script)
        scripts = sorted(k for k in skill.files if k.endswith(".py"))
        if key is None:
            return "", (f"Script '{script}' not found in skill '{name}'. "
                        f"Available: {_list_keys(scripts) or 'none'}"), []
        if not key.endswith(".py"):
            return "", (f"Only Python scripts run inside FreeCAD. Read '{key}' "
                        f"with use_skill(name='{name}', resource='{key}') instead."), []
        return skill.files[key], "", [(k, skill.files[k]) for k in scripts]

    def find_unvalidatable(self, name: str) -> str:
        """Return a relative path (or reason) that makes the skill's Python
        impossible to validate up front, or "" if all is well.

        Scripts can import siblings from their own folder, so everything
        importable must be a scanned .py file. Flags native modules, sourceless
        .pyc files, .py files that resolve outside the folder, and a file count
        that reached MAX_SKILL_FILES (more .py files may exist unscanned).
        """
        skill = self._skills.get(name)
        if not skill:
            return ""
        if len(skill.files) >= MAX_SKILL_FILES:
            return f"(more than {MAX_SKILL_FILES} files in the skill)"
        root = os.path.realpath(skill.path)
        for dirpath, dirnames, filenames in os.walk(skill.path, followlinks=False):
            in_cache = os.path.basename(dirpath) == "__pycache__"
            for fn in sorted(filenames):
                low = fn.lower()
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, skill.path).replace(os.sep, "/")
                if low.endswith((".so", ".pyd")):
                    return rel
                if low.endswith(".pyc") and not in_cache:
                    return rel
                if low.endswith(".py"):
                    real = os.path.realpath(path)
                    if os.path.commonpath([root, real]) != root:
                        return rel
        return ""

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


def _list_keys(keys, limit: int = 20) -> str:
    """Comma-separated sorted keys, capped at `limit` with a '…and N more' tail."""
    keys = sorted(keys)
    listed = ", ".join(keys[:limit])
    if len(keys) > limit:
        listed += f", …and {len(keys) - limit} more"
    return listed


def _scan_skill_files(skill_dir: str) -> dict:
    """Allowlist of every file in a skill folder, keyed by POSIX relative path.

    Built once at load time; reads only ever pick from it, so the model can
    name a path but never reach outside the folder. Symlinks are kept only
    when their target is inside the skill's own realpath.
    """
    root = os.path.realpath(skill_dir)
    files = {}
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in _SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, skill_dir)
        for fn in sorted(filenames):
            if fn.startswith(".") or (rel_dir == "." and fn in _SKIP_ROOT_FILES):
                continue
            path = os.path.join(dirpath, fn)
            real = os.path.realpath(path)
            if os.path.commonpath([root, real]) != root or not os.path.isfile(real):
                continue
            key = fn if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{fn}"
            if len(files) >= MAX_SKILL_FILES:
                _log.warning("Skill %s has more than %d files; the rest are ignored",
                             skill_dir, MAX_SKILL_FILES)
                return files
            files[key] = path
    return files


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
