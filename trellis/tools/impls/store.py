"""Stub `store` tool — persist a value under a key (placeholder)."""

from __future__ import annotations

from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput
from ..decorators import export_io


@export_io(path="debug/tools")
class StoreTool(BaseTool):
    def __init__(self, name: str = "store") -> None:
        super().__init__(name, "Persist a value to the session blackboard (stub)")

    def execute(self, key: str, value: Any, append: bool = False, **kwargs: Any) -> Dict[str, Any]:
        # Actual persistence occurs in the DAG executor (dag.py) which writes to the blackboard.
        # Return the value directly so the CLI's _json_sanitize can serialize it to proper JSON
        # (e.g. PeriodDescriptor dataclasses become dicts), making it copy-pasteable into session files.
        return {
            "status": "success",
            "key": key,
            "append": append,
            "value": value,
        }

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "key": ToolInput(name="key", description="Blackboard/session key", required=True),
            "value": ToolInput(name="value", description="Value to store", required=True),
            "append": ToolInput(name="append", description="Append to existing value", required=False, default=False),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="result", description="Confirmation of store operation", type_="object")

