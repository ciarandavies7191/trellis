"""Mock tool implementation for testing."""

from ..base import BaseTool, ToolInput, ToolOutput
from typing import Any, Dict


class MockTool(BaseTool):
    """Mock tool that returns configurable responses."""

    def __init__(self, name: str = "mock", description: str = "Mock tool for testing"):
        """Initialize mock tool."""
        super().__init__(name, description)
        self.call_count = 0
        self.last_inputs: Dict[str, Any] = {}

    def execute(self, **kwargs) -> Any:
        """
        Execute mock tool.

        Args:
            **kwargs: Mock tool arguments

        Returns:
            Mock response
        """
        self.call_count += 1
        self.last_inputs = kwargs

        return {
            "status": "success",
            "message": "Mock tool executed",
            "inputs": kwargs,
            "call_count": self.call_count
        }

    def get_inputs(self) -> Dict[str, ToolInput]:
        """Get mock tool inputs."""
        return {
            "input_data": ToolInput(
                name="input_data",
                description="Input data for mock tool",
                required=False,
                default=None
            )
        }

    def get_output(self) -> ToolOutput:
        """Get mock tool output."""
        return ToolOutput(
            name="output",
            description="Mock tool output"
        )

    def reset(self) -> None:
        """Reset call count and inputs."""
        self.call_count = 0
        self.last_inputs = {}
