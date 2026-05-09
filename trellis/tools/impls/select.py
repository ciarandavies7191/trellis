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
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..decorators import export_io

if TYPE_CHECKING:
    from trellis.retrieval.pipeline import ChunkPipeline

logger = logging.getLogger(__name__)

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
        result: List[DocumentHandle | PageList] = []
        for i, d in enumerate(document):
            if isinstance(d, (DocumentHandle, PageList)):
                result.append(d)
            else:
                raise TypeError(
                    f"select: unsupported item type {type(d).__name__!r} at index {i} in document list. "
                    "Expected DocumentHandle or PageList. "
                    "If you passed a mixed list, ensure upstream tasks return homogeneous outputs."
                )
        return result
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
        heading = (p.metadata or {}).get("heading", "")
        if heading:
            extras.append(f"heading={heading!r}")
        page_type = (p.metadata or {}).get("type", "")
        if page_type == "table":
            extras.append("type=table")
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
            num_retries=6,
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
        if chosen:
            return _subset_by_pages(document, chosen)
        # LLM returned empty — fall through to passthrough so downstream tools
        # still receive content (XBRL/HTML filings may confuse the selector).
        logger.warning(
            "select: LLM returned no pages for prompt %r on %r — passing through all %d pages",
            prompt.strip()[:80], document.source, len(document.pages),
        )
    # No prompt, no explicit pages, or empty LLM result: passthrough (all pages)
    return PageList(parent_source=document.source, parent_format=document.format, pages=list(document.pages), selector_prompt="[passthrough]")

def _bm25_retrieve_pages(
    document: DocumentHandle | PageList,
    prompt: str,
    chunk_pipeline: Optional[ChunkPipeline],
    top_k: int,
) -> PageList:
    """BM25-based retrieval that always returns a PageList compatible with extract_from_texts.

    When chunk_pipeline is None, builds an ephemeral ChunkPipeline on-the-fly (BM25-only,
    no embeddings). Matches chunks by BM25 score, maps back to source pages, and returns
    a deduplicated PageList ordered by page number.
    """
    import hashlib
    from trellis.retrieval.pipeline import ChunkPipeline as _ChunkPipeline
    from trellis.retrieval.plugins import RetrievalRegistry

    if chunk_pipeline is None:
        pipeline = _ChunkPipeline(RetrievalRegistry())
    else:
        pipeline = chunk_pipeline

    # Use document_id from existing chunk_state if available, else derive from source
    doc_handle = document if isinstance(document, DocumentHandle) else None
    existing_state = getattr(doc_handle, "chunk_state", None) if doc_handle else None
    if existing_state and existing_state.document_id:
        document_id = existing_state.document_id
        already_indexed = document_id in pipeline.bm25_indexes
    else:
        document_id = hashlib.sha256(document.source.encode()).hexdigest()[:16]
        already_indexed = document_id in pipeline.bm25_indexes

    if not already_indexed:
        pipeline.run_sync(document, "default", document_id)

    bm25_idx = pipeline.bm25_indexes.get(document_id)
    if bm25_idx is None:
        logger.warning("select(chunk): BM25 index missing after run_sync — falling back to passthrough")
        return PageList(
            parent_source=document.source,
            parent_format=document.format,
            pages=list(document.pages),
            selector_prompt="[chunk-fallback-passthrough]",
        )

    scored = bm25_idx.search(prompt, k=top_k)
    if not scored:
        logger.warning("select(chunk): BM25 returned no results for %r — falling back to passthrough", prompt[:80])
        return PageList(
            parent_source=document.source,
            parent_format=document.format,
            pages=list(document.pages),
            selector_prompt="[chunk-fallback-passthrough]",
        )

    # Map chunk IDs → page numbers
    matched_pages: set[int] = set()
    for chunk_id, _score in scored:
        chunk = pipeline.chunk_lookup.get(chunk_id)
        if chunk is not None and chunk.page is not None:
            matched_pages.add(chunk.page)

    if not matched_pages:
        logger.warning("select(chunk): no page numbers resolved from BM25 results — passthrough")
        return PageList(
            parent_source=document.source,
            parent_format=document.format,
            pages=list(document.pages),
            selector_prompt="[chunk-fallback-passthrough]",
        )

    logger.info("select(chunk): BM25 matched pages %s for query %r", sorted(matched_pages), prompt[:80])
    return _subset_by_pages(document, sorted(matched_pages))


@export_io(path="debug/tools")
class SelectTool(BaseTool):
    """Retrieval tool: filter a document to relevant pages by NL prompt or explicit page numbers.

    Assumes page text is already populated (run ingest_document first).
    In granularity=page mode (default), uses LLM to identify relevant page numbers.
    In granularity=chunk mode, uses hybrid BM25+dense retrieval via ChunkPipeline.
    """

    def __init__(self, name: str = "select", chunk_pipeline: Optional[ChunkPipeline] = None) -> None:
        super().__init__(name, "Filter a document to relevant pages/sections/chunks")
        self._chunk_pipeline = chunk_pipeline

    def execute(self, document: Any, prompt: str | None = None, pages: List[int] | None = None, **kwargs: Any) -> Any:
        granularity = kwargs.get("granularity", "page")
        model = kwargs.get("model", DEFAULT_SELECT_MODEL)

        if granularity == "chunk":
            if not prompt:
                raise ValueError("select(granularity=chunk): 'prompt' is required in chunk mode.")
            handles = _normalise_input(document)
            top_k = int(kwargs.get("top_k", 15))
            if len(handles) == 1:
                return _bm25_retrieve_pages(handles[0], prompt, self._chunk_pipeline, top_k)
            return [_bm25_retrieve_pages(h, prompt, self._chunk_pipeline, top_k) for h in handles]

        handles = _normalise_input(document)
        results: List[PageList] = []
        for h in handles:
            results.append(_select_handle(h, prompt, pages, model))
        return results[0] if len(results) == 1 else results

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "document": ToolInput(name="document", description="DocumentHandle, PageList, list thereof, or inline str", required=True, accepted_types=(DocumentHandle, PageList, list, str)),
            "prompt": ToolInput(name="prompt", description="Selection prompt (NL)", required=False, default=None),
            "pages": ToolInput(name="pages", description="Explicit page numbers to select (1-based)", required=False, default=None),
            "model": ToolInput(name="model", description="litellm model override", required=False, default=DEFAULT_SELECT_MODEL),
            "granularity": ToolInput(name="granularity", description="'page' (default LLM page scan) or 'chunk' (hybrid BM25+dense retrieval)", required=False, default="page"),
            "filter": ToolInput(name="filter", description="ChunkFilter dict for chunk mode (section_labels, chunk_types, period_labels, document_ids, extra_filters)", required=False, default=None),
            "rerank": ToolInput(name="rerank", description="Apply cross-encoder re-ranking (chunk mode only)", required=False, default=False),
            "top_k": ToolInput(name="top_k", description="Number of chunks to return (chunk mode only)", required=False, default=15),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="selection", description="PageList, list[PageList], or RetrievalResult (chunk mode)", type_="object")
