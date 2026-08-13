"""Tests for the DOCX loader.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §3 (DOCX rows).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.rag.loaders.docx import DocxLoader

FIXTURES = Path(__file__).parent / "fixtures" / "docs"


class TestDocxHeadings:
    def test_headings_become_heading_blocks_with_correct_levels(self):
        blocks = DocxLoader().load(FIXTURES / "nda.docx")
        headings = {b.text: b.heading_level for b in blocks if b.kind == "heading"}

        assert headings["Non-Disclosure Agreement"] == 1
        assert headings["1. Definitions"] == 2
        assert headings["2. Obligations"] == 2
        assert headings["3. Term"] == 2

    def test_headings_are_not_classified_as_paragraphs(self):
        blocks = DocxLoader().load(FIXTURES / "nda.docx")
        paragraph_texts = {b.text for b in blocks if b.kind == "paragraph"}

        assert "Non-Disclosure Agreement" not in paragraph_texts
        assert "1. Definitions" not in paragraph_texts


class TestDocxParagraphRefs:
    def test_source_refs_are_paragraph_numbered(self):
        blocks = DocxLoader().load(FIXTURES / "nda.docx")

        assert blocks
        for block in blocks:
            assert re.fullmatch(r"¶ \d+", block.source_ref), block.source_ref

    def test_refs_increase_in_document_order(self):
        blocks = DocxLoader().load(FIXTURES / "nda.docx")
        numbers = [int(b.source_ref.split(" ")[1]) for b in blocks]

        assert numbers == sorted(numbers)
        assert len(set(numbers)) == len(numbers)


class TestDocxTables:
    def test_table_block_has_rows_and_columns_intact(self):
        blocks = DocxLoader().load(FIXTURES / "nda.docx")
        table_blocks = [b for b in blocks if b.kind == "table"]

        assert len(table_blocks) == 1
        text = table_blocks[0].text
        lines = [line for line in text.strip().splitlines() if line.strip()]

        # header + markdown separator + one body row
        assert len(lines) == 3
        col_counts = {line.strip().strip("|").count("|") for line in lines}
        assert len(col_counts) == 1, f"ragged markdown table: {lines}"

        assert "Effective Date" in text
        assert "2024-01-01" in text
        assert "Term (years)" in text
