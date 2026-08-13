"""Microsoft Teams transport through the Bot Framework SDK."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import urlparse

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity, ActivityTypes, ConversationReference
from fastapi import APIRouter, HTTPException, Request, Response, status

from app.channels.base import InboundMessage
from app.channels.handler import ChannelHandler
from app.channels.store import ChannelStore, CredentialError
from app.rag.generate import ChannelProfile

TEAMS_CHANNEL = "teams"
TEAMS_MESSAGES_PATH = "/api/messages"


def _conversation_reference(context: TurnContext) -> str:
    reference = TurnContext.get_conversation_reference(context.activity)
    return json.dumps(reference.serialize(), separators=(",", ":"), sort_keys=True)


def normalize_activity(context: TurnContext) -> InboundMessage | None:
    """Convert one Bot Framework message activity to the shared shape."""
    activity = context.activity
    if activity.type != ActivityTypes.message:
        return None
    sender = activity.from_property
    text = activity.text if isinstance(activity.text, str) else None
    return InboundMessage(
        channel=TEAMS_CHANNEL,
        user_ref=str(sender.id) if sender and sender.id is not None else None,
        text=text,
        reply_ref=_conversation_reference(context),
    )


class TeamsAdapter:
    channel = TEAMS_CHANNEL

    def __init__(self, context: TurnContext, *, max_message_chars: int) -> None:
        self._context = context
        self.profile = ChannelProfile(
            name=TEAMS_CHANNEL,
            max_chars=max_message_chars,
            markup="html",
            supports_lists=True,
        )

    async def typing(self, reply_ref: str) -> None:
        await self._context.send_activity(Activity(type=ActivityTypes.typing))

    async def send(self, reply_ref: str, text: str) -> None:
        if len(text) > self.profile.max_chars:
            raise ValueError(
                f"Teams message exceeds {self.profile.max_chars} characters"
            )
        await self._context.send_activity(
            Activity(type=ActivityTypes.message, text=text, text_format="xml")
        )


AdapterFactory = Callable[[str, str], BotFrameworkAdapter]


def _default_adapter_factory(app_id: str, app_password: str) -> BotFrameworkAdapter:
    return BotFrameworkAdapter(BotFrameworkAdapterSettings(app_id, app_password))


class TeamsEndpoint:
    """Authenticate and dispatch inbound Bot Framework activities."""

    def __init__(
        self,
        store: ChannelStore,
        handler: ChannelHandler,
        *,
        max_message_chars: int = 28000,
        adapter_factory: AdapterFactory = _default_adapter_factory,
    ) -> None:
        self._store = store
        self._handler = handler
        self._max_message_chars = max_message_chars
        self._adapter_factory = adapter_factory

    async def process(self, request: Request) -> Response:
        if not self._store.is_enabled(TEAMS_CHANNEL):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Teams is disabled")

        try:
            credentials = self._store.load_credentials(TEAMS_CHANNEL)
        except CredentialError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid activity JSON") from exc

        activity = Activity().deserialize(body)
        local_hosts = {"127.0.0.1", "::1", "localhost", "testclient", "testserver"}
        client_host = request.client.host if request.client is not None else ""
        request_host = request.url.hostname or ""
        service_host = urlparse(activity.service_url or "").hostname or ""
        local_emulator = (
            client_host in local_hosts
            and request_host in local_hosts
            and service_host in local_hosts
        )
        if credentials is None:
            if not local_emulator:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Teams credentials are not configured",
                )
            app_id = app_password = ""
        else:
            app_id = credentials.values["app_id"]
            app_password = credentials.values["app_password"]

        auth_header = request.headers.get("Authorization", "")
        adapter = self._adapter_factory(app_id, app_password)

        async def on_turn(context: TurnContext) -> None:
            message = normalize_activity(context)
            if message is None:
                return
            transport = TeamsAdapter(
                context,
                max_message_chars=self._max_message_chars,
            )
            await self._handler.handle(message, transport)

        try:
            invoke_response = await adapter.process_activity(
                activity,
                auth_header,
                on_turn,
            )
        except PermissionError as exc:
            self._store.mark_disconnected(TEAMS_CHANNEL, "Bot Framework authentication failed")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized activity") from exc
        except HTTPException:
            raise
        except Exception as exc:
            self._store.mark_disconnected(TEAMS_CHANNEL, str(exc))
            # A successful acknowledgement prevents platform retry loops; the
            # shared handler already attempts a user-facing failure reply.
            return Response(status_code=status.HTTP_200_OK)

        if invoke_response is None:
            return Response(status_code=status.HTTP_200_OK)
        return Response(
            content=json.dumps(invoke_response.body),
            status_code=invoke_response.status,
            media_type="application/json",
        )


def build_teams_router(endpoint: TeamsEndpoint) -> APIRouter:
    router = APIRouter()

    @router.post(TEAMS_MESSAGES_PATH)
    async def messages(request: Request) -> Response:
        return await endpoint.process(request)

    return router


def deserialize_conversation_reference(value: str) -> ConversationReference:
    """Load the persisted reference used by a future admin test action."""
    return ConversationReference().deserialize(json.loads(value))
