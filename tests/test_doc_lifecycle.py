"""Tests for document lifecycle operations: re-parse, delete, full re-index.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §11.1-11.4.
Reassign (§ "Reassign does not re-embed" / "moves vectors") is exercised
directly against `IndexWriter.reassign_document` in tests/test_index_writer.py
(§8.4-8.5); `app/ingest/lifecycle.py`'s `reassign_document` is a thin,
validated wrapper over it, tested here for the validation it adds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert, select, text

from app.config import AppConfig
from app.db import chunks, create_engine_for, documents, init_schema, query_log
from app.ingest.lifecycle import delete_document, reassign_document, reindex_all, reparse_document
from app.ingest.worker import IngestDeps, ingest_document
from app.providers.base import ProviderError
from app.rag.index_meta import read_meta
from app.rag.index_writer import IndexWriter
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

FIXTURES = Path(__file__).parent / "fixtures" / "docs"
DIMENSION = 8
_PROBE = [1.0] + [0.0] * (DIMENSION - 1)


class _FailingEmbeddingProvider:
    """Local double: always fails. See tests/test_ingest_worker.py for why
    this isn't added to tests/doubles.py's shared `FakeEmbeddingProvider`.
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
def faiss_dir(tmp_path: Path) -> Path:
    return tmp_path / "faiss"


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


@pytest.fixture
def store(faiss_dir: Path) -> VectorStore:
    return VectorStore(faiss_dir, DIMENSION)


@pytest.fixture
def cfg(faiss_dir: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "embedding": {"model": "fake-embed-model", "dimension": DIMENSION},
            "storage": {"faiss_dir": str(faiss_dir)},
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


def _insert_pending_document(engine, filename: str, sha256: str) -> int:
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


def _chunk_ids_for(engine, doc_id: int) -> set[int]:
    with engine.connect() as conn:
        return {
            row.id
            for row in conn.execute(
                select(chunks.c.id).where(chunks.c.document_id == doc_id)
            ).fetchall()
        }


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


def _vector_count(store: VectorStore, slug: str) -> int:
    return len(store.search(slug, _PROBE, top_n=10_000))


# --- 11.1 Re-parse replaces --------------------------------------------------------


def _chunk_texts_for(engine, doc_id: int) -> list[str]:
    with engine.connect() as conn:
        return [
            row.text
            for row in conn.execute(
                select(chunks.c.text).where(chunks.c.document_id == doc_id)
            ).fetchall()
        ]


def test_11_1_reparse_replaces_chunks_preserving_id_and_intent_space(engine, deps, classify_llm):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)
    before = _get_document(engine, doc_id)
    assert before.status == "indexed"
    assert before.intent_slug == "hr"
    old_texts = " ".join(_chunk_texts_for(engine, doc_id))
    assert "annual leave" in old_texts

    # Re-parse against a different file — as if the underlying upload had
    # been replaced with new content — to prove old content is actually
    # gone rather than merely re-derived identically from the same bytes.
    reparse_document(doc_id, FIXTURES / "expense_policy.docx", deps)

    after = _get_document(engine, doc_id)
    assert after.id == before.id
    assert after.intent_slug == "hr"  # preserved, not re-suggested
    assert after.status == "indexed"
    new_texts = " ".join(_chunk_texts_for(engine, doc_id))
    assert "annual leave" not in new_texts
    assert "reimbursed" in new_texts
    assert len(_chunk_ids_for(engine, doc_id)) == after.chunk_count
    # No LLM call for intent suggestion on re-parse — the space is
    # preserved, not re-suggested.
    assert len(classify_llm.calls) == 1


# --- 11.2 Re-parse failure ------------------------------------------------------------


def test_11_2_reparse_failure_sets_failed_with_no_orphaned_vectors(
    engine, store, cfg, classify_llm
):
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
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", healthy_deps)
    assert _get_document(engine, doc_id).status == "indexed"

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

    reparse_document(doc_id, FIXTURES / "handbook.pdf", failing_deps)

    row = _get_document(engine, doc_id)
    assert row.status == "failed"
    assert row.error_message
    assert _chunk_ids_for(engine, doc_id) == set()
    assert _chunk_fts_count_for(engine, doc_id) == 0
    assert _vector_count(store, "hr") == 0


def test_11_2_reparse_of_unparseable_file_leaves_old_chunks_untouched(engine, deps, classify_llm):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)
    old_chunk_ids = _chunk_ids_for(engine, doc_id)
    assert old_chunk_ids

    reparse_document(doc_id, FIXTURES / "corrupt.pdf", deps)

    row = _get_document(engine, doc_id)
    assert row.status == "failed"
    assert row.error_message
    # The load/chunk stage failed before any old data was touched.
    assert _chunk_ids_for(engine, doc_id) == old_chunk_ids


