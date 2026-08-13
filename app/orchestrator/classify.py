"""Intent classification: centroid first, LLM escalation only when unsure.

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

A provider failure or timeout, and an LLM response naming a slug that is
not one of the configured spaces, are both treated as "no usable
classification" rather than propagated: `failed=True` on the former,
confidence forced below any real threshold on the latter, either way with
the anomaly logged rather than raised — `spec: query-orchestration` §
"Classification failure falls back rather than failing" says a broken
classifier must never be a broken answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import AppConfig
from app.orchestrator.centroids import CentroidIndex
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
        logger.warning(
            "classification escalation failed, falling back to %r: "
            "provider error (category=%s): %s",
            cfg.orchestrator.fallback_space,
            exc.category,
            exc,
        )
        return Classification(
            intent_slug=cfg.orchestrator.fallback_space,
            confidence=0.0,
            classified_by="llm",
            reasoning=None,
            failed=True,
        )

    parsed = result.parsed if isinstance(result.parsed, dict) else {}
    slug = parsed.get("slug")
    reasoning = parsed.get("reasoning")

    valid_slugs = {space.slug for space in cfg.intent_spaces}
    if slug not in valid_slugs:
        logger.warning(
            "classification escalation returned an unrecognized slug %r; "
            "treating it as below-threshold",
            slug,
        )
        return Classification(
            intent_slug=slug or cfg.orchestrator.fallback_space,
            confidence=0.0,
            classified_by="llm",
            reasoning=reasoning,
            failed=False,
        )

    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0

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
    otherwise the centroid result is returned as-is, below threshold, and
    `app/orchestrator/route.py::decide_spaces` is what turns that into a
    fallback-space routing decision — this function only classifies, it
    never decides which spaces get searched.
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
        return Classification(
            intent_slug=slug,
            confidence=confidence,
            classified_by="centroid",
            reasoning=None,
            failed=False,
        )

    return _escalate(question, cfg, llm)
