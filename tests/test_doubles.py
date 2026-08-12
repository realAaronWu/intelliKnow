"""Tests for tests.doubles — deterministic fakes for LLM and embedding providers.

Covers test-plan §4. Every later increment's tests import these doubles, so
their determinism (same input -> same output, calls recorded faithfully, no
silent defaults) is load-bearing for every score assertion downstream.
"""

from __future__ import annotations

import math

import pytest

from app.providers.base import ProviderError
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider


class TestFakeLLMProviderQueue:
    def test_queued_texts_return_in_order(self):
        fake = FakeLLMProvider()
        fake.expect_text("one")
        fake.expect_text("two")

        first = fake.complete(system="s", user="u1")
        second = fake.complete(system="s", user="u2")

        assert first.text == "one"
        assert second.text == "two"

    def test_queued_schema_response_parsed_matches_queued_object(self):
        fake = FakeLLMProvider()
        obj = {"intent": "hr", "confidence": 0.9}
        fake.expect_schema(obj)

        result = fake.complete(system="s", user="u", schema={"type": "object"})

        assert result.parsed == obj

    def test_calls_are_recorded_with_exact_params(self):
        fake = FakeLLMProvider()
        fake.expect_text("ignored")

        fake.complete(system="sys-prompt", user="user-prompt", max_tokens=256)

        assert fake.calls[0]["system"] == "sys-prompt"
        assert fake.calls[0]["user"] == "user-prompt"
        assert fake.calls[0]["max_tokens"] == 256

    def test_fail_next_raises_once_then_recovers(self):
        fake = FakeLLMProvider()
        fake.fail_next(ProviderError.rate_limit("slow down"))
        fake.expect_text("recovered")

        with pytest.raises(ProviderError) as exc_info:
            fake.complete(system="s", user="u")
        assert exc_info.value.category == "rate_limit"

        result = fake.complete(system="s", user="u")
        assert result.text == "recovered"

    def test_empty_queue_raises_clear_assertion(self):
        fake = FakeLLMProvider()

        with pytest.raises(AssertionError, match="no response was queued"):
            fake.complete(system="s", user="u")


class TestFakeEmbeddingProvider:
    def test_embedding_is_deterministic_for_same_text(self):
        fake = FakeEmbeddingProvider()

        [first] = fake.embed(["hello world"])
        [second] = fake.embed(["hello world"])

        assert first == second

    def test_embedding_is_unit_norm(self):
        fake = FakeEmbeddingProvider()

        [vec] = fake.embed(["some text"])

        length = math.sqrt(sum(c * c for c in vec))
        assert length == pytest.approx(1.0, abs=1e-6)

    def test_embedding_order_and_count_preserved(self):
        fake = FakeEmbeddingProvider()
        texts = ["alpha", "beta", "gamma"]

        vectors = fake.embed(texts)

        assert len(vectors) == 3
        assert vectors[0] != vectors[1]
        assert vectors[1] != vectors[2]
        assert vectors[0] != vectors[2]
        [beta_alone] = fake.embed(["beta"])
        assert beta_alone == vectors[1]

    def test_set_vector_pins_exact_value_unnormalized(self):
        fake = FakeEmbeddingProvider(dimension=4)
        pinned = [0.5, 0.5, 0.5, 0.5]  # not unit length -- must round-trip verbatim
        fake.set_vector("pinned text", pinned)

        [vec] = fake.embed(["pinned text"])

        assert vec == pinned

    def test_dimension_property_reflects_constructor_argument(self):
        fake = FakeEmbeddingProvider(dimension=16)

        assert fake.dimension == 16
        [vec] = fake.embed(["anything"])
        assert len(vec) == 16

    def test_calls_are_recorded_per_embed_invocation(self):
        fake = FakeEmbeddingProvider()

        fake.embed(["a", "b"])
        fake.embed(["c"])

        assert fake.calls == [["a", "b"], ["c"]]
