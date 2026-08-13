"""Test-plan §5 — context builder.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §5

`build_context` is the seam between retrieval (which scores chunks) and
generation (which reads them as a document). It deliberately throws away
the ranking order for presentation — `test_5_2_...` is the load-bearing
test here — because a model asked to answer from a jumbled, score-ordered
bag of paragraphs reads worse than one given the document back in the
order it was written, even though rank still decides *which* chunks make
the cut in `test_5_4_...`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert

from app.config import RAGConfig
from app.db import chunks, create_engine_for, documents, init_schema
from app.rag.context import Source, build_context
from app.rag.retrieve.rerank import RankedHit


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


def _insert_document(engine, filename: str = "policy.pdf", sha256: str = "a" * 64) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename=filename,
                ext=".pdf",
                size_bytes=1024,
                sha256=sha256,
                intent_slug="hr",
                status="indexed",
                error_message=None,
                chunk_count=0,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at="2026-08-09T00:00:01Z",
            )
        )
        return result.inserted_primary_key[0]


def _insert_chunk(
    engine,
    doc_id: int,
    ordinal: int,
    text: str,
    heading_path: str | None = None,
    source_ref: str | None = None,
) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(chunks).values(
                document_id=doc_id,
                intent_slug="hr",
                ordinal=ordinal,
                text=text,
                heading_path=heading_path,
                source_ref=source_ref or f"p. {ordinal + 1}",
                char_count=len(text),
            )
        )
        return result.inserted_primary_key[0]


def _ranked(chunk_id: int, relevance: float) -> RankedHit:
    return RankedHit(
        chunk_id=chunk_id,
        fused_score=0.0,
        dense_score=None,
        keyword_rank=None,
        rerank_score=relevance,
        relevance=relevance,
    )


def _cfg(max_context_chars: int = 6000) -> RAGConfig:
    return RAGConfig(max_context_chars=max_context_chars)


# --- 5.1 Near-duplicates dropped -------------------------------------------


def test_5_1_near_duplicate_same_document_chunks_collapse_to_one(engine):
    doc_id = _insert_document(engine)
    base = (
        "Employees accrue twenty days of annual leave per calendar year, "
        "credited monthly and carried over up to five days into the next year."
    )
    # Simulates the overlap window a chunker can produce: near-identical
    # text with a short tail difference, not a byte-for-byte duplicate.
    overlapping = base + " Unused balances beyond the cap are forfeited."
    higher_id = _insert_chunk(engine, doc_id, ordinal=0, text=base)
    lower_id = _insert_chunk(engine, doc_id, ordinal=1, text=overlapping)
    hits = [_ranked(higher_id, relevance=0.9), _ranked(lower_id, relevance=0.4)]

    bundle = build_context(hits, engine, _cfg())

    assert len(bundle.sources) == 1
    assert bundle.sources[0].chunk_id == higher_id


# --- 5.2 Ordering: document then position, not score -----------------------


def test_5_2_ordering_is_by_document_then_position_not_score(engine):
    doc_a = _insert_document(engine, filename="a.pdf", sha256="a" * 64)
    doc_b = _insert_document(engine, filename="b.pdf", sha256="b" * 64)
    a0 = _insert_chunk(engine, doc_a, ordinal=0, text="A chunk zero content here.")
    a1 = _insert_chunk(engine, doc_a, ordinal=1, text="A chunk one content follows.")
    b0 = _insert_chunk(engine, doc_b, ordinal=0, text="B chunk zero content here.")

    # Best-ranked (first in hits) is b0, then a1, then a0 — the reverse of
    # document/position order.
    hits = [_ranked(b0, relevance=0.9), _ranked(a1, relevance=0.7), _ranked(a0, relevance=0.5)]

    bundle = build_context(hits, engine, _cfg())

    assert [s.chunk_id for s in bundle.sources] == [a0, a1, b0]


# --- 5.3 Tagging -------------------------------------------------------------


def test_5_3_source_carries_marker_title_ref_heading_path(engine):
    doc_id = _insert_document(engine, filename="nda.docx")
    chunk_id = _insert_chunk(
        engine,
        doc_id,
        ordinal=0,
        text="The receiving party shall keep confidential information secret.",
        heading_path="Non-Disclosure Agreement > 2. Obligations",
        source_ref="¶ 5, ¶ 6",
    )
    hits = [_ranked(chunk_id, relevance=0.9)]

    bundle = build_context(hits, engine, _cfg())

    [source] = bundle.sources
    assert isinstance(source, Source)
    assert source.marker
    assert source.document_id == doc_id
    assert source.document_title == "nda.docx"
    assert source.source_ref == "¶ 5, ¶ 6"
    assert source.heading_path == "Non-Disclosure Agreement > 2. Obligations"
    assert source.text == (
        "The receiving party shall keep confidential information secret."
    )


# --- 5.4 Budget enforced ------------------------------------------------------


def test_5_4_over_budget_drops_lowest_ranked_chunks(engine):
    doc_id = _insert_document(engine)
    text_len_30 = "x" * 30
    top_id = _insert_chunk(engine, doc_id, ordinal=0, text=text_len_30)
    mid_id = _insert_chunk(engine, doc_id, ordinal=1, text="y" * 30)
    bottom_id = _insert_chunk(engine, doc_id, ordinal=2, text="z" * 30)
    hits = [
        _ranked(top_id, relevance=0.9),
        _ranked(mid_id, relevance=0.6),
        _ranked(bottom_id, relevance=0.3),
    ]

    bundle = build_context(hits, engine, _cfg(max_context_chars=50))

    included_ids = {s.chunk_id for s in bundle.sources}
    assert top_id in included_ids
    assert bottom_id not in included_ids
    assert sum(len(s.text) for s in bundle.sources) <= 50


# --- 5.5 Markers unique and stable -------------------------------------------


def test_5_5_markers_stable_across_identical_calls(engine):
    doc_id = _insert_document(engine)
    id1 = _insert_chunk(engine, doc_id, ordinal=0, text="First chunk body text.")
    id2 = _insert_chunk(engine, doc_id, ordinal=1, text="Second chunk body text.")
    hits = [_ranked(id1, relevance=0.9), _ranked(id2, relevance=0.8)]

    bundle_1 = build_context(hits, engine, _cfg())
    bundle_2 = build_context(hits, engine, _cfg())

    markers_1 = [s.marker for s in bundle_1.sources]
    markers_2 = [s.marker for s in bundle_2.sources]
    assert markers_1 == markers_2
    assert len(markers_1) == len(set(markers_1))


# --- 5.6 Content delimited ----------------------------------------------------


def test_5_6_chunk_text_appears_inside_delimiters_in_prompt_block(engine):
    doc_id = _insert_document(engine)
    distinctive_text = "The quarterly budget review happens every March."
    chunk_id = _insert_chunk(engine, doc_id, ordinal=0, text=distinctive_text)
    hits = [_ranked(chunk_id, relevance=0.9)]

    bundle = build_context(hits, engine, _cfg())

    assert f"```\n{distinctive_text}\n```" in bundle.prompt_block
