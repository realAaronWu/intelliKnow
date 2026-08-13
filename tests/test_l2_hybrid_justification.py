"""Test-plan §L2.2 — hybrid justification (`slow`).

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md L2.2

"L2.2 is designed to be able to falsify its own feature. If it passes in
both configurations on the real corpus, BM25 is contributing nothing and
the hybrid design should be reconsidered — report that rather than
deleting the test."

This runs real FTS5 and a real local embedding model
(`all-MiniLM-L6-v2`, the shipped default — already cached locally, no
download and no network call) against a small corpus built from the
actual chunk text the fixture corpus (`tests/fixtures/docs/salary_bands.pdf`
+ `ragged_salary_grid.pdf`) produces once ingested — reproduced here as
literal strings so this test does not depend on `scripts/ingest.py`
having been run first, or on the loader/chunker pipeline at all. No LLM
is involved and no API call is made: this exercises the retrieval layer
directly (`dense_search`, `keyword_search`), not `classify()` or
`generate_answer()`.

Marked `slow` because constructing `SentenceTransformerEmbedding` loads a
real model into the process — the one dependency every other test in
this suite fakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert

from app.db import chunks, create_engine_for, documents, init_schema
from app.providers.local_embedding import SentenceTransformerEmbedding
from app.rag.retrieve.dense import dense_search
from app.rag.retrieve.keyword import keyword_search
from app.rag.vector_store import VectorStore

pytestmark = pytest.mark.slow

DIMENSION = 384

# The natural-language phrasing a user actually types — matching the
# defect report exactly, not a bare-terms query like "Band 3" (which
# matched even under the old, broken AND-joined `fts_query`).
_QUESTION = "What is the Mid salary for Band 3?"

# Verbatim chunk text this fixture corpus actually produces once ingested
# (confirmed against `data/intelliknow.db` for the "hr" space). Two chunks
# hold the exact answer ("Band 3 | 60000 | 68000 | 76000"); the other
# three are real decoys from the same space that a dense model can find
# semantically closer to a bare compensation question than a terse table
# row is.
_CHUNKS: list[tuple[str, str]] = [
    (
        "leave",
        "Leave Policy\n\nFull-time employees accrue 25 days of annual leave per "
        "calendar year, prorated for partial years of\n\nservice.",
    ),
    (
        "comp_prose",
        "Compensation\n\nSalary bands are reviewed annually. See the table below.",
    ),
    (
        "band_table_1",
        "Compensation\n\n| Band | Min | Mid | Max |\n| --- | --- | --- | --- |\n"
        "| Band 1 | 32000 | 38000 | 44000 |\n| Band 2 | 45000 | 52000 | 59000 |\n"
        "| Band 3 | 60000 | 68000 | 76000 |\n| Band 4 | 78000 | 88000 | 98000 |",
    ),
    ("heading_only", "Salary Bands"),
    (
        "band_table_2",
        "| Band | Min | Mid | Max |\n| --- | --- | --- | --- |\n"
        "| Band 1 | 32000 | 38000 | 44000 |\n| Band 2 | 45000 | 52000 | 59000 |\n"
        "| Band 3 | 60000 | 68000 | 76000 |\n| Band 4 | 78000 | 88000 | 98000 |",
    ),
]

_TARGET_KEYS = {"band_table_1", "band_table_2"}

# A realistically small dense candidate budget. The shipped default
# (`rag.vector_top_n: 20`) exceeds this corpus's five chunks, so at that
# setting dense search returns every chunk regardless of rank and would
# "succeed" trivially — proving nothing about ranking quality. A real
# production space holds far more than 20 chunks, so a tight top_n here
# approximates the selection pressure a real deployment applies, rather
# than papering over the small fixture corpus's size.
_DENSE_TOP_N = 2


@pytest.fixture(scope="module")
def embedding() -> SentenceTransformerEmbedding:
    return SentenceTransformerEmbedding("all-MiniLM-L6-v2", 64)


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


@pytest.fixture
def vector_store(tmp_path: Path, embedding: SentenceTransformerEmbedding) -> VectorStore:
    return VectorStore(tmp_path / "faiss", embedding.dimension)


def _seed(engine, vector_store, embedding) -> dict[str, int]:
    with engine.begin() as conn:
        doc_id = conn.execute(
            insert(documents).values(
                filename="salary_bands.pdf",
                ext=".pdf",
                size_bytes=1024,
                sha256="a" * 64,
                intent_slug="hr",
                status="indexed",
                error_message=None,
                chunk_count=len(_CHUNKS),
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at="2026-08-09T00:00:01Z",
            )
        ).inserted_primary_key[0]

        chunk_ids: dict[str, int] = {}
        for ordinal, (key, text) in enumerate(_CHUNKS):
            chunk_ids[key] = conn.execute(
                insert(chunks).values(
                    document_id=doc_id,
                    intent_slug="hr",
                    ordinal=ordinal,
                    text=text,
                    heading_path=None,
                    source_ref=f"p. {ordinal + 1}",
                    char_count=len(text),
                )
            ).inserted_primary_key[0]

    vectors = embedding.embed([text for _key, text in _CHUNKS])
    vector_store.add("hr", [chunk_ids[key] for key, _text in _CHUNKS], vectors)
    return chunk_ids


# --- 1. Exact-token question retrieves its chunk via keyword search --------------


def test_l2_2a_exact_token_question_retrieves_via_keyword(engine, vector_store, embedding):
    chunk_ids = _seed(engine, vector_store, embedding)

    hits = keyword_search(_QUESTION, spaces=["hr"], top_n=5, engine=engine)

    hit_ids = {hit.chunk_id for hit in hits}
    assert hit_ids & {chunk_ids[key] for key in _TARGET_KEYS}


# --- 2. Same question, keyword disabled: dense search alone misses it ------------


def test_l2_2b_same_question_not_retrieved_by_dense_alone(engine, vector_store, embedding):
    chunk_ids = _seed(engine, vector_store, embedding)

    # `keyword_top_n: 0` — the documented way to disable keyword retrieval.
    keyword_hits = keyword_search(_QUESTION, spaces=["hr"], top_n=0, engine=engine)
    assert keyword_hits == []

    [query_vector] = embedding.embed([_QUESTION])
    dense_hits = dense_search(query_vector, ["hr"], _DENSE_TOP_N, vector_store)

    dense_hit_ids = {hit.chunk_id for hit in dense_hits}
    target_ids = {chunk_ids[key] for key in _TARGET_KEYS}
    assert not (dense_hit_ids & target_ids), (
        f"dense search alone (top_n={_DENSE_TOP_N}) unexpectedly surfaced the "
        f"exact-answer chunk — BM25 would be contributing nothing on this "
        f"corpus; report this rather than loosening the assertion. "
        f"dense hits: {sorted(dense_hit_ids)}, target: {sorted(target_ids)}"
    )
