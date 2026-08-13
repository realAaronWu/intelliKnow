"""Tests for the structural chunker.

Covers docs/superpowers/test-plans/03-rag-write-path-tests.md §5.
"""

from __future__ import annotations

import math
from pathlib import Path

from app.config import RAGConfig
from app.rag.blocks import Block
from app.rag.chunker import Chunk, chunk_blocks
from app.rag.loaders.pdf import PdfLoader
from scripts.make_fixtures import SALARY_BANDS

FIXTURES = Path(__file__).parent / "fixtures" / "docs"


class TestLongRunOverlap:
    def test_long_prose_run_yields_multiple_chunks_with_shared_overlap(self):
        text = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20).strip()
        blocks = [Block(kind="paragraph", text=text, source_ref="p. 1")]
        cfg = RAGConfig(chunk_chars=100, chunk_overlap_chars=20)

        chunks = chunk_blocks(blocks, cfg)

        assert len(chunks) > 1
        for prev, nxt in zip(chunks, chunks[1:]):
            assert nxt.text.startswith(prev.text[-cfg.chunk_overlap_chars :])


class TestTableRowsNeverSplit:
    def test_salary_grid_rows_are_never_split_across_chunks(self):
        blocks = PdfLoader().load(FIXTURES / "salary_bands.pdf")
        # Small enough that the table (~150 chars) must split across chunks.
        cfg = RAGConfig(chunk_chars=40, chunk_overlap_chars=5)

        chunks = chunk_blocks(blocks, cfg)

        for band, min_val, mid_val, max_val in SALARY_BANDS:
            row_line = f"| {band} | {min_val} | {mid_val} | {max_val} |"
            matches = [c for c in chunks if row_line in c.text]
            assert matches, f"row for {band!r} not found intact in any chunk"


class TestSmallOversizedTableStaysWhole:
    def test_table_under_1_5x_target_stays_in_one_chunk(self):
        blocks = PdfLoader().load(FIXTURES / "salary_bands.pdf")
        table_block = next(b for b in blocks if b.kind == "table")
        # Choose a target so the table is oversized (> 1x) but under 1.5x.
        target = math.ceil(len(table_block.text) / 1.3)
        cfg = RAGConfig(chunk_chars=target, chunk_overlap_chars=1)
        assert target < len(table_block.text) < target * 1.5

        chunks = chunk_blocks(blocks, cfg)

        whole_table_chunks = [c for c in chunks if table_block.text in c.text]
        assert len(whole_table_chunks) == 1


class TestHeadingPathPrefixed:
    def test_chunk_text_begins_with_heading_path_and_stores_it(self):
        blocks = [
            Block(kind="heading", text="Legal", source_ref="p. 1", heading_level=1),
            Block(kind="heading", text="Confidentiality", source_ref="p. 1", heading_level=2),
            Block(
                kind="paragraph",
                text="Some paragraph text about confidentiality.",
                source_ref="p. 1",
            ),
        ]
        cfg = RAGConfig()

        chunks = chunk_blocks(blocks, cfg)

        assert len(chunks) == 1
        assert chunks[0].text.startswith("Legal > Confidentiality")
        assert chunks[0].heading_path == ["Legal", "Confidentiality"]

    def test_no_heading_yields_no_prefix(self):
        blocks = [Block(kind="paragraph", text="No heading above this.", source_ref="p. 1")]
        cfg = RAGConfig()

        chunks = chunk_blocks(blocks, cfg)

        assert chunks[0].text == "No heading above this."
        assert chunks[0].heading_path == []

    def test_pdf_chunk_carries_a_real_heading_path(self):
        # This is the payoff of Fix A: before it, every PDF line was a
        # `paragraph` block, so `_group_into_sections` never saw a
        # `heading` block and every chunk from a PDF got an empty
        # heading_path — this enrichment was silently inert for the
        # project's primary format.
        blocks = PdfLoader().load(FIXTURES / "handbook.pdf")

        chunks = chunk_blocks(blocks, RAGConfig())

        leave_policy_chunks = [c for c in chunks if "Leave Policy" in c.heading_path]
        assert leave_policy_chunks
        assert leave_policy_chunks[0].text.startswith("Leave Policy")
        assert any(
            c.heading_path == ["Compensation"] and "Salary bands" in c.text for c in chunks
        )


class TestNoOverlapAcrossHeadings:
    def test_overlap_never_bleeds_across_a_heading_boundary(self):
        alpha = ("Alpha section filler text repeated. " * 10).strip()
        beta = ("Beta section filler text repeated. " * 10).strip()
        blocks = [
            Block(kind="heading", text="Legal", source_ref="p. 1", heading_level=1),
            Block(kind="paragraph", text=alpha, source_ref="p. 1"),
            Block(kind="heading", text="Finance", source_ref="p. 2", heading_level=1),
            Block(kind="paragraph", text=beta, source_ref="p. 2"),
        ]
        cfg = RAGConfig(chunk_chars=100, chunk_overlap_chars=20)

        chunks = chunk_blocks(blocks, cfg)

        legal_chunks = [c for c in chunks if c.heading_path == ["Legal"]]
        finance_chunks = [c for c in chunks if c.heading_path == ["Finance"]]
        assert legal_chunks and finance_chunks

        for chunk in finance_chunks:
            assert "Alpha" not in chunk.text
        for chunk in legal_chunks:
            assert "Beta" not in chunk.text


class TestDeterministic:
    def test_identical_input_yields_identical_boundaries(self):
        blocks = [
            Block(kind="heading", text="Section", source_ref="p. 1", heading_level=1),
            Block(kind="paragraph", text="Repeatable text. " * 15, source_ref="p. 1"),
            Block(
                kind="table",
                text="| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |",
                source_ref="p. 2",
            ),
        ]
        cfg = RAGConfig(chunk_chars=60, chunk_overlap_chars=10)

        first_run = chunk_blocks(blocks, cfg)
        second_run = chunk_blocks(blocks, cfg)

        assert _as_tuples(first_run) == _as_tuples(second_run)


class TestConfigRespected:
    def test_changing_chunk_chars_changes_chunk_count(self):
        text = ("Some prose text repeated many times over. " * 40).strip()
        blocks = [Block(kind="paragraph", text=text, source_ref="p. 1")]

        small_chunks = chunk_blocks(blocks, RAGConfig(chunk_chars=100, chunk_overlap_chars=10))
        large_chunks = chunk_blocks(blocks, RAGConfig(chunk_chars=1000, chunk_overlap_chars=10))

        assert len(small_chunks) > len(large_chunks)


class TestSourceRefsCarried:
    def test_single_block_chunk_source_ref_matches_block(self):
        blocks = [Block(kind="paragraph", text="Just one block.", source_ref="p. 5")]

        chunks = chunk_blocks(blocks, RAGConfig())

        assert chunks[0].source_ref == "p. 5"

    def test_chunk_spanning_blocks_records_every_originating_ref(self):
        blocks = [
            Block(kind="paragraph", text="First paragraph on page one.", source_ref="p. 1"),
            Block(
                kind="paragraph", text="Second paragraph also on page one.", source_ref="p. 1"
            ),
            Block(kind="paragraph", text="Third paragraph on page two.", source_ref="p. 2"),
        ]
        # Large enough that all three paragraphs land in one chunk.
        cfg = RAGConfig(chunk_chars=1000, chunk_overlap_chars=10)

        chunks = chunk_blocks(blocks, cfg)

        assert len(chunks) == 1
        assert "p. 1" in chunks[0].source_ref
        assert "p. 2" in chunks[0].source_ref


def _as_tuples(chunks: list[Chunk]) -> list[tuple]:
    return [
        (c.ordinal, c.text, tuple(c.heading_path), c.source_ref, c.char_count) for c in chunks
    ]
