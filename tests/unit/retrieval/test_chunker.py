from __future__ import annotations

import pytest

from trellis.models.document import DocFormat, DocumentHandle, Page
from trellis.retrieval.chunker import StructuralChunker
from trellis.retrieval.models import ChunkType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(text: str, number: int = 1, sheet_name: str | None = None) -> Page:
    return Page(number=number, text=text, is_scanned=False, sheet_name=sheet_name)


def _make_handle(pages: list[Page]) -> DocumentHandle:
    return DocumentHandle(
        source="test.pdf",
        format=DocFormat.PDF,
        pages=pages,
        page_count=len(pages),
    )


def _chunk(pages, **kwargs):
    chunker = StructuralChunker()
    handle = _make_handle(pages) if isinstance(pages, list) else pages
    return chunker.chunk(handle, document_id="doc1", tenant_id="tenant1")


# ---------------------------------------------------------------------------
# Prose
# ---------------------------------------------------------------------------


class TestProseChunking:
    def test_prose_page_produces_prose_chunk(self):
        page = _make_page("This is a normal prose paragraph with some content here.")
        chunks = _chunk([page])
        assert any(c.chunk_type == ChunkType.PROSE for c in chunks)

    def test_prose_chunk_has_correct_text(self):
        text = "This is a normal prose paragraph."
        page = _make_page(text)
        chunks = _chunk([page])
        prose = [c for c in chunks if c.chunk_type == ChunkType.PROSE]
        assert any(text in c.text for c in prose)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


class TestTableChunking:
    def test_pipe_table_produces_table_chunk(self):
        text = "Label | 2023 | 2024\nRevenue | 100 | 200\nExpenses | 50 | 80"
        page = _make_page(text)
        chunks = _chunk([page])
        assert any(c.chunk_type == ChunkType.TABLE for c in chunks)

    def test_pipe_table_extracts_column_labels(self):
        text = "Label | 2023 | 2024\nRevenue | 100 | 200"
        page = _make_page(text)
        chunks = _chunk([page])
        tables = [c for c in chunks if c.chunk_type == ChunkType.TABLE]
        assert tables
        col_labels = tables[0].column_labels
        assert "2023" in col_labels
        assert "2024" in col_labels

    def test_xlsx_page_produces_single_table_chunk(self):
        text = "Item | Value\nCash | 1000"
        page = _make_page(text, sheet_name="Sheet1")
        chunks = _chunk([page])
        assert len(chunks) == 1
        assert chunks[0].chunk_type == ChunkType.TABLE

    def test_xlsx_chunk_has_sheet_name_in_extra(self):
        text = "Item | Value\nCash | 1000"
        page = _make_page(text, sheet_name="Sheet1")
        chunks = _chunk([page])
        assert chunks[0].extra.get("sheet_name") == "Sheet1"


# ---------------------------------------------------------------------------
# Heading
# ---------------------------------------------------------------------------


class TestHeadingChunking:
    def test_all_caps_short_line_produces_heading_chunk(self):
        text = "FINANCIAL HIGHLIGHTS\n\nSome prose follows here with content."
        page = _make_page(text)
        chunks = _chunk([page])
        assert any(c.chunk_type == ChunkType.HEADING for c in chunks)

    def test_heading_text_matches_line(self):
        text = "REVENUE SUMMARY\n\nProse content here."
        page = _make_page(text)
        chunks = _chunk([page])
        headings = [c for c in chunks if c.chunk_type == ChunkType.HEADING]
        assert any("REVENUE SUMMARY" in c.text for c in headings)


# ---------------------------------------------------------------------------
# Footnote
# ---------------------------------------------------------------------------


class TestFootnoteChunking:
    def test_asterisk_footnote_line_produces_footnote_chunk(self):
        text = "* This is a footnote explaining something."
        page = _make_page(text)
        chunks = _chunk([page])
        assert any(c.chunk_type == ChunkType.FOOTNOTE for c in chunks)

    def test_bracketed_number_footnote_produces_footnote_chunk(self):
        text = "[1] Note disclosures follow."
        page = _make_page(text)
        chunks = _chunk([page])
        assert any(c.chunk_type == ChunkType.FOOTNOTE for c in chunks)


# ---------------------------------------------------------------------------
# Long prose splitting
# ---------------------------------------------------------------------------


class TestLongProseSplitting:
    def test_long_prose_paragraph_split_into_multiple_chunks(self):
        # Generate text that exceeds 512 approx-tokens (words * 4/3)
        # ~512 tokens means ~384 words
        sentence = "The company reported strong results this quarter driven by revenue growth. "
        long_text = sentence * 50
        page = _make_page(long_text)
        chunks = _chunk([page])
        prose_chunks = [c for c in chunks if c.chunk_type == ChunkType.PROSE]
        assert len(prose_chunks) > 1


# ---------------------------------------------------------------------------
# Mixed page
# ---------------------------------------------------------------------------


class TestMixedPage:
    def test_mixed_page_produces_multiple_chunk_types(self):
        text = (
            "INCOME STATEMENT\n"
            "\n"
            "Revenue increased this year due to strong demand.\n"
            "\n"
            "Label | 2023 | 2024\n"
            "Revenue | 100 | 200\n"
            "\n"
            "* Amounts in thousands."
        )
        page = _make_page(text)
        chunks = _chunk([page])
        types = {c.chunk_type for c in chunks}
        assert len(types) > 1


# ---------------------------------------------------------------------------
# chunk_id uniqueness
# ---------------------------------------------------------------------------


class TestChunkIdUniqueness:
    def test_chunk_ids_are_unique(self):
        text = (
            "HEADING ONE\n"
            "\n"
            "First prose paragraph here.\n"
            "\n"
            "HEADING TWO\n"
            "\n"
            "Second prose paragraph here.\n"
        )
        page = _make_page(text)
        chunks = _chunk([page])
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# section_label and period_labels defaults
# ---------------------------------------------------------------------------


class TestChunkDefaults:
    def test_section_label_is_none_after_chunking(self):
        page = _make_page("Some prose text for testing purposes.")
        chunks = _chunk([page])
        assert all(c.section_label is None for c in chunks)

    def test_period_labels_is_empty_after_chunking(self):
        page = _make_page("Some prose text for testing purposes.")
        chunks = _chunk([page])
        assert all(c.period_labels == [] for c in chunks)
