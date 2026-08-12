"""Tests for `OpenAILLM` and the shared `chat_complete` helper it and
`LocalLLM` both use.

Fix-round coverage: Critical 1 (transient-failure retry goes through the
app-level `with_retries` policy, not the SDK's own retry — SDK client is
built with `max_retries=0`) and Critical 2 (a malformed structured-output
response is retried once before raising `backend`). Every test injects a
stub `client` and a no-op/recording `sleep`, so nothing here touches the
network or blocks on real time.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

import httpx
import openai
import pytest

from app.providers.base import ProviderError
from app.providers.openai_llm import OpenAILLM


class _StubMessage:
    def __init__(self, content: str, refusal: str | None = None) -> None:
        self.content = content
        self.refusal = refusal


class _StubChoice:
    def __init__(
        self,
        content: str,
        finish_reason: str = "stop",
        refusal: str | None = None,
    ) -> None:
        self.message = _StubMessage(content, refusal=refusal)
        self.finish_reason = finish_reason


class _StubUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _StubChatCompletion:
    def __init__(
        self,
        text: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        finish_reason: str = "stop",
        refusal: str | None = None,
    ) -> None:
        self.choices = [_StubChoice(text, finish_reason=finish_reason, refusal=refusal)]
        self.model = model
        self.usage = _StubUsage(prompt_tokens, completion_tokens)


class _StubCompletionsResource:
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
            "_StubCompletionsResource.create() called but nothing was queued."
        )
        kind, item = self._queue.popleft()
        if kind == "error":
            raise item
        return item


class _StubChat:
    def __init__(self) -> None:
        self.completions = _StubCompletionsResource()


class _StubOpenAIClient:
    def __init__(self) -> None:
        self.chat = _StubChat()


def _make_llm(
    max_retries: int = 2,
    sleep: Callable[[float], None] | None = None,
) -> tuple[OpenAILLM, _StubOpenAIClient]:
    client = _StubOpenAIClient()
    llm = OpenAILLM(
        model="gpt-5",
        api_key="unused",
        timeout_seconds=20,
        max_retries=max_retries,
        client=client,
        sleep=sleep if sleep is not None else (lambda seconds: None),
    )
    return llm, client


def _fake_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("POST", "https://api.openai.com/"))


def test_free_form_completion_returns_text_model_and_token_counts():
    llm, client = _make_llm()
    client.chat.completions.queue_response(
        _StubChatCompletion("hello there", "gpt-5", prompt_tokens=12, completion_tokens=3)
    )

    result = llm.complete(system="be nice", user="hi")

    assert result.text == "hello there"
    assert result.parsed is None
    assert result.model == "gpt-5"
    assert result.input_tokens == 12
    assert result.output_tokens == 3


def test_schema_request_returns_parsed_object_and_carries_schema_in_response_format():
    llm, client = _make_llm()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    client.chat.completions.queue_response(
        _StubChatCompletion('{"answer": "42"}', "gpt-5", prompt_tokens=5, completion_tokens=4)
    )

    result = llm.complete(system="s", user="u", schema=schema)

    assert result.parsed == {"answer": "42"}
    sent_kwargs = client.chat.completions.calls[0]
    assert sent_kwargs["response_format"]["json_schema"]["schema"] == schema


def test_unparseable_schema_response_raises_backend_error_after_one_retry():
    llm, client = _make_llm()
    malformed = _StubChatCompletion("not json", "gpt-5", prompt_tokens=5, completion_tokens=4)
    client.chat.completions.queue_response(malformed)
    client.chat.completions.queue_response(malformed)

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema={"type": "object"})

    assert excinfo.value.category == "backend"
    assert len(client.chat.completions.calls) == 2


def test_schema_retry_recovers_after_malformed_first_response():
    llm, client = _make_llm()
    client.chat.completions.queue_response(
        _StubChatCompletion("not json", "gpt-5", prompt_tokens=5, completion_tokens=4)
    )
    client.chat.completions.queue_response(
        _StubChatCompletion('{"answer": "42"}', "gpt-5", prompt_tokens=5, completion_tokens=4)
    )

    result = llm.complete(system="s", user="u", schema={"type": "object"})

    assert result.parsed == {"answer": "42"}
    assert len(client.chat.completions.calls) == 2


_INTENT_SCHEMA = {
    "title": "IntentClassification",
    "type": "object",
    "properties": {"intent_slug": {"type": "string"}},
    "required": ["intent_slug"],
}


def _schema_completion(text: str) -> _StubChatCompletion:
    return _StubChatCompletion(text, "gpt-5", prompt_tokens=5, completion_tokens=4)


def test_non_object_json_rejected_and_retried_then_raises_naming_the_schema():
    """spec: ai-provider § Structured generation — see the equivalent test in
    tests/test_anthropic_llm.py for the rationale.
    """
    llm, client = _make_llm()
    client.chat.completions.queue_response(_schema_completion("[1, 2]"))
    client.chat.completions.queue_response(_schema_completion("[1, 2]"))

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert excinfo.value.category == "backend"
    assert "IntentClassification" in str(excinfo.value)
    assert len(client.chat.completions.calls) == 2


def test_scalar_json_rejected_even_though_it_parses():
    llm, client = _make_llm()
    client.chat.completions.queue_response(_schema_completion("42"))
    client.chat.completions.queue_response(_schema_completion("42"))

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert excinfo.value.category == "backend"
    assert len(client.chat.completions.calls) == 2


def test_object_violating_the_schema_is_retried_then_raises_naming_the_schema():
    llm, client = _make_llm()
    client.chat.completions.queue_response(_schema_completion('{"wrong_key": "hr"}'))
    client.chat.completions.queue_response(_schema_completion('{"wrong_key": "hr"}'))

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert excinfo.value.category == "backend"
    assert "IntentClassification" in str(excinfo.value)
    assert len(client.chat.completions.calls) == 2


def test_schema_violation_recovers_on_retry():
    llm, client = _make_llm()
    client.chat.completions.queue_response(_schema_completion('{"wrong_key": "hr"}'))
    client.chat.completions.queue_response(_schema_completion('{"intent_slug": "hr"}'))

    result = llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert result.parsed == {"intent_slug": "hr"}
    assert len(client.chat.completions.calls) == 2


def test_auth_exception_mapped_to_auth_category():
    llm, client = _make_llm()
    client.chat.completions.queue_error(
        openai.AuthenticationError("bad key", response=_fake_response(401), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "auth"
    assert len(client.chat.completions.calls) == 1


def test_rate_limit_exception_mapped_to_rate_limit_category():
    llm, client = _make_llm(max_retries=0)
    client.chat.completions.queue_error(
        openai.RateLimitError("slow down", response=_fake_response(429), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "rate_limit"


def test_timeout_exception_mapped_to_timeout_category():
    llm, client = _make_llm(max_retries=0)
    client.chat.completions.queue_error(
        openai.APITimeoutError(httpx.Request("POST", "https://api.openai.com/"))
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "timeout"


def test_other_api_exception_mapped_to_backend_category():
    llm, client = _make_llm()
    client.chat.completions.queue_error(
        openai.InternalServerError("oops", response=_fake_response(500), body=None)
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"
    assert len(client.chat.completions.calls) == 1


def test_transient_failure_retried_by_app_level_policy_not_the_sdk():
    sleeps: list[float] = []
    llm, client = _make_llm(max_retries=2, sleep=sleeps.append)
    client.chat.completions.queue_error(
        openai.RateLimitError("slow down", response=_fake_response(429), body=None)
    )
    client.chat.completions.queue_error(
        openai.RateLimitError("slow down", response=_fake_response(429), body=None)
    )
    client.chat.completions.queue_response(
        _StubChatCompletion("recovered", "gpt-5", prompt_tokens=1, completion_tokens=1)
    )

    result = llm.complete(system="s", user="u")

    assert result.text == "recovered"
    assert len(client.chat.completions.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_default_client_disables_sdk_level_retries():
    llm = OpenAILLM(
        model="gpt-5",
        api_key="sk-test",
        timeout_seconds=20,
        max_retries=5,
    )

    assert llm._client.max_retries == 0


def test_truncated_response_raises_backend_error_naming_truncation():
    """`finish_reason == "length"` is the OpenAI spelling of "the token
    budget ran out mid-generation" — the partial text must not be returned
    as a normal result.
    """
    llm, client = _make_llm()
    client.chat.completions.queue_response(
        _StubChatCompletion(
            "a partial ans",
            "gpt-5",
            prompt_tokens=1,
            completion_tokens=4096,
            finish_reason="length",
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"
    assert "truncat" in str(excinfo.value).lower()


def test_truncation_detected_before_schema_parsing():
    llm, client = _make_llm()
    client.chat.completions.queue_response(
        _StubChatCompletion(
            '{"intent_slu',
            "gpt-5",
            prompt_tokens=1,
            completion_tokens=4096,
            finish_reason="length",
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u", schema=_INTENT_SCHEMA)

    assert "truncat" in str(excinfo.value).lower()
    assert len(client.chat.completions.calls) == 1


def test_refusal_reported_as_a_refusal():
    llm, client = _make_llm()
    client.chat.completions.queue_response(
        _StubChatCompletion(
            None,
            "gpt-5",
            prompt_tokens=10,
            completion_tokens=2,
            finish_reason="stop",
            refusal="I can't help with that.",
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"
    assert "refus" in str(excinfo.value).lower()


def test_content_filter_reported_as_a_refusal():
    llm, client = _make_llm()
    client.chat.completions.queue_response(
        _StubChatCompletion(
            None,
            "gpt-5",
            prompt_tokens=10,
            completion_tokens=0,
            finish_reason="content_filter",
        )
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "backend"
    assert "refus" in str(excinfo.value).lower()
