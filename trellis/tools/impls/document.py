"""ingest_document tool — loads a document and fully resolves it including OCR.

After this tool runs, all pages in the returned DocumentHandle have their
`.text` field populated (native text for digital PDFs, OCR result for scanned
pages and images).  Downstream tools (select, extract_from_texts,
extract_from_tables) never need to consider OCR.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import pathlib
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Union
import litellm  # type: ignore
import fitz  # PyMuPDF  # type: ignore


from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.document import DocumentHandle, Page, DocFormat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Image-coverage ratio at or above which a PDF page is rasterised for OCR.
RASTERIZE_COVERAGE_THRESHOLD: float = float(
    os.getenv("PYMUPDF_RASTERIZE_COVERAGE_THRESHOLD", "0.25")
)

#: DPI used when rasterizing PDF pages to PNG for OCR.
RASTERIZE_DPI: int = int(os.getenv("PYMUPDF_RASTERIZE_DPI", "150"))

#: litellm model string used for OCR.
INGEST_OCR_MODEL: str = os.getenv("INGEST_OCR_MODEL") or os.getenv(
    "EXTRACT_TEXT_MODEL", "openai/gpt-4o"
)

#: Native-char threshold below which we prefer OCR on raster-heavy pages.
OCR_MIN_NATIVE_CHARS: int = int(os.getenv("EXTRACT_MIN_NATIVE_CHARS", "80"))

#: Page image-coverage threshold [0..1] above which we prefer OCR.
OCR_IMAGE_COVERAGE_THRESHOLD: float = float(
    os.getenv("EXTRACT_IMAGE_COVERAGE_THRESHOLD", "0.25")
)

#: Max tokens for OCR transcription per page.
OCR_MAX_TOKENS: int = 2048

# ---------------------------------------------------------------------------
# OCR — multimodal transcription via vision model
# ---------------------------------------------------------------------------

_OCR_SYSTEM = textwrap.dedent("""\
    You are a precise document transcription engine.
    You will be shown an image of a document page.
    Transcribe ALL text exactly as it appears — preserve paragraph breaks,
    list structure, table layouts (use plain-text ASCII alignment), and
    section headings.  Do not add commentary, summaries, or markdown fencing.
    Output only the transcribed text.
""").strip()


def _should_ocr_page(page: Page) -> bool:
    """Return True if this page should be OCR'd rather than using native text."""
    try:
        coverage = page.metadata.get("image_coverage")
    except Exception:
        coverage = None
    try:
        native_chars = page.metadata.get("native_char_count", len(page.text or ""))
    except Exception:
        native_chars = len(page.text or "")

    if coverage is not None:
        return coverage >= OCR_IMAGE_COVERAGE_THRESHOLD or native_chars < OCR_MIN_NATIVE_CHARS
    # Fallback when coverage metric is unavailable
    return len((page.text or "").strip()) < 150 and page.image_bytes is not None


def _ocr_page(page: Page, model: str) -> str:
    """Send page image to vision LLM and return transcribed text."""
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
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "Transcribe all text on this page."},
            ],
        },
    ]

    response = litellm.completion(
        model=model,
        messages=messages,
        max_tokens=OCR_MAX_TOKENS,
    )
    return (response.choices[0].message.content or "").strip()


def _apply_ocr_to_pages(pages: List[Page], model: str) -> List[Page]:
    """Run OCR on any page that needs it; return the updated page list in-place."""
    for page in pages:
        if _should_ocr_page(page) and page.image_bytes is not None:
            logger.info(
                "ingest_document: OCR page=%d  coverage=%s  native_chars=%d",
                page.number,
                page.metadata.get("image_coverage", "N/A"),
                page.metadata.get("native_char_count", len(page.text or "")),
            )
            page.text = _ocr_page(page, model)
            page.is_scanned = True
    return pages


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _is_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def _detect_format(path: str, content_type: Optional[str] = None) -> DocFormat:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct == "application/pdf":
            return DocFormat.PDF
        if ct.startswith("text/") or ct.endswith("json"):
            return DocFormat.TEXT
        if ct in ("image/png", "image/jpeg", "image/jpg", "image/tiff", "image/webp"):
            return DocFormat.IMAGE
        if ct == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            return DocFormat.XLSX
        if ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return DocFormat.DOCX
    ext = pathlib.Path(path).suffix.lower()
    return {
        ".pdf": DocFormat.PDF,
        ".txt": DocFormat.TEXT,
        ".md": DocFormat.TEXT,
        ".csv": DocFormat.TEXT,
        ".json": DocFormat.TEXT,
        ".xlsx": DocFormat.XLSX,
        ".xls": DocFormat.XLSX,
        ".docx": DocFormat.DOCX,
        ".doc": DocFormat.DOCX,
        ".png": DocFormat.IMAGE,
        ".jpg": DocFormat.IMAGE,
        ".jpeg": DocFormat.IMAGE,
        ".tif": DocFormat.IMAGE,
        ".tiff": DocFormat.IMAGE,
        ".webp": DocFormat.IMAGE,
    }.get(ext, DocFormat.UNKNOWN)


