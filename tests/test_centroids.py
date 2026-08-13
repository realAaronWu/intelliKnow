"""Test-plan §8a — centroid index.

Source: docs/superpowers/test-plans/04-rag-read-path-tests.md §8a

Every test pins centroid-source text to exact vectors via
`FakeEmbeddingProvider.set_vector` so cosine similarities — and therefore
softmax probabilities — are exactly known, not merely "plausible". No test
here reads or writes any document or chunk: centroids come from admin-
authored intent-space text alone, which is the whole point of 8a.2.
"""

from __future__ import annotations

import math

import pytest

from app.config import AppConfig
from app.orchestrator.centroids import CentroidIndex
from tests.doubles import FakeEmbeddingProvider

DIMENSION = 8

_HR_VEC = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_LEGAL_VEC = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_GENERAL_VEC = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_HR_VEC_V2 = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]

_QUERY_ALIGNED_WITH_HR = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _space_text(name: str, description: str, keywords: list[str]) -> str:
    """Mirrors `app/orchestrator/centroids.py::_space_text` exactly, so
    tests can pin the embedder to precisely the text the module will embed.
    """
    parts = [name, description]
    if keywords:
        parts.append(" ".join(keywords))
    return " ".join(parts)


def _cfg(*, hr_keywords: list[str] | None = None, temperature: float = 0.05, extra_spaces=None):
    spaces = [
        {
            "slug": "hr",
            "name": "HR",
            "description": "Employee policies",
            "keywords": hr_keywords if hr_keywords is not None else ["leave"],
        },
        {
            "slug": "legal",
            "name": "Legal",
            "description": "Contracts and compliance",
            "keywords": ["contract"],
        },
        {
            "slug": "general",
            "name": "General",
            "description": "Fallback",
            "keywords": [],
        },
    ]
    if extra_spaces:
        spaces = spaces + extra_spaces
    return AppConfig.model_validate(
        {
            "embedding": {"model": "fake-embed-model", "dimension": DIMENSION},
            "orchestrator": {"centroid_temperature": temperature, "fallback_space": "general"},
            "intent_spaces": spaces,
        }
    )


def _embedder_with_defaults(cfg: AppConfig) -> FakeEmbeddingProvider:
    embedder = FakeEmbeddingProvider(dimension=DIMENSION)
    hr = cfg.intent_spaces[0]
    legal = cfg.intent_spaces[1]
    general = cfg.intent_spaces[2]
    embedder.set_vector(_space_text(hr.name, hr.description, hr.keywords), _HR_VEC)
    embedder.set_vector(_space_text(legal.name, legal.description, legal.keywords), _LEGAL_VEC)
    embedder.set_vector(
        _space_text(general.name, general.description, general.keywords), _GENERAL_VEC
    )
    return embedder


# --- 8a.1 Centroid per space --------------------------------------------------


def test_8a_1_one_centroid_per_configured_space():
    cfg = _cfg()
    embedder = _embedder_with_defaults(cfg)
    index = CentroidIndex(embedder, cfg)

    probs = index.score(_QUERY_ALIGNED_WITH_HR)

    assert set(probs.keys()) == {"hr", "legal", "general"}


# --- 8a.2 Works on an empty knowledge base ------------------------------------


def test_8a_2_classifies_with_zero_documents_indexed():
    """No document or chunk table is touched anywhere in this module —
    centroids come from admin-authored space text, not from indexed
    content, so classification must work before a single document exists.
    """
    cfg = _cfg()
    embedder = _embedder_with_defaults(cfg)
    index = CentroidIndex(embedder, cfg)

    slug, confidence = index.top(_QUERY_ALIGNED_WITH_HR)

    assert slug == "hr"
    assert 0.0 <= confidence <= 1.0


# --- 8a.3 Keyword edit rebuilds ------------------------------------------------


def test_8a_3_keyword_edit_rebuilds_centroid_no_restart():
    cfg1 = _cfg(hr_keywords=["leave"])
    embedder = _embedder_with_defaults(cfg1)
    index = CentroidIndex(embedder, cfg1)

    before = index.score(_QUERY_ALIGNED_WITH_HR)["hr"]

    cfg2 = _cfg(hr_keywords=["totally-different-topic"])
    hr2 = cfg2.intent_spaces[0]
    embedder.set_vector(_space_text(hr2.name, hr2.description, hr2.keywords), _HR_VEC_V2)

    index.rebuild(cfg2)
    after = index.score(_QUERY_ALIGNED_WITH_HR)["hr"]

    assert after != before
    assert after < before  # the new centroid is orthogonal to the query


# --- 8a.4 Probabilities sum to 1 ----------------------------------------------


def test_8a_4_probabilities_sum_to_one():
    cfg = _cfg()
    embedder = _embedder_with_defaults(cfg)
    index = CentroidIndex(embedder, cfg)

    probs = index.score(_QUERY_ALIGNED_WITH_HR)

    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


# --- 8a.5 Lower temperature sharpens -------------------------------------------


def test_8a_5_lower_temperature_raises_top_probability():
    cfg_sharp = _cfg(temperature=0.01)
    cfg_soft = _cfg(temperature=1.0)
    embedder_sharp = _embedder_with_defaults(cfg_sharp)
    embedder_soft = _embedder_with_defaults(cfg_soft)

    sharp = CentroidIndex(embedder_sharp, cfg_sharp).score(_QUERY_ALIGNED_WITH_HR)
    soft = CentroidIndex(embedder_soft, cfg_soft).score(_QUERY_ALIGNED_WITH_HR)

    assert sharp["hr"] > soft["hr"]


# --- 8a.6 New space becomes a target -------------------------------------------


def test_8a_6_new_space_gets_a_centroid_and_can_be_classified_into():
    cfg1 = _cfg()
    embedder = _embedder_with_defaults(cfg1)
    index = CentroidIndex(embedder, cfg1)

    assert "finance" not in index.score(_QUERY_ALIGNED_WITH_HR)

    finance_vec = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    cfg2 = _cfg(
        extra_spaces=[
            {
                "slug": "finance",
                "name": "Finance",
                "description": "Money stuff",
                "keywords": ["invoice"],
            }
        ]
    )
    finance = cfg2.intent_spaces[3]
    embedder.set_vector(_space_text(finance.name, finance.description, finance.keywords), finance_vec)

    index.rebuild(cfg2)
    probs = index.score(finance_vec)

    assert "finance" in probs
    slug, _ = index.top(finance_vec)
    assert slug == "finance"


def test_softmax_math_is_temperature_scaled(monkeypatch):
    """Direct check against a hand-computed softmax, so the formula itself
    (not just its qualitative sharpening behaviour) is pinned down.
    """
    cfg = _cfg(temperature=0.5)
    embedder = _embedder_with_defaults(cfg)
    index = CentroidIndex(embedder, cfg)

    # Cosine similarities of the query against hr/legal/general centroids
    # are exactly 1.0, 0.0, 0.0 (orthonormal pinned vectors).
    sims = {"hr": 1.0, "legal": 0.0, "general": 0.0}
    temperature = 0.5
    expected_exp = {slug: math.exp(sim / temperature) for slug, sim in sims.items()}
    total = sum(expected_exp.values())
    expected = {slug: v / total for slug, v in expected_exp.items()}

    probs = index.score(_QUERY_ALIGNED_WITH_HR)

    for slug in expected:
        assert probs[slug] == pytest.approx(expected[slug], abs=1e-9)
