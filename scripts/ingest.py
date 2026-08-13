#!/usr/bin/env python3
"""Demo CLI: run one or more files through the real ingestion pipeline.

Usage:

    uv run python scripts/ingest.py tests/fixtures/docs/handbook.pdf [FILE ...]

Works with the shipped local-default `config.yaml` and no API key: both
the LLM and embedding providers default to backends that need no
credential (a local Ollama server for `llm.provider: local`, a locally
cached `sentence-transformers` model for `embedding.provider: local`). If
no local LLM server is reachable, intent suggestion and ragged-table
repair both fall back rather than raise — `spec: document-ingestion` §
"Suggestion unavailable" and § "Restructuring failure falls back to raw
text" — so the demo still completes end to end; that fallback is the
specified behaviour, not an error.

Prints, per document: status, assigned intent space, chunk count, and the
first few chunks with their heading path and source ref. Then prints
totals across all three stores (`chunks` rows, `chunk_fts` rows, FAISS
vectors per intent space) so the three-stores-agree invariant
`app/rag/index_writer.py` exists to guarantee is visible here, not only
asserted in tests.

Uses the same `data/` paths `config.yaml` names (SQLite database, FAISS
directory) — running this against the shipped config populates the real
local knowledge base, exactly what an admin uploading through the API
would produce.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `python scripts/ingest.py` puts this script's own directory on
# `sys.path[0]`, not the repo root, so `import app...` fails unless the
# repo root is added explicitly — this must run before any `app.*` import.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import insert, select, text  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.db import chunks as chunks_table  # noqa: E402
from app.db import create_engine_for  # noqa: E402
from app.db import documents as documents_table  # noqa: E402
from app.db import init_schema  # noqa: E402
from app.ingest.validate import ValidationError, validate_upload  # noqa: E402
from app.ingest.worker import IngestDeps, _utc_now_iso, ingest_document  # noqa: E402
from app.rag.index_writer import IndexWriter  # noqa: E402
from app.rag.vector_store import VectorStore  # noqa: E402

_CHUNK_PREVIEW_COUNT = 3
_CHUNK_PREVIEW_CHARS = 100

# Human-readable reasons for `documents.intent_assigned_by` values other
# than "model" — see `app/ingest/classify_doc.py::IntentSuggestion`. An
# operator must be able to tell a model-assigned space from a fallback one
# at a glance, per Task 9's fallback-visibility fix.
_ASSIGNED_BY_LABELS = {
    "provider_error": "provider error",
    "invalid_slug": "invalid slug suggested",
}


def _intent_space_label(row) -> str:
    assigned_by = row.intent_assigned_by
    if assigned_by == "model":
        return row.intent_slug
    reason = _ASSIGNED_BY_LABELS.get(assigned_by, assigned_by)
    return f"{row.intent_slug} (fallback: {reason})"


def _build_deps() -> IngestDeps:
    """Wire `IngestDeps` from the real composition root — the same
    `bootstrap()` call every other entry point uses — plus a real
    `Engine`/`VectorStore`/`IndexWriter` opened at the paths named in
    `config.yaml`.
    """
    application = bootstrap()
    cfg = application.config

    engine = create_engine_for(Path(cfg.storage.sqlite_path))
    init_schema(engine)
    vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)
    index_writer = IndexWriter(
        engine,
        vector_store,
        application.embedding,
        batch_size=cfg.embedding.batch_size,
    )

    return IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=application.classify_llm,
        embedding=application.embedding,
        vector_store=vector_store,
        index_writer=index_writer,
    )


def _insert_pending(deps: IngestDeps, validated) -> int:
    with deps.engine.begin() as conn:
        result = conn.execute(
            insert(documents_table).values(
                filename=validated.filename,
                ext=validated.ext,
                size_bytes=validated.size_bytes,
                sha256=validated.sha256,
                intent_slug=deps.cfg.orchestrator.fallback_space,
                status="pending",
                error_message=None,
                chunk_count=0,
                uploaded_at=_utc_now_iso(),
                indexed_at=None,
            )
        )
        return result.inserted_primary_key[0]


def _print_document_result(deps: IngestDeps, doc_id: int, path: Path) -> None:
    with deps.engine.connect() as conn:
        row = conn.execute(select(documents_table).where(documents_table.c.id == doc_id)).one()

    print(f"\n{path.name}")
    print(f"  status:       {row.status}")
    print(f"  intent space: {_intent_space_label(row)}")
    print(f"  chunk count:  {row.chunk_count}")
    if row.status == "failed":
        print(f"  error:        {row.error_message}")
        return

    with deps.engine.connect() as conn:
        chunk_rows = conn.execute(
            select(chunks_table)
            .where(chunks_table.c.document_id == doc_id)
            .order_by(chunks_table.c.ordinal)
            .limit(_CHUNK_PREVIEW_COUNT)
        ).all()

    for chunk in chunk_rows:
        preview = chunk.text.replace("\n", " ")[:_CHUNK_PREVIEW_CHARS]
        ellipsis = "..." if len(chunk.text) > _CHUNK_PREVIEW_CHARS else ""
        heading = chunk.heading_path or "(no heading)"
        print(f"    [{chunk.ordinal}] {heading} | {chunk.source_ref}")
        print(f"        {preview}{ellipsis}")


def _print_totals(deps: IngestDeps) -> None:
    with deps.engine.connect() as conn:
        chunk_row_count = conn.execute(text("SELECT count(*) FROM chunks")).scalar_one()
        fts_row_count = conn.execute(text("SELECT count(*) FROM chunk_fts")).scalar_one()

    print("\n--- Totals across all three stores ---")
    print(f"  chunk rows (SQLite 'chunks'):   {chunk_row_count}")
    print(f"  keyword rows (SQLite 'chunk_fts'): {fts_row_count}")

    dimension = deps.cfg.embedding.dimension
    probe = [1.0] + [0.0] * (dimension - 1)
    print("  FAISS vectors by intent space:")
    for space in deps.cfg.intent_spaces:
        count = len(deps.vector_store.search(space.slug, probe, top_n=1_000_000))
        print(f"    {space.slug:<12} {count}")


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: scripts/ingest.py FILE [FILE ...]", file=sys.stderr)
        return 2

    deps = _build_deps()

    for arg in argv:
        path = Path(arg).resolve()
        if not path.exists():
            print(f"\n{path.name}: file not found, skipping")
            continue

        content = path.read_bytes()
        try:
            validated = validate_upload(path.name, content, deps.cfg, deps.engine)
        except ValidationError as exc:
            print(f"\n{path.name}: rejected at upload — {exc}")
            continue

        doc_id = _insert_pending(deps, validated)
        ingest_document(doc_id, path, deps)
        _print_document_result(deps, doc_id, path)

    _print_totals(deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
