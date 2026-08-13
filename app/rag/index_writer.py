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

Reassignment moves vectors between space indexes without re-embedding:
`VectorStore.move` reconstructs vectors from the source index rather than
calling the embedder, so a reassignment costs one dense move plus a small
SQL update, never a round trip to the embedding provider.
"""

from __future__ import annotations

from sqlalchemy import Engine, delete, select, update

from app.db import chunks as chunks_table
from app.db import documents as documents_table
from app.providers.base import EmbeddingProvider
from app.rag.chunker import Chunk
from app.rag.vector_store import VectorStore

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

    def write_document(self, doc_id: int, slug: str, chunks: list[Chunk]) -> None:
        """Insert `chunks` for `doc_id` into the `chunks` table (which the
        FTS5 triggers mirror automatically) and add their embeddings to
        `slug`'s FAISS index, keyed by the generated `chunks.id`.
        """
        if not chunks:
            return

        chunk_ids: list[int] = []
        with self._engine.begin() as conn:
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

        vectors = self._embed_batched([chunk.text for chunk in chunks])

        self._vector_store.create_space(slug)
        self._vector_store.add(slug, chunk_ids, vectors)
        self._vector_store.persist(slug)

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

        for slug, ids in ids_by_slug.items():
            self._vector_store.remove(slug, ids)
            self._vector_store.persist(slug)

    def reassign_document(self, doc_id: int, new_slug: str) -> None:
        """Move every chunk of `doc_id` to `new_slug`: update `chunks` and
        `documents` in place, then move the matching vectors between FAISS
        indexes — never re-embedding, since the vectors themselves do not
        change, only which space they live in.
        """
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(chunks_table.c.id, chunks_table.c.intent_slug).where(
                    chunks_table.c.document_id == doc_id
                )
            ).all()
            if not rows:
                return
            old_slug = rows[0].intent_slug
            chunk_ids = [row.id for row in rows]

            conn.execute(
                update(chunks_table)
                .where(chunks_table.c.document_id == doc_id)
                .values(intent_slug=new_slug)
            )
            conn.execute(
                update(documents_table)
                .where(documents_table.c.id == doc_id)
                .values(intent_slug=new_slug)
            )

        if old_slug == new_slug:
            return

        self._vector_store.create_space(new_slug)
        self._vector_store.move(old_slug, new_slug, chunk_ids)
        self._vector_store.persist(old_slug)
        self._vector_store.persist(new_slug)
