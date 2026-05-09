# Trellis Retrieval Architecture — Technical Specification

**Spec ID:** TRELLIS-RETRIEVAL-v1.1  
**DSL Version:** Pipeline DSL v1.4  
**Status:** Draft — Implementation Ready  
**Audience:** IDE coding agents, contributors  
**Depends on:** `ARCHITECTURE.md`, `DESIGN.md`, `PIPELINE-DSL.md`

---

## 1. Motivation

The current `select` tool operates at page granularity using an LLM-driven inventory
scan. This is adequate for broad-scope goals ("Risk factors", "Revenue overview") but
fails systematically for fine-grained extraction and multi-document comparison workloads.

Trellis is a generalist framework. The retrieval problem appears across every
finance-adjacent document workflow:

| Workload | Document types | Retrieval challenge |
|---|---|---|
| SEC filing analysis | 10-K, 10-Q | Pin-point table cell extraction across N filings, period alignment |
| Financial spreading | Income statements, balance sheets | Exact field extraction against a known schema |
| Mortgage processing | Applications, appraisals, income verifications | Cross-document fact reconciliation across a loan package |
| Credit underwriting | Credit reports, bank statements, tax returns | Specific field extraction from heterogeneous formats |
| Compliance review | Policy documents, audit reports, regulatory filings | Clause-level retrieval across large document sets |
| MRM (Model Risk) | Validation reports, model documentation | Section-level retrieval across technical documents |

The retrieval architecture in this spec is **domain-agnostic**. It provides a
general-purpose chunking, indexing, and retrieval stack. Domain-specific knowledge
(section classifiers, synonym vocabularies, canonical field taxonomies) is injected
as configuration at deploy time, not hardcoded into the framework.

---

## 2. Problem Taxonomy

| Failure Mode | Description | Wrong Fix | Right Fix |
|---|---|---|---|
| **Granularity mismatch** | Page-level chunks are too coarse for pin-point extraction | Better LLM prompting | Sub-page structural chunking |
| **Vocabulary brittleness** | Same concept, different label across documents or document types | Larger embedding model | Domain synonym expansion (injected at deploy time) |
| **Structural sparsity** | Table chunk `"2,847 | 3,012 | 2,654"` has near-zero semantic content without row/column context | None — embedding models cannot fix this | Metadata-enriched chunks carrying row/column labels |
| **Multi-document misalignment** | Same field extracted inconsistently across a document set | Post-hoc deduplication | Canonical field taxonomy (domain-specific, injected) |
| **Context dilution** | Irrelevant sections flood extraction context | Larger context window | Metadata pre-filter before retrieval |

---

## 3. Architecture Overview

The retrieval architecture introduces two parallel lookup tiers that compose within
the existing `select` tool interface. The DSL author sees no new complexity. The
runtime selects the appropriate execution path based on query parameters.

```
ingest_document (existing, unchanged interface)
    └──► ChunkPipeline  [NEW — runs at ingest time, async-capable]
              ├──► StructuralChunker     → ChunkList
              ├──► MetadataExtractor     → ChunkMetadata[]       (uses injected SectionClassifier)
              ├──► BM25Indexer           → DocumentBM25Index     (uses injected SynonymVocabulary)
              ├──► EmbeddingIndexer      → DocumentVectorIndex   (async, eventually consistent)
              └──► FactExtractor         → StructuredFactStore   (uses injected FieldTaxonomy)

select (extended — new execution paths, existing page-mode interface preserved)
    ├──► granularity: page  → existing LLM inventory scan (unchanged)
    └──► granularity: chunk → RetrievalOrchestrator
              ├──► 1. Metadata pre-filter  (ChunkFilter applied before any search)
              ├──► 2. Tier 1: StructuredFactStore exact lookup
              │         ├── HIT  → return facts + chunk back-references for context
              │         └── MISS → fall through
              ├──► 3. Tier 2: Hybrid BM25 + vector search → optional CrossEncoder re-rank
              └──► 4. Optional: Tier 1 vs Tier 2 cross-validation (confidence / warning)
```

### Design Principle

The retrieval stack is a **general engine**. Everything domain-specific is a plugin:

