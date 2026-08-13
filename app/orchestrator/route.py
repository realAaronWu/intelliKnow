"""Routing decision — turns a `Classification` into an explicit space list.

`decide_spaces` is the only place `spec: query-orchestration` §
"Confidence threshold enforcement" and § "Fallback space searches all
spaces" are actually enforced. Retrieval never sees a confidence value or
a threshold — it only ever receives the `spaces` list this produces
(`spec: query-orchestration` § "Routing hand-off to retrieval"), so a
retrieval bug can never accidentally make a routing decision.

Every fallback-triggering condition below is checked independently of the
others, rather than folded into "confidence < threshold": a test can (and
does, in `tests/test_routing.py`) construct a `Classification` naming an
unrecognized slug with a *high* confidence, or the fallback space's own
slug with high confidence, and both still have to route to every space —
`classify()` would never itself produce either shape on the centroid-only
path, but this function does not get to assume its only caller is
`classify()`.

The exactly-at-threshold case (`confidence >= threshold`, not `>`) is
deliberate — the spec says meets-or-exceeds, and strict greater-than is
the likely bug (`test_10_3_...`).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import AppConfig
from app.orchestrator.classify import Classification


@dataclass(frozen=True)
class RoutingDecision:
    spaces: list[str]
    logged_slug: str
    fallback_used: bool


def _fallback_decision(cfg: AppConfig) -> RoutingDecision:
    all_slugs = [space.slug for space in cfg.intent_spaces]
    return RoutingDecision(spaces=all_slugs, logged_slug=cfg.orchestrator.fallback_space, fallback_used=True)


def decide_spaces(classification: Classification, cfg: AppConfig) -> RoutingDecision:
    """Decide which intent spaces retrieval should search.

    - Classification failed -> every space, logged against the fallback.
    - Slug names no configured space -> every space, logged against the
      fallback (an anomaly `classify()` already logged; this is the
      routing consequence of it).
    - Slug is the fallback space itself, any confidence -> every space.
    - Confidence meets or exceeds the threshold -> that space alone.
    - Otherwise (confidence below threshold) -> every space.
    """
    valid_slugs = {space.slug for space in cfg.intent_spaces}

    if classification.failed:
        return _fallback_decision(cfg)
    if classification.intent_slug not in valid_slugs:
        return _fallback_decision(cfg)
    if classification.intent_slug == cfg.orchestrator.fallback_space:
        return _fallback_decision(cfg)
    if classification.confidence >= cfg.orchestrator.confidence_threshold:
        return RoutingDecision(
            spaces=[classification.intent_slug],
            logged_slug=classification.intent_slug,
            fallback_used=False,
        )
    return _fallback_decision(cfg)
