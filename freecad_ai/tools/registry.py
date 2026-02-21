"""Tool abstractions and registry.

Defines the core data structures for tool calling (ToolParam, ToolDefinition,
ToolResult) and the ToolRegistry that manages tool registration, lookup,
execution, and schema generation for LLM APIs.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolParam:
    """A single parameter for a tool."""
    name: str
    type: str  # "string", "number", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    enum: list[str] | None = None
    default: Any = None
    items: dict | None = None  # For array types: {"type": "string"}


@dataclass
class ToolDefinition:
    """A registered tool with its schema and handler."""
    name: str
    description: str
    parameters: list[ToolParam]
    handler: Callable[..., "ToolResult"]
    category: str = "general"


@dataclass
class ToolResult:
    """Result of executing a tool."""
    success: bool
    output: str  # Human-readable summary
    data: dict = field(default_factory=dict)  # Structured data
    error: str = ""


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        """List all registered tools."""
        return list(self._tools.values())

    def execute(self, name: str, params: dict) -> ToolResult:
        """Execute a tool by name with the given parameters."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                success=False, output="", error=f"Unknown tool: {name}"
            )
        try:
            return tool.handler(**params)
        except TypeError as e:
            return ToolResult(
                success=False, output="", error=f"Invalid parameters for {name}: {e}"
            )
        except Exception as e:
            return ToolResult(
                success=False, output="", error=f"Tool {name} failed: {e}"
            )

    def to_openai_schema(self) -> list[dict]:
        """Convert all tools to OpenAI function calling format."""
        result = []
        for tool in self._tools.values():
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": _params_to_json_schema(tool.parameters),
                },
            })
        return result

    def to_anthropic_schema(self) -> list[dict]:
        """Convert all tools to Anthropic tool_use format."""
        result = []
        for tool in self._tools.values():
            result.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": _params_to_json_schema(tool.parameters),
            })
        return result


def _params_to_json_schema(params: list[ToolParam]) -> dict:
    """Convert a list of ToolParam to a JSON Schema object."""
    properties = {}
    required = []

    for p in params:
        prop: dict[str, Any] = {
            "type": p.type,
            "description": p.description,
        }
        if p.enum is not None:
            prop["enum"] = p.enum
        if p.default is not None:
            prop["default"] = p.default
        if p.items is not None:
            prop["items"] = p.items
        properties[p.name] = prop
        if p.required:
            required.append(p.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema
