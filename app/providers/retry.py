"""Retry policy shared by every `LLMProvider` / `EmbeddingProvider` call site.

Only transient failures — `timeout` and `rate_limit` — are worth retrying.
`auth` and `backend` errors are deterministic: a bad API key or a malformed
request will fail identically on every attempt, so retrying them just burns
the caller's latency budget for a guaranteed failure.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from app.providers.base import ProviderError

T = TypeVar("T")

_RETRYABLE_CATEGORIES = {"timeout", "rate_limit"}

# Exponential backoff, starting at 0.5s and doubling on each subsequent
# retry: 0.5, 1.0, 2.0, 4.0, ...
_INITIAL_BACKOFF_SECONDS = 0.5


def with_retries(
    fn: Callable[[], T],
    *,
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn`, retrying transient `ProviderError`s with backoff.

    `max_retries` is the number of *additional* attempts after the first,
    so the function is called at most `max_retries + 1` times. Non-transient
    errors (`auth`, `backend`) propagate immediately without a retry.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except ProviderError as error:
            if error.category not in _RETRYABLE_CATEGORIES:
                raise
            if attempt >= max_retries:
                raise
            delay = _INITIAL_BACKOFF_SECONDS * (2**attempt)
            sleep(delay)
            attempt += 1
