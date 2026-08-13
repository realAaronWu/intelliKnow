"""Tests for document intent suggestion at ingest.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §9.5-9.7.
"""

from __future__ import annotations

import pytest

from app.config import AppConfig
from app.ingest.classify_doc import suggest_intent
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
    llm.expect_schema({"slug": "hr"})

    suggest_intent("Some document content about leave policy.", cfg, llm)

    assert len(llm.calls) == 1
    prompt = llm.calls[0]["user"] + llm.calls[0]["system"]
    for space in cfg.intent_spaces:
        assert space.name in prompt
        assert space.description in prompt
        for keyword in space.keywords:
            assert keyword in prompt


def test_9_5_prompt_contains_first_2000_characters_of_document(cfg, llm):
    llm.expect_schema({"slug": "hr"})
    long_text = "A" * 2500 + "TAIL_MARKER"

    suggest_intent(long_text, cfg, llm)

    prompt = llm.calls[0]["user"]
    assert "A" * 2000 in prompt
    assert "TAIL_MARKER" not in prompt


# --- 9.6 Suggestion applied ------------------------------------------------------


def test_9_6_returned_slug_becomes_the_documents_space(cfg, llm):
    llm.expect_schema({"slug": "finance"})

    slug = suggest_intent("Expense reimbursement and budget content.", cfg, llm)

    assert slug == "finance"


def test_9_6_unknown_slug_falls_back(cfg, llm):
    llm.expect_schema({"slug": "not-a-real-space"})

    slug = suggest_intent("Some content.", cfg, llm)

    assert slug == cfg.orchestrator.fallback_space


# --- 9.7 Provider failure ---------------------------------------------------------


def test_9_7_provider_failure_falls_back_to_configured_fallback_space(cfg, llm):
    llm.fail_next(ProviderError.backend("provider is down"))

    slug = suggest_intent("Some document content.", cfg, llm)

    assert slug == cfg.orchestrator.fallback_space
