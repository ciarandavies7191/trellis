from __future__ import annotations

import base64
import logging
import os
import textwrap
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import litellm  # type: ignore

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

#: litellm model string used for both OCR and selector calls unless overridden.
DEFAULT_MODEL: str = os.getenv(
    "EXTRACT_TEXT_MODEL", "openai/gpt-4o"
)

#: Native-char threshold below which we prefer OCR on raster-heavy pages.
EXTRACT_MIN_NATIVE_CHARS: int = int(os.getenv("EXTRACT_MIN_NATIVE_CHARS", "80"))

#: Page image coverage threshold [0..1] above which we prefer OCR.
EXTRACT_IMAGE_COVERAGE_THRESHOLD: float = float(
    os.getenv("EXTRACT_IMAGE_COVERAGE_THRESHOLD", "0.25")
)

#: Pages with fewer selectable chars than this are candidates for OCR
#: (legacy fallback only when coverage metric is unavailable).
OCR_CHAR_THRESHOLD: int = int(os.getenv("EXTRACT_TEXT_OCR_THRESHOLD", "150"))

#: Max tokens for OCR transcription per page.
OCR_MAX_TOKENS: int = 2048

#: Max tokens for selector resolution.
SELECTOR_MAX_TOKENS: int = 4096


# ---------------------------------------------------------------------------
# LLMBackend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMBackend(Protocol):
    """
    Minimal protocol for an LLM completion callable.

    Accepts an OpenAI-compatible messages list and keyword arguments, returns
    the response as a plain string.  This is the only seam between the tool
    and any underlying LLM provider.

    The default implementation wraps ``litellm.completion()``.
    Inject a custom backend in tests or to swap providers at call-time.
    """

    def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        **kwargs: Any,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Default litellm backend factory
# ---------------------------------------------------------------------------


def make_litellm_backend(model: str = DEFAULT_MODEL) -> LLMBackend:
    """
    Return an LLMBackend that routes through ``litellm.completion()``.
    """
    if litellm is None:  # fail fast only when actually used
        raise RuntimeError(
            "litellm is not installed. Install with: pip install litellm"
        )
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
# Result model
# ---------------------------------------------------------------------------


@dataclass
class PageResult:
    """
    Extracted text for a single page, with provenance.
    """
    page_number: int
    text:        str
    ocr_applied: bool
    source:      str
    sheet_name:  str | None = None


@dataclass
class ExtractTextResult:
    """
    Full output of the extract_text tool.
    """
    text:           str
    pages:          list[PageResult]
    sources:        list[str]
    selector:       str | None = None
    ocr_page_count: int        = 0
    model:          str        = DEFAULT_MODEL

    def __str__(self) -> str:
        return self.text


# ---------------------------------------------------------------------------
# OCR  —  multimodal transcription via vision model
# ---------------------------------------------------------------------------

_OCR_SYSTEM = textwrap.dedent("""\
    You are a precise document transcription engine.
    You will be shown an image of a document page.
    Transcribe ALL text exactly as it appears — preserve paragraph breaks,
    list structure, table layouts (use plain-text ASCII alignment), and
    section headings.  Do not add commentary, summaries, or markdown fencing.
    Output only the transcribed text.
""").strip()


def _ocr_page(page: Page, backend: LLMBackend) -> str:
    if page.image_bytes is None:
        raise ValueError(f"Page {page.number} has no image bytes — cannot OCR.")

    b64 = base64.standard_b64encode(page.image_bytes).decode("ascii")
    data_url = f"data:{page.image_mime};base64,{b64}"

    logger.debug(
        "OCR: page=%d  mime=%s  bytes=%d",
        page.number, page.image_mime, len(page.image_bytes),
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _OCR_SYSTEM},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                },
                {
                    "type": "text",
                    "text": "Transcribe all text on this page.",
                },
            ],
        },
    ]

    return backend(messages, max_tokens=OCR_MAX_TOKENS).strip()


# ---------------------------------------------------------------------------
# Selector resolution
# ---------------------------------------------------------------------------

_SELECTOR_SYSTEM = textwrap.dedent("""\
    You are a document section extractor.
    Given the full text of a document and a region selector, return only the
    portion of the text that matches the selector.  Preserve the original
    wording exactly — do not paraphrase, summarise, or add commentary.
    If the selector matches nothing, reply with exactly: [NO MATCH]
""").strip()


def _apply_selector(full_text: str, selector: str, backend: LLMBackend) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SELECTOR_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Document text:\n\n{full_text}\n\n"
                f"---\n\nSelector: {selector}\n\n"
                "Extract and return only the section matching the selector."
            ),
        },
    ]

    result = backend(messages, max_tokens=SELECTOR_MAX_TOKENS).strip()

    if result == "[NO MATCH]":
        logger.warning("Selector %r matched nothing — returning full text.", selector)
        return full_text

    return result


# ---------------------------------------------------------------------------
# Per-page extraction
# ---------------------------------------------------------------------------


