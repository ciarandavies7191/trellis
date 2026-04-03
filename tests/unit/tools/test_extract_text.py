import types

import pytest

from trellis.tools.impls.extract import ExtractTextTool, ExtractTextResult
from trellis.models.document import DocumentHandle, Page, DocFormat, PageList


class StubBackend:
    def __init__(self, reply: str = "", no_match: bool = False):
        self.reply = reply
        self.no_match = no_match
        self.calls: list[dict] = []

    def __call__(self, messages, *, max_tokens, **kwargs) -> str:  # noqa: D401
        # Record call for assertions
        self.calls.append({"messages": messages, "max_tokens": max_tokens, **kwargs})
        if self.no_match:
            return "[NO MATCH]"
        return self.reply or "OCR_RESULT"


@pytest.mark.parametrize("text", ["Hello world", "", "Some text across pages"])
def test_extract_from_raw_string_no_selector(text):
    tool = ExtractTextTool()
    result = tool.execute(document=text)  # type: ignore[call-arg]
    assert isinstance(result, ExtractTextResult)
    assert str(result) == (text or "")
    assert result.ocr_page_count == 0


def test_ocr_path_uses_backend_image_message():
    # Page with no selectable text but with image bytes → triggers OCR
    page = Page(number=1, text="", image_bytes=b"fake-bytes", image_mime="image/png", is_scanned=True)
    handle = DocumentHandle(source="/tmp/img.png", format=DocFormat.IMAGE, pages=[page], page_count=1)

    backend = StubBackend(reply="Transcribed text")
    tool = ExtractTextTool()
    result = tool.execute(document=handle, backend=backend)  # type: ignore[call-arg]

    assert isinstance(result, ExtractTextResult)
    assert "Transcribed text" in result.text
    assert result.ocr_page_count == 1
    # Ensure multimodal message with image_url was sent
    assert any(isinstance(c.get("messages"), list) for c in backend.calls)
    mm = backend.calls[0]["messages"][1]["content"][0]
    assert mm["type"] == "image_url"
    assert mm["image_url"]["url"].startswith("data:image/")


def test_selector_applied_via_backend():
    # Build a simple handle with text content
    page = Page(number=1, text="alpha\n\nnotes section here\n\nomega", is_scanned=False)
    handle = DocumentHandle(source="/tmp/report.txt", format=DocFormat.TEXT, pages=[page], page_count=1)

    backend = StubBackend(reply="notes section here")
    tool = ExtractTextTool()
    result = tool.execute(document=handle, selector="notes", backend=backend)  # type: ignore[call-arg]

    assert isinstance(result, ExtractTextResult)
    assert result.text.strip() == "notes section here"


def test_selector_no_match_falls_back_full_text():
    page = Page(number=1, text="alpha beta gamma", is_scanned=False)
    handle = DocumentHandle(source="/tmp/a.txt", format=DocFormat.TEXT, pages=[page], page_count=1)

    backend = StubBackend(no_match=True)
    tool = ExtractTextTool()
    result = tool.execute(document=handle, selector="delta", backend=backend)  # type: ignore[call-arg]

    assert isinstance(result, ExtractTextResult)
    assert result.text.strip() == "alpha beta gamma"


def test_multiple_handles_emit_sources_and_sections():
    p1 = Page(number=1, text="one", is_scanned=False)
    p2 = Page(number=1, text="two", is_scanned=False)
    h1 = DocumentHandle(source="/a.pdf", format=DocFormat.PDF, pages=[p1], page_count=1)
    h2 = DocumentHandle(source="/b.pdf", format=DocFormat.PDF, pages=[p2], page_count=1)

    tool = ExtractTextTool()
    result = tool.execute(document=[h1, h2])  # type: ignore[arg-type]

    assert isinstance(result, ExtractTextResult)
    assert result.sources == ["/a.pdf", "/b.pdf"]
    assert "one" in result.text and "two" in result.text

