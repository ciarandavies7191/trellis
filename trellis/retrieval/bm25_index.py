from __future__ import annotations

from typing import Optional

from .models import ChunkMetadata, ChunkFilter
from .plugins import SynonymVocabulary


def _apply_filter(chunks: list[ChunkMetadata], f: ChunkFilter) -> list[ChunkMetadata]:
    result = chunks
    if f.document_ids is not None:
        result = [c for c in result if c.document_id in f.document_ids]
    if f.chunk_types is not None:
        result = [c for c in result if c.chunk_type in f.chunk_types]
    if f.section_labels is not None:
        result = [c for c in result if c.section_label in f.section_labels]
    if f.period_labels is not None:
        wanted = set(f.period_labels)
        result = [c for c in result if any(p in wanted for p in c.period_labels)]
    if f.extra_filters is not None:
        for k, v in f.extra_filters.items():
            result = [c for c in result if c.extra.get(k) == v]
    return result


class DocumentBM25Index:
    def __init__(self, chunks: list[ChunkMetadata], vocabulary: SynonymVocabulary):
        try:
            from rank_bm25 import BM25Okapi  # type: ignore
        except ImportError:
            raise ImportError(
                "rank-bm25 is required for BM25 retrieval. Install with: pip install rank-bm25"
            )
        self._chunks = chunks
        self._vocabulary = vocabulary
        self._chunk_ids = [c.chunk_id for c in chunks]
        tokenized = [vocabulary.tokenize(c.text) for c in chunks]
        self._bm25 = BM25Okapi(tokenized)

    def search(
        self,
        query: str,
        k: int = 20,
        filters: Optional[ChunkFilter] = None,
    ) -> list[tuple[str, float]]:
        tokens = self._vocabulary.tokenize(query)
        raw_scores = self._bm25.get_scores(tokens)
        scored = list(zip(self._chunk_ids, raw_scores.tolist()))
        if filters is not None:
            allowed_ids = {c.chunk_id for c in _apply_filter(self._chunks, filters)}
            scored = [(cid, s) for cid, s in scored if cid in allowed_ids]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
