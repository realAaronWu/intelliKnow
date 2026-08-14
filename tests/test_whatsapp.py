from __future__ import annotations

import hashlib
import hmac
import json

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.channels.handler import HandlerResult
from app.channels.store import ChannelStore
from app.channels.whatsapp import WhatsAppCloudAPI, WhatsAppEndpoint, build_whatsapp_router
from app.db import create_engine_for, init_schema


class FakeHandler:
    def __init__(self) -> None:
        self.calls = []

    async def handle(self, message, adapter):
        self.calls.append((message, adapter))
        return HandlerResult(True, True, "success", 100)


class FakeAPI:
    def __init__(self) -> None:
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def send_message(self, access_token, phone_number_id, recipient, text):
        self.sent.append((access_token, phone_number_id, recipient, text))


class FakeGraphResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300


class FakeGraphSession:
    def __init__(self, response=None):
        self.response = response or FakeGraphResponse()
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _setup(tmp_path, *, reuse_active_api: bool = False):
    engine = create_engine_for(tmp_path / "whatsapp.db")
    init_schema(engine)
    store = ChannelStore(engine, Fernet.generate_key().decode("ascii"))
    store.initialize("whatsapp", enabled=False)
    store.save_credentials(
        "whatsapp",
        {
            "access_token": "access-token",
            "phone_number_id": "phone-id",
            "app_secret": "app-secret",
            "verify_token": "chosen-verify-token",
        },
    )
    store.set_enabled("whatsapp", True)
    handler = FakeHandler()
    api = FakeAPI()
    endpoint = WhatsAppEndpoint(
        store,
        handler,
        api_factory=(
            (lambda: (_ for _ in ()).throw(
                AssertionError("active WhatsApp client should be reused")
            ))
            if reuse_active_api
            else (lambda: api)
        ),
        api_provider=(lambda: api) if reuse_active_api else None,
    )
    app = FastAPI()
    app.include_router(build_whatsapp_router(endpoint))
    return TestClient(app), store, handler, api


def _signature(body: bytes) -> str:
    digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_meta_verification_returns_exact_challenge(tmp_path):
    client, _, _, _ = _setup(tmp_path)

    response = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "chosen-verify-token",
            "hub.challenge": "123456",
        },
    )

    assert response.status_code == 200
    assert response.text == "123456"


def test_whatsapp_warm_delivery_reads_phone_metadata_without_sending_message():
    session = FakeGraphSession()
    api = WhatsAppCloudAPI(
        session=session,
        graph_root="https://graph.test",
    )

    import asyncio

    asyncio.run(api.warm_delivery("access-token", "phone-id"))

    assert session.calls == [
        (
            "https://graph.test/phone-id",
            {
                "headers": {"Authorization": "Bearer access-token"},
                "params": {"fields": "id"},
                "timeout": session.calls[0][1]["timeout"],
            },
        )
    ]


def test_meta_verification_rejects_wrong_token(tmp_path):
    client, _, _, _ = _setup(tmp_path)

    response = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "123456",
        },
    )

    assert response.status_code == 403


def test_signed_text_message_enters_shared_handler(tmp_path):
    client, store, handler, _ = _setup(tmp_path)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "id": "wamid.abc",
                                    "type": "text",
                                    "text": {"body": "What is our leave policy?"},
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(
        "/api/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _signature(body),
        },
    )

    assert response.status_code == 200
    message, adapter = handler.calls[0]
    assert message.channel == "whatsapp"
    assert message.user_ref == "15551234567"
    assert message.reply_ref == "15551234567"
    assert message.text == "What is our leave policy?"
    assert adapter.profile.markup == "plain"
    assert store.get("whatsapp").enabled is True


def test_invalid_signature_is_rejected_before_dispatch(tmp_path):
    client, _, handler, _ = _setup(tmp_path)

    response = client.post(
        "/api/whatsapp/webhook",
        json={"entry": []},
        headers={"X-Hub-Signature-256": "sha256=wrong"},
    )

    assert response.status_code == 401
    assert handler.calls == []


def test_status_callback_is_acknowledged_without_query(tmp_path):
    client, _, handler, _ = _setup(tmp_path)
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {"statuses": [{"id": "wamid.abc"}]},
                        }
                    ]
                }
            ]
        },
        separators=(",", ":"),
    ).encode()

    response = client.post(
        "/api/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _signature(body),
        },
    )

    assert response.status_code == 200
    assert handler.calls == []


def test_webhook_reuses_active_whatsapp_api(tmp_path):
    client, _, handler, api = _setup(tmp_path, reuse_active_api=True)
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "type": "text",
                                    "text": {"body": "Question"},
                                }
                            ]
                        },
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(
        "/api/whatsapp/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _signature(body)},
    )

    assert response.status_code == 200
    assert handler.calls[0][1]._api is api
