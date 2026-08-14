from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from app.channels.telegram import (
    TelegramAdapter,
    TelegramAPIError,
    TelegramBotAPI,
    TelegramPoller,
    normalize_update,
)
from app.main import _poller_lifespan
from app.db import create_engine_for, init_schema
from app.channels.store import ChannelStore


TEXT_UPDATE = {
    "update_id": 41,
    "message": {
        "message_id": 9,
        "from": {"id": 123, "is_bot": False},
        "chat": {"id": -456, "type": "group"},
        "text": "How much leave do I get?",
    },
}

NON_TEXT_UPDATE = {
    "update_id": 42,
    "message": {
        "message_id": 10,
        "from": {"id": 123, "is_bot": False},
        "chat": {"id": -456, "type": "group"},
        "sticker": {"file_id": "sticker-id"},
    },
}


class FakeResponse:
    def __init__(self, body, *, status=200):
        self.body = body
        self.status_code = status

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, *, json):
        self.calls.append((url, json))
        return self.responses.pop(0)


class FakeAPI:
    def __init__(self, batches=None):
        self.batches = list(batches or [])
        self.polls = []
        self.actions = []
        self.messages = []
        self.error: Exception | None = None

    async def get_updates(self, token, *, offset, timeout):
        self.polls.append((token, offset, timeout))
        if self.error:
            raise self.error
        return self.batches.pop(0) if self.batches else []

    async def send_chat_action(self, token, chat_id):
        self.actions.append((token, chat_id))

    async def send_message(self, token, chat_id, text):
        self.messages.append((token, chat_id, text))


class FakeHandler:
    def __init__(self):
        self.calls = []
        self.error: Exception | None = None

    async def handle(self, message, adapter):
        self.calls.append((message, adapter))
        if self.error:
            raise self.error


def _store(tmp_path, *, token="token-one"):
    engine = create_engine_for(tmp_path / "telegram.db")
    init_schema(engine)
    store = ChannelStore(
        engine,
        Fernet.generate_key().decode("ascii"),
    )
    store.save_credentials("telegram", {"token": token})
    store.set_enabled("telegram", True)
    return store


def test_captured_text_update_normalizes_to_shared_message():
    message = normalize_update(TEXT_UPDATE)

    assert message.channel == "telegram"
    assert message.user_ref == "123"
    assert message.reply_ref == "-456"
    assert message.text == "How much leave do I get?"


def test_captured_non_text_update_is_delivered_to_handler_as_unsupported():
    message = normalize_update(NON_TEXT_UPDATE)

    assert message is not None
    assert message.text is None
    assert message.reply_ref == "-456"


def test_non_message_and_bot_updates_are_ignored():
    assert normalize_update({"update_id": 1, "callback_query": {}}) is None
    bot_update = {
        **TEXT_UPDATE,
        "message": {**TEXT_UPDATE["message"], "from": {"id": 1, "is_bot": True}},
    }
    assert normalize_update(bot_update) is None


def test_bot_api_sends_expected_payloads_without_reformatting():
    session = FakeSession(
        [
            FakeResponse({"ok": True, "result": True}),
            FakeResponse({"ok": True, "result": {"message_id": 10}}),
        ]
    )
    api = TelegramBotAPI(session=session, api_root="https://telegram.test")
    adapter = TelegramAdapter(api, "secret-token", max_message_chars=4096)

    async def exercise():
        await adapter.typing("-456")
        await adapter.send("-456", "already\\-escaped")

    asyncio.run(exercise())

    assert session.calls == [
        (
            "https://telegram.test/botsecret-token/sendChatAction",
            {"chat_id": "-456", "action": "typing"},
        ),
        (
            "https://telegram.test/botsecret-token/sendMessage",
            {
                "chat_id": "-456",
                "text": "already\\-escaped",
                "parse_mode": "MarkdownV2",
            },
        ),
    ]


def test_bot_api_get_updates_sends_offset_timeout_and_message_filter():
    session = FakeSession(
        [FakeResponse({"ok": True, "result": [TEXT_UPDATE]})]
    )
    api = TelegramBotAPI(session=session, api_root="https://telegram.test")

    updates = asyncio.run(api.get_updates("token", offset=42, timeout=20))

    assert updates == [TEXT_UPDATE]
    assert session.calls == [
        (
            "https://telegram.test/bottoken/getUpdates",
            {"offset": 42, "timeout": 20, "allowed_updates": ["message"]},
        )
    ]


