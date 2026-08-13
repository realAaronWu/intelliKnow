#!/usr/bin/env python3
"""Read-path demo CLI: run one question through the full pipeline and
print every stage of the trace, not just the final answer.

Usage:

    uv run python scripts/ingest.py tests/fixtures/docs/*.pdf tests/fixtures/docs/*.docx
    uv run python scripts/ask.py "How many days of annual leave do I get?"
    uv run python scripts/ask.py "What's the NDA term?" --space legal

Works against whatever `data/` already holds — run `scripts/ingest.py`
against the fixture corpus first, or this will classify and route
correctly but retrieve nothing. Uses the shipped default config
(`llm.provider: local`, `embedding.provider: local`), so no API key is
required and no real network-billed API call is made; a local LLM
server (Ollama) is only needed for escalation and answer generation —
without one, classification still runs (centroid confidence is always
computed; escalation just fails gracefully into the fallback path) and
generation will report a failure rather than crash.

`--space SLUG` forces retrieval to a specific intent space and skips
classification entirely — useful for isolating retrieval behaviour (dense
vs. keyword vs. fused vs. reranked ordering) from classification.

**Environment note.** This is the first place in the whole increment
where real FAISS search (`app/rag/vector_store.py`) and a real
`sentence-transformers` `CrossEncoder` (`app/rag/retrieve/rerank.py`) run
in the same process as production would — `tests/test_rerank.py`'s
docstring documents a real risk on this platform: initializing
`sentence_transformers`/torch in a process that has already initialized
faiss's OpenMP runtime can abort the interpreter outright, not raise a
catchable exception. `scripts/calibrate.py` exercised exactly this
combination once, successfully, while producing this task's calibration
report — encouraging, but one successful run in one environment is not a
general guarantee; if this script aborts instead of printing a trace,
that is the known risk manifesting, not a bug in this file.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# `python scripts/ask.py` puts this script's own directory on
# `sys.path[0]`, not the repo root — see `scripts/ingest.py` for the same
# fix, needed before any `app.*` import.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.db import chunks as chunks_table  # noqa: E402
from app.db import create_engine_for, documents as documents_table, init_schema  # noqa: E402
from app.orchestrator.centroids import CentroidIndex  # noqa: E402
from app.orchestrator.classify import Classification, classify  # noqa: E402
from app.orchestrator.route import decide_spaces  # noqa: E402
from app.providers.base import ProviderError  # noqa: E402
from app.rag.citations import verify_citations  # noqa: E402
from app.rag.context import build_context  # noqa: E402
from app.rag.format import format_for_channel  # noqa: E402
from app.rag.generate import ChannelProfile, generate_answer  # noqa: E402
from app.rag.retrieve.dense import dense_search  # noqa: E402
from app.rag.retrieve.fuse import fuse  # noqa: E402
from app.rag.retrieve.gate import passes_gate  # noqa: E402
from app.rag.retrieve.keyword import keyword_search  # noqa: E402
from app.rag.retrieve.rerank import Reranker  # noqa: E402
from app.rag.vector_store import VectorStore  # noqa: E402

_PREVIEW_CHARS = 80

DEMO_CHANNEL = ChannelProfile(
    name="cli-demo", max_chars=4000, markup="plain", supports_lists=True
)


def _build_deps():
    application = bootstrap()
    cfg = application.config

    engine = create_engine_for(Path(cfg.storage.sqlite_path))
    init_schema(engine)
    vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)
    centroids = CentroidIndex(application.embedding, cfg)
    reranker = Reranker(cfg.rag.rerank_model)

    return application, cfg, engine, vector_store, centroids, reranker


def _chunk_previews(engine, chunk_ids: set[int]) -> dict[int, str]:
    if not chunk_ids:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                chunks_table.c.id,
                chunks_table.c.text,
                documents_table.c.filename,
            )
            .select_from(
                chunks_table.join(
                    documents_table, chunks_table.c.document_id == documents_table.c.id
                )
            )
            .where(chunks_table.c.id.in_(chunk_ids))
        ).all()
    previews = {}
    for row in rows:
        preview = row.text.replace("\n", " ")[:_PREVIEW_CHARS]
        ellipsis = "..." if len(row.text) > _PREVIEW_CHARS else ""
        previews[row.id] = f"{row.filename}: {preview}{ellipsis}"
    return previews


def _domain_label(spaces: list[str], cfg) -> str:
    if len(spaces) == 1:
        [slug] = spaces
        for space in cfg.intent_spaces:
            if space.slug == slug:
                return space.name
        return slug
    return "the knowledge base"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", help="the question to ask")
    parser.add_argument(
        "--space",
        help="force this intent space and bypass classification entirely",
    )
    args = parser.parse_args(argv)

    application, cfg, engine, vector_store, centroids, reranker = _build_deps()
    start = time.perf_counter()

    print(f"Question: {args.question!r}")
    [query_vector] = application.embedding.embed([args.question])

    # --- Classification -------------------------------------------------
    print("\n--- Classification ---")
    if args.space:
        valid_slugs = {space.slug for space in cfg.intent_spaces}
        if args.space not in valid_slugs:
            print(
                f"error: {args.space!r} is not a configured intent space "
                f"({', '.join(sorted(valid_slugs))})",
                file=sys.stderr,
            )
            return 2
        print(f"bypassed via --space {args.space!r}")
        classification = Classification(
            intent_slug=args.space,
            confidence=1.0,
            classified_by="centroid",
            reasoning=None,
            failed=False,
        )
    else:
        classification = classify(
            args.question, query_vector, cfg, centroids, application.classify_llm
        )
        print(f"detected space:  {classification.intent_slug}")
        print(f"confidence:      {classification.confidence:.4f}")
        print(f"classified_by:   {classification.classified_by}")
        if classification.reasoning:
            print(f"reasoning:       {classification.reasoning}")
        if classification.failed:
            print("(escalation failed — fell back)")

    # --- Routing ----------------------------------------------------------
    routing = decide_spaces(classification, cfg)
    print("\n--- Routing ---")
    print(f"spaces searched: {', '.join(routing.spaces)}")
    print(f"fallback used:   {routing.fallback_used}")

    # --- Retrieval ----------------------------------------------------------
    dense_hits = dense_search(query_vector, routing.spaces, cfg.rag.vector_top_n, vector_store)
    keyword_hits = keyword_search(args.question, routing.spaces, cfg.rag.keyword_top_n, engine)
    all_chunk_ids = {h.chunk_id for h in dense_hits} | {h.chunk_id for h in keyword_hits}
    previews = _chunk_previews(engine, all_chunk_ids)

    print(f"\n--- Dense hits ({len(dense_hits)}) ---")
    for hit in dense_hits:
        print(f"  [{hit.score:.4f}] chunk {hit.chunk_id}  {previews.get(hit.chunk_id, '')}")

    print(f"\n--- Keyword hits ({len(keyword_hits)}) ---")
    for hit in keyword_hits:
        print(f"  [{hit.score:.4f}] chunk {hit.chunk_id}  {previews.get(hit.chunk_id, '')}")

    fused = fuse(dense_hits, keyword_hits, cfg.rag.rrf_k, cfg.rag.rerank_candidates)
    print(f"\n--- Fused order ({len(fused)}) ---")
    for hit in fused:
        print(f"  [{hit.fused_score:.4f}] chunk {hit.chunk_id}  {previews.get(hit.chunk_id, '')}")

    ranked = reranker.rerank(args.question, fused, engine, cfg.rag.final_top_k)
    print(f"\n--- Reranked order ({len(ranked)}) ---")
    for hit in ranked:
        print(
            f"  [relevance={hit.relevance:.4f} raw={hit.rerank_score:.4f}] "
            f"chunk {hit.chunk_id}  {previews.get(hit.chunk_id, '')}"
        )

    # --- Gate ----------------------------------------------------------------
    best_relevance = max((hit.relevance for hit in ranked), default=0.0)
    gate_passed = passes_gate(ranked, cfg.rag.relevance_floor)
    print("\n--- Gate ---")
    print(f"best relevance: {best_relevance:.4f}   floor: {cfg.rag.relevance_floor}")
    print(f"decision: {'PASS' if gate_passed else 'FAIL — no answer generated'}")

    if not gate_passed:
        domain = _domain_label(routing.spaces, cfg)
        print("\n--- Result ---")
        print(
            f"No match: nothing in {domain} was relevant enough to answer "
            "this question. No generation call was made."
        )
        latency_ms = int(round((time.perf_counter() - start) * 1000))
        print(f"\nLatency: {latency_ms} ms")
        return 0

    # --- Context, generation, citations, formatting --------------------------
    bundle = build_context(ranked, engine, cfg.rag)
    try:
        raw_answer = generate_answer(args.question, bundle, DEMO_CHANNEL, application.generate_llm)
    except ProviderError as exc:
        print("\n--- Result ---")
        print(f"Generation failed: {exc}")
        latency_ms = int(round((time.perf_counter() - start) * 1000))
        print(f"\nLatency: {latency_ms} ms")
        return 1

    cleaned_answer, citations = verify_citations(raw_answer, bundle)
    formatted_answer = format_for_channel(cleaned_answer, citations, DEMO_CHANNEL)

    print("\n--- Answer ---")
    print(formatted_answer)

    print("\n--- Citations ---")
    if not citations:
        print("  (none)")
    for citation in citations:
        ref = f" ({citation.source_ref})" if citation.source_ref else ""
        print(f"  {citation.document_title}{ref}")

    latency_ms = int(round((time.perf_counter() - start) * 1000))
    print(f"\nLatency: {latency_ms} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
