"""Shared FTS5 query escaping.

Extracted out of `app/api/documents.py::_fts_query` so document admin
search and keyword retrieval (`app/rag/retrieve/keyword.py`) share one
escaper instead of growing two subtly different ones. Increment 03 shipped
the unescaped version once already: raw user input passed straight into
FTS5 `MATCH` 500'd on ordinary input like `annual-leave` or `it's`.

Increment 04's read-path demo caught a second defect in the same
function: joining terms with a bare space, which FTS5 reads as an
implicit AND. A bare-terms query like "Band 3" still matched under that
join, so every keyword-retrieval test passed — but a natural-language
question ("What is the Mid salary for Band 3?") carries filler words,
and requiring every one of them (including "What", "is", "the", "for")
to appear in the same chunk matched nothing. In the live demo this
showed up as `Keyword hits (0)` for every real question asked, silently
degrading the hybrid retriever to dense-only and defeating the entire
reason it exists: surfacing exact tokens ("Band 3", "L4", "Form FIN-204")
that arrive inside a sentence, not as a bare query.
"""

from __future__ import annotations


def fts_query(q: str, op: str = "OR") -> str:
    """Turn free-typed search text into a safe FTS5 `MATCH` expression.

    FTS5's `MATCH` argument is a query *language*, not a string: `-`
    introduces a column filter, `AND`/`OR`/`NOT`/`NEAR` are operators, and
    `"`, `(`, `*`, `^`, `:` are all syntax. Passing raw user input through
    meant `annual-leave`, `it's`, `foo(` and a bare `"` each raised
    `OperationalError` — a 500 for input nobody would call malformed.

    Every whitespace-separated term becomes a quoted FTS5 string (with any
    embedded `"` doubled, its only escape), which turns the whole thing
    into a literal phrase search. Multiple terms are joined with `op`,
    which defaults to FTS5's `OR`: BM25 (`app/rag/retrieve/keyword.py`)
    needs to rank chunks by term *overlap*, not reject any chunk missing
    one word of a natural-language question. No term is ever dropped to
    get there — there is no stopword list, short or otherwise. `L4`,
    `W-4`, and a bare `3` are exactly the short exact tokens keyword
    retrieval exists to find, so nothing here is allowed to discard them.
    A filler word like "the" is harmless left in: BM25's own IDF weighting
    already discounts terms that appear in most chunks, so a chunk that
    only matches "the" or "is" ranks far below one that also matches
    "Band" and "3" — no separate filtering is needed to get that effect.

    `app/api/documents.py`'s admin document search passes `op="AND"`
    instead: it is a user-typed filter box, not a ranked retrieval feed,
    and narrowing the result set on every word typed is the behaviour a
    search box implies (typing an extra word should narrow results, not
    widen them). The two call sites want different semantics for that
    reason, not by oversight.

    Returns "" when the input holds no terms at all; there is no valid
    empty `MATCH` expression, so callers skip the FTS clause entirely.
    """
    terms = q.split()
    if not terms:
        return ""
    quoted = ('"' + term.replace('"', '""') + '"' for term in terms)
    return f" {op} ".join(quoted)
