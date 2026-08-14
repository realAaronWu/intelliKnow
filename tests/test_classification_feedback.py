from __future__ import annotations

from sqlalchemy import insert

from app.db import create_engine_for, init_schema, query_log
from app.orchestrator.feedback import (
    ClassificationExample,
    examples_by_intent,
    load_classification_examples,
)


def _reviewed_query(conn, *, question: str, expected: str, created: str) -> None:
    conn.execute(
        insert(query_log).values(
            created_at=created,
            channel="admin",
            question=question,
            intent_slug="general",
            confidence=0.75,
            fallback_used=False,
            status="no_match",
            expected_intent_slug=expected,
            reviewed_correct=False,
            reviewed_at=created,
        )
    )


def test_reviewed_examples_are_newest_deduplicated_and_bounded(tmp_path):
    engine = create_engine_for(tmp_path / "feedback.db")
    init_schema(engine)
    with engine.begin() as conn:
        _reviewed_query(
            conn,
            question="Which travel form?",
            expected="finance",
            created="2026-08-12T10:00:00Z",
        )
        _reviewed_query(
            conn,
            question="  WHICH   TRAVEL FORM? ",
            expected="hr",
            created="2026-08-12T11:00:00Z",
        )
        _reviewed_query(
            conn,
            question="How do I submit expenses?",
            expected="finance",
            created="2026-08-12T12:00:00Z",
        )

    examples = load_classification_examples(engine, limit=10, per_intent=1)

    assert examples == [
        ClassificationExample("How do I submit expenses?", "finance"),
        ClassificationExample("WHICH TRAVEL FORM?", "hr"),
    ]
    assert examples_by_intent(examples, {"finance"}) == {
        "finance": ("How do I submit expenses?",)
    }


def test_reviewed_examples_cap_question_length(tmp_path):
    engine = create_engine_for(tmp_path / "feedback.db")
    init_schema(engine)
    with engine.begin() as conn:
        _reviewed_query(
            conn,
            question="A" * 500,
            expected="legal",
            created="2026-08-12T10:00:00Z",
        )

    examples = load_classification_examples(engine, max_question_chars=40)

    assert examples == [ClassificationExample("A" * 40, "legal")]
