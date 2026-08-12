"""Tests for `OpenAIEmbedding` — Important 4 from fix round 1 (construction /
happy path / exception mapping), plus fix round 1 addendum (Critical 1
applies here too: `OpenAIEmbedding` reaches the network, so its retry must
go through `with_retries`, not the SDK). Every test injects a stub `client`
so nothing here touches the network or blocks on real time.
"""

from __future__ import annotations

from collections import deque

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
    """Records every call's kwargs; returns/raises whatever is queued, in order."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._queue: deque[tuple[str, object]] = deque()

    def queue_response(self, response: _StubEmbeddingResponse) -> None:
        self._queue.append(("response", response))

    def queue_error(self, error: Exception) -> None:
        self._queue.append(("error", error))

    def create(self, *, model: str, input: list[str]):
        self.calls.append({"model": model, "input": list(input)})
        assert self._queue, (
            "_StubEmbeddingsResource.create() called but nothing was queued."
        )
        kind, item = self._queue.popleft()
        if kind == "error":
            raise item
        return item


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


def test_auth_exception_mapped_to_auth_category_and_not_retried():
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
        max_retries=2,
        sleep=lambda seconds: None,
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "auth"
    assert len(client.embeddings.calls) == 1


def test_backend_exception_mapped_to_backend_category_and_not_retried():
    client = _StubOpenAIClient()
    client.embeddings.queue_error(
        openai.InternalServerError("oops", response=_fake_response(500), body=None)
    )
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="unused",
        batch_size=64,
        dimension=2,
        client=client,
        max_retries=2,
        sleep=lambda seconds: None,
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "backend"
    assert len(client.embeddings.calls) == 1


def test_rate_limit_exception_mapped_to_rate_limit_category():
    # rate_limit is retryable at the app level; max_retries=0 isolates the
    # category-mapping assertion from retry-count behaviour, which is
    # covered separately by test_transient_failure_retried_by_app_level_policy_not_the_sdk.
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
        max_retries=0,
        sleep=lambda seconds: None,
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.embed(["alpha"])

    assert excinfo.value.category == "rate_limit"
    assert len(client.embeddings.calls) == 1


def test_transient_failure_retried_by_app_level_policy_not_the_sdk():
    """Critical 1 applies to `OpenAIEmbedding` too — it reaches the network.

    Proven the same way as the LLM providers: the exact 0.5/1.0 backoff
    schedule shows up on the injected `sleep` (the SDK's own retry would
    sleep for real and follow no fixed schedule), and the call only
    succeeds on the third attempt.
    """
    sleeps: list[float] = []
    client = _StubOpenAIClient()
    client.embeddings.queue_error(
        openai.RateLimitError("slow down", response=_fake_response(429), body=None)
    )
    client.embeddings.queue_error(
        openai.RateLimitError("slow down", response=_fake_response(429), body=None)
    )
    client.embeddings.queue_response(_StubEmbeddingResponse([[3.0, 4.0]]))
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="unused",
        batch_size=64,
        dimension=2,
        client=client,
        max_retries=2,
        sleep=sleeps.append,
    )

    vectors = provider.embed(["alpha"])

    assert vectors == [pytest.approx([0.6, 0.8])]
    assert len(client.embeddings.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_default_client_constructed_from_api_key_without_network_call():
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="sk-test",
        batch_size=64,
        dimension=1536,
    )

    assert isinstance(provider._client, openai.OpenAI)


def test_default_client_disables_sdk_level_retries():
    """Critical 1: the SDK client must be built with max_retries=0 so the
    app-level `with_retries` policy is the only thing that ever retries.
    """
    provider = OpenAIEmbedding(
        model_name="text-embedding-3-small",
        api_key="sk-test",
        batch_size=64,
        dimension=1536,
        max_retries=5,
    )

    assert provider._client.max_retries == 0