| Plugin | Interface | Example: SEC filings | Example: Mortgage |
|---|---|---|---|
| `SectionClassifier` | Maps chunk text → section label | `income_statement`, `balance_sheet` | `borrower_income`, `property_appraisal` |
| `SynonymVocabulary` | Maps canonical term → surface variants | `revenue → ["net revenues", "total net sales"]` | `annual_income → ["gross income", "yearly earnings"]` |
| `FieldTaxonomy` | Maps raw label → canonical field key | `"Net revenues" → "revenue.total"` | `"Gross monthly income" → "income.gross_monthly"` |

None of these plugins are part of the core framework. They are registered at deploy time
by the operator, exactly as `FunctionRegistry` and `SchemaRegistry` are today.

---

## 4. Data Models

All new models live in `trellis/retrieval/models.py`.

### 4.1 `ChunkType`

```python
from enum import Enum

class ChunkType(str, Enum):
    TABLE = "table"
    PROSE = "prose"
    HEADING = "heading"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
```

### 4.2 `ChunkMetadata`

Extracted once at ingest time. Stored in parallel to index vectors. Section labels
and field labels are populated by injected plugins — the framework stores whatever
strings those plugins emit without interpreting them.

```python
from dataclasses import dataclass, field
from typing import Optional, Any

@dataclass
class ChunkMetadata:
    chunk_id: str                       # stable uuid
    document_id: str                    # back-ref to source DocumentHandle
    tenant_id: str
    page: int                           # 1-based source page
    chunk_type: ChunkType
    section_label: Optional[str]        # domain-specific; populated by SectionClassifier
                                        # e.g. "income_statement", "borrower_income",
                                        # "model_validation_summary" — framework is agnostic
    period_labels: list[str]            # normalized period strings if detected; empty otherwise
    period_ends: list[str]              # ISO dates if detected; empty otherwise
    row_labels: list[str]               # table row headers (empty for non-TABLE chunks)
    column_labels: list[str]            # table column headers (empty for non-TABLE chunks)
    extra: dict[str, Any]               # plugin-populated free-form metadata
    text: str                           # raw chunk text (for BM25 and context delivery)
    embedding: Optional[list[float]]    # populated async by EmbeddingIndexer
```

`section_label` uses a flat string rather than an enum so that domain plugins can
define their own label vocabularies without modifying the framework.

`extra` is a schema-free extension point for domain-specific metadata that does not
fit the standard fields. Examples: `{"loan_id": "...", "applicant": "..."}` for
mortgage processing, `{"model_id": "...", "validation_stage": "..."}` for MRM.

### 4.3 `StructuredFact`

The Tier 1 lookup record. Domain-agnostic: `section` and `field_canonical` are
populated by the operator's `FieldTaxonomy` plugin and are opaque strings to the
framework. The framework stores, indexes, and retrieves them without interpreting
what they mean.

```python
@dataclass
class StructuredFact:
    fact_id: str
    tenant_id: str
    document_id: str
    chunk_id: str                       # back-ref to source ChunkMetadata
    section: Optional[str]             # from SectionClassifier plugin
    field_raw: str                      # label exactly as it appears in the document
    field_canonical: str                # normalized via FieldTaxonomy plugin
    value: str                          # raw string; callers parse as needed
    value_numeric: Optional[float]      # parsed if value is numeric
    unit: Optional[str]                 # "$", "months", "bps", "%" etc. if detected
    period_label: Optional[str]         # from ChunkMetadata.period_labels if applicable
    confidence: float                   # extraction confidence [0.0, 1.0]
    extra: dict[str, Any]               # plugin-populated free-form provenance
```

### 4.4 `ChunkFilter`

Query-time pre-filter applied before any index search. Field names match
`ChunkMetadata`. `section_labels` accepts whatever label vocabulary the operator's
`SectionClassifier` emits.

```python
@dataclass
class ChunkFilter:
    document_ids: Optional[list[str]] = None
    chunk_types: Optional[list[ChunkType]] = None
    section_labels: Optional[list[str]] = None   # domain-defined; framework is agnostic
    period_labels: Optional[list[str]] = None
    extra_filters: Optional[dict[str, Any]] = None  # matched against ChunkMetadata.extra
```

### 4.5 `ScoredChunk`

```python
@dataclass
class ScoredChunk:
    chunk: ChunkMetadata
    score: float
    retrieval_method: str    # "bm25" | "dense" | "hybrid" | "rerank"
```

### 4.6 `RetrievalResult`

Unified output from `RetrievalOrchestrator`. Returned by `select` in chunk mode.

