"""Test-plan §6 — answer generation.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §6

`generate_answer` makes exactly one call to the injected `LLMProvider` and
asserts on what it *recorded* — `FakeLLMProvider.calls` — rather than on
any generated text, since the fake never actually reads the prompt it is
handed. That is deliberate: these tests exist to prove the prompt itself
carries the grounding rule, the citation instruction, the channel's fit
constraints, and every context marker — not to test the (fake) model's
behaviour.
"""

from __future__ import annotations

import pytest

from app.providers.base import ProviderError
from app.rag.context import ContextBundle, Source
from app.rag.generate import ChannelProfile, generate_answer
from tests.doubles import FakeLLMProvider

TELEGRAM = ChannelProfile(
    name="telegram", max_chars=4096, markup="markdownv2", supports_lists=False
)


def _bundle() -> ContextBundle:
    sources = [
        Source(
            marker="[1]",
            chunk_id=1,
            document_id=1,
            document_title="policy.pdf",
            source_ref="p. 2",
            heading_path="Leave > Annual Leave",
            text="Employees accrue twenty days of annual leave per year.",
        ),
        Source(
            marker="[2]",
            chunk_id=2,
            document_id=2,
            document_title="handbook.docx",
            source_ref="¶ 4",
            heading_path=None,
            text="Unused leave carries over up to five days.",
        ),
    ]
    prompt_block = "\n\n".join(
        f"{s.marker} {s.document_title}\n```\n{s.text}\n```" for s in sources
    )
    return ContextBundle(sources=sources, prompt_block=prompt_block)


# --- 6.1 Grounding instruction -----------------------------------------------


def test_6_1_prompt_instructs_answering_only_from_context():
    llm = FakeLLMProvider()
    llm.expect_text("You get twenty days per year [1].")

    generate_answer("How much leave do I get?", _bundle(), TELEGRAM, llm)

    system = llm.calls[0]["system"].lower()
    assert "only" in system
    assert "context" in system
    # The "say plainly when context lacks the answer" half of the same rule.
    assert "does not contain" in system or "cannot" in system or "don't know" in system or "no information" in system


# --- 6.2 Citation instruction -------------------------------------------------


def test_6_2_prompt_asks_for_marker_citations():
    llm = FakeLLMProvider()
    llm.expect_text("You get twenty days per year [1].")

    generate_answer("How much leave do I get?", _bundle(), TELEGRAM, llm)

    system = llm.calls[0]["system"].lower()
    assert "cite" in system
    assert "[1]" in system or "marker" in system


# --- 6.3 Channel profile in prompt --------------------------------------------


def test_6_3_channel_length_limit_and_markup_in_prompt():
    llm = FakeLLMProvider()
    llm.expect_text("Answer.")

    generate_answer("q", _bundle(), TELEGRAM, llm)

    system = llm.calls[0]["system"]
    assert str(TELEGRAM.max_chars) in system
    assert TELEGRAM.markup in system.lower()
    assert "80 words" in system
    assert llm.calls[0]["max_tokens"] == 1024


def test_6_3_different_channel_profile_changes_the_prompt():
    teams = ChannelProfile(name="teams", max_chars=28000, markup="html", supports_lists=True)
    llm = FakeLLMProvider()
    llm.expect_text("Answer.")

    generate_answer("q", _bundle(), teams, llm)

    system = llm.calls[0]["system"]
    assert "28000" in system
    assert "html" in system.lower()


# --- 6.4 Context in prompt -----------------------------------------------------


def test_6_4_every_source_marker_appears_in_the_recorded_prompt():
    llm = FakeLLMProvider()
    llm.expect_text("Answer.")

    bundle = _bundle()
    generate_answer("q", bundle, TELEGRAM, llm)

    recorded = llm.calls[0]["system"] + "\n" + llm.calls[0]["user"]
    for source in bundle.sources:
        assert source.marker in recorded
    # And the actual chunk content, not just the marker glyphs.
    assert bundle.sources[0].text in recorded
    assert bundle.sources[1].text in recorded


# --- 6.5 Provider failure ------------------------------------------------------


def test_6_5_provider_failure_raises_for_the_caller():
    llm = FakeLLMProvider()
    llm.fail_next(ProviderError.backend("boom"))

    with pytest.raises(ProviderError):
        generate_answer("q", _bundle(), TELEGRAM, llm)
