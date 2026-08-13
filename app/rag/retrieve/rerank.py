"""Cross-encoder reranking of the fused candidate pool.

`Reranker` wraps a `sentence-transformers` `CrossEncoder`
(`cross-encoder/ms-marco-MiniLM-L-6-v2` by default — see
`app.config.RAGConfig.rerank_model`), loaded lazily on first use and cached
on the instance so the model is loaded once, not once per query — mirroring
the lazy-load pattern in `app/providers/local_embedding.py`. Callers that
want it loaded at startup rather than on the first query should construct
`Reranker` and call `.score()` (or `.rerank()`) once during wiring; this
module does not do that itself, since it has no opinion on when "startup"
is.

RRF fusion produces only a rank-derived score, which cannot express how
relevant a chunk actually is — that is what the cross-encoder is for.
`rerank()` scores the whole candidate pool the fused stage handed it in
one batch (never once per candidate — that would be one HTTP-shaped call
per chunk against a model this module has no reason to think is cheap to
invoke repeatedly), reorders by that score, and keeps the raw score
alongside a sigmoid-normalized `relevance` in 0-1 that
`app/rag/retrieve/gate.py` reads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db import chunks as chunks_table
from app.rag.retrieve.fuse import FusedHit


@dataclass(frozen=True)
class RankedHit(FusedHit):
    rerank_score: float
    relevance: float


def _sigmoid(x: float) -> float:
    # Guard against overflow on very negative/positive raw scores rather
    # than trusting `math.exp` not to raise `OverflowError`.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _load_chunk_texts(engine: Engine, chunk_ids: list[int]) -> dict[int, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(chunks_table.c.id, chunks_table.c.text).where(
                chunks_table.c.id.in_(chunk_ids)
            )
        ).all()
    return {row.id: row.text for row in rows}


class Reranker:
    """Scores question/chunk pairs with a cross-encoder model.

    `client`, when supplied, stands in for the real `CrossEncoder` — every
    unit test in `tests/test_rerank.py` injects one so nothing downloads
    or runs the real model. Left `None`, the real model loads lazily on
    first use and is cached for the lifetime of this instance.
    """

    def __init__(self, model_name: str, client: Any | None = None) -> None:
        self._model_name = model_name
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import sentence_transformers

            self._client = sentence_transformers.CrossEncoder(self._model_name)
        return self._client

    def set_model(self, model_name: str) -> None:
        """Point this instance at `model_name`, discarding any cached
        client for a different one so the next `.score()`/`.rerank()` call
        lazily loads it instead of silently continuing to score with the
        old model.

        A caller holding a live config (`app/orchestrator/pipeline.py::
        answer_question`) calls this on every query — a no-op, cheap
        string comparison, when the name hasn't changed (the common case),
        so the cached model survives across queries exactly as before this
        method existed; `tests/test_rerank.py`'s "model loads once, not
        reloaded per query" guarantee still holds.
        """
        if model_name != self._model_name:
            self._model_name = model_name
            self._client = None

    def score(self, question: str, chunks: list[str]) -> list[float]:
        """Score every (question, chunk) pair in one batch call."""
        if not chunks:
            return []
        client = self._get_client()
        pairs = [[question, chunk] for chunk in chunks]
        raw_scores = client.predict(pairs)
        return [float(s) for s in raw_scores]

    def rerank(
        self, question: str, hits: list[FusedHit], engine: Engine, top_k: int
    ) -> list[RankedHit]:
        """Rerank the fused candidate pool `hits` and return the top `top_k`.

        Fewer candidates than the configured pool size is not an error —
        whatever `hits` holds is scored and returned (truncated to
        `top_k`, which may itself exceed `len(hits)`).
        """
        if not hits:
            return []

        chunk_ids = [hit.chunk_id for hit in hits]
        texts_by_id = _load_chunk_texts(engine, chunk_ids)
        chunk_texts = [texts_by_id[chunk_id] for chunk_id in chunk_ids]

        raw_scores = self.score(question, chunk_texts)

        ranked = [
            RankedHit(
                chunk_id=hit.chunk_id,
                fused_score=hit.fused_score,
                dense_score=hit.dense_score,
                keyword_rank=hit.keyword_rank,
                rerank_score=raw_score,
                relevance=_sigmoid(raw_score),
            )
            for hit, raw_score in zip(hits, raw_scores)
        ]
        ranked.sort(key=lambda hit: hit.rerank_score, reverse=True)
        return ranked[:top_k]
