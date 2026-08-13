"""PDF loader: body text via pypdf, tables via pdfplumber.

Two failure modes are deliberately distinguishable: a PDF that parses fine
but yields no extractable text (a scanned, image-only document) raises a
`LoaderError` naming that scanned documents are unsupported; a PDF that
cannot be parsed at all (corrupt/truncated bytes) raises a differently
worded `LoaderError`. Callers that want to tell these apart can inspect
the message; callers that don't care can just catch `LoaderError`.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pypdf

from app.rag.blocks import Block, LoaderError


class PdfLoader:
    """Loads a `.pdf` file into an ordered list of `Block`s."""

    def load(self, path: Path) -> list[Block]:
        path = Path(path)

        try:
            reader = pypdf.PdfReader(str(path))
            page_texts = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise LoaderError(f"could not parse {path.name}: {exc}") from exc

        try:
            tables_by_page = _extract_tables_by_page(path)
        except Exception as exc:
            raise LoaderError(f"could not parse {path.name}: {exc}") from exc

        blocks: list[Block] = []
        for page_num, text in enumerate(page_texts, start=1):
            ref = f"p. {page_num}"
            page_tables = tables_by_page.get(page_num, [])
            table_cell_values = _flatten_cell_values(page_tables)

            for paragraph in _split_paragraphs(text, exclude=table_cell_values):
                blocks.append(Block(kind="paragraph", text=paragraph, source_ref=ref))

            for table in page_tables:
                blocks.append(
                    Block(kind="table", text=_table_to_markdown(table), source_ref=ref)
                )

        if not blocks:
            raise LoaderError(
                f"{path.name} contains no extractable text; scanned documents "
                "are unsupported"
            )

        return blocks


def _extract_tables_by_page(path: Path) -> dict[int, list[list[list[str | None]]]]:
    tables_by_page: dict[int, list[list[list[str | None]]]] = {}
    with pdfplumber.open(str(path)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            if tables:
                tables_by_page[page_num] = tables
    return tables_by_page


def _flatten_cell_values(tables: list[list[list[str | None]]]) -> set[str]:
    values: set[str] = set()
    for table in tables:
        for row in table:
            for cell in row:
                if cell:
                    values.add(cell.strip())
    return values


def _split_paragraphs(text: str, exclude: set[str]) -> list[str]:
    paragraphs = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped in exclude:
            continue
        paragraphs.append(stripped)
    return paragraphs


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
