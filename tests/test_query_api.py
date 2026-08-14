"""Test-plan §12 — admin test-query endpoint.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §12

`TestClient` drives the FastAPI app the same way `tests/test_documents_api.py`
does, built from `create_app(deps, pipeline_deps)` with every dependency a
fake or a tmp-path store — no real API call anywhere in this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.config import AppConfig
from app.db import chunks as chunks_table
from app.db import create_engine_for, documents as documents_table, init_schema
from app.ingest.worker import IngestDeps
from app.main import create_app
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.pipeline import PipelineDeps
from app.providers.base import ProviderError
from app.rag.index_writer import IndexWriter
from app.rag.retrieve.rerank import Reranker
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

DIMENSION = 8
_HR_VEC = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_GENERAL_VEC = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_QUESTION = "how much annual leave do I get"
_CHUNK_TEXT = "Employees receive 25 days of annual leave per year."


class _FakeCrossEncoder:
    def __init__(self, score_by_chunk: dict[str, float]) -> None:
        self._score_by_chunk = score_by_chunk

    def predict(self, pairs):
        return [self._score_by_chunk.get(chunk_text, -10.0) for _q, chunk_text in pairs]


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "faiss", DIMENSION)


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "embedding": {"model": "fake-embed-model", "dimension": DIMENSION},
            "orchestrator": {
                "confidence_threshold": 0.70,
                "centroid_temperature": 0.05,
                "fallback_space": "general",
            },
            "rag": {
                "vector_top_n": 10,
                "keyword_top_n": 10,
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
            "storage": {
                "faiss_dir": str(tmp_path / "faiss"),
                "sqlite_path": str(tmp_path / "intelliknow.db"),
                "upload_dir": str(tmp_path / "uploads"),
            },
        }
    )


@pytest.fixture
def embedder(cfg: AppConfig) -> FakeEmbeddingProvider:
    e = FakeEmbeddingProvider(dimension=DIMENSION)
    e.set_vector(_QUESTION, _HR_VEC)
    hr, general = cfg.intent_spaces
    e.set_vector(f"{hr.name} {hr.description} {' '.join(hr.keywords)}", _HR_VEC)
    e.set_vector(f"{general.name} {general.description}", _GENERAL_VEC)
    return e


@pytest.fixture
def classify_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def generate_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def index_writer(engine, store, embedder) -> IndexWriter:
    return IndexWriter(engine, store, embedder)


@pytest.fixture
def ingest_deps(engine, cfg, classify_llm, embedder, store, index_writer) -> IngestDeps:
    return IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=classify_llm,
        embedding=embedder,
        vector_store=store,
        index_writer=index_writer,
    )


def _reranker(score_by_chunk: dict[str, float]) -> Reranker:
    return Reranker("fake-model", client=_FakeCrossEncoder(score_by_chunk))


def _pipeline_deps(engine, cfg, embedder, classify_llm, generate_llm, store, reranker) -> PipelineDeps:
    centroids = CentroidIndex(embedder, cfg)
    return PipelineDeps(
        engine=engine,
        get_cfg=lambda: cfg,
        embedding=embedder,
        classify_llm=classify_llm,
        generate_llm=generate_llm,
        vector_store=store,
        centroids=centroids,
        reranker=reranker,
    )


def _insert_document(engine) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents_table).values(
                filename="handbook.pdf",
                ext=".pdf",
                size_bytes=100,
                sha256="a" * 64,
                intent_slug="hr",
                status="indexed",
                error_message=None,
                chunk_count=1,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at="2026-08-09T00:00:01Z",
            )
        )
        return result.inserted_primary_key[0]


def _insert_chunk(engine, doc_id: int) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(chunks_table).values(
                document_id=doc_id,
                intent_slug="hr",
                ordinal=0,
                text=_CHUNK_TEXT,
                heading_path="Leave Policy",
                source_ref="p. 1",
                char_count=len(_CHUNK_TEXT),
            )
        )
        return result.inserted_primary_key[0]


# --- 12.1 Returns full result ----------------------------------------------------


def test_12_1_returns_intent_confidence_answer_sources_latency(
    engine, cfg, embedder, classify_llm, generate_llm, store, ingest_deps
):
    doc_id = _insert_document(engine)
    chunk_id = _insert_chunk(engine, doc_id)
    store.add("hr", [chunk_id], [_HR_VEC])
    generate_llm.expect_text("You get 25 days of leave. [1]")
    reranker = _reranker({_CHUNK_TEXT: 5.0})
    pipeline_deps = _pipeline_deps(engine, cfg, embedder, classify_llm, generate_llm, store, reranker)

    app = create_app(ingest_deps, pipeline_deps)
    client = TestClient(app)

    response = client.post("/admin/test-query", json={"question": _QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["intent_slug"] == "hr"
    assert body["confidence"] > 0.0
    assert body["status"] == "success"
    assert "25 days" in body["answer"] or "[1]" in body["answer"]
    assert body["sources"] == [
        {"document_id": doc_id, "document_title": "handbook.pdf", "source_ref": "p. 1"}
    ]
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    assert body["timings_ms"]["pipeline_total"] == body["latency_ms"]
    assert "generation" in body["timings_ms"]


# --- 12.2 No channel involved ------------------------------------------------------


def test_12_2_no_channel_adapter_is_ever_invoked(
    engine, cfg, embedder, classify_llm, generate_llm, store, ingest_deps
):
    """Nothing in `app/api/query.py` imports a Telegram/Teams adapter —
    there is no `send` call to *not* invoke. This test pins that: the
    endpoint's JSON response carries no delivery-channel field at all,
    and the module itself declares no such dependency.
    """
    import ast

    import app.api.query as query_module

    tree = ast.parse(Path(query_module.__file__).read_text())
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("channel" in module.lower() for module in imported_modules)
    assert not any("adapter" in module.lower() for module in imported_modules)

    doc_id = _insert_document(engine)
    chunk_id = _insert_chunk(engine, doc_id)
    store.add("hr", [chunk_id], [_HR_VEC])
    generate_llm.expect_text("You get 25 days of leave. [1]")
    reranker = _reranker({_CHUNK_TEXT: 5.0})
    pipeline_deps = _pipeline_deps(engine, cfg, embedder, classify_llm, generate_llm, store, reranker)

    app = create_app(ingest_deps, pipeline_deps)
    client = TestClient(app)
    response = client.post("/admin/test-query", json={"question": _QUESTION})

    assert "channel" not in response.json()


# --- 12.3 Empty knowledge base -----------------------------------------------------


def test_12_3_empty_knowledge_base_is_no_match_not_an_error(
    engine, cfg, embedder, classify_llm, generate_llm, store, ingest_deps
):
    reranker = _reranker({})
    pipeline_deps = _pipeline_deps(engine, cfg, embedder, classify_llm, generate_llm, store, reranker)

    app = create_app(ingest_deps, pipeline_deps)
    client = TestClient(app)

    response = client.post("/admin/test-query", json={"question": _QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_match"
    assert len(generate_llm.calls) == 0


def test_classifier_outage_returns_retryable_503_without_generation(
    engine, cfg, embedder, classify_llm, generate_llm, store, ingest_deps
):
    ambiguous = [0.0] * DIMENSION
    embedder.set_vector("ambiguous question", ambiguous)
    classify_llm.fail_next(ProviderError.backend("classifier offline"))
    pipeline_deps = _pipeline_deps(
        engine,
        cfg,
        embedder,
        classify_llm,
        generate_llm,
        store,
        _reranker({}),
    )
    client = TestClient(create_app(ingest_deps, pipeline_deps))

    response = client.post(
        "/admin/test-query", json={"question": "ambiguous question"}
    )

    assert response.status_code == 503
    assert "retry" in response.json()["detail"].lower()
    assert len(generate_llm.calls) == 0
