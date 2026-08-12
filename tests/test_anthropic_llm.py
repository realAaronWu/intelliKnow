"""Tests for `AnthropicLLM` — test-plan §6, rows 6.1-6.8.

Every test injects a stub `client` (and a no-op `sleep`) so nothing here
ever reaches the network or blocks on real wall-clock time. The stub mimics
the small slice of the real `anthropic` SDK's shape that `AnthropicLLM`
actually touches: `client.messages.create(**kwargs)` returning an object
with `.content` (a list of blocks with `.type`/`.text`), `.model`, and
`.usage.input_tokens` / `.usage.output_tokens` — or raising one of the real
`anthropic` SDK exception classes.

The response/error queue is a strict FIFO (like `tests/doubles.py`'s
`FakeLLMProvider`): every call to `create()` must have something queued for
it, so a test that under- or over-queues fails loudly instead of silently
replaying the last response.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

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
    def __init__(
        self,
        content,
        model: str,
        input_tokens: int,
        output_tokens: int,
        stop_reason: str = "end_turn",
    ) -> None:
        self.content = content
        self.model = model
        self.usage = _StubUsage(input_tokens, output_tokens)
        self.stop_reason = stop_reason


class _StubMessagesResource:
    """Records every call's kwargs; returns/raises whatever is queued, in order."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._queue: deque[tuple[str, object]] = deque()

    def queue_response(self, response) -> None:
        self._queue.append(("response", response))

    def queue_error(self, error: Exception) -> None:
        self._queue.append(("error", error))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        assert self._queue, (
            "_StubMessagesResource.create() called but nothing was queued — "
            "queue a response or error for every expected call."
        )
        kind, item = self._queue.popleft()
        if kind == "error":
            raise item
        return item


class _StubAnthropicClient:
    def __init__(self) -> None:
        self.messages = _StubMessagesResource()


def _make_llm(
    model: str = "claude-opus-5",
    max_retries: int = 2,
    sleep: Callable[[float], None] | None = None,
) -> tuple[AnthropicLLM, _StubAnthropicClient]:
    client = _StubAnthropicClient()
    llm = AnthropicLLM(
        model=model,
        api_key="unused",
        timeout_seconds=20,
        max_retries=max_retries,
        client=client,
        sleep=sleep if sleep is not None else (lambda seconds: None),
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


def test_unparseable_schema_response_raises_backend_error_after_one_retry():
    llm, client = _make_llm()
    malformed = _StubMessage(
        content=[_StubTextBlock("not json at all")],
        model="claude-opus-5",
        input_tokens=5,
        output_tokens=4,
    )
    # Per spec.md § "Structured generation returns malformed output": the
    # provider retries once before giving up, so two malformed responses
    # must be queued — one per attempt.
    client.messages.queue_response(malformed)
    client.messages.queue_response(malformed)

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema={"type": "object"})

    assert excinfo.value.category == "backend"
    assert len(client.messages.calls) == 2


def test_schema_retry_recovers_after_malformed_first_response():
    llm, client = _make_llm()
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock("not json at all")],
            model="claude-opus-5",
            input_tokens=5,
            output_tokens=4,
        )
    )
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock('{"answer": "42"}')],
            model="claude-opus-5",
            input_tokens=5,
            output_tokens=4,
        )
    )

    result = llm.complete(system="s", user="u", schema={"type": "object"})

    assert result.parsed == {"answer": "42"}
    assert len(client.messages.calls) == 2


_INTENT_SCHEMA = {
    "title": "IntentClassification",
    "type": "object",
    "properties": {"intent_slug": {"type": "string"}},
    "required": ["intent_slug"],
}


def _schema_message(text: str) -> _StubMessage:
    return _StubMessage(
        content=[_StubTextBlock(text)],
        model="claude-opus-5",
        input_tokens=5,
        output_tokens=4,
    )


def test_non_object_json_rejected_and_retried_then_raises_naming_the_schema():
    """spec: ai-provider § Structured generation — the caller receives a
    parsed object *conforming to the schema*. `[1, 2]` is syntactically valid
    JSON but is not an object, so `LLMResult.parsed` (typed `dict | None`)
    must never be handed one.
    """
    llm, client = _make_llm()
    client.messages.queue_response(_schema_message("[1, 2]"))
    client.messages.queue_response(_schema_message("[1, 2]"))

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert excinfo.value.category == "backend"
    assert "IntentClassification" in str(excinfo.value)
    assert len(client.messages.calls) == 2


def test_scalar_json_rejected_even_though_it_parses():
    llm, client = _make_llm()
    client.messages.queue_response(_schema_message("42"))
    client.messages.queue_response(_schema_message("42"))

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert excinfo.value.category == "backend"
    assert len(client.messages.calls) == 2


def test_object_violating_the_schema_is_retried_then_raises_naming_the_schema():
    """A well-formed object that omits a required property is a schema
    violation, and must feed the same retry-once path as a parse failure.
    """
    llm, client = _make_llm()
    client.messages.queue_response(_schema_message('{"wrong_key": "hr"}'))
    client.messages.queue_response(_schema_message('{"wrong_key": "hr"}'))

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert excinfo.value.category == "backend"
    assert "IntentClassification" in str(excinfo.value)
    assert len(client.messages.calls) == 2


