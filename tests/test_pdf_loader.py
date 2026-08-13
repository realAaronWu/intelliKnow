"""Tests for the PDF loader.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.blocks import LoaderError
from app.rag.loaders.pdf import PdfLoader
from scripts.make_fixtures import ANNUAL_LEAVE_DAYS, SALARY_BANDS

FIXTURES = Path(__file__).parent / "fixtures" / "docs"


class TestPdfBodyText:
    def test_handbook_paragraphs_extracted(self):
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")
        paragraph_text = "\n".join(b.text for b in blocks if b.kind == "paragraph")

        assert "Employee Handbook" in paragraph_text
        assert "Leave Policy" in paragraph_text
        assert str(ANNUAL_LEAVE_DAYS) in paragraph_text

    def test_paragraphs_are_in_document_order(self):
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")
        paragraph_text = [b.text for b in blocks if b.kind == "paragraph"]
        joined = "\n".join(paragraph_text)

        assert joined.index("Employee Handbook") < joined.index("Leave Policy")
        assert joined.index("Leave Policy") < joined.index("Compensation")


class TestPdfPageRefs:
    def test_refs_are_one_indexed_page_numbers(self):
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")

        assert blocks
        assert all(b.source_ref == "p. 1" for b in blocks)


class TestPdfTableStructure:
    def test_salary_bands_table_rows_and_columns_recoverable(self):
        blocks = PdfLoader().load(FIXTURES / "salary_bands.pdf")
        table_blocks = [b for b in blocks if b.kind == "table"]

        assert len(table_blocks) == 1
        text = table_blocks[0].text
        lines = [line for line in text.strip().splitlines() if line.strip()]

        # header + markdown separator + one row per band
        assert len(lines) == 2 + len(SALARY_BANDS)

        col_counts = {line.strip().strip("|").count("|") for line in lines}
        assert len(col_counts) == 1, f"ragged markdown table: {lines}"

        for band, _min_val, mid_val, _max_val in SALARY_BANDS:
            assert band in text
            assert str(mid_val) in text


class TestPdfScanned:
    def test_scanned_pdf_raises_loader_error_naming_scanned(self):
        with pytest.raises(LoaderError, match="scanned"):
            PdfLoader().load(FIXTURES / "scanned.pdf")


class TestPdfCorrupt:
    def test_corrupt_pdf_raises_loader_error(self):
        with pytest.raises(LoaderError):
            PdfLoader().load(FIXTURES / "corrupt.pdf")

    def test_corrupt_error_is_distinguishable_from_scanned(self):
        with pytest.raises(LoaderError) as scanned_exc:
            PdfLoader().load(FIXTURES / "scanned.pdf")
        with pytest.raises(LoaderError) as corrupt_exc:
            PdfLoader().load(FIXTURES / "corrupt.pdf")

        scanned_msg = str(scanned_exc.value).lower()
        corrupt_msg = str(corrupt_exc.value).lower()

        assert scanned_msg != corrupt_msg
        assert "scanned" in scanned_msg
        assert "scanned" not in corrupt_msg
