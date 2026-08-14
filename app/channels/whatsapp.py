"""WhatsApp Cloud API webhook and outbound message adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status

from app.channels.base import InboundMessage
from app.channels.handler import ChannelHandler
from app.channels.store import ChannelStore, CredentialError
from app.rag.generate import ChannelProfile

logger = logging.getLogger(__name__)

WHATSAPP_CHANNEL = "whatsapp"
WHATSAPP_WEBHOOK_PATH = "/api/whatsapp/webhook"
WHATSAPP_GRAPH_ROOT = "https://graph.facebook.com"


class WhatsAppAPIError(RuntimeError):
    """A Cloud API request failed without exposing its access token."""


class WhatsAppCloudAPI:
    def __init__(
        self,
        *,
        session: httpx.AsyncClient | None = None,
        graph_root: str = WHATSAPP_GRAPH_ROOT,
        proxy_url: str | None = None,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self._graph_root = graph_root.rstrip("/")
        self._proxy_url = proxy_url

    async def __aenter__(self) -> "WhatsAppCloudAPI":
        if self._session is None:
            self._session = httpx.AsyncClient(
                proxy=self._proxy_url,
                timeout=10,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=300,
                ),
            )
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._owns_session and self._session is not None:
            await self._session.aclose()
            self._session = None

    async def warm_delivery(
        self, access_token: str, phone_number_id: str
    ) -> None:
        """Validate credentials and establish the Graph API connection."""
        if self._session is None:
            raise RuntimeError("WhatsApp API client is not open")
        response = await self._session.get(
            f"{self._graph_root}/{phone_number_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "id"},
            timeout=httpx.Timeout(8, connect=4),
        )
        if response.is_success:
            return
        raise WhatsAppAPIError(
            f"WhatsApp connection warm-up failed ({response.status_code})"
        )

    async def send_message(
        self, access_token: str, phone_number_id: str, recipient: str, text: str
    ) -> None:
        if self._session is None:
            raise RuntimeError("WhatsApp API client is not open")
        response = await self._session.post(
            f"{self._graph_root}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
        )
        if response.is_success:
            return
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("error", {}).get("message", ""))
        except (ValueError, AttributeError):
            pass
        raise WhatsAppAPIError(
            f"WhatsApp send failed ({response.status_code})"
            + (f": {detail}" if detail else "")
        )


class WhatsAppAdapter:
    channel = WHATSAPP_CHANNEL

    def __init__(
        self,
        api: WhatsAppCloudAPI,
        access_token: str,
        phone_number_id: str,
        *,
        max_message_chars: int = 4096,
    ) -> None:
        self._api = api
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self.profile = ChannelProfile(
            name=WHATSAPP_CHANNEL,
            max_chars=max_message_chars,
            markup="plain",
            supports_lists=True,
        )

    async def typing(self, reply_ref: str) -> None:
        return None

    async def send(self, reply_ref: str, text: str) -> None:
        if len(text) > self.profile.max_chars:
            raise ValueError(
                f"WhatsApp message exceeds {self.profile.max_chars} characters"
            )
        await self._api.send_message(
            self._access_token, self._phone_number_id, reply_ref, text
        )


def normalize_messages(payload: Mapping[str, Any]) -> list[InboundMessage]:
    """Extract text messages while ignoring delivery/status callbacks."""
    normalized: list[InboundMessage] = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return normalized
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, Mapping) or change.get("field") != "messages":
                continue
            value = change.get("value", {})
            messages = value.get("messages", []) if isinstance(value, Mapping) else []
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, Mapping):
                    continue
                sender = message.get("from")
                if not isinstance(sender, str) or not sender:
                    continue
                text_object = message.get("text", {})
                text = (
                    text_object.get("body")
                    if message.get("type") == "text" and isinstance(text_object, Mapping)
                    else None
                )
                normalized.append(
                    InboundMessage(
                        channel=WHATSAPP_CHANNEL,
                        user_ref=sender,
                        text=text if isinstance(text, str) else None,
                        reply_ref=sender,
                    )
                )
    return normalized


class WhatsAppEndpoint:
    def __init__(
        self,
        store: ChannelStore,
        handler: ChannelHandler,
        *,
        max_message_chars: int = 4096,
        api_factory=WhatsAppCloudAPI,
        api_provider=None,
    ) -> None:
        self._store = store
        self._handler = handler
        self._max_message_chars = max_message_chars
        self._api_factory = api_factory
        self._api_provider = api_provider or (lambda: None)

    def _credentials(self) -> dict[str, str]:
        try:
            credentials = self._store.load_credentials(WHATSAPP_CHANNEL)
        except CredentialError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        if credentials is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "WhatsApp credentials are not configured",
            )
        return credentials.values

    def verify(self, mode: str | None, token: str | None, challenge: str | None) -> Response:
        credentials = self._credentials()
        if (
            mode != "subscribe"
            or not token
            or not hmac.compare_digest(token, credentials["verify_token"])
            or challenge is None
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Webhook verification failed")
        return Response(challenge, media_type="text/plain")

    async def receive(self, request: Request, background: BackgroundTasks) -> Response:
        credentials = self._credentials()
        body = await request.body()
        supplied = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            credentials["app_secret"].encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook JSON") from exc
        if not isinstance(payload, Mapping):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook payload")
        if not self._store.is_enabled(WHATSAPP_CHANNEL):
            return Response(status_code=status.HTTP_200_OK)
        for message in normalize_messages(payload):
            background.add_task(self._dispatch, message, credentials)
        return Response(status_code=status.HTTP_200_OK)

    async def _dispatch(
        self, message: InboundMessage, credentials: Mapping[str, str]
    ) -> None:
        try:
            api = self._api_provider()
            if api is not None:
                adapter = WhatsAppAdapter(
                    api,
                    credentials["access_token"],
                    credentials["phone_number_id"],
                    max_message_chars=self._max_message_chars,
                )
                await self._handler.handle(message, adapter)
            else:
                async with self._api_factory() as api:
                    adapter = WhatsAppAdapter(
                        api,
                        credentials["access_token"],
                        credentials["phone_number_id"],
                        max_message_chars=self._max_message_chars,
                    )
                    await self._handler.handle(message, adapter)
        except Exception as exc:
            logger.exception("WhatsApp message processing failed")
            self._store.mark_disconnected(WHATSAPP_CHANNEL, str(exc))


def build_whatsapp_router(endpoint: WhatsAppEndpoint) -> APIRouter:
    router = APIRouter()

    @router.get(WHATSAPP_WEBHOOK_PATH)
    def verify_webhook(
        hub_mode: str | None = Query(default=None, alias="hub.mode"),
        hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> Response:
        return endpoint.verify(hub_mode, hub_verify_token, hub_challenge)

    @router.post(WHATSAPP_WEBHOOK_PATH)
    async def receive_webhook(
        request: Request, background_tasks: BackgroundTasks
    ) -> Response:
        return await endpoint.receive(request, background_tasks)

    return router