def test_11_2_reparse_of_unknown_document_is_a_no_op(engine, deps):
    reparse_document(999, FIXTURES / "handbook.pdf", deps)  # must not raise


# --- 11.3 Delete ---------------------------------------------------------------------


def test_11_3_delete_clears_chunks_and_both_indexes(engine, deps, classify_llm, store):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)
    assert _chunk_ids_for(engine, doc_id)
    assert _vector_count(store, "hr") > 0

    delete_document(doc_id, deps)

    assert _chunk_ids_for(engine, doc_id) == set()
    assert _chunk_fts_count_for(engine, doc_id) == 0
    assert _vector_count(store, "hr") == 0
    with engine.connect() as conn:
        remaining = conn.execute(select(documents.c.id)).fetchall()
    assert doc_id not in {row.id for row in remaining}


def test_11_3_delete_preserves_query_log_history(engine, deps, classify_llm):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)

    with engine.begin() as conn:
        conn.execute(
            insert(query_log).values(
                created_at="2026-08-09T00:02:00Z",
                channel="telegram",
                user_ref="user-1",
                question="How much vacation do I get?",
                intent_slug="hr",
                confidence=0.92,
                classified_by="centroid",
                reasoning=None,
                fallback_used=False,
                status="success",
                answer="You accrue vacation monthly.",
                citations_json="[]",
                retrieved_doc_ids_json=f"[{doc_id}]",
                latency_ms=850,
                error=None,
            )
        )

    delete_document(doc_id, deps)

    with engine.connect() as conn:
        rows = conn.execute(select(query_log)).fetchall()
    assert len(rows) == 1
    assert rows[0].retrieved_doc_ids_json == f"[{doc_id}]"


# --- Reassign (thin wrapper over IndexWriter.reassign_document) ----------------------


def test_reassign_moves_vectors_with_zero_embed_calls(engine, deps, classify_llm, embedder, store):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)
    embedder.calls.clear()

    reassign_document(doc_id, "legal", deps)

    assert embedder.calls == []
    assert _get_document(engine, doc_id).intent_slug == "legal"
    assert _vector_count(store, "hr") == 0
    assert _vector_count(store, "legal") > 0


def test_reassign_rejects_an_unconfigured_intent_space(engine, deps, classify_llm):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)

    with pytest.raises(ValueError, match="not-a-real-space"):
        reassign_document(doc_id, "not-a-real-space", deps)

    assert _get_document(engine, doc_id).intent_slug == "hr"


# --- 11.4 Full re-index -----------------------------------------------------------------


def test_11_4_reindex_reembeds_every_document_and_updates_index_meta(
    engine, deps, classify_llm, embedder, store, cfg
):
    hr_doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr"})
    ingest_document(hr_doc_id, FIXTURES / "handbook.pdf", deps)

    finance_doc_id = _insert_pending_document(engine, "salary_bands.pdf", sha256="b" * 64)
    classify_llm.expect_schema({"slug": "finance"})
    ingest_document(finance_doc_id, FIXTURES / "salary_bands.pdf", deps)

    hr_chunk_count_before = len(_chunk_ids_for(engine, hr_doc_id))
    finance_chunk_count_before = len(_chunk_ids_for(engine, finance_doc_id))
    embedder.calls.clear()

    reindex_all(deps)

    total_texts_reembedded = sum(len(call) for call in embedder.calls)
    assert total_texts_reembedded == hr_chunk_count_before + finance_chunk_count_before

    # Chunk rows themselves are untouched — only the vectors are rebuilt.
    assert len(_chunk_ids_for(engine, hr_doc_id)) == hr_chunk_count_before
    assert len(_chunk_ids_for(engine, finance_doc_id)) == finance_chunk_count_before
    assert _vector_count(store, "hr") == hr_chunk_count_before
    assert _vector_count(store, "finance") == finance_chunk_count_before

    meta = read_meta(Path(cfg.storage.faiss_dir))
    assert meta is not None
    assert meta.model == cfg.embedding.model
    assert meta.dimension == cfg.embedding.dimension


def test_11_4_reindex_with_no_documents_still_records_index_meta(deps, cfg):
    reindex_all(deps)

    meta = read_meta(Path(cfg.storage.faiss_dir))
    assert meta is not None
    assert meta.model == cfg.embedding.model
