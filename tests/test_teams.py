from __future__ import annotations

import asyncio
import json

import pytest
from botbuilder.schema import Activity, ActivityTypes
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.channels.store import ChannelStore
from app.channels.teams import (
    TeamsAdapter,
    TeamsEndpoint,
    build_teams_router,
    deserialize_conversation_reference,
    normalize_activity,
)
from app.db import create_engine_for, init_schema


CAPTURED_ACTIVITY = {
    "type": "message",
    "id": "activity-1",
    "timestamp": "2026-08-12T00:00:00Z",
    "serviceUrl": "http://localhost:3978",
    "channelId": "msteams",
    "from": {"id": "user-123", "name": "Finance User"},
    "conversation": {"id": "conversation-456"},
    "recipient": {"id": "bot-789", "name": "IntelliKnow"},
    "text": "What is the meal reimbursement limit?",
}


class FakeContext:
    def __init__(self, activity=None):
        self.activity = activity or Activity().deserialize(CAPTURED_ACTIVITY)
        self.sent = []

    async def send_activity(self, activity):
        self.sent.append(activity)


class FakeHandler:
    def __init__(self):
        self.calls = []
        self.error: Exception | None = None

    async def handle(self, message, adapter):
        self.calls.append((message, adapter))
        if self.error:
            raise self.error


class FakeBotFrameworkAdapter:
    def __init__(self, context=None, error=None):
        self.context = context or FakeContext()
        self.error = error
        self.calls = []

    async def process_activity(self, activity, auth_header, logic):
        self.calls.append((activity, auth_header))
        if self.error:
            raise self.error
        self.context.activity = activity
        await logic(self.context)
        return None


def _store(tmp_path, *, credentials=True):
    engine = create_engine_for(tmp_path / "teams.db")
    init_schema(engine)
    store = ChannelStore(engine, Fernet.generate_key().decode("ascii"))
    if credentials:
        store.save_credentials(
            "teams",
            {"app_id": "teams-app-id", "app_password": "teams-secret"},
        )
    store.set_enabled("teams", True)
    return store


def _client(endpoint):
    app = FastAPI()
    app.include_router(build_teams_router(endpoint))
    return TestClient(app)


def test_captured_activity_normalizes_to_shared_message():
    context = FakeContext()

    message = normalize_activity(context)

    assert message.channel == "teams"
    assert message.user_ref == "user-123"
    assert message.text == "What is the meal reimbursement limit?"
    reference = deserialize_conversation_reference(message.reply_ref)
    assert reference.conversation.id == "conversation-456"
    assert reference.channel_id == "msteams"
    assert reference.service_url == "http://localhost:3978"


def test_non_message_activity_is_ignored():
    activity = Activity(type=ActivityTypes.conversation_update)
    assert normalize_activity(FakeContext(activity)) is None


def test_adapter_sends_typing_and_pipeline_html_without_reformatting():
    context = FakeContext()
    adapter = TeamsAdapter(context, max_message_chars=28000)

    async def exercise():
        await adapter.typing("ignored-reference")
        await adapter.send("ignored-reference", "Answer<ul><li>Source</li></ul>")

    asyncio.run(exercise())

    assert context.sent[0].type == ActivityTypes.typing
    assert context.sent[1].type == ActivityTypes.message
    assert context.sent[1].text == "Answer<ul><li>Source</li></ul>"
    assert context.sent[1].text_format == "xml"


def test_adapter_rejects_over_limit_message_before_send():
    context = FakeContext()
    adapter = TeamsAdapter(context, max_message_chars=4)

    with pytest.raises(ValueError, match="exceeds 4"):
        asyncio.run(adapter.send("reference", "12345"))

    assert context.sent == []


