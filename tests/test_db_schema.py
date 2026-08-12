"""Tests for the SQLite schema — test-plan §7.

Every test uses a real on-disk SQLite file (via pytest's `tmp_path`) rather
than `:memory:` — WAL mode is a no-op for in-memory databases, so a real
file is required to prove `PRAGMA journal_mode` actually took effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db import chunk_fts, chunks, create_engine_for, documents, init_schema, integrations, query_log


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


def _insert_document(conn, sha256: str = "a" * 64):
    result = conn.execute(
        insert(documents).values(
            filename="policy.pdf",
            ext=".pdf",
            size_bytes=1024,
            sha256=sha256,
            intent_slug="hr",
            status="indexed",
            error_message=None,
            chunk_count=1,
            uploaded_at="2026-08-09T00:00:00Z",
            indexed_at="2026-08-09T00:01:00Z",
        )
    )
    return result.inserted_primary_key[0]


def test_all_tables_present(engine):
    table_names = set(inspect(engine).get_table_names())
    for name in ("documents", "chunks", "query_log", "integrations", "chunk_fts"):
        assert name in table_names


def test_wal_journal_mode_enabled(engine):
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"


def _insert_chunk(conn, doc_id: int, body: str, ordinal: int = 0) -> int:
    """Insert a chunk. The FTS row is written by trigger, never by hand."""
    return conn.execute(
        insert(chunks).values(
            document_id=doc_id,
            intent_slug="hr",
            ordinal=ordinal,
            text=body,
            heading_path=None,
            source_ref=None,
            char_count=len(body),
        )
    ).inserted_primary_key[0]


def _fts_rowids(engine, term: str) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH :term"),
            {"term": term},
        ).fetchall()
    return [row[0] for row in rows]


def test_fts5_match_finds_inserted_chunk_by_rowid(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        chunk_id = _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")

    assert _fts_rowids(engine, "vacation") == [chunk_id]


def test_bm25_ranking_available(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")

    with engine.connect() as conn:
        score = conn.execute(
            text("SELECT bm25(chunk_fts) FROM chunk_fts WHERE chunk_fts MATCH :term"),
            {"term": "vacation"},
        ).scalar()

    assert isinstance(score, float)


def test_chunk_fts_is_external_content_linked_to_chunks(engine):
    """The "chunk_fts rowid equals chunks.id" convention must be enforced by
    the schema, not left as a convention a caller can forget.
    """
    with engine.connect() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'chunk_fts'")
        ).scalar()

    assert "content='chunks'" in ddl
    assert "content_rowid='id'" in ddl


def test_cascade_delete_of_a_document_removes_its_fts_rows(engine):
    """Deleting a document cascades to its chunks; without the delete trigger
    the FTS index kept orphaned rows, so keyword search would return hits
    for chunks that no longer exist.
    """
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        chunk_id = _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")
    assert _fts_rowids(engine, "vacation") == [chunk_id]

    with engine.begin() as conn:
        conn.execute(documents.delete().where(documents.c.id == doc_id))

    assert _fts_rowids(engine, "vacation") == []


def test_deleting_a_chunk_directly_removes_its_fts_row(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        chunk_id = _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")

    with engine.begin() as conn:
        conn.execute(chunks.delete().where(chunks.c.id == chunk_id))

    assert _fts_rowids(engine, "vacation") == []


def test_updating_chunk_text_reindexes_it(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        chunk_id = _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")

    with engine.begin() as conn:
        conn.execute(
            chunks.update()
            .where(chunks.c.id == chunk_id)
            .values(text="Employees accrue sabbatical days monthly.")
        )

    assert _fts_rowids(engine, "vacation") == []
    assert _fts_rowids(engine, "sabbatical") == [chunk_id]


def test_deleting_document_cascades_to_chunks(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        conn.execute(
            insert(chunks).values(
                document_id=doc_id,
                intent_slug="hr",
                ordinal=0,
                text="chunk one",
                heading_path=None,
                source_ref=None,
                char_count=9,
            )
        )
        conn.execute(
            insert(chunks).values(
                document_id=doc_id,
                intent_slug="hr",
                ordinal=1,
                text="chunk two",
                heading_path=None,
                source_ref=None,
                char_count=9,
            )
        )

    with engine.begin() as conn:
        conn.execute(documents.delete().where(documents.c.id == doc_id))

    with engine.connect() as conn:
        remaining = conn.execute(select(chunks)).fetchall()
    assert remaining == []


def test_deleting_document_leaves_query_log_intact(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
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

    with engine.begin() as conn:
        conn.execute(documents.delete().where(documents.c.id == doc_id))

    with engine.connect() as conn:
        rows = conn.execute(select(query_log)).fetchall()
    assert len(rows) == 1
    assert rows[0].retrieved_doc_ids_json == f"[{doc_id}]"


def test_duplicate_sha256_rejected(engine):
    with engine.begin() as conn:
        _insert_document(conn, sha256="b" * 64)

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_document(conn, sha256="b" * 64)
