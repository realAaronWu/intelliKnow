"""Tests for the config-driven provider factory — test-plan §6, rows 6.9-6.13.

`build_llm_provider` / `build_embedding_provider` are the only supported way
callers obtain a provider; these tests exercise the routing, credential
checks, and batching behaviour without ever touching the network — real SDK
clients are only constructed by these factories when no test replaces the
underlying transport, and none of these tests calls `.complete()` /
`.embed()` on a provider built with a real (unstubbed) client.
"""

from __future__ import annotations

import math

import pytest

from app.config import AppConfig
from app.providers.anthropic_llm import AnthropicLLM
from app.providers.factory import build_embedding_provider, build_llm_provider
from app.providers.local_embedding import SentenceTransformerEmbedding
from app.providers.local_llm import LocalLLM
from app.providers.openai_embedding import OpenAIEmbedding
from app.providers.openai_llm import OpenAILLM


def test_role_classify_vs_generate_selects_the_right_model():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"
    cfg.llm.model_classify = "claude-opus-5-classify"
    cfg.llm.model_generate = "claude-opus-5-generate"
    env = {"ANTHROPIC_API_KEY": "test-key"}

    classifier = build_llm_provider(cfg, role="classify", env=env)
    generator = build_llm_provider(cfg, role="generate", env=env)

    assert isinstance(classifier, AnthropicLLM)
    assert isinstance(generator, AnthropicLLM)
    assert classifier._model == "claude-opus-5-classify"
    assert generator._model == "claude-opus-5-generate"


def test_missing_api_key_raises_runtime_error_naming_the_env_var():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_llm_provider(cfg, env={})


def test_openai_provider_missing_key_names_openai_env_var():
    cfg = AppConfig()
    cfg.llm.provider = "openai"

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_llm_provider(cfg, env={})


def test_unknown_provider_name_lists_supported_values():
    cfg = AppConfig()
    cfg.llm.provider = "mistral"  # bypasses the Literal check via direct assignment

    with pytest.raises(RuntimeError) as excinfo:
        build_llm_provider(cfg, env={"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "x"})

    message = str(excinfo.value)
    assert "mistral" in message
    assert "anthropic" in message
    assert "openai" in message
    assert "local" in message


def test_openai_provider_builds_openai_llm():
    cfg = AppConfig()
    cfg.llm.provider = "openai"

    provider = build_llm_provider(cfg, env={"OPENAI_API_KEY": "test-key"})

    assert isinstance(provider, OpenAILLM)


def test_llm_effort_comes_from_config_not_from_the_model_name():
    """The effort level used to be inferred from the substring "opus-5" in
    the model name, so it could not be tuned from config.yaml and silently
    changed meaning when the model was swapped.
    """
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"
    cfg.llm.effort = "high"
    cfg.llm.model_generate = "claude-sonnet-4"

    provider = build_llm_provider(cfg, env={"ANTHROPIC_API_KEY": "test-key"})

    assert provider._effort == "high"


def test_embedding_timeout_and_retries_come_from_config():
    cfg = AppConfig()
    cfg.embedding.provider = "openai"
    cfg.embedding.timeout_seconds = 45
    cfg.embedding.max_retries = 7

    provider = build_embedding_provider(cfg, env={"OPENAI_API_KEY": "test-key"})

    assert isinstance(provider, OpenAIEmbedding)
    assert provider._max_retries == 7
    assert provider._client.timeout == 45.0


def test_embedding_missing_api_key_raises_runtime_error_naming_the_env_var():
    cfg = AppConfig()
    cfg.embedding.provider = "openai"

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_embedding_provider(cfg, env={})


def test_unknown_embedding_provider_name_lists_supported_values():
    cfg = AppConfig()
    cfg.embedding.provider = "cohere"  # bypasses the Literal check via direct assignment

    with pytest.raises(RuntimeError) as excinfo:
        build_embedding_provider(cfg, env={"OPENAI_API_KEY": "x"})

    message = str(excinfo.value)
    assert "cohere" in message
    assert "local" in message
    assert "openai" in message


def test_local_embedding_with_empty_env_constructs_without_a_key():
    cfg = AppConfig()
    assert cfg.embedding.provider == "local"

    provider = build_embedding_provider(cfg, env={})

    assert isinstance(provider, SentenceTransformerEmbedding)
    assert provider.dimension == cfg.embedding.dimension


def test_local_llm_uses_configured_base_url_no_network_call():
    """`LLMConfig.base_url` must reach `LocalLLM`'s client unmodified, and
    constructing it must not require a running server — connection happens
    on first call, not at construction.
    """
    cfg = AppConfig()
    cfg.llm.provider = "local"
    cfg.llm.base_url = "http://localhost:9999/v1"

    provider = build_llm_provider(cfg, env={})

    assert isinstance(provider, LocalLLM)
    assert str(provider._client.base_url) == "http://localhost:9999/v1/"


def test_local_llm_default_base_url_targets_ollama():
    cfg = AppConfig()
    cfg.llm.provider = "local"

    provider = build_llm_provider(cfg, env={})

    assert str(provider._client.base_url) == "http://localhost:11434/v1/"


def test_local_llm_requires_no_api_key():
    cfg = AppConfig()
    cfg.llm.provider = "local"

    # Must not raise even though env carries no credentials at all.
    provider = build_llm_provider(cfg, env={})

    assert isinstance(provider, LocalLLM)


def test_embedding_batching_issues_ceil_n_over_batch_size_model_calls():
    calls: list[list[str]] = []

    class _StubEncoder:
        def encode(self, texts):
            calls.append(list(texts))
            return [[0.1, 0.2] for _ in texts]

    batch_size = 32
    provider = SentenceTransformerEmbedding(
        model_name="all-MiniLM-L6-v2",
        batch_size=batch_size,
        dimension=2,
        client=_StubEncoder(),
    )

    texts = [f"doc {i}" for i in range(200)]
    vectors = provider.embed(texts)

    assert len(vectors) == 200
    assert len(calls) == math.ceil(200 / batch_size)
