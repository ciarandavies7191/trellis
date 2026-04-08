"""'select' tool — retrieval step that filters document pages to a relevant subset.

This tool is a pure retrieval mechanism.  It assumes all pages already have
clean text (i.e. the document was ingested via ingest_document, which handles
OCR).  It does not perform OCR or extract structured data — those are the
responsibilities of ingest_document and extract_from_texts/extract_from_tables
respectively.

Selection modes (in priority order):
1. Explicit page numbers — if the caller passes a ``pages`` list.
2. NL prompt — uses LLM to identify relevant page numbers from a page inventory.
3. Passthrough — if neither is provided, all pages are returned as a PageList.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

try:
    import litellm  # type: ignore
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore

from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.document import (
    DocumentHandle,
    Page,
    PageList,
    DocFormat,
)

# Model selection (falls back to EXTRACT_TEXT_MODEL if SELECT_MODEL not set)
DEFAULT_SELECT_MODEL = os.getenv("SELECT_MODEL") or os.getenv(
    "EXTRACT_TEXT_MODEL", "openai/gpt-4o"
)


def _normalise_input(document: Any) -> List[DocumentHandle | PageList]:
    if isinstance(document, (DocumentHandle, PageList)):
        return [document]
    if isinstance(document, list):
        return [d for d in document if isinstance(d, (DocumentHandle, PageList))]
    if isinstance(document, str):
        # Inline text as single-page handle
        page = Page(number=1, text=document, is_scanned=False)
        handle = DocumentHandle(source="<inline>", format=DocFormat.TEXT, pages=[page], page_count=1)
        return [handle]
    raise TypeError(
        f"select: unsupported document type {type(document).__name__!r}. "
        "Expected DocumentHandle, PageList, list[DocumentHandle|PageList], or str."
    )


def _subset_by_pages(handle: DocumentHandle | PageList, pages: List[int]) -> PageList:
    wanted = set(int(p) for p in pages if isinstance(p, (int, str)))
    subset = [p for p in handle.pages if p.number in wanted]
    return PageList(parent_source=handle.source, parent_format=handle.format, pages=subset, selector_prompt="[explicit pages]")


def _build_inventory(handle: DocumentHandle | PageList, max_chars_per_page: int = 300) -> str:
    lines: List[str] = []
    for p in handle.pages:
        text = (p.text or "").replace("\n", " ").strip()
        snippet = text[:max_chars_per_page]
        extras: List[str] = []
        if p.sheet_name:
            extras.append(f"sheet={p.sheet_name}")
        cov = p.metadata.get("image_coverage") if p.metadata else None
        if cov is not None:
            extras.append(f"image_coverage={cov:.2f}")
        meta = ("; ".join(extras)) if extras else ""
        lines.append(f"- page {p.number}{(' [' + meta + ']') if meta else ''}: {snippet}")
    return "\n".join(lines)


def _select_pages_via_llm(prompt: str, handle: DocumentHandle | PageList, model: str) -> List[int]:
    if litellm is None:  # pragma: no cover
        raise RuntimeError("litellm is not installed. pip install litellm")
    system = (
        "You are a precise page selector. Given a list of pages with short snippets, "
        "return a JSON array with the 1-based page numbers that are relevant. Output only the array."
    )
    inventory = _build_inventory(handle)
    user = (
        f"Selection criteria: {prompt}\n\n"
        f"Pages:\n{inventory}\n\n"
        "Return JSON array of integers, e.g., [1,4,7]."
    )
    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=256,
        )
        content = resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        # Best-effort fallback: select nothing on failure
        return []

    # Try to parse JSON array
    text = content.strip()
    # If model wrapped in code fences
    m = re.search(r"\[.*?\]", text, flags=re.S)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [int(x) for x in data if isinstance(x, (int, float, str))]
        if isinstance(data, dict) and "pages" in data and isinstance(data["pages"], list):
            return [int(x) for x in data["pages"]]
    except Exception:
        # Fallback: extract integers
        nums = re.findall(r"\b\d+\b", text)
        return [int(n) for n in nums]
    return []


def _select_handle(document: DocumentHandle | PageList, prompt: str | None, pages: List[int] | None, model: str) -> PageList:
    if pages and len(pages) > 0:
        return _subset_by_pages(document, pages)
    if prompt and prompt.strip():
        chosen = _select_pages_via_llm(prompt.strip(), document, model)
        return _subset_by_pages(document, chosen)
    # No prompt and no explicit pages: passthrough (all pages)
    return PageList(parent_source=document.source, parent_format=document.format, pages=list(document.pages), selector_prompt="[passthrough]")


class SelectTool(BaseTool):
    """Retrieval tool: filter a document to relevant pages by NL prompt or explicit page numbers.

    Assumes page text is already populated (run ingest_document first).
    Uses LLM to identify relevant page numbers from a page inventory.
    """

    def __init__(self, name: str = "select") -> None:
        super().__init__(name, "Filter a document to relevant pages/sections/sheets")

    def execute(self, document: Any, prompt: str | None = None, pages: List[int] | None = None, **kwargs: Any) -> Any:
        model = kwargs.get("model", DEFAULT_SELECT_MODEL)
        handles = _normalise_input(document)
        results: List[PageList] = []
        for h in handles:
            results.append(_select_handle(h, prompt, pages, model))
        return results[0] if len(results) == 1 else results

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "document": ToolInput(name="document", description="Document or list of documents", required=True),
            "prompt": ToolInput(name="prompt", description="Selection prompt (NL)", required=False, default=None),
            "pages": ToolInput(name="pages", description="Explicit page numbers to select (1-based)", required=False, default=None),
            "model": ToolInput(name="model", description="litellm model override", required=False, default=DEFAULT_SELECT_MODEL),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="selection", description="Reduced document (PageList) or list of PageList", type_="object")
