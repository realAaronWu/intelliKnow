"""Tests for the ingestion worker.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §10, plus the
carry-forward assertion that ragged-table repair is actually wired into the
pipeline: ingesting `ragged_salary_grid.pdf` triggers a repair call and
ingesting `salary_bands.pdf` (a clean, ruled table) does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert, select, text

from app.config import AppConfig
from app.db import chunks, create_engine_for, documents, init_schema
from app.ingest.worker import IngestDeps, ingest_document
from app.providers.base import ProviderError
from app.rag.index_writer import IndexWriter
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

FIXTURES = Path(__file__).parent / "fixtures" / "docs"
DIMENSION = 8


class _FailingEmbeddingProvider:
    """A minimal `EmbeddingProvider` that always fails — simulates an
    embedding-provider outage. `tests/doubles.py`'s `FakeEmbeddingProvider`
    has no failure-injection hook (it is deliberately not redefined here),
    so this small local double fills that one gap.
    """

    def __init__(self, dimension: int) -> None:
        self._dimension = dimension
        self.calls: list[list[str]] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        raise ProviderError.backend("embedding provider unreachable")


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "faiss", DIMENSION)


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


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


def _insert_pending_document(engine, filename: str, sha256: str = "a" * 64) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename=filename,
                ext=Path(filename).suffix,
                size_bytes=1024,
                sha256=sha256,
                intent_slug="general",
                status="pending",
                error_message=None,
                chunk_count=0,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at=None,
            )
        )
        return result.inserted_primary_key[0]


def _get_document(engine, doc_id: int):
    with engine.connect() as conn:
        return conn.execute(select(documents).where(documents.c.id == doc_id)).one()


def _chunk_count_for(engine, doc_id: int) -> int:
    with engine.connect() as conn:
        return len(
            conn.execute(select(chunks.c.id).where(chunks.c.document_id == doc_id)).fetchall()
        )


def _chunk_fts_count_for(engine, doc_id: int) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM chunk_fts "
                "JOIN chunks ON chunk_fts.rowid = chunks.id "
                "WHERE chunks.document_id = :doc_id"
            ),
            {"doc_id": doc_id},
        ).scalar_one()


_PROBE = [1.0] + [0.0] * (DIMENSION - 1)


def _vector_count(store: VectorStore, slug: str) -> int:
    return len(store.search(slug, _PROBE, top_n=10_000))


# --- 10.1 Happy path -------------------------------------------------------------


def test_10_1_happy_path_sets_indexed_with_chunk_count_and_timestamp(
    engine, deps, classify_llm
):
    doc_id = _insert_pending_document(engine, "handbook.pdf")
    classify_llm.expect_schema({"slug": "hr"})

    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)

    row = _get_document(engine, doc_id)
    assert row.status == "indexed"
    assert row.chunk_count > 0
    assert row.indexed_at is not None
    assert row.intent_slug == "hr"
    assert row.error_message is None
    assert _chunk_count_for(engine, doc_id) == row.chunk_count


# --- 10.2 Loader failure -----------------------------------------------------------


def test_10_2_loader_failure_sets_failed_with_readable_message(engine, deps):
    doc_id = _insert_pending_document(engine, "corrupt.pdf")

    ingest_document(doc_id, FIXTURES / "corrupt.pdf", deps)

    row = _get_document(engine, doc_id)
    assert row.status == "failed"
    assert row.error_message


def test_10_2_scanned_pdf_failure_message_names_scanned(engine, deps):
    doc_id = _insert_pending_document(engine, "scanned.pdf")

    ingest_document(doc_id, FIXTURES / "scanned.pdf", deps)

    row = _get_document(engine, doc_id)
    assert row.status == "failed"
    assert "scanned" in row.error_message.lower()


# --- 10.3 Failure leaves nothing behind ---------------------------------------------


def test_10_3_embedding_failure_leaves_no_partial_chunks_fts_or_vectors(
    engine, store, cfg, classify_llm
):
    failing_embedder = _FailingEmbeddingProvider(dimension=DIMENSION)
    failing_writer = IndexWriter(engine, store, failing_embedder)
    failing_deps = IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=classify_llm,
        embedding=failing_embedder,
        vector_store=store,
        index_writer=failing_writer,
    )
    doc_id = _insert_pending_document(engine, "handbook.pdf")
    classify_llm.expect_schema({"slug": "hr"})

    ingest_document(doc_id, FIXTURES / "handbook.pdf", failing_deps)

    row = _get_document(engine, doc_id)
    assert row.status == "failed"
    assert row.chunk_count == 0
    assert _chunk_count_for(engine, doc_id) == 0
    assert _chunk_fts_count_for(engine, doc_id) == 0
    assert _vector_count(store, "hr") == 0


# --- 10.4 Embedding provider outage; others remain searchable -----------------------


def test_10_4_provider_outage_leaves_other_indexed_documents_searchable(
    engine, store, cfg, classify_llm
):
    # A healthy document, indexed first.
    healthy_embedder = FakeEmbeddingProvider(dimension=DIMENSION)
    healthy_writer = IndexWriter(engine, store, healthy_embedder)
    healthy_deps = IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=classify_llm,
        embedding=healthy_embedder,
        vector_store=store,
        index_writer=healthy_writer,
    )
    other_doc_id = _insert_pending_document(engine, "salary_bands.pdf", sha256="b" * 64)
    classify_llm.expect_schema({"slug": "finance"})
    ingest_document(other_doc_id, FIXTURES / "salary_bands.pdf", healthy_deps)
    assert _get_document(engine, other_doc_id).status == "indexed"
    other_vector_count_before = _vector_count(store, "finance")
    assert other_vector_count_before > 0

    # A second document whose embedding provider is down.
    failing_embedder = _FailingEmbeddingProvider(dimension=DIMENSION)
    failing_writer = IndexWriter(engine, store, failing_embedder)
    failing_deps = IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=classify_llm,
        embedding=failing_embedder,
        vector_store=store,
        index_writer=failing_writer,
    )
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="c" * 64)
    classify_llm.expect_schema({"slug": "hr"})

    ingest_document(doc_id, FIXTURES / "handbook.pdf", failing_deps)

    assert _get_document(engine, doc_id).status == "failed"
    # The healthy document is untouched: still indexed, its chunks and
    # vectors all still present and searchable.
    other_row = _get_document(engine, other_doc_id)
    assert other_row.status == "indexed"
    assert _chunk_count_for(engine, other_doc_id) == other_row.chunk_count
    assert _vector_count(store, "finance") == other_vector_count_before


# --- 10.5 Document row survives failure ----------------------------------------------


def test_10_5_document_row_survives_failure_and_stays_listed(engine, deps):
    doc_id = _insert_pending_document(engine, "corrupt.pdf")

    ingest_document(doc_id, FIXTURES / "corrupt.pdf", deps)

    with engine.connect() as conn:
        all_ids = {row.id for row in conn.execute(select(documents.c.id)).fetchall()}
    assert doc_id in all_ids


# --- Carry-forward (a): ragged-table repair wired end-to-end ------------------------


def test_ragged_table_document_triggers_repair_and_still_indexes(engine, store, cfg):
    llm = FakeLLMProvider()
    llm.expect_schema(
        {
            "rows": [
                ["Band", "Min", "Mid", "Max"],
                ["Band 1", "32000", "38000", "44000"],
                ["Band 2", "45000", "52000", "59000"],
                ["Band 3", "60000", "68000", "76000"],
            ]
        }
    )
    llm.expect_schema({"slug": "finance"})
    embedder = FakeEmbeddingProvider(dimension=DIMENSION)
    writer = IndexWriter(engine, store, embedder)
    deps = IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=llm,
        embedding=embedder,
        vector_store=store,
        index_writer=writer,
    )
    doc_id = _insert_pending_document(engine, "ragged_salary_grid.pdf")

    ingest_document(doc_id, FIXTURES / "ragged_salary_grid.pdf", deps)

    row = _get_document(engine, doc_id)
    assert row.status == "indexed"
    assert len(llm.calls) == 2, "expected one table-repair call plus one intent-suggestion call"
    repair_call, intent_call = llm.calls
    assert repair_call["schema"] is not None
    assert "rows" in repair_call["schema"].get("properties", {})
    assert intent_call["schema"] == {
        "type": "object",
        "properties": {"slug": {"type": "string"}},
        "required": ["slug"],
    }


def test_clean_table_document_is_not_sent_to_the_model_for_repair(engine, store, cfg):
    llm = FakeLLMProvider()
    llm.expect_schema({"slug": "finance"})  # only the intent-suggestion call
    embedder = FakeEmbeddingProvider(dimension=DIMENSION)
    writer = IndexWriter(engine, store, embedder)
    deps = IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=llm,
        embedding=embedder,
        vector_store=store,
        index_writer=writer,
    )
    doc_id = _insert_pending_document(engine, "salary_bands.pdf")

    ingest_document(doc_id, FIXTURES / "salary_bands.pdf", deps)

    row = _get_document(engine, doc_id)
    assert row.status == "indexed"
    assert len(llm.calls) == 1
