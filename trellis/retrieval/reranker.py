from __future__ import annotations


class CrossEncoderReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self._model_name = model_name
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder  # type: ignore
                self._model = CrossEncoder(self._model_name)
            except ImportError:
                self._model = None

    def rerank(self, query: str, candidates: list, top_k: int = 10) -> tuple[list, list[str]]:
        """Returns (reranked_chunks, warnings). candidates are ScoredChunk."""
        self._load()
        warnings = []
        if self._model is None:
            warnings.append(
                "CrossEncoderReranker: sentence-transformers not installed; "
                "returning hybrid-ranked candidates unchanged. "
                "Install with: pip install sentence-transformers"
            )
            return candidates[:top_k], warnings
        if len(candidates) > 50:
            candidates = candidates[:50]
        pairs = [(query, c.chunk.text) for c in candidates]
        scores = self._model.predict(pairs)
        from .models import ScoredChunk
        reranked = sorted(
            zip(scores, candidates),
            key=lambda x: x[0],
            reverse=True,
        )[:top_k]
        return [
            ScoredChunk(chunk=c.chunk, score=float(s), retrieval_method="rerank")
            for s, c in reranked
        ], warnings
