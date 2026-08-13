"""Centroid index — one vector per intent space, no documents required.

Each intent space gets a **centroid**: the embedding of its name,
description, and keywords concatenated. `CentroidIndex.score` compares a
query vector against every centroid and turns the cosine similarities into
a probability distribution with a temperature-scaled softmax — see
`openspec/changes/add-intelliknow-kms/design.md` § "Classification without
an LLM on the common path".

Centroids come from admin-authored configuration text, not from indexed
document content, which is exactly what makes classification work on a
freshly installed system with zero documents (`spec: query-orchestration`
§ "Centroids available on an empty knowledge base") — a document-derived
centroid would have nothing to average at that point.

Query vectors arrive already unit-normalized from the embedding provider
(`app.providers.base.normalize`), and centroid vectors are produced by the
same provider, so a plain dot product **is** cosine similarity — no
separate normalization step is needed here.

`rebuild()` recomputes every centroid from the current (or a newly
supplied) `AppConfig`. Nothing in this module watches `ConfigService` for
changes on its own — a caller that holds a live config (an admin
intent-space editor, wired in a later increment) calls `rebuild(new_cfg)`
after a save, which is what makes "a keyword edit moves the centroid with
no restart and no re-indexing" true without this index polling anything.
"""

from __future__ import annotations

import math

from app.config import AppConfig, IntentSpace
from app.providers.base import EmbeddingProvider


def _space_text(space: IntentSpace) -> str:
    """Text a space's centroid is embedded from: name, then description,
    then its keywords joined with spaces — keywords are appended, not
    interleaved, so admin-authored wording in the name/description always
    dominates the embedding at least as much as freeform keyword tags.
    """
    parts = [space.name, space.description]
    if space.keywords:
        parts.append(" ".join(space.keywords))
    return " ".join(parts)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _softmax(similarities: dict[str, float], temperature: float) -> dict[str, float]:
    """Temperature-scaled softmax over `similarities`, one term per slug.

    Shifted by the maximum scaled value before exponentiating (the
    standard numerically-stable formulation) — this does not change the
    resulting distribution, only avoids `OverflowError` on a similarity
    close to 1.0 divided by a very small temperature.
    """
    scaled = {slug: sim / temperature for slug, sim in similarities.items()}
    max_scaled = max(scaled.values())
    exp_values = {slug: math.exp(v - max_scaled) for slug, v in scaled.items()}
    total = sum(exp_values.values())
    return {slug: value / total for slug, value in exp_values.items()}


class CentroidIndex:
    """One centroid vector per configured intent space.

    `embedder` is whatever `EmbeddingProvider` the rest of the system uses
    for chunks and queries (`spec: query-orchestration` § "Centroids use
    the configured embedding model") — sharing it is what keeps centroid
    similarities and query embeddings on the same scale.
    """

    def __init__(self, embedder: EmbeddingProvider, cfg: AppConfig) -> None:
        self._embedder = embedder
        self._cfg = cfg
        self._centroids: dict[str, list[float]] = {}
        self.rebuild()

    def rebuild(self, cfg: AppConfig | None = None) -> None:
        """Recompute every space's centroid.

        `cfg`, when supplied, replaces the config this index was built
        from (and every later call) — the seam a config-change caller
        uses. Omitted, this simply re-embeds the spaces from whichever
        config is already held, which is a no-op unless that config
        object's content differs from what was last embedded.
        """
        if cfg is not None:
            self._cfg = cfg
        spaces = self._cfg.intent_spaces
        texts = [_space_text(space) for space in spaces]
        vectors = self._embedder.embed(texts) if texts else []
        self._centroids = {space.slug: vector for space, vector in zip(spaces, vectors)}

    def score(self, query_vector: list[float]) -> dict[str, float]:
        """Slug -> probability, a temperature-scaled softmax over cosine
        similarity to every centroid. Always sums to 1 across every
        configured space.
        """
        temperature = self._cfg.orchestrator.centroid_temperature
        similarities = {
            slug: _dot(query_vector, centroid) for slug, centroid in self._centroids.items()
        }
        return _softmax(similarities, temperature)

    def top(self, query_vector: list[float]) -> tuple[str, float]:
        """The highest-probability slug and its probability."""
        probs = self.score(query_vector)
        return max(probs.items(), key=lambda kv: kv[1])
