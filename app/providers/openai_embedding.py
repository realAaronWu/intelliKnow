"""`EmbeddingProvider` backed by the OpenAI Embeddings API."""

from __future__ import annotations

from typing import Any

import openai

from app.providers.base import ProviderError, normalize
from app.providers.openai_llm import map_exception


class OpenAIEmbedding:
    """Batches texts through an OpenAI embedding model."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        batch_size: int,
        dimension: int | None = None,
        client: Any | None = None,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._dimension = dimension
        self._client = client if client is not None else openai.OpenAI(api_key=api_key)

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
            try:
                response = self._client.embeddings.create(model=self._model_name, input=batch)
            except Exception as exc:
                raise map_exception(exc) from exc
            vectors = [list(item.embedding) for item in response.data]
            results.extend(normalize(vectors))
        return results
