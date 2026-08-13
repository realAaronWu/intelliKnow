"""Block model and loader interface shared by every document loader.

A `Block` is the smallest unit of content a loader extracts from a
document: a heading, a paragraph, or a table. Loaders return them in
document order, and each block carries the provenance string that later
appears verbatim in citations (`p. 4`, `¶ 12`, `Sheet1!A1:F20`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

BlockKind = Literal["heading", "paragraph", "table"]


@dataclass(frozen=True)
class Block:
    """One ordered unit of extracted document content.

    `heading_level` is set if and only if `kind == "heading"` — enforced
    here so a malformed loader fails immediately rather than producing a
    block whose level is meaningless or silently missing.
    """

    kind: BlockKind
    text: str
    source_ref: str
    heading_level: int | None = None

    def __post_init__(self) -> None:
        if self.kind == "heading" and self.heading_level is None:
            raise ValueError("heading blocks must carry a heading_level")
        if self.kind != "heading" and self.heading_level is not None:
            raise ValueError(
                f"heading_level is only valid on heading blocks, not {self.kind!r}"
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
