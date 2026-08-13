"""Tests for what the real entry points actually wire together.

`app/main.py::_build_default_deps`, `app/main.py::_build_pipeline_deps`,
`scripts/ingest.py::_build_deps`, and `scripts/ask.py::_build_deps` are
the only four places production `IngestDeps`/`PipelineDeps` (or, for
`scripts/ask.py`, the equivalent hand-assembled tuple) are assembled.
Everything else in the suite builds them by hand from fakes, so a setting
that a composition root forgets to pass through is invisible to every
other test — which is exactly how `embedding.batch_size` came to be
configurable, documented, and never once read outside a full re-index,
and (per the whole-branch review of this increment) how `PipelineDeps`
came to hold a construction-time config snapshot and a `Reranker` with no
way to point it at a different model without a restart. The latter two
are fixed elsewhere (`app/orchestrator/pipeline.py`,
`app/rag/retrieve/rerank.py`); this file is what would have caught them
missing the real composition roots, which the original increment's test
suite never covered for `_build_pipeline_deps` or `scripts/ask.py` at all.

Every `bootstrap()` call is monkeypatched: these tests assert on wiring,
never construct a real provider, and never reach the network. Tests that
exercise `Reranker`'s real lazy-load path additionally fake
`sentence_transformers` in `sys.modules`, exactly as
`tests/test_rerank.py` does, so no real cross-encoder model ever loads.
"""

from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path

import pytest
import yaml
from sqlalchemy import insert

import app.main
import scripts.ask
import scripts.ingest
from app.bootstrap import Application
from app.config_service import ConfigService
from app.db import chunks as chunks_table
from app.db import documents
from app.rag.chunker import Chunk
from app.rag.retrieve.fuse import FusedHit
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"

DIMENSION = 8


@pytest.fixture
def application(tmp_path: Path):
    """An `Application` shaped exactly like `bootstrap()`'s, but built from
    a config under `tmp_path` and the deterministic provider doubles.
    """

    def build(batch_size: int = 64, rerank_model: str | None = None) -> Application:
        config_path = tmp_path / "config.yaml"
        shutil.copy(SHIPPED_CONFIG, config_path)
        raw = yaml.safe_load(config_path.read_text())
        raw["embedding"]["batch_size"] = batch_size
        raw["embedding"]["dimension"] = DIMENSION
        if rerank_model is not None:
            raw["rag"]["rerank_model"] = rerank_model
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

    return build


def _chunks(count: int) -> list[Chunk]:
    return [
        Chunk(
            ordinal=index,
            text=f"chunk body number {index}",
            heading_path=[],
            source_ref="p. 1",
            char_count=20,
        )
        for index in range(count)
    ]


def _insert_document(engine) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename="policy.pdf",
                ext=".pdf",
                size_bytes=1024,
                sha256="a" * 64,
                intent_slug="hr",
                status="parsing",
                error_message=None,
                chunk_count=0,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at=None,
            )
        )
        return result.inserted_primary_key[0]


def _insert_chunk(engine, doc_id: int, text: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(chunks_table).values(
                document_id=doc_id,
                intent_slug="hr",
                ordinal=0,
                text=text,
                heading_path=None,
                source_ref="p. 1",
                char_count=len(text),
            )
        )
        return result.inserted_primary_key[0]


def _assert_rerank_model_reaches_reranker(
    monkeypatch, engine, reranker, configured_model: str
) -> None:
    """Trigger `reranker`'s real lazy-load path — a fake
    `sentence_transformers` module in `sys.modules`, exactly
    `tests/test_rerank.py::test_3a_6_...`'s technique — and assert the
    fake `CrossEncoder` was constructed with the *configured* model name,
    not whatever `Reranker.__init__`'s own default happens to be. This is
    the composition-root-level counterpart to
    `tests/test_rerank.py`'s C2 tests for `Reranker.set_model` itself:
    those prove the method works once a caller has a `Reranker`; this
    proves the real composition root actually builds one pointed at
    `cfg.rag.rerank_model` in the first place.
    """
    doc_id = _insert_document(engine)
    chunk_id = _insert_chunk(engine, doc_id, "some chunk text")

    constructed_with: list[str] = []

    class _RecordingCrossEncoder:
        def __init__(self, model_name: str) -> None:
            constructed_with.append(model_name)

        def predict(self, pairs: list[list[str]]) -> list[float]:
            return [1.0 for _ in pairs]

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _RecordingCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    reranker.rerank(
        "a question",
        [FusedHit(chunk_id=chunk_id, fused_score=1.0, dense_score=None, keyword_rank=1)],
        engine,
        top_k=1,
    )

    assert constructed_with == [configured_model]


def _assert_batches_at(deps, embedder: FakeEmbeddingProvider) -> None:
    """Write five chunks and assert the writer batched them 2/2/1.

    Asserted through behaviour rather than by reading a private attribute:
    the point is that the configured size reaches the embedding calls, and
    the default (64) would produce a single call of five.
    """
    doc_id = _insert_document(deps.engine)

    deps.index_writer.write_document(doc_id, "hr", _chunks(5))

    assert [len(call) for call in embedder.calls] == [2, 2, 1]


def test_main_passes_the_configured_embedding_batch_size(application, monkeypatch):
    built = application(batch_size=2)
    monkeypatch.setattr(app.main, "bootstrap", lambda: built)

    deps = app.main._build_default_deps()

    assert deps.cfg.embedding.batch_size == 2
    _assert_batches_at(deps, built.embedding)


def test_ingest_script_passes_the_configured_embedding_batch_size(application, monkeypatch):
    built = application(batch_size=2)
    monkeypatch.setattr(scripts.ingest, "bootstrap", lambda: built)

    deps = scripts.ingest._build_deps()

    assert deps.cfg.embedding.batch_size == 2
    _assert_batches_at(deps, built.embedding)


# --- I4: the two composition roots added by this increment --------------------
#
# `app/main.py::_build_pipeline_deps` and `scripts/ask.py::_build_deps` are
# where C1 and C2 actually lived — neither had a wiring test at all before
# this, so nothing here would have caught either regressing again. Both
# tests below check the same kind of setting the `batch_size` tests above
# check for the ingest side: `cfg.rag.rerank_model` reaching the
# `Reranker` each composition root actually constructs, not merely being
# present on the `AppConfig` object they also hand back.


def test_main_pipeline_deps_passes_the_configured_rerank_model(application, monkeypatch):
    built = application(rerank_model="custom-rerank-model")
    monkeypatch.setattr(app.main, "bootstrap", lambda: built)

    deps = app.main._build_pipeline_deps()

    assert deps.get_cfg().rag.rerank_model == "custom-rerank-model"
    _assert_rerank_model_reaches_reranker(
        monkeypatch, deps.engine, deps.reranker, "custom-rerank-model"
    )


def test_ask_script_passes_the_configured_rerank_model(application, monkeypatch):
    built = application(rerank_model="custom-rerank-model")
    monkeypatch.setattr(scripts.ask, "bootstrap", lambda: built)

    _application, cfg, engine, _vector_store, _centroids, reranker = scripts.ask._build_deps()

    assert cfg.rag.rerank_model == "custom-rerank-model"
    _assert_rerank_model_reaches_reranker(monkeypatch, engine, reranker, "custom-rerank-model")
