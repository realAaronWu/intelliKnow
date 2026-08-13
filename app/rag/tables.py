"""Ragged table detection and AI restructuring.

A table extracted from a PDF/DOCX/XLSX source is "ragged" when the raw
cell grid does not reflect the visual table faithfully — inconsistent
column counts row to row, or a majority of empty cells — which is exactly
what a merged-cell salary grid produces once its cells are read back as
plain text. `is_ragged` is a cheap, local check with no external calls, so
it can gate every extracted table without cost.

`repair_table` takes and returns the structural grid — never markdown —
so a repaired table re-enters the pipeline the same shape a loader
produces, and `app/rag/blocks.py::render_table_markdown` stays the one
place a table becomes text. It sends the flagged region to an
`LLMProvider` with a schema requesting a clean rectangular table, and its
result replaces the extraction. Any provider failure — or a response
that, even though schema-valid JSON, is not itself a clean rectangular
table — falls back to the original rows so ingestion always completes.
Both fallback paths
log a warning (naming the document, when known, and — for a provider
failure — the error category) so a repair that silently gave up is visible
in service output rather than indistinguishable from a table that was
never ragged to begin with; see `app/ingest/classify_doc.py`'s module
docstring for the identical reasoning applied to intent suggestion.
"""

from __future__ import annotations

import logging

from app.providers.base import LLMProvider, ProviderError
from app.rag.blocks import render_table_markdown

logger = logging.getLogger(__name__)

_TABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}},
        }
    },
    "required": ["rows"],
    "additionalProperties": False,
}

_REPAIR_SYSTEM_PROMPT = (
    "You clean up a table extracted from a document whose cells did not "
    "extract cleanly — merged cells, inconsistent columns, garbled rows. "
    "Return the corrected table as a rectangular grid: every row, "
    "including the header, is a list of cell strings, and every row has "
    "the same number of cells as every other row. Preserve every value "
    "present in the raw text; never invent data that is not there."
)


def is_ragged(rows: list[list[str]]) -> bool:
    """True if `rows` looks like a table extraction gone wrong.

    Two independent signals, either sufficient on its own:
    - column counts differ across rows (a row lost or gained cells), or
    - a majority of all cells are empty (merged cells left their neighbors
      blank).
    """
    if not rows:
        return False

    if len({len(row) for row in rows}) > 1:
        return True

    total_cells = sum(len(row) for row in rows)
    if total_cells == 0:
        return False

    empty_cells = sum(1 for row in rows for cell in row if not cell or not cell.strip())
    return empty_cells * 2 > total_cells


def repair_table(
    rows: list[list[str]], llm: LLMProvider, *, doc_id: int | None = None
) -> list[list[str]]:
    """Ask `llm` to restructure a ragged table's grid into a clean one.

    Returns the corrected grid on success. Falls back to `rows` unchanged
    if the provider call fails, or if it succeeds but the returned
    structure is not itself a clean rectangular table — never raises, so a
    bad repair attempt cannot block ingestion. Either fallback logs a
    warning naming `doc_id` (when the caller has one) so the degraded
    repair is visible rather than silent.
    """
    try:
        result = llm.complete(
            system=_REPAIR_SYSTEM_PROMPT,
            user=(
                "Ragged table extracted from a document:\n\n"
                f"{render_table_markdown(rows)}"
            ),
            schema=_TABLE_SCHEMA,
        )
    except ProviderError as exc:
        logger.warning(
            "table repair for document %s fell back to raw extracted text: "
            "provider error (category=%s): %s",
            doc_id,
            exc.category,
            exc,
        )
        return rows

    repaired = _clean_rows(result.parsed)
    if repaired is None:
        logger.warning(
            "table repair for document %s fell back to raw extracted text: "
            "model response was not a clean rectangular table",
            doc_id,
        )
        return rows

    return repaired


def _clean_rows(parsed: dict | None) -> list[list[str]] | None:
    """Return `parsed["rows"]` if it is a non-empty, non-ragged grid of
    strings; `None` otherwise.
    """
    if not isinstance(parsed, dict):
        return None

    rows = parsed.get("rows")
    if not isinstance(rows, list) or not rows:
        return None

    for row in rows:
        if not isinstance(row, list) or not row:
            return None
        if not all(isinstance(cell, str) for cell in row):
            return None

    if is_ragged(rows):
        return None

    return rows
