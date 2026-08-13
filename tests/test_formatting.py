"""Test-plan §8 — channel formatting.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §8

The prompt-side length instruction in `app/rag/generate.py` is best-effort
— nothing stops a model from ignoring it. `format_for_channel` is the
guarantee: whatever comes out is escaped for the destination markup and
never exceeds `profile.max_chars`, full stop. `test_8_2_...` is the
load-bearing test for that guarantee; `test_8_3_...` proves the cut is
never mid-word.
"""

from __future__ import annotations

from app.rag.citations import Citation
from app.rag.format import format_for_channel
from app.rag.generate import ChannelProfile

TELEGRAM = ChannelProfile(
    name="telegram", max_chars=4096, markup="markdownv2", supports_lists=False
)
TEAMS = ChannelProfile(name="teams", max_chars=28000, markup="html", supports_lists=True)
PLAIN_SMALL = ChannelProfile(name="test", max_chars=30, markup="plain", supports_lists=False)


# --- 8.1 Reserved characters escaped -------------------------------------------


def test_8_1_telegram_markdownv2_specials_are_escaped():
    answer = "a.b-c!d(e)f"

    result = format_for_channel(answer, [], TELEGRAM)

    assert result == r"a\.b\-c\!d\(e\)f"


# --- 8.2 Always within the limit ------------------------------------------------


def test_8_2_over_limit_answer_is_truncated_below_the_limit():
    answer = "word " * 1200  # 6000 chars, well over telegram's 4096
    assert len(answer) > TELEGRAM.max_chars

    result = format_for_channel(answer, [], TELEGRAM)

    assert len(result) <= TELEGRAM.max_chars


# --- 8.3 Word-boundary truncation -----------------------------------------------


def test_8_3_truncation_lands_on_a_word_boundary_with_a_visible_marker():
    answer = (
        "The quick brown fox jumps over the lazy dog again and again "
        "without end, really quite a long sentence indeed here."
    )

    result = format_for_channel(answer, [], PLAIN_SMALL)

    assert len(result) <= PLAIN_SMALL.max_chars
    assert result.endswith("…")
    prefix = result[: -len("…")]
    assert answer.startswith(prefix.rstrip())
    boundary_index = len(prefix.rstrip())
    assert boundary_index == len(answer) or answer[boundary_index] == " "


# --- 8.4 Under-limit answer untouched --------------------------------------------


def test_8_4_under_limit_answer_has_no_truncation_marker():
    answer = "Short answer."
    profile = ChannelProfile(name="test", max_chars=4096, markup="plain", supports_lists=False)

    result = format_for_channel(answer, [], profile)

    assert result == answer
    assert "…" not in result


# --- 8.5 Teams lists --------------------------------------------------------------


def test_8_5_teams_citations_render_as_bullets():
    citations = [
        Citation(document_id=1, document_title="policy.pdf", source_ref="p. 2"),
        Citation(document_id=2, document_title="handbook.docx", source_ref="¶ 4"),
    ]

    result = format_for_channel("Some answer.", citations, TEAMS)

    assert "<ul>" in result
    assert result.count("<li>") == 2
    assert "policy.pdf" in result
    assert "handbook.docx" in result


# --- 8.6 Citations rendered in a channel-appropriate form -----------------------


def test_8_6_telegram_citations_render_as_escaped_numbered_lines():
    citations = [Citation(document_id=1, document_title="policy.pdf", source_ref="p. 2")]

    result = format_for_channel("Some answer [1].", citations, TELEGRAM)

    assert r"1\." in result
    assert r"policy\.pdf" in result
    assert len(result) <= TELEGRAM.max_chars


def test_8_6_no_citations_produces_no_sources_section():
    result = format_for_channel("Some answer.", [], TELEGRAM)

    assert "Sources" not in result
