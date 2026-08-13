"""Admin test-query API: the only path from the admin console into the
orchestrator.

`spec: query-orchestration` § "Pipeline invocation without a chat
channel": `POST /admin/test-query` runs a question through the exact same
`answer_question` pipeline a real chat message would, and returns intent,
confidence, answer, sources, and latency — but never delivers anywhere.
There is no Telegram/Teams adapter call anywhere in this module, which is
what makes that guarantee true rather than merely documented: nothing
here imports one.

This single endpoint is deliberately the *only* thing the admin console
calls to reach the orchestrator (per the brief) — it backs both the
Dashboard's "Try a query" box and, once channel adapters exist (a later
increment), the per-channel connection test, so both exercise identical
classification and retrieval behaviour rather than two subtly different
code paths.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.analytics.log import QueryLogger
from app.orchestrator.pipeline import PipelineDeps, answer_question
from app.rag.generate import ChannelProfile

# The profile used for every admin test-query, regardless of which real
# channel an operator is ultimately testing for. Generous limits and
# plain markup so the raw answer is legible in the console UI; a
# per-channel profile (matching `app.config.ChannelConfig`) is future
# work for when a channel adapter's own formatting is what is under test.
ADMIN_CHANNEL_PROFILE = ChannelProfile(
    name="admin-test", max_chars=4000, markup="plain", supports_lists=True
)


class TestQueryRequest(BaseModel):
    question: str


def _source_dict(citation) -> dict:
    return {
        "document_id": citation.document_id,
        "document_title": citation.document_title,
        "source_ref": citation.source_ref,
    }


def build_query_router(
    deps: PipelineDeps, query_logger: QueryLogger | None = None
) -> APIRouter:
    """Build the `/admin/test-query` router bound to `deps`.

    A pure function of `deps`, matching `app/api/documents.py::
    build_documents_router` — tests pass a `deps` built entirely from
    fakes and a tmp-path SQLite/FAISS setup, so no test in this suite
    makes a real API call.
    """
    router = APIRouter()

    @router.post("/admin/test-query")
    def test_query(body: TestQueryRequest) -> dict:
        outcome = answer_question(body.question, ADMIN_CHANNEL_PROFILE, deps)
        if query_logger is not None:
            query_logger.record_admin(body.question, outcome)
        if outcome.classification_failed:
            raise HTTPException(
                status_code=503,
                detail=outcome.error or "Intent classification is unavailable; please retry.",
            )
        return {
            "intent_slug": outcome.intent_slug,
            "confidence": outcome.confidence,
            "classified_by": outcome.classified_by,
            "fallback_used": outcome.fallback_used,
            "status": outcome.status,
            "answer": outcome.answer,
            "sources": [_source_dict(c) for c in outcome.citations],
            "retrieved_doc_ids": outcome.retrieved_doc_ids,
            "latency_ms": outcome.latency_ms,
            "error": outcome.error,
        }

    return router
