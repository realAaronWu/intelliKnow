"""`LLMProvider` backed by Anthropic's Messages API.

The concrete `anthropic.Anthropic` client is only ever constructed when the
caller does not inject one (`client=None`) — every automated test injects a
stub, so no test in this repository reaches the network. Constructing the
real client is exercised only by the manual §8 smoke check.

Two independent retry loops are in play here, and they must not be
conflated:

- **Transient-failure retry** (`app.providers.retry.with_retries`): the
  Messages API call itself is wrapped in the app-level retry policy, which
  retries only `timeout` / `rate_limit` `ProviderError`s with the
  0.5/1.0/2.0s backoff, via the injected `sleep`. The SDK client is built
  with `max_retries=0` so the SDK's own retry logic — real wall-clock
  sleep, no fixed backoff schedule, and retrying on 5xx (which we map to
  `backend`, a category that must fail on first occurrence) — never runs
  underneath it.
- **Schema-validation retry**: per the `ai-provider` spec, a response that
  fails to parse *or* fails to conform to the requested schema is not a
  `ProviderError` at all — it's retried once, and only if the retry also
  fails does a `ProviderError` with category `backend` get raised, naming
  the violated schema. See `app/providers/schema_validation.py`.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import anthropic

from app.providers.base import LLMResult, ProviderError
from app.providers.retry import with_retries
from app.providers.schema_validation import (
    SchemaViolation,
    describe_schema,
    parse_and_validate,
)

# Claude Opus 5 supports a low-effort mode that trims latency at some cost to
# depth. Every other knob is left at its default — in particular `thinking`
# is never set to `disabled`: on this model that risks the assistant
# emitting tool calls as plain text and leaking raw thinking tags into the
# visible response.
_LOW_EFFORT_MODEL_MARKER = "opus-5"

# A malformed structured-output response is retried once (initial attempt +
# one retry) before giving up — see module docstring.
_MAX_SCHEMA_ATTEMPTS = 2


class AnthropicLLM:
    """Chat-style completion, optionally schema-guided, via Anthropic."""

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
        self._client = client if client is not None else anthropic.Anthropic(
            api_key=api_key,
            timeout=float(timeout_seconds),
            # The app-level `with_retries` policy is the only retry layer —
            # the SDK must not retry underneath it.
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
        output_config: dict[str, Any] = {}
        if _LOW_EFFORT_MODEL_MARKER in self._model:
            output_config["effort"] = "low"
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if output_config:
            kwargs["output_config"] = output_config

        def _call() -> Any:
            try:
                return self._client.messages.create(**kwargs)
            except Exception as exc:
                raise _map_exception(exc) from exc

        schema_attempts = _MAX_SCHEMA_ATTEMPTS if schema is not None else 1
        response: Any = None
        text = ""
        parsed: dict | None = None

        for attempt in range(1, schema_attempts + 1):
            response = with_retries(_call, max_retries=self._max_retries, sleep=self._sleep)
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
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def _extract_text(response: Any) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ProviderError.backend("response contained no text content block")


def _map_exception(exc: Exception) -> ProviderError:
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError.auth(str(exc))
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError.rate_limit(str(exc))
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError.timeout(str(exc))
    if isinstance(exc, anthropic.APIError):
        return ProviderError.backend(str(exc))
    return ProviderError.backend(str(exc))
