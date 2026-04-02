"""Base tool protocol and interfaces."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass


@dataclass
class ToolInput:
    """Tool input specification."""

    name: str
    description: str
    required: bool = True
    default: Optional[Any] = None


@dataclass
class ToolOutput:
    """Tool output specification."""

    name: str
    description: str
    type_: str = "object"


class BaseTool(ABC):
    """Base class for all tools."""

    def __init__(self, name: str, description: str):
        """
        Initialize tool.

        Args:
            name: Tool name
            description: Tool description
        """
        self.name = name
        self.description = description

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """
        Execute the tool.

        Args:
            **kwargs: Tool-specific arguments

        Returns:
            Tool output
        """
        pass

    def get_inputs(self) -> Dict[str, ToolInput]:
        """Get tool input specifications."""
        return {}

    def get_output(self) -> ToolOutput:
        """Get tool output specification."""
        return ToolOutput(name="output", description="Tool output")

    def validate_inputs(self, inputs: Dict[str, Any]) -> bool:
        """
        Validate tool inputs.

        Args:
            inputs: Input parameters

        Returns:
            True if valid, False otherwise
        """
        required_inputs = {
            name: spec for name, spec in self.get_inputs().items()
            if spec.required
        }

        for req_input in required_inputs:
            if req_input not in inputs:
                return False

        return True


class ToolRegistry:
    """Registry for managing available tools."""

    def __init__(self):
        """Initialize tool registry."""
        self.tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """
        Register a tool.

        Args:
            tool: Tool instance to register
        """
        self.tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        """
        Get tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None
        """
        return self.tools.get(name)

    def list_tools(self) -> Dict[str, BaseTool]:
        """Get all registered tools."""
        return self.tools.copy()
