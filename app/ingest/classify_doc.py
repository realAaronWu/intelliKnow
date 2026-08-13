"""Strict document intent classification used by the ingestion path."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import AppConfig
from app.orchestrator.errors import ClassificationError
from app.providers.base import LLMProvider, LLMResult, ProviderError

logger = logging.getLogger(__name__)

# "the document's first 2000 characters" — spec: document-ingestion §
# "Intent space assignment at ingest" and task 9's brief both name this
# figure explicitly.
_SAMPLE_CHARS = 2000

AssignedBy = Literal["model"]


@dataclass(frozen=True)
class IntentSuggestion:
    """A validated, above-threshold model assignment."""

    slug: str
    confidence: float
    assigned_by: AssignedBy


_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        # Anthropic structured outputs reject numeric range keywords such as
        # `minimum` and `maximum`. The range and configured confidence
        # threshold are enforced below after parsing, for every provider.
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["slug", "confidence", "reasoning"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You assign an uploaded document to the single best-matching intent "
    "space, based on the space descriptions and keywords provided and a "
    "sample of the document's own content. Respond using the schema with "
    "the slug of the single best-matching space, confidence from 0 to 1, "
    "and a short reason. Do not guess when the document is ambiguous."
)

def _spaces_block(cfg: AppConfig) -> str:
    lines = []
    for space in cfg.intent_spaces:
        keywords = ", ".join(space.keywords) if space.keywords else "(none)"
        lines.append(
            f"- slug: {space.slug} | name: {space.name} | "
            f"description: {space.description} | keywords: {keywords}"
        )
    return "\n".join(lines)


def suggest_intent(
    text: str, cfg: AppConfig, llm: LLMProvider, *, doc_id: int | None = None
) -> IntentSuggestion:
    """Return an above-threshold assignment or raise a retryable error."""
    sample = text[:_SAMPLE_CHARS]
    user = (
        f"Intent spaces:\n{_spaces_block(cfg)}\n\n"
        f"Document content (first {_SAMPLE_CHARS} characters):\n{sample}"
    )

    try:
        result = llm.complete(system=_SYSTEM_PROMPT, user=user, schema=_SUGGEST_SCHEMA)
    except ProviderError as exc:
        message = (
            "Document classification is unavailable "
            f"({exc.category}). The document was not indexed; please retry."
        )
        logger.error("intent classification failed for document %s: %s", doc_id, exc)
        raise ClassificationError(message) from exc

    parsed = result.parsed if isinstance(result.parsed, dict) else {}
    slug = parsed.get("slug")
    valid_slugs = {space.slug for space in cfg.intent_spaces}
    if slug not in valid_slugs:
        raise ClassificationError(
            "The document classifier returned an invalid intent. "
            "The document was not indexed; please retry."
        )
    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ClassificationError(
            "The document classifier returned invalid confidence. "
            "The document was not indexed; please retry."
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise ClassificationError(
            "The document classifier returned invalid confidence outside 0 to 1. "
            "The document was not indexed; please retry."
        )
    if confidence < cfg.orchestrator.confidence_threshold:
        raise ClassificationError(
            f"Document classification confidence {confidence:.0%} is below the required "
            f"{cfg.orchestrator.confidence_threshold:.0%}. The document was not indexed; "
            "review its content or intent definitions, then retry."
        )
    return IntentSuggestion(slug, confidence, "model")


def preflight_classifier(cfg: AppConfig, llm: LLMProvider) -> LLMResult:
    """Exercise the production schema so classifier failures precede a write."""
    probe_slug = cfg.intent_spaces[0].slug
    try:
        result = llm.complete(
            system=(
                "This is an availability check for an intent classifier. "
                "Return exactly the requested slug, confidence 1.0, and a short "
                "reason using the schema."
            ),
            user=f"Return this slug exactly: {probe_slug}",
            schema=_SUGGEST_SCHEMA,
        )
    except ProviderError as exc:
        raise ClassificationError(
            f"Classification service is unavailable ({exc.category}); nothing was saved. "
            "Please retry when the model is available."
        ) from exc
    parsed = result.parsed if isinstance(result.parsed, dict) else {}
    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ClassificationError(
            "Classification service failed its response check; nothing was saved. "
            "Please retry."
        ) from exc
    if parsed.get("slug") != probe_slug or not 0.0 <= confidence <= 1.0:
        raise ClassificationError(
            "Classification service failed its response check; nothing was saved. "
            "Please retry."
        )
    return result
