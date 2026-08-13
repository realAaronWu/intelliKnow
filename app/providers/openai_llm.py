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

from app.providers.base import DEFAULT_MAX_TOKENS, LLMResult, ProviderError
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
        max_tokens: int = DEFAULT_MAX_TOKENS,
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
        # `max_tokens` is deprecated in the installed SDK and rejected
        # outright by reasoning-model endpoints.
        "max_completion_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if schema is not None:
        # `strict` is deliberately not sent. Strict mode requires every
        # object in the schema to carry `additionalProperties: false` and to
        # list all of its properties in `required`; callers supply ordinary
        # schemas, and rewriting one to satisfy those rules would silently
        # turn its optional fields into required ones. Conformance is
        # enforced client-side by `app/providers/schema_validation.py`, which
        # applies uniformly across the Anthropic, OpenAI, and local backends.
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema},
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
        # Before anything reads the body — see the equivalent call in
        # `app/providers/anthropic_llm.py`.
        _check_finish_reason(response)
        text = _extract_text(response)

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


def _check_finish_reason(response: Any) -> None:
    """Reject responses whose finish reason means the body is unusable.

    `length` is the OpenAI spelling of Anthropic's `max_tokens` stop reason;
    `refusal` / `content_filter` are its policy declines. Neither is
    retryable — see `app/providers/anthropic_llm._check_stop_reason`.
    """
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)

    if finish_reason == "length":
        raise ProviderError.backend(
            "response was truncated: the model stopped at the "
            "max_completion_tokens budget before finishing, so the partial "
            "text is not an answer"
        )

    refusal = getattr(getattr(choice, "message", None), "refusal", None)
    if refusal or finish_reason == "content_filter":
        detail = refusal or "content filtered"
        raise ProviderError.backend(
            f"the model refused to produce a response: {detail}"
        )


def _extract_text(response: Any) -> str:
    text = response.choices[0].message.content
    if text is None:
        raise ProviderError.backend("response contained no message content")
    return text


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
