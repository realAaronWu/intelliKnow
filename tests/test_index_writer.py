"""Test-plan §8 — index writer.

Source: docs/superpowers/test-plans/03-rag-write-path-tests.md §8

`IndexWriter` keeps three stores in step: the `chunks` row, the `chunk_fts`
row (populated automatically by the triggers in `app/db.py` — never
written here directly), and the space's FAISS index. Every test below
checks agreement across all three, since that agreement is the module's
entire reason to exist.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text

from app.db import chunk_fts, chunks, create_engine_for, documents, init_schema, query_log
from app.rag.chunker import Chunk
from app.rag.index_writer import IndexWriter
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider

DIMENSION = 4
_PROBE = [1.0, 0.0, 0.0, 0.0]


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(tmp_path / "faiss", DIMENSION)


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dimension=DIMENSION)


def _insert_document(engine, sha256: str = "a" * 64, intent_slug: str = "hr") -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename="policy.pdf",
                ext=".pdf",
                size_bytes=1024,
                sha256=sha256,
                intent_slug=intent_slug,
                status="parsing",
                error_message=None,
                chunk_count=0,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at=None,
            )
        )
        return result.inserted_primary_key[0]


def _make_chunks(n: int) -> list[Chunk]:
    return [
        Chunk(
            ordinal=i,
            text=f"chunk body number {i}",
            heading_path=["Policy"],
            source_ref=f"p. {i + 1}",
            char_count=20,
        )
        for i in range(n)
    ]


def _insert_chunk_directly(engine, doc_id: int, slug: str, ordinal: int, body: str) -> int:
    """Insert a chunk row without going through `IndexWriter` — used to set
    up reassignment scenarios where the embedder must see zero calls across
    the whole test, not just a delta.
    """
    with engine.begin() as conn:
        result = conn.execute(
            insert(chunks).values(
                document_id=doc_id,
                intent_slug=slug,
                ordinal=ordinal,
                text=body,
                heading_path=None,
                source_ref="p. 1",
                char_count=len(body),
            )
        )
        return result.inserted_primary_key[0]


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


# --- 8.1 Three stores agree ---------------------------------------------------


def test_8_1_three_stores_agree_after_write(engine, store, embedder):
    doc_id = _insert_document(engine)
    writer = IndexWriter(engine, store, embedder)

    writer.write_document(doc_id, "hr", _make_chunks(3))

    with engine.connect() as conn:
        chunk_row_count = conn.execute(
            select(chunks).where(chunks.c.document_id == doc_id)
        ).fetchall()
    assert len(chunk_row_count) == 3
    assert _chunk_fts_count_for(engine, doc_id) == 3
    assert _vector_count(store, "hr") == 3


# --- 8.2 Removal clears all three ---------------------------------------------


def test_8_2_removal_clears_all_three_stores(engine, store, embedder):
    doc_id = _insert_document(engine)
    writer = IndexWriter(engine, store, embedder)
    writer.write_document(doc_id, "hr", _make_chunks(3))

    writer.remove_document(doc_id)

    with engine.connect() as conn:
        remaining_chunks = conn.execute(
            select(chunks).where(chunks.c.document_id == doc_id)
        ).fetchall()
    assert remaining_chunks == []
    assert _chunk_fts_count_for(engine, doc_id) == 0
    assert _vector_count(store, "hr") == 0


# --- 8.3 Removal preserves history ---------------------------------------------


def test_8_3_removal_preserves_query_log_history(engine, store, embedder):
    doc_id = _insert_document(engine)
    writer = IndexWriter(engine, store, embedder)
    writer.write_document(doc_id, "hr", _make_chunks(1))

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

    writer.remove_document(doc_id)

    with engine.connect() as conn:
        rows = conn.execute(select(query_log)).fetchall()
    assert len(rows) == 1
    assert rows[0].retrieved_doc_ids_json == f"[{doc_id}]"


def test_8_2_removal_of_unknown_document_is_a_no_op(engine, store, embedder):
    writer = IndexWriter(engine, store, embedder)

    writer.remove_document(999)  # must not raise


# --- 8.4 Reassign does not re-embed --------------------------------------------


def test_8_4_reassign_makes_zero_embed_calls(engine, store, embedder):
    doc_id = _insert_document(engine, intent_slug="hr")
    store.create_space("hr")
    store.create_space("legal")
    chunk_id = _insert_chunk_directly(engine, doc_id, "hr", ordinal=0, body="alpha")
    store.add("hr", [chunk_id], [_unit([1.0, 0.0, 0.0, 0.0])])
    writer = IndexWriter(engine, store, embedder)

    writer.reassign_document(doc_id, "legal")

    assert embedder.calls == []


# --- 8.5 Reassign moves vectors -------------------------------------------------


def test_8_5_reassign_moves_vectors_and_updates_chunk_space(engine, store, embedder):
    doc_id = _insert_document(engine, intent_slug="hr")
    store.create_space("hr")
    store.create_space("legal")
    chunk_id = _insert_chunk_directly(engine, doc_id, "hr", ordinal=0, body="alpha")
    store.add("hr", [chunk_id], [_unit([1.0, 0.0, 0.0, 0.0])])
    writer = IndexWriter(engine, store, embedder)

    writer.reassign_document(doc_id, "legal")

    assert _vector_count(store, "hr") == 0
    assert _vector_count(store, "legal") == 1

    with engine.connect() as conn:
        row = conn.execute(
            select(chunks.c.intent_slug).where(chunks.c.id == chunk_id)
        ).scalar_one()
    assert row == "legal"

    with engine.connect() as conn:
        doc_row = conn.execute(
            select(documents.c.intent_slug).where(documents.c.id == doc_id)
        ).scalar_one()
    assert doc_row == "legal"


def test_8_5_reassign_of_unknown_document_is_a_no_op(engine, store, embedder):
    writer = IndexWriter(engine, store, embedder)

    writer.reassign_document(999, "legal")  # must not raise
    assert embedder.calls == []


# --- 8.6 Batching ---------------------------------------------------------------


def test_8_6_embedding_is_batched_at_configured_size(engine, store, embedder):
    doc_id = _insert_document(engine)
    writer = IndexWriter(engine, store, embedder, batch_size=2)

    writer.write_document(doc_id, "hr", _make_chunks(5))

    assert len(embedder.calls) == 3  # ceil(5 / 2)
    assert [len(call) for call in embedder.calls] == [2, 2, 1]


def _unit(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(c * c for c in vector))
    return [c / length for c in vector]
