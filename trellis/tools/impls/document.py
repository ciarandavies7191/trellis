"""Document processing tool emitting DocumentHandle dataclass values."""

from __future__ import annotations

import io
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Union

from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.document import DocumentHandle, Page, DocFormat

import PyPDF2  # type: ignore
import fitz  # PyMuPDF  # type: ignore


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


#: Image-coverage ratio at or above which a PDF page is rasterised for OCR.
RASTERIZE_COVERAGE_THRESHOLD: float = float(
    os.getenv("PYMUPDF_RASTERIZE_COVERAGE_THRESHOLD", "0.25")
)

#: DPI used when rasterising PDF pages to PNG for OCR.
RASTERIZE_DPI: int = int(os.getenv("PYMUPDF_RASTERIZE_DPI", "150"))


def _pymupdf_pages(data: bytes) -> List[Page]:
    """Parse PDF bytes with PyMuPDF to populate rich per-page metadata.

    Provides:
      - width/height (points)
      - native_char_count (from page.get_text("dict"))
      - image_regions (list of image bbox tuples)
      - image_coverage (sum(image areas)/page area)
      - image_coverage_pct (image_coverage * 100, rounded to 2dp)
      - text (concatenated text from blocks for quick preview)

    Pages whose image_coverage meets RASTERIZE_COVERAGE_THRESHOLD are also
    rasterised and stored in image_bytes so extract_text can OCR them.
    """
    if fitz is None:  # pragma: no cover - optional dep not installed
        raise RuntimeError("PyMuPDF (fitz) not installed. pip install pymupdf")

    doc = fitz.open(stream=data, filetype="pdf")
    pages: List[Page] = []
    try:
        for i in range(doc.page_count):
            p = doc.load_page(i)
            rect = p.rect  # fitz.Rect
            width, height = float(rect.width), float(rect.height)

            # Text metrics
            text_dict = p.get_text("dict") or {}
            blocks = text_dict.get("blocks", []) or []
            native_chars = 0
            preview_lines: List[str] = []
            for b in blocks:
                if b.get("type") == 0:  # text block
                    for line in b.get("lines", []) or []:
                        spans = line.get("spans", []) or []
                        for sp in spans:
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

            # Rasterise pages that are image-heavy so downstream OCR has bytes.
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
        # Prefer PyMuPDF if available for rich metadata; fallback to PyPDF2
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

    handle = DocumentHandle(
        source=source if not is_url else source,
        format=fmt,
        pages=pages,
        page_count=page_count,
        is_scanned=False,
        source_url=source if is_url else None,
        metadata={},
    )
    return handle


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


class DocumentTool(BaseTool):
    """Tool for loading documents from paths or URLs into working memory."""

    def __init__(self, name: str = "load_document"):
        super().__init__(name, "Load files or URLs and emit DocumentHandle values")

    def execute(self, path: Union[str, List[str], DocumentHandle] | Dict[str, Any], **kwargs) -> DocumentHandle | List[DocumentHandle]:
        """
        Load a document or list of documents.

        Args:
            path: A file path/URL string, a list of paths/URLs, or an existing DocumentHandle
                   (legacy dict handles are no longer emitted; if provided they are not supported).

        Returns:
            A DocumentHandle or a list[DocumentHandle].
        """
        # Pass-through existing handle
        if isinstance(path, DocumentHandle):
            return path

        # Normalize to list of str paths/URLs
        items: List[str]
        if isinstance(path, list):
            items = [str(p) for p in path]
        else:
            items = [str(path)]

        handles: List[DocumentHandle] = []
        for item in items:
            try:
                if _is_url(item):
                    handles.append(_load_url(item))
                else:
                    handles.append(_load_local(item))
            except (urllib.error.HTTPError, urllib.error.URLError) as e:  # pragma: no cover - network
                raise RuntimeError(f"Failed to fetch {item}: {e}") from e
            except Exception as exc:
                raise RuntimeError(f"Failed to load {item}: {exc}") from exc

        return handles[0] if len(handles) == 1 else handles

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "path": ToolInput(
                name="path",
                description="Path/URL string, list of paths/URLs, or existing handle",
                required=True,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="document",
            description="DocumentHandle or list[DocumentHandle] with pages and metadata",
            type_="object",
        )
