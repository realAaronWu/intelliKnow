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
from tests.fts_helpers import assert_keyword_index_in_sync, fts_indexed_chunk_count

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
    """How many of `doc_id`'s chunks the keyword index can actually find.

    This used to be a `count(*)` over a join with no `MATCH`. On an
    external-content FTS5 table that full-scans `chunks` and returns the
    chunks count whether or not the index is in step, so the assertion
    held even with the sync triggers deleted — see `tests/fts_helpers.py`.
    """
    return fts_indexed_chunk_count(engine, doc_id)


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
    assert_keyword_index_in_sync(engine)


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
    assert_keyword_index_in_sync(engine)


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


# --- Reassigning a document that has no chunks (I1) --------------------------------
#
# The `documents.intent_slug` update sat inside the early return taken when
# a document has no chunks, so reassigning a `pending`, `failed`, or
# zero-chunk document silently did nothing at all — while the API happily
# returned 200 with the old space.


def test_reassign_moves_a_failed_document_with_no_chunks(engine, store, embedder):
    doc_id = _insert_document(engine, intent_slug="hr")
    with engine.begin() as conn:
        conn.execute(
            documents.update()
            .where(documents.c.id == doc_id)
            .values(status="failed", error_message="could not parse", chunk_count=0)
        )
    writer = IndexWriter(engine, store, embedder)

    writer.reassign_document(doc_id, "legal")

    with engine.connect() as conn:
        slug = conn.execute(
            select(documents.c.intent_slug).where(documents.c.id == doc_id)
        ).scalar_one()
    assert slug == "legal"
    assert embedder.calls == []


def test_reassign_of_a_pending_document_moves_it_too(engine, store, embedder):
    doc_id = _insert_document(engine, intent_slug="hr")
    with engine.begin() as conn:
        conn.execute(
            documents.update().where(documents.c.id == doc_id).values(status="pending")
        )
    writer = IndexWriter(engine, store, embedder)

    writer.reassign_document(doc_id, "legal")

    with engine.connect() as conn:
        row = conn.execute(select(documents).where(documents.c.id == doc_id)).one()
    assert row.intent_slug == "legal"
    assert row.status == "pending"


# --- Failure injection: the three stores never split (I3) ---------------------------
#
# All three mutations committed their SQL before touching FAISS, with no
# repair path: a `VectorStore` failure left the database saying one thing
# and the index another, with the document still marked `indexed` and
# nothing anywhere noticing. For a module whose whole reason to exist is
# that the three stores agree, the failure paths were the untested half.


class _FlakyVectorStore:
    """Delegates to a real `VectorStore`, raising on one named method.

    A real store underneath is the point: the assertions are about what
    survives in the actual index after a mid-operation failure, which a
    pure stub could not show.
    """

    def __init__(self, inner: VectorStore, fail_on: str) -> None:
        self._inner = inner
        self._fail_on = fail_on
        self.load_calls: list[str] = []

    def _maybe_fail(self, name: str) -> None:
        if name == self._fail_on:
            raise RuntimeError(f"FAISS {name} failed")

    def create_space(self, slug: str) -> None:
        self._maybe_fail("create_space")
        self._inner.create_space(slug)

    def add(self, slug: str, ids: list[int], vectors: list[list[float]]) -> None:
        self._maybe_fail("add")
        self._inner.add(slug, ids, vectors)

    def remove(self, slug: str, ids: list[int]) -> None:
        self._maybe_fail("remove")
        self._inner.remove(slug, ids)

    def move(self, from_slug: str, to_slug: str, ids: list[int]) -> None:
        self._maybe_fail("move")
        self._inner.move(from_slug, to_slug, ids)

    def persist(self, slug: str) -> None:
        self._maybe_fail("persist")
        self._inner.persist(slug)

    def load(self, slug: str) -> None:
        self.load_calls.append(slug)
        self._inner.load(slug)

    def search(self, slug: str, vector: list[float], top_n: int):
        return self._inner.search(slug, vector, top_n)


