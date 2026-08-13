"""Test-plan §3a — cross-encoder reranker.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §3a

Every test here injects a fake scorer (`_FakeCrossEncoder`), so nothing
downloads or runs the real `cross-encoder/ms-marco-MiniLM-L-6-v2` model.
Test 3a.6 is the exception in spirit only: it still injects nothing real,
but it does exercise the actual lazy-load path by monkeypatching
`sentence_transformers.CrossEncoder` with a counting fake constructor, to
prove the model is built once per `Reranker` instance and reused across
queries rather than reloaded on every call.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from sqlalchemy import insert

from app.db import chunks, create_engine_for, documents, init_schema
from app.rag.retrieve.fuse import FusedHit
from app.rag.retrieve.rerank import RankedHit, Reranker


class _FakeCrossEncoder:
    """Stands in for `sentence_transformers.CrossEncoder`.

    `predict` is called once per `.score()`/`.rerank()` invocation with the
    *entire* batch of pairs — never once per pair — matching the real
    CrossEncoder's batched API.
    """

    def __init__(self, score_by_chunk: dict[str, float] | None = None) -> None:
        self._score_by_chunk = score_by_chunk or {}
        self.predict_calls: list[list[list[str]]] = []

    def predict(self, pairs: list[list[str]]) -> list[float]:
        self.predict_calls.append([list(p) for p in pairs])
        return [self._score_by_chunk.get(chunk_text, 0.0) for _question, chunk_text in pairs]


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


def _fused(chunk_id: int, fused_score: float) -> FusedHit:
    return FusedHit(chunk_id=chunk_id, fused_score=fused_score, dense_score=None, keyword_rank=1)


# --- 3a.1 Whole pool scored in one batch --------------------------------------


def test_3a_1_whole_pool_scored_in_one_batch(engine):
    doc_id = _insert_document(engine)
    ids = [_insert_chunk(engine, doc_id, "hr", i, f"chunk body {i}") for i in range(5)]
    hits = [_fused(cid, fused_score=1.0 / (i + 1)) for i, cid in enumerate(ids)]
    fake = _FakeCrossEncoder()
    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", client=fake)

    reranker.rerank("a question", hits, engine, top_k=5)

    assert len(fake.predict_calls) == 1
    assert len(fake.predict_calls[0]) == 5


# --- 3a.2 Reordering is real ----------------------------------------------------


def test_3a_2_lower_fused_candidate_scoring_higher_moves_up(engine):
    doc_id = _insert_document(engine)
    high_fused_id = _insert_chunk(engine, doc_id, "hr", 0, "irrelevant filler text")
    low_fused_id = _insert_chunk(engine, doc_id, "hr", 1, "the exact answer to the question")
    hits = [_fused(high_fused_id, fused_score=0.9), _fused(low_fused_id, fused_score=0.1)]
    fake = _FakeCrossEncoder(
        score_by_chunk={
            "irrelevant filler text": -2.0,
            "the exact answer to the question": 5.0,
        }
    )
    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", client=fake)

    result = reranker.rerank("a question", hits, engine, top_k=2)

    assert [hit.chunk_id for hit in result] == [low_fused_id, high_fused_id]


# --- 3a.3 top_k respected ---------------------------------------------------------


def test_3a_3_exactly_top_k_returned_when_available(engine):
    doc_id = _insert_document(engine)
    ids = [_insert_chunk(engine, doc_id, "hr", i, f"chunk body {i}") for i in range(8)]
    hits = [_fused(cid, fused_score=1.0 / (i + 1)) for i, cid in enumerate(ids)]
    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", client=_FakeCrossEncoder())

    result = reranker.rerank("a question", hits, engine, top_k=3)

    assert len(result) == 3
    assert all(isinstance(hit, RankedHit) for hit in result)


# --- 3a.4 Fewer candidates than pool size -----------------------------------------


def test_3a_4_fewer_candidates_than_pool_size_is_not_an_error(engine):
    doc_id = _insert_document(engine)
    ids = [_insert_chunk(engine, doc_id, "hr", i, f"chunk body {i}") for i in range(2)]
    hits = [_fused(cid, fused_score=1.0 / (i + 1)) for i, cid in enumerate(ids)]
    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", client=_FakeCrossEncoder())

    result = reranker.rerank("a question", hits, engine, top_k=20)

    assert len(result) == 2


# --- 3a.5 relevance normalized -----------------------------------------------------


def test_3a_5_relevance_normalized_to_0_1_raw_score_retained(engine):
    doc_id = _insert_document(engine)
    ids = [_insert_chunk(engine, doc_id, "hr", i, f"chunk body {i}") for i in range(3)]
    hits = [_fused(cid, fused_score=1.0 / (i + 1)) for i, cid in enumerate(ids)]
    fake = _FakeCrossEncoder(
        score_by_chunk={"chunk body 0": -10.0, "chunk body 1": 0.0, "chunk body 2": 10.0}
    )
    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", client=fake)

    result = reranker.rerank("a question", hits, engine, top_k=3)

    for hit in result:
        assert 0.0 <= hit.relevance <= 1.0
    by_id = {hit.chunk_id: hit for hit in result}
    assert by_id[ids[0]].rerank_score == -10.0
    assert by_id[ids[1]].rerank_score == 0.0
    assert by_id[ids[2]].rerank_score == 10.0
    assert by_id[ids[1]].relevance == pytest.approx(0.5, abs=1e-9)
    assert by_id[ids[2]].relevance > by_id[ids[1]].relevance > by_id[ids[0]].relevance


# --- I1: unresolvable chunk id is skipped, not a KeyError -----------------------
#
# `build_context` (`app/rag/context.py::_select_chunks`) already handles a
# chunk id that no longer resolves -- deleted between retrieval and this
# later stage -- defensively, with `.get()` and a comment explaining why:
# "Chunk retrieved earlier no longer resolves (e.g. deleted between
# retrieval and context assembly) -- skip, don't fail the whole answer over
# one stale id." `Reranker.rerank` sits one stage *earlier* in the same
# pipeline and faces the identical hazard (a delete racing this query
# between fusion and rerank), but indexed `texts_by_id[chunk_id]` directly,
# raising `KeyError` and turning a delete race into an unhandled 500. This
# test seeds a `FusedHit` for a chunk id that was never inserted at all --
# the simplest reproduction of "retrieved earlier, gone by the time this
# stage reads it" -- and asserts it is skipped rather than raising.


def test_i1_unresolvable_chunk_id_is_skipped_not_a_keyerror(engine):
    doc_id = _insert_document(engine)
    real_id = _insert_chunk(engine, doc_id, "hr", 0, "the exact answer to the question")
    missing_id = real_id + 999  # never inserted -- stands in for a deleted chunk
    hits = [_fused(missing_id, fused_score=0.9), _fused(real_id, fused_score=0.1)]
    fake = _FakeCrossEncoder(score_by_chunk={"the exact answer to the question": 5.0})
    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", client=fake)

    result = reranker.rerank("a question", hits, engine, top_k=5)

    assert [hit.chunk_id for hit in result] == [real_id]


# --- 3a.6 Model loads once ----------------------------------------------------------


def test_3a_6_model_loads_once_not_reloaded_per_query(engine, monkeypatch):
    """Exercises the real `Reranker._get_client()` lazy-load path — the
    `import sentence_transformers; sentence_transformers.CrossEncoder(...)`
    line in `app/rag/retrieve/rerank.py` — without ever importing the real
    `sentence_transformers` package.

    Importing the real package would pull in `torch`. On this platform,
    initializing torch's OpenMP runtime in a process that has already
    initialized faiss's own OpenMP runtime (as `tests/test_vector_store.py`
    and others do earlier in a full-suite run) aborts the interpreter —
    `Fatal Python error: Aborted`, not a normal exception, so no
    `pytest.raises` could catch it. A fake module object installed in
    `sys.modules` before the lazy `import sentence_transformers` runs is
    resolved as a plain dict lookup by Python's import machinery, so the
    real package's `__init__.py` (and therefore torch) never executes,
    while the module-level `import` statement and attribute access in the
    production code path still run for real. See task-3a report for the
    full root-cause writeup — a live risk for the read path, since
    production runs dense search (faiss) and rerank (cross-encoder) in one
    process, and this environment constraint is what a real end-to-end run
    would need to survive.
    """
    doc_id = _insert_document(engine)
    ids = [_insert_chunk(engine, doc_id, "hr", i, f"chunk body {i}") for i in range(2)]
    hits = [_fused(cid, fused_score=1.0 / (i + 1)) for i, cid in enumerate(ids)]

    construction_count = 0

    class _CountingCrossEncoder(_FakeCrossEncoder):
        def __init__(self, model_name):
            nonlocal construction_count
            construction_count += 1
            super().__init__()

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _CountingCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2")  # no injected client

    reranker.rerank("first question", hits, engine, top_k=2)
    reranker.rerank("second question", hits, engine, top_k=2)

    assert construction_count == 1


# --- C2 regression: reranker model change without a restart --------------------
#
# `spec: knowledge-retrieval` § "Retrieval parameters are configuration-driven"
# names the reranker model explicitly. Before this fix nothing could ever
# satisfy it: `Reranker.__init__` bakes `model_name` into `self._model_name`
# once, `_get_client()` only ever loads *that* model, and `PipelineDeps` held
# one `Reranker` instance for the process's lifetime with no way to point it
# at a different model. `set_model()` is the seam a live-config caller
# (`answer_question`) now calls on every query: a no-op when the name hasn't
# changed (so the cached client survives, exactly like 3a.6 above), and a
# cache-discarding update when it has, so the *next* `.rerank()`/`.score()`
# call lazily loads the new model instead of silently keeping scoring
# answers with the old one.


def test_c2_set_model_is_a_no_op_when_the_name_is_unchanged(engine, monkeypatch):
    doc_id = _insert_document(engine)
    ids = [_insert_chunk(engine, doc_id, "hr", i, f"chunk body {i}") for i in range(2)]
    hits = [_fused(cid, fused_score=1.0 / (i + 1)) for i, cid in enumerate(ids)]

    construction_count = 0

    class _CountingCrossEncoder(_FakeCrossEncoder):
        def __init__(self, model_name):
            nonlocal construction_count
            construction_count += 1
            super().__init__()

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _CountingCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2")

    reranker.set_model("cross-encoder/ms-marco-MiniLM-L-6-v2")
    reranker.rerank("first question", hits, engine, top_k=2)
    reranker.set_model("cross-encoder/ms-marco-MiniLM-L-6-v2")
    reranker.rerank("second question", hits, engine, top_k=2)

    assert construction_count == 1


def test_c2_set_model_change_discards_the_cached_client_no_restart(engine, monkeypatch):
    doc_id = _insert_document(engine)
    ids = [_insert_chunk(engine, doc_id, "hr", i, f"chunk body {i}") for i in range(2)]
    hits = [_fused(cid, fused_score=1.0 / (i + 1)) for i, cid in enumerate(ids)]

    constructed_with: list[str] = []

    class _RecordingCrossEncoder(_FakeCrossEncoder):
        def __init__(self, model_name):
            constructed_with.append(model_name)
            super().__init__()

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _RecordingCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    reranker = Reranker("model-v1")
    reranker.rerank("first question", hits, engine, top_k=2)
    assert constructed_with == ["model-v1"]

    reranker.set_model("model-v2")
    reranker.rerank("second question", hits, engine, top_k=2)

    assert constructed_with == ["model-v1", "model-v2"]
