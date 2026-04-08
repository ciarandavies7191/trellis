"""Base tool protocol and interfaces."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple, Type
from dataclasses import dataclass, field


@dataclass
class ToolInput:
    """Tool input specification."""

    name: str
    description: str
    required: bool = True
    default: Optional[Any] = None
    #: If set, the value passed at runtime must be an instance of one of these
    #: types. ``None`` means any type is accepted. Used by ``validate_inputs``.
    accepted_types: Optional[Tuple[Type, ...]] = field(default=None, compare=False)


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

    def validate_inputs(self, inputs: Dict[str, Any]) -> None:
        """
        Validate tool inputs against the declared schema.

        Checks that all required inputs are present and that every supplied
        value whose spec declares ``accepted_types`` is an instance of one of
        those types.

        Args:
            inputs: Input parameters to validate.

        Raises:
            ValueError: if a required input is missing or a value has the wrong type.
        """
        spec_map = self.get_inputs()

        for name, spec in spec_map.items():
            if spec.required and name not in inputs:
                raise ValueError(
                    f"Tool {self.name!r}: required input {name!r} is missing."
                )

        for name, value in inputs.items():
            spec = spec_map.get(name)
            if spec is None or spec.accepted_types is None:
                continue
            if not isinstance(value, spec.accepted_types):
                type_names = " | ".join(t.__name__ for t in spec.accepted_types)
                raise TypeError(
                    f"Tool {self.name!r}: input {name!r} must be {type_names}, "
                    f"got {type(value).__name__!r}."
                )


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
