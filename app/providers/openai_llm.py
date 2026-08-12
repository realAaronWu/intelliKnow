"""`LLMProvider` backed by OpenAI's Chat Completions API.

`LocalLLM` (`app/providers/local_llm.py`) talks the same wire protocol
against an OpenAI-API-compatible local server, so the request/response
handling here (`chat_complete`, `map_exception`) is shared rather than
duplicated.

Two independent retry loops are in play, and they must not be conflated —
see `app/providers/anthropic_llm.py`'s module docstring for the full
rationale, which applies identically here:

- **Transient-failure retry** goes through `app.providers.retry.with_retries`
  around the raw `chat.completions.create` call; the SDK client is built
  with `max_retries=0` so the SDK's own retry never runs underneath it.
- **Schema-validation retry**: a response that fails to parse as JSON, or
  parses but does not conform to the requested schema, is retried once
  before raising `ProviderError.backend` naming the violated schema. See
  `app/providers/schema_validation.py`.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import openai

from app.providers.base import LLMResult, ProviderError
from app.providers.retry import with_retries
from app.providers.schema_validation import (
    SchemaViolation,
    describe_schema,
    parse_and_validate,
)

_MAX_SCHEMA_ATTEMPTS = 2


class OpenAILLM:
    """Chat-style completion, optionally schema-guided, via OpenAI."""

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_seconds: int,
        max_retries: int,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._sleep = sleep
        self._client = client if client is not None else openai.OpenAI(
            api_key=api_key,
            timeout=float(timeout_seconds),
            # The app-level `with_retries` policy is the only retry layer.
            max_retries=0,
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
            max_retries=self._max_retries,
            sleep=self._sleep,
        )


def chat_complete(
    client: Any,
    model: str,
    *,
    system: str,
    user: str,
    schema: dict | None,
    max_tokens: int,
    max_retries: int,
    sleep: Callable[[float], None],
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

    def _call() -> Any:
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise map_exception(exc) from exc

    schema_attempts = _MAX_SCHEMA_ATTEMPTS if schema is not None else 1
    response: Any = None
    text = ""
    parsed: dict | None = None

    for attempt in range(1, schema_attempts + 1):
        response = with_retries(_call, max_retries=max_retries, sleep=sleep)
        text = response.choices[0].message.content

        if schema is None:
            break

        try:
            parsed = parse_and_validate(text, schema)
            break
        except SchemaViolation as violation:
            if attempt == schema_attempts:
                raise ProviderError.backend(
                    f"structured response violated schema "
                    f"{describe_schema(schema)} after retry: {violation}: {text!r}"
                ) from violation
            continue

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
