"""Test-plan §10 — routing decision.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §10

`decide_spaces` takes a `Classification` directly — constructed by hand in
every test here, not produced by `classify()` — so each row of the
decision table (including the ones `classify()` would never itself
produce, like a high-confidence unknown slug) is exercised independently
of classification's own behaviour.
"""

from __future__ import annotations

from app.config import AppConfig
from app.orchestrator.classify import Classification
from app.orchestrator.route import decide_spaces


def _cfg(*, threshold: float = 0.70, fallback: str = "general") -> AppConfig:
    return AppConfig.model_validate(
        {
            "orchestrator": {"confidence_threshold": threshold, "fallback_space": fallback},
            "intent_spaces": [
                {"slug": "hr", "name": "HR", "description": "Employee policies", "keywords": []},
                {"slug": "finance", "name": "Finance", "description": "Money", "keywords": []},
                {"slug": "legal", "name": "Legal", "description": "Contracts", "keywords": []},
                {
                    "slug": "general",
                    "name": "General",
                    "description": "Fallback",
                    "keywords": [],
                },
            ],
        }
    )


def _classification(
    slug: str, confidence: float, *, classified_by="centroid", failed=False
) -> Classification:
    return Classification(
        intent_slug=slug,
        confidence=confidence,
        classified_by=classified_by,
        reasoning=None,
        failed=failed,
    )


# --- 10.1 Above threshold ------------------------------------------------------


def test_10_1_confidence_above_threshold_routes_to_single_space():
    cfg = _cfg(threshold=0.70)
    decision = decide_spaces(_classification("finance", 0.91), cfg)

    assert decision.spaces == ["finance"]
    assert decision.logged_slug == "finance"
    assert decision.fallback_used is False


# --- 10.2 Below threshold -------------------------------------------------------


def test_10_2_confidence_below_threshold_routes_to_all_spaces():
    cfg = _cfg(threshold=0.70)
    decision = decide_spaces(_classification("finance", 0.42), cfg)

    assert set(decision.spaces) == {"hr", "finance", "legal", "general"}
    assert decision.fallback_used is True
    assert decision.logged_slug == "general"


# --- 10.3 Confidence exactly at the threshold -----------------------------------


def test_10_3_confidence_exactly_at_threshold_uses_classified_space():
    """The boundary case most likely to be implemented as strict
    greater-than. The spec says meets-or-exceeds.
    """
    cfg = _cfg(threshold=0.70)
    decision = decide_spaces(_classification("hr", 0.70), cfg)

    assert decision.spaces == ["hr"]
    assert decision.fallback_used is False


# --- 10.4 Classified General, high confidence -----------------------------------


def test_10_4_classified_as_fallback_space_always_searches_all_spaces():
    cfg = _cfg(threshold=0.70, fallback="general")
    decision = decide_spaces(_classification("general", 0.99), cfg)

    assert set(decision.spaces) == {"hr", "finance", "legal", "general"}
    assert decision.fallback_used is True
    assert decision.logged_slug == "general"


# --- 10.5 Unknown slug -----------------------------------------------------------


def test_10_5_unknown_slug_routes_to_all_spaces_even_with_high_confidence():
    cfg = _cfg(threshold=0.70)
    decision = decide_spaces(_classification("not-a-real-space", 0.99), cfg)

    assert set(decision.spaces) == {"hr", "finance", "legal", "general"}
    assert decision.fallback_used is True
    assert decision.logged_slug == "general"


# --- 10.6 Classification failed ---------------------------------------------------


def test_10_6_classification_failed_routes_to_all_spaces():
    cfg = _cfg(threshold=0.70)
    decision = decide_spaces(_classification("general", 0.0, failed=True), cfg)

    assert set(decision.spaces) == {"hr", "finance", "legal", "general"}
    assert decision.fallback_used is True
    assert decision.logged_slug == "general"


# --- 10.7 Threshold raised at runtime ----------------------------------------------


def test_10_7_threshold_change_takes_effect_on_next_decision():
    classification = _classification("hr", 0.75)

    low_threshold_cfg = _cfg(threshold=0.70)
    assert decide_spaces(classification, low_threshold_cfg).fallback_used is False

    high_threshold_cfg = _cfg(threshold=0.90)
    decision = decide_spaces(classification, high_threshold_cfg)
    assert decision.fallback_used is True
    assert set(decision.spaces) == {"hr", "finance", "legal", "general"}
