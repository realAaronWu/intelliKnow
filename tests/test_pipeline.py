"""Test-plan §11 — query pipeline.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §11

Everything is a fake or a real-but-local store: `FakeEmbeddingProvider`
and `FakeLLMProvider` (`tests/doubles.py`) for the AI-shaped calls, a real
`VectorStore` and SQLite `Engine` (both at `tmp_path`) for retrieval, and
a `Reranker` constructed with an injected fake cross-encoder client so no
real model ever loads — the environment constraint the whole increment
runs under (see `tests/test_rerank.py`'s docstring for why).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import insert

from app.config import AppConfig
from app.config_service import ConfigService
from app.db import chunks as chunks_table
from app.db import create_engine_for, documents as documents_table, init_schema
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.pipeline import PipelineDeps, QueryTrace, answer_question
from app.providers.base import ProviderError
from app.rag.generate import ChannelProfile
from app.rag.retrieve.rerank import Reranker
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

DIMENSION = 8

_HR_ALIGNED_VEC = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_GENERAL_VEC = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

_QUESTION = "how much annual leave do I get"

# Equidistant between the HR and General centroids (each dot product is
# 0.7071...) so, at the fixture's centroid_temperature=0.05, the softmax
# splits close to evenly -- confidence well below the 0.70 threshold,
# reliably triggering LLM escalation rather than a centroid-only decision.
_AMBIGUOUS_VEC = [0.70710678, 0.70710678, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_AMBIGUOUS_QUESTION = "what about that thing from before"

_CHANNEL = ChannelProfile(name="test", max_chars=4000, markup="plain", supports_lists=True)


class _FakeCrossEncoder:
    def __init__(self, score_by_chunk: dict[str, float]) -> None:
        self._score_by_chunk = score_by_chunk
        self.predict_calls: list[list[list[str]]] = []

    def predict(self, pairs: list[list[str]]) -> list[float]:
        self.predict_calls.append(pairs)
        return [self._score_by_chunk.get(chunk_text, -10.0) for _q, chunk_text in pairs]


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig.model_validate(
        {
            "embedding": {"model": "fake-embed-model", "dimension": DIMENSION},
            "orchestrator": {
                "confidence_threshold": 0.70,
                "centroid_temperature": 0.05,
                "fallback_space": "general",
                "escalate_to_llm": True,
            },
            "rag": {
                "vector_top_n": 10,
                "keyword_top_n": 10,
                "rrf_k": 60,
                # Matches every `Reranker("fake-model", ...)` constructed in
                # this file: `answer_question` now calls
                # `deps.reranker.set_model(cfg.rag.rerank_model)` on every
                # query (see C2), so a mismatch here would discard the
                # injected fake client and try to load a real one.
                "rerank_model": "fake-model",
                "rerank_candidates": 10,
                "final_top_k": 5,
                "relevance_floor": 0.45,
            },
            "intent_spaces": [
                {
                    "slug": "hr",
                    "name": "HR",
                    "description": "Employee policies, leave, benefits",
                    "keywords": ["leave"],
                },
                {
                    "slug": "general",
                    "name": "General",
                    "description": "Fallback",
                    "keywords": [],
                },
            ],
        }
    )


@pytest.fixture
def embedder(cfg: AppConfig) -> FakeEmbeddingProvider:
    e = FakeEmbeddingProvider(dimension=DIMENSION)
    e.set_vector(_QUESTION, _HR_ALIGNED_VEC)
    hr, general = cfg.intent_spaces
    e.set_vector(f"{hr.name} {hr.description} {' '.join(hr.keywords)}", _HR_ALIGNED_VEC)
    e.set_vector(f"{general.name} {general.description}", _GENERAL_VEC)
    return e


@pytest.fixture
def centroids(embedder: FakeEmbeddingProvider, cfg: AppConfig) -> CentroidIndex:
    return CentroidIndex(embedder, cfg)


@pytest.fixture
def classify_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def generate_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def vector_store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "faiss", DIMENSION)


def _insert_document(engine, intent_slug="hr") -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents_table).values(
                filename="handbook.pdf",
                ext=".pdf",
                size_bytes=100,
                sha256="a" * 64,
                intent_slug=intent_slug,
                status="indexed",
                error_message=None,
                chunk_count=1,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at="2026-08-09T00:00:01Z",
            )
        )
        return result.inserted_primary_key[0]


def _insert_chunk(engine, doc_id: int, intent_slug: str, text: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(chunks_table).values(
                document_id=doc_id,
                intent_slug=intent_slug,
                ordinal=0,
                text=text,
                heading_path="Leave Policy",
                source_ref="p. 1",
                char_count=len(text),
            )
        )
        return result.inserted_primary_key[0]


_CHUNK_TEXT = "Employees receive 25 days of annual leave per year."


def _seed_matching_chunk(engine, vector_store) -> tuple[int, int]:
    doc_id = _insert_document(engine)
    chunk_id = _insert_chunk(engine, doc_id, "hr", _CHUNK_TEXT)
    vector_store.add("hr", [chunk_id], [_HR_ALIGNED_VEC])
    return doc_id, chunk_id


def _deps(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker
) -> PipelineDeps:
    return PipelineDeps(
        engine=engine,
        get_cfg=lambda: cfg,
        embedding=embedder,
        classify_llm=classify_llm,
        generate_llm=generate_llm,
        vector_store=vector_store,
        centroids=centroids,
        reranker=reranker,
    )


# --- 11.1 One embedding call ----------------------------------------------------


def test_11_1_exactly_one_embedding_call_per_query(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.expect_text(f"You get 25 days of leave. [1]")
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    calls_before = len(embedder.calls)
    answer_question(_QUESTION, _CHANNEL, deps)

    assert len(embedder.calls) - calls_before == 1
    assert len(classify_llm.calls) == 0  # confidence is high enough that classify() never escalates


# --- 11.2 / 11.3 Gate rejection -> no_match, zero generation calls, names domain ---


def test_11_2_11_3_gate_rejection_yields_no_match_with_zero_generation_calls(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    _seed_matching_chunk(engine, vector_store)
    # Cross-encoder scores the only candidate far below the floor.
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: -10.0}))
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_QUESTION, _CHANNEL, deps)

    assert outcome.status == "no_match"
    assert len(generate_llm.calls) == 0
    assert "HR" in outcome.answer
    assert outcome.citations == []
    assert outcome.retrieved_doc_ids == []


# --- 11.4 Generation failure -----------------------------------------------------


def test_11_4_generation_failure_yields_failed_status_and_user_message(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.fail_next(ProviderError.backend("model exploded"))
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_QUESTION, _CHANNEL, deps)

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert "model exploded" in outcome.error
    assert "try again" in outcome.answer.lower()
    assert outcome.citations == []


# --- 11.5 Success ------------------------------------------------------------------


def test_11_5_success_returns_verified_citations_and_retrieved_doc_ids(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    doc_id, _chunk_id = _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.expect_text("You get 25 days of annual leave. [1]")
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_QUESTION, _CHANNEL, deps)

    assert outcome.status == "success"
    assert outcome.retrieved_doc_ids == [doc_id]
    assert len(outcome.citations) == 1
    assert outcome.citations[0].document_id == doc_id
    assert "[1]" in outcome.answer
    assert outcome.intent_slug == "hr"
    assert outcome.fallback_used is False
    assert outcome.classified_by == "centroid"


# --- 11.6 Latency recorded ----------------------------------------------------------


def test_11_6_latency_is_recorded_and_plausible(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.expect_text("You get 25 days of annual leave. [1]")
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_QUESTION, _CHANNEL, deps)

    assert outcome.latency_ms >= 0
    assert outcome.latency_ms < 5000


# --- C2 regression: PipelineDeps.cfg must not be a construction-time snapshot ---
#
# `test_10_7_threshold_change_takes_effect_on_next_decision`
# (`tests/test_routing.py`) passes two different `AppConfig` objects
# straight into `decide_spaces`, a pure function — it proves `decide_spaces`
# itself is fine, and nothing more. It cannot catch a `PipelineDeps` that
# hands `answer_question` a `cfg` captured once at construction time,
# because it never goes anywhere near `PipelineDeps` or `answer_question`
# at all. That is exactly why five "without a restart" spec scenarios
# shipped unmet: every existing test in this file, like every test in
# `test_routing.py`, passes `cfg` as a direct argument.
#
# This test drives the real seam instead: one `PipelineDeps`, built once,
# reading from a live `ConfigService` — and a `ConfigService.update()`
# (not a second, separately-constructed `AppConfig`) in between two calls
# to `answer_question` on that same `deps`.


# --- C3: QueryOutcome carries classification.reasoning / .failed through ---
#
# `classification.reasoning` and `classification.failed` were computed by
# `classify()` and then discarded when `answer_question` built its
# `QueryOutcome` -- structurally unable to be logged anywhere downstream.
# These three tests drive each `QueryOutcome`-construction site in
# `answer_question` (success, no_match, and the failed-classification path
# that routes to no_match via fallback) and assert the new
# `QueryOutcome.reasoning` / `.classification_failed` fields actually carry
# `Classification`'s values through, not just exist on the dataclass.


def test_c3_success_outcome_carries_classification_reasoning_through(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    _seed_matching_chunk(engine, vector_store)
    embedder.set_vector(_AMBIGUOUS_QUESTION, _AMBIGUOUS_VEC)
    classify_llm.expect_schema(
        {"slug": "hr", "confidence": 0.95, "reasoning": "mentions leave, an HR topic"}
    )
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.expect_text("You get 25 days of leave. [1]")
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_AMBIGUOUS_QUESTION, _CHANNEL, deps)

    assert outcome.status == "success"
    assert outcome.classified_by == "llm"
    assert outcome.reasoning == "mentions leave, an HR topic"
    assert outcome.classification_failed is False


def test_c3_no_match_outcome_carries_classification_reasoning_through(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    """Even a fallback-routed no_match (confidence below threshold) still
    carries whatever reasoning the LLM gave for its (below-threshold) pick
    -- the field is populated whenever `classify()` returned one, not only
    on the success path.
    """
    _seed_matching_chunk(engine, vector_store)
    embedder.set_vector(_AMBIGUOUS_QUESTION, _AMBIGUOUS_VEC)
    classify_llm.expect_schema(
        {"slug": "hr", "confidence": 0.2, "reasoning": "weak signal, low confidence"}
    )
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: -10.0}))
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_AMBIGUOUS_QUESTION, _CHANNEL, deps)

    assert outcome.status == "no_match"
    assert outcome.reasoning == "weak signal, low confidence"
    assert outcome.classification_failed is False


def test_c3_classification_failure_outcome_records_classification_failed(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    """Classification failure (`spec: query-orchestration` § "Classification
    failure falls back rather than failing") must be visible on the
    `QueryOutcome` it produces, not just swallowed into a generic no_match.
    """
    _seed_matching_chunk(engine, vector_store)
    embedder.set_vector(_AMBIGUOUS_QUESTION, _AMBIGUOUS_VEC)
    classify_llm.fail_next(ProviderError.backend("classifier down"))
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: -10.0}))
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_AMBIGUOUS_QUESTION, _CHANNEL, deps)

    assert outcome.status == "no_match"
    assert outcome.classification_failed is True
    assert outcome.reasoning is None


def test_c2_relevance_floor_change_takes_effect_on_next_query_no_restart(
    tmp_path: Path, engine, embedder, classify_llm, generate_llm, vector_store, centroids
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "embedding": {"model": "fake-embed-model", "dimension": DIMENSION},
                "orchestrator": {
                    "confidence_threshold": 0.70,
                    "centroid_temperature": 0.05,
                    "fallback_space": "general",
                    "escalate_to_llm": True,
                },
                "rag": {
                    "vector_top_n": 10,
                    "keyword_top_n": 10,
                    "rrf_k": 60,
                    "rerank_model": "fake-model",
                    "rerank_candidates": 10,
                    "final_top_k": 5,
                    "relevance_floor": 0.3,
                },
                "intent_spaces": [
                    {
                        "slug": "hr",
                        "name": "HR",
                        "description": "Employee policies, leave, benefits",
                        "keywords": ["leave"],
                    },
                    {"slug": "general", "name": "General", "description": "Fallback", "keywords": []},
                ],
            }
        )
    )
    config_service = ConfigService.load(config_path)

    _seed_matching_chunk(engine, vector_store)
    # sigmoid(0.0) == 0.5 -- clears a 0.3 floor, misses a 0.9 floor.
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 0.0}))
    generate_llm.expect_text("You get 25 days of leave. [1]")

    deps = PipelineDeps(
        engine=engine,
        get_cfg=lambda: config_service.current,
        embedding=embedder,
        classify_llm=classify_llm,
        generate_llm=generate_llm,
        vector_store=vector_store,
        centroids=centroids,
        reranker=reranker,
    )

    before = answer_question(_QUESTION, _CHANNEL, deps)
    assert before.status == "success", before

    config_service.update({"rag": {"relevance_floor": 0.9}})

    after = answer_question(_QUESTION, _CHANNEL, deps)

    assert after.status == "no_match", (
        "the relevance-floor update on the live ConfigService never "
        "reached answer_question -- PipelineDeps is still handing it a "
        f"stale cfg snapshot (outcome: {after})"
    )
    assert len(generate_llm.calls) == 1  # only `before` generated; `after` gated out


def test_c2_reranker_model_change_takes_effect_on_next_query_no_restart(
    tmp_path: Path, engine, embedder, classify_llm, generate_llm, vector_store, centroids, monkeypatch
):
    """`Reranker` bakes `model_name` in at construction — `PipelineDeps`
    holds one `Reranker` instance for the process's life, so a config-only
    model change used to have no seam to travel through at all. Proven
    here the same way `tests/test_rerank.py`'s C2 tests prove `Reranker`
    itself: swap `sentence_transformers.CrossEncoder` for a fake
    constructor and assert it is invoked with the *new* model name after
    a `ConfigService.update()`, with no restart and no new `PipelineDeps`.
    """
    import sys
    import types

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "embedding": {"model": "fake-embed-model", "dimension": DIMENSION},
                "orchestrator": {
                    "confidence_threshold": 0.70,
                    "centroid_temperature": 0.05,
                    "fallback_space": "general",
                    "escalate_to_llm": True,
                },
                "rag": {
                    "vector_top_n": 10,
                    "keyword_top_n": 10,
                    "rrf_k": 60,
                    "rerank_model": "model-v1",
                    "rerank_candidates": 10,
                    "final_top_k": 5,
                    "relevance_floor": 0.0,
                },
                "intent_spaces": [
                    {
                        "slug": "hr",
                        "name": "HR",
                        "description": "Employee policies, leave, benefits",
                        "keywords": ["leave"],
                    },
                    {"slug": "general", "name": "General", "description": "Fallback", "keywords": []},
                ],
            }
        )
    )
    config_service = ConfigService.load(config_path)

    _seed_matching_chunk(engine, vector_store)
    generate_llm.expect_text("You get 25 days of leave. [1]")
    generate_llm.expect_text("You get 25 days of leave. [1]")

    constructed_with: list[str] = []

    class _RecordingCrossEncoder:
        def __init__(self, model_name: str) -> None:
            constructed_with.append(model_name)

        def predict(self, pairs: list[list[str]]) -> list[float]:
            return [5.0 for _ in pairs]

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _RecordingCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    reranker = Reranker("model-v1")  # no injected client -- exercises the real lazy-load path
    deps = PipelineDeps(
        engine=engine,
        get_cfg=lambda: config_service.current,
        embedding=embedder,
        classify_llm=classify_llm,
        generate_llm=generate_llm,
        vector_store=vector_store,
        centroids=centroids,
        reranker=reranker,
    )

    answer_question(_QUESTION, _CHANNEL, deps)
    assert constructed_with == ["model-v1"]

    config_service.update({"rag": {"rerank_model": "model-v2"}})
    answer_question(_QUESTION, _CHANNEL, deps)

    assert constructed_with == ["model-v1", "model-v2"], (
        "the reranker model-name update on the live ConfigService never "
        "reached the Reranker instance held by PipelineDeps"
    )


# --- I3: optional QueryTrace / force_space, so scripts/ask.py can call ------------
# answer_question directly instead of re-implementing the pipeline.
#
# `scripts/ask.py` prints a stage-by-stage trace (classification, routing,
# dense hits, keyword hits, fused order, reranked order, gate decision) that
# `QueryOutcome` alone doesn't carry. Rather than have the demo CLI
# re-implement every stage itself (the I3 defect: any future change to
# `answer_question` would silently stop showing up in the demo), an
# optional `trace: QueryTrace` parameter lets a caller opt into having
# `answer_question` fill in that same intermediate data as it computes it
# for real -- one embedding call, one rerank call, same as any other
# caller. `force_space` is the other piece `scripts/ask.py --space SLUG`
# needs: bypass classification/routing entirely and force retrieval to one
# named space, without inventing a second, parallel way to run the
# pipeline.


def test_i3_trace_captures_every_pre_generation_stage(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.expect_text("You get 25 days of leave. [1]")
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)
    trace = QueryTrace()

    outcome = answer_question(_QUESTION, _CHANNEL, deps, trace=trace)

    assert outcome.status == "success"
    assert trace.query_vector is not None and len(trace.query_vector) == DIMENSION
    assert trace.classification is not None
    assert trace.classification.intent_slug == "hr"
    assert trace.routing is not None
    assert trace.routing.spaces == ["hr"]
    assert trace.dense_hits is not None and len(trace.dense_hits) == 1
    assert trace.keyword_hits is not None
    assert trace.fused is not None and len(trace.fused) == 1
    assert trace.ranked is not None and len(trace.ranked) == 1
    assert trace.gate_passed is True


def test_i3_trace_is_populated_even_when_the_gate_rejects(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    """The no_match path returns early, before context/generation -- the
    trace must still carry everything computed up to that point rather
    than stopping partway.
    """
    _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: -10.0}))
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)
    trace = QueryTrace()

    outcome = answer_question(_QUESTION, _CHANNEL, deps, trace=trace)

    assert outcome.status == "no_match"
    assert trace.ranked is not None and len(trace.ranked) == 1
    assert trace.gate_passed is False


def test_i3_trace_defaults_to_none_and_costs_nothing_when_not_requested(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    """Every other existing caller (the admin API, every other test in this
    file) calls `answer_question` with no `trace` argument at all -- this
    pins that the parameter is optional and changes nothing when omitted.
    """
    _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.expect_text("You get 25 days of leave. [1]")
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)

    outcome = answer_question(_QUESTION, _CHANNEL, deps)

    assert outcome.status == "success"


def test_i3_force_space_bypasses_classification_and_forces_routing(
    engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids
):
    """`--space SLUG` in `scripts/ask.py` forces retrieval to one named
    space and skips classification entirely -- no centroid lookup, no LLM
    escalation call, regardless of what the question would otherwise
    classify as.
    """
    _seed_matching_chunk(engine, vector_store)
    reranker = Reranker("fake-model", client=_FakeCrossEncoder({_CHUNK_TEXT: 5.0}))
    generate_llm.expect_text("You get 25 days of leave. [1]")
    deps = _deps(engine, cfg, embedder, classify_llm, generate_llm, vector_store, centroids, reranker)
    trace = QueryTrace()

    outcome = answer_question(_QUESTION, _CHANNEL, deps, trace=trace, force_space="hr")

    assert outcome.status == "success"
    assert outcome.intent_slug == "hr"
    assert outcome.fallback_used is False
    assert len(classify_llm.calls) == 0
    assert trace.classification is not None
    assert trace.classification.intent_slug == "hr"
    assert trace.routing.spaces == ["hr"]