def test_schema_violation_recovers_on_retry():
    llm, client = _make_llm()
    client.messages.queue_response(_schema_message('{"wrong_key": "hr"}'))
    client.messages.queue_response(_schema_message('{"intent_slug": "hr"}'))

    result = llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert result.parsed == {"intent_slug": "hr"}
    assert len(client.messages.calls) == 2


def test_untitled_schema_is_still_named_in_the_error():
    llm, client = _make_llm()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    client.messages.queue_response(_schema_message("{}"))
    client.messages.queue_response(_schema_message("{}"))

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=schema)

    assert "answer" in str(excinfo.value)


def test_auth_exception_mapped_to_auth_category():
    llm, client = _make_llm()
    client.messages.queue_error(
        anthropic.AuthenticationError("bad key", response=_fake_response(401), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "auth"


def test_rate_limit_exception_mapped_to_rate_limit_category():
    # rate_limit is retryable at the app level (Critical 1 fix), so with
    # max_retries=0 the single queued error is exhausted on the first
    # attempt — isolating the category-mapping assertion from retry count,
    # which is covered separately below.
    llm, client = _make_llm(max_retries=0)
    client.messages.queue_error(
        anthropic.RateLimitError("slow down", response=_fake_response(429), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "rate_limit"
    assert len(client.messages.calls) == 1


def test_timeout_exception_mapped_to_timeout_category():
    llm, client = _make_llm(max_retries=0)
    client.messages.queue_error(
        anthropic.APITimeoutError(httpx.Request("POST", "https://api.anthropic.com/"))
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "timeout"
    assert len(client.messages.calls) == 1


def test_transient_failure_retried_by_app_level_policy_not_the_sdk():
    """Critical 1: retry must go through `with_retries`, not SDK auto-retry.

    Proven two ways: (1) the exact 0.5/1.0 backoff schedule from
    `with_retries` shows up on the injected `sleep`, which the SDK's own
    retry would never call since it sleeps for real; (2) the call only
    succeeds on the third attempt, which the SDK client alone couldn't
    produce since it's a plain stub with no retry logic of its own.
    """
    sleeps: list[float] = []
    llm, client = _make_llm(max_retries=2, sleep=sleeps.append)
    client.messages.queue_error(
        anthropic.RateLimitError("slow down", response=_fake_response(429), body=None)
    )
    client.messages.queue_error(
        anthropic.RateLimitError("slow down", response=_fake_response(429), body=None)
    )
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock("recovered")],
            model="claude-opus-5",
            input_tokens=1,
            output_tokens=1,
        )
    )

    result = llm.complete(system="s", user="u")

    assert result.text == "recovered"
    assert len(client.messages.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_default_client_disables_sdk_level_retries():
    """Critical 1: the SDK client must be built with max_retries=0 so the
    app-level `with_retries` policy is the only thing that ever retries.
    """
    llm = AnthropicLLM(
        model="claude-opus-5",
        api_key="sk-test",
        timeout_seconds=20,
        max_retries=5,
    )

    assert llm._client.max_retries == 0


def test_other_api_exception_mapped_to_backend_category():
    llm, client = _make_llm()
    client.messages.queue_error(
        anthropic.InternalServerError("oops", response=_fake_response(500), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"


def test_default_max_tokens_leaves_room_for_thinking_plus_response():
    llm, client = _make_llm()
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock("hi")],
            model="claude-opus-5",
            input_tokens=1,
            output_tokens=1,
        )
    )

    llm.complete(system="s", user="u")

    assert client.messages.calls[0]["max_tokens"] == 4096


def test_truncated_response_raises_backend_error_naming_truncation():
    """A `max_tokens` stop reason means the budget ran out mid-generation.
    Returning the partial text as a normal result would silently hand the
    caller a half-finished answer.
    """
    llm, client = _make_llm()
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock("a partial ans")],
            model="claude-opus-5",
            input_tokens=1,
            output_tokens=4096,
            stop_reason="max_tokens",
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"
    assert "truncat" in str(excinfo.value).lower()
    assert "max_tokens" in str(excinfo.value)


def test_truncation_detected_even_when_a_schema_was_requested():
    """The stop reason must be inspected before the response is parsed —
    otherwise a truncated JSON body is misreported as a schema violation and
    burns the retry.
    """
    llm, client = _make_llm()
    client.messages.queue_response(
        _StubMessage(
            content=[_StubTextBlock('{"intent_slu')],
            model="claude-opus-5",
            input_tokens=1,
            output_tokens=4096,
            stop_reason="max_tokens",
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert "truncat" in str(excinfo.value).lower()
    assert len(client.messages.calls) == 1


def test_refusal_reported_as_a_refusal_not_as_a_missing_text_block():
    """On Opus 5 a policy decline returns HTTP 200 with an empty `content`
    array. Extracting text first surfaced the misleading "response contained
    no text content block".
    """
    llm, client = _make_llm()
    client.messages.queue_response(
        _StubMessage(
            content=[],
            model="claude-opus-5",
            input_tokens=10,
            output_tokens=0,
            stop_reason="refusal",
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"
    message = str(excinfo.value)
    assert "refus" in message.lower()
    assert "no text content block" not in message


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
