"""Config-driven construction of `LLMProvider` / `EmbeddingProvider`.

Every calling component asks for a provider through `build_llm_provider` /
`build_embedding_provider` rather than importing a concrete class directly,
so the backend named in `config.yaml` is the only thing that decides which
implementation — and which credentials — get used.
"""

from __future__ import annotations

import os
from typing import Literal, Mapping

from app.config import AppConfig
from app.providers.anthropic_llm import AnthropicLLM
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.local_embedding import SentenceTransformerEmbedding
from app.providers.local_llm import LocalLLM
from app.providers.openai_embedding import OpenAIEmbedding
from app.providers.openai_llm import OpenAILLM

_SUPPORTED_LLM_PROVIDERS = ("anthropic", "openai", "local")
_SUPPORTED_EMBEDDING_PROVIDERS = ("local", "openai")


def _require_key(env: Mapping[str, str], var_name: str) -> str:
    value = env.get(var_name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {var_name}")
    return value


def build_llm_provider(
    cfg: AppConfig,
    *,
    role: Literal["classify", "generate"] = "generate",
    env: Mapping[str, str] | None = None,
) -> LLMProvider:
    """Build the `LLMProvider` named by `cfg.llm.provider`.

    `role` selects which of `model_classify` / `model_generate` the returned
    provider is configured with — the same config yields two differently
    modelled providers depending on which role calls this.
    """
    env = env if env is not None else os.environ
    model = cfg.llm.model_classify if role == "classify" else cfg.llm.model_generate
    provider_name = cfg.llm.provider

    if provider_name == "anthropic":
        api_key = _require_key(env, "ANTHROPIC_API_KEY")
        return AnthropicLLM(
            model=model,
            api_key=api_key,
            timeout_seconds=cfg.llm.timeout_seconds,
            max_retries=cfg.llm.max_retries,
            effort=cfg.llm.effort,
        )
    if provider_name == "openai":
        api_key = _require_key(env, "OPENAI_API_KEY")
        return OpenAILLM(
            model=model,
            api_key=api_key,
            timeout_seconds=cfg.llm.timeout_seconds,
            max_retries=cfg.llm.max_retries,
        )
    if provider_name == "local":
        return LocalLLM(
            model=model,
            api_key=env.get("LOCAL_LLM_API_KEY", ""),
            timeout_seconds=cfg.llm.timeout_seconds,
            max_retries=cfg.llm.max_retries,
            env=env,
        )

    raise RuntimeError(
        f"Unknown llm.provider {provider_name!r}; supported values: "
        f"{', '.join(_SUPPORTED_LLM_PROVIDERS)}"
    )


def build_embedding_provider(
    cfg: AppConfig,
    env: Mapping[str, str] | None = None,
) -> EmbeddingProvider:
    """Build the `EmbeddingProvider` named by `cfg.embedding.provider`."""
    env = env if env is not None else os.environ
    provider_name = cfg.embedding.provider

    if provider_name == "local":
        return SentenceTransformerEmbedding(
            model_name=cfg.embedding.model,
            batch_size=cfg.embedding.batch_size,
            dimension=cfg.embedding.dimension,
        )
    if provider_name == "openai":
        api_key = _require_key(env, "OPENAI_API_KEY")
        return OpenAIEmbedding(
            model_name=cfg.embedding.model,
            api_key=api_key,
            batch_size=cfg.embedding.batch_size,
            timeout_seconds=cfg.embedding.timeout_seconds,
            max_retries=cfg.embedding.max_retries,
            dimension=cfg.embedding.dimension,
        )

    raise RuntimeError(
        f"Unknown embedding.provider {provider_name!r}; supported values: "
        f"{', '.join(_SUPPORTED_EMBEDDING_PROVIDERS)}"
    )
