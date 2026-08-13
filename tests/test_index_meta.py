"""Test-plan §7 — index metadata and embedding immutability.

Source: docs/superpowers/test-plans/03-rag-write-path-tests.md §7

`assert_compatible` is the loud-failure seam for `spec: configuration` §
"Immutable embedding settings once documents exist": vectors from different
embedding models are not comparable, so a silent model swap would degrade
every answer with no error. This tests that seam directly, without going
through `ConfigService` (task 2's tests own that wiring contract).
"""

from __future__ import annotations

from pathlib import Path

import faiss
import numpy as np
import pytest

from app.config import AppConfig
from app.rag.index_meta import assert_compatible, read_meta, write_meta


def _config_with_model(model: str) -> AppConfig:
    return AppConfig.model_validate({"embedding": {"model": model, "dimension": 4}})


def _write_index_with_vectors(faiss_dir: Path, slug: str, count: int) -> None:
    faiss_dir.mkdir(parents=True, exist_ok=True)
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(4))
    if count:
        vectors = np.eye(count, 4, dtype="float32")[:count]
        ids = np.arange(count, dtype="int64")
        index.add_with_ids(vectors, ids)
    faiss.write_index(index, str(faiss_dir / f"{slug}.index"))


# --- 7.1 Recorded at first ingest --------------------------------------------


def test_7_1_write_meta_then_read_meta_round_trips_model_and_dimension(tmp_path):
    faiss_dir = tmp_path / "faiss"
    faiss_dir.mkdir()

    write_meta(faiss_dir, model="all-MiniLM-L6-v2", dimension=384)
    meta = read_meta(faiss_dir)

    assert meta is not None
    assert meta.model == "all-MiniLM-L6-v2"
    assert meta.dimension == 384


# --- 7.2 Mismatch with documents present -------------------------------------


def test_7_2_mismatch_with_vectors_present_raises_naming_both_models(tmp_path):
    faiss_dir = tmp_path / "faiss"
    write_meta(faiss_dir, model="all-MiniLM-L6-v2", dimension=4)
    _write_index_with_vectors(faiss_dir, "hr", count=1)

    cfg = _config_with_model("text-embedding-3-small")

    with pytest.raises(ValueError) as excinfo:
        assert_compatible(cfg, faiss_dir)

    message = str(excinfo.value)
    assert "all-MiniLM-L6-v2" in message
    assert "text-embedding-3-small" in message
    assert "re-index" in message.lower()


# --- 7.3 No meta recorded -----------------------------------------------------


def test_7_3_no_recorded_meta_permits_any_model(tmp_path):
    faiss_dir = tmp_path / "faiss"
    faiss_dir.mkdir()

    cfg = _config_with_model("anything-goes")

    assert_compatible(cfg, faiss_dir)  # must not raise


# --- 7.4 Empty index -----------------------------------------------------------


def test_7_4_recorded_meta_but_empty_index_permits_model_change(tmp_path):
    faiss_dir = tmp_path / "faiss"
    write_meta(faiss_dir, model="all-MiniLM-L6-v2", dimension=4)
    _write_index_with_vectors(faiss_dir, "hr", count=0)

    cfg = _config_with_model("a-different-model")

    assert_compatible(cfg, faiss_dir)  # must not raise


# --- 7.5 Re-index updates record ----------------------------------------------


def test_7_5_reindex_updates_recorded_model(tmp_path):
    faiss_dir = tmp_path / "faiss"
    write_meta(faiss_dir, model="all-MiniLM-L6-v2", dimension=384)

    write_meta(faiss_dir, model="all-mpnet-base-v2", dimension=768)

    meta = read_meta(faiss_dir)
    assert meta.model == "all-mpnet-base-v2"
    assert meta.dimension == 768


# --- Same model is always compatible ------------------------------------------


def test_same_model_is_always_compatible_even_with_vectors_present(tmp_path):
    faiss_dir = tmp_path / "faiss"
    write_meta(faiss_dir, model="all-MiniLM-L6-v2", dimension=4)
    _write_index_with_vectors(faiss_dir, "hr", count=1)

    cfg = _config_with_model("all-MiniLM-L6-v2")

    assert_compatible(cfg, faiss_dir)  # must not raise
