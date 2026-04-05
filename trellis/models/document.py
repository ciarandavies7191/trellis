"""
pipeline_runtime.models.document
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Shared document representation used by load_document, select, extract_table,
and extract_text tools.

A DocumentHandle is the opaque value that flows between document-oriented
tasks in the DSL.  It carries:

  - raw bytes or decoded text for each page / sheet
  - detected format and source metadata
  - an optional flag indicating the document required (or should require) OCR
  - provenance: which pages / sheets are present in this handle (a `select`
    call produces a reduced handle covering only the matched subset)

PageList is the specialized form emitted by `select` — it holds a subset of
pages from a parent handle so downstream tools know the exact provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Format enum
# ---------------------------------------------------------------------------


class DocFormat(str, Enum):
    PDF        = "pdf"
    XLSX       = "xlsx"
    CSV        = "csv"
    DOCX       = "docx"
    TEXT       = "text"
    IMAGE      = "image"   # jpg / png / tiff / webp treated as single-page docs
    UNKNOWN    = "unknown"

    @classmethod
    def from_suffix(cls, suffix: str) -> "DocFormat":
        _map = {
            ".pdf":  cls.PDF,
            ".xlsx": cls.XLSX,
            ".xls":  cls.XLSX,
            ".csv":  cls.CSV,
            ".docx": cls.DOCX,
            ".doc":  cls.DOCX,
            ".txt":  cls.TEXT,
            ".md":   cls.TEXT,
            ".jpg":  cls.IMAGE,
            ".jpeg": cls.IMAGE,
            ".png":  cls.IMAGE,
            ".tiff": cls.IMAGE,
            ".tif":  cls.IMAGE,
            ".webp": cls.IMAGE,
        }
        return _map.get(suffix.lower(), cls.UNKNOWN)


# ---------------------------------------------------------------------------
# Page representation
# ---------------------------------------------------------------------------


@dataclass
class Page:
    """
    A single page (PDF) or row-band (XLSX sheet) within a document.

    Attributes:
        number:       1-based page number within the source document.
        text:         Decoded text content.  Empty string if image-only.
        image_bytes:  Raw PNG/JPEG bytes of the rendered page, present only
                      when the page was rasterised (scanned PDF or image doc).
        image_mime:   MIME type of image_bytes ("image/png", "image/jpeg", …).
        is_scanned:   True when the page has very little selectable text and
                      was rasterised for OCR.
        sheet_name:   Populated for XLSX pages (one entry per sheet).
        metadata:     Arbitrary per-page metadata dict.
    """
    number:      int
    text:        str                 = ""
    image_bytes: bytes | None        = None
    image_mime:  str                 = "image/png"
    is_scanned:  bool                = False
    sheet_name:  str | None          = None
    metadata:    dict[str, Any]      = field(default_factory=dict)

    # ------------------------------ Helpers ------------------------------ #
    def native_char_count(self) -> int:
        """Return count of native (non-OCR) characters if provided by loader."""
        try:
            return int((self.metadata or {}).get("native_char_count", 0))
        except Exception:
            return 0

    def image_coverage(self) -> float | None:
        """Return cumulative image area coverage ratio [0..1] if provided."""
        val = (self.metadata or {}).get("image_coverage")
        try:
            if val is None:
                return None
            f = float(val)
            if f < 0.0:
                return 0.0
            if f > 1.0:
                return 1.0
            return f
        except Exception:
            return None

    def size(self) -> tuple[float, float] | None:
        """Return (width, height) if provided by loader metadata."""
        md = self.metadata or {}
        w = md.get("width") or md.get("page_width")
        h = md.get("height") or md.get("page_height")
        try:
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                return float(w), float(h)
        except Exception:
            return None
        return None


# ---------------------------------------------------------------------------
# DocumentHandle
# ---------------------------------------------------------------------------


@dataclass
class DocumentHandle:
    """
    Opaque handle that flows between document tasks.

    Attributes:
        source:        Original path or URL the document was loaded from.
        format:        Detected file format.
        pages:         Ordered list of Page objects.
        page_count:    Total pages in the *original* document (may exceed
                       len(pages) if this is a reduced handle from `select`).
        is_scanned:    True if the majority of pages are image-only.
        source_url:    Source URL if the document was fetched from the web.
        metadata:      Document-level metadata (title, author, creation date, …).
    """
    source:     str
    format:     DocFormat
    pages:      list[Page]           = field(default_factory=list)
    page_count: int                  = 0
    is_scanned: bool                 = False
    source_url: str | None           = None
    metadata:   dict[str, Any]       = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Convenience helpers
    # ------------------------------------------------------------------ #

    @property
    def filename(self) -> str:
        return Path(self.source).name

    def page_numbers(self) -> list[int]:
        """1-based page numbers present in this handle."""
        return [p.number for p in self.pages]

    def full_text(self) -> str:
        """Concatenated selectable text across all pages (pre-OCR)."""
        return "\n\n".join(p.text for p in self.pages if p.text)

    def scanned_page_count(self) -> int:
        return sum(1 for p in self.pages if p.is_scanned)

    def needs_ocr(self, threshold: int = 100) -> bool:
        """
        True if any page has less than `threshold` selectable characters
        *and* has image bytes available — i.e. OCR would help.
        """
        return any(
            len(p.text) < threshold and p.image_bytes is not None
            for p in self.pages
        )


# ---------------------------------------------------------------------------
# PageList  (produced by `select`)
# ---------------------------------------------------------------------------


@dataclass
class PageList:
    """
    A reduced view of a DocumentHandle, produced by the `select` tool.

    Carries only the pages that matched the selection prompt, plus full
    provenance back to the parent document.

    Attributes:
        parent_source:  `source` of the DocumentHandle that was filtered.
        parent_format:  Format of the parent document.
        pages:          Subset of Page objects that matched the selector.
        selector_prompt: The natural-language prompt used to select pages.
        metadata:       Inherited document-level metadata.
    """
    parent_source:   str
    parent_format:   DocFormat
    pages:           list[Page]      = field(default_factory=list)
    selector_prompt: str             = ""
    metadata:        dict[str, Any]  = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Helpers matching DocumentHandle's interface so callers are uniform
    # ------------------------------------------------------------------ #

    @property
    def source(self) -> str:
        return self.parent_source

    @property
    def format(self) -> DocFormat:
        return self.parent_format

    @property
    def filename(self) -> str:
        return Path(self.parent_source).name

    def page_numbers(self) -> list[int]:
        return [p.number for p in self.pages]

    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    def needs_ocr(self, threshold: int = 100) -> bool:
        return any(
            len(p.text) < threshold and p.image_bytes is not None
            for p in self.pages
        )


# ---------------------------------------------------------------------------
# Type alias for tool inputs
# ---------------------------------------------------------------------------

#: Anything a document-processing tool will accept as its `document` parameter.
DocumentInput = DocumentHandle | PageList | list[DocumentHandle] | str