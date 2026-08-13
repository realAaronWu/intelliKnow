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

        # "Employee Handbook" and "Leave Policy" are headings (see
        # TestPdfHeadings below), not paragraphs — only true body text
        # belongs here.
        assert str(ANNUAL_LEAVE_DAYS) in paragraph_text

    def test_paragraphs_are_in_document_order(self):
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")
        paragraph_text = [b.text for b in blocks if b.kind == "paragraph"]
        joined = "\n".join(paragraph_text)

        assert joined.index("annual") < joined.index("Salary bands are reviewed")


class TestPdfHeadings:
    """Covers Fix A: pypdf exposes no font metadata, so headings were never
    classified — every non-table PDF line became a `paragraph` block, and
    Task 5's chunker heading-path enrichment was silently inert for PDFs.
    `handbook.pdf` has a real title + two Heading1 sections (Task 0), all
    rendered by reportlab at the same 18pt vs. 10pt body text.
    """

    def test_title_and_heading1_lines_become_heading_blocks(self):
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")
        heading_texts = {b.text for b in blocks if b.kind == "heading"}

        assert heading_texts == {"Employee Handbook", "Leave Policy", "Compensation"}

    def test_heading_blocks_are_no_longer_paragraphs(self):
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")
        paragraph_texts = {b.text for b in blocks if b.kind == "paragraph"}

        assert "Employee Handbook" not in paragraph_texts
        assert "Leave Policy" not in paragraph_texts
        assert "Compensation" not in paragraph_texts

    def test_heading_level_reflects_font_size_ordering(self):
        # reportlab's default stylesheet gives "Title" and "Heading1" the
        # same 18pt font size, so a pure size heuristic correctly collapses
        # both to the same (largest) level relative to the 10pt body text.
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")
        heading_levels = {b.text: b.heading_level for b in blocks if b.kind == "heading"}

        assert heading_levels == {"Employee Handbook": 1, "Leave Policy": 1, "Compensation": 1}

    def test_uniform_font_size_document_has_no_headings(self, tmp_path):
        path = _make_uniform_font_pdf(tmp_path / "uniform.pdf")

        blocks = PdfLoader().load(path)

        assert not [b for b in blocks if b.kind == "heading"]
        assert [b for b in blocks if b.kind == "paragraph"]


def _make_uniform_font_pdf(path: Path) -> Path:
    """A minimal PDF with every line set in the same font size — the edge
    case the heading heuristic must handle without crashing or inventing
    heading levels when there is no size variation to detect.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "This is the first line of body text.")
    c.drawString(72, 680, "This is the second line of body text.")
    c.drawString(72, 660, "This is the third line of body text.")
    c.showPage()
    c.save()
    return path


class TestPdfTableParagraphDedup:
    """Covers Fix B: excluding paragraph lines by exact string match against
    table cell values drops any prose line that happens to equal a cell
    string — silent text loss, not duplication. Position-based exclusion
    (cropping the table's bounding box out of the text-extraction area)
    must fix this without losing real body text or leaking table content
    into paragraph blocks.
    """

    def test_prose_line_matching_a_cell_value_is_not_dropped(self, tmp_path):
        path = _make_prose_matches_cell_pdf(tmp_path / "prose_matches_cell.pdf")

        blocks = PdfLoader().load(path)
        paragraph_texts = [b.text for b in blocks if b.kind == "paragraph"]

        assert "Band 1" in paragraph_texts

    def test_table_cell_values_do_not_leak_into_paragraph_blocks(self, tmp_path):
        path = _make_prose_matches_cell_pdf(tmp_path / "prose_matches_cell.pdf")

        blocks = PdfLoader().load(path)
        paragraph_texts = [b.text for b in blocks if b.kind == "paragraph"]
        table_texts = [b.text for b in blocks if b.kind == "table"]

        assert len(table_texts) == 1
        # The prose line above the table is the only "Band 1" paragraph —
        # the table's own "Band 1" cell must not also leak out as a second,
        # duplicate paragraph block.
        assert paragraph_texts.count("Band 1") == 1

    def test_salary_bands_cell_values_are_not_duplicated_as_paragraphs(self):
        blocks = PdfLoader().load(FIXTURES / "salary_bands.pdf")
        paragraph_texts = "\n".join(b.text for b in blocks if b.kind == "paragraph")

        for band, _min_val, mid_val, _max_val in SALARY_BANDS:
            assert band not in paragraph_texts
            assert str(mid_val) not in paragraph_texts


def _make_prose_matches_cell_pdf(path: Path) -> Path:
    """A paragraph whose full text ("Band 1") exactly equals a table cell
    value in the table below it. The old string-equality de-dup dropped
    this prose line entirely because it matched a cell string;
    position-based exclusion keeps it, since it sits outside the table's
    bounding box.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=letter, invariant=1)
    table = Table(
        [["Band", "Value"], ["Band 1", "100"]],
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]),
    )
    story = [
        Paragraph("Band 1", styles["BodyText"]),
        Spacer(1, 12),
        table,
    ]
    doc.build(story)
    return path


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
