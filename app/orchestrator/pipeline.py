"""The assembled read path: classify -> route -> retrieve -> answer.

`answer_question` is the single function that wires together every stage
built across this increment — `app/orchestrator/classify.py` and
`app/orchestrator/route.py` from this plan, `app/rag/retrieve/*` from
plan 03/this plan, and `app/rag/{context,generate,citations,format}.py`
from earlier in this plan — into the one request/response shape both the
admin test-query endpoint (`app/api/query.py`) and, eventually, a chat
channel adapter call.

**Wiring resolved for this pipeline** (previously an open question):
fusion produces `cfg.rag.rerank_candidates` — the candidate *pool* the
reranker scores — and the reranker itself returns `cfg.rag.final_top_k`,
the number of chunks that actually reach context assembly. `rerank_candidates`
is deliberately >= `final_top_k` so the cross-encoder has more than the
final answer set to choose from.

Two properties matter more than the rest of the plumbing:

- **Exactly one embedding call per query.** `query_vector` is computed
  once, here, and handed to both `classify()` (for centroid scoring) and
  `dense_search()` (for vector retrieval) — neither of those calls the
  embedder itself. See `spec: query-orchestration` § "Query embedding is
  reused".
- **Zero generation calls when the gate rejects.** `passes_gate` is
  checked *before* `build_context`/`generate_answer` are ever reached, so
  a no-match costs no model call at all beyond the one embedding call —
  `spec: knowledge-retrieval` § "Relevance gate".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.engine import Engine

from app.config import AppConfig
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.classify import classify
from app.orchestrator.route import decide_spaces
from app.providers.base import EmbeddingProvider, LLMProvider, ProviderError
from app.rag.citations import Citation, verify_citations
from app.rag.context import build_context
from app.rag.format import format_for_channel
from app.rag.generate import ChannelProfile, generate_answer
from app.rag.retrieve.dense import dense_search
from app.rag.retrieve.fuse import fuse
from app.rag.retrieve.gate import passes_gate
from app.rag.retrieve.keyword import keyword_search
from app.rag.retrieve.rerank import Reranker
from app.rag.vector_store import VectorStore

_FAILURE_MESSAGE = (
    "Sorry, I couldn't generate an answer just now. Please try again in a moment."
)


@dataclass
class PipelineDeps:
    """Everything `answer_question` needs, assembled once by the caller
    from `bootstrap()`'s `Application` plus a real (or, in tests, fake)
    `Engine` / `VectorStore` / `CentroidIndex` / `Reranker`.

    `centroids` and `reranker` are held here rather than constructed
    per-call so their (comparatively expensive) setup — embedding every
    space's centroid text, loading the cross-encoder model — happens once,
    not once per query.
    """

    engine: Engine
    cfg: AppConfig
    embedding: EmbeddingProvider
    classify_llm: LLMProvider
    generate_llm: LLMProvider
    vector_store: VectorStore
    centroids: CentroidIndex
    reranker: Reranker


@dataclass(frozen=True)
class QueryOutcome:
    answer: str
    citations: list[Citation]
    intent_slug: str
    confidence: float
    classified_by: Literal["centroid", "llm"]
    fallback_used: bool
    status: Literal["success", "no_match", "failed"]
    retrieved_doc_ids: list[int]
    latency_ms: int
    error: str | None


def _elapsed_ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))


def _domain_label(spaces: list[str], cfg: AppConfig) -> str:
    """A human-readable name for the domain(s) retrieval searched, for the
    no-match message. A single routed space names itself; the fallback's
    "every space" case is named generically rather than as a list, since
    a comma-joined list of every configured space is not, in fact, a
    domain a user would recognise.
    """
    if len(spaces) == 1:
        [slug] = spaces
        for space in cfg.intent_spaces:
            if space.slug == slug:
                return space.name
        return slug
    return "the knowledge base"


def _no_match_outcome(
    routing_spaces: list[str],
    cfg: AppConfig,
    channel: ChannelProfile,
    *,
    intent_slug: str,
    confidence: float,
    classified_by: Literal["centroid", "llm"],
    fallback_used: bool,
    latency_ms: int,
) -> QueryOutcome:
    domain = _domain_label(routing_spaces, cfg)
    message = (
        f"I couldn't find anything in {domain} that answers that question. "
        "The knowledge base doesn't appear to cover this topic."
    )
    formatted = format_for_channel(message, [], channel)
    return QueryOutcome(
        answer=formatted,
        citations=[],
        intent_slug=intent_slug,
        confidence=confidence,
        classified_by=classified_by,
        fallback_used=fallback_used,
        status="no_match",
        retrieved_doc_ids=[],
        latency_ms=latency_ms,
        error=None,
    )


def answer_question(question: str, channel: ChannelProfile, deps: PipelineDeps) -> QueryOutcome:
    """Run `question` through the full read path and return a `QueryOutcome`.

    Never raises for an ordinary provider failure or a routing miss — the
    only exceptions that propagate are programming errors (a malformed
    `deps`, a database that doesn't exist), matching every other stage's
    "degrade to a recorded status, never crash the caller" contract.
    """
    start = time.perf_counter()
    cfg = deps.cfg

    [query_vector] = deps.embedding.embed([question])

    classification = classify(question, query_vector, cfg, deps.centroids, deps.classify_llm)
    routing = decide_spaces(classification, cfg)

    dense_hits = dense_search(query_vector, routing.spaces, cfg.rag.vector_top_n, deps.vector_store)
    keyword_hits = keyword_search(question, routing.spaces, cfg.rag.keyword_top_n, deps.engine)
    fused = fuse(dense_hits, keyword_hits, cfg.rag.rrf_k, cfg.rag.rerank_candidates)
    ranked = deps.reranker.rerank(question, fused, deps.engine, cfg.rag.final_top_k)

    if not passes_gate(ranked, cfg.rag.relevance_floor):
        return _no_match_outcome(
            routing.spaces,
            cfg,
            channel,
            intent_slug=routing.logged_slug,
            confidence=classification.confidence,
            classified_by=classification.classified_by,
            fallback_used=routing.fallback_used,
            latency_ms=_elapsed_ms(start),
        )

    bundle = build_context(ranked, deps.engine, cfg.rag)

    try:
        raw_answer = generate_answer(question, bundle, channel, deps.generate_llm)
    except ProviderError as exc:
        return QueryOutcome(
            answer=format_for_channel(_FAILURE_MESSAGE, [], channel),
            citations=[],
            intent_slug=routing.logged_slug,
            confidence=classification.confidence,
            classified_by=classification.classified_by,
            fallback_used=routing.fallback_used,
            status="failed",
            retrieved_doc_ids=[],
            latency_ms=_elapsed_ms(start),
            error=str(exc),
        )

    cleaned_answer, citations = verify_citations(raw_answer, bundle)
    formatted_answer = format_for_channel(cleaned_answer, citations, channel)
    retrieved_doc_ids = list(dict.fromkeys(source.document_id for source in bundle.sources))

    return QueryOutcome(
        answer=formatted_answer,
        citations=citations,
        intent_slug=routing.logged_slug,
        confidence=classification.confidence,
        classified_by=classification.classified_by,
        fallback_used=routing.fallback_used,
        status="success",
        retrieved_doc_ids=retrieved_doc_ids,
        latency_ms=_elapsed_ms(start),
        error=None,
    )
