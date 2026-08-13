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
"""

from __future__ import annotations

from app.config import AppConfig
from app.providers.base import LLMProvider, ProviderError

# "the document's first 2000 characters" — spec: document-ingestion §
# "Intent space assignment at ingest" and task 9's brief both name this
# figure explicitly.
_SAMPLE_CHARS = 2000

_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {"slug": {"type": "string"}},
    "required": ["slug"],
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


def suggest_intent(text: str, cfg: AppConfig, llm: LLMProvider) -> str:
    """Suggest an intent space slug for a document whose content starts
    with `text`, using `llm` and the spaces configured in `cfg`.

    Falls back to `cfg.orchestrator.fallback_space` if the provider call
    fails, or if it succeeds but names a slug that is not one of the
    configured intent spaces.
    """
    sample = text[:_SAMPLE_CHARS]
    user = (
        f"Intent spaces:\n{_spaces_block(cfg)}\n\n"
        f"Document content (first {_SAMPLE_CHARS} characters):\n{sample}"
    )

    try:
        result = llm.complete(system=_SYSTEM_PROMPT, user=user, schema=_SUGGEST_SCHEMA)
    except ProviderError:
        return cfg.orchestrator.fallback_space

    slug = result.parsed.get("slug") if isinstance(result.parsed, dict) else None
    valid_slugs = {space.slug for space in cfg.intent_spaces}
    if slug not in valid_slugs:
        return cfg.orchestrator.fallback_space
    return slug
