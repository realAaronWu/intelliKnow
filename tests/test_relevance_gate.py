"""Test-plan §4 — relevance gate.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §4

`passes_gate` is what stops a confident misroute from producing a fluent,
wrong, fully-cited answer: it compares the best *normalized reranker
score* (`RankedHit.relevance`) against `relevance_floor`, and must never
read `fused_score` — a rank-derived, unitless number that cannot express
"nothing here is actually relevant". Test 4.4 constructs that failure mode
directly: a hit ranked first by fusion (highest `fused_score` in the pool)
but with a sub-floor `relevance`. A gate implemented against the fused
score passes every other test in this file and fails only that one — which
is exactly why it exists.
"""

from __future__ import annotations

from app.rag.retrieve.gate import passes_gate
from app.rag.retrieve.rerank import RankedHit

FLOOR = 0.45


def _ranked(chunk_id: int, fused_score: float, relevance: float) -> RankedHit:
    return RankedHit(
        chunk_id=chunk_id,
        fused_score=fused_score,
        dense_score=None,
        keyword_rank=None,
        rerank_score=0.0,
        relevance=relevance,
    )


# --- 4.1 Best normalized reranker score above floor passes ---------------------


def test_4_1_best_relevance_above_floor_passes():
    hits = [_ranked(1, fused_score=0.01, relevance=0.9), _ranked(2, fused_score=0.5, relevance=0.1)]

    assert passes_gate(hits, floor=FLOOR) is True


# --- 4.2 Best below floor fails --------------------------------------------------


def test_4_2_best_relevance_below_floor_fails():
    hits = [_ranked(1, fused_score=0.5, relevance=0.2), _ranked(2, fused_score=0.3, relevance=0.1)]

    assert passes_gate(hits, floor=FLOOR) is False


# --- 4.3 Exactly at floor passes ---------------------------------------------------


def test_4_3_exactly_at_floor_passes():
    hits = [_ranked(1, fused_score=0.1, relevance=FLOOR)]

    assert passes_gate(hits, floor=FLOOR) is True


# --- 4.4 Gate ignores the fused score — the load-bearing test --------------------


def test_4_4_high_fused_rank_with_sub_floor_relevance_fails():
    """A gate reading `fused_score` instead of `relevance` would pass this.

    `hit` is the single, top-ranked (by fusion) candidate in the pool —
    highest `fused_score` of anything present — but the cross-encoder says
    it is not actually relevant. The gate must reject.
    """
    misrouted_top_hit = _ranked(1, fused_score=100.0, relevance=0.05)
    lower_fused_but_still_below_floor = _ranked(2, fused_score=0.001, relevance=0.2)
    hits = [misrouted_top_hit, lower_fused_but_still_below_floor]

    assert passes_gate(hits, floor=FLOOR) is False


# --- 4.5 Empty hits fails -----------------------------------------------------------


def test_4_5_empty_hits_fails():
    assert passes_gate([], floor=FLOOR) is False
