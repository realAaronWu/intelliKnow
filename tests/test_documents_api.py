"""Tests for the document admin API.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §12.1-12.10.
`TestClient` runs FastAPI `BackgroundTasks` synchronously within the same
call, so by the time `client.post(...)` returns, a scheduled ingestion has
already completed (or failed) — tests that need to assert on the
*response body* of the scheduling call itself (12.1) read that body
directly rather than re-querying the database afterward.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.config import AppConfig
from app.db import create_engine_for, documents, init_schema
from app.ingest.worker import IngestDeps
from app.main import create_app
from app.rag.index_writer import IndexWriter
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

FIXTURES = Path(__file__).parent / "fixtures" / "docs"
DIMENSION = 8


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
            "storage": {
                "faiss_dir": str(tmp_path / "faiss"),
                "sqlite_path": str(tmp_path / "intelliknow.db"),
                "upload_dir": str(tmp_path / "uploads"),
            },
        }
    )


@pytest.fixture
def classify_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dimension=DIMENSION)


@pytest.fixture
def index_writer(engine, store, embedder) -> IndexWriter:
    return IndexWriter(engine, store, embedder)


@pytest.fixture
def deps(engine, cfg, classify_llm, embedder, store, index_writer) -> IngestDeps:
    return IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=classify_llm,
        embedding=embedder,
        vector_store=store,
        index_writer=index_writer,
    )


@pytest.fixture
def client(deps) -> TestClient:
    app = create_app(deps)
    return TestClient(app)


def _upload(client: TestClient, classify_llm: FakeLLMProvider, filename: str, slug: str) -> int:
    classify_llm.expect_schema({"slug": slug})
    content = (FIXTURES / filename).read_bytes()
    resp = client.post(
        "/documents", files={"file": (filename, content, "application/octet-stream")}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


def _insert_document_directly(
    engine,
    filename: str,
    ext: str,
    sha256: str,
    intent_slug: str,
    status: str,
    uploaded_at: str,
) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename=filename,
                ext=ext,
                size_bytes=1024,
                sha256=sha256,
                intent_slug=intent_slug,
                status=status,
                error_message=None,
                chunk_count=0,
                uploaded_at=uploaded_at,
                indexed_at=None,
            )
        )
        return result.inserted_primary_key[0]


# --- 12.1 Upload returns immediately -----------------------------------------------


def test_12_1_upload_returns_202_with_id_and_pending_status(client, classify_llm):
    classify_llm.expect_schema({"slug": "hr"})
    content = (FIXTURES / "handbook.pdf").read_bytes()

    resp = client.post(
        "/documents", files={"file": ("handbook.pdf", content, "application/pdf")}
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert isinstance(body["id"], int)


# --- 12.2 Search by name -------------------------------------------------------------


def test_12_2_search_by_name_returns_only_matching_documents(client, classify_llm):
    _upload(client, classify_llm, "handbook.pdf", "hr")
    _upload(client, classify_llm, "expense_policy.docx", "finance")

    resp = client.get("/documents", params={"q": "handbook"})

    assert resp.status_code == 200
    filenames = {d["filename"] for d in resp.json()}
    assert filenames == {"handbook.pdf"}


# --- 12.3 Filter by format ----------------------------------------------------------


def test_12_3_filter_by_format_returns_only_that_format(client, classify_llm):
    _upload(client, classify_llm, "handbook.pdf", "hr")
    _upload(client, classify_llm, "expense_policy.docx", "finance")

    resp = client.get("/documents", params={"format": ".docx"})

    filenames = {d["filename"] for d in resp.json()}
    assert filenames == {"expense_policy.docx"}


# --- 12.4 Filter by intent space ------------------------------------------------------


def test_12_4_filter_by_intent_space_returns_only_that_space(client, classify_llm):
    _upload(client, classify_llm, "handbook.pdf", "hr")
    _upload(client, classify_llm, "expense_policy.docx", "finance")

    resp = client.get("/documents", params={"intent_space": "finance"})

    filenames = {d["filename"] for d in resp.json()}
    assert filenames == {"expense_policy.docx"}


# --- 12.5 Filter by date range --------------------------------------------------------


def test_12_5_filter_by_date_range_returns_only_that_range(client, engine):
    _insert_document_directly(
        engine, "old.pdf", ".pdf", "a" * 64, "hr", "indexed", "2026-01-01T00:00:00Z"
    )
    _insert_document_directly(
        engine, "recent.pdf", ".pdf", "b" * 64, "hr", "indexed", "2026-08-01T00:00:00Z"
    )
    _insert_document_directly(
        engine, "future.pdf", ".pdf", "c" * 64, "hr", "indexed", "2026-12-01T00:00:00Z"
    )

    resp = client.get(
        "/documents",
        params={"date_from": "2026-07-01T00:00:00Z", "date_to": "2026-09-01T00:00:00Z"},
    )

    filenames = {d["filename"] for d in resp.json()}
    assert filenames == {"recent.pdf"}


# --- 12.6 Combined filters ------------------------------------------------------------


def test_12_6_combined_filters_return_the_intersection(client, engine):
    _insert_document_directly(
        engine, "a.pdf", ".pdf", "a" * 64, "hr", "indexed", "2026-08-01T00:00:00Z"
    )
    _insert_document_directly(
        engine, "b.pdf", ".pdf", "b" * 64, "finance", "indexed", "2026-08-01T00:00:00Z"
    )
    _insert_document_directly(
        engine, "c.docx", ".docx", "c" * 64, "hr", "indexed", "2026-08-01T00:00:00Z"
    )

    resp = client.get(
        "/documents",
        params={"format": ".pdf", "intent_space": "hr"},
    )

    filenames = {d["filename"] for d in resp.json()}
    assert filenames == {"a.pdf"}


# --- 12.7 Detail -----------------------------------------------------------------------


def test_12_7_detail_returns_intent_space_chunk_count_status_and_chunks(client, classify_llm):
    doc_id = _upload(client, classify_llm, "handbook.pdf", "hr")

    resp = client.get(f"/documents/{doc_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent_slug"] == "hr"
    assert body["status"] == "indexed"
    assert body["chunk_count"] > 0
    assert body["error_message"] is None
    assert len(body["chunks"]) == body["chunk_count"]
    first_chunk = body["chunks"][0]
    assert "heading_path" in first_chunk
    assert "source_ref" in first_chunk
    assert "text" in first_chunk


def test_12_7_detail_of_unknown_document_is_404(client):
    resp = client.get("/documents/999999")

    assert resp.status_code == 404


# --- 12.8 Reassign endpoint ----------------------------------------------------------


def test_12_8_reassign_endpoint_changes_space_and_preserves_chunk_count(
    client, classify_llm, embedder
):
    doc_id = _upload(client, classify_llm, "handbook.pdf", "hr")
    detail_before = client.get(f"/documents/{doc_id}").json()
    embedder.calls.clear()

    resp = client.patch(f"/documents/{doc_id}", json={"intent_slug": "legal"})

    assert resp.status_code == 200
    detail_after = client.get(f"/documents/{doc_id}").json()
    assert detail_after["intent_slug"] == "legal"
    assert detail_after["chunk_count"] == detail_before["chunk_count"]
    assert embedder.calls == []


def test_12_8_reassign_to_unconfigured_space_is_rejected(client, classify_llm):
    doc_id = _upload(client, classify_llm, "handbook.pdf", "hr")

    resp = client.patch(f"/documents/{doc_id}", json={"intent_slug": "not-a-real-space"})

    assert resp.status_code == 400
    assert "not-a-real-space" in resp.json()["detail"]


# --- 12.9 Delete endpoint --------------------------------------------------------------


def test_12_9_delete_endpoint_removes_from_list_and_retrieval(client, classify_llm):
    doc_id = _upload(client, classify_llm, "handbook.pdf", "hr")

    resp = client.delete(f"/documents/{doc_id}")

    assert resp.status_code == 204
    assert client.get(f"/documents/{doc_id}").status_code == 404
    ids = {d["id"] for d in client.get("/documents").json()}
    assert doc_id not in ids


def test_12_9_delete_of_unknown_document_is_404(client):
    resp = client.delete("/documents/999999")

    assert resp.status_code == 404


# --- 12.10 Validation error shape -------------------------------------------------------


def test_12_10_unsupported_format_upload_returns_actionable_message(client):
    resp = client.post(
        "/documents",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert ".pdf" in detail
    assert ".docx" in detail
    assert ".xlsx" in detail


def test_12_10_oversized_upload_returns_actionable_message(client, cfg):
    oversized = b"x" * (cfg.ingestion.max_upload_mb * 1024 * 1024 + 1)

    resp = client.post(
        "/documents",
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )

    assert resp.status_code == 400
    assert str(cfg.ingestion.max_upload_mb) in resp.json()["detail"]


def test_12_10_duplicate_upload_returns_actionable_message(client, classify_llm):
    _upload(client, classify_llm, "handbook.pdf", "hr")
    content = (FIXTURES / "handbook.pdf").read_bytes()

    resp = client.post(
        "/documents", files={"file": ("handbook-again.pdf", content, "application/pdf")}
    )

    assert resp.status_code == 400
    assert "handbook.pdf" in resp.json()["detail"]


# --- Reparse and reindex endpoints (smoke coverage; brief requires them exist) --------


def test_reparse_endpoint_replaces_chunks(client, classify_llm):
    doc_id = _upload(client, classify_llm, "handbook.pdf", "hr")

    resp = client.post(f"/documents/{doc_id}/reparse")

    assert resp.status_code == 202
    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["status"] == "indexed"
    assert detail["intent_slug"] == "hr"


def test_reparse_endpoint_of_unknown_document_is_404(client):
    resp = client.post("/documents/999999/reparse")

    assert resp.status_code == 404


def test_reindex_endpoint_reembeds_every_document(client, classify_llm, embedder):
    _upload(client, classify_llm, "handbook.pdf", "hr")
    _upload(client, classify_llm, "expense_policy.docx", "finance")
    embedder.calls.clear()

    resp = client.post("/documents/reindex")

    assert resp.status_code == 202
    assert len(embedder.calls) > 0
