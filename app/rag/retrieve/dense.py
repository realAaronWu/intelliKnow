"""Dense (vector) retrieval — one query vector, many spaces.

`dense_search` fans a single already-embedded query vector out across the
`VectorStore` index of each supplied space and merges the results into one
list ranked by raw cosine score. Every space shares one embedding model
(`app.providers.base.EmbeddingProvider`), so scores from different spaces
land on the same scale and are directly comparable — merging is a plain
sort, not a blend.

The isolation property this rests on — a chunk in a space that was not
supplied cannot appear in the result, at any score — is not something this
module has to implement specially: `VectorStore` keeps one index per space
and is never asked to search a space it was not told to. It is still the
single most important behaviour here, since the whole routing design
(orchestrator picks spaces, retrieval only ever sees those) depends on it
holding. See `tests/test_dense_retrieval.py::test_1_2_...` for the direct
assertion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.rag.vector_store import VectorStore


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk from a single retrieval method.

    Shared between dense and keyword retrieval (`app/rag/retrieve/keyword.py`)
    so `fuse()` can treat both lists uniformly.
    """

    chunk_id: int
    score: float
    source: Literal["dense", "keyword"]


def dense_search(
    query_vector: list[float], spaces: list[str], top_n: int, store: VectorStore
) -> list[Hit]:
    """Search `query_vector` against every space in `spaces` and merge.

    A space with no vectors (never created, or created but empty)
    contributes nothing and raises nothing — `VectorStore.search` already
    guarantees that for a single space; this just does not special-case it
    across many.
    """
    hits: list[Hit] = [
        Hit(chunk_id=chunk_id, score=score, source="dense")
        for slug in spaces
        for chunk_id, score in store.search(slug, query_vector, top_n)
    ]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits
