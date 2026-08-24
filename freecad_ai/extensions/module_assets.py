"""Module assets -- lets other FreeCAD modules ship their own assistant assets.

Any installed FreeCAD module may carry an ``ai/`` directory:

    Mod/<AnyModule>/ai/
        skills/<name>/SKILL.md    same layout as the built-in skills
        tools/*.py                same contract as user extension tools
        hooks/<name>/hook.py      same contract as user hooks
        INSTRUCTIONS.md           appended to the system prompt

This exists because the built-in/user split cannot express "this workbench
brings its own assistant knowledge". A module that defines its own document
types knows things the generic assistant cannot infer, and that knowledge
belongs in the module's own repository -- versioned and tested alongside the
code it describes, not copied by hand into the user config directory where the
next reinstall or edit silently diverges from it.

Precedence is deliberate: built-in < module < user. A module may extend the
assistant, and the user always keeps the last word over both.

Note the deliberate naming: "provider" is already taken in this codebase for
LLM providers (see freecad_ai/llm/providers.py), so these are module *assets*.
"""

import os

# Environment override: os.pathsep-separated list of asset directories. Each
# entry is an "ai" directory itself (not a Mod/ root), so a development tree can
# be pointed at without installing. Mirrors FREECAD_AI_CONFIG_DIR.
ASSET_DIRS_ENV = "FREECAD_AI_ASSET_DIRS"

# Subdirectory a module must carry for its assets to be discovered.
ASSET_SUBDIR = "ai"

# Filename a module may use to add instructions to the system prompt.
INSTRUCTIONS_FILENAME = "INSTRUCTIONS.md"

# Discovery walks every installed module on every call site, and
# create_default_registry() runs on every message, so the scan is cached.
_cache: list[str] | None = None


def _mod_roots() -> list[str]:
    """Return the Mod/ directories FreeCAD loads modules from.

    Three bases, because no single one is right everywhere:

    - getUserAppDataDir() holds modules the user installed themselves.
    - getHomePath() is the application's own installation root, and is where a
      packaged build puts the modules it ships.
    - getResourceDir() is where a stock FreeCAD build keeps them.

    The last two are not interchangeable. On a branded build getResourceDir()
    can point at a data/ subdirectory holding only per-module Resources, while
    the actual modules live under getHomePath()/Mod - probing only the resource
    directory finds an empty or unrelated Mod there and silently discovers
    nothing.
    """
    try:
        import FreeCAD as App
    except ImportError:
        return []

    roots = []
    for base in (App.getUserAppDataDir(), App.getHomePath(), App.getResourceDir()):
        if not base:
            continue
        candidate = os.path.join(base, "Mod")
        if os.path.isdir(candidate) and candidate not in roots:
            roots.append(candidate)
    return roots


def _env_dirs() -> list[str]:
    """Return asset directories named by ASSET_DIRS_ENV."""
    raw = os.environ.get(ASSET_DIRS_ENV, "")
    if not raw:
        return []
    return [
        os.path.abspath(part)
        for part in raw.split(os.pathsep)
        if part.strip() and os.path.isdir(part.strip())
    ]


def discover_asset_dirs(refresh: bool = False) -> list[str]:
    """Return every module's ``ai/`` directory, deduplicated.

    Sorted by module name within each Mod root so registration order is
    reproducible across runs. Results are cached; pass ``refresh=True`` after
    installing a module in a running session.
    """
    global _cache
    if _cache is not None and not refresh:
        return list(_cache)

    found: list[str] = []

    for root in _mod_roots():
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            ai_dir = os.path.join(root, entry, ASSET_SUBDIR)
            if os.path.isdir(ai_dir) and ai_dir not in found:
                found.append(ai_dir)

    # Env entries last so a development tree overrides an installed copy.
    for d in _env_dirs():
        if d not in found:
            found.append(d)

    _cache = found
    return list(found)


def asset_subdirs(name: str, refresh: bool = False) -> list[str]:
    """Return every existing ``<module>/ai/<name>`` directory.

    ``name`` is one of "skills", "tools", "hooks".
    """
    dirs = []
    for asset_dir in discover_asset_dirs(refresh=refresh):
        sub = os.path.join(asset_dir, name)
        if os.path.isdir(sub):
            dirs.append(sub)
    return dirs


def owning_module(asset_dir: str) -> str:
    """Return the name of the module that ships an asset directory."""
    return os.path.basename(os.path.dirname(os.path.abspath(asset_dir)))


def load_module_instructions() -> str:
    """Return the concatenated INSTRUCTIONS.md of every module that ships one.

    Each block is headed with the owning module's name so the model can tell
    which workbench is speaking, and so a stale block is traceable to a module.
    Modules that ship no instructions contribute nothing.
    """
    blocks = []
    for asset_dir in discover_asset_dirs():
        path = os.path.join(asset_dir, INSTRUCTIONS_FILENAME)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except (OSError, UnicodeDecodeError):
            continue
        if content:
            blocks.append(f"### {owning_module(asset_dir)}\n{content}")
    return "\n\n".join(blocks)


def reset_cache():
    """Forget the cached scan. Used by tests and after installing a module."""
    global _cache
    _cache = None
