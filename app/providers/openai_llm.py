"""`LLMProvider` backed by OpenAI's Chat Completions API.

`LocalLLM` (`app/providers/local_llm.py`) talks the same wire protocol
against an OpenAI-API-compatible local server, so the request/response
handling here (`chat_complete`, `map_exception`) is shared rather than
duplicated.
"""

from __future__ import annotations

import json
from typing import Any

import openai

from app.providers.base import LLMResult, ProviderError


class OpenAILLM:
    """Chat-style completion, optionally schema-guided, via OpenAI."""

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_seconds: int,
        max_retries: int,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._client = client if client is not None else openai.OpenAI(
            api_key=api_key,
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


def chat_complete(
    client: Any,
    model: str,
    *,
    system: str,
    user: str,
    schema: dict | None,
    max_tokens: int,
) -> LLMResult:
    """Shared request/response handling for any OpenAI-compatible client."""
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema, "strict": True},
        }

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise map_exception(exc) from exc

    text = response.choices[0].message.content
    parsed: dict | None = None
    if schema is not None:
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderError.backend(
                f"structured response did not parse as JSON: {text!r}"
            ) from exc

    return LLMResult(
        text=text,
        parsed=parsed,
        model=response.model,
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
    )


def map_exception(exc: Exception) -> ProviderError:
    if isinstance(exc, openai.AuthenticationError):
        return ProviderError.auth(str(exc))
    if isinstance(exc, openai.RateLimitError):
        return ProviderError.rate_limit(str(exc))
    if isinstance(exc, openai.APITimeoutError):
        return ProviderError.timeout(str(exc))
    if isinstance(exc, openai.APIError):
        return ProviderError.backend(str(exc))
    return ProviderError.backend(str(exc))
