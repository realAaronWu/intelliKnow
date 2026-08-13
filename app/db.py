"""SQLite schema for IntelliKnow KMS.

Five tables via SQLAlchemy Core, WAL mode, timestamps UTC ISO-8601 — see
`openspec/changes/add-intelliknow-kms/design.md` § Data model for the
authoritative column list and rationale.

SQLite disables foreign-key enforcement per connection by default, so
`PRAGMA foreign_keys = ON` is applied on every new DBAPI connection (not
just once) — otherwise the `documents` → `chunks` cascade silently does
nothing. `query_log` deliberately carries no foreign key to `documents`:
deleting a document must not erase the history of it having been used.

`chunk_fts` is an FTS5 *external content* table over `chunks`, kept in step
by triggers — see the comment above `_CREATE_CHUNK_FTS`.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Engine,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    table,
    text,
)

metadata = MetaData()

documents = Table(
    "documents",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("filename", String, nullable=False),
    Column("ext", String, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", String, nullable=False, unique=True),
    Column("intent_slug", String, nullable=False),
    # "model" when `intent_slug` came from the LLM's own suggestion;
    # otherwise names why a fallback was used instead ("provider_error" /
    # "invalid_slug" — see `app/ingest/classify_doc.py::IntentSuggestion`).
    # Defaults to "model" so a row that never reaches a real assignment
    # (e.g. ingestion fails before intent suggestion runs) renders with no
    # fallback annotation in `scripts/ingest.py`, matching prior behaviour.
    Column("intent_assigned_by", String, nullable=False, default="model"),
    Column("status", String, nullable=False),
    Column("error_message", Text, nullable=True),
    Column("chunk_count", Integer, nullable=False, default=0),
    Column("uploaded_at", String, nullable=False),
    Column("indexed_at", String, nullable=True),
)

chunks = Table(
    "chunks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "document_id",
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("intent_slug", String, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("heading_path", String, nullable=True),
    Column("source_ref", String, nullable=True),
    Column("char_count", Integer, nullable=False),
)

# Deliberately no ForeignKey to `documents` — see module docstring.
query_log = Table(
    "query_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("created_at", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("user_ref", String, nullable=True),
    Column("question", Text, nullable=False),
    Column("intent_slug", String, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("classified_by", String, nullable=True),
    Column("reasoning", Text, nullable=True),
    Column("fallback_used", Boolean, nullable=False, default=False),
    Column("status", String, nullable=False),
    Column("answer", Text, nullable=True),
    Column("citations_json", Text, nullable=True),
    Column("retrieved_doc_ids_json", Text, nullable=True),
    Column("latency_ms", Integer, nullable=True),
    Column("error", Text, nullable=True),
)

integrations = Table(
    "integrations",
    metadata,
    Column("channel", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("enabled", Boolean, nullable=False, default=False),
    Column("credentials_encrypted", Text, nullable=True),
    Column("status", String, nullable=True),
    Column("last_ok_at", String, nullable=True),
    Column("last_error", Text, nullable=True),
    Column("updated_at", String, nullable=False),
)

# FTS5 virtual tables are not representable as a SQLAlchemy `Table` (SQLite's
# `CREATE VIRTUAL TABLE ... USING fts5(...)` DDL has no Core equivalent), so
# `chunk_fts` is a lightweight, unbound `table()` construct: it can be used
# to build `select()` / `insert()` statements, but `metadata.create_all()`
# does not know about it — `init_schema` creates it with raw DDL instead.
chunk_fts = table("chunk_fts", Column("text", Text))

# `content='chunks', content_rowid='id'` makes `chunk_fts` an *external
# content* table: `chunks` owns the text, and the FTS table stores only the
# index. That is what makes "chunk_fts rowid equals chunks.id" a property of
# the schema rather than a convention every caller has to remember, and it
# stops the two stores from diverging — a cascade delete of a document used
# to leave orphaned FTS rows, so keyword search could return hits for chunks
# that no longer existed.
#
# External content tables are not maintained automatically; the triggers
# below are the standard FTS5 pattern for keeping the index in step. The
# AFTER DELETE trigger also fires for rows removed by the
# `documents` -> `chunks` ON DELETE CASCADE, so no separate cleanup is
# needed (verified against the SQLite build in use).
_CREATE_CHUNK_FTS = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts "
    "USING fts5(text, content='chunks', content_rowid='id')"
)

_CREATE_CHUNK_FTS_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS chunks_after_insert AFTER INSERT ON chunks BEGIN
        INSERT INTO chunk_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_after_delete AFTER DELETE ON chunks BEGIN
        INSERT INTO chunk_fts(chunk_fts, rowid, text) VALUES('delete', old.id, old.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_after_update AFTER UPDATE ON chunks BEGIN
        INSERT INTO chunk_fts(chunk_fts, rowid, text) VALUES('delete', old.id, old.text);
        INSERT INTO chunk_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
)


def _configure_connection(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
    finally:
        cursor.close()


def create_engine_for(path: Path) -> Engine:
    """Create an `Engine` for the SQLite file at `path`.

    Foreign-key enforcement and WAL journal mode are applied on every new
    DBAPI connection via a `connect` event listener, since both are
    per-connection settings in SQLite.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _configure_connection)
    return engine


def init_schema(engine: Engine) -> None:
    """Create every table, the `chunk_fts` FTS5 index, and its sync triggers.

    The triggers are created with the index because an external content FTS
    table without them is silently empty — `chunks` writes would never reach
    the index.
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(_CREATE_CHUNK_FTS))
        for trigger_ddl in _CREATE_CHUNK_FTS_TRIGGERS:
            conn.execute(text(trigger_ddl))
