"""Destination-aware end-to-end tests for configured chat channels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext

from app.channels.base import InboundMessage
from app.channels.handler import ChannelHandler, HandlerResult
from app.channels.store import ChannelStore, CredentialError
from app.channels.teams import TeamsAdapter, deserialize_conversation_reference
from app.channels.telegram import TelegramAdapter, TelegramBotAPI

TestStage = Literal[
    "setup", "credentials", "destination", "pipeline", "delivery", "complete"
]


@dataclass(frozen=True)
class ChannelTestResult:
    channel: str
    ok: bool
    status: str
    stage: TestStage
    latency_ms: int
    error: str | None = None


def _credential_failure(error: str | None) -> bool:
    value = (error or "").lower()
    return any(
        marker in value
        for marker in ("unauthorized", "authentication", "credential", "token", "401")
    )


class ChannelTestService:
    def __init__(
        self,
        store: ChannelStore,
        handler: ChannelHandler,
        *,
        telegram_max_chars: int = 4096,
        teams_max_chars: int = 28000,
        telegram_api_factory=TelegramBotAPI,
        teams_adapter_factory=None,
    ) -> None:
        self._store = store
        self._handler = handler
        self._telegram_max_chars = telegram_max_chars
        self._teams_max_chars = teams_max_chars
        self._telegram_api_factory = telegram_api_factory
        self._teams_adapter_factory = teams_adapter_factory or self._build_teams_adapter

    @staticmethod
    def _build_teams_adapter(app_id: str, password: str) -> BotFrameworkAdapter:
        return BotFrameworkAdapter(BotFrameworkAdapterSettings(app_id, password))

    async def run(self, channel: str, question: str) -> ChannelTestResult:
        try:
            state = self._store.get(channel)
        except ValueError as exc:
            return ChannelTestResult(channel, False, "failed", "setup", 0, str(exc))
        if not state.enabled:
            return ChannelTestResult(
                channel, False, "failed", "setup", 0, f"{state.display_name} is disabled"
            )

        try:
            credentials = self._store.load_credentials(channel)
        except CredentialError as exc:
            return ChannelTestResult(
                channel, False, "failed", "credentials", 0, str(exc)
            )
        if credentials is None:
            return ChannelTestResult(
                channel,
                False,
                "failed",
                "credentials",
                0,
                f"{state.display_name} credentials are not configured",
            )
        if not state.last_reply_ref:
            return ChannelTestResult(
                channel,
                False,
                "failed",
                "destination",
                0,
                f"A user must message {state.display_name} before running a delivery test",
            )

        if channel == "telegram":
            return await self._run_telegram(
                question, state.last_reply_ref, credentials.values["token"]
            )
        if channel == "teams":
            return await self._run_teams(
                question,
                state.last_reply_ref,
                credentials.values["app_id"],
                credentials.values["app_password"],
            )
        return ChannelTestResult(
            channel, False, "failed", "setup", 0, f"unsupported channel: {channel!r}"
        )

    async def _run_telegram(
        self, question: str, reply_ref: str, token: str
    ) -> ChannelTestResult:
        try:
            async with self._telegram_api_factory() as api:
                adapter = TelegramAdapter(
                    api, token, max_message_chars=self._telegram_max_chars
                )
                result = await self._handler.handle(
                    InboundMessage("telegram", None, question, reply_ref), adapter
                )
        except Exception as exc:
            self._store.mark_disconnected("telegram", str(exc))
            stage: TestStage = "credentials" if _credential_failure(str(exc)) else "delivery"
            return ChannelTestResult("telegram", False, "failed", stage, 0, str(exc))
        return self._from_handler("telegram", result)

    async def _run_teams(
        self,
        question: str,
        reply_ref: str,
        app_id: str,
        password: str,
    ) -> ChannelTestResult:
        try:
            reference = deserialize_conversation_reference(reply_ref)
        except (ValueError, TypeError) as exc:
            error = f"Stored Teams reply destination is invalid: {exc}"
            self._store.mark_disconnected("teams", error)
            return ChannelTestResult("teams", False, "failed", "destination", 0, error)

        sdk_adapter = self._teams_adapter_factory(app_id, password)
        captured: HandlerResult | None = None

        async def continue_turn(context: TurnContext) -> None:
            nonlocal captured
            adapter = TeamsAdapter(context, max_message_chars=self._teams_max_chars)
            captured = await self._handler.handle(
                InboundMessage("teams", None, question, reply_ref), adapter
            )

        try:
            await sdk_adapter.continue_conversation(reference, continue_turn, bot_id=app_id)
        except Exception as exc:
            self._store.mark_disconnected("teams", str(exc))
            stage: TestStage = "credentials" if _credential_failure(str(exc)) else "delivery"
            return ChannelTestResult("teams", False, "failed", stage, 0, str(exc))
        if captured is None:
            error = "Bot Framework delivery completed without running the channel test"
            self._store.mark_disconnected("teams", error)
            return ChannelTestResult("teams", False, "failed", "delivery", 0, error)
        return self._from_handler("teams", captured)

    @staticmethod
    def _from_handler(channel: str, result: HandlerResult) -> ChannelTestResult:
        stage: TestStage = result.failure_stage or "complete"
        if stage == "delivery" and _credential_failure(result.error):
            stage = "credentials"
        ok = result.delivered and result.status in {"success", "no_match"}
        return ChannelTestResult(
            channel=channel,
            ok=ok,
            status=result.status,
            stage=stage,
            latency_ms=result.latency_ms,
            error=result.error,
        )
