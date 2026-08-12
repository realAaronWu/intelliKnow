"""`EmbeddingProvider` backed by the OpenAI Embeddings API.

Unlike `SentenceTransformerEmbedding`, this provider reaches a remote API on
every `embed()` call, so it carries the same transient-failure retry
requirement as the LLM providers (see `app/providers/anthropic_llm.py`'s
module docstring for the full rationale): the raw SDK call is wrapped in
`app.providers.retry.with_retries`, and the default SDK client is built with
`max_retries=0` so the SDK's own retry never runs underneath the app-level
policy.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import openai

from app.providers.base import ProviderError, check_dimensions, normalize
from app.providers.openai_llm import map_exception
from app.providers.retry import with_retries


class OpenAIEmbedding:
    """Batches texts through an OpenAI embedding model."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        batch_size: int,
        dimension: int | None = None,
        client: Any | None = None,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._dimension = dimension
        self._max_retries = max_retries
        self._sleep = sleep
        self._client = client if client is not None else openai.OpenAI(
            api_key=api_key,
            timeout=float(timeout_seconds),
            # The app-level `with_retries` policy is the only retry layer.
            max_retries=0,
        )

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise ProviderError.backend(
                "OpenAIEmbedding dimension was not configured and cannot be "
                "determined without an embed() call"
            )
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]

            def _call(batch: list[str] = batch) -> Any:
                kwargs: dict[str, Any] = {"model": self._model_name, "input": batch}
                # Ask the API for the configured size rather than accepting
                # whatever the model natively returns.
                if self._dimension is not None:
                    kwargs["dimensions"] = self._dimension
                try:
                    return self._client.embeddings.create(**kwargs)
                except Exception as exc:
                    raise map_exception(exc) from exc

            response = with_retries(_call, max_retries=self._max_retries, sleep=self._sleep)
            vectors = [list(item.embedding) for item in response.data]
            check_dimensions(vectors, self.dimension, "OpenAIEmbedding")
            results.extend(normalize(vectors))
        return results
