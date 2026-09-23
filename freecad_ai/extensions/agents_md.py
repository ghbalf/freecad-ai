"""AGENTS.md loader with multi-location search, includes, and variable substitution.

Looks for project-level instruction files (AGENTS.md or FREECAD_AI.md) in:
  1. Active document's directory
  2. Parent directories (up to 3 levels)
  3. User config: ~/.config/FreeCAD/FreeCADAI/AGENTS.md

By default the first file found wins. Set ``merge_agents_md`` to have every
file in the chain concatenated instead, least specific first (#94).

Supports:
  - Include directives: <!-- include: other_file.md -->
  - Variable substitution: {{document_name}}, {{object_count}}, etc.
"""

import os
import re

from ..config import CONFIG_DIR

# Filenames to search for, in priority order
INSTRUCTION_FILENAMES = ["AGENTS.md", "FREECAD_AI.md"]

# Regex for include directives: <!-- include: filename.md -->
INCLUDE_RE = re.compile(r"<!--\s*include:\s*(.+?)\s*-->")

# Regex for variable placeholders: {{variable_name}}
VARIABLE_RE = re.compile(r"\{\{(\w+)\}\}")

# Max parent directories to search upward
MAX_PARENT_LEVELS = 3

# Max include depth to prevent infinite recursion
MAX_INCLUDE_DEPTH = 5


def load_agents_md(merge: bool | None = None) -> str:
    """Load the project instruction file(s) from the best available location.

    Search chain, most specific first:
      1. Active document's directory
      2. Parent directories (up to 3 levels up)
      3. User config directory (~/.config/FreeCAD/FreeCADAI/)

    Args:
        merge: False takes the first file found and ignores the rest.
            True concatenates every file in the chain, *least* specific
            first, so a project file still overrides a global default --
            later text in a prompt carries more weight, and that ordering
            preserves the precedence the first-wins search had.
            None (the default) reads the user's ``merge_agents_md`` setting.

    Returns the processed contents (with includes resolved and variables
    substituted), or an empty string if nothing was found.
    """
    if merge is None:
        from ..config import get_config
        merge = bool(get_config().merge_agents_md)

    # Most specific first: the document's own directory, its parents, then
    # the user config as the last resort.
    chain = _candidate_directories(_get_document_directory()) + [CONFIG_DIR]

    # Includes are resolved per file against the directory it was found in.
    # One shared base directory would give a global file's relative include
    # to the project folder -- or, in merge mode, hand every file the same
    # neighbour regardless of where it actually lives.
    parts = []
    for directory in chain:
        content = _load_from_directory(directory)
        if not content:
            continue
        parts.append(_resolve_includes(content, directory, depth=0))
        if not merge:
            break

    if not parts:
        return ""

    if merge:
        # Reversed: the chain is most-specific-first, the prompt wants the
        # most specific last. A blank line between parts so a trailing
        # bullet never runs into the next file's heading.
        merged = "\n\n".join(part.strip() for part in reversed(parts))
    else:
        merged = parts[0]

    return _substitute_variables(merged)


def _candidate_directories(start_dir: str) -> list:
    """The document directory and its parents, most specific first.

    Empty when the document has never been saved -- there is no directory
    to start from, so only the user config fallback remains.
    """
    if not start_dir:
        return []

    directories = []
    current = start_dir
    for _ in range(MAX_PARENT_LEVELS + 1):
        directories.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break  # Reached filesystem root
        current = parent
    return directories


def _search_directory_chain(start_dir: str) -> str:
    """Search start_dir and its parents for instruction files."""
    for directory in _candidate_directories(start_dir):
        content = _load_from_directory(directory)
        if content:
            return content
    return ""


def _load_from_directory(directory: str) -> str:
    """Try to load an instruction file from a directory."""
    if not directory or not os.path.isdir(directory):
        return ""

    for filename in INSTRUCTION_FILENAMES:
        path = os.path.join(directory, filename)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except (OSError, UnicodeDecodeError):
                continue
    return ""


def _resolve_includes(content: str, base_dir: str, depth: int) -> str:
    """Resolve <!-- include: filename.md --> directives."""
    if depth >= MAX_INCLUDE_DEPTH or not base_dir:
        return content

    def replace_include(match):
        include_path = match.group(1).strip()
        # Resolve relative to base_dir
        full_path = os.path.join(base_dir, include_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    included = f.read()
                # Recursively resolve includes in the included file
                return _resolve_includes(included, os.path.dirname(full_path), depth + 1)
            except (OSError, UnicodeDecodeError):
                return f"<!-- include failed: {include_path} -->"
        return f"<!-- include not found: {include_path} -->"

    return INCLUDE_RE.sub(replace_include, content)


def _substitute_variables(content: str) -> str:
    """Replace {{variable}} placeholders with live values."""
    variables = _get_variables()

    def replace_var(match):
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))  # Keep original if unknown

    return VARIABLE_RE.sub(replace_var, content)


def _get_variables() -> dict:
    """Get current variable values for substitution."""
    variables = {
        "document_name": "",
        "document_path": "",
        "object_count": "0",
        "active_body": "",
    }

    try:
        import FreeCAD as App
        doc = App.ActiveDocument
        if doc:
            variables["document_name"] = doc.Name
            variables["document_path"] = doc.FileName or "(unsaved)"
            variables["object_count"] = str(len(doc.Objects))

            # Find active body
            for obj in doc.Objects:
                if hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body":
                    if hasattr(obj, "IsActive") and obj.IsActive:
                        variables["active_body"] = obj.Label
                        break
    except ImportError:
        pass

    return variables


def _get_document_directory() -> str:
    """Get the directory containing the active FreeCAD document.

    Returns empty string if no document is open or it hasn't been saved.
    """
    try:
        import FreeCAD as App
        doc = App.ActiveDocument
        if doc and doc.FileName:
            return os.path.dirname(doc.FileName)
    except ImportError:
        pass
    return ""
