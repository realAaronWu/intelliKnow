"""Test-plan §7 — citation verification.

Source: superpowers/test-plans/04-rag-read-path-tests.md §7

`verify_citations` is the last line of defense against the main failure
mode of a small RAG system: a fluent answer confidently citing a document
that was never actually retrieved. It costs no extra model call because it
never asks the model anything — it just checks the markers the model
wrote against the sources it was actually given, and strips whatever
doesn't resolve. `test_7_2_...` is the load-bearing test here.
"""

from __future__ import annotations

from app.rag.citations import Citation, verify_citations
from app.rag.context import ContextBundle, Source


def _source(marker: str, document_id: int, title: str, ref: str) -> Source:
    return Source(
        marker=marker,
        chunk_id=document_id * 10,
        document_id=document_id,
        document_title=title,
        source_ref=ref,
        heading_path=None,
        text=f"body of {marker}",
    )


def _bundle(*sources: Source) -> ContextBundle:
    return ContextBundle(sources=list(sources), prompt_block="")


# --- 7.1 Valid marker resolves ------------------------------------------------


def test_7_1_valid_marker_resolves_to_the_right_document_and_ref():
    bundle = _bundle(_source("[1]", 1, "policy.pdf", "p. 2"))

    cleaned, citations = verify_citations("You get twenty days [1].", bundle)

    assert cleaned == "You get twenty days [1]."
    assert citations == [Citation(document_id=1, document_title="policy.pdf", source_ref="p. 2")]


# --- 7.2 Unresolvable marker stripped -----------------------------------------


def test_7_2_unresolvable_marker_stripped_from_answer_and_uncited():
    bundle = _bundle(_source("[1]", 1, "policy.pdf", "p. 2"))

    cleaned, citations = verify_citations("You get twenty days [9].", bundle)

    assert "[9]" not in cleaned
    assert citations == []


# --- 7.3 Remaining citations kept ----------------------------------------------


def test_7_3_valid_marker_survives_alongside_a_stripped_one():
    bundle = _bundle(_source("[1]", 1, "policy.pdf", "p. 2"))

    cleaned, citations = verify_citations("Leave is twenty days [1], also see [9].", bundle)

    assert "[1]" in cleaned
    assert "[9]" not in cleaned
    assert citations == [Citation(document_id=1, document_title="policy.pdf", source_ref="p. 2")]


def test_verified_markers_are_renumbered_to_match_the_displayed_source_list():
    bundle = _bundle(
        _source("[3]", 2, "expenses.docx", "p. 4"),
        _source("[4]", 2, "expenses.docx", "p. 5"),
    )

    cleaned, citations = verify_citations(
        "Use the travel form [3]; approval is also covered [4].", bundle
    )

    assert cleaned == "Use the travel form [1]; approval is also covered [1]."
    assert citations == [
        Citation(document_id=2, document_title="expenses.docx", source_ref="p. 4")
    ]


# --- 7.4 Multiple documents, first-cited order, no duplicates -----------------


def test_7_4_multiple_documents_each_appear_once_in_first_cited_order():
    bundle = _bundle(
        _source("[1]", 1, "policy.pdf", "p. 2"),
        _source("[2]", 2, "handbook.docx", "¶ 4"),
    )

    cleaned, citations = verify_citations(
        "See [2] and [1], also [2] again.", bundle
    )

    assert citations == [
        Citation(document_id=2, document_title="handbook.docx", source_ref="¶ 4"),
        Citation(document_id=1, document_title="policy.pdf", source_ref="p. 2"),
    ]


# --- 7.5 No markers -------------------------------------------------------------


def test_7_5_no_markers_yields_empty_citations_without_error():
    bundle = _bundle(_source("[1]", 1, "policy.pdf", "p. 2"))

    cleaned, citations = verify_citations("I don't have that information.", bundle)

    assert cleaned == "I don't have that information."
    assert citations == []
