"""Tests for `SentenceTransformerEmbedding`.

Every test injects a stub `client`, so no test here loads a real model or
touches the filesystem cache.
"""

from __future__ import annotations

import math

import pytest

from app.providers.base import ProviderError
from app.providers.local_embedding import SentenceTransformerEmbedding


class _StubEncoder:
    """Returns whatever it is told to, and records what it was asked to encode."""

    def __init__(self, vectors_per_text: list[float] | None = None) -> None:
        self._vector = vectors_per_text if vectors_per_text is not None else [3.0, 4.0]
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [list(self._vector) for _ in texts]


class _RaisingEncoder:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def encode(self, texts):
        raise self._error


def _provider(client, dimension: int = 2) -> SentenceTransformerEmbedding:
    return SentenceTransformerEmbedding(
        model_name="all-MiniLM-L6-v2",
        batch_size=64,
        dimension=dimension,
        client=client,
    )


def test_embed_returns_unit_normalized_vectors():
    provider = _provider(_StubEncoder([3.0, 4.0]))

    [vector] = provider.embed(["alpha"])

    assert vector == pytest.approx([0.6, 0.8])
    assert math.sqrt(sum(c * c for c in vector)) == pytest.approx(1.0)


def test_vector_of_the_wrong_length_raises_backend_error():
    """spec: ai-provider § "Reported dimension matches produced vectors".

    Reporting `dimension` straight from config while returning vectors of
    some other length would silently corrupt the FAISS index built in
    increment 03 — e.g. switching provider to `openai` while `dimension`
    stays 384 yields 1536-length vectors.
    """
    provider = _provider(_StubEncoder([1.0, 2.0, 3.0]), dimension=2)

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "backend"
    message = str(excinfo.value)
    assert "2" in message and "3" in message


def test_backend_exception_is_mapped_to_provider_error():
    """spec: ai-provider § "Timeout, retry, and error normalization" requires
    *every* backend failure to become a `ProviderError`. Raw
    sentence_transformers / torch exceptions used to escape unmapped.
    """
    provider = _provider(_RaisingEncoder(RuntimeError("CUDA out of memory")))

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "backend"
    assert "CUDA out of memory" in str(excinfo.value)


def test_provider_error_from_the_backend_is_not_double_wrapped():
    provider = _provider(_RaisingEncoder(ProviderError.timeout("model load timed out")))

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "timeout"


def test_batching_preserves_order_and_count():
    encoder = _StubEncoder([1.0, 0.0])
    provider = SentenceTransformerEmbedding(
        model_name="all-MiniLM-L6-v2",
        batch_size=2,
        dimension=2,
        client=encoder,
    )

    vectors = provider.embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert encoder.calls == [["a", "b"], ["c"]]
