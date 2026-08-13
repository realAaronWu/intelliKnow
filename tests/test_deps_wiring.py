"""Tests for what the two real entry points actually wire together.

`app/main.py::_build_default_deps` and `scripts/ingest.py::_build_deps`
are the only two places production `IngestDeps` are assembled. Everything
else in the suite builds `IngestDeps` by hand from fakes, so a setting
that the composition roots forget to pass through is invisible to every
other test — which is exactly how `embedding.batch_size` came to be
configurable, documented, and never once read outside a full re-index.

Both `bootstrap()` calls are monkeypatched: these tests assert on wiring,
never construct a real provider, and never reach the network.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from sqlalchemy import insert

import app.main
import scripts.ingest
from app.bootstrap import Application
from app.config_service import ConfigService
from app.db import documents
from app.rag.chunker import Chunk
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"

DIMENSION = 8


@pytest.fixture
def application(tmp_path: Path):
    """An `Application` shaped exactly like `bootstrap()`'s, but built from
    a config under `tmp_path` and the deterministic provider doubles.
    """

    def build(batch_size: int) -> Application:
        config_path = tmp_path / "config.yaml"
        shutil.copy(SHIPPED_CONFIG, config_path)
        raw = yaml.safe_load(config_path.read_text())
        raw["embedding"]["batch_size"] = batch_size
        raw["embedding"]["dimension"] = DIMENSION
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
