"""Tests for upload validation.

Covers superpowers/test-plans/03-rag-write-path-tests.md §9.1-9.4.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert

from app.config import AppConfig
from app.db import create_engine_for, documents, init_schema
from app.ingest.validate import ValidatedUpload, ValidationError, validate_upload


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine_for(tmp_path / "intelliknow.db")
    init_schema(eng)
    return eng


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


def _insert_document(engine, filename: str, sha256: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename=filename,
                ext=Path(filename).suffix,
                size_bytes=1024,
                sha256=sha256,
                intent_slug="general",
                status="indexed",
                error_message=None,
                chunk_count=3,
                uploaded_at="2026-08-09T00:00:00Z",
                indexed_at="2026-08-09T00:01:00Z",
            )
        )
        return result.inserted_primary_key[0]


# --- 9.1 Allowed extensions ---------------------------------------------------


@pytest.mark.parametrize("filename", ["policy.pdf", "policy.docx", "policy.xlsx"])
def test_9_1_allowed_extensions_accepted(engine, cfg, filename):
    result = validate_upload(filename, b"some content", cfg, engine)

    assert isinstance(result, ValidatedUpload)
    assert result.filename == filename
    assert result.ext == Path(filename).suffix
    assert result.size_bytes == len(b"some content")
    assert len(result.sha256) == 64


# --- 9.2 Disallowed extension --------------------------------------------------


def test_9_2_disallowed_extension_rejected_names_accepted_formats(engine, cfg):
    with pytest.raises(ValidationError) as excinfo:
        validate_upload("policy.txt", b"some content", cfg, engine)

    message = str(excinfo.value)
    assert ".pdf" in message
    assert ".docx" in message
    assert ".xlsx" in message


# --- 9.3 Oversize ----------------------------------------------------------------


def test_9_3_oversize_upload_rejected_states_limit(engine, cfg):
    oversized = b"x" * (cfg.ingestion.max_upload_mb * 1024 * 1024 + 1)

    with pytest.raises(ValidationError) as excinfo:
        validate_upload("policy.pdf", oversized, cfg, engine)

    assert str(cfg.ingestion.max_upload_mb) in str(excinfo.value)


def test_9_3_upload_at_exact_limit_is_accepted(engine, cfg):
    exactly_at_limit = b"x" * (cfg.ingestion.max_upload_mb * 1024 * 1024)

    result = validate_upload("policy.pdf", exactly_at_limit, cfg, engine)

    assert result.size_bytes == len(exactly_at_limit)


# --- 9.4 Duplicate hash -----------------------------------------------------------


def test_9_4_duplicate_hash_rejected_names_existing_document(engine, cfg):
    content = b"identical bytes"
    import hashlib

    sha256 = hashlib.sha256(content).hexdigest()
    _insert_document(engine, filename="handbook.pdf", sha256=sha256)

    with pytest.raises(ValidationError) as excinfo:
        validate_upload("handbook-copy.pdf", content, cfg, engine)

    assert "handbook.pdf" in str(excinfo.value)


def test_9_4_distinct_content_is_not_rejected(engine, cfg):
    _insert_document(engine, filename="handbook.pdf", sha256="a" * 64)

    result = validate_upload("other.pdf", b"different content entirely", cfg, engine)

    assert result.sha256 != "a" * 64
