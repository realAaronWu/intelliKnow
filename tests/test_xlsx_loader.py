"""Tests for the XLSX loader.

Covers superpowers/test-plans/03-rag-write-path-tests.md §3 (XLSX rows).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.rag.loaders.xlsx import XlsxLoader
from scripts.make_fixtures import BUDGET_SHEET_NAMES

FIXTURES = Path(__file__).parent / "fixtures" / "docs"


class TestXlsxPerSheet:
    def test_every_sheet_yields_at_least_one_table_block(self):
        blocks = XlsxLoader().load(FIXTURES / "budget.xlsx")
        table_blocks = [b for b in blocks if b.kind == "table"]
        sheet_names_seen = {b.source_ref.split("!")[0] for b in table_blocks}

        assert sheet_names_seen == set(BUDGET_SHEET_NAMES)


class TestXlsxRefs:
    def test_refs_name_sheet_and_cell_range(self):
        blocks = XlsxLoader().load(FIXTURES / "budget.xlsx")

        for block in blocks:
            assert re.fullmatch(r"[^!]+![A-Z]+\d+:[A-Z]+\d+", block.source_ref), (
                block.source_ref
            )


class TestXlsxFormulas:
    def test_formula_cells_contribute_computed_value_not_formula_text(self):
        blocks = XlsxLoader().load(FIXTURES / "budget.xlsx")
        q1_block = next(b for b in blocks if b.source_ref.startswith("Q1 Actuals"))

        assert "4000" in q1_block.text
        assert "4200" in q1_block.text
        assert "8200" in q1_block.text
        assert "=B2" not in q1_block.text
        assert "=SUM" not in q1_block.text
