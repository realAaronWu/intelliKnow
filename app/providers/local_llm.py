"""`LLMProvider` for a locally hosted, OpenAI-API-compatible backend.

Local model servers (Ollama, vLLM, LM Studio, ...) commonly expose the same
`/v1/chat/completions` wire protocol as OpenAI, so request/response handling
is shared with `OpenAILLM` via `openai_llm.chat_complete`. Only default
client construction differs: no real API key is required, and the base URL
points at the local server rather than `api.openai.com`.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

import openai

from app.providers.base import LLMResult
from app.providers.openai_llm import chat_complete

_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class LocalLLM:
    """Chat-style completion, optionally schema-guided, via a local server."""

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_seconds: int,
        max_retries: int,
        client: Any | None = None,
        base_url: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._model = model
        if client is not None:
            self._client = client
        else:
            env = env if env is not None else os.environ
            self._client = openai.OpenAI(
                api_key=api_key or "not-needed",
                base_url=base_url or env.get("LOCAL_LLM_BASE_URL", _DEFAULT_BASE_URL),
                timeout=float(timeout_seconds),
                max_retries=max_retries,
            )

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = 1024,
    ) -> LLMResult:
        return chat_complete(
            self._client,
            self._model,
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
        )
