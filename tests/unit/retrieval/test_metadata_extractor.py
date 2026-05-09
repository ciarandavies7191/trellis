from __future__ import annotations

import uuid

import pytest

from trellis.retrieval.metadata import MetadataExtractor, _extract_periods
from trellis.retrieval.models import ChunkMetadata, ChunkType
from trellis.retrieval.plugins import (
    KeywordSectionClassifier,
    NullClassifier,
    RetrievalRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(text: str, chunk_type: ChunkType = ChunkType.PROSE) -> ChunkMetadata:
    return ChunkMetadata(
        chunk_id=str(uuid.uuid4()),
        document_id="doc1",
        tenant_id="tenant1",
        page=1,
        chunk_type=chunk_type,
        section_label=None,
        period_labels=[],
        period_ends=[],
        row_labels=[],
        column_labels=[],
        extra={},
        text=text,
        embedding=None,
    )


def _default_registry() -> RetrievalRegistry:
    return RetrievalRegistry()


# ---------------------------------------------------------------------------
# Period detection (_extract_periods)
# ---------------------------------------------------------------------------


class TestExtractPeriods:
    def test_year_ended_december_detected(self):
        labels, _ = _extract_periods("For the year ended December 31, 2024")
        assert any("December 31, 2024" in l or "december 31, 2024" in l.lower() for l in labels)

    def test_year_ended_produces_iso_date(self):
        _, iso_dates = _extract_periods("For the year ended December 31, 2024")
        assert any("2024" in d for d in iso_dates)

    def test_q3_fy_pattern_detected(self):
        labels, _ = _extract_periods("Results for Q3 FY2024 were strong")
        assert any("Q3" in l and "2024" in l for l in labels)

    def test_no_temporal_pattern_returns_empty(self):
        labels, iso_dates = _extract_periods("Revenue increased due to higher demand.")
        assert labels == []
        assert iso_dates == []

    def test_iso_date_pattern_detected(self):
        labels, _ = _extract_periods("Balance as of 2024-06-30")
        assert "2024-06-30" in labels

    def test_duplicate_periods_not_repeated(self):
        text = "year ended December 31, 2024 and December 31, 2024"
        labels, _ = _extract_periods(text)
        count = sum(1 for l in labels if "December 31, 2024" in l or "december 31, 2024" in l.lower())
        assert count == 1


# ---------------------------------------------------------------------------
# MetadataExtractor
# ---------------------------------------------------------------------------


class TestMetadataExtractor:
    def test_period_labels_populated_from_text(self):
        chunk = _make_chunk("For the year ended December 31, 2024, revenue was $100m")
        extractor = MetadataExtractor()
        extractor.extract([chunk], _default_registry())
        assert any("December 31, 2024" in l or "december 31, 2024" in l.lower() for l in chunk.period_labels)

    def test_period_ends_populated_with_iso_date(self):
        chunk = _make_chunk("For the year ended December 31, 2024, revenue was $100m")
        extractor = MetadataExtractor()
        extractor.extract([chunk], _default_registry())
        assert any("2024" in d for d in chunk.period_ends)

    def test_no_periods_when_no_temporal_text(self):
        chunk = _make_chunk("Revenue increased due to market growth.")
        extractor = MetadataExtractor()
        extractor.extract([chunk], _default_registry())
        assert chunk.period_labels == []
        assert chunk.period_ends == []

    def test_null_classifier_leaves_section_label_none(self):
        chunk = _make_chunk("Revenue increased by 10%")
        registry = RetrievalRegistry()
        registry.register_classifier(NullClassifier())
        extractor = MetadataExtractor()
        extractor.extract([chunk], registry)
        assert chunk.section_label is None

    def test_section_label_set_from_classifier(self):
        chunk = _make_chunk("Total revenue and net income for the period")
        registry = RetrievalRegistry()
        registry.register_classifier(
            KeywordSectionClassifier({"income_statement": ["revenue", "net income"]})
        )
        extractor = MetadataExtractor()
        extractor.extract([chunk], registry)
        assert chunk.section_label == "income_statement"

    def test_mutates_chunks_in_place(self):
        chunk = _make_chunk("For the year ended December 31, 2024")
        chunk_ref = chunk
        extractor = MetadataExtractor()
        extractor.extract([chunk], _default_registry())
        assert chunk is chunk_ref
        assert chunk.period_labels != []

    def test_multiple_chunks_all_processed(self):
        chunks = [
            _make_chunk("For the year ended December 31, 2024"),
            _make_chunk("For the year ended December 31, 2023"),
        ]
        extractor = MetadataExtractor()
        extractor.extract(chunks, _default_registry())
        assert chunks[0].period_labels != []
        assert chunks[1].period_labels != []

    def test_column_labels_included_in_period_detection(self):
        chunk = _make_chunk("Revenue data")
        chunk.column_labels = ["year ended December 31, 2024"]
        extractor = MetadataExtractor()
        extractor.extract([chunk], _default_registry())
        assert chunk.period_labels != []
