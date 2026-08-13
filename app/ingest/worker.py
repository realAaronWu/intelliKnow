"""Ingestion worker: turns an uploaded file into indexed chunks.

Drives a document's status through the full pipeline —
`pending -> parsing -> indexed | failed` — per `spec: document-ingestion`
§ "Asynchronous processing with visible status" and § "Ingestion error
handling": load, repair any ragged table regions, chunk, suggest an
intent space, embed, and index.

Table repair may degrade safely, but intent classification is fail-closed:
an unavailable provider, malformed result, or below-threshold result stops
the document before indexing. Cleanup is identical for every failure:
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
from typing import Callable, Mapping

from sqlalchemy import Engine, update

from app.config import AppConfig
from app.db import documents as documents_table
from app.ingest.classify_doc import suggest_intent
from app.providers.base import EmbeddingProvider, LLMProvider
from app.rag.blocks import Block, DocumentLoader, LoaderError
from app.rag.chunker import Chunk, chunk_blocks
from app.rag.index_meta import record_meta_if_absent
from app.rag.index_writer import IndexWriter
from app.rag.loaders.docx import DocxLoader
from app.rag.loaders.pdf import PdfLoader
from app.rag.loaders.xlsx import XlsxLoader
from app.rag.tables import is_ragged, repair_table
from app.rag.vector_store import VectorStore

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
    re-parse, reassign, delete, and full re-index — need. Assembled once
    by the caller from `bootstrap()`'s `Application` plus an
    `IndexWriter`.

    `embedding` and `vector_store` duplicate what `index_writer` already
    holds internally (privately): per-document operations go through
    `index_writer` so its chunks/chunk_fts/FAISS invariant stays in one
    place, but a full re-index rebuilds every space from the `chunks`
    table directly — an operation `IndexWriter` has no per-document method
    for — and needs the raw embedder and vector store to do it.
    """

    engine: Engine
    cfg: AppConfig
    classify_llm: LLMProvider
    embedding: EmbeddingProvider
    vector_store: VectorStore
    index_writer: IndexWriter
    loaders: Mapping[str, DocumentLoader] = field(default_factory=lambda: DEFAULT_LOADERS)
    get_cfg: Callable[[], AppConfig] | None = None
    classification_preflight: Callable[[AppConfig], None] | None = None

    def current_cfg(self) -> AppConfig:
        return self.get_cfg() if self.get_cfg is not None else self.cfg


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_status(engine: Engine, doc_id: int, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(documents_table)
            .where(documents_table.c.id == doc_id)
            .values(status=status)
        )


def _mark_failed(
    engine: Engine, doc_id: int, message: str, *, clear_intent: bool = False
) -> None:
    values: dict = {"status": "failed", "error_message": message, "chunk_count": 0}
    if clear_intent:
        values.update(intent_slug="unclassified", intent_assigned_by="unclassified")
    with engine.begin() as conn:
        conn.execute(
            update(documents_table)
            .where(documents_table.c.id == doc_id)
            .values(**values)
        )


def _mark_indexed(
    engine: Engine,
    doc_id: int,
    *,
    intent_slug: str,
    chunk_count: int,
    intent_assigned_by: str | None = None,
) -> None:
    """Set status `indexed` plus the given intent/chunk fields.

    `intent_assigned_by` is optional and omitted from the UPDATE entirely
    when not given, leaving the document's existing value untouched — that
    is what lets `app/ingest/lifecycle.py::reparse_document` (which keeps
    the document's existing intent space rather than suggesting a fresh
    one) call this without disturbing who assigned it.
    """
    values: dict = {
        "status": "indexed",
        "error_message": None,
        "intent_slug": intent_slug,
        "chunk_count": chunk_count,
        "indexed_at": _utc_now_iso(),
    }
    if intent_assigned_by is not None:
        values["intent_assigned_by"] = intent_assigned_by
    with engine.begin() as conn:
        conn.execute(
            update(documents_table).where(documents_table.c.id == doc_id).values(**values)
        )


def _repair_ragged_tables(
    blocks: list[Block], llm: LLMProvider, *, doc_id: int | None = None
) -> list[Block]:
    """Replace each ragged table block with an LLM-restructured version;
    clean tables pass through unchanged and are never sent to the model —
    `spec: document-ingestion` § "Clean tables are not sent to the model".

    Raggedness is judged on the block's own structural `rows`. It used to
    be judged on rows reconstructed by splitting the block's rendered
    markdown on newlines, which turned every wrapped or multi-paragraph
    cell into a phantom short row — so a clean table full of wrapped cells
    was declared ragged and sent to the model, in production, while the
    single-line-cell fixtures in the test suite never noticed.
    """
    repaired: list[Block] = []
    for block in blocks:
        if block.kind != "table" or not is_ragged(block.rows or []):
            repaired.append(block)
            continue
        new_rows = repair_table(block.rows or [], llm, doc_id=doc_id)
        repaired.append(Block.table(rows=new_rows, source_ref=block.source_ref))
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
    *,
    doc_id: int | None = None,
) -> tuple[list[Block], list[Chunk]]:
    """Load `path`, repair any ragged table blocks, and chunk the result.

    Shared by `ingest_document` (fresh ingest, below) and
    `app/ingest/lifecycle.py`'s re-parse: both run the identical
    load -> repair -> chunk pipeline and differ only in what happens to
    the document's intent space afterward. `doc_id` is passed through to
    table repair purely so its fallback warning (`app/rag/tables.py`) can
    name the document.
    """
    ext = path.suffix.lower()
    loader = loaders.get(ext)
    if loader is None:
        raise LoaderError(
            f"no loader registered for extension {ext!r}; supported: "
            f"{', '.join(sorted(loaders))}"
        )
    blocks = loader.load(path)
    blocks = _repair_ragged_tables(blocks, classify_llm, doc_id=doc_id)
    chunks = chunk_blocks(blocks, cfg.rag)
    return blocks, chunks


def ingest_document(doc_id: int, path: Path, deps: IngestDeps) -> None:
    """Run the full ingestion pipeline for `doc_id`, whose uploaded file
    lives at `path`: load -> repair tables -> chunk -> suggest intent ->
    embed -> index -> record the embedding model (first ingest only) ->
    status `indexed` with chunk count and timestamp.

    Any failure sets status `failed` with a human-readable message and
    leaves no partial chunks, chunk_fts rows, or vectors behind for this
    document — see the module docstring. The document row itself is never
    touched beyond its status columns, so it stays listed and retryable.
    """
    _set_status(deps.engine, doc_id, "parsing")
    cfg = deps.current_cfg()

    try:
        blocks, chunk_list = load_and_chunk(
            path, cfg, deps.classify_llm, deps.loaders, doc_id=doc_id
        )
        suggestion = suggest_intent(
            _sample_text(blocks), cfg, deps.classify_llm, doc_id=doc_id
        )
        deps.index_writer.write_document(doc_id, suggestion.slug, chunk_list)
    except Exception as exc:
        deps.index_writer.remove_document(doc_id)
        _mark_failed(deps.engine, doc_id, str(exc), clear_intent=True)
        return

    # `spec: document-ingestion` § "Embedding model recorded at first
    # ingest". A no-op after the first document; see
    # `app/rag/index_meta.py::record_meta_if_absent`.
    record_meta_if_absent(
        Path(cfg.storage.faiss_dir),
        model=cfg.embedding.model,
        dimension=cfg.embedding.dimension,
    )

    _mark_indexed(
        deps.engine,
        doc_id,
        intent_slug=suggestion.slug,
        intent_assigned_by=suggestion.assigned_by,
        chunk_count=len(chunk_list),
    )
