"""Keyword (BM25) retrieval — the exact-token half of the hybrid design.

`keyword_search` is the reason a hybrid retriever exists at all: a rare
exact token — a band label, a form number, a section reference — can rank
poorly under dense/semantic search but is exactly what BM25 over `chunk_fts`
is built to find. It runs directly over the FTS5 index that
`app/db.py::chunk_fts`'s sync triggers keep in step with `chunks`, filtered
to the caller's spaces by an ordinary SQL join — no extra bookkeeping table
is needed, since `chunks.intent_slug` is already there to join against.

`top_n` of zero is the documented way to disable keyword retrieval without
touching code (`spec: knowledge-retrieval` § "Keyword retrieval disabled by
configuration") — handled here as a fast, allocation-free early return
rather than a `LIMIT 0` round-trip to SQLite.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.rag.fts_query import fts_query
from app.rag.retrieve.dense import Hit


def keyword_search(question: str, spaces: list[str], top_n: int, engine: Engine) -> list[Hit]:
    if top_n <= 0 or not spaces:
        return []

    match_query = fts_query(question)
    if not match_query:
        # No tokenizable terms (e.g. an all-whitespace question) — there is
        # no valid empty FTS5 MATCH expression, and nothing to search for.
        return []

    space_params = {f"space{i}": slug for i, slug in enumerate(spaces)}
    placeholders = ", ".join(f":{name}" for name in space_params)

    # `bm25(chunk_fts)` is SQLite FTS5's relevance score: lower is a
    # *better* match (it is a cost, not a similarity), so the best matches
    # sort first under a plain ascending ORDER BY.
    sql = (  # noqa: S608 — every interpolated value below is a bound param
        "SELECT chunks.id AS chunk_id, bm25(chunk_fts) AS score "
        "FROM chunk_fts "
        "JOIN chunks ON chunk_fts.rowid = chunks.id "
        "WHERE chunk_fts MATCH :match "
        f"AND chunks.intent_slug IN ({placeholders}) "
        "ORDER BY score "
        "LIMIT :top_n"
    )
    params: dict[str, object] = {"match": match_query, "top_n": top_n, **space_params}

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).all()

    return [
        Hit(chunk_id=row.chunk_id, score=float(row.score), source="keyword") for row in rows
    ]
