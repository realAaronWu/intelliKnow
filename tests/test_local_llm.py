"""Tests for `LocalLLM` — Important 4 from fix round 1: previously untested.

`LocalLLM` shares its request/response handling with `OpenAILLM` via
`openai_llm.chat_complete` (already covered in depth by
`tests/test_openai_llm.py`), so these tests focus on what's specific to
`LocalLLM`: default-client construction needing no real API key, and that
its `complete()` correctly threads through to the shared, already-tested
helper (happy path + one exception-mapping case as a smoke check).
"""

from __future__ import annotations

from collections import deque

import httpx
import openai
import pytest

from app.providers.base import ProviderError
from app.providers.local_llm import LocalLLM


class _StubMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubChoice:
    def __init__(self, content: str) -> None:
        self.message = _StubMessage(content)


class _StubUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _StubChatCompletion:
    def __init__(self, text: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.choices = [_StubChoice(text)]
        self.model = model
        self.usage = _StubUsage(prompt_tokens, completion_tokens)


class _StubCompletionsResource:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._queue: deque[tuple[str, object]] = deque()

    def queue_response(self, response) -> None:
        self._queue.append(("response", response))

    def queue_error(self, error: Exception) -> None:
        self._queue.append(("error", error))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        assert self._queue, "_StubCompletionsResource.create() called but nothing was queued."
        kind, item = self._queue.popleft()
        if kind == "error":
            raise item
        return item


class _StubChat:
    def __init__(self) -> None:
        self.completions = _StubCompletionsResource()


class _StubLocalClient:
    def __init__(self) -> None:
        self.chat = _StubChat()


def test_constructs_without_a_key_and_uses_local_base_url():
    llm = LocalLLM(
        model="llama-3-local",
        api_key="",
        timeout_seconds=20,
        max_retries=2,
        env={},
    )

    assert llm._client.api_key == "not-needed"
    assert str(llm._client.base_url) == "http://localhost:11434/v1/"


def test_base_url_overridden_by_env():
    llm = LocalLLM(
        model="llama-3-local",
        api_key="",
        timeout_seconds=20,
        max_retries=2,
        env={"LOCAL_LLM_BASE_URL": "http://localhost:8000/v1"},
    )

    assert str(llm._client.base_url) == "http://localhost:8000/v1/"


def test_default_client_disables_sdk_level_retries():
    llm = LocalLLM(
        model="llama-3-local",
        api_key="",
        timeout_seconds=20,
        max_retries=5,
        env={},
    )

    assert llm._client.max_retries == 0


def test_free_form_completion_via_injected_client():
    client = _StubLocalClient()
    client.chat.completions.queue_response(
        _StubChatCompletion("hi from local", "llama-3-local", prompt_tokens=4, completion_tokens=3)
    )
    llm = LocalLLM(
        model="llama-3-local",
        api_key="",
        timeout_seconds=20,
        max_retries=2,
        client=client,
        sleep=lambda seconds: None,
    )

    result = llm.complete(system="s", user="u")

    assert result.text == "hi from local"
    assert result.model == "llama-3-local"
    assert result.input_tokens == 4
    assert result.output_tokens == 3


def test_auth_exception_mapped_to_auth_category():
    client = _StubLocalClient()
    response = httpx.Response(401, request=httpx.Request("POST", "http://localhost:11434/v1/"))
    client.chat.completions.queue_error(
        openai.AuthenticationError("bad key", response=response, body=None)
    )
    llm = LocalLLM(
        model="llama-3-local",
        api_key="",
        timeout_seconds=20,
        max_retries=2,
        client=client,
        sleep=lambda seconds: None,
    )

    with pytest.raises(ProviderError) as excinfo:
        llm.complete(system="s", user="u")

    assert excinfo.value.category == "auth"
