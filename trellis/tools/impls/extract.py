"""extract_from_texts and extract_from_tables tools.

These tools assume the document has already been fully ingested by
ingest_document (all pages have clean .text — OCR already applied).
They do NOT perform OCR; they perform structured extraction from text content.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

try:
    import litellm  # type: ignore
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore

from trellis.models.document import (
    DocumentHandle,
    DocumentInput,
    Page,
    PageList,
)
from ..base import BaseTool, ToolInput, ToolOutput

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: litellm model for structured extraction calls.
DEFAULT_MODEL: str = os.getenv("EXTRACT_MODEL") or os.getenv(
    "EXTRACT_TEXT_MODEL", "openai/gpt-4o"
)

#: Max tokens for extraction responses.
EXTRACT_MAX_TOKENS: int = 4096

# ---------------------------------------------------------------------------
# LLMBackend protocol (seam for testing)
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMBackend(Protocol):
    """Minimal protocol for an LLM completion callable."""

    def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        **kwargs: Any,
    ) -> str: ...


def make_litellm_backend(model: str = DEFAULT_MODEL) -> LLMBackend:
    """Return an LLMBackend that routes through litellm.completion()."""
    if litellm is None:  # pragma: no cover
        raise RuntimeError("litellm is not installed. Install with: pip install litellm")

    def _backend(
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        **kwargs: Any,
    ) -> str:
        response = litellm.completion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            **kwargs,
        )
        return response.choices[0].message.content or ""

    return _backend


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TextExtractionResult:
    """Output of the extract_from_texts tool."""

    extracted: dict[str, Any]  # LLM-extracted fields as structured JSON
    source_pages: list[int]    # 1-based page numbers processed
    sources: list[str]         # unique source document paths
    prompt: str                # the extraction prompt used
    model: str = DEFAULT_MODEL

    def __str__(self) -> str:
        return json.dumps(self.extracted, ensure_ascii=False, indent=2)


@dataclass
class TableResult:
    """A single extracted table."""

    headers: list[str]
    rows: list[dict[str, Any]]  # [{column_name: value, ...}, ...]
    source_page: int
    sheet_name: str | None = None
    selector: str | None = None


@dataclass
class TableExtractionResult:
    """Output of the extract_from_tables tool."""

    tables: list[TableResult]
    source_pages: list[int]
    sources: list[str]
    model: str = DEFAULT_MODEL

    def __str__(self) -> str:
        return json.dumps(
            [
                {
                    "headers": t.headers,
                    "rows": t.rows,
                    "source_page": t.source_page,
                    "sheet_name": t.sheet_name,
                    "selector": t.selector,
                }
                for t in self.tables
            ],
            ensure_ascii=False,
            indent=2,
        )


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def _normalise_input(document: DocumentInput) -> list[DocumentHandle | PageList]:
    if isinstance(document, (DocumentHandle, PageList)):
        return [document]
    if isinstance(document, list):
        result: list[DocumentHandle | PageList] = []
        for item in document:
            result.extend(_normalise_input(item))
        return result
    if isinstance(document, str):
        from trellis.models.document import DocFormat
        page = Page(number=1, text=document, is_scanned=False)
        handle = DocumentHandle(
            source="<inline>",
            format=DocFormat.TEXT,
            pages=[page],
            page_count=1,
        )
        return [handle]
    raise TypeError(
        f"Unsupported document type {type(document).__name__!r}. "
        "Expected DocumentHandle, PageList, list[DocumentHandle], or str."
    )


def _pages_text(handle: DocumentHandle | PageList) -> tuple[str, list[int]]:
    """Concatenate page text from a handle; return (text, page_numbers)."""
    parts: list[str] = []
    numbers: list[int] = []
    for p in handle.pages:
        if p.text:
            parts.append(f"[Page {p.number}]\n{p.text}")
            numbers.append(p.number)
    return "\n\n".join(parts), numbers


# ---------------------------------------------------------------------------
# extract_from_texts — structured field extraction from text content
# ---------------------------------------------------------------------------

_EXTRACT_TEXTS_SYSTEM = textwrap.dedent("""\
    You are a precise document extraction engine.
    Given document text and an extraction prompt, extract the requested
    information and return it as a JSON object.
    Use field names that reflect what was asked for.
    If a value cannot be found, set it to null.
    Output only the JSON object — no markdown fencing, no commentary.