```python
@dataclass
class RetrievalResult:
    facts: list[StructuredFact]      # Tier 1 hits (may be empty)
    chunks: list[ScoredChunk]        # Tier 2 hits (may be empty)
    tier_used: str                   # "tier1" | "tier2" | "hybrid"
    warnings: list[str]              # low-confidence flags, index-not-ready notices
    query_periods: list[str]         # period labels used for filtering (if any)
```

---

## 5. Plugin Interfaces

Plugins are registered with `RetrievalRegistry` at deploy time. All three are
optional — the framework degrades gracefully when they are absent.

### 5.1 `SectionClassifier`

Maps a chunk (text + structural signals) to a domain-specific section label.

```python
from abc import ABC, abstractmethod

class SectionClassifier(ABC):
    @abstractmethod
    def classify(self, chunk: ChunkMetadata) -> Optional[str]:
        """
        Return a domain-specific section label, or None if the chunk
        does not belong to a known section type.

        Examples of returned strings (operator-defined):
          SEC filings:   "income_statement", "balance_sheet", "risk_factors"
          Mortgage:      "borrower_income", "property_appraisal", "credit_history"
          MRM:           "model_description", "validation_findings", "limitations"
          Compliance:    "policy_obligation", "control_evidence", "gap_assessment"
        """
```

A `KeywordSectionClassifier` default implementation is provided. It accepts a dict
mapping label → trigger keywords and classifies by keyword density:

```python
classifier = KeywordSectionClassifier(rules={
    "income_statement": ["revenue", "gross profit", "operating income", "net income"],
    "balance_sheet": ["total assets", "total liabilities", "shareholders equity"],
})
```

Operators can replace this with a fine-tuned classifier, an LLM call, or any other
implementation behind the interface.

### 5.2 `SynonymVocabulary`

Maps canonical terms to the surface-form variants expected in target documents.
Applied at both BM25 index time and query time.

```python
class SynonymVocabulary(ABC):
    @abstractmethod
    def expand(self, term: str) -> list[str]:
        """
        Return surface variants for a canonical term including the term itself.
        Returns [term] if no expansions are known.
        """

    @abstractmethod
    def tokenize(self, text: str) -> list[str]:
        """
        Tokenize text with synonym expansion applied.
        Used to build the BM25 index and to tokenize queries.
        """
```

A `DictSynonymVocabulary` default implementation accepts a plain dict:

```python
vocab = DictSynonymVocabulary({
    "revenue": ["revenues", "net revenues", "total revenues", "net sales"],
    "net_income": ["net earnings", "profit for the period", "net profit"],
})
```

### 5.3 `FieldTaxonomy`

Maps raw field labels as they appear in documents to canonical field keys.
Used by `FactExtractor` to populate `StructuredFact.field_canonical`.

```python
class FieldTaxonomy(ABC):
    @abstractmethod
    def canonicalize(self, raw_label: str, section: Optional[str] = None) -> str:
        """
        Return a canonical field key for a raw label string.
        `section` hint may be used to disambiguate (e.g. "assets.total" vs
        "liabilities.total" when the raw label is just "Total").

        Returns "unknown." + slugify(raw_label) if no mapping exists.
        """
```

A `DictFieldTaxonomy` default accepts a dict keyed by `(section, raw_label)`:

```python
taxonomy = DictFieldTaxonomy({
    ("income_statement", "net revenues"): "revenue.total",
    ("income_statement", "total net sales"): "revenue.total",
    ("borrower_income", "gross monthly income"): "income.gross_monthly",
    ("borrower_income", "base salary"): "income.base_salary",
})
```

### 5.4 `RetrievalRegistry`

Holds registered plugins and makes them available to the retrieval stack.

```python
class RetrievalRegistry:
    def register_classifier(self, classifier: SectionClassifier) -> None: ...
    def register_vocabulary(self, vocabulary: SynonymVocabulary) -> None: ...
    def register_taxonomy(self, taxonomy: FieldTaxonomy) -> None: ...

    @property
    def classifier(self) -> SectionClassifier: ...   # falls back to NullClassifier
    @property
    def vocabulary(self) -> SynonymVocabulary: ...   # falls back to WhitespaceSynonymVocabulary
    @property
    def taxonomy(self) -> FieldTaxonomy: ...         # falls back to SlugifyTaxonomy
```

Null/fallback implementations ensure the retrieval stack functions without any
registered plugins — it produces less precise section labels and canonical keys
but does not fail.

---

## 6. Component Specifications

