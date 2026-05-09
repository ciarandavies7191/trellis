from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ChunkType(str, Enum):
    TABLE = "table"
    PROSE = "prose"
    HEADING = "heading"
    FOOTNOTE = "footnote"
    CAPTION = "caption"


@dataclass
class ChunkMetadata:
    chunk_id: str
    document_id: str
    tenant_id: str
    page: int
    chunk_type: ChunkType
    section_label: Optional[str]
    period_labels: list[str]
    period_ends: list[str]
    row_labels: list[str]
    column_labels: list[str]
    extra: dict[str, Any]
    text: str
    embedding: Optional[list[float]]


@dataclass
class StructuredFact:
    fact_id: str
    tenant_id: str
    document_id: str
    chunk_id: str
    section: Optional[str]
    field_raw: str
    field_canonical: str
    value: str
    value_numeric: Optional[float]
    unit: Optional[str]
    period_label: Optional[str]
    confidence: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkFilter:
    document_ids: Optional[list[str]] = None
    chunk_types: Optional[list[ChunkType]] = None
    section_labels: Optional[list[str]] = None
    period_labels: Optional[list[str]] = None
    extra_filters: Optional[dict[str, Any]] = None


@dataclass
class ScoredChunk:
    chunk: ChunkMetadata
    score: float
    retrieval_method: str


@dataclass
class RetrievalResult:
    facts: list[StructuredFact]
    chunks: list[ScoredChunk]
    tier_used: str
    warnings: list[str]
    query_periods: list[str]


@dataclass
class ChunkPipelineState:
    document_id: str
    tenant_id: str
    chunking_complete: bool = False
    metadata_complete: bool = False
    bm25_complete: bool = False
    fact_extraction_complete: bool = False
    embedding_complete: bool = False
    error: Optional[str] = None