def _read_text(data: bytes, max_chars: int = 50000) -> str:
    try:
        text = data.decode("utf-8", errors="replace")
        return text[:max_chars]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# PDF page extraction (PyMuPDF)
# ---------------------------------------------------------------------------


def _pymupdf_pages(data: bytes) -> List[Page]:
    """Parse PDF bytes with PyMuPDF to populate rich per-page metadata.

    Provides per-page: width/height, native_char_count, image_regions,
    image_coverage, image_coverage_pct, and text from native text blocks.

    Pages whose image_coverage meets RASTERIZE_COVERAGE_THRESHOLD are
    rasterised and stored in image_bytes so OCR can run at ingest time.
    """
    if fitz is None:  # pragma: no cover - optional dep not installed
        raise RuntimeError("PyMuPDF (fitz) not installed. pip install pymupdf")

    doc = fitz.open(stream=data, filetype="pdf")
    pages: List[Page] = []
    try:
        for i in range(doc.page_count):
            p = doc.load_page(i)
            rect = p.rect
            width, height = float(rect.width), float(rect.height)

            # Text metrics
            text_dict = p.get_text("dict") or {}
            blocks = text_dict.get("blocks", []) or []
            native_chars = 0
            preview_lines: List[str] = []
            for b in blocks:
                if b.get("type") == 0:  # text block
                    for line in b.get("lines", []) or []:
                        for sp in line.get("spans", []) or []:
                            s = sp.get("text", "") or ""
                            native_chars += len(s)
                            if s:
                                preview_lines.append(s)

            # Image regions
            img_bboxes: List[tuple[float, float, float, float]] = []
            try:
                for info in p.get_image_info():
                    bbox = info.get("bbox")
                    if isinstance(bbox, fitz.Rect):
                        img_bboxes.append((float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1)))
                    elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                        x0, y0, x1, y1 = bbox
                        img_bboxes.append((float(x0), float(y0), float(x1), float(y1)))
            except Exception:
                pass

            page_area = max(1.0, width * height)
            img_area_sum = sum(
                max(0.0, x1 - x0) * max(0.0, y1 - y0)
                for x0, y0, x1, y1 in img_bboxes
            )
            coverage = max(0.0, min(1.0, img_area_sum / page_area)) if img_bboxes else 0.0

            # Rasterise image-heavy pages so OCR has bytes at ingest time.
            image_bytes: Optional[bytes] = None
            is_scanned = False
            if coverage >= RASTERIZE_COVERAGE_THRESHOLD:
                try:
                    mat = fitz.Matrix(RASTERIZE_DPI / 72, RASTERIZE_DPI / 72)
                    pix = p.get_pixmap(matrix=mat, alpha=False)
                    image_bytes = pix.tobytes("png")
                    is_scanned = True
                except Exception:
                    pass

            metadata = {
                "width": width,
                "height": height,
                "native_char_count": native_chars,
                "image_regions": img_bboxes,
                "image_coverage": coverage,
                "image_coverage_pct": round(coverage * 100, 2),
            }

            pages.append(Page(
                number=i + 1,
                text=("\n".join(preview_lines))[:5000],
                image_bytes=image_bytes,
                image_mime="image/png",
                is_scanned=is_scanned,
                metadata=metadata,
            ))
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return pages


# ---------------------------------------------------------------------------
# Handle construction
# ---------------------------------------------------------------------------


