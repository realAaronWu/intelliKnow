"""Tests for document lifecycle operations: re-parse, delete, full re-index.

Covers superpowers/test-plans/03-rag-write-path-tests.md §11.1-11.4.
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
from app.rag.index_meta import read_meta, read_reindex_status, write_reindex_status
from app.rag.index_writer import IndexWriter
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider
from tests.fts_helpers import assert_keyword_index_in_sync, fts_indexed_chunk_count

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
    """How many of `doc_id`'s chunks the keyword index can actually find.

    This used to be a `count(*)` over a join with no `MATCH`. On an
    external-content FTS5 table that full-scans `chunks` and returns the
    chunks count whether or not the index is in step, so the assertion
    held even with the sync triggers deleted — see `tests/fts_helpers.py`.
    """
    return fts_indexed_chunk_count(engine, doc_id)


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
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
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
    # The keyword index was rebuilt alongside the chunks, not left holding
    # the replaced ones.
    assert _chunk_fts_count_for(engine, doc_id) == after.chunk_count
    assert_keyword_index_in_sync(engine)
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
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
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
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
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
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)
    chunk_count = len(_chunk_ids_for(engine, doc_id))
    assert chunk_count > 0
    # The positive half matters as much as the negative one: without it,
    # "0 after delete" would pass against a keyword index that never held
    # the document in the first place.
    assert _chunk_fts_count_for(engine, doc_id) == chunk_count
    assert _vector_count(store, "hr") > 0

    delete_document(doc_id, deps)

    assert _chunk_ids_for(engine, doc_id) == set()
    assert _chunk_fts_count_for(engine, doc_id) == 0
    assert _vector_count(store, "hr") == 0
    assert_keyword_index_in_sync(engine)
    with engine.connect() as conn:
        remaining = conn.execute(select(documents.c.id)).fetchall()
    assert doc_id not in {row.id for row in remaining}


def test_11_3_delete_preserves_query_log_history(engine, deps, classify_llm):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
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
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)
    embedder.calls.clear()

    reassign_document(doc_id, "legal", deps)

    assert embedder.calls == []
    assert _get_document(engine, doc_id).intent_slug == "legal"
    assert _vector_count(store, "hr") == 0
    assert _vector_count(store, "legal") > 0


def test_reassign_rejects_an_unconfigured_intent_space(engine, deps, classify_llm):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)

    with pytest.raises(ValueError, match="not-a-real-space"):
        reassign_document(doc_id, "not-a-real-space", deps)

    assert _get_document(engine, doc_id).intent_slug == "hr"


# --- 11.4 Full re-index -----------------------------------------------------------------


def test_11_4_reindex_reembeds_every_document_and_updates_index_meta(
    engine, deps, classify_llm, embedder, store, cfg
):
    hr_doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(hr_doc_id, FIXTURES / "handbook.pdf", deps)

    finance_doc_id = _insert_pending_document(engine, "salary_bands.pdf", sha256="b" * 64)
    classify_llm.expect_schema({"slug": "finance", "confidence": 0.95, "reasoning": "clear match"})
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


# --- 11.4 Full re-index: failure handling and stale state --------------------------
#
# `reindex_all` deleted, recreated and repopulated each space in turn with
# no failure handling at all. A failure part-way left earlier slugs on the
# new model and later ones on the old — the exact mixed-model state
# `index_meta.json` exists to prevent — with `write_meta` unreached, so the
# record still named the old model and nothing could detect the mix. It
# also never removed the `.index` file of a space that no longer had any
# chunks. And it runs as a background task after a 202, so the failure was
# invisible to the admin who triggered it.


def test_reindex_deletes_the_index_file_of_a_space_with_no_chunks(
    engine, deps, classify_llm, faiss_dir
):
    hr_doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(hr_doc_id, FIXTURES / "handbook.pdf", deps)
    finance_doc_id = _insert_pending_document(engine, "salary_bands.pdf", sha256="b" * 64)
    classify_llm.expect_schema({"slug": "finance", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(finance_doc_id, FIXTURES / "salary_bands.pdf", deps)
    delete_document(finance_doc_id, deps)
    assert (faiss_dir / "finance.index").exists(), "precondition: the stale file is there"

    reindex_all(deps)

    assert (faiss_dir / "hr.index").exists()
    assert not (faiss_dir / "finance.index").exists()


def test_a_failed_reindex_leaves_the_existing_indexes_and_record_untouched(
    engine, store, cfg, classify_llm, faiss_dir
):
    healthy_embedder = FakeEmbeddingProvider(dimension=DIMENSION)
    healthy_deps = IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=classify_llm,
        embedding=healthy_embedder,
        vector_store=store,
        index_writer=IndexWriter(engine, store, healthy_embedder),
    )
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", healthy_deps)
    vectors_before = _vector_count(store, "hr")
    assert vectors_before > 0
    assert read_meta(faiss_dir).model == cfg.embedding.model

    # The operator has switched models and is re-indexing; the provider for
    # the new model is down.
    new_model_cfg = cfg.model_copy(
        update={"embedding": cfg.embedding.model_copy(update={"model": "a-new-model"})}
    )
    failing_embedder = _FailingEmbeddingProvider(dimension=DIMENSION)
    failing_deps = IngestDeps(
        engine=engine,
        cfg=new_model_cfg,
        classify_llm=classify_llm,
        embedding=failing_embedder,
        vector_store=store,
        index_writer=IndexWriter(engine, store, failing_embedder),
    )

    with pytest.raises(ProviderError):
        reindex_all(failing_deps)

    # Every space still holds its old vectors, and the record still names
    # the model that actually built them — not the one that failed.
    assert _vector_count(store, "hr") == vectors_before
    assert read_meta(faiss_dir).model == cfg.embedding.model


def test_a_failed_reindex_is_recorded_where_an_admin_can_see_it(
    engine, store, cfg, classify_llm, faiss_dir
):
    failing_embedder = _FailingEmbeddingProvider(dimension=DIMENSION)
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    healthy_embedder = FakeEmbeddingProvider(dimension=DIMENSION)
    ingest_document(
        doc_id,
        FIXTURES / "handbook.pdf",
        IngestDeps(
            engine=engine,
            cfg=cfg,
            classify_llm=classify_llm,
            embedding=healthy_embedder,
            vector_store=store,
            index_writer=IndexWriter(engine, store, healthy_embedder),
        ),
    )
    failing_deps = IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=classify_llm,
        embedding=failing_embedder,
        vector_store=store,
        index_writer=IndexWriter(engine, store, failing_embedder),
    )

    with pytest.raises(ProviderError):
        reindex_all(failing_deps)

    status = read_reindex_status(faiss_dir)
    assert status is not None
    assert status.status == "failed"
    assert "unreachable" in status.error


def test_a_successful_reindex_records_success(engine, deps, classify_llm, faiss_dir, cfg):
    doc_id = _insert_pending_document(engine, "handbook.pdf", sha256="a" * 64)
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    ingest_document(doc_id, FIXTURES / "handbook.pdf", deps)

    reindex_all(deps)

    status = read_reindex_status(faiss_dir)
    assert status is not None
    assert status.status == "ok"
    assert status.error is None
    assert status.model == cfg.embedding.model


def test_a_later_successful_reindex_clears_a_recorded_failure(
    engine, deps, classify_llm, faiss_dir
):
    write_reindex_status(faiss_dir, status="failed", model="old", error="provider was down")

    reindex_all(deps)

    assert read_reindex_status(faiss_dir).status == "ok"
