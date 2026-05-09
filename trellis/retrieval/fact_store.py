from __future__ import annotations
import uuid
import re
from typing import Optional, Any
from .models import ChunkMetadata, StructuredFact, ChunkType
from .plugins import FieldTaxonomy, RetrievalRegistry


class StructuredFactStore:
    """In-memory fact store keyed by (tenant_id, document_id, section, field_canonical)."""

    def __init__(self):
        self._facts: list[StructuredFact] = []

    def upsert(self, facts: list[StructuredFact]) -> None:
        existing_keys = {
            (f.tenant_id, f.document_id, f.chunk_id, f.field_canonical): i
            for i, f in enumerate(self._facts)
        }
        for fact in facts:
            key = (fact.tenant_id, fact.document_id, fact.chunk_id, fact.field_canonical)
            if key in existing_keys:
                self._facts[existing_keys[key]] = fact
            else:
                self._facts.append(fact)
                existing_keys[key] = len(self._facts) - 1

    def query(
        self,
        tenant_id: str,
        document_ids: Optional[list[str]] = None,
        sections: Optional[list[str]] = None,
        field_canonical: Optional[str] = None,
        period_labels: Optional[list[str]] = None,
    ) -> list[StructuredFact]:
        results = [f for f in self._facts if f.tenant_id == tenant_id]
        if document_ids is not None:
            results = [f for f in results if f.document_id in document_ids]
        if sections is not None:
            results = [f for f in results if f.section in sections]
        if field_canonical is not None:
            results = [f for f in results if f.field_canonical == field_canonical]
        if period_labels is not None:
            wanted = set(period_labels)
            results = [f for f in results if f.period_label in wanted]
        return results

    def has_document(self, tenant_id: str, document_id: str) -> bool:
        return any(f.tenant_id == tenant_id and f.document_id == document_id for f in self._facts)


def _parse_numeric(value: str) -> Optional[float]:
    cleaned = re.sub(r"[,$%\s]", "", value.strip())
    cleaned = cleaned.replace("(", "-").rstrip(")")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _detect_unit(value: str) -> Optional[str]:
    v = value.strip()
    if v.startswith("$"):
        return "$"
    if v.endswith("%"):
        return "%"
    if "bps" in v.lower():
        return "bps"
    if v.endswith("x"):
        return "x"
    return None


class FactExtractor:
    """Extracts StructuredFact records from TABLE chunks using the registered FieldTaxonomy."""

    def extract(
        self,
        chunks: list[ChunkMetadata],
        registry: RetrievalRegistry,
    ) -> list[StructuredFact]:
        facts = []
        taxonomy = registry.taxonomy
        for chunk in chunks:
            if chunk.chunk_type != ChunkType.TABLE:
                continue
            facts.extend(_extract_from_table_chunk(chunk, taxonomy))
        return facts


def _extract_from_table_chunk(chunk: ChunkMetadata, taxonomy: FieldTaxonomy) -> list[StructuredFact]:
    """Parse table text into StructuredFact records.

    Handles pipe-delimited tables (| col | col |) and space-aligned tables.
    Emits one fact per row-label x column-label cell.
    """
    facts = []
    lines = [l.strip() for l in chunk.text.splitlines() if l.strip()]

    if not lines:
        return facts

    pipe_lines = [l for l in lines if "|" in l]
    use_pipe = len(pipe_lines) / len(lines) >= 0.5

    if use_pipe:
        rows = []
        for line in lines:
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
    else:
        rows = []
        for line in lines:
            parts = re.split(r"\s{2,}", line.strip())
            if parts:
                rows.append(parts)

    if len(rows) < 2:
        return facts

    header_row = rows[0]
    col_labels = header_row[1:] if len(header_row) > 1 else header_row

    for row in rows[1:]:
        if not row:
            continue
        row_label = row[0].strip()
        if not row_label:
            continue
        for col_idx, cell_value in enumerate(row[1:]):
            if col_idx >= len(col_labels):
                break
            col_label = col_labels[col_idx].strip()
            if not cell_value.strip() or cell_value.strip() in ("-", "—", "N/A", "n/a", ""):
                continue
            canonical = taxonomy.canonicalize(row_label, chunk.section_label)
            period_label = chunk.period_labels[col_idx] if col_idx < len(chunk.period_labels) else None
            numeric_val = _parse_numeric(cell_value)
            unit = _detect_unit(cell_value)
            facts.append(StructuredFact(
                fact_id=str(uuid.uuid4()),
                tenant_id=chunk.tenant_id,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                section=chunk.section_label,
                field_raw=row_label,
                field_canonical=canonical,
                value=cell_value.strip(),
                value_numeric=numeric_val,
                unit=unit,
                period_label=period_label,
                confidence=0.8,
                extra={"column_label": col_label},
            ))
    return facts
