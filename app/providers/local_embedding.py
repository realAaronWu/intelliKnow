"""`EmbeddingProvider` backed by a local `sentence-transformers` model.

The real model is loaded lazily, on first `embed()` call, and only when no
`client` was injected — so simply constructing this class (as the factory
does at startup) never touches the network or the filesystem cache, and
every automated test can inject a stub `client` instead.
"""

from __future__ import annotations

from typing import Any

from app.providers.base import normalize


class SentenceTransformerEmbedding:
    """Batches texts through a `sentence-transformers` model."""

    def __init__(
        self,
        model_name: str,
        batch_size: int,
        dimension: int | None = None,
        client: Any | None = None,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._dimension = dimension
        self._client = client
        self._model: Any | None = None

    @property
    def dimension(self) -> int:
        # Prefer the dimension declared in config — introspecting the real
        # model would force it to load (and, on first use, download) just
        # to answer a question config already knows the answer to.
        if self._dimension is None:
            self._dimension = self._load_model().get_sentence_embedding_dimension()
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._client if self._client is not None else self._load_model()

        results: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            raw_vectors = client.encode(batch)
            vectors = [[float(component) for component in vector] for vector in raw_vectors]
            results.extend(normalize(vectors))
        return results

    def _load_model(self) -> Any:
        if self._model is None:
            import sentence_transformers

            self._model = sentence_transformers.SentenceTransformer(self._model_name)
        return self._model
