"""Index writer: keeps the `chunks` row, `chunk_fts`, and the space's FAISS
index consistent with each other — the one invariant this module exists to
guarantee.

`chunk_fts` is an external-content FTS5 table with INSERT/UPDATE/DELETE
triggers already wired in `app/db.py` (`chunks_after_insert` /
`chunks_after_delete` / `chunks_after_update`), so it tracks `chunks`
automatically. This module never writes to `chunk_fts` directly — doing so
would risk the two diverging, exactly the failure the triggers exist to
prevent — it only ever writes to `chunks` and lets the triggers follow.

FAISS gets no such automatic sync, so every path here that inserts,
deletes, or moves `chunks` rows performs the matching `VectorStore` call in
the same method, and persists the affected space(s) before returning.

**Every mutation is all-or-nothing across both stores.** The SQL runs
inside a transaction that stays open across the matching FAISS work, so a
`VectorStore` failure rolls the SQL back rather than committing a database
state the index does not share. FAISS itself has no transaction, so its
compensation is `_discard_unpersisted`: reloading the affected space from
its `.index` file throws away whatever the failed operation had done in
memory and restores exactly the state the rolled-back database still
agrees with. (Previously each method committed its SQL first and only then
touched FAISS, with no repair at all — a failed `VectorStore.move` left
the database naming one space and the index another, the document still
marked `indexed`, and nothing anywhere noticing.)

Reassignment moves vectors between space indexes without re-embedding:
`VectorStore.move` reconstructs vectors from the source index rather than
calling the embedder, so a reassignment costs one dense move plus a small
SQL update, never a round trip to the embedding provider.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, delete, select, update

from app.db import chunks as chunks_table
from app.db import documents as documents_table
from app.providers.base import EmbeddingProvider
from app.rag.chunker import Chunk
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 64


class IndexWriter:
    """Writes, removes, and reassigns a document's chunks across all three
    stores: the `chunks` table, `chunk_fts` (via trigger), and FAISS.
    """

    def __init__(
        self,
        engine: Engine,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._engine = engine
        self._vector_store = vector_store
        self._embedder = embedder
        self._batch_size = batch_size

    def _embed_batched(self, texts: list[str]) -> list[list[float]]:
        """Embed `texts` in groups of `batch_size`, preserving order."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(self._embedder.embed(batch))
        return vectors

    def _discard_unpersisted(self, *slugs: str) -> None:
        """Roll each space's in-memory index back to its persisted file.

        FAISS has no transaction, so this is how a failed mutation is
        compensated: whatever the operation managed to do in memory is
        thrown away and the space returns to the state on disk — the state
        the accompanying rolled-back SQL still agrees with.

        Best-effort by design. A failure here must not replace the
        exception that is already propagating, which is the one that says
        what actually went wrong.
        """
        for slug in slugs:
            try:
                self._vector_store.load(slug)
            except Exception:
                logger.exception(
                    "could not roll back in-memory index for space %r after a "
                    "failed write; it may disagree with the chunks table until "
                    "the next restart or re-index",
                    slug,
                )

    def write_document(self, doc_id: int, slug: str, chunks: list[Chunk]) -> None:
        """Insert `chunks` for `doc_id` into the `chunks` table (which the
        FTS5 triggers mirror automatically) and add their embeddings to
        `slug`'s FAISS index, keyed by the generated `chunks.id`.

        Embedding happens first, outside the transaction: it is the most
        failure-prone step and the only one that mutates nothing, so an
        embedding-provider outage costs no rollback at all. Both stores are
        then written inside one transaction — see the module docstring.
        """
        if not chunks:
            return

        vectors = self._embed_batched([chunk.text for chunk in chunks])

        with self._engine.begin() as conn:
            chunk_ids: list[int] = []
            for chunk in chunks:
                result = conn.execute(
                    chunks_table.insert().values(
                        document_id=doc_id,
                        intent_slug=slug,
                        ordinal=chunk.ordinal,
                        text=chunk.text,
                        heading_path=" > ".join(chunk.heading_path) or None,
                        source_ref=chunk.source_ref,
                        char_count=chunk.char_count,
                    )
                )
                chunk_ids.append(result.inserted_primary_key[0])

            try:
                self._vector_store.create_space(slug)
                self._vector_store.add(slug, chunk_ids, vectors)
                self._vector_store.persist(slug)
            except BaseException:
                self._discard_unpersisted(slug)
                raise

    def remove_document(self, doc_id: int) -> None:
        """Delete every chunk row for `doc_id` (the FTS5 triggers clear the
        matching `chunk_fts` rows), and remove the matching vectors from
        whichever space(s) they were recorded under. A document with no
        chunks is a silent no-op — `query_log` is never touched, so a
        document's usage history survives its own removal.
        """
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(chunks_table.c.id, chunks_table.c.intent_slug).where(
                    chunks_table.c.document_id == doc_id
                )
            ).all()
            if not rows:
                return
            conn.execute(delete(chunks_table).where(chunks_table.c.document_id == doc_id))

            ids_by_slug: dict[str, list[int]] = {}
            for row in rows:
                ids_by_slug.setdefault(row.intent_slug, []).append(row.id)

            try:
                for slug, ids in ids_by_slug.items():
                    self._vector_store.remove(slug, ids)
                    self._vector_store.persist(slug)
            except BaseException:
                self._discard_unpersisted(*ids_by_slug)
                raise

    def reassign_document(self, doc_id: int, new_slug: str) -> None:
        """Move `doc_id` to `new_slug`: update `documents`, update every
        chunk it has, then move the matching vectors between FAISS indexes
        — never re-embedding, since the vectors themselves do not change,
        only which space they live in.

        The `documents` row moves unconditionally. Having no chunks is the
        normal state of a `pending`, `failed`, or genuinely empty document,
        and reassigning one used to return early *before* the `documents`
        update, so the move silently did nothing while the API reported
        success. Only the vector move is skipped when there is nothing to
        move.
        """
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(chunks_table.c.id, chunks_table.c.intent_slug).where(
                    chunks_table.c.document_id == doc_id
                )
            ).all()

            conn.execute(
                update(documents_table)
                .where(documents_table.c.id == doc_id)
                .values(intent_slug=new_slug)
            )
            if not rows:
                return

            conn.execute(
                update(chunks_table)
                .where(chunks_table.c.document_id == doc_id)
                .values(intent_slug=new_slug)
            )

            # Grouped by source space rather than assuming one: nothing
            # stops a document's chunks from having been recorded under
            # more than one, and a `move` from the wrong source silently
            # loses vectors.
            ids_by_old_slug: dict[str, list[int]] = {}
            for row in rows:
                ids_by_old_slug.setdefault(row.intent_slug, []).append(row.id)
            ids_by_old_slug.pop(new_slug, None)  # already where they belong
            if not ids_by_old_slug:
                return

            try:
                self._vector_store.create_space(new_slug)
                for old_slug, ids in ids_by_old_slug.items():
                    self._vector_store.move(old_slug, new_slug, ids)
                    self._vector_store.persist(old_slug)
                self._vector_store.persist(new_slug)
            except BaseException:
                self._discard_unpersisted(new_slug, *ids_by_old_slug)
                raise