### 6.1 `StructuralChunker`

**Location:** `trellis/retrieval/chunker.py`  
**Replaces:** naive fixed-size character chunking in `ingest_document`'s HTML-to-pages path  
**Trigger:** called by `ChunkPipeline` after `ingest_document` resolves all page text

The chunker partitions a fully-resolved `DocumentHandle` into atomic semantic units.
It is document-format-aware but domain-agnostic — it produces structural boundaries
without interpreting what the sections mean. Interpretation is delegated entirely to
the `SectionClassifier` plugin in the subsequent `MetadataExtractor` step.

**PDF chunking hierarchy:**

```
Level 1 — Outline tree (PyMuPDF ToC)
    Level 2 — Font size statistics (section boundary detection)
        Level 3 — Spatial position (column break, header/footer region)
            Level 4 — Content patterns (table start/end, footnote markers)
```

**Rules:**

- Tables are atomic. A table spanning multiple pages is one chunk. Never split mid-row.
- Section headings are emitted as standalone `HEADING` chunks to preserve
  heading→body associations in downstream retrieval.
- Footnotes are separated from body prose and tagged `FOOTNOTE`.
- Prose paragraphs within a section are grouped into chunks of ≤512 tokens
  (tiktoken `cl100k_base`) without splitting mid-sentence.
- Column headers are extracted from the first non-empty row → `column_labels`.
  Row headers (first column of each row) → `row_labels`.

**XLSX rules:**

- Each logical table (contiguous non-empty range) per sheet is one chunk.
- Sheet name preserved via `Page.sheet_name`.

**Output:** `list[ChunkMetadata]` with `text`, `row_labels`, `column_labels` populated.
`section_label` and `period_labels` are empty at this stage; populated by `MetadataExtractor`.

---

### 6.2 `MetadataExtractor`

**Location:** `trellis/retrieval/metadata.py`

Runs over each `ChunkMetadata` after chunking. Populates `section_label`,
`period_labels`, `period_ends`. Uses the registered `SectionClassifier` and a
domain-agnostic period regex that fires only when date/period patterns are present.

**Section labelling:** delegates entirely to the registered `SectionClassifier`.
The extractor does not interpret the returned label. If no classifier is registered,
`section_label` remains `None`.

**Period detection** is domain-agnostic. It fires on any document containing temporal
references in column headers or prose — financial statements, loan applications,
audit reports, and model validation documents all contain dates:

```python
PERIOD_PATTERNS = [
    r"(?:three|six|nine|twelve)\s+months?\s+ended\s+(\w+\s+\d{1,2},?\s+\d{4})",
    r"(?:year|quarter|period)\s+ended\s+(\w+\s+\d{1,2},?\s+\d{4})",
    r"(Q[1-4]\s+(?:FY|CY)?\d{4})",
    r"(\d{4}-\d{2}-\d{2})",
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
]
```

Extracted date strings are normalized to ISO format via `dateutil.parser`.
Period detection is **opt-in at query time** via `ChunkFilter.period_labels`.
Workflows where temporal alignment is irrelevant (compliance clause search,
static form extraction) simply omit `period_labels` from the filter.

---

### 6.3 `BM25Indexer`

**Location:** `trellis/retrieval/bm25_index.py`  
**Dependency:** `rank-bm25>=0.2.2`

```python
class DocumentBM25Index:
    def __init__(
        self,
        chunks: list[ChunkMetadata],
        vocabulary: SynonymVocabulary,
    ):
        # Tokenize using vocabulary.tokenize() — synonym expansion at index time
        ...

    def search(
        self,
        query: str,
        k: int = 20,
        filters: ChunkFilter | None = None,
    ) -> list[tuple[str, float]]:
        # Returns (chunk_id, bm25_score); query tokenized via same vocabulary
        ...
```

Because synonym expansion is applied identically at index time and query time via
`SynonymVocabulary.tokenize`, BM25 matches on the expanded vocabulary transparently.
An MRM operator registers a vocabulary covering `"PD model" → ["probability of default
model", "pd estimation model"]`; a mortgage operator registers `"DTI" → ["debt-to-income
ratio"]`. The index implementation is identical in both cases.

---

### 6.4 `DocumentVectorIndex`

**Location:** `trellis/retrieval/vector_index.py`  
**Dependencies:** `faiss-cpu>=1.7.4` (production), `numpy>=1.24` (always available)

