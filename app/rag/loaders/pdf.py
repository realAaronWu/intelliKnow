"""PDF loader: text and headings via pdfplumber font-size heuristics, tables
via pdfplumber's ruled-line table detection.

Two failure modes are deliberately distinguishable: a PDF that parses fine
but yields no extractable text (a scanned, image-only document) raises a
`LoaderError` naming that scanned documents are unsupported; a PDF that
cannot be parsed at all (corrupt/truncated bytes) raises a differently
worded `LoaderError`. Callers that want to tell these apart can inspect
the message; callers that don't care can just catch `LoaderError`.

pypdf exposes no font metadata, so heading detection is built entirely on
pdfplumber's per-character font sizes: a per-document body-text baseline is
the dominant (most common, by character count) size across the document,
and any line whose dominant size is meaningfully above that baseline is a
heading. Heading levels are inferred from the ordering of the distinct
heading sizes found — the largest becomes level 1, the next distinct size
level 2, and so on.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from app.rag.blocks import Block, LoaderError

# A line's dominant character size must be at least this many times the
# document's body-text baseline to count as a heading. 1.2 comfortably
# separates reportlab's 18pt Title/Heading1 from 10pt BodyText (1.8x) while
# not misfiring on small, incidental size jitter within body text.
_HEADING_SIZE_RATIO = 1.2


@dataclass(frozen=True)
class _Line:
    text: str
    size: float


class PdfLoader:
    """Loads a `.pdf` file into an ordered list of `Block`s."""

    def load(self, path: Path) -> list[Block]:
        path = Path(path)

        try:
            page_lines, page_tables = _extract_pages(path)
        except Exception as exc:
            raise LoaderError(f"could not parse {path.name}: {exc}") from exc

        heading_levels = _heading_level_map(page_lines)

        blocks: list[Block] = []
        for page_num, (lines, tables) in enumerate(zip(page_lines, page_tables), start=1):
            ref = f"p. {page_num}"

            for line in lines:
                level = heading_levels.get(line.size)
                if level is not None:
                    blocks.append(
                        Block(
                            kind="heading",
                            text=line.text,
                            source_ref=ref,
                            heading_level=level,
                        )
                    )
                else:
                    blocks.append(Block(kind="paragraph", text=line.text, source_ref=ref))

            for table in tables:
                blocks.append(
                    Block(kind="table", text=_table_to_markdown(table), source_ref=ref)
                )

        if not blocks:
            raise LoaderError(
                f"{path.name} contains no extractable text; scanned documents "
                "are unsupported"
            )

        return blocks


def _extract_pages(
    path: Path,
) -> tuple[list[list[_Line]], list[list[list[list[str | None]]]]]:
    """Return, per page, the non-table text lines (with dominant font size)
    and the raw table rows.

    Table cell values are excluded from the text lines by exact string
    match — a known limitation (silent text loss when a prose line equals a
    cell value) tracked separately and not part of this change.
    """
    page_lines: list[list[_Line]] = []
    page_tables: list[list[list[list[str | None]]]] = []

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            tables = [table.extract() for table in page.find_tables()]
            cell_values = _flatten_cell_values(tables)
            page_lines.append(_extract_lines(page, exclude=cell_values))
            page_tables.append(tables)

    return page_lines, page_tables


def _extract_lines(page: pdfplumber.page.Page, exclude: set[str]) -> list[_Line]:
    lines: list[_Line] = []
    for raw_line in page.extract_text_lines():
        text = raw_line["text"].strip()
        if not text or text in exclude:
            continue
        lines.append(_Line(text=text, size=_dominant_size(raw_line.get("chars") or [])))
    return lines


def _dominant_size(chars: list[dict]) -> float:
    """The most common character size on a line, rounded to 1 decimal place
    to absorb pdfplumber's floating-point jitter within a single font.
    """
    if not chars:
        return 0.0
    sizes = [round(c["size"], 1) for c in chars]
    return Counter(sizes).most_common(1)[0][0]


def _heading_level_map(page_lines: list[list[_Line]]) -> dict[float, int]:
    """Map each heading-qualifying font size to its level (1 = largest).

    The baseline is the size with the most characters across the whole
    document — body text dominates by character count even when headings
    are more numerous, so this is robust to documents with many short
    headings and little prose.
    """
    size_char_counts: Counter[float] = Counter()
    for lines in page_lines:
        for line in lines:
            size_char_counts[line.size] += len(line.text)

    if not size_char_counts:
        return {}

    baseline = size_char_counts.most_common(1)[0][0]
    threshold = baseline * _HEADING_SIZE_RATIO

    heading_sizes = sorted(
        (size for size in size_char_counts if size > baseline and size >= threshold),
        reverse=True,
    )
    return {size: level for level, size in enumerate(heading_sizes, start=1)}


def _flatten_cell_values(tables: list[list[list[str | None]]]) -> set[str]:
    values: set[str] = set()
    for table in tables:
        for row in table:
            for cell in row:
                if cell:
                    values.add(cell.strip())
    return values


def _table_to_markdown(table: list[list[str | None]]) -> str:
    if not table:
        return ""
    rows = [[cell.strip() if cell else "" for cell in row] for row in table]
    header, *body = rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
