"""Document lifecycle operations: re-parse, reassign, delete, full re-index.

`spec: document-ingestion` §§ "Re-parsing an existing document",
"Full re-index", and "Document deletion". Reassignment
(§ "Intent space assignment at ingest" § "Admin overrides the
suggestion") is already fully implemented by
`app/rag/index_writer.py::IndexWriter.reassign_document` — moving vectors
between space indexes without re-embedding — so `reassign_document` here
is a thin, validated wrapper: it rejects a slug that names no configured
intent space before delegating.

Re-parse and delete both build on `app/ingest/worker.py`: re-parse reuses
`load_and_chunk` (the same load -> repair -> chunk pipeline
`ingest_document` uses) and the same status-transition helpers, but never
calls `suggest_intent` — the document keeps its existing intent space
rather than getting a fresh suggestion. Delete reuses
`IndexWriter.remove_document` for the chunks/chunk_fts/FAISS cleanup, then
removes the `documents` row itself (`query_log` carries no foreign key to
`documents` — see `app/db.py` — so its rows survive untouched).

Full re-index is the one operation that does not go through `IndexWriter`
at all: it re-embeds every chunk already stored in `chunks` with whatever
embedding provider `deps` currently holds and rebuilds each intent space's
FAISS index from scratch, then records the new model/dimension via
`app/rag/index_meta.py::write_meta` — the record `assert_compatible` (and
the `ConfigService` guard built from it in `app/bootstrap.py`) checks
against on every future config update.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, select

from app.db import chunks as chunks_table
from app.db import documents as documents_table
from app.ingest.worker import IngestDeps, _mark_failed, _mark_indexed, _set_status, load_and_chunk
from app.rag.index_meta import write_meta


def reparse_document(doc_id: int, path: Path, deps: IngestDeps) -> None:
    """Replace `doc_id`'s chunks and index entries with freshly generated
    ones loaded from `path`, preserving its id and current intent space.

    If loading/repairing/chunking `path` fails, the document's existing
    chunks and vectors are left completely untouched — nothing has been
    torn down yet — and status becomes `failed`. If the swap itself fails
    partway (embedding/indexing), whatever was written is cleaned up via
    `IndexWriter.remove_document`, so no orphaned vectors survive either
    way; the document may end up with zero chunks, but never an
    inconsistent mix of old and new.
    """
    with deps.engine.connect() as conn:
        row = conn.execute(
            select(documents_table.c.intent_slug).where(documents_table.c.id == doc_id)
        ).one_or_none()
    if row is None:
        return
    intent_slug = row.intent_slug

    _set_status(deps.engine, doc_id, "parsing")

    try:
        _blocks, chunk_list = load_and_chunk(path, deps.cfg, deps.classify_llm, deps.loaders)
    except Exception as exc:
        _mark_failed(deps.engine, doc_id, str(exc))
        return

    try:
        deps.index_writer.remove_document(doc_id)
        deps.index_writer.write_document(doc_id, intent_slug, chunk_list)
    except Exception as exc:
        deps.index_writer.remove_document(doc_id)
        _mark_failed(deps.engine, doc_id, str(exc))
        return

    _mark_indexed(deps.engine, doc_id, intent_slug=intent_slug, chunk_count=len(chunk_list))


def reassign_document(doc_id: int, new_slug: str, deps: IngestDeps) -> None:
    """Move `doc_id` to intent space `new_slug`: vectors move between
    FAISS space indexes, chunk_fts entries stay valid with their recorded
    space updated, and the document is never re-parsed or re-embedded.

    Raises `ValueError` if `new_slug` does not name a configured intent
    space — `IndexWriter.reassign_document` has no reason to know about
    `cfg.intent_spaces`, so that validation belongs here.
    """
    valid_slugs = {space.slug for space in deps.cfg.intent_spaces}
    if new_slug not in valid_slugs:
        raise ValueError(
            f"{new_slug!r} is not a configured intent space; configured "
            f"spaces: {', '.join(sorted(valid_slugs))}"
        )
    deps.index_writer.reassign_document(doc_id, new_slug)


def delete_document(doc_id: int, deps: IngestDeps) -> None:
    """Delete `doc_id`: its chunks, chunk_fts entries, and vectors in both
    indexes are removed, and the `documents` row itself is removed so the
    document no longer appears in listings or retrieval. `query_log` rows
    referencing `doc_id` are untouched — the table carries no foreign key
    to `documents` precisely so a document's usage history survives its
    own deletion.
    """
    deps.index_writer.remove_document(doc_id)
    with deps.engine.begin() as conn:
        conn.execute(delete(documents_table).where(documents_table.c.id == doc_id))


def reindex_all(deps: IngestDeps) -> None:
    """Re-embed every chunk currently in `chunks` with `deps.embedding`
    (the currently configured provider), rebuild every intent space's
    FAISS index from scratch, and record the new model/dimension.

    Source files are never re-read and `chunks` rows are never rewritten —
    only their vectors change, per `spec: document-ingestion` §
    "Full re-index": "without re-uploading the source files."
    """
    with deps.engine.connect() as conn:
        rows = conn.execute(
            select(chunks_table.c.id, chunks_table.c.intent_slug, chunks_table.c.text)
        ).fetchall()

    by_slug: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in rows:
        by_slug[row.intent_slug].append((row.id, row.text))

    batch_size = deps.cfg.embedding.batch_size
    for slug, entries in by_slug.items():
        ids = [entry[0] for entry in entries]
        texts = [entry[1] for entry in entries]
        vectors = _embed_batched(deps.embedding, texts, batch_size)

        deps.vector_store.delete_space(slug)
        deps.vector_store.create_space(slug)
        deps.vector_store.add(slug, ids, vectors)
        deps.vector_store.persist(slug)

    write_meta(
        Path(deps.cfg.storage.faiss_dir),
        model=deps.cfg.embedding.model,
        dimension=deps.cfg.embedding.dimension,
    )


def _embed_batched(embedding, texts: list[str], batch_size: int) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors.extend(embedding.embed(batch))
    return vectors
