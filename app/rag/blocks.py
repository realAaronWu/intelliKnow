"""Block model and loader interface shared by every document loader.

A `Block` is the smallest unit of content a loader extracts from a
document: a heading, a paragraph, or a table. Loaders return them in
document order, and each block carries the provenance string that later
appears verbatim in citations (`p. 4`, `¶ 12`, `Sheet1!A1:F20`).

A table block carries its grid **structurally**, in `rows`, and renders
to markdown through `render_table_markdown` — the one renderer in the
codebase. Nothing downstream ever parses that markdown back into rows.
That direction of travel is deliberate and load-bearing: pdfplumber and
python-docx both return cells containing newlines whenever a cell wraps
or holds more than one paragraph, so a markdown round-trip splits one
real row into several ragged ones. That silently made clean tables look
ragged (firing an LLM call per table, defeating the "clean tables are not
sent to the model" cost guard) and made oversized tables split mid-row,
violating "a table row is never split". Structure that only ever flows
rows -> markdown cannot reproduce either failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

BlockKind = Literal["heading", "paragraph", "table"]

_WHITESPACE = re.compile(r"\s+")


def normalize_cell(value: object) -> str:
    """Normalize one extracted cell to a single-line string.

    Any internal whitespace run — crucially including the newlines a
    wrapped or multi-paragraph cell arrives with — collapses to a single
    space, so a cell can never span lines once rendered. `None` (an empty
    cell, as pdfplumber and openpyxl report it) becomes "". Idempotent, so
    normalizing an already-normalized grid is a no-op.

    Deliberately *not* where markdown escaping happens: `rows` holds cell
    values, and a cell containing a pipe is a fact about the document, not
    about how it renders.
    """
    if value is None:
        return ""
    return _WHITESPACE.sub(" ", str(value)).strip()


def _render_cell(value: object) -> str:
    """One normalized cell, escaped for a markdown table: a literal pipe
    would otherwise manufacture a phantom column.
    """
    return normalize_cell(value).replace("|", r"\|")


def render_table_markdown(rows: list[list[str]]) -> str:
    """Render a table grid as a markdown table: one line per row, header
    row first, then the `| --- |` separator, then the body.

    The sole table renderer in the codebase — loaders, AI table repair,
    and the chunker's table splitter all go through it, so a table's text
    representation is defined in exactly one place.
    """
    if not rows:
        return ""
    header, *body = rows
    header_cells = [_render_cell(cell) for cell in header]
    lines = [
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join("---" for _ in header_cells) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(_render_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


@dataclass(frozen=True)
class Block:
    """One ordered unit of extracted document content.

    `heading_level` is set if and only if `kind == "heading"`, and `rows`
    if and only if `kind == "table"` — both enforced here so a malformed
    loader fails immediately rather than producing a block whose level is
    meaningless, or a table whose structure was silently lost to its own
    rendering. Build table blocks with `Block.table`, which keeps `text`
    and `rows` two views of the same grid rather than two sources of
    truth.
    """

    kind: BlockKind
    text: str
    source_ref: str
    heading_level: int | None = None
    rows: list[list[str]] | None = None

    def __post_init__(self) -> None:
        if self.kind == "heading" and self.heading_level is None:
            raise ValueError("heading blocks must carry a heading_level")
        if self.kind != "heading" and self.heading_level is not None:
            raise ValueError(
                f"heading_level is only valid on heading blocks, not {self.kind!r}"
            )
        if self.kind == "table" and self.rows is None:
            raise ValueError(
                "table blocks must carry their structural rows; build them "
                "with Block.table(rows=..., source_ref=...)"
            )
        if self.kind != "table" and self.rows is not None:
            raise ValueError(f"rows is only valid on table blocks, not {self.kind!r}")

    @classmethod
    def table(cls, rows: list[list[object]], source_ref: str) -> "Block":
        """Build a table block from an extracted grid.

        Cells are normalized once, here, so `rows` and `text` can never
        disagree and no later stage has to re-clean them.
        """
        normalized = [[normalize_cell(cell) for cell in row] for row in rows]
        return cls(
            kind="table",
            text=render_table_markdown(normalized),
            source_ref=source_ref,
            rows=normalized,
        )


@runtime_checkable
class DocumentLoader(Protocol):
    """Extracts an ordered list of Blocks from a document file."""

    def load(self, path: Path) -> list[Block]: ...


class LoaderError(Exception):
    """Raised when a document cannot be loaded into blocks.

    Loader implementations use this to distinguish recoverable, described
    failures (e.g. a scanned PDF with no extractable text, a corrupt file)
    from unexpected exceptions — callers can rely on catching just this
    type to detect "this document could not be ingested."
    """
