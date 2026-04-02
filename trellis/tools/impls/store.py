"""Stub `store` tool — persist a value under a key (placeholder)."""

from __future__ import annotations

from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput


class StoreTool(BaseTool):
    def __init__(self, name: str = "store") -> None:
        super().__init__(name, "Persist a value to the session blackboard (stub)")

    def execute(self, key: str, value: Any, append: bool = False, **kwargs: Any) -> Dict[str, Any]:
        # Placeholder: echo intent; actual persistence occurs in orchestrator/blackboard integration
        return {
            "status": "success",
            "key": key,
            "append": append,
            "summary": str(value)[:200],
        }

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "key": ToolInput(name="key", description="Blackboard/session key", required=True),
            "value": ToolInput(name="value", description="Value to store", required=True),
            "append": ToolInput(name="append", description="Append to existing value", required=False, default=False),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="result", description="Confirmation of store operation", type_="object")

