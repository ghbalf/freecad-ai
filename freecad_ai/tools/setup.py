"""Default tool registry factory.

Creates a ToolRegistry pre-loaded with all built-in FreeCAD tools,
and optionally integrates MCP tools from connected servers.
"""

from .registry import ToolRegistry
from .freecad_tools import ALL_TOOLS


def create_default_registry(include_mcp: bool = True) -> ToolRegistry:
    """Create a ToolRegistry with all built-in FreeCAD tools registered.

    If include_mcp is True and MCP servers are connected, their tools
    are also registered (namespaced as server__tool).
    """
    registry = ToolRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    if include_mcp:
        try:
            from ..mcp.manager import get_mcp_manager
            manager = get_mcp_manager()
            manager.register_tools_into(registry)
        except Exception:
            pass  # MCP not available or no servers connected

    return registry
