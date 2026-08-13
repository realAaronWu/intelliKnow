"""Assertions about the keyword index that actually consult it.

`chunk_fts` is an FTS5 **external content** table, which means `chunks`
owns the text and the FTS table stores only the index. A consequence that
is easy to miss: `SELECT count(*) FROM chunk_fts` — and any join over it
that carries no `MATCH` — full-scans the *content table*, so it returns
the chunks count whether or not the index is in step. Every
"three stores agree" assertion in this suite was built on that shape, and
so passed unconditionally; deleting the sync triggers and re-running them
changes nothing. That made the one invariant `app/rag/index_writer.py`
exists to guarantee the one thing not under test.

`fts_indexed_chunk_count` looks each chunk up through `MATCH`, so a chunk
the index never learned about is not counted, and
`assert_keyword_index_in_sync` runs FTS5's own content-vs-index check.
`tests/test_db_schema.py` proves both actually fail when a sync trigger is
missing.
"""

from __future__ import annotations

import re

from sqlalchemy import Engine, select, text

from app.db import chunks as chunks_table

_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _distinctive_term(chunk_text: str) -> str | None:
    """The longest alphanumeric token in `chunk_text`, lowercased.

    Longest because it is the least likely to be a stop-ish token shared
    with everything else — but any token from the chunk's own stored text
    would do, since the lookup is pinned to that chunk's rowid anyway.
    """
    tokens = _TOKEN.findall(chunk_text)
    if not tokens:
        return None
    return max(tokens, key=len).lower()


def fts_indexed_chunk_count(engine: Engine, doc_id: int) -> int:
    """How many of `doc_id`'s chunks the keyword index can actually find.

    Each chunk is looked up by `MATCH` on a term drawn from its own stored
    text, constrained to its own rowid, so the result reflects the state
    of the FTS index rather than of the `chunks` table it shadows.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            select(chunks_table.c.id, chunks_table.c.text).where(
                chunks_table.c.document_id == doc_id
            )
        ).fetchall()

        found = 0
        for row in rows:
            term = _distinctive_term(row.text)
            if term is None:
                continue
            hit = conn.execute(
                text(
                    "SELECT 1 FROM chunk_fts "
                    "WHERE chunk_fts MATCH :term AND rowid = :rowid"
                ),
                {"term": f'"{term}"', "rowid": row.id},
            ).first()
            if hit is not None:
                found += 1
    return found


def assert_keyword_index_in_sync(engine: Engine) -> None:
    """Assert FTS5's own index-vs-content check passes. See
    `app/db.py::check_fts_integrity`.
    """
    from app.db import check_fts_integrity

    check_fts_integrity(engine)
