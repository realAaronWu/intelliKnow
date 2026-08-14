"""Tests for the SQLite schema — test-plan §7.

Every test uses a real on-disk SQLite file (via pytest's `tmp_path`) rather
than `:memory:` — WAL mode is a no-op for in-memory databases, so a real
file is required to prove `PRAGMA journal_mode` actually took effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.db import (
    check_fts_integrity,
    chunk_fts,
    chunks,
    create_engine_for,
    documents,
    init_schema,
    integrations,
    query_log,
    recover_interrupted_documents,
)
from tests.fts_helpers import fts_indexed_chunk_count


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


def test_query_log_has_relevance_observability_column(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("query_log")}

    assert "best_relevance" in columns


def test_integration_table_has_handler_persistence_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("integrations")}

    assert {
        "last_reply_ref",
        "last_error_at",
        "secret_name",
        "active_secret_version",
        "previous_secret_version",
        "pending_secret_version",
        "credential_status",
    } <= columns


def test_init_schema_adds_handler_columns_to_an_existing_integration_table(tmp_path):
    old_engine = create_engine_for(tmp_path / "old.db")
    with old_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE integrations (
                    channel VARCHAR PRIMARY KEY,
                    display_name VARCHAR NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    credentials_encrypted TEXT,
                    status VARCHAR,
                    last_ok_at VARCHAR,
                    last_error TEXT,
                    updated_at VARCHAR NOT NULL
                )
                """
            )
        )

    init_schema(old_engine)

    columns = {column["name"] for column in inspect(old_engine).get_columns("integrations")}
    assert {
        "last_reply_ref",
        "last_error_at",
        "secret_name",
        "active_secret_version",
        "credential_status",
    } <= columns


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


def test_startup_recovery_marks_only_interrupted_documents_failed(engine):
    with engine.begin() as conn:
        pending_id = _insert_document(conn, sha256="c" * 64)
        parsing_id = _insert_document(conn, sha256="d" * 64)
        indexed_id = _insert_document(conn, sha256="e" * 64)
        conn.execute(
            documents.update().where(documents.c.id == pending_id).values(status="pending")
        )
        conn.execute(
            documents.update().where(documents.c.id == parsing_id).values(status="parsing")
        )

    recovered = recover_interrupted_documents(engine)

    with engine.connect() as conn:
        rows = {
            row.id: row
            for row in conn.execute(
                select(documents).where(
                    documents.c.id.in_([pending_id, parsing_id, indexed_id])
                )
            )
        }
    assert recovered == 2
    assert rows[pending_id].status == "failed"
    assert rows[parsing_id].status == "failed"
    assert "interrupted" in rows[pending_id].error_message.lower()
    assert rows[indexed_id].status == "indexed"


# --- The three-stores invariant is actually detectable -------------------------------
#
# `chunk_fts` is an *external content* table, so `SELECT count(*) FROM
# chunk_fts` (and any join over it with no MATCH) full-scans `chunks` and
# reports the chunks count whether or not the index is in step. Every
# "three stores agree" assertion in the suite was built on that shape, and
# `scripts/ingest.py` printed it as the invariant made visible. The tests
# below break the sync deliberately and prove that the replacement
# assertions — `tests/fts_helpers.py` and `app/db.py::check_fts_integrity`
# — notice, and that the old shape does not.


def _drop_insert_trigger(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER chunks_after_insert"))


def test_the_old_count_shape_cannot_detect_a_broken_index(engine):
    """The premise of the fix, pinned: this is why the assertion changed."""
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
    _drop_insert_trigger(engine)
    with engine.begin() as conn:
        _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")

    with engine.connect() as conn:
        counted = conn.execute(text("SELECT count(*) FROM chunk_fts")).scalar_one()

    # The row never reached the index, and the count reports it anyway.
    assert counted == 1
    assert _fts_rowids(engine, "vacation") == []

    init_schema(engine)  # restore the trigger


def test_the_match_based_count_detects_a_broken_index(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")
    assert fts_indexed_chunk_count(engine, doc_id) == 1

    _drop_insert_trigger(engine)
    with engine.begin() as conn:
        _insert_chunk(conn, doc_id, "Expenses are reimbursed within thirty days.", ordinal=1)

    # Two chunk rows, one of which the keyword index never learned about.
    assert _chunk_row_count(engine, doc_id) == 2
    assert fts_indexed_chunk_count(engine, doc_id) == 1

    init_schema(engine)  # restore the trigger


def test_the_integrity_check_detects_a_broken_index(engine):
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
        _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")
    check_fts_integrity(engine)  # in sync: must not raise

    _drop_insert_trigger(engine)
    with engine.begin() as conn:
        _insert_chunk(conn, doc_id, "Expenses are reimbursed within thirty days.", ordinal=1)

    with pytest.raises(DatabaseError):
        check_fts_integrity(engine)

    init_schema(engine)  # restore the trigger


def test_a_restored_trigger_leaves_the_index_checkable_again(engine):
    """The teardown half of the demonstration: recreating the trigger and
    reindexing puts the two stores back in step, so a later assertion is
    meaningful again rather than permanently poisoned.
    """
    with engine.begin() as conn:
        doc_id = _insert_document(conn)
    _drop_insert_trigger(engine)
    with engine.begin() as conn:
        _insert_chunk(conn, doc_id, "Employees accrue vacation days monthly.")

    init_schema(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')"))

    check_fts_integrity(engine)
    assert fts_indexed_chunk_count(engine, doc_id) == 1


def _chunk_row_count(engine, doc_id: int) -> int:
    with engine.connect() as conn:
        rows = conn.execute(select(chunks.c.id).where(chunks.c.document_id == doc_id)).fetchall()
    return len(rows)
