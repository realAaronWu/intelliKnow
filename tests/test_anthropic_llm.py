"""Tests for `AnthropicLLM` — test-plan §6, rows 6.1-6.8.

Every test injects a stub `client` so nothing here ever reaches the network.
The stub mimics the small slice of the real `anthropic` SDK's shape that
`AnthropicLLM` actually touches: `client.messages.create(**kwargs)` returning
an object with `.content` (a list of blocks with `.type`/`.text`), `.model`,
and `.usage.input_tokens` / `.usage.output_tokens` — or raising one of the
real `anthropic` SDK exception classes.
"""

from __future__ import annotations

import json

import anthropic
import httpx
import pytest

from app.providers.anthropic_llm import AnthropicLLM
from app.providers.base import ProviderError


class _StubTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _StubUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _StubMessage:
    def __init__(self, content, model: str, input_tokens: int, output_tokens: int) -> None:
        self.content = content
        self.model = model
        self.usage = _StubUsage(input_tokens, output_tokens)


class _StubMessagesResource:
    """Records every call's kwargs; returns/raises whatever is queued."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._response = None
        self._error: Exception | None = None

    def queue_response(self, response) -> None:
        self._response = response
        self._error = None

    def queue_error(self, error: Exception) -> None:
        self._error = error
        self._response = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class _StubAnthropicClient:
    def __init__(self) -> None:
        self.messages = _StubMessagesResource()


def _make_llm(model: str = "claude-opus-5") -> tuple[AnthropicLLM, _StubAnthropicClient]:
    client = _StubAnthropicClient()
    llm = AnthropicLLM(
        model=model,
        api_key="unused",
        timeout_seconds=20,
        max_retries=2,
        client=client,
    )
    return llm, client


def _fake_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("POST", "https://api.anthropic.com/"))


def test_free_form_completion_returns_text_model_and_token_counts():
    llm, client = _make_llm()
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock("hello there")],
            model="claude-opus-5",
            input_tokens=12,
            output_tokens=3,
        )
    )

    result = llm.complete(system="be nice", user="hi")

    assert result.text == "hello there"
    assert result.parsed is None
    assert result.model == "claude-opus-5"
    assert result.input_tokens == 12
    assert result.output_tokens == 3


def test_schema_request_returns_parsed_object_and_carries_schema_in_output_config():
    llm, client = _make_llm()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock('{"answer": "42"}')],
            model="claude-opus-5",
            input_tokens=5,
            output_tokens=4,
        )
    )

    result = llm.complete(system="s", user="u", schema=schema)

    assert result.parsed == {"answer": "42"}
    sent_kwargs = client.messages.calls[0]
    assert sent_kwargs["output_config"]["format"] == {"type": "json_schema", "schema": schema}


def test_unparseable_schema_response_raises_backend_error():
    llm, client = _make_llm()
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock("not json at all")],
            model="claude-opus-5",
            input_tokens=5,
            output_tokens=4,
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema={"type": "object"})

    assert excinfo.value.category == "backend"


def test_auth_exception_mapped_to_auth_category():
    llm, client = _make_llm()
    client.messages.queue_error(
        anthropic.AuthenticationError("bad key", response=_fake_response(401), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "auth"


def test_rate_limit_exception_mapped_to_rate_limit_category():
    llm, client = _make_llm()
    client.messages.queue_error(
        anthropic.RateLimitError("slow down", response=_fake_response(429), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "rate_limit"


def test_timeout_exception_mapped_to_timeout_category():
    llm, client = _make_llm()
    client.messages.queue_error(
        anthropic.APITimeoutError(httpx.Request("POST", "https://api.anthropic.com/"))
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "timeout"


def test_other_api_exception_mapped_to_backend_category():
    llm, client = _make_llm()
    client.messages.queue_error(
        anthropic.InternalServerError("oops", response=_fake_response(500), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"


def test_low_effort_requested_and_thinking_never_disabled():
    llm, client = _make_llm(model="claude-opus-5")
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock("hi")],
            model="claude-opus-5",
            input_tokens=1,
            output_tokens=1,
        )
    )

    llm.complete(system="s", user="u")

    sent_kwargs = client.messages.calls[0]
    assert sent_kwargs["output_config"]["effort"] == "low"
    assert "thinking" not in sent_kwargs
