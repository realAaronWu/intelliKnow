"""I3: `scripts/ask.py` must call `answer_question` for real, not
re-implement the pipeline.

Before this fix, `scripts/ask.py` re-did every stage itself (embed,
classify, route, dense, keyword, fuse, rerank, gate, context, generate,
verify, format, latency) and never called `answer_question` — so any
future change to the real pipeline (including C3's `QueryOutcome` fields)
would never show up in the demo the project owner runs to see the read
path working.

These tests drive the rewritten `scripts/ask.py::main` end-to-end (fakes
only — a fake `bootstrap()` and a `Reranker` with an injected fake
cross-encoder client, exactly like `tests/test_app_composition.py`, so no
real model ever loads and no real API call is made) and assert every
output section the CLI's docstring promises is still present, sourced from
the real `answer_question`/`QueryTrace` call rather than a second,
parallel implementation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from sqlalchemy import insert

import scripts.ask
from app.bootstrap import Application
from app.config_service import ConfigService
from app.db import chunks as chunks_table
from app.db import create_engine_for, documents as documents_table, init_schema
from app.rag.retrieve.rerank import Reranker
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"

DIMENSION = 8
_CHUNK_TEXT = "Employees receive 25 days of annual leave per year."


class _ConstantCrossEncoder:
    """Every pair scores the same, well above any `relevance_floor` — the
    point of these tests is the CLI's wiring/output, not reranking
    quality.
    """

    def __init__(self, score: float) -> None:
        self._score = score

    def predict(self, pairs: list[list[str]]) -> list[float]:
        return [self._score for _ in pairs]


def _fake_reranker(model_name: str) -> Reranker:
    return Reranker(model_name, client=_ConstantCrossEncoder(5.0))


@pytest.fixture
def application(tmp_path: Path):
    """An `Application` shaped like `bootstrap()`'s, with exactly one
    intent space configured so `classify()`'s centroid softmax always
    resolves to it at confidence 1.0 (a softmax over one value is always
    1.0) — deterministic classification with no embedding vectors to pin,
    mirroring `tests/test_app_composition.py`'s fixture.
    """

    def build() -> Application:
        config_path = tmp_path / "config.yaml"
        shutil.copy(SHIPPED_CONFIG, config_path)
        raw = yaml.safe_load(config_path.read_text())
        raw["embedding"]["dimension"] = DIMENSION
        raw["orchestrator"]["fallback_space"] = "general"
        raw["intent_spaces"] = [
            {
                "slug": "general",
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

    return build


def _seed_chunk(cfg, space_slug: str) -> tuple[int, int]:
    engine = create_engine_for(Path(cfg.storage.sqlite_path))
    init_schema(engine)
    with engine.begin() as conn:
        doc_id = conn.execute(
            insert(documents_table).values(
                filename="handbook.pdf",
                ext=".pdf",
                size_bytes=100,
                sha256="a" * 64,
                intent_slug=space_slug,
                status="indexed",
                error_message=None,
                chunk_count=1,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at="2026-08-09T00:00:01Z",
            )
        ).inserted_primary_key[0]
        chunk_id = conn.execute(
            insert(chunks_table).values(
                document_id=doc_id,
                intent_slug=space_slug,
                ordinal=0,
                text=_CHUNK_TEXT,
                heading_path="Leave Policy",
                source_ref="p. 1",
                char_count=len(_CHUNK_TEXT),
            )
        ).inserted_primary_key[0]
    vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)
    # Dense retrieval never thresholds by score -- whatever vector is
    # stored is returned as long as it's the only entry in the space, so
    # an arbitrary (but correctly-dimensioned) vector is enough to make
    # this chunk a dense hit.
    vector_store.add(space_slug, [chunk_id], [[1.0] + [0.0] * (DIMENSION - 1)])
    # `VectorStore.add` is in-memory only until persisted -- `main()` below
    # builds its own `VectorStore` instance over the same `faiss_dir` (via
    # `scripts.ask._build_deps`), so without this the seeded vector would
    # never actually reach disk for that second instance to read.
    vector_store.persist(space_slug)
    return doc_id, chunk_id


def test_i3_ask_cli_prints_every_stage_via_the_real_pipeline_with_space_bypass(
    application, monkeypatch, capsys
):
    built = application()
    _doc_id, _chunk_id = _seed_chunk(built.config, "general")
    built.generate_llm.expect_text("You get 25 days of leave. [1]")

    monkeypatch.setattr(scripts.ask, "bootstrap", lambda: built)
    monkeypatch.setattr(scripts.ask, "Reranker", _fake_reranker)

    exit_code = scripts.ask.main(["how much annual leave do I get", "--space", "general"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "bypassed via --space 'general'" in out
    assert len(built.classify_llm.calls) == 0
    assert "--- Routing ---" in out
    assert "spaces searched: general" in out
    assert "--- Dense hits (1) ---" in out
    assert "--- Keyword hits" in out
    assert "--- Fused order (1) ---" in out
    assert "--- Reranked order (1) ---" in out
    assert "--- Gate ---" in out
    assert "decision: PASS" in out
    assert "--- Answer ---" in out
    assert "25 days" in out
    assert "--- Citations ---" in out
    assert "handbook.pdf" in out
    assert "Latency:" in out


def test_i3_ask_cli_real_classification_path_prints_classification_section(
    application, monkeypatch, capsys
):
    """Without `--space`, the CLI must still print the classification
    section it always has -- sourced from `trace.classification`, the real
    `Classification` `answer_question` produced, not a locally
    reconstructed one.
    """
    built = application()
    _seed_chunk(built.config, "general")
    built.generate_llm.expect_text("You get 25 days of leave. [1]")

    monkeypatch.setattr(scripts.ask, "bootstrap", lambda: built)
    monkeypatch.setattr(scripts.ask, "Reranker", _fake_reranker)

    exit_code = scripts.ask.main(["how much annual leave do I get"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "detected space:  general" in out
    assert "confidence:      1.0000" in out
    assert "classified_by:   centroid" in out


def test_i3_ask_cli_gate_rejection_prints_no_match_result(application, monkeypatch, capsys):
    built = application()
    _seed_chunk(built.config, "general")

    monkeypatch.setattr(scripts.ask, "bootstrap", lambda: built)
    # Scores far below any relevance_floor -- the gate must reject.
    monkeypatch.setattr(
        scripts.ask, "Reranker", lambda model_name: Reranker(model_name, client=_ConstantCrossEncoder(-10.0))
    )

    exit_code = scripts.ask.main(["how much annual leave do I get", "--space", "general"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "decision: FAIL" in out
    assert "No match: nothing in" in out
    assert len(built.generate_llm.calls) == 0


def test_i3_ask_cli_invalid_space_errors_before_calling_the_pipeline(application, monkeypatch, capsys):
    built = application()

    monkeypatch.setattr(scripts.ask, "bootstrap", lambda: built)

    exit_code = scripts.ask.main(["a question", "--space", "not-a-real-space"])

    assert exit_code == 2
    assert "not a configured intent space" in capsys.readouterr().err
