"""Test-plan §6 — FAISS vector store.

Source: docs/superpowers/test-plans/03-rag-write-path-tests.md §6

`VectorStore` wraps one `IndexFlatIP` (exact inner-product search) per intent
space, keyed by `chunk.id`. Vectors used here are already unit-normalized by
hand, mirroring what the embedding provider guarantees in production — the
store itself must never re-normalize.
"""

from __future__ import annotations

import math

import pytest

from app.rag.vector_store import VectorStore

DIMENSION = 4


def _unit(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(c * c for c in vector))
    return [c / length for c in vector]


# --- 6.1 Add then search -----------------------------------------------------


def test_6_1_add_then_search_returns_added_vector_with_score_near_one(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")

    vector = _unit([1.0, 0.0, 0.0, 0.0])
    store.add("hr", [1], [vector])

    results = store.search("hr", vector, top_n=5)

    assert len(results) == 1
    found_id, score = results[0]
    assert found_id == 1
    assert score == pytest.approx(1.0, abs=1e-5)


# --- 6.2 Disk round-trip ------------------------------------------------------


def test_6_2_disk_round_trip_yields_identical_search_results(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")
    v1 = _unit([1.0, 0.2, 0.0, 0.0])
    v2 = _unit([0.0, 1.0, 0.3, 0.0])
    store.add("hr", [1, 2], [v1, v2])
    store.persist("hr")

    before = store.search("hr", v1, top_n=5)

    reloaded = VectorStore(tmp_path, DIMENSION)
    reloaded.load("hr")
    after = reloaded.search("hr", v1, top_n=5)

    assert before == after


# --- 6.3 Remove ---------------------------------------------------------------


def test_6_3_remove_excludes_id_from_future_searches(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")
    v1 = _unit([1.0, 0.0, 0.0, 0.0])
    v2 = _unit([0.0, 1.0, 0.0, 0.0])
    store.add("hr", [1, 2], [v1, v2])

    store.remove("hr", [1])
    results = store.search("hr", v1, top_n=5)

    returned_ids = [found_id for found_id, _ in results]
    assert 1 not in returned_ids
    assert 2 in returned_ids


# --- 6.4 Move between spaces --------------------------------------------------


def test_6_4_move_transfers_vector_between_spaces(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")
    store.create_space("legal")
    vector = _unit([1.0, 0.0, 0.0, 0.0])
    store.add("hr", [1], [vector])

    store.move("hr", "legal", [1])

    hr_results = store.search("hr", vector, top_n=5)
    legal_results = store.search("legal", vector, top_n=5)

    assert [found_id for found_id, _ in hr_results] == []
    assert [found_id for found_id, _ in legal_results] == [1]


# --- 6.5 Space isolation -------------------------------------------------------


def test_6_5_search_in_space_a_never_returns_chunk_in_space_b(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")
    store.create_space("legal")
    vector = _unit([1.0, 0.0, 0.0, 0.0])
    store.add("hr", [1], [vector])
    store.add("legal", [2], [vector])

    hr_results = store.search("hr", vector, top_n=5)

    assert [found_id for found_id, _ in hr_results] == [1]


# --- 6.6 Empty space -----------------------------------------------------------


def test_6_6_search_on_space_with_no_vectors_returns_empty_not_error(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")

    results = store.search("hr", _unit([1.0, 0.0, 0.0, 0.0]), top_n=5)

    assert results == []


def test_6_6_search_on_never_created_space_returns_empty_not_error(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)

    results = store.search("never-seen", _unit([1.0, 0.0, 0.0, 0.0]), top_n=5)

    assert results == []


# --- 6.7 Delete space -----------------------------------------------------------


def test_6_7_delete_space_removes_index_file(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")
    store.add("hr", [1], [_unit([1.0, 0.0, 0.0, 0.0])])
    store.persist("hr")
    index_path = tmp_path / "hr.index"
    assert index_path.exists()

    store.delete_space("hr")

    assert not index_path.exists()


# --- 6.8 Cross-space comparability ----------------------------------------------


def test_6_8_identical_vector_in_two_spaces_scores_identically(tmp_path):
    store = VectorStore(tmp_path, DIMENSION)
    store.create_space("hr")
    store.create_space("legal")
    identical_vector = _unit([0.5, 0.5, 0.5, 0.1])
    store.add("hr", [1], [identical_vector])
    store.add("legal", [2], [identical_vector])

    query = _unit([1.0, 0.0, 0.0, 0.0])
    hr_score = store.search("hr", query, top_n=1)[0][1]
    legal_score = store.search("legal", query, top_n=1)[0][1]

    assert hr_score == pytest.approx(legal_score, abs=1e-6)