def _extract_page(page: Page, source: str, backend: LLMBackend) -> PageResult:
    # Prefer OCR when coverage is high or native char count is low.
    try:
        coverage = page.metadata.get("image_coverage")
    except Exception:
        coverage = None
    try:
        native_chars = page.metadata.get("native_char_count", len(page.text or ""))
    except Exception:
        native_chars = len(page.text or "")

    prefer_ocr = (
        (coverage is not None and coverage >= EXTRACT_IMAGE_COVERAGE_THRESHOLD)
        or (native_chars < EXTRACT_MIN_NATIVE_CHARS)
    )

    # Legacy fallback when coverage isn't available at all.
    legacy_low_text = len((page.text or "").strip()) < OCR_CHAR_THRESHOLD

    needs_ocr = prefer_ocr or (legacy_low_text and page.image_bytes is not None)

    if needs_ocr and page.image_bytes is None:
        # Cannot OCR without a raster — fall back with a notice.
        logger.info(
            "OCR preferred for page=%d (coverage=%s, native_chars=%d) but no image bytes available — falling back to text.",
            getattr(page, "number", -1),
            f"{coverage:.2f}" if isinstance(coverage, (int, float)) else "None",
            native_chars,
        )
        text = page.text or ""
        ocr_applied = False
    elif needs_ocr:
        logger.info(
            "OCR selected: source=%r  page=%d  coverage=%s  native_chars=%d",
            source,
            getattr(page, "number", -1),
            f"{coverage:.2f}" if isinstance(coverage, (int, float)) else "None",
            native_chars,
        )
        text = _ocr_page(page, backend)
        ocr_applied = True
    else:
        text = page.text
        ocr_applied = False

    return PageResult(
        page_number=page.number,
        text=text,
        ocr_applied=ocr_applied,
        source=source,
        sheet_name=page.sheet_name,
    )


# ---------------------------------------------------------------------------
# Handle extraction
# ---------------------------------------------------------------------------


def _extract_handle(
    handle: DocumentHandle | PageList,
    backend: LLMBackend,
) -> list[PageResult]:
    return [_extract_page(p, handle.source, backend) for p in handle.pages]


# ---------------------------------------------------------------------------
# Polymorphic input normalisation
# ---------------------------------------------------------------------------


def _normalise_input(document: DocumentInput) -> list[DocumentHandle | PageList]:
    """
    Coerce any accepted input form into a flat list of handle-like objects.
    """
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
        f"extract_text: unsupported document type {type(document).__name__!r}. "
        "Expected DocumentHandle, PageList, list[DocumentHandle], or str."
    )


# ---------------------------------------------------------------------------
# Public tool entry point
# ---------------------------------------------------------------------------


def extract_text(
    document: DocumentInput,
    selector: str | None = None,
    *,
    model: str = DEFAULT_MODEL,
    backend: LLMBackend | None = None,
) -> ExtractTextResult:
    """
    Extract plain text from a document handle, page list, or raw string.
    """
    llm = backend if backend is not None else make_litellm_backend(model)

    handles = _normalise_input(document)

    # 1) Extract text for all pages
    all_page_results: list[PageResult] = []
    for handle in handles:
        all_page_results.extend(_extract_handle(handle, llm))

    # 2) Assemble full text with source separators when multiple docs
    if len(handles) > 1:
        sections: list[str] = []
        current_source: str | None = None
        current_lines: list[str] = []

        for pr in all_page_results:
            if pr.source != current_source:
                if current_lines:
                    sections.append("\n\n".join(current_lines))
                current_lines = []
                current_source = pr.source

            header = f"[Page {pr.page_number}"
            if pr.sheet_name:
                header += f" / Sheet: {pr.sheet_name}"
            header += f"  |  source: {pr.source}]"
            current_lines.append(f"{header}\n{pr.text}")

        if current_lines:
            sections.append("\n\n".join(current_lines))

        separator = "\n\n" + ("─" * 60) + "\n\n"
        full_text = separator.join(sections)
    else:
        full_text = "\n\n".join(pr.text for pr in all_page_results if pr.text)

    # 3) Apply selector if provided
    final_text = full_text
    if selector and full_text.strip():
        logger.info("Applying selector: %r", selector)
        final_text = _apply_selector(full_text, selector, llm)

    # 4) Build result
    unique_sources = list(dict.fromkeys(pr.source for pr in all_page_results))
    ocr_count = sum(1 for pr in all_page_results if pr.ocr_applied)

    return ExtractTextResult(
        text=final_text,
        pages=all_page_results,
        sources=unique_sources,
        selector=selector,
        ocr_page_count=ocr_count,
        model=model,
    )


# ---------------------------------------------------------------------------
# BaseTool wrappers (for AsyncToolRegistry auto-discovery)
# ---------------------------------------------------------------------------


class ExtractTextTool(BaseTool):
    def __init__(self, name: str = "extract_text") -> None:
        super().__init__(name, "Extract plain text from documents (LLM OCR capable)")

    def execute(self, document: Any, selector: str | None = None, **kwargs: Any) -> Any:
        model = kwargs.get("model", DEFAULT_MODEL)
        backend = kwargs.get("backend")
        return extract_text(
            document=document,
            selector=selector,
            model=model,
            backend=backend,
        )

    def get_inputs(self) -> dict[str, ToolInput]:
        return {
            "document": ToolInput(
                name="document",
                description="Document handle, page list, list, or raw string",
                required=True,
            ),
            "selector": ToolInput(
                name="selector",
                description="Optional natural-language region selector",
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
            name="text",
            description="Extracted text (stringable result)",
            type_="string",
        )


class ExtractTableTool(BaseTool):
    def __init__(self, name: str = "extract_table") -> None:
        super().__init__(name, "Extract structured tables from documents (stub)")

    def execute(self, document: Any, selector: str | None = None, classification: Any | None = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "document": str(document)[:200],
            "selector": selector,
            "classification": type(classification).__name__ if classification is not None else None,
            "tables": [],
        }

    def get_inputs(self) -> dict[str, ToolInput]:
        return {
            "document": ToolInput(name="document", description="Document handle or text", required=True),
            "selector": ToolInput(name="selector", description="Optional hint or region", required=False, default=None),
            "classification": ToolInput(name="classification", description="PageClassification or list to guide backend", required=False, default=None),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="tables", description="List of extracted tables", type_="array")


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
