"""Test-plan §3 — reciprocal rank fusion.

Source: superpowers/test-plans/04-rag-read-path-tests.md §3

`fuse` combines a dense hit list and a keyword hit list into one ranked
list using RRF: `score = sum(1 / (k + rank))` over whichever lists a chunk
appears in. Test 3.3 is the load-bearing one — it proves the fusion is
rank-only, not score-weighted, by scaling the dense scores' magnitude
while preserving their order and asserting the fused ordering is
unaffected. Cosine and BM25 live on incomparable scales; any score-blended
fusion would need corpus-specific tuning constants that RRF never needs.
"""

from __future__ import annotations

from app.rag.retrieve.dense import Hit
from app.rag.retrieve.fuse import FusedHit, fuse

K = 60


def _dense(chunk_id: int, score: float) -> Hit:
    return Hit(chunk_id=chunk_id, score=score, source="dense")


def _keyword(chunk_id: int, score: float) -> Hit:
    return Hit(chunk_id=chunk_id, score=score, source="keyword")


# --- 3.1 Formula ------------------------------------------------------------


def test_3_1_fused_score_matches_hand_computed_rrf_sum():
    dense = [_dense(1, 0.9), _dense(2, 0.5)]
    keyword = [_keyword(2, -3.0), _keyword(1, -1.0)]

    result = fuse(dense, keyword, k=K, top_k=10)
    by_id = {hit.chunk_id: hit for hit in result}

    # chunk 1: dense rank 1, keyword rank 2
    assert by_id[1].fused_score == 1.0 / (K + 1) + 1.0 / (K + 2)
    # chunk 2: dense rank 2, keyword rank 1
    assert by_id[2].fused_score == 1.0 / (K + 2) + 1.0 / (K + 1)


# --- 3.2 Present in both lists outranks a single-list chunk -----------------


def test_3_2_chunk_in_both_lists_outranks_equally_ranked_single_list_chunk():
    # chunk 1 is rank 1 in both lists; chunk 2 is rank 1 in dense only.
    dense = [_dense(1, 0.9), _dense(2, 0.8)]
    keyword = [_keyword(1, -5.0)]

    result = fuse(dense, keyword, k=K, top_k=10)

    assert [hit.chunk_id for hit in result][0] == 1


# --- 3.3 No normalization -----------------------------------------------------


def test_3_3_scaling_dense_score_magnitude_preserves_fused_ordering():
    dense = [_dense(1, 0.99), _dense(2, 0.5), _dense(3, 0.1)]
    keyword = [_keyword(3, -2.0)]

    baseline = fuse(dense, keyword, k=K, top_k=10)

    scaled_dense = [_dense(hit.chunk_id, hit.score * 1000.0) for hit in dense]
    scaled = fuse(scaled_dense, keyword, k=K, top_k=10)

    assert [hit.chunk_id for hit in baseline] == [hit.chunk_id for hit in scaled]
    # And the fused scores themselves are identical too, not just the order —
    # RRF never looks at score magnitude, only rank.
    assert [hit.fused_score for hit in baseline] == [hit.fused_score for hit in scaled]


# --- 3.4 dense_score carried through unchanged -------------------------------


def test_3_4_dense_score_carried_through_unchanged():
    dense = [_dense(1, 0.123456)]
    keyword: list[Hit] = []

    [hit] = fuse(dense, keyword, k=K, top_k=10)

    assert hit.dense_score == 0.123456


# --- 3.5 Keyword-only hit: dense_score absent, not zero ----------------------


def test_3_5_keyword_only_hit_has_dense_score_none_not_zero():
    dense: list[Hit] = []
    keyword = [_keyword(7, -1.5)]

    [hit] = fuse(dense, keyword, k=K, top_k=10)

    assert hit.dense_score is None
    assert hit.keyword_rank == 1


# --- 3.6 Deterministic --------------------------------------------------------


def test_3_6_identical_input_yields_identical_output_including_tie_order():
    dense = [_dense(1, 0.5), _dense(2, 0.5), _dense(3, 0.5)]  # tied scores
    keyword: list[Hit] = []

    first = fuse(dense, keyword, k=K, top_k=10)
    second = fuse(dense, keyword, k=K, top_k=10)

    assert first == second
    # Ties broken by original (dense-list) order, not re-sorted arbitrarily.
    assert [hit.chunk_id for hit in first] == [1, 2, 3]


# --- 3.7 top_k respected -------------------------------------------------------


def test_3_7_exactly_top_k_returned_when_available():
    dense = [_dense(i, 1.0 - i * 0.01) for i in range(1, 11)]
    keyword: list[Hit] = []

    result = fuse(dense, keyword, k=K, top_k=3)

    assert len(result) == 3
    assert all(isinstance(hit, FusedHit) for hit in result)
