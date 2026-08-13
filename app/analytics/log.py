"""Append-only query history writes."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import Engine, insert

from app.channels.base import InboundMessage
from app.db import query_log
from app.orchestrator.pipeline import QueryOutcome


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class QueryLogger:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self, message: InboundMessage, outcome: QueryOutcome, latency_ms: int
    ) -> None:
        self._insert(message, outcome, latency_ms, status=outcome.status, error=outcome.error)

    def record_failure(
        self,
        message: InboundMessage,
        error: str,
        latency_ms: int,
        outcome: QueryOutcome | None = None,
    ) -> None:
        self._insert(message, outcome, latency_ms, status="failed", error=error)

    def _insert(
        self,
        message: InboundMessage,
        outcome: QueryOutcome | None,
        latency_ms: int,
        *,
        status: str,
        error: str | None,
    ) -> None:
        citations = [asdict(citation) for citation in outcome.citations] if outcome else []
        retrieved_ids = outcome.retrieved_doc_ids if outcome else []
        with self._engine.begin() as conn:
            conn.execute(
                insert(query_log).values(
                    created_at=_utc_now_iso(),
                    channel=message.channel,
                    user_ref=message.user_ref,
                    question=message.text or "",
                    intent_slug=outcome.intent_slug if outcome else None,
                    confidence=outcome.confidence if outcome else None,
                    classified_by=outcome.classified_by if outcome else None,
                    reasoning=outcome.reasoning if outcome else None,
                    fallback_used=outcome.fallback_used if outcome else False,
                    status=status,
                    answer=outcome.answer if outcome else None,
                    citations_json=json.dumps(citations),
                    retrieved_doc_ids_json=json.dumps(retrieved_ids),
                    latency_ms=latency_ms,
                    error=error,
                )
            )
