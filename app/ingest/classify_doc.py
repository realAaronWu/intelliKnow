"""Intent space suggestion at ingest.

`spec: document-ingestion` § "Intent space assignment at ingest": every
uploaded document is offered to the LLM alongside the configured intent
spaces (name, description, and keywords) and a sample of its own content,
and the model's suggestion becomes the document's initial space — subject
to the admin overriding it later via reassignment (`app/ingest/lifecycle.py`).

A provider failure — or a response naming a slug that is not one of the
configured spaces — falls back to `cfg.orchestrator.fallback_space` rather
than raising, so a flaky or misconfigured LLM never blocks ingestion; per
spec, the admin can always reassign by hand afterward.

Falling back so ingestion completes is correct; falling back *silently* is
not — the fallback slug can equal a space the model would have genuinely
chosen anyway, so a caller cannot tell "the LLM judged this general" from
"the LLM call failed" just by looking at the slug. `suggest_intent` returns
an `IntentSuggestion` naming which one happened, logs a warning (with the
document identity and the error category) on a provider failure so it
shows up in service output, and the ingestion worker records
`assigned_by` on the document row so an operator can see it later too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import AppConfig
from app.providers.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

# "the document's first 2000 characters" — spec: document-ingestion §
# "Intent space assignment at ingest" and task 9's brief both name this
# figure explicitly.
_SAMPLE_CHARS = 2000

# `"model"` when the LLM's own suggestion was used; otherwise names why a
# fallback was used instead. Kept distinct from a bare bool so the reason
# survives as far as `scripts/ingest.py`'s per-document output.
AssignedBy = Literal["model", "provider_error", "invalid_slug"]


@dataclass(frozen=True)
class IntentSuggestion:
    """`suggest_intent`'s result: the assigned slug, and whether it came
    from the model or a fallback. Needed because the slug alone is
    ambiguous — the fallback space can equal what the model would have
    genuinely chosen, so `assigned_by` is the only thing that lets a
    caller tell "the model judged this general" from "the model call
    failed and defaulted to general" (see module docstring).
    """

    slug: str
    assigned_by: AssignedBy


_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {"slug": {"type": "string"}},
    "required": ["slug"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You assign an uploaded document to the single best-matching intent "
    "space, based on the space descriptions and keywords provided and a "
    "sample of the document's own content. Respond using the schema with "
    "the slug of the single best-matching space."
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
    """Suggest an intent space slug for a document whose content starts
    with `text`, using `llm` and the spaces configured in `cfg`.

    Falls back to `cfg.orchestrator.fallback_space` if the provider call
    fails, or if it succeeds but names a slug that is not one of the
    configured intent spaces — either way the fallback is visible in the
    returned `IntentSuggestion.assigned_by`, and a provider failure is
    additionally logged at warning level naming `doc_id` and the error's
    category, so a fallback never happens invisibly (see module docstring).
    `doc_id` is optional only so this can be called without one; every
    real caller (`app/ingest/worker.py`) passes it.
    """
    sample = text[:_SAMPLE_CHARS]
    user = (
        f"Intent spaces:\n{_spaces_block(cfg)}\n\n"
        f"Document content (first {_SAMPLE_CHARS} characters):\n{sample}"
    )

    try:
        result = llm.complete(system=_SYSTEM_PROMPT, user=user, schema=_SUGGEST_SCHEMA)
    except ProviderError as exc:
        logger.warning(
            "intent suggestion for document %s fell back to %r: provider "
            "error (category=%s): %s",
            doc_id,
            cfg.orchestrator.fallback_space,
            exc.category,
            exc,
        )
        return IntentSuggestion(cfg.orchestrator.fallback_space, "provider_error")

    slug = result.parsed.get("slug") if isinstance(result.parsed, dict) else None
    valid_slugs = {space.slug for space in cfg.intent_spaces}
    if slug not in valid_slugs:
        return IntentSuggestion(cfg.orchestrator.fallback_space, "invalid_slug")
    return IntentSuggestion(slug, "model")