def _handle_from_bytes(
    source: str,
    data: bytes,
    fmt: DocFormat,
    *,
    is_url: bool = False,
    content_type: Optional[str] = None,
) -> DocumentHandle:
    pages: List[Page] = []
    page_count: int = 1

    if fmt == DocFormat.PDF:
        if fitz is not None:
            try:
                pages = _pymupdf_pages(data)
                page_count = len(pages)
            except Exception:
                pages = []
        if not pages:
            try:
                reader = PyPDF2.PdfReader(io.BytesIO(data))  # type: ignore[attr-defined]
                page_count = len(reader.pages)
                for idx, pg in enumerate(getattr(reader, "pages", []) or []):
                    try:
                        txt = pg.extract_text() or ""
                    except Exception:
                        txt = ""
                    pages.append(Page(number=idx + 1, text=txt, is_scanned=False))
            except Exception:
                pages = [Page(number=1, text="", is_scanned=False)]
                page_count = 1
    elif fmt == DocFormat.IMAGE:
        mime = content_type or {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".webp": "image/webp",
        }.get(pathlib.Path(source).suffix.lower(), "image/png")
        pages = [Page(number=1, text="", image_bytes=data, image_mime=mime, is_scanned=True)]
        page_count = 1
    else:
        text = _read_text(data)
        try:
            obj = json.loads(text)
            text = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            pass
        pages = [Page(number=1, text=text, is_scanned=False)]
        page_count = 1

    return DocumentHandle(
        source=source,
        format=fmt,
        pages=pages,
        page_count=page_count,
        is_scanned=False,
        source_url=source if is_url else None,
        metadata={},
    )


def _load_local(path: str) -> DocumentHandle:
    with open(path, "rb") as fh:
        data = fh.read()
    fmt = _detect_format(path)
    return _handle_from_bytes(os.path.abspath(path), data, fmt, is_url=False)


def _load_url(url: str) -> DocumentHandle:
    req = urllib.request.Request(url, headers={"User-Agent": "Trellis/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type") or None
    fmt = _detect_format(url, content_type)
    return _handle_from_bytes(url, data, fmt, is_url=True, content_type=content_type)


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------


class IngestDocumentTool(BaseTool):
    """Load and fully ingest a document, including eager OCR for scanned pages.

    After execution, every page in the returned DocumentHandle has its `.text`
    field populated — either from native PDF text or from OCR applied at ingest
    time.  Downstream tools (select, extract_from_texts, extract_from_tables)
    can always treat `.text` as ready to use.
    """

    def __init__(self, name: str = "ingest_document"):
        super().__init__(name, "Load a document and fully resolve it (including OCR) into a DocumentHandle")

    def execute(
        self,
        path: Union[str, List[str], DocumentHandle] | Dict[str, Any],
        model: str = INGEST_OCR_MODEL,
        **kwargs: Any,
    ) -> DocumentHandle | List[DocumentHandle]:
        """
        Ingest a document or list of documents.

        Args:
            path: A file path/URL string, a list of paths/URLs, or an existing
                  DocumentHandle (passed through as-is; OCR already applied).
            model: litellm model string for OCR (default: INGEST_OCR_MODEL env var).

        Returns:
            A DocumentHandle or list[DocumentHandle] with all pages fully resolved.
        """
        # Pass-through existing handle (assume already ingested)
        if isinstance(path, DocumentHandle):
            return path

        path_list: List[Any] = path if isinstance(path, list) else [path]

        handles: List[DocumentHandle] = []
        for i, p in enumerate(path_list):
            # Already-ingested handles pass through without re-processing.
            if isinstance(p, DocumentHandle):
                handles.append(p)
                continue
            if not isinstance(p, str):
                raise TypeError(
                    f"ingest_document: unsupported item type {type(p).__name__!r} "
                    f"at index {i}. Expected a file path/URL string or DocumentHandle."
                )
            item = p
            try:
                if _is_url(item):
                    handle = _load_url(item)
                else:
                    handle = _load_local(item)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:  # pragma: no cover
                raise RuntimeError(f"Failed to fetch {item}: {e}") from e
            except Exception as exc:
                raise RuntimeError(f"Failed to load {item}: {exc}") from exc

            # Eagerly run OCR on any scanned/image-heavy pages
            _apply_ocr_to_pages(handle.pages, model)
            handles.append(handle)

        return handles[0] if len(handles) == 1 else handles

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "path": ToolInput(
                name="path",
                description="Path/URL string, list of paths/URLs, or existing DocumentHandle",
                required=True,
            ),
            "model": ToolInput(
                name="model",
                description="litellm model string override for OCR",
                required=False,
                default=INGEST_OCR_MODEL,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="document",
            description="DocumentHandle or list[DocumentHandle] with all pages fully resolved",
            type_="object",
        )
