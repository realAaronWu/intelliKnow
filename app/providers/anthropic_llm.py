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

Before either of those runs, `_validate_schema_shape` rejects a
caller-supplied schema outright if any `type: "object"` node — at any
nesting depth — omits `additionalProperties: false`. The real Anthropic API
returns `400 invalid_request_error: output_config.format.schema: For
'object' type, 'additionalProperties' must be explicitly set to false`, a
constraint no other provider in this codebase enforces, so a schema built
and tested only against the local/OpenAI providers passes every existing
test and only 400s the moment `llm.provider` switches to `anthropic` —
exactly how this defect reached a live run undetected. The check runs here,
client-side, so that failure happens loudly and immediately instead.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import anthropic

from app.providers.base import (
    DEFAULT_MAX_TOKENS,
    EffortLevel,
    LLMResult,
    ProviderError,
)
from app.providers.retry import with_retries
from app.providers.schema_validation import (
    SchemaViolation,
    describe_schema,
    parse_and_validate,
)

# A malformed structured-output response is retried once (initial attempt +
# one retry) before giving up — see module docstring.
_MAX_SCHEMA_ATTEMPTS = 2


def _validate_schema_shape(schema: dict, path: str = "schema") -> None:
    """Raise `ProviderError.backend` if any `type: "object"` node in
    `schema` — at any nesting depth — does not set
    `additionalProperties: false`. See module docstring.

    Walks every place a subschema can appear: `properties`, `items` (both
    the single-schema and tuple-validation list forms), `anyOf`/`oneOf`/
    `allOf`, `not`, and `$defs`/`definitions`. `path` names the offending
    node in the raised error so a multi-object schema doesn't leave the
    caller guessing which part is wrong.
    """
    if not isinstance(schema, dict):
        return

    if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
        raise ProviderError.backend(
            f"schema node at {path!r} has type 'object' but does not set "
            "'additionalProperties: false'; the Anthropic API rejects "
            "object schemas without it (400 invalid_request_error)"
        )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            _validate_schema_shape(subschema, f"{path}.properties.{key}")

    items = schema.get("items")
    if isinstance(items, dict):
        _validate_schema_shape(items, f"{path}.items")
    elif isinstance(items, list):
        for index, subschema in enumerate(items):
            _validate_schema_shape(subschema, f"{path}.items[{index}]")

    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            for index, subschema in enumerate(variants):
                _validate_schema_shape(subschema, f"{path}.{keyword}[{index}]")

    not_schema = schema.get("not")
    if isinstance(not_schema, dict):
        _validate_schema_shape(not_schema, f"{path}.not")

    defs = schema.get("$defs")
    defs_keyword = "$defs"
    if not isinstance(defs, dict):
        defs = schema.get("definitions")
        defs_keyword = "definitions"
    if isinstance(defs, dict):
        for key, subschema in defs.items():
            _validate_schema_shape(subschema, f"{path}.{defs_keyword}.{key}")


class AnthropicLLM:
    """Chat-style completion, optionally schema-guided, via Anthropic."""

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_seconds: int,
        max_retries: int,
        effort: EffortLevel | None,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._effort = effort
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
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult:
        # `thinking` is deliberately never sent: setting it to `disabled` on
        # this model risks the assistant emitting tool calls as plain text
        # and leaking raw thinking tags into the visible response.
        #
        # `effort` is opt-out, not mandatory: not every model accepts this
        # parameter (claude-haiku-4-5 and Sonnet 4.5 reject it with a 400),
        # so it is included only when configured — sending `"effort": null`
        # is a different, still-rejected request shape.
        output_config: dict[str, Any] = {}
        if self._effort is not None:
            output_config["effort"] = self._effort
        if schema is not None:
            _validate_schema_shape(schema)
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
            # Before anything reads the body: a truncated or refused response
            # has no usable content, and diagnosing it as a parse failure
            # would burn the schema retry on a request that cannot succeed.
            _check_stop_reason(response)
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


def _check_stop_reason(response: Any) -> None:
    """Reject responses whose stop reason means the body is unusable.

    Neither case is retryable: repeating an identical request that ran out
    of budget, or that the model declined on policy grounds, produces the
    same outcome.
    """
    stop_reason = getattr(response, "stop_reason", None)

    if stop_reason == "max_tokens":
        raise ProviderError.backend(
            "response was truncated: the model stopped at the max_tokens "
            "budget before finishing. On thinking-enabled models this budget "
            "covers thinking plus visible response text combined, so raise "
            "max_tokens rather than treating the partial text as an answer."
        )

    if stop_reason == "refusal":
        raise ProviderError.backend(
            "the model refused to produce a response (stop_reason "
            "'refusal'); the request was declined on policy grounds and no "
            "content was returned"
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
