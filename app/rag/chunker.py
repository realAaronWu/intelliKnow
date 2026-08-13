"""Structural chunker: turns an ordered list of `Block`s into `Chunk`s ready
for embedding.

Three structural rules drive the design:

- A table row is never split across chunks. A table under 1.5x the target
  size stays whole even though it is technically oversized; a larger one
  splits at row boundaries, never mid-row.
- Every chunk is prefixed with the heading path active where it starts,
  so the embedding carries context the raw sentence lacks and a citation
  can show where the chunk came from.
- Overlap is a prose-only, within-run concept: it never bridges a table,
  and it never bridges a heading boundary — bleeding the end of one
  section into the start of the next is worse than a short chunk.

Chunking is purely a function of `blocks` and `cfg`: no randomness, no
wall-clock, no I/O, so identical input always yields identical chunks.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import RAGConfig
from app.rag.blocks import Block

# A table is left whole even when it exceeds the target size, as long as
# it's under this multiple of it — splitting a small, mildly-oversized
# table buys little and costs a table's internal coherence.
_TABLE_WHOLE_RATIO = 1.5


@dataclass(frozen=True)
class Chunk:
    """One embedding-ready unit of chunked document content.

    `source_ref` carries every distinct source ref of the blocks that
    contributed to this chunk (comma-separated, in first-seen order) —
    a chunk packed from several paragraphs can span more than one.
    """

    ordinal: int
    text: str
    heading_path: list[str]
    source_ref: str
    char_count: int


@dataclass(frozen=True)
class _RawChunk:
    """A chunk's body and source ref, before the heading-path prefix and
    final char count are computed.
    """

    body: str
    source_ref: str


def chunk_blocks(blocks: list[Block], cfg: RAGConfig) -> list[Chunk]:
    """Chunk `blocks` in document order, using `cfg.chunk_chars` as the
    target chunk size and `cfg.chunk_overlap_chars` as the prose overlap.
    """
    chunks: list[Chunk] = []
    ordinal = 0

    for heading_path, section_blocks in _group_into_sections(blocks):
        prefix = " > ".join(heading_path)
        for raw in _chunk_section(section_blocks, cfg.chunk_chars, cfg.chunk_overlap_chars):
            text = f"{prefix}\n\n{raw.body}" if prefix else raw.body
            chunks.append(
                Chunk(
                    ordinal=ordinal,
                    text=text,
                    heading_path=list(heading_path),
                    source_ref=raw.source_ref,
                    char_count=len(text),
                )
            )
            ordinal += 1

    return chunks


def _group_into_sections(blocks: list[Block]) -> list[tuple[list[str], list[Block]]]:
    """Split `blocks` into sections: a heading path plus the run of
    paragraph/table blocks that follow it, up to the next heading.

    Every heading — even one that repeats the same level without changing
    its parent path — starts a fresh section, so overlap (applied within
    `_chunk_section`) can never bridge two sections.
    """
    sections: list[tuple[list[str], list[Block]]] = []
    path: list[str] = []
    current: list[Block] = []

    def flush() -> None:
        if current:
            sections.append(([p for p in path if p], list(current)))
            current.clear()

    for block in blocks:
        if block.kind == "heading":
            flush()
            level = block.heading_level
            assert level is not None  # enforced by Block.__post_init__
            if level > len(path):
                path.extend([""] * (level - len(path)))
            path[level - 1] = block.text
            del path[level:]
        else:
            current.append(block)

    flush()
    return sections


def _chunk_section(blocks: list[Block], target: int, overlap: int) -> list[_RawChunk]:
    """Chunk one section's blocks. Tables always get their own dedicated
    chunk(s) — never merged with surrounding prose — so a table's rows
    stay together and prose overlap never has to reason about a table in
    the middle of a run.
    """
    raw_chunks: list[_RawChunk] = []
    pending: list[tuple[str, str]] = []

    def flush_pending() -> None:
        raw_chunks.extend(_pack_prose(pending, target, overlap))
        pending.clear()

    for block in blocks:
        if block.kind == "table":
            flush_pending()
            for piece in _split_table_pieces(block.text, target):
                raw_chunks.append(_RawChunk(body=piece, source_ref=block.source_ref))
        else:
            pending.append((block.text, block.source_ref))

    flush_pending()
    return raw_chunks


def _pack_prose(
    pieces: list[tuple[str, str]], target: int, overlap: int
) -> list[_RawChunk]:
    """Concatenate `pieces` (text, source_ref) and slide a `target`-sized,
    `overlap`-overlapping window across the result.

    `RAGConfig` guarantees `overlap < target`, so `end - overlap` always
    advances past `start` and the loop terminates.
    """
    if not pieces:
        return []

    combined = ""
    spans: list[tuple[int, int, str]] = []
    for text, ref in pieces:
        if combined:
            combined += "\n\n"
        start = len(combined)
        combined += text
        spans.append((start, len(combined), ref))

    raw_chunks: list[_RawChunk] = []
    total = len(combined)
    start = 0
    while start < total:
        end = min(start + target, total)
        body = combined[start:end]
        raw_chunks.append(_RawChunk(body=body, source_ref=_refs_in_range(spans, start, end)))
        if end == total:
            break
        start = end - overlap

    return raw_chunks


def _refs_in_range(spans: list[tuple[int, int, str]], start: int, end: int) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for span_start, span_end, ref in spans:
        if span_start < end and span_end > start and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ", ".join(ordered)


def _split_table_pieces(text: str, target: int) -> list[str]:
    """Split a table's markdown into pieces that never break a row.

    A table under `_TABLE_WHOLE_RATIO * target` stays whole. A larger one
    splits by whole rows, repeating the header and separator in every
    piece so each remains a self-contained, readable table; a single row
    line is never divided even if it alone exceeds the target.
    """
    if len(text) < target * _TABLE_WHOLE_RATIO:
        return [text]

    lines = text.split("\n")
    if len(lines) < 3:
        return [text]
    header, separator, *rows = lines
    if not rows:
        return [text]

    header_cost = len(header) + len(separator) + 2
    pieces: list[str] = []
    current_rows: list[str] = []
    current_len = header_cost

    def flush() -> None:
        if current_rows:
            pieces.append("\n".join([header, separator, *current_rows]))

    for row in rows:
        row_cost = len(row) + 1
        if current_rows and current_len + row_cost > target:
            flush()
            current_rows = []
            current_len = header_cost
        current_rows.append(row)
        current_len += row_cost

    flush()
    return pieces if pieces else [text]
