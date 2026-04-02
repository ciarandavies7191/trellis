"""Document processing tool."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Union

from ..base import BaseTool, ToolInput, ToolOutput

# Optional PDF support
try:  # pragma: no cover - optional path
    import PyPDF2  # type: ignore
except Exception:  # pragma: no cover - optional path
    PyPDF2 = None  # type: ignore


def _is_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def _detect_format(path: str, content_type: Optional[str] = None) -> str:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct == "application/pdf":
            return "pdf"
        if ct in ("text/plain", "text/markdown"):
            return "txt"
        if ct in ("text/csv", "application/csv"):
            return "csv"
        if ct in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",):
            return "xlsx"
        if ct in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",):
            return "docx"
    ext = pathlib.Path(path).suffix.lower()
    return {
        ".pdf": "pdf",
        ".txt": "txt",
        ".md": "txt",
        ".csv": "csv",
        ".xlsx": "xlsx",
        ".xls": "xlsx",
        ".docx": "docx",
        ".doc": "docx",
        ".json": "json",
    }.get(ext, "unknown")


def _read_text_preview(data: bytes, max_chars: int = 50000) -> str:
    try:
        text = data.decode("utf-8", errors="replace")
        return text[:max_chars]
    except Exception:
        return ""


def _load_local(path: str) -> Dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        size = len(data)
        fmt = _detect_format(path)
        handle: Dict[str, Any] = {
            "type": "document",
            "source": {"path": os.path.abspath(path)},
            "format": fmt,
            "meta": {
                "filename": os.path.basename(path),
                "size_bytes": size,
            },
            "content_preview": "",
        }
        if fmt == "pdf" and PyPDF2 is not None:
            try:
                reader = PyPDF2.PdfReader(io.BytesIO(data))
                handle["meta"]["page_count"] = len(reader.pages)
                # Provide a small text preview from first page
                first_text = ""
                if reader.pages:
                    try:
                        first_text = reader.pages[0].extract_text() or ""
                    except Exception:
                        first_text = ""
                handle["content_preview"] = (first_text or "").strip()[:2000]
            except Exception:
                # Fallback to no PDF parsing
                pass
        elif fmt in ("txt", "csv", "json"):
            preview = _read_text_preview(data)
            handle["content_preview"] = preview
            if fmt == "json":
                try:
                    handle["json"] = json.loads(preview)
                except Exception:
                    pass
        return {"status": "success", "handle": handle}
    except Exception as exc:
        return {"status": "error", "error": f"Failed to read local file: {exc}", "source": {"path": path}}


def _load_url(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Trellis/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type")
        fmt = _detect_format(url, content_type)
        handle: Dict[str, Any] = {
            "type": "document",
            "source": {"url": url},
            "format": fmt,
            "meta": {
                "content_type": content_type,
                "size_bytes": len(data),
            },
            "content_preview": "",
        }
        if fmt == "pdf" and PyPDF2 is not None:
            try:
                reader = PyPDF2.PdfReader(io.BytesIO(data))
                handle["meta"]["page_count"] = len(reader.pages)
                first_text = ""
                if reader.pages:
                    try:
                        first_text = reader.pages[0].extract_text() or ""
                    except Exception:
                        first_text = ""
                handle["content_preview"] = (first_text or "").strip()[:2000]
            except Exception:
                pass
        elif fmt in ("txt", "csv", "json"):
            preview = _read_text_preview(data)
            handle["content_preview"] = preview
            if fmt == "json":
                try:
                    handle["json"] = json.loads(preview)
                except Exception:
                    pass
        return {"status": "success", "handle": handle}
    except urllib.error.HTTPError as e:
        return {"status": "error", "error": f"HTTP error {e.code}", "source": {"url": url}}
    except urllib.error.URLError as e:
        return {"status": "error", "error": f"Network error: {e.reason}", "source": {"url": url}}
    except Exception as exc:
        return {"status": "error", "error": f"Failed to fetch URL: {exc}", "source": {"url": url}}


class DocumentTool(BaseTool):
    """Tool for loading documents from paths or URLs into working memory."""

    def __init__(self, name: str = "load_document"):
        super().__init__(name, "Load files or URLs and emit document handles")

    def execute(self, path: Union[str, List[str]] | Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Load a document or list of documents.

        Args:
            path: A file path/URL string, a list of paths/URLs, or an existing handle
            **kwargs: Ignored but accepted for polymorphism/agility

        Returns:
            A single handle (dict) or a list of handles under key 'handles'.
        """
        # If caller passed an existing handle, return as-is
        if isinstance(path, dict) and path.get("type") == "document":
            return {"status": "success", "handle": path}

        items: List[str]
        if isinstance(path, list):
            items = [str(p) for p in path]
        else:
            items = [str(path)]

        results: List[Dict[str, Any]] = []
        for item in items:
            if _is_url(item):
                results.append(_load_url(item))
            else:
                results.append(_load_local(item))

        # If single input, unwrap to single-handle return for convenience
        if len(results) == 1:
            return results[0]
        return {"status": "success", "handles": results}

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
            description="Document handle or list of handles with metadata and preview",
            type_="object",
        )