Two implementations behind a common interface, allowing the operator to select the
appropriate one based on corpus scale:

```python
class VectorIndex(ABC):
    @abstractmethod
    def upsert(self, chunk_ids: list[str], embeddings: np.ndarray) -> None: ...

    @abstractmethod
    def search(
        self,
        query_embedding: np.ndarray,
        k: int = 20,
        filters: ChunkFilter | None = None,
    ) -> list[tuple[str, float]]:
        # Returns (chunk_id, cosine_score)
        ...

class NumpyVectorIndex(VectorIndex):
    """In-process cosine similarity. No dependencies beyond numpy.
    Adequate for single-document and small corpus workloads (≤5,000 chunks).
    Default implementation."""
    ...

class FAISSVectorIndex(VectorIndex):
    """FAISS flat inner-product index (L2-normalized → cosine similarity).
    Suitable for corpus-scale workloads (≥5,000 chunks).
    Activated by operator registration; interface is identical to NumpyVectorIndex."""
    ...
```

The crossover point is roughly 5,000 chunks. At that scale the latency difference
between numpy cosine and FAISS becomes meaningful. `NumpyVectorIndex` is the default;
`FAISSVectorIndex` is registered by the operator when corpus scale warrants it.

**Embedding model:** configurable via `TRELLIS_EMBEDDING_MODEL` environment variable.
Default: `text-embedding-3-small` (1536-dim, via litellm). Any model accessible through
litellm is supported without code changes.

**Index lifecycle:** embedding generation runs as a background `asyncio.Task`.
`ChunkPipelineState.embedding_complete` tracks readiness. `select` checks readiness
before querying; falls back to BM25-only with a warning if not yet ready.

---

### 6.5 `FactExtractor` + `StructuredFactStore`

**Location:** `trellis/retrieval/fact_store.py`

Tier 1 is an optional precision layer. It is most valuable when the document set
contains structured tabular content with known field semantics — financial statements,
standardised application forms, templated reports. For free-form documents (legal
opinions, narrative audit reports), Tier 1 produces few hits and Tier 2 carries
the load. The framework does not require Tier 1 to function.

```python
class StructuredFactStore:
    """
    In-memory fact store keyed by
    (tenant_id, document_id, section, field_canonical).
    Serializable to/from JSON for session persistence.
    """

    def upsert(self, facts: list[StructuredFact]) -> None: ...

    def query(
        self,
        tenant_id: str,
        document_ids: list[str] | None = None,
        sections: list[str] | None = None,
        field_canonical: str | None = None,
        period_labels: list[str] | None = None,
    ) -> list[StructuredFact]: ...

    def has_document(self, tenant_id: str, document_id: str) -> bool: ...
```

`FactExtractor` runs over `TABLE` chunks using the registered `FieldTaxonomy` to map
raw row labels to canonical keys. It is a generic table-cell extractor: it reads
`row_labels` × `column_labels` intersections and emits one `StructuredFact` per cell.
Whether those facts are useful downstream depends entirely on the operator's taxonomy.

For document types where structured data is available directly from an external source
(structured data APIs, standardised data feeds), `StructuredFact` records can be
ingested directly at `fetch_data` time, bypassing PDF extraction entirely. This is an
operator-level integration decision.

---

### 6.6 `ChunkPipeline` + `ChunkPipelineState`

**Location:** `trellis/retrieval/pipeline.py`

```python
@dataclass
class ChunkPipelineState:
    document_id: str
    tenant_id: str
    chunking_complete: bool = False
    metadata_complete: bool = False
    bm25_complete: bool = False
    fact_extraction_complete: bool = False
    embedding_complete: bool = False        # async — lags behind synchronous steps
    error: str | None = None

class ChunkPipeline:
    def __init__(self, registry: RetrievalRegistry): ...

    async def run(
        self,
        handle: DocumentHandle,
        tenant_id: str,
        document_id: str,
        background: bool = True,
    ) -> ChunkPipelineState: ...
```

**Execution order:**

```
1. StructuralChunker.chunk(handle)               → ChunkList       [sync, fast]
2. MetadataExtractor.extract(chunks, registry)   → ChunkMetadata[] [sync, fast]
3. DocumentBM25Index.build(chunks, vocabulary)   → index           [sync, <200ms]
4. FactExtractor.extract(chunks, taxonomy)       → facts           [sync, variable]
5. EmbeddingIndexer.embed_and_upsert(chunks)     → vector index    [async if background=True]
```

