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

    if fmt == DocFormat.PDF and PyPDF2 is not None:
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
            # Fallback: single empty page
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
        # Treat as text-like (txt/csv/json/unknown)
        text = _read_text(data)
        # If JSON, pretty-print to stabilise structure
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
        is_scanned=fmt == DocFormat.IMAGE,
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
