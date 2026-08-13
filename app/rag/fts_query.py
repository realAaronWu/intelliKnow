"""Shared FTS5 query escaping.

Extracted out of `app/api/documents.py::_fts_query` so document admin
search and keyword retrieval (`app/rag/retrieve/keyword.py`) share one
escaper instead of growing two subtly different ones. Increment 03 shipped
the unescaped version once already: raw user input passed straight into
FTS5 `MATCH` 500'd on ordinary input like `annual-leave` or `it's`.
"""

from __future__ import annotations


def fts_query(q: str) -> str:
    """Turn free-typed search text into a safe FTS5 `MATCH` expression.

    FTS5's `MATCH` argument is a query *language*, not a string: `-`
    introduces a column filter, `AND`/`OR`/`NOT`/`NEAR` are operators, and
    `"`, `(`, `*`, `^`, `:` are all syntax. Passing raw user input through
    meant `annual-leave`, `it's`, `foo(` and a bare `"` each raised
    `OperationalError` — a 500 for input nobody would call malformed.

    Every whitespace-separated term becomes a quoted FTS5 string (with any
    embedded `"` doubled, its only escape), which turns the whole thing
    into a literal phrase search. Multiple terms sit side by side, which
    FTS5 reads as an implicit AND — the behaviour a search box implies.
    Returns "" when the input holds no terms at all; there is no valid
    empty `MATCH` expression, so callers skip the FTS clause entirely.
    """
    terms = q.split()
    return " ".join('"' + term.replace('"', '""') + '"' for term in terms)
