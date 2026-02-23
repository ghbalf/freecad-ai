"""Path utilities for FreeCAD AI workbench.

Isolated in a regular module so it can be imported normally from
InitGui.py methods (FreeCAD's exec() scoping prevents closures
over module-level variables in Init scripts).
"""

import os


def get_wb_dir() -> str:
    """Find the freecad-ai workbench directory."""
    import FreeCAD as App
    for base in (App.getUserAppDataDir(), App.getResourceDir()):
        candidate = os.path.join(base, "Mod", "freecad-ai")
        if os.path.isdir(candidate):
            return candidate
    return ""


def get_translations_path() -> str:
    """Get the path to the translations directory, or empty string."""
    wb = get_wb_dir()
    if wb:
        p = os.path.join(wb, "translations")
        if os.path.isdir(p):
            return p
    return ""


def get_icon_path() -> str:
    """Get the path to the workbench SVG icon, or empty string."""
    wb = get_wb_dir()
    if wb:
        p = os.path.join(wb, "resources", "icons", "freecad_ai.svg")
        if os.path.exists(p):
            return p
    return ""
