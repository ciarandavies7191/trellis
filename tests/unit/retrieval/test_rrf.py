from __future__ import annotations

import pytest

from trellis.retrieval.rrf import reciprocal_rank_fusion


class TestReciprocalRankFusion:
    def test_both_lists_populated_chunk_ids_appear_in_result(self):
        bm25 = [("c1", 1.0), ("c2", 0.5)]
        dense = [("c3", 0.9), ("c1", 0.4)]
        result = reciprocal_rank_fusion(bm25, dense)
        result_ids = [r[0] for r in result]
        assert "c1" in result_ids
        assert "c2" in result_ids
        assert "c3" in result_ids

    def test_score_is_sum_of_reciprocals(self):
        k = 60
        bm25 = [("c1", 1.0)]
        dense = [("c1", 0.9)]
        result = reciprocal_rank_fusion(bm25, dense, k=k)
        expected_score = 1.0 / (k + 0) + 1.0 / (k + 0)
        assert abs(result[0][1] - expected_score) < 1e-9

    def test_one_empty_list_equals_non_empty_with_reciprocal_scores(self):
        k = 60
        bm25 = [("c1", 1.0), ("c2", 0.5)]
        result = reciprocal_rank_fusion(bm25, [], k=k)
        assert result[0][0] == "c1"
        assert abs(result[0][1] - 1.0 / (k + 0)) < 1e-9
        assert result[1][0] == "c2"
        assert abs(result[1][1] - 1.0 / (k + 1)) < 1e-9

    def test_other_empty_list_equals_non_empty_with_reciprocal_scores(self):
        k = 60
        dense = [("c1", 0.9), ("c2", 0.4)]
        result = reciprocal_rank_fusion([], dense, k=k)
        assert result[0][0] == "c1"
        assert abs(result[0][1] - 1.0 / (k + 0)) < 1e-9

    def test_overlapping_chunk_id_gets_higher_score_than_unique(self):
        bm25 = [("shared", 1.0), ("bm25_only", 0.5)]
        dense = [("shared", 0.9), ("dense_only", 0.4)]
        result = reciprocal_rank_fusion(bm25, dense)
        scores = {chunk_id: score for chunk_id, score in result}
        assert scores["shared"] > scores["bm25_only"]
        assert scores["shared"] > scores["dense_only"]

    def test_result_is_sorted_descending(self):
        bm25 = [("c1", 1.0), ("c2", 0.5), ("c3", 0.1)]
        dense = [("c3", 0.9), ("c2", 0.4), ("c1", 0.1)]
        result = reciprocal_rank_fusion(bm25, dense)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)

    def test_both_empty_returns_empty(self):
        result = reciprocal_rank_fusion([], [])
        assert result == []

    def test_custom_k_affects_scores(self):
        bm25 = [("c1", 1.0)]
        result_k60 = reciprocal_rank_fusion(bm25, [], k=60)
        result_k10 = reciprocal_rank_fusion(bm25, [], k=10)
        assert result_k10[0][1] > result_k60[0][1]
