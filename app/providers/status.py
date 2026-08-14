"""Read-only reporting on the active providers and on secret presence.

Serves two spec requirements that both amount to "tell the console what is
configured without telling it any secret":

- `spec: ai-provider` § "Provider status reporting" — the active provider
  names, both models, the embedding model, and the embedding dimension,
  with no credential value in the response.
- `spec: configuration` § "Effective configuration is readable" — indicate
  whether each required secret is set, and name the environment variable to
  set when one is missing.

Which provider needs which variable is *not* decided here: both functions
read `app/providers/factory.py`'s tables, so the console can never report a
credential as satisfied that the factory would then reject.
"""

from __future__ import annotations

from typing import Mapping

from app.config import AppConfig
from app.providers.factory import (
    EMBEDDING_API_KEY_ENV_VARS,
    LLM_API_KEY_ENV_VARS,
)

# Operator secrets that every deployment needs regardless of which AI
# backends are selected. `spec: configuration` § "Secrets separated from
# configuration" names these alongside the provider API keys.
_ALWAYS_REQUIRED_SECRETS = ("ADMIN_PASSWORD",)


def secret_status(cfg: AppConfig, env: Mapping[str, str]) -> dict[str, bool]:
    """Report which required environment variables are set.

    Keys are the variable names themselves, which is what lets the console
    name the variable an operator still has to set. Values are presence
    flags only — never the values. An empty string counts as unset, matching
    the factory's `_require_key`.
    """
    required: list[str] = []

    llm_var = LLM_API_KEY_ENV_VARS.get(cfg.llm.provider)
    if llm_var is not None:
        required.append(llm_var)

    embedding_var = EMBEDDING_API_KEY_ENV_VARS.get(cfg.embedding.provider)
    if embedding_var is not None:
        required.append(embedding_var)

    required.extend(_ALWAYS_REQUIRED_SECRETS)

    # dict preserves insertion order and de-duplicates the shared
    # OPENAI_API_KEY when both providers are OpenAI.
    return {name: bool(env.get(name)) for name in dict.fromkeys(required)}


def provider_status(cfg: AppConfig, env: Mapping[str, str]) -> dict:
    """Report the active providers, models, and embedding dimension.

    Contains no credential value; secret presence is reported as booleans
    under `"secrets"` by `secret_status`.
    """
    return {
        "llm": {
            "provider": cfg.llm.provider,
            "model_classify": cfg.llm.model_classify,
            "model_generate": cfg.llm.model_generate,
            "effort": cfg.llm.effort,
        },
        "embedding": {
            "provider": cfg.embedding.provider,
            "model": cfg.embedding.model,
            "dimension": cfg.embedding.dimension,
        },
        "secrets": secret_status(cfg, env),
    }
