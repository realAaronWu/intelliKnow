from __future__ import annotations

import json

from sqlalchemy import select

from app.analytics.log import QueryLogger
from app.channels.base import InboundMessage
from app.db import create_engine_for, init_schema, query_log
from app.orchestrator.pipeline import QueryOutcome
from app.rag.citations import Citation


def _engine(tmp_path):
    engine = create_engine_for(tmp_path / "queries.db")
    init_schema(engine)
    return engine


def _outcome() -> QueryOutcome:
    return QueryOutcome(
        answer="Grounded answer. [1]",
        citations=[Citation(7, "handbook.pdf", "p. 2")],
        intent_slug="hr",
        confidence=0.91,
        classified_by="centroid",
        reasoning=None,
        classification_failed=False,
        fallback_used=False,
        status="success",
        retrieved_doc_ids=[7],
        latency_ms=120,
        error=None,
    )


def test_query_logger_persists_delivery_latency_and_serialized_sources(tmp_path):
    engine = _engine(tmp_path)
    logger = QueryLogger(engine)
    message = InboundMessage("telegram", "user-1", "What is leave?", "chat-1")

    logger.record(message, _outcome(), latency_ms=340)

    with engine.connect() as conn:
        row = conn.execute(select(query_log)).one()
    assert row.status == "success"
    assert row.latency_ms == 340
    assert row.answer == "Grounded answer. [1]"
    assert json.loads(row.citations_json) == [
        {"document_id": 7, "document_title": "handbook.pdf", "source_ref": "p. 2"}
    ]
    assert json.loads(row.retrieved_doc_ids_json) == [7]


def test_query_logger_records_delivery_failure_with_pipeline_context(tmp_path):
    engine = _engine(tmp_path)
    logger = QueryLogger(engine)
    message = InboundMessage("teams", "user-2", "Question", "conversation")

    logger.record_failure(message, "send failed", latency_ms=500, outcome=_outcome())

    with engine.connect() as conn:
        row = conn.execute(select(query_log)).one()
    assert row.status == "failed"
    assert row.intent_slug == "hr"
    assert row.error == "send failed"
    assert row.latency_ms == 500
