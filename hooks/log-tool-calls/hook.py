"""Log all tool calls to the FreeCAD report view.

Built-in hook that logs each tool execution with its name, success status,
and any error message. Useful for debugging skill and tool issues.
"""
import logging

logger = logging.getLogger("freecad_ai.hooks.log_tool_calls")


def on_post_tool_use(context):
    """Log tool call results."""
    level = logging.INFO if context["success"] else logging.WARNING
    msg = "Tool: %s | success=%s"
    args = [context["tool_name"], context["success"]]
    if context.get("error"):
        msg += " | error=%s"
        args.append(context["error"])
    logger.log(level, msg, *args)
