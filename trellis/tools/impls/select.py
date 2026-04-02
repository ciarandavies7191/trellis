"""Stub 'select' tool — narrows document content based on a prompt (placeholder)."""

from __future__ import annotations

from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput


class SelectTool(BaseTool):
    """Select relevant subset of a document by NL prompt (stub)."""

    def __init__(self, name: str = "select") -> None:
        super().__init__(name, "Filter a document to relevant pages/sections/sheets (stub)")

    def execute(self, document: Any, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        # Placeholder: echo back document with a stub 'selection' note
        return {
            "status": "success",
            "selection_prompt": prompt,
            "document": document,
        }

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "document": ToolInput(name="document", description="Document or list of documents", required=True),
            "prompt": ToolInput(name="prompt", description="Selection prompt", required=True),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="selection", description="Reduced document or subset", type_="object")