Steps 1–4 complete synchronously before `ingest_document` returns. Step 5 runs as a
background `asyncio.Task`. The cancellation path for Step 5 is wired to
`ExecutionOptions.cancel_event` to prevent orphaned embedding API calls on pipeline
cancellation.

---

### 6.7 `CrossEncoderReranker`

**Location:** `trellis/retrieval/reranker.py`  
**Dependency:** `sentence-transformers>=2.6` (optional)

```python
class CrossEncoderReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"): ...

    def rerank(
        self,
        query: str,
        candidates: list[ScoredChunk],
        top_k: int = 10,
    ) -> list[ScoredChunk]: ...
```

Applied when `select` is called with `rerank: true` and candidate set is ≤50 chunks.
If `sentence-transformers` is not installed, returns the hybrid-ranked candidates
unchanged with a warning added to `RetrievalResult`.

---

### 6.8 `RetrievalOrchestrator`

**Location:** `trellis/retrieval/orchestrator.py`

```python
class RetrievalOrchestrator:
    def __init__(
        self,
        fact_store: StructuredFactStore,
        bm25_indexes: dict[str, DocumentBM25Index],
        vector_index: VectorIndex,
        reranker: CrossEncoderReranker | None,
        embedding_fn: Callable[[str], np.ndarray],
        registry: RetrievalRegistry,
    ): ...

    async def retrieve(
        self,
        query: str,
        filters: ChunkFilter,
        top_k: int = 15,
        use_rerank: bool = False,
    ) -> RetrievalResult: ...
```

**Hybrid ranking via Reciprocal Rank Fusion:**

```python
def reciprocal_rank_fusion(
    bm25_results: list[tuple[str, float]],
    dense_results: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for rank, (chunk_id, _) in enumerate(bm25_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    for rank, (chunk_id, _) in enumerate(dense_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

---

## 7. `select` Tool Extension

**Location:** `trellis/tools/impls/select.py` (existing file, extended)

All existing behaviour is preserved when `granularity` is absent or `"page"`.

### New Input Parameters

| Input | Type | Required | Default | Description |
|---|---|---|---|---|
| `granularity` | `"page" \| "chunk"` | no | `"page"` | `"chunk"` activates structured retrieval |
| `filter` | dict (→ `ChunkFilter`) | no | `None` | Pre-filter before search. All sub-fields optional. `section_labels` values are domain-defined strings |
| `rerank` | bool | no | `False` | Apply cross-encoder re-ranking to top-k candidates |
| `top_k` | int | no | `15` | Chunks to return in chunk mode |

In `granularity: chunk` mode, `select` emits a `RetrievalResult`. Downstream tools
(`extract_fields`, `extract_from_texts`, `extract_from_tables`, `llm_job`) accept
`RetrievalResult` as a valid `DocumentInput`. `RetrievalResult.facts` can be consumed
directly by `llm_job` or `compute` tasks, bypassing further extraction when Tier 1
provides sufficient coverage.

---

## 8. DSL Usage Examples

The `select` interface is identical across all workload types. Domain specificity lives
entirely in `filter.section_labels`, which the operator's `SectionClassifier` populates.

### 8.1 Financial Spreading — Pin-Point Extraction

```yaml
- id: select_income_statement
  tool: select
  inputs:
    document: "{{ingest_report.output}}"
    prompt: "Diluted weighted average shares outstanding and earnings per share"
    granularity: chunk
    filter:
      section_labels: [income_statement]
      period_labels: "{{resolve_periods.output}}"
    rerank: true
```

### 8.2 Mortgage Processing — Income Verification

```yaml
- id: select_income_sections
  tool: select
  inputs:
    document: "{{ingest_loan_package.output}}"
    prompt: "Borrower income, employment history, and salary verification"
    granularity: chunk
    filter:
      section_labels: [borrower_income, employment_verification]
```

### 8.3 Credit Underwriting — Multi-Document Reconciliation

```yaml
- id: select_credit_data
  tool: select
  inputs:
    document: "{{ingest_credit_package.output}}"    # list of handles
    prompt: "Outstanding balances, payment history, and delinquency records"
    granularity: chunk
    filter:
      section_labels: [tradeline_summary, derogatory_marks, payment_history]
    top_k: 30
