"""Deterministic test doubles for `LLMProvider` and `EmbeddingProvider`.

Every later increment's tests import these doubles instead of talking to a
real backend. Their determinism — same input always yields the same output,
every call is recorded verbatim, nothing is silently defaulted — is what
makes score assertions in downstream test plans trustworthy.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque

from app.providers.base import DEFAULT_MAX_TOKENS, LLMResult, ProviderError, normalize

_FAKE_MODEL = "fake-llm-model"


class _QueuedResponse:
    __slots__ = ("text", "parsed")

    def __init__(self, text: str, parsed: dict | None) -> None:
        self.text = text
        self.parsed = parsed


class _QueuedFailure:
    __slots__ = ("error",)

    def __init__(self, error: ProviderError) -> None:
        self.error = error


class FakeLLMProvider:
    """Queue-driven `LLMProvider` double.

    Responses queued via `expect_text` / `expect_schema` / `fail_next` are
    returned (or raised) strictly in the order they were queued, regardless
    of which method queued them — so `fail_next` followed by `expect_text`
    means "fail once, then recover," letting retry behaviour be tested.
    """

    def __init__(self) -> None:
        self._queue: deque[_QueuedResponse | _QueuedFailure] = deque()
        self.calls: list[dict] = []

    def expect_text(self, text: str) -> None:
        self._queue.append(_QueuedResponse(text=text, parsed=None))

    def expect_schema(self, schema_response: dict) -> None:
        self._queue.append(
            _QueuedResponse(text=json.dumps(schema_response), parsed=schema_response)
        )

    def fail_next(self, error: ProviderError) -> None:
        self._queue.append(_QueuedFailure(error=error))

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema": schema,
                "max_tokens": max_tokens,
            }
        )

        assert self._queue, (
            "FakeLLMProvider.complete() called but no response was queued — "
            "call expect_text(...), expect_schema(...), or fail_next(...) "
            "before exercising code that calls complete()."
        )

        item = self._queue.popleft()
        if isinstance(item, _QueuedFailure):
            raise item.error

        return LLMResult(
            text=item.text,
            parsed=item.parsed,
            model=_FAKE_MODEL,
            input_tokens=len(system) + len(user),
            output_tokens=len(item.text),
        )


def _hash_vector(text: str, dimension: int) -> list[float]:
    """Deterministically derive a `dimension`-length vector from `text`.

    Same text always produces the same raw vector (before normalization);
    different texts almost certainly differ. Built from repeated SHA-256
    hashing so it can fill any requested dimension.
    """
    values: list[float] = []
    counter = 0
    seed = text.encode("utf-8")
    while len(values) < dimension:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for i in range(0, len(block), 4):
            if len(values) >= dimension:
                break
            as_int = int.from_bytes(block[i : i + 4], "big")
            values.append((as_int / 0xFFFFFFFF) * 2.0 - 1.0)
        counter += 1
    return values


class FakeEmbeddingProvider:
    """Hash-derived `EmbeddingProvider` double.

    Vectors are derived from a hash of the input text, so the same text
    always yields the same unit-norm vector and different texts yield
    different vectors — no queueing required. `set_vector` overrides that
    derivation for a specific text, returning the pinned vector verbatim
    (not re-normalized), so a test can place two chunks at a chosen
    similarity.
    """

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension
        self._pinned: dict[str, list[float]] = {}
        self.calls: list[list[str]] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def set_vector(self, text: str, vector: list[float]) -> None:
        self._pinned[text] = list(vector)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))

        result: list[list[float]] = []
        for text in texts:
            if text in self._pinned:
                result.append(list(self._pinned[text]))
                continue
            raw = _hash_vector(text, self._dimension)
            [unit] = normalize([raw])
            result.append(unit)
        return result