def test_adapter_rejects_over_limit_messages_before_api_call():
    api = FakeAPI()
    adapter = TelegramAdapter(api, "token", max_message_chars=4)

    with pytest.raises(ValueError, match="exceeds 4"):
        asyncio.run(adapter.send("chat", "12345"))

    assert api.messages == []


def test_bot_api_errors_never_include_the_token():
    session = FakeSession(
        [FakeResponse({"ok": False, "description": "Unauthorized"}, status=401)]
    )
    api = TelegramBotAPI(session=session, api_root="https://telegram.test")

    with pytest.raises(TelegramAPIError, match="Unauthorized") as captured:
        asyncio.run(api.send_message("do-not-leak", "chat", "text"))

    assert "do-not-leak" not in str(captured.value)


def test_poller_advances_offset_and_skips_a_duplicate_update(tmp_path):
    store = _store(tmp_path)
    handler = FakeHandler()
    api = FakeAPI([[TEXT_UPDATE], [TEXT_UPDATE]])
    poller = TelegramPoller(store, handler, poll_timeout_seconds=7)

    async def exercise():
        assert await poller.poll_once(api) is True
        assert await poller.poll_once(api) is True

    asyncio.run(exercise())

    assert [call[1] for call in api.polls] == [None, 42]
    assert len(handler.calls) == 1
    assert poller.offset == 42


def test_poller_advances_past_an_update_that_fails_in_the_handler(tmp_path):
    store = _store(tmp_path)
    handler = FakeHandler()
    handler.error = RuntimeError("bad update")
    api = FakeAPI([[TEXT_UPDATE]])
    poller = TelegramPoller(store, handler)

    assert asyncio.run(poller.poll_once(api)) is True

    assert poller.offset == 42
    assert store.get("telegram").status == "disconnected"
    assert store.get("telegram").last_error == "bad update"


def test_poller_resets_offset_when_credentials_change(tmp_path):
    store = _store(tmp_path)
    handler = FakeHandler()
    api = FakeAPI([[TEXT_UPDATE], []])
    poller = TelegramPoller(store, handler)

    async def exercise():
        await poller.poll_once(api)
        store.save_credentials("telegram", {"token": "token-two"})
        await poller.poll_once(api)

    asyncio.run(exercise())

    assert api.polls[0][:2] == ("token-one", None)
    assert api.polls[1][:2] == ("token-two", None)


def test_disabled_or_unconfigured_poller_does_not_call_telegram(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("telegram", False)
    api = FakeAPI()
    poller = TelegramPoller(store, FakeHandler())

    assert asyncio.run(poller.poll_once(api)) is False
    assert api.polls == []

    store.clear_credentials("telegram")
    store.set_enabled("telegram", True)
    assert asyncio.run(poller.poll_once(api)) is False
    assert api.polls == []
    assert "not configured" in store.get("telegram").last_error


def test_polling_api_failure_disconnects_channel(tmp_path):
    store = _store(tmp_path)
    api = FakeAPI()
    api.error = TelegramAPIError("Telegram getUpdates failed: Unauthorized")
    poller = TelegramPoller(store, FakeHandler())

    assert asyncio.run(poller.poll_once(api)) is False

    state = store.get("telegram")
    assert state.status == "disconnected"
    assert state.last_error == "Telegram getUpdates failed: Unauthorized"


def test_application_lifespan_starts_and_stops_the_poller():
    class LifespanPoller:
        def __init__(self):
            self.started = asyncio.Event()
            self.stopped = False

        async def run(self):
            self.started.set()
            await asyncio.Event().wait()

        def stop(self):
            self.stopped = True

    async def exercise():
        poller = LifespanPoller()
        lifespan = _poller_lifespan(poller)
        async with lifespan(None):
            await asyncio.wait_for(poller.started.wait(), timeout=1)
        return poller

    poller = asyncio.run(exercise())

    assert poller.stopped is True
