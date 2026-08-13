"""Resolve a macro identifier to a file path, gated by dangerous mode.

Safe mode: only a bare name resolved within an enumerable set of allowed dirs.
Dangerous mode: any name or absolute/relative path, anywhere.
"""
from __future__ import annotations

import os

from ..branding import APP_NAME

_DANGEROUS_HINT = (
    "Refused: in safe mode run_macro accepts only a bare macro name resolved "
    "from {}'s macro directories. Enable Dangerous mode to run arbitrary "
    "file paths.".format(APP_NAME)
)


def _resolve_name(name: str, allowed_dirs: list[str]):
    """Find <name>, <name>.FCMacro, or <name>.py within allowed_dirs.

    The resolved real path must stay inside the allowed dir. Returns the path
    or None.
    """
    for d in allowed_dirs:
        if not d:
            continue
        base = os.path.realpath(d)
        for candidate in (name, name + ".FCMacro", name + ".py"):
            full = os.path.realpath(os.path.join(d, candidate))
            if not (full == base or full.startswith(base + os.sep)):
                continue
            if os.path.isfile(full):
                # Return the original (display-friendly) path form; safety was
                # already proven against the realpath above.
                return os.path.join(d, candidate)
    return None


def resolve_macro_path(macro: str, allowed_dirs: list[str], dangerous: bool,
                       active_doc_dir: str | None = None,
                       cwd: str | None = None):
    """Return (path, error). Exactly one of the two is non-None."""
    macro = (macro or "").strip()
    if not macro:
        return None, "No macro specified."

    if "\x00" in macro:
        return None, "Invalid macro name (contains a null byte)."

    if dangerous:
        candidates = []
        if os.path.isabs(macro):
            candidates.append(macro)
        else:
            if active_doc_dir:
                candidates.append(os.path.join(active_doc_dir, macro))
            candidates.append(os.path.join(cwd or os.getcwd(), macro))
        for c in candidates:
            if os.path.isfile(c):
                return c, None
        named = _resolve_name(macro, allowed_dirs)
        if named:
            return named, None
        searched = [d for d in ([active_doc_dir] + list(allowed_dirs)) if d]
        return None, (
            f"Macro not found: {macro} (searched: {', '.join(searched) or 'cwd'})"
        )

    # Safe mode — bare name only.
    # Reject anything path-like. The ".." substring check is intentionally
    # broad (also rejects names like "a..b") — conservative at a safety boundary.
    if os.sep in macro or (os.altsep and os.altsep in macro) or ".." in macro:
        return None, _DANGEROUS_HINT
    named = _resolve_name(macro, allowed_dirs)
    if named:
        return named, None
    return None, (
        f"Macro '{macro}' not found in macro directories: "
        f"{', '.join(d for d in allowed_dirs if d)}"
    )
