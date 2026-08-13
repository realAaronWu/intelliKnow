"""Ingestion worker: turns an uploaded file into indexed chunks.

Drives a document's status through the full pipeline —
`pending -> parsing -> indexed | failed` — per `spec: document-ingestion`
§ "Asynchronous processing with visible status" and § "Ingestion error
handling": load, repair any ragged table regions, chunk, suggest an
intent space, embed, and index.

Table repair (`app/rag/tables.py`) and intent suggestion
(`app/ingest/classify_doc.py`) are both built to degrade to a fallback
rather than raise on provider failure, so the only failures this module
actually has to recover from are a loader that cannot parse the file at
all and an embedding/indexing failure partway through
`IndexWriter.write_document`. Either way, cleanup is identical:
`IndexWriter.remove_document` deletes whatever chunk rows (and, via the
FTS5 triggers wired in `app/db.py`, chunk_fts rows) made it into the
database, and removes whatever vectors made it into FAISS — safe to call
even when nothing was written, since it is a no-op for a document with no
chunk rows. That is what lets one `except` branch cover every failure
stage without the worker needing to know which stage failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from sqlalchemy import Engine, update

from app.config import AppConfig
from app.db import documents as documents_table
from app.ingest.classify_doc import suggest_intent
from app.providers.base import LLMProvider
from app.rag.blocks import Block, DocumentLoader, LoaderError
from app.rag.chunker import Chunk, chunk_blocks
from app.rag.index_writer import IndexWriter
from app.rag.loaders.docx import DocxLoader
from app.rag.loaders.pdf import PdfLoader
from app.rag.loaders.xlsx import XlsxLoader
from app.rag.tables import is_ragged, repair_table

#: Extension -> loader. Shared default; a caller may substitute its own
#: mapping (tests do, to inject stub loaders without touching the real
#: file-parsing libraries).
DEFAULT_LOADERS: Mapping[str, DocumentLoader] = {
    ".pdf": PdfLoader(),
    ".docx": DocxLoader(),
    ".xlsx": XlsxLoader(),
}


@dataclass
class IngestDeps:
    """Everything `ingest_document` — and `app/ingest/lifecycle.py`'s
    re-parse, which shares `load_and_chunk` — needs. Assembled once by the
    caller from `bootstrap()`'s `Application` plus an `IndexWriter`.
    """

    engine: Engine
    cfg: AppConfig
    classify_llm: LLMProvider
    index_writer: IndexWriter
    loaders: Mapping[str, DocumentLoader] = field(default_factory=lambda: DEFAULT_LOADERS)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_status(engine: Engine, doc_id: int, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(documents_table)
            .where(documents_table.c.id == doc_id)
            .values(status=status)
        )


def _mark_failed(engine: Engine, doc_id: int, message: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(documents_table)
            .where(documents_table.c.id == doc_id)
            .values(status="failed", error_message=message, chunk_count=0)
        )


def _mark_indexed(engine: Engine, doc_id: int, *, intent_slug: str, chunk_count: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(documents_table)
            .where(documents_table.c.id == doc_id)
            .values(
                status="indexed",
                error_message=None,
                intent_slug=intent_slug,
                chunk_count=chunk_count,
                indexed_at=_utc_now_iso(),
            )
        )


def _markdown_to_rows(markdown: str) -> list[list[str]]:
    """Parse a loader-generated `| cell | cell |` markdown table back into
    raw rows so `is_ragged` — which inspects the pre-markdown grid — can
    look at it. Every loader in `app/rag/loaders` renders tables in this
    exact shape (header row, a `| --- | ... |` separator, then body rows),
    so this is not a general markdown parser — it only has to round-trip
    what this codebase itself produces. Row index 1 (the separator) is
    skipped.
    """
    lines = [line for line in markdown.strip().split("\n") if line.strip()]
    rows: list[list[str]] = []
    for index, line in enumerate(lines):
        if index == 1:
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _repair_ragged_tables(blocks: list[Block], llm: LLMProvider) -> list[Block]:
    """Replace each ragged table block's text with an LLM-restructured
    version; clean tables pass through unchanged and are never sent to the
    model — `spec: document-ingestion` § "Clean tables are not sent to the
    model".
    """
    repaired: list[Block] = []
    for block in blocks:
        if block.kind != "table" or not is_ragged(_markdown_to_rows(block.text)):
            repaired.append(block)
            continue
        new_text = repair_table(block.text, llm)
        repaired.append(Block(kind="table", text=new_text, source_ref=block.source_ref))
    return repaired


def _sample_text(blocks: list[Block]) -> str:
    """The document's content, in order, for intent suggestion — which
    itself truncates to the configured sample size
    (`app/ingest/classify_doc.py`).
    """
    return "\n\n".join(block.text for block in blocks)


def load_and_chunk(
    path: Path,
    cfg: AppConfig,
    classify_llm: LLMProvider,
    loaders: Mapping[str, DocumentLoader] = DEFAULT_LOADERS,
) -> tuple[list[Block], list[Chunk]]:
    """Load `path`, repair any ragged table blocks, and chunk the result.

    Shared by `ingest_document` (fresh ingest, below) and
    `app/ingest/lifecycle.py`'s re-parse: both run the identical
    load -> repair -> chunk pipeline and differ only in what happens to
    the document's intent space afterward.
    """
    ext = path.suffix.lower()
    loader = loaders.get(ext)
    if loader is None:
        raise LoaderError(
            f"no loader registered for extension {ext!r}; supported: "
            f"{', '.join(sorted(loaders))}"
        )
    blocks = loader.load(path)
    blocks = _repair_ragged_tables(blocks, classify_llm)
    chunks = chunk_blocks(blocks, cfg.rag)
    return blocks, chunks


def ingest_document(doc_id: int, path: Path, deps: IngestDeps) -> None:
    """Run the full ingestion pipeline for `doc_id`, whose uploaded file
    lives at `path`: load -> repair tables -> chunk -> suggest intent ->
    embed -> index -> status `indexed` with chunk count and timestamp.

    Any failure sets status `failed` with a human-readable message and
    leaves no partial chunks, chunk_fts rows, or vectors behind for this
    document — see the module docstring. The document row itself is never
    touched beyond its status columns, so it stays listed and retryable.
    """
    _set_status(deps.engine, doc_id, "parsing")

    try:
        blocks, chunk_list = load_and_chunk(path, deps.cfg, deps.classify_llm, deps.loaders)
        intent_slug = suggest_intent(_sample_text(blocks), deps.cfg, deps.classify_llm)
        deps.index_writer.write_document(doc_id, intent_slug, chunk_list)
    except Exception as exc:
        deps.index_writer.remove_document(doc_id)
        _mark_failed(deps.engine, doc_id, str(exc))
        return

    _mark_indexed(deps.engine, doc_id, intent_slug=intent_slug, chunk_count=len(chunk_list))
