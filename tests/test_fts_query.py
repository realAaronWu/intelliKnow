"""Direct tests for `app/rag/fts_query.py::fts_query`.

The defect: joining quoted terms with a bare space makes FTS5 read the
whole expression as an implicit AND, so every term must appear in a chunk
for it to match. A natural-language question carries filler words
("What is the Mid salary for Band 3?"), so the AND-of-every-term
expression matches nothing — keyword retrieval returns zero hits for
exactly the kind of input a user actually types. See
`tests/test_keyword_retrieval.py::test_2_6_...` and
`tests/test_l2_hybrid_justification.py` for the same defect proven
against real FTS5 and a real corpus.
"""

from __future__ import annotations

from app.rag.fts_query import fts_query


def test_multi_term_query_joins_with_or_by_default():
    assert fts_query("Band 3") == '"Band" OR "3"'


def test_single_term_query_has_no_operator():
    assert fts_query("annual") == '"annual"'


def test_natural_language_question_is_not_all_required():
    # The literal bug: every one of these words used to be AND-ed
    # together, so a chunk had to contain all eight to match anything.
    result = fts_query("What is the Mid salary for Band 3?")
    assert " AND " not in result
    assert result.count(" OR ") == 7  # 8 terms, 7 join points


def test_short_tokens_are_never_dropped():
    # `L4` and `W-4` are exactly the short, real tokens keyword retrieval
    # exists to find (see the module docstring) — no stopword list here,
    # so nothing is silently discarded regardless of length.
    assert fts_query("L4") == '"L4"'
    assert fts_query("W-4") == '"W-4"'
    assert fts_query("a") == '"a"'


def test_quoting_and_escaping_unchanged_by_the_or_fix():
    # The embedded-quote escaping was itself a fix for an earlier 500
    # (see the module docstring) and must survive this change untouched.
    assert fts_query('bare "quote') == '"bare" OR """quote"'


def test_empty_input_still_returns_empty_string():
    assert fts_query("") == ""
    assert fts_query("   ") == ""


def test_op_parameter_allows_and_semantics_for_a_narrowing_search_box():
    # `app/api/documents.py` deliberately keeps AND semantics for its
    # admin document search (a user-typed filter box, not a ranked
    # retrieval feed) — see that module's call site for the full reason.
    assert fts_query("Band 3", op="AND") == '"Band" AND "3"'
