from __future__ import annotations

import uuid

import pytest

from trellis.retrieval.bm25_index import DocumentBM25Index
from trellis.retrieval.models import ChunkFilter, ChunkMetadata, ChunkType
from trellis.retrieval.plugins import WhitespaceSynonymVocabulary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    text: str,
    chunk_id: str | None = None,
    chunk_type: ChunkType = ChunkType.PROSE,
    section_label: str | None = None,
    document_id: str = "doc1",
) -> ChunkMetadata:
    return ChunkMetadata(
        chunk_id=chunk_id or str(uuid.uuid4()),
        document_id=document_id,
        tenant_id="tenant1",
        page=1,
        chunk_type=chunk_type,
        section_label=section_label,
        period_labels=[],
        period_ends=[],
        row_labels=[],
        column_labels=[],
        extra={},
        text=text,
        embedding=None,
    )


def _make_index(chunks: list[ChunkMetadata]) -> DocumentBM25Index:
    return DocumentBM25Index(chunks, WhitespaceSynonymVocabulary())


# ---------------------------------------------------------------------------
# Basic search
# ---------------------------------------------------------------------------


class TestDocumentBM25IndexSearch:
    def test_search_returns_chunk_ids_in_descending_score_order(self):
        chunks = [
            _make_chunk("revenue profit income earnings", chunk_id="c1"),
            _make_chunk("cat sat mat hat", chunk_id="c2"),
            _make_chunk("earnings per share revenue", chunk_id="c3"),
        ]
        index = _make_index(chunks)
        results = index.search("revenue earnings")
        ids = [r[0] for r in results]
        scores = [r[1] for r in results]
        assert scores == sorted(scores, reverse=True)
        assert "c1" in ids or "c3" in ids

    def test_exact_match_query_returns_chunk_near_top(self):
        target_id = "target"
        chunks = [
            _make_chunk("completely unrelated text about animals", chunk_id="c1"),
            _make_chunk("operating cash flow from operations", chunk_id=target_id),
            _make_chunk("irrelevant noise words here", chunk_id="c2"),
        ]
        index = _make_index(chunks)
        results = index.search("operating cash flow")
        top_id = results[0][0]
        assert top_id == target_id

    def test_search_returns_at_most_k_results(self):
        chunks = [_make_chunk(f"word{i} text content", chunk_id=f"c{i}") for i in range(10)]
        index = _make_index(chunks)
        results = index.search("word text", k=3)
        assert len(results) <= 3

    def test_empty_query_returns_scores(self):
        chunks = [_make_chunk("some text here", chunk_id="c1")]
        index = _make_index(chunks)
        results = index.search("")
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestDocumentBM25IndexFiltering:
    def test_section_labels_filter_excludes_non_matching_chunks(self):
        chunks = [
            _make_chunk("revenue data", chunk_id="c1", section_label="income_statement"),
            _make_chunk("revenue data", chunk_id="c2", section_label="balance_sheet"),
        ]
        index = _make_index(chunks)
        filt = ChunkFilter(section_labels=["income_statement"])
        results = index.search("revenue", filters=filt)
        ids = [r[0] for r in results]
        assert "c1" in ids
        assert "c2" not in ids

    def test_chunk_types_filter_excludes_non_matching_types(self):
        chunks = [
            _make_chunk("revenue table data", chunk_id="c1", chunk_type=ChunkType.TABLE),
            _make_chunk("revenue prose text", chunk_id="c2", chunk_type=ChunkType.PROSE),
        ]
        index = _make_index(chunks)
        filt = ChunkFilter(chunk_types=[ChunkType.TABLE])
        results = index.search("revenue", filters=filt)
        ids = [r[0] for r in results]
        assert "c1" in ids
        assert "c2" not in ids

    def test_document_ids_filter_excludes_other_docs(self):
        chunks = [
            _make_chunk("revenue data", chunk_id="c1", document_id="doc1"),
            _make_chunk("revenue data", chunk_id="c2", document_id="doc2"),
        ]
        index = _make_index(chunks)
        filt = ChunkFilter(document_ids=["doc1"])
        results = index.search("revenue", filters=filt)
        ids = [r[0] for r in results]
        assert "c1" in ids
        assert "c2" not in ids

    def test_no_filter_returns_all_chunks(self):
        chunks = [
            _make_chunk("revenue data", chunk_id="c1"),
            _make_chunk("revenue data", chunk_id="c2"),
        ]
        index = _make_index(chunks)
        results = index.search("revenue")
        ids = [r[0] for r in results]
        assert "c1" in ids
        assert "c2" in ids
