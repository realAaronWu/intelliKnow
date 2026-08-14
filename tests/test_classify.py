"""Test-plan §9 — intent classification.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §9

`classify()` takes an already-built `CentroidIndex` and an already-embedded
`query_vector` — never touches an `EmbeddingProvider` itself — and an
`LLMProvider` it calls at most once, only on the escalation path. Every
test pins centroid vectors via `FakeEmbeddingProvider.set_vector` so
confidence values are exactly known rather than merely plausible.
"""

from __future__ import annotations

import pytest

from app.config import AppConfig
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.classify import Classification, classify
from app.orchestrator.errors import ClassificationError
from app.orchestrator.feedback import ClassificationExample
from app.providers.base import ProviderError
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider

DIMENSION = 8

# Orthonormal so cosine similarity against a query aligned with one of them
# is exactly 1.0 against that space and 0.0 against the others.
_HR_VEC = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_LEGAL_VEC = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_GENERAL_VEC = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_QUERY_ALIGNED_WITH_HR = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
# Equidistant from every centroid (dot product 0 against each orthonormal
# axis) so the softmax is uniform — low confidence on any space, forcing
# escalation.
_AMBIGUOUS_QUERY = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]


def _space_text(name: str, description: str, keywords: list[str]) -> str:
    parts = [name, description]
    if keywords:
        parts.append(" ".join(keywords))
    return " ".join(parts)


def _cfg(*, threshold: float = 0.70, escalate: bool = True, temperature: float = 0.05):
    return AppConfig.model_validate(
        {
            "embedding": {"model": "fake-embed-model", "dimension": DIMENSION},
            "orchestrator": {
                "confidence_threshold": threshold,
                "escalate_to_llm": escalate,
                "centroid_temperature": temperature,
                "fallback_space": "general",
            },
            "intent_spaces": [
                {
                    "slug": "hr",
                    "name": "HR",
                    "description": "Employee policies, leave, benefits",
                    "keywords": ["leave", "vacation"],
                },
                {
                    "slug": "legal",
                    "name": "Legal",
                    "description": "Contracts and compliance",
                    "keywords": ["contract", "NDA"],
                },
                {
                    "slug": "general",
                    "name": "General",
                    "description": "Fallback — searches every space",
                    "keywords": [],
                },
            ],
        }
    )


def _centroids(cfg: AppConfig) -> tuple[CentroidIndex, FakeEmbeddingProvider]:
    embedder = FakeEmbeddingProvider(dimension=DIMENSION)
    hr, legal, general = cfg.intent_spaces
    embedder.set_vector(_space_text(hr.name, hr.description, hr.keywords), _HR_VEC)
    embedder.set_vector(_space_text(legal.name, legal.description, legal.keywords), _LEGAL_VEC)
    embedder.set_vector(
        _space_text(general.name, general.description, general.keywords), _GENERAL_VEC
    )
    return CentroidIndex(embedder, cfg), embedder


# --- 9.1 High centroid confidence makes no LLM call ---------------------------


def test_9_1_high_centroid_confidence_makes_no_llm_call():
    cfg = _cfg(threshold=0.70)
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()

    result = classify("how much annual leave do I get", _QUERY_ALIGNED_WITH_HR, cfg, centroids, llm)

    assert len(llm.calls) == 0
    assert result.classified_by == "centroid"
    assert result.intent_slug == "hr"
    assert result.failed is False


# --- 9.2 Low centroid confidence escalates -------------------------------------


def test_9_2_low_centroid_confidence_escalates():
    cfg = _cfg(threshold=0.70, temperature=0.05)
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()
    llm.expect_schema({"slug": "hr", "confidence": 0.88, "reasoning": "mentions leave policy"})

    result = classify("what about that thing", _AMBIGUOUS_QUERY, cfg, centroids, llm)

    assert len(llm.calls) == 1
    assert result.classified_by == "llm"
    assert result.intent_slug == "hr"
    assert result.confidence == 0.88
    assert result.reasoning == "LLM selected hr at 88% confidence."
    assert llm.calls[0]["max_tokens"] == 48
    assert "reasoning" not in llm.calls[0]["schema"]["properties"]
    assert result.failed is False


# --- 9.3 Escalation prompt built from live config ------------------------------


def test_9_3_escalation_prompt_contains_every_spaces_name_description_keywords():
    cfg = _cfg()
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()
    llm.expect_schema({"slug": "hr", "confidence": 0.9, "reasoning": "x"})

    classify("ambiguous", _AMBIGUOUS_QUERY, cfg, centroids, llm)

    prompt = llm.calls[0]["system"] + "\n" + llm.calls[0]["user"]
    for space in cfg.intent_spaces:
        assert space.name in prompt
        assert space.description in prompt
        for keyword in space.keywords:
            assert keyword in prompt


# --- 9.4 Keyword edit takes effect ---------------------------------------------


