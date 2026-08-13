"""Tests for the retry/backoff policy — test-plan §5."""

from __future__ import annotations

import pytest

from app.providers.base import ProviderError
from app.providers.retry import with_retries


def test_success_first_try_calls_once():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    result = with_retries(fn, max_retries=3, sleep=lambda s: None)

    assert result == "ok"
    assert len(calls) == 1


def test_rate_limit_twice_then_success_calls_three_times():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ProviderError.rate_limit("rate limited")
        return "recovered"

    result = with_retries(fn, max_retries=3, sleep=lambda s: None)

    assert result == "recovered"
    assert len(calls) == 3


def test_auth_error_raises_immediately_calls_once():
    calls = []

    def fn():
        calls.append(1)
        raise ProviderError.auth("bad key")

    with pytest.raises(ProviderError) as excinfo:
        with_retries(fn, max_retries=3, sleep=lambda s: None)

    assert excinfo.value.category == "auth"
    assert len(calls) == 1


def test_backend_error_raises_immediately_calls_once():
    calls = []

    def fn():
        calls.append(1)
        raise ProviderError.backend("broken response")

    with pytest.raises(ProviderError) as excinfo:
        with_retries(fn, max_retries=3, sleep=lambda s: None)

    assert excinfo.value.category == "backend"
    assert len(calls) == 1


def test_retries_exhausted_raises_last_error_with_category_intact():
    calls = []

    def fn():
        calls.append(1)
        raise ProviderError.timeout(f"timeout #{len(calls)}")

    with pytest.raises(ProviderError) as excinfo:
        with_retries(fn, max_retries=3, sleep=lambda s: None)

    assert excinfo.value.category == "timeout"
    assert str(excinfo.value) == "timeout #4"
    assert len(calls) == 4


def test_backoff_schedule_is_exactly_half_one_two():
    sleeps = []

    def fn():
        raise ProviderError.rate_limit("still limited")

    with pytest.raises(ProviderError):
        with_retries(fn, max_retries=3, sleep=sleeps.append)

    assert sleeps == [0.5, 1.0, 2.0]
