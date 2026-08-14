"""Authenticated integration configuration, status, and delivery tests."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.channels.store import ChannelStore, CredentialError
from app.channels.tester import ChannelTestService

CHANNELS = ("telegram", "teams")
DEFAULT_TEST_QUESTION = "How many days of annual leave do full-time employees receive?"


class CredentialRequest(BaseModel):
    credentials: dict[str, str]
    enabled: bool = True


class EnabledRequest(BaseModel):
    enabled: bool


class ChannelTestRequest(BaseModel):
    question: str = Field(default=DEFAULT_TEST_QUESTION, min_length=1)

    @field_validator("question")
    @classmethod
    def question_must_contain_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must contain text")
        return stripped


def _validate_channel(channel: str) -> None:
    if channel not in CHANNELS:
        raise HTTPException(status_code=404, detail=f"unsupported channel: {channel!r}")


def _summary(store: ChannelStore, channel: str) -> dict:
    state = store.get(channel)
    credential_error = None
    try:
        masked = store.masked_credentials(channel)
    except CredentialError as exc:
        masked = {}
        credential_error = str(exc)
        state = store.get(channel)
    return {
        "channel": state.channel,
        "display_name": state.display_name,
        "enabled": state.enabled,
        "status": state.status,
        "credentials": masked,
        "configured": bool(masked),
        "has_reply_destination": bool(state.last_reply_ref),
        "last_ok_at": state.last_ok_at,
        "last_error": state.last_error,
        "last_error_at": state.last_error_at,
        "recent_errors": store.recent_errors(channel),
        "credential_error": credential_error,
    }


def build_integrations_router(
    store: ChannelStore, tester: ChannelTestService
) -> APIRouter:
    router = APIRouter(prefix="/admin/integrations")

    @router.get("")
    def list_integrations() -> list[dict]:
        return [_summary(store, channel) for channel in CHANNELS]

    @router.get("/{channel}")
    def get_integration(channel: str) -> dict:
        _validate_channel(channel)
        return _summary(store, channel)

    @router.put("/{channel}")
    def save_integration(channel: str, body: CredentialRequest) -> dict:
        _validate_channel(channel)
        try:
            store.save_credentials(channel, body.credentials)
            store.set_enabled(channel, body.enabled)
        except CredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _summary(store, channel)

    @router.patch("/{channel}/enabled")
    def set_enabled(channel: str, body: EnabledRequest) -> dict:
        _validate_channel(channel)
        if body.enabled:
            try:
                credentials = store.load_credentials(channel)
            except CredentialError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            # Teams supports a credential-free Bot Framework Emulator only
            # on loopback. TeamsEndpoint independently rejects credential-free
            # non-loopback requests, so enabling this state does not expose a
            # public unauthenticated bot endpoint.
            if credentials is None and channel != "teams":
                raise HTTPException(
                    status_code=400,
                    detail=f"{channel} credentials must be configured before enabling",
                )
        store.set_enabled(channel, body.enabled)
        return _summary(store, channel)

    @router.delete("/{channel}")
    def clear_integration(channel: str) -> dict:
        _validate_channel(channel)
        store.clear_credentials(channel)
        return _summary(store, channel)

    @router.post("/{channel}/test")
    async def test_integration(channel: str, body: ChannelTestRequest) -> dict:
        _validate_channel(channel)
        result = await tester.run(channel, body.question)
        return asdict(result)

    return router
