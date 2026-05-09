from __future__ import annotations

import uuid

import pytest

from trellis.retrieval.fact_store import (
    FactExtractor,
    StructuredFactStore,
    _detect_unit,
    _parse_numeric,
)
from trellis.retrieval.models import ChunkMetadata, ChunkType, StructuredFact
from trellis.retrieval.plugins import RetrievalRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fact(
    tenant_id: str = "t1",
    document_id: str = "doc1",
    chunk_id: str | None = None,
    field_canonical: str = "is.revenue",
    section: str | None = "income_statement",
    period_label: str | None = "2024",
) -> StructuredFact:
    return StructuredFact(
        fact_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        document_id=document_id,
        chunk_id=chunk_id or str(uuid.uuid4()),
        section=section,
        field_raw="Revenue",
        field_canonical=field_canonical,
        value="$100",
        value_numeric=100.0,
        unit="$",
        period_label=period_label,
        confidence=0.9,
    )


def _make_chunk(
    text: str,
    chunk_type: ChunkType = ChunkType.TABLE,
    section_label: str | None = None,
    period_labels: list[str] | None = None,
    column_labels: list[str] | None = None,
    row_labels: list[str] | None = None,
    chunk_id: str | None = None,
) -> ChunkMetadata:
    return ChunkMetadata(
        chunk_id=chunk_id or str(uuid.uuid4()),
        document_id="doc1",
        tenant_id="t1",
        page=1,
        chunk_type=chunk_type,
        section_label=section_label,
        period_labels=period_labels or [],
        period_ends=[],
        row_labels=row_labels or [],
        column_labels=column_labels or [],
        extra={},
        text=text,
        embedding=None,
    )


def _default_registry() -> RetrievalRegistry:
    return RetrievalRegistry()


# ---------------------------------------------------------------------------
# StructuredFactStore
# ---------------------------------------------------------------------------


class TestStructuredFactStore:
    def test_upsert_then_query_returns_inserted_facts(self):
        store = StructuredFactStore()
        fact = _make_fact()
        store.upsert([fact])
        results = store.query(tenant_id="t1")
        assert any(f.fact_id == fact.fact_id for f in results)

    def test_query_filters_by_tenant_id(self):
        store = StructuredFactStore()
        fact_t1 = _make_fact(tenant_id="t1")
        fact_t2 = _make_fact(tenant_id="t2")
        store.upsert([fact_t1, fact_t2])
        results = store.query(tenant_id="t1")
        assert all(f.tenant_id == "t1" for f in results)

    def test_query_filters_by_document_ids(self):
        store = StructuredFactStore()
        fact_doc1 = _make_fact(document_id="doc1")
        fact_doc2 = _make_fact(document_id="doc2")
        store.upsert([fact_doc1, fact_doc2])
        results = store.query(tenant_id="t1", document_ids=["doc1"])
        assert all(f.document_id == "doc1" for f in results)

    def test_query_filters_by_section(self):
        store = StructuredFactStore()
        fact_is = _make_fact(section="income_statement")
        fact_bs = _make_fact(section="balance_sheet")
        store.upsert([fact_is, fact_bs])
        results = store.query(tenant_id="t1", sections=["income_statement"])
        assert all(f.section == "income_statement" for f in results)

    def test_query_filters_by_field_canonical(self):
        store = StructuredFactStore()
        fact_rev = _make_fact(field_canonical="is.revenue")
        fact_exp = _make_fact(field_canonical="is.expenses")
        store.upsert([fact_rev, fact_exp])
        results = store.query(tenant_id="t1", field_canonical="is.revenue")
        assert all(f.field_canonical == "is.revenue" for f in results)

    def test_query_filters_by_period_labels(self):
        store = StructuredFactStore()
        fact_2024 = _make_fact(period_label="2024")
        fact_2023 = _make_fact(period_label="2023")
        store.upsert([fact_2024, fact_2023])
        results = store.query(tenant_id="t1", period_labels=["2024"])
        assert all(f.period_label == "2024" for f in results)

    def test_upsert_same_key_replaces_not_appends(self):
        store = StructuredFactStore()
        chunk_id = str(uuid.uuid4())
        fact_v1 = _make_fact(chunk_id=chunk_id, field_canonical="is.revenue")
        fact_v1_replace = StructuredFact(
            fact_id=str(uuid.uuid4()),
            tenant_id="t1",
            document_id="doc1",
            chunk_id=chunk_id,
            section="income_statement",
            field_raw="Revenue",
            field_canonical="is.revenue",
            value="$200",
            value_numeric=200.0,
            unit="$",
            period_label="2024",
            confidence=0.95,
        )
        store.upsert([fact_v1])
        store.upsert([fact_v1_replace])
        results = store.query(tenant_id="t1", field_canonical="is.revenue")
        assert len(results) == 1
        assert results[0].value == "$200"

    def test_has_document_returns_true_when_doc_has_facts(self):
        store = StructuredFactStore()
        fact = _make_fact(document_id="doc1")
        store.upsert([fact])
        assert store.has_document("t1", "doc1") is True

    def test_has_document_returns_false_when_no_facts(self):
        store = StructuredFactStore()
        assert store.has_document("t1", "missing_doc") is False

    def test_has_document_is_tenant_scoped(self):
        store = StructuredFactStore()
        fact = _make_fact(tenant_id="t1", document_id="doc1")
        store.upsert([fact])
        assert store.has_document("t2", "doc1") is False