""").strip()


def extract_from_texts(
    document: DocumentInput,
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    backend: LLMBackend | None = None,
) -> TextExtractionResult:
    """Extract specific fields from document text using a prompt.

    Args:
        document: A DocumentHandle, PageList, list thereof, or raw string.
                  Pages must already have clean text (run ingest_document first).
        prompt: What to extract, e.g. "extract the grand total and invoice date".
        model: litellm model string override.
        backend: Optional LLMBackend (for testing).

    Returns:
        TextExtractionResult with extracted dict, source pages, and sources.
    """
    llm = backend if backend is not None else make_litellm_backend(model)
    handles = _normalise_input(document)

    all_text_parts: list[str] = []
    all_page_numbers: list[int] = []
    unique_sources: list[str] = []

    for handle in handles:
        text, page_nums = _pages_text(handle)
        if text:
            all_text_parts.append(text)
        all_page_numbers.extend(page_nums)
        if handle.source not in unique_sources:
            unique_sources.append(handle.source)

    combined_text = "\n\n" + ("─" * 60) + "\n\n".join(all_text_parts)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _EXTRACT_TEXTS_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Document text:\n\n{combined_text}\n\n"
                f"---\n\nExtraction request: {prompt}"
            ),
        },
    ]

    raw = llm(messages, max_tokens=EXTRACT_MAX_TOKENS).strip()

    # Strip markdown fencing if model added it anyway
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError:
        # If JSON parsing fails, wrap raw response to preserve the info
        logger.warning("extract_from_texts: could not parse JSON response; wrapping as raw string")
        extracted = {"result": raw}

    return TextExtractionResult(
        extracted=extracted,
        source_pages=sorted(set(all_page_numbers)),
        sources=unique_sources,
        prompt=prompt,
        model=model,
    )


# ---------------------------------------------------------------------------
# extract_from_tables — structured table extraction
# ---------------------------------------------------------------------------

_EXTRACT_TABLES_SYSTEM = textwrap.dedent("""\
    You are a precise table extraction engine.
    Given document text (and optionally an image), identify all tables present.
    For each table return a JSON object with:
      - "headers": list of column header strings
      - "rows": list of objects mapping each header to its cell value
      - "source_page": the 1-based page number the table appeared on (integer)
      - "sheet_name": sheet name if applicable, otherwise null
    Return a JSON array of these objects — even if there is only one table.
    If no tables are found, return an empty array [].
    Output only the JSON array — no markdown fencing, no commentary.
""").strip()

_EXTRACT_TABLES_SYSTEM_WITH_SELECTOR = textwrap.dedent("""\
    You are a precise table extraction engine.
    Given document text (and optionally an image), identify the table matching
    the provided selector.
    Return a JSON array containing a single JSON object with:
      - "headers": list of column header strings
      - "rows": list of objects mapping each header to its cell value
      - "source_page": the 1-based page number the table appeared on (integer)
      - "sheet_name": sheet name if applicable, otherwise null
    If no matching table is found, return an empty array [].
    Output only the JSON array — no markdown fencing, no commentary.
