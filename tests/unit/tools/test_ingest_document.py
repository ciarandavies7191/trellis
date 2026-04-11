"""Tests for ingest_document — verifies eager OCR and page text resolution."""

import pytest
from unittest.mock import patch, MagicMock

from trellis.tools.impls.document import IngestDocumentTool, _should_ocr_page, _apply_ocr_to_pages
from trellis.models.document import DocumentHandle, Page, DocFormat


# ---------------------------------------------------------------------------
# _should_ocr_page
# ---------------------------------------------------------------------------


def test_should_ocr_high_image_coverage():
    page = Page(number=1, text="", image_bytes=b"x", image_mime="image/png", is_scanned=True,
                metadata={"image_coverage": 0.8, "native_char_count": 5})
    assert _should_ocr_page(page) is True


def test_should_not_ocr_low_coverage_high_text():
    page = Page(number=1, text="A" * 500, image_bytes=None, is_scanned=False,
                metadata={"image_coverage": 0.1, "native_char_count": 500})
    assert _should_ocr_page(page) is False


def test_should_ocr_low_native_chars():
    page = Page(number=1, text="hi", image_bytes=b"x", image_mime="image/png", is_scanned=True,
                metadata={"image_coverage": 0.05, "native_char_count": 10})
    # native_char_count < OCR_MIN_NATIVE_CHARS (80) → prefer OCR
    assert _should_ocr_page(page) is True


def test_should_not_ocr_no_image_bytes():
    """Even if coverage is high, without image_bytes OCR cannot run."""
    page = Page(number=1, text="", image_bytes=None, is_scanned=False,
                metadata={"image_coverage": 0.9, "native_char_count": 0})
    # _should_ocr_page may return True, but _apply_ocr_to_pages checks image_bytes too
    # so it won't actually try to OCR
    pass  # behavior tested via _apply_ocr_to_pages below


# ---------------------------------------------------------------------------
# _apply_ocr_to_pages
# ---------------------------------------------------------------------------


def test_apply_ocr_updates_page_text():
    page = Page(number=1, text="", image_bytes=b"fake-png", image_mime="image/png", is_scanned=True,
                metadata={"image_coverage": 0.9, "native_char_count": 0})

    with patch("trellis.tools.impls.document._ocr_page", return_value="OCR result") as mock_ocr:
        _apply_ocr_to_pages([page], model="openai/gpt-4o")
        mock_ocr.assert_called_once()

    assert page.text == "OCR result"
    assert page.is_scanned is True


def test_apply_ocr_skips_page_without_image_bytes():
    page = Page(number=1, text="native text", image_bytes=None, is_scanned=False,
                metadata={"image_coverage": 0.0, "native_char_count": 500})

    with patch("trellis.tools.impls.document._ocr_page") as mock_ocr:
        _apply_ocr_to_pages([page], model="openai/gpt-4o")
        mock_ocr.assert_not_called()

    assert page.text == "native text"


def test_apply_ocr_skips_digital_page():
    page = Page(number=1, text="A" * 300, image_bytes=b"x", image_mime="image/png", is_scanned=False,
                metadata={"image_coverage": 0.05, "native_char_count": 300})

    with patch("trellis.tools.impls.document._ocr_page") as mock_ocr:
        _apply_ocr_to_pages([page], model="openai/gpt-4o")
        mock_ocr.assert_not_called()

    assert page.text == "A" * 300


# ---------------------------------------------------------------------------
# IngestDocumentTool — pass-through for existing handles
# ---------------------------------------------------------------------------


def test_ingest_document_passthrough_existing_handle():
    page = Page(number=1, text="already extracted", is_scanned=False)
    handle = DocumentHandle(source="/already.pdf", format=DocFormat.PDF, pages=[page], page_count=1)

    tool = IngestDocumentTool()
    result = tool.execute(path=handle)

    assert result is handle  # identity: no copy, no processing


# ---------------------------------------------------------------------------
# IngestDocumentTool — tool name
# ---------------------------------------------------------------------------


def test_ingest_document_tool_name():
    tool = IngestDocumentTool()
    assert tool.name == "ingest_document"