```

### 8.4 Compliance Review — Clause-Level Retrieval

```yaml
- id: select_obligations
  tool: select
  inputs:
    document: "{{ingest_policy_docs.output}}"
    prompt: "Data retention obligations and breach notification requirements"
    granularity: chunk
    filter:
      section_labels: [policy_obligation, regulatory_requirement]
    rerank: true
```

### 8.5 MRM — Validation Finding Extraction

```yaml
- id: select_findings
  tool: select
  inputs:
    document: "{{ingest_validation_report.output}}"
    prompt: "Model limitations, compensating controls, and remediation actions"
    granularity: chunk
    filter:
      section_labels: [validation_findings, model_limitations, remediation]
```

---

## 9. Package Layout

```
trellis/retrieval/
├── __init__.py
├── models.py          # ChunkType, ChunkMetadata, StructuredFact, ChunkFilter,
│                      # ScoredChunk, RetrievalResult, ChunkPipelineState
├── plugins.py         # SectionClassifier, SynonymVocabulary, FieldTaxonomy,
│                      # RetrievalRegistry; default implementations:
│                      # KeywordSectionClassifier, DictSynonymVocabulary,
│                      # DictFieldTaxonomy, NullClassifier, SlugifyTaxonomy
├── chunker.py         # StructuralChunker (PDF + XLSX)
├── metadata.py        # MetadataExtractor
├── bm25_index.py      # DocumentBM25Index
├── vector_index.py    # VectorIndex (ABC), NumpyVectorIndex, FAISSVectorIndex
├── fact_store.py      # StructuredFact, StructuredFactStore, FactExtractor
├── reranker.py        # CrossEncoderReranker
├── rrf.py             # reciprocal_rank_fusion()
├── orchestrator.py    # RetrievalOrchestrator
└── pipeline.py        # ChunkPipeline, ChunkPipelineState
```

`RetrievalRegistry` and `StructuredFactStore` are injected into the `Orchestrator`
at startup alongside the existing `ToolRegistry` and `Blackboard`, following the
established dependency injection pattern.

---

## 10. `ingest_document` Integration

After existing OCR and page resolution completes, `ingest_document` triggers
`ChunkPipeline.run(handle, tenant_id, document_id, background=True)`. The handle is
returned immediately with `handle.chunk_state` attached.

`chunk_pipeline` is injected at `ingest_document` construction time. If not injected
(tests, minimal deployments), the chunk pipeline is skipped and `handle.chunk_state`
is `None`. Page-mode `select` continues to function regardless.

---

## 11. Deploy-Time Configuration Examples

Plugin registration happens at operator startup, mirroring the `FunctionRegistry` and
`SchemaRegistry` registration patterns.

**SEC filing deployment:**

```python
registry = RetrievalRegistry()
registry.register_classifier(KeywordSectionClassifier({
    "income_statement": ["revenue", "gross profit", "net income", "operating income"],
    "balance_sheet": ["total assets", "total liabilities", "shareholders equity"],
    "cash_flow": ["operating activities", "investing activities", "financing activities"],
    "risk_factors": ["risk", "uncertainty", "may adversely affect"],
}))
registry.register_vocabulary(DictSynonymVocabulary({
    "revenue": ["revenues", "net revenues", "total revenues", "net sales"],
    "net_income": ["net earnings", "profit for the period", "net profit"],
    "capex": ["capital expenditures", "purchases of property plant and equipment"],
}))
```

**Mortgage deployment:**

```python
registry.register_classifier(KeywordSectionClassifier({
    "borrower_income": ["gross income", "base salary", "employment income", "monthly income"],
    "property_appraisal": ["appraised value", "market value", "property description"],
    "credit_history": ["credit score", "fico", "derogatory", "payment history"],
    "liabilities": ["monthly obligations", "debt payments", "outstanding balance"],
}))
registry.register_vocabulary(DictSynonymVocabulary({
    "annual_income": ["gross annual income", "yearly earnings", "total annual compensation"],
    "dti": ["debt-to-income", "debt to income ratio", "monthly debt obligations"],
}))
```

**MRM deployment:**

```python
registry.register_classifier(KeywordSectionClassifier({
    "model_description": ["model purpose", "methodology", "theoretical basis"],
    "validation_findings": ["finding", "observation", "exception", "concern"],
    "model_limitations": ["limitation", "weakness", "assumption", "caveat"],
    "remediation": ["remediation", "action item", "compensating control", "recommendation"],
}))
```

---

## 12. Test Coverage

```
tests/
├── unit/retrieval/
│   ├── test_chunker.py
│   ├── test_metadata_extractor.py
│   ├── test_plugins.py               # KeywordSectionClassifier, DictSynonymVocabulary,
│   │                                 # DictFieldTaxonomy, fallback/null implementations
│   ├── test_bm25_index.py
│   ├── test_vector_index.py          # NumpyVectorIndex; FAISSVectorIndex interface parity
│   ├── test_fact_store.py
│   ├── test_reranker.py
│   ├── test_rrf.py
│   └── test_retrieval_orchestrator.py
└── integration/retrieval/
    ├── test_chunk_pipeline.py        # ingest → chunk → index round-trip
    ├── test_select_chunk_mode.py     # select with granularity:chunk and section filter
    └── test_cross_document.py        # multi-document retrieval with ChunkFilter
