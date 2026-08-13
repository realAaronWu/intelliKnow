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
from sqlalchemy import insert

from app.config import AppConfig
from app.db import chunks as chunks_table
from app.db import create_engine_for, documents as documents_table, init_schema
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.pipeline import PipelineDeps, answer_question
from app.providers.base import ProviderError
from app.rag.generate import ChannelProfile
from app.rag.retrieve.rerank import Reranker
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

DIMENSION = 8

_HR_ALIGNED_VEC = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_GENERAL_VEC = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

_QUESTION = "how much annual leave do I get"

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
        cfg=cfg,
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