def test_endpoint_uses_saved_credentials_and_dispatches_activity(tmp_path):
    store = _store(tmp_path)
    handler = FakeHandler()
    sdk = FakeBotFrameworkAdapter()
    built_with = []

    def factory(app_id, password):
        built_with.append((app_id, password))
        return sdk

    endpoint = TeamsEndpoint(store, handler, adapter_factory=factory)
    response = _client(endpoint).post(
        "/api/messages",
        json=CAPTURED_ACTIVITY,
        headers={"Authorization": "Bearer bot-framework-token"},
    )

    assert response.status_code == 200
    assert built_with == [("teams-app-id", "teams-secret")]
    assert sdk.calls[0][1] == "Bearer bot-framework-token"
    assert handler.calls[0][0].text == CAPTURED_ACTIVITY["text"]


def test_loopback_emulator_may_run_without_azure_credentials(tmp_path):
    store = _store(tmp_path, credentials=False)
    handler = FakeHandler()
    built_with = []

    def factory(app_id, password):
        built_with.append((app_id, password))
        return FakeBotFrameworkAdapter()

    endpoint = TeamsEndpoint(store, handler, adapter_factory=factory)
    response = _client(endpoint).post("/api/messages", json=CAPTURED_ACTIVITY)

    assert response.status_code == 200
    assert built_with == [("", "")]
    assert len(handler.calls) == 1


def test_non_loopback_request_requires_azure_credentials(tmp_path):
    store = _store(tmp_path, credentials=False)
    handler = FakeHandler()
    endpoint = TeamsEndpoint(
        store,
        handler,
        adapter_factory=lambda *_: FakeBotFrameworkAdapter(),
    )
    body = json.dumps(CAPTURED_ACTIVITY).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/messages",
            "headers": [(b"content-type", b"application/json")],
            "client": ("203.0.113.10", 12345),
            "server": ("localhost", 8000),
            "scheme": "http",
            "query_string": b"",
        },
        receive,
    )

    with pytest.raises(Exception) as captured:
        asyncio.run(endpoint.process(request))

    assert captured.value.status_code == 503
    assert handler.calls == []


def test_public_host_via_loopback_proxy_still_requires_credentials(tmp_path):
    store = _store(tmp_path, credentials=False)
    endpoint = TeamsEndpoint(
        store,
        FakeHandler(),
        adapter_factory=lambda *_: FakeBotFrameworkAdapter(),
    )
    app = FastAPI()
    app.include_router(build_teams_router(endpoint))

    response = TestClient(app, base_url="https://kms.example.com").post(
        "/api/messages",
        json=CAPTURED_ACTIVITY,
    )

    assert response.status_code == 503


def test_sdk_authentication_failure_returns_401_without_pipeline(tmp_path):
    store = _store(tmp_path)
    handler = FakeHandler()
    sdk = FakeBotFrameworkAdapter(error=PermissionError("bad token"))
    endpoint = TeamsEndpoint(store, handler, adapter_factory=lambda *_: sdk)

    response = _client(endpoint).post("/api/messages", json=CAPTURED_ACTIVITY)

    assert response.status_code == 401
    assert handler.calls == []
    assert store.get("teams").status == "disconnected"
    assert "authentication" in store.get("teams").last_error.lower()


def test_processing_failure_is_acknowledged_to_prevent_retry_loop(tmp_path):
    store = _store(tmp_path)
    handler = FakeHandler()
    sdk = FakeBotFrameworkAdapter(error=RuntimeError("connector failed"))
    endpoint = TeamsEndpoint(store, handler, adapter_factory=lambda *_: sdk)

    response = _client(endpoint).post("/api/messages", json=CAPTURED_ACTIVITY)

    assert response.status_code == 200
    assert store.get("teams").last_error == "connector failed"


def test_disabled_endpoint_returns_503_without_dispatch(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("teams", False)
    handler = FakeHandler()
    endpoint = TeamsEndpoint(
        store,
        handler,
        adapter_factory=lambda *_: FakeBotFrameworkAdapter(),
    )

    response = _client(endpoint).post("/api/messages", json=CAPTURED_ACTIVITY)

    assert response.status_code == 503
    assert handler.calls == []
