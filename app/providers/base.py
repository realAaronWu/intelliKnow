"""Provider protocols and error type shared by every AI backend.

Every later component that calls an LLM or embedding backend does so through
`LLMProvider` / `EmbeddingProvider`. Concrete providers (Anthropic, OpenAI,
local) and their deterministic test doubles (`tests/doubles.py`) both
implement these protocols, so callers never need to know which one they hold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

ErrorCategory = Literal["timeout", "rate_limit", "auth", "backend"]


@dataclass(frozen=True)
class LLMResult:
    """Immutable result of an `LLMProvider.complete` call."""

    text: str
    parsed: dict | None
    model: str
    input_tokens: int
    output_tokens: int


class ProviderError(Exception):
    """Normalized error raised by any `LLMProvider` or `EmbeddingProvider`.

    Concrete providers map SDK-specific exceptions onto one of the four
    categories below via the named constructors, so callers (notably the
    retry policy) never need to know which backend raised.
    """

    def __init__(self, message: str, category: ErrorCategory) -> None:
        super().__init__(message)
        self.category: ErrorCategory = category

    @classmethod
    def timeout(cls, message: str) -> "ProviderError":
        return cls(message, "timeout")

    @classmethod
    def rate_limit(cls, message: str) -> "ProviderError":
        return cls(message, "rate_limit")

    @classmethod
    def auth(cls, message: str) -> "ProviderError":
        return cls(message, "auth")

    @classmethod
    def backend(cls, message: str) -> "ProviderError":
        return cls(message, "backend")


# On thinking-enabled models — including `claude-opus-5`, which the shipped
# config uses and where thinking is on by default because we deliberately
# omit the `thinking` parameter — this budget caps thinking *plus* the
# visible response text combined. 1024 was small enough for a routine answer
# to be cut off mid-generation, which providers now surface as an explicit
# truncation error rather than a partial result.
DEFAULT_MAX_TOKENS = 4096


class LLMProvider(Protocol):
    """A backend capable of chat-style completion, optionally schema-guided."""

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult: ...


class EmbeddingProvider(Protocol):
    """A backend capable of turning text into fixed-dimension vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimension(self) -> int: ...


def normalize(vectors: list[list[float]]) -> list[list[float]]:
    """Return unit-length copies of `vectors`, index-aligned with the input.

    A zero vector has no direction to normalize onto, so it passes through
    unchanged rather than dividing by zero.
    """
    result: list[list[float]] = []
    for vector in vectors:
        length = math.sqrt(sum(component * component for component in vector))
        if length == 0.0:
            result.append(list(vector))
        else:
            result.append([component / length for component in vector])
    return result
