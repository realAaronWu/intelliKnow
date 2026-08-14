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
    """Route accepted classifications or send uncertainty to General."""
    valid_slugs = {space.slug for space in cfg.intent_spaces}

    if classification.failed:
        raise ClassificationError("Intent classification failed. Please retry.")
    if classification.intent_slug not in valid_slugs:
        raise ClassificationError(
            f"Intent classification returned an invalid intent "
            f"{classification.intent_slug!r}. Please retry."
        )
    if classification.confidence < cfg.orchestrator.confidence_threshold:
        return RoutingDecision(
            spaces=[cfg.orchestrator.fallback_space],
            logged_slug=cfg.orchestrator.fallback_space,
            fallback_used=True,
        )
    return RoutingDecision(
        spaces=[classification.intent_slug],
        logged_slug=classification.intent_slug,
        fallback_used=False,
    )
