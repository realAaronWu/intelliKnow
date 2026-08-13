"""Tests for the Block model and loader interface.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §1.
"""

from __future__ import annotations

import pytest

from app.rag.blocks import Block, DocumentLoader, LoaderError


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
        block = Block(kind="table", text="| a | b |", source_ref="p. 1")

        assert block.heading_level is None

    def test_heading_without_level_is_rejected(self):
        with pytest.raises(ValueError):
            Block(kind="heading", text="Introduction", source_ref="p. 1")

    def test_paragraph_with_level_is_rejected(self):
        with pytest.raises(ValueError):
            Block(kind="paragraph", text="Some body text.", source_ref="p. 1", heading_level=1)

    def test_table_with_level_is_rejected(self):
        with pytest.raises(ValueError):
            Block(kind="table", text="| a | b |", source_ref="p. 1", heading_level=1)

    def test_source_ref_is_carried(self):
        block = Block(kind="paragraph", text="Some body text.", source_ref="p. 4")

        assert block.source_ref == "p. 4"


class TestBlockOrdering:
    def test_blocks_preserve_document_order(self):
        blocks = [
            Block(kind="heading", text="Title", source_ref="p. 1", heading_level=1),
            Block(kind="paragraph", text="First paragraph.", source_ref="p. 1"),
            Block(kind="table", text="| a | b |", source_ref="p. 1"),
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