def test_9_4_keyword_edit_changes_next_escalation_prompt_no_restart():
    cfg1 = _cfg()
    centroids, _ = _centroids(cfg1)
    llm = FakeLLMProvider()
    llm.expect_schema({"slug": "hr", "confidence": 0.9, "reasoning": "x"})
    classify("ambiguous", _AMBIGUOUS_QUERY, cfg1, centroids, llm)
    assert "sabbatical" not in llm.calls[0]["user"]

    raw = cfg1.model_dump(mode="json")
    raw["intent_spaces"][0]["keywords"] = ["leave", "vacation", "sabbatical"]
    cfg2 = AppConfig.model_validate(raw)

    llm.expect_schema({"slug": "hr", "confidence": 0.9, "reasoning": "x"})
    classify("ambiguous", _AMBIGUOUS_QUERY, cfg2, centroids, llm)

    assert "sabbatical" in llm.calls[1]["user"]


# --- 9.5 Escalation disabled ----------------------------------------------------


def test_9_5_escalation_disabled_fails_closed_with_zero_llm_calls():
    cfg = _cfg(threshold=0.70, escalate=False)
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()

    with pytest.raises(ClassificationError, match="escalation is disabled"):
        classify("ambiguous", _AMBIGUOUS_QUERY, cfg, centroids, llm)

    assert len(llm.calls) == 0


# --- 9.6 Escalated result also below threshold ---------------------------------


def test_9_6_escalated_result_below_threshold_fails_closed():
    cfg = _cfg(threshold=0.70)
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()
    llm.expect_schema({"slug": "hr", "confidence": 0.40, "reasoning": "not sure"})

    with pytest.raises(ClassificationError, match="below the required"):
        classify("ambiguous", _AMBIGUOUS_QUERY, cfg, centroids, llm)


# --- 9.7 Unknown slug from LLM --------------------------------------------------


def test_9_7_unknown_slug_fails_closed():
    cfg = _cfg(threshold=0.70)
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()
    llm.expect_schema({"slug": "not-a-real-space", "confidence": 0.95, "reasoning": "??"})

    with pytest.raises(ClassificationError, match="not-a-real-space"):
        classify("ambiguous", _AMBIGUOUS_QUERY, cfg, centroids, llm)


# --- 9.8 / 9.9 Provider failure / timeout during escalation ---------------------


@pytest.mark.parametrize(
    "error",
    [ProviderError.backend("boom"), ProviderError.timeout("too slow")],
    ids=["provider_failure", "timeout"],
)
def test_9_8_9_9_provider_failure_or_timeout_is_retryable(error):
    cfg = _cfg(threshold=0.70)
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()
    llm.fail_next(error)

    with pytest.raises(ClassificationError, match="Please retry"):
        classify("ambiguous", _AMBIGUOUS_QUERY, cfg, centroids, llm)


def test_admin_reviewed_exact_question_overrides_model_classification():
    cfg = _cfg()
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()

    result = classify(
        "  WHICH   TRAVEL FORM? ",
        _QUERY_ALIGNED_WITH_HR,
        cfg,
        centroids,
        llm,
        [ClassificationExample("Which travel form?", "legal")],
    )

    assert result.intent_slug == "legal"
    assert result.classified_by == "review"
    assert result.confidence == 1.0
    assert len(llm.calls) == 0


def test_admin_reviewed_examples_reach_escalation_prompt():
    cfg = _cfg()
    centroids, _ = _centroids(cfg)
    llm = FakeLLMProvider()
    llm.expect_schema({"slug": "hr", "confidence": 0.9, "reasoning": "x"})

    classify(
        "ambiguous",
        _AMBIGUOUS_QUERY,
        cfg,
        centroids,
        llm,
        [ClassificationExample("How much parental leave?", "hr")],
    )

    assert '"question": "How much parental leave?"' in llm.calls[0]["user"]
    assert '"slug": "hr"' in llm.calls[0]["user"]


# --- 9.10 Uses classify model ----------------------------------------------------


def test_9_10_uses_whichever_llm_provider_is_injected():
    """`classify()` never builds its own provider — it calls exactly the
    `llm` it was given. Production wiring passes the classify-role
    provider (`build_llm_provider(cfg, role="classify")`, see
    `app/bootstrap.py`); this proves the seam that makes that true rather
    than re-testing the factory itself.
    """
    cfg = _cfg(threshold=0.70)
    centroids, _ = _centroids(cfg)
    classify_llm = FakeLLMProvider()
    generate_llm = FakeLLMProvider()
    classify_llm.expect_schema({"slug": "hr", "confidence": 0.9, "reasoning": "x"})

    classify("ambiguous", _AMBIGUOUS_QUERY, cfg, centroids, classify_llm)

    assert len(classify_llm.calls) == 1
    assert len(generate_llm.calls) == 0
