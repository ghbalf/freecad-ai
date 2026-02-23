"""AGENTS.md loader.

Looks for project-level instruction files (AGENTS.md or FREECAD_AI.md)
in the directory of the currently open FreeCAD document, and injects
their contents into the system prompt.
"""

import os


def load_agents_md() -> str:
    """Load AGENTS.md or FREECAD_AI.md from the active document's directory.

    Returns the file contents, or an empty string if not found.
    """
    doc_dir = _get_document_directory()
    if not doc_dir:
        return ""

    # Check for instruction files in priority order
    candidates = ["AGENTS.md", "FREECAD_AI.md"]
    for filename in candidates:
        path = os.path.join(doc_dir, filename)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except (OSError, UnicodeDecodeError):
                continue

    return ""


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
