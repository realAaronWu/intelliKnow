"""Regression test for C1: the real `app.main.app` composition root must
share one `VectorStore` (and one `Engine`) between the documents API and
the admin test-query pipeline.

Before the fix, `app/main.py::__getattr__("app")` built `IngestDeps` and
`PipelineDeps` from two independent `_build_default_deps()` /
`_build_pipeline_deps()` calls, each constructing its own `VectorStore`
over the same `faiss_dir`. `VectorStore._get_or_create` caches an index in
memory on first touch and never re-reads the file from disk, so once the
query-side store has been touched once, anything written afterward by the
ingest-side store is invisible to it for the life of the process — no
error, no log line, dense retrieval just silently goes stale.

This test reproduces that exact sequence end-to-end through the real HTTP
surface, entirely with fakes/tmp-path storage (no real API call):

    upload doc1 -> query (primes the query-side store's cache) ->
    upload doc2 -> query again (doc2 must still be visible)

Keyword retrieval is turned off (`rag.keyword_top_n: 0`) and a single
intent space is configured, so the only way either document can appear in
`retrieved_doc_ids` is via dense retrieval finding its vector in whichever
`VectorStore` instance the query path actually uses — isolating exactly
the seam C1 is about, independent of routing or reranking behaviour.
"""

from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import app.main
from app.bootstrap import Application
from app.config_service import ConfigService
from app.rag.retrieve.rerank import Reranker
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "docs"

DIMENSION = 8
_SPACE_SLUG = "general"


class _ConstantCrossEncoder:
    """Every (question, chunk) pair scores the same, well above any
    `relevance_floor` — the point of this test is dense-retrieval
    *presence*, not reranking quality, so scoring is deliberately not a
    variable here.
    """

    def __init__(self, score: float) -> None:
        self._score = score

    def predict(self, pairs: list[list[str]]) -> list[float]:
        return [self._score for _ in pairs]


def _fake_reranker(model_name: str) -> Reranker:
    """Stands in for `app.main.Reranker` so the real composition root
    never loads a real `sentence-transformers` `CrossEncoder` — this test
    exercises `app.main`'s real wiring (`__getattr__("app")`), and the
    "make no API calls / no real model loads" constraint applies to it
    just as much as to any other test in this suite.
    """
    return Reranker(model_name, client=_ConstantCrossEncoder(5.0))


@pytest.fixture
def application(tmp_path: Path) -> Application:
    """An `Application` shaped like `bootstrap()`'s, built from a config
    under `tmp_path` with exactly one intent space (so classification and
    routing are trivially deterministic) and keyword retrieval disabled
    (so only dense retrieval can ever surface a chunk) — see module
    docstring for why.
    """
    config_path = tmp_path / "config.yaml"
    shutil.copy(SHIPPED_CONFIG, config_path)
    raw = yaml.safe_load(config_path.read_text())
    raw["embedding"]["dimension"] = DIMENSION
    raw["rag"]["keyword_top_n"] = 0
    raw["rag"]["vector_top_n"] = 20
    raw["rag"]["rerank_candidates"] = 20
    raw["rag"]["final_top_k"] = 20
    raw["orchestrator"]["fallback_space"] = _SPACE_SLUG
    raw["intent_spaces"] = [
        {
            "slug": _SPACE_SLUG,
            "name": "General",
            "description": "Everything lives here.",
            "keywords": [],
        }
    ]
    raw["storage"] = {
        "sqlite_path": str(tmp_path / "intelliknow.db"),
        "faiss_dir": str(tmp_path / "faiss"),
        "upload_dir": str(tmp_path / "uploads"),
    }
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    return Application(
        config_service=ConfigService.load(config_path),
        classify_llm=FakeLLMProvider(),
        generate_llm=FakeLLMProvider(),
        embedding=FakeEmbeddingProvider(dimension=DIMENSION),
    )


def _upload(client: TestClient, classify_llm: FakeLLMProvider, filename: str) -> int:
    classify_llm.expect_schema({"slug": _SPACE_SLUG})
    content = (FIXTURES / filename).read_bytes()
    resp = client.post(
        "/documents", files={"file": (filename, content, "application/octet-stream")}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


def _query(client: TestClient, generate_llm: FakeLLMProvider, question: str) -> dict:
    generate_llm.expect_text("Here is the answer.")
    resp = client.post("/admin/test-query", json={"question": question})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_c1_second_document_visible_to_dense_retrieval_without_restart(
    application: Application, monkeypatch
):
    """Ingest one document, query (which touches/caches the query-side
    store for the first time), ingest a second document, then query
    again — the second document must be retrievable in the same process,
    with no restart between the two uploads.
    """
    monkeypatch.setattr(app.main, "bootstrap", lambda: application)
    monkeypatch.setattr(app.main, "Reranker", _fake_reranker)

    fastapi_app = app.main.app  # triggers the real composition root exactly once
    client = TestClient(fastapi_app)

    doc1_id = _upload(client, application.classify_llm, "handbook.pdf")
    body1 = _query(client, application.generate_llm, "tell me about document one")
    assert body1["status"] == "success", body1
    assert doc1_id in body1["retrieved_doc_ids"], body1

    doc2_id = _upload(client, application.classify_llm, "expense_policy.docx")
    body2 = _query(client, application.generate_llm, "tell me about document two")

    assert body2["status"] == "success", body2
    assert doc2_id in body2["retrieved_doc_ids"], (
        "doc2 is invisible to dense retrieval — the query path is reading "
        "from a stale, separately-cached VectorStore instance rather than "
        "the one the ingest path just wrote and persisted to "
        f"(response: {body2})"
    )


# --- I5: cross-encoder loads at startup, not lazily on the first query -----------
#
# `uvicorn app.main:app` imports faiss at module import (via `VectorStore`
# above) and previously left `sentence-transformers`/torch to load lazily,
# on whichever request happened to reach `Reranker.rerank()` first. On this
# platform, initializing torch's OpenMP runtime in a process that has
# already initialized faiss's aborts the interpreter outright -- so that
# hazard was live in production, not just in the test suite `tests/
# test_rerank.py::test_3a_6_...` documents it for. The composition root now
# loads the cross-encoder client during `app.main.app`'s construction
# instead, fixing the import order at process start.


def test_i5_cross_encoder_loads_eagerly_during_app_composition_not_on_first_query(
    application: Application, monkeypatch
):
    monkeypatch.setattr(app.main, "bootstrap", lambda: application)

    constructed_with: list[str] = []

    class _RecordingCrossEncoder:
        def __init__(self, model_name: str) -> None:
            constructed_with.append(model_name)

        def predict(self, pairs: list[list[str]]) -> list[float]:
            return [5.0 for _ in pairs]

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _RecordingCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    _fastapi_app = app.main.app  # triggers the real composition root exactly once

    assert constructed_with, (
        "the cross-encoder client was not constructed while building "
        "app.main.app -- it is still being deferred to the first query "
        "that reaches Reranker.rerank()"
    )
