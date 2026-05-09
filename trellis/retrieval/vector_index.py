from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from .models import ChunkMetadata, ChunkFilter, ScoredChunk

EMBEDDING_MODEL = os.getenv("TRELLIS_EMBEDDING_MODEL", "text-embedding-3-small")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # a: (D,), b: (N, D)  → (N,)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b, axis=1)
    denom = norm_b * norm_a
    denom = np.where(denom == 0, 1e-10, denom)
    return (b @ a) / denom


class VectorIndex(ABC):
    @abstractmethod
    def upsert(self, chunk_ids: list[str], embeddings: np.ndarray) -> None: ...

    @abstractmethod
    def search(
        self,
        query_embedding: np.ndarray,
        k: int = 20,
        filters: Optional[ChunkFilter] = None,
    ) -> list[tuple[str, float]]: ...


class NumpyVectorIndex(VectorIndex):
    """In-process cosine similarity. No dependencies beyond numpy. Default for ≤5,000 chunks."""

    def __init__(self):
        self._ids: list[str] = []
        self._matrix: Optional[np.ndarray] = None  # (N, D)

    def upsert(self, chunk_ids: list[str], embeddings: np.ndarray) -> None:
        if self._matrix is None:
            self._ids = list(chunk_ids)
            self._matrix = embeddings.copy()
        else:
            existing = {cid: i for i, cid in enumerate(self._ids)}
            new_ids = []
            new_vecs = []
            for i, cid in enumerate(chunk_ids):
                if cid in existing:
                    self._matrix[existing[cid]] = embeddings[i]
                else:
                    new_ids.append(cid)
                    new_vecs.append(embeddings[i])
            if new_ids:
                self._ids.extend(new_ids)
                self._matrix = np.vstack([self._matrix, np.array(new_vecs)])

    def search(
        self,
        query_embedding: np.ndarray,
        k: int = 20,
        filters: Optional[ChunkFilter] = None,
    ) -> list[tuple[str, float]]:
        if self._matrix is None or len(self._ids) == 0:
            return []
        sims = _cosine_similarity(query_embedding, self._matrix)
        if filters is not None and filters.document_ids is not None:
            # Can only filter by document_id here since we don't store full ChunkMetadata
            # Caller is expected to pass pre-filtered chunk_ids or use BM25 filter
            pass
        order = np.argsort(sims)[::-1][:k]
        return [(self._ids[i], float(sims[i])) for i in order]


class FAISSVectorIndex(VectorIndex):
    """FAISS flat inner-product index (L2-normalized → cosine). For ≥5,000 chunks."""

    def __init__(self, dim: int):
        try:
            import faiss  # type: ignore
        except ImportError:
            raise ImportError("faiss-cpu is required. Install with: pip install faiss-cpu")
        self._faiss = faiss
        self._index = faiss.IndexFlatIP(dim)
        self._ids: list[str] = []

    def upsert(self, chunk_ids: list[str], embeddings: np.ndarray) -> None:
        normed = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10)
        self._faiss.normalize_L2(normed)
        self._index.add(normed.astype(np.float32))
        self._ids.extend(chunk_ids)

    def search(
        self,
        query_embedding: np.ndarray,
        k: int = 20,
        filters: Optional[ChunkFilter] = None,
    ) -> list[tuple[str, float]]:
        if len(self._ids) == 0:
            return []
        q = query_embedding.reshape(1, -1).astype(np.float32)
        self._faiss.normalize_L2(q)
        scores, indices = self._index.search(q, min(k, len(self._ids)))
        return [
            (self._ids[int(i)], float(s))
            for i, s in zip(indices[0], scores[0])
            if i >= 0
        ]


async def embed_texts(texts: list[str], model: str = EMBEDDING_MODEL) -> np.ndarray:
    """Embed a list of texts via litellm. Returns (N, D) float32 array."""
    import litellm  # type: ignore

    resp = await litellm.aembedding(model=model, input=texts)
    vectors = [item["embedding"] for item in resp.data]
    return np.array(vectors, dtype=np.float32)
