"""Channel formatting — the hard guarantee, not the best-effort one.

`app/rag/generate.py` asks the model to write within the channel's length
budget by putting it in the prompt, but nothing stops a model from
ignoring that. `format_for_channel` is what actually enforces it: the
returned string is always within `profile.max_chars`, full stop, because a
hard protocol limit (Telegram silently rejects an over-length
`sendMessage`) must not depend on a model's cooperation.

Two independent passes:

- **Escaping.** Reserved characters in the destination markup are escaped
  so the message *renders* instead of failing outright (an unescaped `.`
  or `(` in Telegram MarkdownV2 is a parse error, not a typo) or
  rendering mangled (an unescaped `<` in Teams' HTML).
- **Truncation.** If the escaped answer plus its citations still doesn't
  fit, the answer is cut at the last word boundary that fits and a single
  visible marker (`…`) is appended — chosen because it needs no escaping
  in any of the supported markups, so appending it can never itself push
  the result over the limit.

Citations render citation-appropriate to the channel: Teams (HTML,
`supports_lists`) gets a real `<ul><li>` list; everything else gets a
plain escaped numbered list.
"""

from __future__ import annotations

import html as _html
from typing import Callable

from app.rag.citations import Citation
from app.rag.generate import ChannelProfile, Markup

_TRUNCATION_MARKER = "…"

_MARKDOWNV2_SPECIALS = set("_*[]()~`>#+-=|{}.!")


def _escape_markdownv2(text: str) -> str:
    return "".join(f"\\{ch}" if ch in _MARKDOWNV2_SPECIALS else ch for ch in text)


def _escape_html(text: str) -> str:
    return _html.escape(text, quote=False)


def _escape_plain(text: str) -> str:
    return text


_ESCAPERS: dict[Markup, Callable[[str], str]] = {
    "markdownv2": _escape_markdownv2,
    "html": _escape_html,
    "plain": _escape_plain,
}


def _escape(text: str, markup: Markup) -> str:
    return _ESCAPERS[markup](text)


def _citation_label(citation: Citation) -> str:
    if citation.source_ref:
        return f"{citation.document_title} ({citation.source_ref})"
    return citation.document_title


def _render_citations(citations: list[Citation], profile: ChannelProfile) -> str:
    if not citations:
        return ""

    labels = [_citation_label(c) for c in citations]

    if profile.supports_lists and profile.markup == "html":
        items = "".join(f"<li>{_escape_html(label)}</li>" for label in labels)
        return f"Sources:<ul>{items}</ul>"

    escape = _ESCAPERS[profile.markup]
    if profile.markup == "markdownv2":
        # The literal "N." of a numbered line is itself reserved-character
        # punctuation in MarkdownV2 (Telegram has no native ordered-list
        # syntax), so it is escaped exactly like any other period.
        lines = [f"{i}\\. {escape(label)}" for i, label in enumerate(labels, start=1)]
    else:
        lines = [f"{i}. {escape(label)}" for i, label in enumerate(labels, start=1)]
    return "Sources:\n" + "\n".join(lines)


def _render_compact_source(citation: Citation, profile: ChannelProfile) -> str:
    return f"Source: {_escape(citation.document_title, profile.markup)}"


def _truncate_at_word_boundary(text: str, limit: int) -> str:
    """Cut `text` to at most `limit` characters, preferring the last word
    boundary within budget, and append the truncation marker.

    Guaranteed `len(result) <= limit` for any `limit >= 0`: the pre-marker
    slice is capped at `limit - len(marker)` before the marker is added,
    never after.
    """
    if len(text) <= limit:
        return text

    marker = _TRUNCATION_MARKER
    budget = max(limit - len(marker), 0)
    truncated = text[:budget]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    truncated = truncated.rstrip()
    return (truncated + marker)[:limit]


def format_for_channel(answer: str, citations: list[Citation], profile: ChannelProfile) -> str:
    """Render `answer` (with its verified `citations`) for delivery on
    `profile`'s channel.

    Escapes reserved markup characters, appends a citations section in a
    form appropriate to the channel, and — the one non-negotiable part —
    guarantees the result never exceeds `profile.max_chars`.
    """
    escaped_answer = _escape(answer, profile.markup)
    citations_block = _render_citations(citations, profile)

    combined = f"{escaped_answer}\n\n{citations_block}" if citations_block else escaped_answer
    if len(combined) <= profile.max_chars:
        return combined

    separator_cost = 2  # "\n\n"
    if citations_block and len(citations_block) + separator_cost < profile.max_chars:
        available = profile.max_chars - len(citations_block) - separator_cost
        truncated_answer = _truncate_at_word_boundary(escaped_answer, available)
        combined = f"{truncated_answer}\n\n{citations_block}"
        if len(combined) <= profile.max_chars:
            return combined

    if citations:
        compact_source = _render_compact_source(citations[0], profile)
        if len(compact_source) <= profile.max_chars:
            available = profile.max_chars - len(compact_source) - separator_cost
            if available > 0:
                truncated_answer = _truncate_at_word_boundary(escaped_answer, available)
                return f"{truncated_answer}\n\n{compact_source}"
            return compact_source

    return _truncate_at_word_boundary(escaped_answer, profile.max_chars)
