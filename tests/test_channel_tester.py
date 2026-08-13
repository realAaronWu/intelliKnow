from __future__ import annotations

import asyncio
import json

from cryptography.fernet import Fernet

from app.channels.handler import HandlerResult
from app.channels.store import ChannelStore
from app.channels.tester import ChannelTestService
from app.db import create_engine_for, init_schema


class FakeHandler:
    def __init__(self, result=None):
        self.result = result or HandlerResult(True, True, "success", 321)
        self.calls = []

    async def handle(self, message, adapter):
        self.calls.append((message, adapter))
        return self.result


class FakeTelegramAPI:
    def __init__(self):
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def send_chat_action(self, token, chat_id):
        return None

    async def send_message(self, token, chat_id, text):
        self.messages.append((token, chat_id, text))


class FakeTeamsSDK:
    def __init__(self):
        self.references = []

    async def continue_conversation(self, reference, callback, bot_id=None):
        self.references.append((reference, bot_id))
        await callback(object())


def _store(tmp_path):
    engine = create_engine_for(tmp_path / "tester.db")
    init_schema(engine)
    return ChannelStore(engine, Fernet.generate_key().decode("ascii"))


def _teams_reference():
    return json.dumps(
        {
            "activityId": "activity-1",
            "user": {"id": "user-1"},
            "bot": {"id": "bot-1"},
            "conversation": {"id": "conversation-1"},
            "channelId": "msteams",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
        }
    )


def test_test_requires_enabled_channel(tmp_path):
    store = _store(tmp_path)
    tester = ChannelTestService(store, FakeHandler())

    result = asyncio.run(tester.run("telegram", "question"))

    assert result.ok is False
    assert result.stage == "setup"
    assert "disabled" in result.error


def test_test_requires_credentials_before_destination(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("telegram", True)
    tester = ChannelTestService(store, FakeHandler())

    result = asyncio.run(tester.run("telegram", "question"))

    assert result.stage == "credentials"


def test_test_requires_a_real_prior_reply_destination(tmp_path):
    store = _store(tmp_path)
    store.save_credentials("telegram", {"token": "secret-token"})
    store.set_enabled("telegram", True)
    tester = ChannelTestService(store, FakeHandler())

    result = asyncio.run(tester.run("telegram", "question"))

    assert result.stage == "destination"
    assert "must message" in result.error


def test_telegram_test_uses_saved_chat_and_full_handler(tmp_path):
    store = _store(tmp_path)
    store.save_credentials("telegram", {"token": "secret-token"})
    store.set_enabled("telegram", True)
    store.mark_connected("telegram", "chat-456")
    handler = FakeHandler()
    api = FakeTelegramAPI()
    tester = ChannelTestService(
        store,
        handler,
        telegram_api_factory=lambda: api,
    )

    result = asyncio.run(tester.run("telegram", "What is the leave policy?"))

    assert result.ok is True
    assert result.stage == "complete"
    assert result.latency_ms == 321
    message, adapter = handler.calls[0]
    assert message.reply_ref == "chat-456"
    assert message.text == "What is the leave policy?"
    assert adapter.profile.name == "telegram"


def test_teams_test_uses_saved_conversation_reference(tmp_path):
    store = _store(tmp_path)
    store.save_credentials(
        "teams", {"app_id": "app-id", "app_password": "secret"}
    )
    store.set_enabled("teams", True)
    store.mark_connected("teams", _teams_reference())
    handler = FakeHandler()
    sdk = FakeTeamsSDK()
    tester = ChannelTestService(
        store,
        handler,
        teams_adapter_factory=lambda app_id, password: sdk,
    )

    result = asyncio.run(tester.run("teams", "What is the leave policy?"))

    assert result.ok is True
    reference, bot_id = sdk.references[0]
    assert reference.conversation.id == "conversation-1"
    assert bot_id == "app-id"
    assert handler.calls[0][0].reply_ref == store.get("teams").last_reply_ref


def test_handler_failure_stage_is_preserved(tmp_path):
    store = _store(tmp_path)
    store.save_credentials("telegram", {"token": "secret-token"})
    store.set_enabled("telegram", True)
    store.mark_connected("telegram", "chat-456")
    handler = FakeHandler(
        HandlerResult(True, True, "failed", 88, "model failed", "pipeline")
    )
    tester = ChannelTestService(
        store,
        handler,
        telegram_api_factory=FakeTelegramAPI,
    )

    result = asyncio.run(tester.run("telegram", "question"))

    assert result.ok is False
    assert result.stage == "pipeline"
    assert result.latency_ms == 88


def test_credential_like_delivery_error_is_reported_as_credentials(tmp_path):
    store = _store(tmp_path)
    store.save_credentials("telegram", {"token": "bad-token"})
    store.set_enabled("telegram", True)
    store.mark_connected("telegram", "chat-456")
    handler = FakeHandler(
        HandlerResult(
            True, False, "failed", 12, "Telegram send failed: Unauthorized", "delivery"
        )
    )
    tester = ChannelTestService(
        store,
        handler,
        telegram_api_factory=FakeTelegramAPI,
    )

    result = asyncio.run(tester.run("telegram", "question"))

    assert result.stage == "credentials"
    assert result.ok is False
