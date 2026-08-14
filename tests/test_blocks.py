"""Tests for the Block model and loader interface.

Covers superpowers/test-plans/03-rag-write-path-tests.md §1.
"""

from __future__ import annotations

import pytest

from app.rag.blocks import Block, DocumentLoader, LoaderError, render_table_markdown


class TestBlock:
    def test_heading_block_carries_level(self):
        block = Block(kind="heading", text="Introduction", source_ref="p. 1", heading_level=1)

        assert block.kind == "heading"
        assert block.heading_level == 1

    def test_paragraph_block_defaults_heading_level_to_none(self):
        block = Block(kind="paragraph", text="Some body text.", source_ref="p. 1")

        assert block.kind == "paragraph"
        assert block.heading_level is None

    def test_table_block_defaults_heading_level_to_none(self):
        block = Block.table(rows=[["a", "b"]], source_ref="p. 1")

        assert block.heading_level is None

    def test_heading_without_level_is_rejected(self):
        with pytest.raises(ValueError):
            Block(kind="heading", text="Introduction", source_ref="p. 1")

    def test_paragraph_with_level_is_rejected(self):
        with pytest.raises(ValueError):
            Block(kind="paragraph", text="Some body text.", source_ref="p. 1", heading_level=1)

    def test_table_with_level_is_rejected(self):
        with pytest.raises(ValueError):
            Block(
                kind="table",
                text="| a | b |",
                source_ref="p. 1",
                heading_level=1,
                rows=[["a", "b"]],
            )

    def test_source_ref_is_carried(self):
        block = Block(kind="paragraph", text="Some body text.", source_ref="p. 4")

        assert block.source_ref == "p. 4"


class TestTableBlockStructure:
    """A table block carries its grid structurally, and its markdown is
    derived from that grid — never the other way round. See
    `app/rag/blocks.py`'s module docstring for why the direction matters.
    """

    def test_table_block_carries_its_rows(self):
        block = Block.table(rows=[["A", "B"], ["1", "2"]], source_ref="p. 1")

        assert block.rows == [["A", "B"], ["1", "2"]]

    def test_table_block_renders_its_rows_to_markdown(self):
        block = Block.table(rows=[["A", "B"], ["1", "2"]], source_ref="p. 1")

        assert block.text == "| A | B |\n| --- | --- |\n| 1 | 2 |"

    def test_a_table_block_without_rows_is_rejected(self):
        with pytest.raises(ValueError, match="structural rows"):
            Block(kind="table", text="| a | b |", source_ref="p. 1")

    def test_rows_on_a_non_table_block_is_rejected(self):
        with pytest.raises(ValueError, match="only valid on table blocks"):
            Block(kind="paragraph", text="text", source_ref="p. 1", rows=[["a"]])

    def test_a_wrapped_cell_renders_on_a_single_line(self):
        """python-docx joins a multi-paragraph cell's paragraphs with "\\n"
        and pdfplumber returns newlines for any wrapped cell. A rendered
        row must still occupy exactly one line, or every later stage that
        reasons about rows sees phantom ones.
        """
        block = Block.table(
            rows=[["Policy", "Detail"], ["Leave", "25 days a year.\nProrated."]],
            source_ref="¶ 1",
        )

        assert len(block.text.split("\n")) == 3
        assert "| Leave | 25 days a year. Prorated. |" in block.text

    def test_a_pipe_inside_a_cell_is_escaped_when_rendered_but_kept_in_rows(self):
        block = Block.table(rows=[["A"], ["x | y"]], source_ref="p. 1")

        # The grid keeps the cell's real value; only the rendering escapes.
        assert block.rows == [["A"], ["x | y"]]
        assert block.text.splitlines()[-1] == r"| x \| y |"

    def test_rendering_is_idempotent_over_an_already_normalized_grid(self):
        """`Block.table` normalizes, then renders. Re-rendering the stored
        rows — which the chunker's table splitter does for every piece —
        must not escape an escape.
        """
        block = Block.table(rows=[["A"], ["x | y"]], source_ref="p. 1")

        assert render_table_markdown(block.rows) == block.text

    def test_an_empty_cell_is_rendered_blank_rather_than_none(self):
        block = Block.table(rows=[["A", "B"], ["1", None]], source_ref="p. 1")

        assert block.rows == [["A", "B"], ["1", ""]]
        assert block.text.splitlines()[-1] == "| 1 |  |"


class TestBlockOrdering:
    def test_blocks_preserve_document_order(self):
        blocks = [
            Block(kind="heading", text="Title", source_ref="p. 1", heading_level=1),
            Block(kind="paragraph", text="First paragraph.", source_ref="p. 1"),
            Block.table(rows=[["a", "b"]], source_ref="p. 1"),
            Block(kind="paragraph", text="Second paragraph.", source_ref="p. 2"),
        ]

        assert [b.kind for b in blocks] == ["heading", "paragraph", "table", "paragraph"]
        assert [b.source_ref for b in blocks] == ["p. 1", "p. 1", "p. 1", "p. 2"]


class TestDocumentLoaderProtocol:
    def test_structurally_compatible_loader_returns_blocks(self, tmp_path):
        class FakeLoader:
            def load(self, path):
                return [Block(kind="paragraph", text="hi", source_ref="p. 1")]

        loader: DocumentLoader = FakeLoader()
        result = loader.load(tmp_path / "doc.pdf")

        assert isinstance(result, list)
        assert isinstance(result[0], Block)

    def test_loader_protocol_is_runtime_checkable(self):
        class FakeLoader:
            def load(self, path):
                return []

        assert isinstance(FakeLoader(), DocumentLoader)

    def test_non_loader_does_not_satisfy_protocol(self):
        class NotALoader:
            pass

        assert not isinstance(NotALoader(), DocumentLoader)


class TestLoaderError:
    def test_is_an_exception(self):
        assert issubclass(LoaderError, Exception)

    def test_carries_message(self):
        with pytest.raises(LoaderError, match="could not parse document"):
            raise LoaderError("could not parse document")
