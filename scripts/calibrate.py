#!/usr/bin/env python3
"""Bounded calibration sanity check for `orchestrator.centroid_temperature`
and `rag.relevance_floor`.

Usage:

    uv run python scripts/ingest.py tests/fixtures/docs/*.pdf tests/fixtures/docs/*.docx
    uv run python scripts/calibrate.py

**Scope note, read before trusting any number this prints.** Increment
02's labelled question set was deferred, and the shipped corpus is six
synthetic fixtures — nowhere near enough to support a meaningful
accuracy figure, and this script does not attempt to manufacture one. It
performs the bounded sanity check the project owner asked for instead:

1. A clearly-HR question scores high centroid confidence.
2. A deliberately ambiguous question falls below the confidence threshold
   and triggers an escalation attempt.
3. A question about content absent from the corpus is rejected by the
   relevance gate.

It also sweeps `centroid_temperature` and `relevance_floor` over a small
range so the *qualitative* shape of each — sharper temperature raises
confidence, floor separates real from off-topic content — is visible on
real embeddings and a real cross-encoder, not just asserted in a unit
test with pinned vectors. Both numbers are real calibration guesses on
scales introduced by this increment (see `design.md` § "One number to
watch during calibration") and **real calibration is still pending the
deferred golden question set** — this script's numbers are evidence
toward a starting value, not a validated one.

This script never writes `config.yaml` itself: `ConfigService.update()`
round-trips the file through `yaml.safe_dump`, which would silently
discard every hand-written comment in the shipped file (the provider
switching instructions, the `CALIBRATE` markers, the inline channel
formatting) — an acceptable cost for an admin API PATCH, not for a
one-off calibration pass. Reviewing this script's output and hand-editing
the two values is the deliberate extra step.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `python scripts/calibrate.py` puts this script's own directory on
# `sys.path[0]`, not the repo root — see `scripts/ingest.py` for the same
# fix, needed before any `app.*` import.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.config import AppConfig  # noqa: E402
from app.db import create_engine_for, init_schema  # noqa: E402
from app.orchestrator.centroids import CentroidIndex  # noqa: E402
from app.orchestrator.classify import classify  # noqa: E402
from app.rag.retrieve.dense import dense_search  # noqa: E402
from app.rag.retrieve.fuse import fuse  # noqa: E402
from app.rag.retrieve.gate import passes_gate  # noqa: E402
from app.rag.retrieve.keyword import keyword_search  # noqa: E402
from app.rag.retrieve.rerank import RankedHit, Reranker  # noqa: E402
from app.rag.vector_store import VectorStore  # noqa: E402

# --- The bounded sanity-check question set ----------------------------------
# Not a golden set — three hand-picked questions chosen to be unambiguous
# examples of each behaviour this check exists to confirm. See module
# docstring.

HR_QUESTION = "How many days of annual leave do full-time employees get?"
# Deliberately shares no keyword with any configured space's name,
# description, or keyword list (checked against the shipped config: no
# "leave"/"contract"/"expense"/"process"/etc.) — the point is a question
# with no topical anchor at all, not merely an informally vague one.
AMBIGUOUS_QUESTION = "Is there an update on my situation from before?"
OUT_OF_CORPUS_QUESTION = "What is the departure gate for tomorrow's 9am flight to Tokyo?"

TEMPERATURE_SWEEP = [0.01, 0.03, 0.05, 0.10, 0.20, 0.50, 1.0]
FLOOR_SWEEP = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]


def _spaces_with_chunks(engine) -> list[str]:
    """Every intent slug that actually has indexed chunks.

    The floor sweep cares about relevance *scores*, not routing — forcing
    retrieval to search wherever the ingested fixtures actually landed
    (rather than trusting classify()/route() to pick the right space)
    decouples this check from document intent assignment, which this
    corpus's ingest run may or may not have gotten right (assigning
    intent at ingest needs a live LLM; this script does not require one).
    """
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT intent_slug FROM chunks")).all()
    return [row[0] for row in rows]


def _rerank_for(question: str, spaces: list[str], cfg: AppConfig, engine, vector_store, embedding, reranker) -> list[RankedHit]:
    """Run retrieval-through-rerank for `question` and return the ranked
    hits -- callers read `.relevance` off them directly (for display) and
    pass the list straight to `passes_gate` (the real gate function,
    imported rather than re-implemented) for pass/fail decisions, so this
    script's gate check can never silently drift from what
    `app/orchestrator/pipeline.py::answer_question` actually does.
    """
    [query_vector] = embedding.embed([question])
    dense_hits = dense_search(query_vector, spaces, cfg.rag.vector_top_n, vector_store)
    keyword_hits = keyword_search(question, spaces, cfg.rag.keyword_top_n, engine)
    fused = fuse(dense_hits, keyword_hits, cfg.rag.rrf_k, cfg.rag.rerank_candidates)
    return reranker.rerank(question, fused, engine, cfg.rag.final_top_k)


def _best_relevance(ranked: list[RankedHit]) -> float:
    if not ranked:
        return 0.0
    return max(hit.relevance for hit in ranked)


def main() -> int:
    application = bootstrap()
    cfg = application.config

    engine = create_engine_for(Path(cfg.storage.sqlite_path))
    init_schema(engine)
    vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)

    with engine.connect() as conn:
        chunk_count = conn.execute(text("SELECT count(*) FROM chunks")).scalar_one()
    if chunk_count == 0:
        print(
            "No indexed chunks found. Run scripts/ingest.py against the fixture "
            "corpus first:\n\n"
            "    uv run python scripts/ingest.py tests/fixtures/docs/*.pdf "
            "tests/fixtures/docs/*.docx\n"
        )
        return 1

    print("=== Sanity check (current config: "
          f"centroid_temperature={cfg.orchestrator.centroid_temperature}, "
          f"confidence_threshold={cfg.orchestrator.confidence_threshold}, "
          f"relevance_floor={cfg.rag.relevance_floor}) ===\n")

    centroids = CentroidIndex(application.embedding, cfg)

    # 1. Clearly-HR question scores high centroid confidence.
    hr_vector = application.embedding.embed([HR_QUESTION])[0]
    hr_slug, hr_confidence = centroids.top(hr_vector)
    print(f"1. HR question: {HR_QUESTION!r}")
    print(f"   top space: {hr_slug!r}  confidence: {hr_confidence:.4f}")
    print(f"   {'PASS' if hr_slug == 'hr' and hr_confidence >= cfg.orchestrator.confidence_threshold else 'CHECK'}"
          f" against threshold {cfg.orchestrator.confidence_threshold}\n")

    # 2. Ambiguous question falls below threshold and escalates.
    ambiguous_vector = application.embedding.embed([AMBIGUOUS_QUESTION])[0]
    classification = classify(
        AMBIGUOUS_QUESTION, ambiguous_vector, cfg, centroids, application.classify_llm
    )
    print(f"2. Ambiguous question: {AMBIGUOUS_QUESTION!r}")
    print(f"   classified_by: {classification.classified_by}  "
          f"confidence: {classification.confidence:.4f}  failed: {classification.failed}")
    if classification.classified_by == "llm":
        print("   escalation was attempted (centroid confidence was below threshold)")
        if classification.failed:
            print("   escalation call failed — no local LLM server reachable during this "
                  "run; this still demonstrates escalation firing, just not resolving")
    else:
        print("   CHECK: centroid alone was confident enough that escalation never fired")
    print()

    # 3. Out-of-corpus question rejected by the gate.
    spaces = _spaces_with_chunks(engine)
    reranker = Reranker(cfg.rag.rerank_model)
    positive_ranked = _rerank_for(
        HR_QUESTION, spaces, cfg, engine, vector_store, application.embedding, reranker
    )
    negative_ranked = _rerank_for(
        OUT_OF_CORPUS_QUESTION, spaces, cfg, engine, vector_store, application.embedding, reranker
    )
    positive_relevance = _best_relevance(positive_ranked)
    negative_relevance = _best_relevance(negative_ranked)
    print(f"3. Out-of-corpus question: {OUT_OF_CORPUS_QUESTION!r}")
    print(f"   best relevance (in-corpus HR question):  {positive_relevance:.4f}")
    print(f"   best relevance (out-of-corpus question):  {negative_relevance:.4f}")
    gate_rejects = not passes_gate(negative_ranked, cfg.rag.relevance_floor)
    print(f"   floor {cfg.rag.relevance_floor}: out-of-corpus question "
          f"{'is correctly rejected' if gate_rejects else 'INCORRECTLY PASSES'} the gate\n")

    # --- Temperature sweep ----------------------------------------------------
    print("=== Temperature sweep (HR vs. ambiguous question confidence) ===")
    print(f"{'temperature':>12} | {'HR confidence':>14} | {'ambiguous confidence':>20}")
    for temperature in TEMPERATURE_SWEEP:
        raw = cfg.model_dump(mode="json")
        raw["orchestrator"]["centroid_temperature"] = temperature
        swept_cfg = AppConfig.model_validate(raw)
        swept_centroids = CentroidIndex(application.embedding, swept_cfg)
        _, hr_conf_t = swept_centroids.top(hr_vector)
        _, amb_conf_t = swept_centroids.top(ambiguous_vector)
        print(f"{temperature:>12.2f} | {hr_conf_t:>14.4f} | {amb_conf_t:>20.4f}")
    print()

    # --- Floor sweep ------------------------------------------------------------
    print("=== Floor sweep (pass/fail at each candidate floor) ===")
    print(f"{'floor':>8} | {'HR question':>12} | {'out-of-corpus question':>24}")
    for floor in FLOOR_SWEEP:
        hr_pass = passes_gate(positive_ranked, floor)
        neg_pass = passes_gate(negative_ranked, floor)
        print(f"{floor:>8.2f} | {('pass' if hr_pass else 'FAIL'):>12} | "
              f"{('pass (bad)' if neg_pass else 'reject (good)'):>24}")
    print()

    print(
        "Real calibration against increment 02's golden question set is still "
        "pending — see the recorded report for the values chosen from this "
        "bounded sanity check and why."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
