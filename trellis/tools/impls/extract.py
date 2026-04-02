"""Stub extractors: extract_table and extract_text."""

from __future__ import annotations

from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput


class ExtractTableTool(BaseTool):
    def __init__(self, name: str = "extract_table") -> None:
        super().__init__(name, "Extract structured tables from documents (stub)")

    def execute(self, document: Any, selector: str | None = None, **kwargs: Any) -> Dict[str, Any]:
        return {"status": "success", "document": document, "selector": selector, "tables": []}

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "document": ToolInput(name="document", description="Document handle or text", required=True),
            "selector": ToolInput(name="selector", description="Optional hint or region", required=False, default=None),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="tables", description="List of extracted tables", type_="array")


class ExtractTextTool(BaseTool):
    def __init__(self, name: str = "extract_text") -> None:
        super().__init__(name, "Extract plain text from documents (stub)")

    def execute(self, document: Any, selector: str | None = None, **kwargs: Any) -> Dict[str, Any]:
        return {"status": "success", "document": document, "selector": selector, "text": ""}

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "document": ToolInput(name="document", description="Document handle or region", required=True),
            "selector": ToolInput(name="selector", description="Optional section/range hint", required=False, default=None),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="text", description="Extracted text", type_="string")

