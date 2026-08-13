"""Intent classification: centroid first, strict LLM escalation when unsure.

`classify()` never embeds anything itself — `query_vector` is the same
vector retrieval will use for dense search, computed exactly once by the
caller (`app/orchestrator/pipeline.py`), per `spec: query-orchestration`
§ "Query embedding is reused". Centroid scoring against that vector is
free (a handful of dot products), so the common path — confidence at or
above the threshold — never touches the network and never calls an LLM
at all (`test_9_1_...`).

Escalation is the minority path: it fires only when centroid confidence
is below `cfg.orchestrator.confidence_threshold` *and*
`cfg.orchestrator.escalate_to_llm` is true. It costs exactly one
structured-output call, using whichever `LLMProvider` the caller passes
in — production wires the classify-role provider
(`build_llm_provider(cfg, role="classify")`) there, so `model_classify`
is what actually answers, never `model_generate`.

The escalation prompt is rebuilt from `cfg` on every call rather than
cached, so a `ConfigService.update()` that edits a space's keywords
changes the very next escalation prompt with no restart — the same
"live config, no restart" property `app/ingest/classify_doc.py::
suggest_intent` already has for document classification.

Provider failures, malformed responses, and below-threshold results raise
`ClassificationError`. Retrieval must never broaden its search merely
because the classifier is unavailable or unsure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import AppConfig
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.errors import ClassificationError
from app.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["slug", "confidence", "reasoning"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You classify a user's question into the single best-matching intent "
    "space, using the space names, descriptions, and keywords below. "
    "Respond using the schema with the slug of the best-matching space, "
    "your confidence in the range 0 to 1, and a short reasoning for the "
    "choice."
)


@dataclass(frozen=True)
class Classification:
    intent_slug: str
    confidence: float
    classified_by: Literal["centroid", "llm"]
    reasoning: str | None
    failed: bool


def _spaces_block(cfg: AppConfig) -> str:
    lines = []
    for space in cfg.intent_spaces:
        keywords = ", ".join(space.keywords) if space.keywords else "(none)"
        lines.append(
            f"- slug: {space.slug} | name: {space.name} | "
            f"description: {space.description} | keywords: {keywords}"
        )
    return "\n".join(lines)


def _escalate(question: str, cfg: AppConfig, llm: LLMProvider) -> Classification:
    user = f"Question: {question}\n\nIntent spaces:\n{_spaces_block(cfg)}"

    try:
        result = llm.complete(system=_SYSTEM_PROMPT, user=user, schema=_CLASSIFY_SCHEMA)
    except ProviderError as exc:
        logger.error("classification escalation failed (%s): %s", exc.category, exc)
        raise ClassificationError(
            f"Intent classification is unavailable ({exc.category}). Please retry."
        ) from exc

    parsed = result.parsed if isinstance(result.parsed, dict) else {}
    slug = parsed.get("slug")
    reasoning = parsed.get("reasoning")

    valid_slugs = {space.slug for space in cfg.intent_spaces}
    if slug not in valid_slugs:
        raise ClassificationError(
            f"Intent classification returned an invalid intent {slug!r}. Please retry."
        )

    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ClassificationError(
            "Intent classification returned invalid confidence. Please retry."
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise ClassificationError(
            "Intent classification returned confidence outside 0 to 1. Please retry."
        )
    if confidence < cfg.orchestrator.confidence_threshold:
        raise ClassificationError(
            f"Intent classification confidence {confidence:.0%} is below the required "
            f"{cfg.orchestrator.confidence_threshold:.0%}. Please clarify the question "
            "or retry."
        )

    return Classification(
        intent_slug=slug,
        confidence=confidence,
        classified_by="llm",
        reasoning=reasoning,
        failed=False,
    )


def classify(
    question: str,
    query_vector: list[float],
    cfg: AppConfig,
    centroids: CentroidIndex,
    llm: LLMProvider,
) -> Classification:
    """Classify `question`, given its already-computed `query_vector`.

    Centroid confidence at or above `cfg.orchestrator.confidence_threshold`
    (meets-or-exceeds, not strictly greater — matching the threshold
    enforcement in `app/orchestrator/route.py`) returns immediately with
    `classified_by="centroid"` and no LLM call. Below it, this escalates
    to `llm` only when `cfg.orchestrator.escalate_to_llm` is true;
    otherwise classification fails closed because no accepted routing
    decision can be made without guessing.
    """
    slug, confidence = centroids.top(query_vector)
    threshold = cfg.orchestrator.confidence_threshold

    if confidence >= threshold:
        return Classification(
            intent_slug=slug,
            confidence=confidence,
            classified_by="centroid",
            reasoning=None,
            failed=False,
        )

    if not cfg.orchestrator.escalate_to_llm:
        raise ClassificationError(
            f"Intent classification confidence {confidence:.0%} is below the required "
            f"{threshold:.0%}, and LLM escalation is disabled. Please clarify the "
            "question or enable escalation."
        )

    return _escalate(question, cfg, llm)
