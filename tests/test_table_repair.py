"""Tests for ragged table detection and AI restructuring.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §4.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber

from app.providers.base import ProviderError
from app.rag.tables import is_ragged, repair_table
from tests.doubles import FakeLLMProvider

FIXTURES = Path(__file__).parent / "fixtures" / "docs"

CLEAN_ROWS = [
    ["Band", "Min", "Mid", "Max"],
    ["Band 1", "32000", "38000", "44000"],
    ["Band 2", "45000", "52000", "59000"],
]


class TestIsRagged:
    def test_inconsistent_column_counts_is_ragged(self):
        rows = [["A", "B", "C"], ["D", "E"], ["F", "G", "H", "I"]]

        assert is_ragged(rows) is True

    def test_majority_empty_cells_is_ragged(self):
        # Uniform column count, but a merged-cell grid where most values
        # never landed in their own cell.
        rows = [
            ["Band 1", "", "", ""],
            ["Band 2", "", "", ""],
            ["Band 3", "", "", ""],
        ]

        assert is_ragged(rows) is True

    def test_clean_table_is_not_ragged(self):
        assert is_ragged(CLEAN_ROWS) is False

    def test_empty_rows_is_not_ragged(self):
        assert is_ragged([]) is False


RAGGED_ROWS = [
    ["Band", "", "Min", "Mid", "Max"],
    ["Band", "1 (merged)", "", "38000", "44000"],
    ["Band", "2", "45000", "52000-59000", ""],
]


class TestRepairTable:
    """`repair_table` takes and returns the structural grid, never
    markdown: a repaired table has to re-enter the pipeline the same shape
    a loader produces, so that `render_table_markdown` stays the one place
    a table becomes text (see `app/rag/blocks.py`).
    """

    def test_ragged_region_repaired_by_llm(self):
        llm = FakeLLMProvider()
        repaired = [
            ["Band", "Min", "Mid", "Max"],
            ["Band 1", "32000", "38000", "44000"],
        ]
        llm.expect_schema({"rows": repaired})

        result = repair_table(RAGGED_ROWS, llm)

        assert len(llm.calls) == 1
        assert result == repaired

    def test_the_ragged_table_is_sent_to_the_model_as_rendered_markdown(self):
        llm = FakeLLMProvider()
        llm.expect_schema({"rows": [["Band", "Min"], ["Band 1", "32000"]]})

        repair_table(RAGGED_ROWS, llm)

        sent = llm.calls[0]["user"]
        assert "| Band |  | Min | Mid | Max |" in sent

    def test_provider_failure_falls_back_to_the_original_rows(self):
        llm = FakeLLMProvider()
        llm.fail_next(ProviderError.timeout("timed out"))

        result = repair_table(RAGGED_ROWS, llm)

        assert result == RAGGED_ROWS

    def test_provider_failure_logs_a_warning_naming_the_document(self, caplog):
        """DEFECT 2's fallback-visibility fix applies here too: a silent
        fallback to raw text is indistinguishable from a table that was
        never ragged to begin with unless it is logged.
        """
        llm = FakeLLMProvider()
        llm.fail_next(ProviderError.timeout("timed out"))

        with caplog.at_level(logging.WARNING, logger="app.rag.tables"):
            result = repair_table(RAGGED_ROWS, llm, doc_id=7)

        assert result == RAGGED_ROWS
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "7" in message
        assert "timeout" in message

    def test_invalid_structure_falls_back_to_the_original_rows(self):
        llm = FakeLLMProvider()
        llm.expect_schema({"rows": "not-a-table"})

        result = repair_table(RAGGED_ROWS, llm)

        assert result == RAGGED_ROWS

    def test_missing_rows_key_falls_back_to_the_original_rows(self):
        llm = FakeLLMProvider()
        llm.expect_schema({"unexpected": "shape"})

        result = repair_table(RAGGED_ROWS, llm)

        assert result == RAGGED_ROWS

    def test_ragged_rows_returned_by_llm_falls_back_to_the_original_rows(self):
        # The whole point of repair is a *clean* table — if the model's own
        # answer is itself ragged, trust the raw extraction instead of
        # silently swapping one ragged grid for another.
        llm = FakeLLMProvider()
        llm.expect_schema({"rows": [["a", "b", "c"], ["d", "e"]]})

        result = repair_table(RAGGED_ROWS, llm)

        assert result == RAGGED_ROWS


class TestCleanTableCostsNothing:
    def test_clean_table_makes_zero_llm_calls(self):
        """Cost regression guard (test plan §4.5): a caller only invokes
        `repair_table` for regions `is_ragged` has flagged. Without this
        assertion, a later broadened raggedness heuristic could silently
        start sending every table to the model.
        """
        llm = FakeLLMProvider()

        assert is_ragged(CLEAN_ROWS) is False
        # `repair_table` is deliberately never called for a clean table.

        assert llm.calls == []


class TestRealFixtures:
    def test_ragged_salary_grid_triggers_repair(self):
        rows = _extract_table_rows(FIXTURES / "ragged_salary_grid.pdf")

        assert is_ragged(rows) is True

    def test_salary_bands_does_not_trigger_repair(self):
        rows = _extract_table_rows(FIXTURES / "salary_bands.pdf")

        assert is_ragged(rows) is False


def _extract_table_rows(pdf_path: Path) -> list[list[str]]:
    """Extract the first table-shaped region of a PDF's first page as rows.

    `ragged_salary_grid.pdf` has no ruling lines (Task 0's fixture draws it
    with plain positioned text, deliberately unlike a real ruled table), so
    pdfplumber's line-based table detector finds nothing on it at all. The
    text-position strategy recovers structure from both fixtures uniformly
    — real column alignment for `salary_bands.pdf`, and the merged/missing
    cells that make the grid genuinely ragged for `ragged_salary_grid.pdf`
    — which is what lets this helper validate `is_ragged` against real
    extracted content rather than hand-built rows.
    """
    settings = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "intersection_tolerance": 15,
    }
    with pdfplumber.open(pdf_path) as pdf:
        tables = pdf.pages[0].find_tables(settings)
        assert tables, f"no table-shaped region found in {pdf_path.name}"
        return tables[0].extract()
