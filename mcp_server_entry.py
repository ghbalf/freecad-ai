#!/usr/bin/env python3
"""MCP server entry point for FreeCAD AI.

Launches FreeCAD in console mode, creates a default document,
and exposes all built-in tools via the MCP protocol over STDIO.

Usage in Claude Desktop config:
{
  "mcpServers": {
    "freecad": {
      "command": "/path/to/FreeCAD.AppImage",
      "args": ["-c", "/path/to/freecad-ai/mcp_server_entry.py"]
    }
  }
}
"""

import os
import sys

# Redirect stdout to stderr during FreeCAD init to prevent
# startup messages from corrupting the JSON-RPC stream.
_real_stdout = sys.stdout
sys.stdout = sys.stderr

# Ensure the freecad-ai package is importable
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import FreeCAD  # noqa: E402

# Create a default empty document
if not FreeCAD.ActiveDocument:
    FreeCAD.newDocument("Unnamed")

# Restore stdout for JSON-RPC communication
sys.stdout = _real_stdout

# Create registry without MCP client tools (we ARE the server)
from freecad_ai.tools.setup import create_default_registry  # noqa: E402
from freecad_ai.mcp.server import MCPServer  # noqa: E402

registry = create_default_registry(include_mcp=False)
server = MCPServer(registry)
server.run()
