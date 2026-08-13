"""Tests for document intent suggestion at ingest.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §9.5-9.7.
"""

from __future__ import annotations

import pytest

from app.config import AppConfig
from app.ingest.classify_doc import preflight_classifier, suggest_intent
from app.orchestrator.errors import ClassificationError
from app.providers.base import ProviderError
from tests.doubles import FakeLLMProvider


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


# --- 9.5 Suggestion prompt ------------------------------------------------------


def test_9_5_prompt_contains_every_space_name_description_and_keywords(cfg, llm):
    llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})

    suggest_intent("Some document content about leave policy.", cfg, llm)

    assert len(llm.calls) == 1
    prompt = llm.calls[0]["user"] + llm.calls[0]["system"]
    for space in cfg.intent_spaces:
        assert space.name in prompt
        assert space.description in prompt
        for keyword in space.keywords:
            assert keyword in prompt


def test_9_5_prompt_contains_first_2000_characters_of_document(cfg, llm):
    llm.expect_schema({"slug": "hr", "confidence": 0.95, "reasoning": "clear match"})
    long_text = "A" * 2500 + "TAIL_MARKER"

    suggest_intent(long_text, cfg, llm)

    prompt = llm.calls[0]["user"]
    assert "A" * 2000 in prompt
    assert "TAIL_MARKER" not in prompt


# --- 9.6 Suggestion applied ------------------------------------------------------


def test_9_6_returned_slug_becomes_the_documents_space(cfg, llm):
    llm.expect_schema({"slug": "finance", "confidence": 0.95, "reasoning": "clear match"})

    suggestion = suggest_intent("Expense reimbursement and budget content.", cfg, llm)

    assert suggestion.slug == "finance"
    assert suggestion.assigned_by == "model"


def test_9_6_unknown_slug_fails_closed(cfg, llm):
    llm.expect_schema({"slug": "not-a-real-space", "confidence": 0.95, "reasoning": "clear match"})

    with pytest.raises(ClassificationError, match="invalid intent"):
        suggest_intent("Some content.", cfg, llm)


# --- 9.7 Provider failure ---------------------------------------------------------


def test_9_7_provider_failure_is_retryable_and_never_assigns_general(cfg, llm):
    llm.fail_next(ProviderError.backend("provider is down"))

    with pytest.raises(ClassificationError, match="not indexed; please retry"):
        suggest_intent("Some document content.", cfg, llm)


def test_9_7_provider_failure_logs_an_error_naming_the_document(
    cfg, llm, caplog
):
    llm.fail_next(ProviderError.backend("provider is down"))

    with caplog.at_level("ERROR", logger="app.ingest.classify_doc"):
        with pytest.raises(ClassificationError):
            suggest_intent("Some document content.", cfg, llm, doc_id=42)

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "42" in message


def test_low_confidence_document_classification_fails_closed(cfg, llm):
    llm.expect_schema({"slug": "finance", "confidence": 0.4, "reasoning": "unclear"})

    with pytest.raises(ClassificationError, match="below the required"):
        suggest_intent("Possibly an expense document.", cfg, llm)


def test_anthropic_compatible_schema_keeps_confidence_validation_in_application(cfg, llm):
    llm.expect_schema({"slug": "finance", "confidence": 1.2, "reasoning": "invalid"})

    with pytest.raises(ClassificationError, match="invalid confidence"):
        suggest_intent("Expense reimbursement and budget content.", cfg, llm)

    confidence_schema = llm.calls[0]["schema"]["properties"]["confidence"]
    assert confidence_schema == {"type": "number"}


def test_preflight_exercises_the_exact_document_classification_schema(cfg, llm):
    probe_slug = cfg.intent_spaces[0].slug
    llm.expect_schema({"slug": probe_slug, "confidence": 1.0, "reasoning": "preflight"})

    preflight_classifier(cfg, llm)
    preflight_schema = llm.calls[0]["schema"]

    llm.expect_schema({"slug": "finance", "confidence": 0.95, "reasoning": "clear match"})
    suggest_intent("Expense reimbursement and budget content.", cfg, llm)
    assert preflight_schema == llm.calls[1]["schema"]
