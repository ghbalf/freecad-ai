"""Default tool registry factory.

Creates a ToolRegistry pre-loaded with all built-in FreeCAD tools,
user extension tools, and optionally MCP tools from connected servers.
"""

import os

from .registry import ToolRegistry
from .freecad_tools import ALL_TOOLS


def create_default_registry(include_mcp: bool = True, extra_tools: list | None = None) -> ToolRegistry:
    """Create a ToolRegistry with all built-in FreeCAD tools registered.

    Also loads user extension tools from USER_TOOLS_DIR, and optionally
    integrates MCP tools from connected servers.
    """
    registry = ToolRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    # Load user extension tools, plus any shipped by other installed modules
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
        try:
            from ..extensions.module_assets import asset_subdirs
            extra_dirs.extend(asset_subdirs("tools"))
        except Exception:
            pass  # Module asset discovery is optional
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

    # Register extra tools (e.g., optimize_iteration during optimization)
    if extra_tools:
        for tool in extra_tools:
            registry.register(tool)

    return registry
