"""Turn a validated classification into one explicit retrieval space."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import AppConfig
from app.orchestrator.classify import Classification
from app.orchestrator.errors import ClassificationError


@dataclass(frozen=True)
class RoutingDecision:
    spaces: list[str]
    logged_slug: str
    fallback_used: bool


def decide_spaces(classification: Classification, cfg: AppConfig) -> RoutingDecision:
    """Return one space or reject any uncertain/invalid classification."""
    valid_slugs = {space.slug for space in cfg.intent_spaces}

    if classification.failed:
        raise ClassificationError("Intent classification failed. Please retry.")
    if classification.intent_slug not in valid_slugs:
        raise ClassificationError(
            f"Intent classification returned an invalid intent "
            f"{classification.intent_slug!r}. Please retry."
        )
    if classification.confidence < cfg.orchestrator.confidence_threshold:
        raise ClassificationError(
            f"Intent classification confidence {classification.confidence:.0%} is below "
            f"the required {cfg.orchestrator.confidence_threshold:.0%}. Please clarify "
            "the question or retry."
        )
    return RoutingDecision(
        spaces=[classification.intent_slug],
        logged_slug=classification.intent_slug,
        fallback_used=False,
    )
