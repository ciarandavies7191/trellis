"""Stub `search_web` tool — perform web search and return snippets (placeholder)."""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BaseTool, ToolInput, ToolOutput


class SearchWebTool(BaseTool):
    def __init__(self, name: str = "search_web") -> None:
        super().__init__(name, "Perform web search and return snippets with URLs (stub)")

    def execute(self, query: str | List[str], **kwargs: Any) -> Dict[str, Any]:
        queries = query if isinstance(query, list) else [query]
        results = [
            {"title": f"Result for {q}", "snippet": "...", "url": f"https://example.com/?q={q}"}
            for q in queries
        ]
        return {"status": "success", "results": results}

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "query": ToolInput(name="query", description="Query string or list of queries", required=True),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="results", description="List of search results", type_="array")

