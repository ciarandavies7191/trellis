from __future__ import annotations
import logging
from typing import Optional
import numpy as np
from .models import ChunkMetadata, ChunkFilter, ScoredChunk, RetrievalResult, StructuredFact
from .bm25_index import DocumentBM25Index
from .vector_index import VectorIndex, embed_texts, EMBEDDING_MODEL
from .fact_store import StructuredFactStore
from .rrf import reciprocal_rank_fusion
from .reranker import CrossEncoderReranker
from .plugins import RetrievalRegistry

logger = logging.getLogger(__name__)


class RetrievalOrchestrator:
    def __init__(
        self,
        fact_store: StructuredFactStore,
        bm25_indexes: dict[str, DocumentBM25Index],
        vector_index: VectorIndex,
        reranker: Optional[CrossEncoderReranker],
        embedding_model: str = EMBEDDING_MODEL,
        registry: Optional[RetrievalRegistry] = None,
        chunk_lookup: Optional[dict[str, ChunkMetadata]] = None,
    ):
        self._fact_store = fact_store
        self._bm25_indexes = bm25_indexes
        self._vector_index = vector_index
        self._reranker = reranker
        self._embedding_model = embedding_model
        self._registry = registry
        self._chunk_lookup = chunk_lookup or {}

    def _get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, ChunkMetadata]:
        return {cid: self._chunk_lookup[cid] for cid in chunk_ids if cid in self._chunk_lookup}

    async def retrieve(
        self,
        query: str,
        filters: ChunkFilter,
        top_k: int = 15,
        use_rerank: bool = False,
        tenant_id: str = "default",
    ) -> RetrievalResult:
        warnings: list[str] = []
        query_periods = list(filters.period_labels or [])

        tier1_facts: list[StructuredFact] = []
        if self._fact_store.has_document(tenant_id, filters.document_ids[0] if filters.document_ids else ""):
            tier1_facts = self._fact_store.query(
                tenant_id=tenant_id,
                document_ids=filters.document_ids,
                sections=filters.section_labels,
                period_labels=filters.period_labels,
            )

        bm25_results: list[tuple[str, float]] = []
        for doc_id, bm25_idx in self._bm25_indexes.items():
            if filters.document_ids is not None and doc_id not in filters.document_ids:
                continue
            results = bm25_idx.search(query, k=top_k * 2, filters=filters)
            bm25_results.extend(results)
        bm25_results.sort(key=lambda x: x[1], reverse=True)
        bm25_results = bm25_results[: top_k * 2]

        dense_results: list[tuple[str, float]] = []
        try:
            q_emb = await embed_texts([query], model=self._embedding_model)
            dense_results = self._vector_index.search(q_emb[0], k=top_k * 2, filters=filters)
        except Exception as e:
            warnings.append(f"Vector search unavailable: {e}; falling back to BM25 only.")

        if bm25_results or dense_results:
            fused = reciprocal_rank_fusion(bm25_results, dense_results)
        else:
            fused = []

        chunk_metas = self._get_chunks_by_ids([cid for cid, _ in fused])
        scored_chunks = [
            ScoredChunk(
                chunk=chunk_metas[cid],
                score=score,
                retrieval_method="hybrid" if (bm25_results and dense_results) else ("bm25" if bm25_results else "dense"),
            )
            for cid, score in fused
            if cid in chunk_metas
        ][:top_k]

        if use_rerank and self._reranker is not None and scored_chunks:
            scored_chunks, rerank_warnings = self._reranker.rerank(query, scored_chunks, top_k=top_k)
            warnings.extend(rerank_warnings)

        tier_used = "tier1" if tier1_facts and not scored_chunks else ("hybrid" if (bm25_results and dense_results) else "tier2")
        if tier1_facts and scored_chunks:
            tier_used = "hybrid"

        return RetrievalResult(
            facts=tier1_facts,
            chunks=scored_chunks,
            tier_used=tier_used,
            warnings=warnings,
            query_periods=query_periods,
        )
