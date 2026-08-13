"""XLSX loader: one table block per sheet.

Formula cells contribute their computed value, not the formula text —
`openpyxl` needs `data_only=True` for that. A workbook never opened in
Excel has no cached value for its formulas; such cells read back `None`
and are rendered as an empty cell rather than raising.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from app.rag.blocks import Block, LoaderError


class XlsxLoader:
    """Loads an `.xlsx` file into an ordered list of `Block`s, one table per sheet."""

    def load(self, path: Path) -> list[Block]:
        path = Path(path)

        try:
            workbook = openpyxl.load_workbook(str(path), data_only=True)
        except Exception as exc:
            raise LoaderError(f"could not parse {path.name}: {exc}") from exc

        blocks: list[Block] = []
        for sheet in workbook.worksheets:
            rows = [
                row
                for row in sheet.iter_rows(values_only=True)
                if any(cell is not None for cell in row)
            ]
            if not rows:
                continue
            ref = f"{sheet.title}!{sheet.dimensions}"
            blocks.append(Block(kind="table", text=_table_to_markdown(rows), source_ref=ref))

        if not blocks:
            raise LoaderError(f"{path.name} contains no sheets with data")

        return blocks


def _cell_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _table_to_markdown(rows: list[tuple]) -> str:
    header, *body = rows
    header_cells = [_cell_str(c) for c in header]
    lines = [
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join("---" for _ in header_cells) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(_cell_str(c) for c in row) + " |")
    return "\n".join(lines)
