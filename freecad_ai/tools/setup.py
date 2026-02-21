"""Default tool registry factory.

Creates a ToolRegistry pre-loaded with all built-in FreeCAD tools.
"""

from .registry import ToolRegistry
from .freecad_tools import ALL_TOOLS


def create_default_registry() -> ToolRegistry:
    """Create a ToolRegistry with all built-in FreeCAD tools registered."""
    registry = ToolRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)
    return registry
