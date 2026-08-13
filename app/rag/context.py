"""Context builder — turns ranked chunks into a prompt-ready bundle.

`build_context` is the seam between retrieval (which scores chunks so the
*best* ones survive) and generation (which reads them as a document, not a
leaderboard). Two properties matter more than anything else here:

- **Presentation order is document-then-position, never score.** A model
  handed chunks in rank order sees a paragraph from the middle of a
  document before the one that introduces it; handed them back in the
  order they were written, it reads the way a person would. Rank still
  decides *which* chunks survive the near-duplicate and budget passes
  below — it is simply discarded once that's settled, before the prompt
  block is built.
- **Provenance survives verbatim.** `heading_path` and `source_ref` come
  straight off the `chunks` row with no reformatting, because they are
  exactly what a citation shows the reader later — see
  `app/rag/retrieve/rerank.py` and increment 03's chunker for where they
  first get set.

Near-duplicate detection compares `difflib.SequenceMatcher` ratios on
whitespace-normalized text, scoped to chunks from the *same* document —
the shape overlap-windowed chunking produces (two neighbouring chunks that
share most of their text because the chunker's overlap window straddled
them), not a general cross-document similarity check.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.config import RAGConfig
from app.db import chunks as chunks_table
from app.db import documents as documents_table
from app.rag.retrieve.rerank import RankedHit

# Ratio threshold (0-1, `difflib.SequenceMatcher.ratio()`) above which two
# same-document chunks are treated as the same content rather than two
# genuinely different passages that merely share vocabulary.
_DUPLICATE_RATIO_THRESHOLD = 0.8


@dataclass(frozen=True)
class Source:
    """One chunk that made it into the assembled context, tagged with
    everything a citation needs to point back at where it came from.
    """

    marker: str
    chunk_id: int
    document_id: int
    document_title: str
    source_ref: str | None
    heading_path: str | None
    text: str


@dataclass(frozen=True)
class ContextBundle:
    sources: list[Source]
    prompt_block: str


@dataclass(frozen=True)
class _Row:
    chunk_id: int
    document_id: int
    document_title: str
    source_ref: str | None
    heading_path: str | None
    ordinal: int
    text: str


def _load_rows(engine: Engine, chunk_ids: list[int]) -> dict[int, _Row]:
    if not chunk_ids:
        return {}
    with engine.connect() as conn:
        result = conn.execute(
            select(
                chunks_table.c.id,
                chunks_table.c.document_id,
                chunks_table.c.ordinal,
                chunks_table.c.text,
                chunks_table.c.heading_path,
                chunks_table.c.source_ref,
                documents_table.c.filename,
            )
            .select_from(
                chunks_table.join(
                    documents_table,
                    chunks_table.c.document_id == documents_table.c.id,
                )
            )
            .where(chunks_table.c.id.in_(chunk_ids))
        ).all()
    return {
        row.id: _Row(
            chunk_id=row.id,
            document_id=row.document_id,
            document_title=row.filename,
            source_ref=row.source_ref,
            heading_path=row.heading_path,
            ordinal=row.ordinal,
            text=row.text,
        )
        for row in result
    }


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def _is_near_duplicate(a: str, b: str) -> bool:
    ratio = difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()
    return ratio >= _DUPLICATE_RATIO_THRESHOLD


def _select_chunks(hits: list[RankedHit], rows_by_id: dict[int, _Row], budget: int) -> list[_Row]:
    """Walk `hits` best-first, dropping near-duplicates and then truncating
    at the budget — the lowest-ranked survivor and everything below it.
    """
    accepted: list[_Row] = []
    accepted_by_doc: dict[int, list[_Row]] = {}
    total_chars = 0

    for hit in hits:
        row = rows_by_id.get(hit.chunk_id)
        if row is None:
            # Chunk retrieved earlier no longer resolves (e.g. deleted
            # between retrieval and context assembly) — skip, don't fail
            # the whole answer over one stale id.
            continue

        same_doc = accepted_by_doc.get(row.document_id, [])
        if any(_is_near_duplicate(row.text, other.text) for other in same_doc):
            continue

        if accepted and total_chars + len(row.text) > budget:
            # Everything from here on is lower-ranked than what's already
            # in; the budget is spent, so the rest is dropped rather than
            # cherry-picking whatever happens to fit further down the list.
            break

        accepted.append(row)
        accepted_by_doc.setdefault(row.document_id, []).append(row)
        total_chars += len(row.text)

    return accepted


def _render_block(marker: str, row: _Row) -> str:
    header = f"{marker} {row.document_title}"
    if row.heading_path:
        header += f" > {row.heading_path}"
    if row.source_ref:
        header += f" ({row.source_ref})"
    return f"{header}\n```\n{row.text}\n```"


def build_context(hits: list[RankedHit], engine: Engine, cfg: RAGConfig) -> ContextBundle:
    """Assemble a `ContextBundle` from ranked hits.

    `hits` is assumed best-first (as `Reranker.rerank` / `passes_gate`
    produce it) — that order drives near-duplicate preference and the
    budget cutoff, but never the final presentation order, which is
    always document then position.
    """
    if not hits:
        return ContextBundle(sources=[], prompt_block="")

    rows_by_id = _load_rows(engine, [hit.chunk_id for hit in hits])
    accepted = _select_chunks(hits, rows_by_id, cfg.max_context_chars)
    accepted.sort(key=lambda row: (row.document_id, row.ordinal))

    sources: list[Source] = []
    blocks: list[str] = []
    for i, row in enumerate(accepted, start=1):
        marker = f"[{i}]"
        sources.append(
            Source(
                marker=marker,
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                document_title=row.document_title,
                source_ref=row.source_ref,
                heading_path=row.heading_path,
                text=row.text,
            )
        )
        blocks.append(_render_block(marker, row))

    return ContextBundle(sources=sources, prompt_block="\n\n".join(blocks))
