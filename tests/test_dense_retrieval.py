"""Test-plan §1 — dense retrieval.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §1

`dense_search` fans a single query vector out across the supplied spaces'
`VectorStore` indexes and merges the results into one score-ranked list.
Test 1.2 (isolation) is the load-bearing one: the whole routing design
rests on a chunk in an unsupplied space never being reachable, at any
score, so it is asserted directly rather than inferred from the others.
"""

from __future__ import annotations

import math

import pytest

from app.rag.retrieve.dense import Hit, dense_search
from app.rag.vector_store import VectorStore

DIMENSION = 4


def _unit(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(c * c for c in vector))
    return [c / length for c in vector]


# --- 1.1 Single-space search --------------------------------------------------


def test_1_1_single_space_search_returns_only_that_spaces_chunks(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.add("hr", [1, 2], [_unit([1.0, 0.0, 0.0, 0.0]), _unit([0.0, 1.0, 0.0, 0.0])])

    hits = dense_search(_unit([1.0, 0.0, 0.0, 0.0]), spaces=["hr"], top_n=5, store=store)

    assert {hit.chunk_id for hit in hits} == {1, 2}
    assert all(hit.source == "dense" for hit in hits)
    assert all(isinstance(hit, Hit) for hit in hits)


# --- 1.2 Isolation ---------------------------------------------------------------


def test_1_2_chunk_in_unsupplied_space_cannot_appear_at_any_score(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    query = _unit([1.0, 0.0, 0.0, 0.0])
    # A perfect match, sitting in a space the caller does not ask for.
    store.add("legal", [99], [query])
    store.add("hr", [1], [_unit([0.0, 1.0, 0.0, 0.0])])

    hits = dense_search(query, spaces=["hr"], top_n=10, store=store)

    assert 99 not in {hit.chunk_id for hit in hits}
    assert {hit.chunk_id for hit in hits} == {1}


# --- 1.3 Multi-space merge -------------------------------------------------------


def test_1_3_multi_space_results_merge_into_one_ranked_list(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    query = _unit([1.0, 0.0, 0.0, 0.0])
    store.add("hr", [1], [_unit([1.0, 0.1, 0.0, 0.0])])  # close to query
    store.add("legal", [2], [_unit([0.0, 1.0, 0.0, 0.0])])  # far from query

    hits = dense_search(query, spaces=["hr", "legal"], top_n=10, store=store)

    assert [hit.chunk_id for hit in hits] == [1, 2]
    assert hits[0].score > hits[1].score


# --- 1.4 Empty space --------------------------------------------------------------


def test_1_4_space_with_no_vectors_contributes_nothing_and_raises_nothing(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.add("hr", [1], [_unit([1.0, 0.0, 0.0, 0.0])])

    hits = dense_search(
        _unit([1.0, 0.0, 0.0, 0.0]), spaces=["hr", "never-touched"], top_n=10, store=store
    )

    assert [hit.chunk_id for hit in hits] == [1]


# --- 1.5 top_n respected -----------------------------------------------------------


def test_1_5_top_n_is_applied_per_space(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    query = _unit([1.0, 0.0, 0.0, 0.0])
    store.add(
        "hr",
        [1, 2, 3],
        [_unit([1.0, 0.0, 0.0, 0.0]), _unit([1.0, 0.1, 0.0, 0.0]), _unit([1.0, 0.2, 0.0, 0.0])],
    )
    store.add(
        "legal",
        [4, 5, 6],
        [_unit([1.0, 0.0, 0.0, 0.0]), _unit([1.0, 0.1, 0.0, 0.0]), _unit([1.0, 0.2, 0.0, 0.0])],
    )

    hits = dense_search(query, spaces=["hr", "legal"], top_n=2, store=store)

    hr_hits = [hit for hit in hits if hit.chunk_id in (1, 2, 3)]
    legal_hits = [hit for hit in hits if hit.chunk_id in (4, 5, 6)]
    assert len(hr_hits) == 2
    assert len(legal_hits) == 2


# --- 1.6 Pinned vectors give exact scores -------------------------------------------


def test_1_6_pinned_vectors_produce_exact_hand_computed_cosine_score(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    query = _unit([1.0, 1.0, 0.0, 0.0])
    stored = _unit([1.0, 0.0, 0.0, 0.0])
    store.add("hr", [1], [stored])

    expected = sum(a * b for a, b in zip(query, stored))

    [hit] = dense_search(query, spaces=["hr"], top_n=5, store=store)

    assert hit.score == pytest.approx(expected, abs=1e-6)
