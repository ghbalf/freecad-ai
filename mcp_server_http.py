#!/usr/bin/env python3
"""MCP server entry point for FreeCAD AI (HTTP/SSE mode).

Starts FreeCAD with GUI and exposes all built-in tools via the MCP
protocol over HTTP + Server-Sent Events, so you can watch FreeCAD
update in real-time while an AI client calls tools.

Usage:
    # Start FreeCAD with this script as an argument:
    /path/to/FreeCAD.AppImage /path/to/freecad-ai/mcp_server_http.py

    # Or from inside a running FreeCAD via macro / exec:
    exec(open("/path/to/freecad-ai/mcp_server_http.py").read())

MCP configuration:
{
    "freecad": {
      "type": "remote",
      "url": "http://127.0.0.1:3000/sse"
    }
}

Environment variables:
    MCP_HOST  — listen address  (default: 127.0.0.1)
    MCP_PORT  — listen port     (default: 3000)
"""

import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

# __file__ is undefined when this script is run via exec(open(...).read())
# (a documented usage); guard so that path raises no NameError. In that mode
# the freecad-ai package is already importable from FreeCAD's Mod directory.
_script_path = globals().get("__file__")
if _script_path:
    script_dir = os.path.dirname(os.path.abspath(_script_path))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

import FreeCAD

if not FreeCAD.ActiveDocument:
    FreeCAD.newDocument("Unnamed")

from freecad_ai.mcp.gui_server import get_server_controller, resolve_server_address

# Config is only a fallback here; MCP_HOST / MCP_PORT still win. Reading it
# can fail outside a configured install, which must not stop the server.
try:
    from freecad_ai.config import get_config
    _cfg = get_config()
except Exception:
    _cfg = None

host, port = resolve_server_address(_cfg)

# start() binds before returning, so this line can no longer announce a
# server that never came up.
url = get_server_controller().start(host, port)

print(f"MCP SSE server running on {url}", flush=True)