def test_write_document_leaves_no_rows_behind_when_faiss_add_fails(engine, store, embedder):
    doc_id = _insert_document(engine)
    flaky = _FlakyVectorStore(store, fail_on="add")
    writer = IndexWriter(engine, flaky, embedder)

    with pytest.raises(RuntimeError, match="FAISS add failed"):
        writer.write_document(doc_id, "hr", _make_chunks(3))

    with engine.connect() as conn:
        rows = conn.execute(select(chunks).where(chunks.c.document_id == doc_id)).fetchall()
    assert rows == []
    assert _chunk_fts_count_for(engine, doc_id) == 0
    assert _vector_count(store, "hr") == 0


def test_write_document_leaves_no_rows_behind_when_persist_fails(engine, store, embedder):
    doc_id = _insert_document(engine)
    flaky = _FlakyVectorStore(store, fail_on="persist")
    writer = IndexWriter(engine, flaky, embedder)

    with pytest.raises(RuntimeError, match="FAISS persist failed"):
        writer.write_document(doc_id, "hr", _make_chunks(3))

    with engine.connect() as conn:
        rows = conn.execute(select(chunks).where(chunks.c.document_id == doc_id)).fetchall()
    assert rows == []
    # The in-memory index was rolled back to what is on disk, so the
    # unpersisted vectors do not survive to disagree with the empty table.
    assert _vector_count(store, "hr") == 0


def test_a_failed_write_does_not_disturb_an_already_indexed_document(engine, store, embedder):
    first_id = _insert_document(engine, sha256="a" * 64)
    IndexWriter(engine, store, embedder).write_document(first_id, "hr", _make_chunks(2))
    assert _vector_count(store, "hr") == 2

    second_id = _insert_document(engine, sha256="b" * 64)
    flaky = IndexWriter(engine, _FlakyVectorStore(store, fail_on="add"), embedder)
    with pytest.raises(RuntimeError):
        flaky.write_document(second_id, "hr", _make_chunks(3))

    assert _vector_count(store, "hr") == 2
    with engine.connect() as conn:
        surviving = conn.execute(select(chunks.c.id)).fetchall()
    assert len(surviving) == 2


def test_remove_document_keeps_rows_when_the_vector_removal_fails(engine, store, embedder):
    doc_id = _insert_document(engine)
    IndexWriter(engine, store, embedder).write_document(doc_id, "hr", _make_chunks(3))

    flaky = IndexWriter(engine, _FlakyVectorStore(store, fail_on="remove"), embedder)
    with pytest.raises(RuntimeError, match="FAISS remove failed"):
        flaky.remove_document(doc_id)

    # Both stores still hold all three chunks — split state would be one
    # store emptied and the other not.
    with engine.connect() as conn:
        rows = conn.execute(select(chunks).where(chunks.c.document_id == doc_id)).fetchall()
    assert len(rows) == 3
    assert _chunk_fts_count_for(engine, doc_id) == 3
    assert _vector_count(store, "hr") == 3


def test_reassign_keeps_both_stores_on_the_old_space_when_the_move_fails(
    engine, store, embedder
):
    doc_id = _insert_document(engine, intent_slug="hr")
    IndexWriter(engine, store, embedder).write_document(doc_id, "hr", _make_chunks(3))

    flaky = IndexWriter(engine, _FlakyVectorStore(store, fail_on="move"), embedder)
    with pytest.raises(RuntimeError, match="FAISS move failed"):
        flaky.reassign_document(doc_id, "legal")

    with engine.connect() as conn:
        doc_slug = conn.execute(
            select(documents.c.intent_slug).where(documents.c.id == doc_id)
        ).scalar_one()
        chunk_slugs = {
            row.intent_slug
            for row in conn.execute(
                select(chunks.c.intent_slug).where(chunks.c.document_id == doc_id)
            ).fetchall()
        }
    assert doc_slug == "hr"
    assert chunk_slugs == {"hr"}
    assert _vector_count(store, "hr") == 3
    assert _vector_count(store, "legal") == 0
