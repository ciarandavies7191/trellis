"""Tests for extract_from_texts and extract_from_tables tools.

These tools assume pages already have clean text (OCR handled by ingest_document).
"""

import json
import pytest

from trellis.tools.impls.extract import (
    ExtractFromTextsTool,
    ExtractFromTablesTool,
    TextExtractionResult,
    TableExtractionResult,
    TableResult,
)
from trellis.models.document import DocumentHandle, Page, DocFormat, PageList


class StubBackend:
    """Test double for LLMBackend."""

    def __init__(self, reply: str = "{}"):
        self.reply = reply
        self.calls: list[dict] = []

    def __call__(self, messages, *, max_tokens, **kwargs) -> str:
        self.calls.append({"messages": messages, "max_tokens": max_tokens, **kwargs})
        return self.reply


# ---------------------------------------------------------------------------
# extract_from_texts
# ---------------------------------------------------------------------------


def _text_handle(text: str, source: str = "/tmp/doc.pdf") -> DocumentHandle:
    page = Page(number=1, text=text, is_scanned=False)
    return DocumentHandle(source=source, format=DocFormat.PDF, pages=[page], page_count=1)


def test_extract_from_texts_returns_structured_dict():
    handle = _text_handle("Invoice total: £2,450.00\nDate: 2024-03-15")
    backend = StubBackend(reply='{"grand_total": "£2,450.00", "invoice_date": "2024-03-15"}')

    tool = ExtractFromTextsTool()
    result = tool.execute(document=handle, prompt="extract the grand total and invoice date", backend=backend)

    assert isinstance(result, TextExtractionResult)
    assert result.extracted == {"grand_total": "£2,450.00", "invoice_date": "2024-03-15"}
    assert result.prompt == "extract the grand total and invoice date"
    assert result.source_pages == [1]
    assert result.sources == ["/tmp/doc.pdf"]


def test_extract_from_texts_invalid_json_wraps_as_raw():
    handle = _text_handle("some text")
    backend = StubBackend(reply="not valid json at all")

    tool = ExtractFromTextsTool()
    result = tool.execute(document=handle, prompt="extract something", backend=backend)

    assert isinstance(result, TextExtractionResult)
    assert "result" in result.extracted
    assert result.extracted["result"] == "not valid json at all"


def test_extract_from_texts_strips_markdown_fencing():
    handle = _text_handle("Mortgage date: 1 April 2024")
    backend = StubBackend(reply='```json\n{"mortgage_date": "2024-04-01"}\n```')

    tool = ExtractFromTextsTool()
    result = tool.execute(document=handle, prompt="extract the mortgage date", backend=backend)

    assert result.extracted == {"mortgage_date": "2024-04-01"}


def test_extract_from_texts_raw_string_input():
    backend = StubBackend(reply='{"value": "42"}')
    tool = ExtractFromTextsTool()
    result = tool.execute(document="The answer is 42", prompt="extract the value", backend=backend)

    assert isinstance(result, TextExtractionResult)
    assert result.sources == ["<inline>"]


def test_extract_from_texts_multiple_handles():
    h1 = _text_handle("Doc A content", "/a.pdf")
    h2 = _text_handle("Doc B content", "/b.pdf")
    backend = StubBackend(reply='{"summary": "combined"}')

    tool = ExtractFromTextsTool()
    result = tool.execute(document=[h1, h2], prompt="extract summary", backend=backend)

    assert result.sources == ["/a.pdf", "/b.pdf"]
    assert len(backend.calls) == 1


def test_extract_from_texts_str_output():
    handle = _text_handle("text")
    backend = StubBackend(reply='{"key": "value"}')
    tool = ExtractFromTextsTool()
    result = tool.execute(document=handle, prompt="extract key", backend=backend)

    assert '"key": "value"' in str(result)


# ---------------------------------------------------------------------------
# extract_from_tables
# ---------------------------------------------------------------------------


def test_extract_from_tables_returns_table_result():
    handle = _text_handle("Revenue | 100\nCosts   | 60\nProfit  | 40")
    backend = StubBackend(
        reply=json.dumps([
            {"headers": ["Item", "Value"], "rows": [{"Item": "Revenue", "Value": "100"}], "source_page": 1, "sheet_name": None}
        ])
    )

    tool = ExtractFromTablesTool()
    result = tool.execute(document=handle, backend=backend)

    assert isinstance(result, TableExtractionResult)
    assert len(result.tables) == 1
    assert result.tables[0].headers == ["Item", "Value"]
    assert result.tables[0].rows == [{"Item": "Revenue", "Value": "100"}]
    assert result.tables[0].source_page == 1
    assert result.source_pages == [1]


def test_extract_from_tables_with_selector():
    handle = _text_handle("income statement data here")
    backend = StubBackend(
        reply=json.dumps([
            {"headers": ["Line", "Amount"], "rows": [], "source_page": 1, "sheet_name": None}
        ])
    )

    tool = ExtractFromTablesTool()
    result = tool.execute(document=handle, selector="income statement", backend=backend)

    assert isinstance(result, TableExtractionResult)
    assert result.tables[0].selector == "income statement"


def test_extract_from_tables_no_tables_found():
    handle = _text_handle("just some narrative text, no tables")
    backend = StubBackend(reply="[]")

    tool = ExtractFromTablesTool()
    result = tool.execute(document=handle, backend=backend)

    assert isinstance(result, TableExtractionResult)
    assert result.tables == []


def test_extract_from_tables_invalid_json_returns_empty():
    handle = _text_handle("text")
    backend = StubBackend(reply="not json")

    tool = ExtractFromTablesTool()
    result = tool.execute(document=handle, backend=backend)

    assert result.tables == []


def test_extract_from_tables_str_output():
    handle = _text_handle("data")
    backend = StubBackend(reply=json.dumps([
        {"headers": ["A"], "rows": [{"A": "1"}], "source_page": 1, "sheet_name": None}
    ]))
    tool = ExtractFromTablesTool()
    result = tool.execute(document=handle, backend=backend)

    output = str(result)
    parsed = json.loads(output)
    assert isinstance(parsed, list)
    assert parsed[0]["headers"] == ["A"]


# ---------------------------------------------------------------------------
# OCR is NOT performed by these tools
# ---------------------------------------------------------------------------


def test_extract_from_texts_does_not_ocr_image_pages():
    """extract_from_texts must not attempt OCR — that is ingest_document's job."""
    page = Page(number=1, text="pre-extracted text", image_bytes=b"some-bytes", image_mime="image/png", is_scanned=True)
    handle = DocumentHandle(source="/img.pdf", format=DocFormat.PDF, pages=[page], page_count=1)

    backend = StubBackend(reply='{"content": "pre-extracted text"}')
    tool = ExtractFromTextsTool()
    result = tool.execute(document=handle, prompt="extract content", backend=backend)

    # Only one LLM call — the extraction call, not an OCR call
    assert len(backend.calls) == 1
    # The extraction uses the text already on the page
    assert result.extracted == {"content": "pre-extracted text"}
