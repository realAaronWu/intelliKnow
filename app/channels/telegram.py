"""Telegram Bot API adapter and long-polling service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx

from app.channels.base import InboundMessage
from app.channels.handler import ChannelHandler
from app.channels.store import ChannelStore, CredentialError
from app.rag.generate import ChannelProfile

logger = logging.getLogger(__name__)

TELEGRAM_API_ROOT = "https://api.telegram.org"
TELEGRAM_CHANNEL = "telegram"


class TelegramAPIError(RuntimeError):
    """A Telegram Bot API request failed without exposing its bot token."""


class TelegramAPI(Protocol):
    async def warm_delivery(self, token: str) -> None: ...

    async def get_updates(
        self, token: str, *, offset: int | None, timeout: int
    ) -> list[Mapping[str, Any]]: ...

    async def send_chat_action(self, token: str, chat_id: str) -> None: ...

    async def send_message(self, token: str, chat_id: str, text: str) -> None: ...


class TelegramBotAPI:
    """Small async client for the three Bot API methods used by the MVP."""

    def __init__(
        self,
        *,
        session: httpx.AsyncClient | None = None,
        api_root: str = TELEGRAM_API_ROOT,
        proxy_url: str | None = None,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self._poll_session: httpx.AsyncClient | None = None
        self._typing_session: httpx.AsyncClient | None = None
        self._api_root = api_root.rstrip("/")
        self._proxy_url = proxy_url

    async def __aenter__(self) -> "TelegramBotAPI":
        if self._session is None:
            proxy_url = self._proxy_url or (
                os.environ.get("HTTPS_PROXY")
                or os.environ.get("ALL_PROXY")
                or os.environ.get("HTTP_PROXY")
            )
            client_options = {
                "proxy": proxy_url,
                "timeout": 30,
                "trust_env": proxy_url is None,
                "limits": httpx.Limits(
                    max_connections=5,
                    max_keepalive_connections=2,
                    keepalive_expiry=300,
                ),
            }
            # Long polling and the cancellable typing indicator must not
            # consume or invalidate the warm connection used for answers.
            self._session = httpx.AsyncClient(**client_options)
            self._poll_session = httpx.AsyncClient(**client_options)
            self._typing_session = httpx.AsyncClient(**client_options)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._owns_session:
            for session in (
                self._session,
                self._poll_session,
                self._typing_session,
            ):
                if session is not None:
                    await session.aclose()
            self._session = None
            self._poll_session = None
            self._typing_session = None

    async def get_updates(
        self, token: str, *, offset: int | None, timeout: int
    ) -> list[Mapping[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._request(
            token,
            "getUpdates",
            payload,
            timeout=httpx.Timeout(timeout + 5, connect=5),
            session=self._poll_session,
        )
        if not isinstance(result, list) or not all(
            isinstance(update, Mapping) for update in result
        ):
            raise TelegramAPIError("Telegram getUpdates returned an invalid result")
        return result

    async def warm_delivery(self, token: str) -> None:
        """Validate credentials and establish the answer connection early."""
        await self._request(
            token,
            "getMe",
            {},
            timeout=httpx.Timeout(6, connect=4),
            connect_retries=3,
        )

    async def send_chat_action(self, token: str, chat_id: str) -> None:
        await self._request(
            token,
            "sendChatAction",
            {"chat_id": chat_id, "action": "typing"},
            timeout=httpx.Timeout(5, connect=3),
            session=self._typing_session,
        )

    async def send_message(self, token: str, chat_id: str, text: str) -> None:
        await self._request(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": text},
            timeout=httpx.Timeout(10, connect=5),
            connect_retries=1,
        )

    async def _request(
        self,
        token: str,
        method: str,
        payload: Mapping[str, Any],
        *,
        timeout: httpx.Timeout | None = None,
        connect_retries: int = 0,
        session: httpx.AsyncClient | None = None,
    ) -> Any:
        active_session = session or self._session
        if active_session is None:
            raise RuntimeError("TelegramBotAPI must be used as an async context manager")
        url = f"{self._api_root}/bot{token}/{method}"
        for attempt in range(connect_retries + 1):
            try:
                response = await active_session.post(
                    url,
                    json=dict(payload),
                    timeout=timeout,
                )
                try:
                    body = response.json()
                except ValueError as exc:
                    raise TelegramAPIError(
                        f"Telegram {method} returned an invalid response"
                    ) from exc
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as exc:
                if attempt < connect_retries:
                    logger.warning(
                        "Telegram %s connection failed; retrying once", method
                    )
                    await asyncio.sleep(0.1)
                    continue
                raise TelegramAPIError(
                    f"Telegram {method} could not connect through the configured network"
                ) from exc
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                raise TelegramAPIError(f"Telegram {method} request failed") from exc

        description = body.get("description") if isinstance(body, Mapping) else None
        if (
            response.status_code >= 400
            or not isinstance(body, Mapping)
            or not body.get("ok")
        ):
            detail = str(description or f"HTTP {response.status_code}").replace(
                token, "***"
            )
            raise TelegramAPIError(f"Telegram {method} failed: {detail}")
        return body.get("result")


def normalize_update(update: Mapping[str, Any]) -> InboundMessage | None:
    """Convert one Telegram message update into the shared inbound shape."""
    message = update.get("message")
    if not isinstance(message, Mapping):
        return None

    sender = message.get("from")
    if isinstance(sender, Mapping) and sender.get("is_bot") is True:
        return None

    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, Mapping) else None
    if chat_id is None:
        return None

    sender_id = sender.get("id") if isinstance(sender, Mapping) else None
    text = message.get("text")
    return InboundMessage(
        channel=TELEGRAM_CHANNEL,
        user_ref=str(sender_id) if sender_id is not None else None,
        text=text if isinstance(text, str) else None,
        reply_ref=str(chat_id),
    )


class TelegramAdapter:
    channel = TELEGRAM_CHANNEL

    def __init__(self, api: TelegramAPI, token: str, *, max_message_chars: int) -> None:
        self._api = api
        self._token = token
        self.profile = ChannelProfile(
            name=TELEGRAM_CHANNEL,
            max_chars=max_message_chars,
            markup="plain",
            supports_lists=False,
        )

    async def typing(self, reply_ref: str) -> None:
        await self._api.send_chat_action(self._token, reply_ref)

    async def send(self, reply_ref: str, text: str) -> None:
        if len(text) > self.profile.max_chars:
            raise ValueError(
                f"Telegram message exceeds {self.profile.max_chars} characters"
            )
        await self._api.send_message(self._token, reply_ref, text)


class TelegramPoller:
    """Sequential long poller with dynamic credentials and offset protection."""

    def __init__(
        self,
        store: ChannelStore,
        handler: ChannelHandler,
        *,
        max_message_chars: int = 4096,
        poll_timeout_seconds: int = 20,
        retry_seconds: float = 2.0,
        api_factory: Callable[[], TelegramBotAPI] = TelegramBotAPI,
    ) -> None:
        self._store = store
        self._handler = handler
        self._max_message_chars = max_message_chars
        self._poll_timeout_seconds = poll_timeout_seconds
        self._retry_seconds = retry_seconds
        self._api_factory = api_factory
        self._stop = asyncio.Event()
        self._offset: int | None = None
        self._token_fingerprint: str | None = None
        self._active_api: TelegramBotAPI | None = None

    @property
    def offset(self) -> int | None:
        return self._offset

    @property
    def active_api(self) -> TelegramBotAPI | None:
        return self._active_api

    async def run(self) -> None:
        self._stop.clear()
        async with self._api_factory() as api:
            self._active_api = api
            try:
                while not self._stop.is_set():
                    polling_succeeded = await self.poll_once(api)
                    if not polling_succeeded:
                        await self._pause(self._retry_seconds)
            finally:
                self._active_api = None

    def stop(self) -> None:
        self._stop.set()

    async def poll_once(self, api: TelegramAPI) -> bool:
        if not self._store.is_enabled(TELEGRAM_CHANNEL):
            return False

        try:
            credentials = self._store.load_credentials(TELEGRAM_CHANNEL)
        except CredentialError as exc:
            self._record_error_once(str(exc))
            return False
        if credentials is None:
            self._record_error_once("Telegram bot token is not configured")
            self._token_fingerprint = None
            self._offset = None
            return False

        token = credentials.values["token"]
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if fingerprint != self._token_fingerprint:
            self._token_fingerprint = fingerprint
            self._offset = None
            try:
                await api.warm_delivery(token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Polling may still work even if this optional connection
                # warm-up hits a transient proxy failure.
                logger.warning("Telegram delivery warm-up failed: %s", exc)

        try:
            updates = await api.get_updates(
                token,
                offset=self._offset,
                timeout=self._poll_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._store.mark_disconnected(TELEGRAM_CHANNEL, str(exc))
            return False

        adapter = TelegramAdapter(
            api,
            token,
            max_message_chars=self._max_message_chars,
        )
        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                logger.warning("Telegram update without an integer update_id was ignored")
                continue
            if self._offset is not None and update_id < self._offset:
                continue
            try:
                message = normalize_update(update)
                if message is not None:
                    await self._handler.handle(message, adapter)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Telegram update %s failed", update_id)
                self._store.mark_disconnected(TELEGRAM_CHANNEL, str(exc))
            finally:
                self._offset = update_id + 1
        return True

    def _record_error_once(self, message: str) -> None:
        state = self._store.get(TELEGRAM_CHANNEL)
        if state.last_error != message or state.status != "disconnected":
            self._store.mark_disconnected(TELEGRAM_CHANNEL, message)

    async def _pause(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass
