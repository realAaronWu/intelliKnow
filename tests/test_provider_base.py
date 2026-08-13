"""Tests for app.providers.base — provider protocols and error type.

Covers test-plan §3.
"""

from __future__ import annotations

import dataclasses
import inspect
import math

import pytest

from app.providers.base import LLMProvider, LLMResult, ProviderError, normalize


def test_protocol_max_tokens_default_leaves_room_for_thinking():
    """The shipped config runs claude-opus-5, where thinking is on by default
    (we deliberately omit the `thinking` parameter) and `max_tokens` caps
    thinking *plus* visible response text. 1024 was small enough that a
    normal answer could be truncated mid-generation.
    """
    default = inspect.signature(LLMProvider.complete).parameters["max_tokens"].default
    assert default == 4096


class TestProviderError:
    def test_timeout_constructor_sets_category_and_message(self):
        err = ProviderError.timeout("took too long")
        assert err.category == "timeout"
        assert str(err) == "took too long"

    def test_rate_limit_constructor_sets_category_and_message(self):
        err = ProviderError.rate_limit("slow down")
        assert err.category == "rate_limit"
        assert str(err) == "slow down"

    def test_auth_constructor_sets_category_and_message(self):
        err = ProviderError.auth("bad key")
        assert err.category == "auth"
        assert str(err) == "bad key"

    def test_backend_constructor_sets_category_and_message(self):
        err = ProviderError.backend("server exploded")
        assert err.category == "backend"
        assert str(err) == "server exploded"

    def test_is_raisable_and_catchable_as_exception(self):
        with pytest.raises(Exception):
            raise ProviderError.backend("boom")

        try:
            raise ProviderError.timeout("boom")
        except Exception as exc:
            assert isinstance(exc, ProviderError)


class TestLLMResultImmutability:
    def test_attribute_assignment_raises(self):
        result = LLMResult(
            text="hello",
            parsed=None,
            model="claude-opus-5",
            input_tokens=10,
            output_tokens=5,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.text = "goodbye"


class TestNormalize:
    def test_normalize_returns_unit_length_vector(self):
        [vec] = normalize([[3.0, 4.0]])
        length = math.sqrt(sum(c * c for c in vec))
        assert length == pytest.approx(1.0)
        assert vec[0] == pytest.approx(0.6)

    def test_normalize_zero_vector_passes_through_unchanged(self):
        [vec] = normalize([[0.0, 0.0, 0.0]])
        assert vec == [0.0, 0.0, 0.0]

    def test_normalize_preserves_order_and_count(self):
        vectors = [[1.0, 0.0], [0.0, 2.0], [0.0, 0.0], [3.0, 4.0]]
        result = normalize(vectors)
        assert len(result) == len(vectors)
        # index-aligned: zero stays zero, non-zero stay non-zero and unit length
        assert result[2] == [0.0, 0.0]
        for original, normalized in zip(vectors, result):
            if original != [0.0, 0.0]:
                length = math.sqrt(sum(c * c for c in normalized))
                assert length == pytest.approx(1.0)
