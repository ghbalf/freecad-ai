"""FreeCAD AI Workbench — Non-GUI initialization."""

import os
import sys

# FreeCAD exec's Init.py without setting __file__, so derive the path
# from the known Mod directory structure.
import FreeCAD
_mod_dir = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "freecad-ai")
if not os.path.isdir(_mod_dir):
    # Fallback: check all Mod paths
    for p in FreeCAD.getResourceDir(), FreeCAD.getUserAppDataDir():
        candidate = os.path.join(p, "Mod", "freecad-ai")
        if os.path.isdir(candidate):
            _mod_dir = candidate
            break

if _mod_dir not in sys.path:
    sys.path.append(_mod_dir)
