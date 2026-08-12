"""`LLMProvider` backed by Anthropic's Messages API.

The concrete `anthropic.Anthropic` client is only ever constructed when the
caller does not inject one (`client=None`) — every automated test injects a
stub, so no test in this repository reaches the network. Constructing the
real client is exercised only by the manual §8 smoke check.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from app.providers.base import LLMResult, ProviderError

# Claude Opus 5 supports a low-effort mode that trims latency at some cost to
# depth. Every other knob is left at its default — in particular `thinking`
# is never set to `disabled`: on this model that risks the assistant
# emitting tool calls as plain text and leaking raw thinking tags into the
# visible response.
_LOW_EFFORT_MODEL_MARKER = "opus-5"


class AnthropicLLM:
    """Chat-style completion, optionally schema-guided, via Anthropic."""

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_seconds: int,
        max_retries: int,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._client = client if client is not None else anthropic.Anthropic(
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

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise _map_exception(exc) from exc

        text = _extract_text(response)
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
