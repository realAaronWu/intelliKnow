"""Document admin API: upload, list/search, detail, reparse, reassign,
delete, and full re-index.

`spec: document-ingestion` § "Asynchronous processing with visible
status": upload validates and returns immediately with the new document's
id and status `pending`, and the actual pipeline
(`app/ingest/worker.py::ingest_document`) runs as a `BackgroundTasks` job —
per `design.md`, a single-process MVP needs no broker. Reparse and
full-reindex are scheduled the same way; reassign and delete are cheap
enough (no re-embedding, no file I/O) to run inline.

Every validation failure returns `HTTPException` with a `detail` string
built to be read by a human directly — never a bare status code — per
Task 12's brief: `app/ingest/validate.py::ValidationError` and
`app/ingest/lifecycle.py::reassign_document`'s `ValueError` messages are
already written that way, so this module mostly just forwards them.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import insert, select, text

from app.db import chunks as chunks_table
from app.db import documents as documents_table
from app.ingest.lifecycle import delete_document, reassign_document, reindex_all, reparse_document
from app.ingest.validate import ValidationError, validate_upload
from app.ingest.worker import IngestDeps, _utc_now_iso, ingest_document


class ReassignRequest(BaseModel):
    intent_slug: str


def _upload_path(deps: IngestDeps, doc_id: int, ext: str) -> Path:
    return Path(deps.cfg.storage.upload_dir) / f"{doc_id}{ext}"


def _document_row_or_404(deps: IngestDeps, doc_id: int):
    with deps.engine.connect() as conn:
        row = conn.execute(
            select(documents_table).where(documents_table.c.id == doc_id)
        ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"document {doc_id} not found")
    return row


def _document_summary(row) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "ext": row.ext,
        "size_bytes": row.size_bytes,
        "intent_slug": row.intent_slug,
        "status": row.status,
        "chunk_count": row.chunk_count,
        "uploaded_at": row.uploaded_at,
        "indexed_at": row.indexed_at,
    }


def build_documents_router(deps: IngestDeps) -> APIRouter:
    """Build the `/documents` router bound to `deps`.

    A pure function of `deps` — no bootstrap-style side effects — so tests
    can pass a `deps` built entirely from fakes (`tests/doubles.py`) and
    an in-memory/tmp-path SQLite + FAISS setup, per the "make no API
    calls" constraint on this increment's test suite.
    """
    router = APIRouter()

    @router.post("/documents", status_code=202)
    async def upload_document(
        background_tasks: BackgroundTasks, file: UploadFile = File(...)
    ) -> dict:
        content = await file.read()
        try:
            validated = validate_upload(file.filename, content, deps.cfg, deps.engine)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        uploaded_at = _utc_now_iso()
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
                    uploaded_at=uploaded_at,
                    indexed_at=None,
                )
            )
            doc_id = result.inserted_primary_key[0]

        path = _upload_path(deps, doc_id, validated.ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

        background_tasks.add_task(ingest_document, doc_id, path, deps)

        return {"id": doc_id, "status": "pending"}

    @router.get("/documents")
    def list_documents(
        q: str | None = None,
        format: str | None = None,
        intent_space: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: dict[str, str] = {}

        if q:
            clauses.append(
                "(filename LIKE :q_like OR id IN ("
                "SELECT chunks.document_id FROM chunk_fts "
                "JOIN chunks ON chunk_fts.rowid = chunks.id "
                "WHERE chunk_fts MATCH :q_match))"
            )
            params["q_like"] = f"%{q}%"
            params["q_match"] = q
        if format:
            clauses.append("ext = :fmt")
            params["fmt"] = format if format.startswith(".") else f".{format}"
        if intent_space:
            clauses.append("intent_slug = :intent_space")
            params["intent_space"] = intent_space
        if date_from:
            clauses.append("uploaded_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("uploaded_at <= :date_to")
            params["date_to"] = date_to

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM documents {where} ORDER BY uploaded_at DESC"  # noqa: S608
        with deps.engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]

    @router.get("/documents/{doc_id}")
    def get_document(doc_id: int) -> dict:
        row = _document_row_or_404(deps, doc_id)
        with deps.engine.connect() as conn:
            chunk_rows = conn.execute(
                select(chunks_table)
                .where(chunks_table.c.document_id == doc_id)
                .order_by(chunks_table.c.ordinal)
            ).all()

        return {
            "id": row.id,
            "filename": row.filename,
            "ext": row.ext,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "intent_slug": row.intent_slug,
            "status": row.status,
            "error_message": row.error_message,
            "chunk_count": row.chunk_count,
            "uploaded_at": row.uploaded_at,
            "indexed_at": row.indexed_at,
            "chunks": [
                {
                    "ordinal": chunk.ordinal,
                    "text": chunk.text,
                    "heading_path": chunk.heading_path,
                    "source_ref": chunk.source_ref,
                    "char_count": chunk.char_count,
                }
                for chunk in chunk_rows
            ],
        }

    @router.post("/documents/{doc_id}/reparse", status_code=202)
    def reparse(doc_id: int, background_tasks: BackgroundTasks) -> dict:
        row = _document_row_or_404(deps, doc_id)
        path = _upload_path(deps, doc_id, row.ext)
        background_tasks.add_task(reparse_document, doc_id, path, deps)
        return {"id": doc_id, "status": "parsing"}

    @router.patch("/documents/{doc_id}")
    def reassign(doc_id: int, body: ReassignRequest) -> dict:
        _document_row_or_404(deps, doc_id)
        try:
            reassign_document(doc_id, body.intent_slug, deps)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = _document_row_or_404(deps, doc_id)
        return _document_summary(row)

    @router.delete("/documents/{doc_id}", status_code=204)
    def delete(doc_id: int) -> None:
        _document_row_or_404(deps, doc_id)
        delete_document(doc_id, deps)

    @router.post("/documents/reindex", status_code=202)
    def reindex(background_tasks: BackgroundTasks) -> dict:
        background_tasks.add_task(reindex_all, deps)
        return {"status": "reindexing"}

    return router
