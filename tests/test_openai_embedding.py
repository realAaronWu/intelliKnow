"""Tests for `OpenAIEmbedding` — Important 4 from fix round 1: previously
untested. Every test injects a stub `client` so nothing here touches the
network.
"""

from __future__ import annotations

import httpx
import openai
import pytest

from app.providers.base import ProviderError
from app.providers.openai_embedding import OpenAIEmbedding


class _StubEmbeddingItem:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class _StubEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_StubEmbeddingItem(v) for v in vectors]


class _StubEmbeddingsResource:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._next_error: Exception | None = None
        self._next_response: _StubEmbeddingResponse | None = None

    def queue_response(self, response: _StubEmbeddingResponse) -> None:
        self._next_response = response
        self._next_error = None

    def queue_error(self, error: Exception) -> None:
        self._next_error = error
        self._next_response = None

    def create(self, *, model: str, input: list[str]):
        self.calls.append({"model": model, "input": list(input)})
        if self._next_error is not None:
            raise self._next_error
        return self._next_response


class _StubOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = _StubEmbeddingsResource()


def test_constructs_with_injected_client_no_network():
    client = _StubOpenAIClient()
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="unused",
        batch_size=64,
        dimension=1536,
        client=client,
    )

    assert provider.dimension == 1536


def test_embed_returns_unit_normalized_vectors_in_order():
    client = _StubOpenAIClient()
    client.embeddings.queue_response(_StubEmbeddingResponse([[3.0, 4.0], [0.0, 2.0]]))
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="unused",
        batch_size=64,
        dimension=2,
        client=client,
    )

    vectors = provider.embed(["alpha", "beta"])

    assert len(vectors) == 2
    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert vectors[1] == pytest.approx([0.0, 1.0])
    assert client.embeddings.calls[0]["model"] == "text-embedding-3-small"
    assert client.embeddings.calls[0]["input"] == ["alpha", "beta"]


def test_embed_batches_at_configured_size():
    client = _StubOpenAIClient()
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="unused",
        batch_size=3,
        dimension=1,
        client=client,
    )
    # Each batch call gets its own queued response sized to that batch.
    client.embeddings.queue_response(_StubEmbeddingResponse([[1.0], [1.0], [1.0]]))
    vectors_first = provider.embed(["a", "b", "c"])
    client.embeddings.queue_response(_StubEmbeddingResponse([[1.0], [1.0]]))
    vectors_second = provider.embed(["d", "e"])

    assert len(vectors_first) == 3
    assert len(vectors_second) == 2
    assert len(client.embeddings.calls) == 2
    assert len(client.embeddings.calls[0]["input"]) == 3
    assert len(client.embeddings.calls[1]["input"]) == 2


def _fake_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("POST", "https://api.openai.com/"))


def test_auth_exception_mapped_to_auth_category():
    client = _StubOpenAIClient()
    client.embeddings.queue_error(
        openai.AuthenticationError("bad key", response=_fake_response(401), body=None)
    )
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="unused",
        batch_size=64,
        dimension=2,
        client=client,
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "auth"


def test_rate_limit_exception_mapped_to_rate_limit_category():
    client = _StubOpenAIClient()
    client.embeddings.queue_error(
        openai.RateLimitError("slow down", response=_fake_response(429), body=None)
    )
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="unused",
        batch_size=64,
        dimension=2,
        client=client,
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "rate_limit"


def test_default_client_constructed_from_api_key_without_network_call():
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="sk-test",
        batch_size=64,
        dimension=1536,
    )

    assert isinstance(provider._client, openai.OpenAI)
