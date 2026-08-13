"""Reciprocal rank fusion — combine dense and keyword hit lists by rank.

`score = sum(1 / (k + rank))` over whichever of the two lists a chunk
appears in, ranks starting at 1. Deliberately rank-only: cosine similarity
and BM25 live on incomparable scales, and a score-weighted blend would need
corpus-specific tuning constants that have to be retuned whenever the
corpus changes. RRF needs none of that — it never looks at score
magnitude, only position — which is what `test_3_3_...` in
`tests/test_fusion.py` proves by scaling one list's scores and checking
the fused ordering is untouched.

`dense_score` is carried through unchanged, purely for diagnostics; the
relevance gate downstream (`app/rag/retrieve/gate.py`) reads the
reranker's score, never this one — a rank-derived, unitless number cannot
express "nothing here is relevant".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.retrieve.dense import Hit


@dataclass(frozen=True)
class FusedHit:
    chunk_id: int
    fused_score: float
    dense_score: float | None
    keyword_rank: int | None


def fuse(dense: list[Hit], keyword: list[Hit], k: int, top_k: int) -> list[FusedHit]:
    """Merge `dense` and `keyword` hit lists (each already rank-ordered,
    best first) into one RRF-scored list, sorted best-first, truncated to
    `top_k`.

    Deterministic: a chunk's contribution depends only on its rank in each
    input list, and ties in `fused_score` are broken by preserving the
    order chunks were first encountered in (dense list order, then any
    keyword-only chunks in keyword list order) — Python's sort is stable,
    so this falls out of iteration order rather than needing an explicit
    tie-break key.
    """
    dense_rank: dict[int, int] = {}
    dense_score: dict[int, float] = {}
    for rank, hit in enumerate(dense, start=1):
        if hit.chunk_id not in dense_rank:
            dense_rank[hit.chunk_id] = rank
            dense_score[hit.chunk_id] = hit.score

    keyword_rank: dict[int, int] = {}
    for rank, hit in enumerate(keyword, start=1):
        if hit.chunk_id not in keyword_rank:
            keyword_rank[hit.chunk_id] = rank

    ordered_chunk_ids: list[int] = []
    seen: set[int] = set()
    for hit in dense:
        if hit.chunk_id not in seen:
            seen.add(hit.chunk_id)
            ordered_chunk_ids.append(hit.chunk_id)
    for hit in keyword:
        if hit.chunk_id not in seen:
            seen.add(hit.chunk_id)
            ordered_chunk_ids.append(hit.chunk_id)

    fused: list[FusedHit] = []
    for chunk_id in ordered_chunk_ids:
        score = 0.0
        if chunk_id in dense_rank:
            score += 1.0 / (k + dense_rank[chunk_id])
        if chunk_id in keyword_rank:
            score += 1.0 / (k + keyword_rank[chunk_id])
        fused.append(
            FusedHit(
                chunk_id=chunk_id,
                fused_score=score,
                dense_score=dense_score.get(chunk_id),
                keyword_rank=keyword_rank.get(chunk_id),
            )
        )

    fused.sort(key=lambda hit: hit.fused_score, reverse=True)
    return fused[:top_k]
