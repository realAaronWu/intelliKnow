"""Relevance gate — the last check before an answer is generated.

`passes_gate` compares the best *normalized reranker score*
(`RankedHit.relevance`) against `relevance_floor`. It must never read
`fused_score`: RRF's score is rank-derived and unitless (see
`app/rag/retrieve/fuse.py`), so it can express "this ranked better than
that" but never "nothing here is actually relevant" — a top-ranked chunk
from a misrouted space would still have the highest fused score in its
(irrelevant) pool. `relevance` is on a real 0-1 scale precisely so this
comparison is meaningful.

This is the single most important function in the increment: it is what
stops a confident misroute from producing a fluent, wrong, fully-cited
answer. See `tests/test_relevance_gate.py::test_4_4_...` for the
adversarial case this exists to catch.
"""

from __future__ import annotations

from app.rag.retrieve.rerank import RankedHit


def passes_gate(hits: list[RankedHit], floor: float) -> bool:
    if not hits:
        return False
    best_relevance = max(hit.relevance for hit in hits)
    return best_relevance >= floor