# ---------------------------------------------------------------------------
# FactExtractor
# ---------------------------------------------------------------------------


class TestFactExtractor:
    def test_returns_empty_list_for_non_table_chunk(self):
        chunk = _make_chunk("Some prose text here.", chunk_type=ChunkType.PROSE)
        extractor = FactExtractor()
        facts = extractor.extract([chunk], _default_registry())
        assert facts == []

    def test_returns_empty_list_for_heading_chunk(self):
        chunk = _make_chunk("HEADING", chunk_type=ChunkType.HEADING)
        extractor = FactExtractor()
        facts = extractor.extract([chunk], _default_registry())
        assert facts == []

    def test_extracts_facts_from_pipe_delimited_table(self):
        text = "Label | 2023 | 2024\nRevenue | $100 | $200\nExpenses | $50 | $80"
        chunk = _make_chunk(
            text,
            chunk_type=ChunkType.TABLE,
            column_labels=["2023", "2024"],
            row_labels=["Revenue", "Expenses"],
        )
        extractor = FactExtractor()
        facts = extractor.extract([chunk], _default_registry())
        assert len(facts) > 0

    def test_extracted_fact_has_correct_field_raw(self):
        text = "Label | 2024\nRevenue | $500"
        chunk = _make_chunk(text, chunk_type=ChunkType.TABLE)
        extractor = FactExtractor()
        facts = extractor.extract([chunk], _default_registry())
        assert any(f.field_raw == "Revenue" for f in facts)

    def test_extracted_fact_has_numeric_value(self):
        text = "Label | 2024\nRevenue | $500"
        chunk = _make_chunk(text, chunk_type=ChunkType.TABLE)
        extractor = FactExtractor()
        facts = extractor.extract([chunk], _default_registry())
        revenue_facts = [f for f in facts if f.field_raw == "Revenue"]
        assert any(f.value_numeric == 500.0 for f in revenue_facts)

    def test_skips_na_cells(self):
        text = "Label | 2024\nRevenue | N/A"
        chunk = _make_chunk(text, chunk_type=ChunkType.TABLE)
        extractor = FactExtractor()
        facts = extractor.extract([chunk], _default_registry())
        assert facts == []


# ---------------------------------------------------------------------------
# _parse_numeric
# ---------------------------------------------------------------------------


class TestParseNumeric:
    def test_dollar_amount_with_comma(self):
        assert _parse_numeric("$1,234.56") == pytest.approx(1234.56)

    def test_parenthesized_negative(self):
        assert _parse_numeric("(100)") == -100.0

    def test_na_returns_none(self):
        assert _parse_numeric("N/A") is None

    def test_plain_integer(self):
        assert _parse_numeric("500") == 500.0

    def test_plain_float(self):
        assert _parse_numeric("3.14") == pytest.approx(3.14)

    def test_percentage_stripped(self):
        assert _parse_numeric("25%") == 25.0

    def test_empty_returns_none(self):
        assert _parse_numeric("") is None

    def test_dash_returns_none(self):
        assert _parse_numeric("-") is None


# ---------------------------------------------------------------------------
# _detect_unit
# ---------------------------------------------------------------------------


class TestDetectUnit:
    def test_dollar_prefix_returns_dollar(self):
        assert _detect_unit("$1,234") == "$"

    def test_percent_suffix_returns_percent(self):
        assert _detect_unit("25%") == "%"

    def test_bps_returns_bps(self):
        assert _detect_unit("150bps") == "bps"

    def test_x_suffix_returns_x(self):
        assert _detect_unit("3.5x") == "x"

    def test_plain_number_returns_none(self):
        assert _detect_unit("1000") is None

    def test_na_returns_none(self):
        assert _detect_unit("N/A") is None