""").strip()


def _extract_tables_from_page(
    page: Page,
    source: str,
    selector: str | None,
    backend: LLMBackend,
) -> list[TableResult]:
    """Extract tables from a single page using LLM."""
    system = _EXTRACT_TABLES_SYSTEM_WITH_SELECTOR if selector else _EXTRACT_TABLES_SYSTEM

    content: list[dict[str, Any]] = []

    # Include image if available (for image-based tables)
    if page.image_bytes:
        b64 = base64.standard_b64encode(page.image_bytes).decode("ascii")
        data_url = f"data:{page.image_mime};base64,{b64}"
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    user_text = f"[Page {page.number}]"
    if page.sheet_name:
        user_text += f" [Sheet: {page.sheet_name}]"
    if page.text:
        user_text += f"\n\n{page.text}"
    if selector:
        user_text += f"\n\n---\n\nExtract the table: {selector}"
    else:
        user_text += "\n\n---\n\nExtract all tables from this page."

    content.append({"type": "text", "text": user_text})

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": content if page.image_bytes else user_text},
    ]

    raw = backend(messages, max_tokens=EXTRACT_MAX_TOKENS).strip()

    # Strip markdown fencing
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("extract_from_tables: could not parse JSON for page %d", page.number)
        return []

    if not isinstance(data, list):
        return []

    results: list[TableResult] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        results.append(TableResult(
            headers=item.get("headers", []),
            rows=item.get("rows", []),
            source_page=item.get("source_page", page.number),
            sheet_name=item.get("sheet_name") or page.sheet_name,
            selector=selector,
        ))
    return results


def extract_from_tables(
    document: DocumentInput,
    selector: str | None = None,
    *,
    model: str = DEFAULT_MODEL,
    backend: LLMBackend | None = None,
) -> TableExtractionResult:
    """Extract structured table data from document pages.

    Args:
        document: A DocumentHandle, PageList, list thereof, or raw string.
                  Pages must already have clean text (run ingest_document first).
        selector: Optional hint to target a specific table (e.g. "income statement").
        model: litellm model string override.
        backend: Optional LLMBackend (for testing).

    Returns:
        TableExtractionResult with structured table objects, source pages, and sources.
    """
    llm = backend if backend is not None else make_litellm_backend(model)
    handles = _normalise_input(document)

    all_tables: list[TableResult] = []
    all_page_numbers: list[int] = []
    unique_sources: list[str] = []

    for handle in handles:
        if handle.source not in unique_sources:
            unique_sources.append(handle.source)
        for page in handle.pages:
            tables = _extract_tables_from_page(page, handle.source, selector, llm)
            all_tables.extend(tables)
            if page.number not in all_page_numbers:
                all_page_numbers.append(page.number)

    return TableExtractionResult(
        tables=all_tables,
        source_pages=sorted(set(all_page_numbers)),
        sources=unique_sources,
        model=model,
    )


# ---------------------------------------------------------------------------
# BaseTool wrappers (for AsyncToolRegistry auto-discovery)
# ---------------------------------------------------------------------------


class ExtractFromTextsTool(BaseTool):
    """Extract specific fields from document text as structured JSON."""

    def __init__(self, name: str = "extract_from_texts") -> None:
        super().__init__(name, "Extract specific fields from document text as structured JSON")

    def execute(self, document: Any, prompt: str, **kwargs: Any) -> TextExtractionResult:
        model = kwargs.get("model", DEFAULT_MODEL)
        backend = kwargs.get("backend")
        return extract_from_texts(
            document=document,
            prompt=prompt,
            model=model,
            backend=backend,
        )

    def get_inputs(self) -> dict[str, ToolInput]:
        return {
            "document": ToolInput(
                name="document",
                description="Document handle, page list, list thereof, or raw string (text must already be extracted)",
                required=True,
            ),
            "prompt": ToolInput(
                name="prompt",
                description='What to extract, e.g. "extract the grand total and invoice date"',
                required=True,
            ),
            "model": ToolInput(
                name="model",
                description="litellm model string override",
                required=False,
                default=DEFAULT_MODEL,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="extracted",
            description="Structured JSON dict of extracted fields",
            type_="object",
        )


class ExtractFromTablesTool(BaseTool):
    """Extract structured row/column/cell data from tables in a document."""

    def __init__(self, name: str = "extract_from_tables") -> None:
        super().__init__(name, "Extract structured table data (rows/columns/cells) from document pages")

    def execute(self, document: Any, selector: str | None = None, **kwargs: Any) -> TableExtractionResult:
        model = kwargs.get("model", DEFAULT_MODEL)
        backend = kwargs.get("backend")
        return extract_from_tables(
            document=document,
            selector=selector,
            model=model,
            backend=backend,
        )

    def get_inputs(self) -> dict[str, ToolInput]:
        return {
            "document": ToolInput(
                name="document",
                description="Document handle, page list, list thereof, or raw string",
                required=True,
            ),
            "selector": ToolInput(
                name="selector",
                description='Optional hint to target a specific table, e.g. "income statement"',
                required=False,
                default=None,
            ),
            "model": ToolInput(
                name="model",
                description="litellm model string override",
                required=False,
                default=DEFAULT_MODEL,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="tables",
            description="List of extracted tables with headers and rows",
            type_="array",
        )


class ExtractChartTool(BaseTool):
    def __init__(self, name: str = "extract_chart") -> None:
        super().__init__(name, "Extract chart data from documents (stub)")

    def execute(self, document: Any, classification: Any | None = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "document": str(document)[:200],
            "classification": type(classification).__name__ if classification is not None else None,
            "charts": [],
        }

    def get_inputs(self) -> dict[str, ToolInput]:
        return {
            "document": ToolInput(name="document", description="Document handle or text", required=True),
            "classification": ToolInput(name="classification", description="PageClassification or list to guide backend", required=False, default=None),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="charts", description="List of extracted charts", type_="array")
