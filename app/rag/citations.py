"""Citation verification — the last line of defense against a fluent,
wrongly-cited answer.

`verify_citations` never calls a model. It parses the bracketed markers
(`[1]`, `[2]`, ...) `app/rag/generate.py` asked the model to cite with,
and resolves each against the sources `app/rag/context.py` actually
supplied. A marker that does not resolve — the model citing `[7]` when
only `[1]`-`[3]` were ever in context, whether from hallucination or
truncation — is stripped from the delivered answer and contributes no
citation, rather than being trusted at face value. This is the single
check that stops "a confident answer citing a document that was never
retrieved," the main failure mode of a small RAG system, and it is nearly
free: no extra model call, just a regex and a dict lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.context import ContextBundle

_MARKER_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Citation:
    document_id: int
    document_title: str
    source_ref: str | None


def verify_citations(answer: str, bundle: ContextBundle) -> tuple[str, list[Citation]]:
    """Resolve every marker in `answer` against `bundle.sources`.

    Returns `(cleaned_answer, citations)`:
    - `cleaned_answer` is `answer` with every unresolvable marker removed
      (resolvable markers are left in place, verbatim).
    - `citations` lists each *document* that contributed a resolvable
      marker, once, in the order it was first cited in `answer` — a
      second marker pointing at an already-cited document adds nothing.
    """
    sources_by_marker = {source.marker: source for source in bundle.sources}

    def _strip_unresolvable(match: re.Match[str]) -> str:
        marker = match.group(0)
        return marker if marker in sources_by_marker else ""

    cleaned = _MARKER_RE.sub(_strip_unresolvable, answer)
    # Only tidy the artifact of removal itself (a run of literal spaces
    # left where a marker used to sit) — never touch newlines or other
    # whitespace the model wrote deliberately.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)

    citations: list[Citation] = []
    seen_document_ids: set[int] = set()
    for match in _MARKER_RE.finditer(answer):
        source = sources_by_marker.get(match.group(0))
        if source is None or source.document_id in seen_document_ids:
            continue
        seen_document_ids.add(source.document_id)
        citations.append(
            Citation(
                document_id=source.document_id,
                document_title=source.document_title,
                source_ref=source.source_ref,
            )
        )

    return cleaned, citations
