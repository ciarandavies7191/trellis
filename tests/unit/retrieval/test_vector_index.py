from __future__ import annotations

import numpy as np
import pytest

from trellis.retrieval.vector_index import NumpyVectorIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unit_vec(d: int, hot: int) -> np.ndarray:
    """Return a unit vector with a spike at position `hot`."""
    v = np.zeros(d, dtype=np.float32)
    v[hot] = 1.0
    return v


def _make_index(*chunk_ids_and_vecs: tuple[str, np.ndarray]) -> NumpyVectorIndex:
    idx = NumpyVectorIndex()
    ids = [x[0] for x in chunk_ids_and_vecs]
    vecs = np.stack([x[1] for x in chunk_ids_and_vecs])
    idx.upsert(ids, vecs)
    return idx


# ---------------------------------------------------------------------------
# Upsert and search
# ---------------------------------------------------------------------------


class TestNumpyVectorIndexUpsert:
    def test_upsert_then_search_returns_inserted_chunk_ids(self):
        idx = _make_index(("c1", _unit_vec(4, 0)), ("c2", _unit_vec(4, 1)))
        results = idx.search(_unit_vec(4, 0))
        ids = [r[0] for r in results]
        assert "c1" in ids

    def test_upsert_existing_chunk_id_updates_embedding(self):
        idx = NumpyVectorIndex()
        v1 = _unit_vec(4, 0)
        v2 = _unit_vec(4, 3)
        idx.upsert(["c1"], np.stack([v1]))
        idx.upsert(["c1"], np.stack([v2]))
        results = idx.search(_unit_vec(4, 3), k=1)
        assert results[0][0] == "c1"
        assert results[0][1] > 0.99

    def test_upsert_existing_does_not_create_duplicate(self):
        idx = NumpyVectorIndex()
        v = _unit_vec(4, 0)
        idx.upsert(["c1"], np.stack([v]))
        idx.upsert(["c1"], np.stack([v]))
        results = idx.search(_unit_vec(4, 0))
        assert sum(1 for r in results if r[0] == "c1") == 1


# ---------------------------------------------------------------------------
# Search ordering and limits
# ---------------------------------------------------------------------------


class TestNumpyVectorIndexSearch:
    def test_search_returns_descending_cosine_similarity_order(self):
        d = 4
        idx = _make_index(
            ("c_best", _unit_vec(d, 0)),
            ("c_mid", np.array([0.7, 0.7, 0.0, 0.0], dtype=np.float32)),
            ("c_worst", _unit_vec(d, 3)),
        )
        query = _unit_vec(d, 0)
        results = idx.search(query)
        scores = [r[1] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_top_result_is_most_similar_chunk(self):
        d = 4
        idx = _make_index(
            ("c_match", _unit_vec(d, 2)),
            ("c_other", _unit_vec(d, 1)),
        )
        results = idx.search(_unit_vec(d, 2), k=1)
        assert results[0][0] == "c_match"

    def test_search_with_k_greater_than_total_returns_all(self):
        idx = _make_index(("c1", _unit_vec(4, 0)), ("c2", _unit_vec(4, 1)))
        results = idx.search(_unit_vec(4, 0), k=100)
        assert len(results) == 2

    def test_empty_index_returns_empty_list(self):
        idx = NumpyVectorIndex()
        results = idx.search(_unit_vec(4, 0))
        assert results == []

    def test_search_k_limits_results(self):
        d = 4
        idx = _make_index(
            ("c1", _unit_vec(d, 0)),
            ("c2", _unit_vec(d, 1)),
            ("c3", _unit_vec(d, 2)),
            ("c4", _unit_vec(d, 3)),
        )
        results = idx.search(_unit_vec(d, 0), k=2)
        assert len(results) == 2

    def test_second_upsert_adds_new_ids(self):
        idx = NumpyVectorIndex()
        idx.upsert(["c1"], np.stack([_unit_vec(4, 0)]))
        idx.upsert(["c2"], np.stack([_unit_vec(4, 1)]))
        results = idx.search(_unit_vec(4, 0))
        ids = [r[0] for r in results]
        assert "c1" in ids
        assert "c2" in ids
