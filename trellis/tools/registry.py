"""Tool registry management and discovery."""

from typing import Dict, List, Optional, Type
from .base import BaseTool


class ToolRegistryManager:
    """Manages tool registration and discovery."""

    def __init__(self):
        """Initialize registry manager."""
        self._tools: Dict[str, BaseTool] = {}
        self._tool_classes: Dict[str, Type[BaseTool]] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """
        Register a tool instance.

        Args:
            tool: Tool instance
        """
        self._tools[tool.name] = tool

    def register_tool_class(self, name: str, tool_class: Type[BaseTool]) -> None:
        """
        Register a tool class for lazy instantiation.

        Args:
            name: Tool name
            tool_class: Tool class
        """
        self._tool_classes[name] = tool_class

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """
        Get tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None
        """
        if name in self._tools:
            return self._tools[name]

        if name in self._tool_classes:
            tool = self._tool_classes[name]()
            self._tools[name] = tool
            return tool

        return None

    def list_available_tools(self) -> List[str]:
        """
        List all available tool names.

        Returns:
            List of tool names
        """
        return list(set(list(self._tools.keys()) + list(self._tool_classes.keys())))

    def get_tool_metadata(self, name: str) -> Optional[Dict]:
        """
        Get metadata for a tool.

        Args:
            name: Tool name

        Returns:
            Tool metadata or None
        """
        tool = self.get_tool(name)
        if tool:
            return {
                "name": tool.name,
                "description": tool.description,
                "inputs": tool.get_inputs(),
                "output": tool.get_output()
            }
        return None
