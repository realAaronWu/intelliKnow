"""Test-plan §2 — keyword retrieval.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §2

`keyword_search` runs BM25 over `chunk_fts`, filtered to the supplied
spaces by SQL join. Rare exact tokens — a band label, a form number, a
section reference — are the entire reason the hybrid design exists: dense
search can rank them poorly, so keyword search has to retrieve them
directly (test 2.1). Chunks are inserted through `chunks` directly; the
sync triggers in `app/db.py` populate `chunk_fts` automatically, exactly
as they do in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert

from app.db import chunks, create_engine_for, documents, init_schema
from app.rag.retrieve.keyword import keyword_search


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


def _insert_document(engine, sha256: str = "a" * 64, intent_slug: str = "hr") -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename="policy.pdf",
                ext=".pdf",
                size_bytes=1024,
                sha256=sha256,
                intent_slug=intent_slug,
                status="indexed",
                error_message=None,
                chunk_count=0,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at="2026-08-09T00:00:01Z",
            )
        )
        return result.inserted_primary_key[0]


def _insert_chunk(engine, doc_id: int, slug: str, ordinal: int, body: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(chunks).values(
                document_id=doc_id,
                intent_slug=slug,
                ordinal=ordinal,
                text=body,
                heading_path=None,
                source_ref=f"p. {ordinal + 1}",
                char_count=len(body),
            )
        )
        return result.inserted_primary_key[0]


# --- 2.1 Exact rare token ------------------------------------------------------


def test_2_1_rare_exact_token_retrieves_its_chunk(engine):
    doc_id = _insert_document(engine, intent_slug="hr")
    target = _insert_chunk(
        engine, doc_id, "hr", 0, "Salary band PXQ-7742 applies to this role."
    )
    _insert_chunk(engine, doc_id, "hr", 1, "General onboarding information for new hires.")

    hits = keyword_search("PXQ-7742", spaces=["hr"], top_n=5, engine=engine)

    assert target in {hit.chunk_id for hit in hits}
    assert all(hit.source == "keyword" for hit in hits)


# --- 2.2 Space filter ------------------------------------------------------------


def test_2_2_only_chunks_in_supplied_spaces_are_returned(engine):
    hr_doc = _insert_document(engine, sha256="a" * 64, intent_slug="hr")
    legal_doc = _insert_document(engine, sha256="b" * 64, intent_slug="legal")
    hr_chunk = _insert_chunk(engine, hr_doc, "hr", 0, "The zebranet protocol is described here.")
    _insert_chunk(engine, legal_doc, "legal", 0, "The zebranet protocol appears here too.")

    hits = keyword_search("zebranet", spaces=["hr"], top_n=5, engine=engine)

    assert {hit.chunk_id for hit in hits} == {hr_chunk}


# --- 2.3 top_n = 0 -----------------------------------------------------------------


def test_2_3_top_n_zero_returns_empty_cleanly(engine):
    doc_id = _insert_document(engine, intent_slug="hr")
    _insert_chunk(engine, doc_id, "hr", 0, "Salary band PXQ-7742 applies to this role.")

    hits = keyword_search("PXQ-7742", spaces=["hr"], top_n=0, engine=engine)

    assert hits == []


# --- 2.4 FTS5 operator characters ----------------------------------------------------


@pytest.mark.parametrize(
    "question",
    ["annual-leave", "it's", "foo(", 'bare "quote', "a AND b OR NOT c", "col:value", "term*"],
)
def test_2_4_fts5_operator_characters_do_not_raise(engine, question):
    doc_id = _insert_document(engine, intent_slug="hr")
    _insert_chunk(engine, doc_id, "hr", 0, "Some unrelated policy text.")

    # Must not raise OperationalError or anything else.
    hits = keyword_search(question, spaces=["hr"], top_n=5, engine=engine)

    assert isinstance(hits, list)


# --- 2.5 BM25 ordering -------------------------------------------------------------


# --- 2.6 Natural-language question regression ------------------------------------
#
# The defect this test plan's §L2.2 was supposed to catch and didn't:
# `fts_query` joined quoted terms with a bare space, which FTS5 reads as
# an implicit AND. A bare-terms query like "Band 3" still matched, so
# every other test in this file passed — but a natural-language question
# carries filler words ("What", "is", "the", "for"), and requiring every
# one of them to appear in the same chunk matched nothing. In the read-path
# demo this showed up as `Keyword hits (0)` for every real question asked.


def test_2_6_natural_language_question_returns_nonempty_keyword_hits(engine):
    doc_id = _insert_document(engine, intent_slug="hr")
    target = _insert_chunk(
        engine,
        doc_id,
        "hr",
        0,
        "Compensation\n\n| Band | Min | Mid | Max |\n| --- | --- | --- | --- |\n"
        "| Band 3 | 60000 | 68000 | 76000 |",
    )
    _insert_chunk(engine, doc_id, "hr", 1, "General onboarding information for new hires.")

    hits = keyword_search(
        "What is the Mid salary for Band 3?", spaces=["hr"], top_n=5, engine=engine
    )

    assert hits != []
    assert target in {hit.chunk_id for hit in hits}


def test_2_5_more_term_relevant_chunk_ranks_higher(engine):
    doc_id = _insert_document(engine, intent_slug="hr")
    dense_chunk = _insert_chunk(engine, doc_id, "hr", 0, "onboarding onboarding onboarding")
    diluted_chunk = _insert_chunk(
        engine,
        doc_id,
        "hr",
        1,
        "onboarding is one small topic among many unrelated things this "
        "much longer chunk of text goes on to discuss at great length "
        "with lots of extra padding words that dilute relevance",
    )

    hits = keyword_search("onboarding", spaces=["hr"], top_n=5, engine=engine)

    ids_in_order = [hit.chunk_id for hit in hits]
    assert ids_in_order.index(dense_chunk) < ids_in_order.index(diluted_chunk)
