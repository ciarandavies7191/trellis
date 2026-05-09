from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from trellis.retrieval.models import ChunkMetadata, ChunkType, ScoredChunk
from trellis.retrieval.reranker import CrossEncoderReranker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(text: str) -> ChunkMetadata:
    return ChunkMetadata(
        chunk_id=str(uuid.uuid4()),
        document_id="doc1",
        tenant_id="t1",
        page=1,
        chunk_type=ChunkType.PROSE,
        section_label=None,
        period_labels=[],
        period_ends=[],
        row_labels=[],
        column_labels=[],
        extra={},
        text=text,
        embedding=None,
    )


def _make_scored(text: str, score: float = 0.5) -> ScoredChunk:
    return ScoredChunk(
        chunk=_make_chunk(text),
        score=score,
        retrieval_method="bm25",
    )


# ---------------------------------------------------------------------------
# No sentence-transformers installed
# ---------------------------------------------------------------------------


class TestCrossEncoderRerankerNoLibrary:
    def test_returns_candidates_unchanged_when_not_installed(self):
        candidates = [_make_scored("text one"), _make_scored("text two")]
        reranker = CrossEncoderReranker()
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            reranker._model = None
            result, warnings = reranker.rerank("query", candidates, top_k=10)
        assert result == candidates

    def test_adds_warning_when_not_installed(self):
        candidates = [_make_scored("text one")]
        reranker = CrossEncoderReranker()
        reranker._model = None
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result, warnings = reranker.rerank("query", candidates, top_k=10)
        assert len(warnings) > 0
        assert any("sentence-transformers" in w for w in warnings)

    def test_respects_top_k_when_not_installed(self):
        candidates = [_make_scored(f"text {i}") for i in range(5)]
        reranker = CrossEncoderReranker()
        reranker._model = None
        result, _ = reranker.rerank("query", candidates, top_k=3)
        assert len(result) == 3

    def test_returns_tuple_of_list_and_warnings(self):
        candidates = [_make_scored("text")]
        reranker = CrossEncoderReranker()
        reranker._model = None
        result = reranker.rerank("query", candidates, top_k=10)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)


# ---------------------------------------------------------------------------
# With mocked CrossEncoder
# ---------------------------------------------------------------------------


class TestCrossEncoderRerankerWithModel:
    def _make_reranker_with_mock_model(self, scores: list[float]) -> CrossEncoderReranker:
        mock_model = MagicMock()
        mock_model.predict.return_value = scores
        reranker = CrossEncoderReranker()
        reranker._model = mock_model
        return reranker

    def test_returned_objects_are_scored_chunk_instances(self):
        candidates = [_make_scored("revenue data"), _make_scored("balance sheet")]
        reranker = self._make_reranker_with_mock_model([0.9, 0.4])
        result, warnings = reranker.rerank("revenue", candidates, top_k=2)
        assert all(isinstance(r, ScoredChunk) for r in result)

    def test_retrieval_method_is_rerank(self):
        candidates = [_make_scored("text one"), _make_scored("text two")]
        reranker = self._make_reranker_with_mock_model([0.8, 0.3])
        result, _ = reranker.rerank("query", candidates, top_k=2)
        assert all(r.retrieval_method == "rerank" for r in result)

    def test_reranks_by_model_score(self):
        candidates = [_make_scored("low relevance"), _make_scored("high relevance")]
        reranker = self._make_reranker_with_mock_model([0.2, 0.95])
        result, _ = reranker.rerank("query", candidates, top_k=2)
        assert result[0].chunk.text == "high relevance"

    def test_no_warnings_when_model_available(self):
        candidates = [_make_scored("text")]
        reranker = self._make_reranker_with_mock_model([0.5])
        _, warnings = reranker.rerank("query", candidates, top_k=1)
        assert warnings == []

    def test_top_k_limits_results(self):
        candidates = [_make_scored(f"text {i}") for i in range(5)]
        reranker = self._make_reranker_with_mock_model([0.9, 0.8, 0.7, 0.6, 0.5])
        result, _ = reranker.rerank("query", candidates, top_k=3)
        assert len(result) == 3

    def test_score_comes_from_model_not_original(self):
        candidates = [_make_scored("text", score=0.1)]
        reranker = self._make_reranker_with_mock_model([0.99])
        result, _ = reranker.rerank("query", candidates, top_k=1)
        assert result[0].score == pytest.approx(0.99)

    def test_truncates_candidates_to_50_before_model_call(self):
        candidates = [_make_scored(f"text {i}") for i in range(60)]
        scores = [float(i) for i in range(50)]
        reranker = self._make_reranker_with_mock_model(scores)
        reranker.rerank("query", candidates, top_k=10)
        call_args = reranker._model.predict.call_args[0][0]
        assert len(call_args) == 50
