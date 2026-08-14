"""Bounded classifier examples sourced from admin-reviewed query history."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, select

from app.db import query_log


@dataclass(frozen=True)
class ClassificationExample:
    question: str
    intent_slug: str


def normalize_question(value: str) -> str:
    return " ".join(value.casefold().split())


def load_classification_examples(
    engine: Engine,
    *,
    limit: int = 30,
    per_intent: int = 8,
    max_question_chars: int = 240,
) -> list[ClassificationExample]:
    """Return newest reviewed labels, deduplicated and capped for prompt size."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(query_log.c.question, query_log.c.expected_intent_slug)
            .where(query_log.c.expected_intent_slug.is_not(None))
            .order_by(query_log.c.reviewed_at.desc(), query_log.c.id.desc())
            .limit(max(limit * 4, limit))
        ).all()

    examples: list[ClassificationExample] = []
    seen_questions: set[str] = set()
    counts: dict[str, int] = {}
    for question, intent_slug in rows:
        normalized = normalize_question(question or "")
        if not normalized or not intent_slug or normalized in seen_questions:
            continue
        if counts.get(intent_slug, 0) >= per_intent:
            continue
        compact_question = " ".join(question.split())[:max_question_chars]
        examples.append(ClassificationExample(compact_question, intent_slug))
        seen_questions.add(normalized)
        counts[intent_slug] = counts.get(intent_slug, 0) + 1
        if len(examples) >= limit:
            break
    return examples


def examples_by_intent(
    examples: list[ClassificationExample], valid_slugs: set[str]
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for example in examples:
        if example.intent_slug in valid_slugs:
            grouped.setdefault(example.intent_slug, []).append(example.question)
    return {slug: tuple(questions) for slug, questions in grouped.items()}