```

**Fixture documents** (`tests/fixtures/retrieval/`) must cover multiple document
types to validate domain-agnosticism. Minimum required set: one financial statement
PDF (tabular, structured), one form-based PDF (synthetic loan application), and one
prose-heavy document (synthetic compliance policy). Each fixture includes a
ground-truth extraction file against which integration tests assert exact field values
and verify section label accuracy.

---

## 13. Dependencies

### New Required

| Package | Version | Purpose |
|---|---|---|
| `rank-bm25` | `>=0.2.2` | BM25 index |
| `numpy` | already required | `NumpyVectorIndex`, RRF |
| `python-dateutil` | `>=2.8` | Period date normalisation |

### New Optional

| Package | Version | Purpose | Fallback |
|---|---|---|---|
| `faiss-cpu` | `>=1.7.4` | `FAISSVectorIndex` for corpus scale | `NumpyVectorIndex` |
| `sentence-transformers` | `>=2.6` | Cross-encoder re-ranking | No re-ranking; hybrid rank preserved |

---

## 14. Implementation Priority

**Phase 1 — Structural chunking + metadata + plugin interfaces**  
`StructuralChunker`, `MetadataExtractor`, `plugins.py` with null fallbacks,
`ChunkPipelineState`. Wire into `ingest_document`. Validate chunk quality against all
three fixture document types before building indexes on top.

**Phase 2 — Hybrid retrieval (BM25 + numpy cosine)**  
`DocumentBM25Index`, `NumpyVectorIndex`, `RetrievalOrchestrator` with RRF,
`ChunkFilter`. Extend `select` with `granularity: chunk`. Integration tests across
all fixture types; confirm section label filtering produces correct candidates for
each workload class.

**Phase 3 — Structured fact store**  
`StructuredFactStore`, `FactExtractor`, `FieldTaxonomy` plugin interface.
Tier 1 → Tier 2 fallback chain in `RetrievalOrchestrator`.

**Phase 4 — FAISS + cross-encoder (scale + precision)**  
`FAISSVectorIndex`, async embedding background path with cancellation wiring,
`CrossEncoderReranker`. Both activated by operator registration; defaults unchanged.

---

## 15. Open Questions

1. **`ChunkPipelineState` placement.** Attaching state to `DocumentHandle` keeps the
   tool interface clean but adds a concern to the document model. Alternative: a
   tenant-scoped `ChunkPipelineRegistry` keyed by `document_id`, mirroring the
   `SchemaRegistry` pattern. Decision needed before Phase 1 ships.

2. **Vector index persistence.** Current spec builds indexes in-memory per session.
   For deployments with large or frequently-reused document corpora, a persistent
   vector store (file-serialized FAISS, ChromaDB, Qdrant) avoids re-embedding on
   every session. Pluggable `VectorIndex` interface supports this without framework
   changes — persistence is an operator-level deployment choice.

3. **`SectionClassifier` LLM option.** `KeywordSectionClassifier` is fast and
   transparent but brittle on novel or heterogeneous document types. An
   `LLMSectionClassifier` that uses a `llm_job`-style call for ambiguous chunks is
   a natural extension behind the existing interface. Latency and cost trade-offs
   need evaluation before recommending it as a default.

4. **Embedding model domain sensitivity.** General-purpose embeddings perform well on
   prose but degrade on sparse table content. Whether a domain-tuned model materially
   improves retrieval precision is an empirical question best answered by running the
   fixture ground-truth benchmarks across candidate models before committing.

---

*End of spec. See `ARCHITECTURE.md` for the overall package layout and extension
patterns that this spec extends.*
