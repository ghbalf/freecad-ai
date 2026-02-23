#!/usr/bin/env python3
"""MCP server entry point for FreeCAD AI.

Launches FreeCAD in console mode, creates a default document,
and exposes all built-in tools via the MCP protocol over STDIO.

Usage in Claude Desktop config:
{
  "mcpServers": {
    "freecad": {
      "command": "bash",
      "args": ["-c", "exec 3>&1 1>&2 && /path/to/FreeCAD.AppImage -c /path/to/freecad-ai/mcp_server_entry.py"]
    }
  }
}

The bash wrapper redirects fd 1 to stderr before FreeCAD starts, so the
C++ console banner goes to stderr. This script then restores fd 1 from
the saved fd 3 for clean JSON-RPC output.

Alternatively, without the bash wrapper, MCP clients that skip non-JSON
lines will still work (the StdioClientTransport does this).
"""

import os
import sys

# If launched via the bash wrapper (fd 3 = original stdout), restore it.
# Otherwise, redirect fd 1 -> stderr to catch any remaining C++ output.
_have_saved_fd = True
try:
    os.fstat(3)
except OSError:
    _have_saved_fd = False

if _have_saved_fd:
    # bash wrapper saved real stdout on fd 3
    os.dup2(3, 1)
    os.close(3)
else:
    # Direct invocation: redirect fd 1 -> stderr for any future C++ output.
    # The C++ banner already went to the original fd 1 (unavoidable).
    _saved_fd = os.dup(1)
    os.dup2(2, 1)

# Redirect Python-level stdout to stderr during init
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
if _have_saved_fd:
    # fd 1 already points to real stdout
    sys.stdout = os.fdopen(1, "w")
else:
    # Restore fd 1 from saved copy
    os.dup2(_saved_fd, 1)
    os.close(_saved_fd)
    sys.stdout = os.fdopen(1, "w")

# Create registry without MCP client tools (we ARE the server)
from freecad_ai.tools.setup import create_default_registry  # noqa: E402
from freecad_ai.mcp.server import MCPServer  # noqa: E402

registry = create_default_registry(include_mcp=False)
server = MCPServer(registry)
server.run()
