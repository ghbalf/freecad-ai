"""Default tool registry factory.

Creates a ToolRegistry pre-loaded with all built-in FreeCAD tools,
user extension tools, and optionally MCP tools from connected servers.
"""

import os

from .registry import ToolRegistry
from .freecad_tools import ALL_TOOLS


def create_default_registry(include_mcp: bool = True) -> ToolRegistry:
    """Create a ToolRegistry with all built-in FreeCAD tools registered.

    Also loads user extension tools from USER_TOOLS_DIR, and optionally
    integrates MCP tools from connected servers.
    """
    registry = ToolRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    # Load user extension tools
    try:
        from ..config import USER_TOOLS_DIR, get_config
        from ..extensions.user_tools import load_user_tools
        cfg = get_config()
        extra_dirs = []
        if cfg.scan_freecad_macros:
            fc_macro_dir = os.path.join(
                os.path.expanduser("~"), ".config", "FreeCAD", "Macro"
            )
            if os.path.isdir(fc_macro_dir):
                extra_dirs.append(fc_macro_dir)
        user_tools = load_user_tools(
            USER_TOOLS_DIR,
            disabled=cfg.user_tools_disabled,
            extra_dirs=extra_dirs,
        )
        for tool in user_tools:
            registry.register(tool)
    except Exception:
        pass  # User tools not available

    if include_mcp:
        try:
            from ..mcp.manager import get_mcp_manager
            manager = get_mcp_manager()
            manager.register_tools_into(registry)
        except Exception:
            pass  # MCP not available or no servers connected

    return registry
